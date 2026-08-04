"""Establish the versioned Alpha11 application schema baseline.

Revision ID: 20260728_0001
Revises:
Create Date: 2026-07-28
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260728_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "audit_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("entity_type", sa.String(length=64), nullable=False),
        sa.Column("entity_id", sa.String(length=64), nullable=False),
        sa.Column("action", sa.String(length=100), nullable=False),
        sa.Column("actor", sa.String(length=100), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_audit_events_action"), "audit_events", ["action"], unique=False)
    op.create_index(op.f("ix_audit_events_entity_id"), "audit_events", ["entity_id"], unique=False)
    op.create_index(op.f("ix_audit_events_entity_type"), "audit_events", ["entity_type"], unique=False)

    op.create_table(
        "projects",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("location", sa.String(length=300), nullable=False),
        sa.Column("manager", sa.String(length=100), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_projects_code"), "projects", ["code"], unique=True)
    op.create_index(op.f("ix_projects_status"), "projects", ["status"], unique=False)

    op.create_table(
        "design_baselines",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("site_id", sa.String(length=100), nullable=False),
        sa.Column("procedure_code", sa.String(length=100), nullable=False),
        sa.Column("version", sa.String(length=64), nullable=False),
        sa.Column("source_type", sa.String(length=32), nullable=False),
        sa.Column("expected", sa.JSON(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "project_id",
            "site_id",
            "procedure_code",
            "version",
            name="uq_baseline_scope",
        ),
    )
    op.create_index(
        op.f("ix_design_baselines_procedure_code"),
        "design_baselines",
        ["procedure_code"],
        unique=False,
    )
    op.create_index(
        op.f("ix_design_baselines_project_id"),
        "design_baselines",
        ["project_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_design_baselines_sha256"),
        "design_baselines",
        ["sha256"],
        unique=False,
    )
    op.create_index(
        op.f("ix_design_baselines_site_id"),
        "design_baselines",
        ["site_id"],
        unique=False,
    )

    op.create_table(
        "sensor_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("site_id", sa.String(length=100), nullable=False),
        sa.Column("device_id", sa.String(length=100), nullable=False),
        sa.Column("kind", sa.String(length=100), nullable=False),
        sa.Column("value", sa.Float(), nullable=False),
        sa.Column("unit", sa.String(length=32), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_sensor_events_captured_at"),
        "sensor_events",
        ["captured_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_sensor_events_device_id"),
        "sensor_events",
        ["device_id"],
        unique=False,
    )
    op.create_index(op.f("ix_sensor_events_kind"), "sensor_events", ["kind"], unique=False)
    op.create_index(
        op.f("ix_sensor_events_project_id"),
        "sensor_events",
        ["project_id"],
        unique=False,
    )
    op.create_index(op.f("ix_sensor_events_sha256"), "sensor_events", ["sha256"], unique=False)
    op.create_index(op.f("ix_sensor_events_site_id"), "sensor_events", ["site_id"], unique=False)

    op.create_table(
        "evidence_assets",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("baseline_id", sa.String(length=36), nullable=False),
        sa.Column("original_name", sa.String(length=255), nullable=False),
        sa.Column("stored_name", sa.String(length=255), nullable=False),
        sa.Column("storage_path", sa.Text(), nullable=False),
        sa.Column("content_type", sa.String(length=120), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("device_id", sa.String(length=100), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["baseline_id"], ["design_baselines.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("stored_name"),
    )
    op.create_index(
        op.f("ix_evidence_assets_baseline_id"),
        "evidence_assets",
        ["baseline_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_evidence_assets_project_id"),
        "evidence_assets",
        ["project_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_evidence_assets_sha256"),
        "evidence_assets",
        ["sha256"],
        unique=False,
    )

    op.create_table(
        "verification_jobs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("baseline_id", sa.String(length=36), nullable=False),
        sa.Column("evidence_id", sa.String(length=36), nullable=False),
        sa.Column("analyzer_name", sa.String(length=100), nullable=False),
        sa.Column("analyzer_version", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("progress", sa.Integer(), nullable=False),
        sa.Column("result_json", sa.JSON(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["baseline_id"], ["design_baselines.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["evidence_id"], ["evidence_assets.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("evidence_id"),
    )
    op.create_index(
        op.f("ix_verification_jobs_baseline_id"),
        "verification_jobs",
        ["baseline_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_verification_jobs_project_id"),
        "verification_jobs",
        ["project_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_verification_jobs_status"),
        "verification_jobs",
        ["status"],
        unique=False,
    )

    op.create_table(
        "human_reviews",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("job_id", sa.String(length=36), nullable=False),
        sa.Column("decision", sa.String(length=32), nullable=False),
        sa.Column("reviewer", sa.String(length=100), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["job_id"], ["verification_jobs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_id"),
    )

    op.create_table(
        "structured_reports",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("job_id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("schema_version", sa.String(length=32), nullable=False),
        sa.Column("content_json", sa.JSON(), nullable=False),
        sa.Column("json_path", sa.Text(), nullable=False),
        sa.Column("html_path", sa.Text(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("html_sha256", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["job_id"], ["verification_jobs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_id"),
    )
    op.create_index(
        op.f("ix_structured_reports_project_id"),
        "structured_reports",
        ["project_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_structured_reports_sha256"),
        "structured_reports",
        ["sha256"],
        unique=False,
    )

    op.create_table(
        "verification_job_leases",
        sa.Column("job_id", sa.String(length=36), nullable=False),
        sa.Column("owner_id", sa.String(length=200), nullable=True),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(length=100), nullable=True),
        sa.Column("last_error_retryable", sa.Boolean(), nullable=True),
        sa.Column("dead_lettered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "attempt_count >= 0",
            name="ck_verification_lease_attempt_count",
        ),
        sa.CheckConstraint("generation >= 0", name="ck_verification_lease_generation"),
        sa.ForeignKeyConstraint(["job_id"], ["verification_jobs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("job_id"),
    )
    op.create_index(
        op.f("ix_verification_job_leases_lease_expires_at"),
        "verification_job_leases",
        ["lease_expires_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_verification_job_leases_owner_id"),
        "verification_job_leases",
        ["owner_id"],
        unique=False,
    )

    op.create_table(
        "proof_records",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("archive_id", sa.String(length=64), nullable=False),
        sa.Column("report_id", sa.String(length=36), nullable=False),
        sa.Column("purpose", sa.String(length=32), nullable=False),
        sa.Column("evidence_grade", sa.Boolean(), nullable=False),
        sa.Column("merkle_root", sa.String(length=64), nullable=False),
        sa.Column("manifest_sha256", sa.String(length=64), nullable=False),
        sa.Column("archive_sha256", sa.String(length=64), nullable=False),
        sa.Column("previous_record_hash", sa.String(length=64), nullable=False),
        sa.Column("record_hash", sa.String(length=64), nullable=False),
        sa.Column("archive_path", sa.Text(), nullable=False),
        sa.Column("ledger_index", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["report_id"], ["structured_reports.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("report_id"),
    )
    op.create_index(
        op.f("ix_proof_records_archive_id"),
        "proof_records",
        ["archive_id"],
        unique=True,
    )
    op.create_index(
        op.f("ix_proof_records_archive_sha256"),
        "proof_records",
        ["archive_sha256"],
        unique=False,
    )
    op.create_index(
        op.f("ix_proof_records_manifest_sha256"),
        "proof_records",
        ["manifest_sha256"],
        unique=False,
    )
    op.create_index(
        op.f("ix_proof_records_record_hash"),
        "proof_records",
        ["record_hash"],
        unique=True,
    )

    op.create_table(
        "seal_operations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("job_id", sa.String(length=36), nullable=False),
        sa.Column("review_id", sa.String(length=36), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("report_id", sa.String(length=36), nullable=False),
        sa.Column("archive_id", sa.String(length=64), nullable=False),
        sa.Column("report_status", sa.String(length=32), nullable=True),
        sa.Column("purpose", sa.String(length=32), nullable=True),
        sa.Column("evidence_grade", sa.Boolean(), nullable=False),
        sa.Column("report_content_json", sa.JSON(), nullable=True),
        sa.Column("report_sha256", sa.String(length=64), nullable=True),
        sa.Column("html_sha256", sa.String(length=64), nullable=True),
        sa.Column("manifest_sha256", sa.String(length=64), nullable=True),
        sa.Column("archive_sha256", sa.String(length=64), nullable=True),
        sa.Column("merkle_root", sa.String(length=64), nullable=True),
        sa.Column("ledger_index", sa.Integer(), nullable=True),
        sa.Column("previous_record_hash", sa.String(length=64), nullable=True),
        sa.Column("record_hash", sa.String(length=64), nullable=True),
        sa.Column("ledger_row_json", sa.JSON(), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["job_id"], ["verification_jobs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["review_id"], ["human_reviews.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("archive_id"),
        sa.UniqueConstraint("report_id"),
        sa.UniqueConstraint("review_id"),
    )
    op.create_index(
        op.f("ix_seal_operations_job_id"),
        "seal_operations",
        ["job_id"],
        unique=True,
    )
    op.create_index(
        op.f("ix_seal_operations_state"),
        "seal_operations",
        ["state"],
        unique=False,
    )

    op.create_table(
        "finding_cases",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("source_job_id", sa.String(length=36), nullable=False),
        sa.Column("source_evidence_id", sa.String(length=36), nullable=False),
        sa.Column("baseline_id", sa.String(length=36), nullable=False),
        sa.Column("finding_key", sa.String(length=64), nullable=False),
        sa.Column("finding_index", sa.Integer(), nullable=False),
        sa.Column("finding_sha256", sa.String(length=64), nullable=False),
        sa.Column("source_result_sha256", sa.String(length=64), nullable=False),
        sa.Column("source_finding_json", sa.JSON(), nullable=False),
        sa.Column("analyzer_name", sa.String(length=100), nullable=False),
        sa.Column("analyzer_version", sa.String(length=100), nullable=False),
        sa.Column("analysis_mode", sa.String(length=100), nullable=False),
        sa.Column("source_synthetic", sa.Boolean(), nullable=False),
        sa.Column("source_evidence_grade", sa.Boolean(), nullable=False),
        sa.Column("finding_code", sa.String(length=100), nullable=False),
        sa.Column("proposed_severity", sa.String(length=32), nullable=False),
        sa.Column("finding_message", sa.Text(), nullable=False),
        sa.Column("scope", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("confirmed_severity", sa.String(length=32), nullable=True),
        sa.Column("decision_reason", sa.Text(), nullable=True),
        sa.Column("acknowledged_by", sa.String(length=100), nullable=True),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("assigned_to", sa.String(length=100), nullable=True),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("confirmed_by", sa.String(length=100), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_by", sa.String(length=100), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closure_proof_id", sa.String(length=36), nullable=True),
        sa.Column("active_attempt_no", sa.Integer(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("scope IN ('operational', 'demo')", name="ck_finding_case_scope"),
        sa.CheckConstraint(
            "status IN ('pending_triage', 'open', 'remediation_in_progress', "
            "'verification_pending', 'closed', 'dismissed')",
            name="ck_finding_case_status",
        ),
        sa.ForeignKeyConstraint(["baseline_id"], ["design_baselines.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["closure_proof_id"], ["proof_records.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["source_evidence_id"],
            ["evidence_assets.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["source_job_id"], ["verification_jobs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("finding_key", name="uq_finding_case_key"),
        sa.UniqueConstraint(
            "source_job_id",
            "finding_index",
            name="uq_finding_case_position",
        ),
    )
    for index_name, columns, unique in (
        ("ix_finding_cases_baseline_id", ["baseline_id"], False),
        ("ix_finding_cases_finding_code", ["finding_code"], False),
        ("ix_finding_cases_finding_key", ["finding_key"], True),
        ("ix_finding_cases_finding_sha256", ["finding_sha256"], False),
        ("ix_finding_cases_project_id", ["project_id"], False),
        ("ix_finding_cases_proposed_severity", ["proposed_severity"], False),
        ("ix_finding_cases_scope", ["scope"], False),
        ("ix_finding_cases_source_evidence_id", ["source_evidence_id"], False),
        ("ix_finding_cases_source_job_id", ["source_job_id"], False),
        ("ix_finding_cases_source_result_sha256", ["source_result_sha256"], False),
        ("ix_finding_cases_status", ["status"], False),
    ):
        op.create_index(op.f(index_name), "finding_cases", columns, unique=unique)

    op.create_table(
        "finding_case_commands",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("case_id", sa.String(length=36), nullable=False),
        sa.Column("command", sa.String(length=64), nullable=False),
        sa.Column("from_status", sa.String(length=32), nullable=False),
        sa.Column("to_status", sa.String(length=32), nullable=False),
        sa.Column("actor", sa.String(length=100), nullable=False),
        sa.Column("actor_role", sa.String(length=32), nullable=False),
        sa.Column("payload_sha256", sa.String(length=64), nullable=False),
        sa.Column("result_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["case_id"], ["finding_cases.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_finding_case_commands_case_id"),
        "finding_case_commands",
        ["case_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_finding_case_commands_command"),
        "finding_case_commands",
        ["command"],
        unique=False,
    )

    op.create_table(
        "remediation_attempts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("case_id", sa.String(length=36), nullable=False),
        sa.Column("attempt_no", sa.Integer(), nullable=False),
        sa.Column("client_request_id", sa.String(length=36), nullable=False),
        sa.Column("action_description", sa.Text(), nullable=False),
        sa.Column("submitted_by", sa.String(length=100), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("verification_job_id", sa.String(length=36), nullable=True),
        sa.Column("resolution_decision", sa.String(length=32), nullable=False),
        sa.Column("resolution_note", sa.Text(), nullable=True),
        sa.Column("resolved_by", sa.String(length=100), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("report_id", sa.String(length=36), nullable=True),
        sa.Column("proof_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "resolution_decision IN ('pending', 'resolved', 'not_resolved')",
            name="ck_remediation_resolution",
        ),
        sa.ForeignKeyConstraint(["case_id"], ["finding_cases.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["proof_id"], ["proof_records.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["report_id"], ["structured_reports.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["verification_job_id"],
            ["verification_jobs.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "case_id",
            "attempt_no",
            name="uq_remediation_attempt_number",
        ),
    )
    op.create_index(
        op.f("ix_remediation_attempts_case_id"),
        "remediation_attempts",
        ["case_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_remediation_attempts_client_request_id"),
        "remediation_attempts",
        ["client_request_id"],
        unique=True,
    )
    op.create_index(
        op.f("ix_remediation_attempts_verification_job_id"),
        "remediation_attempts",
        ["verification_job_id"],
        unique=True,
    )


def downgrade() -> None:
    raise RuntimeError(
        "The initial evidence-bearing schema baseline is intentionally irreversible. "
        "Restore a verified backup instead of dropping all application tables."
    )
