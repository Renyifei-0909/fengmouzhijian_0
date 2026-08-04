"""Work-order lifecycle helpers for the QGIS compliance vertical slice."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from ..models import (
    DesignBaseline,
    EngineeringObject,
    WorkOrder,
    new_id,
    utcnow,
)
from .spatial import DEFAULT_GPS_ACCURACY_THRESHOLD_M, DEFAULT_SPATIAL_TOLERANCE_M
from .storage import design_baseline_sha256

# Allowed transitions for the first vertical slice.
WORK_ORDER_TRANSITIONS: dict[str, frozenset[str]] = {
    "draft": frozenset({"assigned", "closed"}),
    "assigned": frozenset({"evidence_uploaded", "closed"}),
    "evidence_uploaded": frozenset({"analyzing", "closed"}),
    "analyzing": frozenset({"needs_review", "deviation", "approved", "closed"}),
    "needs_review": frozenset({"approved", "deviation", "remediating", "closed"}),
    "approved": frozenset({"closed"}),
    "deviation": frozenset({"remediating", "closed"}),
    "remediating": frozenset({"assigned", "evidence_uploaded", "closed"}),
    "closed": frozenset(),
}

# Canonical audit action names (P2-1). Prefer these over free-form strings.
WORK_ORDER_AUDIT_ACTIONS = {
    "work_order_created",
    "work_order_assigned",
    "evidence_captured",
    "spatial_check_completed",
    "analysis_observations_received",
    "rule_evaluation_completed",
    "work_order_transition_failed",
    "human_review_completed",
    "remediation_started",
    "remediation_evidence_submitted",
    "remediation_closed",
}

# Stable domain error codes (non-retryable unless noted).
WORK_ORDER_TRANSITION_FAILED = "WORK_ORDER_TRANSITION_FAILED"
WORK_ORDER_MISSING = "WORK_ORDER_MISSING"

# Terminal statuses produced by analysis-completion transitions.
ANALYSIS_COMPLETION_TARGET_STATUSES = frozenset({"needs_review", "deviation", "approved"})
ANALYSIS_COMPLETION_ENTRY_STATUSES = frozenset({"evidence_uploaded", "analyzing"})


class WorkOrderError(ValueError):
    """Invalid work-order operation."""


class WorkOrderTransitionError(WorkOrderError):
    """Structured failure when a WorkOrder status transition is rejected.

    Non-retryable domain error. Prefer fields over parsing message text.
    """

    def __init__(
        self,
        message: str,
        *,
        work_order_id: str | None,
        current_status: str | None,
        requested_status: str | None,
        stage: str,
        error_code: str = WORK_ORDER_TRANSITION_FAILED,
    ) -> None:
        super().__init__(message)
        self.work_order_id = work_order_id
        self.current_status = current_status
        self.requested_status = requested_status
        self.stage = stage
        self.error_code = error_code
        self.retryable = False

    def to_audit_payload(self, **extra: Any) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "error_code": self.error_code,
            "stage": self.stage,
            "current_status": self.current_status,
            "requested_status": self.requested_status,
            "work_order_id": self.work_order_id,
        }
        payload.update(extra)
        return payload


class WorkOrderIntegrityError(WorkOrderError):
    """Domain integrity failure (e.g. compliance payload references missing WO)."""

    def __init__(
        self,
        message: str,
        *,
        work_order_id: str | None,
        stage: str = "analysis_completion",
        error_code: str = WORK_ORDER_MISSING,
    ) -> None:
        super().__init__(message)
        self.work_order_id = work_order_id
        self.current_status = None
        self.requested_status = None
        self.stage = stage
        self.error_code = error_code
        self.retryable = False

    def to_audit_payload(self, **extra: Any) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "error_code": self.error_code,
            "stage": self.stage,
            "current_status": self.current_status,
            "requested_status": self.requested_status,
            "work_order_id": self.work_order_id,
        }
        payload.update(extra)
        return payload


def create_work_order(
    db: Session,
    *,
    project_id: str,
    engineering_object: EngineeringObject,
    work_order_code: str,
    procedure_code: str | None = None,
    spatial_tolerance_m: float = DEFAULT_SPATIAL_TOLERANCE_M,
    gps_accuracy_threshold_m: float = DEFAULT_GPS_ACCURACY_THRESHOLD_M,
    assigned_to: str | None = None,
    notes: str | None = None,
    create_baseline: bool = True,
) -> WorkOrder:
    if spatial_tolerance_m <= 0:
        raise WorkOrderError("spatial_tolerance_m must be positive")
    if gps_accuracy_threshold_m <= 0:
        raise WorkOrderError("gps_accuracy_threshold_m must be positive")
    if not work_order_code.strip():
        raise WorkOrderError("work_order_code is required")
    if engineering_object.project_id != project_id:
        raise WorkOrderError("Engineering object does not belong to the project")

    procedure = procedure_code or str(
        (engineering_object.attributes_snapshot_json or {}).get("procedure_code")
        or "TRENCH-BEFORE-BACKFILL"
    )
    design_snapshot = {
        "object_code": engineering_object.object_code,
        "object_type": engineering_object.object_type,
        "design_version": engineering_object.design_version,
        "attributes": engineering_object.attributes_snapshot_json,
        "source_layer": engineering_object.source_layer,
        "source_feature_id": engineering_object.source_feature_id,
        "design_package_id": engineering_object.design_package_id,
    }
    geometry_snapshot = {
        "geometry_type": engineering_object.geometry_type,
        "geometry_wgs84": engineering_object.geometry_wgs84_json,
        "geometry_source_crs_epsg": engineering_object.geometry_source_crs_epsg,
    }
    rules_snapshot = dict(engineering_object.expected_rules_json or {})
    rules_snapshot.setdefault("rule_version", "workorder-rules-v0.1")
    # Freeze spatial policy into rules_snapshot so historical judgment does not
    # depend on later EngineeringObject edits or ambient defaults.
    rules_snapshot["spatial_tolerance_m"] = float(spatial_tolerance_m)
    rules_snapshot["gps_accuracy_threshold_m"] = float(gps_accuracy_threshold_m)
    rules_snapshot["frozen_from"] = "work_order_create"
    rules_snapshot["engineering_object_id"] = engineering_object.id

    baseline_id = None
    if create_baseline:
        expected = {
            "scene_type": engineering_object.object_type,
            "work_order_code": work_order_code,
            "object_code": engineering_object.object_code,
            "source": "work_order_freeze",
            "measurements": {},
            **(rules_snapshot.get("expected") or {}),
        }
        # Keep measurements nested for legacy demo_fixture compatibility.
        meas = expected.get("measurements")
        if not isinstance(meas, dict):
            expected["measurements"] = {}
            meas = expected["measurements"]
        attrs = engineering_object.attributes_snapshot_json or {}
        if "expected_pipe_count" in attrs:
            meas["expected_quantity"] = attrs["expected_pipe_count"]
            expected["visible_pipe_count"] = {"equals": attrs["expected_pipe_count"]}
        if "expected_specification" in attrs:
            meas["expected_specification"] = attrs["expected_specification"]
        baseline = DesignBaseline(
            id=new_id(),
            project_id=project_id,
            site_id=engineering_object.object_code,
            procedure_code=procedure,
            version=f"{engineering_object.design_version}:{work_order_code}",
            source_type="gis",
            expected=expected,
            sha256="",
        )
        baseline.sha256 = design_baseline_sha256(
            project_id=project_id,
            site_id=baseline.site_id,
            procedure_code=baseline.procedure_code,
            version=baseline.version,
            source_type=baseline.source_type,
            expected=baseline.expected,
        )
        db.add(baseline)
        db.flush()
        baseline_id = baseline.id

    # P2-1.2: create always starts as draft. Assignment is a separate server command.
    # ``assigned_to`` on create is ignored for status (kept only as optional pre-fill
    # without emitting work_order_assigned or leaving draft).
    work_order = WorkOrder(
        id=new_id(),
        project_id=project_id,
        engineering_object_id=engineering_object.id,
        baseline_id=baseline_id,
        work_order_code=work_order_code.strip(),
        procedure_code=procedure,
        status="draft",
        design_version=engineering_object.design_version,
        design_snapshot_json=design_snapshot,
        geometry_snapshot_json=geometry_snapshot,
        rules_snapshot_json=rules_snapshot,
        spatial_tolerance_m=float(spatial_tolerance_m),
        gps_accuracy_threshold_m=float(gps_accuracy_threshold_m),
        assigned_to=None,
        notes=notes,
    )
    db.add(work_order)
    db.flush()
    return work_order


def assign_work_order(
    work_order: WorkOrder,
    *,
    assigned_to: str,
) -> WorkOrder:
    """Server command: draft|remediating → assigned (or reassign while assigned).

    Emits no audit itself; caller must write ``work_order_assigned``.
    """
    actor = (assigned_to or "").strip()
    if not actor:
        raise WorkOrderError("assigned_to is required")
    current = work_order.status
    if current == "assigned":
        work_order.assigned_to = actor
        work_order.updated_at = utcnow()
        return work_order
    if current in {"draft", "remediating"}:
        transition_work_order(work_order, "assigned")
        work_order.assigned_to = actor
        work_order.updated_at = utcnow()
        return work_order
    raise WorkOrderError(
        f"Cannot assign work order in status {current!r}; "
        "allowed from draft, assigned (reassign), or remediating"
    )


def transition_work_order(work_order: WorkOrder, new_status: str) -> WorkOrder:
    current = work_order.status
    allowed = WORK_ORDER_TRANSITIONS.get(current, frozenset())
    if new_status == current:
        return work_order
    if new_status not in allowed:
        raise WorkOrderError(
            f"Invalid work order transition {current} → {new_status}"
        )
    work_order.status = new_status
    work_order.updated_at = utcnow()
    return work_order


def transition_work_order_strict(
    work_order: WorkOrder,
    new_status: str,
    *,
    stage: str,
) -> WorkOrder:
    """Like ``transition_work_order`` but raises ``WorkOrderTransitionError``."""
    current = work_order.status
    try:
        return transition_work_order(work_order, new_status)
    except WorkOrderError as exc:
        raise WorkOrderTransitionError(
            f"Work order transition failed at {stage}: {current} → {new_status}",
            work_order_id=work_order.id,
            current_status=current,
            requested_status=new_status,
            stage=stage,
            error_code=WORK_ORDER_TRANSITION_FAILED,
        ) from exc


def apply_analysis_completion_transitions(
    work_order: WorkOrder,
    target_status: str,
) -> list[tuple[str, str]]:
    """Apply analysis-completion status path without silent fallback.

    Allowed entry: ``evidence_uploaded`` or ``analyzing``.
    Path: evidence_uploaded → analyzing → target; or analyzing → target.

    Returns list of (from_status, to_status) applied. Raises structured errors
    on illegal entry or rejected transitions — never falls back to needs_review.
    """
    if target_status not in ANALYSIS_COMPLETION_TARGET_STATUSES:
        raise WorkOrderTransitionError(
            f"Invalid analysis completion target status {target_status!r}",
            work_order_id=work_order.id,
            current_status=work_order.status,
            requested_status=target_status,
            stage="analysis_completion_target",
            error_code=WORK_ORDER_TRANSITION_FAILED,
        )

    applied: list[tuple[str, str]] = []
    current = work_order.status
    if current not in ANALYSIS_COMPLETION_ENTRY_STATUSES:
        raise WorkOrderTransitionError(
            (
                f"Work order status {current!r} is not valid for analysis completion; "
                "expected evidence_uploaded or analyzing"
            ),
            work_order_id=work_order.id,
            current_status=current,
            requested_status="analyzing" if current != "analyzing" else target_status,
            stage="analysis_completion_entry",
            error_code=WORK_ORDER_TRANSITION_FAILED,
        )

    if current == "evidence_uploaded":
        transition_work_order_strict(
            work_order, "analyzing", stage="analysis_completion_to_analyzing"
        )
        applied.append(("evidence_uploaded", "analyzing"))
        current = work_order.status

    if current == "analyzing":
        before = current
        transition_work_order_strict(
            work_order, target_status, stage="analysis_completion_to_target"
        )
        if work_order.status != before:
            applied.append((before, work_order.status))
        return applied

    # Defensive: entry set only has evidence_uploaded|analyzing; should not reach.
    raise WorkOrderTransitionError(
        f"Unexpected analysis completion status {work_order.status!r}",
        work_order_id=work_order.id,
        current_status=work_order.status,
        requested_status=target_status,
        stage="analysis_completion_unexpected",
        error_code=WORK_ORDER_TRANSITION_FAILED,
    )


def map_compliance_to_work_order_status(verdict: str) -> str:
    """Map rule-engine verdict to work-order status before human review seals it.

    A compliant engine verdict still lands in ``needs_review``: humans own the
    final approve/reject seal used by the existing report/proof pipeline.
    Engine never maps to ``approved`` — that remains a human review outcome.
    """
    mapping = {
        "compliant": "needs_review",
        "deviation_detected": "deviation",
        "insufficient_evidence": "needs_review",
        "needs_review": "needs_review",
    }
    return mapping.get(verdict, "needs_review")


def work_order_public_dict(work_order: WorkOrder) -> dict[str, Any]:
    return {
        "id": work_order.id,
        "project_id": work_order.project_id,
        "engineering_object_id": work_order.engineering_object_id,
        "baseline_id": work_order.baseline_id,
        "work_order_code": work_order.work_order_code,
        "procedure_code": work_order.procedure_code,
        "status": work_order.status,
        "design_version": work_order.design_version,
        "design_snapshot": work_order.design_snapshot_json,
        "geometry_snapshot": work_order.geometry_snapshot_json,
        "rules_snapshot": work_order.rules_snapshot_json,
        "spatial_tolerance_m": work_order.spatial_tolerance_m,
        "gps_accuracy_threshold_m": work_order.gps_accuracy_threshold_m,
        "assigned_to": work_order.assigned_to,
        "notes": work_order.notes,
        "created_at": work_order.created_at,
        "updated_at": work_order.updated_at,
    }


def frozen_geometry_wgs84(work_order: WorkOrder) -> dict[str, Any]:
    """Return the geometry used for historical SpatialCheck (never live EO)."""
    geometry = (work_order.geometry_snapshot_json or {}).get("geometry_wgs84")
    if not isinstance(geometry, dict):
        raise WorkOrderError("Work order geometry snapshot is missing")
    return geometry


def frozen_rules_snapshot(work_order: WorkOrder) -> dict[str, Any]:
    """Return the rules used for historical RuleEvaluation (never live EO)."""
    rules = work_order.rules_snapshot_json or {}
    if not isinstance(rules, dict):
        raise WorkOrderError("Work order rules snapshot is missing")
    return rules


def frozen_spatial_policy(work_order: WorkOrder) -> tuple[float, float]:
    """Tolerance and GPS accuracy threshold frozen on the work order.

    Prefer column values; fall back to rules_snapshot for defensive reads.
    """
    rules = work_order.rules_snapshot_json or {}
    tol = work_order.spatial_tolerance_m
    acc = work_order.gps_accuracy_threshold_m
    if tol is None and isinstance(rules, dict):
        tol = rules.get("spatial_tolerance_m")
    if acc is None and isinstance(rules, dict):
        acc = rules.get("gps_accuracy_threshold_m")
    if tol is None or acc is None:
        raise WorkOrderError("Work order spatial policy is incomplete")
    return float(tol), float(acc)


__all__ = [
    "ANALYSIS_COMPLETION_ENTRY_STATUSES",
    "ANALYSIS_COMPLETION_TARGET_STATUSES",
    "WORK_ORDER_AUDIT_ACTIONS",
    "WORK_ORDER_MISSING",
    "WORK_ORDER_TRANSITION_FAILED",
    "WORK_ORDER_TRANSITIONS",
    "WorkOrderError",
    "WorkOrderIntegrityError",
    "WorkOrderTransitionError",
    "apply_analysis_completion_transitions",
    "assign_work_order",
    "create_work_order",
    "frozen_geometry_wgs84",
    "frozen_rules_snapshot",
    "frozen_spatial_policy",
    "map_compliance_to_work_order_status",
    "transition_work_order",
    "transition_work_order_strict",
    "work_order_public_dict",
]
