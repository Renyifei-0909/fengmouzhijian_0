"""P1-1 / P1-1.1: standard GeoPackage read-only preflight (synthetic fixtures only)."""

from __future__ import annotations

import os
import sqlite3
import struct
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from app.services.gpkg_preflight import (
    GPKG_APPLICATION_ID,
    GpkgPreflightPolicy,
    IMPORT_CONTRACT_VERSION,
    inspect_gp_header,
    inspect_standard_gpkg,
)
from tests.gpkg_fixture_factory import (
    build_gp_binary,
    create_minimal_gpkg,
    create_valid_pipe_routes_gpkg,
    _wkb_linestring_xy,
    _wkb_point_xy,
)


@pytest.fixture()
def gpkg_dir(tmp_path: Path) -> Path:
    return tmp_path / "gpkg"


def test_valid_synthetic_gpkg_metadata_passes(gpkg_dir: Path) -> None:
    path = create_valid_pipe_routes_gpkg(gpkg_dir / "ok.gpkg")
    report = inspect_standard_gpkg(path)
    assert report.valid is True
    assert report.application_id == GPKG_APPLICATION_ID
    assert report.import_contract_version == IMPORT_CONTRACT_VERSION
    assert report.source_sha256
    assert len(report.source_sha256) == 64
    accepted = [layer for layer in report.layers if layer.accepted]
    assert len(accepted) == 1
    assert accepted[0].name == "pipe_routes"
    assert accepted[0].resolved_epsg == 4326
    assert accepted[0].object_type == "pipe_route"


def test_epsg_4326_and_25832(gpkg_dir: Path) -> None:
    p4326 = create_valid_pipe_routes_gpkg(
        gpkg_dir / "4326.gpkg",
        srs_id=4326,
        organization_coordsys_id=4326,
    )
    p25832 = create_valid_pipe_routes_gpkg(
        gpkg_dir / "25832.gpkg",
        srs_id=25832,
        organization_coordsys_id=25832,
    )
    r1 = inspect_standard_gpkg(p4326)
    r2 = inspect_standard_gpkg(p25832)
    assert r1.valid and r1.layers[0].resolved_epsg == 4326
    assert r2.valid and r2.layers[0].resolved_epsg == 25832


def test_srs_id_differs_from_epsg_but_organization_coordsys_ok(gpkg_dir: Path) -> None:
    path = create_valid_pipe_routes_gpkg(
        gpkg_dir / "srs-map.gpkg",
        srs_id=900001,
        organization="EPSG",
        organization_coordsys_id=25832,
    )
    report = inspect_standard_gpkg(path)
    assert report.valid is True
    layer = next(layer for layer in report.layers if layer.name == "pipe_routes")
    assert layer.source_srs_id == 900001
    assert layer.resolved_epsg == 25832


def test_wrong_application_id(gpkg_dir: Path) -> None:
    path = create_minimal_gpkg(
        gpkg_dir / "bad-appid.gpkg",
        application_id=0x12345678,
        feature_layers=[
            {
                "name": "pipe_routes",
                "geometry_type": "LINESTRING",
                "srs_id": 4326,
                "columns": ["object_code", "name"],
                "rows": [{"object_code": "PIPE-001", "name": "A"}],
            }
        ],
    )
    report = inspect_standard_gpkg(path)
    assert report.valid is False
    assert "application_id_not_gpkg" in report.errors


def test_missing_system_tables(gpkg_dir: Path) -> None:
    path = gpkg_dir / "no-sys.gpkg"
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        conn.execute(f"PRAGMA application_id = {GPKG_APPLICATION_ID}")
        conn.execute("CREATE TABLE dummy (id INTEGER)")
        conn.commit()
    finally:
        conn.close()
    report = inspect_standard_gpkg(path)
    assert report.valid is False
    assert any(e.startswith("missing_table_") for e in report.errors)


def test_tiles_only_no_feature_layers(gpkg_dir: Path) -> None:
    path = create_minimal_gpkg(
        gpkg_dir / "tiles.gpkg",
        feature_layers=[],
        extra_contents=[{"table_name": "basemap", "data_type": "tiles", "srs_id": 4326}],
    )
    report = inspect_standard_gpkg(path)
    assert report.valid is False
    assert "no_feature_layers" in report.errors


