#!/usr/bin/env python3
"""Offline verifier for a Fengmou evidence ZIP.

This verifier checks the portable bundle itself. A local proof-ledger chain or an
external timestamp/anchor must be checked separately because it is intentionally
not embedded into the archive it authenticates.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import zipfile
from pathlib import Path


def sha256_stream(handle) -> str:
    digest = hashlib.sha256()
    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
        digest.update(chunk)
    return digest.hexdigest()


def sha256_file(path: Path) -> str:
    with path.open("rb") as handle:
        return sha256_stream(handle)


def merkle_root(entries: list[dict]) -> str:
    leaves = [
        hashlib.sha256(f"{item['path']}\0{item['sha256']}".encode("utf-8")).digest()
        for item in sorted(entries, key=lambda item: item["path"])
    ]
    if not leaves:
        return "0" * 64
    level = leaves
    while len(level) > 1:
        if len(level) % 2:
            level.append(level[-1])
        level = [hashlib.sha256(level[index] + level[index + 1]).digest() for index in range(0, len(level), 2)]
    return level[0].hex()


def verify(path: Path, expected_archive_sha256: str | None = None) -> dict:
    checks = {
        "archive_exists": path.is_file(),
        "archive_sha256": expected_archive_sha256 is None,
        "manifest_present": False,
        "member_hashes": False,
        "merkle_root": False,
    }
    errors: list[str] = []
    archive_sha = None
    manifest = None
    if not path.is_file():
        errors.append("Archive does not exist")
        return {"valid": False, "checks": checks, "errors": errors}

    archive_sha = sha256_file(path)
    if expected_archive_sha256 is not None:
        checks["archive_sha256"] = archive_sha == expected_archive_sha256.lower()
        if not checks["archive_sha256"]:
            errors.append("Archive SHA-256 differs from the expected sealed digest")

    try:
        with zipfile.ZipFile(path, "r") as bundle:
            names = set(bundle.namelist())
            checks["manifest_present"] = "manifest.json" in names
            if not checks["manifest_present"]:
                raise KeyError("manifest.json")
            manifest = json.loads(bundle.read("manifest.json"))
            if not isinstance(manifest, dict):
                raise TypeError("manifest.json must contain a JSON object")
            files = manifest.get("files")
            if not isinstance(files, list):
                raise TypeError("manifest.files must be a JSON array")
            results = []
            for entry in files:
                if not isinstance(entry, dict):
                    results.append(False)
                    continue
                member = entry.get("path")
                if not isinstance(member, str) or member not in names:
                    results.append(False)
                    continue
                with bundle.open(member, "r") as handle:
                    results.append(sha256_stream(handle) == entry.get("sha256"))
            checks["member_hashes"] = bool(results) and all(results)
            checks["merkle_root"] = merkle_root(files) == manifest.get("merkle_root")
            if not checks["member_hashes"]:
                errors.append("One or more members are missing or have a SHA-256 mismatch")
            if not checks["merkle_root"]:
                errors.append("Merkle root mismatch")
    except (OSError, KeyError, TypeError, ValueError, AttributeError, json.JSONDecodeError, zipfile.BadZipFile) as exc:
        errors.append(f"Archive structure cannot be verified: {exc}")

    return {
        "valid": all(checks.values()),
        "archive": str(path),
        "archive_sha256": archive_sha,
        "archive_id": manifest.get("archive_id") if isinstance(manifest, dict) else None,
        "purpose": manifest.get("purpose") if isinstance(manifest, dict) else None,
        "evidence_grade": manifest.get("evidence_grade") if isinstance(manifest, dict) else None,
        "checks": checks,
        "errors": errors,
        "boundary": "This command verifies bundle bytes, not identity, trusted time, or blockchain anchoring.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify a Fengmou evidence archive")
    parser.add_argument("archive", type=Path)
    parser.add_argument("--expected-archive-sha256")
    args = parser.parse_args()
    result = verify(args.archive, args.expected_archive_sha256)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    sys.exit(main())
