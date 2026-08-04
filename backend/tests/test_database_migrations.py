from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect, text

from app.config import Settings
from app.database import Database
from app.main import create_app
from app.models import Project
from app.schema import (
    ALPHA11_BASELINE_REVISION,
    SchemaMigrationError,
    adopt_legacy_schema,
    expected_schema_heads,
    render_offline_upgrade_sql,
    upgrade_database_schema,
    verify_database_schema,
    _run_alembic,
)


CURRENT_HEAD = "20260801_0004"


def _database(tmp_path: Path, name: str = "schema.db") -> Database:
    return Database(f"sqlite:///{(tmp_path / name).as_posix()}")


def _expected_heads() -> tuple[str, ...]:
    heads = expected_schema_heads()
    assert heads == (CURRENT_HEAD,)
    return heads


def _build_unversioned_alpha11_database(database: Database) -> None:
    """Create a true Alpha11 (revision 0001) schema without an alembic_version row.

    Uses Alembic 0001 upgrade, then strips the version mark so adopt-legacy must
    recognise the Alpha11 baseline and upgrade 0001 → 0002 → 0003.
    """
    with database.engine.begin() as connection:
        _run_alembic(connection, "upgrade", ALPHA11_BASELINE_REVISION)
        connection.execute(text("DELETE FROM alembic_version"))
        # Drop the version table entirely so the DB is truly unversioned.
        connection.execute(text("DROP TABLE alembic_version"))


def test_fresh_sqlite_upgrade_is_exact_and_idempotent(tmp_path: Path) -> None:
    database = _database(tmp_path)
    try:
        first = upgrade_database_schema(database.engine)
        second = upgrade_database_schema(database.engine)
        checked = verify_database_schema(database.engine)
        heads = _expected_heads()

        assert first.expected_heads == heads
        assert first.current_heads == first.expected_heads
        assert first.managed_by_alembic is True
        assert first.at_head is True
        assert first.drift_free is True
        assert second.as_dict() == first.as_dict()
        assert checked.mode == "verify"
        assert checked.at_head is True
        assert checked.drift_free is True

        tables = set(inspect(database.engine).get_table_names())
        assert "alembic_version" in tables
        assert {
            "projects",
            "verification_jobs",
            "proof_records",
            "finding_cases",
            "design_packages",
            "work_orders",
            "evidence_captures",
            "compliance_evaluations",
        } <= tables
    finally:
        database.engine.dispose()


def test_concurrent_sqlite_upgrades_are_serialized_by_the_process_lock(
    tmp_path: Path,
) -> None:
    database_url = f"sqlite:///{(tmp_path / 'concurrent.db').as_posix()}"

    def upgrade_once() -> dict[str, object]:
        database = Database(database_url)
        try:
            return upgrade_database_schema(database.engine).as_dict()
        finally:
            database.engine.dispose()

    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = list(pool.map(lambda _: upgrade_once(), range(2)))

    assert len(statuses) == 2
    assert all(status["at_head"] is True for status in statuses)
    assert all(status["drift_free"] is True for status in statuses)
    assert all(status["current_heads"] == [CURRENT_HEAD] for status in statuses)


def test_verify_mode_starts_app_only_after_an_explicit_upgrade(tmp_path: Path) -> None:
    database_url = f"sqlite:///{(tmp_path / 'verified-startup.db').as_posix()}"
    database = Database(database_url)
    try:
        upgrade_database_schema(database.engine)
    finally:
        database.engine.dispose()

    settings = Settings(
        environment="staging",
        database_url=database_url,
        database_schema_mode="verify",
        storage_root=tmp_path / "storage",
        operator_api_key="test-operator-key",
        reviewer_api_key="test-reviewer-key",
        auditor_api_key="test-auditor-key",
        gpkg_preview_signing_secret="staging-gpkg-preview-signing-secret-32b",
    )
    with TestClient(create_app(settings)) as client:
        response = client.get("/api/v1/meta")

    assert response.status_code == 200
    schema = response.json()["database_schema"]
    assert schema == {
        "mode": "verify",
        "expected_heads": [CURRENT_HEAD],
        "current_heads": [CURRENT_HEAD],
        "managed_by_alembic": True,
        "at_head": True,
        "drift_free": True,
        "legacy_adopted": False,
    }


