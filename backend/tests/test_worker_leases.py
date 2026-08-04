from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import os
from pathlib import Path
import subprocess
import sys
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from app.config import Settings
from app.main import create_app
from app.models import (
    DesignBaseline,
    EvidenceAsset,
    Project,
    VerificationAttempt,
    VerificationAttemptOutcome,
    VerificationJob,
    VerificationJobLease,
    new_id,
    utcnow,
)
from app.services import analysis
from app.services.analyzers.remote_http import RemoteAnalyzerError
from app.services.storage import canonical_json_bytes, design_baseline_sha256, sha256_bytes
from app.worker import _sqlite_single_worker_lock


PNG_BYTES = b"\x89PNG\r\n\x1a\n" + bytes(range(64))


def _settings(tmp_path: Path, **updates: Any) -> Settings:
    values: dict[str, Any] = {
        "environment": "test",
        "database_url": f"sqlite:///{tmp_path / 'worker-leases.db'}",
        "storage_root": tmp_path / "storage",
        "max_upload_bytes": 2 * 1024 * 1024,
        "allow_demo_analyzer": True,
        "operator_api_key": "test-operator-key",
        "reviewer_api_key": "test-reviewer-key",
        "auditor_api_key": "test-auditor-key",
        "verification_lease_seconds": 5.0,
        "verification_heartbeat_seconds": 1.0,
        "cors_origins": ("http://testserver",),
    }
    values.update(updates)
    return Settings(**values)


def _seed(app: FastAPI) -> str:
    app.state.database.create_all()
    app.state.storage.ensure()
    project_id = new_id()
    baseline_id = new_id()
    evidence_id = new_id()
    job_id = new_id()
    baseline_fields = {
        "project_id": project_id,
        "site_id": "WORKER-SITE",
        "procedure_code": "WORKER-PROCEDURE",
        "version": "v1",
        "source_type": "manual",
        "expected": {},
    }
    stored_name = f"{evidence_id}.png"
    evidence_path = app.state.storage.evidence_dir / stored_name
    evidence_path.write_bytes(PNG_BYTES)
    with app.state.database.session_factory() as db:
        db.add(Project(id=project_id, code=f"WRK-{project_id[:8]}", name="Worker test", location="site"))
        db.flush()
        db.add(
            DesignBaseline(
                id=baseline_id,
                **baseline_fields,
                sha256=design_baseline_sha256(**baseline_fields),
            )
        )
        db.flush()
        db.add(
            EvidenceAsset(
                id=evidence_id,
                project_id=project_id,
                baseline_id=baseline_id,
                original_name="worker.png",
                stored_name=stored_name,
                storage_path=str(evidence_path),
                content_type="image/png",
                size_bytes=len(PNG_BYTES),
                sha256=sha256_bytes(PNG_BYTES),
                metadata_json={},
            )
        )
        db.flush()
        db.add(
            VerificationJob(
                id=job_id,
                project_id=project_id,
                baseline_id=baseline_id,
                evidence_id=evidence_id,
                analyzer_name="stub",
                analyzer_version="test-v1",
                status="queued",
                progress=0,
            )
        )
        db.commit()
    return job_id


def _result(app: FastAPI, job_id: str, writer: str) -> dict[str, Any]:
    with app.state.database.session_factory() as db:
        job = db.get(VerificationJob, job_id)
        assert job is not None
        evidence = db.get(EvidenceAsset, job.evidence_id)
        baseline = db.get(DesignBaseline, job.baseline_id)
        assert evidence is not None and baseline is not None
        return {
            "schema_version": "1.0",
            "analysis_mode": "stub",
            "evidence_grade": False,
            "analyzer": {"name": "stub", "version": "test-v1"},
            "provenance": {
                "kind": "test_adapter",
                "synthetic": False,
                "warning": f"writer={writer}",
            },
            "input": {
                "evidence_sha256": evidence.sha256,
                "baseline_sha256": baseline.sha256,
            },
            "observations": {"measurements": {}, "objects": [], "events": []},
            "alignment": {
                "status": "not_evaluated",
                "baseline_version": baseline.version,
                "differences": [],
            },
            "findings": [],
            "confidence": None,
            "recommended_action": "manual_review",
            "accuracy_claim": None,
        }


def _expire(app: FastAPI, job_id: str) -> None:
    with app.state.database.session_factory() as db:
        lease = db.get(VerificationJobLease, job_id)
        assert lease is not None
        lease.lease_expires_at = utcnow() - timedelta(seconds=1)
        db.commit()


