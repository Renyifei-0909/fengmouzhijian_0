from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import dataclass
from io import StringIO
import json
from pathlib import Path
import re
import sys
from typing import Any, Iterator

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import CheckConstraint, Connection, Engine, MetaData, inspect, text

from . import models as _models  # noqa: F401  # Register every table on Base.metadata.
from .database import Base, Database
from .file_lock import acquire_exclusive_file_lock, release_file_lock


MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"
ALEMBIC_VERSION_TABLE = "alembic_version"
POSTGRES_MIGRATION_LOCK_KEY = int.from_bytes(b"FENGMOU", byteorder="big", signed=False)
ALPHA11_BASELINE_REVISION = "20260728_0001"
# Tables introduced after the Alpha11 baseline revision (0001).
# Used only for explicit legacy adoption matching of pre-Alembic Alpha11 DBs.
POST_ALPHA11_TABLES = frozenset(
    {
        # Alpha13
        "verification_attempts",
        "verification_attempt_outcomes",
        # Alpha18 work-order slice
        "design_packages",
        "engineering_objects",
        "work_orders",
        "evidence_captures",
        "compliance_evaluations",
    }
)
# Back-compat alias used by older comments/tests.
ALPHA13_TABLES = frozenset(
    {
        "verification_attempts",
        "verification_attempt_outcomes",
    }
)


class SchemaMigrationError(RuntimeError):
    """The database schema cannot be safely prepared or verified."""


@dataclass(frozen=True, slots=True)
class SchemaStatus:
    mode: str
    expected_heads: tuple[str, ...]
    current_heads: tuple[str, ...]
    managed_by_alembic: bool
    at_head: bool
    drift_free: bool
    legacy_adopted: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "expected_heads": list(self.expected_heads),
            "current_heads": list(self.current_heads),
            "managed_by_alembic": self.managed_by_alembic,
            "at_head": self.at_head,
            "drift_free": self.drift_free,
            "legacy_adopted": self.legacy_adopted,
        }


def alembic_config(
    *,
    connection: Connection | None = None,
    database_url: str | None = None,
    output_buffer: StringIO | None = None,
) -> Config:
    config = Config(output_buffer=output_buffer)
    config.set_main_option("script_location", MIGRATIONS_DIR.as_posix())
    if connection is not None:
        config.attributes["connection"] = connection
    if database_url is not None:
        config.attributes["database_url"] = database_url
    return config


def expected_schema_heads() -> tuple[str, ...]:
    script = ScriptDirectory.from_config(alembic_config())
    return tuple(sorted(script.get_heads()))


def _current_heads(connection: Connection) -> tuple[str, ...]:
    context = MigrationContext.configure(connection)
    return tuple(sorted(context.get_current_heads()))


def _metadata_diffs(
    connection: Connection,
    target_metadata: MetaData | None = None,
) -> list[Any]:
    metadata = target_metadata or Base.metadata
    context = MigrationContext.configure(
        connection,
        opts={
            "compare_type": True,
            "compare_server_default": True,
            "target_metadata": metadata,
        },
    )
    diffs = list(compare_metadata(context, metadata))
    diffs.extend(_check_constraint_diffs(connection, metadata))
    diffs.extend(_append_only_trigger_diffs(connection, metadata))
    return diffs


def _normalized_check_sql(value: object) -> str:
    return re.sub(r"\s+", " ", str(value)).strip().lower()


def _check_constraint_diffs(
    connection: Connection,
    target_metadata: MetaData,
) -> list[Any]:
    """Cover named checks that Alembic autogenerate does not compare reliably.

    SQLite preserves the submitted expression closely enough to compare both
    name and normalized SQL. PostgreSQL may rewrite equivalent expressions
    during reflection, so its portable gate compares the complete set of names;
    live PostgreSQL validation remains a separate deployment prerequisite.
    """

    inspector = inspect(connection)
    existing_tables = set(inspector.get_table_names())
    compare_sql = connection.dialect.name == "sqlite"
    diffs: list[Any] = []
    for table in target_metadata.sorted_tables:
        if table.name not in existing_tables:
            # ``compare_metadata`` already reports the missing table.
            continue
        expected = {
            (
                constraint.name or "<unnamed>",
                _normalized_check_sql(constraint.sqltext) if compare_sql else "",
            )
            for constraint in table.constraints
            if isinstance(constraint, CheckConstraint)
        }
        actual = {
            (
                str(item.get("name") or "<unnamed>"),
                _normalized_check_sql(item.get("sqltext") or "") if compare_sql else "",
            )
            for item in inspector.get_check_constraints(table.name)
        }
        if actual != expected:
            diffs.append(
                (
                    "check_constraint_drift",
                    table.name,
                    sorted(expected),
                    sorted(actual),
                )
            )
    return diffs


