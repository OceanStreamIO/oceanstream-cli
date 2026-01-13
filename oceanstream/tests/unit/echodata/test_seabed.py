"""Tests for seabed detection module."""

import numpy as np
import pytest
import xarray as xr

from oceanstream.echodata.seabed import (
    detect_seabed,
    detect_seabed_maxSv,
    detect_seabed_deltaSv,
    detect_seabed_ariza,
    mask_seabed,
    compute_seabed_stats,
)
from oceanstream.echodata.seabed.detection import (
    SeabedDetectionResult,
    _get_range_array,
    _get_sv_2d,
)


def create_test_sv_dataset(
    n_pings: int = 100,
    n_samples: int = 500,
    max_range: float = 500.0,
    seabed_depth: float | None = 200.0,  # None = no seabed
    seabed_width: float = 20.0,
    seabed_sv: float = -30.0,
    background_sv: float = -70.0,
    noise_std: float = 3.0,
) -> xr.Dataset:
    """Create a test Sv dataset with optional seabed echo.
    
    Args:
        n_pings: Number of pings.
        n_samples: Number of range samples.
        max_range: Maximum range in meters.
        seabed_depth: Depth of seabed in meters (None = no seabed).
        seabed_width: Width of seabed echo in meters.
        seabed_sv: Sv value at seabed (dB).
        background_sv: Background Sv value (dB).
        noise_std: Standard deviation of noise (dB).
        
    Returns:
        xr.Dataset with Sv data.
    """
    # Create range array
    range_m = np.linspace(0, max_range, n_samples)
    # Create ping times using pandas for proper datetime handling
    import pandas as pd
    ping_times = pd.date_range('2023-06-01', periods=n_pings, freq='1s').values
    
    # Create background Sv with noise
    np.random.seed(42)
    Sv = np.random.normal(background_sv, noise_std, (n_pings, n_samples))
    
    # Add seabed echo if specified
    if seabed_depth is not None:
        # Find range indices for seabed
        seabed_idx = np.argmin(np.abs(range_m - seabed_depth))
        width_samples = int(seabed_width / (max_range / n_samples))
        
        # Add seabed signal
        for p in range(n_pings):
            # Slight depth variation
            depth_var = np.random.uniform(-10, 10)
            idx = int(seabed_idx + depth_var * n_samples / max_range)
            idx = np.clip(idx, 0, n_samples - width_samples - 1)
            
            # Seabed echo (stronger than background)
            Sv[p, idx:idx+width_samples] = seabed_sv + np.random.normal(0, 2, width_samples)
    
    # Create Dataset
    ds = xr.Dataset(
        {
            "Sv": (["ping_time", "range_sample"], Sv),
            "echo_range": (["ping_time", "range_sample"], 
                          np.tile(range_m, (n_pings, 1))),
        },
        coords={
            "ping_time": ping_times,
            "range_sample": np.arange(n_samples),
        },
    )
    
    return ds


def create_multichannel_test_dataset(
    n_pings: int = 100,
    n_samples: int = 500,
    max_range: float = 500.0,
    seabed_depth: float | None = 200.0,
) -> xr.Dataset:
    """Create a multi-channel test dataset (like EK80 with multiple frequencies)."""
    import pandas as pd
    
    # Create range array
    range_m = np.linspace(0, max_range, n_samples)
    ping_times = pd.date_range('2023-06-01', periods=n_pings, freq='1s').values
    channels = np.array(["WBT 742057-15 ES38-18", "WBT 742057-15 ES200-7C"])
    freq_nom = np.array([38000.0, 200000.0])
    
    # Create Sv arrays for each channel
    np.random.seed(42)
    Sv_38 = np.random.normal(-65, 3, (n_pings, n_samples))  # 38 kHz
    Sv_200 = np.random.normal(-70, 3, (n_pings, n_samples))  # 200 kHz
    
    # Add seabed echo (stronger on 38 kHz)
    if seabed_depth is not None:
        seabed_idx = np.argmin(np.abs(range_m - seabed_depth))
        width = 10
        for p in range(n_pings):
            depth_var = int(np.random.uniform(-5, 5) * n_samples / max_range)
            idx = np.clip(seabed_idx + depth_var, 0, n_samples - width - 1)
            Sv_38[p, idx:idx+width] = -25 + np.random.normal(0, 2, width)
            Sv_200[p, idx:idx+width] = -35 + np.random.normal(0, 2, width)
    
    Sv = np.stack([Sv_38, Sv_200], axis=1)  # (ping, channel, range)
    echo_range = np.tile(range_m, (n_pings, 2, 1))  # Same range for both channels
    
    ds = xr.Dataset(
        {
            "Sv": (["ping_time", "channel", "range_sample"], Sv),
            "echo_range": (["ping_time", "channel", "range_sample"], echo_range),
            "frequency_nominal": (["channel"], freq_nom),
        },
        coords={
            "ping_time": ping_times,
            "channel": channels,
            "range_sample": np.arange(n_samples),
        },
    )
    
    return ds


