from __future__ import annotations

import hashlib
import importlib
import json
import os
import platform
import signal
import subprocess
import sys
import tempfile
import time
import unicodedata
from pathlib import Path, PurePosixPath
from typing import BinaryIO

from .bundle import (
    case_id_roster_sha256,
    ensure_development_evidence_destination_available,
    publish_development_evidence_bundle,
)
from .errors import ContractError, ExecutionError, IntegrityError
from .jsonio import (
    MAX_JSON_BYTES,
    MAX_JSONL_BYTES,
    open_relative_regular_file,
    parse_json_model_snapshot,
    snapshot_file,
    snapshot_relative_file,
)
from .run_schemas import DevelopmentRunPlan, RunArtifact, TrainingDataManifest
from .schemas import EvaluationModelStatement
from .service import score_dataset, validate_dataset


MAX_ENTRYPOINT_BYTES = 2 * 1024 * 1024
MAX_TRAINING_MANIFEST_BYTES = 16 * 1024 * 1024
MAX_MODEL_ARTIFACT_BYTES = 2 * 1024 * 1024 * 1024
MAX_PUBLIC_ASSETS_TOTAL_BYTES = 8 * 1024 * 1024 * 1024
DEVELOPMENT_ASSURANCE_LIMITATIONS = (
    "development_local_process_only",
    "filesystem_isolation_unverified",
    "network_isolation_unverified",
    "memory_and_process_count_isolation_unverified",
    "runtime_artifact_unpinned",
    "run_bundle_unsealed",
    "trusted_holdout_broker_unimplemented",
)
EVALUATOR_SOURCE_FILES = (
    "__init__.py",
    "bundle.py",
    "bundle_schemas.py",
    "cli.py",
    "controlled_bundle.py",
    "controlled_bundle_schemas.py",
    "errors.py",
    "executor.py",
    "jsonio.py",
    "metrics.py",
    "registry.py",
    "registry_schemas.py",
    "run_schemas.py",
    "schemas.py",
    "service.py",
    "supervisor.py",
)


def _platform_support_issues() -> list[str]:
    """List missing capabilities required by the development-only executor."""

    issues: list[str] = []
    if os.name != "posix":
        issues.append("posix_runtime")
    if not callable(getattr(os, "killpg", None)):
        issues.append("posix_process_groups")
    if os.open not in getattr(os, "supports_dir_fd", ()):
        issues.append("secure_open_dir_fd")
    if not hasattr(os, "O_DIRECTORY"):
        issues.append("secure_open_directory_flag")
    if not hasattr(os, "O_NOFOLLOW"):
        issues.append("secure_open_nofollow_flag")
    try:
        resource_module = importlib.import_module("resource")
    except (ImportError, OSError):
        issues.append("posix_resource_limits")
    else:
        required_resource_members = (
            "getrlimit",
            "setrlimit",
            "RLIMIT_CPU",
            "RLIMIT_FSIZE",
            "RLIMIT_NOFILE",
            "RLIM_INFINITY",
        )
        if any(not hasattr(resource_module, member) for member in required_resource_members):
            issues.append("posix_resource_limits")
    return issues


def _ensure_supported_platform() -> None:
    issues = _platform_support_issues()
    if issues:
        raise ExecutionError(
            "EVAL_RUN_PLATFORM_UNSUPPORTED",
            "The development runner requires POSIX process groups, resource limits, and secure root-confined opens",
            details={"missing_capabilities": issues},
        )


