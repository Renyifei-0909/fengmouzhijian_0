from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
import ipaddress
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from time import perf_counter
from typing import Any, Mapping
import uuid

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text, update
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.schema import CreateSchema, DropSchema

from app.config import Settings
from app.database import Database
from app.main import create_app
from app.models import (
    DesignBaseline,
    EvidenceAsset,
    VerificationAttempt,
    VerificationAttemptOutcome,
    VerificationJob,
    VerificationJobLease,
)
from app.schema import (
    expected_schema_heads,
    upgrade_database_schema,
    verify_database_schema,
)
from app.services import analysis
from app.services.analyzers import validate_analyzer_result
from app.services.analyzers.stub import StubAnalyzer


DATABASE_URL_ENV = "FENGMOU_POSTGRES_ACCEPTANCE_URL"
ACCEPTANCE_DATABASE_NAME = "fengmou_acceptance"
SCHEMA_PREFIX = "fengmou_acceptance_"
SCHEMA_PATTERN = re.compile(r"^fengmou_acceptance_[0-9a-f]{24}$")
MIN_POSTGRES_MAJOR = 15
MAX_POSTGRES_MAJOR = 18
APPEND_ONLY_SQLSTATE = "23000"
PNG_PREFIX = b"\x89PNG\r\n\x1a\n"
BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_ROOT.parent
COMPOSE_ACCEPTANCE_PATH = PROJECT_ROOT / "compose.postgres-acceptance.yaml"
INIT_SQL_PATH = BACKEND_ROOT / "tests" / "postgres" / "init.sql"

# Prometheus 0.0.4 families that the live acceptance harness requires.
# Order is deliberately not authoritative; presence of TYPE/HELP is.
PROMETHEUS_REQUIRED_FAMILIES = (
    "fengmou_verification_operations_info",
    "fengmou_verification_operations_snapshot_timestamp_seconds",
    "fengmou_verification_operations_collection_duration_seconds",
    "fengmou_verification_operations_status",
    "fengmou_verification_jobs",
    "fengmou_verification_dispatch_leases",
    "fengmou_verification_queue_unclaimed_jobs",
    "fengmou_verification_queue_over_warning_jobs",
    "fengmou_verification_dead_letter_jobs",
    "fengmou_verification_attempts",
    "fengmou_verification_attempt_outcomes",
    "fengmou_verification_recent_lease_instability",
    "fengmou_verification_integrity_issues",
    "fengmou_verification_alerts",
    "fengmou_verification_queue_warning_threshold_seconds",
    "fengmou_verification_observability_window_seconds",
    "fengmou_verification_lease_duration_seconds",
    "fengmou_verification_heartbeat_interval_seconds",
)

# Label keys that must never appear on acceptance metrics samples.
PROMETHEUS_FORBIDDEN_LABEL_KEYS = (
    "job_id",
    "worker_id",
    "attempt_id",
    "outcome_id",
    "schema",
    "project_id",
    "api_key",
    "password",
)

FORBIDDEN_SOURCE_SQL_PATTERNS = (
    re.compile(r"\bDROP\s+DATABASE\b", re.IGNORECASE),
    re.compile(r"\bDROP\s+SCHEMA\s+public\b", re.IGNORECASE),
    re.compile(r"\bDROP\s+SCHEMA\s+IF\s+EXISTS\s+public\b", re.IGNORECASE),
)


class AcceptanceError(RuntimeError):
    """A PostgreSQL acceptance stage failed without exposing credentials."""


class AcceptanceRefusal(AcceptanceError):
    """The requested target is outside the deliberately narrow safety boundary."""


@dataclass(frozen=True, slots=True)
class TargetIdentity:
    url: URL
    host: str
    port: int
    database: str

    def public_dict(self) -> dict[str, object]:
        return {
            "host": self.host,
            "port": self.port,
            "database": self.database,
            "driver": self.url.drivername,
        }


@dataclass(frozen=True, slots=True)
class ServerIdentity:
    database: str
    role: str
    version_num: int
    version: str
    server_encoding: str
    default_transaction_isolation: str
    in_recovery: bool
    role_flags: Mapping[str, bool]

    @property
    def major(self) -> int:
        return self.version_num // 10000

    def public_dict(self) -> dict[str, object]:
        return {
            "database": self.database,
            "role": self.role,
            "version_num": self.version_num,
            "version": self.version,
            "major": self.major,
            "server_encoding": self.server_encoding,
            "default_transaction_isolation": self.default_transaction_isolation,
            "in_recovery": self.in_recovery,
            "role_flags": dict(self.role_flags),
        }


