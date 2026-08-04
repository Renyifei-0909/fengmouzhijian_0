from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


NonNegativeInt = Annotated[int, Field(ge=0)]
VerificationAttemptDisposition = Literal[
    "committed_success",
    "committed_failure",
    "lease_expired",
    "lease_lost",
    "write_fenced",
]
VerificationOperationsJobStatus = Literal[
    "queued",
    "running",
    "needs_review",
    "sealing",
    "approved",
    "rejected",
    "failed",
    "other",
]


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class ProjectCreate(BaseModel):
    code: str = Field(min_length=2, max_length=64)
    name: str = Field(min_length=2, max_length=200)
    location: str = Field(min_length=1, max_length=300)
    manager: str | None = Field(default=None, max_length=100)


class ProjectRead(ORMModel):
    id: str
    code: str
    name: str
    location: str
    manager: str | None
    status: str
    created_at: datetime


class BaselineCreate(BaseModel):
    site_id: str = Field(min_length=1, max_length=100)
    procedure_code: str = Field(min_length=1, max_length=100)
    version: str = Field(min_length=1, max_length=64)
    source_type: Literal["manual", "gis", "cad", "api"] = "manual"
    expected: dict[str, Any]


class BaselineRead(ORMModel):
    id: str
    project_id: str
    site_id: str
    procedure_code: str
    version: str
    source_type: str
    expected: dict[str, Any]
    sha256: str
    created_at: datetime


class SensorEventCreate(BaseModel):
    project_id: str
    site_id: str = Field(min_length=1, max_length=100)
    device_id: str = Field(min_length=1, max_length=100)
    kind: str = Field(min_length=1, max_length=100)
    value: float
    unit: str = Field(min_length=1, max_length=32)
    captured_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)


class SensorEventRead(ORMModel):
    id: str
    project_id: str
    site_id: str
    device_id: str
    kind: str
    value: float
    unit: str
    captured_at: datetime
    metadata: dict[str, Any] = Field(validation_alias="metadata_json")
    sha256: str
    created_at: datetime


class EvidenceRead(ORMModel):
    id: str
    project_id: str
    baseline_id: str
    original_name: str
    content_type: str
    size_bytes: int
    sha256: str
    captured_at: datetime | None
    device_id: str | None
    metadata: dict[str, Any] = Field(validation_alias="metadata_json")
    created_at: datetime


class EvidenceContentError(BaseModel):
    """Stable JSON error envelope for protected evidence-content reads."""

    detail: str


class VerificationRead(ORMModel):
    id: str
    project_id: str
    baseline_id: str
    evidence_id: str
    analyzer_name: str
    analyzer_version: str
    status: str
    progress: int
    result: dict[str, Any] | None = Field(validation_alias="result_json")
    error: str | None = Field(validation_alias="error_message")
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None


class VerificationDispatch(BaseModel):
    execution_mode: Literal["inline", "external"]
    state: Literal["unclaimed", "leased", "released", "dead_letter"]
    generation: int
    attempt_count: int
    max_attempts: int
    heartbeat_at: datetime | None = None
    lease_expires_at: datetime | None = None


class VerificationAttemptOutcomeRead(BaseModel):
    id: str
    attempt_id: str
    disposition: VerificationAttemptDisposition
    stage: str | None
    result_sha256: str | None
    error_code: str | None
    error_retryable: bool | None
    error_message: str | None
    upstream_status: int | None
    dead_lettered: bool
    finished_at: datetime


class VerificationAttemptRead(BaseModel):
    id: str
    job_id: str
    generation: int
    attempt_no: int
    worker_ref: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    execution_mode: Literal["inline", "external"]
    analyzer_name: str
    analyzer_version: str
    evidence_sha256: str
    baseline_sha256: str
    max_attempts: int
    claimed_at: datetime
    outcome: VerificationAttemptOutcomeRead | None


class VerificationRecovery(BaseModel):
    action: Literal["none", "retry_analysis", "resume_sealing", "integrity_review"]
    retryable: bool
    reason: str
    operation_state: str | None = None
    attempt_count: int = 0
    last_error: str | None = None
    updated_at: datetime | None = None


class VerificationDetail(BaseModel):
    job: VerificationRead
    dispatch: VerificationDispatch
    attempts: list[VerificationAttemptRead]
    evidence: EvidenceRead
    report: "ReportRead | None" = None
    proof: "ProofRead | None" = None
    remediation_attempt: "RemediationAttemptRead | None" = None
    recovery: VerificationRecovery


