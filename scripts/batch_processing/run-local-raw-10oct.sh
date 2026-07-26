#!/usr/bin/env bash
# Run the raw EK80 pipeline for 2023-10-10 saving all outputs to external volume.
# Raw files are downloaded from Azure File Share and cached on the same volume.
# Skips PMTiles + COG heatmap generation (single stage flag).
#
# Output goes to:   /Volumes/RP60/tpos_saildrone_2023/_experiment/local-raw-10oct/
# Raw cache:        /Volumes/RP60/tpos_saildrone_2023/_experiment/raw_cache/
# EchoData interm.: /Volumes/RP60/tpos_saildrone_2023/_experiment/echodata_intermediate/
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

source /Users/andrei/oceanstream/sd-data-ingest/venv/bin/activate
export PYTHONPATH=/Users/andrei/oceanstream/sd-data-ingest:${PYTHONPATH:-}

# Silence zarr AppleDouble warnings and other noisy PendingDeprecation output
# (macOS creates ._* sidecars on the external volume that zarr tries to open)
export PYTHONWARNINGS="ignore"

# Azure connection (needed only for raw file download from File Share)
set -a; source "$SCRIPT_DIR/.env"; set +a
export AZ_SOURCE_CONNECTION_STRING
export AZURE_STORAGE_CONNECTION_STRING

# Output root — defaults to internal APFS disk. Override with EXPERIMENT_ROOT
# env var to point at an external drive (e.g. /Volumes/RP60/...).
# Example: EXPERIMENT_ROOT=/Volumes/RP60/tpos_saildrone_2023/_experiment ./run-local-raw-10oct.sh
EXPERIMENT_ROOT="${EXPERIMENT_ROOT:-$HOME/oceanstream_experiment/tpos_saildrone_2023}"
LOCAL_OUTPUT="$EXPERIMENT_ROOT"
RAW_CACHE="$EXPERIMENT_ROOT/raw_cache"
LOG_FILE="$SCRIPT_DIR/run-local-raw-10oct.log"

# Allow overriding stage to resume from (default: full run from stage 1)
# For Oct-10 rerun, Sv is already computed, so pass RESUME_STAGE=5 to skip stages 1–4.
RESUME_STAGE="${RESUME_STAGE:-0}"

# Parallel workers for denoise/MVBS/NASC/echogram stages.
# On macOS + exFAT external drives (like RP60): set to 1 — the FSKit exFAT
# driver has weak metadata coherence under concurrent writes.
# On Ubuntu ext4/xfs, macOS APFS, or Azure Blob: leave as 0 (auto-detect from RAM).
PARALLEL_WORKERS="${PARALLEL_WORKERS:-0}"

# Post-denoise Sv sanity clip (dB). Anything louder than this becomes NaN.
# -10 default → catches only the most egregious cross-talk (+4 dB outliers).
# -30 → also catches loud residual noise that survived the mask denoisers.
# -40 → aggressive; may remove upper end of legit fish-school echoes.
SV_CLIP_MAX_DB="${SV_CLIP_MAX_DB:--10}"

# Denoise config TOML. Loading a file flips on per-frequency dispatch
# (use_frequency_specific=true) and gives 200 kHz its own tuning — the
# attenuation ref-band 400–500 m in the global defaults is out of range at
# 200 kHz and returns an empty mask. The tropical_pacific config uses
# 50–150 m for 200 kHz and preserves the weak TPOS DSL at 38 kHz by relaxing
# background SNR from 5 → 3 dB. Set to "" to fall back to the Ryan 2015 @
# 38 kHz global defaults baked into scripts/batch_processing/config.py.
DENOISE_CONFIG="${DENOISE_CONFIG:-$SCRIPT_DIR/tropical_pacific_denoise.toml}"

mkdir -p "$EXPERIMENT_ROOT" "$RAW_CACHE"

# Print output live to stdout AND capture it in the log file.
# PYTHONWARNINGS=ignore above keeps the volume manageable (no zarr warning flood).
# NOTE: run in a real Terminal.app / iTerm2 window — VS Code's terminal buffer
# can OOM if the output volume ever spikes.
DENOISE_ARGS=()
if [[ -n "$DENOISE_CONFIG" ]]; then
  DENOISE_ARGS=(--denoise-config "$DENOISE_CONFIG")
fi

python -u process_from_raw.py \
  --local-test \
  --start-date 2023-10-10 \
  --end-date 2023-10-10 \
  --calibration-file /Users/andrei/oceanstream/saildrone-data/calibration/calibration_values.xlsx \
  --output-container local-raw-10oct \
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
  --n-workers 1 \
  --memory-limit 12GB \
  2>&1 | tee "$LOG_FILE"
# NOTE: --parallel-workers 1 forces sequential denoise/MVBS/NASC/echogram
# execution. RP60 is exFAT and macOS exFAT has flaky concurrent directory
# creation — writing two zarrs simultaneously causes intermittent
# ENOENT failures on chunk subdirectories (`.zarr/<var>/c/`).
# On Ubuntu (ext4/xfs) or Azure Blob, override with:
#   PARALLEL_WORKERS=0 ./run-local-raw-10oct.sh   # auto-detect from RAM
