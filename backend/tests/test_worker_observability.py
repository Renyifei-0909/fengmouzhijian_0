from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import text

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
)


OUTCOME_DISPOSITIONS = {
    "committed_success",
    "committed_failure",
    "lease_expired",
    "lease_lost",
    "write_fenced",
}


def _settings(tmp_path, **updates) -> Settings:
    values = {
        "environment": "test",
        "database_url": f"sqlite:///{tmp_path / 'observability.db'}",
        "database_schema_mode": "create_all",
        "storage_root": tmp_path / "storage",
        "operator_api_key": "observability-operator",
        "reviewer_api_key": "observability-reviewer",
        "auditor_api_key": "observability-auditor",
        "verification_execution_mode": "external",
        "verification_queue_warning_seconds": 30,
        "verification_observability_window_seconds": 900,
    }
    values.update(updates)
    return Settings(**values)


def _base_records(db) -> tuple[Project, DesignBaseline]:
    project = Project(
        id=new_id(),
        code=f"OBS-{new_id()[:8]}",
        name="Worker observability fixture",
        location="anonymous-test-site",
    )
    baseline = DesignBaseline(
        id=new_id(),
        project_id=project.id,
        site_id="SITE-OBS",
        procedure_code="OBSERVABILITY",
        version="v1",
        source_type="manual",
        expected={"fixture": True},
        sha256="b" * 64,
    )
    db.add(project)
    db.flush()
    db.add(baseline)
    db.flush()
    return project, baseline


def _job_with_lease(
    db,
    *,
    project: Project,
    baseline: DesignBaseline,
    job_status: str,
    created_at: datetime,
    generation: int = 0,
    attempt_count: int = 0,
    owner_id: str | None = None,
    claimed_at: datetime | None = None,
    heartbeat_at: datetime | None = None,
    lease_expires_at: datetime | None = None,
    dead_lettered_at: datetime | None = None,
    evidence_digest: str = "a" * 64,
) -> tuple[VerificationJob, VerificationJobLease, EvidenceAsset]:
    evidence_id = new_id()
    evidence = EvidenceAsset(
        id=evidence_id,
        project_id=project.id,
        baseline_id=baseline.id,
        original_name=f"{evidence_id}.png",
        stored_name=f"{evidence_id}.png",
        storage_path=f"evidence/{evidence_id}.png",
        content_type="image/png",
        size_bytes=8,
        sha256=evidence_digest,
        metadata_json={"fixture": True},
        created_at=created_at,
    )
    job = VerificationJob(
        id=new_id(),
        project_id=project.id,
        baseline_id=baseline.id,
        evidence_id=evidence.id,
        analyzer_name="stub",
        analyzer_version="1.0.0",
        status=job_status,
        progress=50 if job_status == "running" else 0,
        created_at=created_at,
        started_at=claimed_at,
    )
    lease = VerificationJobLease(
        job_id=job.id,
        owner_id=owner_id,
        generation=generation,
        attempt_count=attempt_count,
        claimed_at=claimed_at,
        heartbeat_at=heartbeat_at,
        lease_expires_at=lease_expires_at,
        dead_lettered_at=dead_lettered_at,
        updated_at=created_at,
    )
    db.add(evidence)
    db.flush()
    db.add(job)
    db.flush()
    db.add(lease)
    db.flush()
    return job, lease, evidence


