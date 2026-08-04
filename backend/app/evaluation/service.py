from __future__ import annotations

import hashlib
import stat
from collections import Counter
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from .errors import ContractError, IntegrityError
from .jsonio import (
    MAX_JSON_BYTES,
    MAX_JSONL_BYTES,
    FileSnapshot,
    open_relative_regular_file,
    parse_json_model_snapshot,
    parse_jsonl_models_snapshot,
    snapshot_file,
    snapshot_relative_file,
)
from .metrics import score_single_label
from .schemas import (
    ArtifactDescriptor,
    EvaluationCase,
    EvaluationDatasetManifest,
    EvaluationLabel,
    EvaluationModelStatement,
    EvaluationPrediction,
    SplitName,
)


MOCK_SOURCE_ORIGINS = frozenset({"mock", "demo_fixture", "synthetic_placeholder"})
UNSUPPORTED_FORMAL_SOURCE_ORIGINS = frozenset({"authorized_simulation", "sample_scenario"})
NON_FORMAL_IMPLEMENTATIONS = frozenset({"stub", "fixture", "placeholder"})
NON_FORMAL_ADAPTER_NAMES = frozenset({"stub", "demo_fixture"})
FORMAL_SCORING_SPLITS = frozenset({"gate_holdout", "final_holdout"})
FORMAL_REQUIRED_GROUP_KEYS = frozenset(
    {
        "source_lineage_id",
        "capture_session_id",
        "engineering_entity_id",
        "site_group_id",
        "camera_group_id",
        "person_group_id",
    }
)
ASSURANCE_LIMITATIONS = (
    "media_decode_unverified",
    "single_person_crop_provenance_unverified",
    "model_artifact_unverified",
    "blind_isolation_unverified",
    "legal_authorization_unverified",
    "one_shot_holdout_unverified",
    "training_overlap_unverified",
)


@dataclass(frozen=True)
class LoadedDataset:
    manifest_path: Path
    manifest_snapshot: FileSnapshot
    cases_snapshot: FileSnapshot
    labels_snapshot: FileSnapshot
    manifest: EvaluationDatasetManifest
    cases: list[EvaluationCase]
    labels: list[EvaluationLabel]


class _UnionFind:
    def __init__(self, size: int) -> None:
        self.parent = list(range(size))
        self.rank = [0] * size

    def find(self, item: int) -> int:
        while self.parent[item] != item:
            self.parent[item] = self.parent[self.parent[item]]
            item = self.parent[item]
        return item

    def union(self, left: int, right: int) -> None:
        left_root = self.find(left)
        right_root = self.find(right)
        if left_root == right_root:
            return
        if self.rank[left_root] < self.rank[right_root]:
            left_root, right_root = right_root, left_root
        self.parent[right_root] = left_root
        if self.rank[left_root] == self.rank[right_root]:
            self.rank[left_root] += 1


def _verify_expected_digest(
    expected: str | None,
    actual: str,
    *,
    required: bool,
    label: str,
    mismatch_code: str,
) -> None:
    if expected is None:
        if required:
            raise ContractError(
                "EVAL_EXPECTED_DIGEST_REQUIRED",
                f"Formal scoring requires an external expected_{label}_sha256",
                details={"field": f"expected_{label}_sha256"},
            )
        return
    if (
        len(expected) != 64
        or expected != expected.lower()
        or any(character not in "0123456789abcdef" for character in expected)
    ):
        raise ContractError(
            "EVAL_EXPECTED_DIGEST_INVALID",
            f"expected_{label}_sha256 must be 64 lowercase hexadecimal characters",
            details={"field": f"expected_{label}_sha256"},
        )
    if expected != actual:
        raise IntegrityError(
            mismatch_code,
            f"External expected {label} SHA-256 does not match the supplied file",
            details={"expected_sha256": expected, "actual_sha256": actual},
        )


