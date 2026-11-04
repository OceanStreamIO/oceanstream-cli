"""Process a whole cruise on the cluster, resumably.

A cruise is a campaign directory holding ``hpc-run.toml`` (see
:class:`RunConfig` and docs/hpc-campaigns.md). :func:`run_cruise`:

1. stages the raw files of every pending day (or of one batch, in ``array`` mode);
2. runs them as one whole-node job (``node``) or one job array per batch (``array``);
3. removes the raw files, pushes the finished days to S3, verifies and frees them;
4. publishes the STAC Collection with the campaign's metadata, and the track tiles.

Progress goes to ``state.tsv`` in the campaign directory, one line per event.
Running again resumes: days already pushed are skipped, and days whose products
are still on scratch are pushed without being processed again.
"""

from __future__ import annotations

import json
import threading
import tomllib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import slurm, staging
from .console import HpcError, log, ok, warn
from .publish import publish_campaign
from .remote import Cluster

PRESETS_DIR = Path(__file__).resolve().parents[1] / "echodata" / "presets" / "library"
NCEI_BUCKET = "s3://noaa-wcsd-pds/data/raw"


@dataclass
class RunConfig:
    """``hpc-run.toml``: what to process and how."""

    cruise: str
    days: list[str]
    test_days: list[str] = field(default_factory=list)
    # [source]
    source_kind: str = "s3"          # ncei | s3
    source_uri: str = ""             # s3 kind: the raw prefix; '' = the project's raw prefix
    source_layout: str = "days"      # days | flat
    ship: str = ""                   # ncei kind
    instrument: str = "EK80"         # ncei kind (NCEI also files EK80-format data under EK60)
    day_shift_hours: int = 0
    # [gps]
    gps: bool = False
    gps_uri: str = ""
    # [job]
    mode: str = "node"               # node | array
    cpus: int = 4
    walltime: str = "12:00:00"
    constraint: str | None = None
    qos: str = ""
    day_memory_gb: float = 30.0
    parallel: int = 0                # 0 = as many as cores and memory allow
    step_mem_gb: int = 0
    batch: int = 6                   # array mode
    array_limit: int = 0             # array mode; 0 = the batch size
    dask_workers: int = 1
    # [pipeline]
    preset: str = ""
    pipeline_args: list[str] = field(default_factory=list)
    # [output]
    container: str = ""
    dest: str = ""

    @property
    def opendata(self) -> bool:
        return self.source_kind == "ncei"

    def source(self) -> str:
        if self.source_kind == "ncei":
            if not self.ship:
                raise HpcError("source.ship is required for an NCEI source, "
                               "e.g. Henry_B._Bigelow.")
            return f"{NCEI_BUCKET}/{self.ship}/{self.cruise}/{self.instrument}/"
        return self.source_uri

    @classmethod
    def load(cls, campaign_dir: Path, cruise: str = "") -> RunConfig:
        path = campaign_dir / "hpc-run.toml"
        if not path.is_file():
            raise HpcError(f"No {path}. Create it from docs/hpc-campaigns.md.")
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
        cruise = raw.get("cruise") or cruise or _campaign_id(campaign_dir)
        days_cfg = raw.get("days", {})
        days = list(days_cfg.get("list", []))
        if days_cfg.get("file"):
            days += [d.strip() for d in (campaign_dir / days_cfg["file"]).read_text().splitlines()
                     if d.strip() and not d.startswith("#")]
        if not days:
            raise HpcError(f"{path}: give days.file or days.list.")
        src, gps, job = raw.get("source", {}), raw.get("gps", {}), raw.get("job", {})
        pipe, out = raw.get("pipeline", {}), raw.get("output", {})
        known = {"cruise", "days", "source", "gps", "job", "pipeline", "output"}
        unknown = set(raw) - known
        if unknown:
            raise HpcError(f"{path}: unknown sections {sorted(unknown)}")
        cfg = cls(
            cruise=cruise, days=staging.check_days(sorted(dict.fromkeys(days))),
            test_days=list(days_cfg.get("test", [])),
            source_kind=src.get("kind", "s3"), source_uri=src.get("uri", ""),
            source_layout=src.get("layout", "flat" if src.get("kind") == "ncei" else "days"),
            ship=src.get("ship", ""), instrument=src.get("instrument", "EK80"),
            day_shift_hours=int(src.get("day_shift_hours", 0)),
            gps=bool(gps.get("enabled", bool(gps))), gps_uri=gps.get("uri", ""),
            mode=job.get("mode", "node"), cpus=int(job.get("cpus", 4)),
            walltime=job.get("walltime", "12:00:00"), constraint=job.get("constraint"),
            qos=job.get("qos", ""), day_memory_gb=float(job.get("day_memory_gb", 30)),
            parallel=int(job.get("parallel", 0)), step_mem_gb=int(job.get("step_mem_gb", 0)),
            batch=int(job.get("batch", 6)), array_limit=int(job.get("array_limit", 0)),
            dask_workers=int(job.get("dask_workers", 1)),
            preset=pipe.get("preset", ""), pipeline_args=[str(a) for a in pipe.get("args", [])],
            container=out.get("container", "") or cruise, dest=out.get("dest", ""),
        )
        if cfg.mode not in ("node", "array"):
            raise HpcError(f"{path}: job.mode must be 'node' or 'array', not {cfg.mode!r}")
        return cfg


