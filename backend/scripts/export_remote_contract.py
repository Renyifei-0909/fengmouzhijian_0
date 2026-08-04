#!/usr/bin/env python3
"""Export the remote analyzer request/response JSON Schemas deterministically."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys


BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_ROOT.parent
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "docs"
REQUEST_FILENAME = "remote-analyzer-request-v1.schema.json"
RESPONSE_FILENAME = "remote-analyzer-response-v1.schema.json"

sys.path.insert(0, str(BACKEND_ROOT))

from app.services.analyzers.remote_http import (  # noqa: E402
    RemoteAnalyzerRequest,
    RemoteAnalyzerResponse,
)


def _render(model) -> bytes:
    return (json.dumps(model.model_json_schema(), ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
        "utf-8"
    )


def rendered_contracts() -> dict[str, bytes]:
    return {
        REQUEST_FILENAME: _render(RemoteAnalyzerRequest),
        RESPONSE_FILENAME: _render(RemoteAnalyzerResponse),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export remote analyzer JSON Schemas")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--check", action="store_true", help="fail without writing when an artifact is stale")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    output_dir = args.output_dir.expanduser().resolve()
    outputs = rendered_contracts()
    stale: list[Path] = []
    for filename, content in outputs.items():
        path = output_dir / filename
        if args.check:
            if not path.is_file() or path.read_bytes() != content:
                stale.append(path)
            continue
        output_dir.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        print(f"wrote {path} (sha256={hashlib.sha256(content).hexdigest()})")
    if stale:
        for path in stale:
            print(f"Remote analyzer contract is missing or stale: {path}", file=sys.stderr)
        return 1
    if args.check:
        for filename, content in outputs.items():
            print(
                f"verified {output_dir / filename} (sha256={hashlib.sha256(content).hexdigest()})"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
