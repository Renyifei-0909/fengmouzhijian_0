from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import FileResponse, Response, StreamingResponse
from starlette.background import BackgroundTask
from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import __version__
from ..auth import AnyPrincipal, AuditorPrincipal, OperatorPrincipal, OperatorReviewerPrincipal, ReviewerPrincipal
from ..config import Settings
from ..dependencies import get_db, get_settings, get_storage
from ..models import (
    AuditEvent,
    DesignBaseline,
    EvidenceAsset,
    FindingCase,
    FindingCaseCommand,
    HumanReview,
    Project,
    ProofRecord,
    RemediationAttempt,
    SealOperation,
    SensorEvent,
    StructuredReport,
    VerificationAttempt,
    VerificationAttemptOutcome,
    VerificationJob,
    VerificationJobLease,
    new_id,
)
from .work_order_routes import router as work_order_router
from ..schemas import (
    BaselineCreate,
    BaselineRead,
    CapabilityMeta,
    EvidenceContentError,
    EvidenceRead,
    FindingCaseDetail,
    FindingCaseRead,
    FindingCaseSummary,
    FindingRemediationStartRequest,
    FindingTriageRequest,
    IntegrityCheck,
    ProjectCreate,
    ProjectOverview,
    ProjectProgress,
    ProjectRead,
    ProofRead,
    ReportRead,
    RemediationAttemptCreate,
    RemediationAttemptRead,
    ReviewOutcome,
    ReviewRequest,
    SensorEventCreate,
    SensorEventRead,
    VerificationDetail,
    VerificationDispatch,
    VerificationAttemptOutcomeRead,
    VerificationAttemptRead,
    VerificationOperationsSnapshot,
    VerificationRead,
    VerificationRecovery,
)
from ..services.analysis import (
    add_audit,
    ensure_verification_job_lease,
    run_verification_job,
    scan_verification_attempt_integrity,
    scan_verification_dispatch_integrity,
)
from ..services.analyzers import ANALYZER_DESCRIPTORS, analyzer_descriptor
from ..services.analyzers.contracts import delivery_classification
from ..services.media_probe import probe_media
from ..services.metrics import PROMETHEUS_CONTENT_TYPE, render_verification_prometheus
from ..services.observability import verification_operations_snapshot
from ..services.proof import verify_proof_archive
from ..services.reporting import render_final_report
from ..services.remediation import (
    RemediationConflictError,
    RemediationIntegrityError,
    RemediationValidationError,
    bind_attempt_to_verification,
    create_remediation_attempt,
    dismiss_cases_for_rejected_job,
    finding_case_summary,
    prepare_remediation_review,
    scan_remediation_integrity,
    start_remediation,
    triage_case,
    validate_attempt_for_verification,
    validate_case_integrity,
)
from ..services.sealing import (
    RESUMABLE_SEAL_STATES,
    SealBusyError,
    SealIntegrityError,
    resume_seal_operation,
    scan_sealing_integrity,
)
from ..services.storage import (
    FileStorage,
    StoredFileIntegrityError,
    StoredFileMissingError,
    ValidatedStoredFile,
    canonical_json_bytes,
    design_baseline_sha256,
    sha256_bytes,
    sha256_file,
)


router = APIRouter()
router.include_router(work_order_router)
Db = Annotated[Session, Depends(get_db)]
Storage = Annotated[FileStorage, Depends(get_storage)]
AppSettings = Annotated[Settings, Depends(get_settings)]


EVIDENCE_BINARY_CONTENT = {
    media_type: {"schema": {"type": "string", "format": "binary"}}
    for media_type in (
        "video/mp4",
        "video/quicktime",
        "video/x-msvideo",
        "video/x-matroska",
        "video/webm",
        "image/jpeg",
        "image/png",
    )
}
EVIDENCE_CONTENT_HEADERS = {
    "Accept-Ranges": {"schema": {"type": "string"}, "description": "Always `bytes`."},
    "Content-Length": {"schema": {"type": "integer"}},
    "ETag": {"schema": {"type": "string"}, "description": "Quoted ingestion SHA-256."},
}
SINGLE_BYTE_RANGE_PATTERN = re.compile(r"bytes=(\d{0,20})-(\d{0,20})", re.IGNORECASE)
EVIDENCE_ERROR_HEADERS = {
    "Cache-Control": "private, no-store, max-age=0",
    "Pragma": "no-cache",
    "X-Content-Type-Options": "nosniff",
}
OPERATIONS_METRICS_HEADERS = {
    "Cache-Control": "private, no-store, max-age=0",
    "Pragma": "no-cache",
    "X-Content-Type-Options": "nosniff",
}


def _get_or_404(db: Session, model: Any, object_id: str, label: str) -> Any:
    item = db.get(model, object_id)
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"{label} not found")
    return item


def _raise_remediation_http(exc: Exception) -> None:
    if isinstance(exc, RemediationValidationError):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
    if isinstance(exc, RemediationIntegrityError):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Finding/remediation records failed integrity validation",
        ) from exc
    raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


def _verification_recovery(
    job: VerificationJob,
    operation: SealOperation | None,
    settings: Settings,
    failure_retryable: bool | None = None,
    dead_lettered: bool = False,
) -> VerificationRecovery:
    if job.status == "failed":
        try:
            descriptor = analyzer_descriptor(job.analyzer_name, settings=settings)
            enabled = bool(descriptor["enabled"])
            version_matches = str(descriptor["version"]) == job.analyzer_version
        except (KeyError, TypeError, ValueError):
            enabled = False
            version_matches = False
        if dead_lettered:
            reason = "The verification retry budget is exhausted; manual diagnosis and a new job are required."
        elif not enabled:
            reason = "The persisted analyzer is disabled or unknown; this job cannot be retried."
        elif not version_matches:
            reason = "The persisted analyzer version changed; this job cannot be retried in place."
        elif failure_retryable is not True:
            reason = "The persisted failure is non-retryable or has no trusted retry classification."
        else:
            reason = "Analysis failed before review; an operator may explicitly retry the same job."
        return VerificationRecovery(
            action="retry_analysis",
            retryable=(
                not dead_lettered and enabled and version_matches and failure_retryable is True
            ),
            reason=reason,
        )
    if job.status == "sealing":
        if operation is None:
            return VerificationRecovery(
                action="integrity_review",
                retryable=False,
                reason="The job is sealing but has no durable seal operation.",
            )
        if operation.state not in RESUMABLE_SEAL_STATES:
            if operation.state == "manual_attention":
                reason = "The seal failed integrity validation and requires manual review."
            elif operation.state == "completed":
                reason = "The seal is completed but the job is not approved; the database graph is inconsistent."
            else:
                reason = "The seal operation has an unknown persisted state and cannot be resumed automatically."
            return VerificationRecovery(
                action="integrity_review",
                retryable=False,
                reason=reason,
                operation_state=operation.state,
                attempt_count=operation.attempt_count,
                last_error=operation.last_error,
                updated_at=operation.updated_at,
            )
        return VerificationRecovery(
            action="resume_sealing",
            retryable=True,
            reason="Published output is not yet approved; a reviewer may resume the durable seal operation.",
            operation_state=operation.state,
            attempt_count=operation.attempt_count,
            last_error=operation.last_error,
            updated_at=operation.updated_at,
        )
    if job.status == "approved" and (
        operation is None or operation.state != "completed" or operation.last_error is not None
    ):
        return VerificationRecovery(
            action="integrity_review",
            retryable=False,
            reason="The approved job has no matching completed seal operation.",
            operation_state=operation.state if operation else None,
            attempt_count=operation.attempt_count if operation else 0,
            last_error=operation.last_error if operation else None,
            updated_at=operation.updated_at if operation else None,
        )
    return VerificationRecovery(
        action="none",
        retryable=False,
        reason="No explicit recovery action is available for the current persisted state.",
        operation_state=operation.state if operation else None,
        attempt_count=operation.attempt_count if operation else 0,
        last_error=operation.last_error if operation else None,
        updated_at=operation.updated_at if operation else None,
    )