def test_non_whitelist_layer_reported_not_accepted(gpkg_dir: Path) -> None:
    path = create_minimal_gpkg(
        gpkg_dir / "extra.gpkg",
        feature_layers=[
            {
                "name": "pipe_routes",
                "geometry_type": "LINESTRING",
                "srs_id": 4326,
                "columns": ["object_code", "name"],
                "rows": [{"object_code": "PIPE-001", "name": "A"}],
            },
            {
                "name": "random_layer",
                "geometry_type": "POINT",
                "srs_id": 4326,
                "columns": ["object_code", "name"],
                "rows": [{"object_code": "X", "name": "Y"}],
            },
        ],
    )
    report = inspect_standard_gpkg(path)
    random = next(layer for layer in report.layers if layer.name == "random_layer")
    assert random.accepted is False
    assert "layer_not_whitelisted" in random.rejection_reasons
    assert report.valid is True


def test_missing_geometry_columns(gpkg_dir: Path) -> None:
    path = create_valid_pipe_routes_gpkg(gpkg_dir / "no-geom-meta.gpkg")
    conn = sqlite3.connect(path)
    try:
        conn.execute("DELETE FROM gpkg_geometry_columns")
        conn.commit()
    finally:
        conn.close()
    report = inspect_standard_gpkg(path)
    layer = next(layer for layer in report.layers if layer.name == "pipe_routes")
    assert layer.accepted is False
    assert "missing_geometry_columns_row" in layer.rejection_reasons


def test_unknown_crs(gpkg_dir: Path) -> None:
    path = create_valid_pipe_routes_gpkg(
        gpkg_dir / "unknown-crs.gpkg",
        srs_id=999999,
        organization="LOCAL",
        organization_coordsys_id=1,
        definition="LOCAL-CRS",
    )
    report = inspect_standard_gpkg(path)
    layer = next(layer for layer in report.layers if layer.name == "pipe_routes")
    assert layer.accepted is False
    assert "crs_organization_not_epsg" in layer.rejection_reasons


def test_mixed_crs_among_accepted_layers(gpkg_dir: Path) -> None:
    path = create_minimal_gpkg(
        gpkg_dir / "mixed.gpkg",
        feature_layers=[
            {
                "name": "pipe_routes",
                "geometry_type": "LINESTRING",
                "srs_id": 4326,
                "columns": ["object_code", "name"],
                "rows": [{"object_code": "P1", "name": "A"}],
            },
            {
                "name": "infrastructure_points",
                "geometry_type": "POINT",
                "srs_id": 25832,
                "columns": ["object_code", "name"],
                "rows": [{"object_code": "I1", "name": "B"}],
            },
        ],
    )
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            """
            INSERT OR REPLACE INTO gpkg_spatial_ref_sys
            (srs_name, srs_id, organization, organization_coordsys_id, definition, description)
            VALUES ('UTM', 25832, 'EPSG', 25832, 'SYNTH', 'synthetic')
            """
        )
        conn.commit()
    finally:
        conn.close()
    report = inspect_standard_gpkg(path)
    assert report.valid is False
    assert "mixed_crs_among_accepted_layers" in report.errors


def test_unsupported_geometry_type(gpkg_dir: Path) -> None:
    path = create_valid_pipe_routes_gpkg(
        gpkg_dir / "multi.gpkg",
        geometry_type="MULTILINESTRING",
    )
    report = inspect_standard_gpkg(path)
    layer = next(layer for layer in report.layers if layer.name == "pipe_routes")
    assert layer.accepted is False
    assert "geometry_type_unsupported" in layer.rejection_reasons


def test_zm_geometry_rejected(gpkg_dir: Path) -> None:
    path = create_valid_pipe_routes_gpkg(gpkg_dir / "z.gpkg", z=1, m=0)
    report = inspect_standard_gpkg(path)
    layer = next(layer for layer in report.layers if layer.name == "pipe_routes")
    assert layer.accepted is False
    assert "geometry_has_z" in layer.rejection_reasons


