# Typer commands take one parameter per option.
# ruff: noqa: PLR0913, PLR0917
"""``oceanstream hpc``: run campaigns on a Slurm cluster (docs/hpc.md, docs/hpc-campaigns.md).

Also reachable as ``python -m oceanstream.hpc``, which is what batch jobs run on
the cluster: it imports only this package, not the rest of the CLI.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import typer

from . import cluster as cluster_ops
from . import slurm, staging
from .console import HpcError, info, ok
from .node import DayRun, day_from_file, run_day, run_node
from .remote import Cluster
from .site import HpcSite

app = typer.Typer(help="Run the echodata pipeline on a Slurm cluster, over SSH.",
                  no_args_is_help=True)

CAMPAIGNS_DIR = typer.Option(
    None, "--campaigns-dir", envvar="OCEANSTREAM_CAMPAIGNS_DIR",
    help="Where campaign directories live (default ~/.oceanstream/campaigns).")


def _cluster() -> Cluster:
    return Cluster(HpcSite.load())


def campaign_path(campaign_id: str, campaigns_dir: Path | None) -> Path:
    """The campaign's directory: ``<campaigns dir>/<id>``, or *campaign_id* if it is a path."""
    given = Path(campaign_id).expanduser()
    if given.is_dir() and (given / "hpc-run.toml").is_file():
        return given.resolve()
    from oceanstream.geotrack.campaign import get_campaigns_dir  # noqa: PLC0415

    path = (campaigns_dir.expanduser() if campaigns_dir else get_campaigns_dir()) / campaign_id
    if not path.is_dir():
        raise HpcError(f"No campaign directory {path}. Create it with "
                       f"`oceanstream campaign create {campaign_id} --from-catalogue …`.")
    return path


def _days(days: str) -> list[str]:
    return staging.check_days([d for d in days.replace(" ", ",").split(",") if d])


def _fail(e: Exception) -> None:
    typer.secho(f"  ✗ {e}", err=True, fg=typer.colors.RED)
    raise typer.Exit(1)


# -- site ------------------------------------------------------------------------------
@app.command("site")
def site_command(
    check: bool = typer.Option(False, "--check",
                               help="Also log in to the login and transfer hosts."),
) -> None:
    """Show the cluster settings in use (no secrets) and check that they are complete."""
    site = HpcSite.load()
    shown = {k: v for k, v in site.as_env().items() if "KEY" not in k or k == "HPC_SSH_KEY"}
    for k in sorted(shown):
        typer.echo(f"{k}={shown[k]}")
    missing = site.missing("HPC_XFER_HOST", "OCEANSTREAM_S3_BUCKET")
    if missing:
        _fail(HpcError(f"Missing: {' '.join(missing)}"))
    if check:
        c = Cluster(site)
        for name, run in (("login", c.login), ("transfer", c.xfer)):
            r = run("hostname")
            if r.returncode != 0:
                _fail(HpcError(f"{name} host: {r.stderr.strip()[-200:]}"))
            ok(f"{name} host answers: {r.stdout.strip().splitlines()[-1]}")


@app.command("quota")
def quota_command() -> None:
    """Print the group's quota table (``bsc_quota`` on the transfer host)."""
    try:
        typer.echo(cluster_ops.quota_table(_cluster()))
    except HpcError as e:
        _fail(e)


@app.command("smoke-test")
def smoke_test_command() -> None:
    """Prove the transfer path: scratch -> object storage over SSH, then clean up."""
    try:
        if not cluster_ops.smoke_test(_cluster()):
            raise typer.Exit(1)
    except HpcError as e:
        _fail(e)


@app.command("bootstrap")
def bootstrap_command(
    dirs: bool = typer.Option(False, "--dirs", help="Create the directory layout."),
    env: bool = typer.Option(False, "--env", help="Build or update the venv."),
    require_gb: int = typer.Option(0, help="With --dirs: fail below this much free scratch."),
    recreate: bool = typer.Option(False, help="With --env: delete the venv first."),
    online: bool = typer.Option(False, help="With --env: install from PyPI, not the wheelhouse."),
    verify: bool = typer.Option(False, help="Only check the venv's imports."),
) -> None:
    """Prepare the cluster: directories, then (after sync-code) the venv."""
    try:
        c = _cluster()
        if dirs:
            cluster_ops.bootstrap_dirs(c, require_gb)
        if env or verify:
            cluster_ops.bootstrap_env(c, recreate=recreate, online=online, verify_only=verify)
        if not (dirs or env or verify):
            raise HpcError("Give --dirs, --env or --verify.")
    except HpcError as e:
        _fail(e)