def _safe_relative_path(root: Path, raw_path: str, *, code: str = "EVAL_ASSET_PATH_UNSAFE") -> Path:
    try:
        if "\x00" in raw_path:
            raise ValueError("NUL is forbidden in paths")
        pure = PurePosixPath(raw_path)
        if (
            pure.is_absolute()
            or "\\" in raw_path
            or pure.as_posix() != raw_path
            or any(part in {"", ".", ".."} for part in pure.parts)
        ):
            raise ValueError("path is not normalized relative POSIX syntax")
        root_resolved = root.resolve()
        candidate = root_resolved.joinpath(*pure.parts)
        current = root_resolved
        for index, part in enumerate(pure.parts):
            current = current / part
            try:
                metadata = current.lstat()
            except FileNotFoundError:
                break
            if stat.S_ISLNK(metadata.st_mode):
                raise ValueError("symbolic-link path components are forbidden")
            if index < len(pure.parts) - 1 and not stat.S_ISDIR(metadata.st_mode):
                raise ValueError("intermediate path component is not a directory")
    except (OSError, RuntimeError, ValueError) as exc:
        raise IntegrityError(code, f"Unsafe or unresolvable path: {exc}", path=raw_path) from exc
    if candidate != root_resolved and root_resolved not in candidate.parents:
        raise IntegrityError(code, "Path escapes the dataset root", path=raw_path)
    return candidate


def _snapshot_descriptor(root: Path, descriptor: ArtifactDescriptor, *, label: str) -> FileSnapshot:
    _safe_relative_path(root, descriptor.path, code="EVAL_ARTIFACT_PATH_UNSAFE")
    try:
        snapshot = snapshot_relative_file(root, descriptor.path, max_bytes=MAX_JSONL_BYTES)
    except ContractError as exc:
        if exc.code in {
            "EVAL_FILE_READ_FAILED",
            "EVAL_FILE_NOT_REGULAR",
            "EVAL_FILE_IDENTITY_CHANGED",
            "EVAL_FILE_PATH_UNSAFE",
            "EVAL_SECURE_OPEN_UNAVAILABLE",
        }:
            raise IntegrityError(
                "EVAL_DATASET_ARTIFACT_UNAVAILABLE",
                f"Frozen {label} artifact is unavailable or not a stable regular file",
                path=descriptor.path,
                details={"cause": exc.code},
            ) from exc
        raise
    if snapshot.size_bytes != descriptor.size_bytes:
        raise IntegrityError(
            "EVAL_DATASET_DIGEST_MISMATCH",
            f"{label} size differs from the frozen manifest",
            path=descriptor.path,
            details={"expected_size": descriptor.size_bytes, "actual_size": snapshot.size_bytes},
        )
    if snapshot.sha256 != descriptor.sha256:
        raise IntegrityError(
            "EVAL_DATASET_DIGEST_MISMATCH",
            f"{label} SHA-256 differs from the frozen manifest",
            path=descriptor.path,
            details={"expected_sha256": descriptor.sha256, "actual_sha256": snapshot.sha256},
        )
    return snapshot


