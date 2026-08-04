from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
from pathlib import Path
from typing import Any, BinaryIO

import pytest
from fastapi.testclient import TestClient

from app.reference_analyzer import (
    BoundedIdempotencyCache,
    REFERENCE_STUB_ARTIFACT_RELATIVE_PATH,
    REFERENCE_STUB_ARTIFACT_SHA256,
    ReferenceAnalyzerSettings,
    create_reference_analyzer,
)
from app.services.analyzers.remote_http import RemoteAnalyzerResponse
from app.services.storage import canonical_json_bytes, sha256_bytes


TOKEN = "reference-test-token"
EVIDENCE_BYTES = b"\xff\xd8\xffreference-analyzer-evidence"
BASELINE_SHA256 = "b" * 64


def _settings(**overrides: Any) -> ReferenceAnalyzerSettings:
    defaults = ReferenceAnalyzerSettings()
    return replace(defaults, bearer_token=TOKEN, **overrides)


def _request_document(
    settings: ReferenceAnalyzerSettings,
    evidence_bytes: bytes = EVIDENCE_BYTES,
) -> dict[str, Any]:
    return {
        "contract_version": "1.0",
        "job_id": "job-1",
        "task_type": "construction_evidence_analysis",
        "model": {
            "name": settings.model_name,
            "version": settings.model_version,
            "artifact_sha256": settings.model_artifact_sha256,
        },
        "evidence": {
            "id": "evidence-1",
            "sha256": hashlib.sha256(evidence_bytes).hexdigest(),
            "size_bytes": len(evidence_bytes),
            "content_type": "image/jpeg",
            "media_probe": {},
        },
        "baseline": {
            "id": "baseline-1",
            "site_id": "SITE-1",
            "procedure_code": "PROC-1",
            "version": "design-v1",
            "source_type": "manual",
            "expected": {"scene_type": "trench", "measurements": {}},
            "sha256": BASELINE_SHA256,
        },
        "output_policy": {
            "human_review_required": True,
            "evidence_grade_controlled_by_business_service": True,
            "accuracy_claims_forbidden": True,
        },
    }


def _headers(
    request_document: Mapping[str, Any],
    *,
    token: str | None = TOKEN,
    idempotency_key: str | None = None,
) -> dict[str, str]:
    if idempotency_key is None:
        idempotency_key = sha256_bytes(
            canonical_json_bytes(
                {
                    "job_id": request_document["job_id"],
                    "evidence_sha256": request_document["evidence"]["sha256"],
                    "baseline_sha256": request_document["baseline"]["sha256"],
                    "model_sha256": request_document["model"]["artifact_sha256"],
                }
            )
        )
    headers = {
        "X-Fengmou-Contract-Version": "1.0",
        "X-Fengmou-Request-ID": str(request_document["job_id"]),
        "X-Evidence-SHA256": str(request_document["evidence"]["sha256"]),
        "X-Baseline-SHA256": str(request_document["baseline"]["sha256"]),
        "Idempotency-Key": idempotency_key,
    }
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _post(
    client: TestClient,
    request_document: dict[str, Any],
    *,
    evidence_bytes: bytes = EVIDENCE_BYTES,
    evidence_content_type: str = "image/jpeg",
    headers: dict[str, str] | None = None,
    request_bytes: bytes | None = None,
):
    return client.post(
        "/v1/analyze",
        headers=headers or _headers(request_document),
        files={
            "evidence": ("evidence-evidence-1.jpg", evidence_bytes, evidence_content_type),
            "request": (
                "request.json",
                request_bytes or json.dumps(request_document, separators=(",", ":")).encode("utf-8"),
                "application/json",
            ),
        },
    )


def _error_code(response) -> str:
    body = response.json()
    assert set(body) == {"detail"}
    assert isinstance(body["detail"], dict)
    code = body["detail"].get("code")
    assert isinstance(code, str) and code.startswith("REFERENCE_")
    return code


def _valid_prediction() -> dict[str, Any]:
    return {
        "observations": {"measurements": {}, "objects": [], "events": []},
        "alignment": {"status": "not_evaluated", "differences": []},
        "findings": [],
        "confidence": None,
        "limitations": ["reference stub only; no accuracy claim"],
    }


class CountingPredictor:
    def __init__(self, prediction: Mapping[str, Any] | None = None) -> None:
        self.calls = 0
        self.prediction = dict(prediction or _valid_prediction())

    def __call__(self, request_document, evidence_stream: BinaryIO) -> Mapping[str, Any]:
        self.calls += 1
        assert request_document.evidence.sha256 == hashlib.sha256(evidence_stream.read()).hexdigest()
        return deepcopy(self.prediction)


def test_reference_stub_artifact_digest_matches_default_model_identity() -> None:
    project_root = Path(__file__).resolve().parents[2]
    artifact_path = project_root / REFERENCE_STUB_ARTIFACT_RELATIVE_PATH

    assert artifact_path.is_file()
    actual_sha256 = hashlib.sha256(artifact_path.read_bytes()).hexdigest()
    assert actual_sha256 == REFERENCE_STUB_ARTIFACT_SHA256
    assert ReferenceAnalyzerSettings().model_artifact_sha256 == actual_sha256


