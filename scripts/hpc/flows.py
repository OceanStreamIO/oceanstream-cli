"""Prefect flows that process echosounder days on a Slurm cluster.

The heavy work runs on the cluster. These flows only drive ``scripts/hpc/*``
over SSH and publish small JSON documents, so they fit a small worker pod —
never import the pipeline here.

process_day_hpc legs:
  1. sync-code.sh        ship the pipeline source to the cluster
  2. stage-raw.sh        S3 -> cluster scratch (queued transfer)
  3. submit-day.sh       sbatch, poll to exit
  4. push-products.sh    cluster scratch -> S3, verified
  5. publish_stac.py     item.json (last) + collection.json
  6. cleanup             free the day's scratch

Site settings come from the environment (see scripts/hpc/site.env.example).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from prefect import flow, get_run_logger, task

APP = Path(os.environ.get("OCEANSTREAM_SOURCE_PATH", Path(__file__).resolve().parents[2]))
SCRIPTS = APP / "scripts" / "hpc"
PRESETS = APP / "deploy" / "slurm" / "presets"

# Queue wait dominates and is priority-driven; the work itself is ~30 min/day.
SYNC_TIMEOUT_S = 30 * 60
STAGE_TIMEOUT_S = 3 * 60 * 60
JOB_TIMEOUT_S = 36 * 60 * 60
PUSH_TIMEOUT_S = 3 * 60 * 60
PUBLISH_TIMEOUT_S = 30 * 60

DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+$")

# Flags of the SD_TPOS2023_v03 reference runs (denoise-lab preset tpos-sd-2023).
DEFAULT_PIPELINE_ARGS = [
    "--expected-categories", "short_pulse,long_pulse", "--force",
    "--colormaps", "ocean_r,jet",
    "--surface-exclusion-depth", "1.9", "--sv-clip-max-db", "-10.0", "--prune-threshold", "0.8",
    "--mvbs-range-bin", "1m", "--mvbs-ping-time-bin", "10s",
    "--nasc-range-bin", "10m", "--nasc-dist-bin", "0.5nmi",
    "--skip-combined-echograms", "--save-mvbs-netcdf", "--save-nasc-netcdf",
    "--skip-pmtiles", "--skip-campaign-echograms",
]


def _check(value: str, pattern: re.Pattern, what: str) -> str:
    if not pattern.match(value):
        raise ValueError(f"Invalid {what}: {value!r}")
    return value


def _run(cmd: list[str], timeout: int) -> str:
    """Run a helper without a shell, stream its output, return it."""
    log = get_run_logger()
    env = {**os.environ, "HPC_ASSUME_YES": "1", "PYTHONPATH": str(APP)}
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, bufsize=1, env=env, cwd=str(APP))
    assert proc.stdout is not None
    lines: list[str] = []
    try:
        for line in proc.stdout:
            line = line.rstrip()
            lines.append(line)
            log.info(line)
        rc = proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        raise RuntimeError(f"{Path(cmd[0]).name} exceeded {timeout}s")
    if rc != 0:
        raise RuntimeError(f"{Path(cmd[0]).name} failed with exit {rc}")
    return "\n".join(lines)


def _sh(script: str, *args: str, timeout: int) -> str:
    return _run([str(SCRIPTS / script), *args], timeout)


def _products_root(container: str) -> str:
    bucket = os.environ["OCEANSTREAM_S3_BUCKET"]
    prefix = os.environ.get("OCEANSTREAM_S3_PRODUCTS_PREFIX", "hpc/products")
    return f"s3://{bucket}/{prefix}/{container}"


@task(name="hpc-sync-code", retries=1, retry_delay_seconds=30)
def sync_code() -> None:
    _sh("sync-code.sh", timeout=SYNC_TIMEOUT_S)


@task(name="hpc-stage", retries=1, retry_delay_seconds=300)
def stage(cruise_id: str, days: list[str], gps: bool) -> None:
    _sh("stage-raw.sh", "--cruise", cruise_id, "--days", ",".join(days), timeout=STAGE_TIMEOUT_S)
    if gps:
        _sh("stage-raw.sh", "--cruise", cruise_id, "--gps", timeout=STAGE_TIMEOUT_S)


@task(name="hpc-submit-day")
def submit_day(cruise_id: str, day: str, container: str, cpus: int, constraint: str,
               qos: str, walltime: str, preset: str, gps: bool, pipeline_args: list[str]) -> str:
    args = ["--cruise", cruise_id, "--day", day, "--output-container", container,
            "--cpus", str(cpus), "--constraint", constraint, "--time", walltime,
            "--denoise-config", str(PRESETS / f"{preset}.toml"), "--preset-key", preset]
    if qos:
        args += ["--qos", qos]
    extra = list(pipeline_args)
    if gps:
        # Same default as lib.sh: $HPC_SCRATCH/oceanstream/gps/<cruise>.
        gps_root = os.environ.get("HPC_GPS_DIR") or f"{os.environ['HPC_SCRATCH']}/oceanstream/gps"
        extra += ["--gps-dir", f"{gps_root}/{cruise_id}"]
    out = _sh("submit-day.sh", *args, "--", *extra, timeout=JOB_TIMEOUT_S)
    m = re.search(r"^jobid=(\d+)$", out, re.M)
    if not m:
        raise RuntimeError("submit-day.sh reported no job id")
    return m.group(1)


@task(name="hpc-job-metrics", retries=2, retry_delay_seconds=30)
def job_metrics(jobid: str) -> dict:
    out = _sh("job-metrics.sh", jobid, timeout=300)
    return json.loads(out.strip().splitlines()[-1])


@task(name="hpc-push-products", retries=1, retry_delay_seconds=300)
def push_products(container: str, days: list[str], dest: str) -> None:
    _sh("push-products.sh", "--container", container, "--dest", dest,
        "--days", ",".join(days), timeout=PUSH_TIMEOUT_S)


@task(name="publish-stac", retries=2, retry_delay_seconds=60)
def publish_stac(cruise_id: str, dest_container: str, days: list[str], run_id: str,
                 metrics: dict) -> None:
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(metrics, f)
    try:
        _run([sys.executable, str(SCRIPTS / "publish_stac.py"),
              "--root", _products_root(dest_container), "--cruise-id", cruise_id,
              "--days", ",".join(days), "--run-id", run_id, "--metrics-json", f.name,
              "--skip-nasc-export"], PUBLISH_TIMEOUT_S)
    finally:
        os.unlink(f.name)


@task(name="build-tiles", retries=1, retry_delay_seconds=60)
def build_tiles(dest_container: str, campaign_slug: str) -> str:
    bucket = os.environ["OCEANSTREAM_S3_BUCKET"]
    out = f"s3://{bucket}/tiles/{campaign_slug}_echodata.pmtiles"
    _run([sys.executable, str(SCRIPTS / "build_campaign_tiles.py"),
          "--collection", f"{_products_root(dest_container)}/collection.json",
          "--campaign-slug", campaign_slug, "--out", out], PUBLISH_TIMEOUT_S)
    return out


@task(name="hpc-cleanup")
def cleanup(container: str, days: list[str], dest: str) -> None:
    _sh("push-products.sh", "--container", container, "--dest", dest,
        "--days", ",".join(days), "--verify", "--cleanup", timeout=PUSH_TIMEOUT_S)


@flow(name="process-day-hpc", log_prints=True)
def process_day_hpc(
    cruise_id: str = "SD_TPOS2023_v03",
    day: str = "2023-10-10",
    output_container: str = "",
    cpus: int = 16,
    constraint: str = "highmem",
    qos: str = "",
    walltime: str = "03:00:00",
    preset: str = "tpos-sd-2023",
    gps: bool = True,
    pipeline_args: list[str] | None = None,
    campaign_slug: str = "",
    skip_stage: bool = False,
    skip_push: bool = False,
    cleanup_scratch: bool = False,
) -> dict:
    """Process one day on the cluster and publish it (Item, Collection, tiles)."""
    from prefect.runtime import flow_run

    _check(cruise_id, NAME_RE, "cruise_id")
    _check(day, DAY_RE, "day")
    _check(preset, NAME_RE, "preset")
    container = _check(output_container or cruise_id, NAME_RE, "output_container")
    dest = f"{os.environ.get('OCEANSTREAM_S3_PRODUCTS_PREFIX', 'hpc/products')}/{cruise_id}"
    run_id = str(flow_run.get_id() or "manual")

    sync_code()
    if not skip_stage:
        stage(cruise_id, [day], gps)
    jobid = submit_day(cruise_id, day, container, cpus, constraint, qos, walltime, preset, gps,
                       pipeline_args if pipeline_args is not None else DEFAULT_PIPELINE_ARGS)
    metrics = job_metrics(jobid)
    result = {"jobid": jobid, "metrics": metrics}
    if skip_push:
        return result
    push_products(container, [day], dest)
    publish_stac(cruise_id, cruise_id, [day], run_id, {day: metrics})
    if campaign_slug:
        result["tiles"] = build_tiles(cruise_id, _check(campaign_slug, NAME_RE, "campaign_slug"))
    if cleanup_scratch:
        cleanup(container, [day], dest)
    return result


@flow(name="publish-campaign", log_prints=True)
def publish_campaign(cruise_id: str = "SD_TPOS2023_v03", campaign_slug: str = "") -> dict:
    """Rebuild the Collection and (optionally) the campaign's track tiles."""
    _check(cruise_id, NAME_RE, "cruise_id")
    _run([sys.executable, str(SCRIPTS / "publish_stac.py"), "--root", _products_root(cruise_id),
          "--cruise-id", cruise_id, "--collection-only"], PUBLISH_TIMEOUT_S)
    out = {"collection": f"{_products_root(cruise_id)}/collection.json"}
    if campaign_slug:
        out["tiles"] = build_tiles(cruise_id, _check(campaign_slug, NAME_RE, "campaign_slug"))
    return out
