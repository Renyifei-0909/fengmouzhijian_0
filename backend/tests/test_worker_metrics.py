from __future__ import annotations

from datetime import datetime, timezone
from math import isfinite
import re

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.schemas import (
    VerificationOperationsAlert,
    VerificationOperationsAttempts,
    VerificationOperationsDispatch,
    VerificationOperationsIntegrity,
    VerificationOperationsJobs,
    VerificationOperationsSnapshot,
    VerificationOperationsThresholds,
)
from app.services.metrics import (
    PROMETHEUS_ALERT_CODES,
    PROMETHEUS_CONTENT_TYPE,
    PROMETHEUS_JOB_STATUSES,
    render_verification_prometheus,
)
from app.services.observability import OUTCOME_DISPOSITIONS


SAMPLE_NAME = re.compile(r"^[a-zA-Z_:][a-zA-Z0-9_:]*(?:\{.*\})?$")


def _settings(tmp_path) -> Settings:
    return Settings(
        environment="test",
        database_url=f"sqlite:///{tmp_path / 'metrics.db'}",
        database_schema_mode="create_all",
        storage_root=tmp_path / "storage",
        operator_api_key="metrics-operator",
        reviewer_api_key="metrics-reviewer",
        auditor_api_key="metrics-auditor",
        verification_execution_mode="external",
    )


def _outcomes(**updates: int) -> dict[str, int]:
    values = {disposition: 0 for disposition in OUTCOME_DISPOSITIONS}
    values.update(updates)
    return values


def _snapshot() -> VerificationOperationsSnapshot:
    return VerificationOperationsSnapshot(
        status="attention",
        generated_at=datetime(2026, 7, 28, 5, 0, tzinfo=timezone.utc),
        execution_mode="external",
        thresholds=VerificationOperationsThresholds(
            queue_wait_warning_seconds=60,
            recent_window_seconds=900,
            lease_seconds=30,
            heartbeat_seconds=10,
        ),
        jobs=VerificationOperationsJobs(
            total=12,
            by_status={
                "queued": 2,
                "running": 1,
                "needs_review": 1,
                "other": 8,
            },
        ),
        dispatch=VerificationOperationsDispatch(
            lease_rows=12,
            active_leases=1,
            expired_running_leases=0,
            unclaimed_queued_jobs=2,
            queued_over_warning_threshold=1,
            dead_letter_jobs=3,
            oldest_queued_seconds=125.5,
            oldest_active_heartbeat_seconds=4.25,
        ),
        attempts=VerificationOperationsAttempts(
            total=9,
            open=1,
            outcomes_total_by_disposition=_outcomes(
                committed_success=3,
                committed_failure=2,
                lease_expired=1,
            ),
            outcomes_window_by_disposition=_outcomes(
                lease_expired=1,
                write_fenced=2,
            ),
            recent_instability=3,
        ),
        integrity=VerificationOperationsIntegrity(
            status="ok",
            dispatch_issue_count=0,
            attempt_issue_count=0,
            issue_count=0,
        ),
        alerts=[
            VerificationOperationsAlert(
                severity="warning",
                code="DEAD_LETTER_PRESENT",
                count=3,
                message="private job and worker identifiers must never be rendered",
            ),
            VerificationOperationsAlert(
                severity="warning",
                code="QUEUE_WAIT_EXCEEDED",
                count=1,
                message="private queue detail",
            ),
            VerificationOperationsAlert(
                severity="warning",
                code="RECENT_LEASE_INSTABILITY",
                count=3,
                message="private lease detail",
            ),
        ],
        truth_note="private diagnostic prose is not a metric label",
    )


def _samples(body: str, metric_name: str) -> list[str]:
    return [
        line
        for line in body.splitlines()
        if not line.startswith("#") and line.partition("{")[0].partition(" ")[0] == metric_name
    ]