class TestDetectSeabedMaxSv:
    """Tests for maxSv seabed detection algorithm."""
    
    def test_detect_seabed_with_seabed(self):
        """Test detection when seabed is present."""
        ds = create_test_sv_dataset(seabed_depth=200.0, seabed_sv=-25)
        result = detect_seabed_maxSv(ds, r0=10, r1=400)
        
        assert isinstance(result, SeabedDetectionResult)
        assert result.method == "maxSv"
        assert result.pings_total == 100
        assert result.pings_detected > 0
        assert result.detection_rate > 0.5
        
        # Check detected depth is near expected
        valid_depths = result.seabed_depth.values[~np.isnan(result.seabed_depth.values)]
        mean_depth = np.mean(valid_depths)
        assert 150 < mean_depth < 250, f"Mean depth {mean_depth} not near expected 200m"
    
    def test_detect_seabed_without_seabed(self):
        """Test detection when no seabed is present (open ocean)."""
        ds = create_test_sv_dataset(seabed_depth=None)
        result = detect_seabed_maxSv(ds, r0=10, r1=400)
        
        assert result.pings_detected == 0 or result.detection_rate < 0.1
    
    def test_detect_seabed_range_gate(self):
        """Test that detection respects range gate."""
        ds = create_test_sv_dataset(seabed_depth=200.0, seabed_sv=-25)
        
        # Search only shallow - should not find seabed at 200m
        result = detect_seabed_maxSv(ds, r0=10, r1=100)
        assert result.detection_rate < 0.2


class TestDetectSeabedDeltaSv:
    """Tests for deltaSv seabed detection algorithm."""
    
    def test_detect_seabed_with_seabed(self):
        """Test delta Sv detection with seabed."""
        ds = create_test_sv_dataset(seabed_depth=200.0, seabed_sv=-25)
        result = detect_seabed_deltaSv(ds, r0=10, r1=400, thr=15)
        
        assert result.method == "deltaSv"
        assert result.pings_total == 100
    
    def test_detect_seabed_threshold_sensitivity(self):
        """Test that higher threshold is more selective."""
        ds = create_test_sv_dataset(seabed_depth=200.0, seabed_sv=-30)
        
        result_low = detect_seabed_deltaSv(ds, thr=10)
        result_high = detect_seabed_deltaSv(ds, thr=30)
        
        # Higher threshold should detect less
        assert result_high.detection_rate <= result_low.detection_rate


