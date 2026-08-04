from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import html
import json
from pathlib import Path
import subprocess
import sys
from threading import Barrier, Lock
import zipfile

from fastapi.testclient import TestClient

import app.api.router as router_module
from app.models import AuditEvent, EvidenceAsset, ProofRecord, StructuredReport, VerificationJob
from app.services.reporting import _report_truth_boundary


def _submit_verification(
    client: TestClient,
    project: dict,
    baseline: dict,
    *,
    analyzer: str,
    video_bytes: bytes,
) -> str:
    response = client.post(
        "/api/v1/verifications",
        data={
            "project_id": project["id"],
            "baseline_id": baseline["id"],
            "analyzer": analyzer,
            "device_id": "CAM-TEST-01",
            "metadata": '{"source":"pytest","privacy":"synthetic"}',
        },
        files={"file": ("sample.mp4", video_bytes, "video/mp4")},
    )
    assert response.status_code == 202, response.text
    return response.json()["id"]


def test_stub_pipeline_makes_no_physical_claims(client: TestClient, project_and_baseline: tuple[dict, dict], valid_mp4_bytes: bytes) -> None:
    project, baseline = project_and_baseline
    job_id = _submit_verification(client, project, baseline, analyzer="stub", video_bytes=valid_mp4_bytes)
    detail = client.get(f"/api/v1/verifications/{job_id}")
    assert detail.status_code == 200
    job = detail.json()["job"]
    assert job["status"] == "needs_review"
    assert job["result"]["analysis_mode"] == "stub"
    assert job["result"]["accuracy_claim"] is None
    assert job["result"]["observations"]["measurements"] == {}
    assert detail.json()["evidence"]["sha256"]


def test_full_demo_fixture_review_report_and_integrity(
    client: TestClient,
    project_and_baseline: tuple[dict, dict],
    valid_mp4_bytes: bytes,
) -> None:
    project, baseline = project_and_baseline
    sensor = client.post(
        "/api/v1/sensor-events",
        json={
            "project_id": project["id"],
            "site_id": baseline["site_id"],
            "device_id": "DEPTH-SENSOR-01",
            "kind": "water_level",
            "value": 0.03,
            "unit": "m",
            "captured_at": "2026-07-10T08:00:00Z",
            "metadata": {"source": "synthetic-test"},
        },
    )
    assert sensor.status_code == 201, sensor.text

    job_id = _submit_verification(client, project, baseline, analyzer="demo_fixture", video_bytes=valid_mp4_bytes)
    detail = client.get(f"/api/v1/verifications/{job_id}").json()
    assert detail["job"]["status"] == "needs_review"
    assert detail["job"]["result"]["provenance"]["synthetic"] is True
    assert detail["job"]["result"]["accuracy_claim"] is None

    reviewed = client.post(
        f"/api/v1/verifications/{job_id}/review",
        json={
            "decision": "approve",
            "reviewer": "测试审核员",
            "note": "仅批准端到端功能测试，不认可为模型实验结果。",
        },
        headers={"X-API-Key": "test-reviewer-key"},
    )
    assert reviewed.status_code == 200, reviewed.text
    outcome = reviewed.json()
    assert outcome["job"]["status"] == "approved"
    assert outcome["report"]["content"]["analysis"]["analysis_mode"] == "demo_fixture"
    truth_boundary = " ".join(outcome["report"]["content"]["truth_boundary"])
    assert "demo_fixture" in truth_boundary
    assert "deterministic synthetic output" in truth_boundary
    assert "does not change the analyzer mode" in truth_boundary
    assert len(outcome["report"]["content"]["related_sensor_events"]) == 1
    assert len(outcome["proof"]["archive_sha256"]) == 64
    assert len(outcome["proof"]["record_hash"]) == 64

    proof_id = outcome["proof"]["id"]
    integrity = client.get(f"/api/v1/proofs/{proof_id}/verify")
    assert integrity.status_code == 200, integrity.text
    assert integrity.json()["valid"] is True
    assert all(integrity.json()["checks"].values())

    archive = client.get(f"/api/v1/proofs/{proof_id}/archive")
    assert archive.status_code == 200
    assert archive.headers["content-type"].startswith("application/zip")
    assert client.get("/api/v1/reports").json()[0]["sha256"]

    progress = client.get(f"/api/v1/projects/{project['id']}/progress")
    assert progress.status_code == 200
    assert progress.json()["completion_rate"] == 100.0


