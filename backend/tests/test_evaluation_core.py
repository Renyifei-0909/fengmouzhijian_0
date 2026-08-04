from __future__ import annotations

from collections import Counter
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any

import pytest

from app.evaluation import ContractError, IntegrityError, score_dataset, validate_dataset
from app.evaluation import jsonio as evaluation_jsonio
from app.evaluation import service as evaluation_service
from app.evaluation.jsonio import parse_json_object, snapshot_file


CLASSES = [
    {"id": "no-violation", "name": "无违章"},
    {"id": "helmet-missing", "name": "未佩戴安全帽"},
]


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _write_json(path: Path, value: Any, *, allow_nan: bool = False) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=allow_nan) + "\n",
        encoding="utf-8",
    )


def _write_jsonl(path: Path, values: list[dict[str, Any]], *, allow_nan: bool = False) -> None:
    path.write_text(
        "".join(
            json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=allow_nan) + "\n"
            for value in values
        ),
        encoding="utf-8",
    )


def _descriptor(path: Path, root: Path, line_count: int) -> dict[str, Any]:
    payload = path.read_bytes()
    return {
        "path": path.relative_to(root).as_posix(),
        "sha256": _sha256_bytes(payload),
        "size_bytes": len(payload),
        "line_count": line_count,
    }


def _assignment_sha256(cases: list[dict[str, Any]]) -> str:
    payload = "".join(f"{item['case_id']}\t{item['split']}\n" for item in sorted(cases, key=lambda row: row["case_id"]))
    return _sha256_bytes(payload.encode("utf-8"))


def _bundle(
    tmp_path: Path,
    truths: list[str],
    predictions: list[str] | None = None,
    *,
    splits: list[str] | None = None,
    formal_eligible: bool = False,
    status: str = "frozen",
    source_origin: str = "field_real",
    allowed_uses: list[str] | None = None,
    adapter_name: str = "real-adapter",
    implementation_kind: str = "model",
    synthetic: bool = False,
) -> dict[str, Any]:
    root = tmp_path
    assets = root / "assets"
    assets.mkdir()
    splits = splits or ["final_holdout"] * len(truths)
    if len(splits) != len(truths):
        raise AssertionError("test builder split length mismatch")
    cases: list[dict[str, Any]] = []
    labels: list[dict[str, Any]] = []
    for index, (truth, split) in enumerate(zip(truths, splits, strict=True)):
        asset_path = assets / f"asset-{index:03d}.mp4"
        payload = f"evaluation-asset-{index}".encode()
        asset_path.write_bytes(payload)
        case_id = f"case-{index:03d}"
        cases.append(
            {
                "schema_version": "evaluation.case.v0",
                "case_id": case_id,
                "task_type": "violation_event_classification",
                "split": split,
                "source_id": "source-001",
                "inputs": [
                    {
                        "role": "primary_media",
                        "asset_id": f"asset-{index:03d}",
                        "relative_path": asset_path.relative_to(root).as_posix(),
                        "sha256": _sha256_bytes(payload),
                        "size_bytes": len(payload),
                        "content_type": "video/mp4",
                        "segment": {"start_ms": 0, "end_ms": 1000},
                    }
                ],
                "engineering_context": {
                    "project_key": f"project-{index:03d}",
                    "site_key": f"site-{index:03d}",
                    "procedure_code": "SAFETY-CHECK",
                    "baseline_version": "v1",
                    "baseline_sha256": "b" * 64,
                },
                "groups": {
                    "source_lineage_id": f"lineage-{index:03d}",
                    "capture_session_id": f"capture-{index:03d}",
                    "engineering_entity_id": f"entity-{index:03d}",
                    "site_group_id": f"site-group-{index:03d}",
                    "camera_group_id": f"camera-{index:03d}",
                    "person_group_id": f"person-{index:03d}",
                    "project_group_id": None,
                },
            }
        )
        labels.append(
            {
                "schema_version": "evaluation.label.v0",
                "case_id": case_id,
                "annotation": {
                    "spec_version": "labels-v0",
                    "status": "adjudicated",
                    "record_sha256": _sha256_bytes(f"annotation-{index}-{truth}".encode()),
                },
                "truth": {"kind": "violation_single_label", "label": truth},
            }
        )
    prediction_labels = predictions if predictions is not None else list(truths)
    prediction_rows = [
        {
            "schema_version": "evaluation.prediction.v0",
            "case_id": f"case-{index:03d}",
            "output": {"kind": "violation_single_label", "label": label, "confidence": 0.9},
        }
        for index, label in enumerate(prediction_labels)
    ]
    manifest = {
        "schema_version": "evaluation.dataset.v0",
        "dataset_id": "dataset-test-v0",
        "version": "0.1.0",
        "status": status,
        "task": {
            "type": "violation_event_classification",
            "case_unit": "event_window",
            "classes": deepcopy(CLASSES),
            "negative_class": "no-violation",
            "multi_label": False,
            "label_spec_version": "labels-v0",
            "label_spec_sha256": "a" * 64,
            "metric_spec_version": "metrics-v0",
            "metric_spec_sha256": "c" * 64,
            "primary_metric": "accuracy",
            "acceptance": {
                "operator": ">=",
                "threshold": "0.85",
                "ci_level": "0.95",
                "ci_policy": "report_only",
            },
            "minimum_cases_total": 1,
            "minimum_cases_per_class": {"no-violation": 1, "helmet-missing": 1},
        },
        "artifacts": {},
        "splits": [],
        "split_policy": {
            "group_keys": [
                "source_lineage_id",
                "capture_session_id",
                "engineering_entity_id",
                "site_group_id",
                "camera_group_id",
                "person_group_id",
            ],
            "transitive_closure": True,
            "exact_asset_hash_disjoint": True,
            "assignment_sha256": "0" * 64,
            "generalization_unit": "engineering_entity",
        },
        "sources": [
            {
                "source_id": "source-001",
                "origin": source_origin,
                "rights_holder": "test rights holder",
                "acquisition_method": "temporary test generation",
                "allowed_uses": allowed_uses or ["evaluation"],
            }
        ],
        "formal_policy": {
            "formal_eligible": formal_eligible,
            "mock_allowed": False,
            "fixture_allowed": False,
            "synthetic_placeholder_allowed": False,
        },
    }
    model = {
        "schema_version": "evaluation.model.v0",
        "adapter_name": adapter_name,
        "adapter_version": "v1",
        "implementation_kind": implementation_kind,
        "synthetic": synthetic,
        "model_name": "test-model",
        "model_version": "v1",
        "artifact_sha256": "d" * 64,
    }
    result = {
        "root": root,
        "manifest_path": root / "dataset.manifest.json",
        "cases_path": root / "cases.jsonl",
        "labels_path": root / "labels.private.jsonl",
        "predictions_path": root / "predictions.jsonl",
        "model_path": root / "model-freeze.json",
        "cases": cases,
        "labels": labels,
        "predictions": prediction_rows,
        "manifest": manifest,
        "model": model,
    }
    _rewrite_dataset(result)
    _rewrite_predictions(result)
    _write_json(result["model_path"], result["model"])
    return result


