#!/usr/bin/env bash
# =============================================================================
# Create/refresh the submitter's ConfigMap and SSH Secret from local files.
# =============================================================================
#   oceanstream-hpc-site  (ConfigMap) <- scripts/hpc/site.env, minus local-only keys
#   oceanstream-hpc-ssh   (Secret)    <- $HPC_SSH_KEY + known_hosts entries for the hosts
#
# Values are never printed. Usage:
#   ./deploy/edito/bootstrap-hpc-secrets.sh -n <namespace>
# =============================================================================
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# shellcheck source=scripts/hpc/lib.sh
. "$ROOT/scripts/hpc/lib.sh"

NS=""
while [ $# -gt 0 ]; do
    case "$1" in
        -n|--namespace) NS="${2:?}"; shift ;;
        *) fail "Unknown argument: $1" ;;
    esac
    shift
done
[ -n "$NS" ] || fail "Specify -n <namespace>"
require_cmd kubectl ssh-keygen
require_hpc_key
[ -f "$HPC_LIB_DIR/site.env" ] || fail "scripts/hpc/site.env not found"

TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
chmod 700 "$TMP"

# Site ConfigMap: drop local paths that differ inside the pod.
grep -E '^[A-Z_]+=' "$HPC_LIB_DIR/site.env" | grep -vE '^(HPC_SSH_KEY|HPC_KNOWN_HOSTS)=' \
    | sed -E 's/^([A-Z_]+)="(.*)"$/\1=\2/' > "$TMP/site.env"
kubectl -n "$NS" create configmap oceanstream-hpc-site --from-env-file="$TMP/site.env" \
    --dry-run=client -o yaml | kubectl -n "$NS" apply -f - >/dev/null
ok "ConfigMap oceanstream-hpc-site ($(wc -l < "$TMP/site.env" | tr -d ' ') keys)"

# Pin host keys for exactly the hosts the pod talks to.
KH="${HPC_KNOWN_HOSTS:-$HOME/.ssh/known_hosts}"
: > "$TMP/known_hosts"
for h in "$HPC_LOGIN_HOST" "$HPC_XFER_HOST"; do
    ssh-keygen -F "$h" -f "$KH" | grep -v '^#' >> "$TMP/known_hosts" || fail "No known_hosts entry for $h — connect once first"
done
kubectl -n "$NS" create secret generic oceanstream-hpc-ssh \
    --from-file=id_ed25519="$HPC_SSH_KEY" --from-file=known_hosts="$TMP/known_hosts" \
    --dry-run=client -o yaml | kubectl -n "$NS" apply -f - >/dev/null
ok "Secret oceanstream-hpc-ssh (key + $(wc -l < "$TMP/known_hosts" | tr -d ' ') host key(s))"
