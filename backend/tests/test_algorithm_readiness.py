from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
import zipfile

import pytest

from app.algorithm_readiness import (
    APPROVAL_SCHEMA,
    DatasetProfile,
    audit_dataset,
    load_pilot_approval,
    preflight_pilot,
)
from app.evaluation.errors import ContractError


NOW = datetime(2026, 7, 14, 4, 0, tzinfo=timezone.utc)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_archive(root: Path, archive_path: Path) -> None:
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_STORED) as archive:
        for item in sorted(path for path in root.rglob("*") if path.is_file()):
            archive.write(item, arcname=item.relative_to(root).as_posix())


def _tiny_dataset(tmp_path: Path, *, label: str = "0 0.5 0.5 0.5 0.5\n") -> tuple[Path, Path, DatasetProfile]:
    root = tmp_path / "dataset"
    (root / "images" / "train").mkdir(parents=True)
    (root / "labels" / "train").mkdir(parents=True)
    (root / "LICENSE").write_text("test-license\n", encoding="utf-8")
    (root / "data.yaml").write_text("path: test\n", encoding="utf-8")
    (root / "images" / "train" / "frame.jpg").write_bytes(b"not-decoded-by-readiness")
    (root / "labels" / "train" / "frame.txt").write_text(label, encoding="utf-8")
    archive_path = tmp_path / "dataset.zip"
    _write_archive(root, archive_path)
    profile = DatasetProfile(
        source_id="tiny-source-v1",
        archive_sha256=_sha(archive_path),
        archive_size_bytes=archive_path.stat().st_size,
        archive_entry_count=4,
        license_sha256=_sha(root / "LICENSE"),
        data_yaml_sha256=_sha(root / "data.yaml"),
        class_count=1,
        split_counts={"train": (1, 1, 1)},
    )
    return root, archive_path, profile


def _approval(profile: DatasetProfile, weight_sha256: str, **updates: object) -> dict[str, object]:
    result: dict[str, object] = {
        "schema_version": APPROVAL_SCHEMA,
        "approval_id": "pilot-approval-001",
        "route_status": "accepted",
        "scope": "internal_development_only",
        "dataset": {
            "source_id": profile.source_id,
            "archive_sha256": profile.archive_sha256,
            "license_sha256": profile.license_sha256,
            "data_yaml_sha256": profile.data_yaml_sha256,
        },
        "model": {"artifact_sha256": weight_sha256},
        "confirmations": {
            "create_isolated_environment": True,
            "download_and_store_pinned_weights": True,
            "create_derived_data_and_run_artifacts": True,
            "use_local_compute_for_smoke_pilot": True,
            "internal_development_only": True,
            "dataset_usage_boundary_confirmed": True,
        },
        "approvers": {
            "project_lead": "project-lead",
            "data_license_owner": "data-owner",
            "qa_owner": "qa-owner",
            "advisor": "advisor",
        },
        "issued_at": "2026-07-14T03:00:00+00:00",
        "expires_at": "2026-07-15T03:00:00+00:00",
        "authorization_authenticity": "self_asserted_unsigned",
    }
    result.update(updates)
    return result


def _ready_inputs(tmp_path: Path) -> tuple[Path, Path, DatasetProfile, Path, Path, Path, Path, Path]:
    dataset, archive, profile = _tiny_dataset(tmp_path)
    project = tmp_path / "project"
    project.mkdir()
    python = tmp_path / "runtime" / "python-real"
    python.parent.mkdir()
    python.write_bytes(b"test-runtime")
    python.chmod(0o700)
    weight = tmp_path / "artifacts" / "model.pt"
    weight.parent.mkdir()
    weight.write_bytes(b"pinned-model")
    approval_path = tmp_path / "pilot-approval.json"
    approval_path.write_text(json.dumps(_approval(profile, _sha(weight))), encoding="utf-8")
    run_root = tmp_path / "runs" / "pilot-001"
    return dataset, archive, profile, project, python, weight, approval_path, run_root


def test_read_only_audit_accepts_registered_tiny_work_copy(tmp_path: Path) -> None:
    dataset, archive, profile = _tiny_dataset(tmp_path)

    report = audit_dataset(dataset, archive, profile=profile)

    assert report["status"] == "passed"
    assert report["observed_splits"]["train"]["paired_boxes"] == 1
    assert report["truth_boundaries"] == {
        "read_only": True,
        "files_written": False,
        "image_decode_performed": False,
        "near_duplicate_detection_performed": False,
        "formal_dataset_adopted": False,
        "training_authorized": False,
        "formal_metric_available": False,
    }


