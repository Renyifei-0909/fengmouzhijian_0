from __future__ import annotations

from dataclasses import replace
import os
import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.models import VerificationJob
from app.services import analysis
from app.services.analyzers.remote_http import RemoteHTTPAnalyzer
from app.services.storage import FileStorage, ValidatedStoredFile


MODEL_SHA256 = "c" * 64


def _settings(tmp_path) -> Settings:
    return Settings(
        environment="test",
        database_url=f"sqlite:///{tmp_path / 'remote.db'}",
        storage_root=tmp_path / "storage",
        max_upload_bytes=2 * 1024 * 1024,
        allow_demo_analyzer=False,
        operator_api_key="test-operator-key",
        reviewer_api_key="test-reviewer-key",
        auditor_api_key="test-auditor-key",
        remote_analyzer_enabled=True,
        remote_analyzer_url="https://algorithm.example.test/v1/analyze",
        remote_analyzer_api_key="dedicated-remote-secret",
        remote_analyzer_model_name="hidden-work-baseline",
        remote_analyzer_model_version="0.1.0",
        remote_analyzer_model_sha256=MODEL_SHA256,
        remote_analyzer_timeout_seconds=10,
        remote_analyzer_max_upload_bytes=1024 * 1024,
        remote_analyzer_max_response_bytes=4096,
        cors_origins=("http://testserver",),
    )


def _project_and_baseline(client: TestClient) -> tuple[dict, dict]:
    project_response = client.post(
        "/api/v1/projects",
        json={"code": "REMOTE-001", "name": "远程算法联调", "location": "匿名工点"},
    )
    assert project_response.status_code == 201, project_response.text
    project = project_response.json()
    baseline_response = client.post(
        f"/api/v1/projects/{project['id']}/baselines",
        json={
            "site_id": "REMOTE-SITE",
            "procedure_code": "TRENCH-BEFORE-BACKFILL",
            "version": "design-v1",
            "source_type": "manual",
            "expected": {"scene_type": "trench"},
        },
    )
    assert baseline_response.status_code == 201, baseline_response.text
    return project, baseline_response.json()


def _remote_payload() -> dict:
    return {
        "contract_version": "1.0",
        "request_id": "will-be-replaced-by-handler",
        "model": {
            "name": "hidden-work-baseline",
            "version": "0.1.0",
            "artifact_sha256": MODEL_SHA256,
        },
        "runtime": {
            "mode": "model",
            "model_loaded": True,
            "capabilities": ["construction_evidence_analysis"],
        },
        "observations": {
            "measurements": {"depth_m": 0.82},
            "objects": [],
            "events": [],
        },
        "alignment": {"status": "aligned", "differences": []},
        "findings": [
            {"code": "BASELINE_OUTPUT", "severity": "info", "message": "remote baseline output"}
        ],
        "confidence": 0.7,
        "limitations": ["not evaluated on the frozen competition dataset"],
    }


def _patch_remote_builder(monkeypatch, handler) -> None:
    def build(name, *, settings, job_id, pinned_version):
        assert name == "remote_http"
        adapter = RemoteHTTPAnalyzer(
            url=settings.remote_analyzer_url,
            api_key=settings.remote_analyzer_api_key,
            model_name=settings.remote_analyzer_model_name,
            model_version=settings.remote_analyzer_model_version,
            expected_model_sha256=settings.remote_analyzer_model_sha256,
            expected_runtime_mode=settings.remote_analyzer_expected_runtime_mode,
            job_id=job_id,
            timeout_seconds=settings.remote_analyzer_timeout_seconds,
            max_upload_bytes=settings.remote_analyzer_max_upload_bytes,
            max_response_bytes=settings.remote_analyzer_max_response_bytes,
            client_factory=lambda: httpx.Client(transport=httpx.MockTransport(handler)),
        )
        assert adapter.version == pinned_version
        return adapter

    monkeypatch.setattr(analysis, "build_analyzer", build)


def _submit(client: TestClient, project: dict, baseline: dict) -> dict:
    response = client.post(
        "/api/v1/verifications",
        data={
            "project_id": project["id"],
            "baseline_id": baseline["id"],
            "analyzer": "remote_http",
        },
        files={"file": ("sample.jpg", b"\xff\xd8\xffremote-image", "image/jpeg")},
    )
    assert response.status_code == 202, response.text
    return response.json()