def test_missing_required_fields(gpkg_dir: Path) -> None:
    path = create_valid_pipe_routes_gpkg(gpkg_dir / "missing.gpkg", missing_required=True)
    report = inspect_standard_gpkg(path)
    layer = next(layer for layer in report.layers if layer.name == "pipe_routes")
    assert layer.accepted is False
    assert "missing_required_fields" in layer.rejection_reasons
    assert "object_code" in layer.missing_required_fields


def test_too_many_fields(gpkg_dir: Path) -> None:
    cols = ["object_code", "name"] + [f"f{i}" for i in range(70)]
    path = create_minimal_gpkg(
        gpkg_dir / "fields.gpkg",
        feature_layers=[
            {
                "name": "pipe_routes",
                "geometry_type": "LINESTRING",
                "srs_id": 4326,
                "columns": cols,
                "rows": [{c: "x" for c in cols} | {"object_code": "P1", "name": "N"}],
            }
        ],
    )
    report = inspect_standard_gpkg(
        path,
        policy=GpkgPreflightPolicy(max_fields_per_layer=64),
    )
    layer = next(layer for layer in report.layers if layer.name == "pipe_routes")
    assert "too_many_fields" in layer.rejection_reasons


def test_feature_count_limit(gpkg_dir: Path) -> None:
    path = create_valid_pipe_routes_gpkg(gpkg_dir / "many.gpkg", feature_count=5)
    report = inspect_standard_gpkg(
        path,
        policy=GpkgPreflightPolicy(max_features_per_layer=3),
    )
    layer = next(layer for layer in report.layers if layer.name == "pipe_routes")
    assert "too_many_features_layer" in layer.rejection_reasons
    assert report.valid is False


def test_pii_fields_dropped_without_reading_values(gpkg_dir: Path) -> None:
    path = create_valid_pipe_routes_gpkg(gpkg_dir / "pii.gpkg", extra_pii_column=True)
    report = inspect_standard_gpkg(path)
    layer = next(layer for layer in report.layers if layer.name == "pipe_routes")
    assert "phone" in layer.dropped_fields
    assert "email" in layer.dropped_fields
    blob = str(report.to_dict())
    assert "SHOULD_NOT_BE_READ" not in blob


def test_preflight_stable_sha256(gpkg_dir: Path) -> None:
    path = create_valid_pipe_routes_gpkg(gpkg_dir / "stable.gpkg")
    a = inspect_standard_gpkg(path)
    b = inspect_standard_gpkg(path)
    assert a.source_sha256 == b.source_sha256
    assert a.valid == b.valid


def test_preflight_does_not_change_business_database(gpkg_dir: Path, tmp_path: Path) -> None:
    db_path = tmp_path / "biz.db"
    engine = create_engine(f"sqlite:///{db_path}")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE marker (id INTEGER PRIMARY KEY, note TEXT)"))
        conn.execute(text("INSERT INTO marker (id, note) VALUES (1, 'before')"))
    path = create_valid_pipe_routes_gpkg(gpkg_dir / "biz.gpkg")
    inspect_standard_gpkg(path)
    with engine.connect() as conn:
        note = conn.execute(text("SELECT note FROM marker WHERE id = 1")).scalar_one()
        count = conn.execute(text("SELECT COUNT(*) FROM sqlite_master")).scalar_one()
    assert note == "before"
    assert count >= 1


def test_preflight_no_temp_file_residue(gpkg_dir: Path, tmp_path: Path) -> None:
    before = {p.name for p in tmp_path.iterdir()}
    path = create_valid_pipe_routes_gpkg(gpkg_dir / "tmp-check.gpkg")
    inspect_standard_gpkg(path)
    after = {p.name for p in tmp_path.iterdir()}
    extras = after - before
    assert extras == {"gpkg"} or extras.issubset({"gpkg"})
    gpkg_files = list((tmp_path / "gpkg").glob("*"))
    assert all(f.suffix == ".gpkg" for f in gpkg_files)


def test_file_too_large_policy(gpkg_dir: Path) -> None:
    path = create_valid_pipe_routes_gpkg(gpkg_dir / "size.gpkg")
    report = inspect_standard_gpkg(path, policy=GpkgPreflightPolicy(max_file_bytes=10))
    assert report.valid is False
    assert "file_too_large" in report.errors


