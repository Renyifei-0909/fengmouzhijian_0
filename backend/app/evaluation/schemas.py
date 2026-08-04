from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, field_validator, model_validator


SHA256_PATTERN = r"^[0-9a-f]{64}$"
ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$"

SplitName = Literal["train", "validation", "gate_holdout", "final_holdout"]
GroupKey = Literal[
    "source_lineage_id",
    "capture_session_id",
    "engineering_entity_id",
    "site_group_id",
    "camera_group_id",
    "person_group_id",
    "project_group_id",
]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class Segment(StrictModel):
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)

    @model_validator(mode="after")
    def validate_bounds(self) -> "Segment":
        if self.end_ms <= self.start_ms:
            raise ValueError("end_ms must be greater than start_ms")
        return self


class CaseInput(StrictModel):
    role: Literal["primary_media"]
    asset_id: str = Field(pattern=ID_PATTERN)
    relative_path: str = Field(min_length=1, max_length=500)
    sha256: str = Field(pattern=SHA256_PATTERN)
    size_bytes: int = Field(gt=0)
    content_type: str = Field(min_length=1, max_length=120)
    segment: Segment | None = None


class EngineeringContext(StrictModel):
    project_key: str = Field(min_length=1, max_length=128)
    site_key: str = Field(min_length=1, max_length=128)
    procedure_code: str = Field(min_length=1, max_length=128)
    baseline_version: str = Field(min_length=1, max_length=128)
    baseline_sha256: str = Field(pattern=SHA256_PATTERN)


class CaseGroups(StrictModel):
    source_lineage_id: str = Field(min_length=1, max_length=128)
    capture_session_id: str = Field(min_length=1, max_length=128)
    engineering_entity_id: str = Field(min_length=1, max_length=128)
    site_group_id: str = Field(min_length=1, max_length=128)
    camera_group_id: str = Field(min_length=1, max_length=128)
    person_group_id: str = Field(min_length=1, max_length=128)
    project_group_id: str | None = Field(default=None, min_length=1, max_length=128)


class EvaluationCase(StrictModel):
    schema_version: Literal["evaluation.case.v0"]
    case_id: str = Field(pattern=ID_PATTERN)
    task_type: Literal["violation_event_classification"]
    split: SplitName
    source_id: str = Field(pattern=ID_PATTERN)
    inputs: list[CaseInput] = Field(min_length=1, max_length=10)
    engineering_context: EngineeringContext
    groups: CaseGroups

    @model_validator(mode="after")
    def exactly_one_primary_media(self) -> "EvaluationCase":
        if sum(item.role == "primary_media" for item in self.inputs) != 1:
            raise ValueError("exactly one primary_media input is required")
        primary = next(item for item in self.inputs if item.role == "primary_media")
        if primary.segment is None:
            raise ValueError("event_window cases require a primary media segment")
        return self


class AnnotationStatement(StrictModel):
    spec_version: str = Field(min_length=1, max_length=128)
    status: Literal["adjudicated"]
    record_sha256: str = Field(pattern=SHA256_PATTERN)


class ViolationTruth(StrictModel):
    kind: Literal["violation_single_label"]
    label: str = Field(pattern=ID_PATTERN)


class EvaluationLabel(StrictModel):
    schema_version: Literal["evaluation.label.v0"]
    case_id: str = Field(pattern=ID_PATTERN)
    annotation: AnnotationStatement
    truth: ViolationTruth


class ViolationPrediction(StrictModel):
    kind: Literal["violation_single_label"]
    label: str = Field(pattern=ID_PATTERN)
    confidence: FiniteFloat | None = Field(default=None, ge=0.0, le=1.0)
    scores: dict[str, FiniteFloat] | None = None

    @field_validator("scores")
    @classmethod
    def validate_scores(cls, value: dict[str, float] | None) -> dict[str, float] | None:
        if value is None:
            return None
        if not value:
            raise ValueError("scores must not be empty when supplied")
        for class_id, score in value.items():
            if not class_id or len(class_id) > 128:
                raise ValueError("score class identifiers must be non-empty and at most 128 characters")
            if not 0.0 <= score <= 1.0:
                raise ValueError("scores must be within [0, 1]")
        return value


class EvaluationPrediction(StrictModel):
    schema_version: Literal["evaluation.prediction.v0"]
    case_id: str = Field(pattern=ID_PATTERN)
    output: ViolationPrediction


class ArtifactDescriptor(StrictModel):
    path: str = Field(min_length=1, max_length=500)
    sha256: str = Field(pattern=SHA256_PATTERN)
    size_bytes: int = Field(gt=0)
    line_count: int = Field(ge=0)


class DatasetArtifacts(StrictModel):
    cases: ArtifactDescriptor
    labels_private: ArtifactDescriptor


class ClassDefinition(StrictModel):
    id: str = Field(pattern=ID_PATTERN)
    name: str = Field(min_length=1, max_length=200)


class AcceptanceRule(StrictModel):
    operator: Literal[">="]
    # Canonical strings are deliberate: json.loads would round a sufficiently
    # long JSON number before schema validation could detect the difference.
    threshold: Literal["0.85"]
    ci_level: Literal["0.95"]
    ci_policy: Literal["report_only", "lower_bound"] = "report_only"


