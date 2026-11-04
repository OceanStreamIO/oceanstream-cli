#!/usr/bin/env bash
# =============================================================================
# Create/refresh the submitter's ConfigMap and SSH Secret from local files.
# =============================================================================
#   oceanstream-hpc-site  (ConfigMap) <- the site file (oceanstream.hpc.site), minus local-only keys
#   oceanstream-hpc-ssh   (Secret)    <- $HPC_SSH_KEY + known_hosts entries for the hosts
#
# Values are never printed. Usage:
#   ./deploy/edito/bootstrap-hpc-secrets.sh -n <namespace>
# =============================================================================
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SITE_ENV="${HPC_SITE_FILE:-$ROOT/scripts/hpc/site.env}"
ok()   { printf '  ✓ %s\n' "$*" >&2; }
fail() { printf '  ✗ %s\n' "$*" >&2; exit 1; }

NS=""
while [ $# -gt 0 ]; do
    case "$1" in
        -n|--namespace) NS="${2:?}"; shift ;;
        *) fail "Unknown argument: $1" ;;
    esac
    shift
done
[ -n "$NS" ] || fail "Specify -n <namespace>"
command -v kubectl >/dev/null && command -v ssh-keygen >/dev/null || fail "Needs kubectl and ssh-keygen"
[ -f "$SITE_ENV" ] || fail "$SITE_ENV not found (see scripts/hpc/site.env.example)"
set -a; . "$SITE_ENV"; set +a
[ -f "${HPC_SSH_KEY:-}" ] || fail "No SSH key at ${HPC_SSH_KEY:-<unset>} (HPC_SSH_KEY)"

TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
chmod 700 "$TMP"

# Site ConfigMap: drop local paths that differ inside the pod.
grep -E '^[A-Z_]+=' "$SITE_ENV" | grep -vE '^(HPC_SSH_KEY|HPC_KNOWN_HOSTS)=' \
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
