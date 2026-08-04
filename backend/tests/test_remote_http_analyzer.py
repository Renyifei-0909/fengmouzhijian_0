from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
from collections.abc import Iterator
from threading import Barrier, Lock

import httpx
import pytest

from app.models import DesignBaseline, EvidenceAsset
from app.services.analyzers.base import bind_validated_evidence_source
from app.services.analyzers.remote_http import RemoteAnalyzerError, RemoteHTTPAnalyzer, remote_adapter_version
from app.services.storage import ValidatedStoredFile


def _records(tmp_path: Path) -> tuple[EvidenceAsset, DesignBaseline]:
    path = tmp_path / "sample.jpg"
    evidence_bytes = b"sample-evidence-bytes"
    path.write_bytes(evidence_bytes)
    evidence = EvidenceAsset(
        id="evidence-1",
        project_id="project-1",
        baseline_id="baseline-1",
        original_name="sample.jpg",
        stored_name="sample.jpg",
        storage_path=str(path),
        content_type="image/jpeg",
        size_bytes=path.stat().st_size,
        sha256=hashlib.sha256(evidence_bytes).hexdigest(),
        metadata_json={},
    )
    baseline = DesignBaseline(
        id="baseline-1",
        project_id="project-1",
        site_id="SITE-1",
        procedure_code="PROC-1",
        version="design-v1",
        source_type="manual",
        expected={"scene_type": "trench"},
        sha256="b" * 64,
    )
    return evidence, baseline


def _response_payload(**updates) -> dict:
    payload = {
        "contract_version": "1.0",
        "request_id": "job-1",
        "model": {
            "name": "hidden-work-baseline",
            "version": "0.1.0",
            "artifact_sha256": "c" * 64,
        },
        "runtime": {
            "mode": "model",
            "model_loaded": True,
            "capabilities": ["construction_evidence_analysis"],
        },
        "observations": {
            "measurements": {"depth_m": 0.81},
            "objects": [],
            "events": [],
        },
        "alignment": {"status": "aligned", "differences": []},
        "findings": [
            {"code": "REMOTE_BASELINE_RESULT", "severity": "info", "message": "baseline output"}
        ],
        "confidence": 0.72,
        "limitations": ["small internal validation set"],
    }
    payload.update(updates)
    return payload


def _analyzer(handler, **overrides) -> RemoteHTTPAnalyzer:
    defaults = {
        "url": "https://algorithm.example.test/v1/analyze",
        "api_key": "remote-secret",
        "model_name": "hidden-work-baseline",
        "model_version": "0.1.0",
        "expected_model_sha256": "c" * 64,
        "job_id": "job-1",
        "timeout_seconds": 10,
        "max_upload_bytes": 1024,
        "max_response_bytes": 4096,
        "client_factory": lambda: httpx.Client(transport=httpx.MockTransport(handler)),
    }
    defaults.update(overrides)
    return RemoteHTTPAnalyzer(**defaults)


@contextmanager
def _bound_source(evidence: EvidenceAsset) -> Iterator[ValidatedStoredFile]:
    path = Path(evidence.storage_path)
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_BINARY", 0))
    actual_sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
    source = ValidatedStoredFile(
        path=path,
        stat_result=os.fstat(descriptor),
        content_type=evidence.content_type,
        descriptor=descriptor,
        stored_name=evidence.stored_name,
        sha256=actual_sha256,
    )
    with source, bind_validated_evidence_source(source):
        yield source


def _run_analyzer(
    analyzer: RemoteHTTPAnalyzer,
    evidence: EvidenceAsset,
    baseline: DesignBaseline,
) -> dict:
    with _bound_source(evidence):
        return analyzer.analyze(evidence, baseline)


def test_remote_bridge_sends_bounded_multipart_and_normalizes_untrusted_result(tmp_path: Path) -> None:
    evidence, baseline = _records(tmp_path)

    def handler(request: httpx.Request) -> httpx.Response:
        body = request.read()
        assert request.headers["authorization"] == "Bearer remote-secret"
        assert request.headers["x-fengmou-request-id"] == "job-1"
        assert len(request.headers["idempotency-key"]) == 64
        assert b'"job_id":"job-1"' in body
        assert b"sample-evidence-bytes" in body
        return httpx.Response(200, json=_response_payload())

    result = _run_analyzer(_analyzer(handler), evidence, baseline)
    assert result["analysis_mode"] == "remote_http"
    assert result["evidence_grade"] is False
    assert result["accuracy_claim"] is None
    assert result["input"]["evidence_sha256"] == evidence.sha256
    assert result["alignment"]["baseline_version"] == baseline.version
    assert result["provenance"]["model"]["artifact_sha256"] == "c" * 64
    assert len(result["provenance"]["request_sha256"]) == 64
    assert len(result["provenance"]["response_sha256"]) == 64


