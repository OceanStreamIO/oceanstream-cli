#!/usr/bin/env bash
# =============================================================================
# Render and submit the single-day processing job
# =============================================================================
# Raw files must already be staged (stage-raw.sh). Products land in
# $HPC_PRODUCTS_DIR/<output-container>/<day>/ for push-products.sh.
#
# Pipeline flags after `--` are passed through to process_from_raw.py.
# --denoise-config takes a LOCAL path; the file is copied to the cluster.
#
# Usage:
#   ./scripts/hpc/submit-day.sh --cruise SD_X --day 2023-10-10 \
#       [--cpus 16] [--constraint highmem] [--qos debug] [--time 02:00:00] \
#       [--denoise-config presets/x.toml --preset-key x] [--render-only] [--detach] \
#       [-- --skip-pmtiles ...]
#
#   Several days as one Slurm job array (one day per task, at most K at once):
#   ./scripts/hpc/submit-day.sh --cruise SD_X --days d1,d2,d3 --array-limit 4 ...
# =============================================================================
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/hpc/lib.sh
. "$SCRIPT_DIR/lib.sh"

CRUISE=""; DAY=""; CPUS=16; WALLTIME="03:00:00"; QOS="$HPC_QOS"
CONSTRAINT="$HPC_CONSTRAINT"; PARTITION="$HPC_PARTITION"
OUTPUT_CONTAINER=""; DENOISE_CONFIG=""; PRESET_KEY=""
PARALLEL_WORKERS=1; DASK_WORKERS=1; DASK_MEMORY=""
RENDER_ONLY=0; DETACH=0; TEST_ONLY=0; DAYS=""; ARRAY_LIMIT=4
PIPELINE_ARGS=()
while [ $# -gt 0 ]; do
    case "$1" in
        --cruise)           CRUISE="${2:?}"; shift ;;
        --day)              DAY="${2:?}"; shift ;;
        --days)             DAYS="${2:?}"; shift ;;
        --array-limit)      ARRAY_LIMIT="${2:?}"; shift ;;
        --cpus)             CPUS="${2:?}"; shift ;;
        --time)             WALLTIME="${2:?}"; shift ;;
        --qos)              QOS="${2:?}"; shift ;;
        --debug-qos)        QOS="$HPC_DEBUG_QOS" ;;
        --constraint)       CONSTRAINT="${2-}"; shift ;;
        --partition)        PARTITION="${2:?}"; shift ;;
        --output-container) OUTPUT_CONTAINER="${2:?}"; shift ;;
        --denoise-config)   DENOISE_CONFIG="${2:?}"; shift ;;
        --preset-key)       PRESET_KEY="${2:?}"; shift ;;
        --parallel-workers) PARALLEL_WORKERS="${2:?}"; shift ;;
        --dask-workers)     DASK_WORKERS="${2:?}"; shift ;;
        --dask-memory)      DASK_MEMORY="${2:?}"; shift ;;
        --render-only)      RENDER_ONLY=1 ;;
        --test-only)        TEST_ONLY=1 ;;
        --detach)           DETACH=1 ;;
        --)                 shift; PIPELINE_ARGS=("$@"); break ;;
        -h|--help)          sed -n '2,17p' "$0" >&2; exit 0 ;;
        *)                  fail "Unknown argument: $1" ;;
    esac
    shift
done
[ -n "$CRUISE" ] && { [ -n "$DAY" ] || [ -n "$DAYS" ]; } || fail "Specify --cruise and --day or --days"
require_site
OUTPUT_CONTAINER="${OUTPUT_CONTAINER:-$CRUISE}"

ARRAY=""; DAYS_FILE=""; LOG_SUFFIX="%j"; N_DAYS=1
if [ -n "$DAYS" ]; then
    DAY_LIST="$(printf '%s' "$DAYS" | tr ',' '\n' | grep -E '^[0-9]{4}-[0-9]{2}-[0-9]{2}$' || true)"
    N_DAYS="$(printf '%s\n' "$DAY_LIST" | grep -c . || true)"
    [ "$N_DAYS" -eq "$(printf '%s' "$DAYS" | tr ',' '\n' | grep -c .)" ] || fail "Bad day in --days"
    DAY="$(printf '%s\n' "$DAY_LIST" | head -1)"
    ARRAY="0-$((N_DAYS - 1))%${ARRAY_LIMIT}"
    LOG_SUFFIX="%A_%a"
    DAYS_FILE="$HPC_TMP_DIR/days-${CRUISE}-$(date -u +%Y%m%dT%H%M%S).txt"
