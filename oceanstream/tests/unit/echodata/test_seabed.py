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


def create_fjord_dataset(
    n_pings: int = 300,
    seabed_m: float = 73.0,
    dropout: slice = slice(0, 0),
) -> xr.Dataset:
    """A shallow site: a seabed echo with a sharp leading edge at *seabed_m* and
    a tail whose length changes from ping to ping, as a buoy's does.

    Pings in *dropout* have no seabed echo at all.
    """
    import pandas as pd

    rng_m = np.arange(0, 100, 0.1)
    gen = np.random.default_rng(0)
    sv = gen.normal(-75, 2, (n_pings, rng_m.size))
    top = int(np.searchsorted(rng_m, seabed_m))
    for p in range(n_pings):
        if dropout.start <= p < dropout.stop:
            continue
        tail = int(gen.uniform(30, 90))  # 3–9 m above −40 dB
        sv[p, top : top + tail] = np.linspace(-15, -38, tail)
        sv[p, top + tail :] = -55
    return xr.Dataset(
        {
            "Sv": (["ping_time", "range_sample"], sv),
            "echo_range": (["ping_time", "range_sample"], np.tile(rng_m, (n_pings, 1))),
        },
        coords={
            "ping_time": pd.date_range("2026-04-13", periods=n_pings, freq="1s").values,
            "range_sample": np.arange(rng_m.size),
        },
    )


class TestDetectSeabedComposite:
    """The composite detector's two picks: deepest candidate and leading edge."""

    def test_edge_is_the_seabed_surface(self):
        ds = create_fjord_dataset()
        line = detect_seabed(ds, method="composite", pick="edge").seabed_depth.values
        assert np.all(np.abs(line - 73.0) <= 0.2)

    def test_deepest_sits_in_the_tail(self):
        ds = create_fjord_dataset()
        line = detect_seabed(ds, method="composite").seabed_depth.values
        # The legacy pick follows the tail, metres below the surface.
        assert np.median(line) > 76.0

    def test_edge_holds_through_a_short_dropout(self):
        ds = create_fjord_dataset(dropout=slice(100, 110))
        result = detect_seabed(ds, method="composite", pick="edge")
        assert np.all(np.abs(result.seabed_depth.values - 73.0) <= 0.2)
        assert result.pings_detected < 300

    def test_edge_ignores_weak_scatter_on_the_bottom(self):
        ds = create_fjord_dataset()
        # A fish layer resting on the seabed, weaker than the −40 dB floor.
        ds["Sv"][:, 690:730] = -48.0
        line = detect_seabed(ds, method="composite", pick="edge").seabed_depth.values
        assert np.all(np.abs(line - 73.0) <= 0.2)

    def test_edge_mask_starts_at_the_offset(self):
        ds = create_fjord_dataset()
        result = detect_seabed(ds, method="composite", pick="edge")
        masked = mask_seabed(ds, result, offset=0.5)["Sv"].values
        rng_m = ds["echo_range"].values[0]
        assert np.isnan(masked[:, rng_m >= 72.7]).all()
        assert np.isfinite(masked[:, rng_m < 72.3]).all()

    def test_no_seabed_masks_nothing(self):
        ds = create_fjord_dataset(dropout=slice(0, 300))
        result = detect_seabed(ds, method="composite", pick="edge")
        assert result.pings_detected == 0
        assert np.isnan(result.seabed_depth.values).all()
        assert np.isfinite(mask_seabed(ds, result)["Sv"].values).all()

    def test_ring_down_below_the_transducer_is_not_a_seabed(self):
        # Deep water: no seabed in range, but the transmit pulse's ring-down stays above
        # the -40 dB floor down to 12 m, past the gate's 10 m start (SE2204, FKt260415).
        ds = create_fjord_dataset(dropout=slice(0, 300))
        rng_m = ds["echo_range"].values[0]
        ds["Sv"][:, rng_m < 12.0] = 5.0
        result = detect_seabed(ds, method="composite", pick="edge", r0=10)
        assert result.pings_detected == 0

    def test_seabed_under_a_ring_down_is_still_found(self):
        ds = create_fjord_dataset()
        rng_m = ds["echo_range"].values[0]
        ds["Sv"][:, rng_m < 12.0] = 5.0
        line = detect_seabed(ds, method="composite", pick="edge", r0=10).seabed_depth.values
        assert np.all(np.abs(line - 73.0) <= 0.2)

    def test_unknown_pick(self):
        with pytest.raises(ValueError, match="Unknown pick"):
            detect_seabed(create_fjord_dataset(n_pings=10), method="composite", pick="middle")


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