def _split_assignment_sha256(cases: list[EvaluationCase]) -> str:
    body = "".join(f"{case.case_id}\t{case.split}\n" for case in sorted(cases, key=lambda item: item.case_id))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _validate_case_and_label_sets(
    manifest: EvaluationDatasetManifest,
    cases: list[EvaluationCase],
    labels: list[EvaluationLabel],
) -> None:
    if any(case.task_type != manifest.task.type for case in cases):
        raise ContractError("EVAL_TASK_MISMATCH", "Every case task_type must match the dataset manifest")
    event_window_owner: dict[tuple[str, int, int], str] = {}
    for case in cases:
        primary = next(item for item in case.inputs if item.role == "primary_media")
        # EvaluationCase already requires an event-window segment.
        if primary.segment is None:  # pragma: no cover - defensive against future schema drift
            raise ContractError("EVAL_SCHEMA_INVALID", "Event-window case is missing its segment")
        event_key = (primary.sha256, primary.segment.start_ms, primary.segment.end_ms)
        previous_case_id = event_window_owner.get(event_key)
        if previous_case_id is not None:
            raise ContractError(
                "EVAL_EVENT_WINDOW_DUPLICATE",
                "The same media bytes and temporal segment may contribute only one scoring case",
                details={
                    "first_case_id": previous_case_id,
                    "second_case_id": case.case_id,
                    "media_sha256": primary.sha256,
                    "start_ms": primary.segment.start_ms,
                    "end_ms": primary.segment.end_ms,
                },
            )
        event_window_owner[event_key] = case.case_id
    case_ids = {case.case_id for case in cases}
    label_ids = {label.case_id for label in labels}
    if label_ids != case_ids:
        raise ContractError(
            "EVAL_LABEL_SET_MISMATCH",
            "labels.private.jsonl must contain exactly one label for every case",
            details={
                "missing_label_case_ids": sorted(case_ids - label_ids)[:20],
                "extra_label_case_ids": sorted(label_ids - case_ids)[:20],
            },
        )

    class_ids = {item.id for item in manifest.task.classes}
    for label in labels:
        if label.annotation.spec_version != manifest.task.label_spec_version:
            raise ContractError(
                "EVAL_LABEL_SPEC_MISMATCH",
                "Label annotation spec_version must match the frozen manifest",
                details={
                    "case_id": label.case_id,
                    "expected": manifest.task.label_spec_version,
                    "actual": label.annotation.spec_version,
                },
            )
        if label.truth.label not in class_ids:
            raise ContractError(
                "EVAL_LABEL_UNKNOWN_CLASS",
                f"Ground-truth label is not declared in the manifest: {label.truth.label}",
                details={"case_id": label.case_id},
            )

    expected_counts = {item.name: item.case_count for item in manifest.splits}
    actual_counts = Counter(case.split for case in cases)
    if set(expected_counts) != set(actual_counts) or any(
        expected_counts[name] != actual_counts[name] for name in expected_counts
    ):
        raise ContractError(
            "EVAL_CASE_COUNT_MISMATCH",
            "Manifest split counts do not match cases.jsonl",
            details={"expected": expected_counts, "actual": dict(actual_counts)},
        )
    assignment = _split_assignment_sha256(cases)
    if assignment != manifest.split_policy.assignment_sha256:
        raise IntegrityError(
            "EVAL_SPLIT_ASSIGNMENT_MISMATCH",
            "Frozen split assignment digest does not match cases.jsonl",
            details={"expected": manifest.split_policy.assignment_sha256, "actual": assignment},
        )

    source_ids = {source.source_id for source in manifest.sources}
    undeclared = sorted({case.source_id for case in cases} - source_ids)
    if undeclared:
        raise ContractError(
            "EVAL_SOURCE_DECLARATION_MISSING",
            "One or more cases refer to undeclared sources",
            details={"source_ids": undeclared},
        )