def _is_loopback_host(host: str) -> bool:
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def validate_target_url(raw_url: str | None) -> TargetIdentity:
    """Accept only an explicit, password-authenticated, loopback test database."""

    if raw_url is None or not raw_url.strip():
        raise AcceptanceRefusal(
            f"{DATABASE_URL_ENV} is required; the acceptance command never falls back "
            "to the application database"
        )
    try:
        url = make_url(raw_url.strip())
    except Exception as exc:
        raise AcceptanceRefusal(
            f"{DATABASE_URL_ENV} is not a valid SQLAlchemy database URL"
        ) from exc
    if url.drivername != "postgresql+psycopg":
        raise AcceptanceRefusal(
            f"{DATABASE_URL_ENV} must use postgresql+psycopg explicitly"
        )
    if url.database != ACCEPTANCE_DATABASE_NAME:
        raise AcceptanceRefusal(
            f"{DATABASE_URL_ENV} must name the dedicated "
            f"{ACCEPTANCE_DATABASE_NAME!r} database"
        )
    if not url.username or not url.password:
        raise AcceptanceRefusal(
            f"{DATABASE_URL_ENV} must contain a dedicated username and password"
        )
    if not url.host or not _is_loopback_host(url.host):
        raise AcceptanceRefusal(
            f"{DATABASE_URL_ENV} must target localhost or a numeric loopback address"
        )
    if url.query:
        raise AcceptanceRefusal(
            f"{DATABASE_URL_ENV} must not contain query parameters; the harness "
            "adds its own isolated search_path and timeouts"
        )
    port = url.port or 5432
    if not 1 <= port <= 65535:
        raise AcceptanceRefusal(f"{DATABASE_URL_ENV} contains an invalid port")
    return TargetIdentity(
        url=url,
        host=url.host,
        port=port,
        database=url.database,
    )


def validate_run_shape(*, jobs: int, workers: int) -> None:
    if not 2 <= workers <= 16:
        raise AcceptanceRefusal("--workers must be in [2, 16]")
    if not workers <= jobs <= 64:
        raise AcceptanceRefusal("--jobs must be in [workers, 64]")


def _new_schema_name() -> str:
    return f"{SCHEMA_PREFIX}{uuid.uuid4().hex[:24]}"


def _validate_owned_schema_name(schema: str) -> None:
    if SCHEMA_PATTERN.fullmatch(schema) is None:
        raise AcceptanceRefusal(
            "refusing cleanup because the schema name is not an acceptance-run identifier"
        )


def scoped_database_url(target: TargetIdentity, schema: str) -> str:
    _validate_owned_schema_name(schema)
    scoped = target.url.update_query_dict(
        {
            "application_name": "fengmou_postgres_acceptance",
            "connect_timeout": "5",
            "options": (
                f"-csearch_path={schema} "
                "-cstatement_timeout=30000 "
                "-clock_timeout=10000"
            ),
        }
    )
    return scoped.render_as_string(hide_password=False)


def _base_database(target: TargetIdentity) -> Database:
    url = target.url.update_query_dict(
        {
            "application_name": "fengmou_postgres_acceptance_control",
            "connect_timeout": "5",
            "options": "-cstatement_timeout=30000 -clock_timeout=10000",
        }
    )
    return Database(url.render_as_string(hide_password=False))


def inspect_server(database: Database, target: TargetIdentity) -> ServerIdentity:
    query = text(
        """
SELECT
    current_database() AS database_name,
    current_user AS role_name,
    current_setting('server_version_num')::integer AS version_num,
    current_setting('server_version') AS version,
    current_setting('server_encoding') AS server_encoding,
    current_setting('default_transaction_isolation') AS default_transaction_isolation,
    pg_is_in_recovery() AS in_recovery,
    role_row.rolsuper AS role_superuser,
    role_row.rolcreatedb AS role_createdb,
    role_row.rolcreaterole AS role_createrole,
    role_row.rolreplication AS role_replication,
    role_row.rolbypassrls AS role_bypassrls,
    has_database_privilege(current_user, current_database(), 'CONNECT') AS can_connect,
    has_database_privilege(current_user, current_database(), 'CREATE') AS can_create
FROM pg_roles AS role_row
WHERE role_row.rolname = current_user
"""
    )
    try:
        with database.engine.connect() as connection:
            row = connection.execute(query).mappings().one()
    except Exception as exc:
        raise AcceptanceError(
            "could not connect to the dedicated PostgreSQL acceptance database "
            f"({type(exc).__name__})"
        ) from exc
    if row["database_name"] != target.database:
        raise AcceptanceRefusal("connected database identity does not match the accepted target")
    role_flags = {
        "superuser": bool(row["role_superuser"]),
        "createdb": bool(row["role_createdb"]),
        "createrole": bool(row["role_createrole"]),
        "replication": bool(row["role_replication"]),
        "bypassrls": bool(row["role_bypassrls"]),
    }
    if any(role_flags.values()):
        enabled = ", ".join(name for name, enabled in role_flags.items() if enabled)
        raise AcceptanceRefusal(
            "the application acceptance role is over-privileged: " + enabled
        )
    if not bool(row["can_connect"]) or not bool(row["can_create"]):
        raise AcceptanceRefusal(
            "the acceptance role needs CONNECT and CREATE on the dedicated test database"
        )
    identity = ServerIdentity(
        database=str(row["database_name"]),
        role=str(row["role_name"]),
        version_num=int(row["version_num"]),
        version=str(row["version"]),
        server_encoding=str(row["server_encoding"]),
        default_transaction_isolation=str(row["default_transaction_isolation"]),
        in_recovery=bool(row["in_recovery"]),
        role_flags=role_flags,
    )
    if not MIN_POSTGRES_MAJOR <= identity.major <= MAX_POSTGRES_MAJOR:
        raise AcceptanceRefusal(
            "PostgreSQL major version is outside the accepted stable range "
            f"[{MIN_POSTGRES_MAJOR}, {MAX_POSTGRES_MAJOR}]"
        )
    if identity.server_encoding.upper() != "UTF8":
        raise AcceptanceRefusal("the acceptance database must use UTF8 server encoding")
    if identity.default_transaction_isolation.lower() != "read committed":
        raise AcceptanceRefusal(
            "the acceptance database must use READ COMMITTED as its default isolation"
        )
    if identity.in_recovery:
        raise AcceptanceRefusal("the acceptance target is read-only/in recovery")
    return identity