def _rewrite_dataset(bundle: dict[str, Any]) -> None:
    _write_jsonl(bundle["cases_path"], bundle["cases"])
    _write_jsonl(bundle["labels_path"], bundle["labels"])
    bundle["manifest"]["artifacts"] = {
        "cases": _descriptor(bundle["cases_path"], bundle["root"], len(bundle["cases"])),
        "labels_private": _descriptor(bundle["labels_path"], bundle["root"], len(bundle["labels"])),
    }
    split_counts = Counter(item["split"] for item in bundle["cases"])
    bundle["manifest"]["splits"] = [
        {"name": name, "case_count": split_counts[name]}
        for name in ["train", "validation", "gate_holdout", "final_holdout"]
        if split_counts[name]
    ]
    bundle["manifest"]["split_policy"]["assignment_sha256"] = _assignment_sha256(bundle["cases"])
    _write_json(bundle["manifest_path"], bundle["manifest"])


def _rewrite_predictions(bundle: dict[str, Any], *, allow_nan: bool = False) -> None:
    _write_jsonl(bundle["predictions_path"], bundle["predictions"], allow_nan=allow_nan)


def _score(
    bundle: dict[str, Any],
    *,
    formal: bool = False,
    split: str = "final_holdout",
) -> dict[str, Any]:
    expected_manifest = _sha256_bytes(bundle["manifest_path"].read_bytes()) if formal else None
    expected_model = _sha256_bytes(bundle["model_path"].read_bytes()) if formal else None
    return score_dataset(
        bundle["manifest_path"],
        bundle["predictions_path"],
        bundle["model_path"],
        split=split,
        formal=formal,
        expected_manifest_sha256=expected_manifest,
        expected_model_statement_sha256=expected_model,
    )


def test_exact_17_of_20_meets_point_85_percent_and_reports_full_metrics(tmp_path: Path) -> None:
    truths = ["no-violation", "helmet-missing"] * 10
    predictions = list(truths)
    for index in [0, 1, 2]:
        predictions[index] = "helmet-missing" if truths[index] == "no-violation" else "no-violation"
    bundle = _bundle(tmp_path, truths, predictions, formal_eligible=True)

    result = _score(bundle, formal=True)

    assert result["gate_status"] == "not_eligible"
    assert result["compliance_claim_eligible"] is False
    assert result["structural_gate_status"] == "passed"
    assert result["threshold_status"] == "passed"
    assert result["threshold_reasons"] == []
    assert result["assurance_limitations"] == [
        "media_decode_unverified",
        "single_person_crop_provenance_unverified",
        "model_artifact_unverified",
        "blind_isolation_unverified",
        "legal_authorization_unverified",
        "one_shot_holdout_unverified",
        "training_overlap_unverified",
    ]
    assert result["metrics"]["accuracy"]["correct"] == 17
    assert result["metrics"]["accuracy"]["total"] == 20
    assert result["metrics"]["accuracy"]["value"] == 0.85
    assert result["metrics"]["threshold"]["point_passed"] is True
    assert result["metrics"]["threshold"]["value"] == "0.85"
    assert result["metrics"]["class_order"] == ["no-violation", "helmet-missing"]
    assert result["metrics"]["confusion_matrix"] == [[8, 2], [1, 9]]
    assert result["metrics"]["balanced_accuracy"] == pytest.approx(0.85)
    assert result["metrics"]["micro"]["f1"] == pytest.approx(0.85)
    assert 0.0 <= result["metrics"]["accuracy"]["wilson_95"]["lower"] < 0.85
    assert result["metrics"]["accuracy"]["wilson_95"]["upper"] <= 1.0


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFO is unavailable on this platform")
def test_snapshot_rejects_fifo_without_blocking(tmp_path: Path) -> None:
    fifo = tmp_path / "cases.jsonl"
    os.mkfifo(fifo)
    backend_root = Path(__file__).resolve().parents[1]
    script = (
        "from pathlib import Path; import sys; "
        "from app.evaluation.errors import ContractError; "
        "from app.evaluation.jsonio import snapshot_file; "
        "\ntry:\n snapshot_file(Path(sys.argv[1]), max_bytes=1024)\n"
        "except ContractError as exc:\n print(exc.code); raise SystemExit(0)\n"
        "raise SystemExit(9)\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", script, str(fifo)],
        cwd=backend_root,
        capture_output=True,
        text=True,
        timeout=2,
        check=False,
    )

    assert completed.returncode == 0
    assert completed.stdout.strip() == "EVAL_FILE_NOT_REGULAR"


