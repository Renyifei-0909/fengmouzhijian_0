from __future__ import annotations

import hashlib
import hmac
import json
import os
import stat
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import HTTPException, UploadFile, status


ALLOWED_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".jpg", ".jpeg", ".png"}
ACCEPTED_CONTENT_TYPES = {
    ".mp4": frozenset({"video/mp4"}),
    ".mov": frozenset({"video/quicktime"}),
    ".avi": frozenset({"video/x-msvideo", "video/avi"}),
    ".mkv": frozenset({"video/x-matroska", "video/mkv"}),
    ".webm": frozenset({"video/webm"}),
    ".jpg": frozenset({"image/jpeg", "image/pjpeg"}),
    ".jpeg": frozenset({"image/jpeg", "image/pjpeg"}),
    ".png": frozenset({"image/png"}),
}
CANONICAL_CONTENT_TYPES = {
    ".mp4": "video/mp4",
    ".mov": "video/quicktime",
    ".avi": "video/x-msvideo",
    ".mkv": "video/x-matroska",
    ".webm": "video/webm",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
}


def _signature_matches(extension: str, header: bytes) -> bool:
    if extension in {".mp4", ".mov"}:
        return b"ftyp" in header[:64]
    if extension == ".avi":
        return header.startswith(b"RIFF") and header[8:12] == b"AVI "
    if extension in {".mkv", ".webm"}:
        return header.startswith(b"\x1aE\xdf\xa3")
    if extension in {".jpg", ".jpeg"}:
        return header.startswith(b"\xff\xd8\xff")
    if extension == ".png":
        return header.startswith(b"\x89PNG\r\n\x1a\n")
    return False


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def design_baseline_sha256(
    *,
    project_id: str,
    site_id: str,
    procedure_code: str,
    version: str,
    source_type: str,
    expected: dict[str, Any],
) -> str:
    """Return the canonical digest used when a design baseline is sealed."""

    return sha256_bytes(
        canonical_json_bytes(
            {
                "project_id": project_id,
                "site_id": site_id,
                "procedure_code": procedure_code,
                "version": version,
                "source_type": source_type,
                "expected": expected,
            }
        )
    )


@dataclass(frozen=True, slots=True)
class StoredUpload:
    original_name: str
    stored_name: str
    path: Path
    content_type: str
    size_bytes: int
    sha256: str


@dataclass(slots=True)
class ValidatedStoredFile:
    """An open evidence descriptor that matched its sealed database metadata."""

    path: Path
    stat_result: os.stat_result
    content_type: str
    descriptor: int | None
    stored_name: str | None = None
    sha256: str | None = None

    def fileno(self) -> int:
        if self.descriptor is None:
            raise ValueError("Validated evidence descriptor is closed")
        return self.descriptor

    def close(self) -> None:
        if self.descriptor is not None:
            os.close(self.descriptor)
            self.descriptor = None

    def __enter__(self) -> "ValidatedStoredFile":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


class StoredFileMissingError(FileNotFoundError):
    """The database record exists but its stored bytes no longer do."""


class StoredFileIntegrityError(RuntimeError):
    """Stored bytes or their path no longer match the ingestion boundary."""