def _append_only_trigger_diffs(
    connection: Connection,
    target_metadata: MetaData,
) -> list[Any]:
    """Verify the database-level mutation guards Alembic cannot autogenerate."""

    if not ALPHA13_TABLES <= set(target_metadata.tables):
        return []
    expected = set(_models.APPEND_ONLY_TRIGGER_NAMES)
    definitions: dict[str, str] = {}
    function_definition = ""
    if connection.dialect.name == "sqlite":
        definitions = {
            str(row["name"]): str(row["sql"] or "")
            for row in connection.execute(
                text(
                    "SELECT name, sql FROM sqlite_master "
                    "WHERE type = 'trigger' "
                    "AND tbl_name IN "
                    "('verification_attempts', 'verification_attempt_outcomes')"
                )
            ).mappings()
        }
        actual = set(definitions)
    elif connection.dialect.name == "postgresql":
        definitions = {
            str(row["name"]): str(row["definition"] or "")
            for row in connection.execute(
                text(
                    "SELECT trigger_row.tgname AS name, "
                    "pg_get_triggerdef(trigger_row.oid) AS definition "
                    "FROM pg_trigger AS trigger_row "
                    "JOIN pg_class AS table_row "
                    "ON table_row.oid = trigger_row.tgrelid "
                    "JOIN pg_namespace AS namespace_row "
                    "ON namespace_row.oid = table_row.relnamespace "
                    "WHERE NOT trigger_row.tgisinternal "
                    "AND namespace_row.nspname = current_schema() "
                    "AND table_row.relname IN "
                    "('verification_attempts', 'verification_attempt_outcomes')"
                )
            ).mappings()
        }
        actual = set(definitions)
        function_definition = str(
            connection.scalar(
                text(
                    "SELECT pg_get_functiondef(to_regprocedure("
                    "'fengmou_reject_verification_attempt_mutation()'))"
                )
            )
            or ""
        )
    else:
        return [
            (
                "append_only_trigger_drift",
                connection.dialect.name,
                sorted(expected),
                [],
            )
        ]
    invalid_definitions: list[str] = []
    for name, (table_name, operation) in _models.APPEND_ONLY_TRIGGER_TARGETS.items():
        normalized = _normalized_check_sql(definitions.get(name, ""))
        if (
            f"before {operation} on" not in normalized
            or table_name not in normalized
        ):
            invalid_definitions.append(name)
            continue
        if connection.dialect.name == "sqlite" and (
            "raise(abort" not in normalized
            or f"{table_name} is append-only" not in normalized
        ):
            invalid_definitions.append(name)
        if connection.dialect.name == "postgresql" and (
            "fengmou_reject_verification_attempt_mutation" not in normalized
        ):
            invalid_definitions.append(name)
    if connection.dialect.name == "postgresql":
        normalized_function = _normalized_check_sql(function_definition)
        if (
            "raise exception 'verification attempt history is append-only'"
            not in normalized_function
            or "23000" not in normalized_function
        ):
            invalid_definitions.append(
                "fengmou_reject_verification_attempt_mutation"
            )
    if actual == expected and not invalid_definitions:
        return []
    return [
        (
            "append_only_trigger_drift",
            sorted(expected),
            sorted(actual),
            sorted(invalid_definitions),
        )
    ]


def _alpha11_metadata() -> MetaData:
    """Build the exact Alpha11 (revision 0001) metadata for legacy adoption.

    Excludes every table introduced after the Alpha11 baseline so that a real
    0001-shaped database can be stamped and upgraded through 0002 and 0003.
    """

    metadata = MetaData()
    for table in Base.metadata.sorted_tables:
        if table.name not in POST_ALPHA11_TABLES:
            table.to_metadata(metadata)
    return metadata


