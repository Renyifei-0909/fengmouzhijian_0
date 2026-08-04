from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import hashlib
from io import BytesIO
import json
from pathlib import Path
import uuid
import zipfile

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text

from app.config import Settings
from app.main import create_app
from app.models import (
    APPEND_ONLY_TRIGGER_TARGETS,
    AuditEvent,
    FindingCase,
    FindingCaseCommand,
    HumanReview,
    ProofRecord,
    RemediationAttempt,
    SealOperation,
    StructuredReport,
    VerificationAttempt,
    VerificationAttemptOutcome,
    VerificationJob,
)
from app.services.remediation import (
    RemediationIntegrityError,
    materialize_finding_cases,
    validate_remediation_graph,
)
from app.services.storage import canonical_json_bytes, sha256_bytes


REVIEWER = {"X-API-Key": "test-reviewer-key"}
AUDITOR = {"X-API-Key": "test-auditor-key"}


def _ledger_rows(client: TestClient) -> list[dict]:
    path = client.app.state.storage.ledger_path
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _manifest_merkle_root(entries: list[dict]) -> str:
    leaves = [
        hashlib.sha256(f"{item['path']}\0{item['sha256']}".encode("utf-8")).digest()
        for item in sorted(entries, key=lambda item: item["path"])
    ]
    if not leaves:
        return "0" * 64
    level = leaves
    while len(level) > 1:
        if len(level) % 2 == 1:
            level.append(level[-1])
        level = [
            hashlib.sha256(level[index] + level[index + 1]).digest()
            for index in range(0, len(level), 2)
        ]
    return level[0].hex()


def _create_demo_project(client: TestClient) -> tuple[dict, dict]:
    project_response = client.post(
        "/api/v1/projects",
        json={
            "code": f"REMED-{uuid.uuid4().hex[:8]}",
            "name": "整改复验测试工程",
            "location": "匿名化测试工点",
            "manager": "测试负责人",
        },
    )
    assert project_response.status_code == 201, project_response.text
    project = project_response.json()
    baseline_response = client.post(
        f"/api/v1/projects/{project['id']}/baselines",
        json={
            "site_id": "SITE-REMEDIATION",
            "procedure_code": "PPE-RECHECK",
            "version": "design-v1",
            "source_type": "manual",
            "expected": {
                "scene_type": "communication-construction",
                "measurements": {"expected_quantity": "force-demo-deviation"},
            },
        },
    )
    assert baseline_response.status_code == 201, baseline_response.text
    return project, baseline_response.json()


def _restart_settings(tmp_path: Path) -> Settings:
    return Settings(
        environment="test",
        database_url=f"sqlite:///{tmp_path / 'remediation-restart.db'}",
        storage_root=tmp_path / "remediation-restart-storage",
        max_upload_bytes=2 * 1024 * 1024,
        allow_demo_analyzer=True,
        operator_api_key="test-operator-key",
        reviewer_api_key="test-reviewer-key",
        auditor_api_key="test-auditor-key",
        cors_origins=("http://testserver",),
    )