def test_report_truth_boundary_covers_stub_unknown_and_evaluation_evidence_modes() -> None:
    cases = [
        ({"analysis_mode": "stub", "evidence_grade": False}, "safe workflow placeholder"),
        ({"analysis_mode": "future_adapter", "evidence_grade": False}, "not marked as evaluation evidence"),
        ({"analysis_mode": "evaluated_model", "evidence_grade": True}, "server-controlled frozen EvaluationRun"),
    ]

    for result, expected_boundary in cases:
        boundaries = _report_truth_boundary(result)
        joined = " ".join(boundaries)
        assert expected_boundary in joined
        assert "Human approval" in joined
        assert "does not change the analyzer mode" in joined


def test_report_html_escapes_untrusted_project_and_review_text(
    client: TestClient,
    valid_mp4_bytes: bytes,
) -> None:
    malicious_name = "<script>alert('project')</script>"
    malicious_location = "</pre><img src=x onerror=alert(7)>"
    malicious_reviewer = "<svg onload=alert(8)>"
    malicious_note = "</pre><script>alert(9)</script>"
    project_response = client.post(
        "/api/v1/projects",
        json={
            "code": "HTML-001",
            "name": malicious_name,
            "location": malicious_location,
            "manager": "HTML safety test",
        },
    )
    assert project_response.status_code == 201, project_response.text
    project = project_response.json()
    baseline_response = client.post(
        f"/api/v1/projects/{project['id']}/baselines",
        json={
            "site_id": "HTML-SITE",
            "procedure_code": "HTML-REPORT",
            "version": "design-v1",
            "source_type": "manual",
            "expected": {"scene_type": "test"},
        },
    )
    assert baseline_response.status_code == 201, baseline_response.text
    job_id = _submit_verification(
        client,
        project,
        baseline_response.json(),
        analyzer="stub",
        video_bytes=valid_mp4_bytes,
    )
    reviewed = client.post(
        f"/api/v1/verifications/{job_id}/review",
        json={"decision": "approve", "reviewer": malicious_reviewer, "note": malicious_note},
        headers={"X-API-Key": "test-reviewer-key"},
    )
    assert reviewed.status_code == 200, reviewed.text
    content = reviewed.json()["report"]["content"]
    assert content["project"]["name"] == malicious_name
    assert content["project"]["location"] == malicious_location
    assert content["human_review"]["reviewer"] == "api-key:reviewer"
    assert malicious_reviewer in content["human_review"]["note"]
    assert malicious_note in content["human_review"]["note"]
    assert "safe workflow placeholder" in " ".join(content["truth_boundary"])

    report_id = reviewed.json()["report"]["id"]
    with client.app.state.database.session_factory() as db:
        report = db.get(StructuredReport, report_id)
        assert report is not None
        html_document = Path(report.html_path).read_text(encoding="utf-8")

    for untrusted in (malicious_name, malicious_location, malicious_reviewer, malicious_note):
        assert untrusted not in html_document
        assert html.escape(untrusted) in html_document
    assert "<script" not in html_document.casefold()
    assert "<img" not in html_document.casefold()
    assert "<svg" not in html_document.casefold()
    assert html_document.count("</pre>") == 1


def test_tampered_archive_fails_verification(client: TestClient, project_and_baseline: tuple[dict, dict], valid_mp4_bytes: bytes) -> None:
    project, baseline = project_and_baseline
    job_id = _submit_verification(client, project, baseline, analyzer="demo_fixture", video_bytes=valid_mp4_bytes)
    reviewed = client.post(
        f"/api/v1/verifications/{job_id}/review",
        json={"decision": "approve", "reviewer": "审核员", "note": "篡改检测测试"},
        headers={"X-API-Key": "test-reviewer-key"},
    )
    assert reviewed.status_code == 200, reviewed.text
    proof_id = reviewed.json()["proof"]["id"]

    with client.app.state.database.session_factory() as db:
        proof = db.get(ProofRecord, proof_id)
        assert proof is not None
        archive_path = Path(proof.archive_path)
    with archive_path.open("ab") as handle:
        handle.write(b"tampered")

    integrity = client.get(f"/api/v1/proofs/{proof_id}/verify")
    assert integrity.status_code == 200
    body = integrity.json()
    assert body["valid"] is False
    assert body["checks"]["archive_sha256"] is False
    assert client.get(f"/api/v1/proofs/{proof_id}/archive").status_code == 409


