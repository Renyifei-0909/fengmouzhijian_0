from __future__ import annotations

import hashlib
import math
import os
import stat
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Literal

from pydantic import Field, ValidationError, field_validator, model_validator

from .evaluation.errors import ContractError
from .evaluation.jsonio import (
    open_relative_regular_file,
    parse_json_object,
    snapshot_file,
    snapshot_relative_file,
)
from .evaluation.schemas import ID_PATTERN, SHA256_PATTERN, StrictModel


APPROVAL_SCHEMA = "fengmou.algorithm-pilot-approval.v1"
READINESS_SCHEMA = "fengmou.algorithm-readiness-report.v1"
MAX_APPROVAL_BYTES = 64 * 1024
LABEL_EPSILON = 1e-6


@dataclass(frozen=True)
class DatasetProfile:
    source_id: str
    archive_sha256: str
    archive_size_bytes: int
    archive_entry_count: int
    license_sha256: str
    data_yaml_sha256: str
    class_count: int
    split_counts: dict[str, tuple[int, int, int]]
    expected_orphan_labels: tuple[str, ...] = ()
    expected_duplicate_rows: int = 0
    known_cross_split_near_duplicates: tuple[tuple[str, str], ...] = ()


CONSTRUCTION_PPE_PROFILE = DatasetProfile(
    source_id="ultralytics-construction-ppe-work-copy-2026-07-10",
    archive_sha256="bef8dcb599aa4e9d9f5e602cb6fa7143d3c84d7f6a0ff40463d7f2a4c2632ccc",
    archive_size_bytes=178_415_813,
    archive_entry_count=2_852,
    license_sha256="0d96a4ff68ad6d4b6f1f30f713b18d5184912ba8dd389f86aa7710db079abcb0",
    data_yaml_sha256="bfbc2471c75a82beaca2c255b7814d7eaf3087f191f1c7e43c8d8e90a27e961a",
    class_count=11,
    split_counts={
        "train": (1_132, 1_142, 9_098),
        "val": (143, 143, 1_172),
        "test": (141, 141, 1_251),
    },
    expected_orphan_labels=(
        "image940(1)",
        "image941(1)",
        "image944(1)",
        "image945(1)",
        "image946(1)",
        "image947(1)",
        "image948(1)",
        "image949(1)",
        "image950(1)",
        "image95(1)",
    ),
    expected_duplicate_rows=1,
    known_cross_split_near_duplicates=(
        ("images/train/image1050.jpg", "images/val/image1049.jpg"),
        ("images/train/image1087.jpg", "images/test/image1088.jpg"),
        ("images/train/image833.jpg", "images/test/image834.jpg"),
    ),
)


class DatasetApprovalBinding(StrictModel):
    source_id: str = Field(pattern=ID_PATTERN)
    archive_sha256: str = Field(pattern=SHA256_PATTERN)
    license_sha256: str = Field(pattern=SHA256_PATTERN)
    data_yaml_sha256: str = Field(pattern=SHA256_PATTERN)


class ModelApprovalBinding(StrictModel):
    artifact_sha256: str = Field(pattern=SHA256_PATTERN)


class PilotConfirmations(StrictModel):
    create_isolated_environment: Literal[True]
    download_and_store_pinned_weights: Literal[True]
    create_derived_data_and_run_artifacts: Literal[True]
    use_local_compute_for_smoke_pilot: Literal[True]
    internal_development_only: Literal[True]
    dataset_usage_boundary_confirmed: Literal[True]