@pytest.mark.parametrize("protected_field", ["evidence_grade", "accuracy_claim"])
def test_remote_service_cannot_inject_protected_business_claims(
    tmp_path: Path,
    protected_field: str,
) -> None:
    evidence, baseline = _records(tmp_path)
    payload = _response_payload(**{protected_field: True})

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    with pytest.raises(RemoteAnalyzerError, match=protected_field):
        _run_analyzer(_analyzer(handler), evidence, baseline)


@pytest.mark.parametrize(
    "inject",
    [
        lambda payload: payload["observations"]["measurements"].__setitem__("accuracy_claim", 0.99),
        lambda payload: payload["alignment"]["differences"].append({"EvidenceGrade": True}),
        lambda payload: payload["findings"][0].__setitem__("accuracy-claim", {"accuracy": 0.99}),
    ],
)
def test_remote_service_cannot_hide_protected_claims_in_nested_result(
    tmp_path: Path,
    inject,
) -> None:
    evidence, baseline = _records(tmp_path)
    payload = _response_payload()
    inject(payload)

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    with pytest.raises(RemoteAnalyzerError, match="protected business claim"):
        _run_analyzer(_analyzer(handler), evidence, baseline)


def test_remote_bridge_rejects_model_identity_drift(tmp_path: Path) -> None:
    evidence, baseline = _records(tmp_path)
    payload = _response_payload()
    payload["model"]["version"] = "changed-after-queue"

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    with pytest.raises(RemoteAnalyzerError, match="model identity"):
        _run_analyzer(_analyzer(handler), evidence, baseline)


def test_remote_bridge_rejects_request_identity_drift(tmp_path: Path) -> None:
    evidence, baseline = _records(tmp_path)
    payload = _response_payload(request_id="different-job")

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    with pytest.raises(RemoteAnalyzerError, match="request_id"):
        _run_analyzer(_analyzer(handler), evidence, baseline)


def test_remote_bridge_rejects_stub_when_pinned_for_a_real_model(tmp_path: Path) -> None:
    evidence, baseline = _records(tmp_path)
    payload = _response_payload()
    payload["runtime"] = {"mode": "stub", "model_loaded": False, "capabilities": []}

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    with pytest.raises(RemoteAnalyzerError, match="runtime mode") as captured:
        _run_analyzer(_analyzer(handler), evidence, baseline)
    assert captured.value.code == "REMOTE_RUNTIME_MODE_MISMATCH"


@pytest.mark.parametrize(
    "runtime",
    [
        {"mode": "model", "model_loaded": False, "capabilities": ["ppe_detection"]},
        {"mode": "model", "model_loaded": True, "capabilities": []},
        {"mode": "stub", "model_loaded": True, "capabilities": []},
        {"mode": "stub", "model_loaded": False, "capabilities": ["ppe_detection"]},
        {"mode": "model", "model_loaded": 1, "capabilities": ["ppe_detection"]},
        {"mode": "model", "model_loaded": True, "capabilities": ["ppe_detection", "ppe_detection"]},
    ],
)
def test_remote_bridge_rejects_inconsistent_runtime_identity(tmp_path: Path, runtime: dict) -> None:
    evidence, baseline = _records(tmp_path)
    payload = _response_payload()
    payload["runtime"] = runtime

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    with pytest.raises(RemoteAnalyzerError) as captured:
        _run_analyzer(_analyzer(handler), evidence, baseline)
    assert captured.value.code == "REMOTE_RESPONSE_INVALID"


def test_explicit_stub_mode_is_normalized_as_synthetic_demo_output(tmp_path: Path) -> None:
    evidence, baseline = _records(tmp_path)
    payload = _response_payload()
    payload["runtime"] = {"mode": "stub", "model_loaded": False, "capabilities": []}

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    result = _run_analyzer(_analyzer(handler, expected_runtime_mode="stub"), evidence, baseline)
    assert result["provenance"]["kind"] == "remote_contract_stub"
    assert result["provenance"]["synthetic"] is True
    assert result["provenance"]["runtime"] == payload["runtime"]
    assert result["evidence_grade"] is False
    assert result["accuracy_claim"] is None


def test_remote_upload_limit_fails_before_network_call(tmp_path: Path) -> None:
    evidence, baseline = _records(tmp_path)
    called = False

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, json=_response_payload())

    with pytest.raises(RemoteAnalyzerError, match="upload limit"):
        _run_analyzer(_analyzer(handler, max_upload_bytes=1), evidence, baseline)
    assert called is False


def test_remote_baseline_egress_is_allowlisted_before_network_call(tmp_path: Path) -> None:
    evidence, baseline = _records(tmp_path)
    baseline.expected["private_note"] = "must never leave the service"

    def handler(_: httpx.Request) -> httpx.Response:
        raise AssertionError("network must not be called for an unsupported baseline field")

    with pytest.raises(RemoteAnalyzerError, match="allowlisted contract") as captured:
        _run_analyzer(_analyzer(handler), evidence, baseline)
    assert captured.value.code == "REMOTE_REQUEST_INVALID"


