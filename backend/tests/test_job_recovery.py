from __future__ import annotations

from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import Barrier, Event, Lock
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.config import Settings
from app.main import create_app
from app.models import AuditEvent, DesignBaseline, EvidenceAsset, Project, VerificationJob, new_id
from app.services import analysis
from app.services.storage import design_baseline_sha256, sha256_bytes


@dataclass
class _SeededJob:
    id: str
    status: str


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        environment="test",
        database_url=f"sqlite:///{tmp_path / 'recovery.db'}",
        storage_root=tmp_path / "storage",
        max_upload_bytes=2 * 1024 * 1024,
        allow_demo_analyzer=True,
        operator_api_key="test-operator-key",
        reviewer_api_key="test-reviewer-key",
        auditor_api_key="test-auditor-key",
        cors_origins=("http://testserver",),
    )


def _seed_jobs(app: FastAPI, *statuses: str) -> list[_SeededJob]:
    app.state.database.create_all()
    app.state.storage.ensure()
    project_id = new_id()
    baseline_id = new_id()
    baseline_fields = {
        "project_id": project_id,
        "site_id": "RECOVERY-SITE",
        "procedure_code": "RECOVERY-PROCEDURE",
        "version": "v1",
        "source_type": "manual",
        "expected": {},
    }
    jobs: list[_SeededJob] = []
    with app.state.database.session_factory() as db:
        db.add(Project(id=project_id, code=f"REC-{project_id[:8]}", name="恢复测试", location="匿名工点"))
        db.flush()
        db.add(
            DesignBaseline(
                id=baseline_id,
                **baseline_fields,
                sha256=design_baseline_sha256(**baseline_fields),
            )
        )
        db.flush()
        for index, status in enumerate(statuses):
            evidence_id = new_id()
            job_id = new_id()
            evidence_bytes = b"\x89PNG\r\n\x1a\n" + f"recovery-evidence-{index}".encode()
            stored_name = f"{evidence_id}.png"
            evidence_path = app.state.storage.evidence_dir / stored_name
            evidence_path.write_bytes(evidence_bytes)
            db.add(
                EvidenceAsset(
                    id=evidence_id,
                    project_id=project_id,
                    baseline_id=baseline_id,
                    original_name=f"recovery-{index}.png",
                    stored_name=stored_name,
                    storage_path=str(evidence_path),
                    content_type="image/png",
                    size_bytes=len(evidence_bytes),
                    sha256=sha256_bytes(evidence_bytes),
                    metadata_json={},
                )
            )
            db.add(
                VerificationJob(
                    id=job_id,
                    project_id=project_id,
                    baseline_id=baseline_id,
                    evidence_id=evidence_id,
                    analyzer_name="stub",
                    analyzer_version="test-v1",
                    status=status,
                    progress=42 if status == "running" else 0,
                    started_at=datetime.now(timezone.utc) if status == "running" else None,
                )
            )
            jobs.append(_SeededJob(id=job_id, status=status))
        db.commit()
    return jobs