fi
JOB_NAME="os-${CRUISE}-${DAY}"
[ -n "$ARRAY" ] && JOB_NAME="os-${CRUISE}-array"

PER_CORE="$HPC_MEM_PER_CORE_GB"
[ -n "$CONSTRAINT" ] && PER_CORE="$HPC_MEM_PER_CORE_GB_CONSTRAINED"
MEM_BUDGET_GB=$(( CPUS * PER_CORE ))
# Leave a quarter of the budget for the parent process and page cache.
DASK_MEMORY="${DASK_MEMORY:-$(( MEM_BUDGET_GB * 3 / 4 / DASK_WORKERS ))GB}"

if [ -n "$DENOISE_CONFIG" ]; then
    [ -f "$DENOISE_CONFIG" ] || fail "No such file: $DENOISE_CONFIG"
    REMOTE_CFG="$HPC_ROOT/presets/$(basename "$DENOISE_CONFIG")"
    if [ "$RENDER_ONLY" = "0" ]; then
        hpc_ssh_login "mkdir -p '$HPC_ROOT/presets' && cat > '$REMOTE_CFG'" < "$DENOISE_CONFIG" 2>/dev/null \
            || fail "Could not copy $DENOISE_CONFIG"
    fi
    PIPELINE_ARGS=(--denoise-config "$REMOTE_CFG" "${PIPELINE_ARGS[@]+"${PIPELINE_ARGS[@]}"}")
fi
[ -n "$PRESET_KEY" ] && PIPELINE_ARGS=(--preset-key "$PRESET_KEY" "${PIPELINE_ARGS[@]+"${PIPELINE_ARGS[@]}"}")

# Quote each pass-through arg for the rendered shell script.
ARGS_STR=""
for a in "${PIPELINE_ARGS[@]+"${PIPELINE_ARGS[@]}"}"; do ARGS_STR="$ARGS_STR $(printf '%q' "$a")"; done

MODULE_LOAD=""
[ -n "$HPC_MODULES" ] && MODULE_LOAD="module load $HPC_MODULES"

render() {
    local esc_args; esc_args="$(printf '%s' "$ARGS_STR" | sed 's/[&|\\]/\\&/g')"
    sed -e "s|@JOB_NAME@|${JOB_NAME}|g" -e "s|@ACCOUNT@|${HPC_ACCOUNT}|g" \
        -e "s|@QOS@|${QOS}|g" -e "s|@PARTITION@|${PARTITION}|g" \
        -e "s|@CONSTRAINT@|${CONSTRAINT}|g" -e "s|@CPUS@|${CPUS}|g" \
        -e "s|@WALLTIME@|${WALLTIME}|g" -e "s|@LOG_DIR@|${HPC_LOG_DIR}|g" \
        -e "s|@CRUISE@|${CRUISE}|g" -e "s|@DAY@|${DAY}|g" \
        -e "s|@CODE_DIR@|${HPC_CODE_DIR}|g" -e "s|@VENV@|${HPC_VENV}|g" \
        -e "s|@RAW_DIR@|${HPC_RAW_DIR}|g" -e "s|@PRODUCTS_DIR@|${HPC_PRODUCTS_DIR}|g" \
        -e "s|@JOB_TMP@|${HPC_JOB_TMP}|g" -e "s|@MEM_BUDGET_GB@|${MEM_BUDGET_GB}|g" \
        -e "s|@MODULE_LOAD@|${MODULE_LOAD}|g" -e "s|@OUTPUT_CONTAINER@|${OUTPUT_CONTAINER}|g" \
        -e "s|@PARALLEL_WORKERS@|${PARALLEL_WORKERS}|g" -e "s|@DASK_WORKERS@|${DASK_WORKERS}|g" \
        -e "s|@DASK_MEMORY@|${DASK_MEMORY}|g" -e "s|@PIPELINE_ARGS@|${esc_args}|g" \
        -e "s|@ARRAY@|${ARRAY}|g" -e "s|@DAYS_FILE@|${DAYS_FILE}|g" -e "s|@LOG_SUFFIX@|${LOG_SUFFIX}|g" \
        "$PROJECT_DIR/deploy/slurm/process-day.sbatch" \
    | { if [ -z "$CONSTRAINT" ]; then grep -v '^#SBATCH --constraint='; else cat; fi; } \
    | { if [ -z "$PARTITION" ]; then grep -v '^#SBATCH --partition='; else cat; fi; } \
    | { if [ -z "$QOS" ]; then grep -v '^#SBATCH --qos='; else cat; fi; } \
    | { if [ -z "$ARRAY" ]; then grep -v '^#SBATCH --array='; else cat; fi; }
}