class PilotApprovers(StrictModel):
    project_lead: str = Field(min_length=1, max_length=200)
    data_license_owner: str = Field(min_length=1, max_length=200)
    qa_owner: str = Field(min_length=1, max_length=200)
    advisor: str = Field(min_length=1, max_length=200)

    @field_validator("project_lead", "data_license_owner", "qa_owner", "advisor")
    @classmethod
    def reject_blank_approver(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("approver identity must contain a non-whitespace character")
        return normalized

    @model_validator(mode="after")
    def require_distinct_approvers(self) -> "PilotApprovers":
        identities = {
            self.project_lead.casefold(),
            self.data_license_owner.casefold(),
            self.qa_owner.casefold(),
            self.advisor.casefold(),
        }
        if len(identities) != 4:
            raise ValueError("project, data/license, QA, and advisor approvers must be distinct")
        return self


class PilotApproval(StrictModel):
    schema_version: Literal["fengmou.algorithm-pilot-approval.v1"]
    approval_id: str = Field(pattern=ID_PATTERN)
    route_status: Literal["accepted"]
    scope: Literal["internal_development_only"]
    dataset: DatasetApprovalBinding
    model: ModelApprovalBinding
    confirmations: PilotConfirmations
    approvers: PilotApprovers
    issued_at: str = Field(min_length=1, max_length=64)
    expires_at: str = Field(min_length=1, max_length=64)
    authorization_authenticity: Literal["self_asserted_unsigned"]

    @model_validator(mode="after")
    def validate_time_window(self) -> "PilotApproval":
        issued = _parse_aware_datetime(self.issued_at, field="issued_at")
        expires = _parse_aware_datetime(self.expires_at, field="expires_at")
        if expires <= issued:
            raise ValueError("expires_at must be later than issued_at")
        if (expires - issued).total_seconds() > 72 * 60 * 60:
            raise ValueError("approval validity may not exceed 72 hours")
        return self


def _parse_aware_datetime(value: str, *, field: str) -> datetime:
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO-8601 datetime") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field} must include a timezone offset")
    return parsed.astimezone(timezone.utc)


def _check(check_id: str, ok: bool, detail: str, **observed: Any) -> dict[str, Any]:
    result: dict[str, Any] = {"id": check_id, "ok": ok, "detail": detail}
    if observed:
        result["observed"] = observed
    return result


def _hash_regular_file(path: Path) -> tuple[str, int]:
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode):
        raise OSError("path is not a regular file")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            raise OSError("opened path is not a regular file")
        if opened.st_nlink != 1:
            raise OSError("hard-linked files are forbidden")
        if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            raise OSError("file identity changed while opening")
        digest = hashlib.sha256()
        while True:
            block = os.read(descriptor, 1024 * 1024)
            if not block:
                break
            digest.update(block)
        after = os.fstat(descriptor)
        if (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) != (
            opened.st_dev,
            opened.st_ino,
            opened.st_size,
            opened.st_mtime_ns,
        ):
            raise OSError("file changed while hashing")
        return digest.hexdigest(), opened.st_size
    finally:
        os.close(descriptor)


def _symlink_components(path: Path, *, include_leaf: bool = True) -> list[str]:
    absolute = path.absolute()
    components = absolute.parts[1:] if absolute.anchor else absolute.parts
    current = Path(absolute.anchor) if absolute.anchor else Path()
    symlinks: list[str] = []
    limit = len(components) if include_leaf else max(0, len(components) - 1)
    for component in components[:limit]:
        current /= component
        if os.path.lexists(current) and current.is_symlink():
            symlinks.append(str(current))
    return symlinks


def _walk_regular_files(root: Path) -> tuple[list[Path], list[str]]:
    files: list[Path] = []
    errors: list[str] = []
    stack = [root]
    while stack:
        directory = stack.pop()
        try:
            if directory.is_symlink() or not directory.is_dir():
                errors.append(f"not a real directory: {directory}")
                continue
            with os.scandir(directory) as entries:
                for entry in entries:
                    entry_path = Path(entry.path)
                    try:
                        if entry.is_symlink():
                            errors.append(f"symlink forbidden: {entry_path}")
                        elif entry.is_dir(follow_symlinks=False):
                            stack.append(entry_path)
                        elif entry.is_file(follow_symlinks=False):
                            if entry.stat(follow_symlinks=False).st_nlink != 1:
                                errors.append(f"hard-linked file forbidden: {entry_path}")
                            else:
                                files.append(entry_path)
                        else:
                            errors.append(f"special file forbidden: {entry_path}")
                    except OSError as exc:
                        errors.append(f"cannot inspect {entry_path}: {exc}")
        except OSError as exc:
            errors.append(f"cannot scan {directory}: {exc}")
    return sorted(files), sorted(errors)


