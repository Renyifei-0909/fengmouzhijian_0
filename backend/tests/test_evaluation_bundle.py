from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

import app.evaluation.bundle as bundle_module
from app.evaluation.bundle import (
    case_id_roster_sha256,
    publish_development_evidence_bundle,
    verify_development_evidence_bundle,
)
from app.evaluation.errors import ContractError, ExecutionError, IntegrityError
from app.evaluation.executor import evaluator_source_sha256, run_development_plan
from app.evaluation.jsonio import MAX_JSON_BYTES, MAX_JSONL_BYTES, snapshot_file
from app.evaluation.service import score_dataset


PROJECT_ROOT = Path(__file__).resolve().parents[2]
EXAMPLE_ROOT = PROJECT_ROOT / "examples" / "evaluation-v0-nonformal"
MEMBER_PATHS = (
    "inputs/run-plan.json",
    "public/predictions.jsonl",
    "results/run-summary.json",
    "results/score.json",
)


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _canonical(value: object) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        + b"\n"
    )


def _development_inputs() -> tuple:
    plan_path = EXAMPLE_ROOT / "run-plan.json"
    predictions_path = EXAMPLE_ROOT / "runs" / "predictions.validation.jsonl"
    plan_snapshot = snapshot_file(plan_path, max_bytes=MAX_JSON_BYTES)
    predictions_snapshot = snapshot_file(predictions_path, max_bytes=MAX_JSONL_BYTES)
    plan = json.loads(plan_snapshot.text)
    score = score_dataset(
        EXAMPLE_ROOT / "dataset.manifest.json",
        predictions_path,
        EXAMPLE_ROOT / "model" / "model-statement.json",
        split="validation",
        formal=False,
        expected_manifest_sha256=plan["dataset_manifest"]["sha256"],
        expected_model_statement_sha256=plan["model_statement"]["sha256"],
    )
    run_result = {
        "schema_version": "evaluation.development-run.v0",
        "ok": True,
        "run_id": plan["run_id"],
        "mode": "development",
        "runner": "local_process",
        "protocol": "evaluation.predictor-cli.v0",
        "split": plan["split"],
        "formal_requested": False,
        "gate_status": "not_eligible",
        "compliance_claim_eligible": False,
        "run_plan_sha256": plan_snapshot.sha256,
        "evaluator_source_sha256": plan["evaluator_source_sha256"],
        "training_data_manifest_sha256": plan["training_data_manifest"]["sha256"],
        "predictions_sha256": predictions_snapshot.sha256,
        "predictions_size_bytes": predictions_snapshot.size_bytes,
        "process": {
            "return_code": 0,
            "duration_ms": 1,
            "stdout": {"sha256": "0" * 64, "size_bytes": 0},
            "stderr": {"sha256": "0" * 64, "size_bytes": 0},
        },
        "runtime": {
            "python_version": "3.12.0",
            "implementation": "CPython",
            "platform": "darwin",
            "environment_keys": [
                "HOME",
                "LANG",
                "LC_ALL",
                "MKL_NUM_THREADS",
                "OMP_NUM_THREADS",
                "PATH",
                "TEMP",
                "TMP",
                "TMPDIR",
                "TZ",
            ],
        },
        "inference_view": {
            "case_count": 2,
            "asset_count": 2,
            "asset_size_bytes": 7157,
            "private_labels_copied": False,
            "public_cases_sha256": _sha256((EXAMPLE_ROOT / "public" / "cases.jsonl").read_bytes()),
            "case_id_roster_sha256": case_id_roster_sha256(["case-001", "case-002"]),
        },
        "assurance_limitations": [
            "development_local_process_only",
            "filesystem_isolation_unverified",
            "network_isolation_unverified",
            "memory_and_process_count_isolation_unverified",
            "runtime_artifact_unpinned",
            "trusted_holdout_broker_unimplemented",
            "development_evidence_unsigned",
            "public_score_replay_unavailable_without_private_labels",
        ],
        "score": score,
    }
    return plan_snapshot, predictions_snapshot, run_result


