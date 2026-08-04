"""P1-4 / P1-4.1: standard GPKG staging preview + confirm with TOCTOU hardening.

Flow:
  bytes → isolated staging (random id) + sidecar meta
  → preflight + normalize preview
  → HMAC token (independent signing secret) bound to project/actor/digest/staging/package
  → confirm: exclusive claim → private confirm snapshot → re-hash == token digest
  → import_standard_gpkg(expected_source_sha256=...) from snapshot only
  → cleanup

Extension/MIME are never trusted. Client cannot set synthetic/purpose on confirm.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from ..file_lock import FileLockBusyError, acquire_exclusive_file_lock, release_file_lock
from .design_package import DesignPackageImportError
from .gpkg_import import StandardGpkgImportResult, import_standard_gpkg
from .gpkg_normalize import normalize_standard_gpkg
from .gpkg_preflight import IMPORT_CONTRACT_VERSION, inspect_standard_gpkg
from .storage import FileStorage

DEFAULT_PREVIEW_TTL_SECONDS = 15 * 60
STAGING_SUBDIR = "gpkg-staging"
DEFAULT_MAX_PENDING_PER_ACTOR = 5
DEFAULT_MAX_PENDING_BYTES_PER_ACTOR = 64 * 1024 * 1024
DEFAULT_MAX_SCAN_ENTRIES = 256

# Field validation (shared with API schemas)
PACKAGE_CODE_RE = __import__("re").compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{1,99}$")
STAGING_ID_RE = __import__("re").compile(r"^[a-f0-9]{16,64}$")
DESIGN_VERSION_RE = __import__("re").compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


class GpkgStagingError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(slots=True)
class GpkgPreviewResult:
    valid: bool
    preview_token: str | None
    expires_at_unix: int | None
    source_sha256: str
    size_bytes: int
    import_contract_version: str
    package_code: str
    staging_id: str
    candidate_count: int
    object_codes: list[str]
    layers_summary: list[dict[str, Any]]
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    preflight_valid: bool = False
    normalize_valid: bool = False
    error_code: str | None = None
    source_classification: str = "sample_or_unverified"

    def to_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "preview_token": self.preview_token,
            "expires_at_unix": self.expires_at_unix,
            "source_sha256": self.source_sha256,
            "size_bytes": self.size_bytes,
            "import_contract_version": self.import_contract_version,
            "package_code": self.package_code,
            "staging_id": self.staging_id,
            "candidate_count": self.candidate_count,
            "object_codes": list(self.object_codes),
            "layers_summary": list(self.layers_summary),
            "errors": list(self.errors),
            "warnings": list(self.warnings),
            "preflight_valid": self.preflight_valid,
            "normalize_valid": self.normalize_valid,
            "error_code": self.error_code,
            "source_classification": self.source_classification,
        }


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64url_decode(text: str) -> bytes:
    pad = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + pad)


def _token_secret(signing_secret: str | None) -> bytes:
    if not signing_secret:
        raise GpkgStagingError(
            "preview_token_secret_missing",
            "GPKG preview signing secret is not configured",
        )
    return hashlib.sha256(
        f"fengmou-gpkg-preview-v1:{signing_secret}".encode("utf-8")
    ).digest()


def validate_package_code(package_code: str) -> str:
    code = str(package_code or "").strip()
    if not PACKAGE_CODE_RE.fullmatch(code):
        raise GpkgStagingError(
            "package_code_invalid",
            "package_code must be 2-100 chars of letters, digits, . _ -",
        )
    return code


def validate_staging_id(staging_id: str) -> str:
    sid = str(staging_id or "").strip()
    if not STAGING_ID_RE.fullmatch(sid):
        raise GpkgStagingError("staging_id_invalid", "Invalid staging id")
    return sid


def validate_design_version(design_version: str) -> str:
    ver = str(design_version or "design-v1").strip()
    if not DESIGN_VERSION_RE.fullmatch(ver):
        raise GpkgStagingError(
            "design_version_invalid",
            "design_version must be letters, digits, . _ -",
        )
    return ver


def mint_preview_token(
    *,
    secret: str | None,
    project_id: str,
    actor: str,
    source_sha256: str,
    staging_id: str,
    package_code: str,
    import_contract_version: str,
    ttl_seconds: int = DEFAULT_PREVIEW_TTL_SECONDS,
) -> tuple[str, int]:
    exp = int(time.time()) + max(60, int(ttl_seconds))
    payload = {
        "v": 1,
        "project_id": project_id,
        "actor": actor,
        "source_sha256": source_sha256.lower(),
        "staging_id": staging_id,
        "package_code": package_code,
        "import_contract_version": import_contract_version,
        "exp": exp,
    }
    body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    key = _token_secret(secret)
    sig = hmac.new(key, body, hashlib.sha256).digest()
    return f"{_b64url(body)}.{_b64url(sig)}", exp


def verify_preview_token_payload(
    token: str,
    *,
    secret: str | None,
) -> dict[str, Any]:
    """Verify HMAC and expiry; return payload without file-side binding checks."""
    try:
        body_b64, sig_b64 = token.split(".", 1)
        body = _b64url_decode(body_b64)
        sig = _b64url_decode(sig_b64)
    except Exception as exc:
        raise GpkgStagingError("preview_token_malformed", "Invalid preview token") from exc
    key = _token_secret(secret)
    expected = hmac.new(key, body, hashlib.sha256).digest()
    if not hmac.compare_digest(sig, expected):
        raise GpkgStagingError("preview_token_invalid", "Preview token signature mismatch")
    try:
        payload = json.loads(body.decode("utf-8"))
    except Exception as exc:
        raise GpkgStagingError(
            "preview_token_malformed", "Invalid preview token payload"
        ) from exc
    if int(payload.get("exp") or 0) < int(time.time()):
        raise GpkgStagingError("preview_token_expired", "Preview token expired")
    return payload


def bind_preview_token_payload(
    payload: dict[str, Any],
    *,
    project_id: str,
    actor: str,
    staging_id: str,
    package_code: str,
    import_contract_version: str,
) -> str:
    """Ensure request fields match token; return expected_source_sha256."""
    checks = {
        "project_id": project_id,
        "actor": actor,
        "staging_id": staging_id,
        "package_code": package_code,
        "import_contract_version": import_contract_version,
    }
    for key_name, expected_val in checks.items():
        if str(payload.get(key_name) or "") != str(expected_val):
            raise GpkgStagingError(
                "preview_token_binding_mismatch",
                "Preview token binding mismatch",
            )
    digest = str(payload.get("source_sha256") or "").strip().lower()
    if len(digest) != 64:
        raise GpkgStagingError("preview_token_malformed", "Invalid preview token digest")
    return digest


def staging_root(storage: FileStorage) -> Path:
    path = Path(storage.root) / STAGING_SUBDIR
    path.mkdir(parents=True, exist_ok=True)
    return path


def staging_dir(storage: FileStorage, project_id: str) -> Path:
    # project_id is server-owned UUID; still sanitize
    safe = "".join(ch for ch in project_id if ch.isalnum() or ch in "-_")[:80]
    path = staging_root(storage) / safe
    path.mkdir(parents=True, exist_ok=True)
    return path


def _meta_path(gpkg_path: Path) -> Path:
    return gpkg_path.with_suffix(gpkg_path.suffix + ".meta.json")


def _write_meta(gpkg_path: Path, meta: dict[str, Any]) -> None:
    _meta_path(gpkg_path).write_text(
        json.dumps(meta, sort_keys=True),
        encoding="utf-8",
    )


def _read_meta(gpkg_path: Path) -> dict[str, Any] | None:
    mp = _meta_path(gpkg_path)
    if not mp.is_file():
        return None
    try:
        return json.loads(mp.read_text(encoding="utf-8"))
    except Exception:
        return None


def cleanup_staging(path: Path) -> None:
    try:
        if path.is_file():
            path.unlink()
    except OSError:
        pass
    try:
        mp = _meta_path(path)
        if mp.is_file():
            mp.unlink()
    except OSError:
        pass
    try:
        lock = path.with_suffix(path.suffix + ".lock")
        if lock.is_file():
            lock.unlink()
    except OSError:
        pass


def _is_symlink_or_reparse(path: Path) -> bool:
    try:
        if path.is_symlink():
            return True
    except OSError:
        return True
    try:
        st = path.lstat()
        attrs = int(getattr(st, "st_file_attributes", 0) or 0)
        if attrs & 0x400:  # FILE_ATTRIBUTE_REPARSE_POINT
            return True
    except OSError:
        return True
    return False


def purge_expired_staging(
    storage: FileStorage,
    *,
    ttl_seconds: int,
    max_scan: int = DEFAULT_MAX_SCAN_ENTRIES,
    now: float | None = None,
    out_stats: dict[str, int] | None = None,
) -> int:
    """Truly bounded scan: iterative iterdir, stop at max_scan entries total.

    Does not pre-list entire directories. Does not follow symlinks/reparse points.
    Project dirs and file entries share one scan budget.
    """
    root = staging_root(storage)
    cutoff = (now if now is not None else time.time()) - max(60, int(ttl_seconds))
    deleted = 0
    scanned = 0
    try:
        project_iter = root.iterdir()
    except OSError:
        if out_stats is not None:
            out_stats["scanned"] = 0
            out_stats["deleted"] = 0
        return 0
    for pdir in project_iter:
        if scanned >= max_scan:
            break
        scanned += 1
        try:
            if _is_symlink_or_reparse(pdir) or not pdir.is_dir():
                continue
        except OSError:
            continue
        try:
            file_iter = pdir.iterdir()
        except OSError:
            continue
        for entry in file_iter:
            if scanned >= max_scan:
                break
            scanned += 1
            try:
                if _is_symlink_or_reparse(entry) or not entry.is_file():
                    continue
            except OSError:
                continue
            if entry.name.endswith(".lock"):
                try:
                    if entry.stat().st_mtime < cutoff:
                        entry.unlink()
                except OSError:
                    pass
                continue
            if not entry.name.endswith(".gpkg"):
                continue
            try:
                mtime = entry.stat().st_mtime
            except OSError:
                continue
            if mtime < cutoff:
                cleanup_staging(entry)
                deleted += 1
        if scanned >= max_scan:
            break
    if out_stats is not None:
        out_stats["scanned"] = scanned
        out_stats["deleted"] = deleted
    return deleted


def _actor_pending_usage(
    storage: FileStorage,
    *,
    project_id: str,
    actor: str,
    max_scan: int = DEFAULT_MAX_SCAN_ENTRIES,
) -> tuple[int, int, int]:
    """Return (count, total_bytes, scanned) with iterative bounded scan."""
    pdir = staging_dir(storage, project_id)
    count = 0
    total = 0
    scanned = 0
    try:
        file_iter = pdir.iterdir()
    except OSError:
        return 0, 0, 0
    for entry in file_iter:
        if scanned >= max_scan:
            break
        scanned += 1
        try:
            if _is_symlink_or_reparse(entry) or not entry.is_file():
                continue
        except OSError:
            continue
        if not entry.name.endswith(".gpkg") or ".confirm." in entry.name:
            continue
        meta = _read_meta(entry)
        if meta is None or str(meta.get("actor") or "") != actor:
            continue
        count += 1
        try:
            total += int(entry.stat().st_size)
        except OSError:
            pass
    return count, total, scanned


def _quota_lock_path(storage: FileStorage, project_id: str, actor: str) -> Path:
    safe_actor = hashlib.sha256(actor.encode("utf-8")).hexdigest()[:16]
    return staging_dir(storage, project_id) / f".quota-{safe_actor}.lock"


def write_staging_file(
    storage: FileStorage,
    *,
    project_id: str,
    actor: str,
    raw: bytes,
    package_code: str,
    ttl_seconds: int,
) -> tuple[str, Path]:
    """Quota check + write under exclusive per-(project,actor) lock."""
    purge_expired_staging(storage, ttl_seconds=ttl_seconds)
    lock_path = _quota_lock_path(storage, project_id, actor)
    lock_handle = None
    dest: Path | None = None
    try:
        lock_handle = lock_path.open("a+b")
        try:
            acquire_exclusive_file_lock(lock_handle, nonblocking=False)
        except FileLockBusyError as exc:
            raise GpkgStagingError(
                "staging_quota_busy",
                "Staging quota lock busy; retry shortly",
            ) from exc

        count, total, _scanned = _actor_pending_usage(
            storage, project_id=project_id, actor=actor
        )
        if count >= DEFAULT_MAX_PENDING_PER_ACTOR:
            raise GpkgStagingError(
                "staging_quota_count",
                "Too many pending uploads; confirm or wait for expiry",
            )
        if total + len(raw) > DEFAULT_MAX_PENDING_BYTES_PER_ACTOR:
            raise GpkgStagingError(
                "staging_quota_bytes",
                "Pending upload capacity exceeded; confirm or wait for expiry",
            )

        sid = secrets.token_hex(16)
        dest = staging_dir(storage, project_id) / f"{sid}.gpkg"
        # write temp then rename for fewer orphan windows
        tmp = dest.with_suffix(dest.suffix + ".partial")
        try:
            tmp.write_bytes(raw)
            tmp.replace(dest)
        except Exception:
            try:
                if tmp.is_file():
                    tmp.unlink()
            except OSError:
                pass
            raise
        try:
            _write_meta(
                dest,
                {
                    "staging_id": sid,
                    "project_id": project_id,
                    "actor": actor,
                    "package_code": package_code,
                    "created_at_unix": int(time.time()),
                    "size_bytes": len(raw),
                },
            )
        except Exception:
            cleanup_staging(dest)
            raise
        return sid, dest
    except Exception:
        if dest is not None:
            cleanup_staging(dest)
        raise
    finally:
        if lock_handle is not None:
            try:
                release_file_lock(lock_handle)
            except Exception:
                pass
            try:
                lock_handle.close()
            except Exception:
                pass


def preview_standard_gpkg_bytes(
    storage: FileStorage,
    *,
    project_id: str,
    package_code: str,
    raw: bytes,
    actor: str,
    token_secret: str | None,
    max_bytes: int,
    ttl_seconds: int = DEFAULT_PREVIEW_TTL_SECONDS,
) -> GpkgPreviewResult:
    package_code = validate_package_code(package_code)
    if max_bytes <= 0:
        raise GpkgStagingError("max_bytes_invalid", "upload limit must be positive")
    if len(raw) == 0:
        raise GpkgStagingError("empty_upload", "Empty upload")
    if len(raw) > max_bytes:
        raise GpkgStagingError("file_too_large", "Upload exceeds configured size limit")

    staging_id, staging_path = write_staging_file(
        storage,
        project_id=project_id,
        actor=actor,
        raw=raw,
        package_code=package_code,
        ttl_seconds=ttl_seconds,
    )
    errors: list[str] = []
    warnings: list[str] = []
    try:
        preflight = inspect_standard_gpkg(staging_path)
        digest = preflight.source_sha256
        size_bytes = preflight.size_bytes
        layers_summary = [
            {
                "name": layer.name,
                "accepted": layer.accepted,
                "whitelisted": layer.whitelisted,
                "feature_count": layer.feature_count,
                "resolved_epsg": layer.resolved_epsg,
                "rejection_reasons": list(layer.rejection_reasons),
            }
            for layer in preflight.layers
        ]
        errors.extend(preflight.errors)
        warnings.extend(preflight.warnings)

        candidates: list[str] = []
        normalize_valid = False
        if preflight.valid:
            norm = normalize_standard_gpkg(staging_path)
            normalize_valid = norm.valid
            if not norm.valid:
                errors.extend(norm.errors)
            else:
                candidates = [c.object_code for c in norm.candidates]
                # Ensure preview digest matches normalize digest
                if norm.source_sha256.lower() != (digest or "").lower():
                    errors.append("preview_digest_inconsistent")
                    normalize_valid = False
            warnings.extend(norm.warnings)
        else:
            cleanup_staging(staging_path)
            return GpkgPreviewResult(
                valid=False,
                preview_token=None,
                expires_at_unix=None,
                source_sha256=digest or "",
                size_bytes=size_bytes,
                import_contract_version=IMPORT_CONTRACT_VERSION,
                package_code=package_code,
                staging_id=staging_id,
                candidate_count=0,
                object_codes=[],
                layers_summary=layers_summary,
                errors=errors,
                warnings=warnings,
                preflight_valid=False,
                normalize_valid=False,
                error_code="preflight_failed",
            )

        if not normalize_valid or not candidates:
            cleanup_staging(staging_path)
            if not candidates and "no_candidates" not in errors:
                errors.append("no_candidates")
            return GpkgPreviewResult(
                valid=False,
                preview_token=None,
                expires_at_unix=None,
                source_sha256=digest,
                size_bytes=size_bytes,
                import_contract_version=IMPORT_CONTRACT_VERSION,
                package_code=package_code,
                staging_id=staging_id,
                candidate_count=0,
                object_codes=[],
                layers_summary=layers_summary,
                errors=errors,
                warnings=warnings,
                preflight_valid=preflight.valid,
                normalize_valid=normalize_valid,
                error_code="normalize_failed",
            )

        token, exp = mint_preview_token(
            secret=token_secret,
            project_id=project_id,
            actor=actor,
            source_sha256=digest,
            staging_id=staging_id,
            package_code=package_code,
            import_contract_version=IMPORT_CONTRACT_VERSION,
            ttl_seconds=ttl_seconds,
        )
        return GpkgPreviewResult(
            valid=True,
            preview_token=token,
            expires_at_unix=exp,
            source_sha256=digest,
            size_bytes=size_bytes,
            import_contract_version=IMPORT_CONTRACT_VERSION,
            package_code=package_code,
            staging_id=staging_id,
            candidate_count=len(candidates),
            object_codes=candidates,
            layers_summary=layers_summary,
            errors=[],
            warnings=warnings,
            preflight_valid=True,
            normalize_valid=True,
            error_code=None,
        )
    except GpkgStagingError:
        cleanup_staging(staging_path)
        raise
    except Exception as exc:
        cleanup_staging(staging_path)
        raise GpkgStagingError("preview_failed", "Preview failed") from exc


def _create_confirm_snapshot(
    staging_path: Path,
    *,
    expected_source_sha256: str,
) -> Path:
    """Copy staging to a private snapshot and verify digest before import."""
    if not staging_path.is_file():
        raise GpkgStagingError("staging_not_found", "Staging file not found or expired")
    nonce = secrets.token_hex(8)
    snapshot = staging_path.with_name(f"{staging_path.stem}.confirm.{nonce}.gpkg")
    # Binary copy without following client-controlled paths (already under staging dir).
    data = staging_path.read_bytes()
    digest = hashlib.sha256(data).hexdigest().lower()
    if digest != expected_source_sha256.lower():
        # Do not delete staging on bind/digest mismatch (anti DoS of other uploads).
        raise GpkgStagingError(
            "source_sha256_mismatch",
            "Staging file digest does not match preview token",
        )
    snapshot.write_bytes(data)
    # Re-hash snapshot itself
    snap_digest = hashlib.sha256(snapshot.read_bytes()).hexdigest().lower()
    if snap_digest != expected_source_sha256.lower():
        cleanup_staging(snapshot)
        raise GpkgStagingError(
            "source_sha256_mismatch",
            "Confirm snapshot digest does not match preview token",
        )
    return snapshot


def confirm_standard_gpkg_import(
    db: Session,
    storage: FileStorage,
    *,
    project_id: str,
    package_code: str,
    staging_id: str,
    preview_token: str,
    actor: str,
    token_secret: str | None,
    design_version: str = "design-v1",
    ttl_seconds: int = DEFAULT_PREVIEW_TTL_SECONDS,
) -> StandardGpkgImportResult:
    """Confirm import with private snapshot + expected digest barrier.

    Server forces purpose=controlled, synthetic=True (sample_or_unverified).
    Client cannot override authenticity classification.
    """
    package_code = validate_package_code(package_code)
    staging_id = validate_staging_id(staging_id)
    design_version = validate_design_version(design_version)

    purge_expired_staging(storage, ttl_seconds=ttl_seconds)

    payload = verify_preview_token_payload(preview_token, secret=token_secret)
    expected_sha = bind_preview_token_payload(
        payload,
        project_id=project_id,
        actor=actor,
        staging_id=staging_id,
        package_code=package_code,
        import_contract_version=IMPORT_CONTRACT_VERSION,
    )

    staging_path = staging_dir(storage, project_id) / f"{staging_id}.gpkg"
    if not staging_path.is_file():
        # Token expired files may already be purged
        raise GpkgStagingError("staging_not_found", "Staging file not found or expired")

    lock_path = staging_path.with_suffix(staging_path.suffix + ".lock")
    lock_handle = None
    snapshot: Path | None = None
    try:
        lock_handle = lock_path.open("a+b")
        try:
            acquire_exclusive_file_lock(lock_handle, nonblocking=True)
        except FileLockBusyError as exc:
            raise GpkgStagingError(
                "confirm_in_progress",
                "Another confirm is already in progress for this upload",
            ) from exc

        # Claim: if already claimed/deleted by concurrent winner
        if not staging_path.is_file():
            raise GpkgStagingError(
                "confirm_already_completed",
                "This upload was already confirmed or removed",
            )

        snapshot = _create_confirm_snapshot(
            staging_path, expected_source_sha256=expected_sha
        )

        try:
            result = import_standard_gpkg(
                db,
                project_id=project_id,
                gpkg_path=snapshot,
                package_code=package_code,
                purpose="controlled",
                design_version=design_version,
                source_filename=f"{staging_id}.gpkg",
                storage_path=f"imported:{expected_sha[:16]}",
                synthetic=True,
                actor=actor,
                expected_source_sha256=expected_sha,
                force_sample_classification=True,
            )
        except DesignPackageImportError as exc:
            # Non-retryable normalize/digest failures: clean staging
            if exc.code in {
                "source_sha256_mismatch",
                "normalize_rejected",
                "normalize_failed",
                "digest_missing",
            }:
                cleanup_staging(staging_path)
            raise GpkgStagingError(exc.code, str(exc)) from exc

        # Success: remove staging + snapshot
        cleanup_staging(staging_path)
        if snapshot is not None:
            cleanup_staging(snapshot)
        return result
    finally:
        if snapshot is not None and snapshot.is_file():
            cleanup_staging(snapshot)
        if lock_handle is not None:
            try:
                release_file_lock(lock_handle)
            except Exception:
                pass
            try:
                lock_handle.close()
            except Exception:
                pass
            try:
                if lock_path.is_file():
                    lock_path.unlink()
            except OSError:
                pass


__all__ = [
    "DEFAULT_PREVIEW_TTL_SECONDS",
    "GpkgPreviewResult",
    "GpkgStagingError",
    "STAGING_SUBDIR",
    "bind_preview_token_payload",
    "cleanup_staging",
    "confirm_standard_gpkg_import",
    "mint_preview_token",
    "preview_standard_gpkg_bytes",
    "purge_expired_staging",
    "validate_design_version",
    "validate_package_code",
    "validate_staging_id",
    "verify_preview_token_payload",
    "write_staging_file",
]
