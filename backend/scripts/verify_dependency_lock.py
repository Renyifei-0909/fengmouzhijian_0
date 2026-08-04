from __future__ import annotations

import argparse
import hashlib
import re
import sys
import tomllib
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any


LOCK_FORMAT_VERSION = 1
MINIMUM_LOCK_REVISION = 3
PYPI_INDEX = "https://pypi.org/simple"
PYPI_FILES_PREFIX = "https://files.pythonhosted.org/"

_EXACT_UV_VERSION = re.compile(r"^==\d+\.\d+\.\d+$")
_HASH = re.compile(r"^sha256:[0-9a-f]{64}$")
_REQUIREMENT = re.compile(
    r"^(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)"
    r"(?:\[(?P<extras>[A-Za-z0-9._-]+(?:,[A-Za-z0-9._-]+)*)\])?"
    r"(?P<specifier>==[A-Za-z0-9][A-Za-z0-9.!+_-]*)$"
)


class DependencyLockError(ValueError):
    """Raised when the dependency lock violates repository policy."""


@dataclass(frozen=True)
class LockReport:
    package_count: int
    registry_package_count: int
    build_requirement_count: int
    build_artifact_hash_count: int
    uv_bootstrap_hash_count: int
    sha256: str


def _fail(message: str) -> None:
    raise DependencyLockError(message)


def _canonical_name(value: object, *, context: str) -> str:
    if not isinstance(value, str) or not value:
        _fail(f"{context} must be a non-empty string")
    return re.sub(r"[-_.]+", "-", value).lower()


def _extras(value: object, *, context: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item for item in value
    ):
        _fail(f"{context} must be a list of non-empty strings")
    normalized = tuple(
        sorted(_canonical_name(item, context=context) for item in value)
    )
    if len(normalized) != len(set(normalized)):
        _fail(f"{context} contains duplicate extras")
    return normalized


def _parse_requirement(
    requirement: object, *, marker: str | None = None
) -> tuple[str, tuple[str, ...], str, str | None]:
    if not isinstance(requirement, str):
        _fail("project requirements must be strings")
    match = _REQUIREMENT.fullmatch(requirement)
    if match is None:
        _fail(
            f"requirement {requirement!r} is not an exact, marker-free "
            "name[extras]==version pin"
        )
    raw_extras = match.group("extras")
    extras = (
        tuple(
            sorted(
                _canonical_name(item, context=f"extras in {requirement!r}")
                for item in raw_extras.split(",")
            )
        )
        if raw_extras
        else ()
    )
    return (
        _canonical_name(match.group("name"), context=f"name in {requirement!r}"),
        extras,
        match.group("specifier"),
        marker,
    )


def _locked_requirement(
    requirement: object, *, context: str
) -> tuple[str, tuple[str, ...], str, str | None]:
    if not isinstance(requirement, dict):
        _fail(f"{context} must be a table")
    allowed = {"name", "extras", "specifier", "marker"}
    unexpected = set(requirement) - allowed
    if unexpected:
        _fail(f"{context} contains unsupported fields: {sorted(unexpected)}")
    specifier = requirement.get("specifier")
    if not isinstance(specifier, str) or not specifier.startswith("=="):
        _fail(f"{context} must contain an exact == specifier")
    marker = requirement.get("marker")
    if marker is not None and not isinstance(marker, str):
        _fail(f"{context}.marker must be a string")
    return (
        _canonical_name(requirement.get("name"), context=f"{context}.name"),
        _extras(requirement.get("extras"), context=f"{context}.extras"),
        specifier,
        marker,
    )


def _dependency_edge(
    dependency: object, *, context: str
) -> tuple[str, tuple[str, ...]]:
    if not isinstance(dependency, dict):
        _fail(f"{context} must be a table")
    allowed = {"name", "extra"}
    unexpected = set(dependency) - allowed
    if unexpected:
        _fail(f"{context} contains unsupported fields: {sorted(unexpected)}")
    return (
        _canonical_name(dependency.get("name"), context=f"{context}.name"),
        _extras(dependency.get("extra"), context=f"{context}.extra"),
    )


def _validate_artifact(artifact: object, *, context: str) -> None:
    if not isinstance(artifact, dict):
        _fail(f"{context} must be a table")
    url = artifact.get("url")
    if not isinstance(url, str) or not url.startswith(PYPI_FILES_PREFIX):
        _fail(f"{context}.url must use {PYPI_FILES_PREFIX}")
    digest = artifact.get("hash")
    if not isinstance(digest, str) or _HASH.fullmatch(digest) is None:
        _fail(f"{context}.hash must be a lowercase SHA-256 digest")
    size = artifact.get("size")
    if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
        _fail(f"{context}.size must be a positive integer")