@pytest.mark.parametrize(
    ("label", "error_fragment"),
    [
        ("0 0.5 0.5 0.5\n", "expected 5 columns"),
        ("0 NaN 0.5 0.5 0.5\n", "non-finite"),
        ("1 0.5 0.5 0.5 0.5\n", "class id outside"),
        ("0 2 0.5 0.5 0.5\n", "outside tolerance"),
        ("not numeric values here x\n", "non-numeric"),
        ("\n", "empty row"),
    ],
)
def test_audit_rejects_invalid_yolo_rows(tmp_path: Path, label: str, error_fragment: str) -> None:
    dataset, archive, profile = _tiny_dataset(tmp_path, label=label)

    report = audit_dataset(dataset, archive, profile=profile)

    assert report["status"] == "failed"
    check = next(item for item in report["checks"] if item["id"] == "dataset.yolo_rows_valid")
    assert check["ok"] is False
    assert error_fragment in check["observed"]["invalid_labels"][0]["errors"][0]


def test_audit_rejects_tree_symlink_and_archive_drift(tmp_path: Path) -> None:
    dataset, archive, profile = _tiny_dataset(tmp_path)
    (dataset / "images" / "train" / "escape.jpg").symlink_to(tmp_path / "outside.jpg")
    archive.write_bytes(archive.read_bytes() + b"drift")

    report = audit_dataset(dataset, archive, profile=profile)

    assert report["status"] == "failed"
    failed_ids = {item["id"] for item in report["checks"] if not item["ok"]}
    assert "dataset.tree_no_symlinks_or_special_files" in failed_ids
    assert "dataset.archive_identity" in failed_ids


def test_audit_rejects_content_drift_even_when_counts_and_rows_stay_valid(tmp_path: Path) -> None:
    dataset, archive, profile = _tiny_dataset(tmp_path)
    (dataset / "images" / "train" / "frame.jpg").write_bytes(b"same-count-new-image-bytes")
    (dataset / "labels" / "train" / "frame.txt").write_text(
        "0 0.4 0.4 0.4 0.4\n",
        encoding="utf-8",
    )

    report = audit_dataset(dataset, archive, profile=profile)

    extraction = next(item for item in report["checks"] if item["id"] == "dataset.extracted_bytes_match_archive")
    assert report["status"] == "failed"
    assert extraction["ok"] is False
    assert extraction["observed"]["mismatch_count"] == 2


def test_audit_rejects_unknown_extra_and_hard_linked_files(tmp_path: Path) -> None:
    dataset, archive, profile = _tiny_dataset(tmp_path)
    original = dataset / "labels" / "train" / "frame.txt"
    extra = dataset / "labels" / "train" / "unregistered.bin"
    extra.hardlink_to(original)

    report = audit_dataset(dataset, archive, profile=profile)

    extraction = next(item for item in report["checks"] if item["id"] == "dataset.extracted_bytes_match_archive")
    tree = next(item for item in report["checks"] if item["id"] == "dataset.tree_no_symlinks_or_special_files")
    assert report["status"] == "failed"
    assert extraction["ok"] is False
    assert tree["ok"] is False


def test_nested_same_stem_files_are_matched_by_relative_path_not_silently_overwritten(tmp_path: Path) -> None:
    dataset, archive, profile = _tiny_dataset(tmp_path)
    nested_image = dataset / "images" / "train" / "nested" / "frame.jpg"
    nested_label = dataset / "labels" / "train" / "nested" / "frame.txt"
    nested_image.parent.mkdir()
    nested_label.parent.mkdir()
    nested_image.write_bytes(b"second-frame")
    nested_label.write_text("0 0.5 0.5 0.5 0.5\n", encoding="utf-8")
    archive.unlink()
    _write_archive(dataset, archive)
    nested_profile = replace(
        profile,
        archive_sha256=_sha(archive),
        archive_size_bytes=archive.stat().st_size,
        archive_entry_count=6,
        split_counts={"train": (2, 2, 2)},
    )

    report = audit_dataset(dataset, archive, profile=nested_profile)

    assert report["status"] == "passed"
    assert report["observed_splits"]["train"]["paired_images"] == 2


def test_audit_reports_missing_roots_and_registered_files(tmp_path: Path) -> None:
    dataset, archive, profile = _tiny_dataset(tmp_path)
    archive.unlink()
    (dataset / "LICENSE").unlink()
    (dataset / "images" / "train").rename(dataset / "images" / "missing")

    report = audit_dataset(dataset, archive, profile=profile)

    failed_ids = {item["id"] for item in report["checks"] if not item["ok"]}
    assert report["status"] == "failed"
    assert {"dataset.archive_identity", "dataset.license_identity", "dataset.tree_no_symlinks_or_special_files"} <= failed_ids


