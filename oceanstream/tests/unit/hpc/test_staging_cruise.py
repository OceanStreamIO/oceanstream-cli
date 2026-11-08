"""Staging, pushing and the resumable cruise driver, against a fake cluster."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from oceanstream.hpc import cruise, staging
from oceanstream.hpc.console import HpcError
from oceanstream.hpc.site import HpcSite

from .conftest import FakeCluster


def test_flat_sources_pick_files_by_day() -> None:
    assert staging.file_patterns("2022-06-21") == ["*D20220621-T*.raw"]


def test_a_fast_clock_shifts_the_window_across_midnight() -> None:
    # Clock 7 h fast: UTC 2019-07-10 is file time 07:00 on the 10th to 07:00 on the 11th,
    # plus the hour before, for a file that starts then and runs into the day.
    p = staging.file_patterns("2019-07-10", shift_hours=-7)
    assert p[0] == "*D20190710-T06*.raw"
    assert p[-1] == "*D20190711-T06*.raw"
    assert "*D20190710-T23*.raw" in p and "*D20190711-T00*.raw" in p
    assert len(p) == 25


def test_stage_days_layouts(fake: FakeCluster) -> None:
    got = staging.stage_days(fake, "X", ["2022-06-21", "2022-06-22"])
    assert got == {"2022-06-21": True, "2022-06-22": True}
    action, args = fake.transfers[0]
    assert action == "pull" and args[:4] == ["--src", "s3://bucket/hpc/raw/X/", "--dest",
                                             "/scratch/g/oceanstream/raw/X"]
    assert args[4:] == ["--include", "2022-06-21/*", "--include", "2022-06-22/*"]

    fake.transfers.clear()
    staging.stage_days(fake, "X", ["2022-06-21"], src="s3://noaa/X/EK80/", layout="flat",
                       opendata=True)
    _, args = fake.transfers[0]
    assert args == ["--src", "s3://noaa/X/EK80/", "--dest",
                    "/scratch/g/oceanstream/raw/X/2022-06-21", "--include",
                    "*D20220621-T*.raw", "--opendata"]
    with pytest.raises(HpcError, match="needs its URI"):
        staging.stage_days(fake, "X", ["2022-06-21"], layout="flat")


def test_unstage_refuses_paths_outside_the_layout(fake: FakeCluster) -> None:
    staging.unstage(fake, "X", ["2022-06-21"])
    assert fake.commands[-1][1] == "rm -rf /scratch/g/oceanstream/raw/X/2022-06-21"
    with pytest.raises(HpcError, match="unexpected path"):
        staging._safe("/scratch/g/oceanstream/raw/2022-06-21", "raw")
    with pytest.raises(HpcError):
        staging.unstage(fake, "X", ["../.."])


def test_push_checks_then_frees_scratch(fake: FakeCluster) -> None:
    fake.handler = lambda host, cmd: (1, "") if "2022-06-22" in cmd and "test -d" in cmd else (0, "")
    good = staging.push_products(fake, "X", ["2022-06-21", "2022-06-22"], replace=True,
                                 cleanup=True)
    assert not good  # one day had nothing to push
    assert [a for a, _ in fake.transfers] == ["push", "check"]
    push_args = fake.transfers[0][1]
    assert "--replace" in push_args and push_args[3] == "s3://bucket/hpc/products/X/"
    assert "rm -rf /scratch/g/oceanstream/products/X/2022-06-21" in [c for _, c, _ in fake.commands]


# -- cruise driver --------------------------------------------------------------------------
RUN_TOML = """
[days]
file = "days.txt"
test = ["2022-06-21"]

[source]
kind = "ncei"
ship = "Oscar_Elton_Sette"

[job]
mode = "node"
cpus = 4
day_memory_gb = 30
walltime = "06:00:00"

[pipeline]
preset = "research-vessel-open-ocean-ryan-short"
args = ["--encode-mode", "power", "--depth-offset", 5]