def _read_toml(path: Path, *, label: str) -> dict[str, Any]:
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
        raise DependencyLockError(f"cannot read {label} {path}: {exc}") from exc
    if not isinstance(data, dict):
        _fail(f"{label} must contain a TOML table")
    return data


def _read_hashed_requirements(
    path: Path, *, label: str
) -> dict[tuple[str, tuple[str, ...], str, str | None], set[str]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise DependencyLockError(
            f"cannot read {label} {path}: {exc}"
        ) from exc
    constraints: dict[
        tuple[str, tuple[str, ...], str, str | None], set[str]
    ] = {}
    for line_number, raw_line in enumerate(lines, start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        requirement = _parse_requirement(parts[0])
        if requirement in constraints:
            _fail(
                f"{label} line {line_number} duplicates "
                f"{parts[0]!r}"
            )
        hashes: set[str] = set()
        for part in parts[1:]:
            prefix = "--hash="
            if not part.startswith(prefix):
                _fail(
                    f"{label} line {line_number} contains unsupported "
                    f"token {part!r}"
                )
            digest = part[len(prefix) :]
            if _HASH.fullmatch(digest) is None:
                _fail(
                    f"{label} line {line_number} contains an invalid "
                    "SHA-256 hash"
                )
            if digest in hashes:
                _fail(
                    f"{label} line {line_number} contains a duplicate hash"
                )
            hashes.add(digest)
        if not hashes:
            _fail(
                f"{label} line {line_number} must contain at least one hash"
            )
        constraints[requirement] = hashes
    if not constraints:
        _fail(f"{label} must contain at least one requirement")
    return constraints


def verify_dependency_lock(
    pyproject_path: Path | str,
    lock_path: Path | str,
    build_constraints_path: Path | str | None = None,
    uv_bootstrap_path: Path | str | None = None,
) -> LockReport:
    pyproject_path = Path(pyproject_path)
    lock_path = Path(lock_path)
    build_constraints_path = (
        Path(build_constraints_path)
        if build_constraints_path is not None
        else pyproject_path.with_name("build-constraints.txt")
    )
    uv_bootstrap_path = (
        Path(uv_bootstrap_path)
        if uv_bootstrap_path is not None
        else pyproject_path.with_name("uv-bootstrap.txt")
    )
    pyproject = _read_toml(pyproject_path, label="project manifest")
    lock = _read_toml(lock_path, label="dependency lock")

    project = pyproject.get("project")
    if not isinstance(project, dict):
        _fail("pyproject.toml must contain [project]")
    uv_config = pyproject.get("tool", {}).get("uv")
    if not isinstance(uv_config, dict):
        _fail("pyproject.toml must contain [tool.uv]")
    required_uv = uv_config.get("required-version")
    if not isinstance(required_uv, str) or _EXACT_UV_VERSION.fullmatch(required_uv) is None:
        _fail("[tool.uv].required-version must be an exact ==major.minor.patch pin")
    if uv_config.get("python-preference") != "only-system":
        _fail("[tool.uv].python-preference must be 'only-system'")
    hashed_uv_bootstrap = _read_hashed_requirements(
        uv_bootstrap_path, label="uv bootstrap"
    )
    expected_uv_bootstrap = {
        ("uv", (), required_uv, None)
    }
    if set(hashed_uv_bootstrap) != expected_uv_bootstrap:
        _fail("uv-bootstrap.txt must exactly match [tool.uv].required-version")

    build_system = pyproject.get("build-system")
    if not isinstance(build_system, dict):
        _fail("pyproject.toml must contain [build-system]")
    build_requires = build_system.get("requires")
    if not isinstance(build_requires, list) or not build_requires:
        _fail("[build-system].requires must be a non-empty list")
    expected_build_requirements = [
        _parse_requirement(requirement) for requirement in build_requires
    ]
    configured_build_constraints = uv_config.get(
        "build-constraint-dependencies"
    )
    if not isinstance(configured_build_constraints, list):
        _fail("[tool.uv].build-constraint-dependencies must be a list")
    parsed_configured_build_constraints = [
        _parse_requirement(requirement)
        for requirement in configured_build_constraints
    ]
    if Counter(parsed_configured_build_constraints) != Counter(
        expected_build_requirements
    ):
        _fail(
            "[tool.uv].build-constraint-dependencies must exactly match "
            "[build-system].requires"
        )
    hashed_build_constraints = _read_hashed_requirements(
        build_constraints_path, label="build constraints"
    )
    if set(hashed_build_constraints) != set(expected_build_requirements):
        _fail(
            "build-constraints.txt requirements must exactly match "
            "[build-system].requires"
        )
    locked_manifest = lock.get("manifest")
    if not isinstance(locked_manifest, dict):
        _fail("uv.lock must contain [manifest]")
    locked_build_constraints_raw = locked_manifest.get("build-constraints")
    if not isinstance(locked_build_constraints_raw, list):
        _fail("uv.lock build-constraints must be a list")
    locked_build_constraints = [
        _locked_requirement(
            requirement, context=f"build-constraints[{index}]"
        )
        for index, requirement in enumerate(locked_build_constraints_raw)
    ]
    if Counter(locked_build_constraints) != Counter(expected_build_requirements):
        _fail("uv.lock build-constraints do not match pyproject.toml")

    if lock.get("version") != LOCK_FORMAT_VERSION:
        _fail(f"uv.lock version must be {LOCK_FORMAT_VERSION}")
    revision = lock.get("revision")
    if (
        not isinstance(revision, int)
        or isinstance(revision, bool)
        or revision < MINIMUM_LOCK_REVISION
    ):
        _fail(f"uv.lock revision must be at least {MINIMUM_LOCK_REVISION}")
    if lock.get("requires-python") != project.get("requires-python"):
        _fail("uv.lock requires-python does not match pyproject.toml")

    project_name = _canonical_name(project.get("name"), context="project.name")
    project_version = project.get("version")
    if not isinstance(project_version, str) or not project_version:
        _fail("project.version must be a non-empty string")

    packages = lock.get("package")
    if not isinstance(packages, list) or not packages:
        _fail("uv.lock must contain at least one [[package]]")

    project_packages: list[dict[str, Any]] = []
    registry_package_count = 0
    locked_names: set[str] = set()
    seen_identities: set[tuple[str, str, str]] = set()

    for index, package in enumerate(packages):
        context = f"package[{index}]"
        if not isinstance(package, dict):
            _fail(f"{context} must be a table")
        name = _canonical_name(package.get("name"), context=f"{context}.name")
        version = package.get("version")
        if not isinstance(version, str) or not version:
            _fail(f"{context}.version must be a non-empty string")
        source = package.get("source")
        if source == {"editable": "."}:
            source_kind = "editable"
            project_packages.append(package)
        elif source == {"registry": PYPI_INDEX}:
            source_kind = "registry"
            registry_package_count += 1
            artifacts: list[tuple[str, object]] = []
            if "sdist" in package:
                artifacts.append(("sdist", package["sdist"]))
            wheels = package.get("wheels", [])
            if not isinstance(wheels, list):
                _fail(f"{context}.wheels must be a list")
            artifacts.extend(
                (f"wheels[{wheel_index}]", wheel)
                for wheel_index, wheel in enumerate(wheels)
            )
            if not artifacts:
                _fail(f"{context} must contain at least one hashed artifact")
            for artifact_label, artifact in artifacts:
                _validate_artifact(
                    artifact, context=f"{context}.{artifact_label}"
                )
        else:
            _fail(
                f"{context}.source must be the canonical PyPI index or editable '.'"
            )

        identity = (name, version, source_kind)
        if identity in seen_identities:
            _fail(f"duplicate locked package identity: {identity}")
        seen_identities.add(identity)
        locked_names.add(name)

    if len(project_packages) != 1:
        _fail("uv.lock must contain exactly one editable project package")
    locked_project = project_packages[0]
    if (
        _canonical_name(
            locked_project.get("name"), context="editable project package.name"
        )
        != project_name
        or locked_project.get("version") != project_version
    ):
        _fail("editable project identity does not match pyproject.toml")

    main_requirements = [
        _parse_requirement(requirement)
        for requirement in project.get("dependencies", [])
    ]
    optional_requirements = project.get("optional-dependencies", {})
    if not isinstance(optional_requirements, dict):
        _fail("project.optional-dependencies must be a table")
    expected_metadata = list(main_requirements)
    expected_optional_edges: dict[str, list[tuple[str, tuple[str, ...]]]] = {}
    for raw_group, requirements in optional_requirements.items():
        group = _canonical_name(raw_group, context="optional dependency group")
        if group in expected_optional_edges:
            _fail(
                f"optional dependency groups collide after normalization: {raw_group!r}"
            )
        if not isinstance(requirements, list):
            _fail(f"optional dependency group {raw_group!r} must be a list")
        parsed = [
            _parse_requirement(
                requirement, marker=f"extra == '{group}'"
            )
            for requirement in requirements
        ]
        expected_metadata.extend(parsed)
        expected_optional_edges[group] = [
            (name, extras) for name, extras, _specifier, _marker in parsed
        ]

    metadata = locked_project.get("metadata")
    if not isinstance(metadata, dict):
        _fail("editable project package must contain metadata")
    locked_metadata_raw = metadata.get("requires-dist")
    if not isinstance(locked_metadata_raw, list):
        _fail("editable project metadata.requires-dist must be a list")
    locked_metadata = [
        _locked_requirement(item, context=f"metadata.requires-dist[{index}]")
        for index, item in enumerate(locked_metadata_raw)
    ]
    if Counter(locked_metadata) != Counter(expected_metadata):
        _fail("editable project metadata.requires-dist does not match pyproject.toml")

    locked_main_raw = locked_project.get("dependencies", [])
    if not isinstance(locked_main_raw, list):
        _fail("editable project dependencies must be a list")
    locked_main = [
        _dependency_edge(item, context=f"project.dependencies[{index}]")
        for index, item in enumerate(locked_main_raw)
    ]
    expected_main = [
        (name, extras) for name, extras, _specifier, _marker in main_requirements
    ]
    if Counter(locked_main) != Counter(expected_main):
        _fail("editable project dependency edges do not match pyproject.toml")

    locked_optional = locked_project.get("optional-dependencies", {})
    if not isinstance(locked_optional, dict):
        _fail("editable project optional-dependencies must be a table")
    normalized_locked_optional: dict[
        str, list[tuple[str, tuple[str, ...]]]
    ] = {}
    for raw_group, dependencies in locked_optional.items():
        group = _canonical_name(raw_group, context="locked optional dependency group")
        if group in normalized_locked_optional:
            _fail(
                "locked optional dependency groups collide after normalization: "
                f"{raw_group!r}"
            )
        if not isinstance(dependencies, list):
            _fail(f"locked optional dependency group {raw_group!r} must be a list")
        normalized_locked_optional[group] = [
            _dependency_edge(
                item, context=f"project.optional-dependencies.{raw_group}[{index}]"
            )
            for index, item in enumerate(dependencies)
        ]
    if set(normalized_locked_optional) != set(expected_optional_edges):
        _fail("locked optional dependency groups do not match pyproject.toml")
    for group, expected in expected_optional_edges.items():
        if Counter(normalized_locked_optional[group]) != Counter(expected):
            _fail(
                f"locked optional dependency group {group!r} does not match "
                "pyproject.toml"
            )

    provides_extras = locked_project.get("metadata", {}).get("provides-extras", [])
    if not isinstance(provides_extras, list):
        _fail("editable project metadata.provides-extras must be a list")
    normalized_provides_extras = {
        _canonical_name(item, context="metadata.provides-extras")
        for item in provides_extras
    }
    if len(normalized_provides_extras) != len(provides_extras):
        _fail("metadata.provides-extras contains duplicates after normalization")
    if normalized_provides_extras != set(expected_optional_edges):
        _fail("metadata.provides-extras does not match pyproject.toml")

    for package_index, package in enumerate(packages):
        dependencies = package.get("dependencies", [])
        if not isinstance(dependencies, list):
            _fail(f"package[{package_index}].dependencies must be a list")
        for dependency_index, dependency in enumerate(dependencies):
            if not isinstance(dependency, dict):
                _fail(
                    f"package[{package_index}].dependencies[{dependency_index}] "
                    "must be a table"
                )
            dependency_name = _canonical_name(
                dependency.get("name"),
                context=(
                    f"package[{package_index}].dependencies[{dependency_index}].name"
                ),
            )
            if dependency_name not in locked_names:
                _fail(f"dependency edge references unlocked package {dependency_name!r}")

    digest = hashlib.sha256(lock_path.read_bytes()).hexdigest()
    return LockReport(
        package_count=len(packages),
        registry_package_count=registry_package_count,
        build_requirement_count=len(expected_build_requirements),
        build_artifact_hash_count=sum(
            len(hashes) for hashes in hashed_build_constraints.values()
        ),
        uv_bootstrap_hash_count=sum(
            len(hashes) for hashes in hashed_uv_bootstrap.values()
        ),
        sha256=digest,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify uv.lock provenance, hashes, and manifest consistency."
    )
    parser.add_argument(
        "--pyproject",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "pyproject.toml",
    )
    parser.add_argument(
        "--lock",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "uv.lock",
    )
    parser.add_argument(
        "--build-constraints",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "build-constraints.txt",
    )
    parser.add_argument(
        "--uv-bootstrap",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "uv-bootstrap.txt",
    )
    args = parser.parse_args(argv)
    try:
        report = verify_dependency_lock(
            args.pyproject,
            args.lock,
            args.build_constraints,
            args.uv_bootstrap,
        )
    except DependencyLockError as exc:
        print(f"dependency lock verification failed: {exc}", file=sys.stderr)
        return 1
    print(
        "dependency lock verified: "
        f"packages={report.package_count}, "
        f"registry_packages={report.registry_package_count}, "
        f"build_requirements={report.build_requirement_count}, "
        f"build_hashes={report.build_artifact_hash_count}, "
        f"uv_bootstrap_hashes={report.uv_bootstrap_hash_count}, "
        f"sha256={report.sha256}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
