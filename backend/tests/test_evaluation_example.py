from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from app.evaluation import ContractError, score_dataset, validate_dataset


PROJECT_ROOT = Path(__file__).resolve().parents[2]
EXAMPLE_ROOT = PROJECT_ROOT / "examples" / "evaluation-v0-nonformal"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_committed_nonformal_example_validates_and_scores_without_claim_eligibility() -> None:
    dataset = validate_dataset(EXAMPLE_ROOT / "dataset.manifest.json")
    result = score_dataset(
        EXAMPLE_ROOT / "dataset.manifest.json",
        EXAMPLE_ROOT / "runs" / "predictions.validation.jsonl",
        EXAMPLE_ROOT / "model" / "model-statement.json",
        split="validation",
    )

    assert len(dataset.cases) == 2
    assert len(dataset.labels) == 2
    assert dataset.manifest.status == "draft"
    assert dataset.manifest.formal_policy.formal_eligible is False
    assert result["metrics"]["confusion_matrix"] == [[1, 0], [1, 0]]
    assert result["metrics"]["accuracy"]["correct"] == 1
    assert result["metrics"]["accuracy"]["total"] == 2
    assert result["metrics"]["accuracy"]["value"] == 0.5
    assert result["threshold_status"] == "failed"
    assert result["threshold_reasons"] == ["EVAL_THRESHOLD_NOT_MET"]
    assert result["gate_status"] == "not_eligible"
    assert result["compliance_claim_eligible"] is False


def test_committed_nonformal_example_cannot_enter_formal_validation() -> None:
    with pytest.raises(ContractError) as captured:
        validate_dataset(EXAMPLE_ROOT / "dataset.manifest.json", formal=True)

    assert captured.value.code == "EVAL_DATASET_NOT_FORMAL"


def test_committed_nonformal_example_declared_supporting_digests_are_real() -> None:
    manifest = json.loads((EXAMPLE_ROOT / "dataset.manifest.json").read_text(encoding="utf-8"))
    model = json.loads((EXAMPLE_ROOT / "model" / "model-statement.json").read_text(encoding="utf-8"))
    cases = _jsonl(EXAMPLE_ROOT / "public" / "cases.jsonl")
    labels = _jsonl(EXAMPLE_ROOT / "private" / "labels.private.jsonl")

    assert manifest["task"]["label_spec_sha256"] == _sha256(EXAMPLE_ROOT / "public" / "specs" / "label-spec.md")
    assert manifest["task"]["metric_spec_sha256"] == _sha256(
        EXAMPLE_ROOT / "public" / "specs" / "metric-spec.md"
    )
    assert model["artifact_sha256"] == _sha256(EXAMPLE_ROOT / "model" / "constant-label-baseline.json")
    for case in cases:
        assert case["engineering_context"]["baseline_sha256"] == _sha256(
            EXAMPLE_ROOT / "public" / "specs" / "baseline.json"
        )
    for label in labels:
        record = EXAMPLE_ROOT / "private" / "annotations" / f"{label['case_id']}.txt"
        assert label["annotation"]["record_sha256"] == _sha256(record)


def test_committed_nonformal_media_is_real_video_and_segments_fit() -> None:
    ffprobe = shutil.which("ffprobe")
    ffmpeg = shutil.which("ffmpeg")
    assert ffprobe is not None, "ffprobe is required for the canonical video example"
    assert ffmpeg is not None, "ffmpeg is required for full-decode verification"

    for case in _jsonl(EXAMPLE_ROOT / "public" / "cases.jsonl"):
        primary = case["inputs"][0]
        asset = EXAMPLE_ROOT / primary["relative_path"]
        probe = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-count_frames",
                "-show_entries",
                "stream=codec_name,width,height,pix_fmt,r_frame_rate,nb_read_frames",
                "-show_entries",
                "format=duration",
                "-of",
                "json",
                str(asset),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        payload = json.loads(probe.stdout)
        stream = payload["streams"][0]
        duration_ms = round(float(payload["format"]["duration"]) * 1000)

        assert stream == {
            "codec_name": "mpeg4",
            "width": 320,
            "height": 240,
            "pix_fmt": "yuv420p",
            "r_frame_rate": "5/1",
            "nb_read_frames": "5",
        }
        assert duration_ms == 1000
        assert primary["segment"]["end_ms"] <= duration_ms
        subprocess.run(
            [ffmpeg, "-v", "error", "-xerror", "-i", str(asset), "-f", "null", "-"],
            check=True,
            capture_output=True,
            text=True,
        )


def test_fixture_predictor_replays_without_private_files(tmp_path: Path) -> None:
    sandbox = tmp_path / "inference-view"
    shutil.copytree(EXAMPLE_ROOT / "public", sandbox / "public")
    (sandbox / "model").mkdir()
    (sandbox / "tools").mkdir()
    (sandbox / "runs").mkdir()
    shutil.copy2(EXAMPLE_ROOT / "model" / "constant-label-baseline.json", sandbox / "model")
    shutil.copy2(EXAMPLE_ROOT / "tools" / "generate_predictions.py", sandbox / "tools")

    assert not (sandbox / "private").exists()
    generated = sandbox / "runs" / "predictions.validation.jsonl"
    completed = subprocess.run(
        [
            sys.executable,
            str(sandbox / "tools" / "generate_predictions.py"),
            "--cases",
            str(sandbox / "public" / "cases.jsonl"),
            "--model",
            str(sandbox / "model" / "constant-label-baseline.json"),
            "--output",
            str(generated),
        ],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0
    assert completed.stdout == ""
    assert completed.stderr == ""
    assert generated.read_bytes() == (EXAMPLE_ROOT / "runs" / "predictions.validation.jsonl").read_bytes()


def test_public_example_identifiers_do_not_encode_truth_labels() -> None:
    forbidden = ("helmet", "compliant", "missing")
    for case in _jsonl(EXAMPLE_ROOT / "public" / "cases.jsonl"):
        primary = case["inputs"][0]
        exposed_identifiers = (case["case_id"], primary["asset_id"], primary["relative_path"])
        assert all(token not in value.casefold() for token in forbidden for value in exposed_identifiers)


def test_committed_example_cli_exit_contracts_work_from_arbitrary_cwd(tmp_path: Path) -> None:
    script = PROJECT_ROOT / "backend" / "scripts" / "evaluate.py"
    manifest = EXAMPLE_ROOT / "dataset.manifest.json"
    validate = subprocess.run(
        [sys.executable, str(script), "validate", "--manifest", str(manifest)],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )
    formal = subprocess.run(
        [sys.executable, str(script), "validate", "--manifest", str(manifest), "--formal"],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )
    threshold = subprocess.run(
        [
            sys.executable,
            str(script),
            "score",
            "--manifest",
            str(manifest),
            "--predictions",
            str(EXAMPLE_ROOT / "runs" / "predictions.validation.jsonl"),
            "--model-statement",
            str(EXAMPLE_ROOT / "model" / "model-statement.json"),
            "--split",
            "validation",
            "--require-threshold-pass",
        ],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )

    assert validate.returncode == 0
    assert json.loads(validate.stdout)["case_count"] == 2
    assert formal.returncode == 2
    assert json.loads(formal.stdout)["error"]["code"] == "EVAL_DATASET_NOT_FORMAL"
    assert threshold.returncode == 6
    assert json.loads(threshold.stdout)["threshold_status"] == "failed"
