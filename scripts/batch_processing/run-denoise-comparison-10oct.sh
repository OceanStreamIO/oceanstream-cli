#!/usr/bin/env bash
# 3-preset denoise sensitivity case study — 2023-10-10 (Saildrone TPOS 2023).
#
# Runs the pipeline three times from stage 5 (denoise) against ONE immutable
# stage-4 Sv source, once per preset, then builds the comparison report.
#
#   preset          TOML                                output container
#   ─────────────   ─────────────────────────────────   ───────────────────────
#   ryan-inspired   ryan2015_denoise_defaults.toml      local-raw-10oct-ryan
#   tpv1            tropical_pacific_denoise.toml       local-raw-10oct-tpv1
#   tpv3            tropical_pacific_denoise_v3.toml    local-raw-10oct-tpv3
#
# The source container (local-raw-10oct) is NEVER written to; its fingerprint is
# re-verified after every arm. Stage-5 resume is fully local, so no Azure
# credentials are sourced.
#
# Usage:
#   ./run-denoise-comparison-10oct.sh pilot     # short-pulse, one preset, measured
#   ./run-denoise-comparison-10oct.sh run       # full 3-preset matrix
#   ./run-denoise-comparison-10oct.sh report    # metrics + results.md/.pdf
#   ./run-denoise-comparison-10oct.sh all       # pilot → run → report
set -euo pipefail

# Nothing here reads stdin, and inheriting a closed fd 0 (which is what
# `nohup ... &` from an interactive shell leaves behind) makes every later
# `python` abort with "init_sys_streams: can't initialize sys standard streams".
exec </dev/null

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
VENV="${VENV:-$REPO_ROOT/venv}"
# shellcheck disable=SC1091
source "$VENV/bin/activate"
export PYTHONPATH="$REPO_ROOT:${PYTHONPATH:-}"

# ── Experiment contract ────────────────────────────────────────────────────
EXPERIMENT_ROOT="${EXPERIMENT_ROOT:-$HOME/oceanstream_experiment/tpos_saildrone_2023}"
SOURCE_CONTAINER="local-raw-10oct"
DAY="2023-10-10"
CRUISE_ID="SD_TPOS2023_v03"
RESUME_STAGE=5
STOP_AFTER_STAGE=9
SV_CLIP_MAX_DB=-10
PARALLEL_WORKERS=1
DASK_WORKERS=1
DASK_MEMORY="12GB"

RESULTS_ROOT="${RESULTS_ROOT:-$EXPERIMENT_ROOT/denoise-comparison-10oct}"
RUNS_DIR="$RESULTS_ROOT/runs"
COMPARISON_DIR="$RESULTS_ROOT/comparison"
MANIFEST="$RESULTS_ROOT/experiment-manifest.json"

# preset_key | toml | output container
PRESETS=(
  "ryan-inspired|ryan2015_denoise_defaults.toml|local-raw-10oct-ryan"
  "tpv1|tropical_pacific_denoise.toml|local-raw-10oct-tpv1"
  "tpv3|tropical_pacific_denoise_v3.toml|local-raw-10oct-tpv3"
)

# Free disk required before the full matrix (GiB). Measured from the pilot:
# ~2.2 GiB per preset for short pulse plus ~0.1 GiB for long pulse, so three
# arms cost ~7 GiB. The rest is headroom for figures and the PDF.
MIN_FREE_GIB="${MIN_FREE_GIB:-25}"

mkdir -p "$RUNS_DIR" "$COMPARISON_DIR"

log() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
die() { printf '\033[31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }

# ── Preflight ──────────────────────────────────────────────────────────────

preflight_disk() {
  local required_gib="$1"
  local avail_gib
  avail_gib=$(df -g "$EXPERIMENT_ROOT" | awk 'NR==2 {print $4}')
  log "Free disk at $EXPERIMENT_ROOT: ${avail_gib} GiB (need ${required_gib} GiB)"
  if [[ "$avail_gib" -lt "$required_gib" ]]; then
    die "Insufficient free disk: ${avail_gib} GiB < ${required_gib} GiB required."
  fi
}

freeze_contract() {
  if [[ -f "$MANIFEST" ]]; then
    log "Experiment manifest already frozen: $MANIFEST"
    return
  fi
  log "Freezing experiment contract (source fingerprints + schemas + tolerances)"
  python experiment_contract.py fingerprint \
    --source-root "$EXPERIMENT_ROOT/$SOURCE_CONTAINER" \
    --day "$DAY" \
    --out "$MANIFEST"
}

verify_source_immutable() {
  log "Verifying source fingerprints unchanged"
  python experiment_contract.py verify --manifest "$MANIFEST" \
    || die "Stage-4 Sv source changed mid-experiment — results are not comparable."
}

# ── One preset arm ─────────────────────────────────────────────────────────

run_preset() {
  local preset_key="$1" toml="$2" container="$3" categories="$4" measure="$5"

  local run_dir="$RUNS_DIR/$preset_key"
  mkdir -p "$run_dir"
  local log_file="$run_dir/run.log"
  local cmd_file="$run_dir/resolved-command.txt"
  local status_file="$run_dir/status.json"

  [[ -f "$SCRIPT_DIR/$toml" ]] || die "Missing preset TOML: $SCRIPT_DIR/$toml"

  # Idempotent: an arm that already carries a run-manifest and a complete
  # artifact matrix is reused, so an interrupted matrix can be resumed without
  # recomputing arms that already validated.
  if [[ -f "$EXPERIMENT_ROOT/$container/run-manifest.json" ]] \
     && python experiment_contract.py validate-artifacts \
          --run-root "$EXPERIMENT_ROOT/$container" --day "$DAY" \
          --categories "$categories" >/dev/null 2>&1; then
    log "Preset $preset_key already complete in $container — reusing"
    cp "$EXPERIMENT_ROOT/$container/run-manifest.json" "$run_dir/run-manifest.json"
    verify_source_immutable
    return 0
  fi

  local -a cmd=(
    python process_from_raw.py
    --cruise-id "$CRUISE_ID"
    --start-date "$DAY"
    --end-date "$DAY"
    --output-container "$container"
    --sv-source-container "$SOURCE_CONTAINER"
    --local-save "$EXPERIMENT_ROOT"
    --denoise-config "$SCRIPT_DIR/$toml"
    --preset-key "$preset_key"
    --expected-categories "$categories"
    --resume-stage "$RESUME_STAGE"
    --stop-after-stage "$STOP_AFTER_STAGE"
    --sv-clip-max-db "$SV_CLIP_MAX_DB"
    --parallel-workers "$PARALLEL_WORKERS"
    --n-workers "$DASK_WORKERS"
    --memory-limit "$DASK_MEMORY"
    --emit-denoise-diagnostics
    --strict
    --skip-pmtiles
    --skip-combined-echograms
  )

  printf '%q ' "${cmd[@]}" > "$cmd_file"
  printf '\n' >> "$cmd_file"

  log "Preset $preset_key → $container (categories: $categories)"
  echo "  resolved command: $cmd_file"
  echo "  log:              $log_file"

  local started exit_code=0
  started=$(date -u +%Y-%m-%dT%H:%M:%SZ)

  if [[ "$measure" == "measure" ]]; then
    # macOS /usr/bin/time -l reports "maximum resident set size" in bytes.
    /usr/bin/time -l "${cmd[@]}" > >(tee "$log_file") 2> >(tee "$run_dir/time.log" >&2) || exit_code=$?
  else
    "${cmd[@]}" 2>&1 | tee "$log_file" || exit_code="${PIPESTATUS[0]}"
  fi

  local finished
  finished=$(date -u +%Y-%m-%dT%H:%M:%SZ)

  local disk_bytes=0
  if [[ -d "$EXPERIMENT_ROOT/$container" ]]; then
    disk_bytes=$(du -sk "$EXPERIMENT_ROOT/$container" | awk '{print $1 * 1024}')
  fi

  local peak_rss_bytes=null
  if [[ -f "$run_dir/time.log" ]]; then
    peak_rss_bytes=$(awk '/maximum resident set size/ {print $1; exit}' "$run_dir/time.log")
    peak_rss_bytes="${peak_rss_bytes:-null}"
  fi

  cat > "$status_file" <<JSON
{
  "preset": "$preset_key",
  "toml": "$toml",
  "container": "$container",
  "categories": "$categories",
  "started_utc": "$started",
  "finished_utc": "$finished",
  "exit_code": $exit_code,
  "output_bytes": $disk_bytes,
  "peak_rss_bytes": $peak_rss_bytes
}
JSON

  [[ "$exit_code" -eq 0 ]] || die "Preset $preset_key failed (exit $exit_code). See $log_file"

  log "Validating artifact matrix for $preset_key"
  python experiment_contract.py validate-artifacts \
    --run-root "$EXPERIMENT_ROOT/$container" --day "$DAY" --categories "$categories" \
    || die "Preset $preset_key produced an incomplete artifact matrix."

  [[ -f "$EXPERIMENT_ROOT/$container/run-manifest.json" ]] \
    || die "Preset $preset_key wrote no run-manifest.json — run did not validate."
  cp "$EXPERIMENT_ROOT/$container/run-manifest.json" "$run_dir/run-manifest.json"

  verify_source_immutable

  log "Preset $preset_key OK — $(numfmt --to=iec "$disk_bytes" 2>/dev/null || echo "$disk_bytes bytes") on disk"
}

# ── Modes ──────────────────────────────────────────────────────────────────

cmd_pilot() {
  freeze_contract
  preflight_disk 60
  log "PILOT: short-pulse only, one preset, measuring peak RSS and disk"
  IFS='|' read -r key toml container <<< "${PRESETS[2]}"
  rm -rf "${EXPERIMENT_ROOT:?}/${container}-pilot"
  run_preset "$key-pilot" "$toml" "${container}-pilot" "short_pulse" "measure"
  log "Pilot complete. Review $RUNS_DIR/$key-pilot/status.json before the full matrix."
}

cmd_run() {
  freeze_contract
  preflight_disk "$MIN_FREE_GIB"
  for entry in "${PRESETS[@]}"; do
    IFS='|' read -r key toml container <<< "$entry"
    run_preset "$key" "$toml" "$container" "long_pulse,short_pulse" "plain"
  done
  log "All three presets complete and validated."
}

cmd_report() {
  [[ -f "$MANIFEST" ]] || die "No experiment manifest at $MANIFEST — run 'pilot' or 'run' first."
  log "Building comparison metrics + report"
  python generate_denoise_report.py \
    --experiment-manifest "$MANIFEST" \
    --experiment-root "$EXPERIMENT_ROOT" \
    --runs-dir "$RUNS_DIR" \
    --out-dir "$COMPARISON_DIR" \
    --day "$DAY"
  log "Report at $COMPARISON_DIR/results.md (+ results.pdf)"
}

case "${1:-run}" in
  pilot)  cmd_pilot ;;
  run)    cmd_run ;;
  report) cmd_report ;;
  all)    cmd_pilot; cmd_run; cmd_report ;;
  *)      die "Unknown mode '${1}'. Use: pilot | run | report | all" ;;
esac