def create_isolated_schema(database: Database, schema: str) -> None:
    _validate_owned_schema_name(schema)
    try:
        with database.engine.begin() as connection:
            exists = bool(
                connection.scalar(
                    text("SELECT to_regnamespace(:schema_name) IS NOT NULL"),
                    {"schema_name": schema},
                )
            )
            if exists:
                raise AcceptanceRefusal(
                    "the randomly generated acceptance schema already exists"
                )
            connection.execute(CreateSchema(schema))
    except AcceptanceError:
        raise
    except Exception as exc:
        raise AcceptanceError(
            f"could not create the isolated acceptance schema ({type(exc).__name__})"
        ) from exc


def drop_isolated_schema(database: Database, schema: str) -> None:
    _validate_owned_schema_name(schema)
    try:
        with database.engine.begin() as connection:
            owner = connection.scalar(
                text(
                    """
SELECT owner_role.rolname
FROM pg_namespace AS namespace_row
JOIN pg_roles AS owner_role ON owner_role.oid = namespace_row.nspowner
WHERE namespace_row.nspname = :schema_name
"""
                ),
                {"schema_name": schema},
            )
            current_role = connection.scalar(text("SELECT current_user"))
            if owner is None:
                raise AcceptanceError(
                    "the acceptance schema disappeared before controlled cleanup"
                )
            if owner != current_role:
                raise AcceptanceRefusal(
                    "refusing cleanup because the acceptance role no longer owns its schema"
                )
            connection.execute(DropSchema(schema, cascade=True))
            remains = bool(
                connection.scalar(
                    text("SELECT to_regnamespace(:schema_name) IS NOT NULL"),
                    {"schema_name": schema},
                )
            )
            if remains:
                raise AcceptanceError("the isolated acceptance schema was not removed")
    except AcceptanceError:
        raise
    except Exception as exc:
        raise AcceptanceError(
            f"could not remove the isolated acceptance schema ({type(exc).__name__})"
        ) from exc


def run_migration_acceptance(scoped_url: str) -> dict[str, object]:
    def upgrade_once(_: int) -> dict[str, object]:
        database = Database(scoped_url)
        try:
            return upgrade_database_schema(database.engine).as_dict()
        finally:
            database.engine.dispose()

    started = perf_counter()
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            concurrent_statuses = list(pool.map(upgrade_once, range(2)))
        database = Database(scoped_url)
        try:
            repeated = upgrade_database_schema(database.engine).as_dict()
            verified = verify_database_schema(database.engine).as_dict()
        finally:
            database.engine.dispose()
    except Exception as exc:
        raise AcceptanceError(
            f"PostgreSQL migration acceptance failed ({type(exc).__name__})"
        ) from exc
    heads = list(expected_schema_heads())
    statuses = [*concurrent_statuses, repeated, verified]
    if heads != ["20260801_0004"]:
        raise AcceptanceError(f"unexpected application migration heads: {heads!r}")
    if not all(
        status["current_heads"] == heads
        and status["at_head"] is True
        and status["drift_free"] is True
        for status in statuses
    ):
        raise AcceptanceError("migration status did not remain at a drift-free head")
    return {
        "concurrent_upgrade_runs": len(concurrent_statuses),
        "idempotent_upgrade_runs": 1,
        "verify_runs": 1,
        "heads": heads,
        "elapsed_ms": round((perf_counter() - started) * 1000, 3),
    }


def _response_json(
    response: Any,
    *,
    expected_status: int,
    stage: str,
) -> dict[str, Any]:
    if response.status_code != expected_status:
        raise AcceptanceError(
            f"{stage} returned HTTP {response.status_code}, expected {expected_status}"
        )
    payload = response.json()
    if not isinstance(payload, dict):
        raise AcceptanceError(f"{stage} did not return a JSON object")
    return payload


def _acceptance_settings(scoped_url: str, storage_root: Path) -> Settings:
    return Settings(
        environment="staging",
        database_url=scoped_url,
        database_schema_mode="verify",
        storage_root=storage_root,
        max_upload_bytes=2 * 1024 * 1024,
        allow_demo_analyzer=False,
        operator_api_key="acceptance-operator-key",
        reviewer_api_key="acceptance-reviewer-key",
        auditor_api_key="acceptance-auditor-key",
        verification_execution_mode="external",
        verification_lease_seconds=5.0,
        verification_heartbeat_seconds=1.0,
        verification_max_attempts=3,
        verification_worker_poll_seconds=0.05,
        verification_queue_warning_seconds=60.0,
        verification_observability_window_seconds=900,
        cors_origins=("http://testserver",),
    )


