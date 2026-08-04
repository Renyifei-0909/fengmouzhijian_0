"""P1-3A/B: transactional, idempotent standard GeoPackage import (library path).

Not a public upload API. Callers must pass a local Path already under operator
control (staging). Does not accept arbitrary SQLite URIs from clients.

Pipeline:
  normalize_standard_gpkg (preflight + geometry stack)
  → idempotency lookup (project_id + source_sha256 + import_contract_version)
  → package_code / object_code conflict checks
  → single DB transaction (caller commits)
  → audit events (P1-3B): digest, contract version, object count — no PII values
  → no residual package/objects on failure (raise before commit)

Explicit design replace is fail-closed when object_codes already exist
(allow_replace is reserved; default False).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import DesignPackage, EngineeringObject, WorkOrder, new_id, utcnow
from .analysis import add_audit
from .design_package import DesignPackageImportError, _default_rules_for_feature
from .gpkg_normalize import GpkgNormalizeReport, normalize_standard_gpkg
from .gpkg_preflight import IMPORT_CONTRACT_VERSION

SOURCE_TYPE_STANDARD_GPKG = "standard_gpkg"

# Stable audit action codes (queryable via /audit-events).
AUDIT_ACTION_IMPORTED = "standard_gpkg_imported"
AUDIT_ACTION_IDEMPOTENT = "standard_gpkg_import_idempotent"
AUDIT_ENTITY_TYPE = "design_package"

# Work-order statuses considered non-terminal for design-replace blocking (ADR-002).
_NON_TERMINAL_WORK_ORDER = frozenset(
    {
        "draft",
        "assigned",
        "evidence_uploaded",
        "analyzing",
        "needs_review",
        "deviation",
        "remediating",
    }
)


@dataclass(slots=True)
class StandardGpkgImportResult:
    package: DesignPackage
    objects: list[EngineeringObject]
    idempotent: bool
    normalize_report: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "package_id": self.package.id,
            "object_count": len(self.objects),
            "idempotent": self.idempotent,
            "source_sha256": self.package.source_sha256,
            "import_contract_version": self.package.import_contract_version,
            "source_type": self.package.source_type,
        }


def _layer_summary_from_normalize(report: GpkgNormalizeReport) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for layer in report.preflight.get("layers") or []:
        if not isinstance(layer, dict):
            continue
        name = str(layer.get("name") or "")
        if not name:
            continue
        summary[name] = {
            "object_type": layer.get("object_type"),
            "feature_count": layer.get("feature_count"),
            "accepted": bool(layer.get("accepted")),
            "whitelisted": bool(layer.get("whitelisted")),
            "resolved_epsg": layer.get("resolved_epsg"),
        }
    return summary


def _audit_payload(
    *,
    package: DesignPackage,
    project_id: str,
    object_count: int,
    idempotent: bool,
) -> dict[str, Any]:
    """Audit payload: hashes and counts only — no attribute values or full paths."""
    # Basename only if storage_path was set (never absolute path content).
    storage_name = None
    if package.storage_path:
        try:
            storage_name = Path(package.storage_path).name
        except Exception:
            storage_name = None
    return {
        "project_id": project_id,
        "package_code": package.package_code,
        "source_type": package.source_type,
        "source_sha256": package.source_sha256,
        "import_contract_version": package.import_contract_version,
        "object_count": object_count,
        "source_crs_epsg": package.source_crs_epsg,
        "synthetic": package.synthetic,
        "purpose": package.purpose,
        "idempotent": idempotent,
        "source_filename": package.source_filename,
        "storage_basename": storage_name,
    }


def _load_existing_idempotent(
    db: Session,
    *,
    project_id: str,
    source_sha256: str,
    import_contract_version: str,
) -> tuple[DesignPackage, list[EngineeringObject]] | None:
    package = db.scalar(
        select(DesignPackage).where(
            DesignPackage.project_id == project_id,
            DesignPackage.source_sha256 == source_sha256,
            DesignPackage.import_contract_version == import_contract_version,
            DesignPackage.source_type == SOURCE_TYPE_STANDARD_GPKG,
        )
    )
    if package is None:
        return None
    objects = list(
        db.scalars(
            select(EngineeringObject)
            .where(EngineeringObject.design_package_id == package.id)
            .order_by(EngineeringObject.object_code.asc())
        ).all()
    )
    return package, objects


def import_standard_gpkg(
    db: Session,
    *,
    project_id: str,
    gpkg_path: Path,
    package_code: str,
    purpose: str = "controlled",
    design_version: str = "design-v1",
    source_filename: str | None = None,
    storage_path: str | None = None,
    allow_replace: bool = False,
    synthetic: bool = True,
    actor: str = "system",
    expected_source_sha256: str | None = None,
    force_sample_classification: bool = False,
) -> StandardGpkgImportResult:
    """Import a standard GPKG after normalize; library path only.

    Parameters
    ----------
    expected_source_sha256:
        When set (P1-4 confirm path), must equal ``normalize`` digest **before**
        any database read/write. Prevents TOCTOU replacement of staging bytes.
    force_sample_classification:
        When True (public confirm API), ignore client synthetic/purpose and force
        ``purpose=controlled``, ``synthetic=True`` (sample_or_unverified).
    allow_replace:
        When False (default), any existing EngineeringObject with the
        same object_code in the project causes fail-closed rejection.
    actor:
        Audit actor label (operator id / system). Stored on AuditEvent only.
    """
    if force_sample_classification:
        purpose = "controlled"
        synthetic = True
    if purpose not in {"demo", "controlled"}:
        raise DesignPackageImportError(
            "purpose must be demo or controlled",
            code="purpose_invalid",
        )
    if not package_code or not str(package_code).strip():
        raise DesignPackageImportError(
            "package_code is required",
            code="package_code_required",
        )
    package_code = str(package_code).strip()
    path = Path(gpkg_path)
    if not path.is_file():
        raise DesignPackageImportError(
            "standard GPKG file not found",
            code="gpkg_file_not_found",
        )

    try:
        norm = normalize_standard_gpkg(path)
    except Exception as exc:
        raise DesignPackageImportError(
            "standard GPKG normalize failed",
            code="normalize_failed",
        ) from exc

    if not norm.valid:
        raise DesignPackageImportError(
            "standard GPKG normalize rejected",
            code="normalize_rejected",
        )

    digest = norm.source_sha256
    contract = IMPORT_CONTRACT_VERSION
    if not digest or len(digest) != 64:
        raise DesignPackageImportError(
            "standard GPKG digest missing after normalize",
            code="digest_missing",
        )

    # TOCTOU barrier: must match token-bound digest before any DB work.
    if expected_source_sha256 is not None:
        expected = str(expected_source_sha256).strip().lower()
        if len(expected) != 64 or expected != digest.lower():
            raise DesignPackageImportError(
                "normalized digest does not match expected source digest",
                code="source_sha256_mismatch",
            )

    # Idempotent re-submit: return existing package + objects (no new rows).
    existing = _load_existing_idempotent(
        db,
        project_id=project_id,
        source_sha256=digest,
        import_contract_version=contract,
    )
    if existing is not None:
        package, objects = existing
        add_audit(
            db,
            entity_type=AUDIT_ENTITY_TYPE,
            entity_id=package.id,
            action=AUDIT_ACTION_IDEMPOTENT,
            actor=actor or "system",
            payload=_audit_payload(
                package=package,
                project_id=project_id,
                object_count=len(objects),
                idempotent=True,
            ),
        )
        db.flush()
        return StandardGpkgImportResult(
            package=package,
            objects=objects,
            idempotent=True,
            normalize_report=norm.to_dict(),
        )

    # Same package_code with different digest must not auto-overwrite.
    code_conflict = db.scalar(
        select(DesignPackage).where(
            DesignPackage.project_id == project_id,
            DesignPackage.package_code == package_code,
            DesignPackage.source_sha256 != digest,
        )
    )
    if code_conflict is not None:
        raise DesignPackageImportError(
            "same package_code with a different file hash is not allowed",
            code="package_code_conflict_different_digest",
        )

    candidate_codes = [c.object_code for c in norm.candidates]
    if not candidate_codes:
        raise DesignPackageImportError(
            "no engineering object candidates to import",
            code="no_candidates",
        )

    existing_objects = list(
        db.scalars(
            select(EngineeringObject).where(
                EngineeringObject.project_id == project_id,
                EngineeringObject.object_code.in_(candidate_codes),
            )
        ).all()
    )
    if existing_objects and not allow_replace:
        raise DesignPackageImportError(
            "one or more object_code values already exist in this project",
            code="object_code_conflict",
        )

    if existing_objects and allow_replace:
        obj_ids = [obj.id for obj in existing_objects]
        open_orders = list(
            db.scalars(
                select(WorkOrder).where(
                    WorkOrder.engineering_object_id.in_(obj_ids),
                    WorkOrder.status.in_(sorted(_NON_TERMINAL_WORK_ORDER)),
                )
            ).all()
        )
        if open_orders:
            raise DesignPackageImportError(
                "design replace blocked by open work orders",
                code="design_replace_blocked_open_work_orders",
            )
        raise DesignPackageImportError(
            "explicit design replace is not implemented",
            code="design_replace_not_implemented",
        )

    # Resolve primary CRS from accepted candidates (all share CRS after preflight).
    source_crs = int(norm.candidates[0].source_epsg)
    filename = (source_filename or path.name)[:255]
    layer_summary = _layer_summary_from_normalize(norm)

    # source classification snapshot for audit (not a free-form client field)
    classification = (
        "sample_or_unverified" if force_sample_classification or synthetic else "library"
    )

    package = DesignPackage(
        id=new_id(),
        project_id=project_id,
        package_code=package_code,
        source_filename=filename,
        source_sha256=digest,
        source_type=SOURCE_TYPE_STANDARD_GPKG,
        purpose=purpose,
        synthetic=bool(synthetic),
        source_crs_epsg=source_crs,
        import_contract_version=contract,
        layers_json=layer_summary,
        field_mapping_json={
            "required": ["object_code", "name"],
            "optional_whitelist": sorted(
                {
                    "expected_pipe_count",
                    "expected_trench_stage",
                    "expected_specification",
                    "material",
                    "specification",
                    "procedure_code",
                    "design_version",
                    "notes",
                }
            ),
            "source_classification": classification,
        },
        redaction_policy_json={
            "pii_field_names_dropped": True,
            "values_not_read_for_dropped_fields": True,
        },
        import_status="completed",
        import_warnings_json=list(norm.warnings),
        object_count=len(norm.candidates),
        storage_path=storage_path,
        imported_at=utcnow(),
    )
    db.add(package)
    db.flush()

    created: list[EngineeringObject] = []
    for cand in norm.candidates:
        attrs = dict(cand.attributes)
        attrs.setdefault("object_code", cand.object_code)
        attrs.setdefault("name", cand.name)
        rules = _default_rules_for_feature(attrs, cand.object_type)
        geom_type = str(cand.geometry_geojson.get("type") or "")
        obj = EngineeringObject(
            id=new_id(),
            project_id=project_id,
            design_package_id=package.id,
            object_code=cand.object_code,
            object_type=cand.object_type,
            name=cand.name,
            source_layer=cand.source_layer,
            source_feature_id=str(cand.feature_index),
            geometry_type=geom_type,
            geometry_wgs84_json=cand.geometry_geojson,
            geometry_source_crs_epsg=cand.source_epsg,
            attributes_snapshot_json={
                k: v for k, v in attrs.items() if k not in {"object_code", "name"}
            },
            expected_rules_json=rules,
            design_version=str(
                cand.attributes.get("design_version") or design_version
            ),
        )
        db.add(obj)
        created.append(obj)
    db.flush()

    add_audit(
        db,
        entity_type=AUDIT_ENTITY_TYPE,
        entity_id=package.id,
        action=AUDIT_ACTION_IMPORTED,
        actor=actor or "system",
        payload=_audit_payload(
            package=package,
            project_id=project_id,
            object_count=len(created),
            idempotent=False,
        ),
    )
    db.flush()

    return StandardGpkgImportResult(
        package=package,
        objects=created,
        idempotent=False,
        normalize_report=norm.to_dict(),
    )


__all__ = [
    "AUDIT_ACTION_IDEMPOTENT",
    "AUDIT_ACTION_IMPORTED",
    "AUDIT_ENTITY_TYPE",
    "SOURCE_TYPE_STANDARD_GPKG",
    "StandardGpkgImportResult",
    "import_standard_gpkg",
]