def _verification_dispatch(
    job: VerificationJob,
    lease: VerificationJobLease | None,
    settings: Settings,
) -> VerificationDispatch:
    if lease is None:
        return VerificationDispatch(
            execution_mode=settings.verification_execution_mode,
            state="unclaimed" if job.status in {"queued", "running"} else "released",
            generation=0,
            attempt_count=0,
            max_attempts=settings.verification_max_attempts,
        )
    if lease.dead_lettered_at is not None:
        state = "dead_letter"
    elif lease.owner_id is not None:
        state = "leased"
    elif lease.generation > 0:
        state = "released"
    else:
        state = "unclaimed"
    return VerificationDispatch(
        execution_mode=settings.verification_execution_mode,
        state=state,
        generation=lease.generation,
        attempt_count=lease.attempt_count,
        max_attempts=settings.verification_max_attempts,
        heartbeat_at=_utc_timestamp(lease.heartbeat_at),
        lease_expires_at=_utc_timestamp(lease.lease_expires_at),
    )


def _utc_timestamp(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _verification_attempt_history(
    db: Session,
    job_id: str,
) -> list[VerificationAttemptRead]:
    rows = db.execute(
        select(VerificationAttempt, VerificationAttemptOutcome)
        .outerjoin(
            VerificationAttemptOutcome,
            VerificationAttemptOutcome.attempt_id == VerificationAttempt.id,
        )
        .where(VerificationAttempt.job_id == job_id)
        .order_by(VerificationAttempt.attempt_no)
    ).all()
    history: list[VerificationAttemptRead] = []
    for attempt, outcome in rows:
        outcome_read = (
            VerificationAttemptOutcomeRead(
                id=outcome.id,
                attempt_id=outcome.attempt_id,
                disposition=outcome.disposition,
                stage=outcome.stage,
                result_sha256=outcome.result_sha256,
                error_code=outcome.error_code,
                error_retryable=outcome.error_retryable,
                error_message=outcome.error_message,
                upstream_status=outcome.upstream_status,
                dead_lettered=outcome.dead_lettered,
                finished_at=_utc_timestamp(outcome.finished_at),
            )
            if outcome is not None
            else None
        )
        history.append(
            VerificationAttemptRead(
                id=attempt.id,
                job_id=attempt.job_id,
                generation=attempt.generation,
                attempt_no=attempt.attempt_no,
                worker_ref=(
                    "sha256:"
                    + hashlib.sha256(attempt.worker_id.encode("utf-8")).hexdigest()
                ),
                execution_mode=attempt.execution_mode,
                analyzer_name=attempt.analyzer_name,
                analyzer_version=attempt.analyzer_version,
                evidence_sha256=attempt.evidence_sha256,
                baseline_sha256=attempt.baseline_sha256,
                max_attempts=attempt.max_attempts,
                claimed_at=_utc_timestamp(attempt.claimed_at),
                outcome=outcome_read,
            )
        )
    return history


def _latest_analysis_failure_retryable(db: Session, job_id: str) -> bool | None:
    event = db.scalar(
        select(AuditEvent)
        .where(
            AuditEvent.entity_type == "verification_job",
            AuditEvent.entity_id == job_id,
            AuditEvent.action == "analysis_failed",
        )
        .order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc())
    )
    if event is None or not isinstance(event.payload_json, dict):
        return None
    retryable = event.payload_json.get("retryable")
    return retryable if isinstance(retryable, bool) else None


def _validated_evidence_file_or_http(storage: FileStorage, evidence: EvidenceAsset) -> ValidatedStoredFile:
    try:
        return storage.validate_evidence_file(
            storage_path=evidence.storage_path,
            stored_name=evidence.stored_name,
            expected_content_type=evidence.content_type,
            expected_size=evidence.size_bytes,
            expected_sha256=evidence.sha256,
        )
    except StoredFileMissingError as exc:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="Original evidence file is unavailable",
            headers=EVIDENCE_ERROR_HEADERS,
        ) from exc
    except StoredFileIntegrityError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Original evidence failed integrity validation",
            headers=EVIDENCE_ERROR_HEADERS,
        ) from exc


def _parse_single_byte_range(request: Request, file_size: int) -> tuple[int, int] | None:
    range_header = request.headers.get("range")
    if range_header is None:
        return None
    range_error_headers = {**EVIDENCE_ERROR_HEADERS, "Content-Range": f"bytes */{file_size}"}
    if len(range_header) > 128:
        raise HTTPException(
            status_code=status.HTTP_416_RANGE_NOT_SATISFIABLE,
            detail="Requested byte range is not satisfiable",
            headers=range_error_headers,
        )
    match = SINGLE_BYTE_RANGE_PATTERN.fullmatch(range_header)
    if match is None:
        raise HTTPException(
            status_code=status.HTTP_416_RANGE_NOT_SATISFIABLE,
            detail="Only one well-formed bytes range is supported",
            headers=range_error_headers,
        )
    start_text, end_text = match.groups()
    if not start_text and not end_text:
        byte_range = None
    elif not start_text:
        suffix_length = int(end_text)
        byte_range = (max(file_size - suffix_length, 0), file_size - 1) if suffix_length > 0 else None
    else:
        start = int(start_text)
        requested_end = int(end_text) if end_text else file_size - 1
        byte_range = (start, min(requested_end, file_size - 1)) if start < file_size and requested_end >= start else None
    if byte_range is None:
        raise HTTPException(
            status_code=status.HTTP_416_RANGE_NOT_SATISFIABLE,
            detail="Requested byte range is not satisfiable",
            headers=range_error_headers,
        )
    return byte_range


def _iter_validated_evidence(
    validated: ValidatedStoredFile,
    *,
    start: int,
    end: int,
    chunk_size: int = 64 * 1024,
) -> Iterator[bytes]:
    try:
        os.lseek(validated.fileno(), start, os.SEEK_SET)
        remaining = end - start + 1
        while remaining > 0:
            chunk = os.read(validated.fileno(), min(chunk_size, remaining))
            if not chunk:
                raise RuntimeError("Validated evidence descriptor ended unexpectedly")
            remaining -= len(chunk)
            yield chunk
    finally:
        validated.close()