def _submit(
    client: TestClient,
    project: dict,
    baseline: dict,
    video_bytes: bytes,
    *,
    analyzer: str,
    attempt_id: str | None = None,
) -> dict:
    data = {
        "project_id": project["id"],
        "baseline_id": baseline["id"],
        "analyzer": analyzer,
        "device_id": "CAM-REMEDIATION",
        "metadata": '{"source":"pytest","privacy":"synthetic"}',
    }
    if attempt_id:
        data["remediation_attempt_id"] = attempt_id
    response = client.post(
        "/api/v1/verifications",
        data=data,
        files={"file": ("sample.mp4", video_bytes, "video/mp4")},
    )
    assert response.status_code == 202, response.text
    detail = client.get(f"/api/v1/verifications/{response.json()['id']}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["job"]["status"] == "needs_review"
    return detail.json()["job"]


def _inject_operational_finding(client: TestClient, job_id: str) -> FindingCase:
    """Rewrite a completed stub job into a remote-like operational finding.

    Test-only scaffolding: production never rewrites analyzer pins or attempt
    history. After Alpha13, readiness integrity requires the job pin/result and
    the successful attempt/outcome snapshots to stay consistent. SQLite
    append-only triggers are dropped only for this coordinated rewrite, then
    reinstalled before commit.
    """

    with client.app.state.database.session_factory() as db:
        job = db.get(VerificationJob, job_id)
        assert job is not None and job.result_json is not None
        result = deepcopy(job.result_json)
        result["analysis_mode"] = "remote_http"
        result["analyzer"] = {"name": "remote_http", "version": "test-model-v1"}
        result["provenance"] = {
            "kind": "remote_model_unvalidated",
            "synthetic": False,
            "warning": "Test-only normalized remote finding; not evaluation evidence.",
        }
        result["findings"] = [
            {
                "code": "PPE_HELMET_CANDIDATE",
                "severity": "critical",
                "message": "Candidate event for workflow testing; not a verified real-world defect.",
            }
        ]
        result_digest = sha256_bytes(canonical_json_bytes(result))

        success_attempts = list(
            db.scalars(
                select(VerificationAttempt)
                .join(
                    VerificationAttemptOutcome,
                    VerificationAttemptOutcome.attempt_id == VerificationAttempt.id,
                )
                .where(
                    VerificationAttempt.job_id == job_id,
                    VerificationAttemptOutcome.disposition == "committed_success",
                )
                .order_by(VerificationAttempt.generation.desc())
            ).all()
        )
        assert success_attempts, "expected a committed_success attempt to rewrite"

        dialect = db.get_bind().dialect.name
        if dialect != "sqlite":
            raise RuntimeError(
                "_inject_operational_finding is only supported on SQLite create_all tests"
            )
        for trigger_name in APPEND_ONLY_TRIGGER_TARGETS:
            db.execute(text(f"DROP TRIGGER IF EXISTS {trigger_name}"))

        job.analyzer_name = "remote_http"
        job.analyzer_version = "test-model-v1"
        job.result_json = result
        for attempt in success_attempts:
            attempt.analyzer_name = "remote_http"
            attempt.analyzer_version = "test-model-v1"
            outcome = db.scalar(
                select(VerificationAttemptOutcome).where(
                    VerificationAttemptOutcome.attempt_id == attempt.id
                )
            )
            assert outcome is not None
            outcome.result_json = result
            outcome.result_sha256 = result_digest

        db.flush()
        for trigger_name, (table_name, operation) in APPEND_ONLY_TRIGGER_TARGETS.items():
            db.execute(
                text(
                    f"""
CREATE TRIGGER {trigger_name}
BEFORE {operation.upper()} ON {table_name}
BEGIN
    SELECT RAISE(ABORT, '{table_name} is append-only');
END
"""
                )
            )

        cases = materialize_finding_cases(db, job)
        db.commit()
        assert len(cases) == 1
        db.refresh(cases[0])
        return cases[0]


def _triage(client: TestClient, case: dict, *, request_id: str | None = None) -> dict:
    response = client.post(
        f"/api/v1/finding-cases/{case['id']}/triage",
        headers=REVIEWER,
        json={
            "request_id": request_id or str(uuid.uuid4()),
            "expected_version": case["version"],
            "decision": "confirm",
            "confirmed_severity": "warning",
            "reason": "仅确认进入整改流程，不代表模型准确率或现场真值。",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def _approved_operational_case(
    client: TestClient,
    project: dict,
    baseline: dict,
    video_bytes: bytes,
) -> tuple[dict, dict]:
    job = _submit(client, project, baseline, video_bytes, analyzer="stub")
    injected = _inject_operational_finding(client, job["id"])
    case = _triage(
        client,
        {
            "id": injected.id,
            "version": injected.version,
            "proposed_severity": injected.proposed_severity,
        },
    )
    approved = client.post(
        f"/api/v1/verifications/{job['id']}/review",
        headers=REVIEWER,
        json={"decision": "approve", "reviewer": "reviewer", "note": "triaged candidate"},
    )
    assert approved.status_code == 200, approved.text
    return case, approved.json()


def test_stub_system_notice_does_not_materialize_alarm_case(
    client: TestClient,
    project_and_baseline: tuple[dict, dict],
    valid_mp4_bytes: bytes,
) -> None:
    project, baseline = project_and_baseline
    job = _submit(client, project, baseline, valid_mp4_bytes, analyzer="stub")
    response = client.get(f"/api/v1/finding-cases?project_id={project['id']}")
    assert response.status_code == 200
    assert response.json() == []
    with client.app.state.database.session_factory() as db:
        audit = db.scalar(
            select(AuditEvent).where(
                AuditEvent.entity_type == "verification_job",
                AuditEvent.entity_id == job["id"],
                AuditEvent.action == "analysis_completed",
            )
        )
        assert audit is not None
        assert audit.payload_json["finding_case_count"] == 0

    assert client.get("/api/v1/finding-cases?status=not-a-state").status_code == 422
    assert client.get("/api/v1/finding-cases?scope=not-a-scope").status_code == 422


def test_operational_candidate_requires_reviewer_triage_and_is_idempotent(
    client: TestClient,
    project_and_baseline: tuple[dict, dict],
    valid_mp4_bytes: bytes,
) -> None:
    project, baseline = project_and_baseline
    job = _submit(client, project, baseline, valid_mp4_bytes, analyzer="stub")
    case = _inject_operational_finding(client, job["id"])

    blocked = client.post(
        f"/api/v1/verifications/{job['id']}/review",
        headers=REVIEWER,
        json={"decision": "approve", "reviewer": "reviewer", "note": "pending triage"},
    )
    assert blocked.status_code == 409
    assert blocked.json()["detail"]["pending_finding_case_count"] == 1

    forbidden = client.post(
        f"/api/v1/finding-cases/{case.id}/triage",
        json={
            "request_id": str(uuid.uuid4()),
            "expected_version": 0,
            "decision": "confirm",
            "reason": "operator must not triage",
        },
    )
    assert forbidden.status_code == 403

    request_id = str(uuid.uuid4())
    payload = {
        "request_id": request_id,
        "expected_version": 0,
        "decision": "confirm",
        "confirmed_severity": "warning",
        "reason": "Reviewer confirms only an operational case, not model correctness.",
    }
    first = client.post(f"/api/v1/finding-cases/{case.id}/triage", headers=REVIEWER, json=payload)
    replay = client.post(f"/api/v1/finding-cases/{case.id}/triage", headers=REVIEWER, json=payload)
    assert first.status_code == replay.status_code == 200
    assert first.json()["version"] == replay.json()["version"] == 1
    assert first.json()["status"] == "open"

    conflicting_payload = {**payload, "reason": "different payload"}
    conflict = client.post(
        f"/api/v1/finding-cases/{case.id}/triage",
        headers=REVIEWER,
        json=conflicting_payload,
    )
    assert conflict.status_code == 409

    approved = client.post(
        f"/api/v1/verifications/{job['id']}/review",
        headers=REVIEWER,
        json={"decision": "approve", "reviewer": "reviewer", "note": "finding triaged"},
    )
    assert approved.status_code == 200, approved.text
    frozen_cases = approved.json()["report"]["content"]["finding_cases"]
    assert frozen_cases[0]["status_at_seal"] == "open"
    assert frozen_cases[0]["scope"] == "operational"

    summary = client.get(f"/api/v1/finding-cases/summary?project_id={project['id']}")
    assert summary.status_code == 200
    assert summary.json()["confirmed_open_operational"] == 1
    assert "not model metrics" in summary.json()["truth_note"]


def test_demo_case_full_remediation_reverification_closes_with_new_proof(
    client: TestClient,
    valid_mp4_bytes: bytes,
) -> None:
    project, baseline = _create_demo_project(client)
    source_job = _submit(client, project, baseline, valid_mp4_bytes, analyzer="demo_fixture")
    source_review = client.post(
        f"/api/v1/verifications/{source_job['id']}/review",
        headers=REVIEWER,
        json={
            "decision": "approve",
            "reviewer": "demo reviewer",
            "note": "仅批准合成工作流，不认可现场缺陷或模型能力。",
        },
    )
    assert source_review.status_code == 200, source_review.text
    source_outcome = source_review.json()
    original_report_id = source_outcome["report"]["id"]
    original_proof_id = source_outcome["proof"]["id"]
    original_report_record = client.get(f"/api/v1/reports/{original_report_id}").json()
    original_proof_record = client.get(f"/api/v1/proofs/{original_proof_id}").json()
    original_report_json_response = client.get(
        f"/api/v1/reports/{original_report_id}/download",
        params={"format": "json"},
    )
    original_report_html_response = client.get(
        f"/api/v1/reports/{original_report_id}/download",
        params={"format": "html"},
    )
    original_archive_response = client.get(f"/api/v1/proofs/{original_proof_id}/archive")
    assert original_report_json_response.status_code == 200
    assert original_report_html_response.status_code == 200
    assert original_archive_response.status_code == 200
    original_report_json_bytes = original_report_json_response.content
    original_report_html_bytes = original_report_html_response.content
    original_archive_bytes = original_archive_response.content
    original_ledger_rows = _ledger_rows(client)
    assert len(original_ledger_rows) == 1
    original_ledger_row = deepcopy(original_ledger_rows[original_proof_record["ledger_index"]])
    assert original_ledger_row["record_hash"] == original_proof_record["record_hash"]
    assert (
        hashlib.sha256(original_archive_bytes).hexdigest()
        == original_proof_record["archive_sha256"]
    )

    cases = client.get(f"/api/v1/finding-cases?project_id={project['id']}").json()
    assert len(cases) == 1
    assert cases[0]["scope"] == "demo"
    assert cases[0]["status"] == "pending_triage"
    case = _triage(client, cases[0])

    started = client.post(
        f"/api/v1/finding-cases/{case['id']}/start-remediation",
        json={
            "request_id": str(uuid.uuid4()),
            "expected_version": case["version"],
            "assignee": "demo-operator",
            "action_description": "补充合成影像并重新执行演示复验。",
            "due_at": "2026-07-20T08:00:00Z",
        },
    )
    assert started.status_code == 200, started.text
    assert started.json()["status"] == "remediation_in_progress"

    attempt_response = client.post(
        f"/api/v1/finding-cases/{case['id']}/remediation-attempts",
        json={
            "client_request_id": str(uuid.uuid4()),
            "expected_version": started.json()["version"],
            "action_description": "演示整改：重新采集不含真人的几何视频。",
        },
    )
    assert attempt_response.status_code == 201, attempt_response.text
    attempt = attempt_response.json()

    recheck_job = _submit(
        client,
        project,
        baseline,
        valid_mp4_bytes,
        analyzer="demo_fixture",
        attempt_id=attempt["id"],
    )
    pending_detail = client.get(f"/api/v1/finding-cases/{case['id']}").json()
    assert pending_detail["case"]["status"] == "verification_pending"
    assert pending_detail["closure_evidence_status"] == "unsealed"

    resolved = client.post(
        f"/api/v1/verifications/{recheck_job['id']}/review",
        headers=REVIEWER,
        json={
            "decision": "approve",
            "reviewer": "demo resolution reviewer",
            "note": "合成演示复验通过，仅用于验证整改封存状态机。",
            "remediation_resolution": "resolved",
        },
    )
    assert resolved.status_code == 200, resolved.text
    report = resolved.json()["report"]
    proof = resolved.json()["proof"]
    context = report["content"]["remediation_context"]
    assert report["status"] == "reviewed_demo"
    assert proof["report_id"] == report["id"]
    assert proof["purpose"] == "demo"
    assert proof["evidence_grade"] is False
    assert context["case"]["id"] == case["id"]
    assert context["case"]["scope"] == "demo"
    assert context["case"]["source_synthetic"] is True
    assert context["attempt"]["resolution_decision"] == "resolved"

    sealed_report_json_response = client.get(
        f"/api/v1/reports/{report['id']}/download",
        params={"format": "json"},
    )
    sealed_report_html_response = client.get(
        f"/api/v1/reports/{report['id']}/download",
        params={"format": "html"},
    )
    archive = client.get(f"/api/v1/proofs/{proof['id']}/archive")
    assert sealed_report_json_response.status_code == 200
    assert sealed_report_html_response.status_code == 200
    assert archive.status_code == 200
    assert hashlib.sha256(archive.content).hexdigest() == proof["archive_sha256"]
    with zipfile.ZipFile(BytesIO(archive.content)) as bundle:
        names = set(bundle.namelist())
        expected_members = {
            "analysis/result.json",
            "design/baseline.json",
            "review/human-review.json",
            "sensors/events.json",
            "findings/cases-at-seal.json",
            "remediation/case.json",
            "remediation/attempt.json",
            "report/report.json",
            "report/report.html",
            "manifest.json",
        }
        assert expected_members <= names
        manifest_bytes = bundle.read("manifest.json")
        manifest = json.loads(manifest_bytes)
        manifest_paths = [item["path"] for item in manifest["files"]]
        assert len(manifest_paths) == len(set(manifest_paths))
        assert set(manifest_paths) == names - {"manifest.json"}
        report_bytes = bundle.read("report/report.json")
        report_html_bytes = bundle.read("report/report.html")
        archived_report = json.loads(report_bytes)
        archived_case = json.loads(bundle.read("remediation/case.json"))
        archived_attempt = json.loads(bundle.read("remediation/attempt.json"))
        archived_cases_at_seal = json.loads(bundle.read("findings/cases-at-seal.json"))
        assert report_bytes == sealed_report_json_response.content
        assert report_html_bytes == sealed_report_html_response.content
        assert hashlib.sha256(report_bytes).hexdigest() == report["sha256"]
        assert hashlib.sha256(report_html_bytes).hexdigest() == report["html_sha256"]
        assert archived_report == report["content"]
        assert archived_case == context["case"]
        assert archived_attempt == context["attempt"]
        assert archived_cases_at_seal == report["content"]["finding_cases"]
        for item in manifest["files"]:
            member_bytes = bundle.read(item["path"])
            assert len(member_bytes) == item["size_bytes"]
            assert hashlib.sha256(member_bytes).hexdigest() == item["sha256"]

    assert manifest["archive_id"] == proof["archive_id"]
    assert manifest["project_id"] == project["id"]
    assert manifest["job_id"] == recheck_job["id"]
    assert manifest["report_id"] == report["id"] == proof["report_id"]
    assert manifest["purpose"] == proof["purpose"] == "demo"
    assert manifest["evidence_grade"] is proof["evidence_grade"] is False
    assert manifest["merkle_root"] == proof["merkle_root"]
    assert manifest["merkle_root"] == _manifest_merkle_root(manifest["files"])
    assert hashlib.sha256(manifest_bytes).hexdigest() == proof["manifest_sha256"]
    assert context["case"]["project_id"] == project["id"]
    assert context["attempt"]["id"] == attempt["id"]
    assert context["attempt"]["verification_job_id"] == recheck_job["id"]
    verification = client.get(f"/api/v1/proofs/{proof['id']}/verify", headers=AUDITOR)
    assert verification.status_code == 200, verification.text
    assert verification.json()["valid"] is True
    assert all(verification.json()["checks"].values())

    closed = client.get(f"/api/v1/finding-cases/{case['id']}")
    assert closed.status_code == 200, closed.text
    assert closed.json()["case"]["status"] == "closed"
    assert closed.json()["case"]["closure_proof_id"] == proof["id"]
    assert closed.json()["closure_evidence_status"] == "sealed"

    current_report_record = client.get(f"/api/v1/reports/{original_report_id}").json()
    current_proof_record = client.get(f"/api/v1/proofs/{original_proof_id}").json()
    current_report_json = client.get(
        f"/api/v1/reports/{original_report_id}/download",
        params={"format": "json"},
    )
    current_report_html = client.get(
        f"/api/v1/reports/{original_report_id}/download",
        params={"format": "html"},
    )
    current_archive = client.get(f"/api/v1/proofs/{original_proof_id}/archive")
    assert current_report_record == original_report_record
    assert current_proof_record == original_proof_record
    assert current_report_json.status_code == 200
    assert current_report_html.status_code == 200
    assert current_archive.status_code == 200
    assert current_report_json.content == original_report_json_bytes
    assert current_report_html.content == original_report_html_bytes
    assert current_archive.content == original_archive_bytes
    current_ledger_rows = _ledger_rows(client)
    assert len(current_ledger_rows) == len(original_ledger_rows) + 1
    assert current_ledger_rows[: len(original_ledger_rows)] == original_ledger_rows
    assert current_ledger_rows[original_proof_record["ledger_index"]] == original_ledger_row
    assert current_ledger_rows[proof["ledger_index"]]["record_hash"] == proof["record_hash"]
    original_verification = client.get(
        f"/api/v1/proofs/{original_proof_id}/verify",
        headers=AUDITOR,
    )
    assert original_verification.status_code == 200
    assert original_verification.json()["valid"] is True
    assert all(original_verification.json()["checks"].values())
    summary = client.get(f"/api/v1/finding-cases/summary?project_id={project['id']}").json()
    assert summary["demo_cases"] >= 2
    assert summary["closed_operational"] == 0


def test_restart_recovers_remediation_closure_after_final_commit_failure_without_duplicates(
    tmp_path: Path,
    valid_mp4_bytes: bytes,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _restart_settings(tmp_path)
    first_app = create_app(settings)
    review_payload = {
        "decision": "approve",
        "reviewer": "restart recovery reviewer",
        "note": "Resolved only after the new evidence bundle is durably sealed.",
        "remediation_resolution": "resolved",
    }
    stable: dict[str, str | int] = {}
    artifact_bytes: dict[str, bytes] = {}

    with TestClient(first_app) as first:
        first.headers.update({"X-API-Key": "test-operator-key"})
        project, baseline = _create_demo_project(first)
        case, _source_approval = _approved_operational_case(
            first,
            project,
            baseline,
            valid_mp4_bytes,
        )
        started_response = first.post(
            f"/api/v1/finding-cases/{case['id']}/start-remediation",
            json={
                "request_id": str(uuid.uuid4()),
                "expected_version": case["version"],
                "assignee": "restart-test-operator",
                "action_description": "Exercise restart-safe remediation closure.",
            },
        )
        assert started_response.status_code == 200, started_response.text
        attempt_response = first.post(
            f"/api/v1/finding-cases/{case['id']}/remediation-attempts",
            json={
                "client_request_id": str(uuid.uuid4()),
                "expected_version": started_response.json()["version"],
                "action_description": "Submit replacement evidence before a simulated crash.",
            },
        )
        assert attempt_response.status_code == 201, attempt_response.text
        attempt = attempt_response.json()
        recheck = _submit(
            first,
            project,
            baseline,
            valid_mp4_bytes,
            analyzer="stub",
            attempt_id=attempt["id"],
        )
        before_close = first.get(f"/api/v1/finding-cases/{case['id']}").json()
        assert before_close["case"]["status"] == "verification_pending"
        assert before_close["case"]["version"] == 4
        assert before_close["case"]["active_attempt_no"] == 1
        assert before_close["case"]["closure_proof_id"] is None
        assert len(_ledger_rows(first)) == 1

        session_class = first_app.state.database.session_factory.class_
        original_commit = session_class.commit
        injected = False

        def fail_final_commit_once(session) -> None:
            nonlocal injected
            completing_recheck = any(
                isinstance(item, SealOperation)
                and item.job_id == recheck["id"]
                and item.state == "completed"
                for item in session.identity_map.values()
            )
            if completing_recheck and not injected:
                injected = True
                raise OSError("injected remediation final commit failure")
            original_commit(session)

        monkeypatch.setattr(session_class, "commit", fail_final_commit_once)
        failed = first.post(
            f"/api/v1/verifications/{recheck['id']}/review",
            headers=REVIEWER,
            json=review_payload,
        )
        assert failed.status_code == 503, failed.text

        with first_app.state.database.session_factory() as db:
            stored_case = db.get(FindingCase, case["id"])
            stored_attempt = db.get(RemediationAttempt, attempt["id"])
            stored_job = db.get(VerificationJob, recheck["id"])
            operation = db.scalar(select(SealOperation).where(SealOperation.job_id == recheck["id"]))
            reviews = list(db.scalars(select(HumanReview).where(HumanReview.job_id == recheck["id"])).all())
            reports = list(
                db.scalars(select(StructuredReport).where(StructuredReport.job_id == recheck["id"])).all()
            )
            close_audits = list(
                db.scalars(
                    select(AuditEvent).where(
                        AuditEvent.entity_type == "finding_case",
                        AuditEvent.entity_id == case["id"],
                        AuditEvent.action == "case_closed_with_sealed_reverification",
                    )
                ).all()
            )
            failure_audits = list(
                db.scalars(
                    select(AuditEvent).where(
                        AuditEvent.entity_type == "seal_operation",
                        AuditEvent.entity_id == operation.id,
                        AuditEvent.action == "seal_attempt_failed",
                    )
                ).all()
            ) if operation is not None else []
            proofs = list(
                db.scalars(select(ProofRecord).where(ProofRecord.archive_id == operation.archive_id)).all()
            ) if operation is not None else []
            assert stored_case is not None and stored_attempt is not None and operation is not None
            assert stored_job is not None and stored_job.status == "sealing"
            assert stored_case.status == "verification_pending"
            assert stored_case.version == 4
            assert stored_case.active_attempt_no == 1
            assert stored_case.closure_proof_id is None
            assert stored_attempt.resolution_decision == "resolved"
            assert stored_attempt.resolution_note == review_payload["note"]
            assert stored_attempt.resolved_by is not None and stored_attempt.resolved_at is not None
            assert stored_attempt.report_id is None and stored_attempt.proof_id is None
            assert len(reviews) == 1
            assert reports == []
            assert proofs == []
            assert close_audits == []
            assert len(failure_audits) == 1
            assert failure_audits[0].payload_json["state"] == "ledger_appended"
            assert operation.state == "ledger_appended"
            assert operation.attempt_count == 1
            assert "injected remediation final commit failure" in (operation.last_error or "")
            stable = {
                "case_id": case["id"],
                "attempt_id": attempt["id"],
                "job_id": recheck["id"],
                "operation_id": operation.id,
                "review_id": operation.review_id,
                "report_id": operation.report_id,
                "archive_id": operation.archive_id,
                "proof_id": operation.archive_id.removeprefix("ARC-"),
                "resolved_by": stored_attempt.resolved_by,
                "resolved_at": stored_attempt.resolved_at.isoformat(),
            }

        ledger_before = first_app.state.storage.ledger_path.read_bytes()
        assert len(_ledger_rows(first)) == 2
        report_json = first_app.state.storage.report_dir / f"{stable['report_id']}.json"
        report_html = first_app.state.storage.report_dir / f"{stable['report_id']}.html"
        archive = first_app.state.storage.archive_dir / f"{stable['archive_id']}.zip"
        artifact_bytes = {
            "ledger": ledger_before,
            "report_json": report_json.read_bytes(),
            "report_html": report_html.read_bytes(),
            "archive": archive.read_bytes(),
        }
        assert first.get("/api/v1/readyz").status_code == 503

    monkeypatch.setattr(session_class, "commit", original_commit)
    restarted_app = create_app(settings)
    with TestClient(restarted_app) as restarted:
        restarted.headers.update({"X-API-Key": "test-operator-key"})
        assert restarted.get("/api/v1/readyz").json() == {"status": "ready"}
        assert restarted_app.state.storage.ledger_path.read_bytes() == artifact_bytes["ledger"]
        assert (
            restarted_app.state.storage.report_dir / f"{stable['report_id']}.json"
        ).read_bytes() == artifact_bytes["report_json"]
        assert (
            restarted_app.state.storage.report_dir / f"{stable['report_id']}.html"
        ).read_bytes() == artifact_bytes["report_html"]
        assert (
            restarted_app.state.storage.archive_dir / f"{stable['archive_id']}.zip"
        ).read_bytes() == artifact_bytes["archive"]

        with restarted_app.state.database.session_factory() as db:
            stored_case = db.get(FindingCase, str(stable["case_id"]))
            stored_attempt = db.get(RemediationAttempt, str(stable["attempt_id"]))
            operation = db.get(SealOperation, str(stable["operation_id"]))
            report = db.get(StructuredReport, str(stable["report_id"]))
            proof = db.get(ProofRecord, str(stable["proof_id"]))
            reviews = list(
                db.scalars(select(HumanReview).where(HumanReview.job_id == stable["job_id"])).all()
            )
            reports = list(
                db.scalars(select(StructuredReport).where(StructuredReport.job_id == stable["job_id"])).all()
            )
            approved_audits = list(
                db.scalars(
                    select(AuditEvent).where(
                        AuditEvent.entity_type == "verification_job",
                        AuditEvent.entity_id == stable["job_id"],
                        AuditEvent.action == "approved_and_sealed",
                    )
                ).all()
            )
            close_audits = list(
                db.scalars(
                    select(AuditEvent).where(
                        AuditEvent.entity_type == "finding_case",
                        AuditEvent.entity_id == stable["case_id"],
                        AuditEvent.action == "case_closed_with_sealed_reverification",
                    )
                ).all()
            )
            failure_audits = list(
                db.scalars(
                    select(AuditEvent).where(
                        AuditEvent.entity_type == "seal_operation",
                        AuditEvent.entity_id == stable["operation_id"],
                        AuditEvent.action == "seal_attempt_failed",
                    )
                ).all()
            )
            assert stored_case is not None and stored_attempt is not None
            assert operation is not None and report is not None and proof is not None
            assert operation.state == "completed"
            assert operation.attempt_count == 2
            assert operation.last_error is None
            assert operation.review_id == stable["review_id"]
            assert operation.report_id == stable["report_id"]
            assert operation.archive_id == stable["archive_id"]
            assert len(reviews) == len(reports) == len(approved_audits) == len(close_audits) == 1
            assert len(failure_audits) == 1
            assert stored_case.status == "closed"
            assert stored_case.version == 5
            assert stored_case.active_attempt_no is None
            assert stored_case.closure_proof_id == stored_attempt.proof_id == proof.id
            assert stored_attempt.report_id == report.id
            assert stored_attempt.resolved_by == stable["resolved_by"]
            assert stored_attempt.resolved_at is not None
            assert stored_attempt.resolved_at.isoformat() == stable["resolved_at"]
            assert proof.report_id == report.id
            assert report.job_id == stable["job_id"]
            assert report.content_json["remediation_context"]["case"]["status_at_seal"] == "verification_pending"
            assert report.content_json["remediation_context"]["case"]["version_at_seal"] == 4

        verification = restarted.get(f"/api/v1/proofs/{stable['proof_id']}/verify", headers=AUDITOR)
        assert verification.status_code == 200, verification.text
        assert verification.json()["valid"] is True
        assert all(verification.json()["checks"].values())

        replay = restarted.post(
            f"/api/v1/verifications/{stable['job_id']}/review",
            headers=REVIEWER,
            json=review_payload,
        )
        assert replay.status_code == 200, replay.text
        assert replay.json()["report"]["id"] == stable["report_id"]
        assert replay.json()["proof"]["id"] == stable["proof_id"]
        assert restarted_app.state.storage.ledger_path.read_bytes() == artifact_bytes["ledger"]
        with restarted_app.state.database.session_factory() as db:
            operation = db.get(SealOperation, str(stable["operation_id"]))
            assert operation is not None and operation.attempt_count == 2
            review_count = db.scalar(
                select(func.count())
                .select_from(HumanReview)
                .where(HumanReview.job_id == stable["job_id"])
            )
            report_count = db.scalar(
                select(func.count())
                .select_from(StructuredReport)
                .where(StructuredReport.job_id == stable["job_id"])
            )
            proof_count = db.scalar(
                select(func.count())
                .select_from(ProofRecord)
                .where(ProofRecord.archive_id == stable["archive_id"])
            )
            approved_count = db.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(
                    AuditEvent.entity_type == "verification_job",
                    AuditEvent.entity_id == stable["job_id"],
                    AuditEvent.action == "approved_and_sealed",
                )
            )
            close_count = db.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(
                    AuditEvent.entity_type == "finding_case",
                    AuditEvent.entity_id == stable["case_id"],
                    AuditEvent.action == "case_closed_with_sealed_reverification",
                )
            )
            failure_count = db.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(
                    AuditEvent.entity_type == "seal_operation",
                    AuditEvent.entity_id == stable["operation_id"],
                    AuditEvent.action == "seal_attempt_failed",
                )
            )
            assert review_count == report_count == proof_count == 1
            assert approved_count == close_count == failure_count == 1


def test_closed_case_with_different_valid_proof_degrades_readiness(
    client: TestClient,
    project_and_baseline: tuple[dict, dict],
    valid_mp4_bytes: bytes,
) -> None:
    project, baseline = project_and_baseline
    case, source_outcome = _approved_operational_case(
        client,
        project,
        baseline,
        valid_mp4_bytes,
    )
    source_proof_id = source_outcome["proof"]["id"]
    source_verification = client.get(
        f"/api/v1/proofs/{source_proof_id}/verify",
        headers=AUDITOR,
    )
    assert source_verification.status_code == 200
    assert source_verification.json()["valid"] is True

    started = client.post(
        f"/api/v1/finding-cases/{case['id']}/start-remediation",
        json={
            "request_id": str(uuid.uuid4()),
            "expected_version": case["version"],
            "assignee": "site-operator",
            "action_description": "完成整改并重新采集证据。",
        },
    )
    assert started.status_code == 200, started.text
    attempt_response = client.post(
        f"/api/v1/finding-cases/{case['id']}/remediation-attempts",
        json={
            "client_request_id": str(uuid.uuid4()),
            "expected_version": started.json()["version"],
            "action_description": "提交一次独立复验证据。",
        },
    )
    assert attempt_response.status_code == 201, attempt_response.text
    attempt = attempt_response.json()
    recheck = _submit(
        client,
        project,
        baseline,
        valid_mp4_bytes,
        analyzer="stub",
        attempt_id=attempt["id"],
    )
    resolved = client.post(
        f"/api/v1/verifications/{recheck['id']}/review",
        headers=REVIEWER,
        json={
            "decision": "approve",
            "reviewer": "closure reviewer",
            "note": "仅根据本次复验证据执行关闭。",
            "remediation_resolution": "resolved",
        },
    )
    assert resolved.status_code == 200, resolved.text
    closure_proof_id = resolved.json()["proof"]["id"]
    assert closure_proof_id != source_proof_id
    detail = client.get(f"/api/v1/finding-cases/{case['id']}")
    assert detail.status_code == 200
    assert detail.json()["case"]["status"] == "closed"
    assert detail.json()["case"]["closure_proof_id"] == closure_proof_id
    assert client.get("/api/v1/readyz").status_code == 200

    with client.app.state.database.session_factory() as db:
        stored = db.get(FindingCase, case["id"])
        assert stored is not None
        stored.closure_proof_id = source_proof_id
        db.commit()

    ready = client.get("/api/v1/readyz")
    assert ready.status_code == 503
    assert ready.json()["detail"]["status"] == "integrity_incident"


def test_concurrent_triage_has_one_transition_and_no_lost_update(
    client: TestClient,
    project_and_baseline: tuple[dict, dict],
    valid_mp4_bytes: bytes,
) -> None:
    project, baseline = project_and_baseline
    job = _submit(client, project, baseline, valid_mp4_bytes, analyzer="stub")
    case = _inject_operational_finding(client, job["id"])

    def triage(reason: str) -> int:
        response = client.post(
            f"/api/v1/finding-cases/{case.id}/triage",
            headers=REVIEWER,
            json={
                "request_id": str(uuid.uuid4()),
                "expected_version": 0,
                "decision": "confirm",
                "confirmed_severity": "warning",
                "reason": reason,
            },
        )
        return response.status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = sorted(pool.map(triage, ["reviewer decision A", "reviewer decision B"]))
    assert statuses == [200, 409]

    with client.app.state.database.session_factory() as db:
        stored = db.get(FindingCase, case.id)
        assert stored is not None
        assert stored.version == 1
        assert stored.status == "open"
        assert db.scalar(
            select(func.count()).select_from(FindingCaseCommand).where(
                FindingCaseCommand.case_id == case.id,
                FindingCaseCommand.command == "finding_confirmed",
            )
        ) == 1


def test_finding_source_tampering_degrades_readiness(
    client: TestClient,
    project_and_baseline: tuple[dict, dict],
    valid_mp4_bytes: bytes,
) -> None:
    project, baseline = project_and_baseline
    job = _submit(client, project, baseline, valid_mp4_bytes, analyzer="stub")
    case = _inject_operational_finding(client, job["id"])
    with client.app.state.database.session_factory() as db:
        stored = db.get(FindingCase, case.id)
        assert stored is not None
        stored.finding_message = "tampered database text"
        db.commit()

    listing = client.get(f"/api/v1/finding-cases?project_id={project['id']}")
    assert listing.status_code == 409
    ready = client.get("/api/v1/readyz")
    assert ready.status_code == 503
    assert ready.json()["detail"]["status"] == "integrity_incident"


def test_dismiss_command_and_rejected_source_job_are_persisted(
    client: TestClient,
    project_and_baseline: tuple[dict, dict],
    valid_mp4_bytes: bytes,
) -> None:
    project, baseline = project_and_baseline
    first_job = _submit(client, project, baseline, valid_mp4_bytes, analyzer="stub")
    first_case = _inject_operational_finding(client, first_job["id"])
    request_id = str(uuid.uuid4())
    payload = {
        "request_id": request_id,
        "expected_version": 0,
        "decision": "dismiss",
        "reason": "人工复核判定为非现场异常。",
    }
    dismissed = client.post(
        f"/api/v1/finding-cases/{first_case.id}/triage",
        headers=REVIEWER,
        json=payload,
    )
    replay = client.post(
        f"/api/v1/finding-cases/{first_case.id}/triage",
        headers=REVIEWER,
        json=payload,
    )
    assert dismissed.status_code == replay.status_code == 200
    assert dismissed.json()["status"] == "dismissed"
    assert replay.json()["version"] == 1

    second_job = _submit(client, project, baseline, valid_mp4_bytes, analyzer="stub")
    second_case = _inject_operational_finding(client, second_job["id"])
    rejected = client.post(
        f"/api/v1/verifications/{second_job['id']}/review",
        headers=REVIEWER,
        json={"decision": "reject", "reviewer": "reviewer", "note": "source rejected"},
    )
    assert rejected.status_code == 200, rejected.text
    stored = client.get(f"/api/v1/finding-cases/{second_case.id}").json()["case"]
    assert stored["status"] == "dismissed"
    assert stored["decision_reason"] == "source rejected"


def test_start_and_attempt_commands_are_idempotent_and_conflict_safe(
    client: TestClient,
    project_and_baseline: tuple[dict, dict],
    valid_mp4_bytes: bytes,
) -> None:
    project, baseline = project_and_baseline
    case, _ = _approved_operational_case(client, project, baseline, valid_mp4_bytes)
    start_id = str(uuid.uuid4())
    start_payload = {
        "request_id": start_id,
        "expected_version": case["version"],
        "assignee": "site-operator",
        "action_description": "补充防护并重新采集证据。",
    }
    started = client.post(f"/api/v1/finding-cases/{case['id']}/start-remediation", json=start_payload)
    replay = client.post(f"/api/v1/finding-cases/{case['id']}/start-remediation", json=start_payload)
    assert started.status_code == replay.status_code == 200
    assert started.json()["version"] == replay.json()["version"] == 2
    conflict = client.post(
        f"/api/v1/finding-cases/{case['id']}/start-remediation",
        json={**start_payload, "assignee": "different-assignee"},
    )
    assert conflict.status_code == 409

    attempt_id = str(uuid.uuid4())
    attempt_payload = {
        "client_request_id": attempt_id,
        "expected_version": started.json()["version"],
        "action_description": "完成整改并提交复验。",
    }
    created = client.post(
        f"/api/v1/finding-cases/{case['id']}/remediation-attempts",
        json=attempt_payload,
    )
    attempt_replay = client.post(
        f"/api/v1/finding-cases/{case['id']}/remediation-attempts",
        json=attempt_payload,
    )
    assert created.status_code == attempt_replay.status_code == 201
    assert created.json()["id"] == attempt_replay.json()["id"]
    reused_key = client.post(
        f"/api/v1/finding-cases/{case['id']}/remediation-attempts",
        json={**attempt_payload, "action_description": "different action"},
    )
    assert reused_key.status_code == 409
    second_pending = client.post(
        f"/api/v1/finding-cases/{case['id']}/remediation-attempts",
        json={
            "client_request_id": str(uuid.uuid4()),
            "expected_version": 3,
            "action_description": "duplicate pending attempt",
        },
    )
    assert second_pending.status_code == 409


def test_reverification_review_requires_resolution_and_reject_reopens_case(
    client: TestClient,
    project_and_baseline: tuple[dict, dict],
    valid_mp4_bytes: bytes,
) -> None:
    project, baseline = project_and_baseline
    normal_job = _submit(client, project, baseline, valid_mp4_bytes, analyzer="stub")
    invalid_normal_resolution = client.post(
        f"/api/v1/verifications/{normal_job['id']}/review",
        headers=REVIEWER,
        json={
            "decision": "approve",
            "reviewer": "reviewer",
            "note": "normal job",
            "remediation_resolution": "resolved",
        },
    )
    assert invalid_normal_resolution.status_code == 422

    case, _ = _approved_operational_case(client, project, baseline, valid_mp4_bytes)
    started = client.post(
        f"/api/v1/finding-cases/{case['id']}/start-remediation",
        json={
            "request_id": str(uuid.uuid4()),
            "expected_version": case["version"],
            "assignee": "operator",
            "action_description": "start",
        },
    ).json()
    attempt = client.post(
        f"/api/v1/finding-cases/{case['id']}/remediation-attempts",
        json={
            "client_request_id": str(uuid.uuid4()),
            "expected_version": started["version"],
            "action_description": "submit evidence",
        },
    ).json()
    recheck = _submit(
        client,
        project,
        baseline,
        valid_mp4_bytes,
        analyzer="stub",
        attempt_id=attempt["id"],
    )
    missing_resolution = client.post(
        f"/api/v1/verifications/{recheck['id']}/review",
        headers=REVIEWER,
        json={"decision": "approve", "reviewer": "reviewer", "note": "must decide"},
    )
    assert missing_resolution.status_code == 422
    rejected = client.post(
        f"/api/v1/verifications/{recheck['id']}/review",
        headers=REVIEWER,
        json={"decision": "reject", "reviewer": "reviewer", "note": "recheck invalid"},
    )
    assert rejected.status_code == 200, rejected.text
    detail = client.get(f"/api/v1/finding-cases/{case['id']}").json()
    assert detail["case"]["status"] == "remediation_in_progress"
    assert detail["attempts"][0]["resolution_decision"] == "not_resolved"
    assert detail["attempts"][0]["proof_id"] is None


def test_approved_not_resolved_reverification_seals_but_does_not_close(
    client: TestClient,
    project_and_baseline: tuple[dict, dict],
    valid_mp4_bytes: bytes,
) -> None:
    project, baseline = project_and_baseline
    case, _ = _approved_operational_case(client, project, baseline, valid_mp4_bytes)
    started = client.post(
        f"/api/v1/finding-cases/{case['id']}/start-remediation",
        json={
            "request_id": str(uuid.uuid4()),
            "expected_version": case["version"],
            "assignee": "operator",
            "action_description": "start",
        },
    ).json()
    attempt = client.post(
        f"/api/v1/finding-cases/{case['id']}/remediation-attempts",
        json={
            "client_request_id": str(uuid.uuid4()),
            "expected_version": started["version"],
            "action_description": "first repair",
        },
    ).json()
    recheck = _submit(
        client,
        project,
        baseline,
        valid_mp4_bytes,
        analyzer="stub",
        attempt_id=attempt["id"],
    )
    reviewed = client.post(
        f"/api/v1/verifications/{recheck['id']}/review",
        headers=REVIEWER,
        json={
            "decision": "approve",
            "reviewer": "reviewer",
            "note": "复验证据仍不支持关闭。",
            "remediation_resolution": "not_resolved",
        },
    )
    assert reviewed.status_code == 200, reviewed.text
    detail = client.get(f"/api/v1/finding-cases/{case['id']}").json()
    assert detail["case"]["status"] == "remediation_in_progress"
    assert detail["case"]["closure_proof_id"] is None
    assert detail["attempts"][0]["proof_id"] == reviewed.json()["proof"]["id"]
    assert detail["closure_evidence_status"] == "unsealed"


def test_reverification_binding_rejects_cross_project_and_attempt_reuse(
    client: TestClient,
    valid_mp4_bytes: bytes,
) -> None:
    project, baseline = _create_demo_project(client)
    other_project, other_baseline = _create_demo_project(client)
    source = _submit(client, project, baseline, valid_mp4_bytes, analyzer="demo_fixture")
    approved = client.post(
        f"/api/v1/verifications/{source['id']}/review",
        headers=REVIEWER,
        json={"decision": "approve", "reviewer": "reviewer", "note": "demo"},
    )
    assert approved.status_code == 200
    case = _triage(client, client.get(f"/api/v1/finding-cases?project_id={project['id']}").json()[0])
    started = client.post(
        f"/api/v1/finding-cases/{case['id']}/start-remediation",
        json={
            "request_id": str(uuid.uuid4()),
            "expected_version": case["version"],
            "assignee": "operator",
            "action_description": "start",
        },
    ).json()
    attempt = client.post(
        f"/api/v1/finding-cases/{case['id']}/remediation-attempts",
        json={
            "client_request_id": str(uuid.uuid4()),
            "expected_version": started["version"],
            "action_description": "repair",
        },
    ).json()
    wrong = client.post(
        "/api/v1/verifications",
        data={
            "project_id": other_project["id"],
            "baseline_id": other_baseline["id"],
            "analyzer": "stub",
            "remediation_attempt_id": attempt["id"],
        },
        files={"file": ("sample.mp4", valid_mp4_bytes, "video/mp4")},
    )
    assert wrong.status_code == 422
    correct = _submit(
        client,
        project,
        baseline,
        valid_mp4_bytes,
        analyzer="stub",
        attempt_id=attempt["id"],
    )
    reused = client.post(
        "/api/v1/verifications",
        data={
            "project_id": project["id"],
            "baseline_id": baseline["id"],
            "analyzer": "stub",
            "remediation_attempt_id": attempt["id"],
        },
        files={"file": ("sample.mp4", valid_mp4_bytes, "video/mp4")},
    )
    assert correct["status"] == "needs_review"
    assert reused.status_code == 409


def test_concurrent_reverification_binding_has_one_winner_and_no_orphan_job(
    client: TestClient,
    project_and_baseline: tuple[dict, dict],
    valid_mp4_bytes: bytes,
) -> None:
    project, baseline = project_and_baseline
    case, source_approval = _approved_operational_case(client, project, baseline, valid_mp4_bytes)
    started = client.post(
        f"/api/v1/finding-cases/{case['id']}/start-remediation",
        json={
            "request_id": str(uuid.uuid4()),
            "expected_version": case["version"],
            "assignee": "operator",
            "action_description": "start concurrent binding test",
        },
    ).json()
    attempt = client.post(
        f"/api/v1/finding-cases/{case['id']}/remediation-attempts",
        json={
            "client_request_id": str(uuid.uuid4()),
            "expected_version": started["version"],
            "action_description": "one remediation submission",
        },
    ).json()

    def bind(suffix: str) -> tuple[int, str | None]:
        response = client.post(
            "/api/v1/verifications",
            data={
                "project_id": project["id"],
                "baseline_id": baseline["id"],
                "analyzer": "stub",
                "remediation_attempt_id": attempt["id"],
                "metadata": f'{{"request":"{suffix}"}}',
            },
            files={"file": (f"sample-{suffix}.mp4", valid_mp4_bytes, "video/mp4")},
        )
        payload = response.json()
        return response.status_code, payload.get("id") if isinstance(payload, dict) else None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(bind, ["a", "b"]))
    assert sorted(status for status, _job_id in results) == [202, 409]
    winner_id = next(job_id for status, job_id in results if status == 202)
    assert winner_id is not None

    with client.app.state.database.session_factory() as db:
        stored_attempt = db.get(RemediationAttempt, attempt["id"])
        assert stored_attempt is not None
        assert stored_attempt.verification_job_id == winner_id
        job_ids = set(
            db.scalars(
                select(VerificationJob.id).where(VerificationJob.project_id == project["id"])
            ).all()
        )
        assert job_ids == {source_approval["job"]["id"], winner_id}