def test_empty_observability_snapshot_is_authenticated_and_healthy(tmp_path) -> None:
    with TestClient(create_app(_settings(tmp_path))) as client:
        path = "/api/v1/operations/verification-dispatch"
        assert client.get(path).status_code == 401
        response = client.get(path, headers={"X-API-Key": "observability-auditor"})
        assert response.status_code == 200
        payload = response.json()

    assert payload["status"] == "healthy"
    assert payload["generated_at"].endswith("Z")
    assert payload["execution_mode"] == "external"
    assert payload["jobs"] == {"total": 0, "by_status": {}}
    assert payload["dispatch"] == {
        "lease_rows": 0,
        "active_leases": 0,
        "expired_running_leases": 0,
        "unclaimed_queued_jobs": 0,
        "queued_over_warning_threshold": 0,
        "dead_letter_jobs": 0,
        "oldest_queued_seconds": None,
        "oldest_active_heartbeat_seconds": None,
    }
    assert payload["attempts"]["total"] == 0
    assert payload["attempts"]["open"] == 0
    assert set(payload["attempts"]["outcomes_total_by_disposition"]) == OUTCOME_DISPOSITIONS
    assert set(payload["attempts"]["outcomes_window_by_disposition"]) == OUTCOME_DISPOSITIONS
    assert payload["integrity"] == {
        "status": "ok",
        "dispatch_issue_count": 0,
        "attempt_issue_count": 0,
        "issue_count": 0,
    }
    assert payload["alerts"] == []
    assert "not an uptime SLA" in payload["truth_note"]


def test_live_attempt_is_counted_without_becoming_an_incident(tmp_path) -> None:
    settings = _settings(tmp_path)
    app = create_app(settings)
    with TestClient(app) as client:
        now = datetime.now(timezone.utc)
        with app.state.database.session_factory() as db:
            project, baseline = _base_records(db)
            job, _, evidence = _job_with_lease(
                db,
                project=project,
                baseline=baseline,
                job_status="running",
                created_at=now - timedelta(seconds=8),
                generation=1,
                attempt_count=1,
                owner_id="private-live-worker",
                claimed_at=now - timedelta(seconds=8),
                heartbeat_at=now - timedelta(seconds=2),
                lease_expires_at=now + timedelta(seconds=20),
            )
            db.add(
                VerificationAttempt(
                    id=new_id(),
                    job_id=job.id,
                    generation=1,
                    attempt_no=1,
                    worker_id="private-live-worker",
                    execution_mode="external",
                    analyzer_name=job.analyzer_name,
                    analyzer_version=job.analyzer_version,
                    evidence_sha256=evidence.sha256,
                    baseline_sha256=baseline.sha256,
                    max_attempts=settings.verification_max_attempts,
                    claimed_at=now - timedelta(seconds=8),
                )
            )
            db.commit()

        response = client.get(
            "/api/v1/operations/verification-dispatch",
            headers={"X-API-Key": "observability-operator"},
        )
        assert response.status_code == 200
        payload = response.json()
        assert client.get("/api/v1/readyz").status_code == 200

    assert payload["status"] == "healthy"
    assert payload["dispatch"]["active_leases"] == 1
    assert 0 <= payload["dispatch"]["oldest_active_heartbeat_seconds"] < 15
    assert payload["attempts"]["total"] == 1
    assert payload["attempts"]["open"] == 1
    assert payload["integrity"]["issue_count"] == 0
    assert "private-live-worker" not in response.text
    assert job.id not in response.text


