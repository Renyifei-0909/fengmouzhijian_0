# Reproducible Windows backend subset: excludes POSIX secure-open Evaluation /
# Algorithm Readiness modules and deselects 10 FS privilege / link attack cases
# that cannot be constructed without elevated symlink rights on this host.
# This is NOT a Linux 90% coverage gate.
$ErrorActionPreference = "Stop"
$Backend = Join-Path (Split-Path -Parent $PSScriptRoot) "backend"
if (-not (Test-Path (Join-Path $Backend ".venv\Scripts\python.exe"))) {
    throw "backend\.venv\Scripts\python.exe not found; run uv sync --extra dev --locked first"
}

$ffDefault = "E:\Workspaces\xtx\fengmou-tools\ffmpeg\essentials\ffmpeg-8.1.2-essentials_build\bin"
if (Test-Path (Join-Path $ffDefault "ffprobe.exe")) {
    $env:Path = "$ffDefault;" + $env:Path
    Write-Host "ffprobe prepended from portable tools"
} else {
    Write-Host "WARNING: portable ffprobe not found; video tests may skip"
}

Set-Location $Backend
$python = ".\.venv\Scripts\python.exe"
$ignore = @(
    "--ignore=tests/test_evaluation_bundle.py",
    "--ignore=tests/test_evaluation_core.py",
    "--ignore=tests/test_evaluation_cli.py",
    "--ignore=tests/test_evaluation_controlled_bundle.py",
    "--ignore=tests/test_evaluation_example.py",
    "--ignore=tests/test_evaluation_executor.py",
    "--ignore=tests/test_evaluation_registry.py",
    "--ignore=tests/test_evaluation_registry_cli.py",
    "--ignore=tests/test_algorithm_readiness.py"
)
$deselect = @(
    "--deselect=tests/test_evidence_content.py::test_symbolic_links_are_rejected_without_target_disclosure[False]",
    "--deselect=tests/test_evidence_content.py::test_symbolic_links_are_rejected_without_target_disclosure[True]",
    "--deselect=tests/test_evidence_content.py::test_directory_and_hard_link_are_rejected",
    "--deselect=tests/test_evidence_content.py::test_path_swap_after_validation_cannot_change_streamed_bytes",
    "--deselect=tests/test_evidence_content.py::test_review_reuses_secure_storage_validation",
    "--deselect=tests/test_evidence_content.py::test_evidence_directory_symlink_is_rejected_before_member_open",
    "--deselect=tests/test_evidence_content.py::test_path_mutation_after_digest_fails_before_response[disappear]",
    "--deselect=tests/test_remote_http_analyzer.py::test_remote_bridge_streams_the_validated_fd_after_path_replacement",
    "--deselect=tests/test_sealing_saga.py::test_staging_symlink_after_commit_does_not_reclassify_completed_seal",
    "--deselect=tests/test_sealing_saga.py::test_local_sealing_guards_reject_invalid_ids_links_and_busy_lock"
)

Write-Host "Running Windows backend subset (-W error)..."
& $python -m pytest -W error -q --tb=line @ignore @deselect @args
exit $LASTEXITCODE
