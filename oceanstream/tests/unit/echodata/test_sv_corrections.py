"""Tests for Sv dataset corrections and depth conversion.

Tests for:
- correct_echo_range() - converts echo_range to depth with offset
- apply_corrections_ds() - removes empty pings, applies corrections
- swap_range_to_depth() - swaps range_sample dim to depth
"""

import numpy as np
import pytest
import xarray as xr


class TestSwapRangeToDepth:
    """Tests for swap_range_to_depth function."""

    def test_swaps_dimension(self):
        """Test that range_sample dimension is swapped to depth."""
        from oceanstream.echodata.compute.sv import swap_range_to_depth
        
        n_channel, n_ping, n_range = 2, 10, 50
        depth_vals = np.linspace(0, 100, n_range)
        
        ds = xr.Dataset(
            {
                "Sv": (["channel", "ping_time", "range_sample"],
                       np.random.randn(n_channel, n_ping, n_range)),
                "depth": (["channel", "ping_time", "range_sample"],
                          np.broadcast_to(depth_vals, (n_channel, n_ping, n_range))),
            },
            coords={
                "channel": ["ES38", "ES120"],
                "ping_time": np.arange(n_ping),
                "range_sample": np.arange(n_range),
            },
        )
        
        result = swap_range_to_depth(ds)
        
        assert "depth" in result.dims
        assert "range_sample" not in result.dims
        assert result.Sv.dims == ("channel", "ping_time", "depth")

    def test_no_depth_variable(self):
        """Test that missing depth variable is handled gracefully."""
        from oceanstream.echodata.compute.sv import swap_range_to_depth
        
        ds = xr.Dataset(
            {
                "Sv": (["channel", "ping_time", "range_sample"], np.zeros((2, 10, 50))),
            },
            coords={
                "channel": ["ES38", "ES120"],
                "ping_time": np.arange(10),
                "range_sample": np.arange(50),
            },
        )
        
        result = swap_range_to_depth(ds)
        
        # Should return unchanged
        assert "range_sample" in result.dims
        assert "depth" not in result.dims

    def test_already_has_depth_dim(self):
        """Test that dataset with depth dim is returned unchanged."""
        from oceanstream.echodata.compute.sv import swap_range_to_depth
        
        ds = xr.Dataset(
            {
                "Sv": (["channel", "ping_time", "depth"], np.zeros((2, 10, 50))),
            },
            coords={
                "channel": ["ES38", "ES120"],
                "ping_time": np.arange(10),
                "depth": np.linspace(0, 100, 50),
            },
        )
        
        result = swap_range_to_depth(ds)
        
        # Should return unchanged (no range_sample to swap)
        assert "depth" in result.dims


class TestCorrectEchoRange:
    """Tests for correct_echo_range function."""

    def test_applies_depth_offset(self):
        """Test that depth offset is correctly applied."""
        from oceanstream.echodata.compute.sv import correct_echo_range
        
        n_channel, n_ping, n_range = 2, 10, 50
        echo_range_vals = np.linspace(0, 100, n_range)
        
        ds = xr.Dataset(
            {
                "Sv": (["channel", "ping_time", "range_sample"],
                       np.random.randn(n_channel, n_ping, n_range)),
                "echo_range": (["channel", "ping_time", "range_sample"],
                               np.broadcast_to(echo_range_vals, (n_channel, n_ping, n_range))),
            },
            coords={
                "channel": ["ES38", "ES120"],
                "ping_time": np.arange(n_ping),
                "range_sample": np.arange(n_range),
            },
        )
        
        depth_offset = 5.0
        result = correct_echo_range(ds, depth_offset=depth_offset)
        
        # Dimension should be renamed to depth
        assert "depth" in result.dims
        assert "range_sample" not in result.dims
        
        # Depth values should include offset
        min_depth = float(result.depth.min())
        max_depth = float(result.depth.max())
        assert min_depth >= depth_offset - 0.1  # echo_range starts at 0
        assert max_depth <= 100 + depth_offset + 0.1

    def test_preserves_original_range_sample(self):
        """Test that original range_sample values are preserved."""
        from oceanstream.echodata.compute.sv import correct_echo_range
        
        n_range = 50
        ds = xr.Dataset(
            {
                "Sv": (["channel", "ping_time", "range_sample"],
                       np.zeros((2, 10, n_range))),
                "echo_range": (["channel", "ping_time", "range_sample"],
                               np.broadcast_to(np.arange(n_range) * 2.0, (2, 10, n_range))),
            },
            coords={
                "channel": ["ES38", "ES120"],
                "ping_time": np.arange(10),
                "range_sample": np.arange(n_range),
            },
        )
        
        result = correct_echo_range(ds, depth_offset=0.0)
        
        # Should have range_sample preserved
        assert "range_sample" in result.data_vars or "range_sample" in result.coords

    def test_no_echo_range_returns_unchanged(self):
        """Test that missing echo_range is handled gracefully."""
        from oceanstream.echodata.compute.sv import correct_echo_range
        
        ds = xr.Dataset(
            {
                "Sv": (["channel", "ping_time", "range_sample"], np.zeros((2, 10, 50))),
            },
            coords={
                "channel": ["ES38", "ES120"],
                "ping_time": np.arange(10),
                "range_sample": np.arange(50),
            },
        )
        
        result = correct_echo_range(ds, depth_offset=5.0)
        
        # Should return unchanged
        assert "range_sample" in result.dims

    def test_zero_depth_offset(self):
        """Test with zero depth offset."""
        from oceanstream.echodata.compute.sv import correct_echo_range
        
        n_range = 30
        echo_range = np.linspace(5, 100, n_range)
        
        ds = xr.Dataset(
            {
                "Sv": (["channel", "ping_time", "range_sample"],
                       np.zeros((1, 5, n_range))),
                "echo_range": (["channel", "ping_time", "range_sample"],
                               np.broadcast_to(echo_range, (1, 5, n_range))),
            },
            coords={
                "channel": ["ES38"],
                "ping_time": np.arange(5),
                "range_sample": np.arange(n_range),
            },
        )
        
        result = correct_echo_range(ds, depth_offset=0.0)
        
        assert "depth" in result.dims
        assert float(result.depth.min()) >= 5.0 - 0.1
        assert float(result.depth.max()) <= 100.0 + 0.1