def test_invalid_upload_type_is_rejected(client: TestClient, project_and_baseline: tuple[dict, dict]) -> None:
    project, baseline = project_and_baseline
    response = client.post(
        "/api/v1/verifications",
        data={"project_id": project["id"], "baseline_id": baseline["id"], "analyzer": "stub"},
        files={"file": ("payload.exe", b"not-media", "application/octet-stream")},
    )
    assert response.status_code == 415

    disguised = client.post(
        "/api/v1/verifications",
        data={"project_id": project["id"], "baseline_id": baseline["id"], "analyzer": "stub"},
        files={"file": ("fake.mp4", b"MZ-not-a-video", "video/mp4")},
    )
    assert disguised.status_code == 415

    fake_ftyp = client.post(
        "/api/v1/verifications",
        data={"project_id": project["id"], "baseline_id": baseline["id"], "analyzer": "stub"},
        files={"file": ("fake.mp4", b"\x00\x00\x00\x18ftypmp42-not-a-parseable-video", "video/mp4")},
    )
    assert fake_ftyp.status_code == 422


def test_media_extension_and_declared_content_type_must_match(
    client: TestClient,
    project_and_baseline: tuple[dict, dict],
) -> None:
    project, baseline = project_and_baseline
    cases = [
        ("looks-like-video.mp4", b"\x00\x00\x00\x18ftypmp42", "image/jpeg"),
        ("looks-like-image.jpg", b"\xff\xd8\xffpayload", "video/mp4"),
    ]
    for filename, content, declared_type in cases:
        response = client.post(
            "/api/v1/verifications",
            data={"project_id": project["id"], "baseline_id": baseline["id"], "analyzer": "stub"},
            files={"file": (filename, content, declared_type)},
        )
        assert response.status_code == 415
        assert "does not match" in response.json()["detail"]


def test_failed_job_can_be_explicitly_retried(client: TestClient, project_and_baseline: tuple[dict, dict], valid_mp4_bytes: bytes) -> None:
    project, baseline = project_and_baseline
    job_id = _submit_verification(client, project, baseline, analyzer="stub", video_bytes=valid_mp4_bytes)
    with client.app.state.database.session_factory() as db:
        job = db.get(VerificationJob, job_id)
        assert job is not None
        job.status = "failed"
        job.error_message = "simulated worker failure"
        db.add(
            AuditEvent(
                entity_type="verification_job",
                entity_id=job_id,
                action="analysis_failed",
                payload_json={"error_code": "SIMULATED_TRANSIENT", "retryable": True},
            )
        )
        db.commit()

    retried = client.post(f"/api/v1/verifications/{job_id}/retry")
    assert retried.status_code == 200, retried.text
    detail = client.get(f"/api/v1/verifications/{job_id}").json()
    assert detail["job"]["status"] == "needs_review"
    actions = client.get(
        "/api/v1/audit-events",
        params={"entity_type": "verification_job", "entity_id": job_id},
    ).json()
    assert "retry_queued" in [item["action"] for item in actions]

    summary = client.get("/api/v1/dashboard/summary")
    assert summary.status_code == 200
    assert summary.json()["projects"] == 1
    assert summary.json()["jobs_by_status"]["needs_review"] == 1


def test_nonretryable_failure_is_exposed_and_blocked(
    client: TestClient,
    project_and_baseline: tuple[dict, dict],
    valid_mp4_bytes: bytes,
) -> None:
    project, baseline = project_and_baseline
    job_id = _submit_verification(client, project, baseline, analyzer="stub", video_bytes=valid_mp4_bytes)
    with client.app.state.database.session_factory() as db:
        job = db.get(VerificationJob, job_id)
        assert job is not None
        job.status = "failed"
        job.error_message = "deterministic input integrity failure"
        db.add(
            AuditEvent(
                entity_type="verification_job",
                entity_id=job_id,
                action="analysis_failed",
                payload_json={"error_code": "INPUT_INTEGRITY_FAILURE", "retryable": False},
            )
        )
        db.commit()

    detail = client.get(f"/api/v1/verifications/{job_id}")
    assert detail.status_code == 200, detail.text
    recovery = detail.json()["recovery"]
    assert recovery["action"] == "retry_analysis"
    assert recovery["retryable"] is False
    assert "non-retryable" in recovery["reason"]
    retry = client.post(f"/api/v1/verifications/{job_id}/retry")
    assert retry.status_code == 409
    assert "not classified as safely retryable" in retry.json()["detail"]


