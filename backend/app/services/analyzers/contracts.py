from __future__ import annotations

import re
import math
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ...models import DesignBaseline, EvidenceAsset


SHA256_PATTERN = r"^[0-9a-f]{64}$"
PROTECTED_CLAIM_KEYS = frozenset({"accuracyclaim", "evidencegrade"})


class AnalyzerOutputError(RuntimeError):
    """Raised when an adapter returns data outside the versioned business contract."""


class AnalyzerIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=100)
    version: str = Field(min_length=1, max_length=100)


class AnalyzerProvenance(BaseModel):
    model_config = ConfigDict(extra="allow")

    kind: str = Field(min_length=1, max_length=100)
    synthetic: bool
    warning: str = Field(min_length=1, max_length=1000)


class AnalyzerInput(BaseModel):
    model_config = ConfigDict(extra="allow")

    evidence_sha256: str = Field(pattern=SHA256_PATTERN)
    baseline_sha256: str = Field(pattern=SHA256_PATTERN)


class AnalyzerObservations(BaseModel):
    model_config = ConfigDict(extra="forbid")

    measurements: dict[str, Any] = Field(default_factory=dict, max_length=100)
    objects: list[dict[str, Any]] = Field(default_factory=list, max_length=1000)
    events: list[dict[str, Any]] = Field(default_factory=list, max_length=1000)


class AnalyzerAlignment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str = Field(min_length=1, max_length=100)
    baseline_version: str = Field(min_length=1, max_length=100)
    differences: list[dict[str, Any]] = Field(default_factory=list, max_length=1000)


class AnalyzerFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1, max_length=100)
    severity: Literal["info", "warning", "error", "critical"]
    message: str = Field(min_length=1, max_length=4000)


