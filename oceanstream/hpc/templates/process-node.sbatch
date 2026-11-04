#!/bin/bash
# oceanstream: a whole cruise in one job on one node, several days at a time.
# Rendered by oceanstream.hpc.slurm. `python -m oceanstream.hpc run-node` starts each
# day as a job step (srun) running `run-day`; the lines before it only set up the
# environment.
#SBATCH --job-name=@JOB_NAME@
#SBATCH --account=@ACCOUNT@
#SBATCH --qos=@QOS@
#SBATCH --partition=@PARTITION@
#SBATCH --constraint=@CONSTRAINT@
#SBATCH --nodes=1
#SBATCH --exclusive
#SBATCH --ntasks=@PARALLEL@
#SBATCH --cpus-per-task=@CPUS@
#SBATCH --time=@WALLTIME@
#SBATCH --output=@LOG_DIR@/@JOB_NAME@-%j.out
#SBATCH --error=@LOG_DIR@/@JOB_NAME@-%j.out

@MODULE_LOAD@
source "@VENV@/bin/activate"
unset PYTHONHOME
export PYTHONPATH="@CODE_DIR@:@CODE_DIR@/scripts/batch_processing"
cd "@CODE_DIR@" || exit 2
exec python -u -m oceanstream.hpc run-node @NODE_ARGS@ @RUN_ARGS@ -- @PIPELINE_ARGS@