def test_backlog_dead_letter_and_recent_instability_are_attention_not_unready(
    tmp_path,
) -> None:
    settings = _settings(tmp_path)
    app = create_app(settings)
    with TestClient(app) as client:
        now = datetime.now(timezone.utc)
        with app.state.database.session_factory() as db:
            project, baseline = _base_records(db)
            _job_with_lease(
                db,
                project=project,
                baseline=baseline,
                job_status="queued",
                created_at=now - timedelta(seconds=120),
                evidence_digest="a" * 64,
            )
            _job_with_lease(
                db,
                project=project,
                baseline=baseline,
                job_status="queued",
                created_at=now - timedelta(seconds=5),
                evidence_digest="e" * 64,
            )
            _job_with_lease(
                db,
                project=project,
                baseline=baseline,
                job_status="failed",
                created_at=now - timedelta(seconds=300),
                generation=3,
                attempt_count=3,
                dead_lettered_at=now - timedelta(seconds=60),
                evidence_digest="c" * 64,
            )
            expired_job, _, expired_evidence = _job_with_lease(
                db,
                project=project,
                baseline=baseline,
                job_status="failed",
                created_at=now - timedelta(seconds=45),
                generation=1,
                attempt_count=1,
                evidence_digest="d" * 64,
            )
            attempt = VerificationAttempt(
                id=new_id(),
                job_id=expired_job.id,
                generation=1,
                attempt_no=1,
                worker_id="private-expired-worker",
                execution_mode="external",
                analyzer_name=expired_job.analyzer_name,
                analyzer_version=expired_job.analyzer_version,
                evidence_sha256=expired_evidence.sha256,
                baseline_sha256=baseline.sha256,
                max_attempts=settings.verification_max_attempts,
                claimed_at=now - timedelta(seconds=40),
            )
            db.add(attempt)
            db.flush()
            db.add(
                VerificationAttemptOutcome(
                    id=new_id(),
                    attempt_id=attempt.id,
                    disposition="lease_expired",
                    stage="observability_fixture",
                    error_code="WORKER_LEASE_EXPIRED",
                    error_retryable=True,
                    error_message="Synthetic test lease expiry",
                    dead_lettered=False,
                    finished_at=now - timedelta(seconds=35),
                )
            )
            db.commit()

        response = client.get(
            "/api/v1/operations/verification-dispatch",
            headers={"X-API-Key": "observability-auditor"},
        )
        assert response.status_code == 200
        payload = response.json()
        assert client.get("/api/v1/readyz").status_code == 200

    assert payload["status"] == "attention"
    assert payload["jobs"]["total"] == 4
    assert payload["jobs"]["by_status"] == {"failed": 2, "queued": 2}
    assert payload["dispatch"]["unclaimed_queued_jobs"] == 2
    assert payload["dispatch"]["queued_over_warning_threshold"] == 1
    assert payload["dispatch"]["dead_letter_jobs"] == 1
    assert payload["dispatch"]["oldest_queued_seconds"] >= 100
    assert payload["attempts"]["outcomes_window_by_disposition"]["lease_expired"] == 1
    assert payload["attempts"]["recent_instability"] == 1
    assert payload["integrity"]["status"] == "ok"
    assert [alert["code"] for alert in payload["alerts"]] == [
        "DEAD_LETTER_PRESENT",
        "QUEUE_WAIT_EXCEEDED",
        "RECENT_LEASE_INSTABILITY",
    ]
    assert payload["alerts"][1]["count"] == 1
    assert "private-expired-worker" not in response.text
    assert expired_job.id not in response.text


def test_integrity_contradiction_is_incident_and_readyz_remains_fail_closed(
    tmp_path,
) -> None:
    settings = _settings(tmp_path)
    app = create_app(settings)
    with TestClient(app) as client:
        now = datetime.now(timezone.utc)
        with app.state.database.session_factory() as db:
            project, baseline = _base_records(db)
            incident_job, _, _ = _job_with_lease(
                db,
                project=project,
                baseline=baseline,
                job_status="running",
                created_at=now - timedelta(seconds=90),
                generation=1,
                attempt_count=1,
                owner_id="private-stale-worker",
                claimed_at=now - timedelta(seconds=90),
                heartbeat_at=now - timedelta(seconds=80),
                lease_expires_at=now - timedelta(seconds=60),
            )
            db.commit()

        response = client.get(
            "/api/v1/operations/verification-dispatch",
            headers={"X-API-Key": "observability-auditor"},
        )
        ready = client.get("/api/v1/readyz")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "incident"
    assert payload["dispatch"]["expired_running_leases"] == 1
    assert payload["integrity"]["status"] == "incident"
    assert payload["integrity"]["dispatch_issue_count"] == 1
    assert payload["integrity"]["issue_count"] == 1
    assert payload["alerts"][0]["code"] == "INTEGRITY_INCIDENT"
    assert ready.status_code == 503
    assert ready.json()["detail"]["status"] == "integrity_incident"
    assert "private-stale-worker" not in response.text
    assert incident_job.id not in response.text


