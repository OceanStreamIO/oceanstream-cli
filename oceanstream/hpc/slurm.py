"""Render, submit and follow the processing jobs.

Three shapes, all from the templates in ``templates/``:

- ``day``: one job for one day;
- ``array``: one job array, one day per task, at most *array_limit* at once;
- ``node``: one job on one whole node that runs every day of a cruise, *parallel*
  at a time, each as a job step (:func:`oceanstream.hpc.node.run_node`).

Pipeline flags are passed through to ``process_from_raw.py`` unchanged.
"""

from __future__ import annotations

import json
import re
import shlex
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from importlib import resources
from pathlib import PurePosixPath
from typing import Any, Literal

from .console import HpcError, info, warn
from .remote import Cluster
from .site import HpcSite

Mode = Literal["day", "array", "node"]
DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# Leave this share of a node's memory for the job script, page cache and slack.
NODE_MEMORY_SHARE = 0.8


def template(name: str) -> str:
    return resources.files("oceanstream.hpc").joinpath("templates", name).read_text("utf-8")


def render(text: str, values: dict[str, str]) -> str:
    """Fill ``@KEY@`` placeholders; ``#SBATCH --x=`` lines left empty are dropped."""
    for key, value in values.items():
        text = text.replace(f"@{key}@", value)
    lines = [line for line in text.splitlines()
             if not re.match(r"^#SBATCH --[a-z-]+=$", line)]
    leftover = re.findall(r"@[A-Z_]+@", "\n".join(lines))
    if leftover:
        raise HpcError(f"Template placeholders left unfilled: {sorted(set(leftover))}")
    return "\n".join(lines) + "\n"


def quote_args(args: list[str]) -> str:
    return " ".join(shlex.quote(a) for a in args)


