from __future__ import annotations

from pathlib import Path
import re

import pytest
from sqlalchemy.engine import make_url

from app.config import Settings
from app.schemas import (
    VerificationOperationsAlert,
    VerificationOperationsAttempts,
    VerificationOperationsDispatch,
    VerificationOperationsIntegrity,
    VerificationOperationsJobs,
    VerificationOperationsSnapshot,
    VerificationOperationsThresholds,
)
from app.services.metrics import PROMETHEUS_CONTENT_TYPE, render_verification_prometheus
from app.services.observability import OUTCOME_DISPOSITIONS
from scripts.postgres_acceptance import (
    ACCEPTANCE_DATABASE_NAME,
    COMPOSE_ACCEPTANCE_PATH,
    DATABASE_URL_ENV,
    INIT_SQL_PATH,
    PROMETHEUS_REQUIRED_FAMILIES,
    SCHEMA_PREFIX,
    AcceptanceError,
    AcceptanceRefusal,
    _acceptance_settings,
    _new_schema_name,
    _redact_process_error,
    _validate_owned_schema_name,
    _worker_environment,
    main,
    scan_acceptance_source_safety,
    validate_prometheus_acceptance_payload,
    validate_run_shape,
    validate_target_url,
)


BACKEND_ROOT = Path(__file__).resolve().parents[1]
ACCEPTANCE_SCRIPT = BACKEND_ROOT / "scripts" / "postgres_acceptance.py"
PROJECT_ROOT = BACKEND_ROOT.parent


def _valid_url(
    *,
    driver: str = "postgresql+psycopg",
    user: str = "fengmou_app",
    password: str = "acceptance-secret-password",
    host: str = "127.0.0.1",
    port: int = 55432,
    database: str = ACCEPTANCE_DATABASE_NAME,
    query: str = "",
) -> str:
    auth = f"{user}:{password}@" if user or password else ""
    host_part = f"[{host}]" if ":" in host else host
    netloc = f"{host_part}:{port}" if port else host_part
    suffix = f"?{query}" if query else ""
    return f"{driver}://{auth}{netloc}/{database}{suffix}"


def _outcomes(**updates: int) -> dict[str, int]:
    values = {disposition: 0 for disposition in OUTCOME_DISPOSITIONS}
    values.update(updates)
    return values


def _sample_metrics_body() -> str:
    snapshot = VerificationOperationsSnapshot(
        status="healthy",
        generated_at=__import__("datetime").datetime(
            2026, 7, 28, 12, 0, tzinfo=__import__("datetime").timezone.utc
        ),
        execution_mode="external",
        thresholds=VerificationOperationsThresholds(
            queue_wait_warning_seconds=60,
            recent_window_seconds=900,
            lease_seconds=5,
            heartbeat_seconds=1,
        ),
        jobs=VerificationOperationsJobs(
            total=2,
            by_status={"queued": 1, "needs_review": 1},
        ),
        dispatch=VerificationOperationsDispatch(
            lease_rows=2,
            active_leases=0,
            expired_running_leases=0,
            unclaimed_queued_jobs=1,
            queued_over_warning_threshold=0,
            dead_letter_jobs=0,
            oldest_queued_seconds=1.5,
            oldest_active_heartbeat_seconds=None,
        ),
        attempts=VerificationOperationsAttempts(
            total=1,
            open=0,
            outcomes_total_by_disposition=_outcomes(committed_success=1),
            outcomes_window_by_disposition=_outcomes(committed_success=1),
            recent_instability=0,
        ),
        integrity=VerificationOperationsIntegrity(
            status="ok",
            dispatch_issue_count=0,
            attempt_issue_count=0,
            issue_count=0,
        ),
        alerts=[],
        truth_note="acceptance metrics sample",
    )
    return render_verification_prometheus(
        snapshot,
        collection_duration_seconds=0.012,
    )


