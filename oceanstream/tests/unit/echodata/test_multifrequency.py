"""Tests for multi-frequency dB-differencing module."""

import numpy as np
import pandas as pd
import pytest
import xarray as xr

from oceanstream.echodata.multifrequency import (
    db_difference,
    FrequencyDifferencingResult,
)
from oceanstream.echodata.multifrequency.frequency_differencing import _resolve_channel


def create_two_freq_dataset(
    n_pings: int = 50,
    n_samples: int = 100,
    sv_low: float = -60.0,
    sv_high: float = -55.0,
    krill_region: tuple[int, int, int, int] | None = (30, 10, 50, 30),
    krill_diff: float = -8.0,
) -> xr.Dataset:
    """Create a two-frequency Sv dataset for testing.

    Args:
        n_pings: Number of pings.
        n_samples: Number of range samples.
        sv_low: Background Sv for low frequency (dB).
        sv_high: Background Sv for high frequency (dB).
        krill_region: (row0, col0, row1, col1) region where krill signature
            is injected (Sv_low - Sv_high = krill_diff).
        krill_diff: Expected dB difference for krill signature.

    Returns:
        xr.Dataset with two channels.
    """
    np.random.seed(42)
    Sv = np.zeros((2, n_samples, n_pings))
    Sv[0] = sv_low + np.random.normal(0, 1, (n_samples, n_pings))
    Sv[1] = sv_high + np.random.normal(0, 1, (n_samples, n_pings))

    if krill_region is not None:
        r0, c0, r1, c1 = krill_region
        # Inject krill: Sv_low - Sv_high ≈ krill_diff
        Sv[0, r0:r1, c0:c1] = -50.0
        Sv[1, r0:r1, c0:c1] = -50.0 - krill_diff  # so diff = krill_diff

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
            "channel": ["WBT 742057-15 ES38-18", "WBT 742057-15 ES120-18"],
            "range_sample": np.arange(n_samples),
            "ping_time": ping_times,
        },
    )
    return ds


class TestResolveChannel:
    """Tests for channel resolution helper."""

    def test_exact_match(self):
        ds = create_two_freq_dataset()
        assert _resolve_channel(ds, "WBT 742057-15 ES38-18") == "WBT 742057-15 ES38-18"

    def test_substring_match(self):
        ds = create_two_freq_dataset()
        assert "38" in _resolve_channel(ds, "38")

    def test_not_found(self):
        ds = create_two_freq_dataset()
        with pytest.raises(ValueError, match="not found"):
            _resolve_channel(ds, "999999")


class TestDbDifference:
    """Tests for the db_difference function."""

    def test_basic_detection(self):
        """Krill region should be detected with appropriate threshold."""
        ds = create_two_freq_dataset(krill_diff=-8.0)
        result = db_difference(ds, freq_low="38", freq_high="120", thr=(-12, -2))
        assert isinstance(result, FrequencyDifferencingResult)
        assert result.pixels_in_range > 0
        assert result.fraction_in_range > 0

    def test_mask_shape(self):
        """Mask dimensions should match single-channel Sv."""
        ds = create_two_freq_dataset()
        result = db_difference(ds, freq_low="38", freq_high="120")
        expected_shape = (ds.sizes["range_sample"], ds.sizes["ping_time"])
        assert result.mask.shape == expected_shape
        assert result.difference.shape == expected_shape

    def test_no_match_with_narrow_threshold(self):
        """Nothing should match with a threshold outside the actual range."""
        ds = create_two_freq_dataset(krill_diff=-8.0)
        result = db_difference(ds, freq_low="38", freq_high="120", thr=(100.0, 200.0))
        assert result.pixels_in_range == 0

    def test_all_match_with_wide_threshold(self):
        """Everything should match with a very wide threshold."""
        ds = create_two_freq_dataset()
        result = db_difference(ds, freq_low="38", freq_high="120", thr=(-500, 500))
        assert result.fraction_in_range > 0.99

    def test_threshold_order_validation(self):
        """Should raise ValueError if thr[0] > thr[1]."""
        ds = create_two_freq_dataset()
        with pytest.raises(ValueError, match="Lower threshold"):
            db_difference(ds, freq_low="38", freq_high="120", thr=(10, -10))

    def test_no_channel_dimension(self):
        """Should raise ValueError if dataset has no channel dim."""
        ds = create_two_freq_dataset()
        # Select one channel to remove channel dim
        ds_single = ds.sel(channel="WBT 742057-15 ES38-18")
        with pytest.raises(ValueError, match="channel"):
            db_difference(ds_single, freq_low="38", freq_high="120")

    def test_same_channel_raises(self):
        """Should raise ValueError if both freqs resolve to same channel."""
        ds = create_two_freq_dataset()
        with pytest.raises(ValueError, match="same channel"):
            db_difference(ds, freq_low="38", freq_high="38")

    def test_result_metadata(self):
        """Result should contain correct metadata."""
        ds = create_two_freq_dataset()
        result = db_difference(ds, freq_low="38", freq_high="120", thr=(-12, -2))
        assert "38" in result.freq_low
        assert "120" in result.freq_high
        assert result.threshold == (-12.0, -2.0)
        assert result.pixels_total > 0

    def test_difference_computation(self):
        """The difference array should be Sv_low - Sv_high."""
        ds = create_two_freq_dataset()
        result = db_difference(ds, freq_low="38", freq_high="120")
        sv_low = ds["Sv"].sel(channel="WBT 742057-15 ES38-18").values
        sv_high = ds["Sv"].sel(channel="WBT 742057-15 ES120-18").values
        expected_diff = sv_low - sv_high
        np.testing.assert_array_almost_equal(
            result.difference.values, expected_diff, decimal=10
        )

    def test_bug_fix_both_thresholds_applied(self):
        """Verify the echopy bug fix: both lower AND upper thresholds work.

        The original echopy code had a bug where the second mask assignment
        overwrote the first, collapsing to single-sided thresholding.
        """
        ds = create_two_freq_dataset(krill_diff=-8.0)
        # Use a range that should include the krill region  
        result = db_difference(ds, freq_low="38", freq_high="120", thr=(-10, -6))
        
        diff_vals = result.difference.values[result.mask.values]
        if len(diff_vals) > 0:
            # All masked values MUST be within [thr[0], thr[1]]
            assert np.all(diff_vals >= -10.0), "Lower threshold not applied"
            assert np.all(diff_vals <= -6.0), "Upper threshold not applied"