def test_concurrent_claim_advances_only_one_generation(tmp_path: Path) -> None:
    app = create_app(_settings(tmp_path))
    job_id = _seed(app)
    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(
            pool.map(
                lambda worker: analysis.claim_verification_job(app, job_id, worker),
                ("worker-a", "worker-b"),
            )
        )
    winners = [claim for claim in claims if claim is not None]
    assert len(winners) == 1
    assert winners[0].generation == 1
    assert winners[0].attempt_count == 1
    with app.state.database.session_factory() as db:
        attempts = list(
            db.scalars(
                select(VerificationAttempt).where(
                    VerificationAttempt.job_id == job_id
                )
            ).all()
        )
    assert len(attempts) == 1
    assert attempts[0].id == winners[0].attempt_id
    assert attempts[0].worker_id in {"worker-a", "worker-b"}
    assert attempts[0].evidence_sha256 == sha256_bytes(PNG_BYTES)
    assert attempts[0].baseline_sha256
    assert attempts[0].max_attempts == 3


def test_attempt_and_outcome_tables_reject_update_and_delete(
    tmp_path: Path,
) -> None:
    app = create_app(_settings(tmp_path))
    job_id = _seed(app)
    claim = analysis.claim_verification_job(app, job_id, "immutable-worker")
    assert claim is not None
    result = _result(app, job_id, "immutable-worker")
    assert analysis._complete_verification_job(app, claim, result) is True

    statements = (
        (
            "UPDATE verification_attempts SET worker_id = 'tampered' "
            "WHERE id = :row_id",
            claim.attempt_id,
        ),
        (
            "DELETE FROM verification_attempts WHERE id = :row_id",
            claim.attempt_id,
        ),
    )
    with app.state.database.session_factory() as db:
        outcome = db.scalar(
            select(VerificationAttemptOutcome).where(
                VerificationAttemptOutcome.attempt_id == claim.attempt_id
            )
        )
        assert outcome is not None
        outcome_id = outcome.id
    statements += (
        (
            "UPDATE verification_attempt_outcomes "
            "SET error_code = 'TAMPERED' WHERE id = :row_id",
            outcome_id,
        ),
        (
            "DELETE FROM verification_attempt_outcomes WHERE id = :row_id",
            outcome_id,
        ),
    )
    for statement, row_id in statements:
        with pytest.raises(IntegrityError, match="append-only"):
            with app.state.database.engine.begin() as connection:
                connection.execute(text(statement), {"row_id": row_id})

    with app.state.database.session_factory() as db:
        attempt = db.get(VerificationAttempt, claim.attempt_id)
        outcome = db.get(VerificationAttemptOutcome, outcome_id)
        assert attempt is not None and attempt.worker_id == "immutable-worker"
        assert outcome is not None and outcome.disposition == "committed_success"


def test_idempotent_outcome_append_does_not_hide_other_constraint_failures(
    tmp_path: Path,
) -> None:
    app = create_app(_settings(tmp_path))
    job_id = _seed(app)
    claim = analysis.claim_verification_job(app, job_id, "constraint-worker")
    assert claim is not None
    with app.state.database.session_factory() as db:
        attempt = db.get(VerificationAttempt, claim.attempt_id)
        assert attempt is not None
        with pytest.raises(IntegrityError):
            analysis._append_attempt_outcome(
                db,
                attempt,
                disposition="lease_expired",
                result_json={},
                result_sha256=sha256_bytes(canonical_json_bytes({})),
                finished_at=utcnow(),
                allow_existing=True,
            )
        db.rollback()
        assert db.scalar(
            select(VerificationAttemptOutcome.id).where(
                VerificationAttemptOutcome.attempt_id == claim.attempt_id
            )
        ) is None


def test_attempt_api_redacts_worker_identity_and_exposes_result_digest(
    tmp_path: Path,
) -> None:
    app = create_app(_settings(tmp_path))
    job_id = _seed(app)
    claim = analysis.claim_verification_job(app, job_id, "sensitive-worker-id")
    assert claim is not None
    result = _result(app, job_id, "sensitive-worker-id")
    assert analysis._complete_verification_job(app, claim, result) is True

    with TestClient(app) as client:
        response = client.get(
            f"/api/v1/verifications/{job_id}",
            headers={"X-API-Key": "test-operator-key"},
        )

    assert response.status_code == 200, response.text
    attempts = response.json()["attempts"]
    assert len(attempts) == 1
    attempt = attempts[0]
    assert "worker_id" not in attempt
    assert attempt["worker_ref"].startswith("sha256:")
    assert len(attempt["worker_ref"]) == len("sha256:") + 64
    assert attempt["claimed_at"].endswith("Z")
    assert attempt["outcome"]["disposition"] == "committed_success"
    assert attempt["outcome"]["finished_at"].endswith("Z")
    assert attempt["outcome"]["result_sha256"] == sha256_bytes(
        canonical_json_bytes(result)
    )


