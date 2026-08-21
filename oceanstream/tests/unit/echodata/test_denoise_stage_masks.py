"""Unit tests for per-stage mask alignment and execution status.

The comparison depends on being able to tell "the filter ran and found nothing"
apart from "the filter could not run", and on skipped channels reindexing to
``False`` rather than to ``NaN`` (which upcasts the cube to float and then
evaluates as ``True`` in every downstream boolean reduction).
"""

from __future__ import annotations

import numpy as np
import pytest

xr = pytest.importorskip("xarray")
pd = pytest.importorskip("pandas")

from oceanstream.echodata.denoise.denoise import build_full_mask  # noqa: E402


@pytest.fixture
def two_channel_sv() -> xr.Dataset:
    ping_time = pd.date_range("2023-10-10T02:00:00", periods=40, freq="3s")
    depth = np.arange(0, 60, 2, dtype=float)
    sv = np.full((2, ping_time.size, depth.size), -80.0)
    return xr.Dataset(
        {
            "Sv": (["channel", "ping_time", "depth"], sv),
            "frequency_nominal": ("channel", [38000.0, 200000.0]),
        },
        coords={"ping_time": ping_time, "depth": depth, "channel": ["ch38", "ch200"]},
    )


def _mask_fn(flag_ping: int):
    """Stage function that flags one ping across the whole column."""

    def fn(ch_ds, _params):
        mask = xr.zeros_like(ch_ds["Sv"], dtype=bool)
        mask[dict(ping_time=flag_ping)] = True
        return mask, xr.zeros_like(mask)

    return fn


def _failing_fn(ch_ds, _params):
    freq = float(ch_ds["frequency_nominal"].values)
    if freq == 200000.0:
        raise RuntimeError("reference band out of range at 200 kHz")
    mask = xr.zeros_like(ch_ds["Sv"], dtype=bool)
    mask[dict(ping_time=3)] = True
    return mask, xr.zeros_like(mask)


class TestStageMaskAlignment:
    def test_failing_channel_fills_false_not_nan(self, two_channel_sv):
        mask, cubes, status = build_full_mask(
            two_channel_sv,
            stages={"attenuation": {"fn": _failing_fn, "param_sets": {"threshold": 8.0}}},
            return_stage_masks=True,
            return_status=True,
        )
        cube = cubes["attenuation"]
        assert cube.dtype == np.dtype(bool)
        assert cube.sizes["channel"] == 2
        assert not bool(cube.sel(channel="ch200").any())
        assert bool(cube.sel(channel="ch38").any())
        assert not bool(cube.isnull().any())

    def test_failing_channel_status_is_recorded(self, two_channel_sv):
        _, _, status = build_full_mask(
            two_channel_sv,
            stages={"attenuation": {"fn": _failing_fn, "param_sets": {"threshold": 8.0}}},
            return_stage_masks=True,
            return_status=True,
        )
        assert status["attenuation"]["ch38"] == "ran"
        assert status["attenuation"]["ch200"].startswith("failed:")

    def test_ran_and_found_nothing_differs_from_could_not_run(self, two_channel_sv):
        def _empty(ch_ds, _params):
            mask = xr.zeros_like(ch_ds["Sv"], dtype=bool)
            return mask, mask

        _, _, status = build_full_mask(
            two_channel_sv,
            stages={"transient": {"fn": _empty, "param_sets": {"thr_dB": 12.0}}},
            return_stage_masks=True,
            return_status=True,
        )
        assert set(status["transient"].values()) == {"ran"}

    def test_skipped_channel_marked_no_params(self, two_channel_sv):
        # Frequency-keyed params covering only 38 kHz → 200 kHz is skipped.
        _, _, status = build_full_mask(
            two_channel_sv,
            stages={
                "impulse": {
                    "fn": _mask_fn(2),
                    "param_sets": {"38000": {"threshold_db": 10.0}},
                }
            },
            return_stage_masks=True,
            return_status=True,
        )
        assert status["impulse"]["ch38"] == "ran"
        assert status["impulse"]["ch200"] == "skipped_no_params"

    def test_combined_mask_is_boolean_and_full_shape(self, two_channel_sv):
        mask = build_full_mask(
            two_channel_sv,
            stages={"attenuation": {"fn": _failing_fn, "param_sets": {"threshold": 8.0}}},
            return_stage_masks=False,
        )
        assert mask.dtype == np.dtype(bool)
        assert mask.shape == two_channel_sv["Sv"].shape

    def test_union_identity_holds_for_successful_channels(self, two_channel_sv):
        mask, cubes = build_full_mask(
            two_channel_sv,
            stages={
                "impulse": {"fn": _mask_fn(1), "param_sets": {"threshold_db": 10.0}},
                "transient": {"fn": _mask_fn(5), "param_sets": {"thr_dB": 12.0}},
            },
            return_stage_masks=True,
        )
        expected = cubes["impulse"] | cubes["transient"]
        assert bool((mask == expected).all())

    def test_return_flags_control_the_tuple_shape(self, two_channel_sv):
        stages = {"impulse": {"fn": _mask_fn(1), "param_sets": {"threshold_db": 10.0}}}
        only_mask = build_full_mask(
            two_channel_sv, stages=stages, return_stage_masks=False
        )
        assert isinstance(only_mask, xr.DataArray)

        pair = build_full_mask(two_channel_sv, stages=stages, return_stage_masks=True)
        assert len(pair) == 2

        triple = build_full_mask(
            two_channel_sv, stages=stages, return_stage_masks=True, return_status=True
        )
        assert len(triple) == 3

        status_only = build_full_mask(
            two_channel_sv, stages=stages, return_stage_masks=False, return_status=True
        )
        assert len(status_only) == 2
        assert isinstance(status_only[1], dict)