def _campaign_id(campaign_dir: Path) -> str:
    meta = campaign_dir / "campaign.json"
    if meta.is_file():
        cid = json.loads(meta.read_text()).get("campaign_id")
        if cid:
            return str(cid)
    return campaign_dir.name


class State:
    """``state.tsv``: ``<day>\\t<event>\\t<UTC time>`` lines, appended."""

    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch(exist_ok=True)
        self._lock = threading.Lock()

    def mark(self, day: str, event: str) -> None:
        with self._lock, self.path.open("a") as f:
            f.write(f"{day}\t{event}\t{datetime.now(UTC):%Y-%m-%dT%H:%M:%SZ}\n")

    def events(self) -> list[tuple[str, str]]:
        out = []
        for line in self.path.read_text().splitlines():
            parts = line.split("\t")
            if len(parts) >= 2:  # noqa: PLR2004 - day, event
                out.append((parts[0], parts[1]))
        return out

    def pushed(self) -> set[str]:
        return {d for d, e in self.events() if e == "pushed"}


class Pusher:
    """Pushes finished days in the background, one push at a time."""

    def __init__(self, cluster: Cluster, cfg: RunConfig, state: State, streams: int):
        self.cluster, self.cfg, self.state, self.streams = cluster, cfg, state, streams
        self._thread: threading.Thread | None = None

    def _push(self, days: list[str]) -> None:
        try:
            good = staging.push_products(self.cluster, self.cfg.container, days,
                                         dest=self.cfg.dest, replace=True, cleanup=True,
                                         streams=self.streams)
        except HpcError as e:
            warn(str(e))
            good = False
        for d in days:
            self.state.mark(d, "pushed" if good else "failed-push")

    def push(self, days: list[str]) -> None:
        if not days:
            return
        self.wait()
        log(f"Pushing in the background: {' '.join(days)}")
        self._thread = threading.Thread(target=self._push, args=(days,), daemon=True)
        self._thread.start()

    def wait(self) -> None:
        if self._thread is not None:
            self._thread.join()
            self._thread = None


def preset_spec(cluster: Cluster, cfg: RunConfig) -> tuple[str, str]:
    """Ship the preset to the cluster; returns (remote path, key)."""
    if not cfg.preset:
        return "", ""
    path = PRESETS_DIR / f"{cfg.preset}.toml"
    if not path.is_file():
        raise HpcError(f"No preset {cfg.preset!r} in {PRESETS_DIR}.")
    return slurm.ship_preset(cluster, str(path), path.read_text(encoding="utf-8")), cfg.preset


def job_spec(cluster: Cluster, cfg: RunConfig, days: list[str], preset: tuple[str, str]
             ) -> slurm.JobSpec:
    site = cluster.site
    args = list(cfg.pipeline_args)
    if cfg.gps and "--gps-dir" not in args:
        args += ["--gps-dir", f"{site.gps_dir}/{cfg.cruise}"]
    mode: slurm.Mode = "node" if cfg.mode == "node" else "array"
    spec = slurm.JobSpec(cruise=cfg.cruise, days=days, mode=mode,
                         container=cfg.container, cpus=cfg.cpus, walltime=cfg.walltime,
                         qos=cfg.qos, constraint=cfg.constraint, dask_workers=cfg.dask_workers,
                         array_limit=cfg.array_limit or len(days), step_mem_gb=cfg.step_mem_gb,
                         preset_config=preset[0], preset_key=preset[1], pipeline_args=args)
    if spec.mode == "node":
        constraint = site.constraint if cfg.constraint is None else cfg.constraint
        node_gb = site.mem_per_core(constraint) * site.cores_per_node
        spec.parallel = cfg.parallel or slurm.parallel_days(
            len(days), site.cores_per_node, cfg.cpus, node_gb, cfg.day_memory_gb)
        # Each day's budget is its share of the node, not its cores' share.
        spec.dask_memory = spec.dask_memory or f"{int(cfg.day_memory_gb * 3 // 4)}GB"
    return spec