def test_readyz_detects_attempt_result_digest_tampering(tmp_path: Path) -> None:
    app = create_app(_settings(tmp_path))
    job_id = _seed(app)
    claim = analysis.claim_verification_job(app, job_id, "tamper-probe-worker")
    assert claim is not None
    assert analysis._complete_verification_job(
        app,
        claim,
        _result(app, job_id, "tamper-probe-worker"),
    ) is True
    with app.state.database.engine.begin() as connection:
        connection.execute(
            text(
                "DROP TRIGGER "
                "trg_verification_attempt_outcomes_no_update"
            )
        )
        connection.execute(
            text(
                "UPDATE verification_attempt_outcomes "
                "SET result_sha256 = :digest WHERE attempt_id = :attempt_id"
            ),
            {"digest": "0" * 64, "attempt_id": claim.attempt_id},
        )

    with TestClient(app) as client:
        response = client.get("/api/v1/readyz")

    assert response.status_code == 503
    assert response.json()["detail"] == {
        "status": "integrity_incident",
        "issue_count": 2,
    }


def test_attempt_integrity_detects_input_snapshot_drift(tmp_path: Path) -> None:
    app = create_app(_settings(tmp_path))
    job_id = _seed(app)
    claim = analysis.claim_verification_job(app, job_id, "input-drift-worker")
    assert claim is not None
    with app.state.database.session_factory() as db:
        job = db.get(VerificationJob, job_id)
        assert job is not None
        evidence = db.get(EvidenceAsset, job.evidence_id)
        assert evidence is not None
        evidence.sha256 = "0" * 64
        db.commit()
    with app.state.database.session_factory() as db:
        issues = analysis.scan_verification_attempt_integrity(db)
    assert issues == [
        f"verification attempt {claim.attempt_id} disagrees with its evidence record"
    ]


def test_api_restart_preserves_a_live_worker_lease(tmp_path: Path) -> None:
    app = create_app(_settings(tmp_path))
    job_id = _seed(app)
    claim = analysis.claim_verification_job(app, job_id, "live-worker")
    assert claim is not None

    assert analysis.recover_pending_verification_jobs(app) == []
    with app.state.database.session_factory() as db:
        job = db.get(VerificationJob, job_id)
        lease = db.get(VerificationJobLease, job_id)
        assert job is not None and job.status == "running"
        assert lease is not None and lease.owner_id == "live-worker"
        assert lease.generation == claim.generation


def test_heartbeat_renews_only_the_current_unexpired_generation(tmp_path: Path) -> None:
    app = create_app(_settings(tmp_path))
    job_id = _seed(app)
    claim = analysis.claim_verification_job(app, job_id, "heartbeat-worker")
    assert claim is not None
    with app.state.database.session_factory() as db:
        before = db.get(VerificationJobLease, job_id)
        assert before is not None and before.lease_expires_at is not None
        first_deadline = before.lease_expires_at

    assert analysis.renew_verification_job_lease(app, claim) is True
    with app.state.database.session_factory() as db:
        renewed = db.get(VerificationJobLease, job_id)
        assert renewed is not None and renewed.heartbeat_at is not None
        assert renewed.lease_expires_at is not None
        assert renewed.lease_expires_at >= first_deadline

    _expire(app, job_id)
    assert analysis.renew_verification_job_lease(app, claim) is False


def test_dispatch_integrity_reports_an_expired_running_lease(tmp_path: Path) -> None:
    app = create_app(_settings(tmp_path))
    job_id = _seed(app)
    claim = analysis.claim_verification_job(app, job_id, "expired-worker")
    assert claim is not None
    _expire(app, job_id)
    with app.state.database.session_factory() as db:
        issues = analysis.scan_verification_dispatch_integrity(db)
    assert issues == [f"running verification job {job_id} has an expired lease"]


def test_lease_uses_database_clock_instead_of_worker_wall_clock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = create_app(_settings(tmp_path))
    job_id = _seed(app)
    monkeypatch.setattr(analysis, "utcnow", lambda: utcnow() + timedelta(days=3650))
    claim = analysis.claim_verification_job(app, job_id, "skewed-worker")
    assert claim is not None
    with app.state.database.session_factory() as db:
        lease = db.get(VerificationJobLease, job_id)
        assert lease is not None and lease.lease_expires_at is not None
        assert lease.lease_expires_at.year < utcnow().year + 2