def _create_project_and_baseline(
    client: TestClient,
    *,
    schema: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    project = _response_json(
        client.post(
            "/api/v1/projects",
            json={
                "code": f"PG-{schema[-12:].upper()}",
                "name": "PostgreSQL acceptance",
                "location": "isolated acceptance schema",
            },
        ),
        expected_status=201,
        stage="project creation",
    )
    baseline = _response_json(
        client.post(
            f"/api/v1/projects/{project['id']}/baselines",
            json={
                "site_id": "PG-ACCEPTANCE",
                "procedure_code": "WORKER-CONCURRENCY",
                "version": "v1",
                "source_type": "manual",
                "expected": {},
            },
        ),
        expected_status=201,
        stage="baseline creation",
    )
    return project, baseline


def _enqueue_job(
    client: TestClient,
    project: Mapping[str, Any],
    baseline: Mapping[str, Any],
    *,
    index: int,
) -> str:
    image_bytes = PNG_PREFIX + bytes(range(64)) + index.to_bytes(4, "big")
    payload = _response_json(
        client.post(
            "/api/v1/verifications",
            data={
                "project_id": str(project["id"]),
                "baseline_id": str(baseline["id"]),
                "analyzer": "stub",
            },
            files={
                "file": (
                    f"postgres-acceptance-{index}.png",
                    image_bytes,
                    "image/png",
                )
            },
        ),
        expected_status=202,
        stage=f"verification enqueue {index}",
    )
    return str(payload["id"])


def _worker_environment(settings: Settings) -> dict[str, str]:
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("FENGMOU_")
    }
    environment.update(
        {
            "PYTHONUTF8": "1",
            "FENGMOU_ENVIRONMENT": settings.environment,
            "FENGMOU_DATABASE_URL": settings.database_url,
            "FENGMOU_DATABASE_SCHEMA_MODE": "verify",
            "FENGMOU_STORAGE_ROOT": str(settings.storage_root),
            "FENGMOU_MAX_UPLOAD_BYTES": str(settings.max_upload_bytes),
            "FENGMOU_ALLOW_DEMO_ANALYZER": "false",
            "FENGMOU_REMOTE_ANALYZER_ENABLED": "false",
            "FENGMOU_VERIFICATION_EXECUTION_MODE": "external",
            "FENGMOU_VERIFICATION_LEASE_SECONDS": str(
                settings.verification_lease_seconds
            ),
            "FENGMOU_VERIFICATION_HEARTBEAT_SECONDS": str(
                settings.verification_heartbeat_seconds
            ),
            "FENGMOU_VERIFICATION_MAX_ATTEMPTS": str(
                settings.verification_max_attempts
            ),
            "FENGMOU_VERIFICATION_WORKER_POLL_SECONDS": str(
                settings.verification_worker_poll_seconds
            ),
            "FENGMOU_VERIFICATION_QUEUE_WARNING_SECONDS": str(
                settings.verification_queue_warning_seconds
            ),
            "FENGMOU_VERIFICATION_OBSERVABILITY_WINDOW_SECONDS": str(
                settings.verification_observability_window_seconds
            ),
            "FENGMOU_OPERATOR_API_KEY": settings.operator_api_key or "",
            "FENGMOU_REVIEWER_API_KEY": settings.reviewer_api_key or "",
            "FENGMOU_AUDITOR_API_KEY": settings.auditor_api_key or "",
            "FENGMOU_CORS_ORIGINS": ",".join(settings.cors_origins),
        }
    )
    return environment


def _redact_process_error(value: str, settings: Settings) -> str:
    redacted = value
    secrets: list[str] = []
    try:
        password = make_url(settings.database_url).password
    except Exception:
        password = None
    for secret in (
        password,
        settings.operator_api_key,
        settings.reviewer_api_key,
        settings.auditor_api_key,
    ):
        if secret:
            secrets.append(secret)
    for secret in secrets:
        redacted = redacted.replace(secret, "<redacted>")
    return " ".join(redacted.strip().split())[-1200:]


def _public_text_contains_secret(text: str, settings: Settings) -> bool:
    candidates = [
        make_url(settings.database_url).password,
        settings.operator_api_key,
        settings.reviewer_api_key,
        settings.auditor_api_key,
    ]
    return any(secret and secret in text for secret in candidates)


def validate_prometheus_acceptance_payload(
    *,
    body: str,
    content_type: str | None,
) -> None:
    """Validate the Prometheus 0.0.4 contract without relying on metric order."""

    if content_type is None:
        raise AcceptanceError("metrics endpoint omitted Content-Type")
    normalized = content_type.split(";")[0].strip().lower()
    if normalized != "text/plain":
        raise AcceptanceError(
            f"metrics Content-Type media type is {normalized!r}, expected text/plain"
        )
    lowered = content_type.lower()
    if "version=0.0.4" not in lowered:
        raise AcceptanceError(
            "metrics Content-Type must declare Prometheus text version=0.0.4"
        )
    if "charset=utf-8" not in lowered:
        raise AcceptanceError("metrics Content-Type must declare charset=utf-8")
    if not body:
        raise AcceptanceError("metrics endpoint returned an empty body")
    if not body.endswith("\n"):
        raise AcceptanceError("metrics body must end with a trailing newline")
    if "\0" in body:
        raise AcceptanceError("metrics body must not contain NUL bytes")

    for family in PROMETHEUS_REQUIRED_FAMILIES:
        if f"# HELP {family} " not in body:
            raise AcceptanceError(
                f"metrics body is missing HELP for required family {family}"
            )
        if f"# TYPE {family} gauge" not in body:
            raise AcceptanceError(
                f"metrics body is missing TYPE gauge for required family {family}"
            )

    # Reject known high-cardinality / identity label keys if they appear as labels.
    for label_key in PROMETHEUS_FORBIDDEN_LABEL_KEYS:
        if re.search(rf'(?:^|[{{,])\s*{re.escape(label_key)}="', body, re.MULTILINE):
            raise AcceptanceError(
                f"metrics body exposes forbidden label key {label_key!r}"
            )


