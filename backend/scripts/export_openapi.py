#!/usr/bin/env python3
"""Export the backend OpenAPI contract to the versioned documentation artifact.

Run from the backend directory:

    python scripts/export_openapi.py
    python scripts/export_openapi.py --check

The output is rendered with stable key ordering and formatting so a route or
schema change produces a reviewable diff.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
from unittest.mock import patch


BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_ROOT.parent
DEFAULT_OUTPUT = PROJECT_ROOT / "docs" / "openapi-v1.json"

# Direct script execution puts backend/scripts on sys.path, not backend/. Add
# the package root explicitly so this command works from any current directory.
sys.path.insert(0, str(BACKEND_ROOT))

from app.config import Settings  # noqa: E402


def render_openapi() -> bytes:
    """Return a deterministic UTF-8 representation of the live API schema."""

    # Importing app.main creates its conventional module-level ASGI app. Make
    # that import hermetic so a stale deployment environment cannot break or
    # influence contract generation before the explicit export settings apply.
    export_environment = {key: value for key, value in os.environ.items() if not key.startswith("FENGMOU_")}
    export_environment["FENGMOU_ENVIRONMENT"] = "openapi-export"
    with patch.dict(os.environ, export_environment, clear=True):
        from app.main import create_app

    app = create_app(Settings(environment="openapi-export"))
    document = app.openapi()
    return (json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export the FastAPI OpenAPI contract")
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"output JSON path (default: {DEFAULT_OUTPUT})",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="fail instead of writing when the committed artifact is missing or stale",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    output = args.output.expanduser().resolve()
    rendered = render_openapi()

    if args.check:
        if not output.is_file():
            print(f"OpenAPI artifact is missing: {output}", file=sys.stderr)
            return 1
        if output.read_bytes() != rendered:
            print(
                "OpenAPI artifact is stale; regenerate it with "
                f"'{sys.executable} {Path(__file__).resolve()} --output {output}'",
                file=sys.stderr,
            )
            return 1
        action = "verified"
    else:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(rendered)
        action = "wrote"

    digest = hashlib.sha256(rendered).hexdigest()
    print(f"{action} {output} ({len(rendered)} bytes, sha256={digest})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
