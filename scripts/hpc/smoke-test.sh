#!/usr/bin/env bash
# =============================================================================
# Prove the queued transfer path reaches the bucket
# =============================================================================
# Usage:
#   ./scripts/hpc/smoke-test.sh                 # list the bucket's top level
#   ./scripts/hpc/smoke-test.sh --prefix hpc/raw
# =============================================================================
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/hpc/lib.sh
. "$SCRIPT_DIR/lib.sh"

PREFIX=""
while [ $# -gt 0 ]; do
    case "$1" in
        --prefix)  PREFIX="${2:?--prefix needs a value}"; shift ;;
        -h|--help) sed -n '2,7p' "$0" >&2; exit 0 ;;
        *)         fail "Unknown argument: $1" ;;
    esac
    shift
done
require_hpc_key

OUT="$(hpc_xfer_rclone smoke-test lsf "$(s3_remote "$PREFIX")" --max-depth 1)" || fail "Could not submit"
printf '%s\n' "$OUT" >&2
printf '%s' "$OUT" | grep -q '^exit=0$' || fail "Smoke test FAILED"
N="$(printf '%s\n' "$OUT" | grep -cvE '^(exit=|jobid=|---)' || true)"
ok "PASS — listed ${N} entr(y/ies) under ${OCEANSTREAM_S3_BUCKET}/${PREFIX}"
