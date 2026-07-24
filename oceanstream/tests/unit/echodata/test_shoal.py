"""Tests for shoal/school detection module."""

import numpy as np
import pandas as pd
import pytest
import xarray as xr

from oceanstream.echodata.shoal import (
    detect_shoals,
    detect_shoals_weill,
    detect_shoals_echoview,
    mask_shoals,
    ShoalDetectionResult,
)
from oceanstream.echodata.shoal.detection import _weill_core, _echoview_core


def create_test_sv_dataset(
    n_pings: int = 100,
    n_samples: int = 200,
    background_sv: float = -80.0,
    shoal_sv: float = -50.0,
    shoal_bbox: tuple[int, int, int, int] | None = (40, 30, 60, 50),
) -> xr.Dataset:
    """Create a test Sv dataset with optional shoal.

    Args:
        n_pings: Number of pings (horizontal).
        n_samples: Number of range samples (vertical).
        background_sv: Background noise level (dB).
        shoal_sv: Shoal Sv value (dB).
        shoal_bbox: (row_start, col_start, row_end, col_end) for shoal region.
            None = no shoal.

    Returns:
        xr.Dataset with Sv data.
    """
    np.random.seed(42)
    Sv = np.full((n_samples, n_pings), background_sv, dtype=float)

    if shoal_bbox is not None:
        r0, c0, r1, c1 = shoal_bbox
        Sv[r0:r1, c0:c1] = shoal_sv + np.random.normal(0, 2, (r1 - r0, c1 - c0))

    range_m = np.linspace(0, 500.0, n_samples)
    ping_times = pd.date_range("2023-06-01", periods=n_pings, freq="1s").values

    ds = xr.Dataset(
        {
            "Sv": (["range_sample", "ping_time"], Sv),
            "echo_range": (
                ["range_sample", "ping_time"],
                np.tile(range_m[:, np.newaxis], (1, n_pings)),
            ),
        },
        coords={
            "range_sample": np.arange(n_samples),
            "ping_time": ping_times,
        },
    )
    return ds


def create_multichannel_dataset() -> xr.Dataset:
    """Create a two-channel dataset for testing channel selection."""
    n_pings, n_samples = 50, 100
    np.random.seed(42)
    Sv = np.random.normal(-80, 3, (2, n_samples, n_pings))
    # Add a shoal to both channels
    Sv[:, 30:50, 15:35] = -50 + np.random.normal(0, 2, (2, 20, 20))

    range_m = np.linspace(0, 500.0, n_samples)
    ping_times = pd.date_range("2023-06-01", periods=n_pings, freq="1s").values

    ds = xr.Dataset(
        {
            "Sv": (["channel", "range_sample", "ping_time"], Sv),
            "echo_range": (
                ["channel", "range_sample", "ping_time"],
                np.tile(range_m[np.newaxis, :, np.newaxis], (2, 1, n_pings)),
            ),
        },
        coords={
            "channel": ["WBT 742057-15 ES38-18", "WBT 742057-15 ES200-18"],
            "range_sample": np.arange(n_samples),
            "ping_time": ping_times,
        },
    )
    return ds


# =============================================================================
# Core algorithm tests
# =============================================================================


class TestWeillCore:
    """Tests for the core Weill numpy algorithm."""

    def test_basic_detect(self):
        """Detects a bright region above threshold."""
        sv = np.full((100, 50), -80.0)
        sv[30:50, 10:30] = -50.0
        mask, edge = _weill_core(sv, thr=-70.0)
        assert mask[40, 20]  # inside shoal
        assert not mask[10, 10]  # outside shoal

    def test_no_shoals(self):
        """Returns empty mask when all Sv is below threshold."""
        sv = np.full((100, 50), -80.0)
        mask, edge = _weill_core(sv, thr=-70.0)
        assert not mask.any()

    def test_vertical_gap_filling(self):
        """Fills small vertical gaps between masked regions."""
        sv = np.full((100, 50), -80.0)
        sv[30:35, 20] = -50.0  # upper part
        sv[38:43, 20] = -50.0  # lower part, gap of 3 samples
        mask, _ = _weill_core(sv, thr=-70.0, maxvgap=5)
        # Gap should be filled
        assert mask[36, 20]

    def test_vertical_gap_not_filled_when_too_large(self):
        """Preserves large vertical gaps."""
        sv = np.full((100, 50), -80.0)
        sv[30:35, 20] = -50.0
        sv[45:50, 20] = -50.0  # gap of 10 samples
        mask, _ = _weill_core(sv, thr=-70.0, maxvgap=5)
        assert not mask[40, 20]  # gap too big, not filled

    def test_horizontal_gap_filling(self):
        """Fills small horizontal gaps when maxhgap > 0."""
        sv = np.full((100, 50), -80.0)
        sv[40, 10:15] = -50.0
        sv[40, 17:22] = -50.0  # gap of 2 pings
        mask, _ = _weill_core(sv, thr=-70.0, maxhgap=3)
        assert mask[40, 16]

    def test_min_vertical_length_filter(self):
        """Filters out features shorter than minvlen."""
        sv = np.full((100, 50), -80.0)
        sv[40:43, 20:30] = -50.0  # 3 samples tall
        mask, _ = _weill_core(sv, thr=-70.0, minvlen=5)
        assert not mask.any()  # too short

    def test_min_horizontal_length_filter(self):
        """Filters out features narrower than minhlen."""
        sv = np.full((100, 50), -80.0)
        sv[40:50, 20:22] = -50.0  # 2 pings wide
        mask, _ = _weill_core(sv, thr=-70.0, minhlen=5)
        assert not mask.any()


