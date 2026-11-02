#!/usr/bin/env bash
# =============================================================================
# Print the group's `bsc_quota` table (run on the transfer host)
# =============================================================================
# flowlib.parse_bsc_quota reads it to check scratch headroom before a batch.
#
# Usage:
#   ./scripts/hpc/quota.sh
# =============================================================================
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/hpc/lib.sh
. "$SCRIPT_DIR/lib.sh"

require_hpc_key
hpc_ssh_xfer "bsc_quota 2>/dev/null" || fail "bsc_quota failed on $HPC_XFER_HOST"
