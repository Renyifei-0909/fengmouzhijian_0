"""Observe multi-worker claim contention on an explicit acceptance PostgreSQL URL.

This script does NOT implement SKIP LOCKED. It only measures how the current
atomic UPDATE + generation fencing behaves under a short multi-process wave so
future queue changes can be justified with numbers instead of intuition.

Safety is deliberately shared with postgres_acceptance:
loopback-only, fengmou_acceptance DB, temporary owned schema, no silent skip.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from time import perf_counter
from typing import Any
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import select

# Direct script execution puts backend/scripts on sys.path; ensure backend root.
BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from scripts.postgres_acceptance import (  # noqa: E402
    DATABASE_URL_ENV,
    AcceptanceError,
    AcceptanceRefusal,
    _acceptance_settings,
    _create_project_and_baseline,
    _enqueue_job,
    _new_schema_name,
    _redact_process_error,
    _worker_environment,
    create_isolated_schema,
    drop_isolated_schema,
    inspect_server,
    run_migration_acceptance,
    scoped_database_url,
    validate_run_shape,
    validate_target_url,
    _base_database,
)
from app.main import create_app  # noqa: E402
from app.models import (  # noqa: E402
    VerificationAttempt,
    VerificationJob,
    VerificationJobLease,
)


@dataclass(frozen=True, slots=True)
class WaveReport:
    wave: int
    workers_launched: int
    processed_jobs: int
    idle_workers: int
    elapsed_ms: float


def _run_once_wave(
    settings: Any,
    *,
    workers: int,
    wave: int,
) -> WaveReport:
    environment = _worker_environment(settings)
    processes: list[tuple[str, subprocess.Popen[str]]] = []
    started = perf_counter()
    for index in range(workers):
        worker_id = f"pg-contention-{wave:02d}-{index:02d}-{uuid.uuid4().hex[:8]}"
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

    processed = 0
    idle = 0
    for worker_id, process in processes:
        try:
            stdout, stderr = process.communicate(timeout=60)
        except subprocess.TimeoutExpired as exc:
            process.kill()
            process.communicate()
            raise AcceptanceError(
                f"worker {worker_id} exceeded the contention observe timeout"
            ) from exc
        if process.returncode != 0:
            detail = _redact_process_error(stderr or stdout, settings)
            raise AcceptanceError(
                f"worker {worker_id} exited {process.returncode}: {detail}"
            )
        got_job = False
        for line in stdout.splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if (
                isinstance(event, dict)
                and event.get("event") == "verification_worker_stopped"
            ):
                value = event.get("processed_jobs")
                if value == 1:
                    got_job = True
                elif value == 0:
                    got_job = False
                else:
                    raise AcceptanceError(
                        f"worker {worker_id} returned invalid processed_jobs"
                    )
        if got_job:
            processed += 1
        else:
            idle += 1
    return WaveReport(
        wave=wave,
        workers_launched=workers,
        processed_jobs=processed,
        idle_workers=idle,
        elapsed_ms=round((perf_counter() - started) * 1000, 3),
    )


def observe_contention(
    *,
    jobs: int,
    workers: int,
    waves: int,
) -> dict[str, object]:
    validate_run_shape(jobs=jobs, workers=workers)
    if not 1 <= waves <= 16:
        raise AcceptanceRefusal("--waves must be in [1, 16]")

    raw = os.getenv(DATABASE_URL_ENV)
    target = validate_target_url(raw)
    control = _base_database(target)
    schema = _new_schema_name()
    created = False
    primary_error: BaseException | None = None
    report: dict[str, object] | None = None
    cleanup_error: BaseException | None = None
    try:
        server = inspect_server(control, target)
        create_isolated_schema(control, schema)
        created = True
        scoped_url = scoped_database_url(target, schema)
        migration = run_migration_acceptance(scoped_url)
        with tempfile.TemporaryDirectory(prefix="fengmou-pg-contention-") as temporary_root:
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
                    project, baseline = _create_project_and_baseline(
                        client, schema=schema
                    )
                    job_ids = [
                        _enqueue_job(client, project, baseline, index=index)
                        for index in range(jobs)
                    ]
                    wave_reports: list[WaveReport] = []
                    remaining = jobs
                    for wave in range(waves):
                        if remaining <= 0:
                            break
                        wave_report = _run_once_wave(
                            settings, workers=workers, wave=wave
                        )
                        wave_reports.append(wave_report)
                        remaining = max(0, remaining - wave_report.processed_jobs)
                        if wave_report.processed_jobs == 0:
                            break

                    with app.state.database.session_factory() as database:
                        jobs_rows = list(
                            database.scalars(
                                select(VerificationJob).where(
                                    VerificationJob.id.in_(job_ids)
                                )
                            ).all()
                        )
                        attempts = list(
                            database.scalars(
                                select(VerificationAttempt).where(
                                    VerificationAttempt.job_id.in_(job_ids)
                                )
                            ).all()
                        )
                        leases = list(
                            database.scalars(
                                select(VerificationJobLease).where(
                                    VerificationJobLease.job_id.in_(job_ids)
                                )
                            ).all()
                        )

                    status_counts: dict[str, int] = {}
                    for job in jobs_rows:
                        status_counts[str(job.status)] = (
                            status_counts.get(str(job.status), 0) + 1
                        )
                    generation_counts: dict[int, int] = {}
                    for lease in leases:
                        generation_counts[int(lease.generation)] = (
                            generation_counts.get(int(lease.generation), 0) + 1
                        )
                    attempts_per_job: dict[str, int] = {}
                    for attempt in attempts:
                        attempts_per_job[attempt.job_id] = (
                            attempts_per_job.get(attempt.job_id, 0) + 1
                        )
                    multi_attempt_jobs = sum(
                        1 for count in attempts_per_job.values() if count > 1
                    )
                    total_processed = sum(
                        item.processed_jobs for item in wave_reports
                    )
                    total_idle = sum(item.idle_workers for item in wave_reports)
                    # Heuristic only: idle workers in early waves while queue
                    # still had jobs indicate contending once-slots without work.
                    early_idle_while_queued = 0
                    completed_so_far = 0
                    for item in wave_reports:
                        still_queued_before = jobs - completed_so_far
                        if still_queued_before > 0:
                            early_idle_while_queued += item.idle_workers
                        completed_so_far += item.processed_jobs

                    report = {
                        "ok": True,
                        "purpose": (
                            "contention observation only; not a SKIP LOCKED "
                            "implementation and not a capacity SLA"
                        ),
                        "target": target.public_dict(),
                        "server": server.public_dict(),
                        "schema": {
                            "name": schema,
                            "isolation": "per-run PostgreSQL schema/search_path",
                        },
                        "migration": migration,
                        "shape": {
                            "jobs": jobs,
                            "workers_per_wave": workers,
                            "waves_requested": waves,
                            "waves_executed": len(wave_reports),
                        },
                        "waves": [asdict(item) for item in wave_reports],
                        "totals": {
                            "processed_job_slots": total_processed,
                            "idle_worker_slots": total_idle,
                            "idle_while_queue_nonempty": early_idle_while_queued,
                            "jobs_terminal_needs_review": status_counts.get(
                                "needs_review", 0
                            ),
                            "status_counts": status_counts,
                            "attempt_rows": len(attempts),
                            "jobs_with_multiple_attempts": multi_attempt_jobs,
                            "lease_generation_histogram": {
                                str(key): value
                                for key, value in sorted(generation_counts.items())
                            },
                        },
                        "skip_locked_decision": {
                            "implemented": False,
                            "reason": (
                                "current atomic claim + generation fencing remains "
                                "the correctness baseline; SKIP LOCKED requires "
                                "sustained contention evidence, FIFO/fairness review, "
                                "and a real PostgreSQL design pass"
                            ),
                        },
                    }
            finally:
                app.state.database.engine.dispose()
    except BaseException as exc:
        primary_error = exc
    finally:
        if created:
            try:
                drop_isolated_schema(control, schema)
            except BaseException as exc:
                cleanup_error = exc
        control.engine.dispose()

    if primary_error is not None:
        if cleanup_error is not None:
            raise AcceptanceError(
                "contention observe failed and schema cleanup also failed "
                f"({type(cleanup_error).__name__}); schema={schema}"
            ) from primary_error
        raise primary_error
    if cleanup_error is not None:
        raise AcceptanceError(
            "contention observe passed but schema cleanup failed "
            f"({type(cleanup_error).__name__}); schema={schema}"
        ) from cleanup_error
    if report is None:
        raise AcceptanceError("contention observe ended without a report")
    report["schema"] = {
        **dict(report["schema"]),  # type: ignore[arg-type]
        "cleanup": "dropped",
    }
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Observe multi-worker claim contention against an explicit loopback "
            f"fengmou_acceptance database (URL from {DATABASE_URL_ENV} only)."
        )
    )
    parser.add_argument("--jobs", type=int, default=16)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--waves", type=int, default=4)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = observe_contention(
            jobs=args.jobs,
            workers=args.workers,
            waves=args.waves,
        )
    except AcceptanceRefusal as exc:
        print(f"postgres contention observe refused: {exc}", file=sys.stderr)
        return 2
    except AcceptanceError as exc:
        print(f"postgres contention observe failed: {exc}", file=sys.stderr)
        return 1
    except Exception:
        print(
            "postgres contention observe failed with an unexpected exception; "
            "no database URL was printed",
            file=sys.stderr,
        )
        return 1
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
