"""P1-4C-A: TEST_BASELINE.json integrity (exact pytest node ids)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

BASELINE_PATH = Path(__file__).resolve().parents[2] / "docs" / "development" / "TEST_BASELINE.json"


def _load_baseline() -> dict:
    text = BASELINE_PATH.read_text(encoding="utf-8")
    assert "\\n" not in text[-4:] or text.endswith("\n"), "must not contain literal \\n tail corruption"
    # File may end with real newline only
    return json.loads(text)


def test_test_baseline_json_loads() -> None:
    data = _load_baseline()
    assert isinstance(data, dict)
    assert "schema_version" in data


def test_failed_node_ids_are_exact_pytest_nodeids() -> None:
    data = _load_baseline()
    suite = data.get("full_suite_windows_p14c") or data.get("full_suite_windows_p141")
    assert suite is not None, "missing full_suite_windows_p14c block"
    nodes = suite["failed_node_ids"]
    assert isinstance(nodes, list)
    assert suite["failed_node_id_count"] == len(nodes)
    assert len(nodes) == len(set(nodes)), "duplicate node ids"
    for node in nodes:
        assert isinstance(node, str)
        assert node.startswith("tests/"), node
        assert "::" in node, node
        assert "..." not in node, f"truncated node id: {node}"
        assert " - " not in node.split("::", 1)[-1] or "[" in node, (
            # parametrize may have brackets; human message appends use ' - '
            node
        )
        # Reject terminal truncation patterns like ' - app...'
        if " - " in node:
            # only allow if inside parametrize id? Prefer no ' - ' at all in clean nodeids
            # pytest nodeids use :: not ' - ' for messages
            tail = node.rsplit(" - ", 1)[-1]
            assert not tail.endswith("...") and "app..." not in tail


def test_failed_set_sha256_matches() -> None:
    data = _load_baseline()
    suite = data.get("full_suite_windows_p14c") or data.get("full_suite_windows_p141")
    nodes = sorted(suite["failed_node_ids"])
    # baseline must store sorted unique
    assert suite["failed_node_ids"] == nodes
    digest = hashlib.sha256("\n".join(nodes).encode("utf-8")).hexdigest()
    assert suite["failed_set_sha256"] == digest
