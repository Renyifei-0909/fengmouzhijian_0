from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..config import Settings
from ..models import (
    VerificationAttempt,
    VerificationAttemptOutcome,
    VerificationJob,
    VerificationJobLease,
    VERIFICATION_ATTEMPT_DISPOSITIONS,
    VERIFICATION_JOB_STATUSES,
)
from ..schemas import (
    VerificationOperationsAlert,
    VerificationOperationsAttempts,
    VerificationOperationsDispatch,
    VerificationOperationsIntegrity,
    VerificationOperationsJobs,
    VerificationOperationsSnapshot,
    VerificationOperationsThresholds,
)
from .analysis import (
    scan_verification_attempt_integrity,
    scan_verification_dispatch_integrity,
)


OUTCOME_DISPOSITIONS = VERIFICATION_ATTEMPT_DISPOSITIONS
INSTABILITY_DISPOSITIONS = frozenset(
    {
        "lease_expired",
        "lease_lost",
        "write_fenced",
    }
)


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _database_now(db: Session) -> datetime:
    value = db.scalar(select(func.current_timestamp()))
    if not isinstance(value, datetime):
        raise RuntimeError("Database did not return a timestamp for observability")
    return _aware(value)


def _age_seconds(now: datetime, value: datetime | None) -> float | None:
    if value is None:
        return None
    return round(max(0.0, (_aware(now) - _aware(value)).total_seconds()), 3)


def _outcome_counts(
    rows: list[tuple[str, int]],
) -> dict[str, int]:
    counts = {disposition: 0 for disposition in OUTCOME_DISPOSITIONS}
    for disposition, count in rows:
        if disposition in counts:
            counts[disposition] = int(count)
    return counts