def test_unversioned_legacy_database_requires_explicit_exact_adoption(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path, "legacy.db")
    database.create_all()
    with database.session_factory.begin() as session:
        session.add(
            Project(
                id="legacy-project",
                code="LEGACY-001",
                name="Legacy project",
                location="Isolated migration test",
            )
        )

    try:
        with pytest.raises(SchemaMigrationError, match="unversioned legacy"):
            upgrade_database_schema(database.engine)

        assert "alembic_version" not in inspect(database.engine).get_table_names()
        startup_settings = Settings(
            environment="development",
            database_url=str(database.engine.url),
            database_schema_mode="upgrade",
            storage_root=tmp_path / "legacy-storage",
        )
        with pytest.raises(SchemaMigrationError, match="unversioned legacy"):
            with TestClient(create_app(startup_settings)):
                pass

        adopted = adopt_legacy_schema(database.engine)
        checked = verify_database_schema(database.engine)
        adopted_again = adopt_legacy_schema(database.engine)

        assert adopted.legacy_adopted is True
        assert adopted.current_heads == (CURRENT_HEAD,)
        assert checked.at_head is True
        assert checked.drift_free is True
        assert adopted_again.legacy_adopted is False
        with database.session_factory() as session:
            project = session.get(Project, "legacy-project")
            assert project is not None
            assert project.code == "LEGACY-001"
    finally:
        database.engine.dispose()


def test_unversioned_alpha11_database_is_stamped_then_upgraded_without_data_loss(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path, "alpha11-legacy.db")
    _build_unversioned_alpha11_database(database)
    with database.session_factory.begin() as session:
        session.add(
            Project(
                id="alpha11-project",
                code="ALPHA11-001",
                name="Alpha11 project",
                location="Legacy adoption test",
            )
        )

    try:
        # Confirm we really have 0001-shaped tables and no attempt/work-order tables yet.
        pre_tables = set(inspect(database.engine).get_table_names())
        assert "projects" in pre_tables
        assert "verification_attempts" not in pre_tables
        assert "design_packages" not in pre_tables
        assert "alembic_version" not in pre_tables

        adopted = adopt_legacy_schema(database.engine)
        tables = set(inspect(database.engine).get_table_names())
        checked = verify_database_schema(database.engine)

        assert adopted.legacy_adopted is True
        assert adopted.current_heads == (CURRENT_HEAD,)
        assert checked.at_head is True
        assert checked.drift_free is True
        assert {
            "verification_attempts",
            "verification_attempt_outcomes",
            "design_packages",
            "engineering_objects",
            "work_orders",
            "evidence_captures",
            "compliance_evaluations",
        } <= tables
        with database.session_factory() as session:
            project = session.get(Project, "alpha11-project")
            assert project is not None
            assert project.code == "ALPHA11-001"
    finally:
        database.engine.dispose()


def test_legacy_adoption_rejects_metadata_drift_without_stamping(tmp_path: Path) -> None:
    database = _database(tmp_path, "legacy-drift.db")
    database.create_all()
    try:
        with database.engine.begin() as connection:
            connection.execute(text("DROP INDEX ix_projects_status"))

        with pytest.raises(SchemaMigrationError, match="does not exactly match"):
            adopt_legacy_schema(database.engine)

        assert "alembic_version" not in inspect(database.engine).get_table_names()
    finally:
        database.engine.dispose()


def test_versioned_database_rejects_metadata_drift(tmp_path: Path) -> None:
    database = _database(tmp_path, "versioned-drift.db")
    try:
        upgrade_database_schema(database.engine)
        with database.engine.begin() as connection:
            connection.execute(text("DROP INDEX ix_projects_status"))

        with pytest.raises(SchemaMigrationError, match="differs from"):
            verify_database_schema(database.engine)
    finally:
        database.engine.dispose()


def test_versioned_database_rejects_a_missing_append_only_trigger(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path, "trigger-drift.db")
    try:
        upgrade_database_schema(database.engine)
        with database.engine.begin() as connection:
            connection.execute(
                text("DROP TRIGGER trg_verification_attempts_no_update")
            )
            connection.execute(
                text(
                    "CREATE TRIGGER trg_verification_attempts_no_update "
                    "BEFORE UPDATE ON verification_attempts "
                    "BEGIN SELECT 1; END"
                )
            )

        with pytest.raises(
            SchemaMigrationError,
            match="append_only_trigger_drift",
        ):
            verify_database_schema(database.engine)
    finally:
        database.engine.dispose()