def _zip_member_summary(archive: zipfile.ZipFile) -> tuple[int, list[str]]:
    unsafe: list[str] = []
    members = archive.infolist()
    for member in members:
        pure = PurePosixPath(member.filename)
        unix_mode = (member.external_attr >> 16) & 0xFFFF
        if (
            not member.filename
            or "\\" in member.filename
            or pure.is_absolute()
            or any(part in {"", ".", ".."} for part in pure.parts)
            or member.flag_bits & 0x1
            or stat.S_ISLNK(unix_mode)
        ):
            unsafe.append(member.filename)
    return len(members), sorted(unsafe)


def _inspect_zip_regular_file(
    path: Path,
    *,
    extracted_root: Path,
) -> tuple[str, int, int, list[str], int, list[str], list[str]]:
    """Hash the archive and compare the extracted tree to that same opened identity."""

    before = path.lstat()
    if not stat.S_ISREG(before.st_mode):
        raise OSError("archive path is not a regular file")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            raise OSError("opened archive is not a regular file")
        if opened.st_nlink != 1:
            raise OSError("hard-linked archives are forbidden")
        if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            raise OSError("archive identity changed while opening")
        digest = hashlib.sha256()
        while True:
            block = os.read(descriptor, 1024 * 1024)
            if not block:
                break
            digest.update(block)
        with os.fdopen(os.dup(descriptor), "rb", closefd=True) as handle:
            with zipfile.ZipFile(handle) as archive:
                entry_count, unsafe_members = _zip_member_summary(archive)
                archive_files = {
                    member.filename: member
                    for member in archive.infolist()
                    if not member.is_dir() and member.filename not in unsafe_members
                }
                compared_files = 0
                comparison_errors: list[str] = []
                for relative_path, member in sorted(archive_files.items()):
                    try:
                        with open_relative_regular_file(extracted_root, relative_path) as (
                            extracted,
                            extracted_stat,
                            _,
                        ):
                            if extracted_stat.st_nlink != 1:
                                comparison_errors.append(f"hard-linked extracted file: {relative_path}")
                                continue
                            if extracted_stat.st_size != member.file_size:
                                comparison_errors.append(f"size mismatch: {relative_path}")
                                continue
                            with archive.open(member, "r") as archived:
                                while True:
                                    archived_block = archived.read(1024 * 1024)
                                    extracted_block = extracted.read(1024 * 1024)
                                    if archived_block != extracted_block:
                                        comparison_errors.append(f"content mismatch: {relative_path}")
                                        break
                                    if not archived_block:
                                        compared_files += 1
                                        break
                    except (ContractError, OSError, RuntimeError, zipfile.BadZipFile) as exc:
                        comparison_errors.append(f"cannot compare {relative_path}: {exc}")
                extracted_files, extracted_tree_errors = _walk_regular_files(extracted_root)
                extracted_roster = {
                    item.relative_to(extracted_root).as_posix()
                    for item in extracted_files
                }
                extra_extracted_paths = sorted(extracted_roster - set(archive_files))
                comparison_errors.extend(extracted_tree_errors)
        after = os.fstat(descriptor)
        if (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) != (
            opened.st_dev,
            opened.st_ino,
            opened.st_size,
            opened.st_mtime_ns,
        ):
            raise OSError("archive changed while auditing")
        return (
            digest.hexdigest(),
            opened.st_size,
            entry_count,
            unsafe_members,
            compared_files,
            sorted(comparison_errors),
            extra_extracted_paths,
        )
    finally:
        os.close(descriptor)


