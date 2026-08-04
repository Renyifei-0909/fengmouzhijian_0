from __future__ import annotations

from ...config import Settings
from .base import Analyzer
from .contracts import validate_analyzer_result
from .demo_fixture import DemoFixtureAnalyzer
from .remote_http import RemoteHTTPAnalyzer, remote_adapter_version
from .stub import StubAnalyzer


ANALYZER_DESCRIPTORS = {
    "stub": {
        "version": StubAnalyzer.version,
        "purpose": "Safe placeholder. Produces no physical measurement claims.",
        "synthetic": False,
        "enabled": True,
        "mode": "placeholder",
    },
    "demo_fixture": {
        "version": DemoFixtureAnalyzer.version,
        "purpose": "Synthetic deterministic fixture for UI/API demonstrations only.",
        "synthetic": True,
        "enabled": False,
        "mode": "fixture",
    },
    "remote_http": {
        "version": "remote-http-v1:unconfigured",
        "purpose": "Pinned remote model bridge. Single-input inference is never an accuracy evaluation.",
        "synthetic": False,
        "enabled": False,
        "mode": "remote",
    },
}


def analyzer_descriptor(name: str, *, settings: Settings | None = None) -> dict[str, object]:
    if name not in ANALYZER_DESCRIPTORS:
        raise ValueError(f"Unknown analyzer: {name}")
    descriptor = dict(ANALYZER_DESCRIPTORS[name])
    if name == "demo_fixture":
        descriptor["enabled"] = bool(settings and settings.allow_demo_analyzer)
    elif name == "remote_http":
        enabled = bool(settings and settings.remote_analyzer_enabled)
        descriptor["enabled"] = enabled
        runtime_mode = settings.remote_analyzer_expected_runtime_mode if settings else "model"
        descriptor["runtime_mode"] = runtime_mode
        descriptor["synthetic"] = runtime_mode == "stub"
        if enabled and settings:
            descriptor["version"] = remote_adapter_version(
                settings.remote_analyzer_url or "",
                settings.remote_analyzer_model_name or "",
                settings.remote_analyzer_model_version or "",
                settings.remote_analyzer_model_sha256 or "",
                runtime_mode,
            )
    return descriptor


def build_analyzer(
    name: str,
    *,
    settings: Settings,
    job_id: str,
    pinned_version: str,
) -> Analyzer:
    if name == "stub":
        analyzer: Analyzer = StubAnalyzer()
    elif name == "demo_fixture":
        if not settings.allow_demo_analyzer:
            raise RuntimeError("The demo_fixture analyzer is disabled by configuration")
        analyzer = DemoFixtureAnalyzer()
    elif name == "remote_http":
        if not settings.remote_analyzer_enabled:
            raise RuntimeError("The remote_http analyzer is disabled by configuration")
        analyzer = RemoteHTTPAnalyzer(
            url=settings.remote_analyzer_url or "",
            api_key=settings.remote_analyzer_api_key,
            model_name=settings.remote_analyzer_model_name or "",
            model_version=settings.remote_analyzer_model_version or "",
            expected_model_sha256=settings.remote_analyzer_model_sha256 or "",
            expected_runtime_mode=settings.remote_analyzer_expected_runtime_mode,
            job_id=job_id,
            timeout_seconds=settings.remote_analyzer_timeout_seconds,
            max_upload_bytes=settings.remote_analyzer_max_upload_bytes,
            max_response_bytes=settings.remote_analyzer_max_response_bytes,
        )
    else:
        raise ValueError(f"Unknown analyzer: {name}")
    if analyzer.version != pinned_version:
        raise RuntimeError(
            "Analyzer configuration changed after the job was queued; create a new job with the new version"
        )
    return analyzer


__all__ = [
    "ANALYZER_DESCRIPTORS",
    "Analyzer",
    "analyzer_descriptor",
    "build_analyzer",
    "validate_analyzer_result",
]