def test_sqlite_external_mode_rejects_a_second_worker_process_lock(tmp_path: Path) -> None:
    settings = _settings(tmp_path, verification_execution_mode="external")
    with _sqlite_single_worker_lock(settings):
        with pytest.raises(RuntimeError, match="one local worker process only"):
            with _sqlite_single_worker_lock(settings):
                raise AssertionError("second lock unexpectedly succeeded")


def test_expired_owner_is_reassigned_and_stale_success_is_fenced(tmp_path: Path) -> None:
    app = create_app(_settings(tmp_path, verification_max_attempts=3))
    job_id = _seed(app)
    first = analysis.claim_verification_job(app, job_id, "worker-a")
    assert first is not None
    _expire(app, job_id)
    assert analysis.reap_expired_verification_jobs(app) == 1
    second = analysis.claim_verification_job(app, job_id, "worker-b")
    assert second is not None and second.generation > first.generation

    assert analysis._complete_verification_job(app, second, _result(app, job_id, "worker-b")) is True
    assert analysis._complete_verification_job(app, first, _result(app, job_id, "worker-a")) is False
    with app.state.database.session_factory() as db:
        job = db.get(VerificationJob, job_id)
        assert job is not None and job.status == "needs_review"
        assert job.result_json is not None
        assert job.result_json["provenance"]["warning"] == "writer=worker-b"
        attempt_rows = db.execute(
            select(VerificationAttempt, VerificationAttemptOutcome)
            .join(
                VerificationAttemptOutcome,
                VerificationAttemptOutcome.attempt_id == VerificationAttempt.id,
            )
            .where(VerificationAttempt.job_id == job_id)
            .order_by(VerificationAttempt.attempt_no)
        ).all()
        assert [outcome.disposition for _, outcome in attempt_rows] == [
            "lease_expired",
            "committed_success",
        ]
        assert attempt_rows[0][1].stage == "lease_reaper"


def test_expired_owner_failure_cannot_break_the_new_owner(tmp_path: Path) -> None:
    app = create_app(_settings(tmp_path, verification_max_attempts=3))
    job_id = _seed(app)
    first = analysis.claim_verification_job(app, job_id, "worker-a")
    assert first is not None
    _expire(app, job_id)
    assert analysis.reap_expired_verification_jobs(app) == 1
    second = analysis.claim_verification_job(app, job_id, "worker-b")
    assert second is not None

    assert analysis._fail_verification_job(app, first, RuntimeError("late failure")) is False
    with app.state.database.session_factory() as db:
        job = db.get(VerificationJob, job_id)
        lease = db.get(VerificationJobLease, job_id)
        assert job is not None and job.status == "running"
        assert lease is not None and lease.owner_id == "worker-b"


def test_retry_appends_a_new_attempt_without_rewriting_the_failure(
    tmp_path: Path,
) -> None:
    app = create_app(
        _settings(
            tmp_path,
            verification_execution_mode="external",
            verification_max_attempts=3,
        )
    )
    job_id = _seed(app)
    configured_version = str(
        analysis.analyzer_descriptor("stub", settings=app.state.settings)["version"]
    )
    with app.state.database.session_factory() as db:
        job = db.get(VerificationJob, job_id)
        assert job is not None
        job.analyzer_version = configured_version
        db.commit()

    first = analysis.claim_verification_job(app, job_id, "worker-a")
    assert first is not None
    assert analysis._fail_verification_job(
        app,
        first,
        RemoteAnalyzerError(
            "REMOTE_TIMEOUT",
            "temporary upstream timeout",
            retryable=True,
        ),
    ) is True

    with TestClient(app) as client:
        retried = client.post(
            f"/api/v1/verifications/{job_id}/retry",
            headers={"X-API-Key": "test-operator-key"},
        )
        assert retried.status_code == 200, retried.text
        second = analysis.claim_verification_job(app, job_id, "worker-b")
        assert second is not None
        result = _result(app, job_id, "worker-b")
        result["analyzer"]["version"] = configured_version
        assert analysis._complete_verification_job(app, second, result) is True
        detail = client.get(
            f"/api/v1/verifications/{job_id}",
            headers={"X-API-Key": "test-operator-key"},
        )
        assert client.get("/api/v1/readyz").status_code == 200

    attempts = detail.json()["attempts"]
    assert [attempt["attempt_no"] for attempt in attempts] == [1, 2]
    assert [
        attempt["outcome"]["disposition"]
        for attempt in attempts
    ] == ["committed_failure", "committed_success"]
    assert attempts[0]["outcome"]["error_code"] == "REMOTE_TIMEOUT"
    assert attempts[0]["outcome"]["error_retryable"] is True
    assert attempts[1]["outcome"]["result_sha256"]


