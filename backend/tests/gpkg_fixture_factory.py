"""Synthetic GeoPackage fixtures for preflight tests only (P1-1.1).

Not a product geometry encoder/decoder. Builds minimal OGC-like containers
with sqlite3 and optional StandardGeoPackageBinary headers for flag checks.
All coordinates and attributes are synthetic and non-sensitive.
"""

from __future__ import annotations

import sqlite3
import struct
from pathlib import Path

GPKG_APPLICATION_ID = 0x47504B47

# Envelope indicator E → double count (matches OGC 12-128r15 / product preflight).
_ENVELOPE_DOUBLE_COUNTS = {0: 0, 1: 4, 2: 6, 3: 6, 4: 8}


def build_gp_binary(
    srs_id: int,
    wkb: bytes = b"",
    *,
    little_endian: bool = True,
    empty: bool = False,
    extended: bool = False,
    reserved: int = 0,
    envelope_indicator: int = 0,
    envelope_bytes: bytes | None = None,
    version: int = 0,
) -> bytes:
    """Build a minimal GeoPackageBinary prefix + optional WKB for tests only.

    Flags (OGC):
      B (bit0): 1=LE header numerics, 0=BE
      E (bits1-3): envelope type
      Y (bit4): empty
      X (bit5): extended
      reserved (bits6-7)
    """
    flags = 0
    if little_endian:
        flags |= 0x01
    flags |= (envelope_indicator & 0x07) << 1
    if empty:
        flags |= 0x10
    if extended:
        flags |= 0x20
    flags |= (reserved & 0x03) << 6

    endian = "<" if little_endian else ">"
    header = b"GP" + bytes([version & 0xFF, flags]) + struct.pack(f"{endian}i", srs_id)

    if envelope_bytes is not None:
        header += envelope_bytes
    elif envelope_indicator in _ENVELOPE_DOUBLE_COUNTS and envelope_indicator > 0:
        n_doubles = _ENVELOPE_DOUBLE_COUNTS[envelope_indicator]
        # Zero envelope values; endian of doubles matches header B bit.
        header += struct.pack(f"{endian}{n_doubles}d", *([0.0] * n_doubles))

    return header + wkb


def _gp_header(
    srs_id: int,
    wkb: bytes,
    *,
    empty: bool = False,
    little_endian: bool = True,
) -> bytes:
    """Default standard (non-extended) GP header with matching B bit and srs_id endian."""
    return build_gp_binary(
        srs_id,
        wkb,
        little_endian=little_endian,
        empty=empty,
        extended=False,
        reserved=0,
        envelope_indicator=0,
    )


def _wkb_point_xy(x: float, y: float) -> bytes:
    return struct.pack("<BIdd", 1, 1, x, y)


def _wkb_linestring_xy(coords: list[tuple[float, float]]) -> bytes:
    body = struct.pack("<BI", 1, 2)
    body += struct.pack("<I", len(coords))
    for x, y in coords:
        body += struct.pack("<dd", x, y)
    return body


def _init_spatial_ref(
    conn: sqlite3.Connection,
    *,
    srs_id: int,
    organization: str,
    organization_coordsys_id: int,
    definition: str = "SYNTHETIC",
) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS gpkg_spatial_ref_sys (
            srs_name TEXT NOT NULL,
            srs_id INTEGER NOT NULL PRIMARY KEY,
            organization TEXT NOT NULL,
            organization_coordsys_id INTEGER NOT NULL,
            definition TEXT NOT NULL,
            description TEXT
        )
        """
    )
    conn.execute(
        """
        INSERT OR REPLACE INTO gpkg_spatial_ref_sys
        (srs_name, srs_id, organization, organization_coordsys_id, definition, description)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            f"SRS-{srs_id}",
            srs_id,
            organization,
            organization_coordsys_id,
            definition,
            "synthetic",
        ),
    )