def _validate_assets(root: Path, cases: list[EvaluationCase]) -> None:
    verified: dict[tuple[str, str], tuple[int, str]] = {}
    identity_by_asset_id: dict[str, tuple[str, str, int, str, str]] = {}
    for case in cases:
        for asset in case.inputs:
            identity = (asset.relative_path, asset.sha256, asset.size_bytes, asset.content_type, case.case_id)
            previous = identity_by_asset_id.get(asset.asset_id)
            if previous is not None and previous[:4] != identity[:4]:
                raise IntegrityError(
                    "EVAL_ASSET_IDENTITY_MISMATCH",
                    "The same asset_id must bind one path, SHA-256, size, and content type across the dataset",
                    details={
                        "asset_id": asset.asset_id,
                        "first_case_id": previous[4],
                        "second_case_id": case.case_id,
                    },
                )
            identity_by_asset_id.setdefault(asset.asset_id, identity)
            _safe_relative_path(root, asset.relative_path)
            key = (asset.relative_path, asset.sha256)
            if key not in verified:
                try:
                    with open_relative_regular_file(root, asset.relative_path) as (handle, metadata, _):
                        size = metadata.st_size
                        hasher = hashlib.sha256()
                        for block in iter(lambda: handle.read(1024 * 1024), b""):
                            hasher.update(block)
                        digest = hasher.hexdigest()
                except ContractError as exc:
                    raise IntegrityError(
                        "EVAL_ASSET_UNAVAILABLE",
                        "Case asset is unavailable or is not a stable root-confined regular file",
                        path=asset.relative_path,
                        details={"cause": exc.code},
                    ) from exc
                verified[key] = (size, digest)
            size, digest = verified[key]
            if size != asset.size_bytes:
                raise IntegrityError(
                    "EVAL_ASSET_SIZE_MISMATCH",
                    "Case asset size differs from its declaration",
                    path=asset.relative_path,
                    details={"case_id": case.case_id, "expected_size": asset.size_bytes, "actual_size": size},
                )
            if digest != asset.sha256:
                raise IntegrityError(
                    "EVAL_ASSET_HASH_MISMATCH",
                    "Case asset SHA-256 differs from its declaration",
                    path=asset.relative_path,
                    details={"case_id": case.case_id, "expected_sha256": asset.sha256, "actual_sha256": digest},
                )


def _validate_split_isolation(manifest: EvaluationDatasetManifest, cases: list[EvaluationCase]) -> None:
    union = _UnionFind(len(cases))
    for group_key in manifest.split_policy.group_keys:
        first_by_value: dict[str, int] = {}
        for index, case in enumerate(cases):
            value = getattr(case.groups, group_key)
            if value is None:
                if group_key == "project_group_id":
                    raise ContractError(
                        "EVAL_GROUP_VALUE_MISSING",
                        "project_group_id is required on every case when project grouping is enabled",
                        details={"case_id": case.case_id},
                    )
                continue
            if value in first_by_value:
                union.union(first_by_value[value], index)
            else:
                first_by_value[value] = index

    component_members: dict[int, list[int]] = {}
    for index in range(len(cases)):
        component_members.setdefault(union.find(index), []).append(index)
    for members in component_members.values():
        splits = {cases[index].split for index in members}
        if len(splits) > 1:
            case_ids = [cases[index].case_id for index in members]
            raise IntegrityError(
                "EVAL_GROUP_LEAKAGE",
                "A transitive group component crosses dataset splits",
                details={"splits": sorted(splits), "case_ids": sorted(case_ids)[:50]},
            )

    digest_owner: dict[str, tuple[str, str]] = {}
    for case in cases:
        for asset in case.inputs:
            previous = digest_owner.get(asset.sha256)
            if previous is not None and previous[0] != case.split:
                raise IntegrityError(
                    "EVAL_EXACT_ASSET_LEAKAGE",
                    "The same asset SHA-256 appears in different splits",
                    details={
                        "sha256": asset.sha256,
                        "first_split": previous[0],
                        "first_case_id": previous[1],
                        "second_split": case.split,
                        "second_case_id": case.case_id,
                    },
                )
            digest_owner.setdefault(asset.sha256, (case.split, case.case_id))


