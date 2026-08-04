"""P2-1.3: analysis-completion WorkOrder transitions must be atomic and observable."""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.config import Settings
from app.main import create_app
from app.models import (
    AuditEvent,
    ComplianceEvaluation,
    VerificationAttemptOutcome,
    VerificationJob,
    WorkOrder,
)
from app.services import analysis
from app.services.work_orders import (
    WORK_ORDER_MISSING,
    WORK_ORDER_TRANSITION_FAILED,
    WorkOrderIntegrityError,
    WorkOrderTransitionError,
    apply_analysis_completion_transitions,
    map_compliance_to_work_order_status,
)


PACKAGE_PATH = (
    Path(__file__).resolve().parents[2]
    / "examples"
    / "design-package-demo"
    / "synthetic-pipe-route-package.json"
)


def _tiny_png() -> bytes:
    return (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00"
        b"\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
    )


def _near_lon_lat() -> tuple[float, float]:
    from app.services.spatial import utm32n_to_wgs84

    return utm32n_to_wgs84(400000.0, 5700000.0)


def _settings(tmp_path: Path, **updates: Any) -> Settings:
    values: dict[str, Any] = {
        "environment": "test",
        "database_url": f"sqlite:///{tmp_path / 'p213.db'}",
        "database_schema_mode": "create_all",
        "storage_root": tmp_path / "storage",
        "max_upload_bytes": 2 * 1024 * 1024,
        "allow_demo_analyzer": True,
        "operator_api_key": "test-operator-key",
        "reviewer_api_key": "test-reviewer-key",
        "auditor_api_key": "test-auditor-key",
        "gpkg_preview_signing_secret": "test-gpkg-preview-signing-secret-32b!",
        "cors_origins": ("http://testserver",),
        "verification_execution_mode": "external",
    }
    values.update(updates)
    return Settings(**values)


def _bootstrap_work_order(client: TestClient) -> dict[str, Any]:
    project = client.post(
        "/api/v1/projects",
        json={"code": "P213-WO", "name": "transition-tests", "location": "synthetic"},
    ).json()
    imported = client.post(
        f"/api/v1/projects/{project['id']}/design-packages/import-json",
        files={
            "file": (
                "synthetic-pipe-route-package.json",
                io.BytesIO(PACKAGE_PATH.read_bytes()),
                "application/json",
            )
        },
    ).json()
    eng_id = imported["objects"][0]["id"]
    wo = client.post(
        f"/api/v1/projects/{project['id']}/work-orders",
        json={
            "engineering_object_id": eng_id,
            "work_order_code": "PIPE-P213-1",
            "spatial_tolerance_m": 80.0,
            "gps_accuracy_threshold_m": 30.0,
        },
    ).json()
    assert wo["status"] == "draft"
    assigned = client.post(
        f"/api/v1/work-orders/{wo['id']}/assign",
        json={"assigned_to": "worker-p213"},
    ).json()
    assert assigned["status"] == "assigned"
    lon, lat = _near_lon_lat()
    upload = client.post(
        f"/api/v1/work-orders/{wo['id']}/verifications",
        data={
            "analyzer": "demo_fixture",
            "latitude": str(lat),
            "longitude": str(lon),
            "accuracy_m": "8.0",
            "location_source": "synthetic_demo",
            "is_synthetic_location": "true",
            "metadata": "{}",
        },
        files={"file": ("p213.png", io.BytesIO(_tiny_png()), "image/png")},
    )
    assert upload.status_code == 202, upload.text
    body = upload.json()
    return {
        "project_id": project["id"],
        "work_order_id": wo["id"],
        "job_id": body["job"]["id"],
        "capture": body["capture"],
    }


# ---------------------------------------------------------------------------
# Unit: apply_analysis_completion_transitions
# ---------------------------------------------------------------------------


class _FakeWO:
    def __init__(self, status: str, wo_id: str = "wo-fake") -> None:
        self.id = wo_id
        self.status = status
        self.updated_at = None


