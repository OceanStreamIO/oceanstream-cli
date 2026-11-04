#!/bin/bash
# oceanstream: one day of raw echosounder data per job, or per task of a job array.
# Rendered by oceanstream.hpc.slurm. The work is `python -m oceanstream.hpc run-day`;
# the lines before it only set up the environment it runs in.
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
# No --mem on purpose: some sites reject it and derive memory from the core count.

@MODULE_LOAD@
source "@VENV@/bin/activate"
unset PYTHONHOME
export PYTHONPATH="@CODE_DIR@:@CODE_DIR@/scripts/batch_processing"
cd "@CODE_DIR@" || exit 2
exec python -u -m oceanstream.hpc run-day @RUN_ARGS@ -- @PIPELINE_ARGS@