def test_reference_analyzer_success_matches_remote_response_contract() -> None:
    settings = _settings()
    predictor = CountingPredictor()
    app = create_reference_analyzer(settings=settings, predictor=predictor)
    with TestClient(app) as client:
        health = client.get("/healthz")
        assert health.status_code == 200
        assert health.json()["mode"] == "stub"
        assert health.json()["model_loaded"] is False
        assert health.json()["capabilities"] == []
        response = _post(client, _request_document(settings))

    assert response.status_code == 200, response.text
    payload = RemoteAnalyzerResponse.model_validate(response.json())
    assert payload.request_id == "job-1"
    assert payload.model.name == settings.model_name
    assert payload.model.version == settings.model_version
    assert payload.model.artifact_sha256 == settings.model_artifact_sha256
    assert payload.runtime.mode == "stub"
    assert payload.runtime.model_loaded is False
    assert payload.runtime.capabilities == []
    assert predictor.calls == 1
    serialized = response.text.casefold()
    assert "evidence_grade" not in serialized
    assert "accuracy_claim" not in serialized


@pytest.mark.parametrize("token", [None, "wrong-token"])
def test_reference_analyzer_requires_dedicated_bearer_authentication(token: str | None) -> None:
    settings = _settings()
    app = create_reference_analyzer(settings=settings)
    request_document = _request_document(settings)
    with TestClient(app) as client:
        response = _post(client, request_document, headers=_headers(request_document, token=token))

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert _error_code(response) == "REFERENCE_UNAUTHORIZED"
    assert TOKEN not in response.text
    if token is not None:
        assert token not in response.text


def test_reference_analyzer_fails_closed_when_bearer_token_is_not_configured() -> None:
    settings = ReferenceAnalyzerSettings()
    app = create_reference_analyzer(settings=settings)
    request_document = _request_document(settings)
    with TestClient(app) as client:
        response = _post(client, request_document)

    assert response.status_code == 503
    assert _error_code(response) == "REFERENCE_AUTH_NOT_CONFIGURED"


@pytest.mark.parametrize(
    ("header_name", "header_value", "expected_code"),
    [
        ("X-Fengmou-Contract-Version", "2.0", "REFERENCE_CONTRACT_MISMATCH"),
        ("X-Fengmou-Request-ID", "another-job", "REFERENCE_REQUEST_ID_MISMATCH"),
        ("X-Evidence-SHA256", "0" * 64, "REFERENCE_DIGEST_HEADER_MISMATCH"),
        ("X-Baseline-SHA256", "0" * 64, "REFERENCE_DIGEST_HEADER_MISMATCH"),
    ],
)
def test_reference_analyzer_binds_headers_to_request_document(
    header_name: str,
    header_value: str,
    expected_code: str,
) -> None:
    settings = _settings()
    request_document = _request_document(settings)
    headers = _headers(request_document)
    headers[header_name] = header_value
    with TestClient(create_reference_analyzer(settings=settings)) as client:
        response = _post(client, request_document, headers=headers)

    assert response.status_code == 422
    assert _error_code(response) == expected_code


def test_reference_analyzer_requires_bridge_derived_idempotency_key() -> None:
    settings = _settings()
    request_document = _request_document(settings)
    headers = _headers(request_document, idempotency_key="d" * 64)
    with TestClient(create_reference_analyzer(settings=settings)) as client:
        response = _post(client, request_document, headers=headers)

    assert response.status_code == 422
    assert _error_code(response) == "REFERENCE_IDEMPOTENCY_KEY_MISMATCH"


@pytest.mark.parametrize(
    ("mismatch", "expected_status", "expected_code"),
    [
        ("sha256", 422, "REFERENCE_EVIDENCE_DIGEST_MISMATCH"),
        ("size", 422, "REFERENCE_EVIDENCE_DIGEST_MISMATCH"),
        ("content_type", 415, "REFERENCE_EVIDENCE_MEDIA_TYPE_INVALID"),
    ],
)
def test_reference_analyzer_binds_actual_evidence_bytes_size_and_mime(
    mismatch: str,
    expected_status: int,
    expected_code: str,
) -> None:
    settings = _settings()
    request_document = _request_document(settings)
    evidence_bytes = EVIDENCE_BYTES
    evidence_content_type = "image/jpeg"
    if mismatch == "sha256":
        evidence_bytes += b"-changed"
    elif mismatch == "size":
        request_document["evidence"]["size_bytes"] += 1
    else:
        evidence_content_type = "application/octet-stream"
    with TestClient(create_reference_analyzer(settings=settings)) as client:
        response = _post(
            client,
            request_document,
            evidence_bytes=evidence_bytes,
            evidence_content_type=evidence_content_type,
        )

    assert response.status_code == expected_status
    assert _error_code(response) == expected_code


