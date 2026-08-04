from __future__ import annotations

import os
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import exists, func, or_, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import (
    AuditEvent,
    ComplianceEvaluation,
    DesignBaseline,
    EvidenceAsset,
    EvidenceCapture,
    VerificationAttempt,
    VerificationAttemptOutcome,
    VerificationJob,
    VerificationJobLease,
    WorkOrder,
    VERIFICATION_ATTEMPT_DISPOSITIONS,
    VERIFICATION_JOB_STATUSES,
    new_id,
    utcnow,
)
from .analyzers import analyzer_descriptor, build_analyzer, validate_analyzer_result
from .analyzers.base import bind_validated_evidence_source
from .analyzers.remote_http import RemoteAnalyzerError
from .compliance import apply_compliance_to_analyzer_result, evaluate_compliance
from .remediation import materialize_finding_cases
from .storage import (
    StoredFileIntegrityError,
    StoredFileMissingError,
    canonical_json_bytes,
    design_baseline_sha256,
    sha256_bytes,
)
from .work_orders import (
    WorkOrderIntegrityError,
    WorkOrderTransitionError,
    apply_analysis_completion_transitions,
    frozen_rules_snapshot,
    map_compliance_to_work_order_status,
)


@dataclass(frozen=True, slots=True)
class VerificationLeaseClaim:
    job_id: str
    worker_id: str
    generation: int
    attempt_count: int
    attempt_id: str


def add_audit(
    db: Session,
    *,
    entity_type: str,
    entity_id: str,
    action: str,
    actor: str = "system",
    payload: dict[str, Any] | None = None,
) -> None:
    db.add(
        AuditEvent(
            entity_type=entity_type,
            entity_id=entity_id,
            action=action,
            actor=actor,
            payload_json=payload or {},
        )
    )


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _db_now(db: Session) -> datetime:
    """Use the database clock as the lease authority across worker hosts."""

    value = db.scalar(select(func.current_timestamp()))
    if not isinstance(value, datetime):
        raise RuntimeError("Database did not return a timestamp for lease coordination")
    return _aware(value)


def _lease_deadline(app: Any, now: datetime) -> datetime:
    return now + timedelta(seconds=app.state.settings.verification_lease_seconds)


def _default_worker_id(prefix: str = "inline") -> str:
    return f"{prefix}:{os.getpid()}:{threading.get_ident()}:{uuid.uuid4().hex[:12]}"


def ensure_verification_job_lease(db: Session, job_id: str) -> None:
    """Create the companion lease row without racing another worker."""

    bind = db.get_bind()
    if bind.dialect.name == "sqlite":
        db.execute(
            sqlite_insert(VerificationJobLease)
            .values(job_id=job_id, generation=0, attempt_count=0, updated_at=utcnow())
            .on_conflict_do_nothing(index_elements=[VerificationJobLease.job_id])
        )
        return
    if db.get(VerificationJobLease, job_id) is not None:
        return
    try:
        with db.begin_nested():
            db.add(VerificationJobLease(job_id=job_id))
            db.flush()
    except IntegrityError:
        # A concurrent transaction created the same one-to-one row.
        pass


def _claim_attempt(db: Session, claim: VerificationLeaseClaim) -> VerificationAttempt:
    attempt = db.get(VerificationAttempt, claim.attempt_id)
    if attempt is None:
        raise RuntimeError("Verification claim has no immutable attempt record")
    if (
        attempt.job_id != claim.job_id
        or attempt.worker_id != claim.worker_id
        or attempt.generation != claim.generation
        or attempt.attempt_no != claim.attempt_count
    ):
        raise RuntimeError("Verification claim does not match its immutable attempt record")
    return attempt


def _append_attempt_outcome(
    db: Session,
    attempt: VerificationAttempt,
    *,
    disposition: str,
    finished_at: datetime,
    stage: str | None = None,
    result_json: dict[str, Any] | None = None,
    result_sha256: str | None = None,
    error_code: str | None = None,
    error_retryable: bool | None = None,
    error_message: str | None = None,
    upstream_status: int | None = None,
    dead_lettered: bool = False,
    allow_existing: bool = False,
) -> bool:
    """Append the sole terminal row, optionally accepting a concurrent winner."""

    existing = db.scalar(
        select(VerificationAttemptOutcome.id).where(
            VerificationAttemptOutcome.attempt_id == attempt.id
        )
    )
    if existing is not None:
        if allow_existing:
            return False
        raise RuntimeError("Verification attempt already has a terminal outcome")
    outcome = VerificationAttemptOutcome(
        attempt_id=attempt.id,
        disposition=disposition,
        stage=stage,
        result_json=result_json,
        result_sha256=result_sha256,
        error_code=error_code,
        error_retryable=error_retryable,
        error_message=error_message[:2000] if error_message else None,
        upstream_status=upstream_status,
        dead_lettered=dead_lettered,
        finished_at=finished_at,
    )
    if not allow_existing:
        db.add(outcome)
        db.flush()
        return True
    try:
        with db.begin_nested():
            db.add(outcome)
            db.flush()
        return True
    except IntegrityError:
        # A lease observer won the one-outcome race. Its immutable row remains
        # authoritative; any other constraint failure must fail closed.
        winner = db.scalar(
            select(VerificationAttemptOutcome.id).where(
                VerificationAttemptOutcome.attempt_id == attempt.id
            )
        )
        if winner is not None:
            return False
        raise


