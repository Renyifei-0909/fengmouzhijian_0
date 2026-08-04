from __future__ import annotations

import pytest

from app.config import Settings
from app.services.analyzers import analyzer_descriptor, build_analyzer
from app.services.analyzers.remote_http import RemoteHTTPAnalyzer


def _remote_settings(**updates) -> Settings:
    values = {
        "environment": "test",
        "remote_analyzer_enabled": True,
        "remote_analyzer_url": "https://algorithm.example.test/v1/analyze",
        "remote_analyzer_api_key": "dedicated-remote-secret",
        "remote_analyzer_model_name": "hidden-work-baseline",
        "remote_analyzer_model_version": "0.1.0",
        "remote_analyzer_model_sha256": "a" * 64,
    }
    values.update(updates)
    return Settings(**values)


def test_remote_analyzer_is_fail_closed_but_disabled_by_default() -> None:
    assert Settings().remote_analyzer_enabled is False
    with pytest.raises(ValueError, match="configuration is missing"):
        Settings(remote_analyzer_enabled=True)


def test_remote_analyzer_configuration_is_pinned_and_secrets_are_not_repr_exposed() -> None:
    settings = _remote_settings()
    rendered = repr(settings)
    assert settings.remote_analyzer_model_sha256 == "a" * 64
    assert "dedicated-remote-secret" not in rendered
    descriptor = analyzer_descriptor("remote_http", settings=settings)
    assert descriptor["enabled"] is True
    assert descriptor["runtime_mode"] == "model"
    assert descriptor["synthetic"] is False
    adapter = build_analyzer(
        "remote_http",
        settings=settings,
        job_id="job-1",
        pinned_version=str(descriptor["version"]),
    )
    assert isinstance(adapter, RemoteHTTPAnalyzer)
    with pytest.raises(RuntimeError, match="changed after"):
        build_analyzer(
            "remote_http",
            settings=settings,
            job_id="job-1",
            pinned_version="stale-version",
        )


@pytest.mark.parametrize(
    ("updates", "message"),
    [
        ({"remote_analyzer_model_sha256": "not-a-digest"}, "64 lowercase"),
        ({"remote_analyzer_max_upload_bytes": 0}, "upload limit"),
        ({"remote_analyzer_timeout_seconds": 0}, "TIMEOUT_SECONDS"),
        ({"remote_analyzer_url": "https://example.test/analyze?token=secret"}, "must not contain"),
        ({"environment": "production", "remote_analyzer_url": "http://algorithm/analyze"}, "HTTPS"),
        (
            {"environment": "production", "remote_analyzer_expected_runtime_mode": "stub"},
            "only permitted in test or demo",
        ),
        ({"remote_analyzer_expected_runtime_mode": "unexpected"}, "EXPECTED_RUNTIME_MODE"),
    ],
)
def test_invalid_remote_configuration_fails_at_settings_creation(updates, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        _remote_settings(**updates)


@pytest.mark.parametrize("environment", ["test", "demo"])
def test_remote_stub_requires_explicit_nonproduction_mode(environment: str) -> None:
    settings = _remote_settings(
        environment=environment,
        remote_analyzer_expected_runtime_mode="stub",
    )
    descriptor = analyzer_descriptor("remote_http", settings=settings)
    assert descriptor["enabled"] is True
    assert descriptor["runtime_mode"] == "stub"
    assert descriptor["synthetic"] is True


@pytest.mark.parametrize(
    ("updates", "message"),
    [
        ({"verification_execution_mode": "broker"}, "EXECUTION_MODE"),
        (
            {"verification_lease_seconds": 5, "verification_heartbeat_seconds": 5},
            "HEARTBEAT_SECONDS",
        ),
        ({"verification_max_attempts": 0}, "MAX_ATTEMPTS"),
        ({"verification_worker_poll_seconds": 0}, "POLL_SECONDS"),
        ({"verification_queue_warning_seconds": 0}, "QUEUE_WARNING_SECONDS"),
        (
            {"verification_observability_window_seconds": 59},
            "OBSERVABILITY_WINDOW_SECONDS",
        ),
        (
            {
                "environment": "production",
                "database_url": "sqlite:///production.db",
                "verification_execution_mode": "external",
            },
            "limited to development/test/demo",
        ),
    ],
)
def test_invalid_verification_worker_configuration_fails_closed(updates, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        Settings(**updates)


def test_local_external_worker_mode_is_explicitly_allowed_for_single_worker_demo() -> None:
    settings = Settings(environment="demo", verification_execution_mode="external")
    assert settings.verification_execution_mode == "external"


def test_worker_observability_thresholds_are_configurable_from_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FENGMOU_VERIFICATION_QUEUE_WARNING_SECONDS", "125.5")
    monkeypatch.setenv("FENGMOU_VERIFICATION_OBSERVABILITY_WINDOW_SECONDS", "1800")
    settings = Settings.from_env()
    assert settings.verification_queue_warning_seconds == 125.5
    assert settings.verification_observability_window_seconds == 1800


@pytest.mark.parametrize(
    ("environment", "expected_mode"),
    [
        ("test", "create_all"),
        ("openapi-export", "create_all"),
        ("development", "upgrade"),
        ("demo", "upgrade"),
        ("staging", "verify"),
        ("production", "verify"),
    ],
)
def test_schema_mode_defaults_are_environment_specific(
    monkeypatch: pytest.MonkeyPatch,
    environment: str,
    expected_mode: str,
) -> None:
    monkeypatch.setenv("FENGMOU_ENVIRONMENT", environment)
    monkeypatch.delenv("FENGMOU_DATABASE_SCHEMA_MODE", raising=False)
    assert Settings.from_env().database_schema_mode == expected_mode
    assert Settings(environment=environment).database_schema_mode == expected_mode


@pytest.mark.parametrize(
    ("updates", "message"),
    [
        ({"database_url": "mysql://db/fengmou"}, "SQLite or PostgreSQL"),
        ({"database_url": "postgresql://db/fengmou"}, "psycopg 3"),
        ({"database_schema_mode": "stamp"}, "DATABASE_SCHEMA_MODE"),
        (
            {"environment": "production", "database_schema_mode": "create_all"},
            "limited to local/test",
        ),
    ],
)
def test_invalid_database_configuration_fails_closed(updates, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        Settings(**updates)


def test_explicit_psycopg3_database_url_is_accepted() -> None:
    settings = Settings(
        environment="production",
        database_url="postgresql+psycopg://user:database-secret@db/fengmou",
        database_schema_mode="verify",
    )
    assert settings.database_url.startswith("postgresql+psycopg://")
    assert "database-secret" not in repr(settings)