def _init_contents_geometry(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS gpkg_contents (
            table_name TEXT NOT NULL PRIMARY KEY,
            data_type TEXT NOT NULL,
            identifier TEXT,
            description TEXT,
            last_change TEXT,
            min_x REAL, min_y REAL, max_x REAL, max_y REAL,
            srs_id INTEGER
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS gpkg_geometry_columns (
            table_name TEXT NOT NULL,
            column_name TEXT NOT NULL,
            geometry_type_name TEXT NOT NULL,
            srs_id INTEGER NOT NULL,
            z TINYINT NOT NULL,
            m TINYINT NOT NULL,
            PRIMARY KEY (table_name, column_name)
        )
        """
    )


def create_minimal_gpkg(
    path: Path,
    *,
    application_id: int = GPKG_APPLICATION_ID,
    user_version: int = 10200,
    include_system_tables: bool = True,
    feature_layers: list[dict] | None = None,
    extra_contents: list[dict] | None = None,
    little_endian_geom: bool = True,
) -> Path:
    """Create a synthetic GPKG file.

    feature_layers items:
      name, geometry_type, srs_id, columns, rows, z, m, geometry_column,
      geometry_blob (optional override), geometry_column_sql_type (default BLOB)
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    conn = sqlite3.connect(path)
    try:
        conn.execute(f"PRAGMA application_id = {int(application_id)}")
        conn.execute(f"PRAGMA user_version = {int(user_version)}")
        if include_system_tables:
            _init_spatial_ref(
                conn,
                srs_id=4326,
                organization="EPSG",
                organization_coordsys_id=4326,
                definition='GEOGCS["WGS 84"]',
            )
            _init_spatial_ref(
                conn,
                srs_id=900001,
                organization="EPSG",
                organization_coordsys_id=25832,
                definition='PROJCS["ETRS89 / UTM 32N"]',
            )
            _init_spatial_ref(
                conn,
                srs_id=999999,
                organization="LOCAL",
                organization_coordsys_id=1,
                definition="undefined",
            )
            _init_contents_geometry(conn)

        for layer in feature_layers or []:
            name = layer["name"]
            srs_id = int(layer.get("srs_id", 4326))
            geom_col = layer.get("geometry_column", "geom")
            geom_type = layer.get("geometry_type", "LINESTRING")
            z = int(layer.get("z", 0))
            m = int(layer.get("m", 0))
            geom_sql_type = layer.get("geometry_column_sql_type", "BLOB")
            columns: list[str] = list(layer.get("columns") or ["object_code", "name"])
            rows: list[dict] = list(layer.get("rows") or [])
            contents_srs = int(layer.get("contents_srs_id", srs_id))
            geometry_columns_srs = int(layer.get("geometry_columns_srs_id", srs_id))
            le = bool(layer.get("little_endian_geom", little_endian_geom))

            col_sql = ", ".join(f'"{c}" TEXT' for c in columns)
            conn.execute(
                f'CREATE TABLE "{name}" (id INTEGER PRIMARY KEY AUTOINCREMENT, '
                f'"{geom_col}" {geom_sql_type}, {col_sql})'
            )
            conn.execute(
                """
                INSERT INTO gpkg_contents
                (table_name, data_type, identifier, description, last_change, srs_id)
                VALUES (?, 'features', ?, 'synthetic', '2026-08-01T00:00:00Z', ?)
                """,
                (name, name, contents_srs),
            )
            if not layer.get("omit_geometry_columns", False):
                conn.execute(
                    """
                    INSERT INTO gpkg_geometry_columns
                    (table_name, column_name, geometry_type_name, srs_id, z, m)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        name,
                        layer.get("geometry_columns_column", geom_col),
                        geom_type,
                        geometry_columns_srs,
                        z,
                        m,
                    ),
                )
            for row in rows:
                values = [row.get(c) for c in columns]
                geom = row.get(geom_col)
                if geom is None and layer.get("geometry_blob") is not None:
                    geom = layer["geometry_blob"]
                if geom is None:
                    # Synthetic defaults: geographic for 4326; UTM32N-like metres for 25832.
                    coordsys = int(
                        layer.get(
                            "organization_coordsys_id",
                            25832 if srs_id in {25832, 900001} else 4326,
                        )
                    )
                    if coordsys == 25832:
                        default_point = (463552.0, 5549380.0)
                        default_line = [
                            (463552.0, 5549380.0),
                            (463652.0, 5549480.0),
                        ]
                    else:
                        default_point = (8.0, 50.0)
                        default_line = [(8.0, 50.0), (8.1, 50.1)]
                    if layer.get("coords"):
                        # explicit override: list of (x,y) or single point
                        c = layer["coords"]
                        if geom_type.upper().startswith("POINT"):
                            default_point = (float(c[0]), float(c[1]))
                        else:
                            default_line = [(float(a), float(b)) for a, b in c]
                    if geom_type.upper().startswith("POINT"):
                        geom = _gp_header(
                            srs_id,
                            _wkb_point_xy(*default_point),
                            little_endian=le,
                        )
                    else:
                        geom = _gp_header(
                            srs_id,
                            _wkb_linestring_xy(default_line),
                            little_endian=le,
                        )
                placeholders = ", ".join("?" for _ in range(len(columns) + 1))
                col_list = ", ".join(f'"{c}"' for c in columns)
                conn.execute(
                    f'INSERT INTO "{name}" ("{geom_col}", {col_list}) '
                    f"VALUES ({placeholders})",
                    [geom, *values],
                )

        for extra in extra_contents or []:
            conn.execute(
                """
                INSERT INTO gpkg_contents
                (table_name, data_type, identifier, description, last_change, srs_id)
                VALUES (?, ?, ?, 'synthetic', '2026-08-01T00:00:00Z', ?)
                """,
                (
                    extra["table_name"],
                    extra.get("data_type", "tiles"),
                    extra.get("identifier", extra["table_name"]),
                    extra.get("srs_id", 4326),
                ),
            )

        conn.commit()
    finally:
        conn.close()
    return path


def create_valid_pipe_routes_gpkg(
    path: Path,
    *,
    srs_id: int = 4326,
    organization: str = "EPSG",
    organization_coordsys_id: int = 4326,
    definition: str = "SYNTHETIC",
    extra_pii_column: bool = False,
    feature_count: int = 1,
    geometry_type: str = "LINESTRING",
    z: int = 0,
    m: int = 0,
    missing_required: bool = False,
    little_endian_geom: bool = True,
    geometry_blob: bytes | None = None,
    coords: list | tuple | None = None,
) -> Path:
    columns = [] if missing_required else ["object_code", "name", "expected_pipe_count"]
    if extra_pii_column:
        columns = columns + ["phone", "email"]
    rows: list[dict] = []
    for i in range(feature_count):
        row: dict = {
            "object_code": f"PIPE-{i + 1:03d}" if not missing_required else None,
            "name": f"Sample segment {i + 1}" if not missing_required else None,
            "expected_pipe_count": "4",
        }
        if extra_pii_column:
            row["phone"] = "SHOULD_NOT_BE_READ"
            row["email"] = "SHOULD_NOT_BE_READ"
        rows.append(row)

    layer: dict = {
        "name": "pipe_routes",
        "geometry_type": geometry_type,
        "srs_id": srs_id,
        "organization_coordsys_id": organization_coordsys_id,
        "columns": columns or ["placeholder"],
        "rows": rows if columns else [],
        "z": z,
        "m": m,
        "little_endian_geom": little_endian_geom,
    }
    if geometry_blob is not None:
        layer["geometry_blob"] = geometry_blob
    if coords is not None:
        layer["coords"] = coords

    create_minimal_gpkg(path, feature_layers=[layer])
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            """
            INSERT OR REPLACE INTO gpkg_spatial_ref_sys
            (srs_name, srs_id, organization, organization_coordsys_id, definition, description)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                f"SRS-{srs_id}",
                srs_id,
                organization,
                organization_coordsys_id,
                definition,
                "synthetic",
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return path


__all__ = [
    "GPKG_APPLICATION_ID",
    "build_gp_binary",
    "create_minimal_gpkg",
    "create_valid_pipe_routes_gpkg",
]
