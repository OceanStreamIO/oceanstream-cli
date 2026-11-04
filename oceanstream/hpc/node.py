"""What a job runs on its compute node.

:func:`run_day` processes one day: ``process_from_raw.py`` writes into a private
work root under the job's temporary directory, and the finished day is moved into
the shared products tree for :mod:`oceanstream.hpc.staging` to push. The
pipeline keeps intermediates under its storage root and, after stage 4, deletes
everything it finds there, so concurrent days sharing one root would destroy each
other's work. Stores record their sources relative to the container, so the move
is safe.

:func:`run_node` runs every day of a cruise on one node, *parallel* days at a
time, each as a job step (``srun``) of the node's allocation. Steps named
``day-<date>`` give per-day accounting in ``sacct``.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

PIPELINE = Path("scripts") / "batch_processing" / "process_from_raw.py"

# Pipeline exit code when it succeeded but wrote nothing for the day.
NO_PRODUCTS = 3


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class DayRun:
    """Where one day's input, scratch and output live on the cluster."""

    cruise: str
    day: str
    container: str
    raw_root: Path
    products_root: Path
    job_tmp: Path
    code_dir: Path
    mem_budget_gb: int = 0
    parallel_workers: int = 1
    dask_workers: int = 1
    dask_memory: str = ""

    @property
    def raw_dir(self) -> Path:
        return self.raw_root / self.cruise / self.day

    def tmp_dir(self) -> Path:
        # Days of one whole-node job share a job id: the day keeps them apart.
        return self.job_tmp / f"{os.environ.get('SLURM_JOB_ID', 'manual')}-{self.day}"

    def command(self, pipeline_args: Sequence[str], tmp: Path) -> list[str]:
        cmd = [sys.executable, "-u", str(self.code_dir / PIPELINE),
               "--cruise-id", self.cruise,
               "--start-date", self.day, "--end-date", self.day,
               "--output-container", self.container,
               "--raw-source", "local", "--raw-dir", str(self.raw_dir),
               "--storage-backend", "local", "--local-save", str(tmp / "products"),
               "--output-dir", str(tmp / "batch_output"),
               "--raw-cache-dir", str(tmp / "raw_cache"),
               "--parallel-workers", str(self.parallel_workers),
               "--n-workers", str(self.dask_workers)]
        if self.dask_memory:
            cmd += ["--memory-limit", self.dask_memory]
        return [*cmd, "--stop-after-stage", "9", *pipeline_args]


def day_from_file(days_file: Path, task: int) -> str:
    """Line *task* + 1 of a days file: the day of array task *task*."""
    lines = [line.strip() for line in days_file.read_text().splitlines() if line.strip()]
    if task >= len(lines):
        raise ValueError(f"no day for task {task} in {days_file} ({len(lines)} days)")
    return lines[task]


def _du(path: Path) -> int:
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())


