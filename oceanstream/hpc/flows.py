# Prefect flow parameters are the deployment's form fields: one per option.
# ruff: noqa: PLR0912, PLR0913, PLR0915, PLR0917
"""Prefect flows that process echosounder days on a Slurm cluster.

The heavy work runs on the cluster. These flows drive it through
:mod:`oceanstream.hpc` over SSH and publish small JSON documents, so they fit a
small worker pod; never import the pipeline here.

process_day_hpc legs:
  1. sync code       ship the pipeline source to the cluster (when this host has it)
  2. stage           S3 -> cluster scratch (streamed over SSH from here)
  3. submit          sbatch, poll to exit
  4. push            cluster scratch -> S3 over SSH from here, verified
  5. publish STAC    item.json (last) + collection.json
  6. cleanup         free the day's scratch

process_batch_hpc runs the same legs for many days as one Slurm job array and
then imports the Collection into the web app. process_campaign_hpc runs a whole
campaign as a sequence of such batches, so scratch only holds one batch at a
time.

A preset comes from the library by key, or inline as TOML (preset_toml): the
web app sends Denoise Lab presets that way.

Site settings come from the environment (see oceanstream.hpc.site). The web app
import needs OCEANSTREAM_WEB_URL and OCEANSTREAM_INGEST_API_KEY; a run may name
another web app only if OCEANSTREAM_WEB_URLS lists it.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from prefect import flow, get_run_logger, task

from . import cluster as cluster_ops
from . import flowlib, slurm, staging
from .flowlib import DAY_RE, NAME_RE
from .remote import Cluster
from .site import PROJECT_DIR, HpcSite

PRESETS = Path(__file__).resolve().parents[1] / "echodata" / "presets" / "library"

# Scratch a batch needs per GB of its raw input: the raw itself, the per-job
# work stores and the products until they are pushed.
SCRATCH_PER_RAW_GB = 2.0


def _cluster() -> Cluster:
    return Cluster(HpcSite.load())


_check = flowlib.check


def _products_root(container: str) -> str:
    bucket = os.environ["OCEANSTREAM_S3_BUCKET"]
    prefix = os.environ.get("OCEANSTREAM_S3_PRODUCTS_PREFIX", "hpc/products")
    return f"s3://{bucket}/{prefix}/{container}"


@contextmanager
def _preset_config(preset: str, preset_toml: str) -> Iterator[str]:
    """Path of the preset file to ship: the library's, or *preset_toml* written out."""
    if not preset_toml:
        path = PRESETS / f"{preset}.toml"
        if not path.is_file():
            raise ValueError(f"No preset {preset!r} in {PRESETS}; give preset_toml for a preset "
                             "that is not in the library.")
        yield str(path)
        return
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / flowlib.preset_file_name(preset)
        path.write_text(preset_toml, encoding="utf-8")
        yield str(path)


@task(name="hpc-sync-code", retries=1, retry_delay_seconds=30)
def sync_code() -> None:
    """Ship the pipeline source to the cluster, when this host has it.

    The submitter image carries only the oceanstream package, so there is
    nothing to sync from it: the cluster keeps whatever revision was last synced
    from a full checkout. `cat $HPC_CODE_DIR/.revision` on the cluster shows which.
    """
    if not (PROJECT_DIR / "scripts" / "batch_processing").is_dir():
        get_run_logger().info("No pipeline source at %s: skipping the code sync; the cluster "
                              "keeps its current revision.", PROJECT_DIR)
        return
    cluster_ops.sync_code(_cluster())


@task(name="hpc-stage", retries=1, retry_delay_seconds=300)
def stage(cruise_id: str, days: list[str], gps: bool) -> None:
    c = _cluster()
    staged = staging.stage_days(c, cruise_id, days)
    if not all(staged.values()):
        raise RuntimeError(f"Staging failed for {[d for d, ok in staged.items() if not ok]}")
    if gps:
        staging.stage_gps(c, cruise_id)


@task(name="hpc-unstage", retries=1, retry_delay_seconds=60)
def unstage(cruise_id: str, days: list[str]) -> None:
    """Remove the days' raw input from scratch; S3 keeps the original."""
    staging.unstage(_cluster(), cruise_id, days)


@task(name="hpc-scratch-free", retries=2, retry_delay_seconds=60)
def scratch_free_gb() -> float:
    return cluster_ops.scratch_free_gb(_cluster())


