"""API routes for QGIS work-order compliance vertical slice (Alpha18)."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Annotated, Any

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
    status,
)
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..auth import AnyPrincipal, OperatorPrincipal
from ..config import Settings
from ..dependencies import get_db, get_settings, get_storage
from ..models import (
    ComplianceEvaluation,
    DesignPackage,
    EngineeringObject,
    EvidenceAsset,
    EvidenceCapture,
    Project,
    VerificationJob,
    VerificationJobLease,
    WorkOrder,
    new_id,
    utcnow,
)
from ..schemas import (
    ComplianceEvaluationRead,
    DesignPackageImportResult,
    DesignPackageRead,
    EngineeringObjectRead,
    EvidenceCaptureRead,
    ProjectGisSummary,
    StandardGpkgConfirmRequest,
    StandardGpkgImportResult,
    StandardGpkgPreviewResult,
    WorkOrderAssign,
    WorkOrderCreate,
    WorkOrderRead,
    WorkOrderVerificationRead,
)
from ..services.analysis import add_audit, run_verification_job
from ..services.analyzers import analyzer_descriptor
from ..services.design_package import (
    DesignPackageImportError,
    import_design_package_dict,
    read_upload_with_limit,
)
from ..services.media_probe import probe_media
from ..services.spatial import evaluate_spatial_check
from ..services.storage import FileStorage, sha256_bytes
from ..services.work_orders import (
    WorkOrderError,
    assign_work_order,
    create_work_order,
    frozen_geometry_wgs84,
    frozen_spatial_policy,
    transition_work_order,
)

router = APIRouter(tags=["work-orders"])
Db = Annotated[Session, Depends(get_db)]
Storage = Annotated[FileStorage, Depends(get_storage)]
AppSettings = Annotated[Settings, Depends(get_settings)]

TRUTH_NOTE = (
    "Work-order compliance uses frozen design snapshots and a server rule engine. "
    "Model adapters only emit observations. Synthetic design packages and synthetic_demo "
    "GPS must not be presented as real field data or competition accuracy."
)


def _get_or_404(db: Session, model: Any, object_id: str, label: str) -> Any:
    item = db.get(model, object_id)
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"{label} not found")
    return item


@router.post(
    "/projects/{project_id}/design-packages/import-json",
    response_model=DesignPackageImportResult,
    status_code=status.HTTP_201_CREATED,
)
async def import_design_package_json(
    project_id: str,
    db: Db,
    storage: Storage,
    settings: AppSettings,
    principal: OperatorPrincipal,
    file: Annotated[UploadFile, File()],
) -> DesignPackageImportResult:
    """Import a synthetic JSON design package only.

    ``source_type`` is always ``synthetic_json`` for this endpoint. Controlled
    GPKG derivatives are an offline/library path and are not exposed as a public
    arbitrary-path upload API.
    """
    _get_or_404(db, Project, project_id, "Project")
    dest_path = None
    try:
        raw = await read_upload_with_limit(
            file,
            max_bytes=settings.design_package_max_upload_bytes,
        )
    except DesignPackageImportError as exc:
        detail = str(exc)
        code = (
            status.HTTP_413_CONTENT_TOO_LARGE
            if "exceeds" in detail.casefold() or "too large" in detail.casefold()
            else status.HTTP_422_UNPROCESSABLE_CONTENT
        )
        raise HTTPException(status_code=code, detail=detail) from exc
    if not raw:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Empty package file",
        )
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Invalid JSON design package: {exc}",
        ) from exc
    if not isinstance(payload, dict):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Design package JSON root must be an object",
        )
    if "synthetic" not in payload:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="synthetic field is required and must be a boolean",
        )
    if not isinstance(payload["synthetic"], bool):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="synthetic field must be a boolean",
        )
    if payload["synthetic"] is not True:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                "import-json accepts only synthetic=true packages; "
                "controlled GPKG derivatives are not uploaded through this endpoint"
            ),
        )

    digest = sha256_bytes(raw)
    dest_dir = storage.root / "design-packages" / project_id
    dest_dir.mkdir(parents=True, exist_ok=True)
    filename = file.filename or "design-package.json"
    safe_name = f"{digest[:16]}_{filename.replace('/', '_').replace(chr(92), '_')}"
    dest_path = dest_dir / safe_name
    try:
        dest_path.write_bytes(raw)
        # Server-owned source_type: never derived from client synthetic field tricks.
        package, objects = import_design_package_dict(
            db,
            project_id=project_id,
            payload=payload,
            source_filename=filename,
            source_sha256=digest,
            storage_path=str(dest_path),
            source_type="synthetic_json",
            require_synthetic=True,
        )
        add_audit(
            db,
            entity_type="design_package",
            entity_id=package.id,
            action="imported",
            actor=principal.actor,
            payload={
                "source_sha256": package.source_sha256,
                "object_count": package.object_count,
                "synthetic": package.synthetic,
                "source_type": package.source_type,
                "source_crs_epsg": package.source_crs_epsg,
            },
        )
        db.commit()
        for obj in objects:
            db.refresh(obj)
        db.refresh(package)
    except DesignPackageImportError as exc:
        db.rollback()
        if dest_path is not None:
            dest_path.unlink(missing_ok=True)
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
    except IntegrityError as exc:
        db.rollback()
        if dest_path is not None:
            dest_path.unlink(missing_ok=True)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Engineering object code already exists in this project",
        ) from exc
    except Exception:
        db.rollback()
        if dest_path is not None:
            dest_path.unlink(missing_ok=True)
        raise
    return DesignPackageImportResult(
        package=package,
        objects=objects,
        truth_note=TRUTH_NOTE,
    )


@router.post(
    "/projects/{project_id}/design-packages/standard-gpkg/preview",
    response_model=StandardGpkgPreviewResult,
    status_code=status.HTTP_200_OK,
)
async def preview_standard_gpkg(
    project_id: str,
    db: Db,
    storage: Storage,
    settings: AppSettings,
    principal: OperatorPrincipal,
    file: Annotated[UploadFile, File()],
    package_code: Annotated[str, Form(min_length=2, max_length=100)],
) -> StandardGpkgPreviewResult:
    """Upload bytes to isolated staging and return a preflight/normalize preview.

    Extension and Content-Type are **not** trusted. Preview success does **not**
    mean import completed — confirm is required.
    """
    from ..services.design_package import read_upload_with_limit
    from ..services.gpkg_staging import GpkgStagingError, preview_standard_gpkg_bytes

    _get_or_404(db, Project, project_id, "Project")
    try:
        raw = await read_upload_with_limit(
            file,
            max_bytes=settings.standard_gpkg_max_upload_bytes,
        )
    except DesignPackageImportError as exc:
        detail = str(exc)
        code = (
            status.HTTP_413_CONTENT_TOO_LARGE
            if "exceeds" in detail.casefold() or "too large" in detail.casefold()
            else status.HTTP_422_UNPROCESSABLE_CONTENT
        )
        raise HTTPException(status_code=code, detail=detail) from exc

    try:
        preview = preview_standard_gpkg_bytes(
            storage,
            project_id=project_id,
            package_code=package_code,
            raw=raw,
            actor=principal.actor,
            token_secret=settings.gpkg_preview_signing_secret,
            max_bytes=settings.standard_gpkg_max_upload_bytes,
            ttl_seconds=settings.gpkg_preview_token_ttl_seconds,
        )
    except GpkgStagingError as exc:
        http_status = (
            status.HTTP_413_CONTENT_TOO_LARGE
            if exc.code == "file_too_large"
            else status.HTTP_422_UNPROCESSABLE_CONTENT
        )
        raise HTTPException(
            status_code=http_status,
            detail={"error_code": exc.code, "message": str(exc)},
        ) from exc

    return StandardGpkgPreviewResult(**preview.to_dict())


@router.post(
    "/projects/{project_id}/design-packages/standard-gpkg/confirm",
    response_model=StandardGpkgImportResult,
    status_code=status.HTTP_201_CREATED,
)
def confirm_standard_gpkg(
    project_id: str,
    body: StandardGpkgConfirmRequest,
    db: Db,
    storage: Storage,
    settings: AppSettings,
    principal: OperatorPrincipal,
) -> StandardGpkgImportResult:
    """Confirm a prior preview: private snapshot + digest barrier + transactional import.

    Server forces purpose=controlled and synthetic=true (sample_or_unverified).
    Client cannot override authenticity classification.
    """
    from ..services.gpkg_staging import GpkgStagingError, confirm_standard_gpkg_import

    _get_or_404(db, Project, project_id, "Project")
    try:
        result = confirm_standard_gpkg_import(
            db,
            storage,
            project_id=project_id,
            package_code=body.package_code,
            staging_id=body.staging_id,
            preview_token=body.preview_token,
            actor=principal.actor,
            token_secret=settings.gpkg_preview_signing_secret,
            design_version=body.design_version,
            ttl_seconds=settings.gpkg_preview_token_ttl_seconds,
        )
        db.commit()
        for obj in result.objects:
            db.refresh(obj)
        db.refresh(result.package)
    except GpkgStagingError as exc:
        db.rollback()
        http_status = status.HTTP_422_UNPROCESSABLE_CONTENT
        if exc.code in {
            "preview_token_expired",
            "preview_token_invalid",
            "preview_token_binding_mismatch",
            "preview_token_malformed",
            "staging_not_found",
            "confirm_in_progress",
            "confirm_already_completed",
            "source_sha256_mismatch",
        }:
            http_status = status.HTTP_409_CONFLICT
        raise HTTPException(
            status_code=http_status,
            detail={"error_code": exc.code, "message": str(exc)},
        ) from exc
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error_code": "integrity_conflict",
                "message": "Design package or engineering object conflict",
            },
        ) from None
    except Exception:
        db.rollback()
        raise

    return StandardGpkgImportResult(
        package=result.package,
        objects=result.objects,
        idempotent=result.idempotent,
        source_classification="sample_or_unverified",
        truth_note=(
            "标准 GeoPackage 已按契约写入；格式与导入校验通过不等于数据来源获得授权；"
            "当前分类为样例/未核验来源；AI 不在此路径产生合规结论。"
            + ("（幂等返回既有导入）" if result.idempotent else "")
        ),
    )


@router.get("/projects/{project_id}/design-packages", response_model=list[DesignPackageRead])
def list_design_packages(project_id: str, db: Db, _principal: AnyPrincipal) -> list[DesignPackage]:
    _get_or_404(db, Project, project_id, "Project")
    return list(
        db.scalars(
            select(DesignPackage)
            .where(DesignPackage.project_id == project_id)
            .order_by(DesignPackage.created_at.desc())
        ).all()
    )


@router.get("/design-packages/{package_id}", response_model=DesignPackageRead)
def get_design_package(package_id: str, db: Db, _principal: AnyPrincipal) -> DesignPackage:
    return _get_or_404(db, DesignPackage, package_id, "Design package")


@router.get("/projects/{project_id}/engineering-objects", response_model=list[EngineeringObjectRead])
def list_engineering_objects(
    project_id: str,
    db: Db,
    _principal: AnyPrincipal,
    object_type: str | None = Query(default=None),
) -> list[EngineeringObject]:
    _get_or_404(db, Project, project_id, "Project")
    statement = select(EngineeringObject).where(EngineeringObject.project_id == project_id)
    if object_type:
        statement = statement.where(EngineeringObject.object_type == object_type)
    return list(db.scalars(statement.order_by(EngineeringObject.created_at.desc())).all())


@router.get("/engineering-objects/{object_id}", response_model=EngineeringObjectRead)
def get_engineering_object(object_id: str, db: Db, _principal: AnyPrincipal) -> EngineeringObject:
    return _get_or_404(db, EngineeringObject, object_id, "Engineering object")


@router.post(
    "/projects/{project_id}/work-orders",
    response_model=WorkOrderRead,
    status_code=status.HTTP_201_CREATED,
)
def create_project_work_order(
    project_id: str,
    payload: WorkOrderCreate,
    db: Db,
    principal: OperatorPrincipal,
) -> WorkOrder:
    _get_or_404(db, Project, project_id, "Project")
    eng = _get_or_404(db, EngineeringObject, payload.engineering_object_id, "Engineering object")
    try:
        work_order = create_work_order(
            db,
            project_id=project_id,
            engineering_object=eng,
            work_order_code=payload.work_order_code,
            procedure_code=payload.procedure_code,
            spatial_tolerance_m=payload.spatial_tolerance_m,
            gps_accuracy_threshold_m=payload.gps_accuracy_threshold_m,
            notes=payload.notes,
        )
        add_audit(
            db,
            entity_type="work_order",
            entity_id=work_order.id,
            action="work_order_created",
            actor=principal.actor,
            payload={
                "work_order_code": work_order.work_order_code,
                "engineering_object_id": eng.id,
                "status": work_order.status,
                "spatial_tolerance_m": work_order.spatial_tolerance_m,
                "gps_accuracy_threshold_m": work_order.gps_accuracy_threshold_m,
                "rules_snapshot_has_spatial_policy": (
                    "spatial_tolerance_m" in (work_order.rules_snapshot_json or {})
                    and "gps_accuracy_threshold_m" in (work_order.rules_snapshot_json or {})
                ),
                "assign_required": True,
                "create_assigned_to_ignored": payload.assigned_to is not None,
            },
        )
        db.commit()
        db.refresh(work_order)
        return work_order
    except WorkOrderError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Work order code already exists in this project",
        ) from exc


@router.post(
    "/work-orders/{work_order_id}/assign",
    response_model=WorkOrderRead,
)
def assign_project_work_order(
    work_order_id: str,
    payload: WorkOrderAssign,
    db: Db,
    principal: OperatorPrincipal,
) -> WorkOrder:
    """Server command: draft|remediating → assigned. Status is never client-written."""
    work_order = _get_or_404(db, WorkOrder, work_order_id, "Work order")
    try:
        previous = work_order.status
        assign_work_order(work_order, assigned_to=payload.assigned_to)
        add_audit(
            db,
            entity_type="work_order",
            entity_id=work_order.id,
            action="work_order_assigned",
            actor=principal.actor,
            payload={
                "assigned_to": work_order.assigned_to,
                "from_status": previous,
                "status": work_order.status,
            },
        )
        db.commit()
        db.refresh(work_order)
        return work_order
    except WorkOrderError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.get("/projects/{project_id}/work-orders", response_model=list[WorkOrderRead])
def list_work_orders(
    project_id: str,
    db: Db,
    _principal: AnyPrincipal,
    work_status: str | None = Query(default=None, alias="status"),
) -> list[WorkOrder]:
    _get_or_404(db, Project, project_id, "Project")
    statement = select(WorkOrder).where(WorkOrder.project_id == project_id)
    if work_status:
        statement = statement.where(WorkOrder.status == work_status)
    return list(db.scalars(statement.order_by(WorkOrder.created_at.desc())).all())


@router.get("/work-orders/{work_order_id}", response_model=WorkOrderRead)
def get_work_order(work_order_id: str, db: Db, _principal: AnyPrincipal) -> WorkOrder:
    return _get_or_404(db, WorkOrder, work_order_id, "Work order")


@router.get(
    "/work-orders/{work_order_id}/captures",
    response_model=list[EvidenceCaptureRead],
)
def list_work_order_captures(
    work_order_id: str,
    db: Db,
    _principal: AnyPrincipal,
) -> list[EvidenceCapture]:
    _get_or_404(db, WorkOrder, work_order_id, "Work order")
    return list(
        db.scalars(
            select(EvidenceCapture)
            .where(EvidenceCapture.work_order_id == work_order_id)
            .order_by(EvidenceCapture.created_at.desc())
        ).all()
    )


@router.get(
    "/verifications/{job_id}/compliance",
    response_model=ComplianceEvaluationRead,
)
def get_job_compliance(job_id: str, db: Db, _principal: AnyPrincipal) -> ComplianceEvaluation:
    evaluation = db.scalar(
        select(ComplianceEvaluation).where(ComplianceEvaluation.job_id == job_id)
    )
    if evaluation is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No compliance evaluation for this job (work-order path only)",
        )
    return evaluation


@router.get("/projects/{project_id}/gis-summary", response_model=ProjectGisSummary)
def project_gis_summary(project_id: str, db: Db, _principal: AnyPrincipal) -> ProjectGisSummary:
    _get_or_404(db, Project, project_id, "Project")
    objects = list(
        db.scalars(
            select(EngineeringObject)
            .where(EngineeringObject.project_id == project_id)
            .order_by(EngineeringObject.object_code.asc())
        ).all()
    )
    work_orders = list(
        db.scalars(
            select(WorkOrder)
            .where(WorkOrder.project_id == project_id)
            .order_by(WorkOrder.created_at.desc())
        ).all()
    )
    package_count = db.scalar(
        select(func.count()).select_from(DesignPackage).where(DesignPackage.project_id == project_id)
    ) or 0
    return ProjectGisSummary(
        project_id=project_id,
        design_package_count=package_count,
        engineering_object_count=len(objects),
        work_order_count=len(work_orders),
        objects=objects,
        work_orders=work_orders,
        truth_note=TRUTH_NOTE,
    )


@router.post(
    "/work-orders/{work_order_id}/verifications",
    response_model=WorkOrderVerificationRead,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_work_order_verification(
    work_order_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
    db: Db,
    storage: Storage,
    settings: AppSettings,
    principal: OperatorPrincipal,
    file: Annotated[UploadFile, File()],
    analyzer: Annotated[str, Form()] = "demo_fixture",
    latitude: Annotated[float | None, Form()] = None,
    longitude: Annotated[float | None, Form()] = None,
    accuracy_m: Annotated[float | None, Form()] = None,
    location_source: Annotated[str, Form()] = "unknown",
    is_synthetic_location: Annotated[bool, Form()] = False,
    client_captured_at: Annotated[datetime | None, Form()] = None,
    device_id: Annotated[str | None, Form()] = None,
    metadata: Annotated[str, Form()] = "{}",
) -> WorkOrderVerificationRead:
    work_order = _get_or_404(db, WorkOrder, work_order_id, "Work order")
    if work_order.baseline_id is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Work order has no frozen design baseline",
        )
    if work_order.status == "draft":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Work order must be assigned before evidence upload (POST /work-orders/{id}/assign)",
        )
    if work_order.status not in {"assigned", "remediating", "evidence_uploaded", "analyzing"}:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Work order status {work_order.status!r} does not accept evidence upload",
        )
    if location_source not in {"device_gps", "synthetic_demo", "manual", "unknown"}:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Invalid location_source",
        )
    if location_source == "synthetic_demo":
        is_synthetic_location = True
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

    # Historical SpatialCheck MUST use frozen WO snapshot, never live EngineeringObject.
    try:
        geometry = frozen_geometry_wgs84(work_order)
        tolerance_m, gps_accuracy_threshold_m = frozen_spatial_policy(work_order)
    except WorkOrderError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    spatial = evaluate_spatial_check(
        latitude=latitude,
        longitude=longitude,
        accuracy_m=accuracy_m,
        geometry_wgs84=geometry,
        tolerance_m=tolerance_m,
        gps_accuracy_threshold_m=gps_accuracy_threshold_m,
        location_source=location_source,
        is_synthetic_location=is_synthetic_location,
    )

    stored = await storage.save_upload(file)
    media_probe = probe_media(stored.path, stored.content_type)
    if stored.content_type.startswith("video/") and media_probe.get("probe_status") != "ok":
        stored.path.unlink(missing_ok=True)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Video container could not be parsed by ffprobe; the upload was rejected",
        )

    media_metadata = {
        **user_metadata,
        "media_probe": media_probe,
        "work_order_id": work_order.id,
        "work_order_code": work_order.work_order_code,
        "spatial_check": spatial,
        "capture": {
            "client_captured_at": client_captured_at.isoformat() if client_captured_at else None,
            "location_source": location_source,
            "is_synthetic_location": is_synthetic_location,
        },
    }
    evidence = EvidenceAsset(
        id=new_id(),
        project_id=work_order.project_id,
        baseline_id=work_order.baseline_id,
        original_name=stored.original_name,
        stored_name=stored.stored_name,
        storage_path=str(stored.path),
        content_type=stored.content_type,
        size_bytes=stored.size_bytes,
        sha256=stored.sha256,
        captured_at=client_captured_at,
        device_id=device_id,
        metadata_json=media_metadata,
    )
    job = VerificationJob(
        id=new_id(),
        project_id=work_order.project_id,
        baseline_id=work_order.baseline_id,
        evidence_id=evidence.id,
        analyzer_name=analyzer,
        analyzer_version=str(descriptor["version"]),
        status="queued",
        progress=0,
    )
    capture = EvidenceCapture(
        id=new_id(),
        project_id=work_order.project_id,
        work_order_id=work_order.id,
        evidence_id=evidence.id,
        verification_job_id=None,
        client_captured_at=client_captured_at,
        server_received_at=utcnow(),
        latitude=latitude,
        longitude=longitude,
        accuracy_m=accuracy_m,
        location_source=location_source,
        is_synthetic_location=is_synthetic_location,
        distance_to_target_m=spatial.get("distance_to_target_m"),
        tolerance_m=tolerance_m,
        gps_accuracy_threshold_m=gps_accuracy_threshold_m,
        spatial_check_status=str(spatial["spatial_check_status"]),
        spatial_check_reason=str(spatial["spatial_check_reason"]),
    )
    try:
        # Ordered flush: evidence -> job -> capture(job FK) -> lease
        db.add(evidence)
        db.flush()
        db.add(job)
        db.flush()
        capture.verification_job_id = job.id
        db.add(capture)
        db.flush()
        db.add(VerificationJobLease(job_id=job.id))
        if work_order.status in {"assigned", "remediating"}:
            transition_work_order(work_order, "evidence_uploaded")
        add_audit(
            db,
            entity_type="work_order",
            entity_id=work_order.id,
            action="evidence_captured",
            actor=principal.actor,
            payload={
                "evidence_id": evidence.id,
                "capture_id": capture.id,
                "sha256": evidence.sha256,
                "job_id": job.id,
                "is_synthetic_location": is_synthetic_location,
            },
        )
        add_audit(
            db,
            entity_type="work_order",
            entity_id=work_order.id,
            action="spatial_check_completed",
            actor=principal.actor,
            payload={
                "capture_id": capture.id,
                "spatial_check_status": capture.spatial_check_status,
                "distance_to_target_m": capture.distance_to_target_m,
                "tolerance_m": capture.tolerance_m,
                "gps_accuracy_threshold_m": capture.gps_accuracy_threshold_m,
                "geometry_source": "work_order.geometry_snapshot_json",
            },
        )
        add_audit(
            db,
            entity_type="verification_job",
            entity_id=job.id,
            action="queued",
            actor=principal.actor,
            payload={"work_order_id": work_order.id},
        )
        db.commit()
    except WorkOrderError as exc:
        db.rollback()
        stored.path.unlink(missing_ok=True)
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except Exception:
        db.rollback()
        stored.path.unlink(missing_ok=True)
        raise

    db.refresh(job)
    db.refresh(capture)
    db.refresh(work_order)
    if settings.verification_execution_mode == "inline":
        background_tasks.add_task(run_verification_job, request.app, job.id)

    return WorkOrderVerificationRead(
        job=job,
        capture=capture,
        work_order=work_order,
        compliance=None,
        truth_note=TRUTH_NOTE,
    )
