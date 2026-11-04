"""Site settings, batch scripts, job accounting and the per-day runner."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from oceanstream.hpc import node, slurm
from oceanstream.hpc.console import HpcError
from oceanstream.hpc.site import HpcSite, read_env_file

from .conftest import SITE


# -- site -------------------------------------------------------------------------------
def test_layout_is_derived_from_project_and_scratch(site: HpcSite) -> None:
    assert site.root == "/proj/g/oceanstream"
    assert site.code_dir == "/proj/g/oceanstream/code"
    assert site.raw_dir == "/scratch/g/oceanstream/raw"
    assert site.products_dir == "/scratch/g/oceanstream/products"
    assert site.s3_endpoint == "https://s3.example"
    assert site.mem_per_core("highmem") == 8
    assert site.mem_per_core("") == 2


def test_environment_wins_over_the_site_file(tmp_path: Path) -> None:
    f = tmp_path / "site.env"
    f.write_text('# comment\nexport HPC_USER=fileuser\nHPC_MODULES="hdf5 python/3.12.1"\n'
                 "HPC_ACCOUNT=acct # trailing\n")
    assert read_env_file(f) == {"HPC_USER": "fileuser", "HPC_MODULES": "hdf5 python/3.12.1",
                                "HPC_ACCOUNT": "acct"}
    site = HpcSite.load({"HPC_USER": "envuser"}, site_file=f)
    assert site.user == "envuser"
    assert site.modules == "hdf5 python/3.12.1"


def test_missing_settings_are_named() -> None:
    site = HpcSite.from_mapping({"HPC_USER": "u"})
    with pytest.raises(HpcError, match="HPC_LOGIN_HOST.*HPC_ACCOUNT"):
        site.require()
    with pytest.raises(HpcError, match="whole number"):
        HpcSite.from_mapping({"HPC_XFER_STREAMS": "four"})


def test_as_env_round_trips(site: HpcSite) -> None:
    again = HpcSite.from_mapping(site.as_env())
    assert again == site


# -- batch scripts --------------------------------------------------------------------------
def test_empty_sbatch_options_are_dropped() -> None:
    text = "#SBATCH --qos=@QOS@\n#SBATCH --account=@A@\nrun @A@\n"
    assert slurm.render(text, {"QOS": "", "A": "x"}) == "#SBATCH --account=x\nrun x\n"
    with pytest.raises(HpcError, match="unfilled"):
        slurm.render("@MISSING@", {})


def test_day_job_runs_run_day_with_the_pipeline_flags(site: HpcSite) -> None:
    spec = slurm.JobSpec(cruise="RR2205", days=["2022-06-21"], cpus=16, walltime="04:00:00",
                         preset_config="/p/presets/x.toml", preset_key="x",
                         pipeline_args=["--depth-offset", "6.2", "--seabed-line", "edge"])
    job = slurm.build(site, spec)
    s = job.script
    assert job.name == "os-RR2205-2022-06-21" and not job.days_file
    for line in ("#SBATCH --account=acct1", "#SBATCH --qos=normal", "#SBATCH --partition=gpp",
                 "#SBATCH --constraint=highmem", "#SBATCH --cpus-per-task=16",
                 "#SBATCH --time=04:00:00", "module load hdf5 python/3.12.1",
                 'source "/proj/g/oceanstream/venv/bin/activate"'):
        assert line in s
    assert "#SBATCH --array" not in s
    run = s.splitlines()[-1]
    assert run.startswith("exec python -u -m oceanstream.hpc run-day --cruise RR2205")
    assert "--day 2022-06-21" in run and "--mem-budget-gb 128" in run
    assert run.endswith("-- --preset-key x --denoise-config /p/presets/x.toml "
                        "--depth-offset 6.2 --seabed-line edge")


def test_array_job_takes_one_day_per_task(site: HpcSite) -> None:
    spec = slurm.JobSpec(cruise="X", days=["2022-06-21", "2022-06-22", "2022-06-23"],
                         mode="array", array_limit=2, constraint="")
    job = slurm.build(site, spec)
    assert "#SBATCH --array=0-2%2" in job.script
    assert "#SBATCH --constraint" not in job.script
    assert f"--days-file {job.days_file}" in job.script
    assert "--mem-budget-gb 32" in job.script  # 16 cpus x 2 GB without the constraint


def test_node_job_takes_the_whole_node(site: HpcSite) -> None:
    spec = slurm.JobSpec(cruise="X", days=["2022-06-21", "2022-06-22"], mode="node", cpus=4,
                         parallel=2, step_mem_gb=40, dask_memory="22GB")
    s = slurm.build(site, spec).script
    for line in ("#SBATCH --exclusive", "#SBATCH --ntasks=2", "#SBATCH --cpus-per-task=4"):
        assert line in s
    run = s.splitlines()[-1]
    assert "run-node --days-file" in run and "--parallel 2 --cpus-per-day 4" in run
    assert "--step-mem-gb 40" in run and "--dask-memory 22GB" in run


def test_parallel_days_is_bounded_by_days_cores_and_memory() -> None:
    assert slurm.parallel_days(60, 112, 4, 896, 30) == 23   # memory: 716.8 // 30
    assert slurm.parallel_days(60, 112, 16, 896, 10) == 7   # cores
    assert slurm.parallel_days(3, 112, 4, 896, 10) == 3     # days
    with pytest.raises(HpcError, match="needs 900 GB"):
        slurm.parallel_days(3, 112, 4, 896, 900)


def test_bad_specs_are_refused(site: HpcSite) -> None:
    with pytest.raises(HpcError, match="YYYY-MM-DD"):
        slurm.build(site, slurm.JobSpec(cruise="X", days=["22-06-21"]))
    with pytest.raises(HpcError, match="exactly one day"):
        slurm.build(site, slurm.JobSpec(cruise="X", days=["2022-06-21", "2022-06-22"]))


# -- accounting ------------------------------------------------------------------------------
ARRAY_ROWS = """\
100_0|os-X-array|COMPLETED|0:0|00:30:00|01:00:00|32||||n1
100_0.batch|batch|COMPLETED|0:0|00:30:00|01:00:00|32|27.5G|30G|n1
100_1|os-X-array|FAILED|1:0|00:10:00|00:20:00|32||||n2
100_1.batch|batch|FAILED|1:0|00:10:00|00:20:00|32|3.1G|4G|n2
"""
NODE_ROWS = """\
200|os-X-node|COMPLETED|0:0|02:00:00|10:00:00|224||||n3
200.batch|batch|COMPLETED|0:0|02:00:00|00:01:00|224|0.2G|1G|n3
200.0|day-2022-06-21|COMPLETED|0:0|00:40:00|01:30:00|8|25.0G|26G|n3
200.1|day-2022-06-22|FAILED|1:0|00:05:00|00:09:00|8|2.0G|3G|n3
"""


def test_metrics_per_array_task_and_per_node_day() -> None:
    m = slurm.metrics(ARRAY_ROWS)
    assert set(m) == {"0", "1"}
    assert m["0"]["max_rss_gb"] == 27.5 and slurm.completed(m["0"])
    assert not slurm.completed(m["1"])
    days = slurm.metrics(NODE_ROWS)
    assert set(days) == {"2022-06-21", "2022-06-22"}
    assert days["2022-06-21"]["max_rss_gb"] == 25.0
    assert slurm.completed(days["2022-06-21"]) and not slurm.completed(days["2022-06-22"])


def test_submit_parses_the_job_id_past_banners(fake: Any) -> None:
    fake.handler = lambda host, cmd: (0, "Welcome\n4242\n") if "sbatch" in cmd else (0, "")
    job = slurm.build(fake.site, slurm.JobSpec(cruise="X", days=["2022-06-21"]))
    assert slurm.submit(fake, job) == "4242"
    assert any(job.script == inp for _, _, inp in fake.commands)


def test_wait_polls_until_the_job_leaves_the_queue(fake: Any) -> None:
    states = iter(["PENDING|Priority", "RUNNING|n1", "RUNNING|n1", ""])

    def handler(host: str, cmd: str) -> tuple[int, str]:
        if cmd.startswith("squeue"):
            return 0, next(states)
        return 0, "1|COMPLETED|0:0|00:01:00\n"

    fake.handler = handler
    assert "COMPLETED" in slurm.wait(fake, "1", poll_s=1, sleep=lambda s: None)


# -- per-day runner ---------------------------------------------------------------------------
def _day_run(tmp_path: Path) -> node.DayRun:
    raw = tmp_path / "raw" / "X" / "2022-06-21"
    raw.mkdir(parents=True)
    (raw / "a-D20220621-T000000.raw").write_bytes(b"0" * 10)
    return node.DayRun(cruise="X", day="2022-06-21", container="X", raw_root=tmp_path / "raw",
                       products_root=tmp_path / "products", job_tmp=tmp_path / "tmp",
                       code_dir=tmp_path / "code", mem_budget_gb=32)


def test_run_day_moves_finished_products_and_cleans_up(tmp_path: Path) -> None:
    run = _day_run(tmp_path)
    seen: dict[str, Any] = {}

    def runner(cmd: list[str], **kw: Any) -> subprocess.CompletedProcess[Any]:
        seen["cmd"], seen["env"] = cmd, kw["env"]
        work = Path(cmd[cmd.index("--local-save") + 1])
        (work / "X" / "2022-06-21").mkdir(parents=True)
        (work / "X" / "2022-06-21" / "sv.zarr").write_text("x")
        return subprocess.CompletedProcess(cmd, 0)

    assert node.run_day(run, ["--depth-offset", "5"], runner) == 0
    assert (tmp_path / "products" / "X" / "2022-06-21" / "sv.zarr").is_file()
    assert seen["cmd"][-2:] == ["--depth-offset", "5"]
    assert seen["cmd"][seen["cmd"].index("--raw-dir") + 1].endswith("raw/X/2022-06-21")
    assert seen["env"]["OMP_NUM_THREADS"] == "1"
    assert not run.tmp_dir().exists()


def test_run_day_without_products_fails(tmp_path: Path) -> None:
    run = _day_run(tmp_path)
    ok = lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0)  # noqa: E731
    assert node.run_day(run, [], ok) == node.NO_PRODUCTS
    failed = lambda cmd, **kw: subprocess.CompletedProcess(cmd, 7)  # noqa: E731
    assert node.run_day(run, [], failed) == 7


def test_day_from_file(tmp_path: Path) -> None:
    f = tmp_path / "days.txt"
    f.write_text("2022-06-21\n\n2022-06-22\n")
    assert node.day_from_file(f, 1) == "2022-06-22"
    with pytest.raises(ValueError, match="no day for task 2"):
        node.day_from_file(f, 2)


def test_run_node_runs_each_day_as_a_step_and_marks_it(tmp_path: Path) -> None:
    run = _day_run(tmp_path)
    calls: list[list[str]] = []

    def runner(cmd: list[str], **kw: Any) -> subprocess.CompletedProcess[Any]:
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0 if "--job-name=day-2022-06-21" in cmd else 1)

    results = node.run_node(["2022-06-21", "2022-06-22"], 2, 4, run, ["--cruise", "X"],
                            ["--depth-offset", "5"], step_mem_gb=30, runner=runner)
    assert results == {"2022-06-21": 0, "2022-06-22": 1}
    step = next(c for c in calls if "--job-name=day-2022-06-21" in c)
    assert step[:6] == ["srun", "--exact", "--ntasks=1", "--cpus-per-task=4",
                        "--job-name=day-2022-06-21", "--mem=30G"]
    assert step[-4:] == ["2022-06-21", "--", "--depth-offset", "5"]
    assert (tmp_path / "products" / "X" / ".2022-06-21.DONE").is_file()
    assert (tmp_path / "products" / "X" / ".2022-06-22.FAILED").is_file()


def test_templates_only_set_up_the_environment_and_call_the_cli() -> None:
    """The batch scripts hold no logic: SBATCH headers, environment, one exec."""
    for name in ("process-day.sbatch", "process-node.sbatch"):
        body = [line for line in slurm.template(name).splitlines()
                if line and not line.startswith("#")]
        assert body[-1].startswith("exec python -u -m oceanstream.hpc run-")
        allowed = ("@MODULE_LOAD@", "source ", "unset ", "export PYTHONPATH=", "cd ", "exec ")
        assert all(line.startswith(allowed) for line in body), body


def test_site_fixture_matches_the_constants() -> None:
    assert SITE["HPC_ACCOUNT"] == "acct1"
