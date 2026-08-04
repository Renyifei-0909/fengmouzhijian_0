from __future__ import annotations

import hashlib
import json
import os
import ctypes
import errno
import re
import shutil
import stat
import tempfile
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

try:  # The evaluator can still import and report a structured error off POSIX.
    import fcntl
except ImportError:  # pragma: no cover - exercised only on non-POSIX Python
    fcntl = None  # type: ignore[assignment]

from pydantic import ValidationError

from .bundle_schemas import (
    EXPECTED_BUNDLE_MEMBERS,
    DevelopmentEvidenceManifest,
    DevelopmentPublicScore,
    DevelopmentRunSummary,
    EvidenceMember,
)
from .errors import ContractError, EvaluationError, ExecutionError, IntegrityError
from .jsonio import (
    FileSnapshot,
    MAX_JSON_BYTES,
    MAX_JSONL_BYTES,
    parse_json_model_snapshot,
    parse_json_object,
    parse_jsonl_models_snapshot,
    snapshot_file,
)
from .run_schemas import DevelopmentRunPlan
from .schemas import EvaluationPrediction


MANIFEST_PATH = "bundle-manifest.json"
EXPECTED_ROOT_ENTRIES = frozenset({MANIFEST_PATH, "inputs", "public", "results"})
EXPECTED_DIRECTORY_ENTRIES = {
    "inputs": frozenset({"run-plan.json"}),
    "public": frozenset({"predictions.jsonl"}),
    "results": frozenset({"run-summary.json", "score.json"}),
}
MAX_BUNDLE_JSON_BYTES = 2 * 1024 * 1024
_HEX_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_ALLOWED_PRIVATE_KEYS = frozenset(
    {
        "privatelabelrecordsincluded",
        "aggregatemetricsderivedfromprivatelabels",
        "scorereplay",
    }
)
_FORBIDDEN_PRIVATE_KEYS = frozenset(
    {
        "labelsprivate",
        "privatelabel",
        "privatelabels",
        "privatelabelpath",
        "privatelabelsha256",
        "privatelabelsize",
        "privatelabelsizebytes",
        "groundtruth",
        "groundtruths",
        "annotation",
        "annotations",
        "truthrecord",
        "truthrecords",
    }
)
_ALLOWED_SENSITIVE_VALUES = frozenset(
    {
        "unavailable_without_private_labels",
        "public_score_replay_unavailable_without_private_labels",
    }
)


@dataclass(frozen=True)
class DevelopmentEvidenceReceipt:
    manifest_sha256: str
    manifest_size_bytes: int
    member_set_sha256: str
    member_count: int

    def as_dict(self) -> dict[str, int | str]:
        return {
            "manifest_sha256": self.manifest_sha256,
            "manifest_size_bytes": self.manifest_size_bytes,
            "member_set_sha256": self.member_set_sha256,
            "member_count": self.member_count,
        }


