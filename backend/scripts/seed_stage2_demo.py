#!/usr/bin/env python3
"""Idempotently create the Stage 2 synthetic browser-preview demo through public APIs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time
from typing import Any

import httpx


PROJECT_CODE = "DEMO-STAGE2-001"
SAMPLE_SHA256 = "39960190a5439a902dbc63ed60d7238b15ebc8f4b03951bd74f6d74f7f13c6e4"


def parse_args() -> argparse.Namespace:
    project_root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(
        description=(
            "Create or reuse a synthetic H.264 demo and complete upload, demo_fixture analysis, "
            "human review, report, proof archive and integrity verification."
        )
    )
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


def require_ok(response: httpx.Response) -> httpx.Response:
    if response.is_success:
        return response
    try:
        detail = response.json().get("detail", response.text)
    except (ValueError, AttributeError):
        detail = response.text
    raise RuntimeError(f"API {response.request.method} {response.request.url.path} failed ({response.status_code}): {detail}")


def auth_headers(key: str) -> dict[str, str]:
    return {"X-API-Key": key}


def find_or_create_project(client: httpx.Client, operator_key: str) -> dict[str, Any]:
    projects = require_ok(client.get("/projects", headers=auth_headers(operator_key))).json()
    project = next((item for item in projects if item["code"] == PROJECT_CODE), None)
    if project is not None:
        return project
    return require_ok(
        client.post(
            "/projects",
            headers=auth_headers(operator_key),
            json={
                "code": PROJECT_CODE,
                "name": "滨江通信管线可信交付示范工程",
                "location": "杭州滨江 · 匿名化 A-12 工点",
                "manager": "谢涛旭",
            },
        )
    ).json()


def find_or_create_baseline(
    client: httpx.Client,
    operator_key: str,
    project_id: str,
) -> dict[str, Any]:
    baselines = require_ok(
        client.get(f"/projects/{project_id}/baselines", headers=auth_headers(operator_key))
    ).json()
    baseline = next(
        (
            item
            for item in baselines
            if item["site_id"] == "SITE-A12"
            and item["procedure_code"] == "TRENCH-BEFORE-BACKFILL"
            and item["version"] == "design-v1"
        ),
        None,
    )
    if baseline is not None:
        return baseline
    return require_ok(
        client.post(
            f"/projects/{project_id}/baselines",
            headers=auth_headers(operator_key),
            json={
                "site_id": "SITE-A12",
                "procedure_code": "TRENCH-BEFORE-BACKFILL",
                "version": "design-v1",
                "source_type": "manual",
                "expected": {
                    "scene_type": "trench",
                    "measurements": {
                        "min_depth_m": 0.8,
                        "min_spacing_m": 0.2,
                        "expected_quantity": 4,
                        "expected_specification": "PE110 x 4",
                    },
                },
            },
        )
    ).json()


def find_existing_demo(
    client: httpx.Client,
    operator_key: str,
    project_id: str,
) -> dict[str, Any] | None:
    jobs = require_ok(
        client.get(
            "/verifications",
            params={"project_id": project_id},
            headers=auth_headers(operator_key),
        )
    ).json()
    for job in jobs:
        detail = require_ok(
            client.get(f"/verifications/{job['id']}", headers=auth_headers(operator_key))
        ).json()
        if detail["evidence"]["sha256"] == SAMPLE_SHA256:
            return detail
    return None


def submit_demo(
    client: httpx.Client,
    operator_key: str,
    project_id: str,
    baseline_id: str,
    video_path: Path,
) -> dict[str, Any]:
    with video_path.open("rb") as handle:
        response = client.post(
            "/verifications",
            headers=auth_headers(operator_key),
            data={
                "project_id": project_id,
                "baseline_id": baseline_id,
                "analyzer": "demo_fixture",
                "device_id": "DEMO-CAM-H264",
                "metadata": json.dumps(
                    {
                        "source": "stage2-browser-compatible-demo",
                        "privacy": "synthetic-geometric-sample",
                        "codec_note": "H.264 yuv420p for browser preview",
                    },
                    ensure_ascii=False,
                ),
            },
            files={"file": ("event-browser-compatible.mp4", handle, "video/mp4")},
        )
    return require_ok(response).json()


def wait_for_analysis(
    client: httpx.Client,
    operator_key: str,
    job_id: str,
    timeout: float,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        detail = require_ok(
            client.get(f"/verifications/{job_id}", headers=auth_headers(operator_key))
        ).json()
        status = detail["job"]["status"]
        if status in {"needs_review", "approved", "failed", "rejected"}:
            return detail
        time.sleep(0.2)
    raise TimeoutError(f"Job {job_id} did not reach a reviewable state within {timeout:.1f}s")


def approve_if_needed(
    client: httpx.Client,
    reviewer_key: str,
    detail: dict[str, Any],
) -> dict[str, Any]:
    if detail["job"]["status"] == "approved":
        return detail
    if detail["job"]["status"] != "needs_review":
        raise RuntimeError(
            f"Synthetic demo job stopped at {detail['job']['status']}: {detail['job'].get('error')}"
        )
    outcome = require_ok(
        client.post(
            f"/verifications/{detail['job']['id']}/review",
            headers=auth_headers(reviewer_key),
            json={
                "decision": "approve",
                "reviewer": "本地演示审核员",
                "note": (
                    "仅批准 H.264 浏览器兼容性与完整证据链联调；demo_fixture 为合成结果，"
                    "不构成真实模型输出或竞赛指标。"
                ),
            },
        )
    ).json()
    return {
        "job": outcome["job"],
        "evidence": detail["evidence"],
        "report": outcome["report"],
        "proof": outcome["proof"],
    }


def main() -> int:
    args = parse_args()
    video_path = args.video.expanduser().resolve()
    if not video_path.is_file():
        raise FileNotFoundError(f"Synthetic demo video not found: {video_path}")

    with httpx.Client(base_url=args.base_url.rstrip("/"), timeout=30.0) as client:
        require_ok(client.get("/readyz"))
        meta = require_ok(client.get("/meta")).json()
        if not meta["adapters"]["demo_fixture"]["enabled"]:
            raise RuntimeError("demo_fixture is disabled; restart with FENGMOU_ALLOW_DEMO_ANALYZER=true")

        project = find_or_create_project(client, args.operator_key)
        baseline = find_or_create_baseline(client, args.operator_key, project["id"])
        detail = find_existing_demo(client, args.operator_key, project["id"])
        reused = detail is not None
        if detail is None:
            job = submit_demo(client, args.operator_key, project["id"], baseline["id"], video_path)
            detail = wait_for_analysis(
                client,
                args.operator_key,
                job["id"],
                args.poll_timeout,
            )
        completed = approve_if_needed(client, args.reviewer_key, detail)
        proof = completed.get("proof")
        if proof is None:
            completed = require_ok(
                client.get(
                    f"/verifications/{completed['job']['id']}",
                    headers=auth_headers(args.operator_key),
                )
            ).json()
            proof = completed["proof"]
        integrity = require_ok(
            client.get(f"/proofs/{proof['id']}/verify", headers=auth_headers(args.auditor_key))
        ).json()

    output = {
        "reused_existing_demo": reused,
        "project_id": project["id"],
        "project_code": project["code"],
        "baseline_id": baseline["id"],
        "job_id": completed["job"]["id"],
        "evidence_id": completed["evidence"]["id"],
        "evidence_sha256": completed["evidence"]["sha256"],
        "proof_id": proof["id"],
        "proof_valid": integrity["valid"],
        "proof_checks": integrity["checks"],
        "truth_boundary": "synthetic demo_fixture; not real inference or a competition metric",
        "project_url": f"{args.frontend_url.rstrip('/')}/projects/{project['id']}",
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0 if integrity["valid"] and all(integrity["checks"].values()) else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (FileNotFoundError, RuntimeError, TimeoutError, httpx.HTTPError) as exc:
        print(f"seed_stage2_demo: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
