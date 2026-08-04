from __future__ import annotations

from fastapi.testclient import TestClient


def test_project_overview_is_empty_and_explicit_for_a_new_project(
    client: TestClient,
    project_and_baseline: tuple[dict, dict],
) -> None:
    project, _baseline = project_and_baseline

    response = client.get(f"/api/v1/projects/{project['id']}/overview")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["project"]["id"] == project["id"]
    assert body["progress"]["baseline_count"] == 1
    assert body["progress"]["completion_rate"] == 0.0
    assert body["jobs_by_status"] == {}
    assert body["evidence_asset_count"] == 0
    assert body["sensor_event_count"] == 0
    assert body["report_count"] == 0
    assert body["proof_record_count"] == 0
    assert body["recent_verifications"] == []
    assert body["recent_reports"] == []
    assert body["recent_proofs"] == []
    assert "not treated as currently valid" in body["truth_note"]


def test_project_overview_aggregates_only_the_requested_project(
    client: TestClient,
    project_and_baseline: tuple[dict, dict],
    valid_mp4_bytes: bytes,
) -> None:
    project, baseline = project_and_baseline
    other = client.post(
        "/api/v1/projects",
        json={"code": "OTHER-001", "name": "其他项目", "location": "隔离测试工点"},
    ).json()
    other_baseline = client.post(
        f"/api/v1/projects/{other['id']}/baselines",
        json={
            "site_id": "SITE-OTHER",
            "procedure_code": "OTHER-PROCEDURE",
            "version": "v1",
            "source_type": "manual",
            "expected": {"scene_type": "other"},
        },
    ).json()

    sensor = client.post(
        "/api/v1/sensor-events",
        json={
            "project_id": project["id"],
            "site_id": baseline["site_id"],
            "device_id": "SENSOR-OVERVIEW-01",
            "kind": "temperature",
            "value": 21.5,
            "unit": "C",
            "captured_at": "2026-07-14T08:00:00Z",
            "metadata": {"source": "synthetic-overview-test"},
        },
    )
    assert sensor.status_code == 201, sensor.text

    primary_job = client.post(
        "/api/v1/verifications",
        data={
            "project_id": project["id"],
            "baseline_id": baseline["id"],
            "analyzer": "demo_fixture",
        },
        files={"file": ("primary.mp4", valid_mp4_bytes, "video/mp4")},
    )
    assert primary_job.status_code == 202, primary_job.text
    review = client.post(
        f"/api/v1/verifications/{primary_job.json()['id']}/review",
        json={
            "decision": "approve",
            "reviewer": "界面聚合测试员",
            "note": "仅验证项目聚合接口，不声明模型性能。",
        },
        headers={"X-API-Key": "test-reviewer-key"},
    )
    assert review.status_code == 200, review.text

    other_job = client.post(
        "/api/v1/verifications",
        data={
            "project_id": other["id"],
            "baseline_id": other_baseline["id"],
            "analyzer": "stub",
        },
        files={"file": ("other.mp4", valid_mp4_bytes, "video/mp4")},
    )
    assert other_job.status_code == 202, other_job.text

    body = client.get(f"/api/v1/projects/{project['id']}/overview", params={"recent_limit": 1}).json()
    assert body["project"]["id"] == project["id"]
    assert body["progress"]["completion_rate"] == 100.0
    assert body["jobs_by_status"] == {"approved": 1}
    assert body["evidence_asset_count"] == 1
    assert body["sensor_event_count"] == 1
    assert body["report_count"] == 1
    assert body["proof_record_count"] == 1
    assert [item["id"] for item in body["recent_verifications"]] == [primary_job.json()["id"]]
    assert [item["id"] for item in body["recent_reports"]] == [review.json()["report"]["id"]]
    assert [item["id"] for item in body["recent_proofs"]] == [review.json()["proof"]["id"]]
    assert body["recent_proofs"][0]["evidence_grade"] is False


def test_project_overview_rejects_unknown_project_and_invalid_limit(client: TestClient) -> None:
    assert client.get("/api/v1/projects/not-found/overview").status_code == 404
    assert client.get("/api/v1/projects/not-found/overview", params={"recent_limit": 0}).status_code == 422

