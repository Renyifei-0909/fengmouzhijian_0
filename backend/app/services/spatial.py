"""CRS-aware spatial helpers for work-order location checks.

Pure Python only — no geopandas/pyproj dependency. Supports:
- WGS84 (EPSG:4326) lon/lat
- UTM zone 32N (EPSG:25832) easting/northing, as used by the archived QGIS sample

Distances are approximate geodesic (haversine) or local planar in metres after
projecting the capture point into the target geometry's local frame.

GPS accuracy policy (Alpha18 checkpoint):
- ``distance <= spatial_tolerance_m`` is required to pass.
- ``accuracy_m`` is NEVER added to the distance tolerance (worse GPS must not
  make acceptance easier).
- ``accuracy_m`` must be positive and within ``gps_accuracy_threshold_m`` when
  required by the location source.
"""

from __future__ import annotations

import math
from typing import Any

EARTH_RADIUS_M = 6_371_008.8
# WGS84 ellipsoid parameters for UTM
_WGS84_A = 6_378_137.0
_WGS84_F = 1 / 298.257223563
_WGS84_E2 = _WGS84_F * (2 - _WGS84_F)
# ETRS89 / UTM zone 32N
_UTM32N_ZONE = 32
_UTM32N_FALSE_EASTING = 500_000.0
_UTM32N_FALSE_NORTHING = 0.0
_UTM32N_K0 = 0.9996
_UTM32N_LON0_DEG = -183.0 + 6.0 * _UTM32N_ZONE  # 9°E

DEFAULT_SPATIAL_TOLERANCE_M = 50.0
DEFAULT_GPS_ACCURACY_THRESHOLD_M = 30.0


class SpatialError(ValueError):
    """Invalid geometry or CRS input."""