class TestInterleavedSeabed:
    """A seabed line for channels that transmit in turn reaches every ping."""

    @staticmethod
    def interleaved(seabed_m: float = 73.0, n_pings: int = 300) -> xr.Dataset:
        """38 and 120 kHz pinging in turn on one grid, each empty at the other's pings."""
        fjord = create_fjord_dataset(n_pings=n_pings, seabed_m=seabed_m)
        sv = np.stack([fjord["Sv"].values, fjord["Sv"].values])
        sv[0, 1::2] = np.nan  # 38 kHz pings on even pings
        sv[1, 0::2] = np.nan  # 120 kHz on odd ones
        rng = np.where(np.isfinite(sv), np.broadcast_to(fjord["echo_range"].values, sv.shape), np.nan)
        return xr.Dataset(
            {
                "Sv": (["channel", "ping_time", "range_sample"], sv),
                "echo_range": (["channel", "ping_time", "range_sample"], rng),
                "frequency_nominal": (["channel"], [38000.0, 120000.0]),
            },
            coords={
                "channel": ["WBT 1-ES38", "WBT 2-ES120"],
                "ping_time": fjord["ping_time"].values,
                "range_sample": fjord["range_sample"].values,
            },
        )

    def test_line_covers_the_other_channels_pings(self):
        from oceanstream.echodata.seabed import detect_seabed_interleaved

        result = detect_seabed_interleaved(self.interleaved(), pick="edge")
        assert result.channel == "WBT 1-ES38"
        assert result.pings_detected == 300
        assert np.all(np.abs(result.seabed_depth.values - 73.0) <= 0.2)

    def test_mask_reaches_the_120_khz_pings(self):
        from oceanstream.echodata.seabed import detect_seabed_interleaved

        ds = self.interleaved()
        masked = mask_seabed(ds, detect_seabed_interleaved(ds, pick="edge"), offset=0.5)
        rng_m = ds["echo_range"].values[0, 0]
        ch120 = masked["Sv"].values[1, 1::2]
        assert np.isnan(ch120[:, rng_m >= 72.7]).all()
        assert np.isfinite(ch120[:, rng_m < 72.3]).all()

    def test_no_line_far_from_a_detection(self):
        from oceanstream.echodata.seabed import detect_seabed_interleaved

        ds = self.interleaved()
        # Ten minutes of 120 kHz pings only, with no 38 kHz ping to take a line from.
        late = ds.isel(ping_time=slice(0, 20)).assign_coords(
            ping_time=ds["ping_time"].values[:20] + np.timedelta64(900, "s")
        )
        late["Sv"][0] = np.nan
        late["echo_range"][0] = np.nan
        result = detect_seabed_interleaved(
            xr.concat([ds, late], "ping_time", data_vars="minimal"), pick="edge"
        )
        assert np.isnan(result.seabed_depth.values[-20:]).all()
        assert np.isfinite(result.seabed_depth.values[:300]).all()

    def test_spikes_in_deep_water_make_no_line(self):
        from oceanstream.echodata.seabed import detect_seabed_interleaved

        ds = self.interleaved()
        gen = np.random.default_rng(1)
        sv = gen.normal(-75, 2, ds["Sv"].shape)
        # Water deeper than the range, with a few interference spikes above the
        # composite detector's -40 dB floor but far below any seabed echo.
        for p in gen.choice(ds.sizes["ping_time"], 40, replace=False):
            start = int(gen.integers(300, 950))
            sv[:, p, start : start + 30] = -38.0
        ds["Sv"] = ds["Sv"].copy(data=np.where(np.isfinite(ds["Sv"].values), sv, np.nan))
        result = detect_seabed_interleaved(ds, pick="edge")
        assert result.pings_detected == 0
        assert np.isfinite(mask_seabed(ds, result)["Sv"].values).sum() == np.isfinite(ds["Sv"].values).sum()

    def test_the_transmit_pulse_does_not_back_a_line_below_the_gate_top(self):
        from oceanstream.echodata.seabed import detect_seabed_interleaved

        # Deep water with the transmit pulse in the first two metres and a few spikes
        # just below the gate's 10 m top (SE2204 2022-06-30, FKt260415 2026-04-20).
        ds = self.interleaved()
        gen = np.random.default_rng(2)
        sv = gen.normal(-80, 2, ds["Sv"].shape)
        rng_m = ds["echo_range"].values[0, 0]
        sv[:, :, rng_m < 2.0] = 5.0
        for p in gen.choice(ds.sizes["ping_time"], 60, replace=False):
            sv[:, p, (rng_m >= 11.0) & (rng_m < 12.0)] = -38.0
        ds["Sv"] = ds["Sv"].copy(data=np.where(np.isfinite(ds["Sv"].values), sv, np.nan))
        assert detect_seabed_interleaved(ds, pick="edge").pings_detected == 0

    def test_a_weak_stretch_on_station_is_bridged(self):
        from oceanstream.echodata.seabed import detect_seabed_interleaved

        # Two minutes without a seabed echo in the middle of a station.
        fjord = create_fjord_dataset(dropout=slice(100, 220))
        ds = self.interleaved()
        sv = np.stack([fjord["Sv"].values, fjord["Sv"].values])
        sv[0, 1::2] = np.nan
        sv[1, 0::2] = np.nan
        ds["Sv"] = ds["Sv"].copy(data=sv)
        result = detect_seabed_interleaved(ds, pick="edge")
        assert result.pings_detected == 300
        assert np.all(np.abs(result.seabed_depth.values - 73.0) <= 0.2)

    def test_the_line_follows_a_seabed_climbing_a_shelf_edge(self):
        from oceanstream.echodata.seabed import detect_seabed_interleaved

        # HB2403 2024-06-07: the seabed climbs 470 to 250 m in five minutes over rough
        # ground, so its echo starts metres above or below the smoothed line from one
        # ping to the next. No ping may keep seabed echo above its line.
        import pandas as pd

        n_pings, range_m = 300, np.arange(0, 500, 0.2)
        gen = np.random.default_rng(4)
        start = np.clip(np.linspace(470, 250, n_pings) + gen.uniform(-15, 15, n_pings), 0, 480)
        sv = gen.normal(-80, 2, (n_pings, range_m.size))
        for p, d in enumerate(start):
            i = int(np.searchsorted(range_m, d))
            sv[p, i : i + 40] = np.linspace(-15, -38, 40)
            sv[p, i + 40 :] = -55
        ds = TestSimultaneousSeabed.together(sv, range_m)
        depth = detect_seabed_interleaved(ds, pick="edge").seabed_depth.values
        assert np.isfinite(depth).all()
        inner = slice(5, -5)  # the last pings of a day take the line before them
        assert np.all(depth[inner] <= start[inner] + 0.2)
        assert np.median(start - depth) < 0.5  # it follows the edge, not the slope's top

    def test_a_second_seabed_echo_above_the_line_is_masked(self):
        from oceanstream.echodata.seabed import detect_seabed_interleaved

        # HB2403 2024-06-05 00:03: over rough ground a -11 dB echo starts at 15 m, a dip
        # apart from the strongest echo at 21 m.
        fjord = create_fjord_dataset(n_pings=300, seabed_m=21.0)
        sv = fjord["Sv"].values.copy()
        rng_m = fjord["echo_range"].values[0]
        sv[:, (rng_m >= 15.0) & (rng_m < 16.5)] = -11.0
        sv[:, (rng_m >= 21.0) & (rng_m < 22.0)] = -5.0
        ds = TestSimultaneousSeabed.together(sv, rng_m)
        depth = detect_seabed_interleaved(ds, pick="edge").seabed_depth.values
        assert np.all(np.abs(depth - 15.0) <= 0.2)

    def test_a_seabed_coming_back_into_range_gets_a_line(self):
        from oceanstream.echodata.seabed.detection import _echo_top

        # HB2403 2024-06-07 11:27: pings next to a line, the seabed echo at 430 m in them.
        range_m = np.arange(0, 500, 0.2)
        sv = np.full((6, range_m.size), -80.0)
        sv[:, (range_m >= 430.0) & (range_m < 436.0)] = -15.0
        sv[5] = -80.0  # no seabed echo in the last ping
        line = np.array([np.nan, np.nan, 432.0, 432.0, np.nan, np.nan])
        times = np.array([0, 1, 2, 3, 4, 400], dtype=np.int64) * 10**9
        out = _echo_top(sv, range_m, times, line)
        np.testing.assert_allclose(out[:5], 430.0, atol=0.2)
        assert np.isnan(out[5])  # too far from a line, and no echo

    def test_a_carried_line_moves_up_to_the_echo_under_it(self):
        from oceanstream.echodata.seabed.detection import _echo_top

        # HB2403 2024-06-07 11:28: a line carried at 470 m, the ping's own seabed echo
        # (-26 dB, too weak for the -25 dB rule) starting at 430 m.
        range_m = np.arange(0, 500, 0.2)
        sv = np.full((3, range_m.size), -80.0)
        sv[:, (range_m >= 430.0) & (range_m < 440.0)] = -26.0
        line = np.full(3, 470.0)
        out = _echo_top(sv, range_m, np.arange(3, dtype=np.int64) * 10**9, line)
        np.testing.assert_allclose(out, 430.0, atol=0.2)

    def test_a_line_on_the_second_echo_moves_up_to_the_seabed(self):
        from oceanstream.echodata.seabed.detection import _echo_top

        # HB2401 2024-02-17 00:24: the seabed at 7.9 m of range, the line on its second
        # echo at 15.6 m.
        range_m = np.arange(0, 40, 0.18)
        sv = np.full((3, range_m.size), -90.0)
        sv[:, (range_m >= 7.9) & (range_m < 9.3)] = -12.0
        sv[:, (range_m >= 15.6) & (range_m < 17.0)] = -30.0
        out = _echo_top(sv, range_m, np.arange(3, dtype=np.int64) * 10**9, np.full(3, 15.6),
                        top_m=3.0)
        np.testing.assert_allclose(out, 7.9, atol=0.2)

    def test_a_line_on_the_third_echo_moves_up_to_the_seabed(self):
        from oceanstream.echodata.seabed.detection import _echo_top

        # HB2202 2022-03-11, Chesapeake Bay: the seabed at 7.6 m, the line at 22.9 m.
        range_m = np.arange(0, 40, 0.18)
        sv = np.full((3, range_m.size), -90.0)
        sv[:, (range_m >= 7.6) & (range_m < 9.0)] = -10.0
        sv[:, (range_m >= 15.2) & (range_m < 16.6)] = -28.0
        sv[:, (range_m >= 22.9) & (range_m < 24.3)] = -36.0
        out = _echo_top(sv, range_m, np.arange(3, dtype=np.int64) * 10**9, np.full(3, 22.9),
                        top_m=3.0)
        np.testing.assert_allclose(out, 7.6, atol=0.2)

    def test_a_gap_in_the_seabed_echo_does_not_hide_it(self):
        from oceanstream.echodata.seabed.detection import _echo_top

        # HB2202 2022-03-13: impulse removal blanked cells inside the seabed echo at 11 m.
        range_m = np.arange(0, 40, 0.18)
        sv = np.full((3, range_m.size), -90.0)
        sv[:, (range_m >= 10.9) & (range_m < 12.0)] = -11.0
        sv[:, (range_m >= 11.1) & (range_m < 11.7)] = np.nan
        sv[:, (range_m >= 21.9) & (range_m < 22.6)] = -28.0
        out = _echo_top(sv, range_m, np.arange(3, dtype=np.int64) * 10**9, np.full(3, 21.7),
                        top_m=3.0)
        np.testing.assert_allclose(out, 10.9, atol=0.2)

    def test_a_school_at_half_the_depth_is_not_a_repeat(self):
        from oceanstream.echodata.seabed.detection import _echo_top

        range_m = np.arange(0, 120, 0.18)
        sv = np.full((3, range_m.size), -90.0)
        sv[:, (range_m >= 98.0) & (range_m < 100.0)] = -10.0
        sv[:, (range_m >= 48.0) & (range_m < 52.0)] = -32.0  # a school, weaker than -25 dB
        out = _echo_top(sv, range_m, np.arange(3, dtype=np.int64) * 10**9, np.full(3, 98.0),
                        top_m=3.0)
        np.testing.assert_allclose(out, 98.0, atol=0.2)

    def test_a_layer_on_a_flat_seabed_is_not_taken_for_seabed(self):
        from oceanstream.echodata.seabed import detect_seabed_interleaved

        # Pollock on the Bering shelf: a -45 dB layer on the bottom, 8 m thick.
        fjord = create_fjord_dataset(n_pings=300, seabed_m=73.0)
        sv = fjord["Sv"].values.copy()
        rng_m = fjord["echo_range"].values[0]
        sv[:, (rng_m >= 65.0) & (rng_m < 73.0)] = -45.0
        ds = TestSimultaneousSeabed.together(sv, rng_m)
        depth = detect_seabed_interleaved(ds, pick="edge").seabed_depth.values
        assert np.all(np.abs(depth - 73.0) <= 0.2)

    def test_a_dense_school_above_a_flat_seabed_stays_water(self):
        from oceanstream.echodata.seabed import detect_seabed_interleaved

        fjord = create_fjord_dataset(n_pings=300, seabed_m=73.0)
        sv = fjord["Sv"].values.copy()
        rng_m = fjord["echo_range"].values[0]
        sv[100:160, (rng_m >= 55.0) & (rng_m < 70.0)] = -30.0
        ds = TestSimultaneousSeabed.together(sv, rng_m)
        depth = detect_seabed_interleaved(ds, pick="edge").seabed_depth.values
        assert np.all(np.abs(depth - 73.0) <= 0.2)


