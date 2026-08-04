#!/usr/bin/env python3
"""Exercise the real HTTP bridge against the reference analyzer service.

This script creates a complete review delivery chain through public platform
APIs.  The reference analyzer is deliberately a non-evaluated stub; success
only proves protocol and workflow compatibility, never model quality.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time
from typing import Any

import httpx


PROJECT_CODE = "REMOTE-REFERENCE-001"
SAMPLE_SHA256 = "39960190a5439a902dbc63ed60d7238b15ebc8f4b03951bd74f6d74f7f13c6e4"
EXPECTED_PROVENANCE_KIND = "remote_contract_stub"


def parse_args() -> argparse.Namespace:
    project_root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(
        description=(
            "Run upload -> real remote HTTP stub -> review -> report -> proof verification. "
            "This is a protocol smoke test, not a model evaluation."
        )
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:8011/api/v1")
    parser.add_argument("--frontend-url", default="http://127.0.0.1:5173")
    parser.add_argument("--operator-key", default="reference-operator-change-me")
    parser.add_argument("--reviewer-key", default="reference-reviewer-change-me")
    parser.add_argument("--auditor-key", default="reference-auditor-change-me")
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
    raise RuntimeError(
        f"API {response.request.method} {response.request.url.path} failed "
        f"({response.status_code}): {detail}"
    )


def auth_headers(key: str) -> dict[str, str]:
    return {"X-API-Key": key}


def find_or_create_project(client: httpx.Client, operator_key: str) -> dict[str, Any]:
    projects = require_ok(client.get("/projects", headers=auth_headers(operator_key))).json()
    existing = next((item for item in projects if item["code"] == PROJECT_CODE), None)
    if existing is not None:
        return existing
    return require_ok(
        client.post(
            "/projects",
            headers=auth_headers(operator_key),
            json={
                "code": PROJECT_CODE,
                "name": "远程算法合同参考联调",
                "location": "本机隔离环境 · 合成媒体",
                "manager": "协议联调脚本",
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
    existing = next(
        (
            item
            for item in baselines
            if item["site_id"] == "REFERENCE-SITE"
            and item["procedure_code"] == "REMOTE-CONTRACT-SMOKE"
            and item["version"] == "reference-v1"
        ),
        None,
    )
    if existing is not None:
        return existing
    return require_ok(
        client.post(
            f"/projects/{project_id}/baselines",
            headers=auth_headers(operator_key),
            json={
                "site_id": "REFERENCE-SITE",
                "procedure_code": "REMOTE-CONTRACT-SMOKE",
                "version": "reference-v1",
                "source_type": "manual",
                "expected": {
                    "scene_type": "protocol_smoke_fixture",
                    "measurements": {},
                },
            },
        )
    ).json()


def find_existing_job(
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
        if job["analyzer_name"] != "remote_http":
            continue
        detail = require_ok(
            client.get(f"/verifications/{job['id']}", headers=auth_headers(operator_key))
        ).json()
        if detail["evidence"]["sha256"] == SAMPLE_SHA256:
            return detail
    return None


def submit_remote(
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
                "analyzer": "remote_http",
                "device_id": "REFERENCE-CONTRACT-CLIENT",
                "metadata": json.dumps(
                    {
                        "source": "stage2-browser-compatible-demo",
                        "privacy": "synthetic-geometric-sample",
                        "purpose": "remote protocol smoke only",
                    },
                    ensure_ascii=False,
                ),
            },
            files={"file": ("event-browser-compatible.mp4", handle, "video/mp4")},
        )
    return require_ok(response).json()


def wait_for_terminal_analysis(
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
    raise TimeoutError(f"Job {job_id} did not finish analysis within {timeout:.1f}s")


def assert_reference_truth_boundary(detail: dict[str, Any]) -> None:
    job = detail["job"]
    if job["status"] == "failed":
        raise RuntimeError(f"Reference remote analysis failed: {job.get('error')}")
    result = job.get("result") or {}
    failures: list[str] = []
    if result.get("analysis_mode") != "remote_http":
        failures.append("analysis_mode is not remote_http")
    if result.get("evidence_grade") is not False:
        failures.append("evidence_grade is not false")
    if result.get("accuracy_claim") is not None:
        failures.append("accuracy_claim is not null")
    if (result.get("provenance") or {}).get("kind") != EXPECTED_PROVENANCE_KIND:
        failures.append("provenance.kind is not remote_contract_stub")
    runtime = (result.get("provenance") or {}).get("runtime") or {}
    if runtime.get("mode") != "stub" or runtime.get("model_loaded") is not False:
        failures.append("runtime identity does not describe an unloaded contract stub")
    if (result.get("provenance") or {}).get("synthetic") is not True:
        failures.append("reference stub is not isolated as synthetic demo output")
    limitations = (result.get("provenance") or {}).get("limitations") or []
    if not any("stub" in str(item).lower() or "not evaluated" in str(item).lower() for item in limitations):
        failures.append("response lacks an explicit stub/not-evaluated limitation")
    if failures:
        raise RuntimeError("Reference truth-boundary check failed: " + "; ".join(failures))


def approve_if_needed(
    client: httpx.Client,
    reviewer_key: str,
    detail: dict[str, Any],
) -> dict[str, Any]:
    if detail["job"]["status"] == "approved":
        return detail
    if detail["job"]["status"] != "needs_review":
        raise RuntimeError(f"Reference job cannot be reviewed from {detail['job']['status']}")
    return require_ok(
        client.post(
            f"/verifications/{detail['job']['id']}/review",
            headers=auth_headers(reviewer_key),
            json={
                "decision": "approve",
                "reviewer": "远程协议联调审核员",
                "note": (
                    "仅批准参考算法服务的 HTTP 合同与交付链联调；默认 predict() 是空输出占位器，"
                    "本记录不构成真实识别结果、模型有效性或竞赛指标。"
                ),
            },
        )
    ).json()


def main() -> int:
    args = parse_args()
    video_path = args.video.expanduser().resolve()
    if not video_path.is_file():
        raise FileNotFoundError(f"Synthetic demo video not found: {video_path}")

    with httpx.Client(base_url=args.base_url.rstrip("/"), timeout=30.0) as client:
        require_ok(client.get("/readyz"))
        meta = require_ok(client.get("/meta")).json()
        remote_meta = meta["adapters"]["remote_http"]
        if not remote_meta["enabled"]:
            raise RuntimeError("remote_http is disabled; start the isolated backend with reference settings")

        project = find_or_create_project(client, args.operator_key)
        baseline = find_or_create_baseline(client, args.operator_key, project["id"])
        detail = find_existing_job(client, args.operator_key, project["id"])
        reused = detail is not None
        if detail is None:
            job = submit_remote(
                client,
                args.operator_key,
                project["id"],
                baseline["id"],
                video_path,
            )
            detail = wait_for_terminal_analysis(
                client,
                args.operator_key,
                job["id"],
                args.poll_timeout,
            )
        assert_reference_truth_boundary(detail)
        outcome = approve_if_needed(client, args.reviewer_key, detail)
        proof = outcome.get("proof")
        if proof is None:
            refreshed = require_ok(
                client.get(
                    f"/verifications/{outcome['job']['id']}",
                    headers=auth_headers(args.operator_key),
                )
            ).json()
            proof = refreshed["proof"]
        integrity = require_ok(
            client.get(f"/proofs/{proof['id']}/verify", headers=auth_headers(args.auditor_key))
        ).json()

    output = {
        "reused_existing_job": reused,
        "project_id": project["id"],
        "project_code": project["code"],
        "baseline_id": baseline["id"],
        "job_id": outcome["job"]["id"],
        "job_status": outcome["job"]["status"],
        "analyzer_name": outcome["job"]["analyzer_name"],
        "proof_id": proof["id"],
        "proof_valid": integrity["valid"],
        "proof_checks": integrity["checks"],
        "truth_boundary": (
            "real HTTP protocol and delivery-chain smoke test using a synthetic video and "
            "an explicitly non-evaluated reference stub; not real inference or a competition metric"
        ),
        "project_url": f"{args.frontend_url.rstrip('/')}/projects/{project['id']}",
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0 if integrity["valid"] and all(integrity["checks"].values()) else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (FileNotFoundError, RuntimeError, TimeoutError, httpx.HTTPError) as exc:
        print(f"seed_remote_reference_demo: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
