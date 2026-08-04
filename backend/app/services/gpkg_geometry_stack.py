"""P1-2A: mature geometry stack feasibility probe (pyogrio + Shapely + pyproj).

This module does **not**:
- write DesignPackage / EngineeringObject
- expose upload APIs
- hand-parse full GeoPackageBinary / WKB as a product decoder
- require GeoPandas

When the optional ``gpkg`` extra is missing or native libraries fail to load,
callers receive :class:`GpkgGeometryStackError` — there is no silent fallback
to a handwritten parser.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

STACK_EXTRA_NAME = "gpkg"
STACK_PACKAGES = ("pyogrio", "shapely", "pyproj")


class GpkgGeometryStackError(RuntimeError):
    """Raised when the GDAL-family stack is unavailable or unusable."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(slots=True)
class GpkgGeometryStackVersions:
    pyogrio: str | None = None
    shapely: str | None = None
    pyproj: str | None = None
    numpy: str | None = None
    gdal: str | None = None
    geos: str | None = None
    proj_data_dir: str | None = None
    gpkg_driver: bool = False
    available: bool = False
    error_code: str | None = None
    error_message: str | None = None
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def probe_geometry_stack() -> GpkgGeometryStackVersions:
    """Probe optional imports and native library versions (no file I/O)."""
    result = GpkgGeometryStackVersions(
        notes=[
            "optional_extra=gpkg",
            "geopandas_not_required",
            "fiona_not_primary",
            "no_handwritten_wkb_fallback",
        ]
    )
    try:
        import numpy as np
        import pyogrio
        import pyproj
        import shapely
        from pyogrio import list_drivers
        from pyogrio.core import (
            __gdal_version_string__,
            get_gdal_geos_version,
        )
    except ImportError as exc:
        result.error_code = "geometry_stack_not_installed"
        result.error_message = (
            f"Optional geometry stack missing ({exc}). "
            f"Install with: uv sync --extra {STACK_EXTRA_NAME}"
        )
        return result
    except Exception as exc:  # DLL / policy / native load failures
        result.error_code = "geometry_stack_native_load_failed"
        result.error_message = f"Geometry stack native load failed: {type(exc).__name__}"
        return result

    result.pyogrio = getattr(pyogrio, "__version__", None)
    result.shapely = getattr(shapely, "__version__", None)
    result.pyproj = getattr(pyproj, "__version__", None)
    result.numpy = getattr(np, "__version__", None)
    result.gdal = str(__gdal_version_string__)
    geos_ver = get_gdal_geos_version()
    result.geos = (
        ".".join(str(part) for part in geos_ver)
        if isinstance(geos_ver, tuple)
        else str(geos_ver)
    )
    try:
        result.proj_data_dir = str(pyproj.datadir.get_data_dir())
    except Exception:
        result.proj_data_dir = None
        result.notes.append("proj_data_dir_unavailable")

    try:
        drivers = list_drivers()
        result.gpkg_driver = "GPKG" in drivers
    except Exception:
        result.gpkg_driver = False
        result.error_code = "gpkg_driver_probe_failed"
        result.error_message = "Failed to list OGR drivers"
        return result

    if not result.gpkg_driver:
        result.error_code = "gpkg_driver_missing"
        result.error_message = "GDAL/OGR GPKG driver is not available"
        return result

    result.available = True
    return result


def require_geometry_stack() -> GpkgGeometryStackVersions:
    """Return stack versions or raise :class:`GpkgGeometryStackError`."""
    probe = probe_geometry_stack()
    if not probe.available:
        raise GpkgGeometryStackError(
            probe.error_code or "geometry_stack_unavailable",
            probe.error_message or "Geometry stack unavailable",
        )
    return probe


@dataclass(slots=True)
class GpkgLayerReadSample:
    layer_name: str
    feature_count: int
    geometry_type: str | None
    crs: str | None
    fields: list[str]
    sample_geometry_types: list[str]
    sample_wgs84_coords: list[tuple[float, float]] = field(default_factory=list)


