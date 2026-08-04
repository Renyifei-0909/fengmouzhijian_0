from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DDL,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    event,
)
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return str(uuid.uuid4())


VERIFICATION_JOB_STATUSES = (
    "queued",
    "running",
    "needs_review",
    "sealing",
    "approved",
    "rejected",
    "failed",
)

VERIFICATION_ATTEMPT_DISPOSITIONS = (
    "committed_success",
    "committed_failure",
    "lease_expired",
    "lease_lost",
    "write_fenced",
)


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    code: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    location: Mapped[str] = mapped_column(String(300))
    manager: Mapped[str | None] = mapped_column(String(100), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="active", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class DesignBaseline(Base):
    __tablename__ = "design_baselines"
    __table_args__ = (
        UniqueConstraint("project_id", "site_id", "procedure_code", "version", name="uq_baseline_scope"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    site_id: Mapped[str] = mapped_column(String(100), index=True)
    procedure_code: Mapped[str] = mapped_column(String(100), index=True)
    version: Mapped[str] = mapped_column(String(64))
    source_type: Mapped[str] = mapped_column(String(32), default="manual")
    expected: Mapped[dict[str, Any]] = mapped_column(JSON)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SensorEvent(Base):
    __tablename__ = "sensor_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    site_id: Mapped[str] = mapped_column(String(100), index=True)
    device_id: Mapped[str] = mapped_column(String(100), index=True)
    kind: Mapped[str] = mapped_column(String(100), index=True)
    value: Mapped[float] = mapped_column(Float)
    unit: Mapped[str] = mapped_column(String(32))
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class EvidenceAsset(Base):
    __tablename__ = "evidence_assets"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    baseline_id: Mapped[str] = mapped_column(ForeignKey("design_baselines.id", ondelete="RESTRICT"), index=True)
    original_name: Mapped[str] = mapped_column(String(255))
    stored_name: Mapped[str] = mapped_column(String(255), unique=True)
    storage_path: Mapped[str] = mapped_column(Text)
    content_type: Mapped[str] = mapped_column(String(120))
    size_bytes: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    captured_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    device_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class VerificationJob(Base):
    __tablename__ = "verification_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    baseline_id: Mapped[str] = mapped_column(ForeignKey("design_baselines.id", ondelete="RESTRICT"), index=True)
    evidence_id: Mapped[str] = mapped_column(ForeignKey("evidence_assets.id", ondelete="RESTRICT"), unique=True)
    analyzer_name: Mapped[str] = mapped_column(String(100))
    analyzer_version: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    progress: Mapped[int] = mapped_column(Integer, default=0)
    result_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class VerificationJobLease(Base):
    """Durable ownership and retry budget for one verification job.

    ``generation`` is a fencing token: a worker may persist a result only while
    the row still contains the generation and owner it claimed. Keeping this in
    a separate table lets existing SQLite deployments add the feature through
    ``create_all`` without rewriting the established verification job table.
    """

    __tablename__ = "verification_job_leases"
    __table_args__ = (
        CheckConstraint("generation >= 0", name="ck_verification_lease_generation"),
        CheckConstraint("attempt_count >= 0", name="ck_verification_lease_attempt_count"),
    )

    job_id: Mapped[str] = mapped_column(
        ForeignKey("verification_jobs.id", ondelete="CASCADE"), primary_key=True
    )
    owner_id: Mapped[str | None] = mapped_column(String(200), nullable=True, index=True)
    generation: Mapped[int] = mapped_column(Integer, default=0)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    last_error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    last_error_retryable: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    dead_lettered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class VerificationAttempt(Base):
    """Immutable claim-time snapshot for one verification execution attempt."""

    __tablename__ = "verification_attempts"
    __table_args__ = (
        UniqueConstraint("job_id", "generation", name="uq_verification_attempt_generation"),
        UniqueConstraint("job_id", "attempt_no", name="uq_verification_attempt_number"),
        CheckConstraint("generation > 0", name="ck_verification_attempt_generation"),
        CheckConstraint("attempt_no > 0", name="ck_verification_attempt_number"),
        CheckConstraint(
            "execution_mode IN ('inline', 'external')",
            name="ck_verification_attempt_execution_mode",
        ),
        CheckConstraint("max_attempts > 0", name="ck_verification_attempt_max_attempts"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    job_id: Mapped[str] = mapped_column(
        ForeignKey("verification_jobs.id", ondelete="CASCADE"), index=True
    )
    generation: Mapped[int] = mapped_column(Integer)
    attempt_no: Mapped[int] = mapped_column(Integer)
    worker_id: Mapped[str] = mapped_column(String(200))
    execution_mode: Mapped[str] = mapped_column(String(32))
    analyzer_name: Mapped[str] = mapped_column(String(100))
    analyzer_version: Mapped[str] = mapped_column(String(64))
    evidence_sha256: Mapped[str] = mapped_column(String(64))
    baseline_sha256: Mapped[str] = mapped_column(String(64))
    max_attempts: Mapped[int] = mapped_column(Integer)
    claimed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class VerificationAttemptOutcome(Base):
    """Append-only terminal record for a verification attempt."""

    __tablename__ = "verification_attempt_outcomes"
    __table_args__ = (
        UniqueConstraint(
            "attempt_id",
            name="uq_verification_attempt_outcome_attempt",
        ),
        CheckConstraint(
            "disposition IN ('committed_success', 'committed_failure', "
            "'lease_expired', 'lease_lost', 'write_fenced')",
            name="ck_verification_attempt_outcome_disposition",
        ),
        CheckConstraint(
            "(disposition = 'committed_success' "
            "AND result_json IS NOT NULL AND result_sha256 IS NOT NULL) "
            "OR (disposition <> 'committed_success' "
            "AND result_json IS NULL AND result_sha256 IS NULL)",
            name="ck_verification_attempt_outcome_result",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    attempt_id: Mapped[str] = mapped_column(
        ForeignKey("verification_attempts.id", ondelete="CASCADE")
    )
    disposition: Mapped[str] = mapped_column(String(32), index=True)
    stage: Mapped[str | None] = mapped_column(String(100), nullable=True)
    result_json: Mapped[dict[str, Any] | None] = mapped_column(
        JSON(none_as_null=True),
        nullable=True,
    )
    result_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_retryable: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    upstream_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    dead_lettered: Mapped[bool] = mapped_column(Boolean, default=False)
    finished_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


APPEND_ONLY_TRIGGER_TARGETS = {
    "trg_verification_attempts_no_update": (
        "verification_attempts",
        "update",
    ),
    "trg_verification_attempts_no_delete": (
        "verification_attempts",
        "delete",
    ),
    "trg_verification_attempt_outcomes_no_update": (
        "verification_attempt_outcomes",
        "update",
    ),
    "trg_verification_attempt_outcomes_no_delete": (
        "verification_attempt_outcomes",
        "delete",
    ),
}
APPEND_ONLY_TRIGGER_NAMES = frozenset(APPEND_ONLY_TRIGGER_TARGETS)

_POSTGRES_APPEND_ONLY_FUNCTION = """
CREATE OR REPLACE FUNCTION fengmou_reject_verification_attempt_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $fengmou$
BEGIN
    RAISE EXCEPTION 'verification attempt history is append-only'
        USING ERRCODE = '23000';
END;
$fengmou$
"""


def _register_verification_attempt_append_only_ddl() -> None:
    """Install database guards for test ``create_all`` schemas.

    Versioned deployments receive equivalent DDL from the Alembic revision.
    """

    event.listen(
        VerificationAttempt.__table__,
        "before_create",
        DDL(_POSTGRES_APPEND_ONLY_FUNCTION).execute_if(dialect="postgresql"),
    )
    for table, table_name in (
        (VerificationAttempt.__table__, "verification_attempts"),
        (VerificationAttemptOutcome.__table__, "verification_attempt_outcomes"),
    ):
        for operation in ("UPDATE", "DELETE"):
            trigger_name = f"trg_{table_name}_no_{operation.lower()}"
            sqlite_ddl = DDL(
                f"""
CREATE TRIGGER {trigger_name}
BEFORE {operation} ON {table_name}
BEGIN
    SELECT RAISE(ABORT, '{table_name} is append-only');
END
"""
            ).execute_if(dialect="sqlite")
            postgres_ddl = DDL(
                f"""
CREATE TRIGGER {trigger_name}
BEFORE {operation} ON {table_name}
FOR EACH ROW
EXECUTE FUNCTION fengmou_reject_verification_attempt_mutation()
"""
            ).execute_if(dialect="postgresql")
            event.listen(table, "after_create", sqlite_ddl)
            event.listen(table, "after_create", postgres_ddl)
    event.listen(
        VerificationAttempt.__table__,
        "after_drop",
        DDL(
            "DROP FUNCTION IF EXISTS "
            "fengmou_reject_verification_attempt_mutation()"
        ).execute_if(dialect="postgresql"),
    )


_register_verification_attempt_append_only_ddl()


class FindingCase(Base):
    """Human-triaged case derived from one immutable analyzer finding.

    A row is a candidate observation until a reviewer confirms it.  Synthetic
    cases remain useful for workflow demonstrations, but ``scope='demo'`` keeps
    them out of operational alarm totals.
    """

    __tablename__ = "finding_cases"
    __table_args__ = (
        UniqueConstraint("finding_key", name="uq_finding_case_key"),
        UniqueConstraint("source_job_id", "finding_index", name="uq_finding_case_position"),
        CheckConstraint(
            "scope IN ('operational', 'demo')",
            name="ck_finding_case_scope",
        ),
        CheckConstraint(
            "status IN ('pending_triage', 'open', 'remediation_in_progress', "
            "'verification_pending', 'closed', 'dismissed')",
            name="ck_finding_case_status",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    source_job_id: Mapped[str] = mapped_column(
        ForeignKey("verification_jobs.id", ondelete="CASCADE"), index=True
    )
    source_evidence_id: Mapped[str] = mapped_column(
        ForeignKey("evidence_assets.id", ondelete="RESTRICT"), index=True
    )
    baseline_id: Mapped[str] = mapped_column(
        ForeignKey("design_baselines.id", ondelete="RESTRICT"), index=True
    )
    finding_key: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    finding_index: Mapped[int] = mapped_column(Integer)
    finding_sha256: Mapped[str] = mapped_column(String(64), index=True)
    source_result_sha256: Mapped[str] = mapped_column(String(64), index=True)
    source_finding_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    analyzer_name: Mapped[str] = mapped_column(String(100))
    analyzer_version: Mapped[str] = mapped_column(String(100))
    analysis_mode: Mapped[str] = mapped_column(String(100))
    source_synthetic: Mapped[bool] = mapped_column(Boolean, default=False)
    source_evidence_grade: Mapped[bool] = mapped_column(Boolean, default=False)
    finding_code: Mapped[str] = mapped_column(String(100), index=True)
    proposed_severity: Mapped[str] = mapped_column(String(32), index=True)
    finding_message: Mapped[str] = mapped_column(Text)
    scope: Mapped[str] = mapped_column(String(32), index=True)
    status: Mapped[str] = mapped_column(String(32), default="pending_triage", index=True)
    confirmed_severity: Mapped[str | None] = mapped_column(String(32), nullable=True)
    decision_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    acknowledged_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    assigned_to: Mapped[str | None] = mapped_column(String(100), nullable=True)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    confirmed_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closure_proof_id: Mapped[str | None] = mapped_column(
        ForeignKey("proof_records.id", ondelete="RESTRICT"), nullable=True
    )
    active_attempt_no: Mapped[int | None] = mapped_column(Integer, nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class FindingCaseCommand(Base):
    """Idempotency and ordered transition evidence for a finding case."""

    __tablename__ = "finding_case_commands"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    case_id: Mapped[str] = mapped_column(
        ForeignKey("finding_cases.id", ondelete="CASCADE"), index=True
    )
    command: Mapped[str] = mapped_column(String(64), index=True)
    from_status: Mapped[str] = mapped_column(String(32))
    to_status: Mapped[str] = mapped_column(String(32))
    actor: Mapped[str] = mapped_column(String(100))
    actor_role: Mapped[str] = mapped_column(String(32))
    payload_sha256: Mapped[str] = mapped_column(String(64))
    result_version: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class RemediationAttempt(Base):
    """One immutable remediation submission and its linked re-verification."""

    __tablename__ = "remediation_attempts"
    __table_args__ = (
        UniqueConstraint("case_id", "attempt_no", name="uq_remediation_attempt_number"),
        CheckConstraint(
            "resolution_decision IN ('pending', 'resolved', 'not_resolved')",
            name="ck_remediation_resolution",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    case_id: Mapped[str] = mapped_column(
        ForeignKey("finding_cases.id", ondelete="CASCADE"), index=True
    )
    attempt_no: Mapped[int] = mapped_column(Integer)
    client_request_id: Mapped[str] = mapped_column(String(36), unique=True, index=True)
    action_description: Mapped[str] = mapped_column(Text)
    submitted_by: Mapped[str] = mapped_column(String(100))
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    verification_job_id: Mapped[str | None] = mapped_column(
        ForeignKey("verification_jobs.id", ondelete="RESTRICT"), unique=True, nullable=True, index=True
    )
    resolution_decision: Mapped[str] = mapped_column(String(32), default="pending")
    resolution_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolved_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    report_id: Mapped[str | None] = mapped_column(
        ForeignKey("structured_reports.id", ondelete="RESTRICT"), nullable=True
    )
    proof_id: Mapped[str | None] = mapped_column(
        ForeignKey("proof_records.id", ondelete="RESTRICT"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class HumanReview(Base):
    __tablename__ = "human_reviews"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    job_id: Mapped[str] = mapped_column(ForeignKey("verification_jobs.id", ondelete="CASCADE"), unique=True)
    decision: Mapped[str] = mapped_column(String(32))
    reviewer: Mapped[str] = mapped_column(String(100))
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class StructuredReport(Base):
    __tablename__ = "structured_reports"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    job_id: Mapped[str] = mapped_column(ForeignKey("verification_jobs.id", ondelete="CASCADE"), unique=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(32), default="final")
    schema_version: Mapped[str] = mapped_column(String(32), default="1.0")
    content_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    json_path: Mapped[str] = mapped_column(Text)
    html_path: Mapped[str] = mapped_column(Text)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    html_sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ProofRecord(Base):
    __tablename__ = "proof_records"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    archive_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    report_id: Mapped[str] = mapped_column(ForeignKey("structured_reports.id", ondelete="CASCADE"), unique=True)
    purpose: Mapped[str] = mapped_column(String(32), default="demo")
    evidence_grade: Mapped[bool] = mapped_column(Boolean, default=False)
    merkle_root: Mapped[str] = mapped_column(String(64))
    manifest_sha256: Mapped[str] = mapped_column(String(64), index=True)
    archive_sha256: Mapped[str] = mapped_column(String(64), index=True)
    previous_record_hash: Mapped[str] = mapped_column(String(64))
    record_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    archive_path: Mapped[str] = mapped_column(Text)
    ledger_index: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SealOperation(Base):
    """Durable saga state for publishing one approved verification bundle.

    Database rows, report files, the ZIP archive, and the local ledger cannot
    share one transaction.  This record makes every intermediate state
    explicit and gives startup recovery stable artifact identifiers.
    """

    __tablename__ = "seal_operations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    job_id: Mapped[str] = mapped_column(
        ForeignKey("verification_jobs.id", ondelete="CASCADE"), unique=True, index=True
    )
    review_id: Mapped[str] = mapped_column(
        ForeignKey("human_reviews.id", ondelete="CASCADE"), unique=True
    )
    state: Mapped[str] = mapped_column(String(32), default="requested", index=True)
    report_id: Mapped[str] = mapped_column(String(36), unique=True)
    archive_id: Mapped[str] = mapped_column(String(64), unique=True)
    report_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    purpose: Mapped[str | None] = mapped_column(String(32), nullable=True)
    evidence_grade: Mapped[bool] = mapped_column(Boolean, default=False)
    report_content_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    report_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    html_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    manifest_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    archive_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    merkle_root: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ledger_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    previous_record_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    record_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ledger_row_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    entity_type: Mapped[str] = mapped_column(String(64), index=True)
    entity_id: Mapped[str] = mapped_column(String(64), index=True)
    action: Mapped[str] = mapped_column(String(100), index=True)
    actor: Mapped[str] = mapped_column(String(100), default="system")
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# ---------------------------------------------------------------------------
# Alpha18: QGIS work-order compliance verification vertical slice
# ---------------------------------------------------------------------------

WORK_ORDER_STATUSES = (
    "draft",
    "assigned",
    "evidence_uploaded",
    "analyzing",
    "needs_review",
    "approved",
    "deviation",
    "remediating",
    "closed",
)

COMPLIANCE_VERDICTS = (
    "compliant",
    "deviation_detected",
    "insufficient_evidence",
    "needs_review",
)

SPATIAL_CHECK_STATUSES = (
    "passed",
    "failed",
    "skipped",
    "unavailable",
)


class DesignPackage(Base):
    """Imported design package (JSON, legacy GPKG derivative, or standard GPKG)."""

    __tablename__ = "design_packages"
    __table_args__ = (
        CheckConstraint(
            "source_type IN ('synthetic_json', 'gpkg_derivative', 'standard_gpkg')",
            name="ck_design_package_source_type",
        ),
        CheckConstraint(
            "purpose IN ('demo', 'controlled')",
            name="ck_design_package_purpose",
        ),
        CheckConstraint(
            "import_status IN ('pending', 'completed', 'failed', 'partial')",
            name="ck_design_package_import_status",
        ),
        # ADR-002 idempotency: project + file digest + contract version
        UniqueConstraint(
            "project_id",
            "source_sha256",
            "import_contract_version",
            name="uq_design_package_idempotency",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    package_code: Mapped[str] = mapped_column(String(100), index=True)
    source_filename: Mapped[str] = mapped_column(String(255))
    source_sha256: Mapped[str] = mapped_column(String(64), index=True)
    source_type: Mapped[str] = mapped_column(String(32))
    purpose: Mapped[str] = mapped_column(String(32), default="demo")
    synthetic: Mapped[bool] = mapped_column(Boolean, default=True)
    source_crs_epsg: Mapped[int] = mapped_column(Integer)
    # Empty string for pre-P1-3 packages; gpkg-import-contract-v* for standard_gpkg.
    import_contract_version: Mapped[str] = mapped_column(String(64), default="", index=True)
    layers_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    field_mapping_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    redaction_policy_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    import_status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    import_warnings_json: Mapped[list[Any]] = mapped_column(JSON, default=list)
    object_count: Mapped[int] = mapped_column(Integer, default=0)
    storage_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    imported_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class EngineeringObject(Base):
    """Normalized engineering object from a design package import."""

    __tablename__ = "engineering_objects"
    __table_args__ = (
        UniqueConstraint("project_id", "object_code", name="uq_engineering_object_code"),
        CheckConstraint(
            "object_type IN ('pipe_route', 'trench', 'infrastructure_point')",
            name="ck_engineering_object_type",
        ),
        CheckConstraint(
            "geometry_type IN ('Point', 'LineString', 'Polygon')",
            name="ck_engineering_object_geometry_type",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    design_package_id: Mapped[str] = mapped_column(
        ForeignKey("design_packages.id", ondelete="RESTRICT"), index=True
    )
    object_code: Mapped[str] = mapped_column(String(100), index=True)
    object_type: Mapped[str] = mapped_column(String(64), index=True)
    name: Mapped[str] = mapped_column(String(200))
    source_layer: Mapped[str] = mapped_column(String(100))
    source_feature_id: Mapped[str] = mapped_column(String(100))
    geometry_type: Mapped[str] = mapped_column(String(32))
    geometry_wgs84_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    geometry_source_crs_epsg: Mapped[int] = mapped_column(Integer)
    attributes_snapshot_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    expected_rules_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    design_version: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class WorkOrder(Base):
    """Construction work order bound to one engineering object and frozen design."""

    __tablename__ = "work_orders"
    __table_args__ = (
        UniqueConstraint("project_id", "work_order_code", name="uq_work_order_code"),
        CheckConstraint(
            "status IN ('draft', 'assigned', 'evidence_uploaded', 'analyzing', "
            "'needs_review', 'approved', 'deviation', 'remediating', 'closed')",
            name="ck_work_order_status",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    engineering_object_id: Mapped[str] = mapped_column(
        ForeignKey("engineering_objects.id", ondelete="RESTRICT"), index=True
    )
    baseline_id: Mapped[str | None] = mapped_column(
        ForeignKey("design_baselines.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    work_order_code: Mapped[str] = mapped_column(String(100), index=True)
    procedure_code: Mapped[str] = mapped_column(String(100), index=True)
    status: Mapped[str] = mapped_column(String(32), default="draft", index=True)
    design_version: Mapped[str] = mapped_column(String(64))
    design_snapshot_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    geometry_snapshot_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    rules_snapshot_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    spatial_tolerance_m: Mapped[float] = mapped_column(Float, default=50.0)
    gps_accuracy_threshold_m: Mapped[float] = mapped_column(Float, default=30.0)
    assigned_to: Mapped[str | None] = mapped_column(String(100), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class EvidenceCapture(Base):
    """Capture metadata and spatial validation for one evidence asset under a work order."""

    __tablename__ = "evidence_captures"
    __table_args__ = (
        UniqueConstraint("evidence_id", name="uq_evidence_capture_evidence"),
        CheckConstraint(
            "spatial_check_status IN ('passed', 'failed', 'skipped', 'unavailable')",
            name="ck_evidence_capture_spatial_status",
        ),
        CheckConstraint(
            "location_source IN ('device_gps', 'synthetic_demo', 'manual', 'unknown')",
            name="ck_evidence_capture_location_source",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    work_order_id: Mapped[str] = mapped_column(
        ForeignKey("work_orders.id", ondelete="RESTRICT"), index=True
    )
    evidence_id: Mapped[str] = mapped_column(
        ForeignKey("evidence_assets.id", ondelete="RESTRICT"), unique=True
    )
    verification_job_id: Mapped[str | None] = mapped_column(
        ForeignKey("verification_jobs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    client_captured_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    server_received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    accuracy_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    location_source: Mapped[str] = mapped_column(String(32), default="unknown")
    is_synthetic_location: Mapped[bool] = mapped_column(Boolean, default=False)
    distance_to_target_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    tolerance_m: Mapped[float] = mapped_column(Float)
    gps_accuracy_threshold_m: Mapped[float] = mapped_column(Float)
    spatial_check_status: Mapped[str] = mapped_column(String(32), index=True)
    spatial_check_reason: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ComplianceEvaluation(Base):
    """Server-side rule-engine verdict; never authored by the model adapter."""

    __tablename__ = "compliance_evaluations"
    __table_args__ = (
        UniqueConstraint("job_id", name="uq_compliance_evaluation_job"),
        CheckConstraint(
            "verdict IN ('compliant', 'deviation_detected', 'insufficient_evidence', 'needs_review')",
            name="ck_compliance_evaluation_verdict",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    work_order_id: Mapped[str] = mapped_column(
        ForeignKey("work_orders.id", ondelete="RESTRICT"), index=True
    )
    job_id: Mapped[str] = mapped_column(
        ForeignKey("verification_jobs.id", ondelete="CASCADE"), unique=True
    )
    rule_version: Mapped[str] = mapped_column(String(64))
    engine_version: Mapped[str] = mapped_column(String(64))
    expected_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    observed_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    difference_json: Mapped[list[Any]] = mapped_column(JSON, default=list)
    verdict: Mapped[str] = mapped_column(String(32), index=True)
    spatial_check_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