def _parse_label(data: bytes, *, class_count: int) -> tuple[int, int, list[str]]:
    try:
        text = data.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        return 0, 0, [f"invalid UTF-8: {exc}"]
    errors: list[str] = []
    box_count = 0
    duplicate_rows = 0
    seen: set[str] = set()
    for line_number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line:
            errors.append(f"line {line_number}: empty row")
            continue
        columns = line.split()
        if len(columns) != 5:
            errors.append(f"line {line_number}: expected 5 columns")
            continue
        try:
            class_value = float(columns[0])
            coordinates = [float(value) for value in columns[1:]]
        except ValueError:
            errors.append(f"line {line_number}: non-numeric value")
            continue
        if not math.isfinite(class_value) or any(not math.isfinite(value) for value in coordinates):
            errors.append(f"line {line_number}: non-finite value")
            continue
        class_id = int(class_value)
        if class_value != class_id or not 0 <= class_id < class_count:
            errors.append(f"line {line_number}: class id outside 0..{class_count - 1}")
            continue
        x_center, y_center, width, height = coordinates
        if not (
            -LABEL_EPSILON <= x_center <= 1 + LABEL_EPSILON
            and -LABEL_EPSILON <= y_center <= 1 + LABEL_EPSILON
            and 0 < width <= 1 + LABEL_EPSILON
            and 0 < height <= 1 + LABEL_EPSILON
        ):
            errors.append(f"line {line_number}: normalized coordinates outside tolerance")
            continue
        canonical = " ".join(columns)
        if canonical in seen:
            duplicate_rows += 1
        else:
            seen.add(canonical)
        box_count += 1
    return box_count, duplicate_rows, errors


