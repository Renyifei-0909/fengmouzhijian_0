from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, FiniteFloat, model_validator

from .schemas import ID_PATTERN, SHA256_PATTERN, StrictModel


EXPECTED_BUNDLE_MEMBERS = (
    "inputs/run-plan.json",
    "public/predictions.jsonl",
    "results/run-summary.json",
    "results/score.json",
)
PUBLIC_SCORE_ASSURANCE_LIMITATIONS = (
    "media_decode_unverified",
    "single_person_crop_provenance_unverified",
    "model_artifact_unverified",
    "blind_isolation_unverified",
    "legal_authorization_unverified",
    "one_shot_holdout_unverified",
    "training_overlap_unverified",
)
PUBLISHED_RUN_ASSURANCE_LIMITATIONS = (
    "development_local_process_only",
    "filesystem_isolation_unverified",
    "network_isolation_unverified",
    "memory_and_process_count_isolation_unverified",
    "runtime_artifact_unpinned",
    "trusted_holdout_broker_unimplemented",
    "development_evidence_unsigned",
    "public_score_replay_unavailable_without_private_labels",
)
DEVELOPMENT_RUNTIME_ENVIRONMENT_KEYS = (
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
)

ClassId = Annotated[str, Field(pattern=ID_PATTERN)]


class EvidenceMember(StrictModel):
    path: str = Field(min_length=1, max_length=500)
    sha256: str = Field(pattern=SHA256_PATTERN)
    size_bytes: int = Field(gt=0, le=64 * 1024 * 1024)


class DevelopmentEvidenceManifest(StrictModel):
    schema_version: Literal["evaluation.development-evidence-manifest.v0"]
    bundle_kind: Literal["development_run_evidence"]
    fixed_tree_version: Literal["v0"]
    run_id: str = Field(pattern=ID_PATTERN)
    mode: Literal["development"]
    split: Literal["train", "validation"]
    run_plan_sha256: str = Field(pattern=SHA256_PATTERN)
    predictions_sha256: str = Field(pattern=SHA256_PATTERN)
    predictions_size_bytes: int = Field(gt=0, le=64 * 1024 * 1024)
    public_cases_sha256: str = Field(pattern=SHA256_PATTERN)
    case_id_roster_sha256: str = Field(pattern=SHA256_PATTERN)
    member_set_sha256: str = Field(pattern=SHA256_PATTERN)
    verification_scope: Literal["integrity_and_internal_consistency_only"]
    authenticity: Literal["unsigned"]
    aggregate_metrics_derived_from_private_labels: Literal[True]
    score_replay: Literal["unavailable_without_private_labels"]
    formal_requested: Literal[False]
    gate_status: Literal["not_eligible"]
    compliance_claim_eligible: Literal[False]
    private_label_records_included: Literal[False]
    raw_logs_included: Literal[False]
    offline_rescore_supported: Literal[False]
    score_recomputed: Literal[False]
    members: list[EvidenceMember] = Field(min_length=4, max_length=4)

    @model_validator(mode="after")
    def validate_fixed_members(self) -> "DevelopmentEvidenceManifest":
        paths = tuple(member.path for member in self.members)
        if paths != EXPECTED_BUNDLE_MEMBERS:
            raise ValueError("members must be the four fixed paths in lexical order")
        return self


class PublicDatasetIdentity(StrictModel):
    dataset_id: str = Field(pattern=ID_PATTERN)
    version: str = Field(min_length=1, max_length=64)
    status: Literal["draft", "frozen"]
    manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    manifest_size_bytes: int = Field(gt=0, le=2 * 1024 * 1024)
    cases_sha256: str = Field(pattern=SHA256_PATTERN)
    cases_size_bytes: int = Field(gt=0, le=64 * 1024 * 1024)
    split_assignment_sha256: str = Field(pattern=SHA256_PATTERN)
    metric_spec_sha256: str = Field(pattern=SHA256_PATTERN)


class PublicModelIdentity(StrictModel):
    adapter_name: str = Field(min_length=1, max_length=200)
    adapter_version: str = Field(min_length=1, max_length=128)
    model_name: str = Field(min_length=1, max_length=200)
    model_version: str = Field(min_length=1, max_length=128)
    artifact_sha256: str = Field(pattern=SHA256_PATTERN)
    statement_sha256: str = Field(pattern=SHA256_PATTERN)
    statement_size_bytes: int = Field(gt=0, le=2 * 1024 * 1024)


