# Local quality gates that do not require Linux, Docker, or make.
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Backend = Join-Path $Root "backend"
$Frontend = Join-Path $Root "frontend"
$Python = Join-Path $Backend ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) { throw "Missing $Python" }

Write-Host "== backend compileall =="
Push-Location $Backend
& $Python -m compileall -q app scripts
if ($LASTEXITCODE -ne 0) { Pop-Location; exit $LASTEXITCODE }
Write-Host "== pip check =="
& $Python -m pip check
if ($LASTEXITCODE -ne 0) { Pop-Location; exit $LASTEXITCODE }
Write-Host "== dependency lock =="
& $Python scripts\verify_dependency_lock.py
if ($LASTEXITCODE -ne 0) { Pop-Location; exit $LASTEXITCODE }
Write-Host "== openapi contract =="
& $Python scripts\export_openapi.py --check
if ($LASTEXITCODE -ne 0) { Pop-Location; exit $LASTEXITCODE }
Write-Host "== remote contract =="
& $Python scripts\export_remote_contract.py --check
if ($LASTEXITCODE -ne 0) { Pop-Location; exit $LASTEXITCODE }
Write-Host "== schema sql offline =="
& $Python -m app.schema sql --dialect sqlite | Out-Null
if ($LASTEXITCODE -ne 0) { Pop-Location; exit $LASTEXITCODE }
& $Python -m app.schema sql --dialect postgresql | Out-Null
if ($LASTEXITCODE -ne 0) { Pop-Location; exit $LASTEXITCODE }
Write-Host "== postgres acceptance static =="
& $Python -m pytest tests\test_postgres_acceptance.py tests\test_postgres_contention_observe.py -W error -q
if ($LASTEXITCODE -ne 0) { Pop-Location; exit $LASTEXITCODE }
Pop-Location

Write-Host "== frontend verify =="
Push-Location $Frontend
npm.cmd run verify
if ($LASTEXITCODE -ne 0) { Pop-Location; exit $LASTEXITCODE }
Pop-Location

Write-Host "ALL LOCAL QUALITY GATES PASSED"
exit 0
