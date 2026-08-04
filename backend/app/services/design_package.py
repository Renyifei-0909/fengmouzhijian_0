"""Design package import for desensitized / synthetic QGIS derivatives.

Primary format: synthetic JSON design package (safe for demos and tests).

Legacy / restricted path: ``import_gpkg_derivative`` requires explicit
``geom_geojson`` TEXT columns and is **not** standard GeoPackage import.
It must not be used as a public upload entry. Standard GPKG readiness is
handled by ``gpkg_preflight.inspect_standard_gpkg`` (read-only preflight only
in P1-1; full geometry import is P1-2+).
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

from fastapi import UploadFile
from sqlalchemy.orm import Session

from ..models import DesignPackage, EngineeringObject, new_id, utcnow
from .spatial import SpatialError, geometry_to_wgs84
from .storage import canonical_json_bytes, sha256_bytes

PACKAGE_SCHEMA_VERSION = "1.0"
DEFAULT_DESIGN_PACKAGE_MAX_UPLOAD_BYTES = 2 * 1024 * 1024  # 2 MiB

# Whitelisted layer names and object types for the first vertical slice.
LAYER_WHITELIST: dict[str, str] = {
    "pipe_routes": "pipe_route",
    "trenches": "trench",
    "infrastructure_points": "infrastructure_point",
}

# Attribute keys allowed into snapshots (no PII / raw paths / phones).
ATTRIBUTE_WHITELIST = frozenset(
    {
        "object_code",
        "name",
        "expected_pipe_count",
        "visible_pipe_count",
        "trench_stage",
        "expected_trench_stage",
        "material",
        "specification",
        "expected_specification",
        "procedure_code",
        "design_version",
        "notes",
        "object_visibility",
    }
)

# Keys that must never be copied from source attributes.
REDACT_DENYLIST = frozenset(
    {
        "name_person",
        "person_name",
        "phone",
        "telephone",
        "email",
        "address",
        "full_address",
        "contact",
        "attachment_path",
        "photo_path",
        "external_path",
        "owner_name",
        "operator_name",
        "site_owner",
    }
)


class DesignPackageImportError(ValueError):
    """Raised when a design package cannot be imported safely.

    ``code`` is a stable machine-readable token for API mapping; ``message`` is
    safe for clients (no absolute paths or raw attribute values).
    """

    def __init__(self, message: str, *, code: str = "design_package_import_error") -> None:
        self.code = code
        super().__init__(message)


async def read_upload_with_limit(upload: UploadFile, *, max_bytes: int) -> bytes:
    """Read an upload with a hard byte cap; raises if the limit is exceeded."""
    if max_bytes <= 0:
        raise DesignPackageImportError("design package upload limit must be positive")
    chunks: list[bytes] = []
    total = 0
    # Read slightly past the limit so we can distinguish "exactly max" vs "over".
    while True:
        chunk = await upload.read(64 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise DesignPackageImportError(
                f"Design package exceeds max upload size of {max_bytes} bytes"
            )
        chunks.append(chunk)
    return b"".join(chunks)


def sha256_file_stream(path: Path, *, chunk_size: int = 1024 * 1024) -> str:
    """Stream a file into SHA-256 without loading the whole file into memory."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _redact_attributes(raw: dict[str, Any]) -> dict[str, Any]:
    cleaned: dict[str, Any] = {}
    for key, value in raw.items():
        key_l = str(key)
        if key_l.casefold() in {d.casefold() for d in REDACT_DENYLIST}:
            continue
        if key_l not in ATTRIBUTE_WHITELIST:
            continue
        if isinstance(value, (str, int, float, bool)) or value is None:
            cleaned[key_l] = value
        else:
            continue
    return cleaned


def _default_rules_for_feature(attrs: dict[str, Any], object_type: str) -> dict[str, Any]:
    expected: dict[str, Any] = {
        "object_visibility": {"one_of": ["visible", "partially_visible"]},
    }
    _ = object_type
    if "expected_pipe_count" in attrs:
        expected["visible_pipe_count"] = {"equals": attrs["expected_pipe_count"]}
    if "expected_trench_stage" in attrs:
        expected["trench_stage"] = {"equals": attrs["expected_trench_stage"]}
    elif object_type in {"pipe_route", "trench"}:
        expected["trench_stage"] = {
            "one_of": ["excavation", "laying", "backfill", "completed"],
        }
    if "expected_specification" in attrs:
        expected["visible_material_or_specification"] = {
            "equals": attrs["expected_specification"],
        }
    return {
        "rule_version": "workorder-rules-v0.1",
        "expected": expected,
        "metrology_policy": {
            "depth_m": "manual_measurement_required",
            "note": "Single uncalibrated photo cannot claim engineering depth.",
        },
    }