def test_retry_with_unknown_persisted_analyzer_fails_as_conflict(
    client: TestClient,
    project_and_baseline: tuple[dict, dict],
    valid_mp4_bytes: bytes,
) -> None:
    project, baseline = project_and_baseline
    job_id = _submit_verification(client, project, baseline, analyzer="stub", video_bytes=valid_mp4_bytes)
    with client.app.state.database.session_factory() as db:
        job = db.get(VerificationJob, job_id)
        assert job is not None
        job.status = "failed"
        job.analyzer_name = "removed-adapter"
        job.error_message = "transient failure recorded before adapter removal"
        db.add(
            AuditEvent(
                entity_type="verification_job",
                entity_id=job_id,
                action="analysis_failed",
                payload_json={"error_code": "TRANSIENT", "retryable": True},
            )
        )
        db.commit()

    detail = client.get(f"/api/v1/verifications/{job_id}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["recovery"]["retryable"] is False
    retry = client.post(f"/api/v1/verifications/{job_id}/retry")
    assert retry.status_code == 409
    assert "unknown or invalid" in retry.json()["detail"]


def test_concurrent_retry_requests_schedule_only_once(
    client: TestClient,
    project_and_baseline: tuple[dict, dict],
    valid_mp4_bytes: bytes,
    monkeypatch,
) -> None:
    project, baseline = project_and_baseline
    job_id = _submit_verification(client, project, baseline, analyzer="stub", video_bytes=valid_mp4_bytes)
    with client.app.state.database.session_factory() as db:
        job = db.get(VerificationJob, job_id)
        assert job is not None
        job.status = "failed"
        job.progress = 100
        job.result_json = None
        job.error_message = "simulated retry race"
        db.add(
            AuditEvent(
                entity_type="verification_job",
                entity_id=job_id,
                action="analysis_failed",
                payload_json={"error_code": "SIMULATED_TRANSIENT", "retryable": True},
            )
        )
        db.commit()

    scheduled: list[str] = []
    scheduled_lock = Lock()

    def capture_schedule(_app, scheduled_job_id: str) -> None:
        with scheduled_lock:
            scheduled.append(scheduled_job_id)

    monkeypatch.setattr(router_module, "run_verification_job", capture_schedule)
    callers_ready = Barrier(3)

    def invoke_retry():
        callers_ready.wait(timeout=5)
        return client.post(f"/api/v1/verifications/{job_id}/retry")

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(invoke_retry) for _ in range(2)]
        callers_ready.wait(timeout=5)
        responses = [future.result(timeout=10) for future in futures]

    assert sorted(response.status_code for response in responses) == [200, 409]
    assert scheduled == [job_id]
    actions = client.get(
        "/api/v1/audit-events",
        params={"entity_type": "verification_job", "entity_id": job_id},
    ).json()
    assert [item["action"] for item in actions].count("retry_queued") == 1
    detail = client.get(f"/api/v1/verifications/{job_id}").json()
    assert detail["job"]["status"] == "queued"
    assert detail["job"]["result"] is None
    assert detail["job"]["error"] is None


def test_changed_evidence_blocks_review_and_sealing(
    client: TestClient,
    project_and_baseline: tuple[dict, dict],
    valid_mp4_bytes: bytes,
) -> None:
    project, baseline = project_and_baseline
    job_id = _submit_verification(client, project, baseline, analyzer="stub", video_bytes=valid_mp4_bytes)
    with client.app.state.database.session_factory() as db:
        job = db.get(VerificationJob, job_id)
        assert job is not None
        evidence = db.get(EvidenceAsset, job.evidence_id)
        assert evidence is not None
        evidence_path = Path(evidence.storage_path)
    with evidence_path.open("ab") as handle:
        handle.write(b"changed-before-review")

    review = client.post(
        f"/api/v1/verifications/{job_id}/review",
        json={"decision": "approve", "reviewer": "审核员", "note": "should be blocked"},
        headers={"X-API-Key": "test-reviewer-key"},
    )
    assert review.status_code == 409
    detail = client.get(f"/api/v1/verifications/{job_id}").json()
    assert detail["job"]["status"] == "needs_review"
    assert detail["proof"] is None