@app.command("sync-code")
def sync_code_command(dry_run: bool = typer.Option(False, "--dry-run")) -> None:
    """Ship the pipeline source to HPC_CODE_DIR (tar over SSH)."""
    try:
        cluster_ops.sync_code(_cluster(), dry_run=dry_run)
    except HpcError as e:
        _fail(e)


@app.command("transfer", context_settings={"allow_extra_args": True,
                                            "ignore_unknown_options": True})
def transfer_command(ctx: typer.Context, action: str = typer.Argument(
        ..., help="pull, push or check")) -> None:
    """Object storage <-> cluster over SSH (``--src``, ``--dest``, ``--include`` …).

    Arguments after the action go to :mod:`oceanstream.hpc.transfer`; see its
    ``--help`` with ``python -m oceanstream.hpc.transfer <action> --help``.
    """
    try:
        raise typer.Exit(_cluster().transfer(action, ctx.args))
    except HpcError as e:
        _fail(e)


# -- campaign steps ------------------------------------------------------------------------
@app.command("stage")
def stage_command(
    campaign_id: str,
    days: str = typer.Option("", help="Comma-separated days (default: every day)."),
    gps: bool = typer.Option(False, "--gps", help="Stage the campaign's GPS GeoParquet too."),
    cleanup: bool = typer.Option(False, "--cleanup", help="Remove the days' raw input instead."),
    campaigns_dir: Path = CAMPAIGNS_DIR,
) -> None:
    """Stage raw input for a campaign's days on scratch (resumable; e.g. the next cruise)."""
    from .cruise import RunConfig  # noqa: PLC0415

    try:
        cfg = RunConfig.load(campaign_path(campaign_id, campaigns_dir))
        c = _cluster()
        wanted = _days(days) if days else cfg.days
        if cleanup:
            staging.unstage(c, cfg.cruise, wanted)
            return
        result = staging.stage_days(c, cfg.cruise, wanted, src=cfg.source(),
                                    layout=cfg.source_layout, opendata=cfg.opendata,
                                    shift_hours=cfg.day_shift_hours, exclude=cfg.source_exclude)
        if gps or cfg.gps:
            staging.stage_gps(c, cfg.cruise, cfg.gps_uri)
        if not all(result.values()):
            raise typer.Exit(1)
    except HpcError as e:
        _fail(e)


@app.command("submit")
def submit_command(
    campaign_id: str,
    days: str = typer.Option("", help="Comma-separated days (default: every day)."),
    mode: str = typer.Option("", help="day, array or node (default: the campaign's job.mode)."),
    render_only: bool = typer.Option(False, "--render-only", help="Print the batch script."),
    test_only: bool = typer.Option(False, "--test-only", help="Ask Slurm when it would start."),
    detach: bool = typer.Option(False, "--detach", help="Return after submitting."),
    campaigns_dir: Path = CAMPAIGNS_DIR,
) -> None:
    """Submit processing for days already staged; waits for the job unless --detach."""
    from .cruise import RunConfig, calibration_spec, job_spec, preset_spec  # noqa: PLC0415

    try:
        cfg = RunConfig.load(campaign_path(campaign_id, campaigns_dir))
        if mode:
            cfg.mode = "node" if mode == "node" else "array"
        c = _cluster()
        wanted = _days(days) if days else cfg.days
        if render_only:
            preset = (f"<{cfg.preset}.toml>", cfg.preset) if cfg.preset else ("", "")
            calibration = f"<{cfg.cal_params_file.name}>" if cfg.cal_params_file else ""
        else:
            preset = preset_spec(c, cfg)
            calibration = calibration_spec(c, cfg)
        spec = job_spec(c, cfg, wanted, preset, calibration)
        if mode == "day":
            spec.mode = "day"
        job = slurm.build(c.site, spec)
        if render_only:
            typer.echo(job.script, nl=False)
            return
        if test_only:
            typer.echo(slurm.test_only(c, job))
            return
        missing = slurm.missing_raw(c, cfg.cruise, wanted)
        if missing:
            raise HpcError(f"No raw files staged for: {' '.join(missing)}. "
                           f"Run `oceanstream hpc stage {campaign_id}` first.")
        jobid = slurm.submit(c, job)
        ok(f"Submitted job {jobid}")
        typer.echo(f"jobid={jobid}")
        info(f"log: {c.site.log_dir}/{job.name}-{jobid}*.out")
        if not detach:
            typer.echo(slurm.wait(c, jobid))
    except HpcError as e:
        _fail(e)


