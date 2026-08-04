"""P1-2B: standard GPKG → normalized engineering-object candidates (no DB write).

Pipeline:
  preflight (P1-1.1) → whitelist fields only → pyogrio/Shapely geometry
  → type / Z/M / empty / validity checks → CRS transform (always_xy)
  → WGS84 GeoJSON → NormalizedEngineeringObjectCandidate

Does not create DesignPackage/EngineeringObject, expose upload APIs, or
fall back to a handwritten GeoPackageBinary decoder.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .gpkg_geometry_stack import (
    GpkgGeometryStackError,
    require_geometry_stack,
)
from .gpkg_preflight import (
    ALLOWED_FIELDS,
    ALLOWED_GEOMETRY,
    IMPORT_CONTRACT_VERSION,
    LAYER_WHITELIST,
    GpkgPreflightPolicy,
    GpkgPreflightReport,
    inspect_standard_gpkg,
)

TARGET_EPSG = 4326
# Fail-closed bounds after transform to WGS84.
_LON_MIN, _LON_MAX = -180.0, 180.0
_LAT_MIN, _LAT_MAX = -90.0, 90.0
# Soft reasonableness for EPSG:25832 (UTM 32N) before transform.
_UTM32_E_MIN, _UTM32_E_MAX = 100_000.0, 900_000.0
_UTM32_N_MIN, _UTM32_N_MAX = 0.0, 10_000_000.0


class GpkgNormalizeError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(slots=True)
class NormalizedEngineeringObjectCandidate:
    object_code: str
    name: str
    object_type: str
    source_layer: str
    source_epsg: int
    geometry_geojson: dict[str, Any]
    attributes: dict[str, Any]
    feature_index: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class GpkgNormalizeReport:
    valid: bool
    source_sha256: str
    import_contract_version: str
    preflight: dict[str, Any]
    candidates: list[NormalizedEngineeringObjectCandidate] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    stack_versions: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "source_sha256": self.source_sha256,
            "import_contract_version": self.import_contract_version,
            "preflight": self.preflight,
            "candidates": [c.to_dict() for c in self.candidates],
            "errors": list(self.errors),
            "warnings": list(self.warnings),
            "stack_versions": dict(self.stack_versions),
        }


@dataclass(frozen=True, slots=True)
class GpkgNormalizePolicy:
    preflight: GpkgPreflightPolicy = GpkgPreflightPolicy()
    max_features_per_layer: int = 50_000
    max_features_total: int = 100_000
    require_valid_geometry: bool = True


def _finite(value: float) -> bool:
    return isinstance(value, (int, float)) and math.isfinite(float(value))


def _coords_finite_and_in_wgs84(geom: Any) -> list[str]:
    """Walk coordinates; return rejection reason codes."""
    from shapely.geometry.base import BaseGeometry

    reasons: list[str] = []
    if not isinstance(geom, BaseGeometry):
        return ["geometry_not_shapely"]
    if geom.is_empty:
        return ["geometry_empty"]
    try:
        # shapely 2: coords may be nested for polygons
        def walk(obj: Any) -> None:
            if obj is None:
                return
            if isinstance(obj, (float, int)):
                return
            # Coordinate sequence
            try:
                for item in obj:
                    if isinstance(item, (list, tuple)) and item and isinstance(
                        item[0], (int, float)
                    ):
                        x, y = float(item[0]), float(item[1])
                        if not (_finite(x) and _finite(y)):
                            reasons.append("coordinate_not_finite")
                            return
                        if not (_LON_MIN <= x <= _LON_MAX and _LAT_MIN <= y <= _LAT_MAX):
                            reasons.append("coordinate_out_of_wgs84_range")
                            return
                    else:
                        walk(item)
            except TypeError:
                return

        walk(geom.__geo_interface__.get("coordinates"))
    except Exception:
        reasons.append("coordinate_walk_failed")
    return reasons


def _validate_source_range(x: float, y: float, epsg: int) -> list[str]:
    if not (_finite(x) and _finite(y)):
        return ["coordinate_not_finite"]
    if epsg == 4326:
        if not (_LON_MIN <= x <= _LON_MAX and _LAT_MIN <= y <= _LAT_MAX):
            return ["coordinate_out_of_wgs84_range"]
    elif epsg == 25832:
        if not (_UTM32_E_MIN <= x <= _UTM32_E_MAX and _UTM32_N_MIN <= y <= _UTM32_N_MAX):
            return ["coordinate_out_of_source_range"]
    return []


def _transform_geometry(geom: Any, source_epsg: int, target_epsg: int = TARGET_EPSG) -> Any:
    from pyproj import Transformer
    from shapely import transform as shapely_transform
    import numpy as np

    if source_epsg == target_epsg:
        return geom
    transformer = Transformer.from_crs(
        f"EPSG:{source_epsg}",
        f"EPSG:{target_epsg}",
        always_xy=True,
    )

    def _project(coords: Any) -> Any:
        # Shapely 2 transform callback receives ndarray shape (N, 2|3)
        arr = np.asarray(coords, dtype=float)
        if arr.ndim != 2 or arr.shape[1] < 2:
            raise GpkgNormalizeError("coordinate_walk_failed", "Unexpected coordinate array")
        if arr.shape[1] >= 3 and np.any(np.isfinite(arr[:, 2])):
            # Non-NaN Z present — do not silent-drop.
            raise GpkgNormalizeError("geometry_has_z", "Z coordinate present during transform")
        xs = arr[:, 0]
        ys = arr[:, 1]
        ox, oy = transformer.transform(xs, ys)
        ox_a = np.asarray(ox, dtype=float)
        oy_a = np.asarray(oy, dtype=float)
        if not (np.all(np.isfinite(ox_a)) and np.all(np.isfinite(oy_a))):
            raise GpkgNormalizeError("coordinate_not_finite", "Non-finite after transform")
        out = np.column_stack([ox_a, oy_a])
        return out

    return shapely_transform(geom, _project)


def _whitelist_attributes(
    field_names: list[str],
    field_arrays: list[Any],
    row_index: int,
    *,
    geometry_name: str | None,
) -> dict[str, Any]:
    """Copy only allowed attribute values; never include dropped/PII columns."""
    attrs: dict[str, Any] = {}
    for name, arr in zip(field_names, field_arrays, strict=False):
        if name == geometry_name:
            continue
        if name not in ALLOWED_FIELDS:
            continue
        try:
            value = arr[row_index]
        except Exception:
            continue
        # numpy scalar → python
        if hasattr(value, "item"):
            try:
                value = value.item()
            except Exception:
                pass
        if value is None:
            continue
        if isinstance(value, float) and not math.isfinite(value):
            continue
        if isinstance(value, (str, int, float, bool)):
            attrs[name] = value
        else:
            # Coerce simple numpy strings
            attrs[name] = str(value)
    return attrs


def _expected_geom_types(layer_name: str) -> frozenset[str]:
    raw = ALLOWED_GEOMETRY.get(layer_name, frozenset())
    # Shapely uses title-case type names
    mapping = {
        "POINT": "Point",
        "LINESTRING": "LineString",
        "POLYGON": "Polygon",
    }
    return frozenset(mapping[t] for t in raw if t in mapping)


def normalize_standard_gpkg(
    path: Path,
    policy: GpkgNormalizePolicy | None = None,
) -> GpkgNormalizeReport:
    """Normalize a standard GPKG file into candidates (read-only; no DB writes)."""
    policy = policy or GpkgNormalizePolicy()
    errors: list[str] = []
    warnings: list[str] = []
    candidates: list[NormalizedEngineeringObjectCandidate] = []

    preflight = inspect_standard_gpkg(path, policy=policy.preflight)
    preflight_dict = preflight.to_dict()

    if not preflight.valid:
        return GpkgNormalizeReport(
            valid=False,
            source_sha256=preflight.source_sha256,
            import_contract_version=IMPORT_CONTRACT_VERSION,
            preflight=preflight_dict,
            errors=["preflight_failed", *preflight.errors],
            warnings=list(preflight.warnings),
        )

    try:
        stack = require_geometry_stack()
    except GpkgGeometryStackError as exc:
        return GpkgNormalizeReport(
            valid=False,
            source_sha256=preflight.source_sha256,
            import_contract_version=IMPORT_CONTRACT_VERSION,
            preflight=preflight_dict,
            errors=[exc.code],
            warnings=list(preflight.warnings),
        )

    from pyogrio.raw import read as raw_read
    from shapely import from_wkb
    from shapely.geometry import mapping as shapely_mapping

    accepted_layers = [layer for layer in preflight.layers if layer.accepted]
    seen_codes: dict[str, str] = {}
    total = 0

    for layer in accepted_layers:
        layer_name = layer.name
        object_type = LAYER_WHITELIST[layer_name]
        source_epsg = layer.resolved_epsg
        if source_epsg is None:
            errors.append(f"layer_missing_epsg:{layer_name}")
            continue

        max_feat = min(policy.max_features_per_layer, policy.preflight.max_features_per_layer)
        try:
            meta, _fids, geom_arr, field_arrays = raw_read(
                path,
                layer=layer_name,
                max_features=max_feat,
                read_geometry=True,
                columns=[name for name in (layer.allowed_fields or [])],
            )
        except Exception:
            errors.append(f"layer_read_failed:{layer_name}")
            continue

        raw_fields = meta.get("fields") if isinstance(meta, dict) else None
        if raw_fields is None:
            field_names: list[str] = []
        else:
            field_names = [str(n) for n in list(raw_fields)]
        if geom_arr is None:
            errors.append(f"layer_geometry_missing:{layer_name}")
            continue

        expected_types = _expected_geom_types(layer_name)
        n = len(geom_arr)
        total += n
        if total > policy.max_features_total:
            errors.append("too_many_features_total")
            break

        for idx in range(n):
            raw_geom = geom_arr[idx]
            if raw_geom is None:
                errors.append(f"feature_geometry_null:{layer_name}:{idx}")
                continue
            try:
                geom = from_wkb(bytes(raw_geom))
            except Exception:
                errors.append(f"feature_wkb_invalid:{layer_name}:{idx}")
                continue

            if geom.is_empty:
                errors.append(f"feature_geometry_empty:{layer_name}:{idx}")
                continue
            # has_z / has_m — do not silent drop dimensions
            if getattr(geom, "has_z", False):
                errors.append(f"feature_geometry_has_z:{layer_name}:{idx}")
                continue
            if getattr(geom, "has_m", False):
                errors.append(f"feature_geometry_has_m:{layer_name}:{idx}")
                continue
            if geom.geom_type not in expected_types:
                errors.append(f"feature_geometry_type_unsupported:{layer_name}:{idx}")
                continue
            if policy.require_valid_geometry and not geom.is_valid:
                errors.append(f"feature_geometry_invalid:{layer_name}:{idx}")
                continue

            # Source-range check on first coordinate
            try:
                x0, y0 = float(geom.coords[0][0]), float(geom.coords[0][1])
            except Exception:
                try:
                    x0, y0 = float(geom.centroid.x), float(geom.centroid.y)
                except Exception:
                    errors.append(f"feature_coordinate_extract_failed:{layer_name}:{idx}")
                    continue
            range_issues = _validate_source_range(x0, y0, source_epsg)
            if range_issues:
                errors.append(f"{range_issues[0]}:{layer_name}:{idx}")
                continue

            try:
                geom_wgs = _transform_geometry(geom, source_epsg, TARGET_EPSG)
            except GpkgNormalizeError as exc:
                errors.append(f"{exc.code}:{layer_name}:{idx}")
                continue
            except Exception:
                errors.append(f"crs_transform_failed:{layer_name}:{idx}")
                continue

            wgs_issues = _coords_finite_and_in_wgs84(geom_wgs)
            if wgs_issues:
                errors.append(f"{wgs_issues[0]}:{layer_name}:{idx}")
                continue

            attrs = _whitelist_attributes(
                field_names,
                list(field_arrays or []),
                idx,
                geometry_name=str(meta.get("geometry_name") or layer.geometry_column or ""),
            )
            code = attrs.get("object_code")
            name = attrs.get("name")
            if not isinstance(code, str) or not code.strip():
                errors.append(f"feature_missing_object_code:{layer_name}:{idx}")
                continue
            if not isinstance(name, str) or not name.strip():
                errors.append(f"feature_missing_name:{layer_name}:{idx}")
                continue
            code = code.strip()
            if code in seen_codes:
                errors.append(f"object_code_duplicate:{code}")
                continue
            seen_codes[code] = layer_name

            geojson = shapely_mapping(geom_wgs)
            # Ensure pure JSON types
            candidates.append(
                NormalizedEngineeringObjectCandidate(
                    object_code=code,
                    name=str(name).strip(),
                    object_type=object_type,
                    source_layer=layer_name,
                    source_epsg=int(source_epsg),
                    geometry_geojson=dict(geojson),
                    attributes={
                        k: v
                        for k, v in attrs.items()
                        if k not in {"object_code", "name"}
                    },
                    feature_index=idx,
                )
            )

    # If any feature-level errors occurred, fail closed (no partial import candidates
    # presented as fully valid for production write). Callers may still inspect candidates
    # only when valid=True.
    valid = (
        preflight.valid
        and not errors
        and len(candidates) > 0
    )
    if preflight.valid and not candidates and not errors:
        errors.append("no_candidates_produced")

    return GpkgNormalizeReport(
        valid=valid,
        source_sha256=preflight.source_sha256,
        import_contract_version=IMPORT_CONTRACT_VERSION,
        preflight=preflight_dict,
        candidates=candidates if valid else [],
        errors=errors,
        warnings=list(preflight.warnings) + warnings,
        stack_versions=stack.to_dict(),
    )


__all__ = [
    "GpkgNormalizeError",
    "GpkgNormalizePolicy",
    "GpkgNormalizeReport",
    "NormalizedEngineeringObjectCandidate",
    "TARGET_EPSG",
    "normalize_standard_gpkg",
]