def test_missing_frozen_descriptor_is_integrity_failure(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path, ["no-violation", "helmet-missing"])
    bundle["cases_path"].unlink()

    with pytest.raises(IntegrityError) as captured:
        validate_dataset(bundle["manifest_path"])

    assert captured.value.code == "EVAL_DATASET_ARTIFACT_UNAVAILABLE"
    assert captured.value.category == "integrity"


def test_same_root_descriptor_symlink_is_rejected(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path, ["no-violation", "helmet-missing"])
    linked_cases = tmp_path / "cases-link.jsonl"
    linked_cases.symlink_to(bundle["cases_path"].name)
    manifest = json.loads(bundle["manifest_path"].read_text(encoding="utf-8"))
    manifest["artifacts"]["cases"]["path"] = linked_cases.name
    _write_json(bundle["manifest_path"], manifest)

    with pytest.raises(IntegrityError) as captured:
        validate_dataset(bundle["manifest_path"])

    assert captured.value.code == "EVAL_ARTIFACT_PATH_UNSAFE"


@pytest.mark.skipif(
    os.open not in os.supports_dir_fd or not hasattr(os, "O_DIRECTORY") or not hasattr(os, "O_NOFOLLOW"),
    reason="secure openat-style path traversal is unavailable on this platform",
)
def test_parent_directory_swap_cannot_redirect_descriptor_outside_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "dataset"
    root.mkdir()
    bundle = _bundle(root, ["no-violation", "helmet-missing"])
    artifacts = root / "artifacts"
    artifacts.mkdir()
    nested_cases = artifacts / "cases.jsonl"
    bundle["cases_path"].replace(nested_cases)
    bundle["cases_path"] = nested_cases
    manifest = json.loads(bundle["manifest_path"].read_text(encoding="utf-8"))
    manifest["artifacts"]["cases"] = _descriptor(nested_cases, root, len(bundle["cases"]))
    _write_json(bundle["manifest_path"], manifest)

    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "cases.jsonl").write_bytes(nested_cases.read_bytes())
    checked_artifacts = root / "artifacts-checked"
    original_safe_path = evaluation_service._safe_relative_path
    swapped = False

    def swap_after_check(path_root: Path, raw_path: str, *, code: str = "EVAL_ASSET_PATH_UNSAFE") -> Path:
        nonlocal swapped
        candidate = original_safe_path(path_root, raw_path, code=code)
        if raw_path == "artifacts/cases.jsonl" and not swapped:
            artifacts.replace(checked_artifacts)
            artifacts.symlink_to(outside, target_is_directory=True)
            swapped = True
        return candidate

    monkeypatch.setattr(evaluation_service, "_safe_relative_path", swap_after_check)

    with pytest.raises(IntegrityError) as captured:
        validate_dataset(bundle["manifest_path"])

    assert captured.value.code == "EVAL_DATASET_ARTIFACT_UNAVAILABLE"


@pytest.mark.skipif(
    os.open not in os.supports_dir_fd or not hasattr(os, "O_DIRECTORY") or not hasattr(os, "O_NOFOLLOW"),
    reason="secure openat-style path traversal is unavailable on this platform",
)
def test_intermediate_fstat_failure_closes_every_open_descriptor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "input.json").write_text("{}\n", encoding="utf-8")
    original_open = evaluation_jsonio.os.open
    original_close = evaluation_jsonio.os.close
    original_fstat = evaluation_jsonio.os.fstat
    opened: list[int] = []
    closed: list[int] = []
    fstat_calls = 0

    def tracked_open(*args: Any, **kwargs: Any) -> int:
        descriptor = original_open(*args, **kwargs)
        opened.append(descriptor)
        return descriptor

    def tracked_close(descriptor: int) -> None:
        closed.append(descriptor)
        original_close(descriptor)

    def fail_intermediate_fstat(descriptor: int) -> os.stat_result:
        nonlocal fstat_calls
        fstat_calls += 1
        if fstat_calls == 2:
            raise OSError("injected fstat failure")
        return original_fstat(descriptor)

    monkeypatch.setattr(evaluation_jsonio.os, "open", tracked_open)
    monkeypatch.setattr(evaluation_jsonio.os, "close", tracked_close)
    monkeypatch.setattr(evaluation_jsonio.os, "fstat", fail_intermediate_fstat)
    supported_dir_fd = set(evaluation_jsonio.os.supports_dir_fd)
    supported_dir_fd.add(tracked_open)
    monkeypatch.setattr(evaluation_jsonio.os, "supports_dir_fd", supported_dir_fd)

    with pytest.raises(ContractError) as captured:
        evaluation_jsonio.snapshot_relative_file(tmp_path, "nested/input.json", max_bytes=1024)

    assert captured.value.code == "EVAL_FILE_READ_FAILED"
    assert sorted(opened) == sorted(closed)