def audit_dataset(
    dataset_root: Path,
    archive_path: Path,
    *,
    profile: DatasetProfile = CONSTRUCTION_PPE_PROFILE,
) -> dict[str, Any]:
    """Audit a work copy without writing, decoding images, or deriving a dataset."""

    dataset_root = dataset_root.absolute()
    archive_path = archive_path.absolute()
    checks: list[dict[str, Any]] = []

    root_symlinks = _symlink_components(dataset_root)
    root_ok = dataset_root.is_dir() and not dataset_root.is_symlink() and not root_symlinks
    checks.append(
        _check(
            "dataset.root_real_directory",
            root_ok,
            "dataset root exists and has no symlink components" if root_ok else "dataset root is missing, non-directory, or symlinked",
            path=str(dataset_root),
            symlink_components=root_symlinks,
        )
    )
    archive_symlinks = _symlink_components(archive_path)
    try:
        (
            archive_sha256,
            archive_size,
            archive_entries,
            unsafe_members,
            compared_files,
            extraction_mismatches,
            extra_extracted_paths,
        ) = _inspect_zip_regular_file(archive_path, extracted_root=dataset_root)
        archive_ok = (
            not archive_symlinks
            and archive_sha256 == profile.archive_sha256
            and archive_size == profile.archive_size_bytes
            and archive_entries == profile.archive_entry_count
            and not unsafe_members
        )
        archive_detail = "archive identity and safe member contract match" if archive_ok else "archive identity or member contract mismatch"
    except (OSError, zipfile.BadZipFile, ValueError) as exc:
        archive_sha256, archive_size, archive_entries, unsafe_members = None, None, None, []
        compared_files, extraction_mismatches, extra_extracted_paths = 0, [], []
        archive_ok = False
        archive_detail = f"archive cannot be safely audited: {exc}"
    checks.append(
        _check(
            "dataset.archive_identity",
            archive_ok,
            archive_detail,
            path=str(archive_path),
            sha256=archive_sha256,
            size_bytes=archive_size,
            entry_count=archive_entries,
            unsafe_member_count=len(unsafe_members),
            symlink_components=archive_symlinks,
        )
    )
    extraction_ok = archive_ok and not extraction_mismatches and not extra_extracted_paths
    checks.append(
        _check(
            "dataset.extracted_bytes_match_archive",
            extraction_ok,
            "every extracted regular file is byte-identical to the pinned archive and no extra file exists"
            if extraction_ok
            else "extracted tree differs from the pinned archive or could not be securely compared",
            compared_file_count=compared_files,
            mismatch_count=len(extraction_mismatches),
            mismatches=extraction_mismatches[:20],
            extra_file_count=len(extra_extracted_paths),
            extra_files=extra_extracted_paths[:20],
        )
    )

    for relative, expected_sha, check_id in (
        ("LICENSE", profile.license_sha256, "dataset.license_identity"),
        ("data.yaml", profile.data_yaml_sha256, "dataset.yaml_identity"),
    ):
        path = dataset_root / relative
        try:
            digest, size = _hash_regular_file(path)
            ok = digest == expected_sha and not _symlink_components(path)
            detail = "file identity matches the registered work copy" if ok else "file identity differs from the registered work copy"
        except OSError as exc:
            digest, size, ok = None, None, False
            detail = f"cannot safely read file: {exc}"
        checks.append(_check(check_id, ok, detail, path=str(path), sha256=digest, size_bytes=size))

    split_observed: dict[str, dict[str, Any]] = {}
    all_tree_errors: list[str] = []
    invalid_labels: list[dict[str, Any]] = []
    total_duplicate_rows = 0
    actual_orphans: list[str] = []
    for split, expected in profile.split_counts.items():
        image_root = dataset_root / "images" / split
        label_root = dataset_root / "labels" / split
        image_files, image_errors = _walk_regular_files(image_root)
        label_files, label_errors = _walk_regular_files(label_root)
        all_tree_errors.extend(image_errors)
        all_tree_errors.extend(label_errors)
        images = [item for item in image_files if item.suffix.casefold() in {".jpg", ".jpeg", ".png", ".bmp", ".webp"}]
        labels = [item for item in label_files if item.suffix.casefold() == ".txt"]
        image_by_stem = {
            item.relative_to(image_root).with_suffix("").as_posix(): item
            for item in images
        }
        label_by_stem = {
            item.relative_to(label_root).with_suffix("").as_posix(): item
            for item in labels
        }
        paired_stems = sorted(image_by_stem.keys() & label_by_stem.keys())
        missing_labels = sorted(image_by_stem.keys() - label_by_stem.keys())
        orphan_stems = sorted(label_by_stem.keys() - image_by_stem.keys())
        actual_orphans.extend(orphan_stems)
        paired_boxes = 0
        for stem in sorted(label_by_stem):
            label_path = label_by_stem[stem]
            try:
                relative_label = label_path.relative_to(dataset_root).as_posix()
                snapshot = snapshot_relative_file(dataset_root, relative_label, max_bytes=2 * 1024 * 1024)
                boxes, duplicate_rows, errors = _parse_label(snapshot.data, class_count=profile.class_count)
            except (ContractError, OSError, ValueError) as exc:
                boxes, duplicate_rows, errors = 0, 0, [str(exc)]
            total_duplicate_rows += duplicate_rows
            if stem in image_by_stem:
                paired_boxes += boxes
            if errors:
                invalid_labels.append(
                    {
                        "path": label_path.relative_to(dataset_root).as_posix(),
                        "errors": errors[:20],
                        "truncated": len(errors) > 20,
                    }
                )
        split_observed[split] = {
            "images": len(images),
            "labels": len(labels),
            "paired_images": len(paired_stems),
            "paired_boxes": paired_boxes,
            "missing_labels": missing_labels,
            "orphan_labels": orphan_stems,
            "expected": {"images": expected[0], "labels": expected[1], "paired_boxes": expected[2]},
        }

    checks.append(
        _check(
            "dataset.tree_no_symlinks_or_special_files",
            not all_tree_errors,
            "image and label trees contain only real directories and regular files" if not all_tree_errors else "unsafe tree entries found",
            error_count=len(all_tree_errors),
            errors=all_tree_errors[:20],
        )
    )
    counts_ok = all(
        observed["images"] == observed["expected"]["images"]
        and observed["labels"] == observed["expected"]["labels"]
        and observed["paired_boxes"] == observed["expected"]["paired_boxes"]
        and not observed["missing_labels"]
        for observed in split_observed.values()
    )
    checks.append(
        _check(
            "dataset.registered_split_counts",
            counts_ok,
            "split counts match the registered work copy" if counts_ok else "split counts differ from the registered work copy",
            splits=split_observed,
        )
    )
    checks.append(
        _check(
            "dataset.yolo_rows_valid",
            not invalid_labels,
            "all label rows satisfy the registered YOLO scalar contract" if not invalid_labels else "invalid label rows found",
            invalid_label_count=len(invalid_labels),
            invalid_labels=invalid_labels[:20],
            coordinate_epsilon=LABEL_EPSILON,
        )
    )
    orphan_ok = sorted(actual_orphans) == sorted(profile.expected_orphan_labels)
    checks.append(
        _check(
            "dataset.known_orphan_labels",
            orphan_ok,
            "orphan labels match the registered known issue" if orphan_ok else "orphan label set changed",
            observed=sorted(actual_orphans),
            expected=sorted(profile.expected_orphan_labels),
        )
    )
    duplicate_ok = total_duplicate_rows == profile.expected_duplicate_rows
    checks.append(
        _check(
            "dataset.known_duplicate_rows",
            duplicate_ok,
            "duplicate label-row count matches the registered known issue" if duplicate_ok else "duplicate label-row count changed",
            observed=total_duplicate_rows,
            expected=profile.expected_duplicate_rows,
        )
    )
    missing_near_duplicate_paths = [
        relative
        for pair in profile.known_cross_split_near_duplicates
        for relative in pair
        if not (dataset_root / relative).is_file()
    ]
    checks.append(
        _check(
            "dataset.known_cross_split_leakage_references",
            not missing_near_duplicate_paths,
            "registered manually reviewed near-duplicate references are present"
            if not missing_near_duplicate_paths
            else "registered near-duplicate references are missing",
            registered_pairs=[list(pair) for pair in profile.known_cross_split_near_duplicates],
            missing_paths=missing_near_duplicate_paths,
            rederived_by_this_command=False,
        )
    )

    passed = all(item["ok"] for item in checks)
    return {
        "schema_version": "fengmou.dataset-readonly-audit.v1",
        "status": "passed" if passed else "failed",
        "source_id": profile.source_id,
        "dataset_root": str(dataset_root),
        "archive_path": str(archive_path),
        "checks": checks,
        "observed_splits": split_observed,
        "truth_boundaries": {
            "read_only": True,
            "files_written": False,
            "image_decode_performed": False,
            "near_duplicate_detection_performed": False,
            "formal_dataset_adopted": False,
            "training_authorized": False,
            "formal_metric_available": False,
        },
    }


