from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Sequence

from .bundle import verify_development_evidence_bundle
from .controlled_bundle import verify_controlled_local_evidence_bundle
from .errors import EvaluationError
from .executor import run_development_plan
from .jsonio import MAX_JSON_BYTES, parse_json_object, snapshot_file
from .registry import (
    commit_holdout_exposure,
    finalize_holdout_attempt,
    get_holdout_attempt,
    list_holdout_attempts,
    mark_holdout_incident,
    reserve_holdout_attempt,
)
from .service import score_dataset, validate_dataset


class _EvaluationArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise EvaluationError("EVAL_CLI_ARGUMENT_INVALID", message)


def _parser() -> argparse.ArgumentParser:
    parser = _EvaluationArgumentParser(description="Validate and score frozen Fengmou Evaluation v0 datasets")
    subcommands = parser.add_subparsers(dest="command", required=True)

    validate = subcommands.add_parser("validate", help="Validate a dataset manifest and all frozen artifacts")
    validate.add_argument("--manifest", type=Path, required=True)
    validate.add_argument("--formal", action="store_true", help="Apply formal-dataset gates")

    score = subcommands.add_parser("score", help="Score exact-coverage predictions for one split")
    score.add_argument("--manifest", type=Path, required=True)
    score.add_argument("--predictions", type=Path, required=True)
    score.add_argument("--model-statement", type=Path, required=True)
    score.add_argument(
        "--split",
        choices=["train", "validation", "gate_holdout", "final_holdout"],
        required=True,
    )
    score.add_argument("--formal", action="store_true", help="Apply formal dataset/model gates")
    score.add_argument(
        "--expected-manifest-sha256",
        "--expected_manifest_sha256",
        dest="expected_manifest_sha256",
    )
    score.add_argument(
        "--expected-model-statement-sha256",
        "--expected_model_statement_sha256",
        dest="expected_model_statement_sha256",
    )
    score.add_argument(
        "--require-threshold-pass",
        action="store_true",
        help="Exit 6 when a structurally valid score does not meet the frozen threshold",
    )

    run_dev = subcommands.add_parser(
        "run-dev",
        help="Execute a pinned development-only local model plan and score its predictions",
    )
    run_dev.add_argument("--plan", type=Path, required=True)
    run_dev.add_argument("--expected-run-plan-sha256", required=True)
    run_dev.add_argument(
        "--evidence-dir",
        type=Path,
        help="Atomically publish a fixed-tree, unsigned development evidence directory",
    )
    run_dev.add_argument(
        "--require-threshold-pass",
        action="store_true",
        help="Exit 6 when the development score does not meet the frozen threshold",
    )

    verify_dev = subcommands.add_parser(
        "verify-dev-bundle",
        help="Verify an unsigned development evidence directory without private labels",
    )
    verify_dev.add_argument("--bundle", type=Path, required=True)
    verify_dev.add_argument("--expected-manifest-sha256")

    verify_controlled = subcommands.add_parser(
        "verify-controlled-bundle",
        help="Verify a signed non-formal controlled-local evidence bundle",
    )
    verify_controlled.add_argument("--bundle", type=Path, required=True)
    verify_controlled.add_argument("--trust-store", type=Path, required=True)
    verify_controlled.add_argument("--expected-trust-store-sha256", required=True)
    verify_controlled.add_argument("--expected-manifest-sha256")
    verify_controlled.add_argument("--expected-run-id")
    verify_controlled.add_argument("--expected-attempt-id")
    verify_controlled.add_argument("--expected-dataset-manifest-sha256")

    reserve = subcommands.add_parser(
        "holdout-reserve",
        help="Atomically reserve one local holdout consumption key from a strict request document",
    )
    reserve.add_argument("--registry", type=Path, required=True)
    reserve.add_argument("--request", type=Path, required=True)

    exposure = subcommands.add_parser(
        "holdout-commit-exposure",
        help="Persist exposure_committed before any broker releases holdout inputs or labels",
    )
    exposure.add_argument("--registry", type=Path, required=True)
    exposure.add_argument("--attempt-id", required=True)
    exposure.add_argument("--actor", required=True)

    finalize = subcommands.add_parser(
        "holdout-finalize",
        help="Bind a completed result bundle digest to an exposure-committed attempt",
    )
    finalize.add_argument("--registry", type=Path, required=True)
    finalize.add_argument("--attempt-id", required=True)
    finalize.add_argument("--result-sha256", required=True)
    finalize.add_argument("--actor", required=True)

    incident = subcommands.add_parser(
        "holdout-lock-incident",
        help="Irreversibly lock an execution incident using a strict QA approval document",
    )
    incident.add_argument("--registry", type=Path, required=True)
    incident.add_argument("--attempt-id", required=True)
    incident.add_argument("--approval", type=Path, required=True)

    inspect = subcommands.add_parser("holdout-get", help="Inspect one local holdout attempt")
    inspect.add_argument("--registry", type=Path, required=True)
    inspect.add_argument("--attempt-id", required=True)

    list_attempts = subcommands.add_parser("holdout-list", help="List local holdout attempts")
    list_attempts.add_argument("--registry", type=Path, required=True)
    return parser


