"""Add QGIS work-order compliance vertical slice tables.

Revision ID: 20260731_0003
Revises: 20260728_0002
Create Date: 2026-07-31
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260731_0003"
down_revision: str | None = "20260728_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "design_packages",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("package_code", sa.String(length=100), nullable=False),
        sa.Column("source_filename", sa.String(length=255), nullable=False),
        sa.Column("source_sha256", sa.String(length=64), nullable=False),
        sa.Column("source_type", sa.String(length=32), nullable=False),
        sa.Column("purpose", sa.String(length=32), nullable=False),
        sa.Column("synthetic", sa.Boolean(), nullable=False),
        sa.Column("source_crs_epsg", sa.Integer(), nullable=False),
        sa.Column("layers_json", sa.JSON(), nullable=False),
        sa.Column("field_mapping_json", sa.JSON(), nullable=False),
        sa.Column("redaction_policy_json", sa.JSON(), nullable=False),
        sa.Column("import_status", sa.String(length=32), nullable=False),
        sa.Column("import_warnings_json", sa.JSON(), nullable=False),
        sa.Column("object_count", sa.Integer(), nullable=False),
        sa.Column("storage_path", sa.Text(), nullable=True),
        sa.Column("imported_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "source_type IN ('synthetic_json', 'gpkg_derivative')",
            name="ck_design_package_source_type",
        ),
        sa.CheckConstraint(
            "purpose IN ('demo', 'controlled')",
            name="ck_design_package_purpose",
        ),
        sa.CheckConstraint(
            "import_status IN ('pending', 'completed', 'failed', 'partial')",
            name="ck_design_package_import_status",
        ),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_design_packages_project_id", "design_packages", ["project_id"])
    op.create_index("ix_design_packages_package_code", "design_packages", ["package_code"])
    op.create_index("ix_design_packages_source_sha256", "design_packages", ["source_sha256"])
    op.create_index("ix_design_packages_import_status", "design_packages", ["import_status"])

    op.create_table(
        "engineering_objects",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("design_package_id", sa.String(length=36), nullable=False),
        sa.Column("object_code", sa.String(length=100), nullable=False),
        sa.Column("object_type", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("source_layer", sa.String(length=100), nullable=False),
        sa.Column("source_feature_id", sa.String(length=100), nullable=False),
        sa.Column("geometry_type", sa.String(length=32), nullable=False),
        sa.Column("geometry_wgs84_json", sa.JSON(), nullable=False),
        sa.Column("geometry_source_crs_epsg", sa.Integer(), nullable=False),
        sa.Column("attributes_snapshot_json", sa.JSON(), nullable=False),
        sa.Column("expected_rules_json", sa.JSON(), nullable=False),
        sa.Column("design_version", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "object_type IN ('pipe_route', 'trench', 'infrastructure_point')",
            name="ck_engineering_object_type",
        ),
        sa.CheckConstraint(
            "geometry_type IN ('Point', 'LineString', 'Polygon')",
            name="ck_engineering_object_geometry_type",
        ),
        sa.ForeignKeyConstraint(["design_package_id"], ["design_packages.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "object_code", name="uq_engineering_object_code"),
    )
    op.create_index("ix_engineering_objects_project_id", "engineering_objects", ["project_id"])
    op.create_index(
        "ix_engineering_objects_design_package_id",
        "engineering_objects",
        ["design_package_id"],
    )
    op.create_index("ix_engineering_objects_object_code", "engineering_objects", ["object_code"])
    op.create_index("ix_engineering_objects_object_type", "engineering_objects", ["object_type"])

    op.create_table(
        "work_orders",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("engineering_object_id", sa.String(length=36), nullable=False),
        sa.Column("baseline_id", sa.String(length=36), nullable=True),
        sa.Column("work_order_code", sa.String(length=100), nullable=False),
        sa.Column("procedure_code", sa.String(length=100), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("design_version", sa.String(length=64), nullable=False),
        sa.Column("design_snapshot_json", sa.JSON(), nullable=False),
        sa.Column("geometry_snapshot_json", sa.JSON(), nullable=False),
        sa.Column("rules_snapshot_json", sa.JSON(), nullable=False),
        sa.Column("spatial_tolerance_m", sa.Float(), nullable=False),
        sa.Column("gps_accuracy_threshold_m", sa.Float(), nullable=False),
        sa.Column("assigned_to", sa.String(length=100), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('draft', 'assigned', 'evidence_uploaded', 'analyzing', "
            "'needs_review', 'approved', 'deviation', 'remediating', 'closed')",
            name="ck_work_order_status",
        ),
        sa.ForeignKeyConstraint(
            ["engineering_object_id"],
            ["engineering_objects.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["baseline_id"], ["design_baselines.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "work_order_code", name="uq_work_order_code"),
    )
    op.create_index("ix_work_orders_project_id", "work_orders", ["project_id"])
    op.create_index(
        "ix_work_orders_engineering_object_id",
        "work_orders",
        ["engineering_object_id"],
    )
    op.create_index("ix_work_orders_baseline_id", "work_orders", ["baseline_id"])
    op.create_index("ix_work_orders_work_order_code", "work_orders", ["work_order_code"])
    op.create_index("ix_work_orders_procedure_code", "work_orders", ["procedure_code"])
    op.create_index("ix_work_orders_status", "work_orders", ["status"])

    op.create_table(
        "evidence_captures",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("work_order_id", sa.String(length=36), nullable=False),
        sa.Column("evidence_id", sa.String(length=36), nullable=False),
        sa.Column("verification_job_id", sa.String(length=36), nullable=True),
        sa.Column("client_captured_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("server_received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("latitude", sa.Float(), nullable=True),
        sa.Column("longitude", sa.Float(), nullable=True),
        sa.Column("accuracy_m", sa.Float(), nullable=True),
        sa.Column("location_source", sa.String(length=32), nullable=False),
        sa.Column("is_synthetic_location", sa.Boolean(), nullable=False),
        sa.Column("distance_to_target_m", sa.Float(), nullable=True),
        sa.Column("tolerance_m", sa.Float(), nullable=False),
        sa.Column("gps_accuracy_threshold_m", sa.Float(), nullable=False),
        sa.Column("spatial_check_status", sa.String(length=32), nullable=False),
        sa.Column("spatial_check_reason", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "spatial_check_status IN ('passed', 'failed', 'skipped', 'unavailable')",
            name="ck_evidence_capture_spatial_status",
        ),
        sa.CheckConstraint(
            "location_source IN ('device_gps', 'synthetic_demo', 'manual', 'unknown')",
            name="ck_evidence_capture_location_source",
        ),
        sa.ForeignKeyConstraint(["evidence_id"], ["evidence_assets.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["verification_job_id"],
            ["verification_jobs.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(["work_order_id"], ["work_orders.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("evidence_id", name="uq_evidence_capture_evidence"),
    )
    op.create_index("ix_evidence_captures_project_id", "evidence_captures", ["project_id"])
    op.create_index("ix_evidence_captures_work_order_id", "evidence_captures", ["work_order_id"])
    op.create_index(
        "ix_evidence_captures_verification_job_id",
        "evidence_captures",
        ["verification_job_id"],
    )
    op.create_index(
        "ix_evidence_captures_spatial_check_status",
        "evidence_captures",
        ["spatial_check_status"],
    )

    op.create_table(
        "compliance_evaluations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("work_order_id", sa.String(length=36), nullable=False),
        sa.Column("job_id", sa.String(length=36), nullable=False),
        sa.Column("rule_version", sa.String(length=64), nullable=False),
        sa.Column("engine_version", sa.String(length=64), nullable=False),
        sa.Column("expected_json", sa.JSON(), nullable=False),
        sa.Column("observed_json", sa.JSON(), nullable=False),
        sa.Column("difference_json", sa.JSON(), nullable=False),
        sa.Column("verdict", sa.String(length=32), nullable=False),
        sa.Column("spatial_check_status", sa.String(length=32), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "verdict IN ('compliant', 'deviation_detected', 'insufficient_evidence', 'needs_review')",
            name="ck_compliance_evaluation_verdict",
        ),
        sa.ForeignKeyConstraint(["job_id"], ["verification_jobs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["work_order_id"], ["work_orders.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_id", name="uq_compliance_evaluation_job"),
    )
    op.create_index(
        "ix_compliance_evaluations_project_id",
        "compliance_evaluations",
        ["project_id"],
    )
    op.create_index(
        "ix_compliance_evaluations_work_order_id",
        "compliance_evaluations",
        ["work_order_id"],
    )
    op.create_index("ix_compliance_evaluations_verdict", "compliance_evaluations", ["verdict"])


def downgrade() -> None:
    op.drop_table("compliance_evaluations")
    op.drop_table("evidence_captures")
    op.drop_table("work_orders")
    op.drop_table("engineering_objects")
    op.drop_table("design_packages")
