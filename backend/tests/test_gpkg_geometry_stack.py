"""P1-2A: geometry stack feasibility (pyogrio + Shapely + pyproj)."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.services.gpkg_geometry_stack import (
    GpkgGeometryStackError,
    probe_geometry_stack,
    read_layer_sample,
    require_geometry_stack,
    transform_xy_always_xy,
)
from tests.gpkg_fixture_factory import create_valid_pipe_routes_gpkg


@pytest.fixture()
def gpkg_dir(tmp_path: Path) -> Path:
    return tmp_path / "gpkg"


def test_probe_geometry_stack_reports_versions() -> None:
    probe = probe_geometry_stack()
    if not probe.available:
        pytest.skip(
            f"geometry stack unavailable on this host: "
            f"{probe.error_code}: {probe.error_message}"
        )
    assert probe.pyogrio
    assert probe.shapely
    assert probe.pyproj
    assert probe.gdal
    assert probe.gpkg_driver is True
    assert "geopandas_not_required" in probe.notes
    assert "no_handwritten_wkb_fallback" in probe.notes


def test_require_geometry_stack_or_clear_error() -> None:
    try:
        versions = require_geometry_stack()
    except GpkgGeometryStackError as exc:
        assert exc.code in {
            "geometry_stack_not_installed",
            "geometry_stack_native_load_failed",
            "gpkg_driver_missing",
            "gpkg_driver_probe_failed",
            "geometry_stack_unavailable",
        }
        # Must not look like a silent handwritten fallback success.
        assert "handwritten" not in str(exc).lower()
        pytest.skip(f"stack unavailable: {exc.code}")
    assert versions.available is True


def test_list_layers_and_read_info_on_synthetic_gpkg(gpkg_dir: Path) -> None:
    require_geometry_stack()
    path = create_valid_pipe_routes_gpkg(gpkg_dir / "stack-read.gpkg")
    sample = read_layer_sample(path, layer="pipe_routes", max_features=3)
    assert sample.layer_name == "pipe_routes"
    assert sample.feature_count >= 1
    assert sample.geometry_type in {"LineString", "LINESTRING", "Unknown"}
    assert "object_code" in sample.fields
    assert sample.sample_geometry_types
    assert all(t == "LineString" for t in sample.sample_geometry_types)


def test_wkb_to_shapely_and_epsg25832_to_4326(gpkg_dir: Path) -> None:
    require_geometry_stack()
    # Known UTM32N-ish synthetic point → lon/lat with always_xy
    lon, lat = transform_xy_always_xy(463552.0, 5549380.0, source_epsg=25832, target_epsg=4326)
    assert 7.0 < lon < 10.0
    assert 49.0 < lat < 52.0

    path = create_valid_pipe_routes_gpkg(
        gpkg_dir / "utm.gpkg",
        srs_id=25832,
        organization_coordsys_id=25832,
    )
    sample = read_layer_sample(
        path,
        layer="pipe_routes",
        max_features=1,
        source_epsg=25832,
        target_epsg=4326,
    )
    # Fixture coordinates are synthetic lon/lat numbers stored under EPSG:25832 metadata;
    # transform still runs and yields finite numbers (feasibility, not geodetic purity).
    assert sample.sample_wgs84_coords
    x, y = sample.sample_wgs84_coords[0]
    assert x == x and y == y


def test_missing_layer_clear_error(gpkg_dir: Path) -> None:
    require_geometry_stack()
    path = create_valid_pipe_routes_gpkg(gpkg_dir / "missing-layer.gpkg")
    with pytest.raises(GpkgGeometryStackError) as ei:
        read_layer_sample(path, layer="does_not_exist")
    assert ei.value.code == "layer_not_found"


def test_missing_file_clear_error(tmp_path: Path) -> None:
    require_geometry_stack()
    with pytest.raises(GpkgGeometryStackError) as ei:
        read_layer_sample(tmp_path / "nope.gpkg", layer="pipe_routes")
    assert ei.value.code == "file_not_found"


def test_no_database_side_effects(gpkg_dir: Path, tmp_path: Path) -> None:
    from sqlalchemy import create_engine, text

    require_geometry_stack()
    db = tmp_path / "biz.db"
    engine = create_engine(f"sqlite:///{db}")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE marker (id INTEGER PRIMARY KEY, note TEXT)"))
        conn.execute(text("INSERT INTO marker (id, note) VALUES (1, 'before')"))
    path = create_valid_pipe_routes_gpkg(gpkg_dir / "no-db.gpkg")
    read_layer_sample(path, layer="pipe_routes", max_features=1)
    with engine.connect() as conn:
        note = conn.execute(text("SELECT note FROM marker WHERE id = 1")).scalar_one()
    assert note == "before"