def _build_project_progress(db: Session, project_id: str) -> ProjectProgress:
    baseline_count = db.scalar(
        select(func.count()).select_from(DesignBaseline).where(DesignBaseline.project_id == project_id)
    ) or 0
    approved_ids = set(
        db.scalars(
            select(VerificationJob.baseline_id).where(
                VerificationJob.project_id == project_id,
                VerificationJob.status == "approved",
            )
        ).all()
    )
    pending_review_count = db.scalar(
        select(func.count()).select_from(VerificationJob).where(
            VerificationJob.project_id == project_id,
            VerificationJob.status.in_(["queued", "running", "needs_review"]),
        )
    ) or 0
    failed_or_rejected_count = db.scalar(
        select(func.count()).select_from(VerificationJob).where(
            VerificationJob.project_id == project_id,
            VerificationJob.status.in_(["failed", "rejected"]),
        )
    ) or 0
    return ProjectProgress(
        project_id=project_id,
        baseline_count=baseline_count,
        approved_baseline_count=len(approved_ids),
        pending_review_count=pending_review_count,
        failed_or_rejected_count=failed_or_rejected_count,
        completion_rate=round((len(approved_ids) / baseline_count * 100) if baseline_count else 0.0, 2),
        metric_note=(
            "MVP proxy: approved unique design baselines divided by registered baselines; "
            "not a full construction schedule metric."
        ),
    )


@router.get("/healthz", tags=["system"])
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/readyz", tags=["system"])
def readyz(request: Request, db: Db, storage: Storage) -> dict[str, str]:
    db.execute(select(1)).scalar_one()
    issues = (
        scan_sealing_integrity(db, storage)
        + scan_remediation_integrity(db, storage)
        + scan_verification_dispatch_integrity(db)
        + scan_verification_attempt_integrity(db)
    )
    request.app.state.sealing_integrity_issues = issues
    if issues:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"status": "integrity_incident", "issue_count": len(issues)},
        )
    return {"status": "ready"}


@router.get("/meta", response_model=CapabilityMeta, tags=["system"])
def capability_meta(request: Request, settings: AppSettings) -> CapabilityMeta:
    adapters = {name: analyzer_descriptor(name, settings=settings) for name in ANALYZER_DESCRIPTORS}
    schema_status = request.app.state.database.schema_status
    return CapabilityMeta(
        service_version=__version__,
        implemented=[
            "project and design baseline registration",
            "video/image upload with server-side SHA-256",
            "authenticated original-evidence reads with fd-bound integrity validation and single byte ranges",
            "video container validation and metadata extraction through ffprobe",
            "persisted verification job state",
            "fenced verification worker leases with heartbeat, expiry recovery and finite retry budget",
            "append-only verification attempt and terminal outcome history",
            "authenticated aggregate verification dispatch observability with attention/incident separation",
            "authenticated bounded-cardinality Prometheus verification metrics",
            "project-scoped workspace overview from persisted records",
            "human review gate",
            "recoverable report/proof sealing saga with explicit intermediate state",
            "human-triaged finding cases and proof-bound remediation re-verification",
            "structured JSON and printable HTML reports",
            "portable evidence archive and append-only local hash chain",
            "strict integrity verification with explicit not-found behavior",
            "Alembic revision gate with metadata-drift verification and explicit legacy adoption",
            "QGIS/GeoPackage design-package import (desensitized JSON / restricted GPKG derivative)",
            "EngineeringObject and WorkOrder with frozen design/geometry/rules snapshots",
            "Work-order evidence capture with configurable GPS spatial reasonableness checks",
            "Server-side compliance rule engine separated from analyzer observations",
        ],
        adapters=adapters,
        database_schema=(
            schema_status.as_dict()
            if schema_status is not None
            else {
                "mode": settings.database_schema_mode,
                "expected_heads": [],
                "current_heads": [],
                "managed_by_alembic": False,
                "at_head": False,
                "drift_free": False,
                "legacy_adopted": False,
            }
        ),
        verification_execution={
            "mode": settings.verification_execution_mode,
            "queue": "database_polling",
            "lease_seconds": settings.verification_lease_seconds,
            "heartbeat_seconds": settings.verification_heartbeat_seconds,
            "max_attempts": settings.verification_max_attempts,
            "queue_warning_seconds": settings.verification_queue_warning_seconds,
            "observability_window_seconds": settings.verification_observability_window_seconds,
            "sqlite_external_scope": "local single-worker development/demo only",
        },
        truth_boundary=[
            "No competition accuracy metric has been measured by this MVP.",
            "stub makes no physical claims; demo_fixture is synthetic and disabled by default.",
            "remote_http is disabled by default and cannot create accuracy claims or evidence_grade=true.",
            "The local hash chain is tamper-evident but is not a blockchain or trusted timestamp service.",
            "ffprobe is required for video uploads; image uploads do not require it.",
            "SQLite external-worker mode is a local single-worker demonstration, not a production concurrency claim.",
            (
                "PostgreSQL schema support does not by itself establish deployment "
                "readiness; a live target still requires operational validation."
            ),
            (
                "The dispatch observability endpoint is a database snapshot, not an "
                "uptime SLA or external monitoring system."
            ),
            (
                "The Prometheus endpoint exports scrape-time gauges; it does not "
                "provide a monitoring server, alert delivery or an SLA."
            ),
            (
                "Work-order GPS checks are location reasonableness only; they are "
                "not absolute anti-spoofing. synthetic_demo locations must stay labeled."
            ),
            (
                "Compliance verdicts come from the backend rule engine and frozen "
                "design snapshots; analyzer adapters only emit observations and "
                "never create competition accuracy claims."
            ),
            (
                "Design-package samples may be synthetic; raw QGIS personal data "
                "and unauthorized GPKG originals must not enter public repositories."
            ),
        ],
    )


@router.get(
    "/operations/verification-dispatch",
    response_model=VerificationOperationsSnapshot,
    tags=["operations"],
)
def verification_dispatch_operations(
    db: Db,
    settings: AppSettings,
    _principal: AuditorPrincipal,
) -> VerificationOperationsSnapshot:
    return verification_operations_snapshot(db, settings)


@router.get(
    "/operations/verification-dispatch/metrics",
    response_class=Response,
    responses={
        200: {
            "description": (
                "Bounded-cardinality Prometheus text snapshot without task "
                "or worker identifiers."
            ),
            "content": {
                "text/plain": {
                    "schema": {"type": "string"},
                }
            },
        }
    },
    tags=["operations"],
)
def verification_dispatch_metrics(
    db: Db,
    settings: AppSettings,
    _principal: AuditorPrincipal,
) -> Response:
    started_at = perf_counter()
    snapshot = verification_operations_snapshot(db, settings)
    collection_duration_seconds = max(0.0, perf_counter() - started_at)
    return Response(
        content=render_verification_prometheus(
            snapshot,
            collection_duration_seconds=collection_duration_seconds,
        ),
        media_type=PROMETHEUS_CONTENT_TYPE,
        headers=OPERATIONS_METRICS_HEADERS,
    )


