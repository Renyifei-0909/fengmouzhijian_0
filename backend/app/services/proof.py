from __future__ import annotations

import hashlib
import json
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

from ..models import ProofRecord, utcnow
from .storage import FileStorage, canonical_json_bytes, sha256_bytes, sha256_file


ZERO_HASH = "0" * 64


def _record_hash(
    *,
    archive_id: str,
    manifest_sha256: str,
    archive_sha256: str,
    previous_record_hash: str,
    ledger_index: int,
    purpose: str,
    evidence_grade: bool,
    merkle_root_value: str,
) -> str:
    return sha256_bytes(
        canonical_json_bytes(
            {
                "archive_id": archive_id,
                "manifest_sha256": manifest_sha256,
                "archive_sha256": archive_sha256,
                "previous_record_hash": previous_record_hash,
                "ledger_index": ledger_index,
                "purpose": purpose,
                "evidence_grade": evidence_grade,
                "merkle_root": merkle_root_value,
            }
        )
    )


def merkle_root(entries: list[dict[str, Any]]) -> str:
    leaves = [
        hashlib.sha256(f"{item['path']}\0{item['sha256']}".encode("utf-8")).digest()
        for item in sorted(entries, key=lambda item: item["path"])
    ]
    if not leaves:
        return ZERO_HASH
    level = leaves
    while len(level) > 1:
        if len(level) % 2 == 1:
            level.append(level[-1])
        level = [hashlib.sha256(level[index] + level[index + 1]).digest() for index in range(0, len(level), 2)]
    return level[0].hex()


def _read_ledger(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def _zip_member_sha256(bundle: zipfile.ZipFile, member: str) -> str:
    digest = hashlib.sha256()
    with bundle.open(member, "r") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_proof_archive(proof: ProofRecord, storage: FileStorage) -> dict[str, Any]:
    archive_path = Path(proof.archive_path)
    checks = {
        "archive_exists": archive_path.is_file(),
        "archive_sha256": False,
        "manifest_sha256": False,
        "member_hashes": False,
        "merkle_root": False,
        "record_hash": False,
        "ledger_chain": False,
        "metadata_consistency": False,
    }
    errors: list[str] = []
    if not checks["archive_exists"]:
        errors.append("Archive file is missing")
        return {
            "valid": False,
            "archive_id": proof.archive_id,
            "checked_at": utcnow(),
            "checks": checks,
            "errors": errors,
        }

    checks["archive_sha256"] = sha256_file(archive_path) == proof.archive_sha256
    if not checks["archive_sha256"]:
        errors.append("Archive SHA-256 does not match the sealed record")

    manifest: dict[str, Any] | None = None
    try:
        with zipfile.ZipFile(archive_path, "r") as bundle:
            manifest_bytes = bundle.read("manifest.json")
            checks["manifest_sha256"] = sha256_bytes(manifest_bytes) == proof.manifest_sha256
            manifest = json.loads(manifest_bytes)
            members = set(bundle.namelist())
            member_results = []
            for entry in manifest.get("files", []):
                member = entry.get("path")
                member_results.append(
                    isinstance(member, str)
                    and member in members
                    and _zip_member_sha256(bundle, member) == entry.get("sha256")
                )
            checks["member_hashes"] = bool(member_results) and all(member_results)
            checks["merkle_root"] = merkle_root(manifest.get("files", [])) == manifest.get("merkle_root") == proof.merkle_root
            checks["metadata_consistency"] = (
                manifest.get("archive_id") == proof.archive_id
                and manifest.get("report_id") == proof.report_id
                and manifest.get("purpose") == proof.purpose
                and manifest.get("evidence_grade") == proof.evidence_grade
            )
    except (OSError, KeyError, TypeError, ValueError, AttributeError, json.JSONDecodeError, zipfile.BadZipFile) as exc:
        errors.append(f"Archive content cannot be verified: {exc}")
    if not checks["manifest_sha256"]:
        errors.append("Manifest SHA-256 mismatch")
    if not checks["member_hashes"]:
        errors.append("One or more archive members are missing or modified")
    if not checks["merkle_root"]:
        errors.append("Merkle root mismatch")
    if not checks["metadata_consistency"]:
        errors.append("Proof metadata differs from the sealed manifest")

    expected_record_hash = _record_hash(
        archive_id=proof.archive_id,
        manifest_sha256=proof.manifest_sha256,
        archive_sha256=proof.archive_sha256,
        previous_record_hash=proof.previous_record_hash,
        ledger_index=proof.ledger_index,
        purpose=proof.purpose,
        evidence_grade=proof.evidence_grade,
        merkle_root_value=proof.merkle_root,
    )
    checks["record_hash"] = expected_record_hash == proof.record_hash
    if not checks["record_hash"]:
        errors.append("Proof record hash mismatch")

    try:
        ledger = _read_ledger(storage.ledger_path)
        chain_valid = True
        expected_previous = ZERO_HASH
        for index, row in enumerate(ledger):
            if row.get("ledger_index") != index or row.get("previous_record_hash") != expected_previous:
                chain_valid = False
                break
            recomputed = _record_hash(
                archive_id=row["archive_id"],
                manifest_sha256=row["manifest_sha256"],
                archive_sha256=row["archive_sha256"],
                previous_record_hash=row["previous_record_hash"],
                ledger_index=index,
                purpose=row["purpose"],
                evidence_grade=row["evidence_grade"],
                merkle_root_value=row["merkle_root"],
            )
            if recomputed != row.get("record_hash"):
                chain_valid = False
                break
            expected_previous = row["record_hash"]
        row_matches = (
            proof.ledger_index < len(ledger)
            and ledger[proof.ledger_index].get("record_hash") == proof.record_hash
            and ledger[proof.ledger_index].get("archive_id") == proof.archive_id
        )
        checks["ledger_chain"] = chain_valid and row_matches
    except (OSError, KeyError, TypeError, ValueError, AttributeError, json.JSONDecodeError) as exc:
        errors.append(f"Ledger cannot be verified: {exc}")
    if not checks["ledger_chain"]:
        errors.append("Append-only ledger chain mismatch")

    return {
        "valid": all(checks.values()),
        "archive_id": proof.archive_id,
        "checked_at": utcnow(),
        "checks": checks,
        "errors": errors,
    }