class WilsonInterval(StrictModel):
    level: FiniteFloat = Field(ge=0.0, le=1.0)
    lower: FiniteFloat = Field(ge=0.0, le=1.0)
    upper: FiniteFloat = Field(ge=0.0, le=1.0)


class AccuracyMetric(StrictModel):
    correct: int = Field(ge=0)
    total: int = Field(gt=0)
    value: FiniteFloat = Field(ge=0.0, le=1.0)
    wilson_95: WilsonInterval


class ClassMetrics(StrictModel):
    tp: int = Field(ge=0)
    fp: int = Field(ge=0)
    fn: int = Field(ge=0)
    tn: int = Field(ge=0)
    support: int = Field(ge=0)
    predicted_positive: int = Field(ge=0)
    zero_precision_denominator: bool
    precision: FiniteFloat = Field(ge=0.0, le=1.0)
    recall: FiniteFloat = Field(ge=0.0, le=1.0)
    f1: FiniteFloat = Field(ge=0.0, le=1.0)


class AggregateMetrics(StrictModel):
    precision: FiniteFloat = Field(ge=0.0, le=1.0)
    recall: FiniteFloat = Field(ge=0.0, le=1.0)
    f1: FiniteFloat = Field(ge=0.0, le=1.0)


class ThresholdMetric(StrictModel):
    metric: Literal["accuracy"]
    operator: Literal[">="]
    value: Literal["0.85"]
    ci_policy: Literal["report_only", "lower_bound"]
    point_passed: bool
    ci_lower_passed: bool
    passed: bool


class PublicMetrics(StrictModel):
    class_order: list[ClassId] = Field(min_length=2, max_length=100)
    confusion_matrix: list[list[int]] = Field(min_length=2, max_length=100)
    accuracy: AccuracyMetric
    per_class: dict[ClassId, ClassMetrics] = Field(min_length=2, max_length=100)
    balanced_accuracy: FiniteFloat = Field(ge=0.0, le=1.0)
    macro: AggregateMetrics
    micro: AggregateMetrics
    weighted: AggregateMetrics
    threshold: ThresholdMetric

    @model_validator(mode="after")
    def validate_shape(self) -> "PublicMetrics":
        if len(self.class_order) != len(set(self.class_order)):
            raise ValueError("class_order must be unique")
        if set(self.per_class) != set(self.class_order):
            raise ValueError("per_class must contain exactly class_order")
        dimension = len(self.class_order)
        if len(self.confusion_matrix) != dimension or any(
            len(row) != dimension or any(value < 0 for value in row)
            for row in self.confusion_matrix
        ):
            raise ValueError("confusion_matrix must be a non-negative square matrix matching class_order")
        return self


class DevelopmentPublicScore(StrictModel):
    schema_version: Literal["evaluation.development-public-score.v0"]
    source_schema_version: Literal["evaluation.score.v0"]
    ok: Literal[True]
    dataset: PublicDatasetIdentity
    model: PublicModelIdentity
    predictions_sha256: str = Field(pattern=SHA256_PATTERN)
    predictions_size_bytes: int = Field(gt=0, le=64 * 1024 * 1024)
    split: Literal["train", "validation"]
    formal_requested: Literal[False]
    gate_status: Literal["not_eligible"]
    compliance_claim_eligible: Literal[False]
    private_label_records_included: Literal[False]
    offline_rescore_supported: Literal[False]
    score_recomputed: Literal[False]
    structural_gate_status: Literal["passed"]
    threshold_status: Literal["passed", "failed"]
    threshold_reasons: list[Literal["EVAL_THRESHOLD_NOT_MET", "EVAL_CI_GATE_NOT_MET"]] = Field(max_length=2)
    assurance_limitations: list[Literal[
        "media_decode_unverified",
        "single_person_crop_provenance_unverified",
        "model_artifact_unverified",
        "blind_isolation_unverified",
        "legal_authorization_unverified",
        "one_shot_holdout_unverified",
        "training_overlap_unverified",
    ]] = Field(min_length=7, max_length=7)
    metrics: PublicMetrics

    @model_validator(mode="after")
    def validate_threshold_status(self) -> "DevelopmentPublicScore":
        expected = "passed" if self.metrics.threshold.passed else "failed"
        if self.threshold_status != expected:
            raise ValueError("threshold_status must match metrics.threshold.passed")
        if tuple(self.assurance_limitations) != PUBLIC_SCORE_ASSURANCE_LIMITATIONS:
            raise ValueError("public score assurance_limitations must be the fixed v0 list")
        return self