RENDERED="$(render)"
[ "$RENDER_ONLY" = "1" ] && { printf '%s\n' "$RENDERED"; exit 0; }

if [ "$TEST_ONLY" = "1" ]; then
    printf '%s\n' "$RENDERED" | hpc_ssh_login "cat > '$HPC_TMP_DIR/${JOB_NAME}.test.sbatch' && sbatch --test-only '$HPC_TMP_DIR/${JOB_NAME}.test.sbatch'" 2>&1 \
        | grep -E 'sbatch|error|to start' >&2
    exit 0
fi

if [ -n "$DAYS_FILE" ]; then
    MISSING="$(printf '%s\n' "$DAY_LIST" | hpc_ssh_login "mkdir -p '$HPC_TMP_DIR' && tee '$DAYS_FILE' | while read -r d; do ls '$HPC_RAW_DIR/$CRUISE/'\$d/*.raw >/dev/null 2>&1 || echo \$d; done" 2>/dev/null)"
    [ -z "$MISSING" ] || fail "No raw files staged for: $(printf '%s' "$MISSING" | tr '\n' ' ')"
    log "${CRUISE}: ${N_DAYS} days as array ${ARRAY}; per task ${CPUS} cpus (${CONSTRAINT:-no constraint}, ${MEM_BUDGET_GB} GB), qos=${QOS}, ${WALLTIME}"
else
    N_RAW="$(hpc_ssh_login "ls '$HPC_RAW_DIR/$CRUISE/$DAY'/*.raw 2>/dev/null | wc -l" 2>/dev/null | tr -d ' ')"
    [ "${N_RAW:-0}" -gt 0 ] || fail "No raw files staged at $HPC_RAW_DIR/$CRUISE/$DAY — run stage-raw.sh"
    log "${CRUISE} ${DAY}: ${N_RAW} raw files; ${CPUS} cpus (${CONSTRAINT:-no constraint}, ${MEM_BUDGET_GB} GB), qos=${QOS}, ${WALLTIME}"
fi

JOBID="$(printf '%s\n' "$RENDERED" | hpc_submit "$JOB_NAME" | sed -n 's/^jobid=//p')"
ok "Submitted job ${JOBID}"
printf 'jobid=%s\n' "$JOBID"
info "log: ${HPC_LOG_DIR}/${JOB_NAME}-${JOBID}$([ -n "$ARRAY" ] && echo '_<task>').out"
[ "$DETACH" = "1" ] && exit 0

FINAL="$(hpc_wait "$JOBID" "$JOB_NAME")" || true
info "final:"; printf '%s\n' "$FINAL" | sed 's/^/      /' >&2
if [ -n "$ARRAY" ]; then
    BAD="$(printf '%s\n' "$FINAL" | grep -v '|COMPLETED|0:0|' | grep . || true)"
    [ -z "$BAD" ] || fail "Some array tasks did not complete cleanly"
    ok "All ${N_DAYS} days processed"
else
    hpc_ssh_login "tail -25 '${HPC_LOG_DIR}/${JOB_NAME}-${JOBID}.out'" 2>/dev/null >&2 || true
    printf '%s' "$FINAL" | grep -q '|COMPLETED|0:0|' || fail "Job ${JOBID} did not complete cleanly"
    ok "Day ${DAY} processed"
fi
