from __future__ import annotations

from collections.abc import Generator
import shutil
import subprocess

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

# Register failed-node capture plugin for full-suite baseline runs.
pytest_plugins = ["tests.pytest_failed_nodes_plugin"]


@pytest.fixture()
def client(tmp_path) -> Generator[TestClient, None, None]:
    settings = Settings(
        environment="test",
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        database_schema_mode="create_all",
        storage_root=tmp_path / "storage",
        max_upload_bytes=2 * 1024 * 1024,
        allow_demo_analyzer=True,
        operator_api_key="test-operator-key",
        reviewer_api_key="test-reviewer-key",
        auditor_api_key="test-auditor-key",
        gpkg_preview_signing_secret="test-gpkg-preview-signing-secret-32b!",
        cors_origins=("http://testserver",),
    )
    with TestClient(create_app(settings)) as test_client:
        test_client.headers.update({"X-API-Key": "test-operator-key"})
        yield test_client


@pytest.fixture()
def valid_mp4_bytes(tmp_path) -> bytes:
    executable = shutil.which("ffmpeg")
    if executable is None:
        pytest.skip("ffmpeg is required for the media-ingestion integration test")
    path = tmp_path / "sample.mp4"
    subprocess.run(
        [
            executable,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=64x64:d=0.2",
            "-c:v",
            "mpeg4",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ],
        check=True,
    )
    return path.read_bytes()


@pytest.fixture()
def project_and_baseline(client: TestClient) -> tuple[dict, dict]:
    project_response = client.post(
        "/api/v1/projects",
        json={
            "code": "XM-001",
            "name": "通信管线隐蔽工程",
            "location": "匿名化测试工点",
            "manager": "测试审核员",
        },
    )
    assert project_response.status_code == 201, project_response.text
    project = project_response.json()
    baseline_response = client.post(
        f"/api/v1/projects/{project['id']}/baselines",
        json={
            "site_id": "SITE-A12",
            "procedure_code": "TRENCH-BEFORE-BACKFILL",
            "version": "design-v1",
            "source_type": "manual",
            "expected": {
                "scene_type": "trench",
                "measurements": {
                    "min_depth_m": 0.8,
                    "min_spacing_m": 0.2,
                    "expected_quantity": 4,
                    "expected_specification": "PE110 x 4",
                },
            },
        },
    )
    assert baseline_response.status_code == 201, baseline_response.text
    return project, baseline_response.json()
