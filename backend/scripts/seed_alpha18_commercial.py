#!/usr/bin/env python3
"""Idempotent UTF-8 sample data seeder for Alpha18 commercial UI trial.

All Chinese strings live in this UTF-8 source file and the JSON fixture.
Do not pass Chinese via PowerShell CLI arguments.

Usage (from backend/ with API already running, or uses TestClient-compatible URL):

  set FENGMOU_OPERATOR_API_KEY=...
  python scripts/seed_alpha18_commercial.py --base-url http://127.0.0.1:8001/api/v1

Assertions re-read every field via API after create.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
PACKAGE_PATH = ROOT / "examples" / "design-package-demo" / "commercial-pipe-route-package.json"

PROJECT_CODE = "ALPHA18-COMMERCIAL"
PROJECT_NAME = "Alpha18 光缆施工合规项目"
PROJECT_LOCATION = "通信管线施工区域"
PROJECT_MANAGER = "项目管理员"
OBJECT_CODE = "PIPE-101"
OBJECT_NAME = "样例管段 PIPE-101"
WORK_ORDER_CODE = "PIPE-101-WO-COMM"
ASSIGNEE = "现场施工组"

# Near first vertex of sample line (EPSG:25832 → WGS84), from known synthetic geometry.
NEAR_LAT = "51.44235231526482"
NEAR_LON = "7.561123205608768"

TINY_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010802000000907753de"
    "0000000c49444154789c63000100000500010d0a2db40000000049454e44ae426082"
)


class SeedError(RuntimeError):
    pass


def _require(cond: bool, message: str) -> None:
    if not cond:
        raise SeedError(message)


def _headers(api_key: str) -> dict[str, str]:
    return {"X-API-Key": api_key}


def _get_or_create_project(client: httpx.Client, base: str, headers: dict[str, str]) -> dict:
    projects = client.get(f"{base}/projects", headers=headers).json()
    for item in projects:
        if item.get("code") == PROJECT_CODE:
            return item
    created = client.post(
        f"{base}/projects",
        headers=headers,
        json={
            "code": PROJECT_CODE,
            "name": PROJECT_NAME,
            "location": PROJECT_LOCATION,
            "manager": PROJECT_MANAGER,
        },
    )
    if created.status_code not in {200, 201}:
        raise SeedError(f"create project failed: {created.status_code} {created.text}")
    return created.json()


def _import_package(client: httpx.Client, base: str, headers: dict[str, str], project_id: str) -> dict:
    objects = client.get(
        f"{base}/projects/{project_id}/engineering-objects",
        headers=headers,
    ).json()
    existing = next((o for o in objects if o.get("object_code") == OBJECT_CODE), None)
    if existing is not None:
        packages = client.get(
            f"{base}/projects/{project_id}/design-packages",
            headers=headers,
        ).json()
        return {"package": packages[0] if packages else {}, "objects": [existing], "reused": True}

    payload = PACKAGE_PATH.read_bytes()
    # Validate fixture is valid UTF-8 JSON with expected Chinese name.
    fixture = json.loads(payload.decode("utf-8"))
    feature_name = fixture["layers"]["pipe_routes"]["features"][0]["name"]
    _require(feature_name == OBJECT_NAME, f"fixture object name mismatch: {feature_name!r}")

    response = client.post(
        f"{base}/projects/{project_id}/design-packages/import-json",
        headers=headers,
        files={"file": (PACKAGE_PATH.name, payload, "application/json")},
    )
    if response.status_code not in {200, 201}:
        raise SeedError(f"import package failed: {response.status_code} {response.text}")
    body = response.json()
    body["reused"] = False
    return body


def _get_or_create_work_order(
    client: httpx.Client,
    base: str,
    headers: dict[str, str],
    project_id: str,
    engineering_object_id: str,
) -> dict:
    orders = client.get(
        f"{base}/projects/{project_id}/work-orders",
        headers=headers,
    ).json()
    for item in orders:
        if item.get("work_order_code") == WORK_ORDER_CODE:
            return item
    response = client.post(
        f"{base}/projects/{project_id}/work-orders",
        headers=headers,
        json={
            "engineering_object_id": engineering_object_id,
            "work_order_code": WORK_ORDER_CODE,
            "procedure_code": "TRENCH-BEFORE-BACKFILL",
            "spatial_tolerance_m": 80.0,
            "gps_accuracy_threshold_m": 30.0,
            "notes": "样例工单，仅用于系统试用",
        },
    )
    if response.status_code not in {200, 201}:
        raise SeedError(f"create work order failed: {response.status_code} {response.text}")
    work_order = response.json()
    # P2-1.2: create is always draft; assign is a separate server command.
    if work_order.get("status") == "draft" or not work_order.get("assigned_to"):
        assigned = client.post(
            f"{base}/work-orders/{work_order['id']}/assign",
            headers=headers,
            json={"assigned_to": ASSIGNEE},
        )
        if assigned.status_code != 200:
            raise SeedError(f"assign work order failed: {assigned.status_code} {assigned.text}")
        return assigned.json()
    return work_order


def _upload(
    client: httpx.Client,
    base: str,
    headers: dict[str, str],
    work_order_id: str,
    *,
    latitude: str,
    longitude: str,
    accuracy_m: str,
    location_source: str,
    is_synthetic_location: str,
) -> dict:
    response = client.post(
        f"{base}/work-orders/{work_order_id}/verifications",
        headers=headers,
        data={
            "analyzer": "demo_fixture",
            "latitude": latitude,
            "longitude": longitude,
            "accuracy_m": accuracy_m,
            "location_source": location_source,
            "is_synthetic_location": is_synthetic_location,
            "device_id": "SEED-COMMERCIAL",
            "metadata": json.dumps(
                {
                    "source": "seed_alpha18_commercial",
                    "purpose": "demo",
                    "synthetic_demo": is_synthetic_location == "true",
                },
                ensure_ascii=False,
            ),
        },
        files={"file": ("sample.png", TINY_PNG, "image/png")},
    )
    if response.status_code not in {200, 202}:
        raise SeedError(f"upload failed: {response.status_code} {response.text}")
    return response.json()


def _wait_job(client: httpx.Client, base: str, headers: dict[str, str], job_id: str) -> dict:
    for _ in range(40):
        detail = client.get(f"{base}/verifications/{job_id}", headers=headers).json()
        status = detail["job"]["status"]
        if status not in {"queued", "running"}:
            return detail
        time.sleep(0.25)
    raise SeedError(f"job {job_id} did not finish")


def _ensure_three_captures(
    client: httpx.Client,
    base: str,
    headers: dict[str, str],
    work_order_id: str,
) -> list[dict]:
    captures = client.get(
        f"{base}/work-orders/{work_order_id}/captures",
        headers=headers,
    ).json()
    if len(captures) >= 3:
        return captures

    # 1) near + good accuracy → passed
    up1 = _upload(
        client,
        base,
        headers,
        work_order_id,
        latitude=NEAR_LAT,
        longitude=NEAR_LON,
        accuracy_m="8",
        location_source="synthetic_demo",
        is_synthetic_location="true",
    )
    _wait_job(client, base, headers, up1["job"]["id"])

    # 2) far → failed
    up2 = _upload(
        client,
        base,
        headers,
        work_order_id,
        latitude="1.0",
        longitude="1.0",
        accuracy_m="5",
        location_source="synthetic_demo",
        is_synthetic_location="true",
    )
    _wait_job(client, base, headers, up2["job"]["id"])

    # 3) near + poor accuracy → unavailable (+ often insufficient_evidence)
    up3 = _upload(
        client,
        base,
        headers,
        work_order_id,
        latitude=NEAR_LAT,
        longitude=NEAR_LON,
        accuracy_m="100",
        location_source="synthetic_demo",
        is_synthetic_location="true",
    )
    _wait_job(client, base, headers, up3["job"]["id"])

    return client.get(
        f"{base}/work-orders/{work_order_id}/captures",
        headers=headers,
    ).json()


def seed(base_url: str, api_key: str) -> dict:
    base = base_url.rstrip("/")
    headers = _headers(api_key)
    with httpx.Client(timeout=60.0) as client:
        health = client.get(f"{base}/readyz")
        _require(health.status_code == 200, f"API not ready: {health.status_code}")

        project = _get_or_create_project(client, base, headers)
        # Field assertions after read-back
        projects = client.get(f"{base}/projects", headers=headers).json()
        project = next(p for p in projects if p["code"] == PROJECT_CODE)
        _require(project["name"] == PROJECT_NAME, f"project name: {project['name']!r}")
        _require(project["location"] == PROJECT_LOCATION, f"project location: {project['location']!r}")
        _require(project["manager"] == PROJECT_MANAGER, f"project manager: {project['manager']!r}")

        imported = _import_package(client, base, headers, project["id"])
        objects = client.get(
            f"{base}/projects/{project['id']}/engineering-objects",
            headers=headers,
        ).json()
        eng = next(o for o in objects if o["object_code"] == OBJECT_CODE)
        _require(eng["name"] == OBJECT_NAME, f"object name: {eng['name']!r}")
        _require(eng["object_code"] == OBJECT_CODE, f"object code: {eng['object_code']!r}")

        packages = client.get(
            f"{base}/projects/{project['id']}/design-packages",
            headers=headers,
        ).json()
        _require(packages, "no design packages")
        pkg = packages[0]
        _require(pkg.get("synthetic") is True, "package must be synthetic=true")
        _require(pkg.get("purpose") == "demo", f"purpose={pkg.get('purpose')!r}")

        work_order = _get_or_create_work_order(
            client,
            base,
            headers,
            project["id"],
            eng["id"],
        )
        work_order = client.get(
            f"{base}/work-orders/{work_order['id']}",
            headers=headers,
        ).json()
        _require(work_order["work_order_code"] == WORK_ORDER_CODE, "work order code mismatch")
        _require(work_order.get("assigned_to") == ASSIGNEE, f"assignee={work_order.get('assigned_to')!r}")

        captures = _ensure_three_captures(client, base, headers, work_order["id"])
        _require(len(captures) >= 3, f"expected >=3 captures, got {len(captures)}")

        # Classify by spatial status for report
        by_status: dict[str, list[str]] = {}
        for cap in captures:
            by_status.setdefault(cap["spatial_check_status"], []).append(cap["id"])
            job_id = cap.get("verification_job_id")
            if not job_id:
                continue
            job = client.get(f"{base}/verifications/{job_id}", headers=headers).json()
            _require("job" in job, "verification detail missing job")
            comp = client.get(f"{base}/verifications/{job_id}/compliance", headers=headers)
            # compliance may 404 only if engine didn't run; fail closed for seed
            _require(comp.status_code == 200, f"compliance missing for job {job_id}: {comp.status_code}")

        return {
            "project": {
                "id": project["id"],
                "code": project["code"],
                "name": project["name"],
                "location": project["location"],
                "manager": project["manager"],
            },
            "engineering_object": {
                "id": eng["id"],
                "object_code": eng["object_code"],
                "name": eng["name"],
            },
            "work_order": {
                "id": work_order["id"],
                "work_order_code": work_order["work_order_code"],
                "assigned_to": work_order.get("assigned_to"),
            },
            "package": {
                "id": pkg.get("id"),
                "synthetic": pkg.get("synthetic"),
                "purpose": pkg.get("purpose"),
                "import_status": pkg.get("import_status"),
            },
            "capture_count": len(captures),
            "captures_by_spatial_status": {k: len(v) for k, v in by_status.items()},
            "import_reused": bool(imported.get("reused")),
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Seed Alpha18 commercial UTF-8 sample data")
    parser.add_argument(
        "--base-url",
        default="http://127.0.0.1:8001/api/v1",
        help="API base URL including /api/v1",
    )
    parser.add_argument(
        "--api-key",
        default="",
        help="Operator API key (or FENGMOU_OPERATOR_API_KEY env)",
    )
    args = parser.parse_args(argv)
    import os

    api_key = args.api_key or os.environ.get("FENGMOU_OPERATOR_API_KEY") or ""
    if not api_key:
        print("ERROR: provide --api-key or FENGMOU_OPERATOR_API_KEY", file=sys.stderr)
        return 2
    try:
        result = seed(args.base_url, api_key)
    except Exception as exc:  # noqa: BLE001 - CLI boundary
        print(f"SEED FAILED: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print("UTF-8_ASSERTIONS_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
