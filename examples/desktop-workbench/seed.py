"""Add the generated desktop workbench cases through the authenticated API."""

import argparse
import hashlib
import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import httpx


HERE = Path(__file__).resolve().parent
PROVENANCE = json.dumps({"scene_reference": True, "collection": "desktop_workbench_cases", "media_origin": "generated"})
NOW = datetime.now(timezone.utc)


def feature(layer, code, name, procedure, geometry_type, coordinates):
    return layer, {
        "object_code": code,
        "name": name,
        "geometry": {"type": geometry_type, "coordinates": coordinates},
        "attributes": {"procedure_code": procedure},
    }


def route(code, name, procedure, coordinates):
    return feature("pipe_routes", code, name, procedure, "LineString", coordinates)


def point(code, name, procedure, coordinates):
    return feature("infrastructure_points", code, name, procedure, "Point", coordinates)


PROJECTS = [
    ("FM-XX-2611", "西兴街道通信设施整治", "杭州市滨江区 · 西兴街道", "西兴运维组", [
        point("XX-01", "西兴路 · 楼宇光配机房", "光纤配线架核对", [120.221258, 30.174538]),
        route("XX-02", "滨康路东段 · 通信支线", "管线路由定位",
              [[120.226466, 30.185168], [120.225762, 30.185044], [120.225074, 30.1849]]),
        point("XX-03", "西兴路南段 · 通信手孔", "手孔及井盖巡检", [120.22212, 30.19316]),
        route("XX-04", "滨安路东段 · 交接箱引入段", "管线路由定位",
              [[120.22059, 30.18892], [120.22208, 30.1892], [120.22334, 30.18944]]),
    ]),
    ("FM-PY-2608", "浦沿片区光缆路由巡检", "杭州市滨江区 · 浦沿街道", "浦沿巡检组", [
        point("PY-01", "浦沿路南段 · 通信手孔", "手孔及井盖巡检", [120.142089, 30.175908]),
        route("PY-02", "闻涛路西段 · 沿江通道路由", "沿江路由巡查",
              [[120.13015, 30.162621], [120.129878, 30.163018], [120.128585, 30.164887]]),
        point("PY-03", "东冠路西段 · 光缆交接箱", "交接箱检查", [120.14523, 30.16329]),
        route("PY-04", "滨文路西段 · 通信支线", "管线路由定位",
              [[120.14578, 30.16985], [120.1467, 30.17004], [120.14825, 30.17034]]),
    ]),
    ("FM-BJ-2607", "滨江通信管网更新工程", "杭州市滨江区 · 江南大道沿线", "滨江工程组", [
        route("BJ-01", "江南大道西段 · 通信管道", "回填前核验",
              [[120.154708, 30.184888], [120.154307, 30.18483], [120.153629, 30.184769]]),
        route("BJ-02", "滨盛路中段 · 通信管道", "施工范围核对",
              [[120.220089, 30.224576], [120.219456, 30.224011], [120.218789, 30.223478]]),
        point("BJ-03", "江晖路 · 户外光缆交接箱", "交接箱检查", [120.205692, 30.203623]),
        feature("trenches", "BJ-04", "江虹路 · 沟槽作业区", "回填前核验", "Polygon",
                [[[120.198174, 30.19692], [120.19815, 30.19692], [120.198116, 30.19719],
                  [120.198144, 30.19719], [120.198174, 30.19692]]]),
        route("BJ-05", "滨江北段 · 通信管线", "管道敷设核验",
              [[120.19161, 30.21052], [120.19184, 30.21026], [120.19194, 30.21008]]),
        route("BJ-06", "长河路北段 · 光缆联络线", "管线路由定位",
              [[120.18279, 30.20178], [120.18254, 30.20264], [120.18228, 30.20377], [120.18211, 30.20451]]),
        point("BJ-07", "江南大道中段 · 通信手孔", "手孔及井盖巡检", [120.19302, 30.19904]),
        route("BJ-08", "江晖路中段 · 交接箱引入段", "管道敷设核验",
              [[120.20579, 30.20345], [120.20604, 30.20257], [120.20624, 30.20193]]),
        point("BJ-09", "滨盛路北侧 · 光缆接续点", "光缆接续检查", [120.21452, 30.21954]),
    ]),
]