class ViolationTask(StrictModel):
    type: Literal["violation_event_classification"]
    case_unit: Literal["event_window"]
    classes: list[ClassDefinition] = Field(min_length=2, max_length=100)
    negative_class: str = Field(pattern=ID_PATTERN)
    multi_label: Literal[False]
    label_spec_version: str = Field(min_length=1, max_length=128)
    label_spec_sha256: str = Field(pattern=SHA256_PATTERN)
    metric_spec_version: str = Field(min_length=1, max_length=128)
    metric_spec_sha256: str = Field(pattern=SHA256_PATTERN)
    primary_metric: Literal["accuracy"]
    acceptance: AcceptanceRule
    minimum_cases_total: int = Field(ge=1)
    minimum_cases_per_class: dict[str, int]

    @model_validator(mode="after")
    def validate_class_contract(self) -> "ViolationTask":
        class_ids = [item.id for item in self.classes]
        if len(class_ids) != len(set(class_ids)):
            raise ValueError("class identifiers must be unique")
        if self.negative_class not in class_ids:
            raise ValueError("negative_class must be one of the declared classes")
        if set(self.minimum_cases_per_class) != set(class_ids):
            raise ValueError("minimum_cases_per_class must contain exactly the declared classes")
        if any(value < 1 for value in self.minimum_cases_per_class.values()):
            raise ValueError("minimum_cases_per_class values must be at least one")
        return self


class SplitDescriptor(StrictModel):
    name: SplitName
    case_count: int = Field(ge=0)


class SplitPolicy(StrictModel):
    group_keys: list[GroupKey] = Field(min_length=1, max_length=7)
    transitive_closure: Literal[True]
    exact_asset_hash_disjoint: Literal[True]
    assignment_sha256: str = Field(pattern=SHA256_PATTERN)
    generalization_unit: Literal["capture_session", "engineering_entity", "project"]

    @model_validator(mode="after")
    def validate_group_keys(self) -> "SplitPolicy":
        if len(self.group_keys) != len(set(self.group_keys)):
            raise ValueError("group_keys must be unique")
        required = {"source_lineage_id", "capture_session_id", "engineering_entity_id"}
        if not required.issubset(set(self.group_keys)):
            raise ValueError("source_lineage_id, capture_session_id, and engineering_entity_id are required")
        if self.generalization_unit == "project" and "project_group_id" not in self.group_keys:
            raise ValueError("project_group_id is required for project generalization")
        return self


SourceOrigin = Literal[
    "field_real",
    "historical_real",
    "staged_real",
    "authorized_simulation",
    "sample_scenario",
    "mock",
    "demo_fixture",
    "synthetic_placeholder",
]
AllowedUse = Literal[
    "training",
    "evaluation",
    "competition_submission",
    "remote_processing",
    "redistribution",
]


class DatasetSource(StrictModel):
    source_id: str = Field(pattern=ID_PATTERN)
    origin: SourceOrigin
    rights_holder: str = Field(min_length=1, max_length=300)
    acquisition_method: str = Field(min_length=1, max_length=500)
    allowed_uses: list[AllowedUse] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_allowed_uses(self) -> "DatasetSource":
        if len(self.allowed_uses) != len(set(self.allowed_uses)):
            raise ValueError("allowed_uses must be unique")
        return self


class FormalPolicy(StrictModel):
    formal_eligible: bool
    mock_allowed: bool
    fixture_allowed: bool
    synthetic_placeholder_allowed: bool


class EvaluationDatasetManifest(StrictModel):
    schema_version: Literal["evaluation.dataset.v0"]
    dataset_id: str = Field(pattern=ID_PATTERN)
    version: str = Field(min_length=1, max_length=64)
    status: Literal["draft", "frozen"]
    task: ViolationTask
    artifacts: DatasetArtifacts
    splits: list[SplitDescriptor] = Field(min_length=1, max_length=4)
    split_policy: SplitPolicy
    sources: list[DatasetSource] = Field(min_length=1)
    formal_policy: FormalPolicy

    @model_validator(mode="after")
    def validate_manifest_collections(self) -> "EvaluationDatasetManifest":
        split_names = [item.name for item in self.splits]
        if len(split_names) != len(set(split_names)):
            raise ValueError("split names must be unique")
        source_ids = [item.source_id for item in self.sources]
        if len(source_ids) != len(set(source_ids)):
            raise ValueError("source identifiers must be unique")
        return self


class EvaluationModelStatement(StrictModel):
    schema_version: Literal["evaluation.model.v0"]
    adapter_name: str = Field(pattern=ID_PATTERN)
    adapter_version: str = Field(min_length=1, max_length=128)
    implementation_kind: Literal["model", "rule_engine", "hybrid", "stub", "fixture", "placeholder"]
    synthetic: bool
    model_name: str = Field(min_length=1, max_length=200)
    model_version: str = Field(min_length=1, max_length=128)
    artifact_sha256: str = Field(pattern=SHA256_PATTERN)


__all__ = [
    "EvaluationCase",
    "EvaluationDatasetManifest",
    "EvaluationLabel",
    "EvaluationModelStatement",
    "EvaluationPrediction",
    "SplitName",
]
