from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from .schemas import ID_PATTERN, SHA256_PATTERN, StrictModel


HoldoutSplit = Literal["gate_holdout", "final_holdout"]


class FormalEvaluationCapability(StrictModel):
    schema_version: Literal["evaluation.formal-capability.v0"]
    capability_id: str = Field(pattern=ID_PATTERN)
    capability_digest: str = Field(pattern=SHA256_PATTERN)
    dataset_manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    split: HoldoutSplit
    policy_generation: int = Field(ge=0, le=2**31 - 1)
    actor: str = Field(min_length=1, max_length=200)
    scope: Literal["formal_holdout_reservation"]


class QAHoldoutApproval(StrictModel):
    schema_version: Literal["evaluation.qa-holdout-approval.v0"]
    approval_id: str = Field(pattern=ID_PATTERN)
    approval_digest: str = Field(pattern=SHA256_PATTERN)
    approval_kind: Literal["initial_release", "incident_lock", "incident_retry"]
    dataset_manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    split: HoldoutSplit
    policy_generation: int = Field(ge=0, le=2**31 - 1)
    reason: str = Field(min_length=1, max_length=1000)
    actor: str = Field(min_length=1, max_length=200)
    predecessor_attempt_id: str | None = Field(default=None, pattern=ID_PATTERN)

    @model_validator(mode="after")
    def validate_predecessor(self) -> "QAHoldoutApproval":
        if self.approval_kind == "initial_release" and self.predecessor_attempt_id is not None:
            raise ValueError("initial_release must not identify a predecessor attempt")
        if self.approval_kind in {"incident_lock", "incident_retry"} and self.predecessor_attempt_id is None:
            raise ValueError("incident approvals must identify a predecessor attempt")
        return self


class HoldoutReservationRequest(StrictModel):
    schema_version: Literal["evaluation.holdout-reservation.v0"]
    attempt_id: str = Field(pattern=ID_PATTERN)
    run_id: str = Field(pattern=ID_PATTERN)
    dataset_manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    split: HoldoutSplit
    policy_generation: int = Field(ge=0, le=2**31 - 1)
    model_artifact_sha256: str = Field(pattern=SHA256_PATTERN)
    formal_capability: FormalEvaluationCapability
    qa_approval: QAHoldoutApproval

    @model_validator(mode="after")
    def validate_bindings(self) -> "HoldoutReservationRequest":
        expected = (self.dataset_manifest_sha256, self.split, self.policy_generation)
        capability_key = (
            self.formal_capability.dataset_manifest_sha256,
            self.formal_capability.split,
            self.formal_capability.policy_generation,
        )
        approval_key = (
            self.qa_approval.dataset_manifest_sha256,
            self.qa_approval.split,
            self.qa_approval.policy_generation,
        )
        if capability_key != expected:
            raise ValueError("formal capability must bind the requested consumption key")
        if approval_key != expected:
            raise ValueError("QA approval must bind the requested consumption key")
        return self


class HoldoutConsumptionKey(StrictModel):
    dataset_manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    split: HoldoutSplit
    policy_generation: int = Field(ge=0, le=2**31 - 1)
    key_sha256: str = Field(pattern=SHA256_PATTERN)


class HoldoutReservationReceipt(StrictModel):
    schema_version: Literal["evaluation.holdout-reservation-receipt.v0"]
    ok: Literal[True]
    registry_instance_id: str = Field(pattern=ID_PATTERN)
    attempt_id: str = Field(pattern=ID_PATTERN)
    run_id: str = Field(pattern=ID_PATTERN)
    consumption_key: HoldoutConsumptionKey
    state: Literal["reserved"]
    reservation_persisted: Literal[True]
    model_artifact_sha256: str = Field(pattern=SHA256_PATTERN)
    model_identity_part_of_consumption_key: Literal[False]
    authorization_authenticity: Literal["self_asserted_unsigned"]
    formal_execution_completed: Literal[False]
    compliance_claim_eligible: Literal[False]


class HoldoutAttemptRecord(StrictModel):
    schema_version: Literal["evaluation.holdout-attempt.v0"]
    registry_instance_id: str = Field(pattern=ID_PATTERN)
    attempt_id: str = Field(pattern=ID_PATTERN)
    run_id: str = Field(pattern=ID_PATTERN)
    consumption_key: HoldoutConsumptionKey
    state: Literal["reserved", "exposure_committed", "consumed", "incident_review"]
    model_artifact_sha256: str = Field(pattern=SHA256_PATTERN)
    formal_capability_id: str = Field(pattern=ID_PATTERN)
    formal_capability_digest: str = Field(pattern=SHA256_PATTERN)
    qa_approval_id: str = Field(pattern=ID_PATTERN)
    qa_approval_digest: str = Field(pattern=SHA256_PATTERN)
    qa_approval_kind: Literal["initial_release", "incident_retry"]
    qa_approval_reason: str = Field(min_length=1, max_length=1000)
    qa_approval_actor: str = Field(min_length=1, max_length=200)
    predecessor_attempt_id: str | None = Field(default=None, pattern=ID_PATTERN)
    reserved_at: str = Field(min_length=1, max_length=64)
    exposure_committed_at: str | None = Field(default=None, min_length=1, max_length=64)
    consumed_at: str | None = Field(default=None, min_length=1, max_length=64)
    result_sha256: str | None = Field(default=None, pattern=SHA256_PATTERN)
    result_commitment_profile: Literal["evaluation.controlled-run-core-member-set.v0"] | None = None
    incident_review_at: str | None = Field(default=None, min_length=1, max_length=64)
    authorization_authenticity: Literal["self_asserted_unsigned"]
    formal_execution_completed: Literal[False]
    compliance_claim_eligible: Literal[False]


__all__ = [
    "FormalEvaluationCapability",
    "HoldoutAttemptRecord",
    "HoldoutConsumptionKey",
    "HoldoutReservationReceipt",
    "HoldoutReservationRequest",
    "HoldoutSplit",
    "QAHoldoutApproval",
]
