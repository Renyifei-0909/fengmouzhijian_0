from __future__ import annotations

import pytest

from app.api import router as api_router
from app.config import Settings
from app.models import SealOperation, VerificationJob, new_id, utcnow


def _job(status: str) -> VerificationJob:
    return VerificationJob(
        id=new_id(),
        project_id=new_id(),
        baseline_id=new_id(),
        evidence_id=new_id(),
        analyzer_name="stub",
        analyzer_version="stub-1.0",
        status=status,
        progress=0,
    )


def _operation(job: VerificationJob, state: str, *, last_error: str | None = None) -> SealOperation:
    return SealOperation(
        id=new_id(),
        job_id=job.id,
        review_id=new_id(),
        report_id=new_id(),
        archive_id=f"ARC-{new_id()}",
        state=state,
        attempt_count=2,
        last_error=last_error,
        updated_at=utcnow(),
    )


@pytest.mark.parametrize(
    ("descriptor", "retryable", "reason_fragment"),
    [
        ({"enabled": True, "version": "stub-1.0"}, True, "explicitly retry"),
        ({"enabled": False, "version": "stub-1.0"}, False, "disabled or unknown"),
        ({"enabled": True, "version": "stub-2.0"}, False, "version changed"),
    ],
)
def test_failed_analysis_recovery_reflects_current_adapter_contract(
    monkeypatch: pytest.MonkeyPatch,
    descriptor: dict[str, object],
    retryable: bool,
    reason_fragment: str,
) -> None:
    monkeypatch.setattr(api_router, "analyzer_descriptor", lambda *_args, **_kwargs: descriptor)
    recovery = api_router._verification_recovery(
        _job("failed"),
        None,
        Settings(environment="test"),
        failure_retryable=True,
    )
    assert recovery.action == "retry_analysis"
    assert recovery.retryable is retryable
    assert reason_fragment in recovery.reason


def test_failed_analysis_with_unknown_persisted_adapter_is_not_retryable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unknown_adapter(*_args, **_kwargs):
        raise KeyError("unknown adapter")

    monkeypatch.setattr(api_router, "analyzer_descriptor", unknown_adapter)
    recovery = api_router._verification_recovery(
        _job("failed"),
        None,
        Settings(environment="test"),
        failure_retryable=True,
    )
    assert recovery.action == "retry_analysis"
    assert recovery.retryable is False


@pytest.mark.parametrize("failure_retryable", [False, None])
def test_failed_analysis_without_trusted_retry_classification_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    failure_retryable: bool | None,
) -> None:
    monkeypatch.setattr(
        api_router,
        "analyzer_descriptor",
        lambda *_args, **_kwargs: {"enabled": True, "version": "stub-1.0"},
    )
    recovery = api_router._verification_recovery(
        _job("failed"),
        None,
        Settings(environment="test"),
        failure_retryable=failure_retryable,
    )
    assert recovery.action == "retry_analysis"
    assert recovery.retryable is False
    assert "non-retryable" in recovery.reason


def test_sealing_recovery_distinguishes_resume_from_integrity_review() -> None:
    settings = Settings(environment="test")
    sealing_job = _job("sealing")

    missing = api_router._verification_recovery(sealing_job, None, settings)
    assert missing.action == "integrity_review"
    assert missing.retryable is False

    manual = api_router._verification_recovery(
        sealing_job,
        _operation(sealing_job, "manual_attention", last_error="digest mismatch"),
        settings,
    )
    assert manual.action == "integrity_review"
    assert manual.last_error == "digest mismatch"
    assert manual.attempt_count == 2

    inconsistent = api_router._verification_recovery(
        sealing_job,
        _operation(sealing_job, "completed"),
        settings,
    )
    assert inconsistent.action == "integrity_review"
    assert "inconsistent" in inconsistent.reason

    resumable = api_router._verification_recovery(
        sealing_job,
        _operation(sealing_job, "ledger_appended", last_error="temporary database error"),
        settings,
    )
    assert resumable.action == "resume_sealing"
    assert resumable.retryable is True
    assert resumable.operation_state == "ledger_appended"

    unknown = api_router._verification_recovery(
        sealing_job,
        _operation(sealing_job, "unexpected_state", last_error="unrecognized state"),
        settings,
    )
    assert unknown.action == "integrity_review"
    assert unknown.retryable is False
    assert "unknown" in unknown.reason


def test_approved_job_requires_a_completed_operation_and_other_states_have_no_action() -> None:
    settings = Settings(environment="test")
    approved = _job("approved")
    inconsistent = api_router._verification_recovery(approved, None, settings)
    assert inconsistent.action == "integrity_review"

    completed = api_router._verification_recovery(
        approved,
        _operation(approved, "completed"),
        settings,
    )
    assert completed.action == "none"
    assert completed.operation_state == "completed"

    stale_error = api_router._verification_recovery(
        approved,
        _operation(approved, "completed", last_error="stale terminal error"),
        settings,
    )
    assert stale_error.action == "integrity_review"
    assert stale_error.retryable is False
    assert stale_error.last_error == "stale terminal error"

    queued = api_router._verification_recovery(_job("queued"), None, settings)
    assert queued.action == "none"
    assert queued.retryable is False


def test_historical_terminal_job_without_a_lease_is_shown_as_released() -> None:
    settings = Settings(environment="test")
    terminal = api_router._verification_dispatch(_job("approved"), None, settings)
    pending = api_router._verification_dispatch(_job("queued"), None, settings)
    assert terminal.state == "released"
    assert terminal.generation == 0
    assert terminal.attempt_count == 0
    assert pending.state == "unclaimed"