def verification_operations_snapshot(
    db: Session,
    settings: Settings,
) -> VerificationOperationsSnapshot:
    """Return aggregate worker health without exposing task or worker identifiers."""

    now = _database_now(db)
    recent_cutoff = now - timedelta(
        seconds=settings.verification_observability_window_seconds
    )
    queue_warning_cutoff = now - timedelta(
        seconds=settings.verification_queue_warning_seconds
    )

    job_rows = db.execute(
        select(VerificationJob.status, func.count(VerificationJob.id)).group_by(
            VerificationJob.status
        )
    ).all()
    jobs_by_status: dict[str, int] = {}
    for job_status, count in job_rows:
        bounded_status = (
            str(job_status)
            if job_status in VERIFICATION_JOB_STATUSES
            else "other"
        )
        jobs_by_status[bounded_status] = (
            jobs_by_status.get(bounded_status, 0) + int(count)
        )

    active_lease_filter = (
        VerificationJob.status == "running",
        VerificationJobLease.owner_id.is_not(None),
        VerificationJobLease.lease_expires_at.is_not(None),
        VerificationJobLease.lease_expires_at > now,
        VerificationJobLease.dead_lettered_at.is_(None),
    )
    lease_rows = int(
        db.scalar(
            select(func.count(VerificationJobLease.job_id)).select_from(
                VerificationJobLease
            )
        )
        or 0
    )
    active_leases = int(
        db.scalar(
            select(func.count(VerificationJobLease.job_id))
            .select_from(VerificationJobLease)
            .join(
                VerificationJob,
                VerificationJob.id == VerificationJobLease.job_id,
            )
            .where(*active_lease_filter)
        )
        or 0
    )
    expired_running_leases = int(
        db.scalar(
            select(func.count(VerificationJobLease.job_id))
            .select_from(VerificationJobLease)
            .join(
                VerificationJob,
                VerificationJob.id == VerificationJobLease.job_id,
            )
            .where(
                VerificationJob.status == "running",
                VerificationJobLease.owner_id.is_not(None),
                VerificationJobLease.lease_expires_at.is_not(None),
                VerificationJobLease.lease_expires_at <= now,
            )
        )
        or 0
    )
    unclaimed_queued_jobs = int(
        db.scalar(
            select(func.count(VerificationJob.id))
            .select_from(VerificationJob)
            .outerjoin(
                VerificationJobLease,
                VerificationJobLease.job_id == VerificationJob.id,
            )
            .where(
                VerificationJob.status == "queued",
                or_(
                    VerificationJobLease.job_id.is_(None),
                    VerificationJobLease.owner_id.is_(None),
                ),
            )
        )
        or 0
    )
    queued_over_warning_threshold = int(
        db.scalar(
            select(func.count(VerificationJob.id))
            .select_from(VerificationJob)
            .where(
                VerificationJob.status == "queued",
                VerificationJob.created_at < queue_warning_cutoff,
            )
        )
        or 0
    )
    dead_letter_jobs = int(
        db.scalar(
            select(func.count(VerificationJobLease.job_id))
            .select_from(VerificationJobLease)
            .where(VerificationJobLease.dead_lettered_at.is_not(None))
        )
        or 0
    )
    oldest_queued_at = db.scalar(
        select(func.min(VerificationJob.created_at)).where(
            VerificationJob.status == "queued"
        )
    )
    oldest_active_heartbeat_at = db.scalar(
        select(
            func.min(
                func.coalesce(
                    VerificationJobLease.heartbeat_at,
                    VerificationJobLease.claimed_at,
                )
            )
        )
        .select_from(VerificationJobLease)
        .join(
            VerificationJob,
            VerificationJob.id == VerificationJobLease.job_id,
        )
        .where(*active_lease_filter)
    )

    attempt_total = int(
        db.scalar(
            select(func.count(VerificationAttempt.id)).select_from(
                VerificationAttempt
            )
        )
        or 0
    )
    open_attempts = int(
        db.scalar(
            select(func.count(VerificationAttempt.id))
            .select_from(VerificationAttempt)
            .outerjoin(
                VerificationAttemptOutcome,
                VerificationAttemptOutcome.attempt_id == VerificationAttempt.id,
            )
            .where(VerificationAttemptOutcome.id.is_(None))
        )
        or 0
    )
    outcomes_total = _outcome_counts(
        [
            (str(disposition), int(count))
            for disposition, count in db.execute(
                select(
                    VerificationAttemptOutcome.disposition,
                    func.count(VerificationAttemptOutcome.id),
                ).group_by(VerificationAttemptOutcome.disposition)
            ).all()
        ]
    )
    outcomes_window = _outcome_counts(
        [
            (str(disposition), int(count))
            for disposition, count in db.execute(
                select(
                    VerificationAttemptOutcome.disposition,
                    func.count(VerificationAttemptOutcome.id),
                )
                .where(VerificationAttemptOutcome.finished_at >= recent_cutoff)
                .group_by(VerificationAttemptOutcome.disposition)
            ).all()
        ]
    )
    recent_instability = sum(
        outcomes_window[disposition]
        for disposition in INSTABILITY_DISPOSITIONS
    )

    dispatch_issues = scan_verification_dispatch_integrity(db)
    attempt_issues = scan_verification_attempt_integrity(db)
    issue_count = len(dispatch_issues) + len(attempt_issues)

    alerts: list[VerificationOperationsAlert] = []
    if issue_count:
        alerts.append(
            VerificationOperationsAlert(
                severity="incident",
                code="INTEGRITY_INCIDENT",
                count=issue_count,
                message=(
                    "Worker dispatch or attempt history contains an integrity "
                    "contradiction; readyz is expected to fail closed."
                ),
            )
        )
    if dead_letter_jobs:
        alerts.append(
            VerificationOperationsAlert(
                severity="warning",
                code="DEAD_LETTER_PRESENT",
                count=dead_letter_jobs,
                message=(
                    "One or more verification jobs exhausted their retry budget "
                    "and require explicit operational review."
                ),
            )
        )
    oldest_queued_seconds = _age_seconds(now, oldest_queued_at)
    if queued_over_warning_threshold:
        alerts.append(
            VerificationOperationsAlert(
                severity="warning",
                code="QUEUE_WAIT_EXCEEDED",
                count=queued_over_warning_threshold,
                message=(
                    "The oldest queued verification job exceeded the configured "
                    "wait-warning threshold."
                ),
            )
        )
    if recent_instability:
        alerts.append(
            VerificationOperationsAlert(
                severity="warning",
                code="RECENT_LEASE_INSTABILITY",
                count=recent_instability,
                message=(
                    "Lease expiry, lease loss, or fenced writes occurred inside "
                    "the configured observation window."
                ),
            )
        )

    snapshot_status = (
        "incident"
        if issue_count
        else "attention"
        if alerts
        else "healthy"
    )
    return VerificationOperationsSnapshot(
        status=snapshot_status,
        generated_at=now,
        execution_mode=settings.verification_execution_mode,
        thresholds=VerificationOperationsThresholds(
            queue_wait_warning_seconds=settings.verification_queue_warning_seconds,
            recent_window_seconds=settings.verification_observability_window_seconds,
            lease_seconds=settings.verification_lease_seconds,
            heartbeat_seconds=settings.verification_heartbeat_seconds,
        ),
        jobs=VerificationOperationsJobs(
            total=sum(jobs_by_status.values()),
            by_status=jobs_by_status,
        ),
        dispatch=VerificationOperationsDispatch(
            lease_rows=lease_rows,
            active_leases=active_leases,
            expired_running_leases=expired_running_leases,
            unclaimed_queued_jobs=unclaimed_queued_jobs,
            queued_over_warning_threshold=queued_over_warning_threshold,
            dead_letter_jobs=dead_letter_jobs,
            oldest_queued_seconds=oldest_queued_seconds,
            oldest_active_heartbeat_seconds=_age_seconds(
                now,
                oldest_active_heartbeat_at,
            ),
        ),
        attempts=VerificationOperationsAttempts(
            total=attempt_total,
            open=open_attempts,
            outcomes_total_by_disposition=outcomes_total,
            outcomes_window_by_disposition=outcomes_window,
            recent_instability=recent_instability,
        ),
        integrity=VerificationOperationsIntegrity(
            status="incident" if issue_count else "ok",
            dispatch_issue_count=len(dispatch_issues),
            attempt_issue_count=len(attempt_issues),
            issue_count=issue_count,
        ),
        alerts=alerts,
        truth_note=(
            "Database-derived operational snapshot for local diagnosis; it is "
            "not an uptime SLA, external monitoring system, or production-readiness claim."
        ),
    )
