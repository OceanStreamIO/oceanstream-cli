#!/usr/bin/env bash
# RESUME the local pipeline from Stage 4 (denoising).
# Stages 1-3 completed in run-local-raw-01; this picks up from the saved outputs.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

source /Users/andrei/oceanstream/sd-data-ingest/venv/bin/activate

# Azure connection (needed only for raw file download from File Share)
set -a; source "$SCRIPT_DIR/.env"; set +a
export AZ_SOURCE_CONNECTION_STRING
export AZURE_STORAGE_CONNECTION_STRING

LOCAL_OUTPUT="$SCRIPT_DIR"

python process_from_raw.py \
  --local-test \
  --start-date 2023-06-25 \
  --end-date 2023-06-26 \
  --calibration-file /Users/andrei/oceanstream/saildrone-data/calibration/calibration_values.xlsx \
  --output-container local-raw-01 \
  --local-save "$LOCAL_OUTPUT" \
  --resume-stage 5 \
  --n-workers 1 \
  --memory-limit 12GB \
  --skip-nasc