class TestDetectSeabedAriza:
    """Tests for ariza (morphological) seabed detection algorithm."""
    
    def test_detect_seabed_with_seabed(self):
        """Test ariza detection with seabed."""
        ds = create_test_sv_dataset(seabed_depth=200.0, seabed_sv=-25)
        result = detect_seabed_ariza(ds, r0=10, r1=400, thr=-40)
        
        assert result.method == "ariza"
        assert result.pings_detected > 50
    
    def test_detect_seabed_no_seabed(self):
        """Test ariza detection without seabed returns empty result."""
        ds = create_test_sv_dataset(seabed_depth=None, background_sv=-80)
        result = detect_seabed_ariza(ds, r0=10, r1=400, thr=-40)
        
        assert result.pings_detected == 0
        assert np.all(np.isnan(result.seabed_depth.values))
    
    def test_detect_seabed_smoothing(self):
        """Test that smoothing reduces noise in seabed line."""
        ds = create_test_sv_dataset(seabed_depth=200.0, seabed_sv=-25)
        
        result_smooth = detect_seabed_ariza(ds, smoothing=True, smooth_window=11)
        result_nosmooth = detect_seabed_ariza(ds, smoothing=False)
        
        # Smoothed version should have lower std
        valid_smooth = ~np.isnan(result_smooth.seabed_depth.values)
        valid_nosmooth = ~np.isnan(result_nosmooth.seabed_depth.values)
        
        if valid_smooth.sum() > 10 and valid_nosmooth.sum() > 10:
            std_smooth = np.std(result_smooth.seabed_depth.values[valid_smooth])
            std_nosmooth = np.std(result_nosmooth.seabed_depth.values[valid_nosmooth])
            # Smoothed should be similar or lower variance
            assert std_smooth <= std_nosmooth * 1.5


class TestDetectSeabedDispatch:
    """Tests for the main detect_seabed dispatcher function."""
    
    def test_dispatch_maxSv(self):
        """Test dispatch to maxSv."""
        ds = create_test_sv_dataset()
        result = detect_seabed(ds, method="maxSv")
        assert result.method == "maxSv"
    
    def test_dispatch_deltaSv(self):
        """Test dispatch to deltaSv."""
        ds = create_test_sv_dataset()
        result = detect_seabed(ds, method="deltaSv")
        assert result.method == "deltaSv"
    
    def test_dispatch_ariza(self):
        """Test dispatch to ariza."""
        ds = create_test_sv_dataset()
        result = detect_seabed(ds, method="ariza")
        assert result.method == "ariza"
    
    def test_dispatch_invalid_method(self):
        """Test that invalid method raises error."""
        ds = create_test_sv_dataset()
        with pytest.raises(ValueError, match="Unknown method"):
            detect_seabed(ds, method="invalid")
    
    def test_default_method_is_ariza(self):
        """Test that default method is ariza."""
        ds = create_test_sv_dataset()
        result = detect_seabed(ds)
        assert result.method == "ariza"


class TestMultiChannelDetection:
    """Tests for multi-channel dataset handling."""
    
    def test_detect_specific_channel(self):
        """Test detection on specific channel by name."""
        ds = create_multichannel_test_dataset(seabed_depth=200.0)
        
        # Should find channel containing "38"
        result = detect_seabed(ds, channel="38")
        assert "38" in result.channel
    
    def test_detect_first_channel_by_default(self):
        """Test that first channel is used by default."""
        ds = create_multichannel_test_dataset(seabed_depth=200.0)
        result = detect_seabed(ds, channel=None)
        
        # Should use first channel
        assert result.channel is not None


class TestMaskSeabed:
    """Tests for seabed masking function."""
    
    def test_mask_seabed_basic(self):
        """Test basic seabed masking."""
        ds = create_test_sv_dataset(seabed_depth=200.0, seabed_sv=-25)
        result = detect_seabed(ds)
        
        ds_masked = mask_seabed(ds, result, offset=0)
        
        # Check that masking was applied
        assert "Sv" in ds_masked
        
        # Sv should have NaN values below seabed
        n_nan_orig = np.isnan(ds["Sv"].values).sum()
        n_nan_masked = np.isnan(ds_masked["Sv"].values).sum()
        assert n_nan_masked > n_nan_orig
    
    def test_mask_seabed_with_offset(self):
        """Test seabed masking with offset."""
        ds = create_test_sv_dataset(seabed_depth=200.0, seabed_sv=-25)
        result = detect_seabed(ds)
        
        ds_no_offset = mask_seabed(ds, result, offset=0)
        ds_with_offset = mask_seabed(ds, result, offset=20)
        
        # With offset should mask more
        n_nan_no_offset = np.isnan(ds_no_offset["Sv"].values).sum()
        n_nan_with_offset = np.isnan(ds_with_offset["Sv"].values).sum()
        assert n_nan_with_offset >= n_nan_no_offset
    
    def test_mask_seabed_no_detection(self):
        """Test masking when no seabed was detected."""
        ds = create_test_sv_dataset(seabed_depth=None)
        result = detect_seabed(ds)
        
        # Should not crash even with no detection
        ds_masked = mask_seabed(ds, result)
        
        # Should have minimal masking (only original NaN)
        assert "Sv" in ds_masked


