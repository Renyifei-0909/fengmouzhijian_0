from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from copy import deepcopy
import json
from pathlib import Path
from threading import Barrier, Lock
from typing import Any, Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.config import Settings
from app.main import create_app
from app.models import (
    AuditEvent,
    DesignBaseline,
    EvidenceAsset,
    HumanReview,
    ProofRecord,
    SealOperation,
    StructuredReport,
    VerificationJob,
    new_id,
)
from app.services import sealing
from app.services.storage import FileStorage, ValidatedStoredFile


PNG_BYTES = b"\x89PNG\r\n\x1a\n" + bytes(range(64))
REVIEWER_HEADERS = {"X-API-Key": "test-reviewer-key"}
APPROVE_PAYLOAD = {
    "decision": "approve",
    "reviewer": "Saga test reviewer",
    "note": "Only validates recoverable sealing mechanics.",
}


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        environment="test",
        database_url=f"sqlite:///{tmp_path / 'sealing-saga.db'}",
        storage_root=tmp_path / "storage",
        max_upload_bytes=2 * 1024 * 1024,
        allow_demo_analyzer=True,
        operator_api_key="test-operator-key",
        reviewer_api_key="test-reviewer-key",
        auditor_api_key="test-auditor-key",
        cors_origins=("http://testserver",),
    )


def _start_client(app: FastAPI) -> TestClient:
    client = TestClient(app)
    client.headers.update({"X-API-Key": "test-operator-key"})
    return client