def _record_expired_attempt_outcome(
    db: Session,
    *,
    job_id: str,
    generation: int,
    now: datetime,
    stage: str,
    dead_lettered: bool,
) -> bool:
    attempt = db.scalar(
        select(VerificationAttempt).where(
            VerificationAttempt.job_id == job_id,
            VerificationAttempt.generation == generation,
        )
    )
    if attempt is None:
        # Alpha12 rows can legitimately predate immutable attempt history.
        return False
    return _append_attempt_outcome(
        db,
        attempt,
        disposition="lease_expired",
        stage=stage,
        error_code="WORKER_LEASE_EXPIRED",
        error_retryable=True,
        error_message="Verification worker lease expired before a terminal write",
        dead_lettered=dead_lettered,
        finished_at=now,
        allow_existing=True,
    )


def _release_lease_values(now: datetime) -> dict[str, Any]:
    return {
        "owner_id": None,
        "lease_expires_at": None,
        "updated_at": now,
    }


def recover_pending_verification_jobs(app: Any) -> list[str]:
    """Reconcile durable leases and return jobs for inline startup scheduling.

    A live ``running`` lease is never reset merely because another API process
    started. Legacy running rows without a lease, and genuinely expired leases,
    are fenced and requeued (or dead-lettered after the retry budget).
    """

    db = app.state.database.session_factory()
    try:
        candidates = list(
            db.scalars(
                select(VerificationJob)
                .where(VerificationJob.status.in_(("queued", "running")))
                .order_by(VerificationJob.created_at, VerificationJob.id)
            ).all()
        )
        existing_lease_ids = set(
            db.scalars(
                select(VerificationJobLease.job_id).where(
                    VerificationJobLease.job_id.in_([job.id for job in candidates] or [""])
                )
            ).all()
        )
        legacy_running_ids = {
            job.id for job in candidates if job.status == "running" and job.id not in existing_lease_ids
        }
        for job in candidates:
            ensure_verification_job_lease(db, job.id)
        db.commit()

        now = _db_now(db)
        max_attempts = app.state.settings.verification_max_attempts
        for job in candidates:
            current_status = db.scalar(
                select(VerificationJob.status).where(VerificationJob.id == job.id)
            )
            lease = db.get(VerificationJobLease, job.id)
            if current_status is None or lease is None:
                continue
            if current_status == "queued":
                if lease.owner_id is not None or lease.lease_expires_at is not None:
                    db.execute(
                        update(VerificationJobLease)
                        .where(
                            VerificationJobLease.job_id == job.id,
                            exists(
                                select(1).where(
                                    VerificationJob.id == job.id,
                                    VerificationJob.status == "queued",
                                )
                            ),
                        )
                        .values(**_release_lease_values(now))
                        .execution_options(synchronize_session=False)
                    )
                    db.commit()
                continue
            if current_status != "running":
                continue
            live = (
                lease.owner_id is not None
                and lease.lease_expires_at is not None
                and _aware(lease.lease_expires_at) > now
            )
            if live:
                continue

            previous_owner = lease.owner_id
            previous_generation = lease.generation
            dead_lettered = lease.attempt_count >= max_attempts and job.id not in legacy_running_ids
            lease_values = _release_lease_values(now)
            if dead_lettered:
                lease_values.update(
                    dead_lettered_at=now,
                    last_error_code="WORKER_LEASE_EXPIRED",
                    last_error_retryable=True,
                )
                action = "analysis_dead_lettered"
            else:
                action = "recovery_requeued"
            lease_conditions = [
                VerificationJobLease.job_id == job.id,
                VerificationJobLease.generation == previous_generation,
            ]
            if job.id in legacy_running_ids:
                lease_conditions.extend(
                    [
                        VerificationJobLease.owner_id.is_(None),
                        VerificationJobLease.lease_expires_at.is_(None),
                    ]
                )
            else:
                lease_conditions.extend(
                    [
                        VerificationJobLease.owner_id == previous_owner,
                        VerificationJobLease.lease_expires_at <= now,
                    ]
                )
            lease_transition = db.execute(
                update(VerificationJobLease)
                .where(*lease_conditions)
                .values(**lease_values)
                .execution_options(synchronize_session=False)
            )
            if lease_transition.rowcount != 1:
                db.rollback()
                continue
            job_values: dict[str, Any]
            if dead_lettered:
                job_values = {
                    "status": "failed",
                    "progress": 100,
                    "error_message": "Verification worker retry budget exhausted after lease expiry",
                    "completed_at": now,
                }
            else:
                job_values = {
                    "status": "queued",
                    "progress": 0,
                    "result_json": None,
                    "error_message": None,
                    "started_at": None,
                    "completed_at": None,
                }
            job_transition = db.execute(
                update(VerificationJob)
                .where(VerificationJob.id == job.id, VerificationJob.status == "running")
                .values(**job_values)
                .execution_options(synchronize_session=False)
            )
            if job_transition.rowcount != 1:
                db.rollback()
                continue
            outcome_recorded = _record_expired_attempt_outcome(
                db,
                job_id=job.id,
                generation=previous_generation,
                now=now,
                stage="startup_recovery",
                dead_lettered=dead_lettered,
            )
            add_audit(
                db,
                entity_type="verification_job",
                entity_id=job.id,
                action=action,
                payload={
                    "previous_status": "running",
                    "reason": (
                        "legacy_running_without_lease"
                        if job.id in legacy_running_ids
                        else "worker_lease_expired"
                    ),
                    "previous_owner": previous_owner,
                    "previous_generation": previous_generation,
                    "attempt_count": lease.attempt_count,
                    "max_attempts": max_attempts,
                    "attempt_outcome_recorded": outcome_recorded,
                },
            )
            db.commit()
        db.commit()

        pending_ids = list(
            db.scalars(
                select(VerificationJob.id)
                .where(VerificationJob.status == "queued")
                .order_by(VerificationJob.created_at, VerificationJob.id)
            ).all()
        )
        if app.state.settings.verification_execution_mode != "inline":
            return []
        for job_id in pending_ids:
            add_audit(
                db,
                entity_type="verification_job",
                entity_id=job_id,
                action="recovery_scheduled",
                payload={"reason": "application_startup", "execution_mode": "inline"},
            )
        db.commit()
        return pending_ids
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def reap_expired_verification_jobs(app: Any) -> int:
    """Fence expired workers and return their jobs to the durable queue."""

    db = app.state.database.session_factory()
    try:
        now = _db_now(db)
        expired_ids = list(
            db.scalars(
                select(VerificationJobLease.job_id)
                .join(VerificationJob, VerificationJob.id == VerificationJobLease.job_id)
                .where(
                    VerificationJob.status == "running",
                    VerificationJobLease.owner_id.is_not(None),
                    VerificationJobLease.lease_expires_at.is_not(None),
                    VerificationJobLease.lease_expires_at <= now,
                )
                .order_by(VerificationJobLease.lease_expires_at, VerificationJobLease.job_id)
            ).all()
        )
        recovered = 0
        for job_id in expired_ids:
            lease = db.get(VerificationJobLease, job_id)
            if lease is None:
                continue
            if lease.lease_expires_at is None or _aware(lease.lease_expires_at) > now:
                continue
            owner = lease.owner_id
            generation = lease.generation
            dead_lettered = lease.attempt_count >= app.state.settings.verification_max_attempts
            lease_values = _release_lease_values(now)
            lease_values.update(
                last_error_code="WORKER_LEASE_EXPIRED",
                last_error_retryable=True,
            )
            if dead_lettered:
                lease_values["dead_lettered_at"] = now
                action = "analysis_dead_lettered"
            else:
                action = "lease_expired_requeued"
            lease_transition = db.execute(
                update(VerificationJobLease)
                .where(
                    VerificationJobLease.job_id == job_id,
                    VerificationJobLease.owner_id == owner,
                    VerificationJobLease.generation == generation,
                    VerificationJobLease.lease_expires_at <= now,
                )
                .values(**lease_values)
                .execution_options(synchronize_session=False)
            )
            if lease_transition.rowcount != 1:
                db.rollback()
                continue
            if dead_lettered:
                job_values = {
                    "status": "failed",
                    "progress": 100,
                    "error_message": "Verification worker retry budget exhausted after lease expiry",
                    "completed_at": now,
                }
            else:
                job_values = {
                    "status": "queued",
                    "progress": 0,
                    "result_json": None,
                    "error_message": None,
                    "started_at": None,
                    "completed_at": None,
                }
            job_transition = db.execute(
                update(VerificationJob)
                .where(VerificationJob.id == job_id, VerificationJob.status == "running")
                .values(**job_values)
                .execution_options(synchronize_session=False)
            )
            if job_transition.rowcount != 1:
                db.rollback()
                continue
            outcome_recorded = _record_expired_attempt_outcome(
                db,
                job_id=job_id,
                generation=generation,
                now=now,
                stage="lease_reaper",
                dead_lettered=dead_lettered,
            )
            add_audit(
                db,
                entity_type="verification_job",
                entity_id=job_id,
                action=action,
                payload={
                    "previous_owner": owner,
                    "previous_generation": generation,
                    "attempt_count": lease.attempt_count,
                    "max_attempts": app.state.settings.verification_max_attempts,
                    "error_code": "WORKER_LEASE_EXPIRED",
                    "retryable": True,
                    "attempt_outcome_recorded": outcome_recorded,
                },
            )
            db.commit()
            recovered += 1
        db.commit()
        return recovered
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def claim_verification_job(app: Any, job_id: str, worker_id: str) -> VerificationLeaseClaim | None:
    """Atomically claim one queued job and advance its fencing generation."""

    worker_id = worker_id.strip()
    if not worker_id or len(worker_id) > 200:
        raise ValueError("worker_id must contain 1-200 characters")
    db = app.state.database.session_factory()
    try:
        ensure_verification_job_lease(db, job_id)
        db.commit()
        lease = db.get(VerificationJobLease, job_id)
        if lease is None:
            return None
        if lease.dead_lettered_at is not None or lease.attempt_count >= app.state.settings.verification_max_attempts:
            return None
        now = _db_now(db)
        previous_generation = lease.generation
        next_generation = previous_generation + 1
        next_attempt = lease.attempt_count + 1
        lease_claim = db.execute(
            update(VerificationJobLease)
            .where(
                VerificationJobLease.job_id == job_id,
                VerificationJobLease.generation == previous_generation,
                VerificationJobLease.dead_lettered_at.is_(None),
                VerificationJobLease.attempt_count < app.state.settings.verification_max_attempts,
                or_(
                    VerificationJobLease.owner_id.is_(None),
                    VerificationJobLease.lease_expires_at <= now,
                ),
            )
            .values(
                owner_id=worker_id,
                generation=next_generation,
                attempt_count=next_attempt,
                claimed_at=now,
                heartbeat_at=now,
                lease_expires_at=_lease_deadline(app, now),
                last_error_code=None,
                last_error_retryable=None,
                updated_at=now,
            )
            .execution_options(synchronize_session=False)
        )
        if lease_claim.rowcount != 1:
            db.rollback()
            return None
        job_claim = db.execute(
            update(VerificationJob)
            .where(VerificationJob.id == job_id, VerificationJob.status == "queued")
            .values(
                status="running",
                progress=10,
                error_message=None,
                started_at=now,
                completed_at=None,
            )
            .execution_options(synchronize_session=False)
        )
        if job_claim.rowcount != 1:
            db.rollback()
            return None
        job = db.get(VerificationJob, job_id)
        if job is None:
            raise RuntimeError("Claimed verification job no longer exists")
        evidence = db.get(EvidenceAsset, job.evidence_id)
        baseline = db.get(DesignBaseline, job.baseline_id)
        if evidence is None or baseline is None:
            raise RuntimeError("Claimed verification job has no evidence or design baseline")
        attempt = VerificationAttempt(
            job_id=job_id,
            generation=next_generation,
            attempt_no=next_attempt,
            worker_id=worker_id,
            execution_mode=app.state.settings.verification_execution_mode,
            analyzer_name=job.analyzer_name,
            analyzer_version=job.analyzer_version,
            evidence_sha256=evidence.sha256,
            baseline_sha256=baseline.sha256,
            max_attempts=app.state.settings.verification_max_attempts,
            claimed_at=now,
        )
        db.add(attempt)
        db.flush()
        add_audit(
            db,
            entity_type="verification_job",
            entity_id=job_id,
            action="analysis_started",
            actor=worker_id,
            payload={
                "previous_status": "queued",
                "worker_id": worker_id,
                "generation": next_generation,
                "attempt_count": next_attempt,
                "max_attempts": app.state.settings.verification_max_attempts,
                "attempt_id": attempt.id,
            },
        )
        db.commit()
        return VerificationLeaseClaim(
            job_id=job_id,
            worker_id=worker_id,
            generation=next_generation,
            attempt_count=next_attempt,
            attempt_id=attempt.id,
        )
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def renew_verification_job_lease(app: Any, claim: VerificationLeaseClaim) -> bool:
    db = app.state.database.session_factory()
    try:
        now = _db_now(db)
        renewed = db.execute(
            update(VerificationJobLease)
            .where(
                VerificationJobLease.job_id == claim.job_id,
                VerificationJobLease.owner_id == claim.worker_id,
                VerificationJobLease.generation == claim.generation,
                VerificationJobLease.lease_expires_at > now,
                VerificationJobLease.dead_lettered_at.is_(None),
                exists(
                    select(1).where(
                        VerificationJob.id == claim.job_id,
                        VerificationJob.status == "running",
                    )
                ),
            )
            .values(
                heartbeat_at=now,
                lease_expires_at=_lease_deadline(app, now),
                updated_at=now,
            )
            .execution_options(synchronize_session=False)
        )
        if renewed.rowcount != 1:
            db.rollback()
            return False
        db.commit()
        return True
    except Exception:
        db.rollback()
        return False
    finally:
        db.close()