def _diff_summary(diffs: list[Any]) -> str:
    summaries: list[str] = []
    for difference in diffs[:8]:
        if isinstance(difference, tuple) and difference:
            summaries.append(str(difference[0]))
        else:
            summaries.append(type(difference).__name__)
    suffix = "" if len(diffs) <= 8 else f", +{len(diffs) - 8} more"
    return ", ".join(summaries) + suffix


def _status_on_connection(
    connection: Connection,
    *,
    mode: str,
    require_head: bool,
    require_no_drift: bool,
    legacy_adopted: bool = False,
) -> SchemaStatus:
    expected = expected_schema_heads()
    current = _current_heads(connection)
    at_head = bool(expected) and set(current) == set(expected)
    if require_head and not at_head:
        raise SchemaMigrationError(
            "Database revision is not at the application head "
            f"(current={list(current) or ['unversioned']}, expected={list(expected)})"
        )
    diffs = _metadata_diffs(connection)
    drift_free = not diffs
    if require_no_drift and not drift_free:
        raise SchemaMigrationError(
            "Database schema differs from the application metadata: " + _diff_summary(diffs)
        )
    return SchemaStatus(
        mode=mode,
        expected_heads=expected,
        current_heads=current,
        managed_by_alembic=bool(current),
        at_head=at_head,
        drift_free=drift_free,
        legacy_adopted=legacy_adopted,
    )


def _sqlite_lock_path(engine: Engine) -> Path | None:
    database = engine.url.database
    if database in {None, "", ":memory:"}:
        return None
    if database.startswith("file:"):
        raise SchemaMigrationError("SQLite URI databases are not supported by the migration lock")
    path = Path(database).expanduser().resolve()
    return path.with_name(f"{path.name}.alembic.lock")


@contextmanager
def _migration_lock(engine: Engine, connection: Connection) -> Iterator[None]:
    dialect = connection.dialect.name
    if dialect == "sqlite":
        lock_path = _sqlite_lock_path(engine)
        if lock_path is None:
            yield
            return
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("a+b") as handle:
            acquire_exclusive_file_lock(handle)
            try:
                yield
            finally:
                release_file_lock(handle)
        return
    if dialect == "postgresql":
        # Transaction-scoped advisory locks are released even if migration DDL
        # aborts the transaction or the connection is closed unexpectedly.
        connection.execute(
            text("SELECT pg_advisory_xact_lock(:lock_key)"),
            {"lock_key": POSTGRES_MIGRATION_LOCK_KEY},
        )
        yield
        return
    raise SchemaMigrationError(f"Unsupported database dialect for migrations: {dialect}")


def _run_alembic(
    connection: Connection,
    action: str,
    target: str = "heads",
) -> None:
    config = alembic_config(connection=connection)
    try:
        if action == "upgrade":
            command.upgrade(config, target)
        elif action == "stamp":
            command.stamp(config, target)
        elif action == "downgrade":
            command.downgrade(config, target)
        else:
            raise AssertionError(f"Unknown Alembic action: {action}")
    except SchemaMigrationError:
        raise
    except Exception as exc:
        raise SchemaMigrationError(
            f"Alembic {action} failed ({type(exc).__name__})"
        ) from exc


def upgrade_database_schema(engine: Engine) -> SchemaStatus:
    try:
        with engine.begin() as connection:
            with _migration_lock(engine, connection):
                tables = set(inspect(connection).get_table_names())
                current = _current_heads(connection)
                business_tables = tables - {ALEMBIC_VERSION_TABLE}
                if business_tables and not current:
                    raise SchemaMigrationError(
                        "Database contains an unversioned legacy schema. "
                        "Run 'python -m app.schema adopt-legacy' after taking a backup; "
                        "automatic stamping is intentionally disabled."
                    )
                _run_alembic(connection, "upgrade")
                return _status_on_connection(
                    connection,
                    mode="upgrade",
                    require_head=True,
                    require_no_drift=True,
                )
    except SchemaMigrationError:
        raise
    except Exception as exc:
        raise SchemaMigrationError(
            f"Database schema upgrade failed ({type(exc).__name__})"
        ) from exc