def parse_design_package_json(
    payload: dict[str, Any],
    *,
    require_synthetic: bool | None = None,
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise DesignPackageImportError("Design package root must be a JSON object")
    schema = payload.get("schema_version")
    if schema != PACKAGE_SCHEMA_VERSION:
        raise DesignPackageImportError(
            f"Unsupported design package schema_version={schema!r}; expected {PACKAGE_SCHEMA_VERSION}"
        )
    source_crs = payload.get("source_crs_epsg")
    if not isinstance(source_crs, int) or source_crs <= 0:
        raise DesignPackageImportError("source_crs_epsg must be a positive integer")
    if source_crs not in {4326, 25832}:
        raise DesignPackageImportError(
            f"Unsupported source_crs_epsg={source_crs}; Alpha18 supports 4326 and 25832 only"
        )
    layers = payload.get("layers")
    if not isinstance(layers, dict) or not layers:
        raise DesignPackageImportError("layers must be a non-empty object")
    purpose = payload.get("purpose") or "demo"
    if purpose not in {"demo", "controlled"}:
        raise DesignPackageImportError("purpose must be demo or controlled")

    if "synthetic" not in payload:
        raise DesignPackageImportError("synthetic field is required and must be a boolean")
    if not isinstance(payload["synthetic"], bool):
        raise DesignPackageImportError("synthetic field must be a boolean")
    synthetic = payload["synthetic"]
    if require_synthetic is True and synthetic is not True:
        raise DesignPackageImportError(
            "This import path requires synthetic=true; "
            "controlled packages cannot use the synthetic JSON upload endpoint"
        )
    if require_synthetic is False and synthetic is not False:
        raise DesignPackageImportError(
            "This controlled derivative import path requires synthetic=false"
        )

    package_code = str(payload.get("package_code") or "PKG-DEMO")
    redaction = payload.get("redaction_policy") or {
        "attribute_whitelist": sorted(ATTRIBUTE_WHITELIST),
        "deny_list": sorted(REDACT_DENYLIST),
        "note": "PII and external paths are dropped at import.",
    }
    return {
        "package_code": package_code,
        "source_crs_epsg": source_crs,
        "purpose": purpose,
        "synthetic": synthetic,
        "layers": layers,
        "redaction_policy": redaction,
        "field_mapping": payload.get("field_mapping") or {},
        "design_version": str(payload.get("design_version") or "design-v1"),
    }


def _features_from_layers(
    layers: dict[str, Any],
    *,
    source_crs_epsg: int,
    design_version: str,
) -> tuple[list[dict[str, Any]], list[str], dict[str, Any]]:
    objects: list[dict[str, Any]] = []
    warnings: list[str] = []
    layer_summary: dict[str, Any] = {}
    seen_codes: set[str] = set()

    for layer_name, layer_body in layers.items():
        if layer_name not in LAYER_WHITELIST:
            warnings.append(f"Skipped non-whitelisted layer: {layer_name}")
            continue
        object_type = LAYER_WHITELIST[layer_name]
        if not isinstance(layer_body, dict):
            warnings.append(f"Layer {layer_name} is not an object; skipped")
            continue
        features = layer_body.get("features") or []
        if not isinstance(features, list):
            warnings.append(f"Layer {layer_name} features must be a list; skipped")
            continue
        accepted = 0
        for index, feature in enumerate(features):
            if not isinstance(feature, dict):
                warnings.append(f"{layer_name}[{index}] not an object; skipped")
                continue
            geometry = feature.get("geometry")
            if not isinstance(geometry, dict):
                warnings.append(f"{layer_name}[{index}] missing geometry; skipped")
                continue
            try:
                wgs84 = geometry_to_wgs84(geometry, source_crs_epsg)
            except SpatialError as exc:
                warnings.append(f"{layer_name}[{index}] geometry error: {exc}")
                continue
            raw_attrs = feature.get("attributes") or feature.get("properties") or {}
            if not isinstance(raw_attrs, dict):
                raw_attrs = {}
            attrs = _redact_attributes(raw_attrs)
            object_code = str(
                feature.get("object_code")
                or attrs.get("object_code")
                or f"{layer_name.upper()}-{index + 1}"
            )
            if object_code in seen_codes:
                raise DesignPackageImportError(
                    f"Duplicate object_code in package: {object_code}"
                )
            seen_codes.add(object_code)
            name = str(feature.get("name") or attrs.get("name") or object_code)
            source_feature_id = str(feature.get("source_feature_id") or feature.get("id") or index + 1)
            rules = feature.get("expected_rules") or _default_rules_for_feature(attrs, object_type)
            objects.append(
                {
                    "object_code": object_code,
                    "object_type": object_type,
                    "name": name,
                    "source_layer": layer_name,
                    "source_feature_id": source_feature_id,
                    "geometry_type": wgs84["type"],
                    "geometry_wgs84_json": wgs84,
                    "geometry_source_crs_epsg": source_crs_epsg,
                    "attributes_snapshot_json": attrs,
                    "expected_rules_json": rules,
                    "design_version": str(feature.get("design_version") or design_version),
                }
            )
            accepted += 1
        layer_summary[layer_name] = {
            "object_type": object_type,
            "feature_count": accepted,
            "whitelisted": True,
        }
    return objects, warnings, layer_summary


def import_design_package_dict(
    db: Session,
    *,
    project_id: str,
    payload: dict[str, Any],
    source_filename: str,
    source_sha256: str | None = None,
    storage_path: str | None = None,
    source_type: str = "synthetic_json",
    require_synthetic: bool | None = None,
) -> tuple[DesignPackage, list[EngineeringObject]]:
    if source_type not in {"synthetic_json", "gpkg_derivative"}:
        raise DesignPackageImportError(f"Invalid source_type: {source_type}")
    if source_type == "synthetic_json" and require_synthetic is None:
        require_synthetic = True
    if source_type == "gpkg_derivative" and require_synthetic is None:
        require_synthetic = False

    parsed = parse_design_package_json(payload, require_synthetic=require_synthetic)
    if source_type == "synthetic_json" and parsed["synthetic"] is not True:
        raise DesignPackageImportError("synthetic_json source_type requires synthetic=true")
    if source_type == "gpkg_derivative" and parsed["synthetic"] is not False:
        raise DesignPackageImportError("gpkg_derivative source_type requires synthetic=false")

    digest = source_sha256 or sha256_bytes(canonical_json_bytes(payload))
    objects_data, warnings, layer_summary = _features_from_layers(
        parsed["layers"],
        source_crs_epsg=parsed["source_crs_epsg"],
        design_version=parsed["design_version"],
    )
    if not objects_data:
        raise DesignPackageImportError("No whitelisted engineering objects could be imported")

    package = DesignPackage(
        id=new_id(),
        project_id=project_id,
        package_code=parsed["package_code"],
        source_filename=source_filename,
        source_sha256=digest,
        source_type=source_type,
        purpose=parsed["purpose"],
        synthetic=parsed["synthetic"],
        source_crs_epsg=parsed["source_crs_epsg"],
        import_contract_version="",  # JSON / derivative: not standard GPKG contract
        layers_json=layer_summary,
        field_mapping_json=parsed["field_mapping"],
        redaction_policy_json=parsed["redaction_policy"],
        import_status="completed" if not warnings else "partial",
        import_warnings_json=warnings,
        object_count=len(objects_data),
        storage_path=storage_path,
        imported_at=utcnow(),
    )
    db.add(package)
    db.flush()

    created: list[EngineeringObject] = []
    for item in objects_data:
        obj = EngineeringObject(
            id=new_id(),
            project_id=project_id,
            design_package_id=package.id,
            **item,
        )
        db.add(obj)
        created.append(obj)
    db.flush()
    return package, created


def load_json_package_file(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise DesignPackageImportError(f"Failed to read design package JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise DesignPackageImportError("Design package JSON root must be an object")
    return payload


def import_gpkg_derivative(
    db: Session,
    *,
    project_id: str,
    gpkg_path: Path,
    package_code: str = "PKG-GPKG",
    purpose: str = "controlled",
    design_version: str = "design-v1",
) -> tuple[DesignPackage, list[EngineeringObject]]:
    """Legacy restricted GeoPackage *derivative* import (library path only).

    **Not** standard GPKG import and **not** a public upload entry.

    Constraints (intentionally narrow; do not expand toward “standard GPKG”):
    - Requires custom ``geom_geojson`` TEXT column per whitelisted layer;
    - Rejects raw GeoPackageBinary geometry;
    - Uses ``gpkg_contents.srs_id`` as an explicit per-layer CRS key for this
      derivative format only (not a substitute for ``gpkg_spatial_ref_sys``
      organization / organization_coordsys_id resolution used by standard preflight);
    - All imported layers must share one CRS;
    - Not exposed as a public API accepting arbitrary server paths.

    For OGC standard GeoPackage readiness, use ``inspect_standard_gpkg``
    (metadata preflight). Do not call this function from the standard preflight path.
    """
    if not gpkg_path.is_file():
        raise DesignPackageImportError(f"GPKG derivative not found: {gpkg_path}")
    digest = sha256_file_stream(gpkg_path)
    warnings: list[str] = []
    try:
        conn = sqlite3.connect(f"file:{gpkg_path.as_posix()}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        raise DesignPackageImportError(f"Cannot open GPKG derivative: {exc}") from exc
    try:
        conn.row_factory = sqlite3.Row
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        if "gpkg_contents" not in tables:
            raise DesignPackageImportError(
                "Restricted GPKG derivative requires gpkg_contents with per-layer srs_id; "
                "CRS is not guessed as EPSG:25832"
            )

        layer_crs: dict[str, int] = {}
        for layer_name in LAYER_WHITELIST:
            if layer_name not in tables:
                continue
            row = conn.execute(
                "SELECT srs_id FROM gpkg_contents WHERE table_name = ? LIMIT 1",
                (layer_name,),
            ).fetchone()
            if row is None or row[0] is None:
                raise DesignPackageImportError(
                    f"Layer {layer_name} is missing srs_id in gpkg_contents; "
                    "import fails closed without CRS guessing"
                )
            srs_id = int(row[0])
            if srs_id not in {4326, 25832}:
                raise DesignPackageImportError(
                    f"Layer {layer_name} has unsupported srs_id={srs_id}; "
                    "Alpha18 supports 4326 and 25832 only"
                )
            layer_crs[layer_name] = srs_id

        if not layer_crs:
            raise DesignPackageImportError(
                "No whitelisted layers found in gpkg_contents for this restricted derivative"
            )
        unique_crs = set(layer_crs.values())
        if len(unique_crs) != 1:
            raise DesignPackageImportError(
                "Multi-layer CRS mismatch in GPKG derivative: "
                + ", ".join(f"{name}=EPSG:{epsg}" for name, epsg in sorted(layer_crs.items()))
                + ". Rejected (no silent mixed-CRS import)."
            )
        source_crs = next(iter(unique_crs))

        layers: dict[str, Any] = {}
        for layer_name in layer_crs:
            columns = {
                r[1]
                for r in conn.execute(f"PRAGMA table_info('{layer_name}')").fetchall()
            }
            if "geom_geojson" not in columns:
                raise DesignPackageImportError(
                    f"Layer {layer_name} has no geom_geojson column; "
                    "raw GeoPackageBinary geometry is not supported in Alpha18 "
                    "(restricted derivative requires explicit geom_geojson TEXT)."
                )
            features = []
            for row in conn.execute(f"SELECT * FROM '{layer_name}'").fetchall():
                data = dict(row)
                try:
                    geometry = json.loads(data["geom_geojson"])
                except (TypeError, json.JSONDecodeError):
                    warnings.append(f"{layer_name}: invalid geom_geojson; skipped row")
                    continue
                if not isinstance(geometry, dict) or "type" not in geometry:
                    warnings.append(f"{layer_name}: geom_geojson is not a GeoJSON object; skipped")
                    continue
                attrs = {k: data[k] for k in data if k in ATTRIBUTE_WHITELIST}
                features.append(
                    {
                        "object_code": data.get("object_code") or attrs.get("object_code"),
                        "name": data.get("name") or attrs.get("name"),
                        "source_feature_id": str(
                            data.get("id") or data.get("fid") or len(features) + 1
                        ),
                        "geometry": geometry,
                        "attributes": attrs,
                    }
                )
            if features:
                layers[layer_name] = {"features": features}
        if not layers:
            raise DesignPackageImportError(
                "No importable whitelisted layers with valid geom_geojson found in GPKG derivative"
            )
        payload = {
            "schema_version": PACKAGE_SCHEMA_VERSION,
            "package_code": package_code,
            "purpose": purpose,
            "synthetic": False,
            "source_crs_epsg": source_crs,
            "design_version": design_version,
            "layers": layers,
            "redaction_policy": {
                "attribute_whitelist": sorted(ATTRIBUTE_WHITELIST),
                "deny_list": sorted(REDACT_DENYLIST),
                "note": (
                    "Restricted/controlled GPKG derivative import; "
                    "raw personal fields dropped; not a general GPKG importer."
                ),
            },
        }
    finally:
        conn.close()

    package, objects = import_design_package_dict(
        db,
        project_id=project_id,
        payload=payload,
        source_filename=gpkg_path.name,
        source_sha256=digest,
        storage_path=str(gpkg_path),
        source_type="gpkg_derivative",
        require_synthetic=False,
    )
    if warnings:
        package.import_warnings_json = list(package.import_warnings_json or []) + warnings
        package.import_status = "partial"
    return package, objects


__all__ = [
    "ATTRIBUTE_WHITELIST",
    "DEFAULT_DESIGN_PACKAGE_MAX_UPLOAD_BYTES",
    "DesignPackageImportError",
    "LAYER_WHITELIST",
    "PACKAGE_SCHEMA_VERSION",
    "import_design_package_dict",
    "import_gpkg_derivative",
    "load_json_package_file",
    "parse_design_package_json",
    "read_upload_with_limit",
    "sha256_file_stream",
]
