"""Standard GPKG source_type, import_contract_version, idempotency unique key.

Revision ID: 20260801_0004
Revises: 20260731_0003
Create Date: 2026-08-01

Per ADR-002 / P1-3A:
- source_type may be standard_gpkg
- import_contract_version stores gpkg-import-contract-v* (empty string for legacy)
- unique (project_id, source_sha256, import_contract_version) for idempotent re-submit
"""

from typing import Sequence

from alembic import context, op
import sqlalchemy as sa


revision: str = "20260801_0004"
down_revision: str | None = "20260731_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SOURCE_CHECK = (
    "source_type IN ('synthetic_json', 'gpkg_derivative', 'standard_gpkg')"
)
_SOURCE_CHECK_LEGACY = "source_type IN ('synthetic_json', 'gpkg_derivative')"


def _upgrade_sqlite_online() -> None:
    """SQLite cannot ALTER CHECK in place; batch rebuild is required online."""
    with op.batch_alter_table("design_packages") as batch_op:
        batch_op.add_column(
            sa.Column(
                "import_contract_version",
                sa.String(length=64),
                nullable=False,
                server_default="",
            )
        )
        batch_op.create_index(
            "ix_design_packages_import_contract_version",
            ["import_contract_version"],
            unique=False,
        )
        batch_op.drop_constraint("ck_design_package_source_type", type_="check")
        batch_op.create_check_constraint(
            "ck_design_package_source_type",
            _SOURCE_CHECK,
        )
        batch_op.create_unique_constraint(
            "uq_design_package_idempotency",
            ["project_id", "source_sha256", "import_contract_version"],
        )
    with op.batch_alter_table("design_packages") as batch_op:
        batch_op.alter_column(
            "import_contract_version",
            existing_type=sa.String(length=64),
            server_default=None,
            nullable=False,
        )


def _upgrade_postgres() -> None:
    op.add_column(
        "design_packages",
        sa.Column(
            "import_contract_version",
            sa.String(length=64),
            nullable=False,
            server_default="",
        ),
    )
    op.create_index(
        "ix_design_packages_import_contract_version",
        "design_packages",
        ["import_contract_version"],
        unique=False,
    )
    op.drop_constraint("ck_design_package_source_type", "design_packages", type_="check")
    op.create_check_constraint(
        "ck_design_package_source_type",
        "design_packages",
        _SOURCE_CHECK,
    )
    op.create_unique_constraint(
        "uq_design_package_idempotency",
        "design_packages",
        ["project_id", "source_sha256", "import_contract_version"],
    )
    op.alter_column(
        "design_packages",
        "import_contract_version",
        existing_type=sa.String(length=64),
        server_default=None,
        nullable=False,
    )


def _upgrade_offline_sql() -> None:
    """Offline SQL mode cannot reflect SQLite tables for batch rebuild.

    Emit portable statements for review and PostgreSQL offline apply.
    Live SQLite upgrades use batch rebuild online (see _upgrade_sqlite_online).
    """
    op.execute(
        "ALTER TABLE design_packages ADD COLUMN import_contract_version "
        "VARCHAR(64) DEFAULT '' NOT NULL"
    )
    op.execute(
        "CREATE INDEX ix_design_packages_import_contract_version "
        "ON design_packages (import_contract_version)"
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_design_package_idempotency "
        "ON design_packages (project_id, source_sha256, import_contract_version)"
    )
    # PostgreSQL-style CHECK replacement (no-op-safe documentation for offline).
    op.execute(
        "ALTER TABLE design_packages DROP CONSTRAINT IF EXISTS ck_design_package_source_type"
    )
    op.execute(
        "ALTER TABLE design_packages ADD CONSTRAINT ck_design_package_source_type "
        f"CHECK ({_SOURCE_CHECK})"
    )


def upgrade() -> None:
    if context.is_offline_mode():
        _upgrade_offline_sql()
        return
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        _upgrade_sqlite_online()
    else:
        _upgrade_postgres()


def _refuse_downgrade_if_standard_gpkg(bind) -> None:
    """Never silently rewrite standard_gpkg as gpkg_derivative/synthetic_json."""
    result = bind.execute(
        sa.text(
            "SELECT COUNT(*) AS c FROM design_packages WHERE source_type = 'standard_gpkg'"
        )
    )
    row = result.fetchone()
    count = int(row[0] if row is not None else 0)
    if count > 0:
        raise RuntimeError(
            "Refusing downgrade of 20260801_0004: "
            f"{count} design_packages row(s) use source_type=standard_gpkg. "
            "Export/backup those packages before downgrade; "
            "rows will not be silently rewritten to gpkg_derivative or synthetic_json."
        )


def downgrade() -> None:
    if context.is_offline_mode():
        # Online path refuses when standard_gpkg rows exist. Offline SQL is documentary:
        # operators must ensure zero standard_gpkg rows before applying downgrade SQL.
        op.execute(
            "/* GUARD: do not apply if design_packages.source_type='standard_gpkg' "
            "exists; online alembic downgrade refuses automatically */"
        )
        op.execute("DROP INDEX IF EXISTS uq_design_package_idempotency")
        op.execute("DROP INDEX IF EXISTS ix_design_packages_import_contract_version")
        op.execute(
            "ALTER TABLE design_packages DROP CONSTRAINT IF EXISTS ck_design_package_source_type"
        )
        op.execute(
            "ALTER TABLE design_packages ADD CONSTRAINT ck_design_package_source_type "
            f"CHECK ({_SOURCE_CHECK_LEGACY})"
        )
        op.execute("ALTER TABLE design_packages DROP COLUMN import_contract_version")
        return

    bind = op.get_bind()
    _refuse_downgrade_if_standard_gpkg(bind)

    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("design_packages") as batch_op:
            batch_op.drop_constraint("uq_design_package_idempotency", type_="unique")
            batch_op.drop_index("ix_design_packages_import_contract_version")
            batch_op.drop_constraint("ck_design_package_source_type", type_="check")
            batch_op.create_check_constraint(
                "ck_design_package_source_type",
                _SOURCE_CHECK_LEGACY,
            )
            batch_op.drop_column("import_contract_version")
    else:
        op.drop_constraint(
            "uq_design_package_idempotency", "design_packages", type_="unique"
        )
        op.drop_index(
            "ix_design_packages_import_contract_version", table_name="design_packages"
        )
        op.drop_constraint(
            "ck_design_package_source_type", "design_packages", type_="check"
        )
        op.create_check_constraint(
            "ck_design_package_source_type",
            "design_packages",
            _SOURCE_CHECK_LEGACY,
        )
        op.drop_column("design_packages", "import_contract_version")