def test_apply_completion_evidence_uploaded_to_needs_review() -> None:
    wo = _FakeWO("evidence_uploaded")
    applied = apply_analysis_completion_transitions(wo, "needs_review")
    assert wo.status == "needs_review"
    assert applied == [
        ("evidence_uploaded", "analyzing"),
        ("analyzing", "needs_review"),
    ]


def test_apply_completion_evidence_uploaded_to_deviation() -> None:
    wo = _FakeWO("evidence_uploaded")
    applied = apply_analysis_completion_transitions(wo, "deviation")
    assert wo.status == "deviation"
    assert applied[-1] == ("analyzing", "deviation")


def test_apply_completion_from_analyzing_to_needs_review() -> None:
    wo = _FakeWO("analyzing")
    applied = apply_analysis_completion_transitions(wo, "needs_review")
    assert wo.status == "needs_review"
    assert applied == [("analyzing", "needs_review")]


def test_apply_completion_rejects_illegal_entry_statuses() -> None:
    for status in ("draft", "assigned", "approved", "closed", "remediating"):
        wo = _FakeWO(status)
        with pytest.raises(WorkOrderTransitionError) as exc_info:
            apply_analysis_completion_transitions(wo, "needs_review")
        err = exc_info.value
        assert err.error_code == WORK_ORDER_TRANSITION_FAILED
        assert err.current_status == status
        assert err.stage == "analysis_completion_entry"
        assert wo.status == status  # unchanged


def test_apply_completion_second_hop_failure_does_not_leave_analyzing_if_rolled_back() -> None:
    """If target hop fails, caller must roll back; helper itself leaves analyzing.

    This unit test documents that the *helper* applies first hop in-memory;
    transactional rollback is the responsibility of the analysis session.
    """
    from app.services import work_orders as wo_mod

    wo = _FakeWO("evidence_uploaded")
    real = wo_mod.transition_work_order

    def flaky(work_order: Any, new_status: str) -> Any:
        if new_status == "needs_review":
            raise wo_mod.WorkOrderError("simulated target failure")
        return real(work_order, new_status)

    original = wo_mod.transition_work_order
    wo_mod.transition_work_order = flaky  # type: ignore[assignment]
    try:
        with pytest.raises(WorkOrderTransitionError) as exc_info:
            apply_analysis_completion_transitions(wo, "needs_review")
        assert exc_info.value.stage == "analysis_completion_to_target"
        assert exc_info.value.error_code == WORK_ORDER_TRANSITION_FAILED
        # In-memory first hop applied — session rollback is required for atomicity.
        assert wo.status == "analyzing"
    finally:
        wo_mod.transition_work_order = original  # type: ignore[assignment]


def test_map_compliance_never_emits_approved_from_engine() -> None:
    assert map_compliance_to_work_order_status("compliant") == "needs_review"
    assert map_compliance_to_work_order_status("deviation_detected") == "deviation"
    assert map_compliance_to_work_order_status("insufficient_evidence") == "needs_review"


# ---------------------------------------------------------------------------
# Integration: atomic success / failure via external execution mode
# ---------------------------------------------------------------------------