def test_remote_baseline_egress_bounds_allowlisted_strings_before_network_call(tmp_path: Path) -> None:
    evidence, baseline = _records(tmp_path)
    baseline.expected = {"scene_type": "x" * 101}

    def handler(_: httpx.Request) -> httpx.Response:
        raise AssertionError("network must not be called for oversized metadata")

    with pytest.raises(RemoteAnalyzerError, match="scene_type") as captured:
        _run_analyzer(_analyzer(handler), evidence, baseline)
    assert captured.value.code == "REMOTE_REQUEST_INVALID"


def test_remote_bridge_rechecks_evidence_digest_before_data_leaves_the_service(tmp_path: Path) -> None:
    evidence, baseline = _records(tmp_path)
    Path(evidence.storage_path).write_bytes(b"changed-after-ingestion")

    def handler(_: httpx.Request) -> httpx.Response:
        raise AssertionError("network must not be called for changed evidence")

    with pytest.raises(RemoteAnalyzerError, match="HASH_MISMATCH"):
        _run_analyzer(_analyzer(handler), evidence, baseline)


def test_remote_bridge_requires_an_orchestrator_validated_open_source(tmp_path: Path) -> None:
    evidence, baseline = _records(tmp_path)
    called = False

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, json=_response_payload())

    with pytest.raises(RemoteAnalyzerError, match="REMOTE_INPUT_MISSING"):
        _analyzer(handler).analyze(evidence, baseline)
    assert called is False


def test_remote_bridge_rejects_a_closed_validated_source(tmp_path: Path) -> None:
    evidence, baseline = _records(tmp_path)
    with _bound_source(evidence) as source:
        source.close()
        with pytest.raises(RemoteAnalyzerError, match="REMOTE_INPUT_MISSING"):
            _analyzer(lambda _: httpx.Response(200)).analyze(evidence, baseline)


def test_remote_bridge_streams_the_validated_fd_after_path_replacement(tmp_path: Path) -> None:
    evidence, baseline = _records(tmp_path)
    original_bytes = Path(evidence.storage_path).read_bytes()
    replacement_bytes = b"replacement-bytes-must-not-leave"
    observed_body = b""

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal observed_body
        observed_body = request.read()
        return httpx.Response(200, json=_response_payload())

    with _bound_source(evidence) as source:
        path = Path(evidence.storage_path)
        replacement = tmp_path / "replacement.jpg"
        replacement.write_bytes(replacement_bytes)
        path.unlink()
        replacement.replace(path)
        result = _analyzer(handler).analyze(evidence, baseline)
        assert source.descriptor is not None

    assert result["analysis_mode"] == "remote_http"
    assert original_bytes in observed_body
    assert replacement_bytes not in observed_body
    assert source.descriptor is None


def test_remote_bridge_isolates_bound_fds_across_concurrent_threads(tmp_path: Path) -> None:
    bound_sources = Barrier(2)
    active_requests = Barrier(2)
    observed_bodies: dict[str, bytes] = {}
    observed_lock = Lock()

    def worker(index: int) -> tuple[str, bytes, dict, int | None]:
        worker_root = tmp_path / f"worker-{index}"
        worker_root.mkdir()
        evidence, baseline = _records(worker_root)
        evidence_bytes = b"\xff\xd8\xffconcurrent-evidence-" + str(index).encode("ascii") + b"-payload"
        evidence_path = Path(evidence.storage_path)
        evidence_path.write_bytes(evidence_bytes)
        evidence.id = f"evidence-{index}"
        evidence.size_bytes = len(evidence_bytes)
        evidence.sha256 = hashlib.sha256(evidence_bytes).hexdigest()
        job_id = f"job-{index}"

        def handler(request: httpx.Request) -> httpx.Response:
            active_requests.wait(timeout=10)
            body = request.read()
            with observed_lock:
                observed_bodies[job_id] = body
            return httpx.Response(200, json=_response_payload(request_id=job_id))

        analyzer = _analyzer(handler, job_id=job_id)
        with _bound_source(evidence) as source:
            bound_sources.wait(timeout=10)
            result = analyzer.analyze(evidence, baseline)
            assert source.descriptor is not None
        return job_id, evidence_bytes, result, source.descriptor

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(worker, index) for index in (1, 2)]
        results = [future.result(timeout=15) for future in futures]

    expected_payloads = {job_id: evidence_bytes for job_id, evidence_bytes, _, _ in results}
    assert set(observed_bodies) == set(expected_payloads)
    for job_id, evidence_bytes, result, closed_descriptor in results:
        body = observed_bodies[job_id]
        assert body.count(evidence_bytes) == 1
        assert all(other not in body for other_id, other in expected_payloads.items() if other_id != job_id)
        assert result["input"]["evidence_sha256"] == hashlib.sha256(evidence_bytes).hexdigest()
        assert closed_descriptor is None