class _Run:
    """One pass over a cruise's pending days."""

    def __init__(self, cluster: Cluster, cfg: RunConfig, state: State):
        self.cluster, self.cfg, self.state = cluster, cfg, state
        self.pusher = Pusher(cluster, cfg, state, streams=8)
        self.metrics: dict[str, Any] = {}
        self.preset: tuple[str, str] = ("", "")

    def resume(self, group: list[str]) -> list[str]:
        """Push days processed before a restart; returns the days still to process."""
        cluster, cfg = self.cluster, self.cfg
        ready = [d for d in group if staging.on_scratch(cluster, cfg.container, d)]
        if ready:
            staging.unstage(cluster, cfg.cruise, ready)
            self.pusher.push(ready)
        return [d for d in group if d not in ready]

    def stage(self, days: list[str]) -> list[str]:
        cfg = self.cfg
        staged = staging.stage_days(self.cluster, cfg.cruise, days, src=cfg.source(),
                                    layout=cfg.source_layout, opendata=cfg.opendata,
                                    shift_hours=cfg.day_shift_hours)
        for d, good in staged.items():
            self.state.mark(d, "staged" if good else "failed-stage")
        return [d for d in days if staged[d]]

    def process(self, days: list[str]) -> None:
        """One job for *days*; then free their raw input and push what finished."""
        cluster, cfg = self.cluster, self.cfg
        job = slurm.build(cluster.site, job_spec(cluster, cfg, days, self.preset))
        jobid = slurm.submit(cluster, job)
        ok(f"Submitted job {jobid} ({job.name}, {len(days)} days)")
        for d in days:
            self.state.mark(d, f"submitted:{jobid}")
        slurm.wait(cluster, jobid)
        try:
            m = slurm.metrics(slurm.sacct(cluster, jobid))
        except HpcError as e:
            warn(f"No accounting for {jobid}: {e}")
            m = {}
        if cfg.mode == "array":
            m = {days[int(i)]: v for i, v in m.items() if str(i).isdigit()}
        self.metrics.update(m)
        # The raw files are a copy of the source; a re-run streams them again.
        staging.unstage(cluster, cfg.cruise, days)
        done = [d for d in days if staging.on_scratch(cluster, cfg.container, d)]
        for d in sorted(set(days) - set(done)):
            self.state.mark(d, "failed-process")
        self.pusher.push(done)

    def run(self, pending: list[str]) -> None:
        cfg = self.cfg
        self.preset = preset_spec(self.cluster, cfg)
        if cfg.gps:
            staging.stage_gps(self.cluster, cfg.cruise, cfg.gps_uri)
        size = len(pending) if cfg.mode == "node" else cfg.batch
        groups = [pending[i:i + size] for i in range(0, len(pending), size)]
        for n, group in enumerate(groups, 1):
            log(f"Batch {n}/{len(groups)}: {len(group)} day(s), {group[0]} to {group[-1]}")
            todo = self.resume(group)
            run_days = self.stage(todo) if todo else []
            if run_days:
                self.process(run_days)
        self.pusher.wait()


def run_cruise(cluster: Cluster, campaign_dir: Path, *, dry_run: bool = False,
               publish: bool = True) -> dict[str, Any]:
    """Process every pending day of the campaign in *campaign_dir*; see the module doc."""
    cfg = RunConfig.load(campaign_dir)
    cluster.site.require_key("HPC_XFER_HOST", "OCEANSTREAM_S3_BUCKET")
    state = State(campaign_dir / "state.tsv")
    pending = [d for d in cfg.days if d not in state.pushed()]
    log(f"{cfg.cruise}: {len(pending)} of {len(cfg.days)} days to process ({cfg.mode} mode); "
        f"state in {state.path}")
    if dry_run:
        preset = (f"<{cfg.preset}.toml>", cfg.preset) if cfg.preset else ("", "")
        spec = job_spec(cluster, cfg, pending or cfg.days, preset)
        return {"days": pending, "script": slurm.build(cluster.site, spec).script}
    run = _Run(cluster, cfg, state)
    if pending:
        run.run(pending)
        metrics_dir = campaign_dir / "metrics"
        metrics_dir.mkdir(exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        (metrics_dir / f"{stamp}.json").write_text(
            json.dumps(run.metrics, indent=1, sort_keys=True))
    pushed = state.pushed()
    failed = [d for d in cfg.days if d not in pushed]
    log(f"Finished: {len(pushed)} of {len(cfg.days)} days pushed"
        + (f"; not pushed: {' '.join(failed)}" if failed else ""))
    result: dict[str, Any] = {"pushed": sorted(pushed), "failed": failed, "metrics": run.metrics}
    if publish and pending and pushed:
        result["publish"] = publish_campaign(cluster, campaign_dir, cfg, all_metrics(campaign_dir))
    return result


def all_metrics(campaign_dir: Path) -> dict[str, Any]:
    """Per-day job metrics from every run of the campaign, latest last."""
    out: dict[str, Any] = {}
    for path in sorted((campaign_dir / "metrics").glob("*.json")):
        out.update(json.loads(path.read_text()))
    return out