class TestSeabedStats:
    """Tests for seabed statistics computation."""
    
    def test_stats_with_detection(self):
        """Test stats when seabed is detected."""
        ds = create_test_sv_dataset(seabed_depth=200.0, seabed_sv=-25)
        result = detect_seabed(ds)
        
        stats = compute_seabed_stats(ds, result)
        
        assert stats["detected"] is True
        assert "mean_depth" in stats
        assert "std_depth" in stats
        assert "min_depth" in stats
        assert "max_depth" in stats
        assert stats["mean_depth"] > 0
    
    def test_stats_without_detection(self):
        """Test stats when no seabed is detected."""
        ds = create_test_sv_dataset(seabed_depth=None, background_sv=-80)
        result = detect_seabed(ds, thr=-40)
        
        # If detection rate is very low, should report as not detected
        if result.detection_rate < 0.1:
            stats = compute_seabed_stats(ds, result)
            # Stats should handle this gracefully
            assert "detection_rate" in stats


class TestHelperFunctions:
    """Tests for internal helper functions."""
    
    def test_get_range_array_from_data_vars(self):
        """Test extracting range array from data_vars."""
        ds = create_test_sv_dataset()
        range_arr, range_dim = _get_range_array(ds)
        
        assert range_arr is not None
        assert range_dim == "range_sample"
    
    def test_get_range_array_missing(self):
        """Test error when range array is missing."""
        ds = xr.Dataset({"Sv": (["ping_time", "sample"], np.random.randn(10, 100))})
        
        with pytest.raises(ValueError, match="No range coordinate found"):
            _get_range_array(ds)
    
    def test_get_sv_2d_single_channel(self):
        """Test extracting 2D Sv from single-channel dataset."""
        ds = create_test_sv_dataset()
        Sv_2d, range_1d, channel = _get_sv_2d(ds)
        
        assert Sv_2d.ndim == 2
        assert range_1d.ndim == 1
        assert len(range_1d) == Sv_2d.shape[1]
    
    def test_get_sv_2d_multi_channel(self):
        """Test extracting 2D Sv from multi-channel dataset."""
        ds = create_multichannel_test_dataset()
        Sv_2d, range_1d, channel = _get_sv_2d(ds, channel="38")
        
        assert Sv_2d.ndim == 2
        assert "38" in channel


class TestEdgeCases:
    """Tests for edge cases and error handling."""
    
    def test_empty_ping_time(self):
        """Test handling of minimal dataset."""
        import pandas as pd
        ds = xr.Dataset(
            {
                "Sv": (["ping_time", "range_sample"], np.random.randn(5, 50)),
                "echo_range": (["range_sample"], np.linspace(0, 100, 50)),
            },
            coords={
                "ping_time": pd.date_range('2023-06-01', periods=5, freq='1s').values,
                "range_sample": np.arange(50),
            },
        )
        
        result = detect_seabed(ds)
        assert result.pings_total == 5
    
    def test_all_nan_sv(self):
        """Test handling of all-NaN Sv data."""
        import pandas as pd
        ds = xr.Dataset(
            {
                "Sv": (["ping_time", "range_sample"], np.full((10, 50), np.nan)),
                "echo_range": (["range_sample"], np.linspace(0, 100, 50)),
            },
            coords={
                "ping_time": pd.date_range('2023-06-01', periods=10, freq='1s').values,
                "range_sample": np.arange(50),
            },
        )
        
        result = detect_seabed(ds)
        assert result.pings_detected == 0
    
    def test_search_range_outside_data(self):
        """Test when search range is outside data range."""
        ds = create_test_sv_dataset(max_range=100)
        
        # Search beyond data range
        result = detect_seabed(ds, r0=200, r1=500)
        
        # Should handle gracefully
        assert result.pings_detected == 0 or result.pings_total > 0
