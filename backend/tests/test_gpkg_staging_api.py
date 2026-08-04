"""P1-4 / P1-4.1: standard GPKG preview/confirm hardening tests."""

from __future__ import annotations

import hashlib
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.config import Settings
from app.main import create_app
from app.models import AuditEvent, Base, DesignPackage, EngineeringObject, Project, new_id
from app.services.gpkg_geometry_stack import require_geometry_stack
from app.services.gpkg_import import import_standard_gpkg
from app.services.gpkg_staging import (
    GpkgStagingError,
    confirm_standard_gpkg_import,
    preview_standard_gpkg_bytes,
    purge_expired_staging,
    staging_dir,
)
from app.services.storage import FileStorage
from tests.gpkg_fixture_factory import create_valid_pipe_routes_gpkg

SIGNING_SECRET = "test-gpkg-preview-signing-secret-not-for-prod"


@pytest.fixture(autouse=True)
def _require_stack() -> None:
    try:
        require_geometry_stack()
    except Exception as exc:
        pytest.skip(f"geometry stack unavailable: {exc}")


@pytest.fixture()
def gpkg_client(tmp_path: Path) -> TestClient:
    settings = Settings(
        environment="test",
        database_url=f"sqlite:///{tmp_path / 'p14.db'}",
        database_schema_mode="create_all",
        storage_root=tmp_path / "storage",
        allow_demo_analyzer=True,
        operator_api_key="op-key-p14",
        reviewer_api_key="rev-key-p14",
        auditor_api_key="aud-key-p14",
        gpkg_preview_signing_secret=SIGNING_SECRET,
        standard_gpkg_max_upload_bytes=32 * 1024 * 1024,
        gpkg_preview_token_ttl_seconds=900,
    )
    with TestClient(create_app(settings)) as client:
        client.headers.update({"X-API-Key": "op-key-p14"})
        yield client