def load_pilot_approval(path: Path) -> PilotApproval:
    absolute = path.absolute()
    symlinks = _symlink_components(absolute)
    if symlinks:
        raise ContractError(
            "ALGORITHM_APPROVAL_PATH_UNSAFE",
            "Pilot approval path must not contain symbolic-link components",
            path=str(absolute),
            details={"symlink_components": symlinks},
        )
    try:
        snapshot = snapshot_file(absolute, max_bytes=MAX_APPROVAL_BYTES)
        raw = parse_json_object(snapshot.text, location=str(absolute))
        return PilotApproval.model_validate(raw)
    except ValidationError as exc:
        raise ContractError(
            "ALGORITHM_APPROVAL_INVALID",
            "Pilot approval does not satisfy the strict schema",
            path=str(absolute),
            details={
                "errors": exc.errors(
                    include_url=False,
                    include_context=False,
                    include_input=False,
                )
            },
        ) from exc


def _path_is_within(candidate: Path, root: Path) -> bool:
    try:
        candidate.relative_to(root)
        return True
    except ValueError:
        return False


def _artifact_check(path: Path | None, *, executable: bool, check_id: str) -> tuple[dict[str, Any], str | None]:
    if path is None:
        return _check(check_id, False, "explicit artifact path is required", path=None), None
    absolute = path.absolute()
    symlinks = _symlink_components(absolute)
    try:
        digest, size = _hash_regular_file(absolute)
        mode = absolute.lstat().st_mode
        ok = not symlinks and (not executable or bool(mode & 0o111))
        detail = "artifact is a real, immutable-by-identity file" if ok else "artifact is symlinked or not executable"
    except OSError as exc:
        digest, size, ok = None, None, False
        detail = f"artifact cannot be safely read: {exc}"
    return (
        _check(
            check_id,
            ok,
            detail,
            path=str(absolute),
            sha256=digest,
            size_bytes=size,
            executable_required=executable,
            symlink_components=symlinks,
        ),
        digest,
    )