@router.get("/dashboard/summary", tags=["dashboard"])
def dashboard_summary(db: Db, storage: Storage, _principal: AnyPrincipal) -> dict[str, Any]:
    job_rows = db.execute(
        select(VerificationJob.status, func.count()).group_by(VerificationJob.status)
    ).all()
    formal_candidates = db.scalars(select(ProofRecord).where(ProofRecord.evidence_grade.is_(True))).all()
    verified_formal_count = sum(1 for proof in formal_candidates if verify_proof_archive(proof, storage)["valid"])
    return {
        "projects": db.scalar(select(func.count()).select_from(Project)) or 0,
        "design_baselines": db.scalar(select(func.count()).select_from(DesignBaseline)) or 0,
        "evidence_assets": db.scalar(select(func.count()).select_from(EvidenceAsset)) or 0,
        "jobs_by_status": {row[0]: row[1] for row in job_rows},
        "reports": db.scalar(select(func.count()).select_from(StructuredReport)) or 0,
        "proof_archives": db.scalar(select(func.count()).select_from(ProofRecord)) or 0,
        "formal_evidence_archives": verified_formal_count,
        "finding_cases": finding_case_summary(db),
        "note": "All counts come from persisted MVP data; no static project/device/alarm totals are injected.",
    }


@router.post("/projects", response_model=ProjectRead, status_code=status.HTTP_201_CREATED, tags=["projects"])
def create_project(payload: ProjectCreate, db: Db, principal: OperatorPrincipal) -> Project:
    project = Project(id=new_id(), **payload.model_dump())
    db.add(project)
    add_audit(db, entity_type="project", entity_id=project.id, action="created", actor=principal.actor)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Project code already exists") from exc
    db.refresh(project)
    return project


@router.get("/projects", response_model=list[ProjectRead], tags=["projects"])
def list_projects(db: Db, _principal: AnyPrincipal) -> list[Project]:
    return list(db.scalars(select(Project).order_by(Project.created_at.desc())).all())


@router.get("/projects/{project_id}", response_model=ProjectRead, tags=["projects"])
def get_project(project_id: str, db: Db, _principal: AnyPrincipal) -> Project:
    return _get_or_404(db, Project, project_id, "Project")


@router.post(
    "/projects/{project_id}/baselines",
    response_model=BaselineRead,
    status_code=status.HTTP_201_CREATED,
    tags=["baselines"],
)
def create_baseline(project_id: str, payload: BaselineCreate, db: Db, principal: OperatorPrincipal) -> DesignBaseline:
    _get_or_404(db, Project, project_id, "Project")
    baseline = DesignBaseline(
        id=new_id(),
        project_id=project_id,
        **payload.model_dump(),
        sha256=design_baseline_sha256(project_id=project_id, **payload.model_dump()),
    )
    db.add(baseline)
    add_audit(db, entity_type="design_baseline", entity_id=baseline.id, action="created", actor=principal.actor)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A baseline with the same project/site/procedure/version already exists",
        ) from exc
    db.refresh(baseline)
    return baseline


@router.get("/projects/{project_id}/baselines", response_model=list[BaselineRead], tags=["baselines"])
def list_baselines(project_id: str, db: Db, _principal: AnyPrincipal) -> list[DesignBaseline]:
    _get_or_404(db, Project, project_id, "Project")
    return list(
        db.scalars(
            select(DesignBaseline)
            .where(DesignBaseline.project_id == project_id)
            .order_by(DesignBaseline.created_at.desc())
        ).all()
    )


@router.post("/sensor-events", response_model=SensorEventRead, status_code=status.HTTP_201_CREATED, tags=["sensors"])
def create_sensor_event(payload: SensorEventCreate, db: Db, principal: OperatorPrincipal) -> SensorEvent:
    _get_or_404(db, Project, payload.project_id, "Project")
    digest_payload = payload.model_dump(mode="json")
    event = SensorEvent(
        id=new_id(),
        project_id=payload.project_id,
        site_id=payload.site_id,
        device_id=payload.device_id,
        kind=payload.kind,
        value=payload.value,
        unit=payload.unit,
        captured_at=payload.captured_at,
        metadata_json=payload.metadata,
        sha256=sha256_bytes(canonical_json_bytes(digest_payload)),
    )
    db.add(event)
    add_audit(db, entity_type="sensor_event", entity_id=event.id, action="ingested", actor=principal.actor)
    db.commit()
    db.refresh(event)
    return event


@router.get("/sensor-events", response_model=list[SensorEventRead], tags=["sensors"])
def list_sensor_events(
    db: Db,
    _principal: AnyPrincipal,
    project_id: str = Query(...),
    site_id: str | None = Query(default=None),
) -> list[SensorEvent]:
    statement = select(SensorEvent).where(SensorEvent.project_id == project_id)
    if site_id:
        statement = statement.where(SensorEvent.site_id == site_id)
    return list(db.scalars(statement.order_by(SensorEvent.captured_at.desc())).all())


@router.get("/finding-cases/summary", response_model=FindingCaseSummary, tags=["remediation"])
def get_finding_case_summary(
    db: Db,
    storage: Storage,
    _principal: AnyPrincipal,
    project_id: str | None = Query(default=None),
) -> dict[str, Any]:
    if project_id:
        _get_or_404(db, Project, project_id, "Project")
    issues = scan_remediation_integrity(db, storage)
    if issues:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"message": "Finding/remediation integrity check failed", "issue_count": len(issues)},
        )
    return finding_case_summary(db, project_id)


@router.get("/finding-cases", response_model=list[FindingCaseRead], tags=["remediation"])
def list_finding_cases(
    db: Db,
    _principal: AnyPrincipal,
    project_id: str | None = Query(default=None),
    case_status: str | None = Query(default=None, alias="status"),
    scope: str | None = Query(default=None),
) -> list[FindingCase]:
    statement = select(FindingCase)
    if project_id:
        _get_or_404(db, Project, project_id, "Project")
        statement = statement.where(FindingCase.project_id == project_id)
    if case_status:
        if case_status not in {
            "pending_triage",
            "open",
            "remediation_in_progress",
            "verification_pending",
            "closed",
            "dismissed",
        }:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Invalid case status")
        statement = statement.where(FindingCase.status == case_status)
    if scope:
        if scope not in {"operational", "demo"}:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Invalid case scope")
        statement = statement.where(FindingCase.scope == scope)
    cases = list(
        db.scalars(statement.order_by(FindingCase.created_at.desc(), FindingCase.id.desc())).all()
    )
    try:
        for case in cases:
            validate_case_integrity(db, case)
    except RemediationIntegrityError as exc:
        _raise_remediation_http(exc)
    return cases


