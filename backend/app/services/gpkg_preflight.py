"""Read-only standard GeoPackage metadata preflight (P1-1 / P1-1.1).

Does NOT write the database, copy files, decode full WKB, or import features.
Legacy derivative import remains in ``design_package.import_gpkg_derivative``.

GeoPackageBinary flag layout (OGC 12-128r15):
  bit 0     B  — header numeric byte order (0=BE, 1=LE)
  bits 1–3  E  — envelope type (0–4 valid; 5–7 invalid)
  bit 4     Y  — empty geometry
  bit 5     X  — 0=StandardGeoPackageBinary, 1=Extended
  bits 6–7     — reserved, must be 0

This module only validates limited header bytes; full WKB decode is P1-2+.
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
import stat
import struct
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

IMPORT_CONTRACT_VERSION = "gpkg-import-contract-v0.1.1"

# OGC GeoPackage application_id ("GPKG" as big-endian uint32)
GPKG_APPLICATION_ID = 0x47504B47

# FILE_ATTRIBUTE_REPARSE_POINT (Windows)
_FILE_ATTRIBUTE_REPARSE_POINT = 0x400

LAYER_WHITELIST: dict[str, str] = {
    "pipe_routes": "pipe_route",
    "trenches": "trench",
    "infrastructure_points": "infrastructure_point",
}

ALLOWED_GEOMETRY: dict[str, frozenset[str]] = {
    "pipe_routes": frozenset({"LINESTRING"}),
    "trenches": frozenset({"POLYGON", "LINESTRING"}),
    "infrastructure_points": frozenset({"POINT"}),
}

ALLOWED_EPSG = frozenset({4326, 25832})

REQUIRED_FIELDS = frozenset({"object_code", "name"})
OPTIONAL_FIELDS = frozenset(
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
)
ALLOWED_FIELDS = REQUIRED_FIELDS | OPTIONAL_FIELDS

# SQLite declared types acceptable for a geometry BLOB column (case-folded).
_GEOM_SQLITE_TYPES = frozenset(
    {
        "",
        "BLOB",
        "GEOMETRY",
        "POINT",
        "LINESTRING",
        "POLYGON",
        "MULTIPOINT",
        "MULTILINESTRING",
        "MULTIPOLYGON",
        "GEOMETRYCOLLECTION",
    }
)
_GEOM_SQLITE_CONFLICT = frozenset(
    {"TEXT", "INTEGER", "INT", "REAL", "FLOAT", "DOUBLE", "NUMERIC", "BOOLEAN", "BOOL"}
)

# Envelope indicator E → number of 8-byte doubles in the header envelope block.
_ENVELOPE_DOUBLE_COUNTS: dict[int, int] = {
    0: 0,
    1: 4,  # minx, maxx, miny, maxy
    2: 6,  # + minz, maxz
    3: 6,  # + minm, maxm
    4: 8,  # + minz, maxz, minm, maxm
}

PII_NAME_PATTERNS = (
    re.compile(r"phone", re.I),
    re.compile(r"telephone", re.I),
    re.compile(r"email", re.I),
    re.compile(r"address", re.I),
    re.compile(r"person", re.I),
    re.compile(r"owner", re.I),
    re.compile(r"contact", re.I),
    re.compile(r"attachment", re.I),
    re.compile(r"photo_path", re.I),
    re.compile(r"external_path", re.I),
)

_SAFE_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_UNDEFINED_DEF = re.compile(r"^(undefined|undefinedcrs|unknown)$", re.I)


@dataclass(frozen=True, slots=True)
class GpkgPreflightPolicy:
    max_file_bytes: int = 32 * 1024 * 1024
    max_contents_rows: int = 64
    max_features_per_layer: int = 50_000
    max_features_total: int = 100_000
    max_fields_per_layer: int = 64
    sample_geometry_blobs: int = 3
    # PRAGMA quick_check(N) caps the number of error rows returned, not CPU time.
    # Enabled only under max_file_bytes so worst-case work is size-bounded.
    run_quick_check: bool = True


@dataclass(slots=True)
class GpkgLayerReport:
    name: str
    data_type: str | None = None
    accepted: bool = False
    rejection_reasons: list[str] = field(default_factory=list)
    geometry_column: str | None = None
    geometry_type: str | None = None
    z: int | None = None
    m: int | None = None
    source_srs_id: int | None = None
    organization: str | None = None
    organization_coordsys_id: int | None = None
    resolved_epsg: int | None = None
    feature_count: int | None = None
    fields: list[str] = field(default_factory=list)
    missing_required_fields: list[str] = field(default_factory=list)
    allowed_fields: list[str] = field(default_factory=list)
    dropped_fields: list[str] = field(default_factory=list)
    whitelisted: bool = False
    object_type: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class GpkgPreflightReport:
    valid: bool
    source_sha256: str
    size_bytes: int
    application_id: int | None
    user_version: int | None
    detected_spec_version: str | None
    import_contract_version: str
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    layers: list[GpkgLayerReport] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "source_sha256": self.source_sha256,
            "size_bytes": self.size_bytes,
            "application_id": self.application_id,
            "user_version": self.user_version,
            "detected_spec_version": self.detected_spec_version,
            "import_contract_version": self.import_contract_version,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
            "layers": [layer.to_dict() for layer in self.layers],
        }


def _fail_report(
    *,
    errors: list[str],
    digest: str = "",
    size_bytes: int = 0,
    application_id: int | None = None,
    user_version: int | None = None,
    detected_spec: str | None = None,
    warnings: list[str] | None = None,
    layers: list[GpkgLayerReport] | None = None,
) -> GpkgPreflightReport:
    return GpkgPreflightReport(
        valid=False,
        source_sha256=digest,
        size_bytes=size_bytes,
        application_id=application_id,
        user_version=user_version,
        detected_spec_version=detected_spec,
        import_contract_version=IMPORT_CONTRACT_VERSION,
        errors=list(errors),
        warnings=list(warnings or []),
        layers=list(layers or []),
    )


def _stream_sha256(path: Path, *, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _file_identity(st: Any) -> tuple[int, int, int, int]:
    mtime_ns = int(getattr(st, "st_mtime_ns", int(st.st_mtime * 1_000_000_000)))
    return (
        int(st.st_size),
        mtime_ns,
        int(getattr(st, "st_dev", 0) or 0),
        int(getattr(st, "st_ino", 0) or 0),
    )


def _is_symlink_or_reparse(path: Path, st: Any) -> bool:
    if stat.S_ISLNK(st.st_mode):
        return True
    try:
        if path.is_symlink():
            return True
    except OSError:
        pass
    attrs = int(getattr(st, "st_file_attributes", 0) or 0)
    if attrs & _FILE_ATTRIBUTE_REPARSE_POINT:
        return True
    return False


def _is_safe_ident(name: str) -> bool:
    return bool(_SAFE_IDENT.fullmatch(name))


def _is_pii_field(name: str) -> bool:
    return any(p.search(name) for p in PII_NAME_PATTERNS)


def _normalize_geometry_type(raw: str | None) -> str | None:
    if raw is None:
        return None
    text = str(raw).strip().upper().replace(" ", "")
    for suffix in ("ZM", "Z", "M"):
        if text.endswith(suffix) and len(text) > len(suffix):
            text = text[: -len(suffix)]
    return text or None


def _resolve_epsg(
    organization: str | None,
    organization_coordsys_id: Any,
    definition: str | None,
) -> tuple[int | None, list[str]]:
    """Resolve EPSG from gpkg_spatial_ref_sys fields — never from internal srs_id."""
    errors: list[str] = []
    org = (organization or "").strip().upper()
    if org != "EPSG":
        errors.append("crs_organization_not_epsg")
        return None, errors
    try:
        epsg = int(organization_coordsys_id)
    except (TypeError, ValueError):
        errors.append("crs_organization_coordsys_id_invalid")
        return None, errors
    if epsg not in ALLOWED_EPSG:
        errors.append("crs_epsg_not_allowed")
        return None, errors
    if definition is None:
        errors.append("crs_definition_missing")
        return None, errors
    def_text = str(definition).strip()
    if def_text == "":
        errors.append("crs_definition_empty")
        return None, errors
    if _UNDEFINED_DEF.fullmatch(def_text):
        errors.append("crs_definition_undefined")
        return None, errors
    return epsg, errors


def envelope_byte_length(envelope_indicator: int) -> int | None:
    """Return envelope payload length for E, or None if E is illegal (5–7)."""
    if envelope_indicator not in _ENVELOPE_DOUBLE_COUNTS:
        return None
    return _ENVELOPE_DOUBLE_COUNTS[envelope_indicator] * 8


def inspect_gp_header(blob: bytes) -> list[str]:
    """Limited GeoPackageBinary header checks — not a full geometry / WKB decoder.

    Validates magic, version, flags (B/E/Y/X/reserved), and that the blob is long
    enough for the declared envelope. Does not decode WKB coordinates.
    """
    reasons: list[str] = []
    if len(blob) < 8:
        reasons.append("geometry_blob_too_short")
        return reasons
    if blob[0:2] != b"GP":
        reasons.append("geometry_blob_not_gp_magic")
        return reasons
    version = blob[2]
    if version != 0:
        reasons.append("geometry_blob_unsupported_version")
    flags = blob[3]
    # bit 0: B (byte order for header numerics)
    little_endian = bool(flags & 0x01)
    # bits 1–3: E (envelope type)
    envelope_indicator = (flags >> 1) & 0x07
    # bit 4: Y empty
    empty = bool(flags & 0x10)
    # bit 5: X extended
    extended = bool(flags & 0x20)
    # bits 6–7: reserved
    reserved = (flags >> 6) & 0x03

    if reserved != 0:
        reasons.append("geometry_blob_reserved_bits")
    if extended:
        reasons.append("geometry_blob_extended")
    if empty:
        reasons.append("geometry_blob_empty_flag")
    if envelope_indicator >= 5:
        reasons.append("geometry_blob_illegal_envelope")
        return reasons

    env_len = envelope_byte_length(envelope_indicator)
    if env_len is None:
        reasons.append("geometry_blob_illegal_envelope")
        return reasons
    min_header = 8 + env_len
    if len(blob) < min_header:
        reasons.append("geometry_blob_truncated_header")
        return reasons

    # Verify srs_id int32 is readable with declared endianness (value unused for EPSG).
    try:
        endian = "<" if little_endian else ">"
        struct.unpack_from(f"{endian}i", blob, 4)
    except struct.error:
        reasons.append("geometry_blob_srs_id_unreadable")

    return reasons


def _stream_sha256_with_identity(
    path: Path, st_before: Any
) -> tuple[str | None, list[str]]:
    """Hash file and ensure size/mtime/identity did not change during the read."""
    try:
        digest = _stream_sha256(path)
    except OSError:
        return None, ["file_unreadable"]
    try:
        st_after = path.lstat()
    except OSError:
        return None, ["file_unreadable"]
    if _is_symlink_or_reparse(path, st_after):
        return None, ["file_is_symlink_or_reparse"]
    if not stat.S_ISREG(st_after.st_mode):
        return None, ["file_not_regular"]
    if _file_identity(st_before) != _file_identity(st_after):
        return None, ["file_changed_during_hash"]
    return digest, []


def inspect_standard_gpkg(
    path: Path,
    policy: GpkgPreflightPolicy | None = None,
) -> GpkgPreflightReport:
    """Inspect a file path as a candidate standard GeoPackage (read-only)."""
    policy = policy or GpkgPreflightPolicy()
    errors: list[str] = []
    warnings: list[str] = []
    layers: list[GpkgLayerReport] = []
    application_id: int | None = None
    user_version: int | None = None
    detected_spec: str | None = None
    digest = ""
    size_bytes = 0

    # Path safety: inspect the given path without following symlinks.
    try:
        open_path = Path(path).absolute()
    except OSError:
        return _fail_report(errors=["file_unreadable"])

    try:
        st0 = open_path.lstat()
    except FileNotFoundError:
        return _fail_report(errors=["file_not_found"])
    except OSError:
        return _fail_report(errors=["file_unreadable"])

    if _is_symlink_or_reparse(open_path, st0):
        return _fail_report(
            errors=["file_is_symlink_or_reparse"],
            size_bytes=int(st0.st_size),
        )

    if not stat.S_ISREG(st0.st_mode):
        return _fail_report(
            errors=["file_not_regular"],
            size_bytes=int(st0.st_size),
        )

    size_bytes = int(st0.st_size)
    if size_bytes <= 0:
        errors.append("file_empty")
    if size_bytes > policy.max_file_bytes:
        errors.append("file_too_large")

    if errors:
        return _fail_report(errors=errors, size_bytes=size_bytes)

    digest_or_none, hash_errors = _stream_sha256_with_identity(open_path, st0)
    if hash_errors:
        return _fail_report(
            errors=hash_errors,
            size_bytes=size_bytes,
            digest=digest_or_none or "",
        )
    digest = digest_or_none or ""

    # Re-check identity immediately before opening SQLite.
    try:
        st1 = open_path.lstat()
    except OSError:
        return _fail_report(
            errors=["file_unreadable"],
            digest=digest,
            size_bytes=size_bytes,
        )
    if _is_symlink_or_reparse(open_path, st1) or not stat.S_ISREG(st1.st_mode):
        return _fail_report(
            errors=["file_changed_before_open"],
            digest=digest,
            size_bytes=size_bytes,
        )
    if _file_identity(st0) != _file_identity(st1):
        return _fail_report(
            errors=["file_changed_before_open"],
            digest=digest,
            size_bytes=size_bytes,
        )

    uri = f"file:{open_path.as_posix()}?mode=ro"
    conn: sqlite3.Connection | None = None
    try:
        conn = sqlite3.connect(uri, uri=True)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON")
        try:
            conn.enable_load_extension(False)
        except AttributeError:
            pass
        except sqlite3.Error:
            pass

        try:
            application_id = int(conn.execute("PRAGMA application_id").fetchone()[0])
            user_version = int(conn.execute("PRAGMA user_version").fetchone()[0])
        except (TypeError, ValueError, sqlite3.Error):
            errors.append("pragma_read_failed")
            application_id = None
            user_version = None

        if application_id is not None and application_id != GPKG_APPLICATION_ID:
            errors.append("application_id_not_gpkg")

        if user_version is not None and user_version > 0:
            major = user_version // 10000
            minor = (user_version // 100) % 100
            detected_spec = f"{major}.{minor}"
        else:
            detected_spec = None
            if application_id == GPKG_APPLICATION_ID:
                warnings.append("user_version_unspecified")

        if policy.run_quick_check:
            try:
                # quick_check(N) limits how many integrity *errors* are returned.
                # It does not guarantee fixed runtime; cost is constrained here by
                # max_file_bytes on the candidate file. Disable with run_quick_check=False.
                row = conn.execute("PRAGMA quick_check(1)").fetchone()
                if row and str(row[0]).lower() != "ok":
                    errors.append("sqlite_quick_check_failed")
            except sqlite3.Error:
                warnings.append("sqlite_quick_check_unavailable")

        table_names = {
            str(r[0])
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        for required in (
            "gpkg_spatial_ref_sys",
            "gpkg_contents",
            "gpkg_geometry_columns",
        ):
            if required not in table_names:
                errors.append(f"missing_table_{required}")

        if "missing_table_gpkg_contents" in errors:
            return _fail_report(
                errors=errors,
                digest=digest,
                size_bytes=size_bytes,
                application_id=application_id,
                user_version=user_version,
                detected_spec=detected_spec,
                warnings=warnings,
            )

        contents = conn.execute(
            "SELECT table_name, data_type, srs_id FROM gpkg_contents"
        ).fetchall()
        if len(contents) > policy.max_contents_rows:
            errors.append("too_many_content_rows")

        feature_layers = [
            row for row in contents if str(row["data_type"]).lower() == "features"
        ]
        if not feature_layers and not any(e.startswith("missing_table_") for e in errors):
            errors.append("no_feature_layers")

        srs_map: dict[int, sqlite3.Row] = {}
        if "gpkg_spatial_ref_sys" in table_names:
            for row in conn.execute(
                "SELECT srs_id, organization, organization_coordsys_id, definition "
                "FROM gpkg_spatial_ref_sys"
            ):
                try:
                    srs_map[int(row["srs_id"])] = row
                except (TypeError, ValueError):
                    continue

        geom_map: dict[str, sqlite3.Row] = {}
        if "gpkg_geometry_columns" in table_names:
            for row in conn.execute(
                "SELECT table_name, column_name, geometry_type_name, srs_id, z, m "
                "FROM gpkg_geometry_columns"
            ):
                geom_map[str(row["table_name"])] = row

        total_features = 0
        accepted_epsgs: set[int] = set()

        for row in contents:
            layer_name = str(row["table_name"])
            data_type = str(row["data_type"]) if row["data_type"] is not None else None
            report = GpkgLayerReport(name=layer_name, data_type=data_type)
            try:
                report.source_srs_id = (
                    int(row["srs_id"]) if row["srs_id"] is not None else None
                )
            except (TypeError, ValueError):
                report.source_srs_id = None
                report.rejection_reasons.append("contents_srs_id_invalid")

            if data_type is not None and data_type.lower() != "features":
                report.rejection_reasons.append("data_type_not_features")
                layers.append(report)
                continue

            if layer_name not in LAYER_WHITELIST:
                report.rejection_reasons.append("layer_not_whitelisted")
                layers.append(report)
                continue

            if not _is_safe_ident(layer_name):
                report.rejection_reasons.append("layer_name_unsafe")
                layers.append(report)
                continue

            report.whitelisted = True
            report.object_type = LAYER_WHITELIST[layer_name]

            if layer_name not in table_names:
                report.rejection_reasons.append("feature_table_missing")
                layers.append(report)
                continue

            geom = geom_map.get(layer_name)
            if geom is None:
                report.rejection_reasons.append("missing_geometry_columns_row")
                layers.append(report)
                continue

            report.geometry_column = str(geom["column_name"])
            report.geometry_type = _normalize_geometry_type(
                str(geom["geometry_type_name"])
                if geom["geometry_type_name"] is not None
                else None
            )
            try:
                report.z = int(geom["z"])
            except (TypeError, ValueError):
                report.z = None
                report.rejection_reasons.append("geometry_z_invalid")
            try:
                report.m = int(geom["m"])
            except (TypeError, ValueError):
                report.m = None
                report.rejection_reasons.append("geometry_m_invalid")

            if report.z is not None and report.z != 0:
                report.rejection_reasons.append("geometry_has_z")
            if report.m is not None and report.m != 0:
                report.rejection_reasons.append("geometry_has_m")

            allowed_geoms = ALLOWED_GEOMETRY[layer_name]
            if report.geometry_type is None:
                report.rejection_reasons.append("geometry_type_unknown")
            elif report.geometry_type not in allowed_geoms:
                report.rejection_reasons.append("geometry_type_unsupported")

            # contents.srs_id must match geometry_columns.srs_id (package-internal keys).
            try:
                geom_srs = int(geom["srs_id"]) if geom["srs_id"] is not None else None
            except (TypeError, ValueError):
                geom_srs = None
                report.rejection_reasons.append("geometry_columns_srs_id_invalid")
            if (
                report.source_srs_id is not None
                and geom_srs is not None
                and report.source_srs_id != geom_srs
            ):
                report.rejection_reasons.append("contents_geometry_srs_mismatch")

            # CRS via gpkg_spatial_ref_sys — never treat srs_id as EPSG.
            srs_row = (
                srs_map.get(report.source_srs_id)
                if report.source_srs_id is not None
                else None
            )
            if srs_row is None:
                report.rejection_reasons.append("srs_not_found_in_spatial_ref_sys")
            else:
                report.organization = (
                    str(srs_row["organization"])
                    if srs_row["organization"] is not None
                    else None
                )
                try:
                    report.organization_coordsys_id = (
                        int(srs_row["organization_coordsys_id"])
                        if srs_row["organization_coordsys_id"] is not None
                        else None
                    )
                except (TypeError, ValueError):
                    report.organization_coordsys_id = None
                definition = (
                    str(srs_row["definition"])
                    if srs_row["definition"] is not None
                    else None
                )
                # Preserve null as None for missing definition
                if srs_row["definition"] is None:
                    definition = None
                epsg, crs_errors = _resolve_epsg(
                    report.organization,
                    report.organization_coordsys_id,
                    definition,
                )
                report.resolved_epsg = epsg
                report.rejection_reasons.extend(crs_errors)

            # Columns / fields (names only)
            col_type_by_name: dict[str, str] = {}
            try:
                cols = conn.execute(f'PRAGMA table_info("{layer_name}")').fetchall()
            except sqlite3.Error:
                report.rejection_reasons.append("table_info_failed")
                cols = []
            field_names = [str(c["name"]) for c in cols]
            for c in cols:
                col_type_by_name[str(c["name"])] = str(c["type"] or "").strip().upper()
            report.fields = field_names
            if len(field_names) > policy.max_fields_per_layer:
                report.rejection_reasons.append("too_many_fields")

            if report.geometry_column and report.geometry_column not in field_names:
                report.rejection_reasons.append("geometry_column_missing_from_table")
            elif report.geometry_column:
                declared = col_type_by_name.get(report.geometry_column, "")
                if declared in _GEOM_SQLITE_CONFLICT or (
                    declared and declared not in _GEOM_SQLITE_TYPES
                ):
                    # Unknown types that are clearly non-BLOB storage are rejected.
                    if declared in _GEOM_SQLITE_CONFLICT:
                        report.rejection_reasons.append("geometry_column_type_not_blob")

            missing = sorted(REQUIRED_FIELDS - set(field_names))
            report.missing_required_fields = missing
            if missing:
                report.rejection_reasons.append("missing_required_fields")

            allowed_present = [n for n in field_names if n in ALLOWED_FIELDS]
            report.allowed_fields = allowed_present
            dropped: list[str] = []
            for name in field_names:
                if name == report.geometry_column:
                    continue
                if name in ALLOWED_FIELDS:
                    continue
                dropped.append(name)
            report.dropped_fields = dropped

            # Feature count
            try:
                count = int(
                    conn.execute(f'SELECT COUNT(*) FROM "{layer_name}"').fetchone()[0]
                )
            except (TypeError, ValueError, sqlite3.Error):
                count = None
                report.rejection_reasons.append("feature_count_failed")
            report.feature_count = count
            if count is not None:
                if count == 0:
                    # Whitelist target layers with zero features are not importable.
                    report.rejection_reasons.append("empty_whitelisted_layer")
                if count > policy.max_features_per_layer:
                    report.rejection_reasons.append("too_many_features_layer")
                total_features += count

            # Sample geometry BLOBs for limited header validation (not full WKB).
            if (
                report.geometry_column
                and _is_safe_ident(report.geometry_column)
                and count
                and count > 0
                and policy.sample_geometry_blobs > 0
            ):
                try:
                    samples = conn.execute(
                        f'SELECT "{report.geometry_column}" AS g FROM "{layer_name}" '
                        f'WHERE "{report.geometry_column}" IS NOT NULL '
                        f"LIMIT {int(policy.sample_geometry_blobs)}"
                    ).fetchall()
                except sqlite3.Error:
                    report.rejection_reasons.append("geometry_sample_query_failed")
                    samples = []
                else:
                    if not samples:
                        report.rejection_reasons.append("geometry_samples_all_null")
                    for sample in samples:
                        blob = sample["g"]
                        if isinstance(blob, memoryview):
                            blob = blob.tobytes()
                        if not isinstance(blob, (bytes, bytearray)):
                            report.rejection_reasons.append("geometry_blob_not_bytes")
                            break
                        header_issues = inspect_gp_header(bytes(blob))
                        for issue in header_issues:
                            if issue not in report.rejection_reasons:
                                report.rejection_reasons.append(issue)

            if not report.rejection_reasons:
                report.accepted = True
                if report.resolved_epsg is not None:
                    accepted_epsgs.add(report.resolved_epsg)

            layers.append(report)

        if total_features > policy.max_features_total:
            errors.append("too_many_features_total")

        accepted_layers = [layer for layer in layers if layer.accepted]
        whitelist_layers = [layer for layer in layers if layer.whitelisted]

        if whitelist_layers and not accepted_layers and "no_feature_layers" not in errors:
            all_empty = bool(whitelist_layers) and all(
                layer.feature_count == 0 for layer in whitelist_layers
            )
            if all_empty:
                errors.append("all_whitelisted_layers_empty")
            else:
                errors.append("no_accepted_whitelisted_layers")

        if len(accepted_epsgs) > 1:
            errors.append("mixed_crs_among_accepted_layers")

    except sqlite3.Error:
        errors.append("sqlite_open_or_query_failed")
    finally:
        if conn is not None:
            try:
                conn.close()
            except sqlite3.Error:
                pass

    deduped: list[str] = []
    for item in errors:
        if item not in deduped:
            deduped.append(item)

    valid = len(deduped) == 0 and any(layer.accepted for layer in layers)
    return GpkgPreflightReport(
        valid=valid,
        source_sha256=digest,
        size_bytes=size_bytes,
        application_id=application_id,
        user_version=user_version,
        detected_spec_version=detected_spec,
        import_contract_version=IMPORT_CONTRACT_VERSION,
        errors=deduped,
        warnings=warnings,
        layers=layers,
    )


__all__ = [
    "ALLOWED_EPSG",
    "ALLOWED_FIELDS",
    "ALLOWED_GEOMETRY",
    "GPKG_APPLICATION_ID",
    "GpkgLayerReport",
    "GpkgPreflightPolicy",
    "GpkgPreflightReport",
    "IMPORT_CONTRACT_VERSION",
    "LAYER_WHITELIST",
    "REQUIRED_FIELDS",
    "envelope_byte_length",
    "inspect_gp_header",
    "inspect_standard_gpkg",
]
