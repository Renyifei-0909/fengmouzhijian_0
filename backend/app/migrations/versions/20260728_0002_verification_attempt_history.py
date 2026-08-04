"""Add append-only verification attempt and terminal outcome history.

Revision ID: 20260728_0002
Revises: 20260728_0001
Create Date: 2026-07-28
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260728_0002"
down_revision: str | None = "20260728_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


APPEND_ONLY_TABLES = (
    "verification_attempts",
    "verification_attempt_outcomes",
)


def _install_append_only_guards() -> None:
    dialect = op.get_context().dialect.name
    if dialect == "postgresql":
        op.execute(
            """
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
        )
    for table_name in APPEND_ONLY_TABLES:
        for operation in ("UPDATE", "DELETE"):
            trigger_name = f"trg_{table_name}_no_{operation.lower()}"
            if dialect == "sqlite":
                op.execute(
                    f"""
CREATE TRIGGER {trigger_name}
BEFORE {operation} ON {table_name}
BEGIN
    SELECT RAISE(ABORT, '{table_name} is append-only');
END
"""
                )
            elif dialect == "postgresql":
                op.execute(
                    f"""
CREATE TRIGGER {trigger_name}
BEFORE {operation} ON {table_name}
FOR EACH ROW
EXECUTE FUNCTION fengmou_reject_verification_attempt_mutation()
"""
                )
            else:
                raise RuntimeError(
                    f"Unsupported migration dialect for append-only guards: {dialect}"
                )


def upgrade() -> None:
    op.create_table(
        "verification_attempts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("job_id", sa.String(length=36), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("attempt_no", sa.Integer(), nullable=False),
        sa.Column("worker_id", sa.String(length=200), nullable=False),
        sa.Column("execution_mode", sa.String(length=32), nullable=False),
        sa.Column("analyzer_name", sa.String(length=100), nullable=False),
        sa.Column("analyzer_version", sa.String(length=64), nullable=False),
        sa.Column("evidence_sha256", sa.String(length=64), nullable=False),
        sa.Column("baseline_sha256", sa.String(length=64), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "execution_mode IN ('inline', 'external')",
            name="ck_verification_attempt_execution_mode",
        ),
        sa.CheckConstraint(
            "generation > 0",
            name="ck_verification_attempt_generation",
        ),
        sa.CheckConstraint(
            "max_attempts > 0",
            name="ck_verification_attempt_max_attempts",
        ),
        sa.CheckConstraint(
            "attempt_no > 0",
            name="ck_verification_attempt_number",
        ),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["verification_jobs.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "job_id",
            "generation",
            name="uq_verification_attempt_generation",
        ),
        sa.UniqueConstraint(
            "job_id",
            "attempt_no",
            name="uq_verification_attempt_number",
        ),
    )
    op.create_index(
        op.f("ix_verification_attempts_job_id"),
        "verification_attempts",
        ["job_id"],
        unique=False,
    )

    op.create_table(
        "verification_attempt_outcomes",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("attempt_id", sa.String(length=36), nullable=False),
        sa.Column("disposition", sa.String(length=32), nullable=False),
        sa.Column("stage", sa.String(length=100), nullable=True),
        sa.Column("result_json", sa.JSON(none_as_null=True), nullable=True),
        sa.Column("result_sha256", sa.String(length=64), nullable=True),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("error_retryable", sa.Boolean(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("upstream_status", sa.Integer(), nullable=True),
        sa.Column("dead_lettered", sa.Boolean(), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "disposition IN ('committed_success', 'committed_failure', "
            "'lease_expired', 'lease_lost', 'write_fenced')",
            name="ck_verification_attempt_outcome_disposition",
        ),
        sa.CheckConstraint(
            "(disposition = 'committed_success' "
            "AND result_json IS NOT NULL AND result_sha256 IS NOT NULL) "
            "OR (disposition <> 'committed_success' "
            "AND result_json IS NULL AND result_sha256 IS NULL)",
            name="ck_verification_attempt_outcome_result",
        ),
        sa.ForeignKeyConstraint(
            ["attempt_id"],
            ["verification_attempts.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "attempt_id",
            name="uq_verification_attempt_outcome_attempt",
        ),
    )
    op.create_index(
        op.f("ix_verification_attempt_outcomes_disposition"),
        "verification_attempt_outcomes",
        ["disposition"],
        unique=False,
    )
    op.create_index(
        op.f("ix_verification_attempt_outcomes_result_sha256"),
        "verification_attempt_outcomes",
        ["result_sha256"],
        unique=False,
    )
    _install_append_only_guards()


def downgrade() -> None:
    op.drop_index(
        op.f("ix_verification_attempt_outcomes_result_sha256"),
        table_name="verification_attempt_outcomes",
    )
    op.drop_index(
        op.f("ix_verification_attempt_outcomes_disposition"),
        table_name="verification_attempt_outcomes",
    )
    op.drop_table("verification_attempt_outcomes")
    op.drop_index(
        op.f("ix_verification_attempts_job_id"),
        table_name="verification_attempts",
    )
    op.drop_table("verification_attempts")
    if op.get_context().dialect.name == "postgresql":
        op.execute(
            "DROP FUNCTION IF EXISTS "
            "fengmou_reject_verification_attempt_mutation()"
        )