def test_readiness_rejects_closure_proof_substitution_and_frozen_attempt_drift(
    client: TestClient,
    project_and_baseline: tuple[dict, dict],
    valid_mp4_bytes: bytes,
) -> None:
    project, baseline = project_and_baseline
    case, source_approval = _approved_operational_case(client, project, baseline, valid_mp4_bytes)
    started = client.post(
        f"/api/v1/finding-cases/{case['id']}/start-remediation",
        json={
            "request_id": str(uuid.uuid4()),
            "expected_version": case["version"],
            "assignee": "operator",
            "action_description": "start graph integrity test",
        },
    ).json()
    original_action = "repair bound to a frozen closure proof"
    attempt = client.post(
        f"/api/v1/finding-cases/{case['id']}/remediation-attempts",
        json={
            "client_request_id": str(uuid.uuid4()),
            "expected_version": started["version"],
            "action_description": original_action,
        },
    ).json()
    recheck = _submit(
        client,
        project,
        baseline,
        valid_mp4_bytes,
        analyzer="stub",
        attempt_id=attempt["id"],
    )
    resolved = client.post(
        f"/api/v1/verifications/{recheck['id']}/review",
        headers=REVIEWER,
        json={
            "decision": "approve",
            "reviewer": "reviewer",
            "note": "reviewed remediation evidence",
            "remediation_resolution": "resolved",
        },
    )
    assert resolved.status_code == 200, resolved.text
    closure_proof_id = resolved.json()["proof"]["id"]
    source_proof_id = source_approval["proof"]["id"]
    assert client.get("/api/v1/readyz").status_code == 200

    with client.app.state.database.session_factory() as db:
        stored_case = db.get(FindingCase, case["id"])
        assert stored_case is not None
        stored_case.closure_proof_id = source_proof_id
        db.commit()
    assert client.get("/api/v1/readyz").status_code == 503

    with client.app.state.database.session_factory() as db:
        stored_case = db.get(FindingCase, case["id"])
        assert stored_case is not None
        stored_case.closure_proof_id = closure_proof_id
        db.commit()
    assert client.get("/api/v1/readyz").status_code == 200

    with client.app.state.database.session_factory() as db:
        stored_attempt = db.get(RemediationAttempt, attempt["id"])
        assert stored_attempt is not None
        stored_attempt.action_description = "tampered after closure"
        db.commit()
    assert client.get("/api/v1/readyz").status_code == 503

    with client.app.state.database.session_factory() as db:
        stored_attempt = db.get(RemediationAttempt, attempt["id"])
        assert stored_attempt is not None
        stored_attempt.action_description = original_action
        db.commit()
    assert client.get("/api/v1/readyz").status_code == 200

    with client.app.state.database.session_factory() as db:
        stored_case = db.get(FindingCase, case["id"])
        stored_attempt = db.get(RemediationAttempt, attempt["id"])
        assert stored_case is not None and stored_attempt is not None
        closure_proof = db.get(ProofRecord, closure_proof_id)
        closure_report = db.get(StructuredReport, stored_attempt.report_id)
        recheck_job_record = db.get(VerificationJob, stored_attempt.verification_job_id)
        assert closure_proof is not None and closure_report is not None and recheck_job_record is not None

        def rejects(message: str, mutate, restore) -> None:
            mutate()
            with pytest.raises(RemediationIntegrityError, match=message):
                validate_remediation_graph(
                    db,
                    client.app.state.storage,
                    stored_case,
                    attempts=[stored_attempt],
                )
            restore()
            validate_remediation_graph(
                db,
                client.app.state.storage,
                stored_case,
                attempts=[stored_attempt],
            )

        rejects(
            "active remediation attempt is missing",
            lambda: setattr(stored_case, "active_attempt_no", 99),
            lambda: setattr(stored_case, "active_attempt_no", None),
        )
        rejects(
            "not resolved",
            lambda: setattr(stored_attempt, "resolution_decision", "not_resolved"),
            lambda: setattr(stored_attempt, "resolution_decision", "resolved"),
        )
        rejects(
            "incomplete report/proof pair",
            lambda: setattr(stored_attempt, "report_id", None),
            lambda: setattr(stored_attempt, "report_id", closure_report.id),
        )
        original_baseline_id = recheck_job_record.baseline_id
        rejects(
            "invalid re-verification binding",
            lambda: setattr(recheck_job_record, "baseline_id", "cross-baseline"),
            lambda: setattr(recheck_job_record, "baseline_id", original_baseline_id),
        )
        original_project_id = closure_report.project_id
        rejects(
            "sealed artifacts have invalid bindings",
            lambda: setattr(closure_report, "project_id", "cross-project"),
            lambda: setattr(closure_report, "project_id", original_project_id),
        )
        original_job_status = recheck_job_record.status
        rejects(
            "not approved and sealed",
            lambda: setattr(recheck_job_record, "status", "needs_review"),
            lambda: setattr(recheck_job_record, "status", original_job_status),
        )
        original_resolved_by = stored_attempt.resolved_by
        rejects(
            "incomplete reviewer metadata",
            lambda: setattr(stored_attempt, "resolved_by", None),
            lambda: setattr(stored_attempt, "resolved_by", original_resolved_by),
        )
        original_closed_by = stored_case.closed_by
        rejects(
            "differs from the reviewer resolution",
            lambda: setattr(stored_case, "closed_by", "different-reviewer"),
            lambda: setattr(stored_case, "closed_by", original_closed_by),
        )
        rejects(
            "Pending remediation attempt has reviewer resolution metadata",
            lambda: setattr(stored_attempt, "resolution_decision", "pending"),
            lambda: setattr(stored_attempt, "resolution_decision", "resolved"),
        )
        rejects(
            "Pre-remediation case unexpectedly has remediation attempts",
            lambda: setattr(stored_case, "status", "open"),
            lambda: setattr(stored_case, "status", "closed"),
        )
        rejects(
            "Verification-pending case has no active re-verification",
            lambda: setattr(stored_case, "status", "verification_pending"),
            lambda: setattr(stored_case, "status", "closed"),
        )