# --- P1-1.1 header / metadata consistency ---


def test_inspect_gp_header_little_endian_ok() -> None:
    wkb = _wkb_linestring_xy([(0.0, 0.0), (1.0, 1.0)])
    blob = build_gp_binary(4326, wkb, little_endian=True)
    assert blob[3] & 0x01 == 0x01  # B=1
    assert struct.unpack_from("<i", blob, 4)[0] == 4326
    assert inspect_gp_header(blob) == []


def test_inspect_gp_header_big_endian_ok() -> None:
    wkb = _wkb_point_xy(1.0, 2.0)
    blob = build_gp_binary(25832, wkb, little_endian=False)
    assert blob[3] & 0x01 == 0  # B=0
    assert struct.unpack_from(">i", blob, 4)[0] == 25832
    assert inspect_gp_header(blob) == []


def test_little_endian_fixture_preflight_passes(gpkg_dir: Path) -> None:
    path = create_valid_pipe_routes_gpkg(
        gpkg_dir / "le.gpkg", little_endian_geom=True
    )
    report = inspect_standard_gpkg(path)
    assert report.valid is True


def test_big_endian_fixture_preflight_passes(gpkg_dir: Path) -> None:
    path = create_valid_pipe_routes_gpkg(
        gpkg_dir / "be.gpkg", little_endian_geom=False
    )
    report = inspect_standard_gpkg(path)
    assert report.valid is True


def test_extended_flag_rejected(gpkg_dir: Path) -> None:
    wkb = _wkb_linestring_xy([(0.0, 0.0), (1.0, 1.0)])
    blob = build_gp_binary(4326, wkb, extended=True)
    assert "geometry_blob_extended" in inspect_gp_header(blob)
    path = create_valid_pipe_routes_gpkg(
        gpkg_dir / "ext.gpkg", geometry_blob=blob
    )
    report = inspect_standard_gpkg(path)
    layer = next(layer for layer in report.layers if layer.name == "pipe_routes")
    assert "geometry_blob_extended" in layer.rejection_reasons
    assert report.valid is False


def test_reserved_bits_rejected() -> None:
    wkb = _wkb_point_xy(0.0, 0.0)
    blob = build_gp_binary(4326, wkb, reserved=1)
    assert "geometry_blob_reserved_bits" in inspect_gp_header(blob)
    blob2 = build_gp_binary(4326, wkb, reserved=2)
    assert "geometry_blob_reserved_bits" in inspect_gp_header(blob2)


def test_illegal_envelope_indicator_rejected() -> None:
    wkb = _wkb_point_xy(0.0, 0.0)
    for e in (5, 6, 7):
        blob = build_gp_binary(4326, wkb, envelope_indicator=e)
        assert "geometry_blob_illegal_envelope" in inspect_gp_header(blob)


def test_truncated_envelope_rejected() -> None:
    # E=1 expects 32 envelope bytes; supply none beyond 8-byte core header.
    flags = 0x01 | (1 << 1)  # LE + E=1
    blob = b"GP" + bytes([0, flags]) + struct.pack("<i", 4326)  # 8 bytes only
    assert "geometry_blob_truncated_header" in inspect_gp_header(blob)


def test_empty_geometry_flag_rejected(gpkg_dir: Path) -> None:
    blob = build_gp_binary(4326, b"", empty=True)
    assert "geometry_blob_empty_flag" in inspect_gp_header(blob)
    path = create_valid_pipe_routes_gpkg(
        gpkg_dir / "empty-geom.gpkg", geometry_blob=blob
    )
    report = inspect_standard_gpkg(path)
    layer = next(layer for layer in report.layers if layer.name == "pipe_routes")
    assert "geometry_blob_empty_flag" in layer.rejection_reasons


def test_unsupported_version_rejected() -> None:
    blob = build_gp_binary(4326, _wkb_point_xy(0.0, 0.0), version=1)
    assert "geometry_blob_unsupported_version" in inspect_gp_header(blob)


