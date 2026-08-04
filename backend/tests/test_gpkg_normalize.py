"""P1-2B: standard GPKG normalize service (no DB write, synthetic fixtures)."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from app.services.gpkg_geometry_stack import require_geometry_stack
from app.services.gpkg_normalize import normalize_standard_gpkg
from tests.gpkg_fixture_factory import create_minimal_gpkg, create_valid_pipe_routes_gpkg


@pytest.fixture()
def gpkg_dir(tmp_path: Path) -> Path:
    return tmp_path / "gpkg"


@pytest.fixture(autouse=True)
def _require_stack() -> None:
    try:
        require_geometry_stack()
    except Exception as exc:
        pytest.skip(f"geometry stack unavailable: {exc}")


def test_normalize_valid_4326_produces_candidates(gpkg_dir: Path) -> None:
    path = create_valid_pipe_routes_gpkg(gpkg_dir / "n4326.gpkg")
    report = normalize_standard_gpkg(path)
    assert report.valid is True
    assert len(report.candidates) == 1
    c = report.candidates[0]
    assert c.object_code == "PIPE-001"
    assert c.object_type == "pipe_route"
    assert c.source_epsg == 4326
    assert c.geometry_geojson["type"] == "LineString"
    coords = c.geometry_geojson["coordinates"]
    assert 7.0 < coords[0][0] < 9.0
    assert 49.0 < coords[0][1] < 51.0
    assert "phone" not in c.attributes


def test_normalize_25832_to_wgs84(gpkg_dir: Path) -> None:
    path = create_valid_pipe_routes_gpkg(
        gpkg_dir / "n25832.gpkg",
        srs_id=25832,
        organization_coordsys_id=25832,
    )
    report = normalize_standard_gpkg(path)
    assert report.valid is True, report.errors
    c = report.candidates[0]
    assert c.source_epsg == 25832
    lon, lat = c.geometry_geojson["coordinates"][0]
    assert 7.0 < lon < 10.0
    assert 49.0 < lat < 52.0


def test_normalize_rejects_preflight_failure(gpkg_dir: Path) -> None:
    path = create_valid_pipe_routes_gpkg(
        gpkg_dir / "bad-crs.gpkg",
        organization="LOCAL",
        organization_coordsys_id=1,
        definition="LOCAL",
    )
    report = normalize_standard_gpkg(path)
    assert report.valid is False
    assert "preflight_failed" in report.errors
    assert report.candidates == []


def test_normalize_rejects_duplicate_object_code(gpkg_dir: Path) -> None:
    path = create_minimal_gpkg(
        gpkg_dir / "dup.gpkg",
        feature_layers=[
            {
                "name": "pipe_routes",
                "geometry_type": "LINESTRING",
                "srs_id": 4326,
                "organization_coordsys_id": 4326,
                "columns": ["object_code", "name"],
                "rows": [
                    {"object_code": "PIPE-001", "name": "A"},
                    {"object_code": "PIPE-001", "name": "B"},
                ],
            }
        ],
    )
    report = normalize_standard_gpkg(path)
    assert report.valid is False
    assert any(e.startswith("object_code_duplicate") for e in report.errors)
    assert report.candidates == []


def test_normalize_does_not_read_pii_values(gpkg_dir: Path) -> None:
    path = create_valid_pipe_routes_gpkg(gpkg_dir / "pii.gpkg", extra_pii_column=True)
    report = normalize_standard_gpkg(path)
    assert report.valid is True
    blob = str(report.to_dict())
    assert "SHOULD_NOT_BE_READ" not in blob
    assert "phone" not in report.candidates[0].attributes


def test_normalize_no_database_writes(gpkg_dir: Path, tmp_path: Path) -> None:
    db = tmp_path / "biz.db"
    engine = create_engine(f"sqlite:///{db}")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE marker (id INTEGER PRIMARY KEY, note TEXT)"))
        conn.execute(text("INSERT INTO marker (id, note) VALUES (1, 'before')"))
    path = create_valid_pipe_routes_gpkg(gpkg_dir / "nodb.gpkg")
    normalize_standard_gpkg(path)
    with engine.connect() as conn:
        note = conn.execute(text("SELECT note FROM marker WHERE id = 1")).scalar_one()
    assert note == "before"


def test_normalize_rejects_out_of_range_source_coords(gpkg_dir: Path) -> None:
    # Lon/lat numbers mislabeled as 25832 should fail source range checks.
    path = create_valid_pipe_routes_gpkg(
        gpkg_dir / "bad-range.gpkg",
        srs_id=25832,
        organization_coordsys_id=25832,
        coords=[(8.0, 50.0), (8.1, 50.1)],
    )
    report = normalize_standard_gpkg(path)
    assert report.valid is False
    assert any("coordinate_out_of_source_range" in e for e in report.errors)