def _validate_formal_dataset(manifest: EvaluationDatasetManifest) -> None:
    if not manifest.formal_policy.formal_eligible:
        raise ContractError("EVAL_DATASET_NOT_FORMAL", "Dataset is not marked formal_eligible")
    if manifest.status != "frozen":
        raise ContractError("EVAL_DATASET_NOT_FROZEN", "Formal evaluation requires a frozen dataset")
    if (
        manifest.formal_policy.mock_allowed
        or manifest.formal_policy.fixture_allowed
        or manifest.formal_policy.synthetic_placeholder_allowed
    ):
        raise ContractError("EVAL_SYNTHETIC_FIXTURE_FORBIDDEN", "Formal policy must reject mock and fixture data")
    missing_group_keys = sorted(FORMAL_REQUIRED_GROUP_KEYS - set(manifest.split_policy.group_keys))
    if missing_group_keys:
        raise ContractError(
            "EVAL_FORMAL_GROUP_KEY_MISSING",
            "Formal Evaluation v0 requires site, camera, person, lineage, capture, and engineering grouping",
            details={"group_keys": missing_group_keys},
        )
    for source in manifest.sources:
        if source.origin in MOCK_SOURCE_ORIGINS:
            raise ContractError(
                "EVAL_SYNTHETIC_FIXTURE_FORBIDDEN",
                f"Formal evaluation cannot use source origin {source.origin}",
                details={"source_id": source.source_id},
            )
        if source.origin in UNSUPPORTED_FORMAL_SOURCE_ORIGINS:
            raise ContractError(
                "EVAL_SOURCE_SCOPE_UNSUPPORTED",
                "Formal Evaluation v0 cannot use simulation/sample sources until claim_scope is implemented",
                details={"source_id": source.source_id, "origin": source.origin},
            )
        if "evaluation" not in source.allowed_uses:
            raise ContractError(
                "EVAL_SOURCE_USE_FORBIDDEN",
                "Formal evaluation requires evaluation in every source allowed_uses declaration",
                details={"source_id": source.source_id},
            )


def _validate_formal_model(statement: EvaluationModelStatement) -> None:
    normalized_adapter_name = "".join(
        character for character in statement.adapter_name.casefold() if character.isalnum()
    )
    if (
        statement.implementation_kind in NON_FORMAL_IMPLEMENTATIONS
        or statement.adapter_name.casefold() in NON_FORMAL_ADAPTER_NAMES
        or any(
            forbidden in normalized_adapter_name
            for forbidden in ("stub", "fixture", "placeholder", "demofixture")
        )
        or statement.synthetic
    ):
        raise ContractError(
            "EVAL_ADAPTER_NOT_FORMAL",
            "Formal evaluation rejects stub, fixture, placeholder, and synthetic model declarations",
            details={
                "adapter_name": statement.adapter_name,
                "implementation_kind": statement.implementation_kind,
                "synthetic": statement.synthetic,
            },
        )


def _validate_dataset_snapshot(manifest_snapshot: FileSnapshot, *, formal: bool) -> LoadedDataset:
    manifest = parse_json_model_snapshot(manifest_snapshot, EvaluationDatasetManifest)
    root = manifest_snapshot.path.parent
    cases_snapshot = _snapshot_descriptor(root, manifest.artifacts.cases, label="cases")
    labels_snapshot = _snapshot_descriptor(root, manifest.artifacts.labels_private, label="labels_private")
    cases = parse_jsonl_models_snapshot(
        cases_snapshot,
        EvaluationCase,
        record_kind="case",
        unique_key=lambda item: item.case_id,
    )
    labels = parse_jsonl_models_snapshot(
        labels_snapshot,
        EvaluationLabel,
        record_kind="label",
        unique_key=lambda item: item.case_id,
    )
    if len(cases) != manifest.artifacts.cases.line_count:
        raise ContractError(
            "EVAL_CASE_COUNT_MISMATCH",
            "cases line_count does not match the manifest",
            details={"expected": manifest.artifacts.cases.line_count, "actual": len(cases)},
        )
    if len(labels) != manifest.artifacts.labels_private.line_count:
        raise ContractError(
            "EVAL_LABEL_SET_MISMATCH",
            "labels line_count does not match the manifest",
            details={"expected": manifest.artifacts.labels_private.line_count, "actual": len(labels)},
        )
    _validate_case_and_label_sets(manifest, cases, labels)
    _validate_assets(root, cases)
    _validate_split_isolation(manifest, cases)
    if formal:
        _validate_formal_dataset(manifest)
    return LoadedDataset(
        manifest_path=manifest_snapshot.path,
        manifest_snapshot=manifest_snapshot,
        cases_snapshot=cases_snapshot,
        labels_snapshot=labels_snapshot,
        manifest=manifest,
        cases=cases,
        labels=labels,
    )