def _write(payload: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n")


def _read_strict_json_object(path: Path) -> dict[str, Any]:
    snapshot = snapshot_file(path, max_bytes=MAX_JSON_BYTES)
    return parse_json_object(snapshot.text, location=str(path))


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
        if args.command == "validate":
            dataset = validate_dataset(args.manifest, formal=args.formal)
            _write(
                {
                    "ok": True,
                    "schema_version": "evaluation.validation.v0",
                    "dataset_id": dataset.manifest.dataset_id,
                    "version": dataset.manifest.version,
                    "formal_requested": args.formal,
                    "case_count": len(dataset.cases),
                    "label_count": len(dataset.labels),
                }
            )
        elif args.command == "score":
            result = score_dataset(
                args.manifest,
                args.predictions,
                args.model_statement,
                split=args.split,
                formal=args.formal,
                expected_manifest_sha256=args.expected_manifest_sha256,
                expected_model_statement_sha256=args.expected_model_statement_sha256,
            )
            _write(result)
            if args.require_threshold_pass and result["threshold_status"] != "passed":
                return 6
        elif args.command == "run-dev":
            result = run_development_plan(
                args.plan,
                expected_run_plan_sha256=args.expected_run_plan_sha256,
                evidence_directory=args.evidence_dir,
            )
            _write(result)
            if args.require_threshold_pass and result["score"]["threshold_status"] != "passed":
                return 6
        elif args.command == "verify-dev-bundle":
            result = verify_development_evidence_bundle(
                args.bundle,
                expected_manifest_sha256=args.expected_manifest_sha256,
            )
            _write(result)
        elif args.command == "verify-controlled-bundle":
            _write(
                verify_controlled_local_evidence_bundle(
                    args.bundle,
                    args.trust_store,
                    expected_trust_store_sha256=args.expected_trust_store_sha256,
                    expected_manifest_sha256=args.expected_manifest_sha256,
                    expected_run_id=args.expected_run_id,
                    expected_attempt_id=args.expected_attempt_id,
                    expected_dataset_manifest_sha256=args.expected_dataset_manifest_sha256,
                )
            )
        elif args.command == "holdout-reserve":
            _write(reserve_holdout_attempt(args.registry, _read_strict_json_object(args.request)))
        elif args.command == "holdout-commit-exposure":
            _write(
                commit_holdout_exposure(
                    args.registry,
                    attempt_id=args.attempt_id,
                    actor=args.actor,
                )
            )
        elif args.command == "holdout-finalize":
            _write(
                finalize_holdout_attempt(
                    args.registry,
                    attempt_id=args.attempt_id,
                    result_sha256=args.result_sha256,
                    actor=args.actor,
                )
            )
        elif args.command == "holdout-lock-incident":
            _write(
                mark_holdout_incident(
                    args.registry,
                    attempt_id=args.attempt_id,
                    incident_approval=_read_strict_json_object(args.approval),
                )
            )
        elif args.command == "holdout-get":
            _write(
                {
                    "schema_version": "evaluation.holdout-inspection.v0",
                    "ok": True,
                    "attempt": get_holdout_attempt(args.registry, attempt_id=args.attempt_id),
                }
            )
        else:
            attempts = list_holdout_attempts(args.registry)
            _write(
                {
                    "schema_version": "evaluation.holdout-list.v0",
                    "ok": True,
                    "count": len(attempts),
                    "attempts": attempts,
                }
            )
        return 0
    except EvaluationError as exc:
        _write({"ok": False, "error": exc.as_dict()})
        return exc.exit_code


__all__ = ["main"]