def _canonical_json(value: Any) -> bytes:
    try:
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
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        raise ContractError(
            "EVAL_DEV_BUNDLE_JSON_INVALID",
            "Development evidence contains a value that cannot be serialized as strict JSON",
        ) from exc


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _member_set_sha256(members: list[EvidenceMember]) -> str:
    canonical = json.dumps(
        [member.model_dump(mode="json") for member in members],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return _sha256(canonical)


def case_id_roster_sha256(case_ids: list[str]) -> str:
    if not case_ids or len(case_ids) != len(set(case_ids)):
        raise ContractError(
            "EVAL_DEV_BUNDLE_CASE_ROSTER_INVALID",
            "Case-id roster must be non-empty and unique",
        )
    canonical = json.dumps(
        sorted(case_ids),
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return _sha256(canonical)


def _validated_model(model_type: type, payload: dict[str, Any], *, label: str):
    try:
        return model_type.model_validate(payload)
    except ValidationError as exc:
        raise ContractError(
            "EVAL_DEV_BUNDLE_SOURCE_INVALID",
            f"Cannot construct {label} from the development run: {str(exc)[:2000]}",
        ) from exc


def _ensure_mapping(value: Any, *, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ContractError(
            "EVAL_DEV_BUNDLE_SOURCE_INVALID",
            f"Development run {label} must be a JSON object",
        )
    return value


def _public_score(run_result: Mapping[str, Any]) -> DevelopmentPublicScore:
    score = _ensure_mapping(run_result.get("score"), label="score")
    score_boundaries = {
        "schema_version": "evaluation.score.v0",
        "ok": True,
        "formal_requested": False,
        "gate_status": "not_eligible",
        "compliance_claim_eligible": False,
    }
    inconsistent = [key for key, expected in score_boundaries.items() if score.get(key) != expected]
    if inconsistent:
        raise IntegrityError(
            "EVAL_DEV_BUNDLE_SOURCE_BINDING_MISMATCH",
            "Internal score does not preserve the development-only truth boundary",
            details={"fields": [f"score.{key}" for key in inconsistent]},
        )
    dataset = _ensure_mapping(score.get("dataset"), label="score.dataset")
    model = _ensure_mapping(score.get("model"), label="score.model")
    payload = {
        "schema_version": "evaluation.development-public-score.v0",
        "source_schema_version": "evaluation.score.v0",
        "ok": score.get("ok"),
        "dataset": {
            key: dataset.get(key)
            for key in (
                "dataset_id",
                "version",
                "status",
                "manifest_sha256",
                "manifest_size_bytes",
                "cases_sha256",
                "cases_size_bytes",
                "split_assignment_sha256",
                "metric_spec_sha256",
            )
        },
        "model": {
            key: model.get(key)
            for key in (
                "adapter_name",
                "adapter_version",
                "model_name",
                "model_version",
                "artifact_sha256",
                "statement_sha256",
                "statement_size_bytes",
            )
        },
        "predictions_sha256": score.get("predictions_sha256"),
        "predictions_size_bytes": score.get("predictions_size_bytes"),
        "split": score.get("split"),
        "formal_requested": False,
        "gate_status": "not_eligible",
        "compliance_claim_eligible": False,
        "private_label_records_included": False,
        "offline_rescore_supported": False,
        "score_recomputed": False,
        "structural_gate_status": score.get("structural_gate_status"),
        "threshold_status": score.get("threshold_status"),
        "threshold_reasons": score.get("threshold_reasons"),
        "assurance_limitations": score.get("assurance_limitations"),
        "metrics": score.get("metrics"),
    }
    return _validated_model(DevelopmentPublicScore, payload, label="public score")


def _run_summary(
    run_result: Mapping[str, Any],
    plan: DevelopmentRunPlan,
    *,
    run_plan_snapshot: FileSnapshot,
    predictions_snapshot: FileSnapshot,
    public_score_sha256: str,
    public_score_size_bytes: int,
) -> DevelopmentRunSummary:
    process = _ensure_mapping(run_result.get("process"), label="process")
    inference = _ensure_mapping(run_result.get("inference_view"), label="inference_view")
    runtime = _ensure_mapping(run_result.get("runtime"), label="runtime")
    payload = {
        "schema_version": "evaluation.development-run-summary.v0",
        "source_schema_version": "evaluation.development-run.v0",
        "ok": run_result.get("ok"),
        "run_id": run_result.get("run_id"),
        "mode": run_result.get("mode"),
        "runner": run_result.get("runner"),
        "protocol": run_result.get("protocol"),
        "split": run_result.get("split"),
        "formal_requested": False,
        "gate_status": "not_eligible",
        "compliance_claim_eligible": False,
        "run_plan_sha256": run_plan_snapshot.sha256,
        "run_plan_size_bytes": run_plan_snapshot.size_bytes,
        "evaluator_source_sha256": run_result.get("evaluator_source_sha256"),
        "training_data_manifest_sha256": run_result.get("training_data_manifest_sha256"),
        "dataset_manifest_sha256": plan.dataset_manifest.sha256,
        "model_statement_sha256": plan.model_statement.sha256,
        "model_artifact_sha256": plan.model_artifact.sha256,
        "predictions_sha256": predictions_snapshot.sha256,
        "predictions_size_bytes": predictions_snapshot.size_bytes,
        "public_score_sha256": public_score_sha256,
        "public_score_size_bytes": public_score_size_bytes,
        "runtime": {
            key: runtime.get(key)
            for key in ("python_version", "implementation", "platform", "environment_keys")
        },
        "process_return_code": process.get("return_code"),
        "process_duration_ms": process.get("duration_ms"),
        "inference_case_count": inference.get("case_count"),
        "public_cases_sha256": inference.get("public_cases_sha256"),
        "case_id_roster_sha256": inference.get("case_id_roster_sha256"),
        "inference_asset_count": inference.get("asset_count"),
        "inference_asset_size_bytes": inference.get("asset_size_bytes"),
        "private_label_records_included": False,
        "raw_logs_included": False,
        "offline_rescore_supported": False,
        "score_recomputed": False,
        "assurance_limitations": run_result.get("assurance_limitations"),
    }
    return _validated_model(DevelopmentRunSummary, payload, label="run summary")


def _validate_source_bindings(
    run_result: Mapping[str, Any],
    plan: DevelopmentRunPlan,
    run_plan_snapshot: FileSnapshot,
    predictions_snapshot: FileSnapshot,
    public_score: DevelopmentPublicScore,
) -> None:
    mismatches: list[str] = []
    checks = (
        (run_result.get("run_id"), plan.run_id, "run_id"),
        (run_result.get("schema_version"), "evaluation.development-run.v0", "schema_version"),
        (run_result.get("mode"), "development", "mode"),
        (run_result.get("runner"), "local_process", "runner"),
        (run_result.get("protocol"), "evaluation.predictor-cli.v0", "protocol"),
        (run_result.get("split"), plan.split, "split"),
        (run_result.get("formal_requested"), False, "formal_requested"),
        (run_result.get("gate_status"), "not_eligible", "gate_status"),
        (run_result.get("compliance_claim_eligible"), False, "compliance_claim_eligible"),
        (run_result.get("run_plan_sha256"), run_plan_snapshot.sha256, "run_plan_sha256"),
        (run_result.get("evaluator_source_sha256"), plan.evaluator_source_sha256, "evaluator_source_sha256"),
        (
            run_result.get("training_data_manifest_sha256"),
            plan.training_data_manifest.sha256,
            "training_data_manifest_sha256",
        ),
        (run_result.get("predictions_sha256"), predictions_snapshot.sha256, "predictions_sha256"),
        (run_result.get("predictions_size_bytes"), predictions_snapshot.size_bytes, "predictions_size_bytes"),
        (public_score.split, plan.split, "score.split"),
        (public_score.predictions_sha256, predictions_snapshot.sha256, "score.predictions_sha256"),
        (public_score.predictions_size_bytes, predictions_snapshot.size_bytes, "score.predictions_size_bytes"),
        (public_score.dataset.manifest_sha256, plan.dataset_manifest.sha256, "score.dataset.manifest_sha256"),
        (public_score.dataset.manifest_size_bytes, plan.dataset_manifest.size_bytes, "score.dataset.manifest_size_bytes"),
        (public_score.model.statement_sha256, plan.model_statement.sha256, "score.model.statement_sha256"),
        (public_score.model.statement_size_bytes, plan.model_statement.size_bytes, "score.model.statement_size_bytes"),
        (public_score.model.artifact_sha256, plan.model_artifact.sha256, "score.model.artifact_sha256"),
    )
    for actual, expected, label in checks:
        if actual != expected:
            mismatches.append(label)
    if mismatches:
        raise IntegrityError(
            "EVAL_DEV_BUNDLE_SOURCE_BINDING_MISMATCH",
            "Development run inputs and score do not bind the same frozen identities",
            details={"fields": mismatches},
        )


def ensure_development_evidence_destination_available(destination: Path | str) -> Path:
    destination = Path(destination)
    try:
        str(destination).encode("utf-8", errors="strict")
        destination_name_bytes = destination.name.encode("utf-8", errors="strict")
    except UnicodeError as exc:
        raise ContractError(
            "EVAL_DEV_BUNDLE_DESTINATION_INVALID",
            "Evidence destination filename must be valid strict UTF-8",
        ) from exc
    if destination.name in {"", ".", ".."} or len(destination_name_bytes) > 200:
        raise ContractError(
            "EVAL_DEV_BUNDLE_DESTINATION_INVALID",
            "Evidence destination must have a non-empty filename of at most 200 UTF-8 bytes",
            path=str(destination),
        )
    parent = destination.parent
    try:
        metadata = parent.lstat()
    except OSError as exc:
        raise ContractError(
            "EVAL_DEV_BUNDLE_PARENT_INVALID",
            "Evidence destination parent must already exist",
            path=str(parent),
        ) from exc
    if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        raise ContractError(
            "EVAL_DEV_BUNDLE_PARENT_INVALID",
            "Evidence destination parent must be a real directory, not a symlink",
            path=str(parent),
        )
    if os.path.lexists(destination):
        raise ContractError(
            "EVAL_DEV_BUNDLE_TARGET_EXISTS",
            "Evidence destination already exists and will never be overwritten",
            path=str(destination),
        )
    return destination


def _write_file(path: Path, payload: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o600)
    try:
        with os.fdopen(descriptor, "wb", closefd=True) as handle:
            descriptor = -1
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _lock_descriptor(lock_path: Path) -> int:
    if fcntl is None:
        raise ExecutionError(
            "EVAL_DEV_BUNDLE_PLATFORM_UNSUPPORTED",
            "Atomic development evidence publication requires POSIX file locking",
        )
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    return os.open(lock_path, flags, 0o600)


def _rename_directory_noreplace(source: Path, destination: Path) -> None:
    """Atomically publish a directory without replacing any existing target."""

    libc = ctypes.CDLL(None, use_errno=True)
    source_bytes = os.fsencode(source)
    destination_bytes = os.fsencode(destination)
    if sys.platform == "darwin":
        rename_exclusive = getattr(libc, "renamex_np", None)
        if rename_exclusive is None:
            raise ExecutionError(
                "EVAL_DEV_BUNDLE_ATOMIC_NOREPLACE_UNAVAILABLE",
                "This macOS runtime does not expose renamex_np(RENAME_EXCL)",
            )
        rename_exclusive.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        rename_exclusive.restype = ctypes.c_int
        result = rename_exclusive(source_bytes, destination_bytes, 0x00000004)
    elif sys.platform.startswith("linux"):
        rename_exclusive = getattr(libc, "renameat2", None)
        if rename_exclusive is None:
            raise ExecutionError(
                "EVAL_DEV_BUNDLE_ATOMIC_NOREPLACE_UNAVAILABLE",
                "This Linux runtime does not expose renameat2(RENAME_NOREPLACE)",
            )
        rename_exclusive.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        rename_exclusive.restype = ctypes.c_int
        result = rename_exclusive(-100, source_bytes, -100, destination_bytes, 0x00000001)
    else:
        raise ExecutionError(
            "EVAL_DEV_BUNDLE_ATOMIC_NOREPLACE_UNAVAILABLE",
            "Atomic no-replace directory publication is available only on supported macOS/Linux runtimes",
        )
    if result == 0:
        return
    error_number = ctypes.get_errno()
    if error_number in {errno.EEXIST, errno.ENOTEMPTY}:
        raise ContractError(
            "EVAL_DEV_BUNDLE_TARGET_EXISTS",
            "Evidence destination appeared during publication and was not overwritten",
            path=str(destination),
        )
    if error_number in {errno.ENOSYS, errno.ENOTSUP, errno.EINVAL}:
        raise ExecutionError(
            "EVAL_DEV_BUNDLE_ATOMIC_NOREPLACE_UNAVAILABLE",
            "The destination filesystem does not support atomic no-replace directory publication",
            path=str(destination),
            details={"errno": error_number},
        )
    raise OSError(error_number, os.strerror(error_number), str(destination))


def _member(path: str, payload: bytes) -> EvidenceMember:
    return EvidenceMember(path=path, sha256=_sha256(payload), size_bytes=len(payload))


def publish_development_evidence_bundle(
    destination: Path | str,
    *,
    run_plan_snapshot: FileSnapshot,
    predictions_snapshot: FileSnapshot,
    run_result: Mapping[str, Any],
) -> DevelopmentEvidenceReceipt:
    destination = ensure_development_evidence_destination_available(destination)
    if run_plan_snapshot.size_bytes > MAX_JSON_BYTES or predictions_snapshot.size_bytes > MAX_JSONL_BYTES:
        raise ContractError(
            "EVAL_DEV_BUNDLE_SOURCE_TOO_LARGE",
            "Run plan or predictions exceed the development evidence limit",
        )
    plan = parse_json_model_snapshot(run_plan_snapshot, DevelopmentRunPlan)
    public_score = _public_score(run_result)
    _validate_source_bindings(run_result, plan, run_plan_snapshot, predictions_snapshot, public_score)
    score_payload = _canonical_json(public_score.model_dump(mode="json"))
    summary = _run_summary(
        run_result,
        plan,
        run_plan_snapshot=run_plan_snapshot,
        predictions_snapshot=predictions_snapshot,
        public_score_sha256=_sha256(score_payload),
        public_score_size_bytes=len(score_payload),
    )
    summary_payload = _canonical_json(summary.model_dump(mode="json"))
    payloads = {
        "inputs/run-plan.json": run_plan_snapshot.data,
        "public/predictions.jsonl": predictions_snapshot.data,
        "results/run-summary.json": summary_payload,
        "results/score.json": score_payload,
    }
    members = [_member(path, payloads[path]) for path in EXPECTED_BUNDLE_MEMBERS]
    member_set_sha256 = _member_set_sha256(members)
    manifest = DevelopmentEvidenceManifest(
        schema_version="evaluation.development-evidence-manifest.v0",
        bundle_kind="development_run_evidence",
        fixed_tree_version="v0",
        run_id=plan.run_id,
        mode="development",
        split=plan.split,
        run_plan_sha256=run_plan_snapshot.sha256,
        predictions_sha256=predictions_snapshot.sha256,
        predictions_size_bytes=predictions_snapshot.size_bytes,
        public_cases_sha256=summary.public_cases_sha256,
        case_id_roster_sha256=summary.case_id_roster_sha256,
        member_set_sha256=member_set_sha256,
        verification_scope="integrity_and_internal_consistency_only",
        authenticity="unsigned",
        aggregate_metrics_derived_from_private_labels=True,
        score_replay="unavailable_without_private_labels",
        formal_requested=False,
        gate_status="not_eligible",
        compliance_claim_eligible=False,
        private_label_records_included=False,
        raw_logs_included=False,
        offline_rescore_supported=False,
        score_recomputed=False,
        members=members,
    )
    manifest_payload = _canonical_json(manifest.model_dump(mode="json"))
    parent = destination.parent
    staging: Path | None = None
    lock_descriptor: int | None = None
    published = False
    try:
        staging = Path(tempfile.mkdtemp(prefix=f".{destination.name}.tmp-", dir=parent))
        for directory in (staging / "inputs", staging / "public", staging / "results"):
            directory.mkdir(mode=0o700)
        for relative_path, payload in payloads.items():
            _write_file(staging.joinpath(*relative_path.split("/")), payload)
        _write_file(staging / MANIFEST_PATH, manifest_payload)
        verify_development_evidence_bundle(staging)
        for directory in (staging / "inputs", staging / "public", staging / "results", staging):
            _fsync_directory(directory)

        lock_path = parent / f".{destination.name}.publish.lock"
        lock_descriptor = _lock_descriptor(lock_path)
        assert fcntl is not None
        fcntl.flock(lock_descriptor, fcntl.LOCK_EX)
        if os.path.lexists(destination):
            raise ContractError(
                "EVAL_DEV_BUNDLE_TARGET_EXISTS",
                "Evidence destination appeared during publication and was not overwritten",
                path=str(destination),
            )
        _rename_directory_noreplace(staging, destination)
        published = True
        try:
            _fsync_directory(parent)
        except OSError as exc:
            details: dict[str, int | str | bool] = {
                "phase": "parent_directory_fsync",
                "published": True,
                "manifest_sha256": _sha256(manifest_payload),
            }
            if exc.errno is not None:
                details["errno"] = exc.errno
            raise ExecutionError(
                "EVAL_DEV_BUNDLE_DURABILITY_UNCONFIRMED",
                "Evidence directory was atomically published, but parent-directory durability could not be confirmed",
                path=str(destination),
                details=details,
            ) from exc
    except (ContractError, IntegrityError, ExecutionError):
        raise
    except OSError as exc:
        raise ExecutionError(
            "EVAL_DEV_BUNDLE_PUBLISH_FAILED",
            "Cannot publish development evidence bundle",
            path=str(destination),
            details={"errno": exc.errno} if exc.errno is not None else None,
        ) from exc
    finally:
        if lock_descriptor is not None:
            try:
                if fcntl is not None:
                    fcntl.flock(lock_descriptor, fcntl.LOCK_UN)
            except OSError:
                pass
            try:
                os.close(lock_descriptor)
            except OSError:
                pass
        if staging is not None and not published:
            active_exception = sys.exc_info()[1]
            try:
                shutil.rmtree(staging)
            except OSError as cleanup_exc:
                cleanup_issue: dict[str, int | str] = {
                    "operation": "remove_staging_directory",
                    "exception_type": type(cleanup_exc).__name__,
                }
                if cleanup_exc.errno is not None:
                    cleanup_issue["errno"] = cleanup_exc.errno
                if isinstance(active_exception, EvaluationError):
                    active_exception.details.setdefault("cleanup_issues", []).append(cleanup_issue)
                elif active_exception is None:
                    raise ExecutionError(
                        "EVAL_DEV_BUNDLE_STAGING_CLEANUP_FAILED",
                        "An incomplete development evidence staging directory could not be removed",
                        details={"cleanup_issues": [cleanup_issue]},
                    ) from cleanup_exc

    return DevelopmentEvidenceReceipt(
        manifest_sha256=_sha256(manifest_payload),
        manifest_size_bytes=len(manifest_payload),
        member_set_sha256=member_set_sha256,
        member_count=len(members),
    )


def _exact_tree(bundle_root: Path) -> None:
    try:
        root_metadata = bundle_root.lstat()
    except OSError as exc:
        raise ContractError(
            "EVAL_DEV_BUNDLE_ROOT_INVALID",
            "Development evidence root does not exist",
            path=str(bundle_root),
        ) from exc
    if not stat.S_ISDIR(root_metadata.st_mode) or stat.S_ISLNK(root_metadata.st_mode):
        raise ContractError(
            "EVAL_DEV_BUNDLE_ROOT_INVALID",
            "Development evidence root must be a real directory",
            path=str(bundle_root),
        )
    try:
        with os.scandir(bundle_root) as iterator:
            root_entries = {entry.name for entry in iterator}
    except OSError as exc:
        raise ContractError("EVAL_DEV_BUNDLE_TREE_INVALID", f"Cannot inspect bundle tree: {exc}") from exc
    if root_entries != EXPECTED_ROOT_ENTRIES:
        raise ContractError(
            "EVAL_DEV_BUNDLE_TREE_INVALID",
            "Development evidence root must contain exactly the fixed v0 entries",
            details={"entries": sorted(root_entries)},
        )
    manifest_metadata = (bundle_root / MANIFEST_PATH).lstat()
    if not stat.S_ISREG(manifest_metadata.st_mode) or stat.S_ISLNK(manifest_metadata.st_mode):
        raise ContractError("EVAL_DEV_BUNDLE_TREE_INVALID", "Bundle manifest must be a regular file")
    for directory_name, expected_entries in EXPECTED_DIRECTORY_ENTRIES.items():
        directory = bundle_root / directory_name
        metadata = directory.lstat()
        if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
            raise ContractError(
                "EVAL_DEV_BUNDLE_TREE_INVALID",
                "Fixed bundle directory is missing or not a real directory",
                path=directory_name,
            )
        with os.scandir(directory) as iterator:
            entries = {entry.name for entry in iterator}
        if entries != expected_entries:
            raise ContractError(
                "EVAL_DEV_BUNDLE_TREE_INVALID",
                "Fixed bundle directory contains missing or extra entries",
                path=directory_name,
                details={"entries": sorted(entries)},
            )
        for entry_name in entries:
            member_metadata = (directory / entry_name).lstat()
            if not stat.S_ISREG(member_metadata.st_mode) or stat.S_ISLNK(member_metadata.st_mode):
                raise ContractError(
                    "EVAL_DEV_BUNDLE_TREE_INVALID",
                    "Bundle members must be regular files and cannot be symlinks",
                    path=f"{directory_name}/{entry_name}",
                )


def _privacy_path(value: Any, path: str = "$") -> str | None:
    if isinstance(value, dict):
        for raw_key, nested in value.items():
            normalized = re.sub(r"[^a-z0-9]", "", raw_key.casefold())
            next_path = f"{path}.{raw_key}"
            if normalized not in _ALLOWED_PRIVATE_KEYS and (
                normalized in _FORBIDDEN_PRIVATE_KEYS
                or "labelsprivate" in normalized
                or "privatelabel" in normalized
                or "groundtruth" in normalized
            ):
                return next_path
            found = _privacy_path(nested, next_path)
            if found is not None:
                return found
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            found = _privacy_path(nested, f"{path}[{index}]")
            if found is not None:
                return found
    elif isinstance(value, str) and value not in _ALLOWED_SENSITIVE_VALUES:
        normalized = re.sub(r"[^a-z0-9]", "", value.casefold())
        embedded_local_path = re.search(
            r"(?:file://|(?:^|[\s=:])/(?:Users|home|tmp|var|etc|private)/|[A-Za-z]:[\\/]|\\\\)",
            value,
            flags=re.IGNORECASE,
        )
        if (
            "labelsprivate" in normalized
            or "privatelabel" in normalized
            or "groundtruth" in normalized
            or "annotation" in normalized
            or value.startswith(("/", "\\\\", "file://"))
            or re.match(r"^[A-Za-z]:[\\/]", value) is not None
            or embedded_local_path is not None
        ):
            return path
    return None


def _parse_raw_json(snapshot: FileSnapshot) -> dict[str, Any]:
    try:
        value = json.loads(snapshot.text)
    except (json.JSONDecodeError, ValueError, RecursionError) as exc:
        raise ContractError("EVAL_DEV_BUNDLE_JSON_INVALID", "Bundle JSON is invalid", path=str(snapshot.path)) from exc
    if not isinstance(value, dict):
        raise ContractError("EVAL_DEV_BUNDLE_JSON_INVALID", "Bundle JSON must be an object", path=str(snapshot.path))
    return value


def _verify_development_evidence_bundle(
    bundle_root: Path | str,
    *,
    expected_manifest_sha256: str | None = None,
) -> dict[str, Any]:
    bundle_root = Path(bundle_root)
    _exact_tree(bundle_root)
    manifest_snapshot = snapshot_file(bundle_root / MANIFEST_PATH, max_bytes=MAX_BUNDLE_JSON_BYTES)
    if expected_manifest_sha256 is not None:
        if not _HEX_SHA256.fullmatch(expected_manifest_sha256):
            raise ContractError(
                "EVAL_DEV_BUNDLE_EXPECTED_DIGEST_INVALID",
                "Expected bundle manifest SHA-256 must be 64 lowercase hexadecimal characters",
            )
        if manifest_snapshot.sha256 != expected_manifest_sha256:
            raise IntegrityError(
                "EVAL_DEV_BUNDLE_MANIFEST_IDENTITY_MISMATCH",
                "Bundle manifest does not match its external expected SHA-256",
                details={
                    "expected_sha256": expected_manifest_sha256,
                    "actual_sha256": manifest_snapshot.sha256,
                },
            )
    manifest = parse_json_model_snapshot(manifest_snapshot, DevelopmentEvidenceManifest)
    snapshots: dict[str, FileSnapshot] = {}
    members_by_path = {member.path: member for member in manifest.members}
    for relative_path in EXPECTED_BUNDLE_MEMBERS:
        maximum = MAX_JSONL_BYTES if relative_path == "public/predictions.jsonl" else MAX_BUNDLE_JSON_BYTES
        snapshot = snapshot_file(bundle_root.joinpath(*relative_path.split("/")), max_bytes=maximum)
        snapshots[relative_path] = snapshot
        declared = members_by_path[relative_path]
        if snapshot.size_bytes != declared.size_bytes or snapshot.sha256 != declared.sha256:
            raise IntegrityError(
                "EVAL_DEV_BUNDLE_MEMBER_IDENTITY_MISMATCH",
                "Bundle member size or SHA-256 differs from the manifest",
                path=relative_path,
                details={
                    "expected_sha256": declared.sha256,
                    "actual_sha256": snapshot.sha256,
                    "expected_size": declared.size_bytes,
                    "actual_size": snapshot.size_bytes,
                },
            )
    actual_member_set_sha256 = _member_set_sha256(
        [_member(path, snapshots[path].data) for path in EXPECTED_BUNDLE_MEMBERS]
    )
    if manifest.member_set_sha256 != actual_member_set_sha256:
        raise IntegrityError(
            "EVAL_DEV_BUNDLE_MEMBER_SET_MISMATCH",
            "Bundle member-set digest differs from the actual fixed members",
            details={
                "expected_sha256": manifest.member_set_sha256,
                "actual_sha256": actual_member_set_sha256,
            },
        )

    run_plan = parse_json_model_snapshot(snapshots["inputs/run-plan.json"], DevelopmentRunPlan)
    predictions = parse_jsonl_models_snapshot(
        snapshots["public/predictions.jsonl"],
        EvaluationPrediction,
        record_kind="prediction",
        unique_key=lambda item: item.case_id,
        protect_predictions=True,
    )
    public_score = parse_json_model_snapshot(snapshots["results/score.json"], DevelopmentPublicScore)
    run_summary = parse_json_model_snapshot(snapshots["results/run-summary.json"], DevelopmentRunSummary)
    for relative_path in (
        MANIFEST_PATH,
        "inputs/run-plan.json",
        "results/score.json",
        "results/run-summary.json",
    ):
        snapshot = manifest_snapshot if relative_path == MANIFEST_PATH else snapshots[relative_path]
        forbidden_path = _privacy_path(_parse_raw_json(snapshot))
        if forbidden_path is not None:
            raise ContractError(
                "EVAL_DEV_BUNDLE_PRIVATE_FIELD_FORBIDDEN",
                "Public development evidence contains a private-label or ground-truth field",
                path=f"{relative_path}:{forbidden_path}",
            )
    for line_number, line in enumerate(snapshots["public/predictions.jsonl"].text.splitlines(), start=1):
        raw_prediction = parse_json_object(
            line,
            location=f"public/predictions.jsonl:{line_number}",
        )
        forbidden_path = _privacy_path(raw_prediction)
        if forbidden_path is not None:
            raise ContractError(
                "EVAL_DEV_BUNDLE_PRIVATE_FIELD_FORBIDDEN",
                "Public predictions contain a private-label, ground-truth, annotation, or local-path value",
                path=f"public/predictions.jsonl:{line_number}:{forbidden_path}",
            )

    prediction_snapshot = snapshots["public/predictions.jsonl"]
    score_snapshot = snapshots["results/score.json"]
    mismatches: list[str] = []
    checks = (
        (manifest.run_id, run_plan.run_id, "manifest.run_id"),
        (manifest.split, run_plan.split, "manifest.split"),
        (manifest.run_plan_sha256, snapshots["inputs/run-plan.json"].sha256, "manifest.run_plan_sha256"),
        (manifest.predictions_sha256, prediction_snapshot.sha256, "manifest.predictions_sha256"),
        (manifest.predictions_size_bytes, prediction_snapshot.size_bytes, "manifest.predictions_size_bytes"),
        (manifest.public_cases_sha256, run_summary.public_cases_sha256, "manifest.public_cases_sha256"),
        (
            manifest.case_id_roster_sha256,
            run_summary.case_id_roster_sha256,
            "manifest.case_id_roster_sha256",
        ),
        (run_summary.run_id, run_plan.run_id, "summary.run_id"),
        (run_summary.split, run_plan.split, "summary.split"),
        (run_summary.run_plan_sha256, snapshots["inputs/run-plan.json"].sha256, "summary.run_plan_sha256"),
        (run_summary.run_plan_size_bytes, snapshots["inputs/run-plan.json"].size_bytes, "summary.run_plan_size_bytes"),
        (run_summary.evaluator_source_sha256, run_plan.evaluator_source_sha256, "summary.evaluator_source_sha256"),
        (
            run_summary.training_data_manifest_sha256,
            run_plan.training_data_manifest.sha256,
            "summary.training_data_manifest_sha256",
        ),
        (run_summary.dataset_manifest_sha256, run_plan.dataset_manifest.sha256, "summary.dataset_manifest_sha256"),
        (run_summary.model_statement_sha256, run_plan.model_statement.sha256, "summary.model_statement_sha256"),
        (run_summary.model_artifact_sha256, run_plan.model_artifact.sha256, "summary.model_artifact_sha256"),
        (run_summary.predictions_sha256, prediction_snapshot.sha256, "summary.predictions_sha256"),
        (run_summary.predictions_size_bytes, prediction_snapshot.size_bytes, "summary.predictions_size_bytes"),
        (run_summary.inference_case_count, len(predictions), "summary.inference_case_count"),
        (
            run_summary.case_id_roster_sha256,
            case_id_roster_sha256([prediction.case_id for prediction in predictions]),
            "summary.case_id_roster_sha256",
        ),
        (run_summary.public_score_sha256, score_snapshot.sha256, "summary.public_score_sha256"),
        (run_summary.public_score_size_bytes, score_snapshot.size_bytes, "summary.public_score_size_bytes"),
        (public_score.split, run_plan.split, "score.split"),
        (public_score.dataset.manifest_sha256, run_plan.dataset_manifest.sha256, "score.dataset.manifest_sha256"),
        (public_score.dataset.manifest_size_bytes, run_plan.dataset_manifest.size_bytes, "score.dataset.manifest_size_bytes"),
        (public_score.model.statement_sha256, run_plan.model_statement.sha256, "score.model.statement_sha256"),
        (public_score.model.statement_size_bytes, run_plan.model_statement.size_bytes, "score.model.statement_size_bytes"),
        (public_score.model.artifact_sha256, run_plan.model_artifact.sha256, "score.model.artifact_sha256"),
        (public_score.predictions_sha256, prediction_snapshot.sha256, "score.predictions_sha256"),
        (public_score.predictions_size_bytes, prediction_snapshot.size_bytes, "score.predictions_size_bytes"),
    )
    for actual, expected, label in checks:
        if actual != expected:
            mismatches.append(label)
    if mismatches:
        raise IntegrityError(
            "EVAL_DEV_BUNDLE_INTERNAL_BINDING_MISMATCH",
            "Development evidence members do not bind the same run identities",
            details={"fields": mismatches},
        )

    return {
        "schema_version": "evaluation.development-evidence-verification.v0",
        "ok": True,
        "integrity_status": "passed",
        "internal_consistency_status": "passed",
        "score_recomputed": False,
        "manifest_authenticity": "unsigned",
        "expected_manifest_sha256_status": "matched" if expected_manifest_sha256 is not None else "not_supplied",
        "content_origin_status": "unverified",
        "privacy_claim_status": "not_provided",
        "formal_eligible": False,
        "compliance_claim_eligible": False,
        "bundle_manifest_sha256": manifest_snapshot.sha256,
        "bundle_manifest_size_bytes": manifest_snapshot.size_bytes,
        "member_set_sha256": actual_member_set_sha256,
        "member_count": len(EXPECTED_BUNDLE_MEMBERS),
        "run_id": run_plan.run_id,
        "split": run_plan.split,
        "predictions_sha256": prediction_snapshot.sha256,
        "threshold_status": public_score.threshold_status,
    }


def verify_development_evidence_bundle(
    bundle_root: Path | str,
    *,
    expected_manifest_sha256: str | None = None,
) -> dict[str, Any]:
    """Verify one fixed-tree bundle with a machine-readable local I/O boundary."""

    try:
        try:
            str(bundle_root).encode("utf-8", errors="strict")
        except UnicodeError as exc:
            raise ContractError(
                "EVAL_DEV_BUNDLE_ROOT_INVALID",
                "Evidence bundle path must be valid strict UTF-8",
            ) from exc
        return _verify_development_evidence_bundle(
            bundle_root,
            expected_manifest_sha256=expected_manifest_sha256,
        )
    except (ContractError, IntegrityError, ExecutionError):
        raise
    except OSError as exc:
        details: dict[str, int | str] = {"exception_type": type(exc).__name__}
        if exc.errno is not None:
            details["errno"] = exc.errno
        raise ExecutionError(
            "EVAL_DEV_BUNDLE_READ_FAILED",
            "A local filesystem operation failed while reading development evidence",
            path=str(bundle_root),
            details=details,
        ) from exc


__all__ = [
    "DevelopmentEvidenceReceipt",
    "case_id_roster_sha256",
    "ensure_development_evidence_destination_available",
    "publish_development_evidence_bundle",
    "verify_development_evidence_bundle",
]