def test_disabled_remote_adapter_rejects_before_evidence_is_saved(
    client: TestClient,
    project_and_baseline: tuple[dict, dict],
) -> None:
    project, baseline = project_and_baseline
    response = client.post(
        "/api/v1/verifications",
        data={
            "project_id": project["id"],
            "baseline_id": baseline["id"],
            "analyzer": "remote_http",
        },
        files={"file": ("sample.jpg", b"\xff\xd8\xffremote-image", "image/jpeg")},
    )
    assert response.status_code == 403
    summary = client.get("/api/v1/dashboard/summary").json()
    assert summary["evidence_assets"] == 0
    assert summary["jobs_by_status"] == {}


def test_remote_http_result_is_reviewed_but_never_promoted_to_metric_evidence(tmp_path, monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer dedicated-remote-secret"
        payload = _remote_payload()
        payload["request_id"] = request.headers["x-fengmou-request-id"]
        return httpx.Response(200, json=payload)

    _patch_remote_builder(monkeypatch, handler)
    with TestClient(create_app(_settings(tmp_path))) as client:
        client.headers.update({"X-API-Key": "test-operator-key"})
        remote_meta = client.get("/api/v1/meta").json()["adapters"]["remote_http"]
        assert remote_meta["enabled"] is True
        assert len(remote_meta["version"]) == 64
        assert set(remote_meta["version"]) <= set("0123456789abcdef")
        assert "url" not in remote_meta and "api_key" not in remote_meta
        project, baseline = _project_and_baseline(client)
        job = _submit(client, project, baseline)
        detail = client.get(f"/api/v1/verifications/{job['id']}").json()
        assert detail["job"]["status"] == "needs_review"
        assert detail["job"]["result"]["analysis_mode"] == "remote_http"
        assert detail["job"]["result"]["evidence_grade"] is False
        assert detail["job"]["result"]["accuracy_claim"] is None

        reviewed = client.post(
            f"/api/v1/verifications/{job['id']}/review",
            json={"decision": "approve", "reviewer": "算法联调审核员", "note": "非冻结评测"},
            headers={"X-API-Key": "test-reviewer-key"},
        )
        assert reviewed.status_code == 200, reviewed.text
        outcome = reviewed.json()
        assert outcome["report"]["status"] == "reviewed_non_evaluated"
        truth_boundary = " ".join(outcome["report"]["content"]["truth_boundary"])
        assert "remote_http" in truth_boundary
        assert "one pinned remote inference response" in truth_boundary
        assert "not a frozen EvaluationRun" in truth_boundary
        assert "does not change the analyzer mode" in truth_boundary
        assert outcome["proof"]["purpose"] == "review"
        assert outcome["proof"]["evidence_grade"] is False
        integrity = client.get(f"/api/v1/proofs/{outcome['proof']['id']}/verify").json()
        assert integrity["valid"] is True


def test_explicit_test_stub_cannot_form_an_operational_case(tmp_path, monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = _remote_payload()
        payload["request_id"] = request.headers["x-fengmou-request-id"]
        payload["runtime"] = {"mode": "stub", "model_loaded": False, "capabilities": []}
        payload["findings"] = [
            {
                "code": "REFERENCE_STUB_WARNING",
                "severity": "warning",
                "message": "Contract fixture only; no model inference was performed.",
            }
        ]
        return httpx.Response(200, json=payload)

    _patch_remote_builder(monkeypatch, handler)
    settings = replace(_settings(tmp_path), remote_analyzer_expected_runtime_mode="stub")
    with TestClient(create_app(settings)) as client:
        client.headers.update({"X-API-Key": "test-operator-key"})
        project, baseline = _project_and_baseline(client)
        job = _submit(client, project, baseline)
        detail = client.get(f"/api/v1/verifications/{job['id']}").json()
        result = detail["job"]["result"]
        assert result["provenance"]["kind"] == "remote_contract_stub"
        assert result["provenance"]["synthetic"] is True
        assert result["evidence_grade"] is False
        assert result["accuracy_claim"] is None

        cases = client.get("/api/v1/finding-cases").json()
        assert len(cases) == 1
        assert cases[0]["scope"] == "demo"
        assert cases[0]["source_synthetic"] is True
        summary = client.get("/api/v1/finding-cases/summary").json()
        assert summary["demo_cases"] == 1
        assert summary["confirmed_open_operational"] == 0


def test_remote_http_failure_is_structured_and_audited_as_retryable(tmp_path, monkeypatch) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="internal upstream details are not persisted")

    captured_sources: list[ValidatedStoredFile] = []
    captured_descriptors: list[int] = []
    original_validate = FileStorage.validate_evidence_file

    def capture_source(self: FileStorage, **kwargs):
        source = original_validate(self, **kwargs)
        captured_sources.append(source)
        captured_descriptors.append(source.fileno())
        return source

    monkeypatch.setattr(FileStorage, "validate_evidence_file", capture_source)
    _patch_remote_builder(monkeypatch, handler)
    with TestClient(create_app(_settings(tmp_path))) as client:
        client.headers.update({"X-API-Key": "test-operator-key"})
        project, baseline = _project_and_baseline(client)
        job = _submit(client, project, baseline)
        detail = client.get(f"/api/v1/verifications/{job['id']}").json()
        assert detail["job"]["status"] == "failed"
        assert detail["job"]["error"].startswith("REMOTE_HTTP_ERROR:")
        assert detail["recovery"]["action"] == "retry_analysis"
        assert detail["recovery"]["retryable"] is True
        assert "internal upstream" not in detail["job"]["error"]
        events = client.get(
            "/api/v1/audit-events",
            params={"entity_type": "verification_job", "entity_id": job["id"]},
        ).json()
        failure = next(item for item in events if item["action"] == "analysis_failed")
        assert failure["payload"]["error_code"] == "REMOTE_HTTP_ERROR"
        assert failure["payload"]["retryable"] is True
        assert failure["payload"]["upstream_status"] == 503
        assert len(captured_sources) == 1
        assert captured_sources[0].descriptor is None
        with pytest.raises(OSError):
            os.fstat(captured_descriptors[0])
        with client.app.state.database.session_factory() as db:
            persisted = db.get(VerificationJob, job["id"])
            assert persisted is not None
            persisted.analyzer_version = "stale-version"
            db.commit()
        retry = client.post(f"/api/v1/verifications/{job['id']}/retry")
        assert retry.status_code == 409
        assert "configuration changed" in retry.json()["detail"]


def test_remote_http_retry_reuses_idempotency_key_and_succeeds_without_configuration_drift(
    tmp_path,
    monkeypatch,
) -> None:
    observed_idempotency_keys: list[str] = []
    observed_request_ids: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        request.read()
        observed_idempotency_keys.append(request.headers["idempotency-key"])
        observed_request_ids.append(request.headers["x-fengmou-request-id"])
        if len(observed_idempotency_keys) == 1:
            return httpx.Response(503, text="first attempt unavailable")
        payload = _remote_payload()
        payload["request_id"] = request.headers["x-fengmou-request-id"]
        return httpx.Response(200, json=payload)

    _patch_remote_builder(monkeypatch, handler)
    with TestClient(create_app(_settings(tmp_path))) as client:
        client.headers.update({"X-API-Key": "test-operator-key"})
        project, baseline = _project_and_baseline(client)
        job = _submit(client, project, baseline)

        failed = client.get(f"/api/v1/verifications/{job['id']}").json()
        assert failed["job"]["status"] == "failed"
        assert failed["job"]["error"].startswith("REMOTE_HTTP_ERROR:")

        retried = client.post(f"/api/v1/verifications/{job['id']}/retry")
        assert retried.status_code == 200, retried.text
        completed = client.get(f"/api/v1/verifications/{job['id']}").json()
        assert completed["job"]["status"] == "needs_review"
        assert completed["job"]["result"]["analysis_mode"] == "remote_http"

        events = client.get(
            "/api/v1/audit-events",
            params={"entity_type": "verification_job", "entity_id": job["id"]},
        ).json()
        assert [item["action"] for item in events].count("retry_queued") == 1
        assert [item["action"] for item in events].count("analysis_failed") == 1
        assert [item["action"] for item in events].count("analysis_completed") == 1

    assert observed_request_ids == [job["id"], job["id"]]
    assert len(observed_idempotency_keys) == 2
    assert observed_idempotency_keys[0] == observed_idempotency_keys[1]
    assert len(observed_idempotency_keys[0]) == 64