class FileStorage:
    def __init__(self, root: Path, max_upload_bytes: int) -> None:
        self.root = root.resolve()
        self.max_upload_bytes = max_upload_bytes
        self.evidence_dir = self.root / "evidence"
        self.report_dir = self.root / "reports"
        self.archive_dir = self.root / "archives"
        self.seal_staging_dir = self.root / ".seal-staging"
        self.seal_lock_dir = self.root / ".seal-locks"
        self.ledger_path = self.root / "proof-ledger.jsonl"

    def ensure(self) -> None:
        for directory in (
            self.root,
            self.evidence_dir,
            self.report_dir,
            self.archive_dir,
            self.seal_staging_dir,
            self.seal_lock_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)

    def validate_evidence_file(
        self,
        *,
        storage_path: str | Path,
        stored_name: str,
        expected_content_type: str,
        expected_size: int,
        expected_sha256: str,
    ) -> ValidatedStoredFile:
        """Validate one ingested evidence file without following a final symlink.

        The database path is treated as untrusted state. It must still name the
        direct ``evidence/<stored_name>`` child created by :meth:`save_upload`.
        The method checks the storage directories and final member with
        ``lstat``, opens the member with ``O_NOFOLLOW`` where supported, and
        hashes the opened descriptor before returning a stable stat snapshot.
        """

        try:
            raw_path = Path(storage_path)
        except (TypeError, ValueError) as exc:
            raise StoredFileIntegrityError("Evidence storage path is invalid") from exc
        if not raw_path.is_absolute() or ".." in raw_path.parts:
            raise StoredFileIntegrityError("Evidence storage path escaped the configured storage root")
        if (
            not stored_name
            or "/" in stored_name
            or "\\" in stored_name
            or Path(stored_name).name != stored_name
            or stored_name in {".", ".."}
        ):
            raise StoredFileIntegrityError("Evidence stored name is invalid")

        extension = Path(stored_name).suffix.lower()
        canonical_content_type = CANONICAL_CONTENT_TYPES.get(extension)
        if canonical_content_type is None or not hmac.compare_digest(expected_content_type, canonical_content_type):
            raise StoredFileIntegrityError("Evidence media metadata no longer matches its stored extension")
        if expected_size <= 0:
            raise StoredFileIntegrityError("Evidence size metadata is invalid")
        if len(expected_sha256) != 64 or any(character not in "0123456789abcdef" for character in expected_sha256):
            raise StoredFileIntegrityError("Evidence SHA-256 metadata is invalid")

        candidate = Path(os.path.normpath(os.fspath(raw_path)))
        expected_path = self.evidence_dir / stored_name
        try:
            candidate.relative_to(self.root)
        except ValueError as exc:
            raise StoredFileIntegrityError("Evidence storage path escaped the configured storage root") from exc
        if candidate != expected_path:
            raise StoredFileIntegrityError("Evidence storage path does not match its sealed stored name")

        for directory in (self.root, self.evidence_dir):
            try:
                directory_stat = directory.lstat()
            except FileNotFoundError as exc:
                raise StoredFileMissingError("Evidence storage directory is missing") from exc
            except OSError as exc:
                raise StoredFileIntegrityError("Evidence storage directory could not be validated") from exc
            if stat.S_ISLNK(directory_stat.st_mode) or not stat.S_ISDIR(directory_stat.st_mode):
                raise StoredFileIntegrityError("Evidence storage directory is not a trusted regular directory")

        try:
            path_stat = candidate.lstat()
        except FileNotFoundError as exc:
            raise StoredFileMissingError("Evidence file is missing") from exc
        except OSError as exc:
            raise StoredFileIntegrityError("Evidence file metadata could not be read") from exc
        if stat.S_ISLNK(path_stat.st_mode):
            raise StoredFileIntegrityError("Evidence file is a symbolic link")
        if not stat.S_ISREG(path_stat.st_mode):
            raise StoredFileIntegrityError("Evidence path is not a regular file")
        if path_stat.st_nlink != 1:
            raise StoredFileIntegrityError("Evidence file has an unexpected hard-link count")

        flags = (
            os.O_RDONLY
            | getattr(os, "O_BINARY", 0)
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        try:
            descriptor = os.open(candidate, flags)
        except FileNotFoundError as exc:
            raise StoredFileMissingError("Evidence file is missing") from exc
        except OSError as exc:
            raise StoredFileIntegrityError("Evidence file could not be opened without following links") from exc
        try:
            opened_stat = os.fstat(descriptor)
            if not stat.S_ISREG(opened_stat.st_mode):
                raise StoredFileIntegrityError("Evidence path is not a regular file")
            if opened_stat.st_nlink != 1:
                raise StoredFileIntegrityError("Evidence file has an unexpected hard-link count")
            if (opened_stat.st_dev, opened_stat.st_ino) != (path_stat.st_dev, path_stat.st_ino):
                raise StoredFileIntegrityError("Evidence file changed while its path was validated")
            if opened_stat.st_size != expected_size:
                raise StoredFileIntegrityError("Evidence size no longer matches its ingestion record")

            digest = hashlib.sha256()
            header = bytearray()
            while chunk := os.read(descriptor, 1024 * 1024):
                digest.update(chunk)
                if len(header) < 64:
                    header.extend(chunk[: 64 - len(header)])
            final_descriptor_stat = os.fstat(descriptor)
            stable_fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns")
            if any(getattr(opened_stat, field) != getattr(final_descriptor_stat, field) for field in stable_fields):
                raise StoredFileIntegrityError("Evidence file changed while its digest was calculated")
            if not hmac.compare_digest(digest.hexdigest(), expected_sha256):
                raise StoredFileIntegrityError("Evidence SHA-256 no longer matches its ingestion record")
            if not _signature_matches(extension, bytes(header)):
                raise StoredFileIntegrityError("Evidence magic bytes no longer match its stored extension")
            os.lseek(descriptor, 0, os.SEEK_SET)
        except Exception:
            os.close(descriptor)
            raise

        try:
            final_path_stat = candidate.lstat()
        except FileNotFoundError as exc:
            os.close(descriptor)
            raise StoredFileMissingError("Evidence file disappeared after validation") from exc
        except OSError as exc:
            os.close(descriptor)
            raise StoredFileIntegrityError("Evidence file could not be revalidated") from exc
        if stat.S_ISLNK(final_path_stat.st_mode) or (
            final_path_stat.st_dev,
            final_path_stat.st_ino,
            final_path_stat.st_size,
            final_path_stat.st_mtime_ns,
        ) != (
            opened_stat.st_dev,
            opened_stat.st_ino,
            opened_stat.st_size,
            opened_stat.st_mtime_ns,
        ):
            os.close(descriptor)
            raise StoredFileIntegrityError("Evidence file changed after its digest was calculated")
        return ValidatedStoredFile(
            path=candidate,
            stat_result=final_path_stat,
            content_type=canonical_content_type,
            descriptor=descriptor,
            stored_name=stored_name,
            sha256=expected_sha256,
        )

    async def save_upload(self, upload: UploadFile) -> StoredUpload:
        original_name = Path(upload.filename or "upload.bin").name
        extension = Path(original_name).suffix.lower()
        declared_content_type = (upload.content_type or "application/octet-stream").split(";", 1)[0].strip().lower()
        if extension not in ALLOWED_EXTENSIONS:
            raise HTTPException(
                status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                detail=f"Unsupported file extension: {extension or '[none]'}",
            )
        if declared_content_type not in ACCEPTED_CONTENT_TYPES[extension]:
            raise HTTPException(
                status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                detail=(
                    f"Declared content type {declared_content_type!r} does not match "
                    f"the {extension} extension"
                ),
            )
        content_type = CANONICAL_CONTENT_TYPES[extension]

        stored_name = f"{uuid.uuid4()}{extension}"
        destination = self.evidence_dir / stored_name
        size = 0
        digest = hashlib.sha256()
        header = bytearray()
        try:
            with destination.open("xb") as handle:
                while chunk := await upload.read(1024 * 1024):
                    size += len(chunk)
                    if size > self.max_upload_bytes:
                        raise HTTPException(
                            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                            detail=f"Upload exceeds {self.max_upload_bytes} bytes",
                        )
                    digest.update(chunk)
                    if len(header) < 64:
                        header.extend(chunk[: 64 - len(header)])
                    handle.write(chunk)
        except Exception:
            destination.unlink(missing_ok=True)
            raise
        finally:
            await upload.close()

        if size == 0:
            destination.unlink(missing_ok=True)
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Empty upload is not allowed")
        if not _signature_matches(extension, bytes(header)):
            destination.unlink(missing_ok=True)
            raise HTTPException(
                status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                detail="File signature does not match the declared media extension",
            )

        return StoredUpload(
            original_name=original_name,
            stored_name=stored_name,
            path=destination,
            content_type=content_type,
            size_bytes=size,
            sha256=digest.hexdigest(),
        )