def test_unknown_database_revision_is_rejected(tmp_path: Path) -> None:
    database = _database(tmp_path, "future-revision.db")
    try:
        upgrade_database_schema(database.engine)
        with database.engine.begin() as connection:
            connection.execute(
                text("UPDATE alembic_version SET version_num = '20990101_unknown'")
            )

        with pytest.raises(SchemaMigrationError, match="not at the application head"):
            verify_database_schema(database.engine)
    finally:
        database.engine.dispose()


def test_offline_sql_compiles_for_supported_dialects_without_a_live_server() -> None:
    sqlite_sql = render_offline_upgrade_sql("sqlite:///offline.db")
    postgres_sql = render_offline_upgrade_sql(
        "postgresql+psycopg://offline@localhost/fengmou"
    )

    for rendered in (sqlite_sql, postgres_sql):
        assert "CREATE TABLE projects" in rendered
        assert "CREATE TABLE verification_jobs" in rendered
        assert "INSERT INTO alembic_version" in rendered
        assert "20260728_0001" in rendered
        assert "20260728_0002" in rendered
        assert CURRENT_HEAD in rendered
        assert "design_packages" in rendered
        assert "work_orders" in rendered
        assert "trg_verification_attempts_no_update" in rendered
    assert "DATETIME" in sqlite_sql
    assert "TIMESTAMP WITH TIME ZONE" in postgres_sql


