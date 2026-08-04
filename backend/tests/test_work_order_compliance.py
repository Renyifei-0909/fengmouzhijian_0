"""Alpha18: QGIS work-order import, spatial check, and compliance engine slice."""

from __future__ import annotations

import copy
import io
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.services.compliance import evaluate_compliance
from app.services.spatial import (
    evaluate_spatial_check,
    geometry_to_wgs84,
    utm32n_to_wgs84,
)


PACKAGE_PATH = (
    Path(__file__).resolve().parents[2]
    / "examples"
    / "design-package-demo"
    / "synthetic-pipe-route-package.json"
)


def _tiny_png() -> bytes:
    return (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00"
        b"\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
    )


def _demo_line_geom() -> dict:
    return geometry_to_wgs84(
        {
            "type": "LineString",
            "coordinates": [
                [400000.0, 5700000.0],
                [400100.0, 5700020.0],
            ],
        },
        source_epsg=25832,
    )


def _near_point() -> tuple[float, float]:
    return utm32n_to_wgs84(400000.0, 5700000.0)


def _assign(client: TestClient, work_order_id: str, assignee: str = "worker") -> dict:
    resp = client.post(
        f"/api/v1/work-orders/{work_order_id}/assign",
        json={"assigned_to": assignee},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "assigned"
    assert body["assigned_to"] == assignee
    return body


def test_utm32n_roundtrip_and_spatial_policy_matrix() -> None:
    lon, lat = _near_point()
    assert 6.0 < lon < 12.0
    assert 50.0 < lat < 55.0
    geom = _demo_line_geom()
    common = {
        "geometry_wgs84": geom,
        "tolerance_m": 50.0,
        "gps_accuracy_threshold_m": 30.0,
    }

    # 1) High accuracy, in range → passed
    high_near = evaluate_spatial_check(
        latitude=lat,
        longitude=lon,
        accuracy_m=5.0,
        location_source="device_gps",
        is_synthetic_location=False,
        **common,
    )
    assert high_near["spatial_check_status"] == "passed"
    assert high_near["distance_to_target_m"] is not None
    assert high_near["distance_to_target_m"] < 5.0
    assert "not added" in high_near["spatial_check_reason"].casefold() or (
        "separate" in high_near["spatial_check_reason"].casefold()
    )

    # 2) High accuracy, out of range → failed
    far = evaluate_spatial_check(
        latitude=lat + 0.05,
        longitude=lon + 0.05,
        accuracy_m=3.0,
        location_source="device_gps",
        is_synthetic_location=False,
        **common,
    )
    assert far["spatial_check_status"] == "failed"
    assert far["distance_to_target_m"] is not None
    assert far["distance_to_target_m"] > 50.0

    # 3) Very poor accuracy, near distance → unavailable (accuracy gate)
    poor_near = evaluate_spatial_check(
        latitude=lat,
        longitude=lon,
        accuracy_m=100.0,
        location_source="device_gps",
        is_synthetic_location=False,
        **common,
    )
    assert poor_near["spatial_check_status"] == "unavailable"
    assert poor_near["distance_to_target_m"] is None
    assert "gps_accuracy_threshold_m" in poor_near["spatial_check_reason"]

    # 4) Very poor accuracy, far distance → unavailable (accuracy fails first)
    poor_far = evaluate_spatial_check(
        latitude=lat + 0.05,
        longitude=lon + 0.05,
        accuracy_m=100.0,
        location_source="device_gps",
        is_synthetic_location=False,
        **common,
    )
    assert poor_far["spatial_check_status"] == "unavailable"

    # 5) Missing accuracy on device_gps → unavailable
    missing = evaluate_spatial_check(
        latitude=lat,
        longitude=lon,
        accuracy_m=None,
        location_source="device_gps",
        is_synthetic_location=False,
        **common,
    )
    assert missing["spatial_check_status"] == "unavailable"
    assert "missing accuracy_m" in missing["spatial_check_reason"]

    # 6) Negative accuracy → unavailable
    negative = evaluate_spatial_check(
        latitude=lat,
        longitude=lon,
        accuracy_m=-1.0,
        location_source="device_gps",
        is_synthetic_location=False,
        **common,
    )
    assert negative["spatial_check_status"] == "unavailable"
    assert "must be > 0" in negative["spatial_check_reason"]

    # 7) Illegal coordinates → unavailable
    bad_lat = evaluate_spatial_check(
        latitude=95.0,
        longitude=lon,
        accuracy_m=5.0,
        location_source="device_gps",
        is_synthetic_location=False,
        **common,
    )
    assert bad_lat["spatial_check_status"] == "unavailable"
    assert "latitude" in bad_lat["spatial_check_reason"].casefold()

    bad_lon = evaluate_spatial_check(
        latitude=lat,
        longitude=200.0,
        accuracy_m=5.0,
        location_source="device_gps",
        is_synthetic_location=False,
        **common,
    )
    assert bad_lon["spatial_check_status"] == "unavailable"
    assert "longitude" in bad_lon["spatial_check_reason"].casefold()

    # 8) synthetic_demo: distance computed, synthetic label retained, no field proof
    synthetic = evaluate_spatial_check(
        latitude=lat,
        longitude=lon,
        accuracy_m=None,
        location_source="synthetic_demo",
        is_synthetic_location=True,
        **common,
    )
    assert synthetic["spatial_check_status"] == "passed"
    assert synthetic["is_synthetic_location"] is True
    assert "synthetic_demo" in synthetic["spatial_check_reason"]

    # Accuracy must not enlarge distance tolerance (regression for the old bug).
    # Point is ~60 m perpendicular off the line; tolerance 50 m; accuracy 20 m
    # must still fail (old bug would pass with tolerance+accuracy=70).
    lon60, lat60 = utm32n_to_wgs84(400000.0, 5700060.0)
    no_expand = evaluate_spatial_check(
        latitude=lat60,
        longitude=lon60,
        accuracy_m=20.0,
        location_source="device_gps",
        is_synthetic_location=False,
        geometry_wgs84=geom,
        tolerance_m=50.0,
        gps_accuracy_threshold_m=30.0,
    )
    assert no_expand["spatial_check_status"] == "failed"
    assert no_expand["distance_to_target_m"] is not None
    assert no_expand["distance_to_target_m"] > 50.0
    assert no_expand["distance_to_target_m"] < 70.0


def test_compliance_engine_separates_observation_from_verdict() -> None:
    rules = {
        "rule_version": "workorder-rules-v0.1",
        "expected": {
            "visible_pipe_count": {"equals": 4},
            "trench_stage": {"equals": "laying"},
            "object_visibility": {"one_of": ["visible", "partially_visible"]},
        },
    }
    analyzer_result = {
        "observations": {
            "measurements": {
                "visible_pipe_count": 4,
                "trench_stage": "laying",
                "object_visibility": "visible",
            }
        },
        "confidence": 0.5,
    }
    ok = evaluate_compliance(
        rules_snapshot=rules,
        analyzer_result=analyzer_result,
        spatial_check_status="passed",
    )
    assert ok["verdict"] == "compliant"
    assert ok["authority"] == "server_rule_engine"

    bad = evaluate_compliance(
        rules_snapshot=rules,
        analyzer_result={
            "observations": {
                "measurements": {
                    "visible_pipe_count": 2,
                    "trench_stage": "laying",
                    "object_visibility": "visible",
                }
            }
        },
        spatial_check_status="passed",
    )
    assert bad["verdict"] == "deviation_detected"

    depth_rules = {
        "expected": {
            "depth_m": {"operator": ">=", "value": 1.2},
        }
    }
    depth = evaluate_compliance(
        rules_snapshot=depth_rules,
        analyzer_result={"observations": {"measurements": {"depth_m": 1.25}}},
        spatial_check_status="passed",
    )
    assert depth["verdict"] == "insufficient_evidence"

    missing = evaluate_compliance(
        rules_snapshot=rules,
        analyzer_result={"observations": {"measurements": {}}},
        spatial_check_status="passed",
    )
    assert missing["verdict"] == "insufficient_evidence"


def test_work_order_vertical_slice_end_to_end(client: TestClient) -> None:
    project = client.post(
        "/api/v1/projects",
        json={
            "code": "WO-PIPE-DEMO",
            "name": "工单合规垂直切片",
            "location": "合成演示坐标（非真实现场）",
            "manager": "测试操作员",
        },
    )
    assert project.status_code == 201, project.text
    project_id = project.json()["id"]

    package_bytes = PACKAGE_PATH.read_bytes()
    imported = client.post(
        f"/api/v1/projects/{project_id}/design-packages/import-json",
        files={
            "file": (
                "synthetic-pipe-route-package.json",
                io.BytesIO(package_bytes),
                "application/json",
            )
        },
    )
    assert imported.status_code == 201, imported.text
    body = imported.json()
    assert body["package"]["synthetic"] is True
    assert body["package"]["source_type"] == "synthetic_json"
    assert body["package"]["source_crs_epsg"] == 25832
    assert body["package"]["object_count"] == 1
    eng = body["objects"][0]
    assert eng["object_code"] == "PIPE-101"

    work_order = client.post(
        f"/api/v1/projects/{project_id}/work-orders",
        json={
            "engineering_object_id": eng["id"],
            "work_order_code": "PIPE-101-WO-1",
            "spatial_tolerance_m": 80.0,
            "gps_accuracy_threshold_m": 25.0,
            "assigned_to": "现场工人A",
            "notes": "合成演示工单",
        },
    )
    assert work_order.status_code == 201, work_order.text
    wo = work_order.json()
    assert wo["status"] == "draft"
    assert wo["spatial_tolerance_m"] == 80.0
    assert wo["gps_accuracy_threshold_m"] == 25.0
    wo = _assign(client, wo["id"], "worker-a")

    lon, lat = _near_point()
    upload = client.post(
        f"/api/v1/work-orders/{wo['id']}/verifications",
        data={
            "analyzer": "demo_fixture",
            "latitude": str(lat),
            "longitude": str(lon),
            "accuracy_m": "8.0",
            "location_source": "synthetic_demo",
            "is_synthetic_location": "true",
            "client_captured_at": "2026-07-31T10:00:00+00:00",
            "device_id": "demo-phone",
            "metadata": json.dumps({"purpose": "demo"}),
        },
        files={"file": ("pipe-101.png", io.BytesIO(_tiny_png()), "image/png")},
    )
    assert upload.status_code == 202, upload.text
    uploaded = upload.json()
    assert uploaded["capture"]["spatial_check_status"] == "passed"
    assert uploaded["capture"]["is_synthetic_location"] is True
    assert uploaded["capture"]["gps_accuracy_threshold_m"] == 25.0
    assert uploaded["capture"]["tolerance_m"] == 80.0
    job_id = uploaded["job"]["id"]

    detail = client.get(f"/api/v1/verifications/{job_id}")
    assert detail.status_code == 200, detail.text
    result = detail.json()["job"]["result"]
    assert result["provenance"]["synthetic"] is True
    assert result["accuracy_claim"] is None
    assert result["compliance_evaluation"]["authority"] == "server_rule_engine"

    compliance = client.get(f"/api/v1/verifications/{job_id}/compliance")
    assert compliance.status_code == 200, compliance.text
    assert compliance.json()["job_id"] == job_id

    gis = client.get(f"/api/v1/projects/{project_id}/gis-summary")
    assert gis.status_code == 200, gis.text
    assert gis.json()["engineering_object_count"] == 1

    wo_after = client.get(f"/api/v1/work-orders/{wo['id']}").json()
    assert wo_after["status"] in {"needs_review", "deviation"}


def test_spatial_failure_forces_review_verdict(client: TestClient) -> None:
    project = client.post(
        "/api/v1/projects",
        json={"code": "WO-SPATIAL-FAIL", "name": "空间失败用例", "location": "合成"},
    ).json()
    package_bytes = PACKAGE_PATH.read_bytes()
    imported = client.post(
        f"/api/v1/projects/{project['id']}/design-packages/import-json",
        files={
            "file": (
                "synthetic-pipe-route-package.json",
                io.BytesIO(package_bytes),
                "application/json",
            )
        },
    ).json()
    eng_id = imported["objects"][0]["id"]
    wo = client.post(
        f"/api/v1/projects/{project['id']}/work-orders",
        json={
            "engineering_object_id": eng_id,
            "work_order_code": "PIPE-101-WO-FAIL",
            "spatial_tolerance_m": 10.0,
            "gps_accuracy_threshold_m": 30.0,
        },
    ).json()
    assert wo["status"] == "draft"
    wo = _assign(client, wo["id"], "worker")

    upload = client.post(
        f"/api/v1/work-orders/{wo['id']}/verifications",
        data={
            "analyzer": "demo_fixture",
            "latitude": "1.0",
            "longitude": "1.0",
            "accuracy_m": "5.0",
            "location_source": "synthetic_demo",
            "is_synthetic_location": "true",
            "metadata": "{}",
        },
        files={"file": ("far.png", io.BytesIO(_tiny_png()), "image/png")},
    )
    assert upload.status_code == 202, upload.text
    assert upload.json()["capture"]["spatial_check_status"] == "failed"
    job_id = upload.json()["job"]["id"]
    compliance = client.get(f"/api/v1/verifications/{job_id}/compliance").json()
    assert compliance["verdict"] == "needs_review"
    assert compliance["spatial_check_status"] == "failed"


def test_design_package_import_validation_matrix(client: TestClient) -> None:
    project = client.post(
        "/api/v1/projects",
        json={"code": "WO-IMPORT-VAL", "name": "导入校验", "location": "合成"},
    ).json()
    project_id = project["id"]
    base = json.loads(PACKAGE_PATH.read_text(encoding="utf-8"))

    empty = client.post(
        f"/api/v1/projects/{project_id}/design-packages/import-json",
        files={"file": ("empty.json", io.BytesIO(b""), "application/json")},
    )
    assert empty.status_code == 422

    invalid = client.post(
        f"/api/v1/projects/{project_id}/design-packages/import-json",
        files={"file": ("bad.json", io.BytesIO(b"{not-json"), "application/json")},
    )
    assert invalid.status_code == 422

    no_synthetic = copy.deepcopy(base)
    del no_synthetic["synthetic"]
    resp = client.post(
        f"/api/v1/projects/{project_id}/design-packages/import-json",
        files={
            "file": (
                "no-syn.json",
                io.BytesIO(json.dumps(no_synthetic).encode("utf-8")),
                "application/json",
            )
        },
    )
    assert resp.status_code == 422
    assert "synthetic" in resp.json()["detail"].casefold()

    false_synthetic = copy.deepcopy(base)
    false_synthetic["synthetic"] = False
    resp = client.post(
        f"/api/v1/projects/{project_id}/design-packages/import-json",
        files={
            "file": (
                "false-syn.json",
                io.BytesIO(json.dumps(false_synthetic).encode("utf-8")),
                "application/json",
            )
        },
    )
    assert resp.status_code == 422
    assert "synthetic=true" in resp.json()["detail"]

    # Oversize against a tiny configured limit.
    tiny_settings = Settings(
        environment="test",
        database_url=client.app.state.settings.database_url,
        database_schema_mode="create_all",
        storage_root=client.app.state.settings.storage_root,
        max_upload_bytes=2 * 1024 * 1024,
        design_package_max_upload_bytes=64,
        allow_demo_analyzer=True,
        operator_api_key="test-operator-key",
        reviewer_api_key="test-reviewer-key",
        auditor_api_key="test-auditor-key",
        cors_origins=("http://testserver",),
    )
    # Reuse same DB URL/storage is risky; spin isolated app for size limit only.
    from tempfile import TemporaryDirectory

    with TemporaryDirectory() as tmp:
        isolated = Settings(
            environment="test",
            database_url=f"sqlite:///{Path(tmp) / 'limit.db'}",
            database_schema_mode="create_all",
            storage_root=Path(tmp) / "storage",
            design_package_max_upload_bytes=64,
            allow_demo_analyzer=True,
            operator_api_key="test-operator-key",
            reviewer_api_key="test-reviewer-key",
            auditor_api_key="test-auditor-key",
            cors_origins=("http://testserver",),
        )
        with TestClient(create_app(isolated)) as limited:
            limited.headers.update({"X-API-Key": "test-operator-key"})
            p = limited.post(
                "/api/v1/projects",
                json={"code": "LIM-1", "name": "limit", "location": "x"},
            ).json()
            big = json.dumps(base).encode("utf-8")
            assert len(big) > 64
            over = limited.post(
                f"/api/v1/projects/{p['id']}/design-packages/import-json",
                files={"file": ("big.json", io.BytesIO(big), "application/json")},
            )
            assert over.status_code == 413, over.text

    # Successful import then duplicate object_code in same project.
    first = client.post(
        f"/api/v1/projects/{project_id}/design-packages/import-json",
        files={
            "file": (
                "ok.json",
                io.BytesIO(json.dumps(base).encode("utf-8")),
                "application/json",
            )
        },
    )
    assert first.status_code == 201, first.text
    assert first.json()["package"]["source_type"] == "synthetic_json"

    second = client.post(
        f"/api/v1/projects/{project_id}/design-packages/import-json",
        files={
            "file": (
                "dup.json",
                io.BytesIO(json.dumps(base).encode("utf-8")),
                "application/json",
            )
        },
    )
    assert second.status_code == 409, second.text


def test_import_json_cleans_file_on_parse_failure_after_write(tmp_path: Path) -> None:
    """If DB write fails after file save, the on-disk package is removed."""
    settings = Settings(
        environment="test",
        database_url=f"sqlite:///{tmp_path / 'clean.db'}",
        database_schema_mode="create_all",
        storage_root=tmp_path / "storage",
        allow_demo_analyzer=True,
        operator_api_key="test-operator-key",
        reviewer_api_key="test-reviewer-key",
        auditor_api_key="test-auditor-key",
        cors_origins=("http://testserver",),
    )
    with TestClient(create_app(settings)) as client:
        client.headers.update({"X-API-Key": "test-operator-key"})
        project = client.post(
            "/api/v1/projects",
            json={"code": "CLEAN-1", "name": "clean", "location": "x"},
        ).json()
        # Invalid CRS triggers DesignPackageImportError after file is written.
        payload = json.loads(PACKAGE_PATH.read_text(encoding="utf-8"))
        payload["source_crs_epsg"] = 99999
        before = list((tmp_path / "storage" / "design-packages" / project["id"]).glob("*")) if (
            tmp_path / "storage" / "design-packages" / project["id"]
        ).exists() else []
        resp = client.post(
            f"/api/v1/projects/{project['id']}/design-packages/import-json",
            files={
                "file": (
                    "bad-crs.json",
                    io.BytesIO(json.dumps(payload).encode("utf-8")),
                    "application/json",
                )
            },
        )
        assert resp.status_code == 422, resp.text
        package_dir = tmp_path / "storage" / "design-packages" / project["id"]
        if package_dir.exists():
            remaining = list(package_dir.glob("*"))
            assert remaining == before

def test_work_order_freeze_survives_engineering_object_mutation(client: TestClient) -> None:
    """P2-1.1: EO mutation after WO create must not rewrite historical SpatialCheck basis."""
    from sqlalchemy import select

    from app.models import AuditEvent, EngineeringObject, WorkOrder
    from app.services.compliance import evaluate_compliance
    from app.services.spatial import evaluate_spatial_check
    from app.services.work_orders import (
        frozen_geometry_wgs84,
        frozen_rules_snapshot,
        frozen_spatial_policy,
    )

    project = client.post(
        "/api/v1/projects",
        json={"code": "WO-FREEZE-1", "name": "freeze-snapshot", "location": "synthetic"},
    ).json()
    package_bytes = PACKAGE_PATH.read_bytes()
    imported = client.post(
        f"/api/v1/projects/{project['id']}/design-packages/import-json",
        files={
            "file": (
                "synthetic-pipe-route-package.json",
                io.BytesIO(package_bytes),
                "application/json",
            )
        },
    ).json()
    eng = imported["objects"][0]
    wo_resp = client.post(
        f"/api/v1/projects/{project['id']}/work-orders",
        json={
            "engineering_object_id": eng["id"],
            "work_order_code": "PIPE-101-FREEZE",
            "spatial_tolerance_m": 50.0,
            "gps_accuracy_threshold_m": 30.0,
        },
    )
    assert wo_resp.status_code == 201, wo_resp.text
    wo = wo_resp.json()
    assert wo["status"] == "draft"
    assert wo["assigned_to"] is None
    assert wo["rules_snapshot"]["spatial_tolerance_m"] == 50.0
    assert wo["rules_snapshot"]["gps_accuracy_threshold_m"] == 30.0
    frozen_geom = copy.deepcopy(wo["geometry_snapshot"]["geometry_wgs84"])
    frozen_rules = copy.deepcopy(wo["rules_snapshot"])
    mutated_geom = {
        "type": "LineString",
        "coordinates": [[1.0, 1.0], [2.0, 2.0]],
    }

    db = client.app.state.database.session_factory()
    try:
        live = db.get(EngineeringObject, eng["id"])
        assert live is not None
        live.geometry_wgs84_json = mutated_geom
        live.expected_rules_json = {
            "rule_version": "mutated-v999",
            "expected": {"visible_pipe_count": {"equals": 999}},
        }
        db.commit()
    finally:
        db.close()

    wo_after = client.get(f"/api/v1/work-orders/{wo['id']}").json()
    assert wo_after["geometry_snapshot"]["geometry_wgs84"] == frozen_geom
    assert wo_after["rules_snapshot"] == frozen_rules
    assert wo_after["rules_snapshot"].get("rule_version") != "mutated-v999"

    lon, lat = _near_point()
    db = client.app.state.database.session_factory()
    try:
        order = db.get(WorkOrder, wo["id"])
        assert order is not None
        geom = frozen_geometry_wgs84(order)
        tol, acc = frozen_spatial_policy(order)
        assert geom == frozen_geom
        spatial = evaluate_spatial_check(
            latitude=lat,
            longitude=lon,
            accuracy_m=5.0,
            geometry_wgs84=geom,
            tolerance_m=tol,
            gps_accuracy_threshold_m=acc,
            location_source="device_gps",
            is_synthetic_location=False,
        )
        assert spatial["spatial_check_status"] == "passed"
        far = evaluate_spatial_check(
            latitude=lat,
            longitude=lon,
            accuracy_m=5.0,
            geometry_wgs84=mutated_geom,
            tolerance_m=tol,
            gps_accuracy_threshold_m=acc,
            location_source="device_gps",
            is_synthetic_location=False,
        )
        assert far["spatial_check_status"] == "failed"
        rules = frozen_rules_snapshot(order)
        assert rules.get("spatial_tolerance_m") == 50.0
        expected = rules.get("expected") or {}
        if "visible_pipe_count" in expected:
            assert expected["visible_pipe_count"] != {"equals": 999}
        compliance = evaluate_compliance(
            rules_snapshot=rules,
            analyzer_result={
                "observations": {
                    "measurements": {
                        "visible_pipe_count": 4,
                        "trench_stage": "laying",
                        "object_visibility": "visible",
                    }
                }
            },
            spatial_check_status="passed",
        )
        assert compliance["verdict"] in {
            "compliant",
            "insufficient_evidence",
            "needs_review",
            "deviation_detected",
        }
        actions = list(
            db.scalars(
                select(AuditEvent.action).where(AuditEvent.entity_id == wo["id"])
            ).all()
        )
        assert "work_order_created" in actions
        assert "work_order_assigned" not in actions
    finally:
        db.close()


def test_work_order_capture_emits_p21_audit_actions(client: TestClient) -> None:
    """P2-1.1: evidence_captured + spatial_check_completed + rule_evaluation_completed."""
    from sqlalchemy import select

    from app.models import AuditEvent

    project = client.post(
        "/api/v1/projects",
        json={"code": "WO-AUDIT-1", "name": "audit-events", "location": "synthetic"},
    ).json()
    package_bytes = PACKAGE_PATH.read_bytes()
    imported = client.post(
        f"/api/v1/projects/{project['id']}/design-packages/import-json",
        files={
            "file": (
                "synthetic-pipe-route-package.json",
                io.BytesIO(package_bytes),
                "application/json",
            )
        },
    ).json()
    eng_id = imported["objects"][0]["id"]
    wo = client.post(
        f"/api/v1/projects/{project['id']}/work-orders",
        json={
            "engineering_object_id": eng_id,
            "work_order_code": "PIPE-101-AUDIT",
        },
    ).json()
    assert wo["status"] == "draft"
    wo = _assign(client, wo["id"], "auditor-worker")
    lon, lat = _near_point()
    upload = client.post(
        f"/api/v1/work-orders/{wo['id']}/verifications",
        data={
            "analyzer": "demo_fixture",
            "latitude": str(lat),
            "longitude": str(lon),
            "accuracy_m": "8.0",
            "location_source": "synthetic_demo",
            "is_synthetic_location": "true",
            "metadata": "{}",
        },
        files={"file": ("audit.png", io.BytesIO(_tiny_png()), "image/png")},
    )
    assert upload.status_code == 202, upload.text
    job_id = upload.json()["job"]["id"]
    detail = client.get(f"/api/v1/verifications/{job_id}")
    assert detail.status_code == 200, detail.text

    db = client.app.state.database.session_factory()
    try:
        actions = set(
            db.scalars(
                select(AuditEvent.action).where(AuditEvent.entity_id == wo["id"])
            ).all()
        )
        assert "work_order_created" in actions
        assert "work_order_assigned" in actions
        assert "evidence_captured" in actions
        assert "spatial_check_completed" in actions
        assert "rule_evaluation_completed" in actions
        assert "analysis_observations_received" in actions
    finally:
        db.close()


def test_no_public_work_order_status_write_route(client: TestClient) -> None:
    """P2-1: frontend must not be able to PATCH arbitrary work order status."""
    from app.schemas import WorkOrderCreate

    methods_by_path: dict[str, set[str]] = {}
    for route in client.app.routes:
        path = getattr(route, "path", None)
        methods = getattr(route, "methods", None)
        if not path or not methods:
            continue
        if "work-order" not in path:
            continue
        methods_by_path.setdefault(path, set()).update(methods)
    for path, methods in methods_by_path.items():
        if path.rstrip("/").endswith("/status"):
            assert "PUT" not in methods and "PATCH" not in methods and "POST" not in methods
    fields = set(WorkOrderCreate.model_fields.keys())
    assert "status" not in fields

def test_create_stays_draft_until_assign_command(client: TestClient) -> None:
    """P2-1.2: create is draft; assign is the only status transition to assigned."""
    from sqlalchemy import select

    from app.models import AuditEvent

    project = client.post(
        "/api/v1/projects",
        json={"code": "WO-ASSIGN-1", "name": "assign-cmd", "location": "synthetic"},
    ).json()
    package_bytes = PACKAGE_PATH.read_bytes()
    imported = client.post(
        f"/api/v1/projects/{project['id']}/design-packages/import-json",
        files={
            "file": (
                "synthetic-pipe-route-package.json",
                io.BytesIO(package_bytes),
                "application/json",
            )
        },
    ).json()
    eng_id = imported["objects"][0]["id"]
    created = client.post(
        f"/api/v1/projects/{project['id']}/work-orders",
        json={
            "engineering_object_id": eng_id,
            "work_order_code": "PIPE-101-ASSIGN",
            "assigned_to": "ignored-on-create",
        },
    )
    assert created.status_code == 201, created.text
    wo = created.json()
    assert wo["status"] == "draft"
    assert wo["assigned_to"] is None

    lon, lat = _near_point()
    blocked = client.post(
        f"/api/v1/work-orders/{wo['id']}/verifications",
        data={
            "analyzer": "demo_fixture",
            "latitude": str(lat),
            "longitude": str(lon),
            "accuracy_m": "5.0",
            "location_source": "synthetic_demo",
            "is_synthetic_location": "true",
            "metadata": "{}",
        },
        files={"file": ("blocked.png", io.BytesIO(_tiny_png()), "image/png")},
    )
    assert blocked.status_code == 409, blocked.text
    assert "assign" in blocked.json()["detail"].casefold()

    assigned = _assign(client, wo["id"], "worker-b")
    assert assigned["status"] == "assigned"
    assert assigned["assigned_to"] == "worker-b"

    again = client.post(
        f"/api/v1/work-orders/{wo['id']}/assign",
        json={"assigned_to": "worker-c"},
    )
    assert again.status_code == 200, again.text
    assert again.json()["assigned_to"] == "worker-c"
    assert again.json()["status"] == "assigned"

    db = client.app.state.database.session_factory()
    try:
        actions = list(
            db.scalars(
                select(AuditEvent.action).where(AuditEvent.entity_id == wo["id"])
            ).all()
        )
        assert actions.count("work_order_created") == 1
        assert actions.count("work_order_assigned") >= 2
    finally:
        db.close()