def _record_fenced_write(app: Any, claim: VerificationLeaseClaim, stage: str) -> None:
    db = app.state.database.session_factory()
    try:
        now = _db_now(db)
        attempt = _claim_attempt(db, claim)
        disposition = "lease_lost" if stage.startswith("lease_lost") else "write_fenced"
        error_code = "WORKER_LEASE_LOST" if disposition == "lease_lost" else "WORKER_WRITE_FENCED"
        outcome_recorded = _append_attempt_outcome(
            db,
            attempt,
            disposition=disposition,
            stage=stage,
            error_code=error_code,
            error_retryable=True,
            error_message=(
                "Worker stopped after losing its lease"
                if disposition == "lease_lost"
                else "A stale worker terminal write was rejected by its fencing token"
            ),
            finished_at=now,
            allow_existing=True,
        )
        add_audit(
            db,
            entity_type="verification_job",
            entity_id=claim.job_id,
            action="analysis_write_fenced",
            actor=claim.worker_id,
            payload={
                "stage": stage,
                "generation": claim.generation,
                "attempt_count": claim.attempt_count,
                "attempt_id": claim.attempt_id,
                "attempt_outcome_recorded": outcome_recorded,
            },
        )
        db.commit()
    except Exception:
        db.rollback()
    finally:
        db.close()


