from __future__ import annotations

import base64
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from typing import Any, Iterable

from .controlled_bundle_schemas import (
    CONTROLLED_BUNDLE_MEMBER_PATHS,
    CONTROLLED_CORE_MEMBER_PATHS,
    ControlledEvidenceMember,
    ControlledLocalEvidenceManifest,
    ControlledLocalPublicScore,
    ControlledLocalRunPlan,
    ControlledLocalRunSummary,
    Ed25519TrustStore,
)
from .errors import ContractError, ExecutionError, IntegrityError
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
from .registry_schemas import HoldoutAttemptRecord
from .schemas import EvaluationPrediction


MANIFEST_PATH = "bundle-manifest.json"
SIGNATURE_PATH = "bundle-manifest.ed25519"
EXPECTED_ROOT_ENTRIES = frozenset(
    {MANIFEST_PATH, SIGNATURE_PATH, "inputs", "public", "registry", "results"}
)
EXPECTED_DIRECTORY_ENTRIES = {
    "inputs": frozenset({"run-plan.json"}),
    "public": frozenset({"predictions.jsonl"}),
    "registry": frozenset({"attempt.json"}),
    "results": frozenset({"run-summary.json", "score.json"}),
}
CORE_COMMITMENT_DOMAIN = b"evaluation.controlled-run-core-member-set.v0\n"
MEMBER_SET_DOMAIN = b"evaluation.controlled-local-member-set.v0\n"
BUNDLE_ID_DOMAIN = b"evaluation.controlled-local-bundle-id.v0\n"
SIGNATURE_CONTEXT = b"FENGMOU\x00evaluation.controlled-local-evidence-manifest.v0\x00"
MAX_SIGNATURE_BYTES = 64
MAX_TRUST_STORE_BYTES = 2 * 1024 * 1024
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class BinarySnapshot:
    data: bytes
    sha256: str
    size_bytes: int


def _canonical_json(value: Any, *, label: str) -> bytes:
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
            "EVAL_CONTROLLED_JSON_INVALID",
            f"{label} cannot be serialized as canonical strict JSON",
        ) from exc


def canonical_controlled_manifest_bytes(
    manifest: ControlledLocalEvidenceManifest | dict[str, Any],
) -> bytes:
    try:
        parsed = (
            manifest
            if isinstance(manifest, ControlledLocalEvidenceManifest)
            else ControlledLocalEvidenceManifest.model_validate(manifest)
        )
    except Exception as exc:
        raise ContractError(
            "EVAL_CONTROLLED_MANIFEST_INVALID",
            f"Controlled manifest is invalid: {str(exc)[:2000]}",
        ) from exc
    return _canonical_json(parsed.model_dump(mode="json"), label="Controlled manifest")


def canonical_trust_store_bytes(trust_store: Ed25519TrustStore | dict[str, Any]) -> bytes:
    try:
        parsed = (
            trust_store
            if isinstance(trust_store, Ed25519TrustStore)
            else Ed25519TrustStore.model_validate(trust_store)
        )
    except Exception as exc:
        raise ContractError(
            "EVAL_CONTROLLED_TRUST_STORE_INVALID",
            f"Ed25519 trust store is invalid: {str(exc)[:2000]}",
        ) from exc
    return _canonical_json(parsed.model_dump(mode="json"), label="Trust store")


def controlled_manifest_signing_message(canonical_manifest: bytes) -> bytes:
    if not canonical_manifest or len(canonical_manifest) > MAX_JSON_BYTES:
        raise ContractError(
            "EVAL_CONTROLLED_MANIFEST_INVALID",
            "Canonical controlled manifest size is outside the supported range",
        )
    return SIGNATURE_CONTEXT + len(canonical_manifest).to_bytes(8, "big") + canonical_manifest