class ReviewRequest(BaseModel):
    decision: Literal["approve", "reject"]
    reviewer: str = Field(min_length=2, max_length=100)
    note: str | None = Field(default=None, max_length=2000)
    remediation_resolution: Literal["resolved", "not_resolved"] | None = None


class ReviewRead(ORMModel):
    id: str
    job_id: str
    decision: str
    reviewer: str
    note: str | None
    reviewed_at: datetime


class ReportRead(ORMModel):
    id: str
    job_id: str
    project_id: str
    status: str
    schema_version: str
    content: dict[str, Any] = Field(validation_alias="content_json")
    sha256: str
    html_sha256: str
    created_at: datetime


class ProofRead(ORMModel):
    id: str
    archive_id: str
    report_id: str
    purpose: str
    evidence_grade: bool
    merkle_root: str
    manifest_sha256: str
    archive_sha256: str
    previous_record_hash: str
    record_hash: str
    ledger_index: int
    created_at: datetime


class ReviewOutcome(BaseModel):
    job: VerificationRead
    review: ReviewRead
    report: ReportRead | None = None
    proof: ProofRead | None = None


class FindingCaseRead(ORMModel):
    id: str
    project_id: str
    source_job_id: str
    source_evidence_id: str
    baseline_id: str
    finding_key: str
    finding_index: int
    finding_sha256: str
    source_result_sha256: str
    analyzer_name: str
    analyzer_version: str
    analysis_mode: str
    source_synthetic: bool
    source_evidence_grade: bool
    finding_code: str
    proposed_severity: str
    finding_message: str
    scope: str
    status: str
    confirmed_severity: str | None
    decision_reason: str | None
    acknowledged_by: str | None
    acknowledged_at: datetime | None
    assigned_to: str | None
    due_at: datetime | None
    confirmed_by: str | None
    confirmed_at: datetime | None
    closed_by: str | None
    closed_at: datetime | None
    closure_proof_id: str | None
    active_attempt_no: int | None
    version: int
    created_at: datetime
    updated_at: datetime


class FindingCaseSummary(BaseModel):
    pending_triage: int
    confirmed_open_operational: int
    remediation_in_progress_operational: int
    verification_pending_operational: int
    closed_operational: int
    dismissed_operational: int
    demo_cases: int
    truth_note: str


class FindingTriageRequest(BaseModel):
    request_id: UUID
    expected_version: int = Field(ge=0)
    decision: Literal["confirm", "dismiss"]
    confirmed_severity: Literal["info", "warning", "error", "critical"] | None = None
    reason: str = Field(min_length=2, max_length=2000)


class FindingRemediationStartRequest(BaseModel):
    request_id: UUID
    expected_version: int = Field(ge=0)
    assignee: str = Field(min_length=2, max_length=100)
    action_description: str = Field(min_length=2, max_length=4000)
    due_at: datetime | None = None


class RemediationAttemptCreate(BaseModel):
    client_request_id: UUID
    expected_version: int = Field(ge=0)
    action_description: str = Field(min_length=2, max_length=4000)


class RemediationAttemptRead(ORMModel):
    id: str
    case_id: str
    attempt_no: int
    client_request_id: str
    action_description: str
    submitted_by: str
    submitted_at: datetime
    verification_job_id: str | None
    resolution_decision: str
    resolution_note: str | None
    resolved_by: str | None
    resolved_at: datetime | None
    report_id: str | None
    proof_id: str | None
    created_at: datetime


class FindingCaseCommandRead(ORMModel):
    id: str
    case_id: str
    command: str
    from_status: str
    to_status: str
    actor: str
    actor_role: str
    payload_sha256: str
    result_version: int
    created_at: datetime


class FindingCaseDetail(BaseModel):
    case: FindingCaseRead
    attempts: list[RemediationAttemptRead]
    history: list[FindingCaseCommandRead]
    closure_evidence_status: Literal["unsealed", "sealed", "invalid"]


class IntegrityCheck(BaseModel):
    valid: bool
    archive_id: str
    checked_at: datetime
    checks: dict[str, bool]
    errors: list[str]


class ProjectProgress(BaseModel):
    project_id: str
    baseline_count: int
    approved_baseline_count: int
    pending_review_count: int
    failed_or_rejected_count: int
    completion_rate: float
    metric_note: str


class ProjectOverview(BaseModel):
    project: ProjectRead
    progress: ProjectProgress
    jobs_by_status: dict[str, int]
    evidence_asset_count: int
    sensor_event_count: int
    report_count: int
    proof_record_count: int
    recent_verifications: list[VerificationRead]
    recent_reports: list[ReportRead]
    recent_proofs: list[ProofRead]
    truth_note: str