@router.get("/finding-cases/{case_id}", response_model=FindingCaseDetail, tags=["remediation"])
def get_finding_case(
    case_id: str,
    db: Db,
    storage: Storage,
    _principal: AnyPrincipal,
) -> FindingCaseDetail:
    case = _get_or_404(db, FindingCase, case_id, "Finding case")
    try:
        validate_case_integrity(db, case)
    except RemediationIntegrityError as exc:
        _raise_remediation_http(exc)
    attempts = list(
        db.scalars(
            select(RemediationAttempt)
            .where(RemediationAttempt.case_id == case.id)
            .order_by(RemediationAttempt.attempt_no.asc())
        ).all()
    )
    history = list(
        db.scalars(
            select(FindingCaseCommand)
            .where(FindingCaseCommand.case_id == case.id)
            .order_by(FindingCaseCommand.created_at.asc(), FindingCaseCommand.id.asc())
        ).all()
    )
    closure_status = "unsealed"
    if case.closure_proof_id:
        proof = db.get(ProofRecord, case.closure_proof_id)
        closure_status = (
            "sealed" if proof is not None and verify_proof_archive(proof, storage)["valid"] else "invalid"
        )
    return FindingCaseDetail(
        case=case,
        attempts=attempts,
        history=history,
        closure_evidence_status=closure_status,
    )


@router.post("/finding-cases/{case_id}/triage", response_model=FindingCaseRead, tags=["remediation"])
def triage_finding_case(
    case_id: str,
    payload: FindingTriageRequest,
    db: Db,
    principal: ReviewerPrincipal,
) -> FindingCase:
    case = _get_or_404(db, FindingCase, case_id, "Finding case")
    try:
        case = triage_case(
            db,
            case=case,
            request_id=str(payload.request_id),
            expected_version=payload.expected_version,
            decision=payload.decision,
            confirmed_severity=payload.confirmed_severity,
            reason=payload.reason,
            actor=principal.actor,
            actor_role=principal.role,
        )
        db.commit()
        db.refresh(case)
        return case
    except (RemediationConflictError, RemediationIntegrityError, RemediationValidationError) as exc:
        db.rollback()
        _raise_remediation_http(exc)


@router.post("/finding-cases/{case_id}/start-remediation", response_model=FindingCaseRead, tags=["remediation"])
def start_finding_case_remediation(
    case_id: str,
    payload: FindingRemediationStartRequest,
    db: Db,
    principal: OperatorReviewerPrincipal,
) -> FindingCase:
    case = _get_or_404(db, FindingCase, case_id, "Finding case")
    try:
        case = start_remediation(
            db,
            case=case,
            request_id=str(payload.request_id),
            expected_version=payload.expected_version,
            assignee=payload.assignee,
            action_description=payload.action_description,
            due_at=payload.due_at,
            actor=principal.actor,
            actor_role=principal.role,
        )
        db.commit()
        db.refresh(case)
        return case
    except (RemediationConflictError, RemediationIntegrityError, RemediationValidationError) as exc:
        db.rollback()
        _raise_remediation_http(exc)


@router.post(
    "/finding-cases/{case_id}/remediation-attempts",
    response_model=RemediationAttemptRead,
    status_code=status.HTTP_201_CREATED,
    tags=["remediation"],
)
def submit_remediation_attempt(
    case_id: str,
    payload: RemediationAttemptCreate,
    db: Db,
    principal: OperatorReviewerPrincipal,
) -> RemediationAttempt:
    case = _get_or_404(db, FindingCase, case_id, "Finding case")
    try:
        attempt = create_remediation_attempt(
            db,
            case=case,
            client_request_id=str(payload.client_request_id),
            expected_version=payload.expected_version,
            action_description=payload.action_description,
            actor=principal.actor,
            actor_role=principal.role,
        )
        db.commit()
        db.refresh(attempt)
        return attempt
    except (RemediationConflictError, RemediationIntegrityError, RemediationValidationError) as exc:
        db.rollback()
        _raise_remediation_http(exc)


@router.post(
    "/verifications",
    response_model=VerificationRead,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["verification"],
)
async def create_verification(
    request: Request,
    background_tasks: BackgroundTasks,
    db: Db,
    storage: Storage,
    settings: AppSettings,
    principal: OperatorPrincipal,
    project_id: Annotated[str, Form()],
    baseline_id: Annotated[str, Form()],
    file: Annotated[UploadFile, File()],
    analyzer: Annotated[str, Form()] = "stub",
    captured_at: Annotated[datetime | None, Form()] = None,
    device_id: Annotated[str | None, Form()] = None,
    metadata: Annotated[str, Form()] = "{}",
    remediation_attempt_id: Annotated[str | None, Form()] = None,
) -> VerificationJob:
    project = _get_or_404(db, Project, project_id, "Project")
    baseline = _get_or_404(db, DesignBaseline, baseline_id, "Design baseline")
    if baseline.project_id != project.id:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Baseline does not belong to the project")
    remediation_attempt = None
    if remediation_attempt_id:
        remediation_attempt = _get_or_404(
            db,
            RemediationAttempt,
            remediation_attempt_id,
            "Remediation attempt",
        )
        try:
            validate_attempt_for_verification(
                db,
                attempt=remediation_attempt,
                project_id=project_id,
                baseline_id=baseline_id,
            )
        except (RemediationConflictError, RemediationIntegrityError, RemediationValidationError) as exc:
            _raise_remediation_http(exc)
    try:
        descriptor = analyzer_descriptor(analyzer, settings=settings)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
    if not descriptor["enabled"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Analyzer '{analyzer}' is disabled or not fully configured",
        )
    try:
        user_metadata = json.loads(metadata)
        if not isinstance(user_metadata, dict):
            raise ValueError("metadata must be a JSON object")
    except (json.JSONDecodeError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc

    stored = await storage.save_upload(file)
    media_probe = probe_media(stored.path, stored.content_type)
    if stored.content_type.startswith("video/") and media_probe.get("probe_status") != "ok":
        stored.path.unlink(missing_ok=True)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Video container could not be parsed by ffprobe; the upload was rejected",
        )
    media_metadata = {**user_metadata, "media_probe": media_probe}
    evidence = EvidenceAsset(
        id=new_id(),
        project_id=project_id,
        baseline_id=baseline_id,
        original_name=stored.original_name,
        stored_name=stored.stored_name,
        storage_path=str(stored.path),
        content_type=stored.content_type,
        size_bytes=stored.size_bytes,
        sha256=stored.sha256,
        captured_at=captured_at,
        device_id=device_id,
        metadata_json=media_metadata,
    )
    job = VerificationJob(
        id=new_id(),
        project_id=project_id,
        baseline_id=baseline_id,
        evidence_id=evidence.id,
        analyzer_name=analyzer,
        analyzer_version=str(descriptor["version"]),
        status="queued",
        progress=0,
    )
    try:
        db.add_all([evidence, job])
        db.flush()
        db.add(VerificationJobLease(job_id=job.id))
        if remediation_attempt is not None:
            bind_attempt_to_verification(
                db,
                attempt=remediation_attempt,
                job=job,
                actor=principal.actor,
            )
        add_audit(
            db,
            entity_type="evidence_asset",
            entity_id=evidence.id,
            action="uploaded",
            actor=principal.actor,
            payload={"sha256": evidence.sha256, "size_bytes": evidence.size_bytes},
        )
        add_audit(db, entity_type="verification_job", entity_id=job.id, action="queued", actor=principal.actor)
        db.commit()
    except (RemediationConflictError, RemediationIntegrityError, RemediationValidationError) as exc:
        db.rollback()
        stored.path.unlink(missing_ok=True)
        _raise_remediation_http(exc)
    except Exception:
        db.rollback()
        stored.path.unlink(missing_ok=True)
        raise
    db.refresh(job)
    if settings.verification_execution_mode == "inline":
        background_tasks.add_task(run_verification_job, request.app, job.id)
    return job


