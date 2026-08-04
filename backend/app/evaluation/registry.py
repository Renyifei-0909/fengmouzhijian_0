from __future__ import annotations

"""Fail-closed single-host coordination for a trusted private registry root.

This is not a broker authorization service. Approval records are unsigned, and
the path checks do not claim isolation from a malicious same-UID process.
"""

from collections.abc import Mapping
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import time
import uuid
from typing import Any, Iterator

from pydantic import ValidationError

from .errors import ContractError, EvaluationError, ExecutionError, IntegrityError
from .registry_schemas import (
    HoldoutAttemptRecord,
    HoldoutReservationReceipt,
    HoldoutReservationRequest,
    QAHoldoutApproval,
)
from .schemas import ID_PATTERN, SHA256_PATTERN


REGISTRY_SCHEMA_VERSION = "evaluation.holdout-registry.v0"
CONSUMPTION_KEY_DOMAIN = b"evaluation.holdout-consumption-key.v0\n"
RESULT_COMMITMENT_PROFILE = "evaluation.controlled-run-core-member-set.v0"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _validation_summary(exc: ValidationError) -> str:
    parts: list[str] = []
    for item in exc.errors(include_url=False, include_input=False):
        location = ".".join(str(part) for part in item["loc"]) or "$"
        parts.append(f"{location}: {item['msg']}")
    return "; ".join(parts)[:2000]


def _parse_request(value: HoldoutReservationRequest | Mapping[str, Any]) -> HoldoutReservationRequest:
    if isinstance(value, HoldoutReservationRequest):
        raw: Mapping[str, Any] = value.model_dump(mode="python")
    elif isinstance(value, Mapping):
        raw = value
    else:
        raise ContractError(
            "EVAL_HOLDOUT_REQUEST_INVALID",
            "Holdout reservation request must be a strict object",
        )
    if raw.get("formal_capability") is None:
        raise ContractError(
            "EVAL_HOLDOUT_FORMAL_CAPABILITY_REQUIRED",
            "A dataset-bound formal evaluation capability is required",
        )
    if raw.get("qa_approval") is None:
        raise ContractError(
            "EVAL_HOLDOUT_QA_APPROVAL_REQUIRED",
            "An explicit dataset-bound QA approval is required",
        )
    try:
        return HoldoutReservationRequest.model_validate(dict(raw))
    except ValidationError as exc:
        raise ContractError(
            "EVAL_HOLDOUT_REQUEST_INVALID",
            _validation_summary(exc),
        ) from exc


def _parse_incident_approval(value: QAHoldoutApproval | Mapping[str, Any]) -> QAHoldoutApproval:
    if isinstance(value, QAHoldoutApproval):
        raw: Mapping[str, Any] = value.model_dump(mode="python")
    elif isinstance(value, Mapping):
        raw = value
    else:
        raise ContractError(
            "EVAL_HOLDOUT_INCIDENT_APPROVAL_INVALID",
            "Incident approval must be a strict object",
        )
    try:
        approval = QAHoldoutApproval.model_validate(dict(raw))
    except ValidationError as exc:
        raise ContractError(
            "EVAL_HOLDOUT_INCIDENT_APPROVAL_INVALID",
            _validation_summary(exc),
        ) from exc
    if approval.approval_kind not in {"incident_lock", "incident_retry"}:
        raise ContractError(
            "EVAL_HOLDOUT_INCIDENT_APPROVAL_INVALID",
            "Incident locking requires an incident_lock or incident_retry QA approval",
        )
    return approval


def _registry_path(value: Path | str) -> Path:
    path = Path(value).absolute()
    if not path.name:
        raise ContractError("EVAL_HOLDOUT_REGISTRY_PATH_INVALID", "Registry path must name a database file")
    parent = path.parent
    try:
        parent_stat = parent.lstat()
    except OSError as exc:
        raise ContractError(
            "EVAL_HOLDOUT_REGISTRY_PARENT_INVALID",
            f"Registry parent must already exist: {exc}",
            path=str(parent),
        ) from exc
    if not stat.S_ISDIR(parent_stat.st_mode) or stat.S_ISLNK(parent_stat.st_mode):
        raise ContractError(
            "EVAL_HOLDOUT_REGISTRY_PARENT_INVALID",
            "Registry parent must be a real directory, not a symlink",
            path=str(parent),
        )
    try:
        if parent.resolve(strict=True) != parent:
            raise ContractError(
                "EVAL_HOLDOUT_REGISTRY_PARENT_INVALID",
                "Registry parent and its ancestors may not traverse symbolic links",
                path=str(parent),
            )
    except OSError as exc:
        raise ContractError(
            "EVAL_HOLDOUT_REGISTRY_PARENT_INVALID",
            f"Cannot resolve registry parent: {exc}",
            path=str(parent),
        ) from exc
    if hasattr(os, "geteuid") and parent_stat.st_uid != os.geteuid():
        raise ContractError(
            "EVAL_HOLDOUT_REGISTRY_PARENT_UNTRUSTED",
            "Registry parent must be owned by the current OS user",
            path=str(parent),
        )
    if stat.S_IMODE(parent_stat.st_mode) & 0o077:
        raise ContractError(
            "EVAL_HOLDOUT_REGISTRY_PARENT_UNTRUSTED",
            "Registry parent must have owner-only permissions (0700 or stricter)",
            path=str(parent),
        )
    return path


