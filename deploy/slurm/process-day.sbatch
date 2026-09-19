#!/bin/bash
# =============================================================================
# oceanstream: process one day of raw echosounder data on a Slurm cluster
# =============================================================================
# Rendered by scripts/hpc/submit-day.sh, which substitutes the @PLACEHOLDERS@.
# Compute nodes are assumed to have NO internet: raw input is pre-staged on
# the cluster filesystem and products are written locally for push-products.sh.
# =============================================================================

#SBATCH --job-name=@JOB_NAME@
#SBATCH --account=@ACCOUNT@
#SBATCH --qos=@QOS@
#SBATCH --partition=@PARTITION@
#SBATCH --constraint=@CONSTRAINT@
#SBATCH --nodes=1
#SBATCH --ntasks-per-node=1
#SBATCH --cpus-per-task=@CPUS@
#SBATCH --time=@WALLTIME@
#SBATCH --output=@LOG_DIR@/@JOB_NAME@-@LOG_SUFFIX@.out
#SBATCH --error=@LOG_DIR@/@JOB_NAME@-@LOG_SUFFIX@.out
#SBATCH --array=@ARRAY@

# No --mem on purpose: some sites reject it and derive memory from the core
# count. The budget is passed to the pipeline explicitly (@MEM_BUDGET_GB@ GB).

set -uo pipefail

CRUISE="@CRUISE@"
DAY="@DAY@"
DAYS_FILE="@DAYS_FILE@"
# Array jobs: one day per task, line N+1 of the days file.
if [ -n "${DAYS_FILE}" ] && [ -n "${SLURM_ARRAY_TASK_ID:-}" ]; then
    DAY="$(sed -n "$((SLURM_ARRAY_TASK_ID + 1))p" "${DAYS_FILE}")"
    [ -n "${DAY}" ] || { echo "no day for task ${SLURM_ARRAY_TASK_ID}"; exit 2; }
fi
CODE="@CODE_DIR@"
RAW_DIR="@RAW_DIR@/${CRUISE}/${DAY}"
OUT_ROOT="@PRODUCTS_DIR@"

echo "=== oceanstream ${CRUISE} ${DAY} ==="
echo "job=${SLURM_JOB_ID:-?} array=${SLURM_ARRAY_JOB_ID:-}_${SLURM_ARRAY_TASK_ID:-} node=$(hostname) start=$(date -u +%FT%TZ)"
echo "cpus=${SLURM_CPUS_PER_TASK:-?} mem_budget=@MEM_BUDGET_GB@GB code=$(cat "${CODE}/.revision" 2>/dev/null)"

@MODULE_LOAD@
# shellcheck disable=SC1091
source "@VENV@/bin/activate"
unset PYTHONHOME
export PYTHONPATH="${CODE}:${CODE}/scripts/batch_processing"

export TMPDIR="@JOB_TMP@/${SLURM_JOB_ID:-manual}"
export MPLCONFIGDIR="${TMPDIR}/mpl"
mkdir -p "${TMPDIR}" "${MPLCONFIGDIR}" "${OUT_ROOT}"
trap 'rm -rf "${TMPDIR}"' EXIT

# One thread per BLAS call: parallelism comes from the pipeline's own workers.
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1

echo "raw: $(ls "${RAW_DIR}"/*.raw 2>/dev/null | wc -l) files, $(du -sh "${RAW_DIR}" 2>/dev/null | cut -f1)"

cd "${CODE}"
echo "--- pipeline start $(date -u +%FT%TZ) ---"
python -u scripts/batch_processing/process_from_raw.py \
    --cruise-id "${CRUISE}" \
    --start-date "${DAY}" --end-date "${DAY}" \
    --output-container "@OUTPUT_CONTAINER@" \
    --raw-source local --raw-dir "${RAW_DIR}" \
    --storage-backend local --local-save "${OUT_ROOT}" \
    --output-dir "${TMPDIR}/batch_output" \
    --raw-cache-dir "${TMPDIR}/raw_cache" \
    --parallel-workers @PARALLEL_WORKERS@ --n-workers @DASK_WORKERS@ --memory-limit @DASK_MEMORY@ \
    @PIPELINE_ARGS@
rc=$?
echo "--- pipeline end $(date -u +%FT%TZ) rc=${rc} ---"

du -sh "${OUT_ROOT}/@OUTPUT_CONTAINER@/${DAY}" 2>/dev/null || echo "(no products)"
echo "exit=${rc}"
exit "${rc}"
