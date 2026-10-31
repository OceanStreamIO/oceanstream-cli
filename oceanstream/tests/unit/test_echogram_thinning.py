"""Echogram panels thin long days by picking pings, not averaging them."""

from __future__ import annotations

import os
import sys
from contextlib import contextmanager
from pathlib import Path

import numpy as np
import pytest

xr = pytest.importorskip("xarray")
pd = pytest.importorskip("pandas")

_SCRIPTS = Path(__file__).resolve().parents[3] / "scripts" / "batch_processing"


@contextmanager
def scripts_importable():
    """Import ``scripts/batch_processing`` without leaking ``config`` or the environment."""
    env, path, shadowed = dict(os.environ), list(sys.path), sys.modules.pop("config", None)
    sys.path.insert(0, str(_SCRIPTS))
    try:
        yield
    finally:
        sys.path[:] = path
        sys.modules.pop("config", None)
        if shadowed is not None:
            sys.modules["config"] = shadowed
        os.environ.clear()
        os.environ.update(env)


with scripts_importable():
    import process_campaign as pc  # noqa: E402


def _day(n_pings: int) -> xr.Dataset:
    times = pd.date_range("2023-10-10", periods=n_pings, freq="3s")
    rng = np.random.default_rng(0)
    sv = rng.uniform(-90, -50, size=(1, n_pings, 4))
    sv[0, 7, :] = np.nan  # a ping a filter removed
    return xr.Dataset(
        {
            "Sv": (("channel", "ping_time", "range_sample"), sv),
            "denoise_flags": (("channel", "ping_time", "range_sample"),
                              np.zeros_like(sv, dtype=np.uint8)),
            "frequency_nominal": (("channel",), [38000.0]),
        },
        coords={"channel": ["38"], "ping_time": times, "range_sample": np.arange(4)},
    )


def test_short_day_is_drawn_whole():
    ds = _day(10)
    assert pc._thin_pings_for_plot(ds, max_pings=10) is ds


def test_disabled_draws_every_ping():
    ds = _day(30)
    assert pc._thin_pings_for_plot(ds, max_pings=0) is ds


def test_long_day_keeps_every_kth_ping_unchanged():
    ds = _day(29)
    thinned = pc._thin_pings_for_plot(ds, max_pings=10)

    # ceil(29 / 10) = 3: pings 0, 3, 6, ..., 27
    assert thinned.sizes["ping_time"] == 10
    np.testing.assert_array_equal(thinned["ping_time"], ds["ping_time"][::3])
    np.testing.assert_array_equal(thinned["Sv"], ds["Sv"][:, ::3])
    assert thinned["denoise_flags"].dtype == np.uint8
    assert thinned["frequency_nominal"].identical(ds["frequency_nominal"])


def test_removed_ping_stays_empty():
    ds = _day(30)
    thinned = pc._thin_pings_for_plot(ds, max_pings=15)  # every 2nd ping, ping 7 not kept
    assert not np.isnan(thinned["Sv"].values).any()

    thinned = pc._thin_pings_for_plot(ds.isel(ping_time=slice(1, None)), max_pings=15)
    # Starting at ping 1, every 2nd keeps ping 7: it is drawn as removed, not filled in.
    assert np.isnan(thinned["Sv"].sel(ping_time=ds["ping_time"][7]).values).all()
