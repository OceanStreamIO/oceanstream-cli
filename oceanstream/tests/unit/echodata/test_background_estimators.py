"""Tests for the batch pipeline's background noise estimators."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
import xarray as xr

from oceanstream.echodata.denoise.background_estimators import estimate_background_level

NOISE_DB = -150.0
#: How far below the noise the minimum of 20 single-ping block means lands, at least.
MIN_BIAS_DB = 1.2
#: How close the 0.15 quantile stays to the noise.
QUANTILE_TOLERANCE_DB = 1.2
#: How much higher the quantile estimate sits than the minimum, at least.
QUANTILE_GAIN_DB = 0.5
RAISE_DB = 6.0


def _noise_sv(n_pings=60, n_range=600, raised_ping=None, raise_db=6.0):
    """Sv of pure noise at NOISE_DB (power, before range terms), as the pipeline stores it."""
    rng = np.random.default_rng(2)
    power = rng.exponential(1.0, (n_pings, n_range)) * 10 ** (NOISE_DB / 10)
    if raised_ping is not None:
        power[raised_ping] *= 10 ** (raise_db / 10)
    echo_range = np.broadcast_to(1.0 + np.arange(n_range) * 0.5, (n_pings, n_range))
    alpha = 0.01
    sv = 10 * np.log10(power) + 20 * np.log10(echo_range) + 2 * alpha * echo_range
    return xr.Dataset(
        {"Sv": (("channel", "ping_time", "range_sample"), sv[None]),
         "echo_range": (("channel", "ping_time", "range_sample"), echo_range[None]),
         "sound_absorption": (("channel",), [alpha])},
        coords={"channel": ["a"],
                "ping_time": pd.date_range("2022-07-02", periods=n_pings, freq="39s"),
                "range_sample": np.arange(n_range)},
    )


def test_quantile_sits_closer_to_the_noise_on_single_ping_blocks():
    ds = _noise_sv()
    minimum = estimate_background_level(ds, "echopype", ping_num=1, range_sample_num=30)
    quantile = estimate_background_level(ds, "quantile", ping_num=1, range_sample_num=30,
                                         quantile=0.15)
    # The minimum of 20 noisy block means lands well below the noise; the quantile less so.
    assert float(minimum.median()) < NOISE_DB - MIN_BIAS_DB
    assert abs(float(quantile.median()) - NOISE_DB) < QUANTILE_TOLERANCE_DB
    assert float(quantile.median()) > float(minimum.median()) + QUANTILE_GAIN_DB


def test_quantile_follows_a_raised_ping():
    ds = _noise_sv(raised_ping=30, raise_db=RAISE_DB)
    level = estimate_background_level(ds, "quantile", ping_num=1, range_sample_num=30)
    rise = float(level.isel(ping_time=30)) - float(level.drop_isel(ping_time=30).median())
    assert rise == pytest.approx(RAISE_DB, abs=1.5)


def test_same_shape_as_echopype():
    ds = _noise_sv()
    a = estimate_background_level(ds, "echopype", ping_num=5, range_sample_num=30)
    b = estimate_background_level(ds, "quantile", ping_num=5, range_sample_num=30)
    assert a.dims == b.dims and a.shape == b.shape
    assert (a["ping_time"].values == b["ping_time"].values).all()


def test_unknown_estimator():
    with pytest.raises(ValueError, match="Unknown background estimator"):
        estimate_background_level(_noise_sv(), "median", ping_num=1, range_sample_num=30)