@app.command("wait")
def wait_command(jobid: str) -> None:
    """Follow a job until it leaves the queue; prints its final sacct rows."""
    try:
        typer.echo(slurm.wait(_cluster(), jobid))
    except HpcError as e:
        _fail(e)


@app.command("metrics")
def metrics_command(jobid: str) -> None:
    """A finished job's accounting as JSON (per task of an array, per day of a node job)."""
    try:
        typer.echo(slurm.dump(slurm.metrics(slurm.sacct(_cluster(), jobid))))
    except HpcError as e:
        _fail(e)


@app.command("push")
def push_command(
    campaign_id: str,
    days: str = typer.Option(..., help="Comma-separated days."),
    verify: bool = typer.Option(False, "--verify", help="Only compare scratch with S3."),
    replace: bool = typer.Option(False, "--replace", help="Remove what a day no longer has."),
    cleanup: bool = typer.Option(False, "--cleanup", help="Free scratch once verified."),
    campaigns_dir: Path = CAMPAIGNS_DIR,
) -> None:
    """Push days' products from scratch to S3 and verify them."""
    from .cruise import RunConfig  # noqa: PLC0415

    try:
        cfg = RunConfig.load(campaign_path(campaign_id, campaigns_dir))
        good = staging.push_products(_cluster(), cfg.container, _days(days), dest=cfg.dest,
                                     verify_only=verify, replace=replace, cleanup=cleanup)
        if not good:
            raise typer.Exit(1)
    except HpcError as e:
        _fail(e)


@app.command("run-cruise")
def run_cruise_command(
    campaign_id: str,
    dry_run: bool = typer.Option(False, "--dry-run", help="Print the batch script only."),
    publish: bool = typer.Option(True, help="Publish STAC and track tiles at the end."),
    campaigns_dir: Path = CAMPAIGNS_DIR,
) -> None:
    """Stage, process, push and publish every pending day of a campaign. Rerun to resume."""
    from .cruise import run_cruise  # noqa: PLC0415

    try:
        result = run_cruise(_cluster(), campaign_path(campaign_id, campaigns_dir),
                            dry_run=dry_run, publish=publish)
        if dry_run:
            typer.echo(result["script"], nl=False)
        elif result.get("failed"):
            raise typer.Exit(1)
    except HpcError as e:
        _fail(e)


@app.command("publish-stac")
def publish_stac_command(
    campaign_id: str,
    days: str = typer.Option("", help="Days to (re)write Items for (default: all pushed)."),
    tiles: bool = typer.Option(True, help="Rebuild the track tiles too."),
    campaigns_dir: Path = CAMPAIGNS_DIR,
) -> None:
    """Rewrite Items and the Collection with the campaign's metadata, and the track tiles."""
    from .cruise import RunConfig, State, all_metrics  # noqa: PLC0415
    from .publish import publish_campaign  # noqa: PLC0415

    try:
        path = campaign_path(campaign_id, campaigns_dir)
        cfg = RunConfig.load(path)
        state = State(path / "state.tsv")
        chosen = _days(days) if days else sorted(state.pushed())
        out = publish_campaign(_cluster(), path, cfg, all_metrics(path), days=chosen, tiles=tiles)
        for day in chosen:
            state.mark(day, "published")
        typer.echo(json.dumps(out, indent=1))
    except HpcError as e:
        _fail(e)


@app.command("compare")
def compare_command(
    ref: str = typer.Argument(..., help="Reference day directory (path or s3:// URL)."),
    new: str = typer.Argument(..., help="Day directory to check against it."),
    atol: float = typer.Option(1e-6, help="Largest allowed absolute difference."),
    report: str = typer.Option("", "--json", help="Write the full report here."),
) -> None:
    """Compare two runs of the same day store by store; exit 1 if they differ."""
    from .compare import compare_days  # noqa: PLC0415 - needs xarray and dask

    if not compare_days(ref, new, atol, report, echo=typer.echo)["ok"]:
        raise typer.Exit(1)