@router.get(
    "/evidence-assets/{evidence_id}/content",
    response_class=Response,
    tags=["evidence"],
    summary="Securely read original evidence bytes",
    responses={
        200: {
            "description": "Complete original image or video after path, size and SHA-256 validation.",
            "content": EVIDENCE_BINARY_CONTENT,
            "headers": EVIDENCE_CONTENT_HEADERS,
        },
        206: {
            "description": "One satisfiable byte range, suitable for browser video seeking.",
            "content": EVIDENCE_BINARY_CONTENT,
            "headers": {
                **EVIDENCE_CONTENT_HEADERS,
                "Content-Range": {"schema": {"type": "string"}},
            },
        },
        401: {"model": EvidenceContentError, "description": "Missing or invalid X-API-Key."},
        404: {"model": EvidenceContentError, "description": "Evidence record does not exist."},
        409: {
            "model": EvidenceContentError,
            "description": "Path, media metadata, size or SHA-256 conflicts with the ingestion record.",
        },
        410: {"model": EvidenceContentError, "description": "Evidence record exists but its file is missing."},
        416: {
            "model": EvidenceContentError,
            "description": "Malformed, multiple or unsatisfiable byte range.",
            "headers": {"Content-Range": {"schema": {"type": "string"}}},
        },
    },
)
def get_evidence_content(
    evidence_id: str,
    request: Request,
    db: Db,
    storage: Storage,
    _principal: AnyPrincipal,
) -> StreamingResponse:
    evidence = db.get(EvidenceAsset, evidence_id)
    if evidence is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Evidence not found",
            headers=EVIDENCE_ERROR_HEADERS,
        )
    validated = _validated_evidence_file_or_http(storage, evidence)
    file_size = validated.stat_result.st_size
    try:
        byte_range = _parse_single_byte_range(request, file_size)
    except Exception:
        validated.close()
        raise
    start, end = byte_range if byte_range is not None else (0, file_size - 1)
    response_headers = {
        "Accept-Ranges": "bytes",
        "Cache-Control": "private, no-store, max-age=0",
        "Content-Length": str(end - start + 1),
        "ETag": f'"{evidence.sha256}"',
        "Pragma": "no-cache",
        "X-Content-Type-Options": "nosniff",
    }
    response_status = status.HTTP_206_PARTIAL_CONTENT if byte_range is not None else status.HTTP_200_OK
    if byte_range is not None:
        response_headers["Content-Range"] = f"bytes {start}-{end}/{file_size}"
    return StreamingResponse(
        _iter_validated_evidence(validated, start=start, end=end),
        status_code=response_status,
        media_type=validated.content_type,
        headers=response_headers,
        background=BackgroundTask(validated.close),
    )


@router.get("/verifications", response_model=list[VerificationRead], tags=["verification"])
def list_verifications(
    db: Db,
    _principal: AnyPrincipal,
    project_id: str | None = Query(default=None),
    job_status: str | None = Query(default=None, alias="status"),
) -> list[VerificationJob]:
    statement = select(VerificationJob)
    if project_id:
        statement = statement.where(VerificationJob.project_id == project_id)
    if job_status:
        statement = statement.where(VerificationJob.status == job_status)
    return list(db.scalars(statement.order_by(VerificationJob.created_at.desc())).all())


@router.get("/verifications/{job_id}", response_model=VerificationDetail, tags=["verification"])
def get_verification(
    job_id: str,
    db: Db,
    settings: AppSettings,
    _principal: AnyPrincipal,
) -> VerificationDetail:
    job = _get_or_404(db, VerificationJob, job_id, "Verification job")
    evidence = _get_or_404(db, EvidenceAsset, job.evidence_id, "Evidence")
    report = db.scalar(select(StructuredReport).where(StructuredReport.job_id == job.id))
    proof = db.scalar(select(ProofRecord).where(ProofRecord.report_id == report.id)) if report else None
    operation = db.scalar(select(SealOperation).where(SealOperation.job_id == job.id))
    remediation_attempt = db.scalar(
        select(RemediationAttempt).where(RemediationAttempt.verification_job_id == job.id)
    )
    lease = db.get(VerificationJobLease, job.id)
    return VerificationDetail(
        job=job,
        dispatch=_verification_dispatch(job, lease, settings),
        attempts=_verification_attempt_history(db, job.id),
        evidence=evidence,
        report=report,
        proof=proof,
        remediation_attempt=remediation_attempt,
        recovery=_verification_recovery(
            job,
            operation,
            settings,
            failure_retryable=_latest_analysis_failure_retryable(db, job.id),
            dead_lettered=lease is not None and lease.dead_lettered_at is not None,
        ),
    )


@router.post("/verifications/{job_id}/retry", response_model=VerificationRead, tags=["verification"])
def retry_verification(
    request: Request,
    background_tasks: BackgroundTasks,
    job_id: str,
    db: Db,
    settings: AppSettings,
    principal: OperatorPrincipal,
) -> VerificationJob:
    job = _get_or_404(db, VerificationJob, job_id, "Verification job")
    ensure_verification_job_lease(db, job.id)
    db.flush()
    lease = db.get(VerificationJobLease, job.id)
    if job.status != "failed":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Only failed jobs can be retried; current status is '{job.status}'",
        )
    if _latest_analysis_failure_retryable(db, job.id) is not True:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The persisted analysis failure is not classified as safely retryable",
        )
    if lease is None or lease.dead_lettered_at is not None or lease.attempt_count >= settings.verification_max_attempts:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The verification retry budget is exhausted; submit a new job after manual diagnosis",
        )
    try:
        descriptor = analyzer_descriptor(job.analyzer_name, settings=settings)
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The persisted analyzer is unknown or invalid; submit a new job instead",
        ) from exc
    if not descriptor["enabled"]:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="The configured analyzer is disabled")
    if str(descriptor["version"]) != job.analyzer_version:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Analyzer configuration changed since this job was created; submit a new job instead",
        )
    transition = db.execute(
        update(VerificationJob)
        .where(VerificationJob.id == job.id, VerificationJob.status == "failed")
        .values(
            status="queued",
            progress=0,
            result_json=None,
            error_message=None,
            started_at=None,
            completed_at=None,
        )
        .execution_options(synchronize_session=False)
    )
    if transition.rowcount != 1:
        db.rollback()
        current_status = db.scalar(select(VerificationJob.status).where(VerificationJob.id == job.id))
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Retry was not queued because the job state changed concurrently; "
                f"current status is '{current_status or 'unknown'}'"
            ),
        )
    add_audit(db, entity_type="verification_job", entity_id=job.id, action="retry_queued", actor=principal.actor)
    db.commit()
    db.refresh(job)
    if settings.verification_execution_mode == "inline":
        background_tasks.add_task(run_verification_job, request.app, job.id)
    return job


