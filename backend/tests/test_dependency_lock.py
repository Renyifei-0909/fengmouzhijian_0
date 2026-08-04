from __future__ import annotations

from pathlib import Path

import pytest

from scripts.verify_dependency_lock import (
    DependencyLockError,
    verify_dependency_lock,
)


BACKEND_ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = BACKEND_ROOT / "pyproject.toml"
LOCK = BACKEND_ROOT / "uv.lock"
BUILD_CONSTRAINTS = BACKEND_ROOT / "build-constraints.txt"
UV_BOOTSTRAP = BACKEND_ROOT / "uv-bootstrap.txt"
DOCKERFILE = BACKEND_ROOT / "Dockerfile"
PYTHON_IMAGE = (
    "python:3.12.13-slim@"
    "sha256:57cd7c3a7a273101a6485ba99423ee568157882804b1124b4dd04266317710de"
)


def _tampered_files(
    tmp_path: Path,
    *,
    pyproject_replace: tuple[str, str] | None = None,
    lock_replace: tuple[str, str] | None = None,
    build_constraints_replace: tuple[str, str] | None = None,
) -> tuple[Path, Path]:
    pyproject_text = PYPROJECT.read_text(encoding="utf-8")
    lock_text = LOCK.read_text(encoding="utf-8")
    build_constraints_text = BUILD_CONSTRAINTS.read_text(encoding="utf-8")
    uv_bootstrap_text = UV_BOOTSTRAP.read_text(encoding="utf-8")
    if pyproject_replace is not None:
        old, new = pyproject_replace
        assert old in pyproject_text
        pyproject_text = pyproject_text.replace(old, new, 1)
    if lock_replace is not None:
        old, new = lock_replace
        assert old in lock_text
        lock_text = lock_text.replace(old, new, 1)
    if build_constraints_replace is not None:
        old, new = build_constraints_replace
        assert old in build_constraints_text
        build_constraints_text = build_constraints_text.replace(old, new, 1)
    pyproject_path = tmp_path / "pyproject.toml"
    lock_path = tmp_path / "uv.lock"
    build_constraints_path = tmp_path / "build-constraints.txt"
    uv_bootstrap_path = tmp_path / "uv-bootstrap.txt"
    pyproject_path.write_text(pyproject_text, encoding="utf-8")
    lock_path.write_text(lock_text, encoding="utf-8")
    build_constraints_path.write_text(build_constraints_text, encoding="utf-8")
    uv_bootstrap_path.write_text(uv_bootstrap_text, encoding="utf-8")
    return pyproject_path, lock_path


def test_repository_dependency_lock_is_valid() -> None:
    report = verify_dependency_lock(PYPROJECT, LOCK)

    # Base runtime + optional extra gpkg (pyogrio/shapely/pyproj/numpy + ...)
    assert report.package_count == 52
    assert report.registry_package_count == 51
    assert report.build_requirement_count == 1
    assert report.build_artifact_hash_count == 2
    assert report.uv_bootstrap_hash_count == 19
    assert len(report.sha256) == 64

    dockerfile = DOCKERFILE.read_text(encoding="utf-8")
    assert dockerfile.count(f"FROM {PYTHON_IMAGE}") == 2
    assert "pip install --no-cache-dir --require-hashes -r uv-bootstrap.txt" in dockerfile
    assert "uv sync --locked --no-install-project --no-python-downloads" in dockerfile
    assert "uv build --wheel" in dockerfile
    assert "--build-constraints build-constraints.txt" in dockerfile
    assert "python -m pip install --no-cache-dir ." not in dockerfile


def test_dependency_lock_rejects_non_pypi_registry(tmp_path: Path) -> None:
    paths = _tampered_files(
        tmp_path,
        lock_replace=(
            'source = { registry = "https://pypi.org/simple" }',
            'source = { registry = "https://packages.invalid/simple" }',
        ),
    )

    with pytest.raises(DependencyLockError, match="canonical PyPI"):
        verify_dependency_lock(*paths)


def test_dependency_lock_rejects_missing_artifact_hash(tmp_path: Path) -> None:
    paths = _tampered_files(
        tmp_path,
        lock_replace=(
            'hash = "sha256:1554982221dd17e9a749b53902407578eb305e453f71999e8c7f0a48389fff8e", ',
            "",
        ),
    )

    with pytest.raises(DependencyLockError, match=r"sdist\.hash"):
        verify_dependency_lock(*paths)


def test_dependency_lock_rejects_manifest_metadata_drift(tmp_path: Path) -> None:
    paths = _tampered_files(
        tmp_path,
        lock_replace=(
            '{ name = "alembic", specifier = "==1.18.5" }',
            '{ name = "alembic", specifier = "==1.18.4" }',
        ),
    )

    with pytest.raises(DependencyLockError, match="requires-dist does not match"):
        verify_dependency_lock(*paths)


def test_dependency_lock_requires_exact_uv_tool_pin(tmp_path: Path) -> None:
    paths = _tampered_files(
        tmp_path,
        pyproject_replace=(
            'required-version = "==0.11.32"',
            'required-version = ">=0.11.32"',
        ),
    )

    with pytest.raises(DependencyLockError, match="exact"):
        verify_dependency_lock(*paths)


def test_dependency_lock_disallows_automatic_python_downloads(
    tmp_path: Path,
) -> None:
    paths = _tampered_files(
        tmp_path,
        pyproject_replace=(
            'python-preference = "only-system"',
            'python-preference = "managed"',
        ),
    )

    with pytest.raises(DependencyLockError, match="only-system"):
        verify_dependency_lock(*paths)


def test_dependency_lock_requires_exact_build_constraint_match(
    tmp_path: Path,
) -> None:
    paths = _tampered_files(
        tmp_path,
        pyproject_replace=(
            'build-constraint-dependencies = ["setuptools==83.0.0"]',
            'build-constraint-dependencies = ["setuptools==82.0.1"]',
        ),
    )

    with pytest.raises(DependencyLockError, match="exactly match"):
        verify_dependency_lock(*paths)


def test_dependency_lock_requires_hashed_build_artifacts(tmp_path: Path) -> None:
    paths = _tampered_files(
        tmp_path,
        build_constraints_replace=(
            BUILD_CONSTRAINTS.read_text(encoding="utf-8").strip(),
            "setuptools==83.0.0",
        ),
    )

    with pytest.raises(DependencyLockError, match="at least one hash"):
        verify_dependency_lock(*paths)


def test_dependency_lock_rejects_locked_build_constraint_drift(
    tmp_path: Path,
) -> None:
    paths = _tampered_files(
        tmp_path,
        lock_replace=(
            'build-constraints = [{ name = "setuptools", specifier = "==83.0.0" }]',
            'build-constraints = [{ name = "setuptools", specifier = "==82.0.1" }]',
        ),
    )

    with pytest.raises(DependencyLockError, match="build-constraints do not match"):
        verify_dependency_lock(*paths)