class AnalyzerResult(BaseModel):
    """The normalized result persisted by the business service.

    Accuracy is intentionally not accepted here. Competition metrics belong to a
    separate frozen EvaluationRun, not a single inference response.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"]
    analysis_mode: str = Field(min_length=1, max_length=100)
    evidence_grade: bool
    analyzer: AnalyzerIdentity
    provenance: AnalyzerProvenance
    input: AnalyzerInput
    observations: AnalyzerObservations
    alignment: AnalyzerAlignment
    findings: list[AnalyzerFinding] = Field(max_length=1000)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    accuracy_claim: None = None
    recommended_action: Literal["manual_review"]


def _validation_summary(exc: ValidationError) -> str:
    messages: list[str] = []
    for item in exc.errors(include_url=False, include_input=False):
        location = ".".join(str(part) for part in item["loc"]) or "result"
        messages.append(f"{location}: {item['msg']}")
    return "; ".join(messages)[:2000]


def protected_claim_path(value: Any, *, allow_root: bool) -> str | None:
    """Find server-controlled claim keys hidden anywhere in a nested payload.

    Pydantic's ``extra='forbid'`` closes registered object boundaries, while
    domain payloads such as measurements and differences intentionally remain
    JSON-shaped. This recursive guard prevents those extension points from
    becoming a path around the business-owned truth fields.
    """

    def walk(current: Any, path: str, depth: int) -> str | None:
        if isinstance(current, dict):
            for raw_key, nested in current.items():
                key = str(raw_key)
                normalized = re.sub(r"[^a-z0-9]", "", key.casefold())
                next_path = f"{path}.{key}"
                if normalized in PROTECTED_CLAIM_KEYS and not (allow_root and depth == 0):
                    return next_path
                found = walk(nested, next_path, depth + 1)
                if found:
                    return found
        elif isinstance(current, list):
            for index, nested in enumerate(current):
                found = walk(nested, f"{path}[{index}]", depth + 1)
                if found:
                    return found
        return None

    return walk(value, "result", 0)


def invalid_json_scalar_path(value: Any) -> str | None:
    """Return the first path containing a non-finite number or invalid Unicode scalar."""

    def walk(current: Any, path: str) -> str | None:
        if isinstance(current, float) and not math.isfinite(current):
            return path
        if isinstance(current, str):
            try:
                current.encode("utf-8", errors="strict")
            except UnicodeEncodeError:
                return path
            return None
        if isinstance(current, dict):
            for raw_key, nested in current.items():
                key = str(raw_key)
                try:
                    key.encode("utf-8", errors="strict")
                except UnicodeEncodeError:
                    return f"{path}.<invalid-key>"
                found = walk(nested, f"{path}.{key}")
                if found:
                    return found
        elif isinstance(current, list):
            for index, nested in enumerate(current):
                found = walk(nested, f"{path}[{index}]")
                if found:
                    return found
        return None

    return walk(value, "result")


def validate_analyzer_result(
    raw_result: Any,
    *,
    evidence: EvidenceAsset,
    baseline: DesignBaseline,
    expected_name: str,
    expected_version: str,
    expected_synthetic: bool,
    allow_evidence_grade: bool = False,
) -> dict[str, Any]:
    """Validate adapter output and enforce fields controlled by the server."""

    invalid_scalar_path = invalid_json_scalar_path(raw_result)
    if invalid_scalar_path:
        raise AnalyzerOutputError(
            "Analyzer output rejected: non-finite number or invalid Unicode scalar at " + invalid_scalar_path
        )

    protected_path = protected_claim_path(raw_result, allow_root=True)
    if protected_path:
        raise AnalyzerOutputError(
            f"Analyzer output rejected: protected business claim is only allowed at the result root ({protected_path})"
        )

    try:
        result = AnalyzerResult.model_validate(raw_result)
    except ValidationError as exc:
        raise AnalyzerOutputError(f"Analyzer output contract invalid: {_validation_summary(exc)}") from exc

    # Pydantic intentionally normalizes compatible containers (for example a
    # tuple into a list). Guard the normalized Python tree as well so a future
    # in-process adapter cannot bypass the pre-validation recursive checks by
    # choosing a coercible container type.
    normalized_result = result.model_dump(mode="python")
    invalid_scalar_path = invalid_json_scalar_path(normalized_result)
    if invalid_scalar_path:
        raise AnalyzerOutputError(
            "Analyzer output rejected: non-finite number or invalid Unicode scalar at " + invalid_scalar_path
        )
    protected_path = protected_claim_path(normalized_result, allow_root=True)
    if protected_path:
        raise AnalyzerOutputError(
            f"Analyzer output rejected: protected business claim is only allowed at the result root ({protected_path})"
        )

    mismatches: list[str] = []
    if result.analysis_mode != expected_name:
        mismatches.append("analysis_mode does not match the selected adapter")
    if result.analyzer.name != expected_name:
        mismatches.append("analyzer.name does not match the selected adapter")
    if result.analyzer.version != expected_version:
        mismatches.append("analyzer.version does not match the pinned job version")
    if result.input.evidence_sha256 != evidence.sha256:
        mismatches.append("input.evidence_sha256 does not match the ingested evidence")
    if result.input.baseline_sha256 != baseline.sha256:
        mismatches.append("input.baseline_sha256 does not match the bound design baseline")
    if result.provenance.synthetic is not expected_synthetic:
        mismatches.append("provenance.synthetic does not match the registered adapter mode")
    if result.evidence_grade and not allow_evidence_grade:
        mismatches.append("evidence_grade=true is forbidden without a server-side evaluation gate")
    if mismatches:
        raise AnalyzerOutputError("Analyzer output rejected: " + "; ".join(mismatches))

    return result.model_dump(mode="json")


def delivery_classification(result: dict[str, Any] | None) -> tuple[str, str]:
    """Return report status and bundle purpose without conflating real unvalidated output with a mock."""

    payload = result or {}
    if payload.get("evidence_grade") is True:
        return "final", "validation"
    mode = payload.get("analysis_mode")
    if mode == "demo_fixture":
        return "reviewed_demo", "demo"
    if mode == "stub":
        return "reviewed_placeholder", "workflow"
    return "reviewed_non_evaluated", "review"


__all__ = [
    "AnalyzerOutputError",
    "AnalyzerResult",
    "delivery_classification",
    "invalid_json_scalar_path",
    "protected_claim_path",
    "validate_analyzer_result",
]
