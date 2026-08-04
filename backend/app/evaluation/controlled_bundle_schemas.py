from __future__ import annotations

import base64
import hashlib
from pathlib import PurePosixPath
from typing import Literal

from pydantic import Field, field_validator, model_validator

from .registry_schemas import HoldoutConsumptionKey
from .schemas import ID_PATTERN, SHA256_PATTERN, StrictModel


CONTROLLED_CORE_MEMBER_PATHS = (
    "inputs/run-plan.json",
    "public/predictions.jsonl",
    "results/run-summary.json",
    "results/score.json",
)
CONTROLLED_BUNDLE_MEMBER_PATHS = (
    "inputs/run-plan.json",
    "public/predictions.jsonl",
    "registry/attempt.json",
    "results/run-summary.json",
    "results/score.json",
)
CONTROLLED_ASSURANCE_LIMITATIONS = (
    "local_single_host_registry_only",
    "authorization_self_asserted_unsigned",
    "trusted_holdout_broker_unimplemented",
    "filesystem_isolation_unverified",
    "network_isolation_unverified",
    "process_isolation_unverified",
    "private_label_role_separation_unverified",
    "runtime_artifact_unpinned",
    "training_overlap_unverified",
    "trusted_timestamp_unavailable",
    "public_score_replay_unavailable_without_private_labels",
)


class ControlledEvidenceMember(StrictModel):
    path: str = Field(min_length=1, max_length=500)
    sha256: str = Field(pattern=SHA256_PATTERN)
    size_bytes: int = Field(gt=0, le=64 * 1024 * 1024)

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        pure = PurePosixPath(value)
        if (
            "\x00" in value
            or "\\" in value
            or pure.is_absolute()
            or pure.as_posix() != value
            or any(part in {"", ".", ".."} for part in pure.parts)
        ):
            raise ValueError("member path must be normalized relative POSIX syntax")
        return value


class CoreResultCommitment(StrictModel):
    profile: Literal["evaluation.controlled-run-core-member-set.v0"]
    sha256: str = Field(pattern=SHA256_PATTERN)


class ControlledRegistryBinding(StrictModel):
    registry_schema_version: Literal["evaluation.holdout-registry.v0"]
    registry_instance_id: str = Field(pattern=ID_PATTERN)
    snapshot_path: Literal["registry/attempt.json"]
    snapshot_sha256: str = Field(pattern=SHA256_PATTERN)
    state: Literal["consumed"]
    consumption_key: HoldoutConsumptionKey
    result_sha256: str = Field(pattern=SHA256_PATTERN)
    result_commitment_profile: Literal["evaluation.controlled-run-core-member-set.v0"]
    formal_capability_digest: str = Field(pattern=SHA256_PATTERN)
    qa_approval_digest: str = Field(pattern=SHA256_PATTERN)
    authorization_authenticity: Literal["self_asserted_unsigned"]
    formal_execution_completed: Literal[False]
    compliance_claim_eligible: Literal[False]


class ControlledSigningDescriptor(StrictModel):
    algorithm: Literal["ed25519"]
    signature_path: Literal["bundle-manifest.ed25519"]
    signature_encoding: Literal["raw_64_bytes"]
    message_profile: Literal["evaluation.controlled-local-manifest-signature.v0"]
    manifest_canonicalization: Literal["evaluation.canonical-json.v0"]
    key_id: str = Field(pattern=ID_PATTERN)
    public_key_fingerprint_sha256: str = Field(pattern=SHA256_PATTERN)
    required_key_role: Literal["controlled_run_bundle_signer"]
    trust_source_required: Literal["external"]
    time_assurance: Literal["not_provided"]


