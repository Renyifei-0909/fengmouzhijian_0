from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import __version__
from .api.router import router
from .config import Settings
from .database import Database
from .services.analysis import recover_pending_verification_jobs, run_verification_job
from .services.remediation import scan_remediation_integrity
from .services.sealing import recover_seal_operations
from .services.storage import FileStorage


def create_app(settings: Settings | None = None) -> FastAPI:
    active_settings = settings or Settings.from_env()
    # Staging/production must have a strong independent GPKG preview signing secret.
    active_settings.require_gpkg_preview_signing_secret_for_deploy()
    database = Database(active_settings.database_url)
    storage = FileStorage(active_settings.storage_root, active_settings.max_upload_bytes)

    @asynccontextmanager
    async def lifespan(app_instance: FastAPI):
        storage.ensure()
        database.prepare_schema(active_settings.database_schema_mode)
        integrity_issues = recover_seal_operations(app_instance)
        integrity_db = database.session_factory()
        try:
            integrity_issues.extend(scan_remediation_integrity(integrity_db, storage))
        finally:
            integrity_db.close()
        app_instance.state.sealing_integrity_issues = integrity_issues
        pending_job_ids = recover_pending_verification_jobs(app_instance)
        recovery_tasks = tuple(
            asyncio.create_task(
                asyncio.to_thread(run_verification_job, app_instance, job_id),
                name=f"recover-verification-{job_id}",
            )
            for job_id in pending_job_ids
        )
        app_instance.state.recovery_tasks = recovery_tasks
        try:
            yield
        finally:
            if recovery_tasks:
                await asyncio.gather(*recovery_tasks, return_exceptions=True)
            database.engine.dispose()

    app = FastAPI(
        title="烽眸智鉴后端 API",
        version=__version__,
        description=(
            "施工证据上传、设计基线对齐、人工复核、结构化报告与可验证哈希证据包。"
            "当前算法层包含安全占位器、显式标注的演示夹具与默认关闭的远程桥，不声明竞赛准确率。"
        ),
        lifespan=lifespan,
    )
    app.state.settings = active_settings
    app.state.database = database
    app.state.storage = storage
    app.state.sealing_integrity_issues = []
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(active_settings.cors_origins),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["Accept-Ranges", "Content-Length", "Content-Range", "ETag"],
    )
    app.include_router(router, prefix="/api/v1")

    @app.get("/", include_in_schema=False)
    def root() -> dict[str, str]:
        return {
            "service": "fengmou-backend",
            "docs": "/docs",
            "health": "/api/v1/healthz",
            "readiness": "/api/v1/readyz",
        }

    return app



app = create_app()