def _fence_for_write(db: Session, app: Any, claim: VerificationLeaseClaim, now: datetime) -> bool:
    fenced = db.execute(
        update(VerificationJobLease)
        .where(
            VerificationJobLease.job_id == claim.job_id,
            VerificationJobLease.owner_id == claim.worker_id,
            VerificationJobLease.generation == claim.generation,
            VerificationJobLease.lease_expires_at > now,
            VerificationJobLease.dead_lettered_at.is_(None),
            exists(
                select(1).where(
                    VerificationJob.id == claim.job_id,
                    VerificationJob.status == "running",
                )
            ),
        )
        .values(heartbeat_at=now, lease_expires_at=_lease_deadline(app, now), updated_at=now)
        .execution_options(synchronize_session=False)
    )
    return fenced.rowcount == 1


def _complete_verification_job(
    app: Any,
    claim: VerificationLeaseClaim,
    result: dict[str, Any],
    compliance_payload: dict[str, Any] | None = None,
) -> bool:
    db = app.state.database.session_factory()
    try:
        now = _db_now(db)
        if not _fence_for_write(db, app, claim, now):
            db.rollback()
            _record_fenced_write(app, claim, "completion")
            return False
        job = db.get(VerificationJob, claim.job_id)
        lease = db.get(VerificationJobLease, claim.job_id)
        if job is None or lease is None or job.status != "running":
            db.rollback()
            _record_fenced_write(app, claim, "completion_status")
            return False
        attempt = _claim_attempt(db, claim)
        result_sha256 = sha256_bytes(canonical_json_bytes(result))
        _append_attempt_outcome(
            db,
            attempt,
            disposition="committed_success",
            stage="analysis_completion",
            result_json=result,
            result_sha256=result_sha256,
            finished_at=now,
        )
        job.result_json = result
        materialized_cases = materialize_finding_cases(db, job)
        job.status = "needs_review"
        job.progress = 80
        job.completed_at = now
        for key, value in _release_lease_values(now).items():
            setattr(lease, key, value)
        if compliance_payload is not None:
            work_order_id = str(compliance_payload["work_order_id"])
            work_order = db.get(WorkOrder, work_order_id)
            if work_order is None:
                # Domain integrity: never commit success without applying WO transitions.
                raise WorkOrderIntegrityError(
                    "Compliance payload references a missing work order",
                    work_order_id=work_order_id,
                    stage="analysis_completion_work_order_lookup",
                )
            target_status = map_compliance_to_work_order_status(
                str(compliance_payload["verdict"])
            )
            # Strict path: evidence_uploaded|analyzing → target. No silent fallback.
            apply_analysis_completion_transitions(work_order, target_status)

            capture = db.scalar(
                select(EvidenceCapture).where(EvidenceCapture.evidence_id == job.evidence_id)
            )
            if capture is not None and capture.verification_job_id is None:
                capture.verification_job_id = job.id

            evaluation = ComplianceEvaluation(
                id=new_id(),
                project_id=str(compliance_payload["project_id"]),
                work_order_id=work_order.id,
                job_id=job.id,
                rule_version=str(compliance_payload["rule_version"]),
                engine_version=str(compliance_payload["engine_version"]),
                expected_json=compliance_payload.get("expected") or {},
                observed_json=compliance_payload.get("observed") or {},
                difference_json=compliance_payload.get("differences") or [],
                verdict=str(compliance_payload["verdict"]),
                spatial_check_status=compliance_payload.get("spatial_check_status"),
                notes=str(compliance_payload.get("note") or ""),
            )
            db.add(evaluation)
            add_audit(
                db,
                entity_type="work_order",
                entity_id=work_order.id,
                action="rule_evaluation_completed",
                actor=claim.worker_id,
                payload={
                    "verdict": compliance_payload["verdict"],
                    "job_id": job.id,
                    "engine_version": compliance_payload["engine_version"],
                    "rule_version": compliance_payload["rule_version"],
                    "rules_source": "work_order.rules_snapshot_json",
                    "authority": "server_rule_engine",
                    "work_order_status": work_order.status,
                },
            )
            add_audit(
                db,
                entity_type="work_order",
                entity_id=work_order.id,
                action="analysis_observations_received",
                actor=claim.worker_id,
                payload={
                    "job_id": job.id,
                    "result_sha256": result_sha256,
                    "authority": "model_observations_only",
                    "note": "Analyzer output is observations only; verdict is server rule engine",
                },
            )
        add_audit(
            db,
            entity_type="verification_job",
            entity_id=job.id,
            action="analysis_completed",
            actor=claim.worker_id,
            payload={
                "analysis_mode": result.get("analysis_mode"),
                "recommended_action": "manual_review",
                "finding_case_count": len(materialized_cases),
                "finding_truth_boundary": "candidate findings; reviewer triage required",
                "worker_id": claim.worker_id,
                "generation": claim.generation,
                "attempt_count": claim.attempt_count,
                "attempt_id": claim.attempt_id,
                "result_sha256": result_sha256,
                "compliance_verdict": (
                    None if compliance_payload is None else compliance_payload.get("verdict")
                ),
            },
        )
        db.commit()
        return True
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _fail_verification_job(app: Any, claim: VerificationLeaseClaim, exc: Exception) -> bool:
    db = app.state.database.session_factory()
    try:
        now = _db_now(db)
        if not _fence_for_write(db, app, claim, now):
            db.rollback()
            _record_fenced_write(app, claim, "failure")
            return False
        job = db.get(VerificationJob, claim.job_id)
        lease = db.get(VerificationJobLease, claim.job_id)
        if job is None or lease is None or job.status != "running":
            db.rollback()
            _record_fenced_write(app, claim, "failure_status")
            return False
        error_code = "ANALYSIS_FAILURE"
        retryable = False
        upstream_status = None
        if isinstance(exc, RemoteAnalyzerError):
            error_code = exc.code
            retryable = exc.retryable
            upstream_status = exc.upstream_status
        elif isinstance(exc, (WorkOrderTransitionError, WorkOrderIntegrityError)):
            error_code = exc.error_code
            retryable = False
        dead_lettered = retryable and claim.attempt_count >= app.state.settings.verification_max_attempts
        # Keep failure messages short and free of secrets/paths/media content.
        error_message = str(exc)[:500]
        attempt = _claim_attempt(db, claim)
        failure_stage = "analysis_failure"
        if isinstance(exc, (WorkOrderTransitionError, WorkOrderIntegrityError)):
            failure_stage = getattr(exc, "stage", None) or "work_order_transition"
        _append_attempt_outcome(
            db,
            attempt,
            disposition="committed_failure",
            stage=failure_stage,
            error_code=error_code,
            error_retryable=retryable,
            error_message=error_message,
            upstream_status=upstream_status,
            dead_lettered=dead_lettered,
            finished_at=now,
        )
        job.status = "failed"
        job.progress = 100
        job.error_message = error_message
        job.completed_at = now
        lease.last_error_code = error_code
        lease.last_error_retryable = retryable
        if dead_lettered:
            lease.dead_lettered_at = now
        for key, value in _release_lease_values(now).items():
            setattr(lease, key, value)
        failure_payload: dict[str, Any] = {
            "error": job.error_message,
            "previous_status": "running",
            "error_code": error_code,
            "retryable": retryable,
            "dead_lettered": dead_lettered,
            "worker_id": claim.worker_id,
            "generation": claim.generation,
            "attempt_count": claim.attempt_count,
            "attempt_id": claim.attempt_id,
            "max_attempts": app.state.settings.verification_max_attempts,
        }
        if upstream_status is not None:
            failure_payload["upstream_status"] = upstream_status
        add_audit(
            db,
            entity_type="verification_job",
            entity_id=job.id,
            action="analysis_failed",
            actor=claim.worker_id,
            payload=failure_payload,
        )
        if isinstance(exc, (WorkOrderTransitionError, WorkOrderIntegrityError)):
            wo_entity = getattr(exc, "work_order_id", None) or job.id
            add_audit(
                db,
                entity_type="work_order",
                entity_id=str(wo_entity),
                action="work_order_transition_failed",
                actor=claim.worker_id,
                payload=exc.to_audit_payload(
                    job_id=job.id,
                    worker_id=claim.worker_id,
                    generation=claim.generation,
                    attempt_id=claim.attempt_id,
                ),
            )
        if dead_lettered:
            add_audit(
                db,
                entity_type="verification_job",
                entity_id=job.id,
                action="analysis_dead_lettered",
                actor=claim.worker_id,
                payload=failure_payload,
            )
        db.commit()
        return True
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _heartbeat_loop(
    app: Any,
    claim: VerificationLeaseClaim,
    stop: threading.Event,
    lease_lost: threading.Event,
) -> None:
    interval = app.state.settings.verification_heartbeat_seconds
    while not stop.wait(interval):
        if not renew_verification_job_lease(app, claim):
            lease_lost.set()
            return