class ControlledLocalEvidenceManifest(StrictModel):
    schema_version: Literal["evaluation.controlled-local-evidence-manifest.v0"]
    bundle_kind: Literal["controlled_local_run_evidence"]
    fixed_tree_version: Literal["v0"]
    bundle_id: str = Field(pattern=r"^crb0:[0-9a-f]{64}$")
    run_id: str = Field(pattern=ID_PATTERN)
    attempt_id: str = Field(pattern=ID_PATTERN)
    mode: Literal["controlled_local"]
    execution_boundary: Literal["single_host_local_registry"]
    split: Literal["gate_holdout", "final_holdout"]
    dataset_manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    training_data_manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    model_statement_sha256: str = Field(pattern=SHA256_PATTERN)
    model_artifact_sha256: str = Field(pattern=SHA256_PATTERN)
    evaluator_source_sha256: str = Field(pattern=SHA256_PATTERN)
    core_result_commitment: CoreResultCommitment
    registry_binding: ControlledRegistryBinding
    signing: ControlledSigningDescriptor
    verification_scope: Literal["integrity_origin_and_local_registry_binding_only"]
    formal_execution_completed: Literal[False]
    gate_status: Literal["not_eligible"]
    compliance_claim_eligible: Literal[False]
    isolation_status: Literal["unverified"]
    private_label_records_included: Literal[False]
    raw_logs_included: Literal[False]
    score_recomputed: Literal[False]
    assurance_limitations: list[Literal[
        "local_single_host_registry_only",
        "authorization_self_asserted_unsigned",
        "trusted_holdout_broker_unimplemented",
        "filesystem_isolation_unverified",
        "network_isolation_unverified",
        "process_isolation_unverified",
        "private_label_role_separation_unverified",
        "runtime_artifact_unpinned",
        "training_overlap_unverified",
        "trusted_timestamp_unavailable",
        "public_score_replay_unavailable_without_private_labels",
    ]] = Field(min_length=11, max_length=11)
    member_set_sha256: str = Field(pattern=SHA256_PATTERN)
    members: list[ControlledEvidenceMember] = Field(min_length=5, max_length=5)

    @model_validator(mode="after")
    def validate_fixed_contract(self) -> "ControlledLocalEvidenceManifest":
        if tuple(member.path for member in self.members) != CONTROLLED_BUNDLE_MEMBER_PATHS:
            raise ValueError("members must be the five fixed paths in lexical order")
        if tuple(self.assurance_limitations) != CONTROLLED_ASSURANCE_LIMITATIONS:
            raise ValueError("assurance_limitations must be the fixed controlled-local list")
        binding = self.registry_binding
        if (
            binding.consumption_key.dataset_manifest_sha256 != self.dataset_manifest_sha256
            or binding.consumption_key.split != self.split
        ):
            raise ValueError("registry binding must match the dataset and split")
        if binding.result_sha256 != self.core_result_commitment.sha256:
            raise ValueError("registry result must equal the core member-set commitment")
        return self


class ControlledLocalRunPlan(StrictModel):
    schema_version: Literal["evaluation.controlled-local-run-plan.v0"]
    run_id: str = Field(pattern=ID_PATTERN)
    attempt_id: str = Field(pattern=ID_PATTERN)
    mode: Literal["controlled_local"]
    split: Literal["gate_holdout", "final_holdout"]
    dataset_manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    training_data_manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    model_statement_sha256: str = Field(pattern=SHA256_PATTERN)
    model_artifact_sha256: str = Field(pattern=SHA256_PATTERN)
    evaluator_source_sha256: str = Field(pattern=SHA256_PATTERN)
    formal_requested: Literal[False]
    compliance_claim_eligible: Literal[False]


class ControlledLocalRunSummary(StrictModel):
    schema_version: Literal["evaluation.controlled-local-run-summary.v0"]
    run_id: str = Field(pattern=ID_PATTERN)
    attempt_id: str = Field(pattern=ID_PATTERN)
    mode: Literal["controlled_local"]
    split: Literal["gate_holdout", "final_holdout"]
    dataset_manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    training_data_manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    model_statement_sha256: str = Field(pattern=SHA256_PATTERN)
    model_artifact_sha256: str = Field(pattern=SHA256_PATTERN)
    evaluator_source_sha256: str = Field(pattern=SHA256_PATTERN)
    predictions_sha256: str = Field(pattern=SHA256_PATTERN)
    predictions_size_bytes: int = Field(gt=0, le=64 * 1024 * 1024)
    threshold_status: Literal["passed", "failed"]
    execution_status: Literal["completed"]
    formal_execution_completed: Literal[False]
    compliance_claim_eligible: Literal[False]


