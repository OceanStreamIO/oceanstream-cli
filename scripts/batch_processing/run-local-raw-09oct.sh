#!/usr/bin/env bash
# Run the raw EK80 pipeline for 2023-10-09 saving all outputs to $EXPERIMENT_ROOT.
# Mirrors run-local-raw-10oct.sh — see that file for background on all env vars.
#
# Adds --qc-file support: known-bad data windows (e.g. the 2023-10-09 08:00 UTC
# bubble sweep-down event) are visually overlaid on generated echograms without
# masking the underlying Sv data (flag-don't-drop, per ICES CRR 259).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

source /Users/andrei/oceanstream/sd-data-ingest/venv/bin/activate
export PYTHONPATH=/Users/andrei/oceanstream/sd-data-ingest:${PYTHONPATH:-}
export PYTHONWARNINGS="ignore"

set -a; source "$SCRIPT_DIR/.env"; set +a
export AZ_SOURCE_CONNECTION_STRING
export AZURE_STORAGE_CONNECTION_STRING

# Output root — defaults to internal APFS disk. Override with EXPERIMENT_ROOT
# env var to point at an external drive (e.g. /Volumes/RP60/...).
EXPERIMENT_ROOT="${EXPERIMENT_ROOT:-$HOME/oceanstream_experiment/tpos_saildrone_2023}"
LOCAL_OUTPUT="$EXPERIMENT_ROOT"
RAW_CACHE="$EXPERIMENT_ROOT/raw_cache"
LOG_FILE="$SCRIPT_DIR/run-local-raw-09oct.log"

# Resume stage (default: full run from stage 1).
# For an echogram-only rerun after Sv+denoise are already computed on disk,
# use RESUME_STAGE=9 (recomputes MVBS + NASC + echograms).
RESUME_STAGE="${RESUME_STAGE:-0}"

# Parallel workers — see run-local-raw-10oct.sh for the exFAT caveat.
PARALLEL_WORKERS="${PARALLEL_WORKERS:-0}"

# Post-denoise Sv sanity clip (dB).
SV_CLIP_MAX_DB="${SV_CLIP_MAX_DB:--10}"

# Denoise config TOML — tropical_pacific by default (200 kHz-aware).
# Denoise config TOML — v3 is the current recommendation for TPOS. It
# preserves v1's 400-600m AS reference band (needed because oligotrophic
# TPOS has no acoustic biology at 800-1200m to detect attenuation against)
# while keeping v2's tighter transient noise threshold. See the header of
# tropical_pacific_denoise_v3.toml for the full changelog.
# Set to "" to fall back to the Ryan 2015 @ 38 kHz global defaults baked
# into scripts/batch_processing/config.py.
DENOISE_CONFIG="${DENOISE_CONFIG:-$SCRIPT_DIR/tropical_pacific_denoise_v3.toml}"

# QC flag file — overlays known-bad time windows on generated echograms.
# Set to "" to disable overlays.
QC_FILE="${QC_FILE:-$SCRIPT_DIR/tpos_2023_qc.json}"

# Comma-separated colormap list. Each echogram gets rendered once per name.
# ocean_r = default, jet = high contrast, EK500 = legacy Simrad palette.
# Set to "" to fall back to a single colormap (COLORMAP env var, default ocean_r).
COLORMAPS="${COLORMAPS:-ocean_r,jet,EK500}"

mkdir -p "$EXPERIMENT_ROOT" "$RAW_CACHE"

DENOISE_ARGS=()
if [[ -n "$DENOISE_CONFIG" ]]; then
  DENOISE_ARGS=(--denoise-config "$DENOISE_CONFIG")
fi

QC_ARGS=()
if [[ -n "$QC_FILE" ]]; then
  QC_ARGS=(--qc-file "$QC_FILE")
fi

CMAP_ARGS=()
if [[ -n "$COLORMAPS" ]]; then
  CMAP_ARGS=(--colormaps "$COLORMAPS")
fi

python -u process_from_raw.py \
  --local-test \
  --start-date 2023-10-09 \
  --end-date 2023-10-09 \
  --calibration-file /Users/andrei/oceanstream/saildrone-data/calibration/calibration_values.xlsx \
  --output-container local-raw-09oct \
  --local-save "$LOCAL_OUTPUT" \
  --gps-container gpsdata \
  --skip-pmtiles \
  --skip-campaign-echograms \
  --keep-raw \
  --raw-cache-dir "$RAW_CACHE" \
  --resume-stage "$RESUME_STAGE" \
  --parallel-workers "$PARALLEL_WORKERS" \
  --sv-clip-max-db "$SV_CLIP_MAX_DB" \
  "${DENOISE_ARGS[@]}" \
  "${QC_ARGS[@]}" \
  "${CMAP_ARGS[@]}" \
  --n-workers 1 \
  --memory-limit 12GB \
  2>&1 | tee "$LOG_FILE"
