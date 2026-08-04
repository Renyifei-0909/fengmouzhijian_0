from __future__ import annotations

from typing import Any

from ...models import DesignBaseline, EvidenceAsset


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None


class DemoFixtureAnalyzer:
    name = "demo_fixture"
    version = "fixture-v0.1"

    def analyze(self, evidence: EvidenceAsset, baseline: DesignBaseline) -> dict[str, Any]:
        expected = baseline.expected or {}
        expected_measurements = expected.get("measurements", {})
        seed = int(evidence.sha256[:8], 16)
        jitter = ((seed % 11) - 5) / 100

        min_depth = _number(expected_measurements.get("min_depth_m"))
        min_spacing = _number(expected_measurements.get("min_spacing_m"))
        expected_quantity = expected_measurements.get("expected_quantity")
        expected_specification = expected_measurements.get("expected_specification")

        # Work-order observation fields (AI may observe; compliance is server-side).
        pipe_rule = expected.get("visible_pipe_count")
        if isinstance(pipe_rule, dict) and "equals" in pipe_rule:
            expected_quantity = pipe_rule["equals"]
        stage_rule = expected.get("trench_stage")
        expected_stage = None
        if isinstance(stage_rule, dict) and "equals" in stage_rule:
            expected_stage = stage_rule["equals"]
        elif isinstance(stage_rule, dict) and "one_of" in stage_rule and stage_rule["one_of"]:
            expected_stage = stage_rule["one_of"][0]

        # Demo fixture intentionally does NOT claim calibrated depth metrology.
        measured_depth = None
        measured_spacing = round(min_spacing + jitter / 2, 3) if min_spacing is not None else None
        measured_quantity = int(expected_quantity) if isinstance(expected_quantity, int) else None
        trench_stage = expected_stage or "laying"
        object_visibility = "visible"
        material = expected_specification
        if isinstance(expected.get("visible_material_or_specification"), dict):
            material = expected["visible_material_or_specification"].get("equals", material)

        # Legacy alignment block is non-authoritative for work orders; server
        # compliance engine overwrites alignment when EvidenceCapture is present.
        differences: list[dict[str, Any]] = []
        if measured_quantity is not None:
            differences.append(
                {
                    "field": "quantity",
                    "expected": expected_quantity,
                    "observed": measured_quantity,
                    "compliant": True,
                    "note": "Non-authoritative fixture preview; server rule engine decides verdict.",
                }
            )

        findings = [
            {
                "code": "DEMO_FIXTURE_ONLY",
                "severity": "warning",
                "message": (
                    "All observations are deterministic synthetic fixtures and must not be "
                    "reported as model output, accuracy, or competition metrics."
                ),
            },
            {
                "code": "OBSERVATION_ONLY",
                "severity": "info",
                "message": (
                    "Adapter emits observations only; compliance verdict is owned by the "
                    "backend rule engine when a work order is bound."
                ),
            },
        ]
        if min_depth is not None:
            findings.append(
                {
                    "code": "DEPTH_NOT_CLAIMED",
                    "severity": "info",
                    "message": (
                        "min_depth_m is present in design expected fields but single-photo "
                        "depth is not emitted (calibration_missing / manual_measurement_required)."
                    ),
                }
            )

        return {
            "schema_version": "1.0",
            "analysis_mode": "demo_fixture",
            "evidence_grade": False,
            "analyzer": {"name": self.name, "version": self.version},
            "provenance": {
                "kind": "synthetic_fixture",
                "synthetic": True,
                "warning": "Not a computer-vision inference result and not valid for accuracy evaluation.",
            },
            "input": {
                "evidence_sha256": evidence.sha256,
                "baseline_sha256": baseline.sha256,
                "scene_type": expected.get("scene_type", "unspecified"),
            },
            "observations": {
                "measurements": {
                    # depth_m intentionally omitted without calibration
                    "spacing_m": measured_spacing,
                    "quantity": measured_quantity,
                    "specification": material,
                    "visible_pipe_count": measured_quantity,
                    "trench_stage": trench_stage,
                    "object_visibility": object_visibility,
                    "visible_material_or_specification": material,
                },
                "objects": [],
                "events": [],
            },
            "alignment": {
                "status": "not_evaluated",
                "baseline_version": baseline.version,
                "differences": differences,
            },
            "findings": findings,
            "confidence": None,
            "accuracy_claim": None,
            "recommended_action": "manual_review",
        }