def test_unsupported_job_status_is_bounded_and_fails_readiness_closed(
    tmp_path,
) -> None:
    settings = _settings(tmp_path)
    app = create_app(settings)
    private_status = "private-worker-identifier-must-not-be-a-label"
    with TestClient(app) as client:
        now = datetime.now(timezone.utc)
        with app.state.database.session_factory() as db:
            project, baseline = _base_records(db)
            incident_job, _, _ = _job_with_lease(
                db,
                project=project,
                baseline=baseline,
                job_status=private_status,
                created_at=now - timedelta(seconds=30),
            )
            db.commit()

        response = client.get(
            "/api/v1/operations/verification-dispatch",
            headers={"X-API-Key": "observability-auditor"},
        )
        metrics = client.get(
            "/api/v1/operations/verification-dispatch/metrics",
            headers={"X-API-Key": "observability-auditor"},
        )
        ready = client.get("/api/v1/readyz")

    assert response.status_code == 200
    assert metrics.status_code == 200
    payload = response.json()
    assert payload["status"] == "incident"
    assert payload["jobs"] == {"total": 1, "by_status": {"other": 1}}
    assert payload["integrity"]["dispatch_issue_count"] == 1
    assert payload["alerts"][0]["code"] == "INTEGRITY_INCIDENT"
    assert 'fengmou_verification_jobs{status="other"} 1' in metrics.text
    assert (
        'fengmou_verification_operations_status{status="incident"} 1'
        in metrics.text
    )
    assert ready.status_code == 503
    assert private_status not in response.text
    assert private_status not in metrics.text
    assert private_status not in ready.text
    assert incident_job.id not in response.text
    assert incident_job.id not in metrics.text
    assert incident_job.id not in ready.text


def test_unsupported_attempt_disposition_is_bounded_and_fails_readiness_closed(
    tmp_path,
) -> None:
    settings = _settings(tmp_path)
    app = create_app(settings)
    private_disposition = "private-attempt-identifier-must-not-be-a-label"
    with TestClient(app) as client:
        now = datetime.now(timezone.utc)
        with app.state.database.session_factory() as db:
            project, baseline = _base_records(db)
            job, _, evidence = _job_with_lease(
                db,
                project=project,
                baseline=baseline,
                job_status="failed",
                created_at=now - timedelta(seconds=30),
                generation=1,
                attempt_count=1,
            )
            attempt = VerificationAttempt(
                id=new_id(),
                job_id=job.id,
                generation=1,
                attempt_no=1,
                worker_id="private-unsupported-disposition-worker",
                execution_mode="external",
                analyzer_name=job.analyzer_name,
                analyzer_version=job.analyzer_version,
                evidence_sha256=evidence.sha256,
                baseline_sha256=baseline.sha256,
                max_attempts=settings.verification_max_attempts,
                claimed_at=now - timedelta(seconds=25),
            )
            db.add(attempt)
            db.flush()
            db.execute(text("PRAGMA ignore_check_constraints = ON"))
            db.add(
                VerificationAttemptOutcome(
                    id=new_id(),
                    attempt_id=attempt.id,
                    disposition=private_disposition,
                    stage="private-stage-must-not-be-a-label",
                    dead_lettered=False,
                    finished_at=now - timedelta(seconds=20),
                )
            )
            db.commit()

        response = client.get(
            "/api/v1/operations/verification-dispatch",
            headers={"X-API-Key": "observability-auditor"},
        )
        metrics = client.get(
            "/api/v1/operations/verification-dispatch/metrics",
            headers={"X-API-Key": "observability-auditor"},
        )
        ready = client.get("/api/v1/readyz")

    assert response.status_code == 200
    assert metrics.status_code == 200
    payload = response.json()
    assert payload["status"] == "incident"
    assert payload["integrity"]["attempt_issue_count"] == 1
    assert payload["attempts"]["outcomes_total_by_disposition"] == {
        disposition: 0 for disposition in OUTCOME_DISPOSITIONS
    }
    assert (
        'fengmou_verification_operations_status{status="incident"} 1'
        in metrics.text
    )
    assert ready.status_code == 503
    assert private_disposition not in response.text
    assert private_disposition not in metrics.text
    assert private_disposition not in ready.text
    assert "private-stage-must-not-be-a-label" not in metrics.text
    assert "private-unsupported-disposition-worker" not in metrics.text
    assert attempt.id not in response.text
    assert attempt.id not in metrics.text
    assert attempt.id not in ready.text
