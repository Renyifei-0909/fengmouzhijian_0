from __future__ import annotations

from copy import deepcopy

import pytest

from app.models import DesignBaseline, EvidenceAsset
from app.services.analyzers.contracts import AnalyzerOutputError, validate_analyzer_result
from app.services.analyzers.demo_fixture import DemoFixtureAnalyzer
from app.services.analyzers.stub import StubAnalyzer


def _records() -> tuple[EvidenceAsset, DesignBaseline]:
    evidence = EvidenceAsset(
        id="evidence-1",
        project_id="project-1",
        baseline_id="baseline-1",
        original_name="sample.mp4",
        stored_name="stored.mp4",
        storage_path="/tmp/sample.mp4",
        content_type="video/mp4",
        size_bytes=123,
        sha256="a" * 64,
        metadata_json={},
    )
    baseline = DesignBaseline(
        id="baseline-1",
        project_id="project-1",
        site_id="SITE-1",
        procedure_code="PROC-1",
        version="v1",
        source_type="manual",
        expected={"scene_type": "trench", "measurements": {"min_depth_m": 0.8}},
        sha256="b" * 64,
    )
    return evidence, baseline


@pytest.mark.parametrize(
    ("adapter", "synthetic"),
    [(StubAnalyzer(), False), (DemoFixtureAnalyzer(), True)],
)
def test_current_adapters_satisfy_the_versioned_contract(adapter, synthetic: bool) -> None:
    evidence, baseline = _records()
    result = validate_analyzer_result(
        adapter.analyze(evidence, baseline),
        evidence=evidence,
        baseline=baseline,
        expected_name=adapter.name,
        expected_version=adapter.version,
        expected_synthetic=synthetic,
    )
    assert result["schema_version"] == "1.0"
    assert result["accuracy_claim"] is None
    assert result["evidence_grade"] is False


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda value: value["input"].__setitem__("evidence_sha256", "c" * 64), "evidence_sha256"),
        (lambda value: value.__setitem__("analysis_mode", "different-adapter"), "analysis_mode"),
        (lambda value: value["analyzer"].__setitem__("version", "unregistered"), "version"),
        (lambda value: value.__setitem__("evidence_grade", True), "evidence_grade"),
        (lambda value: value.__setitem__("accuracy_claim", {"accuracy": 0.99}), "accuracy_claim"),
        (lambda value: value.__setitem__("confidence", 1.5), "confidence"),
        (lambda value: value["provenance"].__setitem__("synthetic", True), "synthetic"),
    ],
)
def test_contract_rejects_untrusted_or_inconsistent_fields(mutation, message: str) -> None:
    evidence, baseline = _records()
    raw = deepcopy(StubAnalyzer().analyze(evidence, baseline))
    mutation(raw)
    with pytest.raises(AnalyzerOutputError, match=message):
        validate_analyzer_result(
            raw,
            evidence=evidence,
            baseline=baseline,
            expected_name=StubAnalyzer.name,
            expected_version=StubAnalyzer.version,
            expected_synthetic=False,
        )


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value["observations"]["measurements"].__setitem__("accuracy_claim", 0.99),
        lambda value: value["alignment"]["differences"].append({"evidence-grade": True}),
        lambda value: value["findings"][0].__setitem__("AccuracyClaim", {"accuracy": 0.99}),
        lambda value: value["provenance"].__setitem__("evidence_grade", True),
        lambda value: value["observations"].__setitem__("objects", ({"evidence_grade": True},)),
    ],
)
def test_contract_rejects_protected_claims_hidden_in_nested_extension_data(mutation) -> None:
    evidence, baseline = _records()
    raw = deepcopy(StubAnalyzer().analyze(evidence, baseline))
    mutation(raw)

    with pytest.raises(AnalyzerOutputError, match="protected business claim"):
        validate_analyzer_result(
            raw,
            evidence=evidence,
            baseline=baseline,
            expected_name=StubAnalyzer.name,
            expected_version=StubAnalyzer.version,
            expected_synthetic=False,
        )


def test_contract_rejects_unknown_fields_at_registered_nested_boundaries() -> None:
    evidence, baseline = _records()
    raw = deepcopy(StubAnalyzer().analyze(evidence, baseline))
    raw["observations"]["undeclared"] = "must fail closed"

    with pytest.raises(AnalyzerOutputError, match="undeclared"):
        validate_analyzer_result(
            raw,
            evidence=evidence,
            baseline=baseline,
            expected_name=StubAnalyzer.name,
            expected_version=StubAnalyzer.version,
            expected_synthetic=False,
        )


@pytest.mark.parametrize("invalid_value", [float("nan"), float("inf"), float("-inf"), "\ud800"])
def test_contract_rejects_non_json_scalars_in_extension_data(invalid_value) -> None:
    evidence, baseline = _records()
    raw = deepcopy(StubAnalyzer().analyze(evidence, baseline))
    raw["observations"]["measurements"]["untrusted"] = invalid_value

    with pytest.raises(AnalyzerOutputError, match="non-finite number or invalid Unicode"):
        validate_analyzer_result(
            raw,
            evidence=evidence,
            baseline=baseline,
            expected_name=StubAnalyzer.name,
            expected_version=StubAnalyzer.version,
            expected_synthetic=False,
        )


@pytest.mark.parametrize("invalid_value", [float("nan"), float("inf"), float("-inf"), "\ud800"])
def test_contract_rechecks_non_json_scalars_after_container_coercion(invalid_value) -> None:
    evidence, baseline = _records()
    raw = deepcopy(StubAnalyzer().analyze(evidence, baseline))
    raw["observations"]["objects"] = ({"untrusted": invalid_value},)

    with pytest.raises(AnalyzerOutputError, match="non-finite number or invalid Unicode"):
        validate_analyzer_result(
            raw,
            evidence=evidence,
            baseline=baseline,
            expected_name=StubAnalyzer.name,
            expected_version=StubAnalyzer.version,
            expected_synthetic=False,
        )