def _seed_reviewable_job(client: TestClient) -> str:
    project_response = client.post(
        "/api/v1/projects",
        json={"code": "SAGA-001", "name": "Saga sealing test", "location": "anonymous site"},
    )
    assert project_response.status_code == 201, project_response.text
    project = project_response.json()
    baseline_response = client.post(
        f"/api/v1/projects/{project['id']}/baselines",
        json={
            "site_id": "SAGA-SITE",
            "procedure_code": "SAGA-PROCEDURE",
            "version": "v1",
            "source_type": "manual",
            "expected": {"scene_type": "test"},
        },
    )
    assert baseline_response.status_code == 201, baseline_response.text
    response = client.post(
        "/api/v1/verifications",
        data={
            "project_id": project["id"],
            "baseline_id": baseline_response.json()["id"],
            "analyzer": "stub",
        },
        files={"file": ("saga.png", PNG_BYTES, "image/png")},
    )
    assert response.status_code == 202, response.text
    job_id = response.json()["id"]
    detail = client.get(f"/api/v1/verifications/{job_id}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["job"]["status"] == "needs_review"
    return job_id


def _approve(client: TestClient, job_id: str):
    return client.post(
        f"/api/v1/verifications/{job_id}/review",
        json=APPROVE_PAYLOAD,
        headers=REVIEWER_HEADERS,
    )


def _ledger_rows(storage: FileStorage) -> list[dict[str, Any]]:
    if not storage.ledger_path.exists():
        return []
    return [json.loads(line) for line in storage.ledger_path.read_text(encoding="utf-8").splitlines() if line]


def _snapshot(app: FastAPI, job_id: str) -> dict[str, Any]:
    with app.state.database.session_factory() as db:
        job = db.get(VerificationJob, job_id)
        assert job is not None
        reviews = list(db.scalars(select(HumanReview).where(HumanReview.job_id == job_id)).all())
        operations = list(db.scalars(select(SealOperation).where(SealOperation.job_id == job_id)).all())
        reports = list(db.scalars(select(StructuredReport).where(StructuredReport.job_id == job_id)).all())
        proofs = list(
            db.scalars(
                select(ProofRecord)
                .join(StructuredReport, ProofRecord.report_id == StructuredReport.id)
                .where(StructuredReport.job_id == job_id)
            ).all()
        )
        approved_audits = list(
            db.scalars(
                select(AuditEvent).where(
                    AuditEvent.entity_type == "verification_job",
                    AuditEvent.entity_id == job_id,
                    AuditEvent.action == "approved_and_sealed",
                )
            ).all()
        )
        operation = operations[0] if len(operations) == 1 else None
        return {
            "job_status": job.status,
            "review_ids": [item.id for item in reviews],
            "report_ids": [item.id for item in reports],
            "proof_ids": [item.id for item in proofs],
            "proof_archive_ids": [item.archive_id for item in proofs],
            "approved_audit_count": len(approved_audits),
            "operation_count": len(operations),
            "operation_id": operation.id if operation else None,
            "operation_state": operation.state if operation else None,
            "operation_review_id": operation.review_id if operation else None,
            "operation_report_id": operation.report_id if operation else None,
            "operation_archive_id": operation.archive_id if operation else None,
            "operation_attempt_count": operation.attempt_count if operation else None,
            "operation_last_error": operation.last_error if operation else None,
        }


def _assert_completed_singleton(app: FastAPI, job_id: str) -> dict[str, Any]:
    snapshot = _snapshot(app, job_id)
    assert snapshot["job_status"] == "approved"
    assert snapshot["operation_count"] == 1
    assert snapshot["operation_state"] == "completed"
    assert snapshot["operation_last_error"] is None
    assert len(snapshot["review_ids"]) == 1
    assert len(snapshot["report_ids"]) == 1
    assert len(snapshot["proof_ids"]) == 1
    assert snapshot["approved_audit_count"] == 1
    assert snapshot["operation_review_id"] == snapshot["review_ids"][0]
    assert snapshot["operation_report_id"] == snapshot["report_ids"][0]
    assert snapshot["operation_archive_id"] == snapshot["proof_archive_ids"][0]
    rows = _ledger_rows(app.state.storage)
    assert len(rows) == 1
    assert rows[0]["archive_id"] == snapshot["operation_archive_id"]
    assert rows[0]["ledger_index"] == 0
    assert (app.state.storage.report_dir / f"{snapshot['operation_report_id']}.json").is_file()
    assert (app.state.storage.report_dir / f"{snapshot['operation_report_id']}.html").is_file()
    assert (app.state.storage.archive_dir / f"{snapshot['operation_archive_id']}.zip").is_file()
    return snapshot


def test_approve_completes_one_seal_operation_and_one_artifact_set(tmp_path: Path) -> None:
    app = create_app(_settings(tmp_path))
    with _start_client(app) as client:
        job_id = _seed_reviewable_job(client)
        response = _approve(client, job_id)
        assert response.status_code == 200, response.text
        snapshot = _assert_completed_singleton(app, job_id)
        assert snapshot["operation_attempt_count"] == 1
        assert response.json()["report"]["id"] == snapshot["operation_report_id"]
        assert response.json()["proof"]["archive_id"] == snapshot["operation_archive_id"]
        integrity = client.get(f"/api/v1/proofs/{snapshot['proof_ids'][0]}/verify")
        assert integrity.status_code == 200, integrity.text
        assert integrity.json()["valid"] is True
        assert client.get("/api/v1/readyz").status_code == 200


def test_staging_failure_is_resumable_with_stable_ids_and_no_duplicate_ledger(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = create_app(_settings(tmp_path))
    original = sealing._build_staged_artifacts
    injected = False

    def fail_once(*args, **kwargs):
        nonlocal injected
        if not injected:
            injected = True
            raise OSError("injected staging failure")
        return original(*args, **kwargs)

    monkeypatch.setattr(sealing, "_build_staged_artifacts", fail_once)
    with _start_client(app) as client:
        job_id = _seed_reviewable_job(client)
        failed = _approve(client, job_id)
        assert failed.status_code == 503, failed.text
        first = _snapshot(app, job_id)
        assert first["job_status"] == "sealing"
        assert first["operation_state"] == "requested"
        assert first["operation_attempt_count"] == 1
        assert "injected staging failure" in first["operation_last_error"]
        assert len(first["review_ids"]) == 1
        assert first["report_ids"] == []
        assert first["proof_ids"] == []
        assert _ledger_rows(app.state.storage) == []

        recovered = _approve(client, job_id)
        assert recovered.status_code == 200, recovered.text
        completed = _assert_completed_singleton(app, job_id)
        assert completed["operation_attempt_count"] == 2
        assert completed["operation_id"] == first["operation_id"]
        assert completed["operation_review_id"] == first["operation_review_id"]
        assert completed["operation_report_id"] == first["operation_report_id"]
        assert completed["operation_archive_id"] == first["operation_archive_id"]


def test_final_database_failure_after_ledger_publish_resumes_without_duplicate_row(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = create_app(_settings(tmp_path))
    session_class = app.state.database.session_factory.class_
    original_commit = session_class.commit
    injected = False

    def fail_completed_commit(session) -> None:
        nonlocal injected
        completing = any(
            isinstance(item, SealOperation) and item.state == "completed"
            for item in session.identity_map.values()
        )
        if completing and not injected:
            injected = True
            raise OSError("injected final database commit failure")
        original_commit(session)

    monkeypatch.setattr(session_class, "commit", fail_completed_commit)
    with _start_client(app) as client:
        job_id = _seed_reviewable_job(client)
        failed = _approve(client, job_id)
        assert failed.status_code == 503, failed.text
        first = _snapshot(app, job_id)
        assert first["job_status"] == "sealing"
        assert first["operation_state"] == "ledger_appended"
        assert first["operation_attempt_count"] == 1
        assert "injected final database commit failure" in first["operation_last_error"]
        assert first["report_ids"] == []
        assert first["proof_ids"] == []
        ledger_before = app.state.storage.ledger_path.read_bytes()
        assert len(_ledger_rows(app.state.storage)) == 1
        with app.state.database.session_factory() as db:
            failure_audits = list(
                db.scalars(
                    select(AuditEvent).where(
                        AuditEvent.entity_type == "seal_operation",
                        AuditEvent.entity_id == first["operation_id"],
                        AuditEvent.action == "seal_attempt_failed",
                    )
                ).all()
            )
        assert len(failure_audits) == 1
        assert failure_audits[0].payload_json["state"] == "ledger_appended"
        failed_detail = client.get(f"/api/v1/verifications/{job_id}")
        assert failed_detail.status_code == 200, failed_detail.text
        recovery = failed_detail.json()["recovery"]
        assert recovery["action"] == "resume_sealing"
        assert recovery["retryable"] is True
        assert recovery["operation_state"] == "ledger_appended"
        assert recovery["attempt_count"] == 1
        assert "injected final database commit failure" in recovery["last_error"]

        recovered = _approve(client, job_id)
        assert recovered.status_code == 200, recovered.text
        completed = _assert_completed_singleton(app, job_id)
        assert completed["operation_attempt_count"] == 2
        assert completed["operation_id"] == first["operation_id"]
        assert completed["operation_report_id"] == first["operation_report_id"]
        assert completed["operation_archive_id"] == first["operation_archive_id"]
        assert completed["proof_ids"] == [first["operation_archive_id"].removeprefix("ARC-")]
        assert app.state.storage.ledger_path.read_bytes() == ledger_before
        completed_recovery = client.get(f"/api/v1/verifications/{job_id}").json()["recovery"]
        assert completed_recovery["action"] == "none"
        assert completed_recovery["operation_state"] == "completed"
        assert completed_recovery["last_error"] is None
        with app.state.database.session_factory() as db:
            failure_count = len(
                list(
                    db.scalars(
                        select(AuditEvent).where(
                            AuditEvent.entity_type == "seal_operation",
                            AuditEvent.entity_id == first["operation_id"],
                            AuditEvent.action == "seal_attempt_failed",
                        )
                    ).all()
                )
            )
        assert failure_count == 1


def test_commit_acknowledgement_loss_returns_persisted_success_without_false_failure_audit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = create_app(_settings(tmp_path))
    session_class = app.state.database.session_factory.class_
    original_commit = session_class.commit
    injected = False

    def commit_then_lose_acknowledgement(session) -> None:
        nonlocal injected
        completing = any(
            isinstance(item, SealOperation) and item.state == "completed"
            for item in session.identity_map.values()
        )
        original_commit(session)
        if completing and not injected:
            injected = True
            raise OSError("injected commit acknowledgement loss")

    monkeypatch.setattr(session_class, "commit", commit_then_lose_acknowledgement)
    with _start_client(app) as client:
        job_id = _seed_reviewable_job(client)
        approved = _approve(client, job_id)
        assert approved.status_code == 200, approved.text
        assert injected is True
        snapshot = _assert_completed_singleton(app, job_id)
        assert snapshot["operation_last_error"] is None
        with app.state.database.session_factory() as db:
            false_failures = list(
                db.scalars(
                    select(AuditEvent).where(
                        AuditEvent.entity_type == "seal_operation",
                        AuditEvent.entity_id == snapshot["operation_id"],
                        AuditEvent.action == "seal_attempt_failed",
                    )
                ).all()
            )
        assert false_failures == []
        assert client.get("/api/v1/readyz").json() == {"status": "ready"}


def test_commit_reconciliation_persists_completed_graph_failure_as_manual_attention(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = create_app(_settings(tmp_path))
    original_complete = sealing._complete_operation
    injected = False

    def complete_then_corrupt_graph_and_lose_ack(db, storage, operation, actor) -> None:
        nonlocal injected
        original_complete(db, storage, operation, actor)
        if not injected:
            injected = True
            with app.state.database.session_factory() as tamper:
                tamper.add(
                    AuditEvent(
                        entity_type="verification_job",
                        entity_id=operation.job_id,
                        action="approved_and_sealed",
                        actor="tamper-test",
                        payload_json={
                            "report_id": operation.report_id,
                            "archive_id": operation.archive_id,
                            "seal_operation_id": operation.id,
                        },
                    )
                )
                tamper.commit()
            raise OSError("injected commit acknowledgement loss after graph corruption")

    monkeypatch.setattr(sealing, "_complete_operation", complete_then_corrupt_graph_and_lose_ack)
    with _start_client(app) as client:
        job_id = _seed_reviewable_job(client)
        failed = _approve(client, job_id)
        assert failed.status_code == 409, failed.text
        assert injected is True

        with app.state.database.session_factory() as db:
            operation = db.scalar(select(SealOperation).where(SealOperation.job_id == job_id))
            assert operation is not None
            assert operation.state == "manual_attention"
            assert "invalid approval audit" in (operation.last_error or "")
            manual_audits = list(
                db.scalars(
                    select(AuditEvent).where(
                        AuditEvent.entity_type == "seal_operation",
                        AuditEvent.entity_id == operation.id,
                        AuditEvent.action == "seal_manual_attention",
                    )
                ).all()
            )
            assert len(manual_audits) == 1

        recovery = client.get(f"/api/v1/verifications/{job_id}").json()["recovery"]
        assert recovery["action"] == "integrity_review"
        assert recovery["retryable"] is False
        assert recovery["operation_state"] == "manual_attention"
        assert "invalid approval audit" in recovery["last_error"]
        assert client.get("/api/v1/readyz").status_code == 503

        replay = _approve(client, job_id)
        assert replay.status_code == 409, replay.text
        with app.state.database.session_factory() as db:
            operation = db.scalar(select(SealOperation).where(SealOperation.job_id == job_id))
            assert operation is not None and operation.state == "manual_attention"
            manual_audits = list(
                db.scalars(
                    select(AuditEvent).where(
                        AuditEvent.entity_type == "seal_operation",
                        AuditEvent.entity_id == operation.id,
                        AuditEvent.action == "seal_manual_attention",
                    )
                ).all()
            )
            assert len(manual_audits) == 1


def test_staging_cleanup_failure_does_not_reclassify_completed_seal_as_failed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = create_app(_settings(tmp_path))
    original_rmtree = sealing.shutil.rmtree
    injected = False

    def fail_staging_cleanup_once(path, *args, **kwargs):
        nonlocal injected
        candidate = Path(path)
        if candidate.parent == app.state.storage.seal_staging_dir and not injected:
            injected = True
            raise OSError("injected staging cleanup failure")
        return original_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(sealing.shutil, "rmtree", fail_staging_cleanup_once)
    with _start_client(app) as client:
        job_id = _seed_reviewable_job(client)
        approved = _approve(client, job_id)
        assert approved.status_code == 200, approved.text
        snapshot = _assert_completed_singleton(app, job_id)
        staging = app.state.storage.seal_staging_dir / snapshot["operation_id"]
        assert staging.is_dir()
        assert snapshot["operation_last_error"] is None

        replay = _approve(client, job_id)
        assert replay.status_code == 200, replay.text
        assert not staging.exists()
        after = _assert_completed_singleton(app, job_id)
        assert after["operation_attempt_count"] == snapshot["operation_attempt_count"]
        with app.state.database.session_factory() as db:
            false_failure_count = len(
                list(
                    db.scalars(
                        select(AuditEvent).where(
                            AuditEvent.entity_type == "seal_operation",
                            AuditEvent.entity_id == snapshot["operation_id"],
                            AuditEvent.action == "seal_attempt_failed",
                        )
                    ).all()
                )
            )
        assert false_failure_count == 0


def test_staging_symlink_after_commit_does_not_reclassify_completed_seal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = create_app(_settings(tmp_path))
    original_complete = sealing._complete_operation
    protected = tmp_path / "protected-cleanup-target"
    protected.mkdir()
    marker = protected / "must-survive.txt"
    marker.write_text("do not follow staging symlinks", encoding="utf-8")
    injected = False

    def replace_staging_with_symlink(db, storage, operation, actor):
        nonlocal injected
        original_complete(db, storage, operation, actor)
        if not injected:
            staging = storage.seal_staging_dir / operation.id
            sealing.shutil.rmtree(staging)
            staging.symlink_to(protected, target_is_directory=True)
            injected = True

    monkeypatch.setattr(sealing, "_complete_operation", replace_staging_with_symlink)
    with _start_client(app) as client:
        job_id = _seed_reviewable_job(client)
        approved = _approve(client, job_id)
        assert approved.status_code == 200, approved.text
        assert injected is True
        snapshot = _assert_completed_singleton(app, job_id)
        staging = app.state.storage.seal_staging_dir / snapshot["operation_id"]
        assert staging.is_symlink()
        assert marker.read_text(encoding="utf-8") == "do not follow staging symlinks"
        assert client.get("/api/v1/readyz").json() == {"status": "ready"}
        staging.unlink()


def test_concurrent_approve_has_one_winner_and_one_conflict(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = create_app(_settings(tmp_path))
    original_validate = FileStorage.validate_evidence_file
    validations_ready = Barrier(2)
    requests_ready = Barrier(3)
    count_lock = Lock()
    validation_count = 0

    @contextmanager
    def synchronize_initial_validation(
        storage: FileStorage,
        **kwargs,
    ) -> Iterator[ValidatedStoredFile]:
        nonlocal validation_count
        with original_validate(storage, **kwargs) as validated:
            with count_lock:
                validation_count += 1
                current = validation_count
            if current <= 2:
                validations_ready.wait(timeout=10)
            yield validated

    with _start_client(app) as client:
        job_id = _seed_reviewable_job(client)
        monkeypatch.setattr(FileStorage, "validate_evidence_file", synchronize_initial_validation)

        def invoke_approve():
            requests_ready.wait(timeout=10)
            return _approve(client, job_id)

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(invoke_approve) for _ in range(2)]
            requests_ready.wait(timeout=10)
            responses = [future.result(timeout=20) for future in futures]

        assert sorted(response.status_code for response in responses) == [200, 409]
        completed = _assert_completed_singleton(app, job_id)
        assert completed["operation_attempt_count"] == 1
        assert validation_count >= 4


def test_startup_recovers_an_incomplete_seal_operation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(tmp_path)
    first_app = create_app(settings)
    original = sealing._build_staged_artifacts
    injected = False

    def fail_once(*args, **kwargs):
        nonlocal injected
        if not injected:
            injected = True
            raise OSError("injected pre-restart staging failure")
        return original(*args, **kwargs)

    monkeypatch.setattr(sealing, "_build_staged_artifacts", fail_once)
    with _start_client(first_app) as client:
        job_id = _seed_reviewable_job(client)
        failed = _approve(client, job_id)
        assert failed.status_code == 503, failed.text
        before = _snapshot(first_app, job_id)
        assert before["job_status"] == "sealing"
        assert before["operation_state"] == "requested"
        assert before["operation_attempt_count"] == 1

    restarted_app = create_app(settings)
    with _start_client(restarted_app) as restarted:
        completed = _assert_completed_singleton(restarted_app, job_id)
        assert completed["operation_attempt_count"] == 2
        assert completed["operation_id"] == before["operation_id"]
        assert completed["operation_review_id"] == before["operation_review_id"]
        assert completed["operation_report_id"] == before["operation_report_id"]
        assert completed["operation_archive_id"] == before["operation_archive_id"]
        assert restarted.get("/api/v1/readyz").json() == {"status": "ready"}


def test_resume_rebuilds_missing_staging_with_the_frozen_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = create_app(_settings(tmp_path))
    original_publish = sealing._publish_artifacts
    injected = False

    def fail_before_publish(*args, **kwargs):
        nonlocal injected
        if not injected:
            injected = True
            raise OSError("injected failure before artifact publication")
        return original_publish(*args, **kwargs)

    monkeypatch.setattr(sealing, "_publish_artifacts", fail_before_publish)
    with _start_client(app) as client:
        job_id = _seed_reviewable_job(client)
        failed = _approve(client, job_id)
        assert failed.status_code == 503, failed.text
        before = _snapshot(app, job_id)
        assert before["operation_state"] == "artifacts_staged"
        staging = app.state.storage.seal_staging_dir / before["operation_id"]
        assert staging.is_dir()
        for path in staging.iterdir():
            path.unlink()

        recovered = _approve(client, job_id)
        assert recovered.status_code == 200, recovered.text
        completed = _assert_completed_singleton(app, job_id)
        assert completed["operation_report_id"] == before["operation_report_id"]
        assert completed["operation_archive_id"] == before["operation_archive_id"]


def test_resume_reuses_matching_ledger_and_database_rows_without_duplicates(tmp_path: Path) -> None:
    app = create_app(_settings(tmp_path))
    with _start_client(app) as client:
        job_id = _seed_reviewable_job(client)
        approved = _approve(client, job_id)
        assert approved.status_code == 200, approved.text
        before = _assert_completed_singleton(app, job_id)
        ledger_before = app.state.storage.ledger_path.read_bytes()

        with app.state.database.session_factory() as db:
            operation = db.get(SealOperation, before["operation_id"])
            job = db.get(VerificationJob, job_id)
            assert operation is not None and job is not None
            operation.state = "files_published"
            job.status = "sealing"
            db.commit()

        recovered = _approve(client, job_id)
        assert recovered.status_code == 200, recovered.text
        after = _assert_completed_singleton(app, job_id)
        assert after["operation_id"] == before["operation_id"]
        assert after["report_ids"] == before["report_ids"]
        assert after["proof_ids"] == before["proof_ids"]
        assert app.state.storage.ledger_path.read_bytes() == ledger_before


def test_local_sealing_guards_reject_invalid_ids_links_and_busy_lock(tmp_path: Path) -> None:
    storage = FileStorage(tmp_path / "guard-storage", 1024)
    storage.ensure()
    operation = SealOperation(
        id=new_id(),
        job_id=new_id(),
        review_id=new_id(),
        report_id=new_id(),
        archive_id=f"ARC-{new_id()}",
    )

    assert sealing._is_uuid(None) is False
    invalid_id = SealOperation(
        id="../escape",
        job_id=new_id(),
        review_id=new_id(),
        report_id=new_id(),
        archive_id=f"ARC-{new_id()}",
    )
    with pytest.raises(sealing.SealIntegrityError, match="invalid identifier"):
        sealing._validate_operation_identity(invalid_id)
    invalid_archive = SealOperation(
        id=new_id(),
        job_id=new_id(),
        review_id=new_id(),
        report_id=new_id(),
        archive_id="ARC-not-a-uuid",
    )
    with pytest.raises(sealing.SealIntegrityError, match="archive identifier"):
        sealing._validate_operation_identity(invalid_archive)

    storage.archive_dir.rmdir()
    with pytest.raises(sealing.SealIntegrityError, match="unavailable"):
        sealing._validate_storage_layout(storage)
    storage.archive_dir.mkdir()
    storage.report_dir.rmdir()
    storage.report_dir.symlink_to(storage.archive_dir, target_is_directory=True)
    with pytest.raises(sealing.SealIntegrityError, match="direct directory"):
        sealing._validate_storage_layout(storage)
    storage.report_dir.unlink()
    storage.report_dir.mkdir()
    storage.ledger_path.symlink_to(storage.root / "elsewhere-ledger.jsonl")
    with pytest.raises(sealing.SealIntegrityError, match="symbolic link"):
        sealing._validate_storage_layout(storage)
    storage.ledger_path.unlink()

    stage_target = storage.root / "stage-target"
    stage_target.mkdir()
    (storage.seal_staging_dir / operation.id).symlink_to(stage_target, target_is_directory=True)
    with pytest.raises(sealing.SealIntegrityError, match="symbolic link"):
        sealing._operation_staging_dir(storage, operation)
    (storage.seal_staging_dir / operation.id).unlink()

    lock_path = storage.seal_lock_dir / "busy.lock"
    with sealing._exclusive_file_lock(lock_path):
        with pytest.raises(sealing.SealBusyError, match="already running"):
            with sealing._exclusive_file_lock(lock_path, nonblocking=True):
                pytest.fail("busy lock unexpectedly acquired")

    with pytest.raises(sealing.SealIntegrityError, match="index or predecessor"):
        sealing._validated_ledger([{"ledger_index": 4, "previous_record_hash": "bad"}])
    malformed_hash_row = {
        "ledger_index": 0,
        "archive_id": f"ARC-{new_id()}",
        "manifest_sha256": "1" * 64,
        "archive_sha256": "2" * 64,
        "previous_record_hash": "0" * 64,
        "record_hash": "3" * 64,
        "purpose": "review",
        "evidence_grade": False,
        "merkle_root": "4" * 64,
    }
    with pytest.raises(sealing.SealIntegrityError, match="record hash"):
        sealing._validated_ledger([malformed_hash_row])

    database = create_app(_settings(tmp_path / "invalid-resume")).state.database
    database.create_all()
    with database.session_factory() as db:
        with pytest.raises(sealing.SealIntegrityError, match="identifier is invalid"):
            sealing.resume_seal_operation(
                db,
                storage,
                operation_id="not-a-uuid",
                actor="test",
            )
    database.engine.dispose()


def test_frozen_report_snapshot_rejects_every_linked_record_drift(tmp_path: Path) -> None:
    app = create_app(_settings(tmp_path))
    with _start_client(app) as client:
        job_id = _seed_reviewable_job(client)
        assert _approve(client, job_id).status_code == 200
        with app.state.database.session_factory() as db:
            job = db.get(VerificationJob, job_id)
            operation = db.scalar(select(SealOperation).where(SealOperation.job_id == job_id))
            review = db.scalar(select(HumanReview).where(HumanReview.job_id == job_id))
            assert job is not None and operation is not None and review is not None
            baseline = db.get(DesignBaseline, job.baseline_id)
            evidence = db.get(EvidenceAsset, job.evidence_id)
            assert baseline is not None and evidence is not None
            frozen = deepcopy(operation.report_content_json)

            mutations = [
                lambda value: None,
                lambda value: {**value, "human_review": "invalid"},
                lambda value: {**value, "analysis": {"changed": True}},
                lambda value: {
                    **value,
                    "design_baseline": {**value["design_baseline"], "sha256": "0" * 64},
                },
                lambda value: {
                    **value,
                    "evidence": {**value["evidence"], "id": new_id()},
                },
                lambda value: {
                    **value,
                    "human_review": {**value["human_review"], "note": "changed"},
                },
            ]
            for mutate in mutations:
                operation.report_content_json = mutate(deepcopy(frozen))
                with pytest.raises(sealing.SealIntegrityError):
                    sealing._validate_frozen_snapshot(
                        operation,
                        job=job,
                        review=review,
                        baseline=baseline,
                        evidence=evidence,
                    )


def test_readyz_detects_database_report_snapshot_tampering(tmp_path: Path) -> None:
    app = create_app(_settings(tmp_path))
    with _start_client(app) as client:
        job_id = _seed_reviewable_job(client)
        assert _approve(client, job_id).status_code == 200
        with app.state.database.session_factory() as db:
            report = db.scalar(select(StructuredReport).where(StructuredReport.job_id == job_id))
            assert report is not None
            tampered = deepcopy(report.content_json)
            tampered["truth_boundary"] = ["tampered database snapshot"]
            report.content_json = tampered
            db.commit()

        ready = client.get("/api/v1/readyz")
        assert ready.status_code == 503
        detail = ready.json()["detail"]
        assert detail["status"] == "integrity_incident"
        assert any("structured report" in issue for issue in app.state.sealing_integrity_issues)


def test_readyz_detects_completed_human_review_drift(tmp_path: Path) -> None:
    app = create_app(_settings(tmp_path))
    with _start_client(app) as client:
        job_id = _seed_reviewable_job(client)
        assert _approve(client, job_id).status_code == 200
        with app.state.database.session_factory() as db:
            review = db.scalar(select(HumanReview).where(HumanReview.job_id == job_id))
            assert review is not None
            review.note = "tampered after the frozen report was sealed"
            db.commit()

        ready = client.get("/api/v1/readyz")
        assert ready.status_code == 503
        assert any(
            "Human review differs from the frozen seal snapshot" in issue
            for issue in app.state.sealing_integrity_issues
        )


def test_readyz_rejects_a_completed_operation_with_a_stale_failure_error(tmp_path: Path) -> None:
    app = create_app(_settings(tmp_path))
    with _start_client(app) as client:
        job_id = _seed_reviewable_job(client)
        assert _approve(client, job_id).status_code == 200
        with app.state.database.session_factory() as db:
            operation = db.scalar(select(SealOperation).where(SealOperation.job_id == job_id))
            assert operation is not None and operation.state == "completed"
            operation.last_error = "stale error must not survive a completed seal"
            db.commit()

        ready = client.get("/api/v1/readyz")
        assert ready.status_code == 503
        assert any(
            "retains a failure error" in issue
            for issue in app.state.sealing_integrity_issues
        )
        recovery = client.get(f"/api/v1/verifications/{job_id}").json()["recovery"]
        assert recovery["action"] == "integrity_review"
        assert recovery["retryable"] is False
        assert recovery["last_error"] == "stale error must not survive a completed seal"


def test_commit_reconciliation_rejects_an_inconsistent_completed_database_graph(tmp_path: Path) -> None:
    app = create_app(_settings(tmp_path))
    with _start_client(app) as client:
        job_id = _seed_reviewable_job(client)
        assert _approve(client, job_id).status_code == 200
        with app.state.database.session_factory() as db:
            operation = db.scalar(select(SealOperation).where(SealOperation.job_id == job_id))
            assert operation is not None and operation.state == "completed"
            db.add(
                AuditEvent(
                    entity_type="verification_job",
                    entity_id=job_id,
                    action="approved_and_sealed",
                    actor="tamper-test",
                    payload_json={
                        "report_id": operation.report_id,
                        "archive_id": operation.archive_id,
                        "seal_operation_id": operation.id,
                    },
                )
            )
            db.commit()
            with pytest.raises(sealing.SealIntegrityError, match="invalid approval audit"):
                sealing._load_persisted_completion(db, app.state.storage, operation.id)
        replay = _approve(client, job_id)
        assert replay.status_code == 409, replay.text
        with app.state.database.session_factory() as db:
            operation = db.scalar(select(SealOperation).where(SealOperation.job_id == job_id))
            assert operation is not None
            assert operation.state == "manual_attention"
            assert "invalid approval audit" in (operation.last_error or "")
            manual_audits = list(
                db.scalars(
                    select(AuditEvent).where(
                        AuditEvent.entity_type == "seal_operation",
                        AuditEvent.entity_id == operation.id,
                        AuditEvent.action == "seal_manual_attention",
                    )
                ).all()
            )
            assert len(manual_audits) == 1

        recovery = client.get(f"/api/v1/verifications/{job_id}").json()["recovery"]
        assert recovery["action"] == "integrity_review"
        assert recovery["retryable"] is False
        assert recovery["operation_state"] == "manual_attention"
        assert "invalid approval audit" in recovery["last_error"]
        assert client.get("/api/v1/readyz").status_code == 503

        second_replay = _approve(client, job_id)
        assert second_replay.status_code == 409, second_replay.text
        with app.state.database.session_factory() as db:
            operation = db.scalar(select(SealOperation).where(SealOperation.job_id == job_id))
            assert operation is not None and operation.state == "manual_attention"
            manual_audit_count = len(
                list(
                    db.scalars(
                        select(AuditEvent).where(
                            AuditEvent.entity_type == "seal_operation",
                            AuditEvent.entity_id == operation.id,
                            AuditEvent.action == "seal_manual_attention",
                        )
                    ).all()
                )
            )
            assert manual_audit_count == 1


def test_legacy_random_proof_identifier_remains_compatible(tmp_path: Path) -> None:
    app = create_app(_settings(tmp_path))
    with _start_client(app) as client:
        job_id = _seed_reviewable_job(client)
        first = _approve(client, job_id)
        assert first.status_code == 200, first.text
        deterministic_id = first.json()["proof"]["id"]
        legacy_id = new_id()
        assert legacy_id != deterministic_id
        with app.state.database.session_factory() as db:
            proof = db.get(ProofRecord, deterministic_id)
            assert proof is not None
            proof.id = legacy_id
            db.commit()

        assert client.get("/api/v1/readyz").json() == {"status": "ready"}
        verified = client.get(
            f"/api/v1/proofs/{legacy_id}/verify",
            headers={"X-API-Key": "test-auditor-key"},
        )
        assert verified.status_code == 200, verified.text
        assert verified.json()["valid"] is True
        replay = _approve(client, job_id)
        assert replay.status_code == 200, replay.text
        assert replay.json()["proof"]["id"] == legacy_id
        with app.state.database.session_factory() as db:
            assert db.get(ProofRecord, deterministic_id) is None
            assert db.get(ProofRecord, legacy_id) is not None


def test_changed_archive_before_final_commit_enters_manual_attention(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = create_app(_settings(tmp_path))
    original_complete = sealing._complete_operation
    injected = False

    def tamper_then_complete(db, storage, operation, actor):
        nonlocal injected
        if not injected:
            injected = True
            archive = storage.archive_dir / f"{operation.archive_id}.zip"
            with archive.open("ab") as handle:
                handle.write(b"tampered-before-db-approval")
        return original_complete(db, storage, operation, actor)

    monkeypatch.setattr(sealing, "_complete_operation", tamper_then_complete)
    with _start_client(app) as client:
        job_id = _seed_reviewable_job(client)
        blocked = _approve(client, job_id)
        assert blocked.status_code == 409, blocked.text
        snapshot = _snapshot(app, job_id)
        assert snapshot["job_status"] == "sealing"
        assert snapshot["operation_state"] == "manual_attention"
        assert "missing or changed" in snapshot["operation_last_error"]
        assert snapshot["report_ids"] == []
        assert snapshot["proof_ids"] == []
        recovery = client.get(f"/api/v1/verifications/{job_id}").json()["recovery"]
        assert recovery["action"] == "integrity_review"
        assert recovery["retryable"] is False
        assert recovery["operation_state"] == "manual_attention"
        ready = client.get("/api/v1/readyz")
        assert ready.status_code == 503
        assert ready.json()["detail"]["issue_count"] >= 1


@pytest.mark.parametrize(
    ("missing_artifact", "identifier_field", "expected_issue"),
    [
        ("report_json", "operation_report_id", "missing or changed files"),
        ("archive", "operation_archive_id", "failed integrity verification"),
    ],
)
def test_restart_degrades_readiness_when_completed_artifact_is_missing(
    tmp_path: Path,
    missing_artifact: str,
    identifier_field: str,
    expected_issue: str,
) -> None:
    settings = _settings(tmp_path)
    first_app = create_app(settings)
    with _start_client(first_app) as client:
        job_id = _seed_reviewable_job(client)
        approved = _approve(client, job_id)
        assert approved.status_code == 200, approved.text
        completed = _assert_completed_singleton(first_app, job_id)
        if missing_artifact == "report_json":
            target = first_app.state.storage.report_dir / f"{completed['operation_report_id']}.json"
        else:
            target = first_app.state.storage.archive_dir / f"{completed['operation_archive_id']}.zip"
        target.unlink()
        live_ready = client.get("/api/v1/readyz")
        assert live_ready.status_code == 503

    restarted_app = create_app(settings)
    with _start_client(restarted_app) as restarted:
        ready = restarted.get("/api/v1/readyz")
        assert ready.status_code == 503, ready.text
        detail = ready.json()["detail"]
        assert detail["status"] == "integrity_incident"
        assert detail["issue_count"] >= 1
        assert any(
            completed[identifier_field] in issue and expected_issue in issue
            for issue in restarted_app.state.sealing_integrity_issues
        )