def _publish(tmp_path: Path) -> tuple[Path, dict]:
    plan_snapshot, predictions_snapshot, run_result = _development_inputs()
    destination = tmp_path / "run.dev-evidence"
    receipt = publish_development_evidence_bundle(
        destination,
        run_plan_snapshot=plan_snapshot,
        predictions_snapshot=predictions_snapshot,
        run_result=run_result,
    )
    return destination, receipt.as_dict()


def _copy_current_example(tmp_path: Path) -> tuple[Path, str]:
    root = tmp_path / "example-copy"
    shutil.copytree(EXAMPLE_ROOT, root)
    plan_path = root / "run-plan.json"
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    plan["evaluator_source_sha256"] = evaluator_source_sha256()
    plan_path.write_bytes(_canonical(plan))
    return plan_path, _sha256(plan_path.read_bytes())


def _reseal(bundle: Path) -> None:
    score_path = bundle / "results" / "score.json"
    summary_path = bundle / "results" / "run-summary.json"
    manifest_path = bundle / "bundle-manifest.json"
    score_payload = score_path.read_bytes()
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    run_plan_payload = (bundle / "inputs" / "run-plan.json").read_bytes()
    summary["run_plan_sha256"] = _sha256(run_plan_payload)
    summary["run_plan_size_bytes"] = len(run_plan_payload)
    summary["public_score_sha256"] = _sha256(score_payload)
    summary["public_score_size_bytes"] = len(score_payload)
    summary_path.write_bytes(_canonical(summary))

    members = []
    for relative_path in MEMBER_PATHS:
        payload = bundle.joinpath(*relative_path.split("/")).read_bytes()
        members.append({"path": relative_path, "sha256": _sha256(payload), "size_bytes": len(payload)})
    member_set = json.dumps(
        members,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["members"] = members
    manifest["member_set_sha256"] = _sha256(member_set)
    manifest["run_plan_sha256"] = next(member["sha256"] for member in members if member["path"] == "inputs/run-plan.json")
    prediction_member = next(member for member in members if member["path"] == "public/predictions.jsonl")
    manifest["predictions_sha256"] = prediction_member["sha256"]
    manifest["predictions_size_bytes"] = prediction_member["size_bytes"]
    manifest_path.write_bytes(_canonical(manifest))


def test_publish_and_verify_fixed_public_bundle(tmp_path: Path) -> None:
    bundle, receipt = _publish(tmp_path)

    verification = verify_development_evidence_bundle(
        bundle,
        expected_manifest_sha256=receipt["manifest_sha256"],
    )

    assert verification["ok"] is True
    assert verification["manifest_authenticity"] == "unsigned"
    assert verification["expected_manifest_sha256_status"] == "matched"
    assert verification["formal_eligible"] is False
    assert verification["compliance_claim_eligible"] is False
    assert verification["score_recomputed"] is False
    assert verification["content_origin_status"] == "unverified"
    assert verification["privacy_claim_status"] == "not_provided"
    assert receipt["member_count"] == 4
    assert "bundle_path" not in receipt
    assert {path.relative_to(bundle).as_posix() for path in bundle.rglob("*")} == {
        "bundle-manifest.json",
        "inputs",
        "inputs/run-plan.json",
        "public",
        "public/predictions.jsonl",
        "results",
        "results/run-summary.json",
        "results/score.json",
    }
    score_text = (bundle / "results" / "score.json").read_text(encoding="utf-8")
    assert "labels_private_sha256" not in score_text
    assert "labels_private_size_bytes" not in score_text
    assert not (bundle / "private").exists()
    assert not any(path.name.endswith(".log") for path in bundle.rglob("*"))


def test_bundle_verifies_after_copy_without_source_dataset(tmp_path: Path) -> None:
    bundle, _ = _publish(tmp_path)
    isolated = tmp_path / "isolated" / "copied.dev-evidence"
    isolated.parent.mkdir()
    shutil.copytree(bundle, isolated)

    verification = verify_development_evidence_bundle(isolated)

    assert verification["ok"] is True
    assert verification["manifest_authenticity"] == "unsigned"
    assert verification["expected_manifest_sha256_status"] == "not_supplied"


def test_executor_publishes_bundle_before_temporary_predictions_are_removed(tmp_path: Path) -> None:
    plan_path, expected_plan_sha256 = _copy_current_example(tmp_path)
    evidence = tmp_path / "executor.dev-evidence"

    result = run_development_plan(
        plan_path,
        expected_run_plan_sha256=expected_plan_sha256,
        evidence_directory=evidence,
    )
    verification = verify_development_evidence_bundle(
        evidence,
        expected_manifest_sha256=result["evidence_bundle"]["manifest_sha256"],
    )

    assert result["score"]["threshold_status"] == "failed"
    assert result["evidence_bundle"]["member_count"] == 4
    assert "run_bundle_unsealed" not in result["assurance_limitations"]
    assert "development_evidence_unsigned" in result["assurance_limitations"]
    assert verification["predictions_sha256"] == result["predictions_sha256"]


def test_cli_run_and_verify_bundle_from_arbitrary_working_directory(tmp_path: Path) -> None:
    plan_path, expected_plan_sha256 = _copy_current_example(tmp_path)
    evidence = tmp_path / "cli.dev-evidence"
    cwd = tmp_path / "arbitrary-cwd"
    cwd.mkdir()
    script = PROJECT_ROOT / "backend" / "scripts" / "evaluate.py"

    run = subprocess.run(
        [
            sys.executable,
            str(script),
            "run-dev",
            "--plan",
            str(plan_path),
            "--expected-run-plan-sha256",
            expected_plan_sha256,
            "--evidence-dir",
            str(evidence),
            "--require-threshold-pass",
        ],
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
    )
    run_payload = json.loads(run.stdout)

    assert run.returncode == 6
    assert run.stderr == ""
    assert evidence.is_dir()
    manifest_sha256 = run_payload["evidence_bundle"]["manifest_sha256"]

    verify = subprocess.run(
        [
            sys.executable,
            str(script),
            "verify-dev-bundle",
            "--bundle",
            str(evidence),
            "--expected-manifest-sha256",
            manifest_sha256,
        ],
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
    )
    verify_payload = json.loads(verify.stdout)

    assert verify.returncode == 0
    assert verify.stderr == ""
    assert verify_payload["ok"] is True
    assert verify_payload["manifest_authenticity"] == "unsigned"
    assert verify_payload["expected_manifest_sha256_status"] == "matched"
    assert verify_payload["formal_eligible"] is False


def test_publish_never_overwrites_existing_target(tmp_path: Path) -> None:
    target = tmp_path / "run.dev-evidence"
    target.mkdir()
    marker = target / "keep.txt"
    marker.write_text("keep", encoding="utf-8")
    plan_snapshot, predictions_snapshot, run_result = _development_inputs()

    with pytest.raises(ContractError) as captured:
        publish_development_evidence_bundle(
            target,
            run_plan_snapshot=plan_snapshot,
            predictions_snapshot=predictions_snapshot,
            run_result=run_result,
        )

    assert captured.value.code == "EVAL_DEV_BUNDLE_TARGET_EXISTS"
    assert marker.read_text(encoding="utf-8") == "keep"


@pytest.mark.parametrize("case_ids", [[], ["case-001", "case-001"]])
def test_case_roster_rejects_empty_or_duplicate_ids(case_ids: list[str]) -> None:
    with pytest.raises(ContractError) as captured:
        case_id_roster_sha256(case_ids)

    assert captured.value.code == "EVAL_DEV_BUNDLE_CASE_ROSTER_INVALID"


def test_publish_rejects_internal_score_truth_boundary_mismatch(tmp_path: Path) -> None:
    plan_snapshot, predictions_snapshot, run_result = _development_inputs()
    run_result["score"]["formal_requested"] = True

    with pytest.raises(IntegrityError) as captured:
        publish_development_evidence_bundle(
            tmp_path / "run.dev-evidence",
            run_plan_snapshot=plan_snapshot,
            predictions_snapshot=predictions_snapshot,
            run_result=run_result,
        )

    assert captured.value.code == "EVAL_DEV_BUNDLE_SOURCE_BINDING_MISMATCH"
    assert "score.formal_requested" in captured.value.details["fields"]


def test_publish_requires_object_score_payload(tmp_path: Path) -> None:
    plan_snapshot, predictions_snapshot, run_result = _development_inputs()
    run_result["score"] = None

    with pytest.raises(ContractError) as captured:
        publish_development_evidence_bundle(
            tmp_path / "run.dev-evidence",
            run_plan_snapshot=plan_snapshot,
            predictions_snapshot=predictions_snapshot,
            run_result=run_result,
        )

    assert captured.value.code == "EVAL_DEV_BUNDLE_SOURCE_INVALID"


def test_publish_requires_existing_real_parent_directory(tmp_path: Path) -> None:
    plan_snapshot, predictions_snapshot, run_result = _development_inputs()

    with pytest.raises(ContractError) as captured:
        publish_development_evidence_bundle(
            tmp_path / "missing" / "run.dev-evidence",
            run_plan_snapshot=plan_snapshot,
            predictions_snapshot=predictions_snapshot,
            run_result=run_result,
        )

    assert captured.value.code == "EVAL_DEV_BUNDLE_PARENT_INVALID"


def test_invalid_unicode_destination_is_structured_contract_error(tmp_path: Path) -> None:
    plan_snapshot, predictions_snapshot, run_result = _development_inputs()
    destination = tmp_path / "bad\udcff"

    with pytest.raises(ContractError) as captured:
        publish_development_evidence_bundle(
            destination,
            run_plan_snapshot=plan_snapshot,
            predictions_snapshot=predictions_snapshot,
            run_result=run_result,
        )

    assert captured.value.code == "EVAL_DEV_BUNDLE_DESTINATION_INVALID"
    assert captured.value.path is None


def test_overlong_destination_name_is_structured_contract_error(tmp_path: Path) -> None:
    plan_snapshot, predictions_snapshot, run_result = _development_inputs()

    with pytest.raises(ContractError) as captured:
        publish_development_evidence_bundle(
            tmp_path / ("x" * 201),
            run_plan_snapshot=plan_snapshot,
            predictions_snapshot=predictions_snapshot,
            run_result=run_result,
        )

    assert captured.value.code == "EVAL_DEV_BUNDLE_DESTINATION_INVALID"


def test_noncooperating_empty_target_race_is_not_replaced(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "run.dev-evidence"
    plan_snapshot, predictions_snapshot, run_result = _development_inputs()
    original_rename = bundle_module._rename_directory_noreplace

    def create_target_then_rename(source: Path, destination: Path) -> None:
        destination.mkdir()
        original_rename(source, destination)

    monkeypatch.setattr(bundle_module, "_rename_directory_noreplace", create_target_then_rename)

    with pytest.raises(ContractError) as captured:
        publish_development_evidence_bundle(
            target,
            run_plan_snapshot=plan_snapshot,
            predictions_snapshot=predictions_snapshot,
            run_result=run_result,
        )

    assert captured.value.code == "EVAL_DEV_BUNDLE_TARGET_EXISTS"
    assert target.is_dir()
    assert list(target.iterdir()) == []


def test_parent_fsync_failure_reports_published_but_durability_unconfirmed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "run.dev-evidence"
    plan_snapshot, predictions_snapshot, run_result = _development_inputs()
    original_fsync_directory = bundle_module._fsync_directory

    def fail_parent_fsync(path: Path) -> None:
        if path == tmp_path:
            raise OSError(5, "simulated parent fsync failure")
        original_fsync_directory(path)

    monkeypatch.setattr(bundle_module, "_fsync_directory", fail_parent_fsync)

    with pytest.raises(ExecutionError) as captured:
        publish_development_evidence_bundle(
            target,
            run_plan_snapshot=plan_snapshot,
            predictions_snapshot=predictions_snapshot,
            run_result=run_result,
        )

    assert captured.value.code == "EVAL_DEV_BUNDLE_DURABILITY_UNCONFIRMED"
    assert captured.value.details["published"] is True
    assert target.is_dir()
    assert verify_development_evidence_bundle(target)["ok"] is True


def test_staging_cleanup_failure_is_attached_to_primary_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "run.dev-evidence"
    plan_snapshot, predictions_snapshot, run_result = _development_inputs()

    def fail_publication(source: Path, destination: Path) -> None:
        raise ContractError("EVAL_DEV_BUNDLE_TARGET_EXISTS", "simulated publication race")

    def fail_cleanup(path: Path) -> None:
        raise OSError(5, "simulated staging cleanup failure")

    monkeypatch.setattr(bundle_module, "_rename_directory_noreplace", fail_publication)
    monkeypatch.setattr(bundle_module.shutil, "rmtree", fail_cleanup)

    with pytest.raises(ContractError) as captured:
        publish_development_evidence_bundle(
            target,
            run_plan_snapshot=plan_snapshot,
            predictions_snapshot=predictions_snapshot,
            run_result=run_result,
        )

    assert captured.value.code == "EVAL_DEV_BUNDLE_TARGET_EXISTS"
    assert captured.value.details["cleanup_issues"][0]["operation"] == "remove_staging_directory"


def test_modified_member_fails_integrity(tmp_path: Path) -> None:
    bundle, _ = _publish(tmp_path)
    predictions = bundle / "public" / "predictions.jsonl"
    predictions.write_bytes(predictions.read_bytes() + b"{}\n")

    with pytest.raises(IntegrityError) as captured:
        verify_development_evidence_bundle(bundle)

    assert captured.value.code == "EVAL_DEV_BUNDLE_MEMBER_IDENTITY_MISMATCH"


@pytest.mark.parametrize("change", ["delete", "extra"])
def test_missing_or_extra_member_fails_fixed_tree(tmp_path: Path, change: str) -> None:
    bundle, _ = _publish(tmp_path)
    if change == "delete":
        (bundle / "results" / "score.json").unlink()
    else:
        (bundle / "results" / "extra.json").write_text("{}\n", encoding="utf-8")

    with pytest.raises(ContractError) as captured:
        verify_development_evidence_bundle(bundle)

    assert captured.value.code == "EVAL_DEV_BUNDLE_TREE_INVALID"


def test_symlink_member_fails_fixed_tree(tmp_path: Path) -> None:
    bundle, _ = _publish(tmp_path)
    score = bundle / "results" / "score.json"
    score.unlink()
    os.symlink(bundle / "inputs" / "run-plan.json", score)

    with pytest.raises(ContractError) as captured:
        verify_development_evidence_bundle(bundle)

    assert captured.value.code == "EVAL_DEV_BUNDLE_TREE_INVALID"


def test_wrong_external_manifest_pin_fails_integrity(tmp_path: Path) -> None:
    bundle, _ = _publish(tmp_path)

    with pytest.raises(IntegrityError) as captured:
        verify_development_evidence_bundle(bundle, expected_manifest_sha256="0" * 64)

    assert captured.value.code == "EVAL_DEV_BUNDLE_MANIFEST_IDENTITY_MISMATCH"


def test_malformed_external_manifest_pin_is_contract_error(tmp_path: Path) -> None:
    bundle, _ = _publish(tmp_path)

    with pytest.raises(ContractError) as captured:
        verify_development_evidence_bundle(bundle, expected_manifest_sha256="NOT-A-SHA")

    assert captured.value.code == "EVAL_DEV_BUNDLE_EXPECTED_DIGEST_INVALID"


def test_invalid_unicode_bundle_root_is_structured_contract_error() -> None:
    with pytest.raises(ContractError) as captured:
        verify_development_evidence_bundle(Path("/tmp") / "bad\udcff")

    assert captured.value.code == "EVAL_DEV_BUNDLE_ROOT_INVALID"
    assert captured.value.path is None


def test_resealed_private_field_injection_is_still_rejected(tmp_path: Path) -> None:
    bundle, _ = _publish(tmp_path)
    score_path = bundle / "results" / "score.json"
    score = json.loads(score_path.read_text(encoding="utf-8"))
    original_class = score["metrics"]["class_order"][0]
    private_class = "ground-truth"
    score["metrics"]["class_order"][0] = private_class
    score["metrics"]["per_class"][private_class] = score["metrics"]["per_class"].pop(original_class)
    score_path.write_bytes(_canonical(score))
    _reseal(bundle)

    with pytest.raises(ContractError) as captured:
        verify_development_evidence_bundle(bundle)

    assert captured.value.code == "EVAL_DEV_BUNDLE_PRIVATE_FIELD_FORBIDDEN"


def test_resealed_prediction_ground_truth_injection_is_rejected(tmp_path: Path) -> None:
    bundle, _ = _publish(tmp_path)
    predictions_path = bundle / "public" / "predictions.jsonl"
    predictions = [json.loads(line) for line in predictions_path.read_text(encoding="utf-8").splitlines()]
    predictions[0]["ground_truth"] = "helmet_compliant"
    predictions_path.write_text(
        "".join(json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n" for item in predictions),
        encoding="utf-8",
    )
    _reseal(bundle)

    with pytest.raises(ContractError) as captured:
        verify_development_evidence_bundle(bundle)

    assert captured.value.code in {"EVAL_PROTECTED_CLAIM_FORBIDDEN", "EVAL_SCHEMA_INVALID"}


def test_resealed_run_plan_private_artifact_path_is_rejected(tmp_path: Path) -> None:
    bundle, _ = _publish(tmp_path)
    plan_path = bundle / "inputs" / "run-plan.json"
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    plan["dataset_manifest"]["path"] = "private/labels.private.jsonl"
    plan_path.write_bytes(_canonical(plan))
    _reseal(bundle)

    with pytest.raises(ContractError) as captured:
        verify_development_evidence_bundle(bundle)

    assert captured.value.code == "EVAL_DEV_BUNDLE_PRIVATE_FIELD_FORBIDDEN"


def test_resealed_absolute_local_path_string_is_rejected(tmp_path: Path) -> None:
    bundle, _ = _publish(tmp_path)
    score_path = bundle / "results" / "score.json"
    score = json.loads(score_path.read_text(encoding="utf-8"))
    score["model"]["model_name"] = "checkpoint path=/Users/example/secret/model.bin"
    score_path.write_bytes(_canonical(score))
    _reseal(bundle)

    with pytest.raises(ContractError) as captured:
        verify_development_evidence_bundle(bundle)

    assert captured.value.code == "EVAL_DEV_BUNDLE_PRIVATE_FIELD_FORBIDDEN"


def test_resealed_prediction_case_id_must_match_published_roster_commitment(tmp_path: Path) -> None:
    bundle, _ = _publish(tmp_path)
    predictions_path = bundle / "public" / "predictions.jsonl"
    predictions = [json.loads(line) for line in predictions_path.read_text(encoding="utf-8").splitlines()]
    predictions[0]["case_id"] = "case-log-JBSWY3DPEBLW64TMMQ"
    predictions_path.write_text(
        "".join(json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n" for item in predictions),
        encoding="utf-8",
    )
    _reseal(bundle)

    with pytest.raises(IntegrityError) as captured:
        verify_development_evidence_bundle(bundle)

    assert captured.value.code == "EVAL_DEV_BUNDLE_INTERNAL_BINDING_MISMATCH"
    assert "summary.case_id_roster_sha256" in captured.value.details["fields"]


def test_resealed_cross_binding_change_fails_integrity(tmp_path: Path) -> None:
    bundle, _ = _publish(tmp_path)
    summary_path = bundle / "results" / "run-summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary["dataset_manifest_sha256"] = "0" * 64
    summary_path.write_bytes(_canonical(summary))
    _reseal(bundle)

    with pytest.raises(IntegrityError) as captured:
        verify_development_evidence_bundle(bundle)

    assert captured.value.code == "EVAL_DEV_BUNDLE_INTERNAL_BINDING_MISMATCH"
    assert "summary.dataset_manifest_sha256" in captured.value.details["fields"]


def test_old_evaluator_digest_bundle_remains_offline_verifiable(tmp_path: Path) -> None:
    bundle, _ = _publish(tmp_path)
    old_digest = "1" * 64
    plan_path = bundle / "inputs" / "run-plan.json"
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    plan["evaluator_source_sha256"] = old_digest
    plan_path.write_bytes(_canonical(plan))
    summary_path = bundle / "results" / "run-summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary["evaluator_source_sha256"] = old_digest
    summary_path.write_bytes(_canonical(summary))
    _reseal(bundle)

    verification = verify_development_evidence_bundle(bundle)

    assert verification["ok"] is True
    assert verification["content_origin_status"] == "unverified"