def _inspect_registry_file(path: Path) -> os.stat_result:
    try:
        path_stat = path.lstat()
    except OSError as exc:
        raise ContractError(
            "EVAL_HOLDOUT_REGISTRY_PATH_INVALID",
            f"Cannot inspect registry database: {exc}",
            path=str(path),
        ) from exc
    if not stat.S_ISREG(path_stat.st_mode) or stat.S_ISLNK(path_stat.st_mode):
        raise ContractError(
            "EVAL_HOLDOUT_REGISTRY_PATH_INVALID",
            "Registry database must be a regular file and may not be a symlink",
            path=str(path),
        )
    if hasattr(os, "geteuid") and path_stat.st_uid != os.geteuid():
        raise ContractError(
            "EVAL_HOLDOUT_REGISTRY_PATH_INVALID",
            "Registry database must be owned by the current OS user",
            path=str(path),
        )
    if stat.S_IMODE(path_stat.st_mode) & 0o077:
        raise ContractError(
            "EVAL_HOLDOUT_REGISTRY_PATH_INVALID",
            "Registry database must have owner-only permissions",
            path=str(path),
        )
    return path_stat


def _prepare_registry_file(
    value: Path | str,
    *,
    create_if_missing: bool,
) -> tuple[Path, tuple[int, int]]:
    path = _registry_path(value)
    descriptor: int | None = None
    if create_if_missing:
        flags = (
            os.O_RDWR
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        try:
            descriptor = os.open(path, flags, 0o600)
            opened_stat = os.fstat(descriptor)
            if not stat.S_ISREG(opened_stat.st_mode):
                raise ContractError(
                    "EVAL_HOLDOUT_REGISTRY_PATH_INVALID",
                    "New registry path is not a regular file",
                    path=str(path),
                )
            os.fsync(descriptor)
        except FileExistsError:
            pass
        except ContractError:
            raise
        except OSError as exc:
            raise ContractError(
                "EVAL_HOLDOUT_REGISTRY_PATH_INVALID",
                f"Cannot securely create registry database: {exc}",
                path=str(path),
            ) from exc
        finally:
            if descriptor is not None:
                os.close(descriptor)
    elif not os.path.lexists(path):
        raise ContractError(
            "EVAL_HOLDOUT_REGISTRY_NOT_FOUND",
            "Holdout registry does not exist; only reserve_holdout_attempt may create it",
            path=str(path),
        )
    # Always repeat the directory fsync, including for a pre-existing empty
    # inode left by an earlier failed initialization.  A persistence receipt is
    # therefore never returned after silently skipping a failed parent fsync.
    try:
        _fsync_parent(path)
    except OSError as exc:
        raise ExecutionError(
            "EVAL_HOLDOUT_REGISTRY_PERSISTENCE_FAILED",
            f"Cannot durably persist the registry directory entry: {exc}",
            path=str(path),
        ) from exc
    path_stat = _inspect_registry_file(path)
    return path, (path_stat.st_dev, path_stat.st_ino)


def _fsync_parent(path: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0)
    descriptor = os.open(path.parent, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _enable_wal(connection: sqlite3.Connection) -> None:
    """Converge concurrent first-openers on WAL without leaking transient BUSY."""

    deadline = time.monotonic() + 30.0
    delay = 0.005
    while True:
        try:
            row = connection.execute("PRAGMA journal_mode = WAL").fetchone()
            if row is None or str(row[0]).casefold() != "wal":
                raise sqlite3.OperationalError("registry did not enter WAL journal mode")
            return
        except sqlite3.OperationalError as exc:
            locked = "locked" in str(exc).casefold() or "busy" in str(exc).casefold()
            if not locked or time.monotonic() >= deadline:
                raise
            time.sleep(delay)
            delay = min(delay * 2.0, 0.1)


def _initialize_schema(connection: sqlite3.Connection) -> None:
    statements = (
        """
        CREATE TABLE IF NOT EXISTS registry_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS holdout_attempts (
            attempt_id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL UNIQUE,
            dataset_manifest_sha256 TEXT NOT NULL,
            split TEXT NOT NULL CHECK(split IN ('gate_holdout', 'final_holdout')),
            policy_generation INTEGER NOT NULL CHECK(policy_generation >= 0),
            consumption_key_sha256 TEXT NOT NULL UNIQUE,
            state TEXT NOT NULL CHECK(state IN ('reserved', 'exposure_committed', 'consumed', 'incident_review')),
            model_artifact_sha256 TEXT NOT NULL,
            formal_capability_id TEXT NOT NULL UNIQUE,
            formal_capability_digest TEXT NOT NULL,
            qa_approval_id TEXT NOT NULL UNIQUE,
            qa_approval_digest TEXT NOT NULL,
            qa_approval_kind TEXT NOT NULL CHECK(qa_approval_kind IN ('initial_release', 'incident_retry')),
            qa_approval_reason TEXT NOT NULL,
            qa_approval_actor TEXT NOT NULL,
            predecessor_attempt_id TEXT REFERENCES holdout_attempts(attempt_id),
            reserved_at TEXT NOT NULL,
            exposure_committed_at TEXT,
            consumed_at TEXT,
            result_sha256 TEXT,
            result_commitment_profile TEXT CHECK(
                result_commitment_profile IS NULL OR
                result_commitment_profile = 'evaluation.controlled-run-core-member-set.v0'
            ),
            incident_review_at TEXT,
            UNIQUE(dataset_manifest_sha256, split, policy_generation)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS holdout_events (
            event_id INTEGER PRIMARY KEY AUTOINCREMENT,
            attempt_id TEXT NOT NULL REFERENCES holdout_attempts(attempt_id),
            event_type TEXT NOT NULL CHECK(event_type IN ('reserved', 'exposure_committed', 'consumed', 'incident_review')),
            event_at TEXT NOT NULL,
            details_json TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS incident_authorizations (
            approval_id TEXT PRIMARY KEY,
            approval_digest TEXT NOT NULL,
            dataset_manifest_sha256 TEXT NOT NULL,
            split TEXT NOT NULL CHECK(split = 'gate_holdout'),
            policy_generation INTEGER NOT NULL CHECK(policy_generation > 0),
            predecessor_attempt_id TEXT NOT NULL UNIQUE REFERENCES holdout_attempts(attempt_id),
            reason TEXT NOT NULL,
            actor TEXT NOT NULL,
            authorized_at TEXT NOT NULL,
            used_attempt_id TEXT UNIQUE REFERENCES holdout_attempts(attempt_id),
            UNIQUE(dataset_manifest_sha256, split, policy_generation)
        )
        """,
    )
    for statement in statements:
        connection.execute(statement)
    connection.execute(
        "INSERT OR IGNORE INTO registry_meta(key, value) VALUES (?, ?)",
        ("schema_version", REGISTRY_SCHEMA_VERSION),
    )
    connection.execute(
        "INSERT OR IGNORE INTO registry_meta(key, value) VALUES (?, ?)",
        ("registry_instance_id", f"registry-{uuid.uuid4().hex}"),
    )
    row = connection.execute(
        "SELECT value FROM registry_meta WHERE key = 'schema_version'"
    ).fetchone()
    if row is None or row[0] != REGISTRY_SCHEMA_VERSION:
        raise IntegrityError(
            "EVAL_HOLDOUT_REGISTRY_VERSION_MISMATCH",
            "Registry schema version is not supported",
        )


def _registry_instance_id(connection: sqlite3.Connection) -> str:
    row = connection.execute(
        "SELECT value FROM registry_meta WHERE key = 'registry_instance_id'"
    ).fetchone()
    if row is None or re.fullmatch(ID_PATTERN, str(row[0])) is None:
        raise IntegrityError(
            "EVAL_HOLDOUT_REGISTRY_INSTANCE_INVALID",
            "Registry instance identity is missing or invalid",
        )
    return str(row[0])


@contextmanager
def _connect(
    value: Path | str,
    *,
    create_if_missing: bool = False,
) -> Iterator[sqlite3.Connection]:
    path, expected_identity = _prepare_registry_file(value, create_if_missing=create_if_missing)
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(
            str(path),
            timeout=30.0,
            isolation_level=None,
        )
        current_stat = _inspect_registry_file(path)
        current_identity = (current_stat.st_dev, current_stat.st_ino)
        if current_identity != expected_identity:
            raise ContractError(
                "EVAL_HOLDOUT_REGISTRY_IDENTITY_CHANGED",
                "Registry path changed identity while SQLite was opening it",
                path=str(path),
            )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 30000")
        connection.execute("PRAGMA foreign_keys = ON")
        _enable_wal(connection)
        connection.execute("PRAGMA synchronous = FULL")
        try:
            connection.execute("PRAGMA fullfsync = ON")
        except sqlite3.DatabaseError:
            pass
        foreign_keys = connection.execute("PRAGMA foreign_keys").fetchone()
        synchronous = connection.execute("PRAGMA synchronous").fetchone()
        busy_timeout = connection.execute("PRAGMA busy_timeout").fetchone()
        if (
            foreign_keys is None
            or foreign_keys[0] != 1
            or synchronous is None
            or synchronous[0] != 2
            or busy_timeout is None
            or busy_timeout[0] < 30000
        ):
            raise ExecutionError(
                "EVAL_HOLDOUT_REGISTRY_DURABILITY_UNAVAILABLE",
                "SQLite did not retain required foreign-key, FULL synchronous, and busy-timeout settings",
                path=str(path),
            )
        yield connection
    except EvaluationError:
        raise
    except sqlite3.DatabaseError as exc:
        raise ExecutionError(
            "EVAL_HOLDOUT_REGISTRY_PERSISTENCE_FAILED",
            f"Holdout registry persistence failed: {exc}",
            path=str(path),
        ) from exc
    finally:
        if connection is not None:
            connection.close()


def _canonical_details(value: Mapping[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _consumption_key_digest(dataset_manifest_sha256: str, split: str, policy_generation: int) -> str:
    payload = _canonical_details(
        {
            "dataset_manifest_sha256": dataset_manifest_sha256,
            "policy_generation": policy_generation,
            "split": split,
        }
    ).encode("utf-8")
    return hashlib.sha256(CONSUMPTION_KEY_DOMAIN + payload).hexdigest()


def _rollback(connection: sqlite3.Connection) -> None:
    try:
        connection.execute("ROLLBACK")
    except sqlite3.DatabaseError:
        pass


def mark_holdout_incident(
    registry_path: Path | str,
    *,
    attempt_id: str,
    incident_approval: QAHoldoutApproval | Mapping[str, Any],
) -> dict[str, Any]:
    """Irreversibly lock a real execution incident; optionally authorize a gate retry."""

    approval = _parse_incident_approval(incident_approval)
    if approval.predecessor_attempt_id != attempt_id:
        raise ContractError(
            "EVAL_HOLDOUT_PREDECESSOR_MISMATCH",
            "Incident approval predecessor must match the attempt being locked",
        )
    now = _utc_now()
    registry_instance_id: str | None = None
    with _connect(registry_path) as connection:
        try:
            connection.execute("BEGIN IMMEDIATE")
            _initialize_schema(connection)
            registry_instance_id = _registry_instance_id(connection)
            row = connection.execute(
                "SELECT * FROM holdout_attempts WHERE attempt_id = ?",
                (attempt_id,),
            ).fetchone()
            if row is None:
                raise ContractError("EVAL_HOLDOUT_ATTEMPT_NOT_FOUND", "Holdout attempt does not exist")
            if row["state"] not in {"reserved", "exposure_committed"}:
                raise IntegrityError(
                    "EVAL_HOLDOUT_INCIDENT_STATE_INVALID",
                    "Only a reserved or exposure_committed execution accident may enter incident review",
                    details={"state": row["state"]},
                )
            is_final = row["split"] == "final_holdout"
            if is_final and approval.approval_kind != "incident_lock":
                raise IntegrityError(
                    "EVAL_FINAL_RERUN_FORBIDDEN",
                    "final_holdout may be incident-locked for audit but can never authorize a rerun",
                )
            if row["state"] == "exposure_committed" and approval.approval_kind == "incident_retry":
                raise IntegrityError(
                    "EVAL_HOLDOUT_EXPOSED_RERUN_FORBIDDEN",
                    "A gate generation cannot be retried after holdout exposure was durably committed",
                )
            if not is_final and approval.approval_kind not in {"incident_lock", "incident_retry"}:
                raise ContractError(
                    "EVAL_HOLDOUT_INCIDENT_APPROVAL_INVALID",
                    "Gate incident approval kind is invalid",
                )
            expected_generation = row["policy_generation"] + (1 if approval.approval_kind == "incident_retry" else 0)
            expected = (
                row["dataset_manifest_sha256"],
                row["split"],
                expected_generation,
            )
            actual = (
                approval.dataset_manifest_sha256,
                approval.split,
                approval.policy_generation,
            )
            if actual != expected:
                raise IntegrityError(
                    "EVAL_HOLDOUT_INCIDENT_APPROVAL_MISMATCH",
                    "Incident approval must bind this lock or the next gate generation",
                    details={"expected_generation": expected[2]},
                )
            if approval.approval_kind == "incident_retry":
                connection.execute(
                    """
                    INSERT INTO incident_authorizations(
                        approval_id, approval_digest, dataset_manifest_sha256, split,
                        policy_generation, predecessor_attempt_id, reason, actor, authorized_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        approval.approval_id,
                        approval.approval_digest,
                        approval.dataset_manifest_sha256,
                        approval.split,
                        approval.policy_generation,
                        attempt_id,
                        approval.reason,
                        approval.actor,
                        now,
                    ),
                )
            connection.execute(
                "UPDATE holdout_attempts SET state = 'incident_review', incident_review_at = ? WHERE attempt_id = ?",
                (now, attempt_id),
            )
            connection.execute(
                """
                INSERT INTO holdout_events(attempt_id, event_type, event_at, details_json)
                VALUES (?, 'incident_review', ?, ?)
                """,
                (
                    attempt_id,
                    now,
                    _canonical_details(
                        {
                            "approval_id": approval.approval_id,
                            "approval_digest": approval.approval_digest,
                            "actor": approval.actor,
                            "reason": approval.reason,
                            "approval_kind": approval.approval_kind,
                            "authorized_generation": (
                                approval.policy_generation if approval.approval_kind == "incident_retry" else None
                            ),
                        }
                    ),
                ),
            )
            connection.execute("COMMIT")
        except EvaluationError:
            _rollback(connection)
            raise
        except sqlite3.IntegrityError as exc:
            _rollback(connection)
            raise IntegrityError(
                "EVAL_HOLDOUT_INCIDENT_APPROVAL_REUSED",
                "Incident approval or predecessor has already authorized a retry",
            ) from exc
        except BaseException:
            _rollback(connection)
            raise
    return {
        "schema_version": "evaluation.holdout-incident-lock.v0",
        "ok": True,
        "registry_instance_id": registry_instance_id,
        "attempt_id": attempt_id,
        "state": "incident_review",
        "retry_authorized": approval.approval_kind == "incident_retry",
        "authorized_policy_generation": (
            approval.policy_generation if approval.approval_kind == "incident_retry" else None
        ),
        "authorization_authenticity": "self_asserted_unsigned",
        "formal_execution_completed": False,
        "compliance_claim_eligible": False,
    }


def reserve_holdout_attempt(
    registry_path: Path | str,
    request: HoldoutReservationRequest | Mapping[str, Any],
) -> dict[str, Any]:
    """Persist a fail-closed one-shot reservation before returning to a caller.

    Model identity is retained for audit only.  The UNIQUE consumption key is
    exactly frozen dataset manifest digest, holdout split, and policy generation.
    Reservations have no lease or automatic recovery; a crashed caller leaves a
    durable ``reserved`` record that requires an explicit QA incident retry.
    """

    parsed = _parse_request(request)
    now = _utc_now()
    consumption_key_sha256 = _consumption_key_digest(
        parsed.dataset_manifest_sha256,
        parsed.split,
        parsed.policy_generation,
    )
    registry_instance_id: str | None = None
    with _connect(registry_path, create_if_missing=True) as connection:
        try:
            connection.execute("BEGIN IMMEDIATE")
            _initialize_schema(connection)
            registry_instance_id = _registry_instance_id(connection)

            duplicate = connection.execute(
                """
                SELECT attempt_id, state, model_artifact_sha256
                FROM holdout_attempts
                WHERE dataset_manifest_sha256 = ? AND split = ? AND policy_generation = ?
                """,
                (parsed.dataset_manifest_sha256, parsed.split, parsed.policy_generation),
            ).fetchone()
            if duplicate is not None:
                raise IntegrityError(
                    "EVAL_HOLDOUT_ALREADY_CLAIMED",
                    "This holdout consumption key is already reserved or consumed",
                    details={
                        "attempt_id": duplicate["attempt_id"],
                        "state": duplicate["state"],
                        "model_identity_ignored_for_key": True,
                    },
                )

            latest = connection.execute(
                """
                SELECT attempt_id, policy_generation, state
                FROM holdout_attempts
                WHERE dataset_manifest_sha256 = ? AND split = ?
                ORDER BY policy_generation DESC LIMIT 1
                """,
                (parsed.dataset_manifest_sha256, parsed.split),
            ).fetchone()

            approval = parsed.qa_approval
            if latest is None:
                if approval.approval_kind != "initial_release":
                    raise ContractError(
                        "EVAL_HOLDOUT_INITIAL_APPROVAL_REQUIRED",
                        "The first generation requires an initial_release QA approval",
                    )
            else:
                if parsed.split == "final_holdout":
                    raise IntegrityError(
                        "EVAL_FINAL_RERUN_FORBIDDEN",
                        "A final_holdout dataset digest may never receive another generation",
                    )
                if approval.approval_kind != "incident_retry":
                    raise IntegrityError(
                        "EVAL_HOLDOUT_INCIDENT_APPROVAL_REQUIRED",
                        "Only an explicit QA incident_retry approval may create another generation",
                        details={"latest_attempt_id": latest["attempt_id"]},
                    )
                if latest["state"] != "incident_review":
                    raise IntegrityError(
                        "EVAL_HOLDOUT_INCIDENT_LOCK_REQUIRED",
                        "The latest gate attempt must be explicitly locked for incident review before retry",
                        details={"latest_attempt_id": latest["attempt_id"], "state": latest["state"]},
                    )
                if approval.predecessor_attempt_id != latest["attempt_id"]:
                    raise IntegrityError(
                        "EVAL_HOLDOUT_PREDECESSOR_MISMATCH",
                        "Incident approval must bind the latest historical attempt",
                        details={"latest_attempt_id": latest["attempt_id"]},
                    )
                if parsed.policy_generation != latest["policy_generation"] + 1:
                    raise IntegrityError(
                        "EVAL_HOLDOUT_GENERATION_INVALID",
                        "Incident retry generation must immediately follow the latest generation",
                        details={"expected": latest["policy_generation"] + 1},
                    )
                authorization = connection.execute(
                    """
                    SELECT * FROM incident_authorizations
                    WHERE approval_id = ? AND predecessor_attempt_id = ?
                    """,
                    (approval.approval_id, latest["attempt_id"]),
                ).fetchone()
                if authorization is None or authorization["used_attempt_id"] is not None:
                    raise IntegrityError(
                        "EVAL_HOLDOUT_INCIDENT_APPROVAL_NOT_PERSISTED",
                        "Incident retry approval must be explicitly persisted before reserving a new generation",
                    )
                authorization_values = (
                    approval.approval_digest,
                    approval.dataset_manifest_sha256,
                    approval.split,
                    approval.policy_generation,
                    approval.reason,
                    approval.actor,
                )
                persisted_values = (
                    authorization["approval_digest"],
                    authorization["dataset_manifest_sha256"],
                    authorization["split"],
                    authorization["policy_generation"],
                    authorization["reason"],
                    authorization["actor"],
                )
                if authorization_values != persisted_values:
                    raise IntegrityError(
                        "EVAL_HOLDOUT_INCIDENT_APPROVAL_MISMATCH",
                        "Retry request does not exactly match the persisted incident approval",
                    )

            connection.execute(
                """
                INSERT INTO holdout_attempts(
                    attempt_id, run_id, dataset_manifest_sha256, split, policy_generation,
                    consumption_key_sha256, state, model_artifact_sha256, formal_capability_id,
                    formal_capability_digest, qa_approval_id, qa_approval_digest,
                    qa_approval_kind, qa_approval_reason, qa_approval_actor,
                    predecessor_attempt_id, reserved_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'reserved', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    parsed.attempt_id,
                    parsed.run_id,
                    parsed.dataset_manifest_sha256,
                    parsed.split,
                    parsed.policy_generation,
                    consumption_key_sha256,
                    parsed.model_artifact_sha256,
                    parsed.formal_capability.capability_id,
                    parsed.formal_capability.capability_digest,
                    approval.approval_id,
                    approval.approval_digest,
                    approval.approval_kind,
                    approval.reason,
                    approval.actor,
                    approval.predecessor_attempt_id,
                    now,
                ),
            )
            if approval.approval_kind == "incident_retry":
                connection.execute(
                    "UPDATE incident_authorizations SET used_attempt_id = ? WHERE approval_id = ?",
                    (parsed.attempt_id, approval.approval_id),
                )
            connection.execute(
                """
                INSERT INTO holdout_events(attempt_id, event_type, event_at, details_json)
                VALUES (?, 'reserved', ?, ?)
                """,
                (
                    parsed.attempt_id,
                    now,
                    _canonical_details(
                        {
                            "formal_capability_id": parsed.formal_capability.capability_id,
                            "qa_approval_id": approval.approval_id,
                            "model_identity_part_of_consumption_key": False,
                        }
                    ),
                ),
            )
            connection.execute("COMMIT")
        except EvaluationError:
            _rollback(connection)
            raise
        except sqlite3.IntegrityError as exc:
            _rollback(connection)
            raise IntegrityError(
                "EVAL_HOLDOUT_AUDIT_ID_REUSED",
                "Attempt, run, capability, or QA approval identity has already been used",
            ) from exc
        except BaseException:
            _rollback(connection)
            raise

    receipt = HoldoutReservationReceipt(
        schema_version="evaluation.holdout-reservation-receipt.v0",
        ok=True,
        registry_instance_id=registry_instance_id,
        attempt_id=parsed.attempt_id,
        run_id=parsed.run_id,
        consumption_key={
            "dataset_manifest_sha256": parsed.dataset_manifest_sha256,
            "split": parsed.split,
            "policy_generation": parsed.policy_generation,
            "key_sha256": consumption_key_sha256,
        },
        state="reserved",
        reservation_persisted=True,
        model_artifact_sha256=parsed.model_artifact_sha256,
        model_identity_part_of_consumption_key=False,
        authorization_authenticity="self_asserted_unsigned",
        formal_execution_completed=False,
        compliance_claim_eligible=False,
    )
    return receipt.model_dump(mode="json")


def _validate_transition_inputs(attempt_id: str, actor: str, result_sha256: str | None = None) -> None:
    if not isinstance(actor, str) or not actor.strip() or len(actor) > 200:
        raise ContractError("EVAL_HOLDOUT_ACTOR_INVALID", "Transition actor is required")
    if not isinstance(attempt_id, str) or re.fullmatch(ID_PATTERN, attempt_id) is None:
        raise ContractError("EVAL_HOLDOUT_TRANSITION_INVALID", "Invalid attempt identity")
    if result_sha256 is not None and (
        not isinstance(result_sha256, str) or re.fullmatch(SHA256_PATTERN, result_sha256) is None
    ):
        raise ContractError("EVAL_HOLDOUT_TRANSITION_INVALID", "Invalid result identity")


def commit_holdout_exposure(
    registry_path: Path | str,
    *,
    attempt_id: str,
    actor: str,
) -> dict[str, Any]:
    """Durably burn the attempt before any broker exposes holdout inputs or labels."""

    _validate_transition_inputs(attempt_id, actor)
    now = _utc_now()
    registry_instance_id: str | None = None
    with _connect(registry_path) as connection:
        try:
            connection.execute("BEGIN IMMEDIATE")
            _initialize_schema(connection)
            registry_instance_id = _registry_instance_id(connection)
            row = connection.execute(
                "SELECT state FROM holdout_attempts WHERE attempt_id = ?",
                (attempt_id,),
            ).fetchone()
            if row is None:
                raise ContractError("EVAL_HOLDOUT_ATTEMPT_NOT_FOUND", "Holdout attempt does not exist")
            if row["state"] != "reserved":
                raise IntegrityError(
                    "EVAL_HOLDOUT_STATE_INVALID",
                    "Only a reserved attempt can commit holdout exposure",
                    details={"state": row["state"]},
                )
            connection.execute(
                """
                UPDATE holdout_attempts
                SET state = 'exposure_committed', exposure_committed_at = ?
                WHERE attempt_id = ? AND state = 'reserved'
                """,
                (now, attempt_id),
            )
            connection.execute(
                """
                INSERT INTO holdout_events(attempt_id, event_type, event_at, details_json)
                VALUES (?, 'exposure_committed', ?, ?)
                """,
                (attempt_id, now, _canonical_details({"actor": actor})),
            )
            connection.execute("COMMIT")
        except EvaluationError:
            _rollback(connection)
            raise
        except BaseException:
            _rollback(connection)
            raise
    return {
        "schema_version": "evaluation.holdout-exposure-commit.v0",
        "ok": True,
        "registry_instance_id": registry_instance_id,
        "attempt_id": attempt_id,
        "state": "exposure_committed",
        "exposure_commit_persisted": True,
        "local_exposure_state_persisted": True,
        "authorization_authenticity": "self_asserted_unsigned",
        "trusted_broker_release_authorized": False,
        "result_finalized": False,
        "formal_execution_completed": False,
        "compliance_claim_eligible": False,
    }


def finalize_holdout_attempt(
    registry_path: Path | str,
    *,
    attempt_id: str,
    result_sha256: str,
    actor: str,
) -> dict[str, Any]:
    _validate_transition_inputs(attempt_id, actor, result_sha256)
    now = _utc_now()
    with _connect(registry_path) as connection:
        try:
            connection.execute("BEGIN IMMEDIATE")
            _initialize_schema(connection)
            row = connection.execute(
                "SELECT state FROM holdout_attempts WHERE attempt_id = ?",
                (attempt_id,),
            ).fetchone()
            if row is None:
                raise ContractError("EVAL_HOLDOUT_ATTEMPT_NOT_FOUND", "Holdout attempt does not exist")
            if row["state"] != "exposure_committed":
                raise IntegrityError(
                    "EVAL_HOLDOUT_STATE_INVALID",
                    "Only an exposure_committed attempt can be finalized",
                    details={"state": row["state"]},
                )
            connection.execute(
                """
                UPDATE holdout_attempts
                SET state = 'consumed', consumed_at = ?, result_sha256 = ?,
                    result_commitment_profile = ?
                WHERE attempt_id = ? AND state = 'exposure_committed'
                """,
                (now, result_sha256, RESULT_COMMITMENT_PROFILE, attempt_id),
            )
            connection.execute(
                """
                INSERT INTO holdout_events(attempt_id, event_type, event_at, details_json)
                VALUES (?, 'consumed', ?, ?)
                """,
                (
                    attempt_id,
                    now,
                    _canonical_details(
                        {
                            "actor": actor,
                            "result_sha256": result_sha256,
                            "result_commitment_profile": RESULT_COMMITMENT_PROFILE,
                        }
                    ),
                ),
            )
            connection.execute("COMMIT")
        except EvaluationError:
            _rollback(connection)
            raise
        except BaseException:
            _rollback(connection)
            raise
    return {
        "schema_version": "evaluation.holdout-finalization-receipt.v0",
        "ok": True,
        "attempt": get_holdout_attempt(registry_path, attempt_id=attempt_id),
        "formal_execution_completed": False,
        "compliance_claim_eligible": False,
    }


def _record_from_row(row: sqlite3.Row, *, registry_instance_id: str) -> dict[str, Any]:
    record = HoldoutAttemptRecord(
        schema_version="evaluation.holdout-attempt.v0",
        registry_instance_id=registry_instance_id,
        attempt_id=row["attempt_id"],
        run_id=row["run_id"],
        consumption_key={
            "dataset_manifest_sha256": row["dataset_manifest_sha256"],
            "split": row["split"],
            "policy_generation": row["policy_generation"],
            "key_sha256": row["consumption_key_sha256"],
        },
        state=row["state"],
        model_artifact_sha256=row["model_artifact_sha256"],
        formal_capability_id=row["formal_capability_id"],
        formal_capability_digest=row["formal_capability_digest"],
        qa_approval_id=row["qa_approval_id"],
        qa_approval_digest=row["qa_approval_digest"],
        qa_approval_kind=row["qa_approval_kind"],
        qa_approval_reason=row["qa_approval_reason"],
        qa_approval_actor=row["qa_approval_actor"],
        predecessor_attempt_id=row["predecessor_attempt_id"],
        reserved_at=row["reserved_at"],
        exposure_committed_at=row["exposure_committed_at"],
        consumed_at=row["consumed_at"],
        result_sha256=row["result_sha256"],
        result_commitment_profile=row["result_commitment_profile"],
        incident_review_at=row["incident_review_at"],
        authorization_authenticity="self_asserted_unsigned",
        formal_execution_completed=False,
        compliance_claim_eligible=False,
    )
    return record.model_dump(mode="json")


def get_holdout_attempt(registry_path: Path | str, *, attempt_id: str) -> dict[str, Any]:
    registry_instance_id: str | None = None
    with _connect(registry_path) as connection:
        try:
            connection.execute("BEGIN IMMEDIATE")
            _initialize_schema(connection)
            registry_instance_id = _registry_instance_id(connection)
            row = connection.execute(
                "SELECT * FROM holdout_attempts WHERE attempt_id = ?",
                (attempt_id,),
            ).fetchone()
            connection.execute("COMMIT")
        except BaseException:
            _rollback(connection)
            raise
    if row is None:
        raise ContractError("EVAL_HOLDOUT_ATTEMPT_NOT_FOUND", "Holdout attempt does not exist")
    return _record_from_row(row, registry_instance_id=registry_instance_id)


def list_holdout_attempts(registry_path: Path | str) -> list[dict[str, Any]]:
    registry_instance_id: str | None = None
    with _connect(registry_path) as connection:
        try:
            connection.execute("BEGIN IMMEDIATE")
            _initialize_schema(connection)
            registry_instance_id = _registry_instance_id(connection)
            rows = connection.execute(
                "SELECT * FROM holdout_attempts ORDER BY dataset_manifest_sha256, split, policy_generation"
            ).fetchall()
            connection.execute("COMMIT")
        except BaseException:
            _rollback(connection)
            raise
    return [_record_from_row(row, registry_instance_id=registry_instance_id) for row in rows]


__all__ = [
    "commit_holdout_exposure",
    "finalize_holdout_attempt",
    "get_holdout_attempt",
    "list_holdout_attempts",
    "mark_holdout_incident",
    "reserve_holdout_attempt",
]
