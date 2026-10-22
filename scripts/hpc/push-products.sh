#!/usr/bin/env bash
# =============================================================================
# Push one or more processed days from cluster scratch to S3
# =============================================================================
# Source: $HPC_PRODUCTS_DIR/<container>/<day>/
# Dest:   s3://$OCEANSTREAM_S3_BUCKET/$OCEANSTREAM_S3_PRODUCTS_PREFIX/<container>/<day>/
#
# Read over SSH and uploaded from this side: the cluster may not connect out.
# A plain push never deletes at the destination; `--replace` also removes what
# the day no longer has, once every upload has succeeded, so a reprocessed day
# replaces the old one exactly. The STAC item.json is written by publish-side
# tooling AFTER this push, so its presence marks a complete day.
#
# Usage:
#   ./scripts/hpc/push-products.sh --container SD_X --day 2023-10-10
#   ./scripts/hpc/push-products.sh --container SD_X --days d1,d2 --verify
#   ./scripts/hpc/push-products.sh --container SD_X --day d1 --cleanup   # free scratch after a verified push
#   ./scripts/hpc/push-products.sh --container run-x --dest hpc/products/SD_X --day d1
# =============================================================================
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/hpc/lib.sh
. "$SCRIPT_DIR/lib.sh"

CONTAINER=""; DEST=""; DAYS=""; VERIFY_ONLY=0; CLEANUP=0; REPLACE=0
while [ $# -gt 0 ]; do
    case "$1" in
        --container)  CONTAINER="${2:?}"; shift ;;
        --dest)       DEST="${2:?}"; shift ;;   # S3 key prefix (default: products prefix/<container>)
        --day|--days) DAYS="${2:?}"; shift ;;
        --verify)     VERIFY_ONLY=1 ;;
        --replace)    REPLACE=1 ;;
        --cleanup)    CLEANUP=1 ;;
        -h|--help)    sed -n '2,16p' "$0" >&2; exit 0 ;;
        *)            fail "Unknown argument: $1" ;;
    esac
    shift
done
[ -n "$CONTAINER" ] && [ -n "$DAYS" ] || fail "Specify --container and --day(s)"
require_hpc_key
DEST="${DEST:-$OCEANSTREAM_S3_PRODUCTS_PREFIX/$CONTAINER}"

# One push and one check for all days: --include filters keep other days out of
# both, and --replace only deletes destination objects the filters match.
SRC_ROOT="$HPC_PRODUCTS_DIR/$CONTAINER"
DST_ROOT="$(s3_uri "$DEST/")"
INCLUDES=(); PRESENT=""; rc=0
for d in $(printf '%s' "$DAYS" | tr ',' ' '); do
    case "$d" in [0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]) ;; *) fail "Bad day: $d" ;; esac
    if hpc_ssh_xfer "test -d '$SRC_ROOT/$d'" 2>/dev/null; then
        INCLUDES+=(--include "${d}/*"); PRESENT="$PRESENT $d"
    else
        warn "$d: nothing at $SRC_ROOT/$d"; rc=1
    fi
done
[ -n "$PRESENT" ] || fail "No products to push"
N="$(printf '%s' "$PRESENT" | wc -w | tr -d ' ')"
info "$N day(s), $(hpc_ssh_xfer "cd '$SRC_ROOT' && du -shc $PRESENT | tail -1 | cut -f1" 2>/dev/null) total"

if [ "$VERIFY_ONLY" = "0" ]; then
    REPLACE_FLAG=(); [ "$REPLACE" = "1" ] && REPLACE_FLAG=(--replace --always)
    hpc_xfer push --src "$SRC_ROOT" --dest "$DST_ROOT" "${INCLUDES[@]}" \
        ${REPLACE_FLAG[@]+"${REPLACE_FLAG[@]}"} \
        || fail "push failed"
    ok "pushed $N day(s)"
fi

if hpc_xfer check --src "$SRC_ROOT" --dest "$DST_ROOT" "${INCLUDES[@]}"; then
    ok "S3 matches scratch for $N day(s)"
    if [ "$CLEANUP" = "1" ]; then
        for d in $PRESENT; do
            case "$SRC_ROOT/$d" in */products/?*/????-??-??) ;; *) fail "Refusing to remove unexpected path: $SRC_ROOT/$d" ;; esac
            hpc_ssh_xfer "rm -rf '$SRC_ROOT/$d'" && ok "$d: scratch freed"
        done
    fi
else
    warn "differences found"; rc=1
fi
exit "$rc"
