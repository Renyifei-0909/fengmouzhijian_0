from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import timezone
from math import isfinite

from ..models import VERIFICATION_JOB_STATUSES
from ..schemas import VerificationOperationsSnapshot
from .observability import OUTCOME_DISPOSITIONS


PROMETHEUS_CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"
PROMETHEUS_JOB_STATUSES = VERIFICATION_JOB_STATUSES
PROMETHEUS_OPERATION_STATUSES = ("healthy", "attention", "incident")
PROMETHEUS_ALERT_CODES = (
    "INTEGRITY_INCIDENT",
    "DEAD_LETTER_PRESENT",
    "QUEUE_WAIT_EXCEEDED",
    "RECENT_LEASE_INSTABILITY",
)


MetricLabels = Mapping[str, str]
MetricSample = tuple[MetricLabels, int | float]


def _escape_label(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _format_value(value: int | float) -> str:
    if isinstance(value, int):
        return str(value)
    numeric = float(value)
    if not isfinite(numeric):
        raise ValueError("Prometheus metrics must be finite")
    if numeric.is_integer():
        return str(int(numeric))
    return format(numeric, ".15g")


def _metric_family(
    name: str,
    help_text: str,
    samples: Iterable[MetricSample],
) -> list[str]:
    lines = [
        f"# HELP {name} {help_text}",
        f"# TYPE {name} gauge",
    ]
    for labels, value in samples:
        label_text = ""
        if labels:
            pairs = ",".join(
                f'{label}="{_escape_label(label_value)}"'
                for label, label_value in labels.items()
            )
            label_text = f"{{{pairs}}}"
        lines.append(f"{name}{label_text} {_format_value(value)}")
    return lines


def render_verification_prometheus(
    snapshot: VerificationOperationsSnapshot,
    *,
    collection_duration_seconds: float,
) -> str:
    """Render a bounded-cardinality Prometheus 0.0.4 snapshot.

    All labels are selected from fixed enumerations. Database identifiers,
    arbitrary status strings, and alert messages are intentionally excluded.
    Stored history counts remain gauges because restore or administrative
    replacement can move a database snapshot backwards.
    """

    if collection_duration_seconds < 0:
        raise ValueError("Prometheus collection duration must not be negative")
    bounded_job_statuses = (*PROMETHEUS_JOB_STATUSES, "other")
    bounded_job_total = sum(
        snapshot.jobs.by_status.get(job_status, 0)
        for job_status in bounded_job_statuses
    )
    if bounded_job_total != snapshot.jobs.total:
        raise ValueError("Verification job status totals are inconsistent")
    other_job_total = snapshot.jobs.by_status.get("other", 0)

    alerts_by_code = {code: 0 for code in PROMETHEUS_ALERT_CODES}
    for alert in snapshot.alerts:
        alerts_by_code[alert.code] += alert.count

    generated_at = snapshot.generated_at
    if generated_at.tzinfo is None:
        generated_at = generated_at.replace(tzinfo=timezone.utc)

    lines: list[str] = []
    lines.extend(
        _metric_family(
            "fengmou_verification_operations_info",
            "Static information about the verification operations snapshot.",
            [({"execution_mode": snapshot.execution_mode}, 1)],
        )
    )
    lines.extend(
        _metric_family(
            "fengmou_verification_operations_snapshot_timestamp_seconds",
            "Database timestamp used as the reference time for this snapshot.",
            [({}, generated_at.timestamp())],
        )
    )
    lines.extend(
        _metric_family(
            "fengmou_verification_operations_collection_duration_seconds",
            "Local duration required to collect the database snapshot.",
            [({}, collection_duration_seconds)],
        )
    )
    lines.extend(
        _metric_family(
            "fengmou_verification_operations_status",
            "Current verification operations status represented as a one-hot gauge.",
            [
                ({"status": operation_status}, int(snapshot.status == operation_status))
                for operation_status in PROMETHEUS_OPERATION_STATUSES
            ],
        )
    )
    lines.extend(
        _metric_family(
            "fengmou_verification_jobs",
            "Current number of persisted verification jobs by bounded status.",
            [
                (
                    {"status": job_status},
                    snapshot.jobs.by_status.get(job_status, 0),
                )
                for job_status in PROMETHEUS_JOB_STATUSES
            ]
            + [({"status": "other"}, other_job_total)],
        )
    )
    lines.extend(
        _metric_family(
            "fengmou_verification_dispatch_leases",
            "Current number of verification dispatch lease rows by bounded state.",
            [
                ({"state": "stored"}, snapshot.dispatch.lease_rows),
                ({"state": "active"}, snapshot.dispatch.active_leases),
                (
                    {"state": "expired_running"},
                    snapshot.dispatch.expired_running_leases,
                ),
            ],
        )
    )
    lines.extend(
        _metric_family(
            "fengmou_verification_queue_unclaimed_jobs",
            "Current number of queued verification jobs without an owner.",
            [({}, snapshot.dispatch.unclaimed_queued_jobs)],
        )
    )
    lines.extend(
        _metric_family(
            "fengmou_verification_queue_over_warning_jobs",
            "Current number of queued jobs older than the configured warning threshold.",
            [({}, snapshot.dispatch.queued_over_warning_threshold)],
        )
    )
    lines.extend(
        _metric_family(
            "fengmou_verification_dead_letter_jobs",
            "Current number of verification jobs marked as dead letter.",
            [({}, snapshot.dispatch.dead_letter_jobs)],
        )
    )
    lines.extend(
        _metric_family(
            "fengmou_verification_queue_oldest_age_seconds",
            "Age of the oldest queued verification job; absent when the queue is empty.",
            (
                [({}, snapshot.dispatch.oldest_queued_seconds)]
                if snapshot.dispatch.oldest_queued_seconds is not None
                else []
            ),
        )
    )
    lines.extend(
        _metric_family(
            "fengmou_verification_active_lease_oldest_heartbeat_age_seconds",
            "Age of the oldest active lease heartbeat; absent when no lease is active.",
            (
                [({}, snapshot.dispatch.oldest_active_heartbeat_seconds)]
                if snapshot.dispatch.oldest_active_heartbeat_seconds is not None
                else []
            ),
        )
    )
    lines.extend(
        _metric_family(
            "fengmou_verification_attempts",
            "Current number of persisted verification attempts by bounded state.",
            [
                ({"state": "stored"}, snapshot.attempts.total),
                ({"state": "open"}, snapshot.attempts.open),
            ],
        )
    )
    lines.extend(
        _metric_family(
            "fengmou_verification_attempt_outcomes",
            "Database-stored attempt outcome count by disposition and bounded window.",
            [
                (
                    {
                        "window": window,
                        "disposition": disposition,
                    },
                    counts.get(disposition, 0),
                )
                for window, counts in (
                    ("all", snapshot.attempts.outcomes_total_by_disposition),
                    ("recent", snapshot.attempts.outcomes_window_by_disposition),
                )
                for disposition in OUTCOME_DISPOSITIONS
            ],
        )
    )
    lines.extend(
        _metric_family(
            "fengmou_verification_recent_lease_instability",
            "Attempt outcomes indicating lease instability inside the recent window.",
            [({}, snapshot.attempts.recent_instability)],
        )
    )
    lines.extend(
        _metric_family(
            "fengmou_verification_integrity_issues",
            "Current number of verification integrity contradictions by component.",
            [
                (
                    {"component": "dispatch"},
                    snapshot.integrity.dispatch_issue_count,
                ),
                (
                    {"component": "attempt"},
                    snapshot.integrity.attempt_issue_count,
                ),
            ],
        )
    )
    lines.extend(
        _metric_family(
            "fengmou_verification_alerts",
            "Current verification operations alert count by bounded machine code.",
            [
                ({"code": code}, alerts_by_code[code])
                for code in PROMETHEUS_ALERT_CODES
            ],
        )
    )
    lines.extend(
        _metric_family(
            "fengmou_verification_queue_warning_threshold_seconds",
            "Configured queue wait warning threshold.",
            [({}, snapshot.thresholds.queue_wait_warning_seconds)],
        )
    )
    lines.extend(
        _metric_family(
            "fengmou_verification_observability_window_seconds",
            "Configured recent outcome observation window.",
            [({}, snapshot.thresholds.recent_window_seconds)],
        )
    )
    lines.extend(
        _metric_family(
            "fengmou_verification_lease_duration_seconds",
            "Configured verification lease duration.",
            [({}, snapshot.thresholds.lease_seconds)],
        )
    )
    lines.extend(
        _metric_family(
            "fengmou_verification_heartbeat_interval_seconds",
            "Configured verification worker heartbeat interval.",
            [({}, snapshot.thresholds.heartbeat_seconds)],
        )
    )
    return "\n".join(lines) + "\n"
