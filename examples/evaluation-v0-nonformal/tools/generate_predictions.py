#!/usr/bin/env python3
"""Development-only predictor that deliberately emits one constant class.

It reads public cases only. It does not inspect asset filenames or private labels,
and it is intentionally incapable of entering formal Evaluation v0.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate non-formal constant fixture predictions")
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()
    if args.seed < 0:
        raise ValueError("seed must be non-negative")

    with args.model.open("r", encoding="utf-8", errors="strict") as handle:
        model = json.load(handle)
    if set(model) != {"schema_version", "constant_label"}:
        raise ValueError("fixture model must contain exactly schema_version and constant_label")
    if model["schema_version"] != "fixture.constant-label.v0":
        raise ValueError("unexpected fixture model schema")
    constant_label = model["constant_label"]
    if not isinstance(constant_label, str) or not constant_label:
        raise ValueError("constant_label must be a non-empty string")

    case_ids: list[str] = []
    seen: set[str] = set()
    with args.cases.open("r", encoding="utf-8", errors="strict") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                raise ValueError(f"blank cases line at {line_number}")
            record = json.loads(line)
            if record.get("schema_version") != "evaluation.case.v0":
                raise ValueError(f"unexpected case schema at line {line_number}")
            case_id = record.get("case_id")
            if not isinstance(case_id, str) or not case_id or case_id in seen:
                raise ValueError(f"invalid or duplicate case_id at line {line_number}")
            seen.add(case_id)
            case_ids.append(case_id)

    if not case_ids:
        raise ValueError("cases file is empty")
    with args.output.open("w", encoding="utf-8", errors="strict", newline="\n") as handle:
        for case_id in case_ids:
            prediction = {
                "schema_version": "evaluation.prediction.v0",
                "case_id": case_id,
                "output": {
                    "kind": "violation_single_label",
                    "label": constant_label,
                },
            }
            handle.write(json.dumps(prediction, ensure_ascii=False, separators=(",", ":")) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
