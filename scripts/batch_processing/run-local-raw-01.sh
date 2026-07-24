#!/usr/bin/env bash
# Run the raw EK80 pipeline saving all outputs LOCALLY (no Azure blob writes).
# Raw files are still downloaded from Azure File Share.
#
# Output goes to: $SCRIPT_DIR/local-raw-01/
# Raw files cached in: $SCRIPT_DIR/raw_cache/
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

source /Users/andrei/oceanstream/sd-data-ingest/venv/bin/activate
export PYTHONPATH=/Users/andrei/oceanstream/sd-data-ingest:${PYTHONPATH:-}

# Azure connection (needed only for raw file download from File Share)
set -a; source "$SCRIPT_DIR/.env"; set +a
export AZ_SOURCE_CONNECTION_STRING
export AZURE_STORAGE_CONNECTION_STRING

LOCAL_OUTPUT="$SCRIPT_DIR"
LOG_FILE="$SCRIPT_DIR/run-local-raw-01.log"

python process_from_raw.py \
  --local-test \
  --start-date 2023-06-25 \
  --end-date 2023-06-27 \
  --calibration-file /Users/andrei/oceanstream/saildrone-data/calibration/calibration_values.xlsx \
  --output-container local-raw-01 \
  --local-save "$LOCAL_OUTPUT" \
  --gps-container gpsdata \
  --keep-raw \
  --raw-cache-dir "$SCRIPT_DIR/raw_cache" \
  --n-workers 1 \
  --memory-limit 12GB \
  2>&1 | tee "$LOG_FILE"
