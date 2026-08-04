#!/bin/sh
set -eu

version="${1:-stage2-alpha11-$(date +%Y-%m-%d)}"
case "$version" in
  *[!A-Za-z0-9._-]* | "")
    echo "Version may contain only letters, numbers, dot, underscore and dash" >&2
    exit 2
    ;;
esac

project_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
project_name=$(basename -- "$project_root")
output_dir=$(dirname -- "$project_root")
archive="$output_dir/${project_name}-${version}.zip"
checksum="$archive.sha256"

if [ -e "$archive" ] || [ -e "$checksum" ]; then
  echo "Refusing to overwrite an existing delivery artifact: $archive" >&2
  exit 2
fi

tmp_archive=$(mktemp "$output_dir/.${project_name}-${version}.XXXXXX.zip")
tmp_checksum="$checksum.tmp.$$"
trap 'rm -f "$tmp_archive" "$tmp_checksum"' EXIT HUP INT TERM
# Info-ZIP treats an existing empty file as a corrupt archive instead of a new target.
rm -f "$tmp_archive"

cd "$output_dir"
zip -q -r "$tmp_archive" "$project_name" \
  -x "*/.git" "*/.git/*" "*/.DS_Store" \
     "*/.env" "*/.env.local" "*/.env.production" "*/.env.development" \
     "*/.env.test" "*/.env.staging" "*/.env.*.local" \
     "$project_name/frontend/node_modules/*" "$project_name/frontend/dist/*" \
     "$project_name/backend/data/*" "$project_name/runtime-data/*" \
     "$project_name/data/*" "$project_name/work/*" \
     "$project_name/.playwright-cli/*" \
     "*/.pytest_cache/*" "*/__pycache__/*" "*.pyc" "*.pyo" \
     "*/.coverage" "*/.coverage.*" "*/htmlcov/*" "*/*.egg-info/*" \
     "*.log" "*.zip" "*.zip.sha256" \
     "*.pem" "*.key" "*.p12" "*.pfx" \
     "*.pt" "*.pth" "*.onnx" "*.safetensors"

unzip -tq "$tmp_archive" >/dev/null

zipinfo -1 "$tmp_archive" | while IFS= read -r member; do
  case "$member" in
    /* | ../* | */../* | *\\*)
      echo "Unsafe ZIP member: $member" >&2
      exit 1
      ;;
  esac
done

# Exclusions keep the current package small; this second gate fails closed if a
# future secret/runtime/cache path is added without a matching exclusion.
zipinfo -1 "$tmp_archive" | while IFS= read -r member; do
  case "$member" in
    "$project_name/.env.example" | "$project_name/backend/.env.example" | \
      "$project_name/backend/.env.reference.example" | "$project_name/frontend/.env.example")
      ;;
    */.git | */.git/* | */.DS_Store | */.env | */.env.* | \
      "$project_name/backend/data" | "$project_name/backend/data/"* | \
      "$project_name/runtime-data" | "$project_name/runtime-data/"* | \
      "$project_name/data" | "$project_name/data/"* | \
      "$project_name/work" | "$project_name/work/"* | \
      */node_modules | */node_modules/* | */dist | */dist/* | \
      */.playwright-cli | */.playwright-cli/* | */.pytest_cache | */.pytest_cache/* | \
      */__pycache__ | */__pycache__/* | */htmlcov | */htmlcov/* | \
      */.coverage | */.coverage.* | */*.egg-info | */*.egg-info/* | \
      *.pyc | *.pyo | *.log | *.zip | *.zip.sha256 | \
      *.pem | *.key | *.p12 | *.pfx | *.pt | *.pth | *.onnx | *.safetensors)
      echo "Forbidden delivery member: $member" >&2
      exit 1
      ;;
  esac
done

if zipinfo -l "$tmp_archive" | awk '$1 ~ /^l/ { found=1 } END { exit found ? 0 : 1 }'; then
  echo "Delivery ZIP contains a symbolic link" >&2
  exit 1
fi

for required in \
  "$project_name/README.md" \
  "$project_name/Makefile" \
  "$project_name/compose.yaml" \
  "$project_name/backend/pyproject.toml" \
  "$project_name/backend/app/algorithm_readiness.py" \
  "$project_name/backend/app/worker.py" \
  "$project_name/backend/scripts/algorithm_preflight.py" \
  "$project_name/backend/tests/test_algorithm_readiness.py" \
  "$project_name/backend/tests/test_worker_leases.py" \
  "$project_name/backend/tests/test_worker_cli.py" \
  "$project_name/backend/tests/test_sealing_saga.py" \
  "$project_name/frontend/package-lock.json" \
  "$project_name/frontend/src/pages/BackendWorkflowPage.tsx" \
  "$project_name/docs/openapi-v1.json" \
  "$project_name/docs/remote-analyzer-request-v1.schema.json" \
  "$project_name/docs/remote-analyzer-response-v1.schema.json" \
  "$project_name/docs/STAGE2_ALPHA9_RECOVERY.md" \
  "$project_name/docs/STAGE2_ALPHA10_ALGORITHM_READINESS.md" \
  "$project_name/docs/STAGE2_ALPHA11_WORKER_LEASES.md" \
  "$project_name/docs/algorithm-data/ALGORITHM_READINESS_0.md" \
  "$project_name/docs/design/stage2-alpha6-image2-concept.png" \
  "$project_name/output/playwright/stage2-alpha9-latest-completed-desktop.png" \
  "$project_name/examples/stage2-demo/event-browser-compatible.mp4"; do
  if ! zipinfo -1 "$tmp_archive" | grep -Fqx "$required"; then
    echo "Required delivery member is missing: $required" >&2
    exit 1
  fi
done

mv "$tmp_archive" "$archive"
(
  cd "$output_dir"
  shasum -a 256 "$(basename -- "$archive")" >"$tmp_checksum"
)
mv "$tmp_checksum" "$checksum"
trap - EXIT HUP INT TERM

echo "$archive"
echo "$checksum"