@router.post("/verifications/{job_id}/review", response_model=ReviewOutcome, tags=["verification"])
def review_verification(
    job_id: str,
    payload: ReviewRequest,
    request: Request,
    db: Db,
    storage: Storage,
    principal: ReviewerPrincipal,
) -> ReviewOutcome:
    job = _get_or_404(db, VerificationJob, job_id, "Verification job")

    if payload.decision == "approve" and job.status in {"sealing", "approved"}:
        operation = db.scalar(select(SealOperation).where(SealOperation.job_id == job.id))
        if operation is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Job is '{job.status}' but has no recoverable seal operation",
            )
        if job.status == "approved" and operation.state != "completed":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Approved job has an incomplete seal operation; readiness is degraded",
            )
        try:
            prepare_remediation_review(
                db,
                job=job,
                review_decision=payload.decision,
                resolution_decision=payload.remediation_resolution,
                resolution_note=payload.note,
                actor=principal.actor,
            )
        except (RemediationConflictError, RemediationIntegrityError, RemediationValidationError) as exc:
            db.rollback()
            _raise_remediation_http(exc)
        try:
            job, review, report, proof = resume_seal_operation(
                db,
                storage,
                operation_id=operation.id,
                actor=principal.actor,
            )
        except SealBusyError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        except SealIntegrityError as exc:
            request.app.state.sealing_integrity_issues = scan_sealing_integrity(db, storage)
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Seal operation requires integrity review before it can continue",
            ) from exc
        except Exception as exc:
            request.app.state.sealing_integrity_issues = scan_sealing_integrity(db, storage)
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Seal operation was persisted but did not finish; retry or restart to resume it",
            ) from exc
        request.app.state.sealing_integrity_issues = scan_sealing_integrity(db, storage)
        return ReviewOutcome(job=job, review=review, report=report, proof=proof)

    if job.status != "needs_review":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Job cannot be reviewed from status '{job.status}'",
        )
    if payload.decision == "approve":
        pending_case_count = db.scalar(
            select(func.count())
            .select_from(FindingCase)
            .where(
                FindingCase.source_job_id == job.id,
                FindingCase.status == "pending_triage",
                FindingCase.scope == "operational",
            )
        ) or 0
        if pending_case_count:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "message": "All candidate findings must be triaged before approval",
                    "pending_finding_case_count": pending_case_count,
                },
            )
    evidence = _get_or_404(db, EvidenceAsset, job.evidence_id, "Evidence")
    baseline = _get_or_404(db, DesignBaseline, job.baseline_id, "Design baseline")
    baseline_digest = design_baseline_sha256(
        project_id=baseline.project_id,
        site_id=baseline.site_id,
        procedure_code=baseline.procedure_code,
        version=baseline.version,
        source_type=baseline.source_type,
        expected=baseline.expected,
    )
    integrity_errors = []
    try:
        with storage.validate_evidence_file(
            storage_path=evidence.storage_path,
            stored_name=evidence.stored_name,
            expected_content_type=evidence.content_type,
            expected_size=evidence.size_bytes,
            expected_sha256=evidence.sha256,
        ):
            pass
    except (StoredFileMissingError, StoredFileIntegrityError) as exc:
        integrity_errors.append(f"Original evidence failed secure integrity validation: {exc}")
    if baseline_digest != baseline.sha256:
        integrity_errors.append("Design baseline content changed after registration")
    if integrity_errors:
        add_audit(
            db,
            entity_type="verification_job",
            entity_id=job.id,
            action="seal_blocked_integrity",
            actor=principal.actor,
            payload={"errors": integrity_errors},
        )
        db.commit()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail={"integrity_errors": integrity_errors})
    review_note = f"submitted reviewer label: {payload.reviewer}"
    if payload.note:
        review_note += f"\n{payload.note}"
    target_status = "sealing" if payload.decision == "approve" else "rejected"
    transition = db.execute(
        update(VerificationJob)
        .where(VerificationJob.id == job.id, VerificationJob.status == "needs_review")
        .values(status=target_status, progress=100)
        .execution_options(synchronize_session=False)
    )
    if transition.rowcount != 1:
        db.rollback()
        current_status = db.scalar(select(VerificationJob.status).where(VerificationJob.id == job.id))
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Review was not accepted because job state changed to '{current_status or 'unknown'}'",
        )
    review = HumanReview(
        job_id=job.id,
        decision=payload.decision,
        reviewer=principal.actor,
        note=review_note,
    )
    db.add(review)
    db.flush()
    try:
        prepare_remediation_review(
            db,
            job=job,
            review_decision=payload.decision,
            resolution_decision=payload.remediation_resolution,
            resolution_note=payload.note,
            actor=principal.actor,
        )
        if payload.decision == "reject":
            dismiss_cases_for_rejected_job(
                db,
                job=job,
                actor=principal.actor,
                reason=payload.note or "Source verification was rejected by the reviewer.",
            )
    except (RemediationConflictError, RemediationIntegrityError, RemediationValidationError) as exc:
        db.rollback()
        _raise_remediation_http(exc)
    if payload.decision == "approve":
        operation = SealOperation(
            job_id=job.id,
            review_id=review.id,
            report_id=new_id(),
            archive_id=f"ARC-{new_id()}",
        )
        db.add(operation)
        db.flush()
        report_content, report_status, _, _ = render_final_report(
            db,
            job=job,
            review=review,
            report_id=operation.report_id,
            created_at=operation.created_at,
        )
        _, purpose = delivery_classification(job.result_json)
        operation.report_content_json = report_content
        operation.report_status = report_status
        operation.purpose = purpose
        operation.evidence_grade = bool((job.result_json or {}).get("evidence_grade", False))
        add_audit(
            db,
            entity_type="seal_operation",
            entity_id=operation.id,
            action="seal_requested",
            actor=principal.actor,
            payload={"job_id": job.id, "report_id": operation.report_id, "archive_id": operation.archive_id},
        )
    else:
        add_audit(
            db,
            entity_type="verification_job",
            entity_id=job.id,
            action="rejected",
            actor=principal.actor,
            payload={"note": payload.note},
        )
    db.commit()
    db.refresh(job)
    if payload.decision == "reject":
        db.refresh(job)
        db.refresh(review)
        return ReviewOutcome(job=job, review=review)
    try:
        job, review, report, proof = resume_seal_operation(
            db,
            storage,
            operation_id=operation.id,
            actor=principal.actor,
        )
    except SealBusyError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except SealIntegrityError as exc:
        request.app.state.sealing_integrity_issues = scan_sealing_integrity(db, storage)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Seal operation requires integrity review before it can continue",
        ) from exc
    except Exception as exc:
        request.app.state.sealing_integrity_issues = scan_sealing_integrity(db, storage)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Seal operation was persisted but did not finish; retry or restart to resume it",
        ) from exc
    request.app.state.sealing_integrity_issues = scan_sealing_integrity(db, storage)
    return ReviewOutcome(job=job, review=review, report=report, proof=proof)