def scan_acceptance_source_safety(source: str) -> list[str]:
    """Return static safety issues found in acceptance harness source text."""

    issues: list[str] = []
    for pattern in FORBIDDEN_SOURCE_SQL_PATTERNS:
        if pattern.search(source) is not None:
            issues.append(f"forbidden SQL pattern: {pattern.pattern}")
    return issues


def _run_worker_wave(
    settings: Settings,
    *,
    workers: int,
    wave: int,
) -> list[dict[str, object]]:
    environment = _worker_environment(settings)
    processes: list[tuple[str, subprocess.Popen[str]]] = []
    for index in range(workers):
        worker_id = f"pg-acceptance-{wave:02d}-{index:02d}-{uuid.uuid4().hex[:8]}"
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "app.worker",
                "--once",
                "--worker-id",
                worker_id,
            ],
            cwd=BACKEND_ROOT,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        processes.append((worker_id, process))

    reports: list[dict[str, object]] = []
    for worker_id, process in processes:
        try:
            stdout, stderr = process.communicate(timeout=60)
        except subprocess.TimeoutExpired as exc:
            process.kill()
            process.communicate()
            raise AcceptanceError(
                f"worker {worker_id} exceeded the 60 second acceptance timeout"
            ) from exc
        if process.returncode != 0:
            detail = _redact_process_error(stderr or stdout, settings)
            raise AcceptanceError(
                f"worker {worker_id} exited {process.returncode}: {detail}"
            )
        events: list[dict[str, Any]] = []
        for line in stdout.splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict):
                events.append(event)
        stopped = [
            event
            for event in events
            if event.get("event") == "verification_worker_stopped"
        ]
        if len(stopped) != 1:
            raise AcceptanceError(
                f"worker {worker_id} did not emit one terminal lifecycle event"
            )
        processed = stopped[0].get("processed_jobs")
        if not isinstance(processed, int) or processed not in {0, 1}:
            raise AcceptanceError(
                f"worker {worker_id} returned an invalid processed_jobs value"
            )
        reports.append({"worker_id": worker_id, "processed_jobs": processed})
    return reports


def _job_status_counts(app: FastAPI, job_ids: list[str]) -> dict[str, int]:
    with app.state.database.session_factory() as database:
        statuses = list(
            database.scalars(
                select(VerificationJob.status).where(
                    VerificationJob.id.in_(job_ids)
                )
            ).all()
        )
    counts: dict[str, int] = {}
    for status in statuses:
        counts[str(status)] = counts.get(str(status), 0) + 1
    return counts


def _run_process_workers(
    app: FastAPI,
    settings: Settings,
    job_ids: list[str],
    *,
    workers: int,
) -> dict[str, object]:
    reports: list[dict[str, object]] = []
    previous_completed = 0
    max_waves = math.ceil(len(job_ids) / workers) + 3
    started = perf_counter()
    for wave in range(max_waves):
        counts = _job_status_counts(app, job_ids)
        if counts.get("needs_review", 0) == len(job_ids):
            break
        invalid = set(counts) - {"queued", "needs_review"}
        if invalid:
            raise AcceptanceError(
                "worker acceptance reached unexpected job states: "
                + ", ".join(sorted(invalid))
            )
        wave_reports = _run_worker_wave(settings, workers=workers, wave=wave)
        reports.extend(wave_reports)
        counts = _job_status_counts(app, job_ids)
        completed = counts.get("needs_review", 0)
        if completed <= previous_completed:
            raise AcceptanceError(
                "a complete worker wave made no durable queue progress"
            )
        previous_completed = completed
    final_counts = _job_status_counts(app, job_ids)
    if final_counts != {"needs_review": len(job_ids)}:
        raise AcceptanceError(
            f"process workers did not finish every queued job: {final_counts!r}"
        )
    workers_with_jobs = [
        str(report["worker_id"])
        for report in reports
        if report["processed_jobs"] == 1
    ]
    if len(workers_with_jobs) != len(job_ids):
        raise AcceptanceError(
            "worker lifecycle totals do not match the completed job count"
        )
    if len(set(workers_with_jobs)) < 2:
        raise AcceptanceError("fewer than two independent workers completed jobs")
    return {
        "jobs": len(job_ids),
        "workers_per_wave": workers,
        "processes_launched": len(reports),
        "processes_with_jobs": len(workers_with_jobs),
        "distinct_processes_with_jobs": len(set(workers_with_jobs)),
        "elapsed_ms": round((perf_counter() - started) * 1000, 3),
    }


