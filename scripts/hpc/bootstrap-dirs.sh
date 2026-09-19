#!/usr/bin/env bash
# =============================================================================
# Create the oceanstream directory tree on the cluster and report free space
# =============================================================================
# Persistent (code, venv, wheelhouse, logs) under $HPC_PROJ/oceanstream,
# regenerable (raw, products, tmp) under $HPC_SCRATCH/oceanstream. Safe to re-run.
#
# Usage:
#   ./scripts/hpc/bootstrap-dirs.sh                  # create + report
#   ./scripts/hpc/bootstrap-dirs.sh --require-gb 150 # fail below this headroom
# =============================================================================
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/hpc/lib.sh
. "$SCRIPT_DIR/lib.sh"

REQUIRE_GB=0
while [ $# -gt 0 ]; do
    case "$1" in
        --require-gb) REQUIRE_GB="${2:?--require-gb needs a value}"; shift ;;
        -h|--help)    sed -n '2,11p' "$0" >&2; exit 0 ;;
        *)            fail "Unknown argument: $1" ;;
    esac
    shift
done
require_hpc_key

log "Creating ${HPC_ROOT} and ${HPC_STAGE}"
hpc_ssh_login "
    set -eu
    mkdir -p '$HPC_CODE_DIR' '$HPC_WHEELHOUSE' '$HPC_LOG_DIR' '$HPC_TMP_DIR' \
             '$HPC_RAW_DIR' '$HPC_PRODUCTS_DIR' '$HPC_JOB_TMP'
    chmod 2770 '$HPC_ROOT' '$HPC_STAGE' 2>/dev/null || true
    ls -ld '$HPC_ROOT'/* '$HPC_STAGE'/*
" >&2

AVAIL_KB="$(hpc_ssh_login "df -Pk '$HPC_STAGE' | awk 'NR==2 {print \$4}'" 2>/dev/null)"
case "$AVAIL_KB" in ''|*[!0-9]*) fail "Unexpected df output: '$AVAIL_KB'" ;; esac
AVAIL_GB=$(( AVAIL_KB / 1024 / 1024 ))
hpc_ssh_login "command -v bsc_quota >/dev/null && bsc_quota 2>/dev/null | grep -iE 'filesystem|projects|scratch' || true" >&2 || true
info "df free on ${HPC_STAGE}: ${AVAIL_GB} GB (a group quota may be lower — see above)"
[ "$AVAIL_GB" -ge "$REQUIRE_GB" ] || fail "Only ${AVAIL_GB} GB free, need ${REQUIRE_GB} GB"
ok "Layout ready"
