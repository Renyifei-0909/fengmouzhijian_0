"""Server-side compliance rule engine.

The model adapter may only emit observations. This module alone decides
compliant / deviation_detected / insufficient_evidence / needs_review.
"""

from __future__ import annotations

from typing import Any

ENGINE_VERSION = "compliance-engine-v0.1"
DEFAULT_RULE_VERSION = "workorder-rules-v0.1"

# Fields that must never be inferred as metrology from a single uncalibrated photo.
DEPTH_LIKE_FIELDS = frozenset(
    {
        "depth_m",
        "trench_depth_m",
        "min_depth_m",
        "measured_depth_m",
        "bury_depth_m",
    }
)


class ComplianceEngineError(ValueError):
    """Invalid rules or observation payload."""


def _as_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def extract_observed_fields(analyzer_result: dict[str, Any]) -> dict[str, Any]:
    """Pull flat observation fields from a validated analyzer result."""
    observations = analyzer_result.get("observations") or {}
    measurements = observations.get("measurements") if isinstance(observations, dict) else {}
    if not isinstance(measurements, dict):
        measurements = {}
    flat: dict[str, Any] = dict(measurements)
    # Optional structured keys used by the work-order observation contract.
    for key in (
        "visible_pipe_count",
        "trench_stage",
        "visible_material_or_specification",
        "object_visibility",
        "insufficient_evidence_reason",
    ):
        if key in measurements:
            flat[key] = measurements[key]
        elif isinstance(observations, dict) and key in observations:
            flat[key] = observations[key]
    if "confidence" in analyzer_result and analyzer_result["confidence"] is not None:
        flat["confidence"] = analyzer_result["confidence"]
    return flat


def evaluate_compliance(
    *,
    rules_snapshot: dict[str, Any],
    analyzer_result: dict[str, Any],
    spatial_check_status: str | None = None,
    rule_version: str | None = None,
) -> dict[str, Any]:
    """Compare frozen work-order rules against model observations.

    Returns a serialisable evaluation dict suitable for persistence and for
    overwriting server-owned ``alignment`` fields on the analyzer result.
    """
    if not isinstance(rules_snapshot, dict):
        raise ComplianceEngineError("rules_snapshot must be an object")
    expected = rules_snapshot.get("expected")
    if expected is None:
        expected = rules_snapshot.get("fields") or rules_snapshot
    if not isinstance(expected, dict):
        raise ComplianceEngineError("rules expected fields must be an object")

    observed = extract_observed_fields(analyzer_result)
    differences: list[dict[str, Any]] = []
    has_deviation = False
    has_insufficient = False
    has_needs_review = False

    # Spatial failure forces needs_review unless rules explicitly ignore it.
    ignore_spatial = bool(rules_snapshot.get("ignore_spatial_failure", False))
    if spatial_check_status == "failed" and not ignore_spatial:
        differences.append(
            {
                "field": "spatial_check",
                "expected": "passed",
                "observed": spatial_check_status,
                "status": "needs_review",
                "message": "GPS spatial check failed; human review required.",
            }
        )
        has_needs_review = True
    elif spatial_check_status == "unavailable" and not ignore_spatial:
        differences.append(
            {
                "field": "spatial_check",
                "expected": "passed_or_present",
                "observed": spatial_check_status,
                "status": "insufficient_evidence",
                "message": "Capture location unavailable; cannot complete location reasonableness check.",
            }
        )
        has_insufficient = True

    for field, rule in expected.items():
        if field.startswith("_"):
            continue
        # Nested containers like measurements are expanded below if present.
        if field == "measurements" and isinstance(rule, dict):
            continue
        item = _evaluate_field(field, rule, observed)
        differences.append(item)
        status = item["status"]
        if status == "deviation_detected":
            has_deviation = True
        elif status == "insufficient_evidence":
            has_insufficient = True
        elif status == "needs_review":
            has_needs_review = True

    measurements_rules = expected.get("measurements")
    if isinstance(measurements_rules, dict):
        for field, rule in measurements_rules.items():
            item = _evaluate_field(field, rule, observed)
            differences.append(item)
            status = item["status"]
            if status == "deviation_detected":
                has_deviation = True
            elif status == "insufficient_evidence":
                has_insufficient = True
            elif status == "needs_review":
                has_needs_review = True

    # Explicit insufficient_evidence from the model is authoritative for that field.
    reason = observed.get("insufficient_evidence_reason")
    if isinstance(reason, str) and reason.strip():
        has_insufficient = True
        differences.append(
            {
                "field": "insufficient_evidence_reason",
                "expected": None,
                "observed": reason.strip(),
                "status": "insufficient_evidence",
                "message": "Analyzer reported insufficient evidence for one or more fields.",
            }
        )

    if has_insufficient:
        verdict = "insufficient_evidence"
    elif has_needs_review:
        verdict = "needs_review"
    elif has_deviation:
        verdict = "deviation_detected"
    elif not differences:
        verdict = "needs_review"
    else:
        # All field statuses are compliant (or skipped info).
        if all(d.get("status") in {"compliant", "not_applicable"} for d in differences):
            verdict = "compliant"
        else:
            verdict = "needs_review"

    return {
        "engine_version": ENGINE_VERSION,
        "rule_version": rule_version or str(rules_snapshot.get("rule_version") or DEFAULT_RULE_VERSION),
        "expected": expected,
        "observed": observed,
        "differences": differences,
        "verdict": verdict,
        "spatial_check_status": spatial_check_status,
        "authority": "server_rule_engine",
        "note": (
            "Verdict is produced by the backend rule engine from frozen design "
            "snapshots; the model adapter does not decide compliance."
        ),
    }