@pytest.fixture()
def project_id(gpkg_client: TestClient) -> str:
    resp = gpkg_client.post(
        "/api/v1/projects",
        json={
            "code": "P14-01",
            "name": "GPKG Preview Project",
            "location": "synthetic",
            "manager": "tester",
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def test_preview_and_confirm_happy_path(
    gpkg_client: TestClient, project_id: str, tmp_path: Path
) -> None:
    gpkg = create_valid_pipe_routes_gpkg(tmp_path / "ok.gpkg")
    raw = gpkg.read_bytes()
    preview = gpkg_client.post(
        f"/api/v1/projects/{project_id}/design-packages/standard-gpkg/preview",
        data={"package_code": "PKG-P14-1"},
        files={"file": ("ignored-name.bin", raw, "application/octet-stream")},
    )
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert body["valid"] is True
    assert body["preview_token"]
    assert body["candidate_count"] >= 1
    assert "PIPE-001" in body["object_codes"]
    assert body["source_classification"] == "sample_or_unverified"
    assert "不等于导入完成" in body["truth_note"] or "预检" in body["truth_note"]
    assert "授权" in body["truth_note"]

    confirm = gpkg_client.post(
        f"/api/v1/projects/{project_id}/design-packages/standard-gpkg/confirm",
        json={
            "package_code": "PKG-P14-1",
            "staging_id": body["staging_id"],
            "preview_token": body["preview_token"],
            "design_version": "design-v1",
        },
    )
    assert confirm.status_code == 201, confirm.text
    imported = confirm.json()
    assert imported["package"]["source_type"] == "standard_gpkg"
    assert imported["package"]["synthetic"] is True
    assert imported["package"]["purpose"] == "controlled"
    assert imported["source_classification"] == "sample_or_unverified"
    assert imported["idempotent"] is False
    assert "授权" in imported["truth_note"]

    staging_root = Path(gpkg_client.app.state.settings.storage_root) / "gpkg-staging"
    leftovers = list(staging_root.rglob("*.gpkg")) if staging_root.exists() else []
    assert leftovers == []


def test_confirm_rejects_client_synthetic_false_even_if_sent(
    gpkg_client: TestClient, project_id: str, tmp_path: Path
) -> None:
    """Schema no longer accepts synthetic; if extra fields present they are ignored."""
    gpkg = create_valid_pipe_routes_gpkg(tmp_path / "syn.gpkg")
    preview = gpkg_client.post(
        f"/api/v1/projects/{project_id}/design-packages/standard-gpkg/preview",
        data={"package_code": "PKG-SYN"},
        files={"file": ("x.gpkg", gpkg.read_bytes(), "application/octet-stream")},
    )
    body = preview.json()
    assert body["valid"] is True
    # Pydantic v2 ignores extra by default unless configured — send synthetic=false
    confirm = gpkg_client.post(
        f"/api/v1/projects/{project_id}/design-packages/standard-gpkg/confirm",
        json={
            "package_code": "PKG-SYN",
            "staging_id": body["staging_id"],
            "preview_token": body["preview_token"],
            "synthetic": False,
            "purpose": "demo",
        },
    )
    assert confirm.status_code == 201, confirm.text
    pkg = confirm.json()["package"]
    assert pkg["synthetic"] is True
    assert pkg["purpose"] == "controlled"


def test_confirm_rejects_tampered_token(
    gpkg_client: TestClient, project_id: str, tmp_path: Path
) -> None:
    gpkg = create_valid_pipe_routes_gpkg(tmp_path / "tok.gpkg")
    preview = gpkg_client.post(
        f"/api/v1/projects/{project_id}/design-packages/standard-gpkg/preview",
        data={"package_code": "PKG-P14-TOK"},
        files={"file": ("x.gpkg", gpkg.read_bytes(), "application/octet-stream")},
    )
    assert preview.status_code == 200
    body = preview.json()
    bad = body["preview_token"][:-4] + "xxxx"
    confirm = gpkg_client.post(
        f"/api/v1/projects/{project_id}/design-packages/standard-gpkg/confirm",
        json={
            "package_code": "PKG-P14-TOK",
            "staging_id": body["staging_id"],
            "preview_token": bad,
        },
    )
    assert confirm.status_code == 409
    detail = confirm.json()["detail"]
    assert isinstance(detail, dict)
    assert detail["error_code"] in {
        "preview_token_invalid",
        "preview_token_malformed",
    }
    assert "\\" not in detail.get("message", "")
    assert "Workspaces" not in detail.get("message", "")


def test_preview_invalid_gpkg_no_token(
    gpkg_client: TestClient, project_id: str
) -> None:
    preview = gpkg_client.post(
        f"/api/v1/projects/{project_id}/design-packages/standard-gpkg/preview",
        data={"package_code": "PKG-BAD"},
        files={"file": ("x.gpkg", b"not-a-sqlite-file", "application/octet-stream")},
    )
    assert preview.status_code == 200
    body = preview.json()
    assert body["valid"] is False
    assert body["preview_token"] is None
    assert body["errors"]
    assert body.get("error_code")


def test_reviewer_cannot_preview(
    gpkg_client: TestClient, project_id: str, tmp_path: Path
) -> None:
    gpkg = create_valid_pipe_routes_gpkg(tmp_path / "role.gpkg")
    gpkg_client.headers.update({"X-API-Key": "rev-key-p14"})
    preview = gpkg_client.post(
        f"/api/v1/projects/{project_id}/design-packages/standard-gpkg/preview",
        data={"package_code": "PKG-ROLE"},
        files={"file": ("x.gpkg", gpkg.read_bytes(), "application/octet-stream")},
    )
    assert preview.status_code == 403


def test_toctou_staging_replace_fails_closed(tmp_path: Path) -> None:
    """Replace staging after token bind with different GPKG → confirm fails, DB empty."""
    storage = FileStorage(tmp_path / "storage", max_upload_bytes=32 * 1024 * 1024)
    storage.ensure()
    engine = create_engine(f"sqlite:///{tmp_path / 'toctou.db'}")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    project = Project(
        id=new_id(),
        code="TOCTOU",
        name="toctou",
        location="synthetic",
        manager="t",
    )
    db.add(project)
    db.commit()
    pid = project.id

    one = create_valid_pipe_routes_gpkg(tmp_path / "one.gpkg", feature_count=1)
    two = create_valid_pipe_routes_gpkg(tmp_path / "two.gpkg", feature_count=2)
    preview = preview_standard_gpkg_bytes(
        storage,
        project_id=pid,
        package_code="PKG-TOCTOU",
        raw=one.read_bytes(),
        actor="operator:a",
        token_secret=SIGNING_SECRET,
        max_bytes=32 * 1024 * 1024,
    )
    assert preview.valid is True
    assert preview.candidate_count == 1

    # Replace staging file with a different valid GPKG (2 objects).
    staging_path = staging_dir(storage, pid) / f"{preview.staging_id}.gpkg"
    assert staging_path.is_file()
    staging_path.write_bytes(two.read_bytes())

    with pytest.raises(GpkgStagingError) as ei:
        confirm_standard_gpkg_import(
            db,
            storage,
            project_id=pid,
            package_code="PKG-TOCTOU",
            staging_id=preview.staging_id,
            preview_token=preview.preview_token or "",
            actor="operator:a",
            token_secret=SIGNING_SECRET,
        )
    assert ei.value.code == "source_sha256_mismatch"
    assert "\\" not in str(ei.value)
    db.rollback()

    assert db.scalar(select(DesignPackage).where(DesignPackage.project_id == pid)) is None
    assert (
        db.scalar(select(EngineeringObject).where(EngineeringObject.project_id == pid))
        is None
    )
    assert db.scalar(select(AuditEvent)) is None
    db.close()
    engine.dispose()


def test_import_expected_digest_mismatch_no_db_write(tmp_path: Path) -> None:
    storage_root = tmp_path / "s"
    storage_root.mkdir()
    engine = create_engine(f"sqlite:///{tmp_path / 'exp.db'}")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    project = Project(
        id=new_id(), code="E", name="e", location="s", manager="t"
    )
    db.add(project)
    db.commit()
    path = create_valid_pipe_routes_gpkg(tmp_path / "e.gpkg")
    with pytest.raises(Exception) as ei:
        import_standard_gpkg(
            db,
            project_id=project.id,
            gpkg_path=path,
            package_code="PKG-E",
            expected_source_sha256="0" * 64,
            force_sample_classification=True,
        )
    assert getattr(ei.value, "code", "") == "source_sha256_mismatch"
    db.rollback()
    assert db.scalar(select(DesignPackage)) is None
    assert db.scalar(select(AuditEvent)) is None
    db.close()
    engine.dispose()


def test_purge_expired_staging(tmp_path: Path) -> None:
    storage = FileStorage(tmp_path / "storage", max_upload_bytes=32 * 1024 * 1024)
    storage.ensure()
    preview = preview_standard_gpkg_bytes(
        storage,
        project_id="proj-ttl",
        package_code="PKG-TTL",
        raw=create_valid_pipe_routes_gpkg(tmp_path / "ttl.gpkg").read_bytes(),
        actor="op",
        token_secret=SIGNING_SECRET,
        max_bytes=32 * 1024 * 1024,
        ttl_seconds=900,
    )
    assert preview.valid
    path = staging_dir(storage, "proj-ttl") / f"{preview.staging_id}.gpkg"
    assert path.is_file()
    # Force mtime into the past
    import os

    old = 1_000_000.0
    os.utime(path, (old, old))
    deleted = purge_expired_staging(storage, ttl_seconds=60, now=old + 120)
    assert deleted >= 1
    assert not path.is_file()


def test_signing_secret_not_operator_key(tmp_path: Path) -> None:
    storage = FileStorage(tmp_path / "storage", max_upload_bytes=32 * 1024 * 1024)
    storage.ensure()
    raw = create_valid_pipe_routes_gpkg(tmp_path / "sec.gpkg").read_bytes()
    # Mint with secret A
    p = preview_standard_gpkg_bytes(
        storage,
        project_id="p",
        package_code="PKG-SEC",
        raw=raw,
        actor="op",
        token_secret="secret-a",
        max_bytes=32 * 1024 * 1024,
    )
    assert p.valid
    # Confirm with secret B fails
    engine = create_engine(f"sqlite:///{tmp_path / 'sec.db'}")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    db.add(Project(id="p", code="p", name="p", location="s", manager="t"))
    db.commit()
    with pytest.raises(GpkgStagingError) as ei:
        confirm_standard_gpkg_import(
            db,
            storage,
            project_id="p",
            package_code="PKG-SEC",
            staging_id=p.staging_id,
            preview_token=p.preview_token or "",
            actor="op",
            token_secret="secret-b",
        )
    assert ei.value.code in {"preview_token_invalid", "preview_token_malformed"}
    db.close()
    engine.dispose()


def test_settings_repr_hides_signing_secret() -> None:
    s = Settings(
        environment="test",
        gpkg_preview_signing_secret="super-secret-value-xyz",
        operator_api_key="op-key",
    )
    text = repr(s)
    assert "super-secret-value-xyz" not in text
    assert "super-secret" not in text


def test_production_rejects_missing_or_weak_or_reused_signing_secret() -> None:
    base = dict(
        environment="production",
        database_url="postgresql+psycopg://u:p@localhost/db",
        database_schema_mode="verify",
        operator_api_key="operator-key-not-for-signing-use-32b",
    )
    with pytest.raises(ValueError, match="GPKG_PREVIEW_SIGNING_SECRET"):
        Settings(**base, gpkg_preview_signing_secret=None).require_gpkg_preview_signing_secret_for_deploy()
    with pytest.raises(ValueError, match="32 bytes"):
        Settings(**base, gpkg_preview_signing_secret="too-short").require_gpkg_preview_signing_secret_for_deploy()
    with pytest.raises(ValueError, match="default placeholder"):
        Settings(
            **base,
            gpkg_preview_signing_secret="replace-with-a-long-random-gpkg-preview-signing-secret",
        ).require_gpkg_preview_signing_secret_for_deploy()
    with pytest.raises(ValueError, match="must not reuse the operator"):
        Settings(
            **base,
            gpkg_preview_signing_secret="operator-key-not-for-signing-use-32b",
        ).require_gpkg_preview_signing_secret_for_deploy()
    ok = Settings(
        **base,
        gpkg_preview_signing_secret="production-gpkg-signing-secret-value-ok-32b",
    )
    ok.require_gpkg_preview_signing_secret_for_deploy()
    assert ok.gpkg_preview_signing_secret.startswith("production-gpkg")


def test_mint_fail_closed_without_signing_secret(tmp_path: Path) -> None:
    storage = FileStorage(tmp_path / "storage", max_upload_bytes=32 * 1024 * 1024)
    storage.ensure()
    with pytest.raises(GpkgStagingError) as ei:
        preview_standard_gpkg_bytes(
            storage,
            project_id="p",
            package_code="PKG-NOSEC",
            raw=create_valid_pipe_routes_gpkg(tmp_path / "nosec.gpkg").read_bytes(),
            actor="op",
            token_secret=None,
            max_bytes=32 * 1024 * 1024,
        )
    assert ei.value.code == "preview_token_secret_missing"


def test_concurrent_confirm_only_one_succeeds(tmp_path: Path) -> None:
    storage = FileStorage(tmp_path / "storage", max_upload_bytes=32 * 1024 * 1024)
    storage.ensure()
    engine = create_engine(
        f"sqlite:///{tmp_path / 'conc.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db0 = Session()
    proj = Project(id=new_id(), code="C", name="c", location="s", manager="t")
    db0.add(proj)
    db0.commit()
    pid = proj.id
    db0.close()

    preview = preview_standard_gpkg_bytes(
        storage,
        project_id=pid,
        package_code="PKG-CONC",
        raw=create_valid_pipe_routes_gpkg(tmp_path / "c.gpkg").read_bytes(),
        actor="op",
        token_secret=SIGNING_SECRET,
        max_bytes=32 * 1024 * 1024,
    )
    assert preview.valid

    allowed_fail = {
        "confirm_in_progress",
        "confirm_already_completed",
        "staging_not_found",
        "source_sha256_mismatch",
    }

    def _one() -> str:
        db = Session()
        try:
            confirm_standard_gpkg_import(
                db,
                storage,
                project_id=pid,
                package_code="PKG-CONC",
                staging_id=preview.staging_id,
                preview_token=preview.preview_token or "",
                actor="op",
                token_secret=SIGNING_SECRET,
            )
            db.commit()
            return "ok"
        except GpkgStagingError as exc:
            db.rollback()
            return exc.code
        except Exception as exc:
            db.rollback()
            # Must not silently swallow unknown DB errors as success.
            return f"unexpected:{type(exc).__name__}"
        finally:
            db.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futs = [pool.submit(_one), pool.submit(_one)]
        results = [f.result() for f in futs]

    assert results.count("ok") == 1, results
    other = [r for r in results if r != "ok"]
    assert len(other) == 1
    assert other[0] in allowed_fail, results
    assert not any(r.startswith("unexpected:") for r in results), results

    db = Session()
    pkgs = db.scalars(select(DesignPackage).where(DesignPackage.project_id == pid)).all()
    assert len(pkgs) == 1
    objs = db.scalars(
        select(EngineeringObject).where(EngineeringObject.project_id == pid)
    ).all()
    assert len(objs) == pkgs[0].object_count
    audits = db.scalars(
        select(AuditEvent).where(
            AuditEvent.entity_type == "design_package",
            AuditEvent.action == "standard_gpkg_imported",
        )
    ).all()
    assert len(audits) == 1
    db.close()

    # no residual staging/snapshot/lock for this id
    leftovers = list((tmp_path / "storage" / "gpkg-staging").rglob(f"*{preview.staging_id}*"))
    assert leftovers == [], leftovers
    engine.dispose()


def test_purge_scan_budget_is_hard_cap(tmp_path: Path) -> None:
    storage = FileStorage(tmp_path / "storage", max_upload_bytes=32 * 1024 * 1024)
    storage.ensure()
    root = tmp_path / "storage" / "gpkg-staging"
    # many project dirs, each with many files
    for i in range(40):
        d = root / f"proj{i:03d}"
        d.mkdir(parents=True, exist_ok=True)
        for j in range(20):
            (d / f"f{j:03d}.gpkg").write_bytes(b"x")
    stats: dict[str, int] = {}
    purge_expired_staging(storage, ttl_seconds=1, max_scan=50, now=time.time() + 10_000, out_stats=stats)
    assert stats["scanned"] <= 50
    assert stats["scanned"] == 50  # should hit the budget


def test_concurrent_preview_respects_quota(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import app.services.gpkg_staging as staging_mod

    monkeypatch.setattr(staging_mod, "DEFAULT_MAX_PENDING_PER_ACTOR", 2)
    storage = FileStorage(tmp_path / "storage", max_upload_bytes=32 * 1024 * 1024)
    storage.ensure()
    raw = create_valid_pipe_routes_gpkg(tmp_path / "q.gpkg").read_bytes()

    def _one(i: int) -> str:
        try:
            preview_standard_gpkg_bytes(
                storage,
                project_id="proj-q",
                package_code=f"PKG-Q{i}",
                raw=raw,
                actor="actor-q",
                token_secret=SIGNING_SECRET,
                max_bytes=32 * 1024 * 1024,
            )
            return "ok"
        except GpkgStagingError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(_one, range(6)))
    assert results.count("ok") <= 2
    assert results.count("ok") >= 1
    assert "staging_quota_count" in results or results.count("ok") == 2