def _submit(spec: slurm.JobSpec, config: str, render_only: bool = False) -> str:
    """Ship the preset, submit *spec* and wait for it; returns the job id (or the script)."""
    c = _cluster()
    if render_only:
        spec.preset_config = config
        return slurm.build(c.site, spec).script
    spec.preset_config = slurm.ship_preset(c, config, Path(config).read_text(encoding="utf-8"))
    job = slurm.build(c.site, spec)
    jobid = slurm.submit(c, job)
    get_run_logger().info("Submitted %s as job %s", job.name, jobid)
    slurm.wait(c, jobid)
    return jobid


@task(name="hpc-submit-day")
def submit_day(cruise_id: str, day: str, container: str, cpus: int, constraint: str,
               qos: str, walltime: str, preset: str, config: str, gps: bool,
               pipeline_args: list[str] | None) -> str:
    spec = slurm.JobSpec(cruise=cruise_id, days=[day], mode="day", container=container,
                         cpus=cpus, constraint=constraint, qos=qos, walltime=walltime,
                         preset_key=preset,
                         pipeline_args=_pipeline_extra(cruise_id, gps, pipeline_args))
    return _submit(spec, config)


@task(name="hpc-job-metrics", retries=2, retry_delay_seconds=30)
def job_metrics(jobid: str) -> dict[str, Any]:
    return slurm.metrics(slurm.sacct(_cluster(), jobid))


@task(name="hpc-push-products", retries=1, retry_delay_seconds=300)
def push_products(container: str, days: list[str], dest: str, replace: bool = False) -> None:
    # With replace, a reprocessed day does not keep stale chunks.
    if not staging.push_products(_cluster(), container, days, dest=dest, replace=replace):
        raise RuntimeError(f"Push of {container} {days} did not verify")


@task(name="publish-stac", retries=2, retry_delay_seconds=60)
def publish_stac(cruise_id: str, dest_container: str, days: list[str], run_id: str,
                 metrics: dict[str, Any]) -> None:
    from oceanstream.stac.publish import publish  # noqa: PLC0415 - needs fsspec

    # The Collection keeps the campaign metadata published before (licence, DOI, ...).
    publish(_products_root(dest_container), cruise_id, days, run_id=run_id, metrics=metrics,
            nasc_export=False)


@task(name="build-tiles", retries=1, retry_delay_seconds=60)
def build_tiles(dest_container: str, campaign_slug: str) -> str:
    from oceanstream.stac.tracks import build_track_tiles  # noqa: PLC0415 - needs fsspec

    bucket = os.environ["OCEANSTREAM_S3_BUCKET"]
    out = f"s3://{bucket}/tiles/{campaign_slug}_echodata.pmtiles"
    build_track_tiles(f"{_products_root(dest_container)}/collection.json", campaign_slug, out)
    return out


@task(name="hpc-cleanup")
def cleanup(container: str, days: list[str], dest: str) -> None:
    staging.push_products(_cluster(), container, days, dest=dest, verify_only=True,
                          cleanup=True)


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
    from prefect.runtime import flow_run  # noqa: PLC0415

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
    with _preset_config(preset, "") as config:
        jobid = submit_day(cruise_id, day, container, cpus, constraint, qos, walltime, preset,
                           config, gps, pipeline_args)
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
    extra = flowlib.pipeline_args(pipeline_args)
    if gps:
        # Same default as lib.sh: $HPC_SCRATCH/oceanstream/gps/<cruise>.
        gps_root = os.environ.get("HPC_GPS_DIR") or f"{os.environ['HPC_SCRATCH']}/oceanstream/gps"
        extra += ["--gps-dir", f"{gps_root}/{cruise_id}"]
    return extra


@task(name="hpc-submit-array")
def submit_array(cruise_id: str, days: list[str], container: str, cpus: int,
                 constraint: str, qos: str, walltime: str, preset: str, config: str, gps: bool,
                 pipeline_args: list[str] | None, array_limit: int,
                 render_only: bool = False) -> str:
    """One Slurm job array, one day per task. Returns the array job id.

    Failed tasks do not raise here: the caller reads per-task states and
    carries on with the days that completed. With *render_only*, nothing is
    copied or submitted and the rendered batch script is returned instead.
    """
    spec = slurm.JobSpec(cruise=cruise_id, days=days, mode="array", container=container,
                         cpus=cpus, constraint=constraint, qos=qos, walltime=walltime,
                         array_limit=array_limit, preset_key=preset,
                         pipeline_args=_pipeline_extra(cruise_id, gps, pipeline_args))
    return _submit(spec, config, render_only=render_only)


def _s3fs() -> tuple[Any, dict[str, str]]:
    import fsspec  # noqa: PLC0415 - optional dependency

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
def published_days(dest_container: str) -> dict:
    """Days that already have an item.json (pushed and published), with its write time."""
    from datetime import UTC  # noqa: PLC0415

    url_to_fs, so = _s3fs()
    fs, root = url_to_fs(_products_root(dest_container), **so)
    out = {}
    for path, info in fs.glob(f"{root}/*/item.json", detail=True).items():
        t = info.get("LastModified") or info.get("mtime")
        if t is not None and t.tzinfo is None:
            t = t.replace(tzinfo=UTC)
        out[path.rstrip("/").split("/")[-2]] = t
    return out