def _run_root_check(run_root: Path | None, *, project_root: Path, dataset_root: Path) -> dict[str, Any]:
    if run_root is None:
        return _check("pilot.run_root_safe", False, "explicit run root is required", path=None)
    absolute = run_root.absolute()
    normalized = absolute.resolve(strict=False)
    symlinks = _symlink_components(absolute)
    absolute_required = run_root.is_absolute()
    outside_protected = not _path_is_within(normalized, project_root.resolve()) and not _path_is_within(
        normalized, dataset_root.resolve()
    )
    if os.path.lexists(absolute):
        existing_ok = absolute.is_dir() and not absolute.is_symlink()
        try:
            empty = existing_ok and not any(absolute.iterdir())
        except OSError:
            empty = False
    else:
        existing_ok = True
        empty = True
    ok = absolute_required and outside_protected and existing_ok and empty and not symlinks
    return _check(
        "pilot.run_root_safe",
        ok,
        "run root is absolute, empty/nonexistent, non-symlinked, and outside source/data roots"
        if ok
        else "run root violates isolation, emptiness, or symlink requirements",
        path=str(absolute),
        absolute_required=absolute_required,
        outside_project_and_dataset=outside_protected,
        empty_or_nonexistent=empty,
        symlink_components=symlinks,
    )