def test_main_exits_2_when_url_missing(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv(DATABASE_URL_ENV, raising=False)
    assert main([]) == 2
    captured = capsys.readouterr()
    assert "refused" in captured.err
    assert DATABASE_URL_ENV in captured.err
    assert "postgresql+psycopg://" not in captured.err
    assert "acceptance-secret" not in captured.err


def test_rejects_sqlite_and_non_psycopg_urls() -> None:
    with pytest.raises(AcceptanceRefusal, match="postgresql\\+psycopg"):
        validate_target_url("sqlite:///tmp/acceptance.db")
    with pytest.raises(AcceptanceRefusal, match="postgresql\\+psycopg"):
        validate_target_url(
            "postgresql://fengmou_app:secret@127.0.0.1:5432/fengmou_acceptance"
        )
    with pytest.raises(AcceptanceRefusal, match="postgresql\\+psycopg"):
        validate_target_url(
            "postgresql+asyncpg://fengmou_app:secret@127.0.0.1:5432/fengmou_acceptance"
        )


def test_rejects_wrong_database_name() -> None:
    with pytest.raises(AcceptanceRefusal, match="fengmou_acceptance"):
        validate_target_url(
            _valid_url(database="postgres")
        )
    with pytest.raises(AcceptanceRefusal, match="fengmou_acceptance"):
        validate_target_url(
            _valid_url(database="fengmou_acceptance_other")
        )


def test_rejects_non_loopback_hosts() -> None:
    with pytest.raises(AcceptanceRefusal, match="loopback"):
        validate_target_url(_valid_url(host="10.0.0.8"))
    with pytest.raises(AcceptanceRefusal, match="loopback"):
        validate_target_url(_valid_url(host="db.internal.example"))
    with pytest.raises(AcceptanceRefusal, match="loopback"):
        validate_target_url(_valid_url(host=""))


def test_accepts_loopback_hosts() -> None:
    for host in ("127.0.0.1", "localhost", "::1"):
        identity = validate_target_url(_valid_url(host=host))
        assert identity.database == ACCEPTANCE_DATABASE_NAME
        assert identity.host == host
        assert identity.url.drivername == "postgresql+psycopg"
        public = identity.public_dict()
        assert "password" not in public
        assert "username" not in public
        assert public["database"] == ACCEPTANCE_DATABASE_NAME


def test_rejects_missing_username_or_password() -> None:
    with pytest.raises(AcceptanceRefusal, match="username and password"):
        validate_target_url(
            f"postgresql+psycopg://:secret@127.0.0.1:55432/{ACCEPTANCE_DATABASE_NAME}"
        )
    with pytest.raises(AcceptanceRefusal, match="username and password"):
        validate_target_url(
            f"postgresql+psycopg://fengmou_app@127.0.0.1:55432/{ACCEPTANCE_DATABASE_NAME}"
        )


def test_rejects_external_query_and_options_injection() -> None:
    with pytest.raises(AcceptanceRefusal, match="query parameters"):
        validate_target_url(_valid_url(query="options=-csearch_path=public"))
    with pytest.raises(AcceptanceRefusal, match="query parameters"):
        validate_target_url(_valid_url(query="sslmode=require"))
    with pytest.raises(AcceptanceRefusal, match="query parameters"):
        validate_target_url(_valid_url(query="connect_timeout=1"))


def test_rejects_out_of_range_jobs_and_workers() -> None:
    with pytest.raises(AcceptanceRefusal, match="--workers"):
        validate_run_shape(jobs=8, workers=1)
    with pytest.raises(AcceptanceRefusal, match="--workers"):
        validate_run_shape(jobs=8, workers=17)
    with pytest.raises(AcceptanceRefusal, match="--jobs"):
        validate_run_shape(jobs=3, workers=4)
    with pytest.raises(AcceptanceRefusal, match="--jobs"):
        validate_run_shape(jobs=65, workers=4)
    validate_run_shape(jobs=8, workers=4)


def test_schema_cleanup_requires_strict_owned_name() -> None:
    generated = _new_schema_name()
    assert generated.startswith(SCHEMA_PREFIX)
    _validate_owned_schema_name(generated)

    for bad in (
        "public",
        "fengmou_acceptance",
        "fengmou_acceptance_",
        "fengmou_acceptance_nothexzzzzzzzzzzzzzzzz",
        "fengmou_acceptance_" + "a" * 23,
        "fengmou_acceptance_" + "a" * 25,
        "other_prefix_" + "a" * 24,
        "FENGMOU_ACCEPTANCE_" + "a" * 24,
        "../escape",
        "",
    ):
        with pytest.raises(AcceptanceRefusal, match="refusing cleanup"):
            _validate_owned_schema_name(bad)


def test_public_errors_and_redaction_omit_password_and_api_keys() -> None:
    settings = _acceptance_settings(
        _valid_url(password="super-secret-db-password"),
        Path("unused-storage"),
    )
    # Override keys to distinctive secrets for redaction checks.
    settings = Settings(
        environment="staging",
        database_url=_valid_url(password="super-secret-db-password"),
        database_schema_mode="verify",
        storage_root=Path("unused-storage"),
        allow_demo_analyzer=False,
        operator_api_key="acceptance-operator-secret-key",
        reviewer_api_key="acceptance-reviewer-secret-key",
        auditor_api_key="acceptance-auditor-secret-key",
        verification_execution_mode="external",
        cors_origins=("http://testserver",),
    )
    noisy = (
        "worker boom password=super-secret-db-password "
        "key=acceptance-operator-secret-key "
        "review=acceptance-reviewer-secret-key "
        "audit=acceptance-auditor-secret-key"
    )
    redacted = _redact_process_error(noisy, settings)
    assert "super-secret-db-password" not in redacted
    assert "acceptance-operator-secret-key" not in redacted
    assert "acceptance-reviewer-secret-key" not in redacted
    assert "acceptance-auditor-secret-key" not in redacted
    assert redacted.count("<redacted>") >= 4

    with pytest.raises(AcceptanceRefusal) as exc_info:
        validate_target_url(None)
    message = str(exc_info.value)
    assert "super-secret" not in message
    assert "acceptance-operator-secret-key" not in message


def test_worker_environment_clears_unrelated_fengmou_pollution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FENGMOU_ALLOW_DEMO_ANALYZER", "true")
    monkeypatch.setenv("FENGMOU_REMOTE_ANALYZER_ENABLED", "true")
    monkeypatch.setenv("FENGMOU_REMOTE_ANALYZER_URL", "http://evil.example")
    monkeypatch.setenv("FENGMOU_REMOTE_ANALYZER_API_KEY", "live-token-should-not-pass")
    monkeypatch.setenv("FENGMOU_OPERATOR_API_KEY", "polluted-operator")
    monkeypatch.setenv("PATH", "C:\\Windows\\System32")
    monkeypatch.setenv("UNRELATED_FLAG", "keep-me")

    settings = _acceptance_settings(
        _valid_url(password="worker-env-password"),
        Path("E:/tmp/acceptance-storage"),
    )
    environment = _worker_environment(settings)

    assert environment["UNRELATED_FLAG"] == "keep-me"
    assert environment["PATH"] == "C:\\Windows\\System32"
    assert environment["FENGMOU_ALLOW_DEMO_ANALYZER"] == "false"
    assert environment["FENGMOU_REMOTE_ANALYZER_ENABLED"] == "false"
    assert "FENGMOU_REMOTE_ANALYZER_URL" not in environment
    assert "FENGMOU_REMOTE_ANALYZER_API_KEY" not in environment
    assert environment["FENGMOU_DATABASE_SCHEMA_MODE"] == "verify"
    assert environment["FENGMOU_VERIFICATION_EXECUTION_MODE"] == "external"
    assert environment["FENGMOU_DATABASE_URL"] == settings.database_url
    assert environment["FENGMOU_OPERATOR_API_KEY"] == settings.operator_api_key
    assert settings.allow_demo_analyzer is False


def test_compose_pins_postgres_image_digest_and_loopback_only() -> None:
    text = COMPOSE_ACCEPTANCE_PATH.read_text(encoding="utf-8")
    assert "image: postgres:" in text
    assert re.search(
        r"image:\s*postgres:[^\s]+@sha256:[0-9a-f]{64}",
        text,
    ), "compose must pin a full image digest"
    assert '"127.0.0.1:55432:5432"' in text or "127.0.0.1:55432:5432" in text
    assert re.search(r'["\']?0\.0\.0\.0:', text) is None
    assert "55432:5432" in text
    # Temporary data directory: tmpfs, not a project runtime-data bind.
    assert "tmpfs:" in text
    assert "/var/lib/postgresql/data" in text
    assert "runtime-data" not in text
    assert "./data" not in text
    assert "volumes:" in text
    assert "backend/tests/postgres/init.sql" in text
    assert "healthcheck:" in text
    assert "fengmou_acceptance" in text
    # Explicit demo-only credentials must stay local and obvious.
    assert "local-postgres-admin-acceptance-only" in text
    assert "POSTGRES_PASSWORD" in text


def test_init_role_is_non_privileged() -> None:
    text = INIT_SQL_PATH.read_text(encoding="utf-8")
    for flag in (
        "NOSUPERUSER",
        "NOCREATEDB",
        "NOCREATEROLE",
        "NOREPLICATION",
        "NOBYPASSRLS",
    ):
        assert flag in text
    assert "CREATE ROLE fengmou_app" in text
    assert "SUPERUSER" not in text.replace("NOSUPERUSER", "")
    assert "CREATEDB" not in text.replace("NOCREATEDB", "")
    assert "CREATEROLE" not in text.replace("NOCREATEROLE", "")
    assert "REPLICATION" not in text.replace("NOREPLICATION", "")
    assert "BYPASSRLS" not in text.replace("NOBYPASSRLS", "")


def test_acceptance_settings_forbid_demo_fixture_and_external_keys() -> None:
    settings = _acceptance_settings(
        _valid_url(),
        Path("E:/tmp/acceptance-storage"),
    )
    assert settings.allow_demo_analyzer is False
    assert settings.verification_execution_mode == "external"
    assert settings.database_schema_mode == "verify"
    assert settings.environment == "staging"
    assert settings.remote_analyzer_enabled is False
    assert settings.remote_analyzer_url is None
    assert settings.remote_analyzer_api_key is None
    assert settings.operator_api_key
    assert settings.reviewer_api_key
    assert settings.auditor_api_key
    # Keys are local acceptance fixtures, not production secrets, but must exist.
    assert "acceptance" in (settings.operator_api_key or "")


def test_static_source_forbids_drop_database_and_public_schema_drop() -> None:
    source = ACCEPTANCE_SCRIPT.read_text(encoding="utf-8")
    assert scan_acceptance_source_safety(source) == []
    executable_drop_database = re.findall(
        r'(?:execute|text)\(\s*[rf]?["\'].*DROP\s+DATABASE',
        source,
        flags=re.IGNORECASE,
    )
    assert executable_drop_database == []
    assert "DropSchema" in source
    assert "drop_isolated_schema" in source
    assert "_validate_owned_schema_name" in source
    assert "to_regnamespace" in source

    assert scan_acceptance_source_safety("DROP DATABASE fengmou_acceptance;") == [
        "forbidden SQL pattern: \\bDROP\\s+DATABASE\\b"
    ]
    assert scan_acceptance_source_safety("drop schema public cascade;") == [
        "forbidden SQL pattern: \\bDROP\\s+SCHEMA\\s+public\\b"
    ]


def test_prometheus_acceptance_contract_matches_renderer() -> None:
    body = _sample_metrics_body()
    validate_prometheus_acceptance_payload(
        body=body,
        content_type=PROMETHEUS_CONTENT_TYPE,
    )
    for family in PROMETHEUS_REQUIRED_FAMILIES:
        assert f"# HELP {family} " in body
        assert f"# TYPE {family} gauge" in body
    # Contract must not depend on an incorrect first-family name.
    assert body.startswith("# HELP fengmou_verification_operations_info")
    assert "fengmou_verification_dispatch_status" not in body

    with pytest.raises(AcceptanceError, match="version=0.0.4"):
        validate_prometheus_acceptance_payload(
            body=body,
            content_type="text/plain; charset=utf-8",
        )
    with pytest.raises(AcceptanceError, match="missing HELP"):
        validate_prometheus_acceptance_payload(
            body="# TYPE fengmou_verification_jobs gauge\n",
            content_type=PROMETHEUS_CONTENT_TYPE,
        )
    with pytest.raises(AcceptanceError, match="forbidden label key"):
        validate_prometheus_acceptance_payload(
            body=(
                body
                + 'fengmou_verification_jobs{status="queued",job_id="abc"} 1\n'
            ),
            content_type=PROMETHEUS_CONTENT_TYPE,
        )
    with pytest.raises(AcceptanceError, match="trailing newline"):
        validate_prometheus_acceptance_payload(
            body=body.rstrip("\n"),
            content_type=PROMETHEUS_CONTENT_TYPE,
        )


def test_main_refuses_invalid_url_without_leaking_secret(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    secret = "leaked-password-value-xyz"
    monkeypatch.setenv(
        DATABASE_URL_ENV,
        f"postgresql+psycopg://fengmou_app:{secret}@10.1.2.3:5432/{ACCEPTANCE_DATABASE_NAME}",
    )
    assert main([]) == 2
    captured = capsys.readouterr()
    assert "refused" in captured.err
    assert secret not in captured.err
    assert secret not in captured.out


def test_acceptance_script_py_compiles() -> None:
    import py_compile

    py_compile.compile(str(ACCEPTANCE_SCRIPT), doraise=True)


def test_scoped_url_injects_search_path_only_for_valid_schema() -> None:
    from scripts.postgres_acceptance import scoped_database_url, TargetIdentity

    target = validate_target_url(_valid_url())
    schema = _new_schema_name()
    scoped = scoped_database_url(target, schema)
    url = make_url(scoped)
    assert url.database == ACCEPTANCE_DATABASE_NAME
    options = url.query.get("options")
    assert isinstance(options, str)
    assert f"search_path={schema}" in options
    assert "statement_timeout=30000" in options
    with pytest.raises(AcceptanceRefusal):
        scoped_database_url(target, "public")


def test_compose_and_init_paths_exist_at_repository_locations() -> None:
    assert COMPOSE_ACCEPTANCE_PATH.is_file()
    assert INIT_SQL_PATH.is_file()
    assert COMPOSE_ACCEPTANCE_PATH.resolve().parent == PROJECT_ROOT.resolve()
    assert INIT_SQL_PATH.resolve().parent == (BACKEND_ROOT / "tests" / "postgres").resolve()