def test_metrics_endpoint_is_authenticated_private_prometheus_text(tmp_path) -> None:
    app = create_app(_settings(tmp_path))
    path = "/api/v1/operations/verification-dispatch/metrics"
    with TestClient(app) as client:
        assert client.get(path).status_code == 401
        response = client.get(path, headers={"X-API-Key": "metrics-reviewer"})

    assert response.status_code == 200
    assert response.headers["content-type"] == PROMETHEUS_CONTENT_TYPE
    assert response.headers["cache-control"] == "private, no-store, max-age=0"
    assert response.headers["pragma"] == "no-cache"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.text.endswith("\n")
    assert "# TYPE fengmou_verification_jobs gauge" in response.text
    assert _samples(response.text, "fengmou_verification_queue_oldest_age_seconds") == []
    assert (
        _samples(
            response.text,
            "fengmou_verification_active_lease_oldest_heartbeat_age_seconds",
        )
        == []
    )
    assert "job_id" not in response.text
    assert "worker_id" not in response.text
    assert "metrics-reviewer" not in response.text


def test_renderer_uses_only_bounded_labels_and_snapshot_gauges() -> None:
    body = render_verification_prometheus(
        _snapshot(),
        collection_duration_seconds=0.0125,
    )

    assert 'fengmou_verification_operations_info{execution_mode="external"} 1' in body
    assert (
        "fengmou_verification_operations_collection_duration_seconds 0.0125"
        in body
    )
    assert 'fengmou_verification_operations_status{status="healthy"} 0' in body
    assert 'fengmou_verification_operations_status{status="attention"} 1' in body
    assert 'fengmou_verification_operations_status{status="incident"} 0' in body
    for status in PROMETHEUS_JOB_STATUSES:
        assert len(
            [
                line
                for line in _samples(body, "fengmou_verification_jobs")
                if f'status="{status}"' in line
            ]
        ) == 1
    assert 'fengmou_verification_jobs{status="other"} 8' in body
    assert "private job and worker identifiers" not in body
    assert "private diagnostic prose" not in body
    assert "fengmou_verification_queue_oldest_age_seconds 125.5" in body
    assert (
        "fengmou_verification_active_lease_oldest_heartbeat_age_seconds 4.25"
        in body
    )
    assert (
        'fengmou_verification_attempt_outcomes{window="recent",'
        'disposition="write_fenced"} 2'
    ) in body
    assert (
        'fengmou_verification_alerts{code="DEAD_LETTER_PRESENT"} 3'
        in body
    )
    assert (
        'fengmou_verification_alerts{code="INTEGRITY_INCIDENT"} 0'
        in body
    )
    for code in PROMETHEUS_ALERT_CODES:
        assert len(
            [
                line
                for line in _samples(body, "fengmou_verification_alerts")
                if f'code="{code}"' in line
            ]
        ) == 1

    sample_keys: set[str] = set()
    lines = body.splitlines()
    help_positions: dict[str, int] = {}
    type_positions: dict[str, int] = {}
    for index, line in enumerate(lines):
        if line.startswith("# HELP "):
            family = line.split(" ", 3)[2]
            assert family not in help_positions
            help_positions[family] = index
        elif line.startswith("# TYPE "):
            _, _, family, metric_type = line.split(" ")
            assert metric_type == "gauge"
            assert family not in type_positions
            type_positions[family] = index

    for index, line in enumerate(lines):
        if line.startswith("#") or not line:
            continue
        key, value = line.rsplit(" ", 1)
        assert SAMPLE_NAME.fullmatch(key), line
        assert isfinite(float(value))
        assert key not in sample_keys
        sample_keys.add(key)
        family = key.partition("{")[0]
        assert help_positions[family] < index
        assert type_positions[family] < index


def test_renderer_rejects_inconsistent_or_nonfinite_snapshots() -> None:
    snapshot = _snapshot()
    snapshot.jobs.total = 1
    with pytest.raises(ValueError, match="status totals"):
        render_verification_prometheus(
            snapshot,
            collection_duration_seconds=0.01,
        )

    snapshot = _snapshot()
    with pytest.raises(ValueError, match="finite"):
        render_verification_prometheus(
            snapshot,
            collection_duration_seconds=float("nan"),
        )

    with pytest.raises(ValueError, match="must not be negative"):
        render_verification_prometheus(
            snapshot,
            collection_duration_seconds=-0.01,
        )

    snapshot.generated_at = datetime(2026, 7, 28, 5, 0)
    body = render_verification_prometheus(
        snapshot,
        collection_duration_seconds=0.01,
    )
    assert (
        "fengmou_verification_operations_snapshot_timestamp_seconds "
        f"{int(datetime(2026, 7, 28, 5, 0, tzinfo=timezone.utc).timestamp())}"
    ) in body