class DevelopmentRuntimeIdentity(StrictModel):
    python_version: str = Field(min_length=1, max_length=128)
    implementation: str = Field(min_length=1, max_length=128)
    platform: str = Field(min_length=1, max_length=128)
    environment_keys: list[Literal[
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
    ]] = Field(min_length=10, max_length=10)

    @model_validator(mode="after")
    def validate_environment_keys(self) -> "DevelopmentRuntimeIdentity":
        if tuple(self.environment_keys) != DEVELOPMENT_RUNTIME_ENVIRONMENT_KEYS:
            raise ValueError("environment_keys must be the fixed development runner allowlist")
        return self


class DevelopmentRunSummary(StrictModel):
    schema_version: Literal["evaluation.development-run-summary.v0"]
    source_schema_version: Literal["evaluation.development-run.v0"]
    ok: Literal[True]
    run_id: str = Field(pattern=ID_PATTERN)
    mode: Literal["development"]
    runner: Literal["local_process"]
    protocol: Literal["evaluation.predictor-cli.v0"]
    split: Literal["train", "validation"]
    formal_requested: Literal[False]
    gate_status: Literal["not_eligible"]
    compliance_claim_eligible: Literal[False]
    run_plan_sha256: str = Field(pattern=SHA256_PATTERN)
    run_plan_size_bytes: int = Field(gt=0, le=2 * 1024 * 1024)
    evaluator_source_sha256: str = Field(pattern=SHA256_PATTERN)
    training_data_manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    dataset_manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    model_statement_sha256: str = Field(pattern=SHA256_PATTERN)
    model_artifact_sha256: str = Field(pattern=SHA256_PATTERN)
    predictions_sha256: str = Field(pattern=SHA256_PATTERN)
    predictions_size_bytes: int = Field(gt=0, le=64 * 1024 * 1024)
    public_score_sha256: str = Field(pattern=SHA256_PATTERN)
    public_score_size_bytes: int = Field(gt=0, le=2 * 1024 * 1024)
    runtime: DevelopmentRuntimeIdentity
    process_return_code: Literal[0]
    process_duration_ms: int = Field(ge=0)
    inference_case_count: int = Field(gt=0)
    public_cases_sha256: str = Field(pattern=SHA256_PATTERN)
    case_id_roster_sha256: str = Field(pattern=SHA256_PATTERN)
    inference_asset_count: int = Field(ge=0)
    inference_asset_size_bytes: int = Field(ge=0)
    private_label_records_included: Literal[False]
    raw_logs_included: Literal[False]
    offline_rescore_supported: Literal[False]
    score_recomputed: Literal[False]
    assurance_limitations: list[Literal[
        "development_local_process_only",
        "filesystem_isolation_unverified",
        "network_isolation_unverified",
        "memory_and_process_count_isolation_unverified",
        "runtime_artifact_unpinned",
        "trusted_holdout_broker_unimplemented",
        "development_evidence_unsigned",
        "public_score_replay_unavailable_without_private_labels",
    ]] = Field(min_length=8, max_length=8)

    @model_validator(mode="after")
    def validate_assurance_limitations(self) -> "DevelopmentRunSummary":
        if tuple(self.assurance_limitations) != PUBLISHED_RUN_ASSURANCE_LIMITATIONS:
            raise ValueError("run assurance_limitations must be the fixed published-evidence list")
        return self


__all__ = [
    "DevelopmentEvidenceManifest",
    "DevelopmentPublicScore",
    "DevelopmentRuntimeIdentity",
    "DevelopmentRunSummary",
    "EvidenceMember",
    "EXPECTED_BUNDLE_MEMBERS",
    "DEVELOPMENT_RUNTIME_ENVIRONMENT_KEYS",
    "PUBLIC_SCORE_ASSURANCE_LIMITATIONS",
    "PUBLISHED_RUN_ASSURANCE_LIMITATIONS",
]
