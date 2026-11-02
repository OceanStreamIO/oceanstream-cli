"""Day selection, batching and guards of the HPC Prefect flows (scripts/hpc/flowlib.py)."""

from __future__ import annotations

import importlib.util
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

HPC = Path(__file__).resolve().parents[3] / "scripts" / "hpc"


def _flowlib():
    spec = importlib.util.spec_from_file_location("flowlib", HPC / "flowlib.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["flowlib"] = module
    spec.loader.exec_module(module)
    return module


fl = _flowlib()


def _t(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=UTC)


def test_expand_days_from_range_and_list():
    assert fl.expand_days(None, "2023-06-01", "2023-06-03") == [
        "2023-06-01", "2023-06-02", "2023-06-03"]
    assert fl.expand_days(["2023-06-02", "2023-06-01", "2023-06-02"], "", "") == [
        "2023-06-01", "2023-06-02"]
    with pytest.raises(ValueError, match="start_date"):
        fl.expand_days(None, "", "")
    with pytest.raises(ValueError, match="before"):
        fl.expand_days(None, "2023-06-03", "2023-06-01")
    with pytest.raises(ValueError, match="Invalid day"):
        fl.expand_days(["2023-6-1"], "", "")


def test_select_days_without_reprocess_skips_published():
    published = {"2023-06-01": _t("2026-09-20T00:00:00")}
    todo, skipped = fl.select_days(["2023-06-01", "2023-06-02"], published, False)
    assert todo == ["2023-06-02"]
    assert skipped == ["2023-06-01"]


def test_select_days_reprocess_redoes_everything():
    published = {"2023-06-01": _t("2026-09-20T00:00:00")}
    assert fl.select_days(["2023-06-01", "2023-06-02"], published, True) == (
        ["2023-06-01", "2023-06-02"], [])


def test_select_days_reprocess_since_resumes_in_place():
    published = {
        "2023-06-01": _t("2026-09-20T00:00:00"),  # old run: redo
        "2023-06-02": _t("2026-11-03T10:00:00"),  # already redone by this run
    }
    todo, skipped = fl.select_days(
        ["2023-06-01", "2023-06-02", "2023-06-03"], published, True, "2026-11-03T09:00:00Z")
    assert todo == ["2023-06-01", "2023-06-03"]
    assert skipped == ["2023-06-02"]


def test_since_without_zone_is_utc():
    assert fl.parse_since("2026-11-03T09:00:00") == _t("2026-11-03T09:00:00")
    assert fl.parse_since("") is None


def test_batches():
    days = [f"2023-06-{d:02d}" for d in range(1, 8)]
    assert [len(b) for b in fl.batches(days, 3)] == [3, 3, 1]
    assert fl.batches(days, 40) == [days]
    with pytest.raises(ValueError):
        fl.batches(days, 0)


def test_web_url_allowlist():
    default = "https://web.example.org/"
    allowed = "https://dev.example.org, https://other.example.org/"
    assert fl.web_url("", allowed, default) == "https://web.example.org"
    assert fl.web_url("https://dev.example.org/", allowed, default) == "https://dev.example.org"
    assert fl.web_url("https://web.example.org", "", default) == "https://web.example.org"
    with pytest.raises(ValueError, match="OCEANSTREAM_WEB_URLS"):
        fl.web_url("https://evil.example.org", allowed, default)


def test_preset_file_name_is_the_key():
    assert fl.preset_file_name("denoise-lab.tpos-sd-2023.1a2b3c4d") == (
        "denoise-lab.tpos-sd-2023.1a2b3c4d.toml")
    with pytest.raises(ValueError):
        fl.preset_file_name("../etc/passwd")


def test_pipeline_args_caller_flags_come_last():
    assert fl.pipeline_args(None) == [*fl.HPC_RUN_ARGS, *fl.TPOS_PIPELINE_ARGS]
    args = fl.pipeline_args(["--sv-clip-max-db", "-20"])
    assert args[: len(fl.HPC_RUN_ARGS)] == fl.HPC_RUN_ARGS
    assert args[-2:] == ["--sv-clip-max-db", "-20"]
    assert "--expected-categories" not in args


BSC_QUOTA = (
    "\x1b[1m\x1b[38;5;117m Printing quota for group ehpc812:\x1b[22m\x1b[0m\n\n"
    "\x1b[1;30m\x1b[47m    Filesystem   Type          Usage          Quota          Limit\x1b[0m\n"
    "\x1b[38;5;252m\x1b[48;5;235m     gpfs_home    USR        5.05 MB       80.00 GB       84.00 GB\x1b[0m\n"
    "\x1b[38;5;252m\x1b[48;5;235m gpfs_projects    GRP        8.54 GB     1000.00 GB        1.03 TB\x1b[0m\n"
    "\x1b[38;5;252m\x1b[48;5;235m  gpfs_scratch    GRP      519.29 GB        1.00 TB        1.03 TB"
    "\x1b[38;5;252m      1.53 GB      None  |      139264       313  \x1b[0m\n"
)


def test_parse_bsc_quota():
    used, quota = fl.parse_bsc_quota(BSC_QUOTA)
    assert used == pytest.approx(519.29)
    assert quota == pytest.approx(1024.0)
    used, quota = fl.parse_bsc_quota(BSC_QUOTA, "gpfs_home")
    assert used == pytest.approx(5.05 / 1024)
    with pytest.raises(ValueError):
        fl.parse_bsc_quota("nothing here")