# -- on the compute node ---------------------------------------------------------------------
def _day_run(cruise: str, container: str, day: str, raw_root: str, products_root: str,
             job_tmp: str, mem_budget_gb: int, parallel_workers: int, dask_workers: int,
             dask_memory: str) -> DayRun:
    return DayRun(cruise=cruise, day=day, container=container or cruise,
                  raw_root=Path(raw_root), products_root=Path(products_root),
                  job_tmp=Path(job_tmp), code_dir=Path.cwd(), mem_budget_gb=mem_budget_gb,
                  parallel_workers=parallel_workers, dask_workers=dask_workers,
                  dask_memory=dask_memory)


@app.command("run-day", context_settings={"allow_extra_args": True,
                                           "ignore_unknown_options": True})
def run_day_command(
    ctx: typer.Context,
    cruise: str = typer.Option(...),
    container: str = typer.Option(""),
    day: str = typer.Option("", help="The day; or --days-file with SLURM_ARRAY_TASK_ID."),
    days_file: Path | None = typer.Option(None),
    raw_root: str = typer.Option(...),
    products_root: str = typer.Option(...),
    job_tmp: str = typer.Option(...),
    mem_budget_gb: int = typer.Option(0),
    parallel_workers: int = typer.Option(1),
    dask_workers: int = typer.Option(1),
    dask_memory: str = typer.Option(""),
) -> None:
    """(On the cluster.) Process one day with process_from_raw.py; flags after -- go to it."""
    if not day:
        task = os.environ.get("SLURM_ARRAY_TASK_ID", "")
        if not (days_file and task.isdigit()):
            _fail(HpcError("Give --day, or --days-file inside an array task."))
        assert days_file is not None
        day = day_from_file(days_file, int(task))
    run = _day_run(cruise, container, day, raw_root, products_root, job_tmp, mem_budget_gb,
                   parallel_workers, dask_workers, dask_memory)
    raise typer.Exit(run_day(run, _after_dashes(ctx)))


@app.command("run-node", context_settings={"allow_extra_args": True,
                                            "ignore_unknown_options": True})
def run_node_command(
    ctx: typer.Context,
    days_file: Path = typer.Option(...),
    parallel: int = typer.Option(...),
    cpus_per_day: int = typer.Option(...),
    step_mem_gb: int = typer.Option(0),
    cruise: str = typer.Option(...),
    container: str = typer.Option(""),
    raw_root: str = typer.Option(...),
    products_root: str = typer.Option(...),
    job_tmp: str = typer.Option(...),
    mem_budget_gb: int = typer.Option(0),
    parallel_workers: int = typer.Option(1),
    dask_workers: int = typer.Option(1),
    dask_memory: str = typer.Option(""),
) -> None:
    """(On the cluster.) Run every day of a whole-node job, *parallel* at a time, as job steps."""
    days = [d.strip() for d in days_file.read_text().splitlines() if d.strip()]
    run = _day_run(cruise, container, "", raw_root, products_root, job_tmp, mem_budget_gb,
                   parallel_workers, dask_workers, dask_memory)
    run_args = ["--cruise", cruise, "--container", run.container, "--raw-root", raw_root,
                "--products-root", products_root, "--job-tmp", job_tmp,
                "--mem-budget-gb", str(mem_budget_gb), "--parallel-workers", str(parallel_workers),
                "--dask-workers", str(dask_workers), "--dask-memory", dask_memory]
    results = run_node(days, parallel, cpus_per_day, run, run_args, _after_dashes(ctx),
                       step_mem_gb)
    raise typer.Exit(0 if all(rc == 0 for rc in results.values()) else 1)


def _after_dashes(ctx: typer.Context) -> list[str]:
    """Pipeline flags: everything after ``--`` (click keeps them in ctx.args)."""
    args = list(ctx.args)
    return args[1:] if args[:1] == ["--"] else args


def main() -> None:
    try:
        app()
    except HpcError as e:
        print(f"  ✗ {e}", file=sys.stderr)
        sys.exit(1)
