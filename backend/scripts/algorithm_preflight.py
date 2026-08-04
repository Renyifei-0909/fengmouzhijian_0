#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_ROOT.parent
sys.path.insert(0, str(BACKEND_ROOT))

from app.algorithm_readiness import audit_dataset, preflight_pilot


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read-only Construction-PPE audit and fail-closed pilot preflight. Never launches training."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    audit = subparsers.add_parser("audit", help="verify the registered work copy without writing")
    audit.add_argument("--dataset-root", type=Path, required=True)
    audit.add_argument("--archive", type=Path, required=True)

    preflight = subparsers.add_parser("preflight", help="evaluate pilot gates without launching a subprocess")
    preflight.add_argument("--dataset-root", type=Path, required=True)
    preflight.add_argument("--archive", type=Path, required=True)
    preflight.add_argument("--approval", type=Path)
    preflight.add_argument("--training-python", type=Path)
    preflight.add_argument("--weight-artifact", type=Path)
    preflight.add_argument("--run-root", type=Path)
    preflight.add_argument("--project-root", type=Path, default=PROJECT_ROOT)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "audit":
        result = audit_dataset(args.dataset_root, args.archive)
        exit_code = 0 if result["status"] == "passed" else 3
    else:
        result = preflight_pilot(
            args.dataset_root,
            args.archive,
            project_root=args.project_root,
            approval_path=args.approval,
            training_python=args.training_python,
            weight_artifact=args.weight_artifact,
            run_root=args.run_root,
        )
        exit_code = 0 if result["status"] == "ready" else 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