def test_remote_transport_timeout_is_retryable_without_leaking_credentials(tmp_path: Path) -> None:
    evidence, baseline = _records(tmp_path)

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("secret remote-secret", request=request)

    with _bound_source(evidence) as source:
        with pytest.raises(RemoteAnalyzerError, match="REMOTE_TIMEOUT") as captured:
            _analyzer(handler).analyze(evidence, baseline)
        assert source.descriptor is not None
    assert source.descriptor is None
    assert captured.value.retryable is True
    assert "remote-secret" not in str(captured.value)


def test_remote_client_factory_transport_failure_closes_bound_descriptor(tmp_path: Path) -> None:
    evidence, baseline = _records(tmp_path)

    def failing_client_factory() -> httpx.Client:
        raise httpx.ConnectError("transport initialization failed")

    analyzer = _analyzer(
        lambda _: httpx.Response(200),
        client_factory=failing_client_factory,
    )
    with _bound_source(evidence) as source:
        with pytest.raises(RemoteAnalyzerError, match="REMOTE_TRANSPORT_ERROR") as captured:
            analyzer.analyze(evidence, baseline)
        assert source.descriptor is not None
    assert source.descriptor is None
    assert captured.value.retryable is True


@pytest.mark.parametrize(
    ("response", "message"),
    [
        (httpx.Response(503, text="temporarily unavailable"), "HTTP 503"),
        (httpx.Response(200, content=b"{}", headers={"Content-Type": "text/plain"}), "application/json"),
        (httpx.Response(200, content=b"not-json", headers={"Content-Type": "application/json"}), "not valid JSON"),
        (httpx.Response(200, content=b"x" * 100, headers={"Content-Type": "application/json"}), "exceeds 10 bytes"),
    ],
)
def test_remote_failures_are_bounded_and_explainable(
    tmp_path: Path,
    response: httpx.Response,
    message: str,
) -> None:
    evidence, baseline = _records(tmp_path)

    def handler(_: httpx.Request) -> httpx.Response:
        return response

    overrides = {"max_response_bytes": 10} if "exceeds" in message else {}
    with pytest.raises(RemoteAnalyzerError, match=message):
        _run_analyzer(_analyzer(handler, **overrides), evidence, baseline)


def test_remote_url_rejects_embedded_credentials_or_query_tokens() -> None:
    for url in (
        "https://user:secret@example.test/analyze",
        "https://example.test/analyze?token=secret",
        "file:///tmp/model",
    ):
        with pytest.raises(ValueError):
            _analyzer(lambda _: httpx.Response(200), url=url)


def test_remote_configuration_fingerprint_pins_endpoint_and_full_model_digest() -> None:
    base = remote_adapter_version("https://one.example.test/analyze", "model", "v1", "a" * 64)
    changed_endpoint = remote_adapter_version("https://two.example.test/analyze", "model", "v1", "a" * 64)
    changed_digest_suffix = remote_adapter_version(
        "https://one.example.test/analyze",
        "model",
        "v1",
        "a" * 12 + "b" * 52,
    )
    changed_runtime_mode = remote_adapter_version(
        "https://one.example.test/analyze",
        "model",
        "v1",
        "a" * 64,
        "stub",
    )

    assert len(base) == 64
    assert base != changed_endpoint
    assert base != changed_digest_suffix
    assert base != changed_runtime_mode


@pytest.mark.parametrize("invalid_value", [float("nan"), float("inf"), float("-inf"), "\ud800"])
def test_remote_bridge_rejects_non_standard_json_scalars(tmp_path: Path, invalid_value) -> None:
    evidence, baseline = _records(tmp_path)
    payload = _response_payload()
    payload["observations"]["measurements"]["untrusted"] = invalid_value
    response_bytes = json.dumps(payload, allow_nan=True).encode("utf-8")

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=response_bytes, headers={"Content-Type": "application/json"})

    with pytest.raises(RemoteAnalyzerError, match="not valid JSON|invalid Unicode"):
        _run_analyzer(_analyzer(handler), evidence, baseline)


def test_remote_idempotency_key_is_stable_for_the_same_pinned_job(tmp_path: Path) -> None:
    evidence, baseline = _records(tmp_path)
    observed_keys: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        observed_keys.append(request.headers["idempotency-key"])
        return httpx.Response(200, json=_response_payload())

    analyzer = _analyzer(handler)
    _run_analyzer(analyzer, evidence, baseline)
    _run_analyzer(analyzer, evidence, baseline)
    assert len(observed_keys) == 2
    assert observed_keys[0] == observed_keys[1]