def test_audit_handles_invalid_utf8_and_registered_duplicate_row(tmp_path: Path) -> None:
    dataset, archive, profile = _tiny_dataset(tmp_path)
    label_path = dataset / "labels" / "train" / "frame.txt"
    label_path.write_bytes(b"\xff")
    invalid = audit_dataset(dataset, archive, profile=profile)
    assert "not strict UTF-8" in next(
        item for item in invalid["checks"] if item["id"] == "dataset.yolo_rows_valid"
    )["observed"]["invalid_labels"][0]["errors"][0]

    label_path.write_text("0 0.5 0.5 0.5 0.5\n0 0.5 0.5 0.5 0.5\n", encoding="utf-8")
    archive.unlink()
    _write_archive(dataset, archive)
    duplicate_profile = replace(
        profile,
        archive_sha256=_sha(archive),
        archive_size_bytes=archive.stat().st_size,
        split_counts={"train": (1, 1, 2)},
        expected_duplicate_rows=1,
    )
    duplicate = audit_dataset(dataset, archive, profile=duplicate_profile)
    assert duplicate["status"] == "passed"


def test_symlinked_dataset_component_is_never_accepted(tmp_path: Path) -> None:
    dataset, archive, profile = _tiny_dataset(tmp_path)
    linked = tmp_path / "linked-dataset"
    linked.symlink_to(dataset, target_is_directory=True)

    report = audit_dataset(linked, archive, profile=profile)

    root_check = next(item for item in report["checks"] if item["id"] == "dataset.root_real_directory")
    assert report["status"] == "failed"
    assert root_check["observed"]["symlink_components"] == [str(linked)]


def test_missing_approval_blocks_without_creating_run_root(tmp_path: Path) -> None:
    dataset, archive, profile = _tiny_dataset(tmp_path)
    project = tmp_path / "project"
    project.mkdir()
    run_root = tmp_path / "runs" / "must-not-be-created"

    report = preflight_pilot(
        dataset,
        archive,
        project_root=project,
        run_root=run_root,
        now=NOW,
        profile=profile,
    )

    assert report["status"] == "blocked"
    assert report["pilot_launch_eligible"] is False
    assert not run_root.exists()
    assert report["truth_flags"]["subprocess_started"] is False
    assert report["truth_flags"]["network_accessed"] is False
    assert report["truth_flags"]["files_written"] is False
    assert report["truth_flags"]["formal_metric_available"] is False


def test_fully_bound_static_inputs_still_cannot_authorize_launch(tmp_path: Path) -> None:
    dataset, archive, profile, project, python, weight, approval, run_root = _ready_inputs(tmp_path)

    report = preflight_pilot(
        dataset,
        archive,
        project_root=project,
        approval_path=approval,
        training_python=python,
        weight_artifact=weight,
        run_root=run_root,
        now=NOW,
        profile=profile,
    )

    assert report["status"] == "blocked"
    assert report["static_diagnostic_checks_passed"] is True
    assert report["pilot_launch_eligible"] is False
    assert {check["id"] for check in report["checks"] if not check["ok"]} == {
        "pilot.trusted_authorization_verified",
        "pilot.runtime_health_verified",
        "pilot.atomic_launch_handoff_available",
    }
    assert report["truth_flags"]["route_status"] == "pending"
    assert report["truth_flags"]["self_asserted_route_status"] == "accepted"
    assert report["truth_flags"]["weight_artifact_supplied"] is True
    assert report["truth_flags"]["weights_downloaded_by_preflight"] is False
    assert report["truth_flags"]["training_started"] is False
    assert report["truth_flags"]["authorization_cryptographically_verified"] is False
    assert report["truth_flags"]["runtime_health_verified"] is False
    assert report["truth_flags"]["atomic_launch_handoff_available"] is False
    assert not run_root.exists()


@pytest.mark.parametrize("failure", ["expired", "dataset_binding", "weight_binding"])
def test_expired_or_mismatched_approval_blocks(tmp_path: Path, failure: str) -> None:
    dataset, archive, profile, project, python, weight, approval_path, run_root = _ready_inputs(tmp_path)
    approval = json.loads(approval_path.read_text(encoding="utf-8"))
    if failure == "expired":
        approval["issued_at"] = "2026-07-12T03:00:00+00:00"
        approval["expires_at"] = "2026-07-13T03:00:00+00:00"
    elif failure == "dataset_binding":
        approval["dataset"]["archive_sha256"] = "f" * 64
    else:
        approval["model"]["artifact_sha256"] = "e" * 64
    approval_path.write_text(json.dumps(approval), encoding="utf-8")

    report = preflight_pilot(
        dataset,
        archive,
        project_root=project,
        approval_path=approval_path,
        training_python=python,
        weight_artifact=weight,
        run_root=run_root,
        now=NOW,
        profile=profile,
    )

    assert report["status"] == "blocked"
    assert any(not check["ok"] for check in report["checks"])