class TestSimultaneousSeabed:
    """Channels that ping together get the same guarded line (IESSNS 2021)."""

    @staticmethod
    def together(sv_one: np.ndarray, range_m: np.ndarray) -> xr.Dataset:
        import pandas as pd

        sv = np.stack([sv_one, sv_one])
        n_pings = sv_one.shape[0]
        return xr.Dataset(
            {
                "Sv": (["channel", "ping_time", "range_sample"], sv),
                "echo_range": (["channel", "ping_time", "range_sample"],
                               np.broadcast_to(range_m, sv.shape).copy()),
                "frequency_nominal": (["channel"], [38000.0, 120000.0]),
            },
            coords={
                "channel": ["WBT 1-ES38", "WBT 2-ES120"],
                "ping_time": pd.date_range("2021-07-12", periods=n_pings, freq="1s").values,
                "range_sample": np.arange(range_m.size),
            },
        )

    def test_shelf_seabed_on_every_ping(self):
        from oceanstream.echodata.seabed import detect_seabed_interleaved

        fjord = create_fjord_dataset(n_pings=300, seabed_m=73.0)
        ds = self.together(fjord["Sv"].values, fjord["echo_range"].values[0])
        result = detect_seabed_interleaved(ds, pick="edge")
        assert result.pings_detected == 300
        assert np.all(np.abs(result.seabed_depth.values - 73.0) <= 0.2)

    def test_a_school_in_deep_water_does_not_become_hours_of_seabed(self):
        from oceanstream.echodata.seabed import detect_seabed_interleaved

        gen = np.random.default_rng(3)
        range_m = np.linspace(0, 500, 1000)
        sv = gen.normal(-80, 2, (1200, range_m.size))
        sv[100:116, (range_m > 150) & (range_m < 160)] = -32.0  # a dense school, 16 s
        ds = self.together(sv, range_m)
        result = detect_seabed_interleaved(ds, pick="edge")
        depth = result.seabed_depth.values
        # Whatever stands near the school, nothing is drawn minutes away from it.
        assert np.isnan(depth[300:]).all()


class TestSeabedRegion:
    """The seabed flag covers cells that an earlier step had already removed."""

    def test_region_includes_cells_already_removed(self):
        from oceanstream.echodata.seabed import detect_seabed_interleaved, seabed_region

        fjord = create_fjord_dataset(n_pings=300, seabed_m=73.0)
        ds = TestSimultaneousSeabed.together(fjord["Sv"].values, fjord["echo_range"].values[0])
        rng = ds["echo_range"].values[0, 0]
        result = detect_seabed_interleaved(ds, pick="edge")
        ds["Sv"][:, :, rng > 90] = np.nan  # removed before seabed masking, e.g. below the noise
        region = seabed_region(ds, result, offset=0.5).values
        assert region[:, :, rng >= 72.7].all()  # below the line, removed earlier or not
        assert not region[:, :, rng < 72.3].any()

