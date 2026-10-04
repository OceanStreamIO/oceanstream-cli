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

process_batch_hpc runs the same legs for many days as one Slurm job array and
then imports the Collection into the web app.

Site settings come from the environment (see scripts/hpc/site.env.example).
The web app import needs OCEANSTREAM_WEB_URL and OCEANSTREAM_INGEST_API_KEY.
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


def _run(cmd: list[str], timeout: int, check: bool = True) -> str:
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
    if rc != 0 and check:
        raise RuntimeError(f"{Path(cmd[0]).name} failed with exit {rc}")
    return "\n".join(lines)


def _sh(script: str, *args: str, timeout: int, check: bool = True) -> str:
    return _run([str(SCRIPTS / script), *args], timeout, check)


def _products_root(container: str) -> str:
    bucket = os.environ["OCEANSTREAM_S3_BUCKET"]
    prefix = os.environ.get("OCEANSTREAM_S3_PRODUCTS_PREFIX", "hpc/products")
    return f"s3://{bucket}/{prefix}/{container}"


@task(name="hpc-sync-code", retries=1, retry_delay_seconds=30)
def sync_code() -> None:
    """Ship the pipeline source to the cluster, when this host has it.

    The submitter image carries only scripts/hpc, so there is nothing to sync
    from it: the cluster keeps whatever revision was last synced from a full
    checkout. `cat $HPC_CODE_DIR/.revision` on the cluster shows which.
    """
    if not (APP / "scripts" / "batch_processing").is_dir():
        get_run_logger().info(
            "No pipeline source at %s — skipping the code sync; the cluster keeps its current revision.", APP,
        )
        return
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
def push_products(container: str, days: list[str], dest: str, replace: bool = False) -> None:
    args = ["--container", container, "--dest", dest, "--days", ",".join(days)]
    if replace:
        args.append("--replace")  # a reprocessed day must not keep stale chunks
    _sh("push-products.sh", *args, timeout=PUSH_TIMEOUT_S)


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
    # Mask stores (denoised/pruned) locate their Sv through the container name,
    # so the cluster-side container must be the S3 directory name.
    if output_container and output_container != cruise_id:
        raise ValueError("output_container must equal cruise_id: mask stores resolve through it")
    container = cruise_id
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
    push_products(container, [day], dest, replace=True)
    publish_stac(cruise_id, cruise_id, [day], run_id, {day: metrics})
    if campaign_slug:
        result["tiles"] = build_tiles(cruise_id, _check(campaign_slug, NAME_RE, "campaign_slug"))
    if cleanup_scratch:
        cleanup(container, [day], dest)
    return result


def _pipeline_extra(cruise_id: str, gps: bool, pipeline_args: list[str] | None) -> list[str]:
    extra = list(pipeline_args if pipeline_args is not None else DEFAULT_PIPELINE_ARGS)
    if gps:
        # Same default as lib.sh: $HPC_SCRATCH/oceanstream/gps/<cruise>.
        gps_root = os.environ.get("HPC_GPS_DIR") or f"{os.environ['HPC_SCRATCH']}/oceanstream/gps"
        extra += ["--gps-dir", f"{gps_root}/{cruise_id}"]
    return extra


@task(name="hpc-submit-array")
def submit_array(cruise_id: str, days: list[str], container: str, cpus: int, constraint: str,
                 qos: str, walltime: str, preset: str, gps: bool, pipeline_args: list[str] | None,
                 array_limit: int) -> str:
    """One Slurm job array, one day per task. Returns the array job id.

    Failed tasks do not raise here: the caller reads per-task states and
    carries on with the days that completed.
    """
    args = ["--cruise", cruise_id, "--days", ",".join(days), "--array-limit", str(array_limit),
            "--output-container", container, "--cpus", str(cpus), "--constraint", constraint,
            "--time", walltime, "--denoise-config", str(PRESETS / f"{preset}.toml"),
            "--preset-key", preset]
    if qos:
        args += ["--qos", qos]
    out = _sh("submit-day.sh", *args, "--", *_pipeline_extra(cruise_id, gps, pipeline_args),
              timeout=JOB_TIMEOUT_S, check=False)
    m = re.search(r"^jobid=(\d+)$", out, re.M)
    if not m:
        raise RuntimeError("submit-day.sh reported no job id")
    return m.group(1)


def _s3fs():
    import fsspec

    ep = os.environ.get("AWS_S3_ENDPOINT") or os.environ.get("S3_ENDPOINT_URL") or ""
    if ep and not ep.startswith(("http://", "https://")):
        ep = f"https://{ep}"
    return fsspec.core.url_to_fs, ({"endpoint_url": ep} if ep else {})


@task(name="available-days", retries=2, retry_delay_seconds=30)
def available_days(cruise_id: str) -> set[str]:
    """Days that have raw data mirrored to S3 — a cruise has gaps."""
    url_to_fs, so = _s3fs()
    bucket = os.environ["OCEANSTREAM_S3_BUCKET"]
    prefix = os.environ.get("OCEANSTREAM_S3_RAW_PREFIX", "hpc/raw")
    fs, root = url_to_fs(f"s3://{bucket}/{prefix}/{cruise_id}", **so)
    return {p.rstrip("/").rsplit("/", 1)[-1] for p in fs.ls(root, detail=False)}


