"""P1-3A/B: transactional idempotent standard GPKG import + audit (isolated DB)."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.orm import Session, sessionmaker

from app.models import AuditEvent, Base, DesignPackage, EngineeringObject, Project, new_id
from app.services.design_package import DesignPackageImportError
from app.services.gpkg_geometry_stack import require_geometry_stack
from app.services.gpkg_import import (
    AUDIT_ACTION_IDEMPOTENT,
    AUDIT_ACTION_IMPORTED,
    AUDIT_ENTITY_TYPE,
    SOURCE_TYPE_STANDARD_GPKG,
    import_standard_gpkg,
)
from app.services.gpkg_preflight import IMPORT_CONTRACT_VERSION
from tests.gpkg_fixture_factory import create_valid_pipe_routes_gpkg


@pytest.fixture(autouse=True)
def _require_stack() -> None:
    try:
        require_geometry_stack()
    except Exception as exc:
        pytest.skip(f"geometry stack unavailable: {exc}")


@pytest.fixture()
def db_session(tmp_path: Path) -> Session:
    engine = create_engine(f"sqlite:///{tmp_path / 'p13a.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    session = factory()
    project = Project(
        id=new_id(),
        code="P13A-01",
        name="P1-3A Isolated",
        location="synthetic",
        manager="tester",
    )
    session.add(project)
    session.commit()
    session._p13a_project_id = project.id  # type: ignore[attr-defined]
    session._p13a_engine = engine  # type: ignore[attr-defined]
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture()
def project_id(db_session: Session) -> str:
    return db_session._p13a_project_id  # type: ignore[attr-defined]


@pytest.fixture()
def gpkg_dir(tmp_path: Path) -> Path:
    return tmp_path / "gpkg"


def test_import_standard_gpkg_creates_package_and_objects(
    db_session: Session, project_id: str, gpkg_dir: Path
) -> None:
    path = create_valid_pipe_routes_gpkg(gpkg_dir / "imp.gpkg")
    result = import_standard_gpkg(
        db_session,
        project_id=project_id,
        gpkg_path=path,
        package_code="PKG-STD-001",
        purpose="controlled",
        synthetic=True,
    )
    db_session.commit()

    assert result.idempotent is False
    assert result.package.source_type == SOURCE_TYPE_STANDARD_GPKG
    assert result.package.import_contract_version == IMPORT_CONTRACT_VERSION
    assert result.package.source_sha256
    assert len(result.package.source_sha256) == 64
    assert len(result.objects) == 1
    assert result.objects[0].object_code == "PIPE-001"
    assert result.objects[0].geometry_type == "LineString"
    assert result.objects[0].geometry_wgs84_json["type"] == "LineString"

    # Reload from DB
    pkg = db_session.get(DesignPackage, result.package.id)
    assert pkg is not None
    assert pkg.object_count == 1
    objs = db_session.scalars(
        select(EngineeringObject).where(EngineeringObject.design_package_id == pkg.id)
    ).all()
    assert len(objs) == 1


def test_import_idempotent_same_digest(
    db_session: Session, project_id: str, gpkg_dir: Path
) -> None:
    path = create_valid_pipe_routes_gpkg(gpkg_dir / "idem.gpkg")
    first = import_standard_gpkg(
        db_session,
        project_id=project_id,
        gpkg_path=path,
        package_code="PKG-IDEM",
    )
    db_session.commit()
    second = import_standard_gpkg(
        db_session,
        project_id=project_id,
        gpkg_path=path,
        package_code="PKG-IDEM",
    )
    db_session.commit()

    assert second.idempotent is True
    assert second.package.id == first.package.id
    count = db_session.scalar(select(DesignPackage).where(DesignPackage.project_id == project_id))
    packages = db_session.scalars(
        select(DesignPackage).where(DesignPackage.project_id == project_id)
    ).all()
    assert len(packages) == 1
    objects = db_session.scalars(
        select(EngineeringObject).where(EngineeringObject.project_id == project_id)
    ).all()
    assert len(objects) == 1
    _ = count


def test_package_code_conflict_different_digest(
    db_session: Session, project_id: str, gpkg_dir: Path
) -> None:
    p1 = create_valid_pipe_routes_gpkg(gpkg_dir / "a.gpkg", feature_count=1)
    p2 = create_valid_pipe_routes_gpkg(gpkg_dir / "b.gpkg", feature_count=2)
    import_standard_gpkg(
        db_session,
        project_id=project_id,
        gpkg_path=p1,
        package_code="PKG-SAME-CODE",
    )
    db_session.commit()
    with pytest.raises(DesignPackageImportError) as ei:
        import_standard_gpkg(
            db_session,
            project_id=project_id,
            gpkg_path=p2,
            package_code="PKG-SAME-CODE",
        )
    assert ei.value.code == "package_code_conflict_different_digest"
    db_session.rollback()
    packages = db_session.scalars(
        select(DesignPackage).where(DesignPackage.project_id == project_id)
    ).all()
    assert len(packages) == 1


def test_object_code_conflict_fail_closed(
    db_session: Session, project_id: str, gpkg_dir: Path
) -> None:
    from app.services.gpkg_preflight import inspect_standard_gpkg

    p1 = create_valid_pipe_routes_gpkg(gpkg_dir / "o1.gpkg", feature_count=1)
    # Different content/hash, still includes object_code PIPE-001
    p2 = create_valid_pipe_routes_gpkg(gpkg_dir / "o2.gpkg", feature_count=2)
    d1 = inspect_standard_gpkg(p1).source_sha256
    d2 = inspect_standard_gpkg(p2).source_sha256
    assert d1 != d2

    import_standard_gpkg(
        db_session,
        project_id=project_id,
        gpkg_path=p1,
        package_code="PKG-O1",
    )
    db_session.commit()
    with pytest.raises(DesignPackageImportError) as ei:
        import_standard_gpkg(
            db_session,
            project_id=project_id,
            gpkg_path=p2,
            package_code="PKG-O2",
        )
    assert ei.value.code == "object_code_conflict"
    db_session.rollback()
    objects = db_session.scalars(
        select(EngineeringObject).where(EngineeringObject.project_id == project_id)
    ).all()
    assert len(objects) == 1


def test_failed_import_leaves_no_rows(
    db_session: Session, project_id: str, gpkg_dir: Path
) -> None:
    bad = create_valid_pipe_routes_gpkg(
        gpkg_dir / "bad.gpkg",
        organization="LOCAL",
        organization_coordsys_id=1,
        definition="LOCAL",
    )
    with pytest.raises(DesignPackageImportError, match="normalize rejected"):
        import_standard_gpkg(
            db_session,
            project_id=project_id,
            gpkg_path=bad,
            package_code="PKG-BAD",
        )
    db_session.rollback()
    assert (
        db_session.scalar(
            select(DesignPackage).where(DesignPackage.project_id == project_id)
        )
        is None
    )
    assert (
        db_session.scalar(
            select(EngineeringObject).where(EngineeringObject.project_id == project_id)
        )
        is None
    )


def test_standard_gpkg_routes_are_preview_confirm_only() -> None:
    """P1-4 exposes preview/confirm only — not arbitrary server-path import."""
    from app.main import create_app
    from app.config import Settings
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        settings = Settings(
            environment="test",
            database_url=f"sqlite:///{Path(tmp) / 'x.db'}",
            database_schema_mode="create_all",
            storage_root=Path(tmp) / "storage",
            allow_demo_analyzer=True,
            operator_api_key="k",
            reviewer_api_key="k",
            auditor_api_key="k",
        )
        app = create_app(settings)
        paths = set(app.openapi()["paths"].keys())
        assert any("standard-gpkg/preview" in p for p in paths)
        assert any("standard-gpkg/confirm" in p for p in paths)
        # No raw path-based import endpoint
        assert not any("import-gpkg-path" in p or "server-path" in p for p in paths)


def test_migration_head_includes_idempotency_columns(tmp_path: Path) -> None:
    from app.database import Database
    from app.schema import upgrade_database_schema, expected_schema_heads

    db = Database(f"sqlite:///{(tmp_path / 'mig.db').as_posix()}")
    try:
        report = upgrade_database_schema(db.engine)
        assert report.at_head is True
        assert "20260801_0004" in expected_schema_heads()
        cols = {c["name"] for c in inspect(db.engine).get_columns("design_packages")}
        assert "import_contract_version" in cols
        # Unique constraint / index present
        uniques = {
            u["name"]
            for u in inspect(db.engine).get_unique_constraints("design_packages")
        }
        indexes = {i["name"] for i in inspect(db.engine).get_indexes("design_packages")}
        assert (
            "uq_design_package_idempotency" in uniques
            or "uq_design_package_idempotency" in indexes
        )
    finally:
        db.engine.dispose()


def test_import_writes_audit_with_digest_and_contract(
    db_session: Session, project_id: str, gpkg_dir: Path
) -> None:
    path = create_valid_pipe_routes_gpkg(gpkg_dir / "audit.gpkg")
    result = import_standard_gpkg(
        db_session,
        project_id=project_id,
        gpkg_path=path,
        package_code="PKG-AUD",
        actor="operator:test",
        storage_path=str(gpkg_dir / "staging" / "audit.gpkg"),
    )
    db_session.commit()

    events = list(
        db_session.scalars(
            select(AuditEvent)
            .where(
                AuditEvent.entity_type == AUDIT_ENTITY_TYPE,
                AuditEvent.entity_id == result.package.id,
                AuditEvent.action == AUDIT_ACTION_IMPORTED,
            )
            .order_by(AuditEvent.created_at.asc())
        ).all()
    )
    assert len(events) == 1
    event = events[0]
    assert event.actor == "operator:test"
    payload = event.payload_json
    assert payload["source_sha256"] == result.package.source_sha256
    assert payload["import_contract_version"] == IMPORT_CONTRACT_VERSION
    assert payload["object_count"] == 1
    assert payload["idempotent"] is False
    assert payload["source_type"] == SOURCE_TYPE_STANDARD_GPKG
    assert payload["package_code"] == "PKG-AUD"
    # No absolute path leakage; basename only for storage
    assert payload.get("storage_basename") == "audit.gpkg"
    blob = str(payload)
    assert "SHOULD_NOT" not in blob
    assert ":\\" not in blob
    assert "Workspaces" not in blob


def test_idempotent_import_writes_separate_audit(
    db_session: Session, project_id: str, gpkg_dir: Path
) -> None:
    path = create_valid_pipe_routes_gpkg(gpkg_dir / "audit-idem.gpkg")
    first = import_standard_gpkg(
        db_session,
        project_id=project_id,
        gpkg_path=path,
        package_code="PKG-AUD-IDEM",
        actor="operator:a",
    )
    db_session.commit()
    second = import_standard_gpkg(
        db_session,
        project_id=project_id,
        gpkg_path=path,
        package_code="PKG-AUD-IDEM",
        actor="operator:b",
    )
    db_session.commit()
    assert second.idempotent is True

    imported = db_session.scalars(
        select(AuditEvent).where(
            AuditEvent.entity_id == first.package.id,
            AuditEvent.action == AUDIT_ACTION_IMPORTED,
        )
    ).all()
    idem = db_session.scalars(
        select(AuditEvent).where(
            AuditEvent.entity_id == first.package.id,
            AuditEvent.action == AUDIT_ACTION_IDEMPOTENT,
        )
    ).all()
    assert len(imported) == 1
    assert len(idem) == 1
    assert idem[0].actor == "operator:b"
    assert idem[0].payload_json["idempotent"] is True
    assert idem[0].payload_json["source_sha256"] == first.package.source_sha256
    assert idem[0].payload_json["object_count"] == 1


def test_failed_import_writes_no_audit(
    db_session: Session, project_id: str, gpkg_dir: Path
) -> None:
    bad = create_valid_pipe_routes_gpkg(
        gpkg_dir / "audit-fail.gpkg",
        organization="LOCAL",
        organization_coordsys_id=1,
        definition="LOCAL",
    )
    with pytest.raises(DesignPackageImportError):
        import_standard_gpkg(
            db_session,
            project_id=project_id,
            gpkg_path=bad,
            package_code="PKG-AUD-FAIL",
            actor="operator:x",
        )
    db_session.rollback()
    count = db_session.scalar(select(AuditEvent))
    assert count is None