def _validated_stub_result(app: FastAPI, job_id: str) -> dict[str, Any]:
    with app.state.database.session_factory() as database:
        job = database.get(VerificationJob, job_id)
        if job is None:
            raise AcceptanceError("fencing job disappeared")
        evidence = database.get(EvidenceAsset, job.evidence_id)
        baseline = database.get(DesignBaseline, job.baseline_id)
        if evidence is None or baseline is None:
            raise AcceptanceError("fencing job lost its bound input")
        analyzer = StubAnalyzer()
        raw_result = analyzer.analyze(evidence, baseline)
        return validate_analyzer_result(
            raw_result,
            evidence=evidence,
            baseline=baseline,
            expected_name=analyzer.name,
            expected_version=analyzer.version,
            expected_synthetic=False,
        )


def _run_fencing_acceptance(app: FastAPI, job_id: str) -> dict[str, object]:
    stale = analysis.claim_verification_job(app, job_id, "pg-acceptance-stale")
    if stale is None:
        raise AcceptanceError("could not establish the stale lease")
    with app.state.database.session_factory.begin() as database:
        expired = database.execute(
            update(VerificationJobLease)
            .where(
                VerificationJobLease.job_id == job_id,
                VerificationJobLease.owner_id == stale.worker_id,
                VerificationJobLease.generation == stale.generation,
            )
            .values(
                lease_expires_at=func.current_timestamp()
                - text("INTERVAL '1 second'")
            )
            .execution_options(synchronize_session=False)
        )
        if expired.rowcount != 1:
            raise AcceptanceError("could not expire the controlled stale lease")
    if analysis.reap_expired_verification_jobs(app) != 1:
        raise AcceptanceError("the PostgreSQL lease reaper did not recover one job")
    fresh = analysis.claim_verification_job(app, job_id, "pg-acceptance-fresh")
    if fresh is None:
        raise AcceptanceError("the recovered job could not be reclaimed")
    if analysis.renew_verification_job_lease(app, stale):
        raise AcceptanceError("a stale generation renewed after reassignment")
    if not analysis.renew_verification_job_lease(app, fresh):
        raise AcceptanceError("the current generation could not renew")
    result = _validated_stub_result(app, job_id)
    if analysis._complete_verification_job(app, stale, result):  # noqa: SLF001
        raise AcceptanceError("a stale generation crossed the terminal write fence")
    if not analysis._complete_verification_job(app, fresh, result):  # noqa: SLF001
        raise AcceptanceError("the current generation could not commit its result")

    with app.state.database.session_factory() as database:
        attempts = list(
            database.scalars(
                select(VerificationAttempt)
                .where(VerificationAttempt.job_id == job_id)
                .order_by(VerificationAttempt.generation)
            ).all()
        )
        outcomes = list(
            database.scalars(
                select(VerificationAttemptOutcome)
                .join(
                    VerificationAttempt,
                    VerificationAttempt.id
                    == VerificationAttemptOutcome.attempt_id,
                )
                .where(VerificationAttempt.job_id == job_id)
                .order_by(VerificationAttempt.generation)
            ).all()
        )
        job = database.get(VerificationJob, job_id)
        lease = database.get(VerificationJobLease, job_id)
    if [attempt.generation for attempt in attempts] != [1, 2]:
        raise AcceptanceError("fencing attempts did not advance generations [1, 2]")
    if [outcome.disposition for outcome in outcomes] != [
        "lease_expired",
        "committed_success",
    ]:
        raise AcceptanceError("fencing outcomes did not preserve expiry then success")
    if (
        job is None
        or job.status != "needs_review"
        or lease is None
        or lease.owner_id is not None
        or lease.generation != 2
        or lease.attempt_count != 2
    ):
        raise AcceptanceError("fencing terminal state is inconsistent")
    return {
        "stale_generation": stale.generation,
        "fresh_generation": fresh.generation,
        "stale_renewal_rejected": True,
        "stale_terminal_write_rejected": True,
        "outcomes": [outcome.disposition for outcome in outcomes],
    }


def _assert_append_only_rejection(
    app: FastAPI,
    statement: Any,
    parameters: Mapping[str, object],
) -> None:
    try:
        with app.state.database.engine.begin() as connection:
            connection.execute(statement, parameters)
    except DBAPIError as exc:
        sqlstate = getattr(exc.orig, "sqlstate", None)
        if sqlstate != APPEND_ONLY_SQLSTATE:
            raise AcceptanceError(
                "append-only trigger returned unexpected SQLSTATE "
                f"{sqlstate!r}, expected {APPEND_ONLY_SQLSTATE!r}"
            ) from exc
        return
    raise AcceptanceError("an append-only UPDATE/DELETE unexpectedly committed")