def test_normal_path_analysis_completion_and_audits(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    with TestClient(create_app(settings)) as client:
        client.headers.update({"X-API-Key": "test-operator-key"})
        ctx = _bootstrap_work_order(client)
        app = client.app
        assert analysis.run_verification_job(app, ctx["job_id"], "worker-p213") is True

        db = app.state.database.session_factory()
        try:
            wo = db.get(WorkOrder, ctx["work_order_id"])
            job = db.get(VerificationJob, ctx["job_id"])
            assert wo is not None and job is not None
            assert wo.status in {"needs_review", "deviation"}
            assert job.status == "needs_review"
            evaluation = db.scalar(
                select(ComplianceEvaluation).where(
                    ComplianceEvaluation.job_id == ctx["job_id"]
                )
            )
            assert evaluation is not None
            assert evaluation.verdict in {
                "compliant",
                "deviation_detected",
                "insufficient_evidence",
                "needs_review",
            }
            actions = set(
                db.scalars(
                    select(AuditEvent.action).where(
                        AuditEvent.entity_id.in_([ctx["work_order_id"], ctx["job_id"]])
                    )
                ).all()
            )
            assert "analysis_completed" in actions
            assert "rule_evaluation_completed" in actions
            assert "analysis_observations_received" in actions
            assert "work_order_transition_failed" not in actions
            success_outcomes = list(
                db.scalars(
                    select(VerificationAttemptOutcome).where(
                        VerificationAttemptOutcome.disposition == "committed_success"
                    )
                ).all()
            )
            assert len(success_outcomes) == 1
        finally:
            db.close()


@pytest.mark.parametrize("illegal_status", ["draft", "assigned", "approved", "closed"])
def test_illegal_entry_status_fails_atomically(
    tmp_path: Path, illegal_status: str
) -> None:
    settings = _settings(tmp_path)
    with TestClient(create_app(settings)) as client:
        client.headers.update({"X-API-Key": "test-operator-key"})
        ctx = _bootstrap_work_order(client)
        app = client.app

        db = app.state.database.session_factory()
        try:
            wo = db.get(WorkOrder, ctx["work_order_id"])
            assert wo is not None
            assert wo.status == "evidence_uploaded"
            wo.status = illegal_status
            db.commit()
        finally:
            db.close()

        assert analysis.run_verification_job(app, ctx["job_id"], "worker-fail") is True

        db = app.state.database.session_factory()
        try:
            wo = db.get(WorkOrder, ctx["work_order_id"])
            job = db.get(VerificationJob, ctx["job_id"])
            assert wo is not None and job is not None
            assert wo.status == illegal_status
            assert job.status == "failed"
            assert job.error_message
            evaluation = db.scalar(
                select(ComplianceEvaluation).where(
                    ComplianceEvaluation.job_id == ctx["job_id"]
                )
            )
            assert evaluation is None
            outcomes = list(
                db.scalars(
                    select(VerificationAttemptOutcome).where(
                        VerificationAttemptOutcome.error_code.is_not(None)
                    )
                ).all()
            )
            assert any(o.error_code == WORK_ORDER_TRANSITION_FAILED for o in outcomes)
            assert not any(o.disposition == "committed_success" for o in outcomes)
            actions = list(
                db.scalars(
                    select(AuditEvent.action).where(
                        AuditEvent.entity_id == ctx["work_order_id"]
                    )
                ).all()
            )
            assert "work_order_transition_failed" in actions
            assert "rule_evaluation_completed" not in actions
            assert "analysis_observations_received" not in actions
            job_actions = list(
                db.scalars(
                    select(AuditEvent.action).where(
                        AuditEvent.entity_id == ctx["job_id"]
                    )
                ).all()
            )
            assert "analysis_failed" in job_actions
            assert "analysis_completed" not in job_actions
            # lease last_error_code
            from app.models import VerificationJobLease

            lease = db.get(VerificationJobLease, ctx["job_id"])
            assert lease is not None
            assert lease.last_error_code == WORK_ORDER_TRANSITION_FAILED
            assert lease.last_error_retryable is False
        finally:
            db.close()


def test_second_hop_failure_rolls_back_first_hop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(tmp_path)
    with TestClient(create_app(settings)) as client:
        client.headers.update({"X-API-Key": "test-operator-key"})
        ctx = _bootstrap_work_order(client)
        app = client.app

        from app.services import work_orders as wo_mod

        real = wo_mod.transition_work_order

        def flaky(work_order: Any, new_status: str) -> Any:
            if new_status in {"needs_review", "deviation", "approved"}:
                raise wo_mod.WorkOrderError("simulated second-hop failure")
            return real(work_order, new_status)

        monkeypatch.setattr(wo_mod, "transition_work_order", flaky)

        assert analysis.run_verification_job(app, ctx["job_id"], "worker-hop") is True

        db = app.state.database.session_factory()
        try:
            wo = db.get(WorkOrder, ctx["work_order_id"])
            job = db.get(VerificationJob, ctx["job_id"])
            assert wo is not None and job is not None
            # First hop must not survive commit — still evidence_uploaded.
            assert wo.status == "evidence_uploaded"
            assert job.status == "failed"
            assert db.scalar(
                select(ComplianceEvaluation).where(
                    ComplianceEvaluation.job_id == ctx["job_id"]
                )
            ) is None
            outcomes = list(db.scalars(select(VerificationAttemptOutcome)).all())
            assert any(o.error_code == WORK_ORDER_TRANSITION_FAILED for o in outcomes)
            assert not any(o.disposition == "committed_success" for o in outcomes)
        finally:
            db.close()


def test_missing_work_order_fails_completion(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    with TestClient(create_app(settings)) as client:
        client.headers.update({"X-API-Key": "test-operator-key"})
        ctx = _bootstrap_work_order(client)
        app = client.app

        claim = analysis.claim_verification_job(app, ctx["job_id"], "worker-missing")
        assert claim is not None
        result = {
            "schema_version": "1.0",
            "analysis_mode": "demo",
            "evidence_grade": False,
            "analyzer": {"name": "demo_fixture", "version": "test"},
            "provenance": {"kind": "test", "synthetic": True, "warning": None},
            "input": {"evidence_sha256": "0" * 64, "baseline_sha256": "1" * 64},
            "observations": {"measurements": {}, "objects": [], "events": []},
            "alignment": {
                "status": "not_evaluated",
                "baseline_version": "v1",
                "differences": [],
            },
            "findings": [],
            "confidence": None,
            "recommended_action": "manual_review",
            "accuracy_claim": None,
        }
        payload = {
            "project_id": ctx["project_id"],
            "work_order_id": "00000000-0000-0000-0000-000000000099",
            "verdict": "compliant",
            "rule_version": "workorder-rules-v0.1",
            "engine_version": "compliance-v0",
            "expected": {},
            "observed": {},
            "differences": [],
            "note": "",
            "spatial_check_status": "passed",
        }
        with pytest.raises(WorkOrderIntegrityError) as exc_info:
            analysis._complete_verification_job(
                app, claim, result, compliance_payload=payload
            )
        assert exc_info.value.error_code == WORK_ORDER_MISSING

        # Outer fail path
        assert analysis._fail_verification_job(app, claim, exc_info.value) is True

        db = app.state.database.session_factory()
        try:
            job = db.get(VerificationJob, ctx["job_id"])
            assert job is not None
            assert job.status == "failed"
            outcomes = list(db.scalars(select(VerificationAttemptOutcome)).all())
            assert any(o.error_code == WORK_ORDER_MISSING for o in outcomes)
            assert not any(o.disposition == "committed_success" for o in outcomes)
            assert db.scalar(
                select(ComplianceEvaluation).where(
                    ComplianceEvaluation.job_id == ctx["job_id"]
                )
            ) is None
            actions = set(db.scalars(select(AuditEvent.action)).all())
            assert "work_order_transition_failed" in actions
            assert "analysis_completed" not in actions
            assert "rule_evaluation_completed" not in actions
        finally:
            db.close()


def test_ai_observations_only_verdict_from_server_engine() -> None:
    """Adapter cannot dictate final WO status mapping; engine verdict is sole input."""
    assert map_compliance_to_work_order_status("approved") == "needs_review"  # unknown → review
    # Engine verdict "compliant" never becomes WO "approved"
    assert map_compliance_to_work_order_status("compliant") != "approved"


def test_no_silent_except_pass_in_analysis_completion_source() -> None:
    source = Path(analysis.__file__).read_text(encoding="utf-8")
    # Guard against regression of the specific silent swallow pattern around transitions.
    assert "except Exception:\n                        pass" not in source
    assert "except Exception:\n                            pass" not in source
    assert "apply_analysis_completion_transitions" in source