@task(name="published-days", retries=2, retry_delay_seconds=30)
def published_days(dest_container: str) -> set[str]:
    """Days that already have an item.json, i.e. were pushed and published."""
    url_to_fs, so = _s3fs()
    fs, root = url_to_fs(_products_root(dest_container), **so)
    return {p.rstrip("/").split("/")[-2] for p in fs.glob(f"{root}/*/item.json")}


@task(name="import-webapp", retries=2, retry_delay_seconds=60)
def import_webapp(dest_container: str, campaign_slug: str, name: str, provider: str) -> dict:
    """Register the Collection with the web app (idempotent upsert)."""
    import httpx

    base = os.environ.get("OCEANSTREAM_WEB_URL", "").rstrip("/")
    key = os.environ.get("OCEANSTREAM_INGEST_API_KEY", "")
    if not base or not key:
        raise RuntimeError("OCEANSTREAM_WEB_URL and OCEANSTREAM_INGEST_API_KEY must be set")
    r = httpx.post(
        f"{base}/api/ingest/stac-collection",
        headers={"X-API-Key": key},
        json={"collectionUrl": f"{_products_root(dest_container)}/collection.json",
              "campaignSlug": campaign_slug, "name": name or None, "provider": provider},
        timeout=600,
    )
    r.raise_for_status()
    get_run_logger().info("web app import: %s", r.json())
    return r.json()


@flow(name="process-batch-hpc", log_prints=True)
def process_batch_hpc(
    cruise_id: str = "SD_TPOS2023_v03",
    days: list[str] | None = None,
    start_date: str = "",
    end_date: str = "",
    reprocess: bool = False,
    array_limit: int = 20,
    cpus: int = 8,
    constraint: str = "highmem",
    qos: str = "",
    walltime: str = "02:00:00",
    preset: str = "tpos-sd-2023",
    gps: bool = True,
    pipeline_args: list[str] | None = None,
    campaign_slug: str = "",
    campaign_name: str = "",
    provider: str = "saildrone",
    cleanup_scratch: bool = True,
) -> dict:
    """Process many days as one Slurm job array, then publish and import them.

    Give *days*, or *start_date*/*end_date* (inclusive). Days that already
    have a published item.json are skipped unless *reprocess* is set, so a
    re-run resumes. Raw input must be mirrored to S3 first (mirror_raw_to_s3.py).
    """
    from datetime import date, timedelta

    from prefect.runtime import flow_run

    log = get_run_logger()
    _check(cruise_id, NAME_RE, "cruise_id")
    _check(preset, NAME_RE, "preset")
    if campaign_slug:
        _check(campaign_slug, NAME_RE, "campaign_slug")
    if days is None:
        if not (start_date and end_date):
            raise ValueError("Give days, or start_date and end_date.")
        d0, d1 = date.fromisoformat(start_date), date.fromisoformat(end_date)
        days = [(d0 + timedelta(n)).isoformat() for n in range((d1 - d0).days + 1)]
    days = sorted({_check(d, DAY_RE, "day") for d in days})
    # A cruise has gaps: keep only days whose raw data is mirrored to S3,
    # otherwise the cluster has nothing to process and the array is refused.
    have = available_days(cruise_id)
    absent = [d for d in days if d not in have]
    days = [d for d in days if d in have]
    if absent:
        log.info("No raw data in S3, skipped: %s", ", ".join(absent))
    container = cruise_id  # mask stores find their Sv through this name, so it must match S3
    dest = f"{os.environ.get('OCEANSTREAM_S3_PRODUCTS_PREFIX', 'hpc/products')}/{cruise_id}"
    run_id = str(flow_run.get_id() or "manual")

    if not reprocess:
        done = published_days(cruise_id)
        skipped = [d for d in days if d in done]
        days = [d for d in days if d not in done]
        if skipped:
            log.info("Already published, skipped: %s", ", ".join(skipped))
    if not days:
        return {"processed": [], "failed": [], "note": "nothing to do"}

    sync_code()
    stage(cruise_id, days, gps)
    jobid = submit_array(cruise_id, days, container, cpus, constraint, qos or os.environ.get("HPC_QOS", ""),
                         walltime, preset, gps, pipeline_args, array_limit)
    tasks = job_metrics(jobid)  # {"<task index>": {...}}
    ok, failed, metrics = [], [], {}
    for i, day in enumerate(days):
        t = tasks.get(str(i), {})
        if t.get("state") == "COMPLETED" and t.get("exit_code") == "0:0":
            ok.append(day)
            metrics[day] = t
        else:
            failed.append(day)
    log.info("Array %s: %d completed, %d failed %s", jobid, len(ok), len(failed), failed or "")

    result: dict = {"jobid": jobid, "processed": ok, "failed": failed}
    if not ok:
        raise RuntimeError(f"No day of array {jobid} completed")
    push_products(container, ok, dest, replace=True)
    publish_stac(cruise_id, cruise_id, ok, run_id, metrics)
    if campaign_slug:
        result["tiles"] = build_tiles(cruise_id, campaign_slug)
        result["import"] = import_webapp(cruise_id, campaign_slug, campaign_name, provider)
    if cleanup_scratch:
        cleanup(container, ok, dest)
    if failed:
        log.warning("Re-run for the failed days: %s", ",".join(failed))
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