@task(name="raw-size-gb", retries=2, retry_delay_seconds=30)
def raw_size_gb(cruise_id: str, days: list[str]) -> float:
    """Size of the days' raw input in S3."""
    url_to_fs, so = _s3fs()
    bucket = os.environ["OCEANSTREAM_S3_BUCKET"]
    prefix = os.environ.get("OCEANSTREAM_S3_RAW_PREFIX", "hpc/raw")
    fs, root = url_to_fs(f"s3://{bucket}/{prefix}/{cruise_id}", **so)
    return float(sum(fs.du(f"{root}/{d}") for d in days)) / 1024 ** 3


def _web_url(requested: str) -> str:
    return flowlib.web_url(requested, os.environ.get("OCEANSTREAM_WEB_URLS", ""),
                           os.environ.get("OCEANSTREAM_WEB_URL", ""))


@task(name="import-webapp", retries=2, retry_delay_seconds=60)
def import_webapp(dest_container: str, campaign_slug: str, name: str, provider: str,
                  web_url: str = "") -> dict:
    """Register the Collection with the web app (idempotent upsert)."""
    import httpx  # noqa: PLC0415 - optional dependency

    base = _web_url(web_url)
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
    return dict(r.json())


@flow(name="process-batch-hpc", log_prints=True)
def process_batch_hpc(
    cruise_id: str = "SD_TPOS2023_v03",
    days: list[str] | None = None,
    start_date: str = "",
    end_date: str = "",
    reprocess: bool = False,
    reprocess_since: str = "",
    array_limit: int = 20,
    cpus: int = 8,
    constraint: str = "highmem",
    qos: str = "",
    walltime: str = "02:00:00",
    preset: str = "tpos-sd-2023",
    preset_toml: str = "",
    gps: bool = True,
    pipeline_args: list[str] | None = None,
    campaign_slug: str = "",
    campaign_name: str = "",
    provider: str = "saildrone",
    web_url: str = "",
    cleanup_scratch: bool = True,
    dry_run: bool = False,
) -> dict:
    """Process many days as one Slurm job array, then publish and import them.

    Give *days*, or *start_date*/*end_date* (inclusive). Days that already
    have a published item.json are skipped unless *reprocess* is set, so a
    re-run resumes; with *reprocess_since* (ISO time) a day published after it
    counts as redone, so an in-place reprocess resumes too. Raw input must be
    mirrored to S3 first (mirror_raw_to_s3.py).

    *preset_toml* ships that text as the preset, under the key *preset*; the
    key must change whenever the text does. *web_url* picks the web app to
    import into from OCEANSTREAM_WEB_URLS. *dry_run* stages and submits
    nothing and returns the rendered batch script.
    """
    from prefect.runtime import flow_run  # noqa: PLC0415

    log = get_run_logger()
    _check(cruise_id, NAME_RE, "cruise_id")
    _check(preset, NAME_RE, "preset")
    if campaign_slug:
        _check(campaign_slug, NAME_RE, "campaign_slug")
    if campaign_slug and not dry_run:
        _web_url(web_url)  # refuse a bad target before hours of cluster time
    days = flowlib.expand_days(days, start_date, end_date)
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

    if not reprocess or reprocess_since:
        days, skipped = flowlib.select_days(days, published_days(cruise_id), reprocess,
                                            reprocess_since)
        if skipped:
            log.info("Already published, skipped: %s", ", ".join(skipped))
    if not days:
        return {"processed": [], "failed": [], "note": "nothing to do"}

    with _preset_config(preset, preset_toml) as config:
        if dry_run:
            script = submit_array(cruise_id, days, container, cpus, constraint,
                                  qos or os.environ.get("HPC_QOS", ""), walltime, preset, config,
                                  gps, pipeline_args, array_limit, render_only=True)
            return {"dry_run": True, "days": days, "script": script}
        sync_code()
        stage(cruise_id, days, gps)
        jobid = submit_array(cruise_id, days, container, cpus, constraint,
                             qos or os.environ.get("HPC_QOS", ""), walltime, preset, config, gps,
                             pipeline_args, array_limit)
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
        if cleanup_scratch:
            unstage(cruise_id, days)
        raise RuntimeError(f"No day of array {jobid} completed")
    push_products(container, ok, dest, replace=True)
    publish_stac(cruise_id, cruise_id, ok, run_id, metrics)
    if campaign_slug:
        result["tiles"] = build_tiles(cruise_id, campaign_slug)
        result["import"] = import_webapp(cruise_id, campaign_slug, campaign_name, provider, web_url)
    if cleanup_scratch:
        cleanup(container, ok, dest)
        # Failed days are restaged by the re-run, which skips files already there.
        unstage(cruise_id, days)
    if failed:
        log.warning("Re-run for the failed days: %s", ",".join(failed))
    return result


