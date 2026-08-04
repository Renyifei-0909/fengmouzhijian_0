from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any


def probe_media(path: Path, content_type: str) -> dict[str, Any]:
    if not content_type.startswith("video/"):
        return {"probe_status": "skipped", "reason": "Only video probing is implemented in the MVP"}
    executable = shutil.which("ffprobe")
    if executable is None:
        return {"probe_status": "unavailable", "reason": "ffprobe is not installed"}
    command = [
        executable,
        "-v",
        "error",
        "-show_format",
        "-show_streams",
        "-of",
        "json",
        str(path),
    ]
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=20, check=True)
        payload = json.loads(completed.stdout)
        video_stream = next((item for item in payload.get("streams", []) if item.get("codec_type") == "video"), {})
        format_info = payload.get("format", {})
        return {
            "probe_status": "ok",
            "duration_seconds": _float_or_none(format_info.get("duration")),
            "format_name": format_info.get("format_name"),
            "bit_rate": _int_or_none(format_info.get("bit_rate")),
            "video_codec": video_stream.get("codec_name"),
            "width": video_stream.get("width"),
            "height": video_stream.get("height"),
            "frame_rate": video_stream.get("avg_frame_rate"),
        }
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
        return {"probe_status": "failed", "reason": str(exc)[:500]}


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