def read_layer_sample(
    path: Path,
    *,
    layer: str,
    max_features: int = 5,
    source_epsg: int | None = None,
    target_epsg: int = 4326,
) -> GpkgLayerReadSample:
    """Read a bounded feature sample via pyogrio → Shapely → optional pyproj.

    Does not write the application database. Does not implement full import policy.
    """
    stack = require_geometry_stack()
    _ = stack  # versions validated

    from pyogrio import list_layers, read_info
    from pyogrio.raw import read as raw_read
    from pyproj import Transformer
    from shapely import from_wkb

    if not path.is_file():
        raise GpkgGeometryStackError("file_not_found", "GPKG path is not a regular file")

    try:
        layers = list_layers(path)
    except Exception as exc:
        raise GpkgGeometryStackError(
            "gpkg_list_layers_failed",
            f"list_layers failed: {type(exc).__name__}",
        ) from exc

    layer_names = [str(row[0]) for row in layers]
    if layer not in layer_names:
        raise GpkgGeometryStackError(
            "layer_not_found",
            f"Layer not found in dataset (available={len(layer_names)})",
        )

    try:
        info = read_info(path, layer=layer)
    except Exception as exc:
        raise GpkgGeometryStackError(
            "gpkg_read_info_failed",
            f"read_info failed: {type(exc).__name__}",
        ) from exc

    try:
        meta, _fids, geom_arr, _fields = raw_read(
            path,
            layer=layer,
            max_features=max(0, int(max_features)),
            read_geometry=True,
        )
    except Exception as exc:
        raise GpkgGeometryStackError(
            "gpkg_raw_read_failed",
            f"raw read failed: {type(exc).__name__}",
        ) from exc

    crs_text = meta.get("crs") if isinstance(meta, dict) else info.get("crs")
    resolved_source = source_epsg
    if resolved_source is None and isinstance(crs_text, str) and crs_text.upper().startswith(
        "EPSG:"
    ):
        try:
            resolved_source = int(crs_text.split(":", 1)[1])
        except ValueError:
            resolved_source = None

    transformer: Transformer | None = None
    if resolved_source is not None and resolved_source != target_epsg:
        try:
            transformer = Transformer.from_crs(
                f"EPSG:{resolved_source}",
                f"EPSG:{target_epsg}",
                always_xy=True,
            )
        except Exception as exc:
            raise GpkgGeometryStackError(
                "crs_transformer_failed",
                f"pyproj transformer failed: {type(exc).__name__}",
            ) from exc

    sample_types: list[str] = []
    sample_coords: list[tuple[float, float]] = []
    if geom_arr is not None:
        for raw in geom_arr:
            if raw is None:
                continue
            try:
                geom = from_wkb(bytes(raw))
            except Exception as exc:
                raise GpkgGeometryStackError(
                    "wkb_parse_failed",
                    f"Shapely from_wkb failed: {type(exc).__name__}",
                ) from exc
            sample_types.append(geom.geom_type)
            if geom.is_empty:
                continue
            # Sample a single representative XY (no full normalization policy here).
            try:
                x, y = float(geom.coords[0][0]), float(geom.coords[0][1])
            except Exception:
                try:
                    x, y = float(geom.centroid.x), float(geom.centroid.y)
                except Exception as exc:
                    raise GpkgGeometryStackError(
                        "geometry_coordinate_extract_failed",
                        f"coordinate extract failed: {type(exc).__name__}",
                    ) from exc
            if transformer is not None:
                x, y = transformer.transform(x, y)
            if not (x == x and y == y):  # NaN check
                raise GpkgGeometryStackError(
                    "coordinate_not_finite",
                    "Transformed coordinates are not finite",
                )
            sample_coords.append((float(x), float(y)))

    fields = []
    if isinstance(meta, dict) and meta.get("fields") is not None:
        fields = [str(name) for name in list(meta["fields"])]
    elif info.get("fields") is not None:
        fields = [str(name) for name in list(info["fields"])]

    return GpkgLayerReadSample(
        layer_name=layer,
        feature_count=int(info.get("features") or 0),
        geometry_type=(
            str(info.get("geometry_type")) if info.get("geometry_type") is not None else None
        ),
        crs=str(crs_text) if crs_text is not None else None,
        fields=fields,
        sample_geometry_types=sample_types,
        sample_wgs84_coords=sample_coords,
    )


def transform_xy_always_xy(
    x: float,
    y: float,
    *,
    source_epsg: int,
    target_epsg: int = 4326,
) -> tuple[float, float]:
    """CRS transform using pyproj with always_xy=True."""
    require_geometry_stack()
    from pyproj import Transformer

    try:
        transformer = Transformer.from_crs(
            f"EPSG:{source_epsg}",
            f"EPSG:{target_epsg}",
            always_xy=True,
        )
        out_x, out_y = transformer.transform(x, y)
    except Exception as exc:
        raise GpkgGeometryStackError(
            "crs_transform_failed",
            f"transform failed: {type(exc).__name__}",
        ) from exc
    if not (out_x == out_x and out_y == out_y):
        raise GpkgGeometryStackError("coordinate_not_finite", "Non-finite transform result")
    return float(out_x), float(out_y)


__all__ = [
    "GpkgGeometryStackError",
    "GpkgGeometryStackVersions",
    "GpkgLayerReadSample",
    "STACK_EXTRA_NAME",
    "STACK_PACKAGES",
    "probe_geometry_stack",
    "read_layer_sample",
    "require_geometry_stack",
    "transform_xy_always_xy",
]