def _result(evidence: EvidenceAsset, baseline: DesignBaseline) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "analysis_mode": "stub",
        "evidence_grade": False,
        "analyzer": {"name": "stub", "version": "test-v1"},
        "provenance": {
            "kind": "test_adapter",
            "synthetic": False,
            "warning": "Deterministic recovery test adapter; not a model result.",
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


def _actions_for(app: FastAPI, job_id: str) -> list[str]:
    with app.state.database.session_factory() as db:
        return list(
            db.scalars(
                select(AuditEvent.action)
                .where(AuditEvent.entity_type == "verification_job", AuditEvent.entity_id == job_id)
                .order_by(AuditEvent.created_at, AuditEvent.id)
            ).all()
        )


def test_startup_recovers_queued_and_running_jobs(tmp_path: Path, monkeypatch: Any) -> None:
    app = create_app(_settings(tmp_path))
    queued, running = _seed_jobs(app, "queued", "running")
    completed = Event()
    call_lock = Lock()
    analyzed: list[str] = []

    class RecoveryAnalyzer:
        def analyze(self, evidence: EvidenceAsset, _baseline: DesignBaseline) -> dict[str, Any]:
            with call_lock:
                analyzed.append(evidence.id)
                if len(analyzed) == 2:
                    completed.set()
            return _result(evidence, _baseline)

    monkeypatch.setattr(analysis, "build_analyzer", lambda *_args, **_kwargs: RecoveryAnalyzer())

    with TestClient(app):
        assert completed.wait(timeout=5), "startup recovery did not execute both unfinished jobs"

    with app.state.database.session_factory() as db:
        queued_job = db.get(VerificationJob, queued.id)
        running_job = db.get(VerificationJob, running.id)
        assert queued_job is not None and queued_job.status == "needs_review"
        assert running_job is not None and running_job.status == "needs_review"
        assert running_job.progress == 80

    assert _actions_for(app, queued.id) == [
        "recovery_scheduled",
        "analysis_started",
        "analysis_completed",
    ]
    assert _actions_for(app, running.id) == [
        "recovery_requeued",
        "recovery_scheduled",
        "analysis_started",
        "analysis_completed",
    ]
    assert len(analyzed) == 2


def test_concurrent_workers_execute_a_job_only_once(tmp_path: Path, monkeypatch: Any) -> None:
    app = create_app(_settings(tmp_path))
    (seeded,) = _seed_jobs(app, "queued")
    callers_ready = Barrier(3)
    analyzer_entered = Event()
    release_analyzer = Event()
    call_lock = Lock()
    analyze_calls = 0

    class BlockingAnalyzer:
        def analyze(self, _evidence: EvidenceAsset, _baseline: DesignBaseline) -> dict[str, Any]:
            nonlocal analyze_calls
            with call_lock:
                analyze_calls += 1
            analyzer_entered.set()
            assert release_analyzer.wait(timeout=5), "test did not release analyzer"
            return _result(_evidence, _baseline)

    monkeypatch.setattr(analysis, "build_analyzer", lambda *_args, **_kwargs: BlockingAnalyzer())

    def invoke() -> bool:
        callers_ready.wait(timeout=5)
        return analysis.run_verification_job(app, seeded.id)

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(invoke)
        second = executor.submit(invoke)
        callers_ready.wait(timeout=5)
        assert analyzer_entered.wait(timeout=5), "neither worker entered the analyzer"
        completed, _ = wait([first, second], timeout=5, return_when=FIRST_COMPLETED)
        assert completed, "the losing worker did not return after the atomic claim"
        losers = [future for future in completed if future.result(timeout=0) is False]
        assert len(losers) == 1, "the completed worker unexpectedly won the claim"
        release_analyzer.set()
        results = [first.result(timeout=5), second.result(timeout=5)]

    assert sorted(results) == [False, True]
    assert analyze_calls == 1
    with app.state.database.session_factory() as db:
        job = db.get(VerificationJob, seeded.id)
        assert job is not None and job.status == "needs_review"
    assert _actions_for(app, seeded.id) == ["analysis_started", "analysis_completed"]


def test_claimed_job_exception_is_failed_and_audited(tmp_path: Path, monkeypatch: Any) -> None:
    app = create_app(_settings(tmp_path))
    (seeded,) = _seed_jobs(app, "queued")

    class FailingAnalyzer:
        def analyze(self, _evidence: EvidenceAsset, _baseline: DesignBaseline) -> dict[str, Any]:
            raise RuntimeError("deterministic analyzer failure")

    monkeypatch.setattr(analysis, "build_analyzer", lambda *_args, **_kwargs: FailingAnalyzer())

    assert analysis.run_verification_job(app, seeded.id) is True
    with app.state.database.session_factory() as db:
        job = db.get(VerificationJob, seeded.id)
        assert job is not None
        assert job.status == "failed"
        assert job.progress == 100
        assert job.error_message == "deterministic analyzer failure"
        failure = db.scalar(
            select(AuditEvent).where(
                AuditEvent.entity_type == "verification_job",
                AuditEvent.entity_id == seeded.id,
                AuditEvent.action == "analysis_failed",
            )
        )
        assert failure is not None
        assert failure.payload_json["error_code"] == "ANALYSIS_FAILURE"
        assert failure.payload_json["retryable"] is False
    assert _actions_for(app, seeded.id) == ["analysis_started", "analysis_failed"]


def test_changed_evidence_is_rejected_before_analyzer_execution(tmp_path: Path, monkeypatch: Any) -> None:
    app = create_app(_settings(tmp_path))
    (seeded,) = _seed_jobs(app, "queued")
    with app.state.database.session_factory() as db:
        job = db.get(VerificationJob, seeded.id)
        assert job is not None
        evidence = db.get(EvidenceAsset, job.evidence_id)
        assert evidence is not None
        Path(evidence.storage_path).write_bytes(b"tampered-after-ingestion")

    monkeypatch.setattr(
        analysis,
        "build_analyzer",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("analyzer must not be built")),
    )
    assert analysis.run_verification_job(app, seeded.id) is True
    with app.state.database.session_factory() as db:
        job = db.get(VerificationJob, seeded.id)
        assert job is not None and job.status == "failed"
        assert "Evidence integrity check failed before analysis" in (job.error_message or "")


def test_changed_baseline_is_rejected_before_analyzer_execution(tmp_path: Path, monkeypatch: Any) -> None:
    app = create_app(_settings(tmp_path))
    (seeded,) = _seed_jobs(app, "queued")
    with app.state.database.session_factory() as db:
        job = db.get(VerificationJob, seeded.id)
        assert job is not None
        baseline = db.get(DesignBaseline, job.baseline_id)
        assert baseline is not None
        baseline.expected = {"scene_type": "tampered-after-seal"}
        db.commit()

    monkeypatch.setattr(
        analysis,
        "build_analyzer",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("analyzer must not be built")),
    )
    assert analysis.run_verification_job(app, seeded.id) is True
    with app.state.database.session_factory() as db:
        job = db.get(VerificationJob, seeded.id)
        assert job is not None and job.status == "failed"
        assert "Design baseline integrity check failed before analysis" in (job.error_message or "")
