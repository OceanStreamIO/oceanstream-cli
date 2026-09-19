#!/usr/bin/env bash
# =============================================================================
# Build the Python venv on the cluster login node and verify the imports
# =============================================================================
# Two install modes:
#   offline (default)  pip install --no-index --find-links=$HPC_WHEELHOUSE
#   --online           plain pip install (only if the login node reaches PyPI)
#
# The oceanstream package is NOT installed: jobs put $HPC_CODE_DIR on
# PYTHONPATH, so a code sync never needs a rebuild.
#
# Build the wheelhouse on a machine with internet using a real Python 3.12
# (pip download from 3.13 silently drops version-gated deps):
#   pip download --no-deps --only-binary=:all: --python-version 3.12 \
#     --implementation cp --abi cp312 --abi abi3 --abi none \
#     --platform manylinux_2_28_x86_64 --platform manylinux2014_x86_64 --platform any ...
#   pip wheel --no-deps <sdist-only and git requirements>
#
# Usage:
#   ./scripts/hpc/bootstrap-env.sh             # create/update + verify
#   ./scripts/hpc/bootstrap-env.sh --recreate
#   ./scripts/hpc/bootstrap-env.sh --verify
#   ./scripts/hpc/bootstrap-env.sh --online
# =============================================================================
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/hpc/lib.sh
. "$SCRIPT_DIR/lib.sh"

RECREATE=0; VERIFY_ONLY=0; ONLINE=0
while [ $# -gt 0 ]; do
    case "$1" in
        --recreate) RECREATE=1 ;;
        --verify)   VERIFY_ONLY=1 ;;
        --online)   ONLINE=1 ;;
        -h|--help)  sed -n '2,24p' "$0" >&2; exit 0 ;;
        *)          fail "Unknown argument: $1" ;;
    esac
    shift
done
require_hpc_key
PRELUDE="$(hpc_module_prelude)"
PIP_SRC="--no-index --find-links='$HPC_WHEELHOUSE'"
[ "$ONLINE" = "1" ] && PIP_SRC=""

if [ "$VERIFY_ONLY" = "0" ]; then
    log "Building venv at ${HPC_VENV} ($([ "$ONLINE" = 1 ] && echo online || echo "wheelhouse"))"
    hpc_ssh_login "
        set -e
        $PRELUDE
        python3 -V 2>&1 | grep -q ' ${HPC_PYTHON_MINOR}\.' || { echo \"ABORT: python3 is \$(python3 -V 2>&1), want ${HPC_PYTHON_MINOR}\"; exit 3; }
        [ '$RECREATE' = '1' ] && rm -rf '$HPC_VENV'
        [ -x '$HPC_VENV/bin/python' ] || python3 -m venv '$HPC_VENV'
        . '$HPC_VENV/bin/activate'; unset PYTHONHOME PYTHONPATH
        reqs='$HPC_CODE_DIR/requirements-hpc.txt'
        if [ '$ONLINE' = '0' ]; then
            # Direct references (name @ git+https://...) cannot be fetched offline;
            # the wheelhouse holds a wheel built from exactly that reference.
            sed -E 's/^([A-Za-z0-9_.-]+) @ .*/\\1/' \"\$reqs\" > '$HPC_TMP_DIR/requirements-offline.txt'
            reqs='$HPC_TMP_DIR/requirements-offline.txt'
        fi
        python -m pip install -q $PIP_SRC -r \"\$reqs\" 2>&1 | tail -20
        echo \"installed=\$(python -m pip list --format=freeze 2>/dev/null | wc -l)\"
    " 2>&1 | grep -vE '^(load|unload|Loads|remove|Set INTEL|CPLUS)' >&2 || fail "venv build failed"
fi

log "Verifying imports"
OUT="$(hpc_ssh_login "
    $PRELUDE
    . '$HPC_VENV/bin/activate'; unset PYTHONHOME
    export PYTHONPATH='$HPC_CODE_DIR:$HPC_CODE_DIR/scripts/batch_processing'
    cd '$HPC_CODE_DIR'
    python - <<'PY'
import importlib, sys
print(f'python {sys.version.split()[0]} at {sys.executable}')
mods = ['numpy', 'echopype', 'xarray', 'zarr', 'dask', 'distributed', 'netCDF4', 'numcodecs',
        'bottleneck', 'scipy', 'rasterio', 'geopandas', 'pyarrow', 'matplotlib', 's3fs', 'pystac',
        'oceanstream', 'process_from_raw', 'process_campaign']
for m in mods:
    try:
        mod = importlib.import_module(m)
        print(f'  ok   {m:18s} {getattr(mod, \"__version__\", \"\")}')
    except Exception as e:
        print(f'  FAIL {m:18s} {type(e).__name__}: {e}')
PY
" 2>&1 | grep -vE '^(load|unload|Loads|remove|Set INTEL|CPLUS)')"
printf '%s\n' "$OUT" >&2
printf '%s' "$OUT" | grep -q 'FAIL' && fail "Some imports failed"
ok "Environment ready at ${HPC_VENV}"