def _evaluate_field(field: str, rule: Any, observed: dict[str, Any]) -> dict[str, Any]:
    observed_value = observed.get(field)

    # Depth-like fields without calibration evidence are never auto-compliant.
    if field in DEPTH_LIKE_FIELDS or (
        isinstance(rule, dict) and rule.get("requires_calibration") is True
    ):
        if observed_value is None:
            return {
                "field": field,
                "expected": rule,
                "observed": None,
                "status": "insufficient_evidence",
                "message": (
                    "Depth/metrology field requires calibration, scale mark, "
                    "multi-view reconstruction or independent measurement; "
                    "single photo is insufficient."
                ),
            }
        # Even if a number is present, without calibration flag mark needs_review.
        if observed.get("calibration_present") is not True:
            return {
                "field": field,
                "expected": rule,
                "observed": observed_value,
                "status": "insufficient_evidence",
                "message": "calibration_missing: depth observation rejected without controlled metrology conditions.",
            }

    if isinstance(rule, dict) and "operator" in rule:
        return _evaluate_operator_rule(field, rule, observed_value)

    if isinstance(rule, dict) and "equals" in rule:
        expected_value = rule["equals"]
        if observed_value is None:
            return {
                "field": field,
                "expected": expected_value,
                "observed": None,
                "status": "insufficient_evidence",
                "message": f"Missing observation for {field}",
            }
        ok = observed_value == expected_value
        return {
            "field": field,
            "expected": expected_value,
            "observed": observed_value,
            "status": "compliant" if ok else "deviation_detected",
            "message": None if ok else f"{field} differs from design snapshot",
        }

    if isinstance(rule, dict) and "one_of" in rule:
        allowed = rule["one_of"]
        if observed_value is None:
            return {
                "field": field,
                "expected": {"one_of": allowed},
                "observed": None,
                "status": "insufficient_evidence",
                "message": f"Missing observation for {field}",
            }
        ok = observed_value in allowed
        return {
            "field": field,
            "expected": {"one_of": allowed},
            "observed": observed_value,
            "status": "compliant" if ok else "deviation_detected",
            "message": None if ok else f"{field} not in allowed set",
        }

    # Bare equality against a scalar/list expectation.
    if observed_value is None:
        return {
            "field": field,
            "expected": rule,
            "observed": None,
            "status": "insufficient_evidence",
            "message": f"Missing observation for {field}",
        }
    ok = observed_value == rule
    return {
        "field": field,
        "expected": rule,
        "observed": observed_value,
        "status": "compliant" if ok else "deviation_detected",
        "message": None if ok else f"{field} differs from design snapshot",
    }


def _evaluate_operator_rule(field: str, rule: dict[str, Any], observed_value: Any) -> dict[str, Any]:
    operator = str(rule.get("operator") or "")
    expected_value = rule.get("value")
    if observed_value is None:
        return {
            "field": field,
            "expected": rule,
            "observed": None,
            "status": "insufficient_evidence",
            "message": f"Missing observation for {field}",
        }
    left = _as_number(observed_value)
    right = _as_number(expected_value)
    if left is None or right is None:
        return {
            "field": field,
            "expected": rule,
            "observed": observed_value,
            "status": "needs_review",
            "message": f"Non-numeric comparison for {field}",
        }
    ops = {
        ">=": left >= right,
        ">": left > right,
        "<=": left <= right,
        "<": left < right,
        "==": left == right,
        "!=": left != right,
    }
    if operator not in ops:
        return {
            "field": field,
            "expected": rule,
            "observed": observed_value,
            "status": "needs_review",
            "message": f"Unknown operator {operator}",
        }
    ok = ops[operator]
    return {
        "field": field,
        "expected": rule,
        "observed": observed_value,
        "status": "compliant" if ok else "deviation_detected",
        "message": None if ok else f"{field} fails {operator} {right}",
    }


def apply_compliance_to_analyzer_result(
    analyzer_result: dict[str, Any],
    evaluation: dict[str, Any],
    *,
    baseline_version: str,
) -> dict[str, Any]:
    """Overwrite server-owned alignment using the rule-engine verdict.

    Keeps analyzer observations intact; never invents accuracy claims.
    """
    result = dict(analyzer_result)
    status_map = {
        "compliant": "aligned",
        "deviation_detected": "deviation_detected",
        "insufficient_evidence": "insufficient_evidence",
        "needs_review": "needs_review",
    }
    result["alignment"] = {
        "status": status_map.get(evaluation["verdict"], "needs_review"),
        "baseline_version": baseline_version,
        "differences": evaluation["differences"],
    }
    # Server-only side channel (not part of protected claim keys).
    result["compliance_evaluation"] = {
        "verdict": evaluation["verdict"],
        "engine_version": evaluation["engine_version"],
        "rule_version": evaluation["rule_version"],
        "authority": evaluation["authority"],
        "spatial_check_status": evaluation.get("spatial_check_status"),
        "note": evaluation["note"],
    }
    # Append a finding that states the engine verdict without claiming accuracy.
    findings = list(result.get("findings") or [])
    findings.append(
        {
            "code": f"COMPLIANCE_{evaluation['verdict'].upper()}",
            "severity": "info" if evaluation["verdict"] == "compliant" else "warning",
            "message": (
                f"Rule engine verdict={evaluation['verdict']} "
                f"(engine={evaluation['engine_version']}, rules={evaluation['rule_version']}). "
                "Not a model accuracy claim."
            ),
        }
    )
    result["findings"] = findings
    return result


__all__ = [
    "ComplianceEngineError",
    "DEFAULT_RULE_VERSION",
    "ENGINE_VERSION",
    "apply_compliance_to_analyzer_result",
    "evaluate_compliance",
    "extract_observed_fields",
]