class DatabaseSchemaMeta(BaseModel):
    mode: Literal["create_all", "upgrade", "verify"]
    expected_heads: list[str]
    current_heads: list[str]
    managed_by_alembic: bool
    at_head: bool
    drift_free: bool
    legacy_adopted: bool


class VerificationOperationsThresholds(BaseModel):
    queue_wait_warning_seconds: float = Field(ge=1)
    recent_window_seconds: int = Field(ge=60)
    lease_seconds: float = Field(gt=0)
    heartbeat_seconds: float = Field(gt=0)


class VerificationOperationsJobs(BaseModel):
    total: int = Field(ge=0)
    by_status: dict[VerificationOperationsJobStatus, NonNegativeInt]


class VerificationOperationsDispatch(BaseModel):
    lease_rows: int = Field(ge=0)
    active_leases: int = Field(ge=0)
    expired_running_leases: int = Field(ge=0)
    unclaimed_queued_jobs: int = Field(ge=0)
    queued_over_warning_threshold: int = Field(ge=0)
    dead_letter_jobs: int = Field(ge=0)
    oldest_queued_seconds: float | None = Field(default=None, ge=0)
    oldest_active_heartbeat_seconds: float | None = Field(default=None, ge=0)


class VerificationOperationsAttempts(BaseModel):
    total: int = Field(ge=0)
    open: int = Field(ge=0)
    outcomes_total_by_disposition: dict[
        VerificationAttemptDisposition,
        NonNegativeInt,
    ]
    outcomes_window_by_disposition: dict[
        VerificationAttemptDisposition,
        NonNegativeInt,
    ]
    recent_instability: int = Field(ge=0)


class VerificationOperationsIntegrity(BaseModel):
    status: Literal["ok", "incident"]
    dispatch_issue_count: int = Field(ge=0)
    attempt_issue_count: int = Field(ge=0)
    issue_count: int = Field(ge=0)


class VerificationOperationsAlert(BaseModel):
    severity: Literal["warning", "incident"]
    code: Literal[
        "INTEGRITY_INCIDENT",
        "DEAD_LETTER_PRESENT",
        "QUEUE_WAIT_EXCEEDED",
        "RECENT_LEASE_INSTABILITY",
    ]
    count: int = Field(ge=1)
    message: str


class VerificationOperationsSnapshot(BaseModel):
    status: Literal["healthy", "attention", "incident"]
    generated_at: datetime
    execution_mode: Literal["inline", "external"]
    thresholds: VerificationOperationsThresholds
    jobs: VerificationOperationsJobs
    dispatch: VerificationOperationsDispatch
    attempts: VerificationOperationsAttempts
    integrity: VerificationOperationsIntegrity
    alerts: list[VerificationOperationsAlert]
    truth_note: str


class CapabilityMeta(BaseModel):
    service_version: str
    implemented: list[str]
    adapters: dict[str, dict[str, Any]]
    database_schema: DatabaseSchemaMeta
    verification_execution: dict[str, Any]
    truth_boundary: list[str]


# ---------------------------------------------------------------------------
# Alpha18: QGIS work-order compliance schemas
# ---------------------------------------------------------------------------


class DesignPackageRead(ORMModel):
    id: str
    project_id: str
    package_code: str
    source_filename: str
    source_sha256: str
    source_type: str
    purpose: str
    synthetic: bool
    source_crs_epsg: int
    import_contract_version: str = ""
    layers: dict[str, Any] = Field(validation_alias="layers_json")
    field_mapping: dict[str, Any] = Field(validation_alias="field_mapping_json")
    redaction_policy: dict[str, Any] = Field(validation_alias="redaction_policy_json")
    import_status: str
    import_warnings: list[Any] = Field(validation_alias="import_warnings_json")
    object_count: int
    imported_at: datetime | None
    created_at: datetime


class EngineeringObjectRead(ORMModel):
    id: str
    project_id: str
    design_package_id: str
    object_code: str
    object_type: str
    name: str
    source_layer: str
    source_feature_id: str
    geometry_type: str
    geometry_wgs84: dict[str, Any] = Field(validation_alias="geometry_wgs84_json")
    geometry_source_crs_epsg: int
    attributes_snapshot: dict[str, Any] = Field(validation_alias="attributes_snapshot_json")
    expected_rules: dict[str, Any] = Field(validation_alias="expected_rules_json")
    design_version: str
    created_at: datetime