def _descriptor_payload(members: Iterable[ControlledEvidenceMember]) -> bytes:
    return json.dumps(
        [member.model_dump(mode="json") for member in members],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def controlled_core_result_sha256(members: Iterable[ControlledEvidenceMember]) -> str:
    materialized = list(members)
    if tuple(member.path for member in materialized) != CONTROLLED_CORE_MEMBER_PATHS:
        raise ContractError(
            "EVAL_CONTROLLED_CORE_MEMBER_SET_INVALID",
            "Core commitment requires the four fixed descriptors in lexical order",
        )
    return hashlib.sha256(CORE_COMMITMENT_DOMAIN + _descriptor_payload(materialized)).hexdigest()


def controlled_member_set_sha256(members: Iterable[ControlledEvidenceMember]) -> str:
    materialized = list(members)
    if tuple(member.path for member in materialized) != CONTROLLED_BUNDLE_MEMBER_PATHS:
        raise ContractError(
            "EVAL_CONTROLLED_MEMBER_SET_INVALID",
            "Controlled bundle requires the five fixed descriptors in lexical order",
        )
    return hashlib.sha256(MEMBER_SET_DOMAIN + _descriptor_payload(materialized)).hexdigest()


def controlled_bundle_id(
    *,
    registry_instance_id: str,
    attempt_id: str,
    run_id: str,
    core_result_sha256: str,
) -> str:
    payload = _canonical_json(
        {
            "attempt_id": attempt_id,
            "core_result_sha256": core_result_sha256,
            "registry_instance_id": registry_instance_id,
            "run_id": run_id,
        },
        label="Controlled bundle identity",
    )
    return "crb0:" + hashlib.sha256(BUNDLE_ID_DOMAIN + payload).hexdigest()


def _member(path: str, snapshot: FileSnapshot) -> ControlledEvidenceMember:
    return ControlledEvidenceMember(path=path, sha256=snapshot.sha256, size_bytes=snapshot.size_bytes)


def _exact_tree(root: Path) -> None:
    try:
        metadata = root.lstat()
    except OSError as exc:
        raise ContractError(
            "EVAL_CONTROLLED_BUNDLE_ROOT_INVALID",
            "Controlled bundle root does not exist",
            path=str(root),
        ) from exc
    if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        raise ContractError(
            "EVAL_CONTROLLED_BUNDLE_ROOT_INVALID",
            "Controlled bundle root must be a real directory",
            path=str(root),
        )
    with os.scandir(root) as entries:
        root_entries = {entry.name for entry in entries}
    if root_entries != EXPECTED_ROOT_ENTRIES:
        raise ContractError(
            "EVAL_CONTROLLED_BUNDLE_TREE_INVALID",
            "Controlled bundle root must contain exactly the fixed v0 entries",
            details={"entries": sorted(root_entries)},
        )
    for directory_name, expected in EXPECTED_DIRECTORY_ENTRIES.items():
        directory = root / directory_name
        directory_metadata = directory.lstat()
        if not stat.S_ISDIR(directory_metadata.st_mode) or stat.S_ISLNK(directory_metadata.st_mode):
            raise ContractError(
                "EVAL_CONTROLLED_BUNDLE_TREE_INVALID",
                "Controlled bundle directories must be real directories",
                path=directory_name,
            )
        with os.scandir(directory) as entries:
            actual = {entry.name for entry in entries}
        if actual != expected:
            raise ContractError(
                "EVAL_CONTROLLED_BUNDLE_TREE_INVALID",
                "Controlled bundle directory contains missing or extra entries",
                path=directory_name,
                details={"entries": sorted(actual)},
            )


def _snapshot_binary(root: Path, relative_path: str, *, max_bytes: int) -> BinarySnapshot:
    with open_relative_regular_file(root, relative_path) as (handle, metadata, _):
        if metadata.st_size > max_bytes:
            raise ContractError(
                "EVAL_CONTROLLED_FILE_TOO_LARGE",
                "Controlled bundle binary member exceeds its fixed limit",
                path=relative_path,
                details={"max_bytes": max_bytes},
            )
        payload = handle.read(max_bytes + 1)
    if len(payload) > max_bytes:
        raise ContractError(
            "EVAL_CONTROLLED_FILE_TOO_LARGE",
            "Controlled bundle binary member exceeds its fixed limit",
            path=relative_path,
            details={"max_bytes": max_bytes},
        )
    return BinarySnapshot(
        data=payload,
        sha256=hashlib.sha256(payload).hexdigest(),
        size_bytes=len(payload),
    )


def _validate_expected_digest(value: str, *, label: str) -> None:
    if _SHA256.fullmatch(value) is None:
        raise ContractError(
            "EVAL_CONTROLLED_EXPECTED_DIGEST_INVALID",
            f"Expected {label} SHA-256 must be 64 lowercase hexadecimal characters",
        )


def _verify_signature(
    *,
    signature: bytes,
    message: bytes,
    raw_public_key: bytes,
) -> None:
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    except ImportError as exc:  # pragma: no cover - dependency is pinned in production/tests
        raise ExecutionError(
            "EVAL_CONTROLLED_SIGNING_BACKEND_UNAVAILABLE",
            "The pinned Ed25519 verification backend is unavailable",
        ) from exc
    try:
        Ed25519PublicKey.from_public_bytes(raw_public_key).verify(signature, message)
    except InvalidSignature as exc:
        raise IntegrityError(
            "EVAL_CONTROLLED_SIGNATURE_INVALID",
            "Controlled bundle manifest signature is invalid",
        ) from exc
    except ValueError as exc:
        raise ContractError(
            "EVAL_CONTROLLED_PUBLIC_KEY_INVALID",
            "Trusted Ed25519 public key bytes are invalid",
        ) from exc


def _verify_controlled_local_bundle(
    bundle_root: Path,
    trust_store_path: Path,
    *,
    expected_trust_store_sha256: str,
    expected_manifest_sha256: str | None,
    expected_run_id: str | None,
    expected_attempt_id: str | None,
    expected_dataset_manifest_sha256: str | None,
) -> dict[str, Any]:
    _exact_tree(bundle_root)
    _validate_expected_digest(expected_trust_store_sha256, label="trust-store")
    if expected_manifest_sha256 is not None:
        _validate_expected_digest(expected_manifest_sha256, label="manifest")
    if expected_dataset_manifest_sha256 is not None:
        _validate_expected_digest(expected_dataset_manifest_sha256, label="dataset-manifest")

    manifest_snapshot = snapshot_relative_file(bundle_root, MANIFEST_PATH, max_bytes=MAX_JSON_BYTES)
    manifest = parse_json_model_snapshot(manifest_snapshot, ControlledLocalEvidenceManifest)
    canonical_manifest = canonical_controlled_manifest_bytes(manifest)
    if manifest_snapshot.data != canonical_manifest:
        raise ContractError(
            "EVAL_CONTROLLED_MANIFEST_NOT_CANONICAL",
            "Controlled bundle manifest bytes are not the unique canonical JSON representation",
        )
    if expected_manifest_sha256 is not None and manifest_snapshot.sha256 != expected_manifest_sha256:
        raise IntegrityError(
            "EVAL_CONTROLLED_MANIFEST_IDENTITY_MISMATCH",
            "Controlled bundle manifest does not match its external expected digest",
            details={
                "expected_sha256": expected_manifest_sha256,
                "actual_sha256": manifest_snapshot.sha256,
            },
        )

    trust_snapshot = snapshot_file(trust_store_path, max_bytes=MAX_TRUST_STORE_BYTES)
    if trust_snapshot.sha256 != expected_trust_store_sha256:
        raise IntegrityError(
            "EVAL_CONTROLLED_TRUST_STORE_IDENTITY_MISMATCH",
            "Trust store does not match its external expected digest",
            details={
                "expected_sha256": expected_trust_store_sha256,
                "actual_sha256": trust_snapshot.sha256,
            },
        )
    trust_store = parse_json_model_snapshot(trust_snapshot, Ed25519TrustStore)
    if trust_snapshot.data != canonical_trust_store_bytes(trust_store):
        raise ContractError(
            "EVAL_CONTROLLED_TRUST_STORE_NOT_CANONICAL",
            "Trust-store bytes are not the unique canonical JSON representation",
        )
    matching_keys = [item for item in trust_store.keys if item.key_id == manifest.signing.key_id]
    if not matching_keys:
        raise IntegrityError(
            "EVAL_CONTROLLED_SIGNER_UNKNOWN",
            "Manifest signer key_id is absent from the externally pinned trust store",
        )
    trusted_key = matching_keys[0]
    if trusted_key.status != "active":
        raise IntegrityError(
            "EVAL_CONTROLLED_SIGNER_REVOKED",
            "Manifest signer key is revoked; no trusted signing time is available",
        )
    if trusted_key.public_key_fingerprint_sha256 != manifest.signing.public_key_fingerprint_sha256:
        raise IntegrityError(
            "EVAL_CONTROLLED_SIGNER_FINGERPRINT_MISMATCH",
            "Manifest signer fingerprint differs from the pinned trust-store key",
        )
    raw_public_key = base64.b64decode(trusted_key.public_key_base64, validate=True)
    signature_snapshot = _snapshot_binary(bundle_root, SIGNATURE_PATH, max_bytes=MAX_SIGNATURE_BYTES)
    if signature_snapshot.size_bytes != MAX_SIGNATURE_BYTES:
        raise ContractError(
            "EVAL_CONTROLLED_SIGNATURE_SIZE_INVALID",
            "Ed25519 detached signature must contain exactly 64 raw bytes",
        )
    _verify_signature(
        signature=signature_snapshot.data,
        message=controlled_manifest_signing_message(canonical_manifest),
        raw_public_key=raw_public_key,
    )

    snapshots: dict[str, FileSnapshot] = {}
    actual_members: list[ControlledEvidenceMember] = []
    declared_by_path = {member.path: member for member in manifest.members}
    for relative_path in CONTROLLED_BUNDLE_MEMBER_PATHS:
        maximum = MAX_JSONL_BYTES if relative_path == "public/predictions.jsonl" else MAX_JSON_BYTES
        snapshot = snapshot_relative_file(bundle_root, relative_path, max_bytes=maximum)
        snapshots[relative_path] = snapshot
        actual = _member(relative_path, snapshot)
        actual_members.append(actual)
        declared = declared_by_path[relative_path]
        if actual != declared:
            raise IntegrityError(
                "EVAL_CONTROLLED_MEMBER_IDENTITY_MISMATCH",
                "Controlled bundle member differs from its signed descriptor",
                path=relative_path,
                details={
                    "expected_sha256": declared.sha256,
                    "actual_sha256": actual.sha256,
                    "expected_size": declared.size_bytes,
                    "actual_size": actual.size_bytes,
                },
            )
    actual_member_set_sha256 = controlled_member_set_sha256(actual_members)
    if actual_member_set_sha256 != manifest.member_set_sha256:
        raise IntegrityError(
            "EVAL_CONTROLLED_MEMBER_SET_MISMATCH",
            "Controlled bundle member-set digest is inconsistent",
        )
    actual_by_path = {member.path: member for member in actual_members}
    core_members = [actual_by_path[path] for path in CONTROLLED_CORE_MEMBER_PATHS]
    actual_core_sha256 = controlled_core_result_sha256(core_members)
    if actual_core_sha256 != manifest.core_result_commitment.sha256:
        raise IntegrityError(
            "EVAL_CONTROLLED_CORE_COMMITMENT_MISMATCH",
            "Controlled bundle core result commitment is inconsistent",
        )
    expected_bundle_id = controlled_bundle_id(
        registry_instance_id=manifest.registry_binding.registry_instance_id,
        attempt_id=manifest.attempt_id,
        run_id=manifest.run_id,
        core_result_sha256=actual_core_sha256,
    )
    if manifest.bundle_id != expected_bundle_id:
        raise IntegrityError(
            "EVAL_CONTROLLED_BUNDLE_ID_MISMATCH",
            "Controlled bundle_id is not the deterministic identity of this run",
        )

    run_plan = parse_json_model_snapshot(snapshots["inputs/run-plan.json"], ControlledLocalRunPlan)
    predictions = parse_jsonl_models_snapshot(
        snapshots["public/predictions.jsonl"],
        EvaluationPrediction,
        record_kind="prediction",
        unique_key=lambda item: item.case_id,
        protect_predictions=True,
    )
    attempt = parse_json_model_snapshot(snapshots["registry/attempt.json"], HoldoutAttemptRecord)
    summary = parse_json_model_snapshot(snapshots["results/run-summary.json"], ControlledLocalRunSummary)
    score = parse_json_model_snapshot(snapshots["results/score.json"], ControlledLocalPublicScore)

    prediction_snapshot = snapshots["public/predictions.jsonl"]
    binding = manifest.registry_binding
    checks = (
        (run_plan.run_id, manifest.run_id, "run_plan.run_id"),
        (run_plan.attempt_id, manifest.attempt_id, "run_plan.attempt_id"),
        (run_plan.split, manifest.split, "run_plan.split"),
        (run_plan.dataset_manifest_sha256, manifest.dataset_manifest_sha256, "run_plan.dataset"),
        (run_plan.training_data_manifest_sha256, manifest.training_data_manifest_sha256, "run_plan.training"),
        (run_plan.model_statement_sha256, manifest.model_statement_sha256, "run_plan.model_statement"),
        (run_plan.model_artifact_sha256, manifest.model_artifact_sha256, "run_plan.model_artifact"),
        (run_plan.evaluator_source_sha256, manifest.evaluator_source_sha256, "run_plan.evaluator"),
        (summary.run_id, manifest.run_id, "summary.run_id"),
        (summary.attempt_id, manifest.attempt_id, "summary.attempt_id"),
        (summary.split, manifest.split, "summary.split"),
        (summary.dataset_manifest_sha256, manifest.dataset_manifest_sha256, "summary.dataset"),
        (summary.training_data_manifest_sha256, manifest.training_data_manifest_sha256, "summary.training"),
        (summary.model_statement_sha256, manifest.model_statement_sha256, "summary.model_statement"),
        (summary.model_artifact_sha256, manifest.model_artifact_sha256, "summary.model_artifact"),
        (summary.evaluator_source_sha256, manifest.evaluator_source_sha256, "summary.evaluator"),
        (summary.predictions_sha256, prediction_snapshot.sha256, "summary.predictions_sha256"),
        (summary.predictions_size_bytes, prediction_snapshot.size_bytes, "summary.predictions_size"),
        (score.run_id, manifest.run_id, "score.run_id"),
        (score.attempt_id, manifest.attempt_id, "score.attempt_id"),
        (score.split, manifest.split, "score.split"),
        (score.dataset_manifest_sha256, manifest.dataset_manifest_sha256, "score.dataset"),
        (score.model_artifact_sha256, manifest.model_artifact_sha256, "score.model_artifact"),
        (score.predictions_sha256, prediction_snapshot.sha256, "score.predictions_sha256"),
        (score.predictions_size_bytes, prediction_snapshot.size_bytes, "score.predictions_size"),
        (score.threshold_status, summary.threshold_status, "score.threshold_status"),
        (attempt.registry_instance_id, binding.registry_instance_id, "attempt.registry_instance_id"),
        (attempt.attempt_id, manifest.attempt_id, "attempt.attempt_id"),
        (attempt.run_id, manifest.run_id, "attempt.run_id"),
        (attempt.state, binding.state, "attempt.state"),
        (attempt.consumption_key, binding.consumption_key, "attempt.consumption_key"),
        (attempt.result_sha256, actual_core_sha256, "attempt.result_sha256"),
        (
            attempt.result_commitment_profile,
            binding.result_commitment_profile,
            "attempt.result_commitment_profile",
        ),
        (attempt.model_artifact_sha256, manifest.model_artifact_sha256, "attempt.model_artifact"),
        (attempt.formal_capability_digest, binding.formal_capability_digest, "attempt.capability"),
        (attempt.qa_approval_digest, binding.qa_approval_digest, "attempt.qa_approval"),
        (
            snapshots["registry/attempt.json"].sha256,
            binding.snapshot_sha256,
            "registry.snapshot_sha256",
        ),
    )
    mismatches = [label for actual, expected, label in checks if actual != expected]
    if mismatches:
        raise IntegrityError(
            "EVAL_CONTROLLED_INTERNAL_BINDING_MISMATCH",
            "Signed controlled bundle members do not bind the same run identities",
            details={"fields": mismatches},
        )
    if not predictions:
        raise ContractError(
            "EVAL_CONTROLLED_PREDICTIONS_EMPTY",
            "Controlled bundle must contain at least one prediction",
        )
    if expected_run_id is not None and manifest.run_id != expected_run_id:
        raise IntegrityError("EVAL_CONTROLLED_RUN_ID_MISMATCH", "Controlled run_id differs from the expected value")
    if expected_attempt_id is not None and manifest.attempt_id != expected_attempt_id:
        raise IntegrityError(
            "EVAL_CONTROLLED_ATTEMPT_ID_MISMATCH",
            "Controlled attempt_id differs from the expected value",
        )
    if (
        expected_dataset_manifest_sha256 is not None
        and manifest.dataset_manifest_sha256 != expected_dataset_manifest_sha256
    ):
        raise IntegrityError(
            "EVAL_CONTROLLED_DATASET_IDENTITY_MISMATCH",
            "Controlled dataset manifest differs from the expected value",
        )

    return {
        "schema_version": "evaluation.controlled-local-evidence-verification.v0",
        "ok": True,
        "verdict": "valid_nonformal_signed_evidence",
        "integrity_status": "valid",
        "signature_status": "valid",
        "signer_trust_status": "matched_externally_pinned_trust_store",
        "registry_binding_status": "local_snapshot_consistent",
        "authorization_authenticity": "self_asserted_unsigned",
        "isolation_status": "unverified",
        "formal_execution_completed": False,
        "compliance_claim_eligible": False,
        "replay_status": "not_checked",
        "trusted_timestamp_status": "not_provided",
        "bundle_id": manifest.bundle_id,
        "bundle_manifest_sha256": manifest_snapshot.sha256,
        "signature_sha256": signature_snapshot.sha256,
        "trust_store_sha256": trust_snapshot.sha256,
        "trust_store_id": trust_store.trust_store_id,
        "trust_store_generation": trust_store.generation,
        "signer_key_id": trusted_key.key_id,
        "signer_public_key_fingerprint_sha256": trusted_key.public_key_fingerprint_sha256,
        "registry_instance_id": attempt.registry_instance_id,
        "run_id": manifest.run_id,
        "attempt_id": manifest.attempt_id,
        "split": manifest.split,
        "dataset_manifest_sha256": manifest.dataset_manifest_sha256,
        "core_result_sha256": actual_core_sha256,
        "member_set_sha256": actual_member_set_sha256,
        "member_count": len(actual_members),
        "prediction_count": len(predictions),
        "threshold_status": score.threshold_status,
    }


def verify_controlled_local_evidence_bundle(
    bundle_root: Path | str,
    trust_store_path: Path | str,
    *,
    expected_trust_store_sha256: str,
    expected_manifest_sha256: str | None = None,
    expected_run_id: str | None = None,
    expected_attempt_id: str | None = None,
    expected_dataset_manifest_sha256: str | None = None,
) -> dict[str, Any]:
    """Verify a signed controlled-local bundle without upgrading it to formal evidence."""

    try:
        return _verify_controlled_local_bundle(
            Path(bundle_root),
            Path(trust_store_path),
            expected_trust_store_sha256=expected_trust_store_sha256,
            expected_manifest_sha256=expected_manifest_sha256,
            expected_run_id=expected_run_id,
            expected_attempt_id=expected_attempt_id,
            expected_dataset_manifest_sha256=expected_dataset_manifest_sha256,
        )
    except (ContractError, IntegrityError, ExecutionError):
        raise
    except OSError as exc:
        raise ExecutionError(
            "EVAL_CONTROLLED_BUNDLE_READ_FAILED",
            "A local filesystem operation failed while reading controlled evidence",
            path=str(bundle_root),
            details={"exception_type": type(exc).__name__, "errno": exc.errno},
        ) from exc


__all__ = [
    "canonical_controlled_manifest_bytes",
    "canonical_trust_store_bytes",
    "controlled_bundle_id",
    "controlled_core_result_sha256",
    "controlled_manifest_signing_message",
    "controlled_member_set_sha256",
    "verify_controlled_local_evidence_bundle",
]
