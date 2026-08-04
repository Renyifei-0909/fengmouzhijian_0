from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from app.evaluation.cli import main


DATASET_SHA = "1" * 64


def _request(suffix: str, *, model_sha256: str = "2" * 64) -> dict[str, Any]:
    return {
        "schema_version": "evaluation.holdout-reservation.v0",
        "attempt_id": f"attempt-{suffix}",
        "run_id": f"run-{suffix}",
        "dataset_manifest_sha256": DATASET_SHA,
        "split": "final_holdout",
        "policy_generation": 0,
        "model_artifact_sha256": model_sha256,
        "formal_capability": {
            "schema_version": "evaluation.formal-capability.v0",
            "capability_id": f"capability-{suffix}",
            "capability_digest": hashlib.sha256(f"capability:{suffix}".encode()).hexdigest(),
            "dataset_manifest_sha256": DATASET_SHA,
            "split": "final_holdout",
            "policy_generation": 0,
            "actor": "self-asserted-capability-issuer",
            "scope": "formal_holdout_reservation",
        },
        "qa_approval": {
            "schema_version": "evaluation.qa-holdout-approval.v0",
            "approval_id": f"approval-{suffix}",
            "approval_digest": hashlib.sha256(f"approval:{suffix}".encode()).hexdigest(),
            "approval_kind": "initial_release",
            "dataset_manifest_sha256": DATASET_SHA,
            "split": "final_holdout",
            "policy_generation": 0,
            "reason": "Unsigned local registry CLI contract fixture",
            "actor": "self-asserted-qa",
            "predecessor_attempt_id": None,
        },
    }


def _invoke(capsys, *arguments: str) -> tuple[int, dict[str, Any]]:
    exit_code = main(list(arguments))
    captured = capsys.readouterr()
    assert captured.err == ""
    return exit_code, json.loads(captured.out)


def test_registry_cli_runs_reserve_exposure_finalize_and_inspection_flow(
    tmp_path: Path,
    capsys,
) -> None:
    registry = tmp_path / "holdout.sqlite3"
    request_path = tmp_path / "request.json"
    request_path.write_text(json.dumps(_request("cli")), encoding="utf-8")

    exit_code, reserved = _invoke(
        capsys,
        "holdout-reserve",
        "--registry",
        str(registry),
        "--request",
        str(request_path),
    )
    assert exit_code == 0
    assert reserved["state"] == "reserved"
    assert reserved["authorization_authenticity"] == "self_asserted_unsigned"
    assert reserved["formal_execution_completed"] is False

    exit_code, exposed = _invoke(
        capsys,
        "holdout-commit-exposure",
        "--registry",
        str(registry),
        "--attempt-id",
        "attempt-cli",
        "--actor",
        "local-contract-broker",
    )
    assert exit_code == 0
    assert exposed["local_exposure_state_persisted"] is True
    assert exposed["trusted_broker_release_authorized"] is False

    exit_code, finalized = _invoke(
        capsys,
        "holdout-finalize",
        "--registry",
        str(registry),
        "--attempt-id",
        "attempt-cli",
        "--result-sha256",
        "9" * 64,
        "--actor",
        "local-contract-worker",
    )
    assert exit_code == 0
    assert finalized["attempt"]["state"] == "consumed"
    assert finalized["compliance_claim_eligible"] is False

    exit_code, inspected = _invoke(
        capsys,
        "holdout-get",
        "--registry",
        str(registry),
        "--attempt-id",
        "attempt-cli",
    )
    assert exit_code == 0
    assert inspected["attempt"]["result_sha256"] == "9" * 64
    assert inspected["attempt"]["authorization_authenticity"] == "self_asserted_unsigned"
    assert inspected["attempt"]["formal_execution_completed"] is False
    assert inspected["attempt"]["compliance_claim_eligible"] is False

    exit_code, listed = _invoke(capsys, "holdout-list", "--registry", str(registry))
    assert exit_code == 0
    assert listed["count"] == 1
    assert listed["attempts"][0]["attempt_id"] == "attempt-cli"
    assert listed["attempts"][0]["authorization_authenticity"] == "self_asserted_unsigned"


def test_registry_cli_duplicate_key_is_structured_integrity_failure(tmp_path: Path, capsys) -> None:
    registry = tmp_path / "holdout.sqlite3"
    first_path = tmp_path / "first.json"
    second_path = tmp_path / "second.json"
    first_path.write_text(json.dumps(_request("first", model_sha256="a" * 64)), encoding="utf-8")
    second_path.write_text(json.dumps(_request("second", model_sha256="b" * 64)), encoding="utf-8")

    assert _invoke(
        capsys,
        "holdout-reserve",
        "--registry",
        str(registry),
        "--request",
        str(first_path),
    )[0] == 0
    exit_code, failure = _invoke(
        capsys,
        "holdout-reserve",
        "--registry",
        str(registry),
        "--request",
        str(second_path),
    )

    assert exit_code == 3
    assert failure["ok"] is False
    assert failure["error"]["code"] == "EVAL_HOLDOUT_ALREADY_CLAIMED"
    assert failure["error"]["details"]["model_identity_ignored_for_key"] is True


def test_registry_cli_strict_json_rejects_duplicate_keys_before_reservation(tmp_path: Path, capsys) -> None:
    registry = tmp_path / "holdout.sqlite3"
    request_path = tmp_path / "duplicate.json"
    request_path.write_text('{"schema_version":"x","schema_version":"y"}', encoding="utf-8")

    exit_code, failure = _invoke(
        capsys,
        "holdout-reserve",
        "--registry",
        str(registry),
        "--request",
        str(request_path),
    )

    assert exit_code == 2
    assert failure["error"]["code"] == "EVAL_JSON_DUPLICATE_KEY"
    assert not registry.exists()
