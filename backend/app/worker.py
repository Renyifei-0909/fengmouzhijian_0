from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import sys
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Iterator, TextIO

from .config import Settings
from .database import Database
from .file_lock import FileLockBusyError, acquire_exclusive_file_lock, release_file_lock
from .services.analysis import (
    reap_expired_verification_jobs,
    recover_pending_verification_jobs,
    run_next_verification_job,
)
from .services.storage import FileStorage


def create_worker_context(settings: Settings) -> SimpleNamespace:
    database = Database(settings.database_url)
    storage = FileStorage(settings.storage_root, settings.max_upload_bytes)
    storage.ensure()
    database.prepare_schema(settings.database_schema_mode)
    return SimpleNamespace(
        state=SimpleNamespace(settings=settings, database=database, storage=storage)
    )


@contextmanager
def _sqlite_single_worker_lock(settings: Settings) -> Iterator[TextIO | None]:
    if not settings.database_url.startswith("sqlite"):
        yield None
        return
    lock_path = Path(settings.storage_root) / ".verification-worker.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("a+", encoding="utf-8")
    acquired = False
    try:
        try:
            acquire_exclusive_file_lock(handle, nonblocking=True)
            acquired = True
        except FileLockBusyError as exc:
            raise RuntimeError(
                "SQLite external-worker mode permits one local worker process only"
            ) from exc
        handle.seek(0)
        handle.truncate()
        handle.write(str(os.getpid()))
        handle.flush()
        yield handle
    finally:
        try:
            if acquired:
                release_file_lock(handle)
        finally:
            handle.close()


def run_worker(
    settings: Settings,
    *,
    worker_id: str,
    once: bool = False,
    max_jobs: int | None = None,
) -> int:
    if settings.verification_execution_mode != "external":
        raise RuntimeError(
            "Set FENGMOU_VERIFICATION_EXECUTION_MODE=external before starting the independent worker"
        )
    if max_jobs is not None and max_jobs < 1:
        raise ValueError("max_jobs must be positive")
    context = create_worker_context(settings)
    stop = threading.Event()

    def request_stop(_signum: int, _frame: object) -> None:
        stop.set()

    previous_handlers: dict[int, object] = {}
    for signum in (signal.SIGINT, signal.SIGTERM):
        previous_handlers[signum] = signal.getsignal(signum)
        signal.signal(signum, request_stop)
    processed = 0
    try:
        with _sqlite_single_worker_lock(settings):
            recover_pending_verification_jobs(context)
            print(
                json.dumps(
                    {
                        "event": "verification_worker_started",
                        "worker_id": worker_id,
                        "execution_mode": settings.verification_execution_mode,
                        "database": "sqlite" if settings.database_url.startswith("sqlite") else "server",
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
            while not stop.is_set():
                reaped = reap_expired_verification_jobs(context)
                worked = run_next_verification_job(context, worker_id)
                if worked:
                    processed += 1
                if once or (max_jobs is not None and processed >= max_jobs):
                    break
                if not worked and not reaped:
                    stop.wait(settings.verification_worker_poll_seconds)
            print(
                json.dumps(
                    {
                        "event": "verification_worker_stopped",
                        "worker_id": worker_id,
                        "processed_jobs": processed,
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
    finally:
        context.state.database.engine.dispose()
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)
    return processed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the fenced database-polling verification worker."
    )
    parser.add_argument(
        "--worker-id",
        default=f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:12]}",
    )
    parser.add_argument("--once", action="store_true", help="Poll/reap once, then exit.")
    parser.add_argument("--max-jobs", type=int, default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        run_worker(
            Settings.from_env(),
            worker_id=args.worker_id,
            once=args.once,
            max_jobs=args.max_jobs,
        )
    except (RuntimeError, ValueError) as exc:
        print(f"verification worker refused to start: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
