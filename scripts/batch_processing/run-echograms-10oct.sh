#!/usr/bin/env bash
# Echogram-only 3-preset run — 2023-10-10 (Saildrone TPOS 2023).
#
# Same three denoise presets as the sensitivity case study, but the deliverable
# is echograms rendered in three colormaps (ek500, ocean_r, jet) for every
# stage — source Sv, denoised, pruned, MVBS and the combined 38 kHz 24 h panel.
# No diagnostics, no metrics, no report.
#
#   preset          TOML                                output container
#   ─────────────   ─────────────────────────────────   ──────────────────────────
#   ryan-inspired   ryan2015_denoise_defaults.toml      local-raw-10oct-echo-ryan
#   tpv1            tropical_pacific_denoise.toml       local-raw-10oct-echo-tpv1
#   tpv3            tropical_pacific_denoise_v3.toml    local-raw-10oct-echo-tpv3
#
# Each arm resumes at stage 5 from the immutable stage-4 Sv source
# (local-raw-10oct), so all three see byte-identical Sv input and no Azure
# credentials are needed. Set FROM_RAW=1 for a true stage-1 run that downloads
# and converts the .raw files first.
#
# Usage:
#   ./run-echograms-10oct.sh                  # all three presets
#   ./run-echograms-10oct.sh tpv3             # a single preset
#   FORCE=1 ./run-echograms-10oct.sh          # wipe and redo populated arms
#   FROM_RAW=1 ./run-echograms-10oct.sh       # full pipeline from raw files
#   COMBINED_ONLY=1 ./run-echograms-10oct.sh  # rebuild combined-38kHz/ only (~2 min/arm)
set -euo pipefail

# Nothing here reads stdin, and inheriting a closed fd 0 (what `nohup ... &`
# from an interactive shell leaves behind) makes python abort with
# "init_sys_streams: can't initialize sys standard streams".
exec </dev/null

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
VENV="${VENV:-$REPO_ROOT/venv}"
# shellcheck disable=SC1091
source "$VENV/bin/activate"
export PYTHONPATH="$REPO_ROOT:${PYTHONPATH:-}"
export PYTHONWARNINGS="ignore"

# ── Configuration ──────────────────────────────────────────────────────────
EXPERIMENT_ROOT="${EXPERIMENT_ROOT:-$HOME/oceanstream_experiment/tpos_saildrone_2023}"
SOURCE_CONTAINER="${SOURCE_CONTAINER:-local-raw-10oct}"
DAY="2023-10-10"
CRUISE_ID="SD_TPOS2023_v03"

# Comma-separated colormap list. Each Sv echogram is rendered once per name and
# the filename gets a '--{cmap}' suffix. Names are lowercased for filenames.
COLORMAPS="${COLORMAPS:-ek500,ocean_r,jet}"

SV_CLIP_MAX_DB="${SV_CLIP_MAX_DB:--10}"
PARALLEL_WORKERS="${PARALLEL_WORKERS:-1}"
DASK_WORKERS="${DASK_WORKERS:-1}"
DASK_MEMORY="${DASK_MEMORY:-12GB}"

FROM_RAW="${FROM_RAW:-0}"
FORCE="${FORCE:-0}"
COMBINED_ONLY="${COMBINED_ONLY:-0}"
MIN_FREE_GIB="${MIN_FREE_GIB:-25}"

CALIBRATION_FILE="${CALIBRATION_FILE:-/Users/andrei/oceanstream/saildrone-data/calibration/calibration_values.xlsx}"
RAW_CACHE="$EXPERIMENT_ROOT/raw_cache"
LOG_ROOT="${LOG_ROOT:-$EXPERIMENT_ROOT/echograms-10oct}"

# preset_key | toml | output container
PRESETS=(
  "ryan-inspired|ryan2015_denoise_defaults.toml|local-raw-10oct-echo-ryan"
  "tpv1|tropical_pacific_denoise.toml|local-raw-10oct-echo-tpv1"
  "tpv3|tropical_pacific_denoise_v3.toml|local-raw-10oct-echo-tpv3"
)

mkdir -p "$EXPERIMENT_ROOT" "$LOG_ROOT"

log() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
die() { printf '\033[31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }

write_parameters() {
  python write_run_parameters.py --run-root "$1" \
    || die "Failed to write parameters.txt for $1"
}

# ── Preflight ──────────────────────────────────────────────────────────────

preflight() {
  local avail_gib
  avail_gib=$(df -g "$EXPERIMENT_ROOT" | awk 'NR==2 {print $4}')
  log "Free disk at $EXPERIMENT_ROOT: ${avail_gib} GiB (need ${MIN_FREE_GIB} GiB)"
  [[ "$avail_gib" -ge "$MIN_FREE_GIB" ]] \
    || die "Insufficient free disk: ${avail_gib} GiB < ${MIN_FREE_GIB} GiB required."

  if [[ "$FROM_RAW" == "1" ]]; then
    mkdir -p "$RAW_CACHE"
    [[ -f "$SCRIPT_DIR/.env" ]] || die "FROM_RAW=1 needs Azure credentials in $SCRIPT_DIR/.env"
    [[ -f "$CALIBRATION_FILE" ]] || die "Missing calibration file: $CALIBRATION_FILE"
    set -a; source "$SCRIPT_DIR/.env"; set +a
    export AZ_SOURCE_CONNECTION_STRING AZURE_STORAGE_CONNECTION_STRING
  else
    [[ -d "$EXPERIMENT_ROOT/$SOURCE_CONTAINER/$DAY" ]] \
      || die "No stage-4 Sv source at $EXPERIMENT_ROOT/$SOURCE_CONTAINER/$DAY. \
Run run-local-raw-10oct.sh first, or set FROM_RAW=1."
  fi
}

# ── One preset arm ─────────────────────────────────────────────────────────

run_preset() {
  local key="$1" toml="$2" container="$3"
  local out="$EXPERIMENT_ROOT/$container"
  local run_dir="$LOG_ROOT/$key"
  local log_file="$run_dir/run.log"
  local cmd_file="$run_dir/resolved-command.txt"
  if [[ "$COMBINED_ONLY" == "1" ]]; then
    log_file="$run_dir/run-combined.log"
    cmd_file="$run_dir/resolved-command-combined.txt"
  fi

  [[ -f "$SCRIPT_DIR/$toml" ]] || die "Missing preset TOML: $SCRIPT_DIR/$toml"
  mkdir -p "$run_dir"

  # run-manifest.json is written only after the artifact matrix validates, so
  # its absence means the arm was interrupted. Reusing such a container would
  # be worse than redoing it: stage 9 skips any day/category that already has
  # echograms, so a partial arm keeps whatever colormap set it stopped at.
  if [[ "$COMBINED_ONLY" == "1" ]]; then
    [[ -f "$out/run-manifest.json" ]] \
      || die "$container has no run-manifest.json — run the full pipeline for $key first."
    # Stale --denoised-- panels would otherwise sit next to the new ones.
    log "Combined-only: clearing $out/$DAY/combined-38kHz"
    rm -f "$out/$DAY/combined-38kHz"/*.png
  elif [[ -d "$out" ]] && [[ -n "$(ls -A "$out" 2>/dev/null)" ]]; then
    if [[ -f "$out/run-manifest.json" ]] && [[ "$FORCE" != "1" ]]; then
      log "Skipping $key — $container already complete (FORCE=1 to redo)"
      write_parameters "$out"
      return 0
    fi
    log "Removing incomplete $out"
    rm -rf "$out"
  fi

  local -a cmd=(
    python -u process_from_raw.py
    --cruise-id "$CRUISE_ID"
    --start-date "$DAY"
    --end-date "$DAY"
    --output-container "$container"
    --local-save "$EXPERIMENT_ROOT"
    --denoise-config "$SCRIPT_DIR/$toml"
    --preset-key "$key"
    --colormaps "$COLORMAPS"
    --stop-after-stage 9
    --sv-clip-max-db "$SV_CLIP_MAX_DB"
    --parallel-workers "$PARALLEL_WORKERS"
    --n-workers "$DASK_WORKERS"
    --memory-limit "$DASK_MEMORY"
    --skip-pmtiles
    --strict
  )

  if [[ "$FROM_RAW" == "1" ]]; then
    cmd+=(
      --local-test
      --calibration-file "$CALIBRATION_FILE"
      --gps-container gpsdata
      --keep-raw
      --raw-cache-dir "$RAW_CACHE"
      --resume-stage 0
    )
  elif [[ "$COMBINED_ONLY" == "1" ]]; then
    # Stage 9 skips day/categories that already have figures, so only the
    # combined panels get rebuilt. Stage 6b re-runs (~29 s, deterministic).
    cmd+=(
      --sv-source-container "$SOURCE_CONTAINER"
      --resume-stage 9
      --force
    )
  else
    cmd+=(
      --sv-source-container "$SOURCE_CONTAINER"
      --resume-stage 5
    )
  fi

  printf '%q ' "${cmd[@]}" > "$cmd_file"
  printf '\n' >> "$cmd_file"

  log "Preset $key → $container (colormaps: $COLORMAPS)"
  echo "  resolved command: $cmd_file"
  echo "  log:              $log_file"

  local started exit_code=0
  started=$SECONDS
  "${cmd[@]}" 2>&1 | tee "$log_file" || exit_code="${PIPESTATUS[0]}"
  [[ "$exit_code" -eq 0 ]] || die "Preset $key failed (exit $exit_code). See $log_file"

  local pngs
  pngs=$(find "$out" -name '*.png' | wc -l | tr -d ' ')
  write_parameters "$out"
  log "Preset $key OK — $pngs echograms, $(du -sh "$out" | awk '{print $1}') in $((SECONDS - started))s"
}

# ── Main ───────────────────────────────────────────────────────────────────

SELECTED="${*:-}"
preflight

for entry in "${PRESETS[@]}"; do
  IFS='|' read -r key toml container <<< "$entry"
  if [[ -n "$SELECTED" ]] && [[ " $SELECTED " != *" $key "* ]]; then
    continue
  fi
  run_preset "$key" "$toml" "$container"
done

log "Done. Echograms under:"
for entry in "${PRESETS[@]}"; do
  IFS='|' read -r _ _ container <<< "$entry"
  if [[ -d "$EXPERIMENT_ROOT/$container" ]]; then
    echo "  $EXPERIMENT_ROOT/$container/$DAY/{raw,denoised,pruned,mvbs}"
  fi
done