def evaluator_source_sha256() -> str:
    """Digest every Python source file that defines the local evaluator."""

    source_root = Path(__file__).resolve().parent
    records: list[dict[str, int | str]] = []
    for name in EVALUATOR_SOURCE_FILES:
        path = source_root / name
        payload = path.read_bytes()
        records.append(
            {
                "path": name,
                "size_bytes": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
        )
    canonical = json.dumps(records, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _verify_snapshot(snapshot_sha256: str, snapshot_size: int, artifact: RunArtifact, *, label: str) -> None:
    if snapshot_size != artifact.size_bytes:
        raise IntegrityError(
            "EVAL_RUN_ARTIFACT_SIZE_MISMATCH",
            f"{label} size differs from the run plan",
            path=artifact.path,
            details={"expected_size": artifact.size_bytes, "actual_size": snapshot_size},
        )
    if snapshot_sha256 != artifact.sha256:
        raise IntegrityError(
            "EVAL_RUN_ARTIFACT_HASH_MISMATCH",
            f"{label} SHA-256 differs from the run plan",
            path=artifact.path,
            details={"expected_sha256": artifact.sha256, "actual_sha256": snapshot_sha256},
        )


def _verify_external_digest(expected_sha256: str, actual_sha256: str) -> None:
    if (
        len(expected_sha256) != 64
        or expected_sha256 != expected_sha256.lower()
        or any(character not in "0123456789abcdef" for character in expected_sha256)
    ):
        raise ContractError(
            "EVAL_RUN_EXPECTED_DIGEST_INVALID",
            "expected_run_plan_sha256 must be 64 lowercase hexadecimal characters",
        )
    if expected_sha256 != actual_sha256:
        raise IntegrityError(
            "EVAL_RUN_PLAN_IDENTITY_MISMATCH",
            "External run-plan SHA-256 does not match the supplied plan",
            details={"expected_sha256": expected_sha256, "actual_sha256": actual_sha256},
        )


def _copy_verified_relative_file(
    root: Path,
    relative_path: str,
    *,
    expected_sha256: str,
    expected_size: int,
    destination: Path,
    max_bytes: int,
    label: str,
) -> None:
    if expected_size > max_bytes:
        raise ContractError(
            "EVAL_RUN_ARTIFACT_TOO_LARGE",
            f"{label} exceeds the development runner limit",
            path=relative_path,
            details={"max_bytes": max_bytes, "declared_size": expected_size},
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    copied = 0
    try:
        with open_relative_regular_file(root, relative_path) as (source, metadata, _):
            if metadata.st_size != expected_size:
                raise IntegrityError(
                    "EVAL_RUN_ARTIFACT_SIZE_MISMATCH",
                    f"{label} size differs from its declaration",
                    path=relative_path,
                    details={"expected_size": expected_size, "actual_size": metadata.st_size},
                )
            with destination.open("xb") as target:
                for block in iter(lambda: source.read(1024 * 1024), b""):
                    copied += len(block)
                    if copied > max_bytes:
                        raise ContractError(
                            "EVAL_RUN_ARTIFACT_TOO_LARGE",
                            f"{label} exceeded the development runner limit while being copied",
                            path=relative_path,
                            details={"max_bytes": max_bytes},
                        )
                    digest.update(block)
                    target.write(block)
                target.flush()
                os.fsync(target.fileno())
    except (ContractError, IntegrityError):
        raise
    except OSError as exc:
        raise ExecutionError(
            "EVAL_RUN_ARTIFACT_COPY_FAILED",
            f"Cannot stage {label}: {exc}",
            path=relative_path,
        ) from exc
    if copied != expected_size:
        raise IntegrityError(
            "EVAL_RUN_ARTIFACT_SIZE_MISMATCH",
            f"{label} bytes changed while being copied",
            path=relative_path,
            details={"expected_size": expected_size, "actual_size": copied},
        )
    actual_sha256 = digest.hexdigest()
    if actual_sha256 != expected_sha256:
        raise IntegrityError(
            "EVAL_RUN_ARTIFACT_HASH_MISMATCH",
            f"{label} SHA-256 differs from its declaration",
            path=relative_path,
            details={"expected_sha256": expected_sha256, "actual_sha256": actual_sha256},
        )
    destination.chmod(0o400)


def _copy_run_artifact(
    root: Path,
    artifact: RunArtifact,
    destination: Path,
    *,
    max_bytes: int,
    label: str,
) -> None:
    _copy_verified_relative_file(
        root,
        artifact.path,
        expected_sha256=artifact.sha256,
        expected_size=artifact.size_bytes,
        destination=destination,
        max_bytes=max_bytes,
        label=label,
    )


def _verify_run_artifact_current(
    root: Path,
    artifact: RunArtifact,
    *,
    max_bytes: int,
    label: str,
) -> None:
    if artifact.size_bytes > max_bytes:
        raise ContractError(
            "EVAL_RUN_ARTIFACT_TOO_LARGE",
            f"{label} exceeds the development runner limit",
            path=artifact.path,
            details={"max_bytes": max_bytes, "declared_size": artifact.size_bytes},
        )
    digest = hashlib.sha256()
    with open_relative_regular_file(root, artifact.path) as (source, metadata, _):
        if metadata.st_size != artifact.size_bytes:
            raise IntegrityError(
                "EVAL_RUN_ARTIFACT_SIZE_MISMATCH",
                f"{label} size differs from the run plan",
                path=artifact.path,
                details={"expected_size": artifact.size_bytes, "actual_size": metadata.st_size},
            )
        read_bytes = 0
        for block in iter(lambda: source.read(1024 * 1024), b""):
            read_bytes += len(block)
            if read_bytes > max_bytes:
                raise ContractError(
                    "EVAL_RUN_ARTIFACT_TOO_LARGE",
                    f"{label} exceeded the development runner limit while being verified",
                    path=artifact.path,
                    details={"max_bytes": max_bytes},
                )
            digest.update(block)
    actual_sha256 = digest.hexdigest()
    if actual_sha256 != artifact.sha256:
        raise IntegrityError(
            "EVAL_RUN_ARTIFACT_HASH_MISMATCH",
            f"{label} SHA-256 differs from the run plan",
            path=artifact.path,
            details={"expected_sha256": artifact.sha256, "actual_sha256": actual_sha256},
        )


def _write_inference_cases(handle: BinaryIO, target_cases: list) -> None:
    written = 0
    for case in sorted(target_cases, key=lambda item: item.case_id):
        payload = case.model_dump_json(exclude_none=False).encode("utf-8") + b"\n"
        written += len(payload)
        if written > MAX_JSONL_BYTES:
            raise ContractError(
                "EVAL_RUN_PUBLIC_CASES_TOO_LARGE",
                "Target-split public cases exceed the development runner limit",
                details={"max_bytes": MAX_JSONL_BYTES},
            )
        handle.write(payload)
    handle.flush()
    os.fsync(handle.fileno())


def _log_identity_handle(handle: BinaryIO, *, max_bytes: int, label: str) -> dict[str, int | str]:
    handle.flush()
    size = os.fstat(handle.fileno()).st_size
    if size > max_bytes:
        raise ExecutionError(
            "EVAL_RUN_LOG_TOO_LARGE",
            f"{label} exceeded its configured byte limit",
            details={"max_bytes": max_bytes, "actual_size": size},
        )
    digest = hashlib.sha256()
    handle.seek(0)
    for block in iter(lambda: handle.read(1024 * 1024), b""):
        digest.update(block)
    return {"sha256": digest.hexdigest(), "size_bytes": size}


def _terminate_process_group(process: subprocess.Popen) -> list[dict[str, int | str]]:
    issues: list[dict[str, int | str]] = []

    def record(operation: str, exc: OSError) -> None:
        issue: dict[str, int | str] = {"operation": operation, "exception_type": type(exc).__name__}
        if exc.errno is not None:
            issue["errno"] = exc.errno
        issues.append(issue)

    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    except OSError as exc:
        record("sigterm_process_group", exc)
    if process.poll() is None:
        try:
            process.wait(timeout=0.5)
        except subprocess.TimeoutExpired:
            pass
        except OSError as exc:
            record("wait_after_sigterm", exc)
    time.sleep(0.05)
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    except OSError as exc:
        record("sigkill_process_group", exc)
    if process.poll() is None:
        try:
            process.wait(timeout=0.5)
        except subprocess.TimeoutExpired:
            pass
        except OSError as exc:
            record("wait_after_sigkill", exc)
    return issues


def _run_process(
    *,
    supervisor_path: Path,
    runner_path: Path,
    cases_path: Path,
    model_path: Path,
    output_path: Path,
    stdout_path: Path,
    stderr_path: Path,
    sandbox: Path,
    plan: DevelopmentRunPlan,
) -> tuple[int, int, dict[str, int | str], dict[str, int | str]]:
    _ensure_supported_platform()
    home_path = sandbox / "home"
    temp_path = sandbox / "tmp"
    home_path.mkdir()
    temp_path.mkdir()
    environment = {
        "LANG": "C",
        "LC_ALL": "C",
        "PATH": os.defpath,
        "HOME": str(home_path),
        "TMPDIR": str(temp_path),
        "TMP": str(temp_path),
        "TEMP": str(temp_path),
        "TZ": "UTC",
        "OMP_NUM_THREADS": "1",
        "MKL_NUM_THREADS": "1",
    }
    max_file_bytes = max(plan.max_predictions_bytes, plan.max_log_bytes)
    started = time.monotonic()
    try:
        with stdout_path.open("x+b") as stdout_handle, stderr_path.open("x+b") as stderr_handle:
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-I",
                    "-B",
                    "-X",
                    "utf8",
                    str(supervisor_path),
                    "--max-file-bytes",
                    str(max_file_bytes),
                    "--cpu-seconds",
                    str(plan.timeout_seconds),
                    "--python",
                    sys.executable,
                    "--entrypoint",
                    str(runner_path),
                    "--",
                    "--cases",
                    str(cases_path),
                    "--model",
                    str(model_path),
                    "--output",
                    str(output_path),
                    "--seed",
                    str(plan.random_seed),
                ],
                cwd=sandbox,
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=stdout_handle,
                stderr=stderr_handle,
                close_fds=True,
                start_new_session=True,
            )
            try:
                return_code = process.wait(timeout=plan.timeout_seconds)
            except subprocess.TimeoutExpired as exc:
                cleanup_issues = _terminate_process_group(process)
                details: dict[str, object] = {"timeout_seconds": plan.timeout_seconds}
                if cleanup_issues:
                    details["cleanup_issues"] = cleanup_issues
                raise ExecutionError(
                    "EVAL_RUN_TIMEOUT",
                    "Development model process exceeded the run-plan timeout",
                    details=details,
                ) from exc
            except BaseException:
                _terminate_process_group(process)
                raise
            cleanup_issues = _terminate_process_group(process)
            if cleanup_issues:
                raise ExecutionError(
                    "EVAL_RUN_PROCESS_CLEANUP_FAILED",
                    "Development model process exited but process-group cleanup was incomplete",
                    details={"cleanup_issues": cleanup_issues},
                )
            duration_ms = round((time.monotonic() - started) * 1000)
            stdout_identity = _log_identity_handle(
                stdout_handle,
                max_bytes=plan.max_log_bytes,
                label="stdout",
            )
            stderr_identity = _log_identity_handle(
                stderr_handle,
                max_bytes=plan.max_log_bytes,
                label="stderr",
            )
            return return_code, duration_ms, stdout_identity, stderr_identity
    except ExecutionError:
        raise
    except OSError as exc:
        raise ExecutionError("EVAL_RUN_PROCESS_START_FAILED", f"Cannot start development model process: {exc}") from exc


def _run_development_plan(
    plan_path: Path | str,
    *,
    expected_run_plan_sha256: str,
    evidence_directory: Path | str | None = None,
) -> dict:
    plan_path = Path(plan_path)
    plan_snapshot = snapshot_file(plan_path, max_bytes=MAX_JSON_BYTES)
    _verify_external_digest(expected_run_plan_sha256, plan_snapshot.sha256)
    plan = parse_json_model_snapshot(plan_snapshot, DevelopmentRunPlan)
    plan_root = plan_path.parent

    if PurePosixPath(plan.entrypoint.path).suffix != ".py":
        raise ContractError(
            "EVAL_RUN_ENTRYPOINT_INVALID",
            "Development runner entrypoint must be one self-contained Python file",
            path=plan.entrypoint.path,
        )

    # Parse the strict plan first so formal/holdout requests are rejected by
    # the contract even on an unsupported host.  Check host capabilities before
    # opening relative artifacts or creating any execution workspace.
    _ensure_supported_platform()
    evidence_destination = (
        ensure_development_evidence_destination_available(evidence_directory)
        if evidence_directory is not None
        else None
    )

    actual_evaluator_sha256 = evaluator_source_sha256()
    if plan.evaluator_source_sha256 != actual_evaluator_sha256:
        raise IntegrityError(
            "EVAL_RUN_EVALUATOR_IDENTITY_MISMATCH",
            "Evaluator source digest differs from the run plan",
            details={
                "expected_sha256": plan.evaluator_source_sha256,
                "actual_sha256": actual_evaluator_sha256,
            },
        )

    manifest_snapshot = snapshot_relative_file(plan_root, plan.dataset_manifest.path, max_bytes=MAX_JSON_BYTES)
    _verify_snapshot(
        manifest_snapshot.sha256,
        manifest_snapshot.size_bytes,
        plan.dataset_manifest,
        label="dataset manifest",
    )
    manifest_path = plan_root.resolve().joinpath(*PurePosixPath(plan.dataset_manifest.path).parts)
    dataset = validate_dataset(manifest_path, formal=False)
    target_cases = [case for case in dataset.cases if case.split == plan.split]
    if not target_cases:
        raise ContractError("EVAL_EMPTY_EVALUATION_SET", f"Split {plan.split} has no cases")

    model_statement_snapshot = snapshot_relative_file(plan_root, plan.model_statement.path, max_bytes=MAX_JSON_BYTES)
    _verify_snapshot(
        model_statement_snapshot.sha256,
        model_statement_snapshot.size_bytes,
        plan.model_statement,
        label="model statement",
    )
    statement = parse_json_model_snapshot(model_statement_snapshot, EvaluationModelStatement)
    if statement.artifact_sha256 != plan.model_artifact.sha256:
        raise IntegrityError(
            "EVAL_RUN_MODEL_BINDING_MISMATCH",
            "Model statement artifact digest does not match the run-plan model artifact",
            details={
                "statement_sha256": statement.artifact_sha256,
                "artifact_sha256": plan.model_artifact.sha256,
            },
        )
    training_manifest_snapshot = snapshot_relative_file(
        plan_root,
        plan.training_data_manifest.path,
        max_bytes=MAX_TRAINING_MANIFEST_BYTES,
    )
    _verify_snapshot(
        training_manifest_snapshot.sha256,
        training_manifest_snapshot.size_bytes,
        plan.training_data_manifest,
        label="training-data manifest",
    )
    training_manifest = parse_json_model_snapshot(training_manifest_snapshot, TrainingDataManifest)
    if training_manifest.model_artifact_sha256 != plan.model_artifact.sha256:
        raise IntegrityError(
            "EVAL_RUN_TRAINING_BINDING_MISMATCH",
            "Training-data manifest does not bind the run-plan model artifact",
            details={
                "training_manifest_sha256": training_manifest.model_artifact_sha256,
                "artifact_sha256": plan.model_artifact.sha256,
            },
        )
    model_statement_path = plan_root.resolve().joinpath(*PurePosixPath(plan.model_statement.path).parts)

    with tempfile.TemporaryDirectory(prefix=f"fengmou-{plan.run_id}-") as temporary:
        temporary_root = Path(temporary)
        sandbox = temporary_root / "inference"
        trusted_root = temporary_root / "trusted"
        sandbox.mkdir()
        trusted_root.mkdir()
        cases_path = sandbox / "public" / "cases.jsonl"
        model_path = sandbox / "model" / "artifact"
        runner_path = sandbox / "tools" / "entrypoint.py"
        supervisor_path = sandbox / "tools" / "supervisor.py"
        output_path = sandbox / "runs" / "predictions.jsonl"
        stdout_path = sandbox / "runs" / "stdout.log"
        stderr_path = sandbox / "runs" / "stderr.log"
        output_path.parent.mkdir(parents=True, exist_ok=True)

        supervisor_path.parent.mkdir(parents=True, exist_ok=True)
        supervisor_payload = (Path(__file__).resolve().parent / "supervisor.py").read_bytes()
        with supervisor_path.open("xb") as handle:
            handle.write(supervisor_payload)
            handle.flush()
            os.fsync(handle.fileno())
        supervisor_path.chmod(0o400)

        cases_path.parent.mkdir(parents=True, exist_ok=True)
        with cases_path.open("xb") as handle:
            _write_inference_cases(handle, target_cases)
        cases_path.chmod(0o400)
        public_cases_snapshot = snapshot_file(cases_path, max_bytes=MAX_JSONL_BYTES)

        copied_assets: set[str] = set()
        copied_asset_bytes = 0
        normalized_destinations = {
            unicodedata.normalize("NFC", "public/cases.jsonl").casefold(): "public/cases.jsonl"
        }
        dataset_root = dataset.manifest_path.parent
        for case in target_cases:
            for asset in case.inputs:
                asset_parts = PurePosixPath(asset.relative_path).parts
                if not asset_parts or asset_parts[0].casefold() != "public":
                    raise ContractError(
                        "EVAL_RUN_NONPUBLIC_ASSET_FORBIDDEN",
                        "Development inference assets must live under the public path namespace",
                        path=asset.relative_path,
                    )
                if asset.relative_path in copied_assets:
                    continue
                copied_asset_bytes += asset.size_bytes
                if copied_asset_bytes > MAX_PUBLIC_ASSETS_TOTAL_BYTES:
                    raise ContractError(
                        "EVAL_RUN_PUBLIC_ASSETS_TOO_LARGE",
                        "Target-split public assets exceed the development runner total-byte limit",
                        details={"max_bytes": MAX_PUBLIC_ASSETS_TOTAL_BYTES},
                    )
                normalized = unicodedata.normalize("NFC", asset.relative_path).casefold()
                previous_path = normalized_destinations.get(normalized)
                if previous_path is not None:
                    raise IntegrityError(
                        "EVAL_RUN_PATH_COLLISION",
                        "Inference paths collide after NFC and case folding",
                        details={"first_path": previous_path, "second_path": asset.relative_path},
                    )
                normalized_destinations[normalized] = asset.relative_path
                destination = sandbox.joinpath(*asset_parts)
                _copy_verified_relative_file(
                    dataset_root,
                    asset.relative_path,
                    expected_sha256=asset.sha256,
                    expected_size=asset.size_bytes,
                    destination=destination,
                    max_bytes=MAX_MODEL_ARTIFACT_BYTES,
                    label="inference asset",
                )
                copied_assets.add(asset.relative_path)

        _copy_run_artifact(
            plan_root,
            plan.model_artifact,
            model_path,
            max_bytes=MAX_MODEL_ARTIFACT_BYTES,
            label="model artifact",
        )
        _copy_run_artifact(
            plan_root,
            plan.entrypoint,
            runner_path,
            max_bytes=MAX_ENTRYPOINT_BYTES,
            label="runner entrypoint",
        )
        return_code, duration_ms, stdout_identity, stderr_identity = _run_process(
            supervisor_path=supervisor_path,
            runner_path=runner_path,
            cases_path=cases_path,
            model_path=model_path,
            output_path=output_path,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
            sandbox=sandbox,
            plan=plan,
        )
        if return_code != 0:
            raise ExecutionError(
                "EVAL_RUN_PROCESS_FAILED",
                "Development model process returned a non-zero exit code",
                details={
                    "return_code": return_code,
                    "stdout": stdout_identity,
                    "stderr": stderr_identity,
                },
            )
        _verify_external_digest(expected_run_plan_sha256, snapshot_file(plan_path, max_bytes=MAX_JSON_BYTES).sha256)
        _verify_run_artifact_current(
            plan_root,
            plan.dataset_manifest,
            max_bytes=MAX_JSON_BYTES,
            label="dataset manifest",
        )
        _verify_run_artifact_current(
            plan_root,
            plan.model_statement,
            max_bytes=MAX_JSON_BYTES,
            label="model statement",
        )
        _verify_run_artifact_current(
            plan_root,
            plan.model_artifact,
            max_bytes=MAX_MODEL_ARTIFACT_BYTES,
            label="model artifact",
        )
        _verify_run_artifact_current(
            plan_root,
            plan.entrypoint,
            max_bytes=MAX_ENTRYPOINT_BYTES,
            label="runner entrypoint",
        )
        _verify_run_artifact_current(
            plan_root,
            plan.training_data_manifest,
            max_bytes=MAX_TRAINING_MANIFEST_BYTES,
            label="training-data manifest",
        )
        if evaluator_source_sha256() != actual_evaluator_sha256:
            raise IntegrityError(
                "EVAL_RUN_EVALUATOR_IDENTITY_MISMATCH",
                "Evaluator source changed during the development run",
            )
        if not output_path.exists():
            raise ExecutionError("EVAL_RUN_PREDICTIONS_MISSING", "Development model produced no predictions file")
        try:
            predictions_snapshot = snapshot_file(output_path, max_bytes=plan.max_predictions_bytes)
        except ContractError as exc:
            raise ExecutionError(
                "EVAL_RUN_PREDICTIONS_INVALID",
                "Development model produced an unreadable or unsafe predictions artifact",
                details={"cause": exc.code},
            ) from exc
        trusted_predictions_path = trusted_root / "predictions.jsonl"
        with trusted_predictions_path.open("xb") as handle:
            handle.write(predictions_snapshot.data)
            handle.flush()
            os.fsync(handle.fileno())
        trusted_predictions_path.chmod(0o400)
        try:
            score = score_dataset(
                manifest_path,
                trusted_predictions_path,
                model_statement_path,
                split=plan.split,
                formal=False,
                expected_manifest_sha256=plan.dataset_manifest.sha256,
                expected_model_statement_sha256=plan.model_statement.sha256,
            )
        except ContractError as exc:
            prediction_path_error = exc.path is not None and exc.path.startswith(str(trusted_predictions_path))
            if exc.code.startswith("EVAL_PREDICTION_") or prediction_path_error:
                raise ExecutionError(
                    "EVAL_RUN_PREDICTIONS_INVALID",
                    "Development model predictions do not satisfy the scoring contract",
                    details={"cause": exc.code},
                ) from exc
            raise

        assurance_limitations = list(DEVELOPMENT_ASSURANCE_LIMITATIONS)
        if evidence_destination is not None:
            assurance_limitations.remove("run_bundle_unsealed")
            assurance_limitations.extend(
                [
                    "development_evidence_unsigned",
                    "public_score_replay_unavailable_without_private_labels",
                ]
            )
        result = {
            "schema_version": "evaluation.development-run.v0",
            "ok": True,
            "run_id": plan.run_id,
            "mode": "development",
            "runner": "local_process",
            "protocol": "evaluation.predictor-cli.v0",
            "split": plan.split,
            "formal_requested": False,
            "gate_status": "not_eligible",
            "compliance_claim_eligible": False,
            "run_plan_sha256": plan_snapshot.sha256,
            "evaluator_source_sha256": actual_evaluator_sha256,
            "training_data_manifest_sha256": plan.training_data_manifest.sha256,
            "runtime": {
                "python_version": platform.python_version(),
                "implementation": platform.python_implementation(),
                "platform": platform.system().casefold(),
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
            "process": {
                "return_code": return_code,
                "duration_ms": duration_ms,
                "stdout": stdout_identity,
                "stderr": stderr_identity,
            },
            "inference_view": {
                "case_count": len(target_cases),
                "asset_count": len(copied_assets),
                "asset_size_bytes": copied_asset_bytes,
                "private_labels_copied": False,
                "public_cases_sha256": public_cases_snapshot.sha256,
                "case_id_roster_sha256": case_id_roster_sha256([case.case_id for case in target_cases]),
            },
            "predictions_sha256": predictions_snapshot.sha256,
            "predictions_size_bytes": predictions_snapshot.size_bytes,
            "assurance_limitations": assurance_limitations,
            "score": score,
        }
        if evidence_destination is not None:
            receipt = publish_development_evidence_bundle(
                evidence_destination,
                run_plan_snapshot=plan_snapshot,
                predictions_snapshot=predictions_snapshot,
                run_result=result,
            )
            result["evidence_bundle"] = receipt.as_dict()
        return result


def run_development_plan(
    plan_path: Path | str,
    *,
    expected_run_plan_sha256: str,
    evidence_directory: Path | str | None = None,
) -> dict:
    """Run one development plan with a machine-readable local I/O boundary."""

    try:
        return _run_development_plan(
            plan_path,
            expected_run_plan_sha256=expected_run_plan_sha256,
            evidence_directory=evidence_directory,
        )
    except (ContractError, IntegrityError, ExecutionError):
        raise
    except OSError as exc:
        details: dict[str, int | str] = {"exception_type": type(exc).__name__}
        if exc.errno is not None:
            details["errno"] = exc.errno
        reason = str(exc)
        if reason:
            details["reason"] = reason[:1000]
        raise ExecutionError(
            "EVAL_RUN_LOCAL_IO_FAILED",
            "A local filesystem operation failed while preparing or recording the development run",
            details=details,
        ) from exc


__all__ = ["evaluator_source_sha256", "run_development_plan"]
