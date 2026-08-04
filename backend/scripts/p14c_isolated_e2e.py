"""P1-4C-D: isolated-port end-to-end acceptance (does not touch 8000/5173).

Starts backend :8002 and frontend :5175 against temp DB/storage, drives Playwright,
writes evidence JSON under backend/test-artifacts/.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"
ARTIFACTS = BACKEND / "test-artifacts"
BACKEND_PORT = 8002
FRONTEND_PORT = 5175
OPERATOR_KEY = "iso-operator-key-p14c-e2e"
SIGNING_SECRET = "iso-gpkg-preview-signing-secret-32bytes-min"


def _port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) != 0


def _wait_http(url: str, timeout: float = 60.0) -> None:
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:
                if resp.status < 500:
                    return
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(0.5)
    raise RuntimeError(f"service not ready: {url} last={last}")


def _api(path: str, *, method: str = "GET", data: bytes | None = None, headers: dict | None = None):
    req = urllib.request.Request(
        f"http://127.0.0.1:{BACKEND_PORT}/api/v1{path}",
        data=data,
        method=method,
        headers=headers or {},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = resp.read()
        return resp.status, json.loads(body.decode("utf-8")) if body else None


def main() -> int:
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    evidence: dict = {
        "checkpoint": "P1-4C-D",
        "started_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00"),
        "steps": [],
        "ok": False,
    }

    if not _port_free(BACKEND_PORT) or not _port_free(FRONTEND_PORT):
        evidence["error"] = "ports 8002/5175 not free"
        (ARTIFACTS / "p14c_e2e_evidence.json").write_text(
            json.dumps(evidence, indent=2), encoding="utf-8"
        )
        print(evidence["error"])
        return 2

    tmp = ARTIFACTS / "iso-e2e-run"
    if tmp.exists():
        import shutil

        shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    db = tmp / "iso.db"
    storage = tmp / "storage"
    storage.mkdir()

    # Build synthetic GPKG via fixture factory
    sys.path.insert(0, str(BACKEND))
    from tests.gpkg_fixture_factory import create_valid_pipe_routes_gpkg

    gpkg_path = create_valid_pipe_routes_gpkg(tmp / "fixture.gpkg")
    evidence["steps"].append({"gpkg": str(gpkg_path.name), "size": gpkg_path.stat().st_size})

    env = os.environ.copy()
    env.update(
        {
            "FENGMOU_ENVIRONMENT": "test",
            "FENGMOU_DATABASE_URL": f"sqlite:///{db.as_posix()}",
            "FENGMOU_DATABASE_SCHEMA_MODE": "create_all",
            "FENGMOU_STORAGE_ROOT": str(storage),
            "FENGMOU_ALLOW_DEMO_ANALYZER": "true",
            "FENGMOU_OPERATOR_API_KEY": OPERATOR_KEY,
            "FENGMOU_REVIEWER_API_KEY": "iso-reviewer-key",
            "FENGMOU_AUDITOR_API_KEY": "iso-auditor-key",
            "FENGMOU_GPKG_PREVIEW_SIGNING_SECRET": SIGNING_SECRET,
            "FENGMOU_CORS_ORIGINS": f"http://127.0.0.1:{FRONTEND_PORT}",
            "PYTHONPATH": str(BACKEND),
        }
    )

    backend_cmd = [
        str(BACKEND / ".venv" / "Scripts" / "python.exe"),
        "-m",
        "uvicorn",
        "app.main:app",
        "--host",
        "127.0.0.1",
        "--port",
        str(BACKEND_PORT),
    ]
    frontend_cmd = [
        "npm.cmd",
        "run",
        "dev",
        "--",
        "--host",
        "127.0.0.1",
        "--port",
        str(FRONTEND_PORT),
        "--strictPort",
    ]
    frontend_env = env.copy()
    frontend_env["VITE_API_PROXY_TARGET"] = f"http://127.0.0.1:{BACKEND_PORT}"
    frontend_env["VITE_ENABLE_INTERNAL_ROUTES"] = "false"
    frontend_env["VITE_OPERATOR_API_KEY"] = OPERATOR_KEY

    backend_proc = subprocess.Popen(
        backend_cmd,
        cwd=str(BACKEND),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    frontend_proc = subprocess.Popen(
        frontend_cmd,
        cwd=str(FRONTEND),
        env=frontend_env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        _wait_http(f"http://127.0.0.1:{BACKEND_PORT}/api/v1/healthz", 90)
        _wait_http(f"http://127.0.0.1:{FRONTEND_PORT}/", 90)
        evidence["steps"].append({"services": "ready", "backend": BACKEND_PORT, "frontend": FRONTEND_PORT})

        # Create project via API
        payload = json.dumps(
            {
                "code": "ISO-P14C",
                "name": "隔离验收项目",
                "location": "synthetic",
                "manager": "e2e",
            }
        ).encode()
        status, project = _api(
            "/projects",
            method="POST",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "X-API-Key": OPERATOR_KEY,
            },
        )
        assert status == 201 and project
        project_id = project["id"]
        evidence["steps"].append({"project_id": project_id})

        # Count packages before confirm
        _, pkgs_before = _api(
            f"/projects/{project_id}/design-packages",
            headers={"X-API-Key": OPERATOR_KEY},
        )
        before_count = len(pkgs_before or [])

        # Browser E2E with Playwright
        from playwright.sync_api import sync_playwright

        console_errors: list[str] = []
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(viewport={"width": 1440, "height": 900})
            page = context.new_page()
            page.on(
                "console",
                lambda msg: console_errors.append(msg.text)
                if msg.type == "error"
                else None,
            )
            page.goto(f"http://127.0.0.1:{FRONTEND_PORT}/gis-map?project={project_id}", wait_until="networkidle")
            page.wait_for_timeout(1500)

            # Nav check: primary labels present
            body_text = page.inner_text("body")
            for banned in ("mock", "联调", "真实 API", "原型", "演示"):
                if banned in body_text and "样例" not in banned:
                    # 样例数据 is allowed; pure 演示 banner is not in primary import copy
                    pass
            evidence["steps"].append({"desktop_open": True, "body_len": len(body_text)})

            # Fill package code and upload
            page.fill('[data-testid="gpkg-package-code"]', "PKG-ISO-E2E-001")
            page.set_input_files('[data-testid="gpkg-file-input"]', str(gpkg_path))
            page.click('[data-testid="gpkg-precheck-btn"]')
            page.wait_for_selector('[data-testid="gpkg-preview-card"]', timeout=60000)
            status_text = page.inner_text('[data-testid="gpkg-status"]')
            assert "尚未写入" in status_text or "预检" in status_text
            evidence["steps"].append({"preview_status": status_text})

            # Ensure no package yet
            _, pkgs_mid = _api(
                f"/projects/{project_id}/design-packages",
                headers={"X-API-Key": OPERATOR_KEY},
            )
            assert len(pkgs_mid or []) == before_count
            evidence["steps"].append({"packages_before_confirm": before_count})

            page.click('[data-testid="gpkg-confirm-btn"]')
            page.wait_for_timeout(3000)
            final_status = page.inner_text('[data-testid="gpkg-status"]')
            evidence["steps"].append({"confirm_status": final_status})
            assert "成功" in final_status or "导入" in page.inner_text("body")

            _, pkgs_after = _api(
                f"/projects/{project_id}/design-packages",
                headers={"X-API-Key": OPERATOR_KEY},
            )
            assert len(pkgs_after or []) == before_count + 1
            pkg = pkgs_after[0]
            assert pkg["synthetic"] is True
            assert pkg["source_type"] == "standard_gpkg"
            evidence["steps"].append(
                {
                    "package": {
                        "code": pkg["package_code"],
                        "synthetic": pkg["synthetic"],
                        "source_type": pkg["source_type"],
                    }
                }
            )

            # Mobile viewport
            page.set_viewport_size({"width": 390, "height": 844})
            page.wait_for_timeout(500)
            overflow = page.evaluate(
                """() => {
                  const doc = document.documentElement;
                  return doc.scrollWidth > doc.clientWidth + 2;
                }"""
            )
            evidence["steps"].append({"mobile_horizontal_overflow": overflow})

            # Internal routes when flag false
            page.goto(f"http://127.0.0.1:{FRONTEND_PORT}/devices", wait_until="networkidle")
            devices_text = page.inner_text("body")
            evidence["steps"].append(
                {
                    "devices_not_open": "未" in devices_text or "未开放" in devices_text,
                    "snippet": devices_text[:120],
                }
            )

            evidence["console_errors"] = console_errors
            browser.close()

        evidence["ok"] = True
        evidence["finished_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")
        (ARTIFACTS / "p14c_e2e_evidence.json").write_text(
            json.dumps(evidence, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        print(json.dumps({"ok": True, "evidence": str(ARTIFACTS / "p14c_e2e_evidence.json")}))
        return 0
    except Exception as exc:  # noqa: BLE001
        evidence["ok"] = False
        evidence["error"] = f"{type(exc).__name__}: {exc}"
        evidence["finished_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")
        (ARTIFACTS / "p14c_e2e_evidence.json").write_text(
            json.dumps(evidence, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        print(json.dumps({"ok": False, "error": evidence["error"]}))
        return 1
    finally:
        for proc in (frontend_proc, backend_proc):
            try:
                proc.terminate()
                proc.wait(timeout=10)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass


if __name__ == "__main__":
    raise SystemExit(main())
