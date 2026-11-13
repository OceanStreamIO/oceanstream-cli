"""Echograms squeeze long recording gaps, draw the seabed line, and describe themselves.

The ``<png>.json`` sidecar is what a viewer uses to read time, depth and Sv off the image,
so the tests read pixels back through it the way a viewer does.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

xr = pytest.importorskip("xarray")
pd = pytest.importorskip("pandas")
pytest.importorskip("matplotlib")
Image = pytest.importorskip("PIL.Image")

from oceanstream.echodata.plot.echogram import (  # noqa: E402
    plot_sv_channel,
    time_axis,
    transducer_offset,
)

OFFSET_M = 3.57


def three_stretches() -> xr.Dataset:
    """NF2104 2021-06-17 in miniature: three recorded stretches, hours apart.

    Sv is -75 dB except a -55 dB layer at 10-12 m of depth during the second stretch,
    and a seabed echo (-20 dB) from 25 m of depth down.
    """
    stretches = [("2021-06-17T00:20", 240), ("2021-06-17T08:30", 220), ("2021-06-17T16:50", 400)]
    times = np.concatenate([
        pd.date_range(start, periods=n, freq="60s").values for start, n in stretches
    ])
    rng_m = np.arange(0, 30, 0.1)
    depth_m = rng_m + OFFSET_M
    sv = np.full((times.size, rng_m.size), -75.0)
    second = slice(240, 460)
    sv[second, (depth_m >= 10) & (depth_m < 12)] = -55.0
    sv[:, depth_m >= 25] = -20.0
    shape = (1, times.size, rng_m.size)
    return xr.Dataset(
        {
            "Sv": (["channel", "ping_time", "range_sample"], sv[np.newaxis]),
            "echo_range": (["channel", "ping_time", "range_sample"],
                           np.broadcast_to(rng_m, shape).copy()),
            "depth": (["channel", "ping_time", "range_sample"],
                      np.broadcast_to(depth_m, shape).copy()),
        },
        coords={"channel": ["GPT 38 kHz"], "ping_time": times,
                "range_sample": np.arange(rng_m.size)},
    )


def read_back(png: Path, doc: dict, when: np.datetime64, depth: float) -> tuple[float, float, str]:
    """The pixel at *when* and *depth*, through the sidecar: (x, y, colour)."""
    t = np.datetime64(when, "ns").astype(np.int64)
    run = next(r for r in doc["time"]["runs"]
               if np.datetime64(r["t0"][:-1], "ns").astype(np.int64) <= t
               <= np.datetime64(r["t1"][:-1], "ns").astype(np.int64))
    t0 = np.datetime64(run["t0"][:-1], "ns").astype(np.int64)
    t1 = np.datetime64(run["t1"][:-1], "ns").astype(np.int64)
    x = run["x0"] + (t - t0) / (t1 - t0) * (run["x1"] - run["x0"])
    area, vert = doc["plot_area"], doc["vertical"]
    frac = (depth - vert["top"]) / (vert["bottom"] - vert["top"])
    y = area["top"] + frac * (area["bottom"] - area["top"])
    with Image.open(png) as im:
        r, g, b = im.convert("RGB").getpixel((int(round(x)), int(round(y))))
    return x, y, f"#{r:02x}{g:02x}{b:02x}"


def sv_of(colour: str, doc: dict) -> float:
    lut = doc["colour"]["lut"]
    i = lut.index(colour)
    lo, hi = doc["colour"]["vmin"], doc["colour"]["vmax"]
    return lo + (i + 0.5) * (hi - lo) / len(lut)


class TestTimeAxis:
    def test_long_gaps_get_narrow_bands(self):
        ds = three_stretches()
        axis = time_axis(ds["ping_time"].values, squeeze_s=600)
        assert len(axis.runs) == 3 and len(axis.gaps) == 2
        recorded = sum(r[1] - r[0] for r in axis.runs) / 1e9
        for g0, g1, x0, x1 in axis.gaps:
            assert x1 - x0 == pytest.approx(max(60.0, 0.015 * recorded))
            assert (g1 - g0) / 1e9 > 4 * 3600
        # The drawn axis is the recorded time plus the two bands, not 16.5 hours.
        assert axis.span_s == pytest.approx(recorded + 2 * max(60.0, 0.015 * recorded))

    def test_x_is_monotonic_and_invertible_inside_runs(self):
        times = three_stretches()["ping_time"].values
        axis = time_axis(times, squeeze_s=600)
        x = axis.to_x(times)
        assert np.all(np.diff(x) > 0)
        back = axis.to_time(x)
        ns = times.astype("datetime64[ns]").astype(np.int64)
        assert np.all(np.abs(back.astype(np.int64) - ns) < 1e6)

    def test_nothing_squeezed_without_a_threshold(self):
        times = three_stretches()["ping_time"].values
        axis = time_axis(times, squeeze_s=None)
        assert axis.gaps == []
        assert axis.span_s == pytest.approx((times[-1] - times[0]) / np.timedelta64(1, "s"))


class TestEchogramPanel:
    @pytest.fixture()
    def drawn(self, tmp_path: Path) -> tuple[Path, dict, xr.Dataset]:
        ds = three_stretches()
        seabed = xr.DataArray(np.full(ds.sizes["ping_time"], 25.0 - OFFSET_M),
                              dims=["ping_time"], coords={"ping_time": ds["ping_time"].values})
        png = plot_sv_channel(
            ds, channel=0, file_base_name="day", echogram_path=str(tmp_path),
            seabed_depth=seabed + transducer_offset(ds), dpi=60,
        )
        doc = json.loads(png.with_suffix(".json").read_text())
        return png, doc, ds

    def test_sidecar_describes_the_image(self, drawn):
        png, doc, _ = drawn
        with Image.open(png) as im:
            assert (doc["image"]["width"], doc["image"]["height"]) == im.size
        area = doc["plot_area"]
        assert 0 < area["left"] < area["right"] < doc["image"]["width"]
        assert 0 < area["top"] < area["bottom"] < doc["image"]["height"]
        assert len(doc["time"]["runs"]) == 3 and len(doc["time"]["gaps"]) == 2
        assert doc["time"]["gaps"][0]["t0"].startswith("2021-06-17T04:19")
        assert len(doc["colour"]["lut"]) == 256
        assert doc["vertical"]["name"] == "depth"
        assert doc["seabed_line"] is True

    def test_gaps_take_a_small_share_of_the_width(self, drawn):
        _, doc, _ = drawn
        area = doc["plot_area"]
        gaps = sum(g["x1"] - g["x0"] for g in doc["time"]["gaps"])
        assert gaps / (area["right"] - area["left"]) < 0.05

    def test_a_pixel_reads_back_as_its_sv(self, drawn):
        png, doc, _ = drawn
        # The -55 dB layer at 11 m in the second stretch, and -75 dB water above it.
        _, _, layer = read_back(png, doc, np.datetime64("2021-06-17T10:00"), 11.0)
        assert sv_of(layer, doc) == pytest.approx(-55.0, abs=0.2)
        _, _, water = read_back(png, doc, np.datetime64("2021-06-17T10:00"), 7.0)
        assert sv_of(water, doc) == pytest.approx(-75.0, abs=0.2)
        _, _, other = read_back(png, doc, np.datetime64("2021-06-17T02:00"), 11.0)
        assert sv_of(other, doc) == pytest.approx(-75.0, abs=0.2)

    def test_the_seabed_line_is_drawn_at_its_depth(self, drawn):
        png, doc, _ = drawn
        _, _, colour = read_back(png, doc, np.datetime64("2021-06-17T18:00"), 25.0)
        r, g, b = (int(colour[i : i + 2], 16) for i in (1, 3, 5))
        assert max(r, g, b) < 90 or min(r, g, b) > 235  # black line or its white edge
