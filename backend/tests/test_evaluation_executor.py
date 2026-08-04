from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time
from typing import Any

import pytest

resource = pytest.importorskip("resource")

import app.evaluation.executor as executor_module
from app.evaluation import ContractError, ExecutionError, IntegrityError
from app.evaluation.executor import evaluator_source_sha256, run_development_plan
from app.evaluation.supervisor import tightened_resource_limit


PROJECT_ROOT = Path(__file__).resolve().parents[2]
EXAMPLE_ROOT = PROJECT_ROOT / "examples" / "evaluation-v0-nonformal"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )


def _write_jsonl(path: Path, values: list[dict[str, Any]]) -> None:
    path.write_text(
        "".join(json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n" for value in values),
        encoding="utf-8",
    )


def _copy_example(tmp_path: Path) -> Path:
    root = tmp_path / "bundle"
    shutil.copytree(EXAMPLE_ROOT, root)
    return root


def _load_plan(root: Path) -> dict[str, Any]:
    return json.loads((root / "run-plan.json").read_text(encoding="utf-8"))


def _bind_artifact(plan: dict[str, Any], key: str, root: Path) -> None:
    path = root / plan[key]["path"]
    plan[key]["sha256"] = _sha256(path)
    plan[key]["size_bytes"] = path.stat().st_size


def _write_plan(root: Path, plan: dict[str, Any]) -> Path:
    plan["evaluator_source_sha256"] = evaluator_source_sha256()
    path = root / "run-plan.json"
    _write_json(path, plan)
    return path


def _replace_entrypoint(root: Path, source: str, *, timeout_seconds: int | None = None) -> Path:
    entrypoint = root / "tools" / "generate_predictions.py"
    entrypoint.write_text(source, encoding="utf-8")
    plan = _load_plan(root)
    if timeout_seconds is not None:
        plan["timeout_seconds"] = timeout_seconds
    _bind_artifact(plan, "entrypoint", root)
    return _write_plan(root, plan)


@pytest.mark.parametrize(
    ("inherited", "requested", "expected"),
    [
        ((1024, 1024), (2048, 2048), (1024, 1024)),
        ((128, 512), (256, 256), (128, 256)),
        ((256, 512), (128, 129), (128, 129)),
        (
            (resource.RLIM_INFINITY, resource.RLIM_INFINITY),
            (2048, 2048),
            (2048, 2048),
        ),
        (
            (64, resource.RLIM_INFINITY),
            (resource.RLIM_INFINITY, resource.RLIM_INFINITY),
            (64, resource.RLIM_INFINITY),
        ),
    ],
)
def test_supervisor_resource_limit_is_strictly_tightened(
    inherited: tuple[int, int],
    requested: tuple[int, int],
    expected: tuple[int, int],
) -> None:
    assert tightened_resource_limit(inherited, requested) == expected


def test_supervisor_rejects_invalid_requested_limit() -> None:
    with pytest.raises(ValueError):
        tightened_resource_limit((256, 256), (257, 256))


def test_canonical_development_run_executes_public_fixture_and_scores() -> None:
    plan = EXAMPLE_ROOT / "run-plan.json"
    result = run_development_plan(plan, expected_run_plan_sha256=_sha256(plan))

    assert result["ok"] is True
    assert result["mode"] == "development"
    assert result["formal_requested"] is False
    assert result["gate_status"] == "not_eligible"
    assert result["compliance_claim_eligible"] is False
    assert result["inference_view"]["private_labels_copied"] is False
    assert result["inference_view"]["case_count"] == 2
    assert result["inference_view"]["asset_count"] == 2
    assert result["process"]["return_code"] == 0
    assert result["score"]["metrics"]["accuracy"]["value"] == 0.5
    assert result["score"]["threshold_status"] == "failed"


def test_external_run_plan_pin_mismatch_rejects_before_execution() -> None:
    with pytest.raises(IntegrityError) as captured:
        run_development_plan(EXAMPLE_ROOT / "run-plan.json", expected_run_plan_sha256="0" * 64)

    assert captured.value.code == "EVAL_RUN_PLAN_IDENTITY_MISMATCH"


def test_unsupported_secure_open_platform_is_reported_before_artifact_staging(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _copy_example(tmp_path)
    plan_path = _write_plan(root, _load_plan(root))
    monkeypatch.setattr(executor_module.os, "supports_dir_fd", set())

    with pytest.raises(ExecutionError) as captured:
        run_development_plan(plan_path, expected_run_plan_sha256=_sha256(plan_path))

    assert captured.value.code == "EVAL_RUN_PLATFORM_UNSUPPORTED"
    assert "secure_open_dir_fd" in captured.value.details["missing_capabilities"]


def test_temporary_workspace_oserror_is_machine_readable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _copy_example(tmp_path)
    plan_path = _write_plan(root, _load_plan(root))

    def fail_temporary_directory(*args: Any, **kwargs: Any) -> None:
        raise OSError(28, "simulated no space for temporary workspace")

    monkeypatch.setattr(executor_module.tempfile, "TemporaryDirectory", fail_temporary_directory)

    with pytest.raises(ExecutionError) as captured:
        run_development_plan(plan_path, expected_run_plan_sha256=_sha256(plan_path))

    assert captured.value.code == "EVAL_RUN_LOCAL_IO_FAILED"
    assert captured.value.details["errno"] == 28


def test_sandbox_directory_oserror_is_machine_readable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _copy_example(tmp_path)
    plan_path = _write_plan(root, _load_plan(root))
    original_mkdir = Path.mkdir

    def fail_inference_directory(path: Path, *args: Any, **kwargs: Any) -> None:
        if path.name == "inference" and path.parent.name.startswith("fengmou-"):
            raise OSError(13, "simulated sandbox directory denial")
        original_mkdir(path, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", fail_inference_directory)

    with pytest.raises(ExecutionError) as captured:
        run_development_plan(plan_path, expected_run_plan_sha256=_sha256(plan_path))

    assert captured.value.code == "EVAL_RUN_LOCAL_IO_FAILED"
    assert captured.value.details["errno"] == 13


def test_trusted_predictions_oserror_is_machine_readable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _copy_example(tmp_path)
    plan_path = _write_plan(root, _load_plan(root))
    original_open = Path.open

    def fail_trusted_predictions(path: Path, *args: Any, **kwargs: Any):
        if path.name == "predictions.jsonl" and path.parent.name == "trusted":
            raise OSError(28, "simulated trusted predictions write failure")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", fail_trusted_predictions)

    with pytest.raises(ExecutionError) as captured:
        run_development_plan(plan_path, expected_run_plan_sha256=_sha256(plan_path))

    assert captured.value.code == "EVAL_RUN_LOCAL_IO_FAILED"
    assert captured.value.details["errno"] == 28


@pytest.mark.parametrize(("field", "value"), [("formal_requested", True), ("split", "final_holdout")])
def test_formal_or_holdout_plan_is_rejected_before_entrypoint(
    tmp_path: Path,
    field: str,
    value: Any,
) -> None:
    root = _copy_example(tmp_path)
    plan = _load_plan(root)
    plan[field] = value
    plan_path = _write_plan(root, plan)

    with pytest.raises(ContractError) as captured:
        run_development_plan(plan_path, expected_run_plan_sha256=_sha256(plan_path))

    assert captured.value.code == "EVAL_SCHEMA_INVALID"


def test_tampered_model_artifact_is_rejected_before_entrypoint(tmp_path: Path) -> None:
    root = _copy_example(tmp_path)
    artifact = root / "model" / "constant-label-baseline.json"
    artifact.write_text('{"tampered":true}\n', encoding="utf-8")
    plan_path = root / "run-plan.json"

    with pytest.raises(IntegrityError) as captured:
        run_development_plan(plan_path, expected_run_plan_sha256=_sha256(plan_path))

    assert captured.value.code in {"EVAL_RUN_ARTIFACT_SIZE_MISMATCH", "EVAL_RUN_ARTIFACT_HASH_MISMATCH"}


def test_training_manifest_must_bind_the_model_artifact(tmp_path: Path) -> None:
    root = _copy_example(tmp_path)
    training = root / "model" / "training-data-manifest.json"
    _write_json(
        training,
        {
            "schema_version": "evaluation.training-manifest.v0",
            "model_artifact_sha256": "0" * 64,
        },
    )
    plan = _load_plan(root)
    _bind_artifact(plan, "training_data_manifest", root)
    plan_path = _write_plan(root, plan)

    with pytest.raises(IntegrityError) as captured:
        run_development_plan(plan_path, expected_run_plan_sha256=_sha256(plan_path))

    assert captured.value.code == "EVAL_RUN_TRAINING_BINDING_MISMATCH"


def test_entrypoint_runs_with_minimal_environment_and_no_private_view(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _copy_example(tmp_path)
    entrypoint = root / "tools" / "generate_predictions.py"
    entrypoint.write_text(
        """import argparse
import json
import os
from pathlib import Path

p = argparse.ArgumentParser()
p.add_argument("--cases")
p.add_argument("--model")
p.add_argument("--output")
p.add_argument("--seed")
a = p.parse_args()
assert not (Path.cwd() / "private").exists()
assert not (Path.cwd() / "dataset.manifest.json").exists()
assert not (Path.cwd() / "model" / "model-statement.json").exists()
assert not (Path.cwd() / "model" / "training-data-manifest.json").exists()
assert all(key not in os.environ for key in ["FENGMOU_OPERATOR_API_KEY", "HTTPS_PROXY", "PYTHONPATH"])
with open(a.model, encoding="utf-8") as model_handle:
    model = json.load(model_handle)
with open(a.output, "w", encoding="utf-8", newline="\\n") as output_handle:
    with open(a.cases, encoding="utf-8") as cases_handle:
        for line in cases_handle:
            case = json.loads(line)
            result = {
                "schema_version": "evaluation.prediction.v0",
                "case_id": case["case_id"],
                "output": {
                    "kind": "violation_single_label",
                    "label": model["constant_label"],
                },
            }
            output_handle.write(json.dumps(result, separators=(",", ":")) + "\\n")
""",
        encoding="utf-8",
    )
    plan = _load_plan(root)
    _bind_artifact(plan, "entrypoint", root)
    plan_path = _write_plan(root, plan)
    monkeypatch.setenv("FENGMOU_OPERATOR_API_KEY", "must-not-leak")
    monkeypatch.setenv("HTTPS_PROXY", "http://must-not-leak.invalid")
    monkeypatch.setenv("PYTHONPATH", "/must/not/leak")

    result = run_development_plan(plan_path, expected_run_plan_sha256=_sha256(plan_path))

    assert result["process"]["return_code"] == 0
    assert result["score"]["metrics"]["accuracy"]["value"] == 0.5


def test_nonpublic_case_asset_is_rejected_before_entrypoint(tmp_path: Path) -> None:
    root = _copy_example(tmp_path)
    private_asset = root / "private" / "event-001.mp4"
    shutil.copy2(root / "public" / "assets" / "event-001.mp4", private_asset)
    cases_path = root / "public" / "cases.jsonl"
    cases = [json.loads(line) for line in cases_path.read_text(encoding="utf-8").splitlines()]
    cases[0]["inputs"][0]["relative_path"] = "private/event-001.mp4"
    _write_jsonl(cases_path, cases)
    manifest_path = root / "dataset.manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["artifacts"]["cases"]["sha256"] = _sha256(cases_path)
    manifest["artifacts"]["cases"]["size_bytes"] = cases_path.stat().st_size
    _write_json(manifest_path, manifest)
    plan = _load_plan(root)
    _bind_artifact(plan, "dataset_manifest", root)
    plan_path = _write_plan(root, plan)

    with pytest.raises(ContractError) as captured:
        run_development_plan(plan_path, expected_run_plan_sha256=_sha256(plan_path))

    assert captured.value.code == "EVAL_RUN_NONPUBLIC_ASSET_FORBIDDEN"


def test_development_entrypoint_timeout_is_execution_error(tmp_path: Path) -> None:
    root = _copy_example(tmp_path)
    entrypoint = root / "tools" / "generate_predictions.py"
    entrypoint.write_text("import time\ntime.sleep(30)\n", encoding="utf-8")
    plan = _load_plan(root)
    plan["timeout_seconds"] = 1
    _bind_artifact(plan, "entrypoint", root)
    plan_path = _write_plan(root, plan)
    started = time.monotonic()

    with pytest.raises(ExecutionError) as captured:
        run_development_plan(plan_path, expected_run_plan_sha256=_sha256(plan_path))

    assert captured.value.code == "EVAL_RUN_TIMEOUT"
    assert time.monotonic() - started < 4


def test_timeout_is_preserved_when_cleanup_reports_an_operational_issue(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _copy_example(tmp_path)
    plan_path = _replace_entrypoint(root, "import time\ntime.sleep(30)\n", timeout_seconds=1)
    original_cleanup = executor_module._terminate_process_group

    def cleanup_with_issue(process: subprocess.Popen) -> list[dict[str, int | str]]:
        original_cleanup(process)
        return [{"operation": "simulated_cleanup", "errno": 5}]

    monkeypatch.setattr(executor_module, "_terminate_process_group", cleanup_with_issue)

    with pytest.raises(ExecutionError) as captured:
        run_development_plan(plan_path, expected_run_plan_sha256=_sha256(plan_path))

    assert captured.value.code == "EVAL_RUN_TIMEOUT"
    assert captured.value.details["cleanup_issues"][0]["operation"] == "simulated_cleanup"


def test_successful_process_with_cleanup_issue_is_not_misreported_as_start_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _copy_example(tmp_path)
    plan_path = _write_plan(root, _load_plan(root))
    original_cleanup = executor_module._terminate_process_group

    def cleanup_with_issue(process: subprocess.Popen) -> list[dict[str, int | str]]:
        original_cleanup(process)
        return [{"operation": "simulated_cleanup", "errno": 5}]

    monkeypatch.setattr(executor_module, "_terminate_process_group", cleanup_with_issue)

    with pytest.raises(ExecutionError) as captured:
        run_development_plan(plan_path, expected_run_plan_sha256=_sha256(plan_path))

    assert captured.value.code == "EVAL_RUN_PROCESS_CLEANUP_FAILED"


def test_development_entrypoint_nonzero_exit_is_execution_error(tmp_path: Path) -> None:
    root = _copy_example(tmp_path)
    plan_path = _replace_entrypoint(root, "raise SystemExit(7)\n")

    with pytest.raises(ExecutionError) as captured:
        run_development_plan(plan_path, expected_run_plan_sha256=_sha256(plan_path))

    assert captured.value.code == "EVAL_RUN_PROCESS_FAILED"
    assert captured.value.details["return_code"] == 7
    assert captured.value.details["stdout"]["size_bytes"] == 0


def test_development_entrypoint_cannot_substitute_predictions_symlink(tmp_path: Path) -> None:
    root = _copy_example(tmp_path)
    plan_path = _replace_entrypoint(
        root,
        """import argparse
import os

p = argparse.ArgumentParser()
p.add_argument("--cases")
p.add_argument("--model")
p.add_argument("--output")
p.add_argument("--seed")
a = p.parse_args()
os.symlink(a.cases, a.output)
""",
    )

    with pytest.raises(ExecutionError) as captured:
        run_development_plan(plan_path, expected_run_plan_sha256=_sha256(plan_path))

    assert captured.value.code == "EVAL_RUN_PREDICTIONS_INVALID"
    assert captured.value.details["cause"] == "EVAL_FILE_NOT_REGULAR"


def test_development_entrypoint_invalid_prediction_contract_is_execution_error(tmp_path: Path) -> None:
    root = _copy_example(tmp_path)
    plan_path = _replace_entrypoint(
        root,
        """import argparse

p = argparse.ArgumentParser()
p.add_argument("--cases")
p.add_argument("--model")
p.add_argument("--output")
p.add_argument("--seed")
a = p.parse_args()
with open(a.output, "w", encoding="utf-8") as output_handle:
    output_handle.write("{}\\n")
""",
    )

    with pytest.raises(ExecutionError) as captured:
        run_development_plan(plan_path, expected_run_plan_sha256=_sha256(plan_path))

    assert captured.value.code == "EVAL_RUN_PREDICTIONS_INVALID"
    assert captured.value.details["cause"] == "EVAL_SCHEMA_INVALID"


def test_unscoreable_dataset_contract_is_not_misreported_as_prediction_failure(tmp_path: Path) -> None:
    root = _copy_example(tmp_path)
    manifest_path = root / "dataset.manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["task"]["minimum_cases_total"] = 3
    _write_json(manifest_path, manifest)
    plan = _load_plan(root)
    _bind_artifact(plan, "dataset_manifest", root)
    plan_path = _write_plan(root, plan)

    with pytest.raises(ContractError) as captured:
        run_development_plan(plan_path, expected_run_plan_sha256=_sha256(plan_path))

    assert captured.value.code == "EVAL_SAMPLE_MINIMUM_NOT_MET"


def test_development_runner_terminates_background_process_group(tmp_path: Path) -> None:
    root = _copy_example(tmp_path)
    heartbeat = tmp_path / "background-heartbeat.txt"
    background_source = (
        "import pathlib,time\n"
        f"p=pathlib.Path({str(heartbeat)!r})\n"
        "while True:\n"
        "    with p.open('a', encoding='utf-8') as h: h.write('tick\\n')\n"
        "    time.sleep(0.02)\n"
    )
    plan_path = _replace_entrypoint(
        root,
        f"""import argparse
import json
import subprocess
import sys
import time

p = argparse.ArgumentParser()
p.add_argument("--cases")
p.add_argument("--model")
p.add_argument("--output")
p.add_argument("--seed")
a = p.parse_args()
subprocess.Popen([sys.executable, "-I", "-B", "-c", {background_source!r}])
time.sleep(0.12)
with open(a.model, encoding="utf-8") as model_handle:
    model = json.load(model_handle)
with open(a.output, "w", encoding="utf-8", newline="\\n") as output_handle:
    with open(a.cases, encoding="utf-8") as cases_handle:
        for line in cases_handle:
            case = json.loads(line)
            output_handle.write(json.dumps({{
                "schema_version": "evaluation.prediction.v0",
                "case_id": case["case_id"],
                "output": {{
                    "kind": "violation_single_label",
                    "label": model["constant_label"],
                }},
            }}, separators=(",", ":")) + "\\n")
""",
    )

    result = run_development_plan(plan_path, expected_run_plan_sha256=_sha256(plan_path))

    assert result["process"]["return_code"] == 0
    assert heartbeat.exists()
    first_size = heartbeat.stat().st_size
    time.sleep(0.25)
    assert heartbeat.stat().st_size == first_size


def test_development_runner_terminates_process_group_when_parent_is_interrupted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _copy_example(tmp_path)
    heartbeat = tmp_path / "interrupted-heartbeat.txt"
    plan_path = _replace_entrypoint(
        root,
        f"""import time
from pathlib import Path

p = Path({str(heartbeat)!r})
while True:
    with p.open("a", encoding="utf-8") as handle:
        handle.write("tick\\n")
    time.sleep(0.02)
""",
    )
    original_wait = subprocess.Popen.wait
    interrupt_pending = True

    def interrupt_first_wait(process: subprocess.Popen, timeout: float | None = None) -> int:
        nonlocal interrupt_pending
        if interrupt_pending:
            deadline = time.monotonic() + 1
            while not heartbeat.exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            interrupt_pending = False
            raise KeyboardInterrupt
        return original_wait(process, timeout=timeout)

    monkeypatch.setattr(subprocess.Popen, "wait", interrupt_first_wait)

    with pytest.raises(KeyboardInterrupt):
        run_development_plan(plan_path, expected_run_plan_sha256=_sha256(plan_path))

    assert heartbeat.exists()
    first_size = heartbeat.stat().st_size
    time.sleep(0.25)
    assert heartbeat.stat().st_size == first_size


def test_development_runner_detects_original_artifact_mutation_after_process(
    tmp_path: Path,
) -> None:
    root = _copy_example(tmp_path)
    original_model = root / "model" / "constant-label-baseline.json"
    plan_path = _replace_entrypoint(
        root,
        f"""import argparse
import json
from pathlib import Path

p = argparse.ArgumentParser()
p.add_argument("--cases")
p.add_argument("--model")
p.add_argument("--output")
p.add_argument("--seed")
a = p.parse_args()
with open(a.model, encoding="utf-8") as model_handle:
    model = json.load(model_handle)
Path({str(original_model)!r}).write_text('{{"mutated":true}}\\n', encoding="utf-8")
with open(a.output, "w", encoding="utf-8", newline="\\n") as output_handle:
    with open(a.cases, encoding="utf-8") as cases_handle:
        for line in cases_handle:
            case = json.loads(line)
            output_handle.write(json.dumps({{
                "schema_version": "evaluation.prediction.v0",
                "case_id": case["case_id"],
                "output": {{
                    "kind": "violation_single_label",
                    "label": model["constant_label"],
                }},
            }}, separators=(",", ":")) + "\\n")
""",
    )

    with pytest.raises(IntegrityError) as captured:
        run_development_plan(plan_path, expected_run_plan_sha256=_sha256(plan_path))

    assert captured.value.code in {"EVAL_RUN_ARTIFACT_SIZE_MISMATCH", "EVAL_RUN_ARTIFACT_HASH_MISMATCH"}


def test_run_dev_cli_reports_threshold_exit_six_from_arbitrary_cwd(tmp_path: Path) -> None:
    script = PROJECT_ROOT / "backend" / "scripts" / "evaluate.py"
    plan = EXAMPLE_ROOT / "run-plan.json"
    completed = subprocess.run(
        [
            sys.executable,
            str(script),
            "run-dev",
            "--plan",
            str(plan),
            "--expected-run-plan-sha256",
            _sha256(plan),
            "--require-threshold-pass",
        ],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )

    payload = json.loads(completed.stdout)
    assert completed.returncode == 6
    assert payload["ok"] is True
    assert payload["score"]["threshold_status"] == "failed"
    assert payload["compliance_claim_eligible"] is False