def test_84_of_100_is_valid_science_but_fails_formal_point_gate(tmp_path: Path) -> None:
    truths = ["no-violation", "helmet-missing"] * 50
    predictions = list(truths)
    for index in range(16):
        predictions[index] = "helmet-missing" if truths[index] == "no-violation" else "no-violation"
    bundle = _bundle(tmp_path, truths, predictions, formal_eligible=True)

    result = _score(bundle, formal=True)

    assert result["ok"] is True
    assert result["gate_status"] == "not_eligible"
    assert result["compliance_claim_eligible"] is False
    assert result["structural_gate_status"] == "passed"
    assert result["threshold_status"] == "failed"
    assert result["threshold_reasons"] == ["EVAL_THRESHOLD_NOT_MET"]
    assert result["metrics"]["accuracy"]["correct"] == 84
    assert result["metrics"]["threshold"]["point_passed"] is False


def test_nonformal_score_is_also_never_compliance_eligible(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path, ["no-violation", "helmet-missing"])

    result = _score(bundle)

    assert result["gate_status"] == "not_eligible"
    assert result["compliance_claim_eligible"] is False
    assert result["structural_gate_status"] == "passed"
    assert result["threshold_status"] == "passed"


@pytest.mark.parametrize("invalid_number", [float("nan"), float("inf"), float("-inf")])
def test_nonfinite_prediction_is_rejected_before_schema_coercion(
    tmp_path: Path,
    invalid_number: float,
) -> None:
    bundle = _bundle(tmp_path, ["no-violation", "helmet-missing"])
    bundle["predictions"][0]["output"]["confidence"] = invalid_number
    _rewrite_predictions(bundle, allow_nan=True)

    with pytest.raises(ContractError) as captured:
        _score(bundle)
    assert captured.value.code == "EVAL_JSON_NONFINITE"


def test_missing_prediction_never_reduces_metric_denominator(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path, ["no-violation", "helmet-missing"])
    bundle["predictions"].pop()
    _rewrite_predictions(bundle)

    with pytest.raises(ContractError) as captured:
        _score(bundle)
    assert captured.value.code == "EVAL_PREDICTION_MISSING"


def test_extra_prediction_is_rejected(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path, ["no-violation", "helmet-missing"])
    extra = deepcopy(bundle["predictions"][0])
    extra["case_id"] = "case-extra"
    bundle["predictions"].append(extra)
    _rewrite_predictions(bundle)

    with pytest.raises(ContractError) as captured:
        _score(bundle)
    assert captured.value.code == "EVAL_PREDICTION_EXTRA"


def test_duplicate_prediction_is_rejected(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path, ["no-violation", "helmet-missing"])
    bundle["predictions"].append(deepcopy(bundle["predictions"][0]))
    _rewrite_predictions(bundle)

    with pytest.raises(ContractError) as captured:
        _score(bundle)
    assert captured.value.code == "EVAL_PREDICTION_DUPLICATE"


def test_unknown_prediction_class_is_rejected(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path, ["no-violation", "helmet-missing"])
    bundle["predictions"][0]["output"]["label"] = "invented-class"
    _rewrite_predictions(bundle)

    with pytest.raises(ContractError) as captured:
        _score(bundle)
    assert captured.value.code == "EVAL_PREDICTION_UNKNOWN_CLASS"


def test_declared_class_with_zero_target_support_is_rejected(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path, ["no-violation", "no-violation"])

    with pytest.raises(ContractError) as captured:
        _score(bundle)
    assert captured.value.code == "EVAL_CLASS_SUPPORT_ZERO"


@pytest.mark.parametrize("protected_key", ["accuracy_claim", "evidence-grade", "metrics", "GroundTruth"])
def test_protected_claim_is_rejected_at_any_prediction_depth(tmp_path: Path, protected_key: str) -> None:
    bundle = _bundle(tmp_path, ["no-violation", "helmet-missing"])
    bundle["predictions"][0]["output"]["scores"] = {protected_key: 0.99}
    _rewrite_predictions(bundle)

    with pytest.raises(ContractError) as captured:
        _score(bundle)
    assert captured.value.code == "EVAL_PROTECTED_CLAIM_FORBIDDEN"