def _run_append_only_acceptance(
    app: FastAPI,
    *,
    job_id: str,
) -> dict[str, object]:
    with app.state.database.session_factory() as database:
        attempt = database.scalar(
            select(VerificationAttempt)
            .where(VerificationAttempt.job_id == job_id)
            .order_by(VerificationAttempt.generation.desc())
        )
        if attempt is None:
            raise AcceptanceError("no attempt exists for append-only validation")
        outcome = database.scalar(
            select(VerificationAttemptOutcome).where(
                VerificationAttemptOutcome.attempt_id == attempt.id
            )
        )
        if outcome is None:
            raise AcceptanceError("no outcome exists for append-only validation")
        attempt_id = attempt.id
        outcome_id = outcome.id

    checks = (
        (
            text(
                "UPDATE verification_attempts "
                "SET worker_id = worker_id WHERE id = :row_id"
            ),
            attempt_id,
        ),
        (
            text("DELETE FROM verification_attempts WHERE id = :row_id"),
            attempt_id,
        ),
        (
            text(
                "UPDATE verification_attempt_outcomes "
                "SET stage = stage WHERE id = :row_id"
            ),
            outcome_id,
        ),
        (
            text(
                "DELETE FROM verification_attempt_outcomes WHERE id = :row_id"
            ),
            outcome_id,
        ),
    )
    for statement, row_id in checks:
        _assert_append_only_rejection(app, statement, {"row_id": row_id})
    with app.state.database.session_factory() as database:
        if database.get(VerificationAttempt, attempt_id) is None:
            raise AcceptanceError("attempt row disappeared after rejected mutation")
        if database.get(VerificationAttemptOutcome, outcome_id) is None:
            raise AcceptanceError("outcome row disappeared after rejected mutation")
    return {
        "mutations_rejected": len(checks),
        "sqlstate": APPEND_ONLY_SQLSTATE,
        "rows_preserved": True,
    }


def _verify_worker_history(
    app: FastAPI,
    *,
    job_ids: list[str],
) -> dict[str, object]:
    with app.state.database.session_factory() as database:
        jobs = list(
            database.scalars(
                select(VerificationJob).where(VerificationJob.id.in_(job_ids))
            ).all()
        )
        attempts = list(
            database.scalars(
                select(VerificationAttempt).where(
                    VerificationAttempt.job_id.in_(job_ids)
                )
            ).all()
        )
        outcomes = list(
            database.scalars(
                select(VerificationAttemptOutcome)
                .join(
                    VerificationAttempt,
                    VerificationAttempt.id
                    == VerificationAttemptOutcome.attempt_id,
                )
                .where(VerificationAttempt.job_id.in_(job_ids))
            ).all()
        )
        leases = list(
            database.scalars(
                select(VerificationJobLease).where(
                    VerificationJobLease.job_id.in_(job_ids)
                )
            ).all()
        )
        dispatch_issues = analysis.scan_verification_dispatch_integrity(database)
        attempt_issues = analysis.scan_verification_attempt_integrity(database)
    if len(jobs) != len(job_ids):
        raise AcceptanceError("some process-worker jobs disappeared")
    if len(attempts) != len(job_ids) or len(outcomes) != len(job_ids):
        raise AcceptanceError(
            "process-worker jobs did not produce exactly one attempt and outcome each"
        )
    if len(leases) != len(job_ids):
        raise AcceptanceError("some process-worker jobs lost their lease row")
    if any(job.status != "needs_review" for job in jobs):
        raise AcceptanceError("a process-worker job did not reach needs_review")
    if any(
        lease.owner_id is not None
        or lease.generation != 1
        or lease.attempt_count != 1
        for lease in leases
    ):
        raise AcceptanceError("process-worker lease counters are inconsistent")
    if any(outcome.disposition != "committed_success" for outcome in outcomes):
        raise AcceptanceError("a process-worker outcome is not committed_success")
    if dispatch_issues or attempt_issues:
        raise AcceptanceError(
            "worker integrity scanners found contradictions after PostgreSQL execution"
        )
    return {
        "jobs": len(jobs),
        "attempts": len(attempts),
        "outcomes": len(outcomes),
        "leases": len(leases),
        "dispatch_integrity_issues": 0,
        "attempt_integrity_issues": 0,
    }