def run_day(run: DayRun, pipeline_args: Sequence[str],
            runner: Callable[..., subprocess.CompletedProcess[Any]] = subprocess.run) -> int:
    """Process one day; returns the exit code (0, the pipeline's, or 3 for no products)."""
    raws = sorted(run.raw_dir.glob("*.raw"))
    revision = (run.code_dir / ".revision")
    print(f"=== oceanstream {run.cruise} {run.day} ===")
    print(f"job={os.environ.get('SLURM_JOB_ID', '?')} "
          f"array={os.environ.get('SLURM_ARRAY_JOB_ID', '')}_"
          f"{os.environ.get('SLURM_ARRAY_TASK_ID', '')} "
          f"step={os.environ.get('SLURM_STEP_ID', '')} node={socket.gethostname()} start={_now()}")
    print(f"cpus={os.environ.get('SLURM_CPUS_PER_TASK', '?')} mem_budget={run.mem_budget_gb}GB "
          f"code={revision.read_text().strip() if revision.is_file() else ''}")
    print(f"raw: {len(raws)} files, {sum(p.stat().st_size for p in raws) / 1e9:.1f} GB",
          flush=True)

    tmp = run.tmp_dir()
    (tmp / "mpl").mkdir(parents=True, exist_ok=True)
    run.products_root.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "TMPDIR": str(tmp), "MPLCONFIGDIR": str(tmp / "mpl"),
           # One thread per BLAS call: parallelism comes from the pipeline's own workers.
           "OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1", "MKL_NUM_THREADS": "1"}
    try:
        print(f"--- pipeline start {_now()} ---", flush=True)
        rc = runner(run.command(pipeline_args, tmp), cwd=str(run.code_dir), env=env,
                    check=False).returncode
        print(f"--- pipeline end {_now()} rc={rc} ---", flush=True)
        if rc == 0:
            done = tmp / "products" / run.container / run.day
            if done.is_dir():
                target = run.products_root / run.container / run.day
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.rmtree(target, ignore_errors=True)
                shutil.move(str(done), str(target))
                print(f"{_du(target) / 1e9:.2f} GB\t{target}")
            else:
                print(f"pipeline succeeded but wrote no products for {run.day}")
                rc = NO_PRODUCTS
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"exit={rc}", flush=True)
    return int(rc)


def marker(run: DayRun, ok: bool) -> Path:
    """``DONE``/``FAILED`` marker next to the day's products."""
    return run.products_root / run.container / f".{run.day}.{'DONE' if ok else 'FAILED'}"


def step_command(day: str, cpus_per_day: int, run_args: Sequence[str],
                 pipeline_args: Sequence[str], step_mem_gb: int = 0) -> list[str]:
    """``srun`` for one day as a step of the node's allocation."""
    cmd = ["srun", "--exact", "--ntasks=1", f"--cpus-per-task={cpus_per_day}",
           f"--job-name=day-{day}"]
    if step_mem_gb:
        cmd.append(f"--mem={step_mem_gb}G")
    return [*cmd, sys.executable, "-u", "-m", "oceanstream.hpc", "run-day",
            *run_args, "--day", day, "--", *pipeline_args]


def run_node(  # noqa: PLR0913, PLR0917
        days: Sequence[str], parallel: int, cpus_per_day: int, run: DayRun,
             run_args: Sequence[str], pipeline_args: Sequence[str], step_mem_gb: int = 0,
             runner: Callable[..., subprocess.CompletedProcess[Any]] = subprocess.run
             ) -> dict[str, int]:
    """Run each day as a job step, *parallel* at a time; returns day -> exit code."""
    print(f"=== oceanstream {run.cruise}: {len(days)} days, {parallel} at a time, "
          f"{cpus_per_day} cpus each, node={socket.gethostname()} start={_now()} ===",
          flush=True)
    for day in days:
        for stale in (marker(DayRun(**{**run.__dict__, "day": day}), True),
                      marker(DayRun(**{**run.__dict__, "day": day}), False)):
            stale.unlink(missing_ok=True)

    def one(day: str) -> tuple[str, int]:
        started = _now()
        rc = runner(step_command(day, cpus_per_day, run_args, pipeline_args, step_mem_gb),
                    check=False).returncode
        this = DayRun(**{**run.__dict__, "day": day})
        m = marker(this, rc == 0)
        m.parent.mkdir(parents=True, exist_ok=True)
        m.write_text(f"{started} {_now()} rc={rc}\n")
        print(f"[{_now()}] {day}: exit {rc}", flush=True)
        return day, int(rc)

    with ThreadPoolExecutor(max_workers=max(parallel, 1)) as pool:
        results = dict(pool.map(one, days))
    failed = sorted(d for d, rc in results.items() if rc != 0)
    print(f"=== done {_now()}: {len(results) - len(failed)} ok, {len(failed)} failed "
          f"{' '.join(failed)} ===", flush=True)
    return results