def test_retry_budget_exhaustion_is_terminal_across_recovery(tmp_path: Path) -> None:
    app = create_app(_settings(tmp_path, verification_max_attempts=2))
    job_id = _seed(app)
    first = analysis.claim_verification_job(app, job_id, "worker-a")
    assert first is not None
    _expire(app, job_id)
    assert analysis.reap_expired_verification_jobs(app) == 1
    second = analysis.claim_verification_job(app, job_id, "worker-b")
    assert second is not None
    _expire(app, job_id)
    assert analysis.reap_expired_verification_jobs(app) == 1
    assert analysis.recover_pending_verification_jobs(app) == []

    with app.state.database.session_factory() as db:
        job = db.get(VerificationJob, job_id)
        lease = db.get(VerificationJobLease, job_id)
        assert job is not None and job.status == "failed"
        assert lease is not None and lease.dead_lettered_at is not None
        assert lease.attempt_count == 2
    assert analysis.claim_verification_job(app, job_id, "worker-c") is None


def test_external_api_only_enqueues_and_exposes_dispatch_state(tmp_path: Path) -> None:
    app = create_app(_settings(tmp_path, verification_execution_mode="external"))
    with TestClient(app) as client:
        client.headers.update({"X-API-Key": "test-operator-key"})
        project = client.post(
            "/api/v1/projects",
            json={"code": "EXT-001", "name": "External worker", "location": "site"},
        ).json()
        baseline = client.post(
            f"/api/v1/projects/{project['id']}/baselines",
            json={
                "site_id": "EXT-SITE",
                "procedure_code": "EXT-PROC",
                "version": "v1",
                "source_type": "manual",
                "expected": {},
            },
        ).json()
        response = client.post(
            "/api/v1/verifications",
            data={"project_id": project["id"], "baseline_id": baseline["id"], "analyzer": "stub"},
            files={"file": ("external.png", PNG_BYTES, "image/png")},
        )
        assert response.status_code == 202, response.text
        job_id = response.json()["id"]
        detail = client.get(f"/api/v1/verifications/{job_id}")
        assert detail.status_code == 200, detail.text
        assert detail.json()["job"]["status"] == "queued"
        assert detail.json()["dispatch"] == {
            "execution_mode": "external",
            "state": "unclaimed",
            "generation": 0,
            "attempt_count": 0,
            "max_attempts": 3,
            "heartbeat_at": None,
            "lease_expires_at": None,
        }


def test_independent_worker_process_consumes_the_persisted_queue(tmp_path: Path) -> None:
    settings = _settings(tmp_path, verification_execution_mode="external")
    app = create_app(settings)
    with TestClient(app) as client:
        client.headers.update({"X-API-Key": "test-operator-key"})
        project = client.post(
            "/api/v1/projects",
            json={"code": "PROC-001", "name": "Process worker", "location": "site"},
        ).json()
        baseline = client.post(
            f"/api/v1/projects/{project['id']}/baselines",
            json={
                "site_id": "PROC-SITE",
                "procedure_code": "PROC-WORKER",
                "version": "v1",
                "source_type": "manual",
                "expected": {},
            },
        ).json()
        queued = client.post(
            "/api/v1/verifications",
            data={"project_id": project["id"], "baseline_id": baseline["id"], "analyzer": "stub"},
            files={"file": ("process.png", PNG_BYTES, "image/png")},
        )
        assert queued.status_code == 202, queued.text
        job_id = queued.json()["id"]

    env = {
        **os.environ,
        "FENGMOU_ENVIRONMENT": "test",
        "FENGMOU_DATABASE_URL": settings.database_url,
        "FENGMOU_STORAGE_ROOT": str(settings.storage_root),
        "FENGMOU_VERIFICATION_EXECUTION_MODE": "external",
        "FENGMOU_OPERATOR_API_KEY": "test-operator-key",
        "FENGMOU_REVIEWER_API_KEY": "test-reviewer-key",
        "FENGMOU_AUDITOR_API_KEY": "test-auditor-key",
    }
    completed = subprocess.run(
        [sys.executable, "-m", "app.worker", "--once", "--worker-id", "process-test-worker"],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert completed.returncode == 0, completed.stderr
    assert '"event": "verification_worker_started"' in completed.stdout
    assert '"processed_jobs": 1' in completed.stdout
    with app.state.database.session_factory() as db:
        job = db.get(VerificationJob, job_id)
        lease = db.get(VerificationJobLease, job_id)
        assert job is not None and job.status == "needs_review"
        assert lease is not None and lease.owner_id is None and lease.generation == 1