def run_verification_job(app: Any, job_id: str, worker_id: str | None = None) -> bool:
    """Claim and execute one job with heartbeat and fenced terminal writes."""

    claim = claim_verification_job(app, job_id, worker_id or _default_worker_id())
    if claim is None:
        return False
    stop_heartbeat = threading.Event()
    lease_lost = threading.Event()
    heartbeat = threading.Thread(
        target=_heartbeat_loop,
        args=(app, claim, stop_heartbeat, lease_lost),
        name=f"verification-heartbeat-{job_id}",
        daemon=True,
    )
    heartbeat.start()
    try:
        db = app.state.database.session_factory()
        try:
            job = db.get(VerificationJob, job_id)
            if job is None:
                raise RuntimeError("Claimed verification job no longer exists")
            evidence = db.get(EvidenceAsset, job.evidence_id)
            baseline = db.get(DesignBaseline, job.baseline_id)
            if evidence is None or baseline is None:
                raise RuntimeError("Evidence or design baseline no longer exists")
            try:
                validated_evidence = app.state.storage.validate_evidence_file(
                    storage_path=evidence.storage_path,
                    stored_name=evidence.stored_name,
                    expected_content_type=evidence.content_type,
                    expected_size=evidence.size_bytes,
                    expected_sha256=evidence.sha256,
                )
            except (StoredFileMissingError, StoredFileIntegrityError) as exc:
                raise RuntimeError(f"Evidence integrity check failed before analysis: {exc}") from exc
            with validated_evidence:
                current_baseline_sha256 = design_baseline_sha256(
                    project_id=baseline.project_id,
                    site_id=baseline.site_id,
                    procedure_code=baseline.procedure_code,
                    version=baseline.version,
                    source_type=baseline.source_type,
                    expected=baseline.expected,
                )
                if current_baseline_sha256 != baseline.sha256:
                    raise RuntimeError(
                        "Design baseline integrity check failed before analysis: canonical SHA-256 changed"
                    )
                settings = app.state.settings
                descriptor = analyzer_descriptor(job.analyzer_name, settings=settings)
                analyzer = build_analyzer(
                    job.analyzer_name,
                    settings=settings,
                    job_id=job.id,
                    pinned_version=job.analyzer_version,
                )
                with bind_validated_evidence_source(validated_evidence):
                    raw_result = analyzer.analyze(evidence, baseline)
            result = validate_analyzer_result(
                raw_result,
                evidence=evidence,
                baseline=baseline,
                expected_name=job.analyzer_name,
                expected_version=job.analyzer_version,
                expected_synthetic=bool(descriptor["synthetic"]),
                allow_evidence_grade=False,
            )
            # Work-order path: server rule engine owns compliance, not the adapter.
            capture = db.scalar(
                select(EvidenceCapture).where(EvidenceCapture.evidence_id == evidence.id)
            )
            compliance_payload = None
            if capture is not None:
                work_order = db.get(WorkOrder, capture.work_order_id)
                if work_order is None:
                    raise WorkOrderIntegrityError(
                        "Evidence capture references a missing work order",
                        work_order_id=str(capture.work_order_id),
                        stage="analysis_capture_work_order_lookup",
                    )
                # Historical RuleEvaluation MUST use frozen WO rules, never live EO.
                compliance_payload = evaluate_compliance(
                    rules_snapshot=frozen_rules_snapshot(work_order),
                    analyzer_result=result,
                    spatial_check_status=capture.spatial_check_status,
                )
                result = apply_compliance_to_analyzer_result(
                    result,
                    compliance_payload,
                    baseline_version=baseline.version,
                )
                compliance_payload = {
                    **compliance_payload,
                    "work_order_id": work_order.id,
                    "project_id": work_order.project_id,
                    "job_id": job.id,
                    "spatial_check_status": capture.spatial_check_status,
                }
        finally:
            db.close()
        stop_heartbeat.set()
        heartbeat.join(timeout=app.state.settings.verification_heartbeat_seconds + 1)
        if lease_lost.is_set():
            _record_fenced_write(app, claim, "lease_lost_before_completion")
            return True
        _complete_verification_job(app, claim, result, compliance_payload=compliance_payload)
        return True
    except Exception as exc:
        stop_heartbeat.set()
        heartbeat.join(timeout=app.state.settings.verification_heartbeat_seconds + 1)
        if lease_lost.is_set():
            _record_fenced_write(app, claim, "lease_lost_before_failure")
            return True
        _fail_verification_job(app, claim, exc)
        return True
    finally:
        stop_heartbeat.set()