def test_transitive_group_component_cannot_cross_splits(tmp_path: Path) -> None:
    bundle = _bundle(
        tmp_path,
        ["no-violation", "helmet-missing", "no-violation"],
        splits=["train", "train", "final_holdout"],
    )
    # A--B share lineage; B--C share entity.  Only transitive closure exposes A/B/C as one component.
    bundle["cases"][1]["groups"]["source_lineage_id"] = bundle["cases"][0]["groups"]["source_lineage_id"]
    bundle["cases"][2]["groups"]["engineering_entity_id"] = bundle["cases"][1]["groups"]["engineering_entity_id"]
    _rewrite_dataset(bundle)

    with pytest.raises(IntegrityError) as captured:
        validate_dataset(bundle["manifest_path"])
    assert captured.value.code == "EVAL_GROUP_LEAKAGE"


@pytest.mark.parametrize("group_key", ["site_group_id", "camera_group_id", "person_group_id"])
def test_formal_identity_groups_cannot_cross_splits(tmp_path: Path, group_key: str) -> None:
    bundle = _bundle(
        tmp_path,
        ["no-violation", "helmet-missing"],
        splits=["train", "final_holdout"],
    )
    bundle["cases"][1]["groups"][group_key] = bundle["cases"][0]["groups"][group_key]
    _rewrite_dataset(bundle)

    with pytest.raises(IntegrityError) as captured:
        validate_dataset(bundle["manifest_path"])
    assert captured.value.code == "EVAL_GROUP_LEAKAGE"


def test_same_asset_bytes_under_different_names_cannot_cross_splits(tmp_path: Path) -> None:
    bundle = _bundle(
        tmp_path,
        ["no-violation", "helmet-missing"],
        splits=["train", "final_holdout"],
    )
    first_path = tmp_path / bundle["cases"][0]["inputs"][0]["relative_path"]
    second_path = tmp_path / bundle["cases"][1]["inputs"][0]["relative_path"]
    second_path.write_bytes(first_path.read_bytes())
    digest = _sha256_bytes(second_path.read_bytes())
    bundle["cases"][1]["inputs"][0]["sha256"] = digest
    bundle["cases"][1]["inputs"][0]["size_bytes"] = second_path.stat().st_size
    # A different temporal window avoids the stronger duplicate-case-unit
    # rejection so this test remains focused on cross-split byte leakage.
    bundle["cases"][1]["inputs"][0]["segment"] = {"start_ms": 1000, "end_ms": 2000}
    _rewrite_dataset(bundle)

    with pytest.raises(IntegrityError) as captured:
        validate_dataset(bundle["manifest_path"])
    assert captured.value.code == "EVAL_EXACT_ASSET_LEAKAGE"


def test_same_asset_id_cannot_change_identity_across_splits(tmp_path: Path) -> None:
    bundle = _bundle(
        tmp_path,
        ["no-violation", "helmet-missing"],
        splits=["train", "final_holdout"],
    )
    bundle["cases"][1]["inputs"][0]["asset_id"] = bundle["cases"][0]["inputs"][0]["asset_id"]
    _rewrite_dataset(bundle)

    with pytest.raises(IntegrityError) as captured:
        validate_dataset(bundle["manifest_path"])
    assert captured.value.code == "EVAL_ASSET_IDENTITY_MISMATCH"


def test_84_percent_cannot_be_flipped_by_copying_correct_event_windows(tmp_path: Path) -> None:
    truths = ["no-violation", "helmet-missing"] * 50
    predictions = list(truths)
    for index in range(16):
        predictions[index] = "helmet-missing" if truths[index] == "no-violation" else "no-violation"
    bundle = _bundle(tmp_path, truths, predictions, formal_eligible=True)

    # Seven copied correct cases would change 84/100 to 91/107 (>85%) if the
    # evaluator trusted new IDs/paths/groups instead of the event-window key.
    for duplicate_index, original_index in enumerate(range(16, 23)):
        original_case = bundle["cases"][original_index]
        duplicate_case = deepcopy(original_case)
        duplicate_case_id = f"case-duplicate-{duplicate_index:03d}"
        duplicate_asset_path = tmp_path / "assets" / f"duplicate-{duplicate_index:03d}.mp4"
        original_asset_path = tmp_path / original_case["inputs"][0]["relative_path"]
        duplicate_asset_path.write_bytes(original_asset_path.read_bytes())
        duplicate_case["case_id"] = duplicate_case_id
        duplicate_case["inputs"][0]["asset_id"] = f"asset-duplicate-{duplicate_index:03d}"
        duplicate_case["inputs"][0]["relative_path"] = duplicate_asset_path.relative_to(tmp_path).as_posix()
        for group_key in [
            "source_lineage_id",
            "capture_session_id",
            "engineering_entity_id",
            "site_group_id",
            "camera_group_id",
            "person_group_id",
        ]:
            duplicate_case["groups"][group_key] = f"{group_key}-duplicate-{duplicate_index:03d}"
        duplicate_label = deepcopy(bundle["labels"][original_index])
        duplicate_label["case_id"] = duplicate_case_id
        duplicate_label["annotation"]["record_sha256"] = _sha256_bytes(
            f"duplicate-annotation-{duplicate_index}".encode()
        )
        duplicate_prediction = deepcopy(bundle["predictions"][original_index])
        duplicate_prediction["case_id"] = duplicate_case_id
        bundle["cases"].append(duplicate_case)
        bundle["labels"].append(duplicate_label)
        bundle["predictions"].append(duplicate_prediction)
    _rewrite_dataset(bundle)
    _rewrite_predictions(bundle)

    with pytest.raises(ContractError) as captured:
        _score(bundle, formal=True)
    assert captured.value.code == "EVAL_EVENT_WINDOW_DUPLICATE"


