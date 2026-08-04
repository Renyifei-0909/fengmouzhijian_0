"""Pytest plugin: capture exact failed report.nodeid values (no terminal parsing).

Enable with: pytest -p tests.pytest_failed_nodes_plugin
Or import via conftest registration.

Writes JSON artifact under backend/test-artifacts/ (gitignored).
"""

from __future__ import annotations

import hashlib
import json
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def pytest_addoption(parser: Any) -> None:
    group = parser.getgroup("fengmou-baseline")
    group.addoption(
        "--fengmou-failed-nodes-out",
        action="store",
        default=None,
        help="Write sorted failed node ids JSON to this path",
    )
    group.addoption(
        "--fengmou-checkpoint",
        action="store",
        default="unspecified",
        help="Checkpoint label stored in failed-nodes artifact",
    )


def pytest_runtest_logreport(report: Any) -> None:
    # Capture call-phase failures and setup/teardown errors that fail the test.
    if report.failed and report.when in {"setup", "call", "teardown"}:
        nodeid = str(report.nodeid)
        # TestReport has no .config; stash is set on session in pytest_sessionstart.
        # Fallback: use a module-level list filled via pytest_configure.
        global _FAILED
        if nodeid not in _FAILED:
            _FAILED.append(nodeid)


_FAILED: list[str] = []


def pytest_configure(config: Any) -> None:
    global _FAILED
    _FAILED = []
    config._fengmou_failed_nodeids = _FAILED  # type: ignore[attr-defined]


def pytest_sessionfinish(session: Any, exitstatus: int) -> None:
    config = session.config
    nodes = sorted(set(_FAILED))
    config._fengmou_failed_nodeids = nodes  # type: ignore[attr-defined]
    out = config.getoption("--fengmou-failed-nodes-out", default=None)
    if not out:
        return
    path = Path(out)
    path.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256("\n".join(nodes).encode("utf-8")).hexdigest()
    payload = {
        "schema_version": "1.0",
        "captured_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00"),
        "checkpoint": config.getoption("--fengmou-checkpoint", default="unspecified"),
        "exit_code": int(exitstatus),
        "platform": {
            "os": platform.system(),
            "python": platform.python_version(),
            "executable": sys.executable,
        },
        "failed_node_ids": nodes,
        "failed_node_id_count": len(nodes),
        "failed_set_sha256": digest,
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