@router.get("/projects/{project_id}/progress", response_model=ProjectProgress, tags=["projects"])
def project_progress(project_id: str, db: Db, _principal: AnyPrincipal) -> ProjectProgress:
    _get_or_404(db, Project, project_id, "Project")
    return _build_project_progress(db, project_id)


@router.get("/projects/{project_id}/overview", response_model=ProjectOverview, tags=["projects"])
def project_overview(
    project_id: str,
    db: Db,
    _principal: AnyPrincipal,
    recent_limit: int = Query(default=10, ge=1, le=50),
) -> ProjectOverview:
    """Return one project-scoped, database-backed workspace snapshot.

    Proof records are counted as locally sealed records. Their current byte-level
    validity is intentionally not inferred here; callers must invoke the explicit
    proof verification endpoint when they need a fresh integrity result.
    """

    project = _get_or_404(db, Project, project_id, "Project")
    progress = _build_project_progress(db, project_id)

    job_rows = db.execute(
        select(VerificationJob.status, func.count())
        .where(VerificationJob.project_id == project_id)
        .group_by(VerificationJob.status)
    ).all()
    recent_verifications = list(
        db.scalars(
            select(VerificationJob)
            .where(VerificationJob.project_id == project_id)
            .order_by(VerificationJob.created_at.desc(), VerificationJob.id.desc())
            .limit(recent_limit)
        ).all()
    )
    recent_reports = list(
        db.scalars(
            select(StructuredReport)
            .where(StructuredReport.project_id == project_id)
            .order_by(StructuredReport.created_at.desc(), StructuredReport.id.desc())
            .limit(recent_limit)
        ).all()
    )
    project_proofs = (
        select(ProofRecord)
        .join(StructuredReport, ProofRecord.report_id == StructuredReport.id)
        .where(StructuredReport.project_id == project_id)
    )
    recent_proofs = list(
        db.scalars(
            project_proofs.order_by(ProofRecord.created_at.desc(), ProofRecord.id.desc()).limit(recent_limit)
        ).all()
    )
    return ProjectOverview(
        project=project,
        progress=progress,
        jobs_by_status={row[0]: row[1] for row in job_rows},
        evidence_asset_count=db.scalar(
            select(func.count()).select_from(EvidenceAsset).where(EvidenceAsset.project_id == project_id)
        )
        or 0,
        sensor_event_count=db.scalar(
            select(func.count()).select_from(SensorEvent).where(SensorEvent.project_id == project_id)
        )
        or 0,
        report_count=db.scalar(
            select(func.count()).select_from(StructuredReport).where(StructuredReport.project_id == project_id)
        )
        or 0,
        proof_record_count=db.scalar(select(func.count()).select_from(project_proofs.subquery())) or 0,
        recent_verifications=recent_verifications,
        recent_reports=recent_reports,
        recent_proofs=recent_proofs,
        truth_note=(
            "Counts come from persisted project-scoped records. Proof records are not treated as currently valid "
            "until /proofs/{proof_id}/verify is called; completion_rate remains an MVP baseline proxy."
        ),
    )


@router.get("/reports", response_model=list[ReportRead], tags=["reports"])
def list_reports(db: Db, _principal: AnyPrincipal, project_id: str | None = Query(default=None)) -> list[StructuredReport]:
    statement = select(StructuredReport)
    if project_id:
        statement = statement.where(StructuredReport.project_id == project_id)
    return list(db.scalars(statement.order_by(StructuredReport.created_at.desc())).all())


@router.get("/reports/{report_id}", response_model=ReportRead, tags=["reports"])
def get_report(report_id: str, db: Db, _principal: AnyPrincipal) -> StructuredReport:
    return _get_or_404(db, StructuredReport, report_id, "Report")


@router.get("/reports/{report_id}/download", tags=["reports"])
def download_report(report_id: str, db: Db, _principal: AnyPrincipal, format: str = Query(default="json", pattern="^(json|html)$")) -> FileResponse:
    report = _get_or_404(db, StructuredReport, report_id, "Report")
    path = Path(report.json_path if format == "json" else report.html_path)
    if not path.is_file():
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="Report artifact is missing")
    expected_sha256 = report.sha256 if format == "json" else report.html_sha256
    if sha256_file(path) != expected_sha256:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Report artifact failed its sealed SHA-256 check")
    media_type = "application/json" if format == "json" else "text/html"
    return FileResponse(path, media_type=media_type, filename=path.name)


@router.get("/proofs", response_model=list[ProofRead], tags=["proofs"])
def list_proofs(db: Db, _principal: AnyPrincipal, fingerprint: str | None = Query(default=None)) -> list[ProofRecord]:
    statement = select(ProofRecord)
    if fingerprint:
        statement = statement.where(
            or_(
                ProofRecord.archive_id == fingerprint,
                ProofRecord.archive_sha256 == fingerprint,
                ProofRecord.manifest_sha256 == fingerprint,
                ProofRecord.record_hash == fingerprint,
            )
        )
    return list(db.scalars(statement.order_by(ProofRecord.created_at.desc())).all())


@router.get("/proofs/{proof_id}", response_model=ProofRead, tags=["proofs"])
def get_proof(proof_id: str, db: Db, _principal: AnyPrincipal) -> ProofRecord:
    return _get_or_404(db, ProofRecord, proof_id, "Proof")


@router.get("/proofs/{proof_id}/verify", response_model=IntegrityCheck, tags=["proofs"])
def verify_proof(proof_id: str, db: Db, storage: Storage, _principal: AuditorPrincipal) -> dict[str, Any]:
    proof = _get_or_404(db, ProofRecord, proof_id, "Proof")
    return verify_proof_archive(proof, storage)


@router.get("/proofs/{proof_id}/archive", tags=["proofs"])
def download_proof_archive(
    proof_id: str,
    db: Db,
    storage: Storage,
    _principal: AnyPrincipal,
) -> FileResponse:
    proof = _get_or_404(db, ProofRecord, proof_id, "Proof")
    path = Path(proof.archive_path)
    if not path.is_file():
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="Evidence archive is missing")
    integrity = verify_proof_archive(proof, storage)
    if not integrity["valid"]:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": "Evidence archive failed integrity verification and will not be served",
                "errors": integrity["errors"],
            },
        )
    return FileResponse(path, media_type="application/zip", filename=path.name)


@router.get("/audit-events", tags=["audit"])
def list_audit_events(
    db: Db,
    _principal: AuditorPrincipal,
    entity_type: str | None = Query(default=None),
    entity_id: str | None = Query(default=None),
) -> list[dict[str, Any]]:
    statement = select(AuditEvent)
    if entity_type:
        statement = statement.where(AuditEvent.entity_type == entity_type)
    if entity_id:
        statement = statement.where(AuditEvent.entity_id == entity_id)
    rows = db.scalars(statement.order_by(AuditEvent.created_at.asc())).all()
    return [
        {
            "id": item.id,
            "entity_type": item.entity_type,
            "entity_id": item.entity_id,
            "action": item.action,
            "actor": item.actor,
            "payload": item.payload_json,
            "created_at": item.created_at,
        }
        for item in rows
    ]