class TestEchoviewCore:
    """Tests for the core Echoview-style numpy algorithm."""

    def test_basic_detect(self):
        """Detects shoals above threshold."""
        sv = np.full((100, 50), -80.0)
        sv[30:50, 10:30] = -50.0
        idim = np.arange(100, dtype=float)
        jdim = np.arange(50, dtype=float)
        mask, edge = _echoview_core(
            sv, idim, jdim, thr=-70.0, mincan=(1, 1), minsho=(1, 1)
        )
        assert mask[40, 20]

    def test_candidate_filtering(self):
        """Removes candidates smaller than mincan."""
        sv = np.full((100, 50), -80.0)
        sv[40:42, 20:22] = -50.0  # 2×2 feature
        idim = np.arange(100, dtype=float)
        jdim = np.arange(50, dtype=float)
        mask, _ = _echoview_core(
            sv, idim, jdim, thr=-70.0, mincan=(5, 5), minsho=(1, 1)
        )
        assert not mask.any()

    def test_linking(self):
        """Links nearby shoals within maxlink distance."""
        sv = np.full((100, 50), -80.0)
        sv[30:40, 10:15] = -50.0
        sv[30:40, 17:22] = -50.0  # gap of 2 pings
        idim = np.arange(100, dtype=float)
        jdim = np.arange(50, dtype=float)
        mask, _ = _echoview_core(
            sv, idim, jdim, thr=-70.0,
            mincan=(1, 1), maxlink=(5, 5), minsho=(1, 1),
        )
        # Both features should be retained (linked)
        assert mask[35, 12]
        assert mask[35, 19]

    def test_post_link_filtering(self):
        """Removes linked shoals smaller than minsho."""
        sv = np.full((100, 50), -80.0)
        sv[40:43, 20:23] = -50.0  # small shoal
        idim = np.arange(100, dtype=float)
        jdim = np.arange(50, dtype=float)
        mask, _ = _echoview_core(
            sv, idim, jdim, thr=-70.0,
            mincan=(1, 1), maxlink=(3, 3), minsho=(10, 10),
        )
        assert not mask.any()

    def test_nan_idim_raises(self):
        """Raises ValueError for NaN in range dimension."""
        sv = np.full((10, 10), -50.0)
        idim = np.arange(10, dtype=float)
        idim[5] = np.nan
        jdim = np.arange(10, dtype=float)
        with pytest.raises(ValueError, match="NaN values in range"):
            _echoview_core(sv, idim, jdim)


# =============================================================================
# xarray wrapper tests
# =============================================================================


class TestDetectShoalsWeill:
    """Tests for the Weill xarray wrapper."""

    def test_basic(self):
        ds = create_test_sv_dataset()
        result = detect_shoals_weill(ds, thr=-70.0)
        assert isinstance(result, ShoalDetectionResult)
        assert result.method == "weill"
        assert result.num_shoals >= 1
        assert result.shoal_fraction > 0

    def test_no_shoals(self):
        ds = create_test_sv_dataset(shoal_bbox=None)
        result = detect_shoals_weill(ds, thr=-70.0)
        assert result.num_shoals == 0
        assert result.shoal_fraction == 0.0

    def test_mask_shape_matches_sv(self):
        ds = create_test_sv_dataset()
        result = detect_shoals_weill(ds)
        assert result.mask.shape == ds["Sv"].shape

    def test_channel_selection(self):
        ds = create_multichannel_dataset()
        result = detect_shoals_weill(ds, channel="38")
        assert "38" in result.channel


class TestDetectShoalsEchoview:
    """Tests for the Echoview xarray wrapper."""

    def test_basic(self):
        ds = create_test_sv_dataset()
        result = detect_shoals_echoview(
            ds, thr=-70.0, mincan=(1, 1), maxlink=(3, 15), minsho=(1, 1)
        )
        assert isinstance(result, ShoalDetectionResult)
        assert result.method == "echoview"
        assert result.num_shoals >= 1

    def test_mask_shape_matches_sv(self):
        ds = create_test_sv_dataset()
        result = detect_shoals_echoview(
            ds, mincan=(1, 1), maxlink=(3, 15), minsho=(1, 1)
        )
        assert result.mask.shape == ds["Sv"].shape


class TestDetectShoalsDispatcher:
    """Tests for the detect_shoals() dispatcher."""

    def test_weill_dispatch(self):
        ds = create_test_sv_dataset()
        result = detect_shoals(ds, method="weill", thr=-70.0)
        assert result.method == "weill"

    def test_echoview_dispatch(self):
        ds = create_test_sv_dataset()
        result = detect_shoals(
            ds, method="echoview", mincan=(1, 1), minsho=(1, 1)
        )
        assert result.method == "echoview"

    def test_invalid_method(self):
        ds = create_test_sv_dataset()
        with pytest.raises(ValueError, match="Unknown shoal detection method"):
            detect_shoals(ds, method="invalid")


class TestMaskShoals:
    """Tests for applying shoal masks."""

    def test_mask_replaces_with_nan(self):
        ds = create_test_sv_dataset()
        result = detect_shoals_weill(ds, thr=-70.0)
        masked = mask_shoals(ds, result)
        # Shoal pixels should be NaN
        shoal_vals = masked["Sv"].values[result.mask.values]
        assert np.all(np.isnan(shoal_vals))

    def test_non_shoal_preserved(self):
        ds = create_test_sv_dataset()
        result = detect_shoals_weill(ds, thr=-70.0)
        masked = mask_shoals(ds, result)
        # Non-shoal pixels should be unchanged
        non_shoal = ~result.mask.values
        np.testing.assert_array_equal(
            masked["Sv"].values[non_shoal],
            ds["Sv"].values[non_shoal],
        )