@pytest.mark.parametrize(
    ("adapter_name", "implementation_kind", "synthetic"),
    [
        ("stub", "stub", False),
        ("renamed-adapter", "fixture", False),
        ("renamed-adapter", "placeholder", False),
        ("real-adapter", "model", True),
        ("production-stub-v2", "model", False),
        ("productionFixtureV2", "model", False),
        ("safe-placeholder-model", "model", False),
        ("remote-demo_fixture-v1", "model", False),
    ],
)
def test_formal_score_rejects_non_model_adapters_even_when_stub_says_not_synthetic(
    tmp_path: Path,
    adapter_name: str,
    implementation_kind: str,
    synthetic: bool,
) -> None:
    bundle = _bundle(
        tmp_path,
        ["no-violation", "helmet-missing"],
        formal_eligible=True,
        adapter_name=adapter_name,
        implementation_kind=implementation_kind,
        synthetic=synthetic,
    )

    with pytest.raises(ContractError) as captured:
        _score(bundle, formal=True)
    assert captured.value.code == "EVAL_ADAPTER_NOT_FORMAL"


@pytest.mark.parametrize("origin", ["mock", "demo_fixture", "synthetic_placeholder"])
def test_formal_dataset_rejects_mock_fixture_sources(tmp_path: Path, origin: str) -> None:
    bundle = _bundle(
        tmp_path,
        ["no-violation", "helmet-missing"],
        formal_eligible=True,
        source_origin=origin,
    )

    with pytest.raises(ContractError) as captured:
        validate_dataset(bundle["manifest_path"], formal=True)
    assert captured.value.code == "EVAL_SYNTHETIC_FIXTURE_FORBIDDEN"


@pytest.mark.parametrize("origin", ["authorized_simulation", "sample_scenario"])
def test_formal_v0_rejects_sources_without_implemented_claim_scope(tmp_path: Path, origin: str) -> None:
    bundle = _bundle(
        tmp_path,
        ["no-violation", "helmet-missing"],
        formal_eligible=True,
        source_origin=origin,
    )

    with pytest.raises(ContractError) as captured:
        validate_dataset(bundle["manifest_path"], formal=True)
    assert captured.value.code == "EVAL_SOURCE_SCOPE_UNSUPPORTED"


def test_formal_dataset_must_be_frozen(tmp_path: Path) -> None:
    bundle = _bundle(
        tmp_path,
        ["no-violation", "helmet-missing"],
        formal_eligible=True,
        status="draft",
    )

    with pytest.raises(ContractError) as captured:
        validate_dataset(bundle["manifest_path"], formal=True)
    assert captured.value.code == "EVAL_DATASET_NOT_FROZEN"


def test_formal_dataset_requires_evaluation_allowed_use(tmp_path: Path) -> None:
    bundle = _bundle(
        tmp_path,
        ["no-violation", "helmet-missing"],
        formal_eligible=True,
        allowed_uses=["training"],
    )

    with pytest.raises(ContractError) as captured:
        validate_dataset(bundle["manifest_path"], formal=True)
    assert captured.value.code == "EVAL_SOURCE_USE_FORBIDDEN"


def test_formal_dataset_requires_site_camera_and_person_group_keys(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path, ["no-violation", "helmet-missing"], formal_eligible=True)
    bundle["manifest"]["split_policy"]["group_keys"].remove("person_group_id")
    _write_json(bundle["manifest_path"], bundle["manifest"])

    with pytest.raises(ContractError) as captured:
        validate_dataset(bundle["manifest_path"], formal=True)
    assert captured.value.code == "EVAL_FORMAL_GROUP_KEY_MISSING"
    assert captured.value.details["group_keys"] == ["person_group_id"]


def test_formal_scoring_rejects_train_and_validation_splits(tmp_path: Path) -> None:
    for split in ["train", "validation"]:
        split_root = tmp_path / split
        split_root.mkdir()
        bundle = _bundle(
            split_root,
            ["no-violation", "helmet-missing"],
            splits=[split, split],
            formal_eligible=True,
        )

        with pytest.raises(ContractError) as captured:
            _score(bundle, formal=True, split=split)
        assert captured.value.code == "EVAL_FORMAL_SPLIT_FORBIDDEN"


