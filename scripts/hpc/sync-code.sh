#!/usr/bin/env bash
# =============================================================================
# Ship the pipeline source to the cluster (tar over SSH — no git, no rsync)
# =============================================================================
# Usage:
#   ./scripts/hpc/sync-code.sh
#   ./scripts/hpc/sync-code.sh --dry-run
# =============================================================================
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/hpc/lib.sh
. "$SCRIPT_DIR/lib.sh"

DRY_RUN=0
while [ $# -gt 0 ]; do
    case "$1" in
        --dry-run) DRY_RUN=1 ;;
        -h|--help) sed -n '2,7p' "$0" >&2; exit 0 ;;
        *)         fail "Unknown argument: $1" ;;
    esac
    shift
done
require_cmd ssh tar git
require_hpc_key

cd "$PROJECT_DIR"
PATHS="oceanstream scripts/batch_processing scripts/hpc pyproject.toml requirements-hpc.txt"
EXCLUDES="--exclude=__pycache__ --exclude=*.pyc --exclude=.pytest_cache --exclude=site.env --exclude=tests"

if [ "$DRY_RUN" = "1" ]; then
    # shellcheck disable=SC2086
    info "$(tar -cf - $EXCLUDES $PATHS | tar -tf - | wc -l | tr -d ' ') entries would be sent"
    exit 0
fi

REV="$(git rev-parse --short HEAD 2>/dev/null || echo unknown)"
# shellcheck disable=SC2086
DIRTY=""; [ -z "$(git status --porcelain -- $PATHS 2>/dev/null)" ] || DIRTY="-dirty"
log "Syncing ${REV}${DIRTY} -> ${HPC_LOGIN_HOST}:${HPC_CODE_DIR}"
# shellcheck disable=SC2086
tar -czf - $EXCLUDES $PATHS \
    | hpc_ssh_login "mkdir -p '$HPC_CODE_DIR' && tar -xzf - -C '$HPC_CODE_DIR' && echo '${REV}${DIRTY}' > '$HPC_CODE_DIR/.revision'" \
    || fail "Code sync failed"
ok "Synced ${REV}${DIRTY}"