def test_contents_geometry_srs_mismatch(gpkg_dir: Path) -> None:
    path = create_minimal_gpkg(
        gpkg_dir / "srs-mis.gpkg",
        feature_layers=[
            {
                "name": "pipe_routes",
                "geometry_type": "LINESTRING",
                "srs_id": 4326,
                "contents_srs_id": 4326,
                "geometry_columns_srs_id": 900001,
                "columns": ["object_code", "name"],
                "rows": [{"object_code": "P1", "name": "A"}],
            }
        ],
    )
    report = inspect_standard_gpkg(path)
    layer = next(layer for layer in report.layers if layer.name == "pipe_routes")
    assert "contents_geometry_srs_mismatch" in layer.rejection_reasons
    assert layer.accepted is False


def test_geometry_column_missing_from_table(gpkg_dir: Path) -> None:
    path = create_minimal_gpkg(
        gpkg_dir / "geom-col-missing.gpkg",
        feature_layers=[
            {
                "name": "pipe_routes",
                "geometry_type": "LINESTRING",
                "srs_id": 4326,
                "geometry_column": "geom",
                "geometry_columns_column": "missing_geom",
                "columns": ["object_code", "name"],
                "rows": [{"object_code": "P1", "name": "A"}],
            }
        ],
    )
    report = inspect_standard_gpkg(path)
    layer = next(layer for layer in report.layers if layer.name == "pipe_routes")
    assert "geometry_column_missing_from_table" in layer.rejection_reasons


def test_geometry_column_type_text_rejected(gpkg_dir: Path) -> None:
    path = create_minimal_gpkg(
        gpkg_dir / "geom-text.gpkg",
        feature_layers=[
            {
                "name": "pipe_routes",
                "geometry_type": "LINESTRING",
                "srs_id": 4326,
                "geometry_column_sql_type": "TEXT",
                "columns": ["object_code", "name"],
                "rows": [{"object_code": "P1", "name": "A"}],
            }
        ],
    )
    report = inspect_standard_gpkg(path)
    layer = next(layer for layer in report.layers if layer.name == "pipe_routes")
    assert "geometry_column_type_not_blob" in layer.rejection_reasons


def test_crs_definition_missing(gpkg_dir: Path) -> None:
    from app.services.gpkg_preflight import _resolve_epsg

    epsg, errs = _resolve_epsg("EPSG", 4326, None)
    assert epsg is None
    assert "crs_definition_missing" in errs

    # Integration: empty definition is rejected (SQLite NOT NULL may block true NULL).
    path = create_valid_pipe_routes_gpkg(
        gpkg_dir / "def-empty.gpkg",
        definition="",
    )
    report = inspect_standard_gpkg(path)
    layer = next(layer for layer in report.layers if layer.name == "pipe_routes")
    assert "crs_definition_empty" in layer.rejection_reasons


def test_crs_definition_undefined(gpkg_dir: Path) -> None:
    path = create_valid_pipe_routes_gpkg(
        gpkg_dir / "def-undef.gpkg",
        definition="undefined",
    )
    report = inspect_standard_gpkg(path)
    layer = next(layer for layer in report.layers if layer.name == "pipe_routes")
    assert "crs_definition_undefined" in layer.rejection_reasons


def test_empty_whitelisted_layer_not_accepted(gpkg_dir: Path) -> None:
    path = create_minimal_gpkg(
        gpkg_dir / "empty-layer.gpkg",
        feature_layers=[
            {
                "name": "pipe_routes",
                "geometry_type": "LINESTRING",
                "srs_id": 4326,
                "columns": ["object_code", "name"],
                "rows": [],
            }
        ],
    )
    report = inspect_standard_gpkg(path)
    layer = next(layer for layer in report.layers if layer.name == "pipe_routes")
    assert layer.feature_count == 0
    assert "empty_whitelisted_layer" in layer.rejection_reasons
    assert layer.accepted is False
    assert report.valid is False
    assert "all_whitelisted_layers_empty" in report.errors


