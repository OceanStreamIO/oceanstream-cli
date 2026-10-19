#!/usr/bin/env bash
# =============================================================================
# Stage raw files for one or more days from S3 onto cluster scratch
# =============================================================================
# Source: s3://$OCEANSTREAM_S3_BUCKET/$OCEANSTREAM_S3_RAW_PREFIX/<cruise>/<day>/
# (filled by mirror_raw_to_s3.py). Dest:  $HPC_RAW_DIR/<cruise>/<day>/
# Streamed from object storage through this machine and over SSH: the cluster
# may not connect out itself. Nothing at the destination is deleted, and a
# re-run resumes, skipping files already there at their full size.
#
# Usage:
#   ./scripts/hpc/stage-raw.sh --cruise SD_X --day 2023-10-10
#   ./scripts/hpc/stage-raw.sh --cruise SD_X --days 2023-10-10,2023-10-11
#   ./scripts/hpc/stage-raw.sh --cruise SD_X --gps     # the cruise's GPS GeoParquet
# =============================================================================
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/hpc/lib.sh
. "$SCRIPT_DIR/lib.sh"

CRUISE=""; DAYS=""; GPS=0
while [ $# -gt 0 ]; do
    case "$1" in
        --cruise)     CRUISE="${2:?}"; shift ;;
        --day|--days) DAYS="${2:?}"; shift ;;
        --gps)        GPS=1 ;;
        -h|--help)    sed -n '2,13p' "$0" >&2; exit 0 ;;
        *)            fail "Unknown argument: $1" ;;
    esac
    shift
done
[ -n "$CRUISE" ] || fail "Specify --cruise"
require_hpc_key

if [ "$GPS" = "1" ]; then
    DST="$HPC_GPS_DIR/$CRUISE"
    hpc_xfer pull --src "$(s3_uri "$OCEANSTREAM_S3_GPS_PREFIX/$CRUISE/")" --dest "$DST" \
        || fail "GPS staging failed"
    info "$(hpc_ssh_login "find '$DST' -name '*.parquet' | wc -l; du -sh '$DST' | cut -f1" 2>/dev/null | tr '\n' ' ')"
    ok "GPS staged at $DST"
    exit 0
fi
[ -n "$DAYS" ] || fail "Specify --day(s) or --gps"

SRC_ROOT="${OCEANSTREAM_S3_RAW_PREFIX}/${CRUISE}"
DST_ROOT="${HPC_RAW_DIR}/${CRUISE}"

# One pull for all days: --include filters keep the other days out.
INCLUDES=()
for d in $(printf '%s' "$DAYS" | tr ',' ' '); do
    case "$d" in [0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]) ;; *) fail "Bad day: $d" ;; esac
    INCLUDES+=(--include "${d}/*")
done

hpc_xfer pull --src "$(s3_uri "$SRC_ROOT/")" --dest "$DST_ROOT" "${INCLUDES[@]}" \
    || fail "Staging failed"

for d in $(printf '%s' "$DAYS" | tr ',' ' '); do
    info "$d: $(hpc_ssh_login "ls '$DST_ROOT/$d'/*.raw 2>/dev/null | wc -l; du -sh '$DST_ROOT/$d' 2>/dev/null | cut -f1" 2>/dev/null | tr '\n' ' ')"
done
ok "Staged under ${DST_ROOT}"
