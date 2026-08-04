from __future__ import annotations

import json
import os
import shutil
import stat
import uuid
import zipfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..file_lock import FileLockBusyError, acquire_exclusive_file_lock, release_file_lock
from ..models import (
    AuditEvent,
    DesignBaseline,
    EvidenceAsset,
    FindingCase,
    HumanReview,
    ProofRecord,
    RemediationAttempt,
    SealOperation,
    StructuredReport,
    VerificationJob,
    utcnow,
)
from .analysis import add_audit
from .proof import ZERO_HASH, _read_ledger, _record_hash, merkle_root, verify_proof_archive
from .reporting import render_report_bytes
from .remediation import (
    RemediationIntegrityError,
    finalize_remediation_after_seal,
    validate_remediation_graph,
    validate_frozen_remediation_context,
)
from .storage import (
    FileStorage,
    canonical_json_bytes,
    design_baseline_sha256,
    sha256_bytes,
    sha256_file,
)


class SealBusyError(RuntimeError):
    pass


class SealIntegrityError(RuntimeError):
    pass


RESUMABLE_SEAL_STATES = frozenset({"requested", "artifacts_staged", "files_published", "ledger_appended"})


def _safe_error(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"[:1000]


def _is_uuid(value: str) -> bool:
    try:
        return str(uuid.UUID(value)) == value
    except (AttributeError, TypeError, ValueError):
        return False


def _validate_operation_identity(operation: SealOperation) -> None:
    if not _is_uuid(operation.id) or not _is_uuid(operation.report_id):
        raise SealIntegrityError("Seal operation contains an invalid identifier")
    if not operation.archive_id.startswith("ARC-") or not _is_uuid(operation.archive_id.removeprefix("ARC-")):
        raise SealIntegrityError("Seal operation contains an invalid archive identifier")


def _validate_storage_layout(storage: FileStorage) -> None:
    for directory in (
        storage.root,
        storage.evidence_dir,
        storage.report_dir,
        storage.archive_dir,
        storage.seal_staging_dir,
        storage.seal_lock_dir,
    ):
        try:
            metadata = directory.lstat()
        except OSError as exc:
            raise SealIntegrityError("Required storage directory is unavailable") from exc
        if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
            raise SealIntegrityError("Required storage directory is not a direct directory")
    if storage.ledger_path.is_symlink():
        raise SealIntegrityError("Proof ledger must not be a symbolic link")


def _operation_staging_dir(storage: FileStorage, operation: SealOperation) -> Path:
    _validate_operation_identity(operation)
    path = storage.seal_staging_dir / operation.id
    if path.parent != storage.seal_staging_dir:
        raise SealIntegrityError("Seal staging path escaped the storage root")
    if path.is_symlink():
        raise SealIntegrityError("Seal staging directory must not be a symbolic link")
    return path


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return _utc(value).isoformat()


def _fsync_dir(path: Path) -> None:
    if not hasattr(os, "O_DIRECTORY"):
        return
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.unlink(missing_ok=True)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(temporary, flags, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        _fsync_dir(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def _exclusive_file_lock(path: Path, *, nonblocking: bool = False) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o600)
    with os.fdopen(descriptor, "a+b") as handle:
        try:
            acquire_exclusive_file_lock(handle, nonblocking=nonblocking)
        except FileLockBusyError as exc:
            raise SealBusyError("The seal operation is already running") from exc
        try:
            yield
        finally:
            release_file_lock(handle)


def _zip_info(name: str, created_at: datetime) -> zipfile.ZipInfo:
    timestamp = _utc(created_at)
    info = zipfile.ZipInfo(name, timestamp.timetuple()[:6])
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o600 << 16
    return info


def _write_zip_bytes(bundle: zipfile.ZipFile, name: str, data: bytes, created_at: datetime) -> None:
    bundle.writestr(_zip_info(name, created_at), data)


def _validate_source_integrity(
    storage: FileStorage,
    *,
    baseline: DesignBaseline,
    evidence: EvidenceAsset,
) -> None:
    digest = design_baseline_sha256(
        project_id=baseline.project_id,
        site_id=baseline.site_id,
        procedure_code=baseline.procedure_code,
        version=baseline.version,
        source_type=baseline.source_type,
        expected=baseline.expected,
    )
    if digest != baseline.sha256:
        raise SealIntegrityError("Design baseline content changed after registration")
    with storage.validate_evidence_file(
        storage_path=evidence.storage_path,
        stored_name=evidence.stored_name,
        expected_content_type=evidence.content_type,
        expected_size=evidence.size_bytes,
        expected_sha256=evidence.sha256,
    ):
        pass


def _json_entry(path: str, value: Any) -> tuple[str, bytes, dict[str, Any]]:
    data = canonical_json_bytes(value)
    return path, data, {"path": path, "sha256": sha256_bytes(data), "size_bytes": len(data)}


def _validate_frozen_snapshot(
    operation: SealOperation,
    *,
    job: VerificationJob,
    review: HumanReview,
    baseline: DesignBaseline,
    evidence: EvidenceAsset,
) -> dict[str, Any]:
    content = operation.report_content_json
    if not isinstance(content, dict):
        raise SealIntegrityError("Seal operation does not contain a frozen report snapshot")
    frozen_baseline = content.get("design_baseline")
    frozen_evidence = content.get("evidence")
    frozen_review = content.get("human_review")
    if not all(isinstance(value, dict) for value in (frozen_baseline, frozen_evidence, frozen_review)):
        raise SealIntegrityError("Frozen report snapshot has invalid linked-record metadata")
    if content.get("analysis") != job.result_json:
        raise SealIntegrityError("Verification result changed after seal intent was recorded")
    if (
        frozen_baseline.get("id") != baseline.id
        or frozen_baseline.get("sha256") != baseline.sha256
        or frozen_baseline.get("expected") != baseline.expected
    ):
        raise SealIntegrityError("Design baseline differs from the frozen seal snapshot")
    if (
        frozen_evidence.get("id") != evidence.id
        or frozen_evidence.get("sha256") != evidence.sha256
        or frozen_evidence.get("size_bytes") != evidence.size_bytes
        or frozen_evidence.get("original_name") != evidence.original_name
    ):
        raise SealIntegrityError("Evidence metadata differs from the frozen seal snapshot")
    if (
        frozen_review.get("id") != review.id
        or frozen_review.get("decision") != review.decision
        or frozen_review.get("reviewer") != review.reviewer
        or frozen_review.get("note") != review.note
        or frozen_review.get("reviewed_at") != _iso(review.reviewed_at)
    ):
        raise SealIntegrityError("Human review differs from the frozen seal snapshot")
    return content


def _build_staged_artifacts(db: Session, storage: FileStorage, operation: SealOperation) -> dict[str, Any]:
    job = db.get(VerificationJob, operation.job_id)
    review = db.get(HumanReview, operation.review_id)
    if job is None or review is None:
        raise SealIntegrityError("Seal operation lost its job or review record")
    baseline = db.get(DesignBaseline, job.baseline_id)
    evidence = db.get(EvidenceAsset, job.evidence_id)
    if baseline is None or evidence is None:
        raise SealIntegrityError("Seal operation lost linked evidence or baseline metadata")
    _validate_source_integrity(storage, baseline=baseline, evidence=evidence)
    content = _validate_frozen_snapshot(
        operation,
        job=job,
        review=review,
        baseline=baseline,
        evidence=evidence,
    )
    try:
        validate_frozen_remediation_context(db, job, content.get("remediation_context"))
    except RemediationIntegrityError as exc:
        raise SealIntegrityError(str(exc)) from exc

    _validate_storage_layout(storage)
    _validate_operation_identity(operation)
    staging = _operation_staging_dir(storage, operation)
    staging.mkdir(parents=True, exist_ok=True)
    report_json = staging / f"{operation.report_id}.json"
    report_html = staging / f"{operation.report_id}.html"
    archive = staging / f"{operation.archive_id}.zip"
    if operation.report_status is None or operation.purpose is None:
        raise SealIntegrityError("Seal operation does not contain a frozen report snapshot")
    report_status = operation.report_status
    json_bytes, html_bytes = render_report_bytes(content, report_id=operation.report_id)
    _atomic_write(report_json, json_bytes)
    _atomic_write(report_html, html_bytes)

    evidence_member = f"evidence/original{Path(evidence.stored_name).suffix.lower()}"
    entries: list[dict[str, Any]] = [
        {
            "path": evidence_member,
            "sha256": evidence.sha256,
            "size_bytes": evidence.size_bytes,
            "source": "original_evidence",
        }
    ]
    json_entries = [
        _json_entry("analysis/result.json", content.get("analysis")),
        _json_entry(
            "design/baseline.json",
            {
                **dict(content.get("design_baseline") or {}),
            },
        ),
        _json_entry(
            "review/human-review.json",
            {
                **dict(content.get("human_review") or {}),
            },
        ),
        _json_entry(
            "sensors/events.json",
            content.get("related_sensor_events") or [],
        ),
        _json_entry(
            "findings/cases-at-seal.json",
            content.get("finding_cases") or [],
        ),
    ]
    remediation_context = content.get("remediation_context")
    if isinstance(remediation_context, dict):
        json_entries.extend(
            [
                _json_entry("remediation/case.json", remediation_context.get("case")),
                _json_entry("remediation/attempt.json", remediation_context.get("attempt")),
            ]
        )
    entries.extend(item[2] for item in json_entries)
    report_sha = sha256_bytes(json_bytes)
    html_sha = sha256_bytes(html_bytes)
    entries.extend(
        [
            {
                "path": "report/report.json",
                "sha256": report_sha,
                "size_bytes": len(json_bytes),
                "source": "final_structured_report",
            },
            {
                "path": "report/report.html",
                "sha256": html_sha,
                "size_bytes": len(html_bytes),
                "source": "printable_report",
            },
        ]
    )
    root = merkle_root(entries)
    evidence_grade = operation.evidence_grade
    purpose = operation.purpose
    manifest = {
        "schema_version": "1.0",
        "archive_id": operation.archive_id,
        "issued_at": _iso(operation.created_at),
        "project_id": job.project_id,
        "job_id": job.id,
        "report_id": operation.report_id,
        "purpose": purpose,
        "evidence_grade": evidence_grade,
        "merkle_root": root,
        "merkle_rule": "Leaves are SHA-256(path + NUL + member_sha256), sorted by path; duplicate the final node at odd levels.",
        "files": entries,
        "verification_rule": "Every listed member must exist and match its SHA-256 digest.",
        "timestamp_boundary": "issued_at is application time, not an external trusted timestamp.",
        "submission_boundary": "Bundles with evidence_grade=false must not be used as competition metric or submission evidence.",
    }
    manifest_bytes = canonical_json_bytes(manifest)
    temporary_archive = archive.with_suffix(".zip.tmp")
    temporary_archive.unlink(missing_ok=True)
    try:
        with storage.validate_evidence_file(
            storage_path=evidence.storage_path,
            stored_name=evidence.stored_name,
            expected_content_type=evidence.content_type,
            expected_size=evidence.size_bytes,
            expected_sha256=evidence.sha256,
        ) as validated:
            with zipfile.ZipFile(temporary_archive, "w", allowZip64=True) as bundle:
                with bundle.open(_zip_info(evidence_member, operation.created_at), "w") as target:
                    while chunk := os.read(validated.fileno(), 1024 * 1024):
                        target.write(chunk)
                for member_path, data, _ in json_entries:
                    _write_zip_bytes(bundle, member_path, data, operation.created_at)
                _write_zip_bytes(bundle, "report/report.json", json_bytes, operation.created_at)
                _write_zip_bytes(bundle, "report/report.html", html_bytes, operation.created_at)
                _write_zip_bytes(bundle, "manifest.json", manifest_bytes, operation.created_at)
        with temporary_archive.open("rb+") as handle:
            os.fsync(handle.fileno())
        os.replace(temporary_archive, archive)
        _fsync_dir(staging)
    finally:
        temporary_archive.unlink(missing_ok=True)

    return {
        "content": content,
        "report_status": report_status,
        "purpose": purpose,
        "evidence_grade": evidence_grade,
        "report_sha256": report_sha,
        "html_sha256": html_sha,
        "manifest_sha256": sha256_bytes(manifest_bytes),
        "archive_sha256": sha256_file(archive),
        "merkle_root": root,
    }


def _stage_artifacts(db: Session, storage: FileStorage, operation: SealOperation) -> None:
    values = _build_staged_artifacts(db, storage, operation)
    if operation.report_sha256 is not None:
        expected = (
            operation.report_sha256,
            operation.html_sha256,
            operation.manifest_sha256,
            operation.archive_sha256,
            operation.merkle_root,
        )
        actual = (
            values["report_sha256"],
            values["html_sha256"],
            values["manifest_sha256"],
            values["archive_sha256"],
            values["merkle_root"],
        )
        if expected != actual:
            raise SealIntegrityError("Rebuilt staging artifacts differ from the persisted seal operation")
    operation.report_content_json = values["content"]
    operation.report_status = values["report_status"]
    operation.purpose = values["purpose"]
    operation.evidence_grade = values["evidence_grade"]
    operation.report_sha256 = values["report_sha256"]
    operation.html_sha256 = values["html_sha256"]
    operation.manifest_sha256 = values["manifest_sha256"]
    operation.archive_sha256 = values["archive_sha256"]
    operation.merkle_root = values["merkle_root"]
    operation.state = "artifacts_staged"
    operation.last_error = None
    db.commit()


def _publish_one(source: Path, destination: Path, expected_sha256: str) -> None:
    if source.is_symlink() or destination.is_symlink():
        raise SealIntegrityError("Seal artifact path must not be a symbolic link")
    if destination.exists():
        if sha256_file(destination) != expected_sha256:
            raise SealIntegrityError(f"Published artifact conflicts with seal operation: {destination.name}")
        source.unlink(missing_ok=True)
        return
    if not source.is_file() or sha256_file(source) != expected_sha256:
        raise SealIntegrityError(f"Staged artifact is missing or changed: {source.name}")
    os.replace(source, destination)


def _publish_artifacts(db: Session, storage: FileStorage, operation: SealOperation) -> None:
    required = (
        operation.report_sha256,
        operation.html_sha256,
        operation.archive_sha256,
    )
    if not all(required):
        raise SealIntegrityError("Seal operation has incomplete staged digests")
    _validate_storage_layout(storage)
    staging = _operation_staging_dir(storage, operation)
    stage_paths = (
        staging / f"{operation.report_id}.json",
        staging / f"{operation.report_id}.html",
        staging / f"{operation.archive_id}.zip",
    )
    if not all(path.is_file() for path in stage_paths) and not all(
        path.is_file()
        for path in (
            storage.report_dir / f"{operation.report_id}.json",
            storage.report_dir / f"{operation.report_id}.html",
            storage.archive_dir / f"{operation.archive_id}.zip",
        )
    ):
        _stage_artifacts(db, storage, operation)
        operation = db.get(SealOperation, operation.id) or operation
    _publish_one(
        staging / f"{operation.report_id}.json",
        storage.report_dir / f"{operation.report_id}.json",
        operation.report_sha256 or "",
    )
    _publish_one(
        staging / f"{operation.report_id}.html",
        storage.report_dir / f"{operation.report_id}.html",
        operation.html_sha256 or "",
    )
    _publish_one(
        staging / f"{operation.archive_id}.zip",
        storage.archive_dir / f"{operation.archive_id}.zip",
        operation.archive_sha256 or "",
    )
    _fsync_dir(storage.report_dir)
    _fsync_dir(storage.archive_dir)
    operation.state = "files_published"
    operation.last_error = None
    db.commit()


def _validated_ledger(rows: list[dict[str, Any]]) -> None:
    previous = ZERO_HASH
    for index, row in enumerate(rows):
        if row.get("ledger_index") != index or row.get("previous_record_hash") != previous:
            raise SealIntegrityError("Ledger index or predecessor chain is invalid")
        expected = _record_hash(
            archive_id=row["archive_id"],
            manifest_sha256=row["manifest_sha256"],
            archive_sha256=row["archive_sha256"],
            previous_record_hash=row["previous_record_hash"],
            ledger_index=index,
            purpose=row["purpose"],
            evidence_grade=row["evidence_grade"],
            merkle_root_value=row["merkle_root"],
        )
        if row.get("record_hash") != expected:
            raise SealIntegrityError("Ledger record hash is invalid")
        previous = expected


def _publish_ledger(storage: FileStorage, operation: SealOperation) -> dict[str, Any]:
    _validate_storage_layout(storage)
    _validate_operation_identity(operation)
    required = (
        operation.manifest_sha256,
        operation.archive_sha256,
        operation.purpose,
        operation.merkle_root,
    )
    if not all(required):
        raise SealIntegrityError("Seal operation has incomplete proof metadata")
    lock_path = storage.seal_lock_dir / "proof-ledger.lock"
    with _exclusive_file_lock(lock_path):
        try:
            rows = _read_ledger(storage.ledger_path)
            _validated_ledger(rows)
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise SealIntegrityError("Existing proof ledger is unreadable or invalid") from exc
        matches = [row for row in rows if row.get("archive_id") == operation.archive_id]
        if matches:
            row = matches[0]
            stable = {
                "archive_id": operation.archive_id,
                "manifest_sha256": operation.manifest_sha256,
                "archive_sha256": operation.archive_sha256,
                "purpose": operation.purpose,
                "evidence_grade": operation.evidence_grade,
                "merkle_root": operation.merkle_root,
            }
            if any(row.get(key) != value for key, value in stable.items()):
                raise SealIntegrityError("Existing ledger row conflicts with this seal operation")
            return row
        index = len(rows)
        previous = rows[-1]["record_hash"] if rows else ZERO_HASH
        record_hash = _record_hash(
            archive_id=operation.archive_id,
            manifest_sha256=operation.manifest_sha256 or "",
            archive_sha256=operation.archive_sha256 or "",
            previous_record_hash=previous,
            ledger_index=index,
            purpose=operation.purpose or "",
            evidence_grade=operation.evidence_grade,
            merkle_root_value=operation.merkle_root or "",
        )
        row = {
            "ledger_index": index,
            "archive_id": operation.archive_id,
            "manifest_sha256": operation.manifest_sha256,
            "archive_sha256": operation.archive_sha256,
            "previous_record_hash": previous,
            "record_hash": record_hash,
            "purpose": operation.purpose,
            "evidence_grade": operation.evidence_grade,
            "merkle_root": operation.merkle_root,
            "created_at": _iso(operation.created_at),
        }
        rows.append(row)
        payload = b"".join(
            json.dumps(item, ensure_ascii=False, sort_keys=True).encode("utf-8") + b"\n" for item in rows
        )
        _atomic_write(storage.ledger_path, payload)
        return row


def _record_ledger(db: Session, storage: FileStorage, operation: SealOperation) -> None:
    row = _publish_ledger(storage, operation)
    operation.ledger_index = row["ledger_index"]
    operation.previous_record_hash = row["previous_record_hash"]
    operation.record_hash = row["record_hash"]
    operation.ledger_row_json = row
    operation.state = "ledger_appended"
    operation.last_error = None
    db.commit()


def _validate_published_operation(storage: FileStorage, operation: SealOperation) -> None:
    """Recheck every published byte and ledger field before DB approval."""

    _validate_storage_layout(storage)
    _validate_operation_identity(operation)
    required = (
        operation.report_sha256,
        operation.html_sha256,
        operation.manifest_sha256,
        operation.archive_sha256,
        operation.merkle_root,
        operation.purpose,
        operation.previous_record_hash,
        operation.record_hash,
        operation.ledger_row_json,
        operation.ledger_index is not None,
    )
    if not all(required):
        raise SealIntegrityError("Published seal operation has incomplete integrity metadata")
    report_json = storage.report_dir / f"{operation.report_id}.json"
    report_html = storage.report_dir / f"{operation.report_id}.html"
    archive = storage.archive_dir / f"{operation.archive_id}.zip"
    for path, expected in (
        (report_json, operation.report_sha256),
        (report_html, operation.html_sha256),
        (archive, operation.archive_sha256),
    ):
        if path.is_symlink() or not path.is_file() or sha256_file(path) != expected:
            raise SealIntegrityError(f"Published artifact is missing or changed: {path.name}")
    try:
        rows = _read_ledger(storage.ledger_path)
        _validated_ledger(rows)
        row = rows[operation.ledger_index or 0]
    except (OSError, IndexError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise SealIntegrityError("Published ledger row is unavailable or invalid") from exc
    if row != operation.ledger_row_json or row.get("record_hash") != operation.record_hash:
        raise SealIntegrityError("Published ledger row differs from the seal operation")
    candidate = ProofRecord(
        archive_id=operation.archive_id,
        report_id=operation.report_id,
        purpose=operation.purpose or "",
        evidence_grade=operation.evidence_grade,
        merkle_root=operation.merkle_root or "",
        manifest_sha256=operation.manifest_sha256 or "",
        archive_sha256=operation.archive_sha256 or "",
        previous_record_hash=operation.previous_record_hash or "",
        record_hash=operation.record_hash or "",
        archive_path=str(archive),
        ledger_index=operation.ledger_index or 0,
        created_at=operation.created_at,
    )
    try:
        if not verify_proof_archive(candidate, storage)["valid"]:
            raise SealIntegrityError("Published proof archive failed full integrity verification")
    except OSError as exc:
        raise SealIntegrityError("Published proof archive could not be read") from exc


def _completed_database_graph(
    db: Session,
    storage: FileStorage,
    operation: SealOperation,
) -> tuple[VerificationJob, HumanReview, StructuredReport, ProofRecord]:
    """Validate every persisted row that makes one seal operation completed."""

    if operation.state != "completed" or operation.last_error is not None:
        raise SealIntegrityError("Completed seal operation has an invalid terminal state")
    job = db.get(VerificationJob, operation.job_id, populate_existing=True)
    review = db.get(HumanReview, operation.review_id, populate_existing=True)
    report = db.get(StructuredReport, operation.report_id, populate_existing=True)
    proof = db.scalar(
        select(ProofRecord)
        .where(ProofRecord.archive_id == operation.archive_id)
        .execution_options(populate_existing=True)
    )
    if job is None or review is None or report is None or proof is None:
        raise SealIntegrityError("Completed seal operation has missing database records")
    if job.status != "approved" or review.job_id != job.id or review.decision != "approve":
        raise SealIntegrityError("Completed seal operation has an invalid job or review binding")
    baseline = db.get(DesignBaseline, job.baseline_id, populate_existing=True)
    evidence = db.get(EvidenceAsset, job.evidence_id, populate_existing=True)
    if baseline is None or evidence is None:
        raise SealIntegrityError("Completed seal operation lost linked evidence or baseline metadata")
    if (
        baseline.project_id != job.project_id
        or evidence.project_id != job.project_id
        or evidence.baseline_id != job.baseline_id
    ):
        raise SealIntegrityError("Completed seal operation has an invalid source-record binding")
    _validate_frozen_snapshot(
        operation,
        job=job,
        review=review,
        baseline=baseline,
        evidence=evidence,
    )
    if report.schema_version != "1.0":
        raise SealIntegrityError("Completed seal operation report has an unexpected schema version")
    expected_report = (
        job.id,
        job.project_id,
        operation.report_status,
        operation.report_content_json,
        str(storage.report_dir / f"{operation.report_id}.json"),
        str(storage.report_dir / f"{operation.report_id}.html"),
        operation.report_sha256,
        operation.html_sha256,
    )
    actual_report = (
        report.job_id,
        report.project_id,
        report.status,
        report.content_json,
        report.json_path,
        report.html_path,
        report.sha256,
        report.html_sha256,
    )
    if actual_report != expected_report:
        raise SealIntegrityError("Completed seal operation report differs from its frozen operation")
    expected_proof = (
        report.id,
        operation.purpose,
        operation.evidence_grade,
        operation.merkle_root,
        operation.manifest_sha256,
        operation.archive_sha256,
        operation.previous_record_hash,
        operation.record_hash,
        str(storage.archive_dir / f"{operation.archive_id}.zip"),
        operation.ledger_index,
    )
    actual_proof = (
        proof.report_id,
        proof.purpose,
        proof.evidence_grade,
        proof.merkle_root,
        proof.manifest_sha256,
        proof.archive_sha256,
        proof.previous_record_hash,
        proof.record_hash,
        proof.archive_path,
        proof.ledger_index,
    )
    if actual_proof != expected_proof:
        raise SealIntegrityError("Completed seal operation proof differs from its frozen operation")
    approval_audits = list(
        db.scalars(
            select(AuditEvent).where(
                AuditEvent.entity_type == "verification_job",
                AuditEvent.entity_id == job.id,
                AuditEvent.action == "approved_and_sealed",
            )
        ).all()
    )
    expected_audit_payload = {
        "report_id": report.id,
        "archive_id": proof.archive_id,
        "seal_operation_id": operation.id,
    }
    if len(approval_audits) != 1 or approval_audits[0].payload_json != expected_audit_payload:
        raise SealIntegrityError("Completed seal operation has an invalid approval audit")
    remediation_attempt = db.scalar(
        select(RemediationAttempt).where(RemediationAttempt.verification_job_id == job.id)
    )
    if remediation_attempt is not None:
        case = db.get(FindingCase, remediation_attempt.case_id)
        if case is None:
            raise SealIntegrityError("Completed remediation seal lost its finding case")
        try:
            validate_remediation_graph(db, storage, case)
        except RemediationIntegrityError as exc:
            raise SealIntegrityError(str(exc)) from exc
    return job, review, report, proof


def _complete_operation(db: Session, storage: FileStorage, operation: SealOperation, actor: str) -> None:
    _validate_published_operation(storage, operation)
    job = db.get(VerificationJob, operation.job_id)
    if job is None or operation.report_content_json is None:
        raise SealIntegrityError("Seal operation cannot complete without job and report content")
    if not all(
        (
            operation.report_status,
            operation.report_sha256,
            operation.html_sha256,
            operation.purpose,
            operation.manifest_sha256,
            operation.archive_sha256,
            operation.merkle_root,
            operation.previous_record_hash,
            operation.record_hash,
            operation.ledger_index is not None,
        )
    ):
        raise SealIntegrityError("Seal operation cannot complete with missing metadata")
    report = db.get(StructuredReport, operation.report_id)
    if report is None:
        report = StructuredReport(
            id=operation.report_id,
            job_id=job.id,
            project_id=job.project_id,
            status=operation.report_status or "final",
            schema_version="1.0",
            content_json=operation.report_content_json,
            json_path=str(storage.report_dir / f"{operation.report_id}.json"),
            html_path=str(storage.report_dir / f"{operation.report_id}.html"),
            sha256=operation.report_sha256 or "",
            html_sha256=operation.html_sha256 or "",
            created_at=operation.created_at,
        )
        db.add(report)
        db.flush()
    else:
        expected_report = (
            job.id,
            job.project_id,
            operation.report_status,
            operation.report_content_json,
            str(storage.report_dir / f"{operation.report_id}.json"),
            str(storage.report_dir / f"{operation.report_id}.html"),
            operation.report_sha256,
            operation.html_sha256,
        )
        actual_report = (
            report.job_id,
            report.project_id,
            report.status,
            report.content_json,
            report.json_path,
            report.html_path,
            report.sha256,
            report.html_sha256,
        )
        if actual_report != expected_report:
            raise SealIntegrityError("Existing structured report conflicts with seal operation")
    proof = db.scalar(select(ProofRecord).where(ProofRecord.archive_id == operation.archive_id))
    if proof is None:
        proof = ProofRecord(
            id=operation.archive_id.removeprefix("ARC-"),
            archive_id=operation.archive_id,
            report_id=operation.report_id,
            purpose=operation.purpose or "review",
            evidence_grade=operation.evidence_grade,
            merkle_root=operation.merkle_root or "",
            manifest_sha256=operation.manifest_sha256 or "",
            archive_sha256=operation.archive_sha256 or "",
            previous_record_hash=operation.previous_record_hash or "",
            record_hash=operation.record_hash or "",
            archive_path=str(storage.archive_dir / f"{operation.archive_id}.zip"),
            ledger_index=operation.ledger_index or 0,
            created_at=operation.created_at,
        )
        db.add(proof)
        db.flush()
    else:
        expected_proof = (
            operation.report_id,
            operation.purpose,
            operation.evidence_grade,
            operation.merkle_root,
            operation.manifest_sha256,
            operation.archive_sha256,
            operation.previous_record_hash,
            operation.record_hash,
            str(storage.archive_dir / f"{operation.archive_id}.zip"),
            operation.ledger_index,
        )
        actual_proof = (
            proof.report_id,
            proof.purpose,
            proof.evidence_grade,
            proof.merkle_root,
            proof.manifest_sha256,
            proof.archive_sha256,
            proof.previous_record_hash,
            proof.record_hash,
            proof.archive_path,
            proof.ledger_index,
        )
        if actual_proof != expected_proof:
            raise SealIntegrityError("Existing proof record conflicts with seal operation")
    existing_audit = db.scalar(
        select(AuditEvent).where(
            AuditEvent.entity_type == "verification_job",
            AuditEvent.entity_id == job.id,
            AuditEvent.action == "approved_and_sealed",
        )
    )
    if existing_audit is None:
        add_audit(
            db,
            entity_type="verification_job",
            entity_id=job.id,
            action="approved_and_sealed",
            actor=actor,
            payload={"report_id": report.id, "archive_id": proof.archive_id, "seal_operation_id": operation.id},
        )
    elif existing_audit.payload_json != {
        "report_id": report.id,
        "archive_id": proof.archive_id,
        "seal_operation_id": operation.id,
    }:
        raise SealIntegrityError("Existing approval audit conflicts with seal operation")
    try:
        finalize_remediation_after_seal(db, job=job, report=report, proof=proof)
    except RemediationIntegrityError as exc:
        raise SealIntegrityError(str(exc)) from exc
    transition = db.execute(
        VerificationJob.__table__.update()
        .where(VerificationJob.id == job.id, VerificationJob.status == "sealing")
        .values(status="approved", progress=100)
    )
    if transition.rowcount != 1:
        raise SealIntegrityError("Verification job is no longer in the sealing state")
    operation.state = "completed"
    operation.last_error = None
    db.commit()


def _load_persisted_completion(
    db: Session,
    storage: FileStorage,
    operation_id: str,
) -> tuple[VerificationJob, HumanReview, StructuredReport, ProofRecord] | None:
    """Resolve an uncertain commit outcome using a fresh database session.

    A database driver may raise after the server has durably committed.  The
    request session cannot distinguish that case from a pre-commit failure and
    may also retain identity-map objects because ``expire_on_commit`` is
    disabled.  A fresh read is therefore the authority before recording a
    failed seal attempt.
    """

    with Session(bind=db.get_bind(), expire_on_commit=False) as reconciliation:
        operation = reconciliation.get(SealOperation, operation_id)
        if operation is None or operation.state != "completed":
            return None
        _validate_published_operation(storage, operation)
        job, review, report, proof = _completed_database_graph(reconciliation, storage, operation)
        for item in (job, review, report, proof):
            reconciliation.expunge(item)
        return job, review, report, proof


def _cleanup_staging_best_effort(storage: FileStorage, operation: SealOperation) -> None:
    """Remove recoverable scratch data without changing the seal outcome."""

    try:
        staging = _operation_staging_dir(storage, operation)
        if staging.exists():
            shutil.rmtree(staging)
    except (OSError, SealIntegrityError):
        # Published artifacts, ledger and database rows are already authoritative.
        # A later replay/startup can retry this bounded housekeeping step.
        return


def _persist_manual_attention(
    db: Session,
    *,
    operation_id: str,
    actor: str,
    error: SealIntegrityError,
) -> None:
    """Persist one terminal integrity incident without duplicating its audit."""

    db.rollback()
    failed = db.get(SealOperation, operation_id, populate_existing=True)
    if failed is None:
        return
    failed.state = "manual_attention"
    failed.last_error = _safe_error(error)
    existing_audit = db.scalar(
        select(AuditEvent.id).where(
            AuditEvent.entity_type == "seal_operation",
            AuditEvent.entity_id == operation_id,
            AuditEvent.action == "seal_manual_attention",
        )
    )
    if existing_audit is None:
        add_audit(
            db,
            entity_type="seal_operation",
            entity_id=operation_id,
            action="seal_manual_attention",
            actor=actor,
            payload={"error": failed.last_error},
        )
    db.commit()


def resume_seal_operation(
    db: Session,
    storage: FileStorage,
    *,
    operation_id: str,
    actor: str,
) -> tuple[VerificationJob, HumanReview, StructuredReport, ProofRecord]:
    if not _is_uuid(operation_id):
        raise SealIntegrityError("Seal operation identifier is invalid")
    _validate_storage_layout(storage)
    lock_path = storage.seal_lock_dir / f"{operation_id}.lock"
    with _exclusive_file_lock(lock_path, nonblocking=True):
        operation = db.get(SealOperation, operation_id)
        if operation is None:
            raise SealIntegrityError("Seal operation no longer exists")
        _validate_operation_identity(operation)
        if operation.state == "manual_attention":
            raise SealIntegrityError(operation.last_error or "Seal operation requires manual attention")
        try:
            allowed_states = RESUMABLE_SEAL_STATES | {"completed"}
            if operation.state not in allowed_states:
                raise SealIntegrityError("Seal operation has an unknown state")
            if operation.state != "completed":
                operation.attempt_count += 1
                operation.last_error = None
                db.commit()
            if operation.state == "requested":
                _stage_artifacts(db, storage, operation)
            if operation.state == "artifacts_staged":
                _publish_artifacts(db, storage, operation)
            if operation.state == "files_published":
                _record_ledger(db, storage, operation)
            if operation.state == "ledger_appended":
                _complete_operation(db, storage, operation, actor)
            if operation.state == "completed":
                _validate_published_operation(storage, operation)
        except SealIntegrityError as exc:
            _persist_manual_attention(db, operation_id=operation_id, actor=actor, error=exc)
            raise
        except Exception as exc:
            db.rollback()
            try:
                reconciled = _load_persisted_completion(db, storage, operation_id)
            except SealIntegrityError as integrity_exc:
                _persist_manual_attention(
                    db,
                    operation_id=operation_id,
                    actor=actor,
                    error=integrity_exc,
                )
                raise
            if reconciled is not None:
                persisted_operation = db.get(SealOperation, operation_id, populate_existing=True)
                if persisted_operation is not None:
                    _cleanup_staging_best_effort(storage, persisted_operation)
                return reconciled
            failed = db.get(SealOperation, operation_id, populate_existing=True)
            if failed is not None:
                failed.last_error = _safe_error(exc)
                add_audit(
                    db,
                    entity_type="seal_operation",
                    entity_id=operation_id,
                    action="seal_attempt_failed",
                    actor=actor,
                    payload={"state": failed.state, "error": failed.last_error},
                )
                db.commit()
            raise
        operation = db.get(SealOperation, operation_id, populate_existing=True)
        if operation is None or operation.state != "completed":
            raise SealIntegrityError("Seal operation stopped before completion")
        try:
            job, review, report, proof = _completed_database_graph(db, storage, operation)
        except SealIntegrityError as exc:
            _persist_manual_attention(db, operation_id=operation_id, actor=actor, error=exc)
            raise
        _cleanup_staging_best_effort(storage, operation)
        return job, review, report, proof


def scan_sealing_integrity(db: Session, storage: FileStorage) -> list[str]:
    issues: list[str] = []
    try:
        _validate_storage_layout(storage)
    except SealIntegrityError as exc:
        return [f"storage layout is invalid: {_safe_error(exc)}"]
    operations = db.scalars(select(SealOperation)).all()
    reports = db.scalars(select(StructuredReport)).all()
    proofs = db.scalars(select(ProofRecord)).all()
    for report in reports:
        try:
            json_path = Path(report.json_path)
            html_path = Path(report.html_path)
            rendered_json, rendered_html = render_report_bytes(report.content_json, report_id=report.id)
            valid = (
                json_path == storage.report_dir / f"{report.id}.json"
                and html_path == storage.report_dir / f"{report.id}.html"
                and not json_path.is_symlink()
                and not html_path.is_symlink()
                and json_path.is_file()
                and html_path.is_file()
                and sha256_file(json_path) == report.sha256
                and sha256_file(html_path) == report.html_sha256
                and sha256_bytes(rendered_json) == report.sha256
                and sha256_bytes(rendered_html) == report.html_sha256
            )
        except (OSError, TypeError, ValueError):
            valid = False
        if not valid:
            issues.append(f"structured report {report.id} has missing or changed files")
    for proof in proofs:
        try:
            valid = (
                Path(proof.archive_path) == storage.archive_dir / f"{proof.archive_id}.zip"
                and not Path(proof.archive_path).is_symlink()
                and verify_proof_archive(proof, storage)["valid"]
            )
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError, zipfile.BadZipFile):
            valid = False
        if not valid:
            issues.append(f"proof archive {proof.archive_id} failed integrity verification")
    valid_operations: list[SealOperation] = []
    for operation in operations:
        try:
            _validate_operation_identity(operation)
        except SealIntegrityError:
            issues.append("seal operation has invalid persisted identifiers")
            continue
        valid_operations.append(operation)
        if operation.state != "completed":
            issues.append(f"seal operation {operation.id} is {operation.state}")
            continue
        if operation.last_error is not None:
            issues.append(f"completed seal operation {operation.id} retains a failure error")
            continue
        try:
            _completed_database_graph(db, storage, operation)
        except SealIntegrityError as exc:
            issues.append(f"completed seal operation {operation.id} has inconsistent database records: {_safe_error(exc)}")
    try:
        ledger = _read_ledger(storage.ledger_path)
        _validated_ledger(ledger)
        known = {item.archive_id for item in proofs}
        known.update(item.archive_id for item in valid_operations if item.state != "completed")
        for row in ledger:
            if row.get("archive_id") not in known:
                issues.append(f"ledger archive {row.get('archive_id')} has no database or recovery operation")
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError, SealIntegrityError) as exc:
        issues.append(f"proof ledger is unreadable or invalid: {_safe_error(exc)}")
    expected_reports = {storage.report_dir / f"{item.id}.json" for item in reports}
    expected_reports.update(storage.report_dir / f"{item.id}.html" for item in reports)
    expected_reports.update(storage.report_dir / f"{item.report_id}.json" for item in valid_operations)
    expected_reports.update(storage.report_dir / f"{item.report_id}.html" for item in valid_operations)
    expected_archives = {storage.archive_dir / f"{item.archive_id}.zip" for item in proofs}
    expected_archives.update(storage.archive_dir / f"{item.archive_id}.zip" for item in valid_operations)
    try:
        for path in (*storage.report_dir.glob("*.json"), *storage.report_dir.glob("*.html")):
            if path.resolve() not in expected_reports:
                issues.append(f"orphan report artifact detected: {path.name}")
        for path in storage.archive_dir.glob("*.zip"):
            if path.resolve() not in expected_archives:
                issues.append(f"orphan proof archive detected: {path.name}")
    except OSError as exc:
        issues.append(f"artifact directories could not be scanned: {_safe_error(exc)}")
    return issues


def recover_seal_operations(app: Any) -> list[str]:
    database = app.state.database
    storage = app.state.storage
    with database.session_factory() as db:
        operation_ids = db.scalars(
            select(SealOperation.id).where(SealOperation.state.not_in(("completed", "manual_attention")))
        ).all()
    for operation_id in operation_ids:
        try:
            with database.session_factory() as db:
                resume_seal_operation(db, storage, operation_id=operation_id, actor="startup-recovery")
        except Exception:
            continue
    with database.session_factory() as db:
        return scan_sealing_integrity(db, storage)