def test_non_whitelist_empty_ok_when_whitelist_has_features(gpkg_dir: Path) -> None:
    path = create_minimal_gpkg(
        gpkg_dir / "empty-other.gpkg",
        feature_layers=[
            {
                "name": "pipe_routes",
                "geometry_type": "LINESTRING",
                "srs_id": 4326,
                "columns": ["object_code", "name"],
                "rows": [{"object_code": "P1", "name": "A"}],
            },
            {
                "name": "scratch_notes",
                "geometry_type": "POINT",
                "srs_id": 4326,
                "columns": ["object_code", "name"],
                "rows": [],
            },
        ],
    )
    report = inspect_standard_gpkg(path)
    assert report.valid is True
    scratch = next(layer for layer in report.layers if layer.name == "scratch_notes")
    assert scratch.accepted is False
    assert "layer_not_whitelisted" in scratch.rejection_reasons


def test_geometry_sample_query_failure_rejects_layer(
    gpkg_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = create_valid_pipe_routes_gpkg(gpkg_dir / "sample-fail.gpkg")
    import app.services.gpkg_preflight as mod

    real_connect = mod.sqlite3.connect

    class FailingConn:
        def __init__(self, real: sqlite3.Connection) -> None:
            object.__setattr__(self, "_real", real)

        def __setattr__(self, name: str, value: object) -> None:
            if name == "_real":
                object.__setattr__(self, name, value)
                return
            setattr(self._real, name, value)

        def __getattr__(self, name: str):
            return getattr(self._real, name)

        def execute(self, sql: str, *args, **kwargs):  # type: ignore[no-untyped-def]
            sql_u = sql.upper()
            if (
                "AS G" in sql_u
                and "PIPE_ROUTES" in sql_u
                and "COUNT" not in sql_u
            ):
                raise sqlite3.Error("simulated sample failure")
            return self._real.execute(sql, *args, **kwargs)

        def close(self) -> None:
            self._real.close()

    def connect_wrapper(*args, **kwargs):  # type: ignore[no-untyped-def]
        return FailingConn(real_connect(*args, **kwargs))

    monkeypatch.setattr(mod.sqlite3, "connect", connect_wrapper)

    report = inspect_standard_gpkg(path)
    layer = next(layer for layer in report.layers if layer.name == "pipe_routes")
    assert "geometry_sample_query_failed" in layer.rejection_reasons
    assert layer.accepted is False


def test_file_changed_during_hash_detected(
    gpkg_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = create_valid_pipe_routes_gpkg(gpkg_dir / "mutating.gpkg")
    import app.services.gpkg_preflight as mod

    calls = {"n": 0}
    real_stream = mod._stream_sha256

    def flaky_hash(p: Path, **kwargs):  # type: ignore[no-untyped-def]
        calls["n"] += 1
        digest = real_stream(p, **kwargs)
        # Mutate file after first byte stream completes (simulates TOCTOU).
        with p.open("ab") as handle:
            handle.write(b"\x00")
        return digest

    monkeypatch.setattr(mod, "_stream_sha256", flaky_hash)
    report = inspect_standard_gpkg(path)
    assert report.valid is False
    assert "file_changed_during_hash" in report.errors


def test_symlink_rejected_when_capability_allows(tmp_path: Path, gpkg_dir: Path) -> None:
    target = create_valid_pipe_routes_gpkg(gpkg_dir / "symlink-target.gpkg")
    link = tmp_path / "link.gpkg"
    try:
        os.symlink(target, link)
    except OSError as exc:
        pytest.skip(f"symlink capability unavailable on this platform/user: {exc}")
    if not link.is_symlink():
        pytest.skip("created path is not a symlink")
    report = inspect_standard_gpkg(link)
    assert report.valid is False
    assert "file_is_symlink_or_reparse" in report.errors
    # Must not leak full path strings in errors
    for err in report.errors:
        assert "\\" not in err
        assert "/" not in err or err.startswith("file_")


def test_errors_do_not_embed_absolute_paths(gpkg_dir: Path) -> None:
    missing = gpkg_dir / "does-not-exist-anywhere.gpkg"
    report = inspect_standard_gpkg(missing)
    assert report.valid is False
    assert report.errors == ["file_not_found"]
    joined = " ".join(report.errors)
    assert str(missing) not in joined
    assert "Workspaces" not in joined


def test_valid_envelope_e1_header_length() -> None:
    wkb = _wkb_point_xy(1.0, 2.0)
    blob = build_gp_binary(4326, wkb, envelope_indicator=1)
    assert len(blob) >= 8 + 32
    assert inspect_gp_header(blob) == []
