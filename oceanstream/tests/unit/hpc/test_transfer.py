"""Transfers move data over SSH from this side; the cluster never connects out."""

from __future__ import annotations

from pathlib import Path

from oceanstream.hpc import transfer

ROOT = Path(__file__).resolve().parents[4]


def _xfer():
    return transfer


def test_no_shell_script_left_with_transfer_logic():
    """Transfers live in oceanstream.hpc.transfer; no script reimplements them."""
    for script in sorted((ROOT / "scripts").rglob("*.sh")):
        text = script.read_text()
        for word in ("hpc_xfer_rclone", "HPC_XFER_RCLONE_CMD", "dtrclone", "s3_remote"):
            assert word not in text, f"{script.name} still uses {word}"


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
