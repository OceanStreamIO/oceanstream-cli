#!/usr/bin/env bash
# =============================================================================
# oceanstream HPC helpers — shared settings and functions
# =============================================================================
# Sourced by every script in scripts/hpc/. Not executable on its own.
#
# Nothing site-specific lives in this repository. Every host, account and path
# comes from the environment, or from scripts/hpc/site.env (gitignored) when it
# exists. Copy scripts/hpc/site.env.example to get started.
#
# Assumed cluster shape (see docs/hpc.md):
#   * a login node that accepts `sbatch` for the compute partition;
#   * compute nodes WITHOUT outbound internet;
#   * a data-transfer node whose queued rclone wrapper can reach S3.
#
# Portability: must run under bash on Linux and macOS. No `readlink -f`,
# no `mapfile`, no GNU-only `date -d`.
# =============================================================================

HPC_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$HPC_LIB_DIR/../.." && pwd)"

if [ -f "$HPC_LIB_DIR/site.env" ]; then
    set -a
    # shellcheck disable=SC1091
    . "$HPC_LIB_DIR/site.env"
    set +a
fi

# ---------------------------------------------------------------------------
# Site (required — no defaults on purpose)
# ---------------------------------------------------------------------------
HPC_USER="${HPC_USER:-}"
HPC_LOGIN_HOST="${HPC_LOGIN_HOST:-}"      # sbatch for compute jobs
HPC_XFER_HOST="${HPC_XFER_HOST:-}"        # queued S3 transfers
HPC_ACCOUNT="${HPC_ACCOUNT:-}"
HPC_PROJ="${HPC_PROJ:-}"                  # persistent: code, venv, logs
HPC_SCRATCH="${HPC_SCRATCH:-}"            # regenerable: raw, products, tmp
HPC_SSH_KEY="${HPC_SSH_KEY:-}"

# ---------------------------------------------------------------------------
# Site (optional)
# ---------------------------------------------------------------------------
HPC_QOS="${HPC_QOS:-}"
HPC_DEBUG_QOS="${HPC_DEBUG_QOS:-}"
HPC_PARTITION="${HPC_PARTITION:-}"
HPC_CONSTRAINT="${HPC_CONSTRAINT:-}"
# GB of RAM per allocated core, per node feature. Sites that reject --mem
# derive memory from the core count; the job needs to know its budget.
HPC_MEM_PER_CORE_GB="${HPC_MEM_PER_CORE_GB:-2}"
HPC_MEM_PER_CORE_GB_CONSTRAINED="${HPC_MEM_PER_CORE_GB_CONSTRAINED:-$HPC_MEM_PER_CORE_GB}"
HPC_MODULES="${HPC_MODULES:-}"            # e.g. "hdf5 python/3.12"
HPC_PYTHON_MINOR="${HPC_PYTHON_MINOR:-3.12}"

# Layout. Everything is namespaced under oceanstream/ so it can share a group
# allocation with other projects.
HPC_ROOT="${HPC_ROOT:-${HPC_PROJ:+$HPC_PROJ/oceanstream}}"
HPC_STAGE="${HPC_STAGE:-${HPC_SCRATCH:+$HPC_SCRATCH/oceanstream}}"
HPC_CODE_DIR="${HPC_CODE_DIR:-$HPC_ROOT/code}"
HPC_VENV="${HPC_VENV:-$HPC_ROOT/venv}"
HPC_WHEELHOUSE="${HPC_WHEELHOUSE:-$HPC_ROOT/wheelhouse}"
HPC_LOG_DIR="${HPC_LOG_DIR:-$HPC_ROOT/logs}"
HPC_TMP_DIR="${HPC_TMP_DIR:-$HPC_ROOT/tmp}"
HPC_RAW_DIR="${HPC_RAW_DIR:-$HPC_STAGE/raw}"
HPC_GPS_DIR="${HPC_GPS_DIR:-$HPC_STAGE/gps}"
HPC_PRODUCTS_DIR="${HPC_PRODUCTS_DIR:-$HPC_STAGE/products}"
HPC_JOB_TMP="${HPC_JOB_TMP:-$HPC_STAGE/tmp}"

