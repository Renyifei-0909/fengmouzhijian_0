from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit


def _as_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _default_schema_mode(environment: str) -> str:
    if environment.lower() in {"test", "openapi-export"}:
        return "create_all"
    if environment.lower() in {"development", "demo"}:
        return "upgrade"
    return "verify"


@dataclass(frozen=True, slots=True)
class Settings:
    environment: str = "development"
    database_url: str = field(default="sqlite:///./data/fengmou.db", repr=False)
    database_schema_mode: str = "environment-default"
    storage_root: Path = field(default_factory=lambda: Path("./data/storage"))
    max_upload_bytes: int = 500 * 1024 * 1024
    design_package_max_upload_bytes: int = 2 * 1024 * 1024
    # Standard GPKG preview upload (P1-4); independent of JSON design package limit.
    standard_gpkg_max_upload_bytes: int = 32 * 1024 * 1024
    gpkg_preview_token_ttl_seconds: int = 15 * 60
    # Independent of operator_api_key; never log or expose in API/repr.
    gpkg_preview_signing_secret: str | None = field(default=None, repr=False)
    allow_demo_analyzer: bool = False
    operator_api_key: str | None = field(default=None, repr=False)
    reviewer_api_key: str | None = field(default=None, repr=False)
    auditor_api_key: str | None = field(default=None, repr=False)
    remote_analyzer_enabled: bool = False
    remote_analyzer_url: str | None = None
    remote_analyzer_api_key: str | None = field(default=None, repr=False)
    remote_analyzer_model_name: str | None = None
    remote_analyzer_model_version: str | None = None
    remote_analyzer_model_sha256: str | None = None
    remote_analyzer_expected_runtime_mode: str = "model"
    remote_analyzer_timeout_seconds: float = 120.0
    remote_analyzer_max_upload_bytes: int = 100 * 1024 * 1024
    remote_analyzer_max_response_bytes: int = 2 * 1024 * 1024
    verification_execution_mode: str = "inline"
    verification_lease_seconds: float = 30.0
    verification_heartbeat_seconds: float = 10.0
    verification_max_attempts: int = 3
    verification_worker_poll_seconds: float = 1.0
    verification_queue_warning_seconds: float = 60.0
    verification_observability_window_seconds: int = 900
    cors_origins: tuple[str, ...] = ("http://localhost:5173", "http://localhost:4173")

    def __post_init__(self) -> None:
        if self.database_schema_mode == "environment-default":
            object.__setattr__(
                self,
                "database_schema_mode",
                _default_schema_mode(self.environment),
            )
        database_driver = self.database_url.partition(":")[0].lower()
        database_backend = database_driver.partition("+")[0]
        if database_backend not in {"sqlite", "postgresql"}:
            raise ValueError("FENGMOU_DATABASE_URL must use SQLite or PostgreSQL")
        if database_backend == "postgresql" and database_driver != "postgresql+psycopg":
            raise ValueError(
                "PostgreSQL URLs must explicitly use the psycopg 3 driver: "
                "postgresql+psycopg://..."
            )
        if self.database_schema_mode not in {"create_all", "upgrade", "verify"}:
            raise ValueError(
                "FENGMOU_DATABASE_SCHEMA_MODE must be 'create_all', 'upgrade', or 'verify'"
            )
        if (
            self.database_schema_mode == "create_all"
            and self.environment.lower() not in {"development", "test", "demo", "openapi-export"}
        ):
            raise ValueError(
                "FENGMOU_DATABASE_SCHEMA_MODE=create_all is limited to local/test environments; "
                "deployed environments must use Alembic upgrade/verify"
            )
        if self.max_upload_bytes <= 0:
            raise ValueError("FENGMOU_MAX_UPLOAD_BYTES must be positive")
        if self.design_package_max_upload_bytes <= 0:
            raise ValueError("FENGMOU_DESIGN_PACKAGE_MAX_UPLOAD_BYTES must be positive")
        if self.standard_gpkg_max_upload_bytes <= 0:
            raise ValueError("FENGMOU_STANDARD_GPKG_MAX_UPLOAD_BYTES must be positive")
        if self.gpkg_preview_token_ttl_seconds < 60:
            raise ValueError("FENGMOU_GPKG_PREVIEW_TOKEN_TTL_SECONDS must be >= 60")
        # Cap GPKG / design-package limits to the global upload ceiling (tests often shrink max).
        if self.standard_gpkg_max_upload_bytes > self.max_upload_bytes:
            object.__setattr__(
                self,
                "standard_gpkg_max_upload_bytes",
                self.max_upload_bytes,
            )
        if self.design_package_max_upload_bytes > self.max_upload_bytes:
            object.__setattr__(
                self,
                "design_package_max_upload_bytes",
                self.max_upload_bytes,
            )
        if self.verification_execution_mode not in {"inline", "external"}:
            raise ValueError("FENGMOU_VERIFICATION_EXECUTION_MODE must be 'inline' or 'external'")
        if (
            self.verification_execution_mode == "external"
            and self.database_url.startswith("sqlite")
            and self.environment.lower() not in {"development", "test", "demo"}
        ):
            raise ValueError(
                "External verification workers with SQLite are limited to development/test/demo; "
                "use PostgreSQL for a deployed multi-process worker"
            )
        if not 1 <= self.verification_lease_seconds <= 3600:
            raise ValueError("FENGMOU_VERIFICATION_LEASE_SECONDS must be in [1, 3600]")
        if not 0.1 <= self.verification_heartbeat_seconds < self.verification_lease_seconds:
            raise ValueError(
                "FENGMOU_VERIFICATION_HEARTBEAT_SECONDS must be at least 0.1 and shorter than the lease"
            )
        if not 1 <= self.verification_max_attempts <= 20:
            raise ValueError("FENGMOU_VERIFICATION_MAX_ATTEMPTS must be in [1, 20]")
        if not 0.05 <= self.verification_worker_poll_seconds <= 60:
            raise ValueError("FENGMOU_VERIFICATION_WORKER_POLL_SECONDS must be in [0.05, 60]")
        if not 1 <= self.verification_queue_warning_seconds <= 86400:
            raise ValueError(
                "FENGMOU_VERIFICATION_QUEUE_WARNING_SECONDS must be in [1, 86400]"
            )
        if not 60 <= self.verification_observability_window_seconds <= 604800:
            raise ValueError(
                "FENGMOU_VERIFICATION_OBSERVABILITY_WINDOW_SECONDS must be in [60, 604800]"
            )
        if not self.remote_analyzer_enabled:
            return

        required = {
            "FENGMOU_REMOTE_ANALYZER_URL": self.remote_analyzer_url,
            "FENGMOU_REMOTE_ANALYZER_API_KEY": self.remote_analyzer_api_key,
            "FENGMOU_REMOTE_ANALYZER_MODEL_NAME": self.remote_analyzer_model_name,
            "FENGMOU_REMOTE_ANALYZER_MODEL_VERSION": self.remote_analyzer_model_version,
            "FENGMOU_REMOTE_ANALYZER_MODEL_SHA256": self.remote_analyzer_model_sha256,
        }
        missing = [name for name, value in required.items() if not value]
        if missing:
            raise ValueError("Remote analyzer is enabled but configuration is missing: " + ", ".join(missing))
        if len(self.remote_analyzer_api_key or "") < 16:
            raise ValueError("FENGMOU_REMOTE_ANALYZER_API_KEY must contain at least 16 characters")
        if not re.fullmatch(r"[0-9a-f]{64}", self.remote_analyzer_model_sha256 or ""):
            raise ValueError("FENGMOU_REMOTE_ANALYZER_MODEL_SHA256 must be 64 lowercase hexadecimal characters")
        if self.remote_analyzer_expected_runtime_mode not in {"model", "stub"}:
            raise ValueError("FENGMOU_REMOTE_ANALYZER_EXPECTED_RUNTIME_MODE must be 'model' or 'stub'")
        if (
            self.remote_analyzer_expected_runtime_mode == "stub"
            and self.environment.lower() not in {"test", "demo"}
        ):
            raise ValueError("Remote analyzer STUB mode is only permitted in test or demo environments")
        if not 0 < self.remote_analyzer_timeout_seconds <= 600:
            raise ValueError("FENGMOU_REMOTE_ANALYZER_TIMEOUT_SECONDS must be in (0, 600]")
        if not 0 < self.remote_analyzer_max_upload_bytes <= self.max_upload_bytes:
            raise ValueError("Remote analyzer upload limit must be positive and no larger than the platform limit")
        if self.remote_analyzer_max_response_bytes <= 0:
            raise ValueError("FENGMOU_REMOTE_ANALYZER_MAX_RESPONSE_BYTES must be positive")

        parsed = urlsplit(self.remote_analyzer_url or "")
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("FENGMOU_REMOTE_ANALYZER_URL must be an absolute HTTP(S) URL")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("Remote analyzer URL must not contain credentials, query parameters, or a fragment")
        local_environments = {"development", "test", "demo", "openapi-export"}
        if self.environment.lower() not in local_environments and parsed.scheme != "https":
            raise ValueError("Remote analyzer URL must use HTTPS outside local/demo environments")
        if len(self.remote_analyzer_model_name or "") > 100 or len(self.remote_analyzer_model_version or "") > 100:
            raise ValueError("Remote analyzer model name/version must not exceed 100 characters")

    def require_gpkg_preview_signing_secret_for_deploy(self) -> None:
        """Fail closed at application start for staging/production deployments."""
        env_l = self.environment.lower()
        if env_l in {"development", "test", "demo", "openapi-export"}:
            return
        secret = self.gpkg_preview_signing_secret
        if not secret:
            raise ValueError(
                "FENGMOU_GPKG_PREVIEW_SIGNING_SECRET is required for "
                f"environment={self.environment!r}"
            )
        if len(secret.encode("utf-8")) < 32:
            raise ValueError(
                "FENGMOU_GPKG_PREVIEW_SIGNING_SECRET must be at least 32 bytes"
            )
        weak = {
            "replace-with-a-long-random-gpkg-preview-signing-secret",
            "changeme",
            "secret",
            "password",
            "test",
        }
        if secret.strip().lower() in weak:
            raise ValueError(
                "FENGMOU_GPKG_PREVIEW_SIGNING_SECRET must not use a default placeholder"
            )
        if self.operator_api_key and secret == self.operator_api_key:
            raise ValueError(
                "FENGMOU_GPKG_PREVIEW_SIGNING_SECRET must not reuse the operator API key"
            )

    @classmethod
    def from_env(cls) -> "Settings":
        environment = os.getenv("FENGMOU_ENVIRONMENT", "development")
        database_schema_mode = os.getenv("FENGMOU_DATABASE_SCHEMA_MODE")
        cors = os.getenv("FENGMOU_CORS_ORIGINS", "http://localhost:5173,http://localhost:4173")
        return cls(
            environment=environment,
            database_url=os.getenv("FENGMOU_DATABASE_URL", "sqlite:///./data/fengmou.db"),
            database_schema_mode=(
                database_schema_mode.strip().lower()
                if database_schema_mode is not None
                else _default_schema_mode(environment)
            ),
            storage_root=Path(os.getenv("FENGMOU_STORAGE_ROOT", "./data/storage")),
            max_upload_bytes=int(os.getenv("FENGMOU_MAX_UPLOAD_BYTES", str(500 * 1024 * 1024))),
            design_package_max_upload_bytes=int(
                os.getenv("FENGMOU_DESIGN_PACKAGE_MAX_UPLOAD_BYTES", str(2 * 1024 * 1024))
            ),
            standard_gpkg_max_upload_bytes=int(
                os.getenv("FENGMOU_STANDARD_GPKG_MAX_UPLOAD_BYTES", str(32 * 1024 * 1024))
            ),
            gpkg_preview_token_ttl_seconds=int(
                os.getenv("FENGMOU_GPKG_PREVIEW_TOKEN_TTL_SECONDS", str(15 * 60))
            ),
            gpkg_preview_signing_secret=os.getenv("FENGMOU_GPKG_PREVIEW_SIGNING_SECRET"),
            allow_demo_analyzer=_as_bool(os.getenv("FENGMOU_ALLOW_DEMO_ANALYZER")),
            operator_api_key=os.getenv("FENGMOU_OPERATOR_API_KEY"),
            reviewer_api_key=os.getenv("FENGMOU_REVIEWER_API_KEY"),
            auditor_api_key=os.getenv("FENGMOU_AUDITOR_API_KEY"),
            remote_analyzer_enabled=_as_bool(os.getenv("FENGMOU_REMOTE_ANALYZER_ENABLED")),
            remote_analyzer_url=os.getenv("FENGMOU_REMOTE_ANALYZER_URL"),
            remote_analyzer_api_key=os.getenv("FENGMOU_REMOTE_ANALYZER_API_KEY"),
            remote_analyzer_model_name=os.getenv("FENGMOU_REMOTE_ANALYZER_MODEL_NAME"),
            remote_analyzer_model_version=os.getenv("FENGMOU_REMOTE_ANALYZER_MODEL_VERSION"),
            remote_analyzer_model_sha256=os.getenv("FENGMOU_REMOTE_ANALYZER_MODEL_SHA256"),
            remote_analyzer_expected_runtime_mode=os.getenv(
                "FENGMOU_REMOTE_ANALYZER_EXPECTED_RUNTIME_MODE",
                "model",
            ).strip().lower(),
            remote_analyzer_timeout_seconds=float(
                os.getenv("FENGMOU_REMOTE_ANALYZER_TIMEOUT_SECONDS", "120")
            ),
            remote_analyzer_max_upload_bytes=int(
                os.getenv("FENGMOU_REMOTE_ANALYZER_MAX_UPLOAD_BYTES", str(100 * 1024 * 1024))
            ),
            remote_analyzer_max_response_bytes=int(
                os.getenv("FENGMOU_REMOTE_ANALYZER_MAX_RESPONSE_BYTES", str(2 * 1024 * 1024))
            ),
            verification_execution_mode=os.getenv(
                "FENGMOU_VERIFICATION_EXECUTION_MODE",
                "inline",
            ).strip().lower(),
            verification_lease_seconds=float(
                os.getenv("FENGMOU_VERIFICATION_LEASE_SECONDS", "30")
            ),
            verification_heartbeat_seconds=float(
                os.getenv("FENGMOU_VERIFICATION_HEARTBEAT_SECONDS", "10")
            ),
            verification_max_attempts=int(
                os.getenv("FENGMOU_VERIFICATION_MAX_ATTEMPTS", "3")
            ),
            verification_worker_poll_seconds=float(
                os.getenv("FENGMOU_VERIFICATION_WORKER_POLL_SECONDS", "1")
            ),
            verification_queue_warning_seconds=float(
                os.getenv("FENGMOU_VERIFICATION_QUEUE_WARNING_SECONDS", "60")
            ),
            verification_observability_window_seconds=int(
                os.getenv("FENGMOU_VERIFICATION_OBSERVABILITY_WINDOW_SECONDS", "900")
            ),
            cors_origins=tuple(item.strip() for item in cors.split(",") if item.strip()),
        )