class TestApplyCorrectionsDs:
    """Tests for apply_corrections_ds function."""

    def test_drops_empty_pings(self):
        """Test that all-NaN pings are removed."""
        from oceanstream.echodata.compute.sv import apply_corrections_ds
        
        n_ping = 10
        sv_data = np.random.randn(2, n_ping, 50)
        # Make some pings all NaN
        sv_data[:, 3, :] = np.nan
        sv_data[:, 7, :] = np.nan
        
        ds = xr.Dataset(
            {
                "Sv": (["channel", "ping_time", "range_sample"], sv_data),
            },
            coords={
                "channel": ["ES38", "ES120"],
                "ping_time": np.arange(n_ping),
                "range_sample": np.arange(50),
            },
        )
        
        result = apply_corrections_ds(ds)
        
        # Should have 2 fewer pings
        assert result.dims["ping_time"] == n_ping - 2

    def test_keeps_partial_nan_pings(self):
        """Test that partially NaN pings are kept."""
        from oceanstream.echodata.compute.sv import apply_corrections_ds
        
        n_ping = 5
        sv_data = np.random.randn(2, n_ping, 50)
        # Make only some samples NaN (not all)
        sv_data[:, 2, :25] = np.nan
        
        ds = xr.Dataset(
            {
                "Sv": (["channel", "ping_time", "range_sample"], sv_data),
            },
            coords={
                "channel": ["ES38", "ES120"],
                "ping_time": np.arange(n_ping),
                "range_sample": np.arange(50),
            },
        )
        
        result = apply_corrections_ds(ds)
        
        # All pings should remain (none are all-NaN)
        assert result.dims["ping_time"] == n_ping

    def test_applies_depth_offset(self):
        """Test that depth correction is applied when offset provided."""
        from oceanstream.echodata.compute.sv import apply_corrections_ds
        
        echo_range = np.linspace(0, 100, 50)
        
        ds = xr.Dataset(
            {
                "Sv": (["channel", "ping_time", "range_sample"],
                       np.random.randn(2, 10, 50)),
                "echo_range": (["channel", "ping_time", "range_sample"],
                               np.broadcast_to(echo_range, (2, 10, 50))),
            },
            coords={
                "channel": ["ES38", "ES120"],
                "ping_time": np.arange(10),
                "range_sample": np.arange(50),
            },
        )
        
        result = apply_corrections_ds(ds, depth_offset=7.5)
        
        # Should have depth dimension after correction
        assert "depth" in result.dims

    def test_no_correction_without_offset(self):
        """Test that no depth correction without offset."""
        from oceanstream.echodata.compute.sv import apply_corrections_ds
        
        ds = xr.Dataset(
            {
                "Sv": (["channel", "ping_time", "range_sample"],
                       np.random.randn(2, 10, 50)),
                "echo_range": (["channel", "ping_time", "range_sample"],
                               np.broadcast_to(np.arange(50), (2, 10, 50))),
            },
            coords={
                "channel": ["ES38", "ES120"],
                "ping_time": np.arange(10),
                "range_sample": np.arange(50),
            },
        )
        
        result = apply_corrections_ds(ds)  # No depth_offset
        
        # Should still have range_sample
        assert "range_sample" in result.dims


class TestDetectPulseMode:
    """Tests for detect_pulse_mode function."""

    def test_function_is_importable(self):
        """Test that detect_pulse_mode is importable."""
        from oceanstream.echodata import detect_pulse_mode
        assert callable(detect_pulse_mode)

    def test_function_exists_in_calibrate(self):
        """Test that detect_pulse_mode exists in calibrate module."""
        from oceanstream.echodata.calibrate import detect_pulse_mode
        assert callable(detect_pulse_mode)


class TestCalibrateIntegration:
    """Integration tests for calibration functions."""

    def test_load_saildrone_calibration_exists(self):
        """Test that load_saildrone_calibration is available."""
        from oceanstream.echodata import load_saildrone_calibration
        assert callable(load_saildrone_calibration)

    def test_calibrate_saildrone_exists(self):
        """Test that calibrate_saildrone is available."""
        from oceanstream.echodata import calibrate_saildrone
        assert callable(calibrate_saildrone)