# Transfers. The wrapper submits rclone as a queued job on the transfer
# cluster; HPC_DT_SLURM_CONF is that cluster's slurm.conf for squeue/sacct.
# Plain rclone on the transfer node (non-login shells may not have it on PATH).
HPC_RCLONE_BIN="${HPC_RCLONE_BIN:-rclone}"
HPC_XFER_RCLONE_CMD="${HPC_XFER_RCLONE_CMD:-rclone}"
HPC_DT_SLURM_CONF="${HPC_DT_SLURM_CONF:-}"
HPC_RCLONE_REMOTE="${HPC_RCLONE_REMOTE:-oceanstream}"

# ---------------------------------------------------------------------------
# Object storage
# ---------------------------------------------------------------------------
OCEANSTREAM_S3_BUCKET="${OCEANSTREAM_S3_BUCKET:-}"
OCEANSTREAM_S3_ENDPOINT="${OCEANSTREAM_S3_ENDPOINT:-${AWS_S3_ENDPOINT:-${S3_ENDPOINT_URL:-}}}"
OCEANSTREAM_S3_REGION="${OCEANSTREAM_S3_REGION:-${AWS_DEFAULT_REGION:-}}"
OCEANSTREAM_S3_RAW_PREFIX="${OCEANSTREAM_S3_RAW_PREFIX:-hpc/raw}"
OCEANSTREAM_S3_GPS_PREFIX="${OCEANSTREAM_S3_GPS_PREFIX:-hpc/gps}"
OCEANSTREAM_S3_PRODUCTS_PREFIX="${OCEANSTREAM_S3_PRODUCTS_PREFIX:-hpc/products}"

# ---------------------------------------------------------------------------
# Logging — stderr only, so stdout stays clean for machine-readable output
# ---------------------------------------------------------------------------
if [ -t 2 ]; then
    _C_BLUE=$'\033[1;34m'; _C_GREEN=$'\033[1;32m'; _C_YELLOW=$'\033[1;33m'
    _C_RED=$'\033[1;31m';  _C_DIM=$'\033[2m';      _C_OFF=$'\033[0m'
else
    _C_BLUE=''; _C_GREEN=''; _C_YELLOW=''; _C_RED=''; _C_DIM=''; _C_OFF=''
fi

log()  { printf '%s==>%s %s\n' "$_C_BLUE"   "$_C_OFF" "$*" >&2; }
ok()   { printf '%s  ✓%s %s\n' "$_C_GREEN"  "$_C_OFF" "$*" >&2; }
warn() { printf '%s  !%s %s\n' "$_C_YELLOW" "$_C_OFF" "$*" >&2; }
info() { printf '%s    %s%s\n' "$_C_DIM"    "$*"      "$_C_OFF" >&2; }
fail() { printf '%s  ✗%s %s\n' "$_C_RED"    "$_C_OFF" "$*" >&2; exit 1; }

# ---------------------------------------------------------------------------
# Preconditions
# ---------------------------------------------------------------------------
require_cmd() {
    local cmd
    for cmd in "$@"; do
        command -v "$cmd" >/dev/null 2>&1 || fail "Required command not found: $cmd"
    done
}

# Fail with the list of unset site variables.
require_site() {
    local missing="" v val
    for v in HPC_USER HPC_LOGIN_HOST HPC_ACCOUNT HPC_PROJ HPC_SCRATCH HPC_SSH_KEY "$@"; do
        eval "val=\${$v:-}"
        [ -n "$val" ] || missing="$missing $v"
    done
    [ -z "$missing" ] || fail "Site settings missing:$missing
  Copy scripts/hpc/site.env.example to scripts/hpc/site.env and fill it in,
  or export the variables. See docs/hpc.md."
}

require_hpc_key() {
    require_site
    [ -f "$HPC_SSH_KEY" ] || fail "No SSH key at $HPC_SSH_KEY (HPC_SSH_KEY)."
}

# ---------------------------------------------------------------------------
# Secret hygiene
# ---------------------------------------------------------------------------
# Strip signed-URL parameters and anything that looks like an S3 secret from
# streamed output. Job logs on shared filesystems are group-readable.
redact_stream() {
    sed -e 's/sig=[^&"[:space:]]*/sig=<redacted>/g' \
        -e 's/X-Amz-Signature=[^&"[:space:]]*/X-Amz-Signature=<redacted>/g' \
        -e 's/secret_access_key *= *.*/secret_access_key = <redacted>/g'
}