def adopt_legacy_schema(engine: Engine) -> SchemaStatus:
    try:
        with engine.begin() as connection:
            with _migration_lock(engine, connection):
                current = _current_heads(connection)
                if current:
                    return _status_on_connection(
                        connection,
                        mode="adopt-legacy",
                        require_head=True,
                        require_no_drift=True,
                    )
                tables = set(inspect(connection).get_table_names()) - {ALEMBIC_VERSION_TABLE}
                if not tables:
                    raise SchemaMigrationError(
                        "Database has no legacy application tables; use the normal upgrade command"
                    )
                current_diffs = _metadata_diffs(connection)
                if not current_diffs:
                    _run_alembic(connection, "stamp")
                else:
                    alpha11_diffs = _metadata_diffs(
                        connection,
                        _alpha11_metadata(),
                    )
                    if alpha11_diffs:
                        raise SchemaMigrationError(
                            "Legacy schema does not exactly match either the current metadata "
                            "or the Alpha11 baseline and will not be stamped: "
                            + _diff_summary(alpha11_diffs)
                        )
                    _run_alembic(
                        connection,
                        "stamp",
                        ALPHA11_BASELINE_REVISION,
                    )
                    _run_alembic(connection, "upgrade")
                return _status_on_connection(
                    connection,
                    mode="adopt-legacy",
                    require_head=True,
                    require_no_drift=True,
                    legacy_adopted=True,
                )
    except SchemaMigrationError:
        raise
    except Exception as exc:
        raise SchemaMigrationError(
            f"Legacy schema adoption failed ({type(exc).__name__})"
        ) from exc


def verify_database_schema(engine: Engine) -> SchemaStatus:
    try:
        with engine.connect() as connection:
            return _status_on_connection(
                connection,
                mode="verify",
                require_head=True,
                require_no_drift=True,
            )
    except SchemaMigrationError:
        raise
    except Exception as exc:
        raise SchemaMigrationError(
            f"Database schema verification failed ({type(exc).__name__})"
        ) from exc


def prepare_database_schema(engine: Engine, *, mode: str) -> SchemaStatus:
    if mode == "create_all":
        try:
            Base.metadata.create_all(engine)
            with engine.connect() as connection:
                return _status_on_connection(
                    connection,
                    mode=mode,
                    require_head=False,
                    require_no_drift=False,
                )
        except Exception as exc:
            raise SchemaMigrationError(
                f"Test schema creation failed ({type(exc).__name__})"
            ) from exc
    if mode == "upgrade":
        return upgrade_database_schema(engine)
    if mode == "verify":
        return verify_database_schema(engine)
    raise SchemaMigrationError(f"Unsupported schema mode: {mode}")


def render_offline_upgrade_sql(database_url: str) -> str:
    output = StringIO()
    config = alembic_config(database_url=database_url, output_buffer=output)
    try:
        command.upgrade(config, "heads", sql=True)
    except Exception as exc:
        raise SchemaMigrationError(
            f"Offline Alembic rendering failed ({type(exc).__name__})"
        ) from exc
    return output.getvalue()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Prepare or verify the Fengmou application database schema"
    )
    parser.add_argument(
        "command",
        choices=("upgrade", "check", "adopt-legacy", "sql"),
        help=(
            "upgrade an empty/versioned database; check the current revision and metadata; "
            "explicitly stamp an exact pre-Alembic Alpha11 schema; or render offline SQL"
        ),
    )
    parser.add_argument(
        "--dialect",
        choices=("sqlite", "postgresql"),
        default="sqlite",
        help="offline SQL dialect (only used by the sql command)",
    )
    return parser.parse_args()


def main() -> int:
    from .config import Settings

    args = _parse_args()
    if args.command == "sql":
        database_url = (
            "sqlite:///fengmou-offline.db"
            if args.dialect == "sqlite"
            else "postgresql+psycopg://offline@localhost/fengmou"
        )
        try:
            print(render_offline_upgrade_sql(database_url), end="")
        except SchemaMigrationError as exc:
            print(f"schema error: {exc}", file=sys.stderr)
            return 1
        return 0

    try:
        settings = Settings.from_env()
    except ValueError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2
    database = Database(settings.database_url)
    try:
        if args.command == "upgrade":
            status = upgrade_database_schema(database.engine)
        elif args.command == "adopt-legacy":
            status = adopt_legacy_schema(database.engine)
        else:
            status = verify_database_schema(database.engine)
    except SchemaMigrationError as exc:
        print(f"schema error: {exc}", file=sys.stderr)
        return 1
    finally:
        database.engine.dispose()
    print(json.dumps(status.as_dict(), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