def test_schema_cli_upgrades_and_checks_an_isolated_database(tmp_path: Path) -> None:
    backend_root = Path(__file__).resolve().parents[1]
    database_path = tmp_path / "cli.db"
    environment = os.environ.copy()
    environment.update(
        {
            "FENGMOU_ENVIRONMENT": "development",
            "FENGMOU_DATABASE_URL": f"sqlite:///{database_path.as_posix()}",
            "FENGMOU_DATABASE_SCHEMA_MODE": "upgrade",
        }
    )

    upgraded = subprocess.run(
        [sys.executable, "-m", "app.schema", "upgrade"],
        cwd=backend_root,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    checked = subprocess.run(
        [sys.executable, "-m", "app.schema", "check"],
        cwd=backend_root,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    invalid_environment = environment.copy()
    invalid_environment["FENGMOU_DATABASE_SCHEMA_MODE"] = "not-a-mode"
    invalid = subprocess.run(
        [sys.executable, "-m", "app.schema", "check"],
        cwd=backend_root,
        env=invalid_environment,
        capture_output=True,
        text=True,
        check=False,
    )
    legacy_path = tmp_path / "cli-legacy.db"
    legacy_database = Database(f"sqlite:///{legacy_path.as_posix()}")
    try:
        legacy_database.create_all()
    finally:
        legacy_database.engine.dispose()
    legacy_environment = environment.copy()
    legacy_environment["FENGMOU_DATABASE_URL"] = f"sqlite:///{legacy_path.as_posix()}"
    adopted = subprocess.run(
        [sys.executable, "-m", "app.schema", "adopt-legacy"],
        cwd=backend_root,
        env=legacy_environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert upgraded.returncode == 0, upgraded.stderr + upgraded.stdout
    assert checked.returncode == 0, checked.stderr + checked.stdout
    assert json.loads(upgraded.stdout)["mode"] == "upgrade"
    assert json.loads(checked.stdout)["mode"] == "verify"
    assert json.loads(upgraded.stdout)["current_heads"] == [CURRENT_HEAD]
    assert database_path.is_file()
    assert invalid.returncode == 2
    assert invalid.stdout == ""
    assert "configuration error" in invalid.stderr
    assert adopted.returncode == 0, adopted.stderr + adopted.stdout
    assert json.loads(adopted.stdout)["legacy_adopted"] is True
    assert json.loads(adopted.stdout)["current_heads"] == [CURRENT_HEAD]

def test_upgrade_downgrade_upgrade_empty_sqlite(tmp_path: Path) -> None:
    from app.schema import _run_alembic
    from sqlalchemy import create_engine, text

    url = f"sqlite:///{(tmp_path / 'ud.db').as_posix()}"
    engine = create_engine(url)
    try:
        with engine.begin() as conn:
            _run_alembic(conn, "upgrade", "head")
        with engine.begin() as conn:
            ver = conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            assert ver == CURRENT_HEAD
            _run_alembic(conn, "downgrade", "20260731_0003")
        with engine.begin() as conn:
            ver = conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            assert ver == "20260731_0003"
            cols = {c["name"] for c in inspect(conn).get_columns("design_packages")}
            assert "import_contract_version" not in cols
            _run_alembic(conn, "upgrade", "head")
        with engine.begin() as conn:
            ver = conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            assert ver == CURRENT_HEAD
            cols = {c["name"] for c in inspect(conn).get_columns("design_packages")}
            assert "import_contract_version" in cols
    finally:
        engine.dispose()


def test_downgrade_refuses_when_standard_gpkg_present(tmp_path: Path) -> None:
    from app.schema import SchemaMigrationError, _run_alembic
    from sqlalchemy import create_engine, text
    import uuid

    url = f"sqlite:///{(tmp_path / 'refuse.db').as_posix()}"
    engine = create_engine(url)
    try:
        with engine.begin() as conn:
            _run_alembic(conn, "upgrade", "head")
        with engine.begin() as conn:
            pid = str(uuid.uuid4())
            conn.execute(
                text(
                    "INSERT INTO projects (id, code, name, location, manager, status, created_at) "
                    "VALUES (:id, 'D', 'd', 's', 'm', 'active', CURRENT_TIMESTAMP)"
                ),
                {"id": pid},
            )
            conn.execute(
                text(
                    "INSERT INTO design_packages ("
                    "id, project_id, package_code, source_filename, source_sha256, source_type, "
                    "purpose, synthetic, source_crs_epsg, import_contract_version, layers_json, "
                    "field_mapping_json, redaction_policy_json, import_status, import_warnings_json, "
                    "object_count, created_at"
                    ") VALUES ("
                    ":id, :pid, 'PKG', 'f.gpkg', :sha, 'standard_gpkg', 'controlled', 1, 4326, "
                    "'gpkg-import-contract-v0.1.1', '{}', '{}', '{}', 'completed', '[]', 0, "
                    "CURRENT_TIMESTAMP)"
                ),
                {"id": str(uuid.uuid4()), "pid": pid, "sha": "a" * 64},
            )
        with engine.begin() as conn:
            with pytest.raises(SchemaMigrationError) as ei:
                _run_alembic(conn, "downgrade", "20260731_0003")
            assert "standard_gpkg" in str(ei.value.__cause__ or ei.value)
            ver = conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            assert ver == CURRENT_HEAD
    finally:
        engine.dispose()


def test_downgrade_with_legacy_packages_only(tmp_path: Path) -> None:
    from app.schema import _run_alembic
    from sqlalchemy import create_engine, text
    import uuid

    url = f"sqlite:///{(tmp_path / 'legacy.db').as_posix()}"
    engine = create_engine(url)
    try:
        with engine.begin() as conn:
            _run_alembic(conn, "upgrade", "head")
        with engine.begin() as conn:
            pid = str(uuid.uuid4())
            conn.execute(
                text(
                    "INSERT INTO projects (id, code, name, location, manager, status, created_at) "
                    "VALUES (:id, 'L', 'l', 's', 'm', 'active', CURRENT_TIMESTAMP)"
                ),
                {"id": pid},
            )
            conn.execute(
                text(
                    "INSERT INTO design_packages ("
                    "id, project_id, package_code, source_filename, source_sha256, source_type, "
                    "purpose, synthetic, source_crs_epsg, import_contract_version, layers_json, "
                    "field_mapping_json, redaction_policy_json, import_status, import_warnings_json, "
                    "object_count, created_at"
                    ") VALUES ("
                    ":id, :pid, 'PKG-L', 'f.json', :sha, 'synthetic_json', 'demo', 1, 4326, "
                    "'', '{}', '{}', '{}', 'completed', '[]', 0, CURRENT_TIMESTAMP)"
                ),
                {"id": str(uuid.uuid4()), "pid": pid, "sha": "b" * 64},
            )
        with engine.begin() as conn:
            _run_alembic(conn, "downgrade", "20260731_0003")
        with engine.begin() as conn:
            ver = conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            assert ver == "20260731_0003"
            st = conn.execute(
                text("SELECT source_type FROM design_packages WHERE package_code='PKG-L'")
            ).scalar_one()
            assert st == "synthetic_json"
    finally:
        engine.dispose()