def haversine_m(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    """Great-circle distance in metres between two WGS84 lon/lat points."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(a)))


def utm32n_to_wgs84(easting: float, northing: float) -> tuple[float, float]:
    """Convert EPSG:25832 (easting, northing) to (longitude, latitude) degrees."""
    x = easting - _UTM32N_FALSE_EASTING
    y = northing - _UTM32N_FALSE_NORTHING
    e2 = _WGS84_E2
    ep2 = e2 / (1 - e2)
    m = y / _UTM32N_K0
    mu = m / (_WGS84_A * (1 - e2 / 4 - 3 * e2**2 / 64 - 5 * e2**3 / 256))
    e1 = (1 - math.sqrt(1 - e2)) / (1 + math.sqrt(1 - e2))
    j1 = 3 * e1 / 2 - 27 * e1**3 / 32
    j2 = 21 * e1**2 / 16 - 55 * e1**4 / 32
    j3 = 151 * e1**3 / 96
    j4 = 1097 * e1**4 / 512
    fp = (
        mu
        + j1 * math.sin(2 * mu)
        + j2 * math.sin(4 * mu)
        + j3 * math.sin(6 * mu)
        + j4 * math.sin(8 * mu)
    )
    sin_fp = math.sin(fp)
    cos_fp = math.cos(fp)
    tan_fp = math.tan(fp)
    c1 = ep2 * cos_fp**2
    t1 = tan_fp**2
    r1 = _WGS84_A * (1 - e2) / (1 - e2 * sin_fp**2) ** 1.5
    n1 = _WGS84_A / math.sqrt(1 - e2 * sin_fp**2)
    d = x / (n1 * _UTM32N_K0)
    q1 = n1 * tan_fp / r1
    q2 = d**2 / 2
    q3 = (5 + 3 * t1 + 10 * c1 - 4 * c1**2 - 9 * ep2) * d**4 / 24
    q4 = (61 + 90 * t1 + 298 * c1 + 45 * t1**2 - 252 * ep2 - 3 * c1**2) * d**6 / 720
    lat = math.degrees(fp - q1 * (q2 - q3 + q4))
    q5 = d
    q6 = (1 + 2 * t1 + c1) * d**3 / 6
    q7 = (5 - 2 * c1 + 28 * t1 - 3 * c1**2 + 8 * ep2 + 24 * t1**2) * d**5 / 120
    lon = _UTM32N_LON0_DEG + math.degrees((q5 - q6 + q7) / cos_fp)
    return lon, lat


def wgs84_to_utm32n(lon: float, lat: float) -> tuple[float, float]:
    """Convert WGS84 lon/lat degrees to EPSG:25832 easting/northing metres."""
    lat_r = math.radians(lat)
    lon_r = math.radians(lon)
    lon0 = math.radians(_UTM32N_LON0_DEG)
    e2 = _WGS84_E2
    ep2 = e2 / (1 - e2)
    n = _WGS84_A / math.sqrt(1 - e2 * math.sin(lat_r) ** 2)
    t = math.tan(lat_r) ** 2
    c = ep2 * math.cos(lat_r) ** 2
    a = math.cos(lat_r) * (lon_r - lon0)
    m = _WGS84_A * (
        (1 - e2 / 4 - 3 * e2**2 / 64 - 5 * e2**3 / 256) * lat_r
        - (3 * e2 / 8 + 3 * e2**2 / 32 + 45 * e2**3 / 1024) * math.sin(2 * lat_r)
        + (15 * e2**2 / 256 + 45 * e2**3 / 1024) * math.sin(4 * lat_r)
        - (35 * e2**3 / 3072) * math.sin(6 * lat_r)
    )
    easting = (
        _UTM32N_K0
        * n
        * (
            a
            + (1 - t + c) * a**3 / 6
            + (5 - 18 * t + t**2 + 72 * c - 58 * ep2) * a**5 / 120
        )
        + _UTM32N_FALSE_EASTING
    )
    northing = (
        _UTM32N_K0
        * (
            m
            + n
            * math.tan(lat_r)
            * (
                a**2 / 2
                + (5 - t + 9 * c + 4 * c**2) * a**4 / 24
                + (61 - 58 * t + t**2 + 600 * c - 330 * ep2) * a**6 / 720
            )
        )
        + _UTM32N_FALSE_NORTHING
    )
    return easting, northing


def transform_coordinates(
    coords: Any,
    *,
    source_epsg: int,
    target_epsg: int = 4326,
) -> Any:
    """Recursively transform GeoJSON-style coordinate arrays."""
    if source_epsg == target_epsg:
        return coords
    if not isinstance(coords, (list, tuple)) or not coords:
        raise SpatialError("Invalid coordinate structure")
    if isinstance(coords[0], (int, float)):
        x, y = float(coords[0]), float(coords[1])
        if source_epsg == 25832 and target_epsg == 4326:
            lon, lat = utm32n_to_wgs84(x, y)
            return [lon, lat]
        if source_epsg == 4326 and target_epsg == 25832:
            e, n = wgs84_to_utm32n(x, y)
            return [e, n]
        raise SpatialError(f"Unsupported CRS transform {source_epsg} → {target_epsg}")
    return [transform_coordinates(item, source_epsg=source_epsg, target_epsg=target_epsg) for item in coords]


def geometry_to_wgs84(geometry: dict[str, Any], source_epsg: int) -> dict[str, Any]:
    """Return a GeoJSON geometry dict with coordinates in EPSG:4326."""
    if not isinstance(geometry, dict) or "type" not in geometry or "coordinates" not in geometry:
        raise SpatialError("Geometry must be a GeoJSON object with type and coordinates")
    geom_type = geometry["type"]
    if geom_type not in {"Point", "LineString", "Polygon"}:
        raise SpatialError(f"Unsupported geometry type: {geom_type}")
    return {
        "type": geom_type,
        "coordinates": transform_coordinates(
            geometry["coordinates"],
            source_epsg=source_epsg,
            target_epsg=4326,
        ),
    }


def _point_to_segment_m(
    px: float,
    py: float,
    ax: float,
    ay: float,
    bx: float,
    by: float,
) -> float:
    """Distance from point P to segment AB in planar metres."""
    abx, aby = bx - ax, by - ay
    apx, apy = px - ax, py - ay
    ab2 = abx * abx + aby * aby
    if ab2 <= 0:
        return math.hypot(apx, apy)
    t = max(0.0, min(1.0, (apx * abx + apy * aby) / ab2))
    cx, cy = ax + t * abx, ay + t * aby
    return math.hypot(px - cx, py - cy)


def validate_wgs84_coordinates(longitude: float, latitude: float) -> None:
    if not (-90.0 <= float(latitude) <= 90.0):
        raise SpatialError(f"latitude out of range [-90, 90]: {latitude}")
    if not (-180.0 <= float(longitude) <= 180.0):
        raise SpatialError(f"longitude out of range [-180, 180]: {longitude}")


def distance_point_to_geometry_m(
    longitude: float,
    latitude: float,
    geometry_wgs84: dict[str, Any],
) -> float:
    """Distance from a WGS84 capture point to a WGS84 GeoJSON geometry (metres)."""
    validate_wgs84_coordinates(longitude, latitude)
    geom_type = geometry_wgs84.get("type")
    coords = geometry_wgs84.get("coordinates")
    if geom_type == "Point":
        lon2, lat2 = float(coords[0]), float(coords[1])
        return haversine_m(longitude, latitude, lon2, lat2)
    # Project capture and geometry into local UTM32N metres for planar distance.
    # Acceptable for short construction-site distances in the demo CRS region.
    px, py = wgs84_to_utm32n(longitude, latitude)
    if geom_type == "LineString":
        if not isinstance(coords, list) or len(coords) < 2:
            raise SpatialError("LineString requires at least two positions")
        best = float("inf")
        for i in range(len(coords) - 1):
            a_lon, a_lat = float(coords[i][0]), float(coords[i][1])
            b_lon, b_lat = float(coords[i + 1][0]), float(coords[i + 1][1])
            ax, ay = wgs84_to_utm32n(a_lon, a_lat)
            bx, by = wgs84_to_utm32n(b_lon, b_lat)
            best = min(best, _point_to_segment_m(px, py, ax, ay, bx, by))
        return best
    if geom_type == "Polygon":
        ring = coords[0] if coords else None
        if not isinstance(ring, list) or len(ring) < 3:
            raise SpatialError("Polygon requires an exterior ring")
        local = [wgs84_to_utm32n(float(p[0]), float(p[1])) for p in ring]
        if _point_in_ring(px, py, local):
            return 0.0
        best = float("inf")
        for i in range(len(local) - 1):
            ax, ay = local[i]
            bx, by = local[i + 1]
            best = min(best, _point_to_segment_m(px, py, ax, ay, bx, by))
        ax, ay = local[-1]
        bx, by = local[0]
        best = min(best, _point_to_segment_m(px, py, ax, ay, bx, by))
        return best
    raise SpatialError(f"Unsupported geometry type for distance: {geom_type}")


def _point_in_ring(px: float, py: float, ring: list[tuple[float, float]]) -> bool:
    inside = False
    n = len(ring)
    j = n - 1
    for i in range(n):
        xi, yi = ring[i]
        xj, yj = ring[j]
        if ((yi > py) != (yj > py)) and (px < (xj - xi) * (py - yi) / (yj - yi + 1e-15) + xi):
            inside = not inside
        j = i
    return inside


def evaluate_spatial_check(
    *,
    latitude: float | None,
    longitude: float | None,
    accuracy_m: float | None,
    geometry_wgs84: dict[str, Any],
    tolerance_m: float,
    gps_accuracy_threshold_m: float,
    location_source: str,
    is_synthetic_location: bool,
) -> dict[str, Any]:
    """Return an explainable spatial validation record.

    Pass formula (distance only)::

        distance_to_target_m <= spatial_tolerance_m

    Accuracy is a separate quality gate: larger reported error (worse GPS) must
    not enlarge the acceptance radius. Fail-closed on missing/invalid inputs.
    """
    if tolerance_m <= 0:
        raise SpatialError("spatial_tolerance_m must be positive")
    if gps_accuracy_threshold_m <= 0:
        raise SpatialError("gps_accuracy_threshold_m must be positive")

    synthetic = bool(is_synthetic_location or location_source == "synthetic_demo")
    base: dict[str, Any] = {
        "tolerance_m": float(tolerance_m),
        "gps_accuracy_threshold_m": float(gps_accuracy_threshold_m),
        "location_source": location_source,
        "is_synthetic_location": synthetic,
        "accuracy_m": float(accuracy_m) if accuracy_m is not None else None,
        "latitude": latitude,
        "longitude": longitude,
    }

    def _result(
        *,
        status: str,
        reason: str,
        distance: float | None = None,
    ) -> dict[str, Any]:
        if synthetic and "synthetic" not in reason.casefold():
            reason = (
                reason.rstrip(".")
                + ". Location is synthetic_demo and must not be presented as field GPS."
            )
        return {
            **base,
            "distance_to_target_m": distance,
            "spatial_check_status": status,
            "spatial_check_reason": reason,
        }

    if latitude is None or longitude is None:
        return _result(
            status="unavailable",
            reason="Capture coordinates missing; spatial check cannot pass silently.",
        )

    try:
        validate_wgs84_coordinates(float(longitude), float(latitude))
    except SpatialError as exc:
        return _result(
            status="unavailable",
            reason=f"Invalid WGS84 coordinates: {exc}",
        )

    # Accuracy quality gate (independent of distance).
    # device_gps requires a positive accuracy reading.
    if location_source == "device_gps" and accuracy_m is None:
        return _result(
            status="unavailable",
            reason=(
                "device_gps capture is missing accuracy_m; "
                "location reasonableness cannot pass without a positive accuracy."
            ),
        )
    if accuracy_m is not None:
        if accuracy_m <= 0:
            return _result(
                status="unavailable",
                reason=(
                    f"accuracy_m must be > 0 (received {accuracy_m}); "
                    "invalid GPS accuracy cannot pass."
                ),
            )
        if accuracy_m > float(gps_accuracy_threshold_m):
            return _result(
                status="unavailable",
                reason=(
                    f"accuracy_m={accuracy_m:.1f} m exceeds frozen "
                    f"gps_accuracy_threshold_m={gps_accuracy_threshold_m:.1f} m; "
                    "GPS quality is insufficient (worse accuracy does not enlarge "
                    "distance tolerance)."
                ),
            )
    elif location_source not in {"synthetic_demo"} and not synthetic:
        # manual/unknown without accuracy: cannot pass as field location.
        return _result(
            status="unavailable",
            reason=(
                f"location_source={location_source!r} is missing accuracy_m; "
                "positive accuracy is required except synthetic_demo demos."
            ),
        )

    try:
        distance = distance_point_to_geometry_m(
            float(longitude),
            float(latitude),
            geometry_wgs84,
        )
    except SpatialError as exc:
        return _result(
            status="unavailable",
            reason=f"Geometry/CRS error: {exc}",
        )
    distance = round(distance, 3)

    # Distance gate only — never tolerance_m + accuracy_m.
    if distance <= float(tolerance_m):
        reason = (
            f"Capture is {distance:.1f} m from work-order target "
            f"(distance <= spatial_tolerance_m={tolerance_m:.1f} m). "
            "Accuracy is a separate quality gate and is not added to distance tolerance. "
            "Location reasonableness only; not absolute anti-spoofing."
        )
        if accuracy_m is not None:
            reason += (
                f" Reported accuracy_m={accuracy_m:.1f} m "
                f"(threshold {gps_accuracy_threshold_m:.1f} m)."
            )
        elif synthetic:
            reason += " synthetic_demo distance computed without field accuracy proof."
        return _result(status="passed", reason=reason, distance=distance)

    return _result(
        status="failed",
        reason=(
            f"Capture is {distance:.1f} m from work-order target; exceeds "
            f"spatial_tolerance_m={tolerance_m:.1f} m. Marked failed (not silent pass). "
            "Accuracy is not added to the distance tolerance."
        ),
        distance=distance,
    )


__all__ = [
    "DEFAULT_GPS_ACCURACY_THRESHOLD_M",
    "DEFAULT_SPATIAL_TOLERANCE_M",
    "SpatialError",
    "distance_point_to_geometry_m",
    "evaluate_spatial_check",
    "geometry_to_wgs84",
    "haversine_m",
    "transform_coordinates",
    "utm32n_to_wgs84",
    "validate_wgs84_coordinates",
    "wgs84_to_utm32n",
]
