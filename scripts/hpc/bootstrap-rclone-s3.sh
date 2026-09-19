#!/usr/bin/env bash
# =============================================================================
# Install/rotate the S3 rclone remote on the transfer node
# =============================================================================
# Credentials are read from the environment (or the repo .env) and piped over
# SSH. They are never echoed or written anywhere but the remote rclone.conf,
# which is created 0600. Any existing stanza with the same name is replaced.
#
# Env: OCEANSTREAM_S3_ACCESS_KEY_ID / OCEANSTREAM_S3_SECRET_ACCESS_KEY
#      (fallbacks: S3_ACCESS_KEY_ID / S3_SECRET_ACCESS_KEY, AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY)
#
# Usage:
#   ./scripts/hpc/bootstrap-rclone-s3.sh            # install + verify
#   ./scripts/hpc/bootstrap-rclone-s3.sh --verify   # verify only
# =============================================================================
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/hpc/lib.sh
. "$SCRIPT_DIR/lib.sh"

VERIFY_ONLY=0
while [ $# -gt 0 ]; do
    case "$1" in
        --verify)  VERIFY_ONLY=1 ;;
        -h|--help) sed -n '2,15p' "$0" >&2; exit 0 ;;
        *)         fail "Unknown argument: $1" ;;
    esac
    shift
done
require_hpc_key

if [ -f "${OCEANSTREAM_ENV_FILE:-$PROJECT_DIR/.env}" ]; then
    set -a
    # shellcheck disable=SC1090
    . "${OCEANSTREAM_ENV_FILE:-$PROJECT_DIR/.env}"
    set +a
fi
AK="${OCEANSTREAM_S3_ACCESS_KEY_ID:-${S3_ACCESS_KEY_ID:-${AWS_ACCESS_KEY_ID:-}}}"
SK="${OCEANSTREAM_S3_SECRET_ACCESS_KEY:-${S3_SECRET_ACCESS_KEY:-${AWS_SECRET_ACCESS_KEY:-}}}"
EP="$OCEANSTREAM_S3_ENDPOINT"
case "$EP" in http://*|https://*|'') ;; *) EP="https://$EP" ;; esac
for v in AK SK EP OCEANSTREAM_S3_BUCKET; do
    eval "val=\${$v:-}"; [ -n "$val" ] || fail "$v is empty"
done
REMOTE="$HPC_RCLONE_REMOTE"

if [ "$VERIFY_ONLY" = "0" ]; then
    log "Writing [$REMOTE] on ${HPC_XFER_HOST} (access key $(printf '%s' "$AK" | cut -c1-4)****)"
    printf '[%s]\ntype = s3\nprovider = Ceph\nendpoint = %s\nregion = %s\naccess_key_id = %s\nsecret_access_key = %s\nforce_path_style = true\n' \
        "$REMOTE" "$EP" "${OCEANSTREAM_S3_REGION:-}" "$AK" "$SK" \
    | hpc_ssh_xfer "
        set -eu
        umask 077
        d=\"\$HOME/.config/rclone\"; c=\"\$d/rclone.conf\"
        mkdir -p \"\$d\"; chmod 700 \"\$d\"
        stanza=\"\$(cat)\"
        if [ -f \"\$c\" ]; then
            awk -v s='[$REMOTE]' 'BEGIN{skip=0} \$0==s {skip=1; next} /^\[/ {skip=0} {if(!skip) print}' \"\$c\" > \"\$c.new\"
        else
            : > \"\$c.new\"
        fi
        printf '%s\n' \"\$stanza\" >> \"\$c.new\"
        chmod 600 \"\$c.new\"; mv -f \"\$c.new\" \"\$c\"
    " || fail "Could not write rclone.conf"
    ok "rclone.conf updated (0600)"
fi

hpc_ssh_xfer "'$HPC_RCLONE_BIN' listremotes 2>/dev/null" 2>/dev/null | grep -qx "${REMOTE}:" \
    || fail "rclone on ${HPC_XFER_HOST} does not list '${REMOTE}:'"
ok "remote '${REMOTE}:' registered — run smoke-test.sh to prove the queued path reaches S3"