class ControlledLocalPublicScore(StrictModel):
    schema_version: Literal["evaluation.controlled-local-public-score.v0"]
    run_id: str = Field(pattern=ID_PATTERN)
    attempt_id: str = Field(pattern=ID_PATTERN)
    split: Literal["gate_holdout", "final_holdout"]
    dataset_manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    model_artifact_sha256: str = Field(pattern=SHA256_PATTERN)
    predictions_sha256: str = Field(pattern=SHA256_PATTERN)
    predictions_size_bytes: int = Field(gt=0, le=64 * 1024 * 1024)
    threshold_status: Literal["passed", "failed"]
    formal_requested: Literal[False]
    gate_status: Literal["not_eligible"]
    score_recomputed: Literal[False]
    private_label_records_included: Literal[False]
    compliance_claim_eligible: Literal[False]


class TrustedEd25519Key(StrictModel):
    key_id: str = Field(pattern=ID_PATTERN)
    algorithm: Literal["ed25519"]
    public_key_encoding: Literal["raw_base64"]
    public_key_base64: str = Field(min_length=44, max_length=44)
    public_key_fingerprint_sha256: str = Field(pattern=SHA256_PATTERN)
    roles: list[Literal["controlled_run_bundle_signer"]] = Field(min_length=1, max_length=1)
    status: Literal["active", "revoked"]

    @field_validator("public_key_base64")
    @classmethod
    def validate_public_key(cls, value: str) -> str:
        try:
            decoded = base64.b64decode(value, validate=True)
        except (ValueError, base64.binascii.Error) as exc:
            raise ValueError("public key must use canonical base64") from exc
        if len(decoded) != 32 or base64.b64encode(decoded).decode("ascii") != value:
            raise ValueError("public key must be canonical base64 for exactly 32 bytes")
        return value

    @model_validator(mode="after")
    def validate_fingerprint_and_role(self) -> "TrustedEd25519Key":
        decoded = base64.b64decode(self.public_key_base64, validate=True)
        if hashlib.sha256(decoded).hexdigest() != self.public_key_fingerprint_sha256:
            raise ValueError("public key fingerprint does not match raw key bytes")
        if self.roles != ["controlled_run_bundle_signer"]:
            raise ValueError("trust key must have exactly the controlled bundle signer role")
        return self


class Ed25519TrustStore(StrictModel):
    schema_version: Literal["evaluation.ed25519-trust-store.v0"]
    trust_store_id: str = Field(pattern=ID_PATTERN)
    generation: int = Field(ge=1, le=2**31 - 1)
    keys: list[TrustedEd25519Key] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def validate_unique_keys(self) -> "Ed25519TrustStore":
        key_ids = [item.key_id for item in self.keys]
        fingerprints = [item.public_key_fingerprint_sha256 for item in self.keys]
        if len(key_ids) != len(set(key_ids)):
            raise ValueError("trust-store key_id values must be unique")
        if len(fingerprints) != len(set(fingerprints)):
            raise ValueError("trust-store fingerprints must be unique")
        return self


__all__ = [
    "CONTROLLED_ASSURANCE_LIMITATIONS",
    "CONTROLLED_BUNDLE_MEMBER_PATHS",
    "CONTROLLED_CORE_MEMBER_PATHS",
    "ControlledEvidenceMember",
    "ControlledLocalEvidenceManifest",
    "ControlledLocalPublicScore",
    "ControlledLocalRunPlan",
    "ControlledLocalRunSummary",
    "Ed25519TrustStore",
]