@pytest.mark.parametrize(
    ("manifest_digest", "model_digest", "missing_field"),
    [
        (None, "actual", "expected_manifest_sha256"),
        ("actual", None, "expected_model_statement_sha256"),
    ],
)
def test_formal_scoring_requires_external_identity_pins(
    tmp_path: Path,
    manifest_digest: str | None,
    model_digest: str | None,
    missing_field: str,
) -> None:
    bundle = _bundle(tmp_path, ["no-violation", "helmet-missing"], formal_eligible=True)
    actual_manifest = _sha256_bytes(bundle["manifest_path"].read_bytes())
    actual_model = _sha256_bytes(bundle["model_path"].read_bytes())

    with pytest.raises(ContractError) as captured:
        score_dataset(
            bundle["manifest_path"],
            bundle["predictions_path"],
            bundle["model_path"],
            split="final_holdout",
            formal=True,
            expected_manifest_sha256=actual_manifest if manifest_digest == "actual" else None,
            expected_model_statement_sha256=actual_model if model_digest == "actual" else None,
        )
    assert captured.value.code == "EVAL_EXPECTED_DIGEST_REQUIRED"
    assert captured.value.details["field"] == missing_field


@pytest.mark.parametrize(
    ("bad_target", "expected_code"),
    [
        ("manifest", "EVAL_MANIFEST_IDENTITY_MISMATCH"),
        ("model", "EVAL_MODEL_STATEMENT_IDENTITY_MISMATCH"),
    ],
)
def test_formal_scoring_rejects_external_identity_mismatch(
    tmp_path: Path,
    bad_target: str,
    expected_code: str,
) -> None:
    bundle = _bundle(tmp_path, ["no-violation", "helmet-missing"], formal_eligible=True)
    manifest_digest = _sha256_bytes(bundle["manifest_path"].read_bytes())
    model_digest = _sha256_bytes(bundle["model_path"].read_bytes())

    with pytest.raises(IntegrityError) as captured:
        score_dataset(
            bundle["manifest_path"],
            bundle["predictions_path"],
            bundle["model_path"],
            split="final_holdout",
            formal=True,
            expected_manifest_sha256="0" * 64 if bad_target == "manifest" else manifest_digest,
            expected_model_statement_sha256="0" * 64 if bad_target == "model" else model_digest,
        )
    assert captured.value.code == expected_code


def test_external_manifest_pin_blocks_rehashed_truth_and_split_mutation(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path, ["no-violation", "helmet-missing"], formal_eligible=True)
    expected_manifest = _sha256_bytes(bundle["manifest_path"].read_bytes())
    expected_model = _sha256_bytes(bundle["model_path"].read_bytes())
    # An attacker rewrites truth and regenerates all hashes stored inside the manifest.
    bundle["labels"][0]["truth"]["label"] = "helmet-missing"
    bundle["cases"][0]["split"] = "gate_holdout"
    bundle["cases"][1]["split"] = "gate_holdout"
    _rewrite_dataset(bundle)

    with pytest.raises(IntegrityError) as captured:
        score_dataset(
            bundle["manifest_path"],
            bundle["predictions_path"],
            bundle["model_path"],
            split="gate_holdout",
            formal=True,
            expected_manifest_sha256=expected_manifest,
            expected_model_statement_sha256=expected_model,
        )
    assert captured.value.code == "EVAL_MANIFEST_IDENTITY_MISMATCH"


@pytest.mark.parametrize(
    ("target_key", "result_path"),
    [
        ("manifest_path", ("dataset", "manifest_sha256")),
        ("model_path", ("model", "statement_sha256")),
        ("cases_path", ("dataset", "cases_sha256")),
        ("labels_path", ("dataset", "labels_private_sha256")),
        ("predictions_path", ("predictions_sha256",)),
    ],
)
def test_score_uses_one_immutable_snapshot_when_path_is_replaced_after_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    target_key: str,
    result_path: tuple[str, ...],
) -> None:
    bundle = _bundle(tmp_path, ["no-violation", "helmet-missing"], formal_eligible=True)
    target_path = bundle[target_key]
    old_target_digest = _sha256_bytes(target_path.read_bytes())
    expected_manifest = _sha256_bytes(bundle["manifest_path"].read_bytes())
    expected_model = _sha256_bytes(bundle["model_path"].read_bytes())
    swapped = False

    if target_key in {"cases_path", "labels_path"}:
        original_relative_snapshot = evaluation_service.snapshot_relative_file

        def relative_snapshot_then_replace(root: Path, relative_path: str, *, max_bytes: int):
            nonlocal swapped
            snapshot = original_relative_snapshot(root, relative_path, max_bytes=max_bytes)
            if root / relative_path == target_path and not swapped:
                target_path.write_bytes(b'{"concurrent_replacement":true}\n')
                swapped = True
            return snapshot

        monkeypatch.setattr(evaluation_service, "snapshot_relative_file", relative_snapshot_then_replace)
    else:
        original_snapshot_file = evaluation_service.snapshot_file

        def snapshot_then_replace(path: Path, *, max_bytes: int):
            nonlocal swapped
            snapshot = original_snapshot_file(path, max_bytes=max_bytes)
            if Path(path) == target_path and not swapped:
                target_path.write_bytes(b'{"concurrent_replacement":true}\n')
                swapped = True
            return snapshot

        monkeypatch.setattr(evaluation_service, "snapshot_file", snapshot_then_replace)

    result = evaluation_service.score_dataset(
        bundle["manifest_path"],
        bundle["predictions_path"],
        bundle["model_path"],
        split="final_holdout",
        formal=True,
        expected_manifest_sha256=expected_manifest,
        expected_model_statement_sha256=expected_model,
    )

    observed: Any = result
    for key in result_path:
        observed = observed[key]
    assert swapped is True
    assert observed == old_target_digest
    assert _sha256_bytes(target_path.read_bytes()) != old_target_digest
    assert result["structural_gate_status"] == "passed"
    assert result["threshold_status"] == "passed"