def parallel_days(n_days: int, cores_per_node: int, cpus_per_day: int,
                  node_memory_gb: float, day_memory_gb: float) -> int:
    """How many days a whole node runs at once: bounded by days, cores and memory."""
    if cpus_per_day < 1 or day_memory_gb <= 0:
        raise HpcError("cpus_per_day must be >= 1 and day_memory_gb > 0")
    by_cores = cores_per_node // cpus_per_day
    by_memory = int(node_memory_gb * NODE_MEMORY_SHARE // day_memory_gb)
    p = min(n_days, by_cores, by_memory)
    if p < 1:
        raise HpcError(
            f"One day needs {day_memory_gb:g} GB and {cpus_per_day} cores; a node has "
            f"{node_memory_gb:g} GB ({NODE_MEMORY_SHARE:.0%} usable) and {cores_per_node} cores."
        )
    return p


@dataclass
class JobSpec:
    """What to run. *days* has one entry for ``day`` mode."""

    cruise: str
    days: list[str]
    mode: Mode = "day"
    container: str = ""
    cpus: int = 16
    walltime: str = "03:00:00"
    qos: str = ""
    constraint: str | None = None
    partition: str | None = None
    array_limit: int = 4
    parallel: int = 1
    parallel_workers: int = 1
    dask_workers: int = 1
    dask_memory: str = ""
    step_mem_gb: int = 0
    preset_config: str = ""   # path on the cluster
    preset_key: str = ""
    pipeline_args: list[str] = field(default_factory=list)

    def validate(self) -> None:
        if not self.days:
            raise HpcError("No days to process.")
        bad = [d for d in self.days if not DAY_RE.match(d)]
        if bad:
            raise HpcError(f"Days must be YYYY-MM-DD: {bad}")
        if self.mode == "day" and len(self.days) != 1:
            raise HpcError("A 'day' job takes exactly one day; use 'array' or 'node'.")


@dataclass
class RenderedJob:
    name: str
    script: str
    days_file: str      # path on the cluster ('' for a single day)
    days: list[str]


def build(site: HpcSite, spec: JobSpec) -> RenderedJob:
    """The batch script for *spec*, with the site's account, layout and modules."""
    spec.validate()
    constraint = site.constraint if spec.constraint is None else spec.constraint
    partition = site.partition if spec.partition is None else spec.partition
    qos = spec.qos or site.qos
    container = spec.container or spec.cruise
    per_core = site.mem_per_core(constraint)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")

    if spec.mode == "node":
        mem_budget = spec.cpus * per_core
        name = f"os-{spec.cruise}-node"
    else:
        mem_budget = spec.cpus * per_core
        name = f"os-{spec.cruise}-" + (spec.days[0] if spec.mode == "day" else "array")
    # Leave a quarter of the budget for the parent process and page cache.
    dask_memory = spec.dask_memory or f"{mem_budget * 3 // 4 // max(spec.dask_workers, 1)}GB"

    days_file = ""
    if spec.mode != "day":
        days_file = f"{site.tmp_dir}/days-{spec.cruise}-{stamp}.txt"

    pipeline = list(spec.pipeline_args)
    if spec.preset_config:
        pipeline = ["--denoise-config", spec.preset_config, *pipeline]
    if spec.preset_key:
        pipeline = ["--preset-key", spec.preset_key, *pipeline]

    run_args = ["--cruise", spec.cruise, "--container", container,
                "--raw-root", site.raw_dir, "--products-root", site.products_dir,
                "--job-tmp", site.job_tmp, "--mem-budget-gb", str(mem_budget),
                "--parallel-workers", str(spec.parallel_workers),
                "--dask-workers", str(spec.dask_workers), "--dask-memory", dask_memory]
    if spec.mode == "day":
        run_args += ["--day", spec.days[0]]
    elif spec.mode == "array":
        run_args += ["--days-file", days_file]

    values = {
        "JOB_NAME": name, "ACCOUNT": site.account, "QOS": qos, "PARTITION": partition,
        "CONSTRAINT": constraint, "CPUS": str(spec.cpus), "WALLTIME": spec.walltime,
        "LOG_DIR": site.log_dir, "VENV": site.venv, "CODE_DIR": site.code_dir,
        "MODULE_LOAD": f"module load {site.modules}" if site.modules else "",
        "RUN_ARGS": quote_args(run_args), "PIPELINE_ARGS": quote_args(pipeline),
    }
    if spec.mode == "node":
        node_args = ["--days-file", days_file, "--parallel", str(spec.parallel),
                     "--cpus-per-day", str(spec.cpus)]
        if spec.step_mem_gb:
            node_args += ["--step-mem-gb", str(spec.step_mem_gb)]
        values.update({"PARALLEL": str(spec.parallel), "NODE_ARGS": quote_args(node_args)})
        script = render(template("process-node.sbatch"), values)
    else:
        values.update({
            "ARRAY": (f"0-{len(spec.days) - 1}%{spec.array_limit}"
                      if spec.mode == "array" else ""),
            "LOG_SUFFIX": "%A_%a" if spec.mode == "array" else "%j",
        })
        script = render(template("process-day.sbatch"), values)
    return RenderedJob(name=name, script=script, days_file=days_file, days=list(spec.days))


def ship_preset(cluster: Cluster, local_path: str, text: str) -> str:
    """Copy a preset file to the cluster; returns its path there."""
    remote = f"{cluster.site.root}/presets/{PurePosixPath(local_path).name}"
    r = cluster.login(f"mkdir -p {shlex.quote(cluster.site.root)}/presets && "
                      f"cat > {shlex.quote(remote)}", input=text)
    if r.returncode != 0:
        raise HpcError(f"Could not copy {local_path} to {remote}: {r.stderr.strip()[-300:]}")
    return remote


def missing_raw(cluster: Cluster, cruise: str, days: list[str]) -> list[str]:
    """Days with no raw files staged on the cluster."""
    root = f"{cluster.site.raw_dir}/{cruise}"
    script = (f"for d in {' '.join(shlex.quote(d) for d in days)}; do "
              f"ls {shlex.quote(root)}/$d/*.raw >/dev/null 2>&1 || echo $d; done")
    r = cluster.login(script)
    if r.returncode != 0:
        raise HpcError(f"Could not list staged raw files: {r.stderr.strip()[-300:]}")
    return [line.strip() for line in r.stdout.splitlines() if DAY_RE.match(line.strip())]


def write_days_file(cluster: Cluster, job: RenderedJob) -> None:
    if not job.days_file:
        return
    tmp = shlex.quote(cluster.site.tmp_dir)
    r = cluster.login(f"mkdir -p {tmp} && cat > {shlex.quote(job.days_file)}",
                      input="\n".join(job.days) + "\n")
    if r.returncode != 0:
        raise HpcError(f"Could not write {job.days_file}: {r.stderr.strip()[-300:]}")


def submit(cluster: Cluster, job: RenderedJob) -> str:
    """Stage the script on the login node and submit it; returns the job id."""
    site = cluster.site
    write_days_file(cluster, job)
    path = f"{site.tmp_dir}/{job.name}.sbatch"
    r = cluster.login(f"mkdir -p {shlex.quote(site.tmp_dir)} {shlex.quote(site.log_dir)} && "
                      f"cat > {shlex.quote(path)}", input=job.script)
    if r.returncode != 0:
        raise HpcError(f"Could not stage {path}: {r.stderr.strip()[-300:]}")
    # Login shells may print banners, so parse the id defensively.
    r = cluster.login(f"sbatch --parsable {shlex.quote(path)}")
    ids = re.findall(r"^(\d+)", (r.stdout or "").strip(), re.M)
    if not ids:
        raise HpcError(f"sbatch gave no job id for {job.name}. stdout: {r.stdout!r} "
                       f"stderr: {(r.stderr or '').strip()[-500:]}")
    return str(ids[-1])


def test_only(cluster: Cluster, job: RenderedJob) -> str:
    """``sbatch --test-only``: when and where the job would start, nothing submitted."""
    path = f"{cluster.site.tmp_dir}/{job.name}.test.sbatch"
    r = cluster.login(f"mkdir -p {shlex.quote(cluster.site.tmp_dir)} && "
                      f"cat > {shlex.quote(path)} && sbatch --test-only {shlex.quote(path)}",
                      input=job.script)
    out = (r.stdout or "") + (r.stderr or "")
    return "\n".join(line for line in out.splitlines()
                     if re.search(r"sbatch|error|to start", line))


def wait(cluster: Cluster, jobid: str, poll_s: int | None = None,
         timeout_s: int | None = None, sleep: Any = time.sleep) -> str:
    """Poll until the job leaves the queue; returns its final ``sacct`` rows."""
    poll = poll_s or cluster.site.poll_interval_s
    limit = timeout_s or cluster.site.poll_timeout_s
    waited, last = 0, ""
    while waited < limit:
        r = cluster.login(f"squeue -j {shlex.quote(jobid)} -h -o '%T|%R' 2>/dev/null "
                          "| sort -u | head -1 || true")
        state = (r.stdout or "").strip()
        if not state:
            break
        if state != last:
            info(state)
            last = state
        sleep(poll)
        waited += poll
    else:
        warn(f"Stopped polling after {limit}s; job {jobid} may still be queued.")
        raise HpcError(f"Job {jobid} still queued after {limit}s.")
    r = cluster.login(f"sacct -j {shlex.quote(jobid)} -X -n -P "
                      "--format=JobID,State,ExitCode,Elapsed 2>/dev/null")
    return str(r.stdout or "")


SACCT_KEYS = ["job_id", "job_name", "state", "exit_code", "elapsed", "total_cpu",
              "alloc_cpus", "max_rss", "max_vmsize", "nodes"]


def sacct(cluster: Cluster, jobid: str) -> str:
    r = cluster.login(f"sacct -j {shlex.quote(jobid)} -n -P --units=G "
                      "--format=JobID,JobName,State,ExitCode,Elapsed,TotalCPU,AllocCPUS,"
                      "MaxRSS,MaxVMSize,NodeList 2>/dev/null")
    return str(r.stdout or "")


def _gb(value: str) -> float:
    try:
        return float(value.rstrip("G"))
    except ValueError:
        return 0.0


def metrics(rows: str) -> dict[str, Any]:
    """Accounting summary from ``sacct`` rows (:func:`sacct`).

    A plain job gives one summary. An array gives ``{task index: summary}``. A
    whole-node job gives ``{day: summary}`` from its steps named ``day-<date>``.
    """
    parsed = [dict(zip(SACCT_KEYS, line.rstrip("\n").split("|"), strict=False))
              for line in rows.splitlines() if line.strip()]
    if not parsed:
        raise HpcError("No accounting rows.")

    def summary(row: dict[str, str], steps: list[dict[str, str]]) -> dict[str, Any]:
        out: dict[str, Any] = {k: row.get(k, "") for k in SACCT_KEYS[:7]}
        rss = [_gb(s.get("max_rss", "")) for s in steps if s.get("max_rss")]
        out["max_rss_gb"] = max(rss) if rss else None
        return out

    days = {r["job_name"][4:]: summary(r, [r]) for r in parsed
            if "." in r["job_id"] and r.get("job_name", "").startswith("day-")}
    if days:
        return days
    # Allocation rows have no "." in the id; steps (.batch, .extern) carry MaxRSS.
    jobs = [r for r in parsed if "." not in r["job_id"]]
    by_job = {j["job_id"]: summary(j, [r for r in parsed
                                       if r["job_id"].split(".")[0] == j["job_id"]])
              for j in jobs}
    tasks = {jid.split("_", 1)[1]: v for jid, v in by_job.items()
             if "_" in jid and "[" not in jid}
    return tasks if tasks else next(iter(by_job.values()))


def completed(m: dict[str, Any]) -> bool:
    return bool(m.get("state") == "COMPLETED" and m.get("exit_code") == "0:0")


def dump(m: dict[str, Any]) -> str:
    return json.dumps(m, sort_keys=True)
