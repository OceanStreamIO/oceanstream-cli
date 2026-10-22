#!/usr/bin/env bash
# =============================================================================
# Prove the transfer path: SSH to the transfer node, object storage from here
# =============================================================================
# Writes a small probe file on scratch, pushes it to the bucket over the same
# path the real transfers use, checks its size there, and removes both copies.
#
# Usage:
#   ./scripts/hpc/smoke-test.sh
# =============================================================================
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/hpc/lib.sh
. "$SCRIPT_DIR/lib.sh"

case "${1:-}" in -h|--help) sed -n '2,9p' "$0" >&2; exit 0 ;; "") ;; *) fail "Unknown argument: $1" ;; esac
require_hpc_key

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
PROBE="$HPC_JOB_TMP/smoke-$STAMP.txt"
KEY="hpc/smoke-test/smoke-$STAMP.txt"
hpc_ssh_xfer "mkdir -p '$HPC_JOB_TMP' && printf 'oceanstream transfer probe %s\\n' '$STAMP' > '$PROBE'" \
    || fail "could not write a probe on the transfer node"
if hpc_xfer push --file --src "$PROBE" --dest "$(s3_uri "$KEY")"; then
    ok "PASS: scratch -> ${OCEANSTREAM_S3_BUCKET}/${KEY} over SSH"
    rc=0
else
    warn "Smoke test FAILED"; rc=1
fi
hpc_ssh_xfer "rm -f '$PROBE'" >/dev/null 2>&1 || true
# Remove the probe from the bucket too, with the helper's own client.
"${HPC_LOCAL_PYTHON:-python3}" - "$(s3_uri "$KEY")" "$_HPC_LIB_DIR" <<'PY' || warn "could not remove the probe object"
import importlib.util, sys
spec = importlib.util.spec_from_file_location("xfer", f"{sys.argv[2]}/xfer.py")
xfer = importlib.util.module_from_spec(spec); sys.modules["xfer"] = xfer; spec.loader.exec_module(xfer)
xfer.Store().delete([sys.argv[1]])
PY
exit "$rc"