def run_application_acceptance(
    scoped_url: str,
    schema: str,
    *,
    jobs: int,
    workers: int,
) -> dict[str, object]:
    with tempfile.TemporaryDirectory(
        prefix="fengmou-postgres-acceptance-"
    ) as temporary_root:
        settings = _acceptance_settings(
            scoped_url,
            Path(temporary_root) / "storage",
        )
        app = create_app(settings)
        try:
            with TestClient(app) as client:
                client.headers.update(
                    {"X-API-Key": settings.operator_api_key or ""}
                )
                ready = client.get("/api/v1/readyz")
                if ready.status_code != 200:
                    raise AcceptanceError(
                        f"API readiness returned HTTP {ready.status_code}"
                    )
                meta = _response_json(
                    client.get("/api/v1/meta"),
                    expected_status=200,
                    stage="API metadata",
                )
                schema_status = meta.get("database_schema")
                if not isinstance(schema_status, dict) or not (
                    schema_status.get("at_head") is True
                    and schema_status.get("drift_free") is True
                    and schema_status.get("mode") == "verify"
                ):
                    raise AcceptanceError(
                        "API did not start against a verified PostgreSQL schema"
                    )
                project, baseline = _create_project_and_baseline(
                    client,
                    schema=schema,
                )
                process_job_ids = [
                    _enqueue_job(
                        client,
                        project,
                        baseline,
                        index=index,
                    )
                    for index in range(jobs)
                ]
                process_report = _run_process_workers(
                    app,
                    settings,
                    process_job_ids,
                    workers=workers,
                )
                history_report = _verify_worker_history(
                    app,
                    job_ids=process_job_ids,
                )
                sample_detail = _response_json(
                    client.get(
                        f"/api/v1/verifications/{process_job_ids[0]}"
                    ),
                    expected_status=200,
                    stage="completed verification detail",
                )
                attempts_payload = sample_detail.get("attempts")
                if not isinstance(attempts_payload, list) or len(attempts_payload) != 1:
                    raise AcceptanceError(
                        "API did not expose exactly one redacted attempt for the sample job"
                    )
                if "worker_id" in attempts_payload[0]:
                    raise AcceptanceError("API exposed a raw worker identity")

                metrics_started = perf_counter()
                metrics = client.get(
                    "/api/v1/operations/verification-dispatch/metrics",
                    headers={"X-API-Key": settings.auditor_api_key or ""},
                )
                metrics_elapsed_ms = round(
                    (perf_counter() - metrics_started) * 1000,
                    3,
                )
                if metrics.status_code != 200:
                    raise AcceptanceError(
                        f"metrics endpoint returned HTTP {metrics.status_code}"
                    )
                validate_prometheus_acceptance_payload(
                    body=metrics.text,
                    content_type=metrics.headers.get("content-type"),
                )
                if _public_text_contains_secret(metrics.text, settings):
                    raise AcceptanceError(
                        "metrics body leaked an acceptance credential"
                    )

                fencing_job_id = _enqueue_job(
                    client,
                    project,
                    baseline,
                    index=jobs,
                )
                fencing_report = _run_fencing_acceptance(
                    app,
                    fencing_job_id,
                )
                append_only_report = _run_append_only_acceptance(
                    app,
                    job_id=fencing_job_id,
                )
                final_status = verify_database_schema(
                    app.state.database.engine
                )
                if not final_status.at_head or not final_status.drift_free:
                    raise AcceptanceError(
                        "schema verification failed after live application writes"
                    )
                ready_after = client.get("/api/v1/readyz")
                if ready_after.status_code != 200:
                    raise AcceptanceError(
                        f"final API readiness returned HTTP {ready_after.status_code}"
                    )
            return {
                "api_schema_mode": "verify",
                "api_ready_before": True,
                "api_ready_after": True,
                "process_workers": process_report,
                "worker_history": history_report,
                "fencing": fencing_report,
                "append_only": append_only_report,
                "metrics": {
                    "status": 200,
                    "elapsed_ms": metrics_elapsed_ms,
                    "prometheus_contract": True,
                },
            }
        except AcceptanceError:
            raise
        except Exception as exc:
            raise AcceptanceError(
                f"PostgreSQL application acceptance failed ({type(exc).__name__})"
            ) from exc
        finally:
            app.state.database.engine.dispose()


def run_acceptance(
    target: TargetIdentity,
    *,
    jobs: int,
    workers: int,
) -> dict[str, object]:
    validate_run_shape(jobs=jobs, workers=workers)
    control_database = _base_database(target)
    schema = _new_schema_name()
    created = False
    primary_error: BaseException | None = None
    report: dict[str, object] | None = None
    cleanup_error: BaseException | None = None
    try:
        server = inspect_server(control_database, target)
        create_isolated_schema(control_database, schema)
        created = True
        scoped_url = scoped_database_url(target, schema)
        migration = run_migration_acceptance(scoped_url)
        application = run_application_acceptance(
            scoped_url,
            schema,
            jobs=jobs,
            workers=workers,
        )
        report = {
            "ok": True,
            "target": target.public_dict(),
            "server": server.public_dict(),
            "schema": {
                "name": schema,
                "isolation": "per-run PostgreSQL schema/search_path",
            },
            "migration": migration,
            "application": application,
        }
    except BaseException as exc:
        primary_error = exc
    finally:
        if created:
            try:
                drop_isolated_schema(control_database, schema)
            except BaseException as exc:
                cleanup_error = exc
        control_database.engine.dispose()

    if primary_error is not None:
        if cleanup_error is not None:
            raise AcceptanceError(
                "acceptance failed and controlled schema cleanup also failed "
                f"({type(cleanup_error).__name__}); schema={schema}"
            ) from primary_error
        raise primary_error
    if cleanup_error is not None:
        raise AcceptanceError(
            "acceptance checks passed but controlled schema cleanup failed "
            f"({type(cleanup_error).__name__}); schema={schema}"
        ) from cleanup_error
    if report is None:
        raise AcceptanceError("acceptance ended without a report")
    report["schema"] = {
        **dict(report["schema"]),  # type: ignore[arg-type]
        "cleanup": "dropped",
    }
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run destructive-safe live Fengmou acceptance against an explicit "
            "loopback PostgreSQL test database. The database URL is read only "
            f"from {DATABASE_URL_ENV} so credentials do not enter argv."
        )
    )
    parser.add_argument(
        "--jobs",
        type=int,
        default=8,
        help="number of independent external-worker jobs (default: 8)",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=4,
        help="worker processes launched per contention wave (default: 4)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        target = validate_target_url(os.getenv(DATABASE_URL_ENV))
        report = run_acceptance(
            target,
            jobs=args.jobs,
            workers=args.workers,
        )
    except AcceptanceRefusal as exc:
        print(f"postgres acceptance refused: {exc}", file=sys.stderr)
        return 2
    except AcceptanceError as exc:
        print(f"postgres acceptance failed: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(
            "postgres acceptance failed with an unexpected "
            f"{type(exc).__name__}; no database URL was printed",
            file=sys.stderr,
        )
        return 1
    print(
        json.dumps(
            report,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
