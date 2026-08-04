from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

from app.evaluation.cli import main
from test_evaluation_core import _bundle, _rewrite_dataset, _sha256_bytes


def test_direct_script_help_works_without_editable_install(tmp_path: Path) -> None:
    script = Path(__file__).resolve().parents[1] / "scripts" / "evaluate.py"

    completed = subprocess.run(
        [sys.executable, str(script), "--help"],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0
    assert "Validate and score frozen Fengmou Evaluation v0 datasets" in completed.stdout
    assert completed.stderr == ""


def test_cli_valid_but_below_threshold_exits_zero_and_reports_failed_gate(tmp_path: Path, capsys) -> None:
    truths = ["no-violation", "helmet-missing"] * 50
    predictions = list(truths)
    for index in range(16):
        predictions[index] = "helmet-missing" if truths[index] == "no-violation" else "no-violation"
    bundle = _bundle(tmp_path, truths, predictions, formal_eligible=True)
    expected_manifest = _sha256_bytes(bundle["manifest_path"].read_bytes())
    expected_model = _sha256_bytes(bundle["model_path"].read_bytes())

    exit_code = main(
        [
            "score",
            "--manifest",
            str(bundle["manifest_path"]),
            "--predictions",
            str(bundle["predictions_path"]),
            "--model-statement",
            str(bundle["model_path"]),
            "--split",
            "final_holdout",
            "--formal",
            "--expected-manifest-sha256",
            expected_manifest,
            "--expected-model-statement-sha256",
            expected_model,
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["ok"] is True
    assert payload["gate_status"] == "not_eligible"
    assert payload["structural_gate_status"] == "passed"
    assert payload["threshold_status"] == "failed"
    assert payload["compliance_claim_eligible"] is False
    assert payload["metrics"]["accuracy"]["value"] == 0.84


def test_cli_require_threshold_pass_exits_six_for_valid_below_threshold_run(tmp_path: Path, capsys) -> None:
    truths = ["no-violation", "helmet-missing"] * 50
    predictions = list(truths)
    for index in range(16):
        predictions[index] = "helmet-missing" if truths[index] == "no-violation" else "no-violation"
    bundle = _bundle(tmp_path, truths, predictions, formal_eligible=True)
    expected_manifest = _sha256_bytes(bundle["manifest_path"].read_bytes())
    expected_model = _sha256_bytes(bundle["model_path"].read_bytes())

    exit_code = main(
        [
            "score",
            "--manifest",
            str(bundle["manifest_path"]),
            "--predictions",
            str(bundle["predictions_path"]),
            "--model-statement",
            str(bundle["model_path"]),
            "--split",
            "final_holdout",
            "--formal",
            "--expected-manifest-sha256",
            expected_manifest,
            "--expected-model-statement-sha256",
            expected_model,
            "--require-threshold-pass",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 6
    assert payload["ok"] is True
    assert payload["structural_gate_status"] == "passed"
    assert payload["threshold_status"] == "failed"
    assert payload["gate_status"] == "not_eligible"


def test_cli_formal_score_without_external_pins_is_structured_contract_error(tmp_path: Path, capsys) -> None:
    bundle = _bundle(tmp_path, ["no-violation", "helmet-missing"], formal_eligible=True)

    exit_code = main(
        [
            "score",
            "--manifest",
            str(bundle["manifest_path"]),
            "--predictions",
            str(bundle["predictions_path"]),
            "--model-statement",
            str(bundle["model_path"]),
            "--split",
            "final_holdout",
            "--formal",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 2
    assert payload["ok"] is False
    assert payload["error"]["code"] == "EVAL_EXPECTED_DIGEST_REQUIRED"


def test_cli_contract_failure_exits_two_with_structured_error(tmp_path: Path, capsys) -> None:
    bundle = _bundle(tmp_path, ["no-violation", "helmet-missing"])
    bundle["predictions"].pop()
    bundle["predictions_path"].write_text(
        "".join(json.dumps(row) + "\n" for row in bundle["predictions"]),
        encoding="utf-8",
    )

    exit_code = main(
        [
            "score",
            "--manifest",
            str(bundle["manifest_path"]),
            "--predictions",
            str(bundle["predictions_path"]),
            "--model-statement",
            str(bundle["model_path"]),
            "--split",
            "final_holdout",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 2
    assert payload == {
        "error": {
            "category": "contract",
            "code": "EVAL_PREDICTION_MISSING",
            "details": {"case_ids": ["case-001"], "count": 1},
            "message": "Predictions must cover every target case; the metric denominator is never reduced",
        },
        "ok": False,
    }


def test_cli_split_integrity_failure_exits_three(tmp_path: Path, capsys) -> None:
    bundle = _bundle(
        tmp_path,
        ["no-violation", "helmet-missing"],
        splits=["train", "final_holdout"],
    )
    bundle["cases"][1]["groups"]["source_lineage_id"] = bundle["cases"][0]["groups"]["source_lineage_id"]
    _rewrite_dataset(bundle)

    exit_code = main(["validate", "--manifest", str(bundle["manifest_path"])])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 3
    assert payload["ok"] is False
    assert payload["error"]["category"] == "integrity"
    assert payload["error"]["code"] == "EVAL_GROUP_LEAKAGE"


def test_cli_argument_errors_are_structured_contract_failures(capsys) -> None:
    exit_code = main(["score"])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 2
    assert payload["ok"] is False
    assert payload["error"]["category"] == "contract"
    assert payload["error"]["code"] == "EVAL_CLI_ARGUMENT_INVALID"