def run_next_verification_job(app: Any, worker_id: str) -> bool:
    """Try the oldest claimable jobs until one claim succeeds."""

    db = app.state.database.session_factory()
    try:
        candidate_ids = list(
            db.scalars(
                select(VerificationJob.id)
                .where(VerificationJob.status == "queued")
                .order_by(VerificationJob.created_at, VerificationJob.id)
                .limit(20)
            ).all()
        )
    finally:
        db.close()
    for job_id in candidate_ids:
        if run_verification_job(app, job_id, worker_id):
            return True
    return False


def scan_verification_dispatch_integrity(db: Session) -> list[str]:
    """Return durable state contradictions; transient backlog is not an incident."""

    issues: list[str] = []
    now = _db_now(db)
    unsupported_job_ids = db.scalars(
        select(VerificationJob.id).where(
            or_(
                VerificationJob.status.is_(None),
                VerificationJob.status.not_in(VERIFICATION_JOB_STATUSES),
            )
        )
    ).all()
    issues.extend(
        f"verification job {job_id} has an unsupported persisted status"
        for job_id in unsupported_job_ids
    )
    rows = db.execute(
        select(VerificationJob, VerificationJobLease)
        .outerjoin(VerificationJobLease, VerificationJobLease.job_id == VerificationJob.id)
        .where(VerificationJob.status.in_(("queued", "running")))
    ).all()
    for job, lease in rows:
        if lease is None:
            issues.append(f"verification job {job.id} has no dispatch lease row")
        elif job.status == "running" and (
            lease.owner_id is None or lease.lease_expires_at is None or lease.attempt_count < 1
        ):
            issues.append(f"running verification job {job.id} has no valid lease owner")
        elif (
            job.status == "running"
            and lease.lease_expires_at is not None
            and _aware(lease.lease_expires_at) <= now
        ):
            issues.append(f"running verification job {job.id} has an expired lease")
        elif job.status == "queued" and lease.owner_id is not None:
            issues.append(f"queued verification job {job.id} still has a lease owner")
    return issues


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def scan_verification_attempt_integrity(db: Session) -> list[str]:
    """Validate immutable attempt chronology and outcome payloads."""

    issues: list[str] = []
    now = _db_now(db)
    rows = db.execute(
        select(VerificationAttempt, VerificationAttemptOutcome)
        .outerjoin(
            VerificationAttemptOutcome,
            VerificationAttemptOutcome.attempt_id == VerificationAttempt.id,
        )
        .order_by(
            VerificationAttempt.job_id,
            VerificationAttempt.attempt_no,
        )
    ).all()
    jobs = {
        job.id: job
        for job in db.scalars(select(VerificationJob)).all()
    }
    leases = {
        lease.job_id: lease
        for lease in db.scalars(select(VerificationJobLease)).all()
    }
    evidence_records = {
        evidence.id: evidence
        for evidence in db.scalars(select(EvidenceAsset)).all()
    }
    baseline_records = {
        baseline.id: baseline
        for baseline in db.scalars(select(DesignBaseline)).all()
    }
    attempts_by_job: dict[
        str,
        list[tuple[VerificationAttempt, VerificationAttemptOutcome | None]],
    ] = {}
    for attempt, outcome in rows:
        attempts_by_job.setdefault(attempt.job_id, []).append((attempt, outcome))
        if not _is_sha256(attempt.evidence_sha256):
            issues.append(
                f"verification attempt {attempt.id} has an invalid evidence digest"
            )
        if not _is_sha256(attempt.baseline_sha256):
            issues.append(
                f"verification attempt {attempt.id} has an invalid baseline digest"
            )
        if attempt.attempt_no > attempt.max_attempts:
            issues.append(
                f"verification attempt {attempt.id} exceeds its captured retry budget"
            )
        job = jobs.get(attempt.job_id)
        if job is None:
            issues.append(
                f"verification attempt {attempt.id} has no verification job"
            )
        else:
            evidence = evidence_records.get(job.evidence_id)
            baseline = baseline_records.get(job.baseline_id)
            if (
                evidence is None
                or evidence.sha256 != attempt.evidence_sha256
            ):
                issues.append(
                    f"verification attempt {attempt.id} disagrees with its evidence record"
                )
            if (
                baseline is None
                or baseline.sha256 != attempt.baseline_sha256
            ):
                issues.append(
                    f"verification attempt {attempt.id} disagrees with its baseline record"
                )
            if (
                job.analyzer_name != attempt.analyzer_name
                or job.analyzer_version != attempt.analyzer_version
            ):
                issues.append(
                    f"verification attempt {attempt.id} disagrees with its analyzer pin"
                )
        if outcome is None:
            continue
        if _aware(outcome.finished_at) < _aware(attempt.claimed_at):
            issues.append(
                f"verification attempt {attempt.id} finishes before it was claimed"
            )
        if outcome.disposition not in VERIFICATION_ATTEMPT_DISPOSITIONS:
            issues.append(
                f"verification attempt {attempt.id} has an unsupported disposition"
            )
            continue
        if outcome.disposition == "committed_success":
            if not isinstance(outcome.result_json, dict) or not _is_sha256(
                outcome.result_sha256
            ):
                issues.append(
                    f"verification attempt {attempt.id} has an incomplete success outcome"
                )
            elif (
                sha256_bytes(canonical_json_bytes(outcome.result_json))
                != outcome.result_sha256
            ):
                issues.append(
                    f"verification attempt {attempt.id} has a mismatched result digest"
                )
            if (
                outcome.error_code is not None
                or outcome.error_message is not None
                or outcome.error_retryable is not None
                or outcome.dead_lettered
            ):
                issues.append(
                    f"verification attempt {attempt.id} mixes success and failure fields"
                )
            if job is None or job.result_json is None:
                issues.append(
                    f"verification attempt {attempt.id} success has no persisted job result"
                )
            elif _is_sha256(outcome.result_sha256) and (
                sha256_bytes(canonical_json_bytes(job.result_json))
                != outcome.result_sha256
            ):
                issues.append(
                    f"verification attempt {attempt.id} disagrees with the persisted job result"
                )
        else:
            if outcome.result_json is not None or outcome.result_sha256 is not None:
                issues.append(
                    f"verification attempt {attempt.id} has a result on a non-success outcome"
                )
            if outcome.disposition == "committed_failure" and (
                not outcome.error_code
                or outcome.error_retryable is None
                or not outcome.error_message
            ):
                issues.append(
                    f"verification attempt {attempt.id} has an incomplete failure outcome"
                )
            if outcome.disposition == "lease_expired" and (
                outcome.error_code != "WORKER_LEASE_EXPIRED"
                or outcome.error_retryable is not True
            ):
                issues.append(
                    f"verification attempt {attempt.id} has an invalid lease-expiry outcome"
                )
            if outcome.disposition == "lease_lost" and (
                outcome.error_code != "WORKER_LEASE_LOST"
                or outcome.error_retryable is not True
            ):
                issues.append(
                    f"verification attempt {attempt.id} has an invalid lease-loss outcome"
                )
            if outcome.disposition == "write_fenced" and (
                outcome.error_code != "WORKER_WRITE_FENCED"
                or outcome.error_retryable is not True
            ):
                issues.append(
                    f"verification attempt {attempt.id} has an invalid fenced-write outcome"
                )
            if outcome.dead_lettered and outcome.disposition not in {
                "committed_failure",
                "lease_expired",
            }:
                issues.append(
                    f"verification attempt {attempt.id} has an invalid dead-letter marker"
                )

    for job_id, attempt_rows in attempts_by_job.items():
        job = jobs.get(job_id)
        lease = leases.get(job_id)
        latest_attempt, _ = attempt_rows[-1]
        if lease is None:
            issues.append(
                f"verification job {job_id} has attempt history but no dispatch lease"
            )
        elif (
            latest_attempt.generation != lease.generation
            or latest_attempt.attempt_no != lease.attempt_count
        ):
            issues.append(
                f"verification job {job_id} lease counters disagree with attempt history"
            )
        for attempt, outcome in attempt_rows:
            is_current_live_attempt = (
                job is not None
                and job.status == "running"
                and lease is not None
                and lease.owner_id == attempt.worker_id
                and lease.generation == attempt.generation
                and lease.attempt_count == attempt.attempt_no
                and lease.lease_expires_at is not None
                and _aware(lease.lease_expires_at) > now
            )
            if outcome is None and not is_current_live_attempt:
                issues.append(
                    f"verification attempt {attempt.id} has no terminal outcome "
                    "and is not the current live lease"
                )
            if outcome is not None and is_current_live_attempt:
                issues.append(
                    f"verification attempt {attempt.id} is terminal while its lease is live"
                )

    for job_id, job in jobs.items():
        if job.status != "running":
            continue
        lease = leases.get(job_id)
        if (
            lease is None
            or lease.owner_id is None
            or lease.lease_expires_at is None
            or _aware(lease.lease_expires_at) <= now
        ):
            continue
        matching = [
            attempt
            for attempt, _ in attempts_by_job.get(job_id, [])
            if attempt.generation == lease.generation
            and attempt.attempt_no == lease.attempt_count
            and attempt.worker_id == lease.owner_id
        ]
        if len(matching) != 1:
            issues.append(
                f"running verification job {job_id} has no matching immutable attempt"
            )
    return issues