def test_unsafe_or_nonempty_run_root_blocks(tmp_path: Path) -> None:
    dataset, archive, profile, project, python, weight, approval, _ = _ready_inputs(tmp_path)
    run_root = project / "runs"
    run_root.mkdir()
    (run_root / "old.txt").write_text("do not overwrite", encoding="utf-8")

    report = preflight_pilot(
        dataset,
        archive,
        project_root=project,
        approval_path=approval,
        training_python=python,
        weight_artifact=weight,
        run_root=run_root,
        now=NOW,
        profile=profile,
    )

    check = next(item for item in report["checks"] if item["id"] == "pilot.run_root_safe")
    assert report["status"] == "blocked"
    assert check["ok"] is False
    assert (run_root / "old.txt").read_text(encoding="utf-8") == "do not overwrite"


def test_duplicate_json_keys_and_non_independent_approvers_are_rejected(tmp_path: Path) -> None:
    duplicate = tmp_path / "duplicate.json"
    duplicate.write_text('{"schema_version":"x","schema_version":"y"}', encoding="utf-8")
    with pytest.raises(ContractError) as duplicate_error:
        load_pilot_approval(duplicate)
    assert duplicate_error.value.code == "EVAL_JSON_DUPLICATE_KEY"

    _, _, profile, _, _, weight, approval_path, _ = _ready_inputs(tmp_path / "second")
    approval = _approval(profile, _sha(weight))
    approval["approvers"]["qa_owner"] = "data-owner"
    approval_path.write_text(json.dumps(approval), encoding="utf-8")
    with pytest.raises(ContractError) as approver_error:
        load_pilot_approval(approval_path)
    assert approver_error.value.code == "ALGORITHM_APPROVAL_INVALID"


def test_blank_or_symlinked_approval_is_rejected(tmp_path: Path) -> None:
    _, _, profile, _, _, weight, approval_path, _ = _ready_inputs(tmp_path)
    approval = _approval(profile, _sha(weight))
    approval["approvers"]["project_lead"] = "   "
    approval_path.write_text(json.dumps(approval), encoding="utf-8")
    with pytest.raises(ContractError) as blank_error:
        load_pilot_approval(approval_path)
    assert blank_error.value.code == "ALGORITHM_APPROVAL_INVALID"

    real_parent = tmp_path / "real-approval-root"
    real_parent.mkdir()
    real_approval = real_parent / "approval.json"
    real_approval.write_text(json.dumps(_approval(profile, _sha(weight))), encoding="utf-8")
    linked_parent = tmp_path / "linked-approval-root"
    linked_parent.symlink_to(real_parent, target_is_directory=True)
    with pytest.raises(ContractError) as linked_error:
        load_pilot_approval(linked_parent / "approval.json")
    assert linked_error.value.code == "ALGORITHM_APPROVAL_PATH_UNSAFE"


@pytest.mark.parametrize(
    ("issued_at", "expires_at"),
    [
        ("not-a-date", "2026-07-15T03:00:00+00:00"),
        ("2026-07-14T03:00:00", "2026-07-15T03:00:00+00:00"),
        ("2026-07-15T03:00:00Z", "2026-07-14T03:00:00Z"),
        ("2026-07-14T03:00:00Z", "2026-07-18T03:00:01Z"),
    ],
)
def test_approval_requires_aware_increasing_time_window(
    tmp_path: Path,
    issued_at: str,
    expires_at: str,
) -> None:
    _, _, profile, _, _, weight, approval_path, _ = _ready_inputs(tmp_path)
    approval = _approval(profile, _sha(weight), issued_at=issued_at, expires_at=expires_at)
    approval_path.write_text(json.dumps(approval), encoding="utf-8")

    with pytest.raises(ContractError) as captured:
        load_pilot_approval(approval_path)

    assert captured.value.code == "ALGORITHM_APPROVAL_INVALID"


def test_invalid_approval_and_missing_artifacts_are_reported_as_blocked(tmp_path: Path) -> None:
    dataset, archive, profile = _tiny_dataset(tmp_path)
    project = tmp_path / "project"
    project.mkdir()
    invalid_approval = tmp_path / "approval.json"
    invalid_approval.write_text("{}", encoding="utf-8")

    report = preflight_pilot(
        dataset,
        archive,
        project_root=project,
        approval_path=invalid_approval,
        training_python=tmp_path / "missing-python",
        weight_artifact=tmp_path / "missing-weight",
        run_root=None,
        now=NOW,
        profile=profile,
    )

    assert report["status"] == "blocked"
    assert report["approval_error"]["code"] == "ALGORITHM_APPROVAL_INVALID"
    json.dumps(report, allow_nan=False)
    failed_ids = {item["id"] for item in report["checks"] if not item["ok"]}
    assert {"pilot.training_python_regular", "pilot.weight_artifact_regular", "pilot.run_root_safe"} <= failed_ids
