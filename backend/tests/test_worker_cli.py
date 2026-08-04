from __future__ import annotations

from pathlib import Path

import pytest

from app.config import Settings
from app.worker import _sqlite_single_worker_lock, main, run_worker


def test_worker_refuses_inline_execution_mode(tmp_path: Path) -> None:
    settings = Settings(
        environment="test",
        database_url=f"sqlite:///{tmp_path / 'inline.db'}",
        storage_root=tmp_path / "storage",
    )
    with pytest.raises(RuntimeError, match="EXECUTION_MODE=external"):
        run_worker(settings, worker_id="test-worker", once=True)


def test_worker_rejects_nonpositive_job_limit_before_opening_database(tmp_path: Path) -> None:
    settings = Settings(
        environment="test",
        database_url=f"sqlite:///{tmp_path / 'external.db'}",
        storage_root=tmp_path / "storage",
        verification_execution_mode="external",
    )
    with pytest.raises(ValueError, match="max_jobs must be positive"):
        run_worker(settings, worker_id="test-worker", max_jobs=0)


def test_worker_main_returns_nonzero_with_actionable_startup_error(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("FENGMOU_VERIFICATION_EXECUTION_MODE", "inline")
    assert main(["--once"]) == 2
    assert "refused to start" in capsys.readouterr().err


def test_server_database_does_not_use_the_sqlite_process_lock() -> None:
    settings = Settings(
        environment="test",
        database_url="postgresql+psycopg://db/fengmou",
    )
    with _sqlite_single_worker_lock(settings) as handle:
        assert handle is None
