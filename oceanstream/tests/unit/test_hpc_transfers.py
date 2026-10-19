"""The HPC scripts move data over SSH from this side; the cluster never connects out."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

HPC = Path(__file__).resolve().parents[3] / "scripts" / "hpc"


def _xfer():
    spec = importlib.util.spec_from_file_location("xfer", HPC / "xfer.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["xfer"] = module
    spec.loader.exec_module(module)
    return module


def test_no_script_uses_the_queued_rclone_path():
    for script in sorted(HPC.glob("*.sh")):
        text = script.read_text()
        for word in ("hpc_xfer_rclone", "HPC_XFER_RCLONE_CMD", "dtrclone", "s3_remote"):
            assert word not in text, f"{script.name} still uses {word}"
    assert "xfer.py" in (HPC / "lib.sh").read_text()


def test_day_filters_select_whole_days():
    xfer = _xfer()
    assert xfer.wanted("2023-10-10/a.raw", ["2023-10-10/*"], [])
    assert xfer.wanted("2023-10-10/sub/b.nc", ["2023-10-10/*"], [])
    assert not xfer.wanted("2023-10-11/a.raw", ["2023-10-10/*"], [])


def test_replace_removes_only_what_the_pushed_days_no_longer_have():
    xfer = _xfer()
    present = {"2023-10-10/a.nc": 1, "2023-10-10/old.nc": 1, "2023-10-11/b.nc": 1}
    assert xfer.stale(present, {"2023-10-10/a.nc": 1}, ["2023-10-10/*"], []) == [
        "2023-10-10/old.nc"]
