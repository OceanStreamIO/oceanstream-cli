#!/usr/bin/env bash
# =============================================================================
# Push one or more processed days from cluster scratch to S3
# =============================================================================
# Source: $HPC_PRODUCTS_DIR/<container>/<day>/
# Dest:   s3://$OCEANSTREAM_S3_BUCKET/$OCEANSTREAM_S3_PRODUCTS_PREFIX/<container>/<day>/
#
# `copy` never deletes at the destination; `--replace` uses `sync` so a
# reprocessed day replaces the old one exactly. The STAC item.json is written
# by publish-side tooling AFTER this push, so its presence marks a complete day.
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

CONTAINER=""; DEST=""; DAYS=""; VERIFY_ONLY=0; CLEANUP=0; OP=copy
while [ $# -gt 0 ]; do
    case "$1" in
        --container)  CONTAINER="${2:?}"; shift ;;
        --dest)       DEST="${2:?}"; shift ;;   # S3 key prefix (default: products prefix/<container>)
        --day|--days) DAYS="${2:?}"; shift ;;
        --verify)     VERIFY_ONLY=1 ;;
        --replace)    OP=sync ;;
        --cleanup)    CLEANUP=1 ;;
        -h|--help)    sed -n '2,16p' "$0" >&2; exit 0 ;;
        *)            fail "Unknown argument: $1" ;;
    esac
    shift
done
[ -n "$CONTAINER" ] && [ -n "$DAYS" ] || fail "Specify --container and --day(s)"
require_hpc_key
DEST="${DEST:-$OCEANSTREAM_S3_PRODUCTS_PREFIX/$CONTAINER}"

rc=0
for d in $(printf '%s' "$DAYS" | tr ',' ' '); do
    case "$d" in [0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]) ;; *) fail "Bad day: $d" ;; esac
    SRC="$HPC_PRODUCTS_DIR/$CONTAINER/$d"
    DST="$(s3_remote "$DEST/$d")"
    hpc_ssh_xfer "test -d '$SRC'" 2>/dev/null || { warn "$d: nothing at $SRC"; rc=1; continue; }
    info "$d: $(hpc_ssh_xfer "du -sh '$SRC' | cut -f1" 2>/dev/null)"

    if [ "$VERIFY_ONLY" = "0" ]; then
        OUT="$(hpc_xfer_rclone "push-$d" "$OP" "$SRC" "$DST" --transfers=32 --checkers=64 --fast-list \
            --retries=3 --low-level-retries=10 --stats=60s --stats-one-line)" || { warn "$d: submit failed"; rc=1; continue; }
        printf '%s' "$OUT" | grep -q '^exit=0$' || { printf '%s\n' "$OUT" >&2; warn "$d: push failed"; rc=1; continue; }
        ok "$d: pushed"
    fi

    OUT="$(hpc_xfer_rclone "check-$d" check "$SRC" "$DST" --one-way --size-only --fast-list)" || true
    if printf '%s' "$OUT" | grep -q '^exit=0$'; then
        ok "$d: S3 matches scratch"
        if [ "$CLEANUP" = "1" ]; then
            case "$SRC" in */products/?*/????-??-??) hpc_ssh_xfer "rm -rf '$SRC'" && ok "$d: scratch freed" ;;
                           *) fail "Refusing to remove unexpected path: $SRC" ;; esac
        fi
    else
        printf '%s\n' "$OUT" | tail -8 >&2; warn "$d: differences found"; rc=1
    fi
done
exit "$rc"
