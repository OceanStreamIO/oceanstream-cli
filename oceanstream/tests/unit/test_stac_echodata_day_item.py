"""Day Items from processed echosounder directories (oceanstream.stac.echodata)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import xarray as xr

from oceanstream.stac.echodata import _parse_echogram, build_day_item

DAY = "2022-06-21"
CH = "WBT-747014-15-ES38-7_ES-38-kHz"


@pytest.mark.parametrize(
    ("name", "folder", "expected"),
    [
        # One colormap: no colormap segment, raw has no variant either.
        (f"{DAY}--short_pulse_{CH}.png", "raw", ("raw", "ocean_r", CH)),
        (f"{DAY}--short_pulse--denoised_{CH}.png", "denoised",
         ("denoised", "ocean_r", CH)),
        (f"{DAY}--short_pulse--denoised-pruned_{CH}.png", "pruned",
         ("denoised-pruned", "ocean_r", CH)),
        (f"{DAY}--short_pulse--mvbs_{CH}.png", "mvbs", ("mvbs", "ocean_r", CH)),
        (f"{DAY}--short_pulse--nasc-integrated_ch0.png", "nasc",
         ("nasc-integrated", "ocean_r", "ch0")),
        # Several colormaps: the colormap follows the variant.
        (f"{DAY}--short_pulse--jet_{CH}.png", "raw", ("raw", "jet", CH)),
        (f"{DAY}--short_pulse--denoised--ocean_r_{CH}.png", "denoised",
         ("denoised", "ocean_r", CH)),
        (f"{DAY}--long_pulse--denoised-pruned--EK500_{CH}.png", "pruned",
         ("denoised-pruned", "EK500", CH)),
    ],
)
def test_parse_echogram_names(name: str, folder: str, expected: tuple) -> None:
    pm = _parse_echogram(name, DAY, folder, {"short_pulse", "long_pulse"}, "ocean_r")
    assert pm is not None
    assert (pm["variant"], pm["cmap"], pm["channel"]) == expected
    assert pm["pulse"] == name.split("--", 2)[1].split("_WBT", 1)[0]
    assert pm["thumb"] is False


def test_parse_echogram_thumb_and_other_day() -> None:
    pulses = {"short_pulse"}
    name = f"{DAY}--short_pulse--denoised_{CH}--thumb.png"
    pm = _parse_echogram(name, DAY, "denoised", pulses, "x")
    assert pm is not None and pm["thumb"] and pm["channel"] == CH
    assert _parse_echogram(f"2022-06-22--short_pulse_{CH}.png", DAY, "raw", pulses, "x") is None
    assert _parse_echogram(f"{DAY}--unknown_{CH}.png", DAY, "raw", pulses, "x") is None


def test_build_day_item_single_colormap(tmp_path: Path) -> None:
    day_dir = tmp_path / DAY
    day_dir.mkdir()
    times = pd.date_range(f"{DAY}T00:00", periods=4, freq="1min")
    ds = xr.Dataset(
        {"Sv": (("channel", "ping_time"), np.zeros((1, 4)))},
        coords={"channel": [CH], "ping_time": times, "frequency_nominal": ("channel", [38000.0]),
                "latitude": ("ping_time", [30.0, 30.1, 30.2, 30.3]),
                "longitude": ("ping_time", [-150.0, -150.1, -150.2, -150.3])},
    )
    ds.to_zarr(day_dir / f"{DAY}--short_pulse.zarr")
    (day_dir / f"{DAY}--short_pulse--denoise_stats.json").write_text("{}")
    for folder, stem in [("raw", "short_pulse"), ("denoised", "short_pulse--denoised"),
                         ("pruned", "short_pulse--denoised-pruned")]:
        (day_dir / folder).mkdir()
        (day_dir / folder / f"{DAY}--{stem}_{CH}.png").write_bytes(b"")

    item = build_day_item(str(day_dir), "RR2205")

    assets = item["assets"]
    assert item["properties"]["oceanstream:georeferenced"]
    assert assets["short_pulse_denoise_stats"]["roles"] == ["metadata"]
    echograms = {k: a for k, a in assets.items() if k.startswith("echogram_")}
    variants = {a["oceanstream:variant"] for a in echograms.values()}
    assert variants == {"raw", "denoised", "denoised-pruned"}
    assert {a["oceanstream:colormap"] for a in echograms.values()} == {"ocean_r"}
    assert "echogram_short_pulse_raw_ocean-r_wbt-747014-15-es38-7-es-38-khz" in echograms