def preflight_pilot(
    dataset_root: Path,
    archive_path: Path,
    *,
    project_root: Path,
    approval_path: Path | None = None,
    training_python: Path | None = None,
    weight_artifact: Path | None = None,
    run_root: Path | None = None,
    now: datetime | None = None,
    profile: DatasetProfile = CONSTRUCTION_PPE_PROFILE,
) -> dict[str, Any]:
    """Return readiness only. This function never installs, downloads, writes, or starts a subprocess."""

    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    dataset_audit = audit_dataset(dataset_root, archive_path, profile=profile)
    checks: list[dict[str, Any]] = [
        _check(
            "pilot.dataset_audit_passed",
            dataset_audit["status"] == "passed",
            "registered dataset work copy passed read-only audit"
            if dataset_audit["status"] == "passed"
            else "dataset work copy failed read-only audit",
        )
    ]

    approval: PilotApproval | None = None
    approval_error: dict[str, Any] | None = None
    if approval_path is None:
        checks.append(_check("pilot.approval_present_and_valid", False, "strict six-item approval is required", path=None))
    else:
        try:
            approval = load_pilot_approval(approval_path)
            checks.append(
                _check(
                    "pilot.approval_present_and_valid",
                    True,
                    "strict approval schema is valid; authenticity remains self-asserted and unsigned",
                    path=str(approval_path.absolute()),
                    approval_id=approval.approval_id,
                )
            )
        except ContractError as exc:
            approval_error = exc.as_dict()
            checks.append(
                _check(
                    "pilot.approval_present_and_valid",
                    False,
                    "approval is missing, unreadable, or invalid",
                    path=str(approval_path.absolute()),
                    error=approval_error,
                )
            )

    self_asserted_route_status = "missing"
    if approval is not None:
        issued = _parse_aware_datetime(approval.issued_at, field="issued_at")
        expires = _parse_aware_datetime(approval.expires_at, field="expires_at")
        time_ok = issued <= current <= expires
        checks.append(
            _check(
                "pilot.approval_time_window",
                time_ok,
                "approval is active at evaluation time" if time_ok else "approval is not active at evaluation time",
                issued_at=issued.isoformat(),
                expires_at=expires.isoformat(),
                evaluated_at=current.isoformat(),
            )
        )
        expected_binding = (
            profile.source_id,
            profile.archive_sha256,
            profile.license_sha256,
            profile.data_yaml_sha256,
        )
        actual_binding = (
            approval.dataset.source_id,
            approval.dataset.archive_sha256,
            approval.dataset.license_sha256,
            approval.dataset.data_yaml_sha256,
        )
        binding_ok = expected_binding == actual_binding
        checks.append(
            _check(
                "pilot.approval_dataset_binding",
                binding_ok,
                "approval is bound to this registered dataset work copy"
                if binding_ok
                else "approval dataset binding does not match this work copy",
            )
        )
        if time_ok and binding_ok:
            self_asserted_route_status = approval.route_status

    python_check, _ = _artifact_check(training_python, executable=True, check_id="pilot.training_python_regular")
    weight_check, weight_sha256 = _artifact_check(weight_artifact, executable=False, check_id="pilot.weight_artifact_regular")
    checks.extend([python_check, weight_check])
    if approval is None:
        weight_binding_ok = False
        weight_binding_detail = "valid approval is required before model binding can pass"
    else:
        weight_binding_ok = weight_sha256 == approval.model.artifact_sha256
        weight_binding_detail = (
            "weight artifact digest matches approval" if weight_binding_ok else "weight artifact digest does not match approval"
        )
    checks.append(
        _check(
            "pilot.weight_artifact_approval_binding",
            weight_binding_ok,
            weight_binding_detail,
            observed_sha256=weight_sha256,
            approved_sha256=approval.model.artifact_sha256 if approval else None,
        )
    )
    checks.append(
        _run_root_check(run_root, project_root=project_root.absolute(), dataset_root=dataset_root.absolute())
    )

    static_diagnostic_checks_passed = all(item["ok"] for item in checks)
    checks.extend(
        [
            _check(
                "pilot.trusted_authorization_verified",
                False,
                "Readiness 0 accepts only self-asserted unsigned records; trusted authorization is not implemented",
            ),
            _check(
                "pilot.runtime_health_verified",
                False,
                "a regular executable path does not prove Python, dependency lock, pip check, decoder, or device health",
            ),
            _check(
                "pilot.atomic_launch_handoff_available",
                False,
                "read-only preflight cannot reserve the run root or bind checked descriptors to a launcher",
            ),
        ]
    )
    return {
        "schema_version": READINESS_SCHEMA,
        "status": "blocked",
        "static_diagnostic_checks_passed": static_diagnostic_checks_passed,
        "pilot_launch_eligible": False,
        "evaluated_at": current.isoformat(),
        "checks": checks,
        "dataset_audit": dataset_audit,
        "approval_error": approval_error,
        "truth_flags": {
            "training_started": False,
            "subprocess_started": False,
            "network_accessed": False,
            "weights_downloaded": False,
            "weights_downloaded_by_preflight": False,
            "weight_artifact_supplied": weight_artifact is not None,
            "derived_data_generated": False,
            "files_written": False,
            "route_status": "pending",
            "self_asserted_route_status": self_asserted_route_status,
            "formal_metric_available": False,
            "formal_dataset_adopted": False,
            "authorization_authenticity": approval.authorization_authenticity if approval else "missing",
            "authorization_cryptographically_verified": False,
            "runtime_health_verified": False,
            "atomic_launch_handoff_available": False,
            "compliance_claim_eligible": False,
        },
    }


__all__ = [
    "APPROVAL_SCHEMA",
    "CONSTRUCTION_PPE_PROFILE",
    "DatasetProfile",
    "PilotApproval",
    "audit_dataset",
    "load_pilot_approval",
    "preflight_pilot",
]