def test_modified_report_is_not_served(
    client: TestClient,
    project_and_baseline: tuple[dict, dict],
    valid_mp4_bytes: bytes,
) -> None:
    project, baseline = project_and_baseline
    job_id = _submit_verification(client, project, baseline, analyzer="demo_fixture", video_bytes=valid_mp4_bytes)
    reviewed = client.post(
        f"/api/v1/verifications/{job_id}/review",
        json={"decision": "approve", "reviewer": "审核员", "note": "report integrity"},
        headers={"X-API-Key": "test-reviewer-key"},
    )
    assert reviewed.status_code == 200, reviewed.text
    report_id = reviewed.json()["report"]["id"]
    proof_id = reviewed.json()["proof"]["id"]
    with client.app.state.database.session_factory() as db:
        report = db.get(StructuredReport, report_id)
        assert report is not None
        html_path = Path(report.html_path)
    with html_path.open("a", encoding="utf-8") as handle:
        handle.write("tampered")
    assert client.get(f"/api/v1/reports/{report_id}/download", params={"format": "html"}).status_code == 409
    assert client.get(f"/api/v1/proofs/{proof_id}/verify").json()["valid"] is True


def test_proof_metadata_tampering_invalidates_record_and_dashboard(
    client: TestClient,
    project_and_baseline: tuple[dict, dict],
    valid_mp4_bytes: bytes,
) -> None:
    project, baseline = project_and_baseline
    job_id = _submit_verification(client, project, baseline, analyzer="demo_fixture", video_bytes=valid_mp4_bytes)
    reviewed = client.post(
        f"/api/v1/verifications/{job_id}/review",
        json={"decision": "approve", "reviewer": "审核员", "note": "metadata tamper"},
        headers={"X-API-Key": "test-reviewer-key"},
    )
    proof_id = reviewed.json()["proof"]["id"]
    with client.app.state.database.session_factory() as db:
        proof = db.get(ProofRecord, proof_id)
        assert proof is not None
        proof.purpose = "submission"
        proof.evidence_grade = True
        db.commit()
    integrity = client.get(f"/api/v1/proofs/{proof_id}/verify").json()
    assert integrity["valid"] is False
    assert integrity["checks"]["metadata_consistency"] is False
    assert client.get("/api/v1/dashboard/summary").json()["formal_evidence_archives"] == 0


def test_malformed_manifest_returns_structured_failure(
    client: TestClient,
    project_and_baseline: tuple[dict, dict],
    valid_mp4_bytes: bytes,
) -> None:
    project, baseline = project_and_baseline
    job_id = _submit_verification(client, project, baseline, analyzer="demo_fixture", video_bytes=valid_mp4_bytes)
    reviewed = client.post(
        f"/api/v1/verifications/{job_id}/review",
        json={"decision": "approve", "reviewer": "审核员", "note": "manifest tamper"},
        headers={"X-API-Key": "test-reviewer-key"},
    )
    proof_id = reviewed.json()["proof"]["id"]
    with client.app.state.database.session_factory() as db:
        proof = db.get(ProofRecord, proof_id)
        assert proof is not None
        archive_path = Path(proof.archive_path)
    corrupt_path = archive_path.with_suffix(".corrupt.zip")
    with zipfile.ZipFile(archive_path, "r") as source, zipfile.ZipFile(corrupt_path, "w") as target:
        for item in source.infolist():
            target.writestr(item, b"[]" if item.filename == "manifest.json" else source.read(item.filename))
    corrupt_path.replace(archive_path)
    response = client.get(f"/api/v1/proofs/{proof_id}/verify")
    assert response.status_code == 200
    assert response.json()["valid"] is False
    assert response.json()["errors"]

    verifier = Path(__file__).parents[1] / "scripts" / "verify_bundle.py"
    offline = subprocess.run(
        [sys.executable, str(verifier), str(archive_path)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert offline.returncode == 1
    offline_result = json.loads(offline.stdout)
    assert offline_result["valid"] is False
    assert offline_result["errors"]
    assert "Traceback" not in offline.stderr