def test_json_snapshot_enforces_byte_limit(tmp_path: Path) -> None:
    path = tmp_path / "oversized.jsonl"
    path.write_bytes(b'{"a":1}\n')

    with pytest.raises(ContractError) as captured:
        snapshot_file(path, max_bytes=4)
    assert captured.value.code == "EVAL_FILE_TOO_LARGE"


def test_deep_json_recursion_is_a_structured_contract_error() -> None:
    deeply_nested = '{"value":' + "[" * 2000 + "0" + "]" * 2000 + "}"

    with pytest.raises(ContractError) as captured:
        parse_json_object(deeply_nested, location="deep.json:1")
    assert captured.value.code == "EVAL_JSON_NESTING_TOO_DEEP"


def test_label_annotation_version_must_match_manifest(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path, ["no-violation", "helmet-missing"])
    bundle["labels"][0]["annotation"]["spec_version"] = "labels-other"
    _rewrite_dataset(bundle)

    with pytest.raises(ContractError) as captured:
        validate_dataset(bundle["manifest_path"])
    assert captured.value.code == "EVAL_LABEL_SPEC_MISMATCH"


def test_nul_path_is_structured_integrity_error(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path, ["no-violation", "helmet-missing"])
    bundle["cases"][0]["inputs"][0]["relative_path"] = "assets/\x00bad.mp4"
    _rewrite_dataset(bundle)

    with pytest.raises(IntegrityError) as captured:
        validate_dataset(bundle["manifest_path"])
    assert captured.value.code == "EVAL_ASSET_PATH_UNSAFE"


def test_zero_size_asset_declaration_is_rejected_by_schema(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path, ["no-violation", "helmet-missing"])
    bundle["cases"][0]["inputs"][0]["size_bytes"] = 0
    _rewrite_dataset(bundle)

    with pytest.raises(ContractError) as captured:
        validate_dataset(bundle["manifest_path"])
    assert captured.value.code == "EVAL_SCHEMA_INVALID"


@pytest.mark.parametrize(
    "invalid_threshold",
    [0.85, "0.850", "0.8500000000000000000000000000000000000000000000000000000000000001"],
)
def test_threshold_must_be_the_canonical_json_string(invalid_threshold: Any, tmp_path: Path) -> None:
    bundle = _bundle(tmp_path, ["no-violation", "helmet-missing"])
    bundle["manifest"]["task"]["acceptance"]["threshold"] = invalid_threshold
    _write_json(bundle["manifest_path"], bundle["manifest"])

    with pytest.raises(ContractError) as captured:
        validate_dataset(bundle["manifest_path"])
    assert captured.value.code == "EVAL_SCHEMA_INVALID"


@pytest.mark.parametrize("invalid_ci_level", [0.95, "0.950", "0.9500000000000000000000000000001"])
def test_ci_level_must_be_the_canonical_json_string(invalid_ci_level: Any, tmp_path: Path) -> None:
    bundle = _bundle(tmp_path, ["no-violation", "helmet-missing"])
    bundle["manifest"]["task"]["acceptance"]["ci_level"] = invalid_ci_level
    _write_json(bundle["manifest_path"], bundle["manifest"])

    with pytest.raises(ContractError) as captured:
        validate_dataset(bundle["manifest_path"])
    assert captured.value.code == "EVAL_SCHEMA_INVALID"


def test_strict_schema_rejects_extra_prediction_fields(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path, ["no-violation", "helmet-missing"])
    bundle["predictions"][0]["unexpected"] = "forbidden"
    _rewrite_predictions(bundle)

    with pytest.raises(ContractError) as captured:
        _score(bundle)
    assert captured.value.code == "EVAL_SCHEMA_INVALID"


def test_prediction_score_keys_must_be_declared_classes(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path, ["no-violation", "helmet-missing"])
    bundle["predictions"][0]["output"]["scores"] = {"invented-class": 0.5}
    _rewrite_predictions(bundle)

    with pytest.raises(ContractError) as captured:
        _score(bundle)
    assert captured.value.code == "EVAL_PREDICTION_UNKNOWN_CLASS"


def test_tampered_frozen_cases_file_is_integrity_error(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path, ["no-violation", "helmet-missing"])
    bundle["cases_path"].write_bytes(bundle["cases_path"].read_bytes() + b" ")

    with pytest.raises(IntegrityError) as captured:
        validate_dataset(bundle["manifest_path"])
    assert captured.value.code == "EVAL_DATASET_DIGEST_MISMATCH"


def test_prediction_file_must_be_strict_utf8(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path, ["no-violation", "helmet-missing"])
    bundle["predictions_path"].write_bytes(b"\xff\xfe")

    with pytest.raises(ContractError) as captured:
        _score(bundle)
    assert captured.value.code == "EVAL_JSON_INVALID_UTF8"
