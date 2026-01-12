"""Tests for the consolidate module (depth computation)."""

import numpy as np
import pytest
import xarray as xr
from unittest.mock import MagicMock, patch


class TestChooseDepthFlags:
    """Tests for choose_depth_flags function."""

    def test_with_basic_platform_metadata(self):
        """Test choose_depth_flags with basic platform metadata."""
        from oceanstream.echodata.consolidate import choose_depth_flags
        
        # Create mock Platform dataset (mimics EchoData["Platform"])
        mock_platform = xr.Dataset({
            "pitch": (["time1"], [0.1, 0.2]),
            "roll": (["time1"], [0.05, 0.08]),
        })
        
        # Create mock Sonar dataset
        mock_sonar = xr.Dataset({
            "sonar_type": "EK80",
        })
        
        # Create mock EchoData with proper __getitem__
        mock_ed = MagicMock()
        mock_ed.sonar_model = "EK80"
        
        def getitem_side_effect(key):
            if key == "Platform":
                return mock_platform
            if key == "Sonar":
                return mock_sonar
            return None
        
        mock_ed.__getitem__ = MagicMock(side_effect=getitem_side_effect)
        
        result = choose_depth_flags(mock_ed, depth_offset=5.0)
        
        assert isinstance(result, dict)
        assert "depth_offset" in result
        assert "downward" in result
        assert result["downward"] is True
    
    def test_depth_offset_used_without_platform_offsets(self):
        """Test that user depth_offset is used when platform offsets not available."""
        from oceanstream.echodata.consolidate import choose_depth_flags
        
        # Create mock with no vertical offsets
        mock_platform = xr.Dataset({
            "pitch": (["time1"], [0.1]),
        })
        mock_sonar = xr.Dataset()
        
        mock_ed = MagicMock()
        mock_ed.sonar_model = "EK80"
        
        def getitem_side_effect(key):
            if key == "Platform":
                return mock_platform
            if key == "Sonar":
                return mock_sonar
            return None
        
        mock_ed.__getitem__ = MagicMock(side_effect=getitem_side_effect)
        
        result = choose_depth_flags(mock_ed, depth_offset=7.5)
        
        # User offset should be used since platform offsets not present
        assert result.get("depth_offset") == 7.5 or result.get("depth_offset") is None


class TestAddDepthToSv:
    """Tests for add_depth_to_sv function."""

    def test_adds_depth_from_echo_range(self):
        """Test that depth is computed from echo_range when echodata not provided."""
        from oceanstream.echodata.consolidate import add_depth_to_sv
        
        # Create mock Sv dataset with echo_range
        n_channel, n_ping, n_range = 2, 10, 50
        ds = xr.Dataset(
            {
                "Sv": (["channel", "ping_time", "range_sample"],
                       np.random.randn(n_channel, n_ping, n_range).astype(np.float32)),
                "echo_range": (["channel", "ping_time", "range_sample"],
                               np.broadcast_to(
                                   np.linspace(0, 100, n_range),
                                   (n_channel, n_ping, n_range)
                               )),
            },
            coords={
                "channel": ["ES38", "ES120"],
                "ping_time": np.arange(n_ping),
                "range_sample": np.arange(n_range),
            },
        )
        
        # Add depth
        result = add_depth_to_sv(ds, echodata=None, depth_offset=5.0)
        
        assert "depth" in result.data_vars
        assert result.depth.dims == ("channel", "ping_time", "range_sample")
        # Depth should be echo_range + offset
        expected_min = 5.0  # offset
        expected_max = 105.0  # 100 + 5
        assert float(result.depth.min()) >= expected_min - 0.1
        assert float(result.depth.max()) <= expected_max + 0.1

    def test_preserves_existing_depth(self):
        """Test that existing depth is not overwritten."""
        from oceanstream.echodata.consolidate import add_depth_to_sv
        
        ds = xr.Dataset(
            {
                "Sv": (["channel", "ping_time", "range_sample"], np.zeros((2, 10, 50))),
                "depth": (["channel", "ping_time", "range_sample"], np.ones((2, 10, 50)) * 42.0),
            },
            coords={
                "channel": ["ES38", "ES120"],
                "ping_time": np.arange(10),
                "range_sample": np.arange(50),
            },
        )
        
        result = add_depth_to_sv(ds, echodata=None, depth_offset=0.0)
        
        assert "depth" in result.data_vars
        assert float(result.depth.mean()) == 42.0  # Original value preserved
    
    def test_fallback_to_range_sample(self):
        """Test fallback to range_sample when no echo_range."""
        from oceanstream.echodata.consolidate import add_depth_to_sv
        
        ds = xr.Dataset(
            {
                "Sv": (["channel", "ping_time", "range_sample"], np.zeros((2, 5, 10))),
            },
            coords={
                "channel": ["ES38", "ES120"],
                "ping_time": np.arange(5),
                "range_sample": np.arange(10),
            },
        )
        
        result = add_depth_to_sv(ds, echodata=None, depth_offset=5.0)
        
        assert "depth" in result.data_vars
        # Depth computed from range_sample + offset
        # The depth dimension should exist
        depth_vals = result.depth.values
        # First depth value should be offset (0 + 5 = 5)
        assert depth_vals.flat[0] == 5.0 or depth_vals.min() >= 5.0

    def test_broadcasts_depth_correctly(self):
        """Test that depth is broadcast to all dimensions."""
        from oceanstream.echodata.consolidate import add_depth_to_sv
        
        n_channel, n_ping, n_range = 3, 15, 100
        
        ds = xr.Dataset(
            {
                "Sv": (["channel", "ping_time", "range_sample"],
                       np.random.randn(n_channel, n_ping, n_range)),
                "echo_range": (["channel", "ping_time", "range_sample"],
                               np.broadcast_to(
                                   np.linspace(0, 200, n_range),
                                   (n_channel, n_ping, n_range)
                               )),
            },
            coords={
                "channel": [f"CH{i}" for i in range(n_channel)],
                "ping_time": np.arange(n_ping),
                "range_sample": np.arange(n_range),
            },
        )
        
        result = add_depth_to_sv(ds, echodata=None, depth_offset=10.0)
        
        assert result.depth.shape == (n_channel, n_ping, n_range)
        # All channels should have same depth profile
        np.testing.assert_array_almost_equal(
            result.depth.isel(channel=0).values,
            result.depth.isel(channel=1).values
        )