@flow(name="process-campaign-hpc", log_prints=True)
def process_campaign_hpc(
    cruise_id: str,
    days: list[str] | None = None,
    start_date: str = "",
    end_date: str = "",
    reprocess: bool = False,
    reprocess_since: str = "",
    batch_size: int = 40,
    array_limit: int = 20,
    cpus: int = 8,
    constraint: str = "highmem",
    qos: str = "",
    walltime: str = "02:00:00",
    preset: str = "tpos-sd-2023",
    preset_toml: str = "",
    gps: bool = True,
    pipeline_args: list[str] | None = None,
    campaign_slug: str = "",
    campaign_name: str = "",
    provider: str = "saildrone",
    web_url: str = "",
    dry_run: bool = False,
) -> dict:
    """Process a whole campaign as a sequence of batches (see process_batch_hpc).

    Each batch is staged, run as one job array, pushed, published, imported
    into the web app and removed from scratch before the next one starts, so
    scratch holds one batch at a time. Before each batch the scratch quota is
    checked against the batch's raw size; the run stops cleanly if it is short.
    A failed day does not stop the campaign; the days are listed at the end.
    Re-running with the same arguments resumes (see *reprocess_since*).
    """
    log = get_run_logger()
    _check(cruise_id, NAME_RE, "cruise_id")
    _check(preset, NAME_RE, "preset")
    if campaign_slug and not dry_run:
        _web_url(web_url)
    days = flowlib.expand_days(days, start_date, end_date)
    have = available_days(cruise_id)
    days = [d for d in days if d in have]
    if not reprocess or reprocess_since:
        days, skipped = flowlib.select_days(days, published_days(cruise_id), reprocess,
                                            reprocess_since)
        if skipped:
            log.info("%d day(s) already published, skipped", len(skipped))
    groups = flowlib.batches(days, batch_size) if days else []
    log.info("%d day(s) in %d batch(es) of up to %d", len(days), len(groups), batch_size)
    if dry_run:
        groups = groups[:1]  # one rendered script shows what every batch runs

    done: list[str] = []
    failed: list[str] = []
    runs: list[dict] = []
    for n, group in enumerate(groups, 1):
        if not dry_run:
            need = raw_size_gb(cruise_id, group) * SCRATCH_PER_RAW_GB
            free = scratch_free_gb()
            if free < need:
                raise RuntimeError(
                    f"Batch {n}/{len(groups)} needs about {need:.0f} GB of scratch and "
                    f"{free:.0f} GB is free. Free scratch (cleanup-staging) or lower "
                    "batch_size, then re-run with the same arguments to resume."
                )
            log.info("Batch %d/%d: %d day(s), %s to %s (scratch: %.0f GB needed, %.0f GB free)",
                     n, len(groups), len(group), group[0], group[-1], need, free)
        result = process_batch_hpc(
            cruise_id=cruise_id, days=group, reprocess=True, array_limit=array_limit, cpus=cpus,
            constraint=constraint, qos=qos, walltime=walltime, preset=preset,
            preset_toml=preset_toml, gps=gps, pipeline_args=pipeline_args,
            campaign_slug=campaign_slug, campaign_name=campaign_name, provider=provider,
            web_url=web_url, cleanup_scratch=True, dry_run=dry_run,
        )
        runs.append(result)
        done += result.get("processed", [])
        failed += result.get("failed", [])
    if failed:
        log.warning("%d day(s) failed; re-run for: %s", len(failed), ",".join(failed))
    return {"batches": len(groups), "processed": done, "failed": failed, "runs": runs}


@flow(name="publish-campaign", log_prints=True)
def publish_campaign(cruise_id: str = "SD_TPOS2023_v03", campaign_slug: str = "") -> dict:
    """Rebuild the Collection and (optionally) the campaign's track tiles."""
    _check(cruise_id, NAME_RE, "cruise_id")
    from oceanstream.stac.publish import publish  # noqa: PLC0415 - needs fsspec

    publish(_products_root(cruise_id), cruise_id)
    out = {"collection": f"{_products_root(cruise_id)}/collection.json"}
    if campaign_slug:
        out["tiles"] = build_tiles(cruise_id, _check(campaign_slug, NAME_RE, "campaign_slug"))
    return out