[output]
dest = "hpc/products/SE2204"
"""


def _campaign(tmp_path: Path, days: list[str], toml: str = RUN_TOML) -> Path:
    d = tmp_path / "SE2204"
    d.mkdir(parents=True)
    (d / "hpc-run.toml").write_text(toml)
    (d / "days.txt").write_text("\n".join(days) + "\n")
    return d


def test_run_config(tmp_path: Path) -> None:
    cfg = cruise.RunConfig.load(_campaign(tmp_path, ["2022-06-22", "2022-06-21"]))
    assert cfg.cruise == "SE2204" and cfg.days == ["2022-06-21", "2022-06-22"]
    assert cfg.source() == "s3://noaa-wcsd-pds/data/raw/Oscar_Elton_Sette/SE2204/EK80/"
    assert cfg.source_layout == "flat" and cfg.opendata
    assert cfg.pipeline_args == ["--encode-mode", "power", "--depth-offset", "5"]
    bad = _campaign(tmp_path / "b", ["2022-06-21"], RUN_TOML + "\n[jobs]\nx = 1\n")
    with pytest.raises(HpcError, match="unknown sections"):
        cruise.RunConfig.load(bad)


class ClusterSim(FakeCluster):
    """Scratch as a set of paths; a submitted job 'processes' every day but *fail*."""

    def __init__(self, site: HpcSite, fail: set[str] | None = None,
                 already: set[str] | None = None):
        self.staged: set[str] = set()
        super().__init__(site, self.answer)
        self.products: set[str] = set(already or ())
        self.fail = fail or set()
        self.jobs: list[str] = []

    def answer(self, host: str, cmd: str) -> tuple[int, str]:
        if cmd.startswith("test -d "):
            return (0, "") if cmd.rsplit("/", 1)[-1] in self.products else (1, "")
        if cmd.startswith("sbatch --parsable"):
            script = self.commands[-2][2]
            self.jobs.append(script)
            return 0, f"{100 + len(self.jobs)}\n"
        if cmd.startswith("squeue"):
            return 0, ""
        if cmd.startswith("sacct -j") and "--units=G" in cmd:
            return 0, "\n".join(f"1.{i}|day-{d}|COMPLETED|0:0|00:30:00|01:00:00|8|20G|21G|n1"
                                for i, d in enumerate(self.todo))
        if " > " in cmd and "days-" in cmd:
            return 0, ""
        return 0, ""

    def transfer(self, action: str, args: Any, *, streams: int | None = None,
                 extra_env: dict[str, str] | None = None) -> int:
        if action == "pull" and "--opendata" in args:
            day = args[3].rsplit("/", 1)[-1]
            self.staged.add(day)
        return super().transfer(action, args, streams=streams)

    @property
    def todo(self) -> list[str]:
        return sorted(self.staged)


@pytest.fixture
def sim(site: HpcSite) -> ClusterSim:
    return ClusterSim(site)


def _process(sim: ClusterSim) -> None:
    # A job leaves products for the staged days, except the failing ones.
    original = sim.answer

    def answer(host: str, cmd: str) -> tuple[int, str]:
        if cmd.startswith("squeue"):
            sim.products |= sim.staged - sim.fail
        return original(host, cmd)

    sim.handler = answer


def test_run_cruise_processes_pushes_and_resumes(tmp_path: Path, sim: ClusterSim,
                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    d = _campaign(tmp_path, ["2022-06-21", "2022-06-22", "2022-06-23"])
    sim.fail = {"2022-06-23"}
    _process(sim)
    published: list[Any] = []
    monkeypatch.setattr(cruise, "publish_campaign", lambda *a, **k: published.append(a) or {})
    result = cruise.run_cruise(sim, d)
    assert result["pushed"] == ["2022-06-21", "2022-06-22"]
    assert result["failed"] == ["2022-06-23"]
    assert len(sim.jobs) == 1 and "#SBATCH --exclusive" in sim.jobs[0]
    assert "--parallel 3" in sim.jobs[0]
    assert published, "STAC is published after a run that pushed days"
    events = (d / "state.tsv").read_text()
    assert "2022-06-23\tfailed-process" in events
    assert list((d / "metrics").glob("*.json"))

    # Rerun: only the failed day is staged and processed again.
    sim.fail = set()
    sim.staged = set()
    sim.transfers.clear()
    result = cruise.run_cruise(sim, d)
    pulls = [a for act, a in sim.transfers if act == "pull"]
    assert len(pulls) == 1 and pulls[0][3].endswith("2022-06-23")
    assert result["failed"] == []
    assert len(published) == 2  # noqa: PLR2004

    # Nothing left to push or publish: no new Collection.
    cruise.run_cruise(sim, d)
    assert len(published) == 2  # noqa: PLR2004

    # A run stopped after its push but before its publish publishes on resume.
    state = d / "state.tsv"
    state.write_text("".join(line + "\n" for line in state.read_text().splitlines()
                             if "\tpublished\t" not in line))
    result = cruise.run_cruise(sim, d)
    assert len(published) == 3 and "publish" in result  # noqa: PLR2004


def test_days_left_on_scratch_are_pushed_without_reprocessing(
        tmp_path: Path, site: HpcSite, monkeypatch: pytest.MonkeyPatch) -> None:
    d = _campaign(tmp_path, ["2022-06-21"])
    sim = ClusterSim(site, already={"2022-06-21"})
    monkeypatch.setattr(cruise, "publish_campaign", lambda *a, **k: {})
    result = cruise.run_cruise(sim, d)
    assert result["pushed"] == ["2022-06-21"]
    assert not sim.jobs
    assert [a for a, _ in sim.transfers] == ["push", "check"]


def test_dry_run_renders_without_touching_the_cluster(tmp_path: Path, sim: ClusterSim) -> None:
    d = _campaign(tmp_path, ["2022-06-21", "2022-06-22"])
    out = cruise.run_cruise(sim, d, dry_run=True)
    assert "run-node" in out["script"] and not sim.transfers
    assert "--encode-mode power --depth-offset 5" in out["script"]
    assert "--ping-time-offset" not in out["script"]


def test_a_fast_clock_corrects_the_ping_times_as_well(tmp_path: Path, sim: ClusterSim) -> None:
    toml = RUN_TOML.replace('ship = "Oscar_Elton_Sette"',
                            'ship = "Oscar_Elton_Sette"\nday_shift_hours = -7')
    d = _campaign(tmp_path, ["2022-06-21"], toml)
    out = cruise.run_cruise(sim, d, dry_run=True)
    assert "--depth-offset 5 --ping-time-offset -25200" in out["script"]


def test_finished_days_come_from_the_done_markers(fake: FakeCluster) -> None:
    listing = ".\n..\n.2022-06-21.DONE\n.2022-06-22.FAILED\n2022-06-21\n"
    fake.handler = lambda host, cmd: (0, listing) if cmd.startswith("ls -a") else (0, "")
    assert staging.finished_days(fake, "X", ["2022-06-21", "2022-06-22"]) == ["2022-06-21"]
    assert fake.commands[-1][1].startswith("ls -a /scratch/g/oceanstream/products/X")