# ---------------------------------------------------------------------------
# SSH
# ---------------------------------------------------------------------------
_hpc_ssh_opts() {
    printf '%s\n' -o BatchMode=yes -o StrictHostKeyChecking=accept-new \
                  -o ConnectTimeout=20 -o ServerAliveInterval=30 \
                  -o IdentitiesOnly=yes -i "$HPC_SSH_KEY"
    if [ -n "${HPC_KNOWN_HOSTS:-}" ]; then
        printf '%s\n' -o "UserKnownHostsFile=$HPC_KNOWN_HOSTS"
    fi
}

# Callers control quoting: single-quote anything that must expand remotely.
# shellcheck disable=SC2029
hpc_ssh_login() {
    require_hpc_key
    local opts=() line
    while IFS= read -r line; do opts+=("$line"); done < <(_hpc_ssh_opts)
    ssh "${opts[@]}" "${HPC_USER}@${HPC_LOGIN_HOST}" "$@"
}

# shellcheck disable=SC2029
hpc_ssh_xfer() {
    require_hpc_key
    [ -n "$HPC_XFER_HOST" ] || fail "HPC_XFER_HOST is not set"
    local opts=() line
    while IFS= read -r line; do opts+=("$line"); done < <(_hpc_ssh_opts)
    ssh "${opts[@]}" "${HPC_USER}@${HPC_XFER_HOST}" "$@"
}

# Module loading for remote shells. `module` is a shell function: never pipe
# or subshell it, or the environment changes are lost.
hpc_module_prelude() {
    local p=""
    [ -n "$HPC_MODULES" ] && p="module load $HPC_MODULES >/dev/null 2>&1;"
    # Some site Python modules export PYTHONHOME/PYTHONPATH, which shadow a venv.
    printf '%s unset PYTHONHOME PYTHONPATH;' "$p"
}

# ---------------------------------------------------------------------------
# Slurm (compute cluster, via the login node)
# ---------------------------------------------------------------------------

# Stage an sbatch script (stdin) on the login node and submit it.
# $1 = job name. Prints "jobid=<id>" on stdout.
hpc_submit() {
    local job_name="$1"
    local remote_script="$HPC_TMP_DIR/${job_name}.sbatch"

    hpc_ssh_login "mkdir -p '$HPC_TMP_DIR' '$HPC_LOG_DIR' && cat > '$remote_script'" \
        || fail "Could not stage $remote_script"

    # Login shells may print banners on stderr, so keep stderr out of the
    # capture and parse the id defensively.
    local out err jobid
    err="$(mktemp)"
    out="$(hpc_ssh_login "sbatch --parsable '$remote_script'" 2>"$err")" || true
    jobid="$(printf '%s' "$out" | tr -d '[:space:]' | grep -oE '^[0-9]+' | tail -1)"
    if [ -z "$jobid" ]; then
        warn "sbatch produced no job id. stdout: ${out:-<empty>}"
        sed 's/^/      /' "$err" >&2
        rm -f "$err"
        fail "Submission failed for $job_name"
    fi
    rm -f "$err"
    printf 'jobid=%s\n' "$jobid"
}

# Poll a compute job until it leaves the queue. Prints its final sacct row.
# $1 = job id, $2 = job name (for the log path).
hpc_wait() {
    local jobid="$1" job_name="${2:-}"
    local waited=0 interval="${HPC_POLL_INTERVAL:-30}" timeout="${HPC_POLL_TIMEOUT:-172800}"
    local state="" last=""
    while [ "$waited" -lt "$timeout" ]; do
        state="$(hpc_ssh_login "squeue -j '$jobid' -h -o '%T|%R' 2>/dev/null | sort -u | head -1 || true" 2>/dev/null | tr -d '[:space:]')"
        [ -z "$state" ] && break
        if [ "$state" != "$last" ]; then info "$state"; last="$state"; fi
        sleep "$interval"
        waited=$((waited + interval))
    done
    [ "$waited" -ge "$timeout" ] && { warn "Stopped polling after ${timeout}s; job $jobid may still be queued."; return 2; }

    hpc_ssh_login "sacct -j '$jobid' -X -n -P --format=JobID,State,ExitCode,Elapsed 2>/dev/null" 2>/dev/null
}