MEDIA = {
    "BJ-01": ("jiangnan-trench", "江南大道沟槽回填前"),
    "BJ-02": ("binsheng-street-overview", "滨盛路施工区全景"),
    "BJ-03": ("jianghui-cabinet", "江晖路交接箱检查"),
    "BJ-05": ("binjiang-north-duct", "滨江北段管道敷设核验"),
    "XX-01": ("xixing-optical-handover", "西兴路机房光配架核对"),
    "XX-02": ("xixing-branch-location", "滨康路支线定位核对"),
    "PY-01": ("puyan-optical-patrol", "浦沿路通信手孔巡检"),
    "PY-02": ("puyan-corridor-overview", "闻涛路沿江通道俯瞰"),
    "BJ-R1": ("jiangnan-route-recheck", "江南大道工点复查"),
    "BJ-R2": ("jiangnan-restored-street", "江南大道路面恢复检查"),
}


def media(code):
    stem, title = MEDIA[code]
    return title + ".jpg", (HERE / "scenes" / (stem + ".jpg")).read_bytes(), "image/jpeg"


def run(client, operator, reviewer):
    def call(method, path, expected=200, *, review=False, **kwargs):
        response = client.request(method, path, headers={"X-API-Key": reviewer if review else operator}, **kwargs)
        if response.status_code != expected:
            raise RuntimeError(f"{method} {path}: HTTP {response.status_code}: {response.text[:280]}")
        return response.json()

    def order_state(code):
        return call("GET", f"work-orders/{orders[code]['id']}")["status"]

    def wait_for_review(job_id):
        for _ in range(120):
            job = call("GET", f"verifications/{job_id}")["job"]
            if job["status"] == "needs_review":
                return job
            if job["status"] == "failed":
                raise RuntimeError(f"Verification failed: {job['error']}")
            time.sleep(0.5)
        raise TimeoutError(f"Verification {job_id} did not reach review")

    def submit(code, minutes, coordinates=None):
        order = orders[code]
        data = {
            "analyzer": "stub",
            "location_source": "synthetic_demo" if coordinates else "unknown",
            "client_captured_at": (NOW - timedelta(minutes=minutes)).isoformat(),
            "metadata": PROVENANCE,
        }
        if coordinates:
            data.update(longitude=str(coordinates[0]), latitude=str(coordinates[1]), accuracy_m="8")
        result = call("POST", f"work-orders/{order['id']}/verifications", 202, data=data, files={"file": media(code)})
        return wait_for_review(result["job"]["id"])

    def findings(job):
        return [item for item in call("GET", "finding-cases", params={"project_id": job["project_id"]})
                if item["source_job_id"] == job["id"]]

    def triage(item, decision, reason):
        return call("POST", f"finding-cases/{item['id']}/triage", review=True, json={
            "request_id": str(uuid4()), "expected_version": item["version"], "decision": decision, "reason": reason,
            **({"confirmed_severity": "warning"} if decision == "confirm" else {}),
        })

    def seal(job, note, resolution=None):
        result = call("POST", f"verifications/{job['id']}/review", review=True, json={
            "decision": "approve", "reviewer": "项目复核组", "note": note,
            **({"remediation_resolution": resolution} if resolution else {}),
        })
        proof = call("GET", f"proofs/{result['proof']['id']}/verify", review=True)
        assert result["job"]["status"] == "approved" and proof["valid"] and all(proof["checks"].values())
        assert result["report"]["content"]["readable"]["version"] in (1, 2)
        return result

    def start(case, group, action):
        return call("POST", f"finding-cases/{case['id']}/start-remediation", json={
            "request_id": str(uuid4()), "expected_version": case["version"], "assignee": group,
            "action_description": action, "due_at": (NOW + timedelta(hours=30)).isoformat(),
        })

    def recheck(case, code, action, note, decision, minutes):
        attempt = call("POST", f"finding-cases/{case['id']}/remediation-attempts", 201, json={
            "client_request_id": str(uuid4()), "expected_version": case["version"], "action_description": action,
        })
        result = call("POST", "verifications", 202, data={
            "project_id": case["project_id"], "baseline_id": case["baseline_id"], "analyzer": "stub",
            "captured_at": (NOW - timedelta(minutes=minutes)).isoformat(), "remediation_attempt_id": attempt["id"],
            "metadata": PROVENANCE,
        }, files={"file": media(code)})
        assert order_state("BJ-01") == "remediating"
        result = seal(wait_for_review(result["id"]), note, resolution=decision)
        return call("GET", f"finding-cases/{case['id']}")["case"], result

    if call("GET", "readyz")["status"] != "ready":
        raise RuntimeError("Service is not ready")
    call("GET", "operations/verification-dispatch", review=True)
    occupied = {p["code"] for p in call("GET", "projects")} & {row[0] for row in PROJECTS}
    if occupied:
        raise RuntimeError("Case project codes already exist: " + ", ".join(sorted(occupied)))
    assert len({hashlib.sha256(media(code)[1]).digest() for code in MEDIA}) == len(MEDIA), "Duplicate media"
    projects = {}
    orders = {}
    for code, name, location, manager, features in PROJECTS:
        project = call("POST", "projects", 201, json={"code": code, "name": name, "location": location, "manager": manager})
        project_id = project["id"]
        projects[code] = project_id
        package = {"schema_version": "1.0", "package_code": f"{code}-R2", "source_crs_epsg": 4326,
                   "design_version": "施工图 R2", "purpose": "demo", "synthetic": True, "layers": {}}
        for layer, item in features:
            package["layers"].setdefault(layer, {"features": []})["features"].append(item)
        imported = call("POST", f"projects/{project_id}/design-packages/import-json", 201,
                        files={"file": (code + ".json", json.dumps(package, ensure_ascii=False).encode(), "application/json")})
        assert len(imported["objects"]) == len(features)
        for obj in imported["objects"]:
            unit = obj["object_code"]
            order = call("POST", f"projects/{project_id}/work-orders", 201, json={
                "engineering_object_id": obj["id"], "work_order_code": unit + "-工单",
                "spatial_tolerance_m": 35, "gps_accuracy_threshold_m": 25,
                "notes": "采集工点全貌和对应工序资料，对照施工图后提交复核。",
            })
            orders[unit] = call("POST", f"work-orders/{order['id']}/assign", json={"assigned_to": manager})

    clean = submit("BJ-02", 135, (120.219456, 30.224011))
    for case in findings(clean):
        triage(case, "dismiss", "施工区范围已核对，本次不派发整改。")
    approved = seal(clean, "已核对施工区范围和设计版本，本次资料归档。")
    assert order_state("BJ-02") == "approved"

    main = submit("BJ-01", 120)
    [case] = findings(main)
    case = triage(case, "confirm", "补齐工点定位与路面恢复影像。")
    seal(main, "原始影像已登记，按工单补采定位与恢复资料。")
    assert order_state("BJ-01") == "deviation"
    case = start(case, "滨江工程组", "复查手孔与路由位置，记录路面恢复范围。")
    case, _ = recheck(case, "BJ-R1", "补拍手孔和工点周边。",
                      "手孔位置已记录，继续补充恢复范围。", "not_resolved", 50)
    assert case["status"] == "remediation_in_progress" and order_state("BJ-01") == "remediating"
    case, closure = recheck(case, "BJ-R2", "补齐路面恢复和井盖周边影像。",
                            "路面恢复范围和井盖周边已复核，关闭整改。", "resolved", 30)
    assert case["status"] == "closed" and order_state("BJ-01") == "closed"

    cabinet = submit("XX-01", 70, (120.221258, 30.174538))
    for case_item in findings(cabinet):
        triage(case_item, "dismiss", "已核对机房配线架和端口清单，本次不派发整改。")
    seal(cabinet, "完成光纤配线架与端口清单核对，本次资料归档。")
    field = submit("XX-02", 63)
    [issue] = findings(field)
    issue = triage(issue, "confirm", "需补充滨康路支线工点定位。")
    seal(field, "保留初次资料，派发工点补采。")
    working = start(issue, "西兴运维组", "补采位置及与街边交接箱的相对关系。")
    assert order_state("XX-02") == "remediating"

    submit("BJ-03", 37, (120.205692, 30.203623))
    submit("PY-01", 29, (120.142089, 30.175908))
    submit("PY-02", 22, (120.129878, 30.163018))
    north = submit("BJ-05", 13, (120.19184, 30.21026))
    assert order_state("BJ-05") == "needs_review"

    return {
        "projects": projects, "orders": {code: order["id"] for code, order in orders.items()},
        "north_job": north["id"], "closed_case": case["id"], "working_case": working["id"],
        "approved_report": approved["report"]["id"], "closure_report": closure["report"]["id"],
        "closure_proof": closure["proof"]["id"],
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000/api/v1/")
    args = parser.parse_args()
    with httpx.Client(base_url=args.base_url.rstrip("/") + "/", trust_env=False, timeout=35) as client:
        result = run(client, os.environ["FENGMOU_OPERATOR_API_KEY"], os.environ["FENGMOU_REVIEWER_API_KEY"])
    print(json.dumps(result, ensure_ascii=False, indent=2))