def validate_dataset(manifest_path: Path | str, *, formal: bool = False) -> LoadedDataset:
    manifest_snapshot = snapshot_file(Path(manifest_path), max_bytes=MAX_JSON_BYTES)
    return _validate_dataset_snapshot(manifest_snapshot, formal=formal)


def score_dataset(
    manifest_path: Path | str,
    predictions_path: Path | str,
    model_statement_path: Path | str,
    *,
    split: SplitName,
    formal: bool = False,
    expected_manifest_sha256: str | None = None,
    expected_model_statement_sha256: str | None = None,
) -> dict[str, Any]:
    if formal and split not in FORMAL_SCORING_SPLITS:
        raise ContractError(
            "EVAL_FORMAL_SPLIT_FORBIDDEN",
            "Formal scoring is limited to gate_holdout and final_holdout",
            details={"split": split},
        )
    manifest_path = Path(manifest_path)
    model_statement_path = Path(model_statement_path)
    manifest_snapshot = snapshot_file(manifest_path, max_bytes=MAX_JSON_BYTES)
    model_statement_snapshot = snapshot_file(model_statement_path, max_bytes=MAX_JSON_BYTES)
    actual_manifest_sha256 = manifest_snapshot.sha256
    actual_model_statement_sha256 = model_statement_snapshot.sha256
    _verify_expected_digest(
        expected_manifest_sha256,
        actual_manifest_sha256,
        required=formal,
        label="manifest",
        mismatch_code="EVAL_MANIFEST_IDENTITY_MISMATCH",
    )
    _verify_expected_digest(
        expected_model_statement_sha256,
        actual_model_statement_sha256,
        required=formal,
        label="model_statement",
        mismatch_code="EVAL_MODEL_STATEMENT_IDENTITY_MISMATCH",
    )
    dataset = _validate_dataset_snapshot(manifest_snapshot, formal=formal)
    statement = parse_json_model_snapshot(model_statement_snapshot, EvaluationModelStatement)
    if formal:
        _validate_formal_model(statement)
    predictions_snapshot = snapshot_file(Path(predictions_path), max_bytes=MAX_JSONL_BYTES)
    predictions = parse_jsonl_models_snapshot(
        predictions_snapshot,
        EvaluationPrediction,
        record_kind="prediction",
        unique_key=lambda item: item.case_id,
        protect_predictions=True,
    )

    class_order = [item.id for item in dataset.manifest.task.classes]
    class_ids = set(class_order)
    for prediction in predictions:
        if prediction.output.label not in class_ids:
            raise ContractError(
                "EVAL_PREDICTION_UNKNOWN_CLASS",
                f"Prediction class is not declared in the manifest: {prediction.output.label}",
                details={"case_id": prediction.case_id},
            )
        if prediction.output.scores is not None:
            unknown_scores = sorted(set(prediction.output.scores) - class_ids)
            if unknown_scores:
                raise ContractError(
                    "EVAL_PREDICTION_UNKNOWN_CLASS",
                    "Prediction scores include undeclared classes",
                    details={"case_id": prediction.case_id, "class_ids": unknown_scores},
                )

    target_cases = [case for case in dataset.cases if case.split == split]
    if not target_cases:
        raise ContractError("EVAL_EMPTY_EVALUATION_SET", f"Split {split} has no cases")
    target_ids = {case.case_id for case in target_cases}
    prediction_ids = {prediction.case_id for prediction in predictions}
    missing = sorted(target_ids - prediction_ids)
    extra = sorted(prediction_ids - target_ids)
    if missing:
        raise ContractError(
            "EVAL_PREDICTION_MISSING",
            "Predictions must cover every target case; the metric denominator is never reduced",
            details={"case_ids": missing[:50], "count": len(missing)},
        )
    if extra:
        raise ContractError(
            "EVAL_PREDICTION_EXTRA",
            "Predictions contain cases outside the target split",
            details={"case_ids": extra[:50], "count": len(extra)},
        )

    label_by_id = {label.case_id: label.truth.label for label in dataset.labels}
    prediction_by_id = {prediction.case_id: prediction.output.label for prediction in predictions}
    support = Counter(label_by_id[case.case_id] for case in target_cases)
    zero_support = [class_id for class_id in class_order if support[class_id] == 0]
    if zero_support:
        raise ContractError(
            "EVAL_CLASS_SUPPORT_ZERO",
            "Every declared class must have non-zero support in the scored split",
            details={"class_ids": zero_support, "split": split},
        )
    minimum_total = dataset.manifest.task.minimum_cases_total
    if len(target_cases) < minimum_total:
        raise ContractError(
            "EVAL_SAMPLE_MINIMUM_NOT_MET",
            "Target split does not meet minimum_cases_total",
            details={"required": minimum_total, "actual": len(target_cases)},
        )
    deficient = {
        class_id: {"required": dataset.manifest.task.minimum_cases_per_class[class_id], "actual": support[class_id]}
        for class_id in class_order
        if support[class_id] < dataset.manifest.task.minimum_cases_per_class[class_id]
    }
    if deficient:
        raise ContractError(
            "EVAL_SAMPLE_MINIMUM_NOT_MET",
            "Target split does not meet minimum_cases_per_class",
            details={"classes": deficient},
        )

    ordered_cases = sorted(target_cases, key=lambda item: item.case_id)
    metrics = score_single_label(
        [label_by_id[case.case_id] for case in ordered_cases],
        [prediction_by_id[case.case_id] for case in ordered_cases],
        class_order,
        threshold=dataset.manifest.task.acceptance.threshold,
        ci_policy=dataset.manifest.task.acceptance.ci_policy,
    )
    threshold_status = "passed" if metrics["threshold"]["passed"] else "failed"
    threshold_reasons: list[str] = []
    if threshold_status == "failed":
        if not metrics["threshold"]["point_passed"]:
            threshold_reasons.append("EVAL_THRESHOLD_NOT_MET")
        elif not metrics["threshold"]["ci_lower_passed"]:
            threshold_reasons.append("EVAL_CI_GATE_NOT_MET")
    return {
        "schema_version": "evaluation.score.v0",
        "ok": True,
        "dataset": {
            "dataset_id": dataset.manifest.dataset_id,
            "version": dataset.manifest.version,
            "status": dataset.manifest.status,
            "manifest_sha256": actual_manifest_sha256,
            "manifest_size_bytes": dataset.manifest_snapshot.size_bytes,
            "cases_sha256": dataset.cases_snapshot.sha256,
            "cases_size_bytes": dataset.cases_snapshot.size_bytes,
            "labels_private_sha256": dataset.labels_snapshot.sha256,
            "labels_private_size_bytes": dataset.labels_snapshot.size_bytes,
            "split_assignment_sha256": dataset.manifest.split_policy.assignment_sha256,
            "metric_spec_sha256": dataset.manifest.task.metric_spec_sha256,
        },
        "model": {
            "adapter_name": statement.adapter_name,
            "adapter_version": statement.adapter_version,
            "model_name": statement.model_name,
            "model_version": statement.model_version,
            "artifact_sha256": statement.artifact_sha256,
            "statement_sha256": actual_model_statement_sha256,
            "statement_size_bytes": model_statement_snapshot.size_bytes,
        },
        "predictions_sha256": predictions_snapshot.sha256,
        "predictions_size_bytes": predictions_snapshot.size_bytes,
        "split": split,
        "formal_requested": formal,
        "gate_status": "not_eligible",
        "compliance_claim_eligible": False,
        "structural_gate_status": "passed",
        "threshold_status": threshold_status,
        "threshold_reasons": threshold_reasons,
        "assurance_limitations": list(ASSURANCE_LIMITATIONS),
        "metrics": metrics,
    }


__all__ = ["LoadedDataset", "score_dataset", "validate_dataset"]