# Job accounting as key=value lines (MaxRSS is on the batch step).
hpc_sacct() {
    local jobid="$1"
    hpc_ssh_login "sacct -j '$jobid' -n -P --units=G \
        --format=JobID,JobName,State,ExitCode,Elapsed,TotalCPU,AllocCPUS,MaxRSS,MaxVMSize,NodeList 2>/dev/null" 2>/dev/null
}

# ---------------------------------------------------------------------------
# Queued rclone (transfer cluster)
# ---------------------------------------------------------------------------

# Run an rclone operation through the site's queued transfer wrapper, wait,
# and print its output with a trailing `exit=N` line.
# $1 = label, remaining args = rclone arguments.
hpc_xfer_rclone() {
    local label="$1"; shift
    local slurm_env=""
    [ -n "$HPC_DT_SLURM_CONF" ] && slurm_env="SLURM_CONF='$HPC_DT_SLURM_CONF'"

    log "Submitting $label via $HPC_XFER_RCLONE_CMD"
    info "rclone $*"

    # Wrappers commonly write <cmd>_<jobid>.{out,err} into $PWD.
    local submitted jobid
    submitted="$(hpc_ssh_xfer "mkdir -p '$HPC_LOG_DIR' && cd '$HPC_LOG_DIR' && $HPC_XFER_RCLONE_CMD $*" 2>/dev/null)"
    jobid="$(printf '%s' "$submitted" | grep -oE 'Submitted batch job [0-9]+' | grep -oE '[0-9]+' | tail -1)"
    [ -n "$jobid" ] || { warn "No job id from transfer wrapper. Output: ${submitted:-<empty>}"; return 1; }
    log "Transfer job $jobid submitted"

    if [ "${HPC_SUBMIT_NOWAIT:-0}" = "1" ]; then
        printf 'jobid=%s\nexit=pending\n' "$jobid"
        return 0
    fi

    local state last="" waited=0 interval="${HPC_POLL_INTERVAL:-15}" timeout="${HPC_POLL_TIMEOUT:-172800}"
    while [ "$waited" -lt "$timeout" ]; do
        state="$(hpc_ssh_xfer "$slurm_env squeue -j '$jobid' -h -o '%T' 2>/dev/null || true" 2>/dev/null | tr -d '[:space:]')"
        [ -z "$state" ] && break
        [ "$state" != "$last" ] && { info "$state"; last="$state"; }
        sleep "$interval"
        waited=$((waited + interval))
    done
    [ "$waited" -ge "$timeout" ] && { warn "Stopped polling after ${timeout}s"; return 1; }

    local wrapper; wrapper="$(basename "${HPC_XFER_RCLONE_CMD%% *}")"
    hpc_ssh_xfer "
        cat '$HPC_LOG_DIR/${wrapper}_${jobid}.out' 2>/dev/null | tail -40
        if [ -s '$HPC_LOG_DIR/${wrapper}_${jobid}.err' ]; then
            echo '--- stderr ---'; tail -40 '$HPC_LOG_DIR/${wrapper}_${jobid}.err'
        fi
        code=\$($slurm_env sacct -j '$jobid' --format=ExitCode -n 2>/dev/null | head -1 | tr -d ' ')
        echo \"jobid=$jobid\"
        echo \"exit=\${code%%:*}\"
    " 2>/dev/null | redact_stream
}

# Remote path for an S3 key under the bucket.
s3_remote() { printf '%s:%s/%s\n' "$HPC_RCLONE_REMOTE" "$OCEANSTREAM_S3_BUCKET" "${1#/}"; }

# ---------------------------------------------------------------------------
# Misc
# ---------------------------------------------------------------------------
confirm() {
    local prompt="${1:-Continue?}" reply
    if [ "${HPC_ASSUME_YES:-0}" = "1" ]; then
        info "$prompt (auto-confirmed via HPC_ASSUME_YES)"
        return 0
    fi
    printf '%s [y/N] ' "$prompt" >&2
    read -r reply
    case "$reply" in [yY]|[yY][eE][sS]) return 0 ;; *) return 1 ;; esac
}
