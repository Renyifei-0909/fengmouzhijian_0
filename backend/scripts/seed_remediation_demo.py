#!/usr/bin/env python3
"""Create or reuse one fully sealed synthetic remediation demo through public APIs.

The generated cases are always ``scope=demo``.  This script proves only the
human-triage/remediation/re-verification/report/proof workflow; it does not
exercise a real vision model or produce a competition metric.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time
from typing import Any
import uuid

import httpx


PROJECT_CODE = "REMEDIATION-DEMO-001"


def parse_args() -> argparse.Namespace:
    project_root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description="Seed the synthetic finding-to-remediation closed loop")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000/api/v1")
    parser.add_argument("--frontend-url", default="http://127.0.0.1:5173")
    parser.add_argument("--operator-key", default="local-operator-change-me")
    parser.add_argument("--reviewer-key", default="local-reviewer-change-me")
    parser.add_argument("--auditor-key", default="local-auditor-change-me")
    parser.add_argument(
        "--video",
        type=Path,
        default=project_root / "examples" / "stage2-demo" / "event-browser-compatible.mp4",
    )
    parser.add_argument("--poll-timeout", type=float, default=30.0)
    return parser.parse_args()


def headers(key: str) -> dict[str, str]:
    return {"X-API-Key": key}


def require_ok(response: httpx.Response) -> httpx.Response:
    if response.is_success:
        return response
    try:
        detail = response.json().get("detail", response.text)
    except (AttributeError, ValueError):
        detail = response.text
    raise RuntimeError(
        f"API {response.request.method} {response.request.url.path} failed "
        f"({response.status_code}): {detail}"
    )


def wait_for_review(client: httpx.Client, job_id: str, operator_key: str, timeout: float) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        detail = require_ok(client.get(f"/verifications/{job_id}", headers=headers(operator_key))).json()
        if detail["job"]["status"] in {"needs_review", "approved", "rejected", "failed"}:
            return detail
        time.sleep(0.2)
    raise TimeoutError(f"Verification {job_id} did not become reviewable within {timeout:.1f}s")


def find_or_create_project(client: httpx.Client, operator_key: str) -> dict[str, Any]:
    projects = require_ok(client.get("/projects", headers=headers(operator_key))).json()
    existing = next((item for item in projects if item["code"] == PROJECT_CODE), None)
    if existing:
        return existing
    return require_ok(
        client.post(
            "/projects",
            headers=headers(operator_key),
            json={
                "code": PROJECT_CODE,
                "name": "烽眸智鉴 · 合成整改闭环演示",
                "location": "本机匿名化合成工点",
                "manager": "谢涛旭",
            },
        )
    ).json()


def find_or_create_baseline(client: httpx.Client, project_id: str, operator_key: str) -> dict[str, Any]:
    baselines = require_ok(
        client.get(f"/projects/{project_id}/baselines", headers=headers(operator_key))
    ).json()
    existing = next((item for item in baselines if item["version"] == "remediation-demo-v1"), None)
    if existing:
        return existing
    return require_ok(
        client.post(
            f"/projects/{project_id}/baselines",
            headers=headers(operator_key),
            json={
                "site_id": "SYNTHETIC-SITE-R1",
                "procedure_code": "REMEDIATION-RECHECK",
                "version": "remediation-demo-v1",
                "source_type": "manual",
                "expected": {
                    "scene_type": "synthetic_geometry",
                    "measurements": {"expected_quantity": "force-demo-deviation"},
                },
            },
        )
    ).json()


def upload(
    client: httpx.Client,
    *,
    project_id: str,
    baseline_id: str,
    video: Path,
    operator_key: str,
    attempt_id: str | None = None,
) -> dict[str, Any]:
    data = {
        "project_id": project_id,
        "baseline_id": baseline_id,
        "analyzer": "demo_fixture",
        "device_id": "SYNTHETIC-REMEDIATION-CAMERA",
        "metadata": json.dumps(
            {
                "source": "synthetic-remediation-demo",
                "privacy": "no real person or site",
                "truth_boundary": "workflow-only fixture",
            },
            ensure_ascii=False,
        ),
    }
    if attempt_id:
        data["remediation_attempt_id"] = attempt_id
    with video.open("rb") as handle:
        response = client.post(
            "/verifications",
            headers=headers(operator_key),
            data=data,
            files={"file": (video.name, handle, "video/mp4")},
        )
    return require_ok(response).json()


def main() -> int:
    args = parse_args()
    video = args.video.expanduser().resolve()
    if not video.is_file():
        raise FileNotFoundError(f"Synthetic demo video not found: {video}")

    with httpx.Client(base_url=args.base_url.rstrip("/"), timeout=30.0) as client:
        require_ok(client.get("/readyz"))
        meta = require_ok(client.get("/meta")).json()
        if not meta["adapters"]["demo_fixture"]["enabled"]:
            raise RuntimeError("demo_fixture is disabled; enable it only in a local demo environment")

        project = find_or_create_project(client, args.operator_key)
        baseline = find_or_create_baseline(client, project["id"], args.operator_key)
        existing_cases = require_ok(
            client.get(
                "/finding-cases",
                params={"project_id": project["id"], "status": "closed", "scope": "demo"},
                headers=headers(args.operator_key),
            )
        ).json()
        if existing_cases:
            case = existing_cases[0]
            detail = require_ok(
                client.get(f"/finding-cases/{case['id']}", headers=headers(args.operator_key))
            ).json()
            proof_id = detail["case"]["closure_proof_id"]
            proof_check = require_ok(
                client.get(f"/proofs/{proof_id}/verify", headers=headers(args.auditor_key))
            ).json()
            reused = True
            attempt = next(item for item in detail["attempts"] if item["proof_id"] == proof_id)
        else:
            reused = False
            source_job = upload(
                client,
                project_id=project["id"],
                baseline_id=baseline["id"],
                video=video,
                operator_key=args.operator_key,
            )
            source_detail = wait_for_review(client, source_job["id"], args.operator_key, args.poll_timeout)
            if source_detail["job"]["status"] != "needs_review":
                raise RuntimeError(f"Source demo stopped at {source_detail['job']['status']}")
            require_ok(
                client.post(
                    f"/verifications/{source_job['id']}/review",
                    headers=headers(args.reviewer_key),
                    json={
                        "decision": "approve",
                        "reviewer": "合成演示复核员",
                        "note": "仅批准合成工作流与原始证据封存，不确认现场事实或模型能力。",
                    },
                )
            )
            cases = require_ok(
                client.get(
                    "/finding-cases",
                    params={"project_id": project["id"]},
                    headers=headers(args.operator_key),
                )
            ).json()
            case = next(item for item in cases if item["source_job_id"] == source_job["id"])
            case = require_ok(
                client.post(
                    f"/finding-cases/{case['id']}/triage",
                    headers=headers(args.reviewer_key),
                    json={
                        "request_id": str(uuid.uuid4()),
                        "expected_version": case["version"],
                        "decision": "confirm",
                        "confirmed_severity": "warning",
                        "reason": "合成候选仅用于验证整改状态机，始终不计入运营统计。",
                    },
                )
            ).json()
            case = require_ok(
                client.post(
                    f"/finding-cases/{case['id']}/start-remediation",
                    headers=headers(args.operator_key),
                    json={
                        "request_id": str(uuid.uuid4()),
                        "expected_version": case["version"],
                        "assignee": "本地演示操作员",
                        "action_description": "重新采集合成几何视频并提交独立复验证据。",
                    },
                )
            ).json()
            attempt = require_ok(
                client.post(
                    f"/finding-cases/{case['id']}/remediation-attempts",
                    headers=headers(args.operator_key),
                    json={
                        "client_request_id": str(uuid.uuid4()),
                        "expected_version": case["version"],
                        "action_description": "已重新采集无真人、无真实现场的 H.264 合成视频。",
                    },
                )
            ).json()
            recheck_job = upload(
                client,
                project_id=project["id"],
                baseline_id=baseline["id"],
                video=video,
                operator_key=args.operator_key,
                attempt_id=attempt["id"],
            )
            recheck_detail = wait_for_review(client, recheck_job["id"], args.operator_key, args.poll_timeout)
            if recheck_detail["job"]["status"] != "needs_review":
                raise RuntimeError(f"Re-verification demo stopped at {recheck_detail['job']['status']}")
            outcome = require_ok(
                client.post(
                    f"/verifications/{recheck_job['id']}/review",
                    headers=headers(args.reviewer_key),
                    json={
                        "decision": "approve",
                        "reviewer": "合成整改复验员",
                        "note": "仅判定合成整改流程已完成；不代表真实现场缺陷、真实模型或竞赛指标。",
                        "remediation_resolution": "resolved",
                    },
                )
            ).json()
            proof_id = outcome["proof"]["id"]
            proof_check = require_ok(
                client.get(f"/proofs/{proof_id}/verify", headers=headers(args.auditor_key))
            ).json()
            detail = require_ok(
                client.get(f"/finding-cases/{case['id']}", headers=headers(args.operator_key))
            ).json()
            if detail["case"]["status"] != "closed" or detail["closure_evidence_status"] != "sealed":
                raise RuntimeError("Remediation case did not close with a sealed proof")

        attempt = next(item for item in detail["attempts"] if item["id"] == attempt["id"])
        output = {
            "reused_existing_demo": reused,
            "project_id": project["id"],
            "baseline_id": baseline["id"],
            "case_id": case["id"],
            "attempt_id": attempt["id"],
            "verification_job_id": attempt["verification_job_id"],
            "closure_proof_id": proof_id,
            "proof_valid": proof_check["valid"],
            "proof_checks": proof_check["checks"],
            "truth_boundary": "scope=demo synthetic fixture; workflow proof only, not real inference or metrics",
            "alarms_url": f"{args.frontend_url.rstrip('/')}/alarms",
        }
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return 0 if proof_check["valid"] and all(proof_check["checks"].values()) else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (FileNotFoundError, RuntimeError, TimeoutError, httpx.HTTPError) as exc:
        print(f"seed_remediation_demo: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