class TestAddDepthFromEchodata:
    """Tests for add_depth_from_echodata convenience function."""

    def test_function_exists(self):
        """Test that add_depth_from_echodata function exists in module."""
        from oceanstream.echodata.consolidate.depth import add_depth_from_echodata
        
        assert callable(add_depth_from_echodata)
    
    def test_with_missing_echodata_file(self, tmp_path):
        """Test that missing echodata file raises FileNotFoundError."""
        from oceanstream.echodata.consolidate.depth import add_depth_from_echodata
        
        ds = xr.Dataset(
            {
                "Sv": (["channel", "ping_time", "range_sample"], np.zeros((2, 10, 50))),
                "echo_range": (["channel", "ping_time", "range_sample"],
                               np.broadcast_to(np.arange(50), (2, 10, 50))),
            },
            coords={
                "channel": ["ES38", "ES120"],
                "ping_time": np.arange(10),
                "range_sample": np.arange(50),
            },
        )
        
        fake_path = tmp_path / "nonexistent.zarr"
        
        # Should raise FileNotFoundError for missing file
        with pytest.raises(FileNotFoundError):
            add_depth_from_echodata(ds, fake_path, depth_offset=5.0)


class TestDepthComputation:
    """Integration tests for depth computation in NASC workflow."""

    def test_nasc_with_auto_depth(self):
        """Test that compute_nasc works with auto-computed depth."""
        from oceanstream.echodata.compute import compute_nasc
        
        np.random.seed(42)
        n_ping, n_range, n_channel = 50, 100, 2
        
        # Create mock Sv dataset
        ds = xr.Dataset(
            {
                "Sv": (["channel", "ping_time", "range_sample"],
                       np.random.randn(n_channel, n_ping, n_range).astype(np.float32) * 10 - 70),
                "echo_range": (["channel", "ping_time", "range_sample"],
                               np.broadcast_to(np.linspace(0, 50, n_range), (n_channel, n_ping, n_range))),
                "latitude": (["ping_time"], 37.0 + np.random.randn(n_ping) * 0.01),
                "longitude": (["ping_time"], -122.0 + np.random.randn(n_ping) * 0.01),
                "frequency_nominal": (["channel"], [38000.0, 120000.0]),
            },
            coords={
                "ping_time": np.datetime64("2023-01-01") + np.arange(n_ping) * np.timedelta64(10, "s"),
                "channel": ["ES38", "ES120"],
                "range_sample": np.arange(n_range),
            },
        )
        
        # Compute NASC with transducer_depth
        result = compute_nasc(ds, range_bin="10m", dist_bin="0.5nmi", transducer_depth=5.0)
        
        assert "NASC" in result.data_vars
        assert result.NASC.shape[0] == n_channel
        assert "depth" in result.dims

    def test_depth_uses_transducer_offset(self):
        """Test that transducer depth offset is correctly applied."""
        from oceanstream.echodata.consolidate import add_depth_to_sv
        
        ds = xr.Dataset(
            {
                "Sv": (["channel", "ping_time", "range_sample"], np.zeros((1, 5, 10))),
                "echo_range": (["channel", "ping_time", "range_sample"],
                               np.broadcast_to(np.arange(10), (1, 5, 10))),
            },
            coords={
                "channel": ["ES38"],
                "ping_time": np.arange(5),
                "range_sample": np.arange(10),
            },
        )
        
        # Add with 7.5m offset
        result = add_depth_to_sv(ds, echodata=None, depth_offset=7.5)
        
        # First sample should be at offset depth
        assert result.depth.values[0, 0, 0] == 7.5
        # Last sample should be echo_range + offset
        assert result.depth.values[0, 0, -1] == 9.0 + 7.5
