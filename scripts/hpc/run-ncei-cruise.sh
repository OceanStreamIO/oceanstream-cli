#!/usr/bin/env bash
# =============================================================================
# Process a whole NCEI cruise on the cluster, a batch of days at a time
# =============================================================================
# Raw files stream from NCEI's public bucket straight to cluster scratch over
# SSH (xfer.py --opendata), one batch of days at a time, so scratch never holds
# the whole cruise. Each batch runs as one Slurm array (submit-day.sh) and its
# raw files are removed as soon as the array ends. The finished days are then
# pushed to S3, verified and removed from scratch in the background, while the
# next batch stages and runs; at most one push runs at a time.
#
# Progress goes to a state file, one line per event. Running the same command
# again resumes: days already pushed are skipped, and days whose products are
# still on scratch are pushed without being processed again.
#
# Usage:
#   ./scripts/hpc/run-ncei-cruise.sh --ship Roger_Revelle --cruise RR2205 \
#       --days-file days.txt --batch 6 --dest hpc/products/RR2205 \
#       --state ~/work/hpc-runs/rr2205/state.tsv \
#       [--instrument EK80] [--container RR2205] [--cpus 16] [--time 04:00:00] \
#       [--preset NAME] -- <process_from_raw.py flags>
# =============================================================================
set -uo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/hpc/lib.sh
. "$SCRIPT_DIR/lib.sh"

SHIP=""; CRUISE=""; INSTRUMENT="EK80"; DAYS_FILE=""; BATCH=6; DEST=""; STATE=""
CONTAINER=""; CPUS=16; WALLTIME="04:00:00"; PRESET=""; PIPELINE_ARGS=()
while [ $# -gt 0 ]; do
    case "$1" in
        --ship)        SHIP="${2:?}"; shift ;;
        --cruise)      CRUISE="${2:?}"; shift ;;
        --instrument)  INSTRUMENT="${2:?}"; shift ;;
        --days-file)   DAYS_FILE="${2:?}"; shift ;;
        --batch)       BATCH="${2:?}"; shift ;;
        --dest)        DEST="${2:?}"; shift ;;
        --state)       STATE="${2:?}"; shift ;;
        --container)   CONTAINER="${2:?}"; shift ;;
        --cpus)        CPUS="${2:?}"; shift ;;
        --time)        WALLTIME="${2:?}"; shift ;;
        --preset)      PRESET="${2:?}"; shift ;;
        --)            shift; PIPELINE_ARGS=("$@"); break ;;
        -h|--help)     sed -n '2,22p' "$0" >&2; exit 0 ;;
        *)             fail "Unknown argument: $1" ;;
    esac
    shift
done
[ -n "$SHIP" ] && [ -n "$CRUISE" ] && [ -n "$DAYS_FILE" ] && [ -n "$DEST" ] && [ -n "$STATE" ] \
    || fail "Specify --ship, --cruise, --days-file, --dest and --state"
[ -f "$DAYS_FILE" ] || fail "No days file at $DAYS_FILE"
CONTAINER="${CONTAINER:-$CRUISE}"
SRC="s3://noaa-wcsd-pds/data/raw/${SHIP}/${CRUISE}/${INSTRUMENT}/"
# NCEI's bucket is in us-east-1; xfer.py's open-data client defaults elsewhere.
export HPC_OPENDATA_REGION="${HPC_OPENDATA_REGION:-us-east-1}"
# botocore applies AWS_ENDPOINT_URL to every client it makes, which would send the
# open-data client to our own endpoint (NoSuchBucket). Uploads are unaffected:
# xfer.py passes our endpoint explicitly, from the site settings.
unset AWS_ENDPOINT_URL AWS_ENDPOINT_URL_S3
# Products are thousands of small Zarr chunks, one SSH channel each: more streams.
PUSH_STREAMS="${PUSH_STREAMS:-8}"
mkdir -p "$(dirname "$STATE")"
touch "$STATE"

mark() { printf '%s\t%s\t%s\n' "$1" "$2" "$(date -u +%FT%TZ)" >> "$STATE"; }
pushed() { grep -q "^$1	pushed	" "$STATE"; }
on_scratch() { hpc_ssh_xfer "test -d '$HPC_PRODUCTS_DIR/$CONTAINER/$1'" 2>/dev/null; }

# push_days DAY...: push, verify and free the days' products; one state line per day.
push_days() {
    if HPC_XFER_STREAMS="$PUSH_STREAMS" "$SCRIPT_DIR/push-products.sh" --container "$CONTAINER" \
            --dest "$DEST" --days "$(IFS=,; echo "$*")" --cleanup; then
        for day in "$@"; do mark "$day" pushed; done
    else
        for day in "$@"; do mark "$day" failed-push; done
    fi
}

PUSH_PID=""
wait_push() { [ -z "$PUSH_PID" ] || wait "$PUSH_PID"; PUSH_PID=""; }
push_in_background() {
    [ $# -gt 0 ] || return 0
    wait_push
    log "Pushing in the background: $*"
    push_days "$@" &
    PUSH_PID=$!
}

PENDING=()
while read -r day; do
    [ -n "$day" ] || continue
    pushed "$day" || PENDING+=("$day")
done < "$DAYS_FILE"
log "${CRUISE}: ${#PENDING[@]} days to process, ${BATCH} per batch; state in ${STATE}"

for ((i = 0; i < ${#PENDING[@]}; i += BATCH)); do
    batch=("${PENDING[@]:i:BATCH}")
    log "Batch $((i / BATCH + 1)): ${batch[*]}"

    # Days processed before a restart only need their push, and their raw files removed.
    ready=(); todo=()
    for day in "${batch[@]}"; do
        if on_scratch "$day"; then
            ready+=("$day")
            hpc_ssh_xfer "rm -rf '$HPC_RAW_DIR/$CRUISE/$day'" 2>/dev/null || warn "Could not remove raw $day"
        else
            todo+=("$day")
        fi
    done
    [ ${#ready[@]} -eq 0 ] || push_in_background "${ready[@]}"

    staged=()
    for day in "${todo[@]}"; do
        if hpc_xfer pull --opendata --src "$SRC" --include "D${day//-/}-T*.raw" \
                --dest "$HPC_RAW_DIR/$CRUISE/$day"; then
            staged+=("$day"); mark "$day" staged
        else
            mark "$day" failed-stage
        fi
    done
    [ ${#staged[@]} -gt 0 ] || continue

    SUBMIT=("$SCRIPT_DIR/submit-day.sh" --cruise "$CRUISE"
            --days "$(IFS=,; echo "${staged[*]}")" --array-limit "${#staged[@]}"
            --cpus "$CPUS" --time "$WALLTIME" --output-container "$CONTAINER")
    [ -n "$PRESET" ] && SUBMIT+=(--preset "$PRESET")
    "${SUBMIT[@]}" -- "${PIPELINE_ARGS[@]}" || warn "Some days of the batch failed"

    done_days=()
    for day in "${staged[@]}"; do
        # The raw files are NCEI's; a rerun streams them again.
        hpc_ssh_xfer "rm -rf '$HPC_RAW_DIR/$CRUISE/$day'" 2>/dev/null || warn "Could not remove raw $day"
        if on_scratch "$day"; then done_days+=("$day"); else mark "$day" failed-process; fi
    done
    [ ${#done_days[@]} -eq 0 ] || push_in_background "${done_days[@]}"
done
wait_push

done_n=$(cut -f1,2 "$STATE" | grep -c "	pushed$" || true)
log "Finished: ${done_n} days pushed; see ${STATE} for failures"