class WorkOrderCreate(BaseModel):
    engineering_object_id: str
    work_order_code: str = Field(min_length=2, max_length=100)
    procedure_code: str | None = Field(default=None, max_length=100)
    spatial_tolerance_m: float = Field(default=50.0, gt=0, le=10_000)
    gps_accuracy_threshold_m: float = Field(default=30.0, gt=0, le=10_000)
    # Deprecated for status: create always yields draft. Use POST .../assign.
    assigned_to: str | None = Field(
        default=None,
        max_length=100,
        description="Ignored for status (P2-1.2). Use POST /work-orders/{id}/assign.",
    )
    notes: str | None = Field(default=None, max_length=4000)


class WorkOrderAssign(BaseModel):
    assigned_to: str = Field(min_length=1, max_length=100)


class WorkOrderRead(ORMModel):
    id: str
    project_id: str
    engineering_object_id: str
    baseline_id: str | None
    work_order_code: str
    procedure_code: str
    status: str
    design_version: str
    design_snapshot: dict[str, Any] = Field(validation_alias="design_snapshot_json")
    geometry_snapshot: dict[str, Any] = Field(validation_alias="geometry_snapshot_json")
    rules_snapshot: dict[str, Any] = Field(validation_alias="rules_snapshot_json")
    spatial_tolerance_m: float
    gps_accuracy_threshold_m: float
    assigned_to: str | None
    notes: str | None
    created_at: datetime
    updated_at: datetime


class EvidenceCaptureRead(ORMModel):
    id: str
    project_id: str
    work_order_id: str
    evidence_id: str
    verification_job_id: str | None
    client_captured_at: datetime | None
    server_received_at: datetime
    latitude: float | None
    longitude: float | None
    accuracy_m: float | None
    location_source: str
    is_synthetic_location: bool
    distance_to_target_m: float | None
    tolerance_m: float
    gps_accuracy_threshold_m: float
    spatial_check_status: str
    spatial_check_reason: str
    created_at: datetime


class ComplianceEvaluationRead(ORMModel):
    id: str
    project_id: str
    work_order_id: str
    job_id: str
    rule_version: str
    engine_version: str
    expected: dict[str, Any] = Field(validation_alias="expected_json")
    observed: dict[str, Any] = Field(validation_alias="observed_json")
    differences: list[Any] = Field(validation_alias="difference_json")
    verdict: str
    spatial_check_status: str | None
    notes: str | None
    created_at: datetime


class DesignPackageImportResult(BaseModel):
    package: DesignPackageRead
    objects: list[EngineeringObjectRead]
    truth_note: str


class StandardGpkgPreviewResult(BaseModel):
    """Preview-only report; valid preview is not an import completion."""

    valid: bool
    preview_token: str | None = None
    expires_at_unix: int | None = None
    source_sha256: str
    size_bytes: int
    import_contract_version: str
    package_code: str
    staging_id: str
    candidate_count: int
    object_codes: list[str]
    layers_summary: list[dict[str, Any]]
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    preflight_valid: bool = False
    normalize_valid: bool = False
    error_code: str | None = None
    source_classification: str = "sample_or_unverified"
    truth_note: str = (
        "预检与规范化预览不等于导入完成；格式校验通过不等于数据来源已获授权；"
        "确认后方可写入设计包与工程对象。"
    )


class StandardGpkgConfirmRequest(BaseModel):
    """Client cannot set synthetic/purpose — server forces sample_or_unverified."""

    package_code: str = Field(
        min_length=2,
        max_length=100,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{1,99}$",
    )
    staging_id: str = Field(
        min_length=16,
        max_length=64,
        pattern=r"^[a-f0-9]{16,64}$",
    )
    preview_token: str = Field(min_length=16, max_length=4096)
    design_version: str = Field(
        default="design-v1",
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$",
    )


class StandardGpkgImportResult(BaseModel):
    package: DesignPackageRead
    objects: list[EngineeringObjectRead]
    idempotent: bool
    truth_note: str
    source_classification: str = "sample_or_unverified"



class WorkOrderVerificationRead(BaseModel):
    job: VerificationRead
    capture: EvidenceCaptureRead
    work_order: WorkOrderRead
    compliance: ComplianceEvaluationRead | None = None
    truth_note: str


class ProjectGisSummary(BaseModel):
    project_id: str
    design_package_count: int
    engineering_object_count: int
    work_order_count: int
    objects: list[EngineeringObjectRead]
    work_orders: list[WorkOrderRead]
    truth_note: str


VerificationDetail.model_rebuild()