def test_reference_analyzer_rejects_evidence_above_its_independent_limit() -> None:
    settings = _settings(max_upload_bytes=len(EVIDENCE_BYTES) - 1)
    request_document = _request_document(settings)
    with TestClient(create_reference_analyzer(settings=settings)) as client:
        response = _post(client, request_document)

    assert response.status_code == 413
    assert _error_code(response) == "REFERENCE_EVIDENCE_TOO_LARGE"


def test_reference_analyzer_rejects_request_document_above_its_limit() -> None:
    baseline_settings = _settings()
    request_document = _request_document(baseline_settings)
    request_bytes = json.dumps(request_document, separators=(",", ":")).encode("utf-8")
    settings = _settings(max_request_bytes=len(request_bytes) - 1)
    request_document = _request_document(settings)
    with TestClient(create_reference_analyzer(settings=settings)) as client:
        response = _post(client, request_document, request_bytes=request_bytes)

    assert response.status_code == 413
    assert _error_code(response) == "REFERENCE_REQUEST_TOO_LARGE"


@pytest.mark.parametrize("field", ["name", "version", "artifact_sha256"])
def test_reference_analyzer_rejects_model_identity_drift(field: str) -> None:
    settings = _settings()
    request_document = _request_document(settings)
    request_document["model"][field] = "0" * 64 if field == "artifact_sha256" else "drifted"
    with TestClient(create_reference_analyzer(settings=settings)) as client:
        response = _post(client, request_document)

    assert response.status_code == 409
    assert _error_code(response) == "REFERENCE_MODEL_IDENTITY_MISMATCH"


def test_reference_analyzer_replays_same_idempotent_result_without_running_predictor_twice() -> None:
    settings = _settings()
    predictor = CountingPredictor()
    cache = BoundedIdempotencyCache(capacity=4)
    request_document = _request_document(settings)
    with TestClient(create_reference_analyzer(settings=settings, predictor=predictor, cache=cache)) as client:
        first = _post(client, request_document)
        second = _post(client, request_document)

    assert first.status_code == second.status_code == 200
    assert first.content == second.content
    assert first.headers["x-idempotent-replay"] == "false"
    assert second.headers["x-idempotent-replay"] == "true"
    assert predictor.calls == 1
    assert len(cache) == 1


def test_reference_analyzer_rejects_idempotency_key_reuse_for_different_identity() -> None:
    settings = _settings()
    predictor = CountingPredictor()
    cache = BoundedIdempotencyCache(capacity=4)
    first_document = _request_document(settings)
    second_document = deepcopy(first_document)
    second_document["evidence"]["media_probe"] = {"format_name": "changed-but-same-bound-digests"}
    with TestClient(create_reference_analyzer(settings=settings, predictor=predictor, cache=cache)) as client:
        first = _post(client, first_document)
        second = _post(client, second_document)

    assert first.status_code == 200
    assert second.status_code == 409
    assert _error_code(second) == "REFERENCE_IDEMPOTENCY_CONFLICT"
    assert predictor.calls == 1
    assert len(cache) == 1


@pytest.mark.parametrize(
    ("injection", "expected_code"),
    [
        ("root_protected", "REFERENCE_PREDICTOR_OUTPUT_FORBIDDEN"),
        ("nested_protected", "REFERENCE_PREDICTOR_OUTPUT_FORBIDDEN"),
        ("extra", "REFERENCE_PREDICTOR_OUTPUT_INVALID"),
    ],
)
def test_reference_analyzer_rejects_untrusted_predictor_claims_and_extra_fields(
    injection: str,
    expected_code: str,
) -> None:
    settings = _settings()
    prediction = _valid_prediction()
    if injection == "root_protected":
        prediction["evidence_grade"] = True
    elif injection == "nested_protected":
        prediction["observations"]["measurements"]["accuracy_claim"] = 0.99
    else:
        prediction["unexpected"] = "must not cross the service boundary"
    predictor = CountingPredictor(prediction)
    with TestClient(
        create_reference_analyzer(settings=settings, predictor=predictor),
        raise_server_exceptions=False,
    ) as client:
        response = _post(client, _request_document(settings))

    assert response.status_code >= 400
    assert _error_code(response) == expected_code
    assert "must not cross" not in response.text
    assert "0.99" not in response.text


def test_reference_analyzer_internal_errors_do_not_leak_token_or_local_path() -> None:
    settings = _settings()
    sensitive_path = "/Users/private/reference-model/checkpoint.bin"

    def failing_predictor(_request_document, _evidence_stream: BinaryIO) -> Mapping[str, Any]:
        raise RuntimeError(f"predictor failed with {TOKEN} at {sensitive_path}")

    with TestClient(
        create_reference_analyzer(settings=settings, predictor=failing_predictor),
        raise_server_exceptions=False,
    ) as client:
        response = _post(client, _request_document(settings))

    assert response.status_code == 502
    assert TOKEN not in response.text
    assert sensitive_path not in response.text
    assert _error_code(response) == "REFERENCE_PREDICTOR_FAILED"
