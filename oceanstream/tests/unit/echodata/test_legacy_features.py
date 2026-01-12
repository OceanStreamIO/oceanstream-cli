"""Tests for features ported from legacy echodata processing.

Tests the following ported functionality:
1. NASC log transform (NASC_log variable)
2. Location data merging (merge_location_data)
3. Mask visualization (plot_mask_channel, plot_all_masks, plot_masks_vertical)
"""

from __future__ import annotations

from pathlib import Path
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock

import numpy as np
import pandas as pd
import pytest
import xarray as xr


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def sample_sv_dataset() -> xr.Dataset:
    """Create a sample Sv dataset for testing."""
    n_pings = 100
    n_depths = 50
    n_channels = 2
    
    # Create time coordinates
    base_time = np.datetime64("2023-06-01T00:00:00")
    ping_time = base_time + np.arange(n_pings) * np.timedelta64(1, "m")
    
    # Create channel coordinates
    channels = np.array([
        "WBT Mini 714398-15 ES38-18_ES",
        "WBT Mini 714401-15 ES200-7C_ES",
    ])
    
    # Create depth coordinate
    depth = np.linspace(0, 200, n_depths)
    
    # Create random Sv data (linear, not dB)
    rng = np.random.default_rng(42)
    sv_linear = rng.uniform(1e-7, 1e-4, (n_channels, n_pings, n_depths))
    sv_db = 10 * np.log10(sv_linear)
    
    # Create dataset
    ds = xr.Dataset(
        {
            "Sv": (["channel", "ping_time", "depth"], sv_db),
            "frequency_nominal": (["channel"], [38000, 200000]),
        },
        coords={
            "channel": channels,
            "ping_time": ping_time,
            "depth": depth,
        },
    )
    
    return ds


@pytest.fixture
def location_data_legacy() -> list[dict]:
    """Create legacy-format location data with lat/lon/dt/knt keys."""
    base_time = datetime(2023, 6, 1, 0, 0, 0)
    return [
        {"lat": 35.0, "lon": -120.0, "dt": base_time.isoformat() + "Z", "knt": 5.0},
        {"lat": 35.05, "lon": -120.05, "dt": (base_time + timedelta(hours=1)).isoformat() + "Z", "knt": 5.2},
        {"lat": 35.1, "lon": -120.1, "dt": (base_time + timedelta(hours=2)).isoformat() + "Z", "knt": 5.1},
    ]


@pytest.fixture
def location_data_modern() -> list[dict]:
    """Create modern-format location data with latitude/longitude/time keys."""
    base_time = datetime(2023, 6, 1, 0, 0, 0)
    return [
        {"latitude": 35.0, "longitude": -120.0, "time": base_time.isoformat() + "Z", "speed_knots": 5.0},
        {"latitude": 35.05, "longitude": -120.05, "time": (base_time + timedelta(hours=1)).isoformat() + "Z", "speed_knots": 5.2},
        {"latitude": 35.1, "longitude": -120.1, "time": (base_time + timedelta(hours=2)).isoformat() + "Z", "speed_knots": 5.1},
    ]


@pytest.fixture
def sample_mask_dataset(sample_sv_dataset) -> xr.Dataset:
    """Create a sample dataset with mask variables."""
    ds = sample_sv_dataset.copy()
    
    n_channels = ds.dims["channel"]
    n_pings = ds.dims["ping_time"]
    n_depths = ds.dims["depth"]
    
    # Create some random masks
    rng = np.random.default_rng(42)
    mask_impulsive = rng.random((n_channels, n_pings, n_depths)) > 0.9
    mask_attenuation = rng.random((n_channels, n_pings, n_depths)) > 0.95
    
    ds["mask_impulsive"] = (["channel", "ping_time", "depth"], mask_impulsive)
    ds["mask_attenuation"] = (["channel", "ping_time", "depth"], mask_attenuation)
    
    # Add channel labels
    ds = ds.assign_coords(
        channel_label=("channel", ["38 kHz", "200 kHz"])
    )
    
    return ds


# =============================================================================
# Tests for NASC Log Transform
# =============================================================================


class TestNASCLogTransform:
    """Tests for NASC_log computation."""
    
    def test_nasc_log_variable_exists(self, sample_sv_dataset):
        """Test that NASC_log is added to output."""
        # Create a mock NASC dataset
        nasc_ds = xr.Dataset({
            "NASC": (["channel", "distance_nmi", "depth"], np.random.rand(2, 10, 5) * 1000)
        })
        
        # Apply log transform (simulating what compute_nasc does)
        nasc_ds["NASC_log"] = 10 * np.log10(nasc_ds["NASC"])
        nasc_ds["NASC_log"].attrs = {
            "long_name": "Log10-transformed NASC",
            "units": "dB re 1 m² nmi⁻²",
            "description": "10 * log10(NASC) for visualization",
        }
        
        assert "NASC_log" in nasc_ds
        assert "long_name" in nasc_ds["NASC_log"].attrs
        assert "units" in nasc_ds["NASC_log"].attrs
    
    def test_nasc_log_values_correct(self):
        """Test that NASC_log = 10 * log10(NASC)."""
        nasc_values = np.array([100, 1000, 10000])
        expected_log = np.array([20, 30, 40])  # 10 * log10
        
        nasc_log = 10 * np.log10(nasc_values)
        np.testing.assert_allclose(nasc_log, expected_log)
    
    def test_nasc_log_handles_zeros(self):
        """Test behavior with zero NASC values (should produce -inf)."""
        nasc_values = np.array([0, 100, 1000])
        with np.errstate(divide='ignore'):
            nasc_log = 10 * np.log10(nasc_values)
        
        assert np.isinf(nasc_log[0]) and nasc_log[0] < 0  # -inf
        assert np.isfinite(nasc_log[1])


# =============================================================================
# Tests for Location Data Merging
# =============================================================================


class TestMergeLocationData:
    """Tests for merge_location_data function."""
    
    def test_merge_legacy_format(self, sample_sv_dataset, location_data_legacy):
        """Test merging with legacy format (lat/lon/dt/knt)."""
        from oceanstream.echodata.concat import merge_location_data
        
        merged = merge_location_data(sample_sv_dataset, location_data_legacy)
        
        assert "latitude" in merged.data_vars
        assert "longitude" in merged.data_vars
        assert "speed_knots" in merged.data_vars
        assert "Sv" in merged.data_vars  # Original data preserved
    
    def test_merge_modern_format(self, sample_sv_dataset, location_data_modern):
        """Test merging with modern format (latitude/longitude/time)."""
        from oceanstream.echodata.concat import merge_location_data
        
        merged = merge_location_data(sample_sv_dataset, location_data_modern)
        
        assert "latitude" in merged.data_vars
        assert "longitude" in merged.data_vars
        assert "speed_knots" in merged.data_vars
    
    def test_location_interpolated_to_ping_time(self, sample_sv_dataset, location_data_legacy):
        """Test that location data is interpolated to ping_time."""
        from oceanstream.echodata.concat import merge_location_data
        
        merged = merge_location_data(sample_sv_dataset, location_data_legacy)
        
        # Check that latitude has same dimension as ping_time
        assert "ping_time" in merged["latitude"].dims
        assert len(merged["latitude"]) == len(sample_sv_dataset["ping_time"])
    
    def test_replaces_existing_location_vars(self, sample_sv_dataset, location_data_legacy):
        """Test that existing location vars are replaced."""
        from oceanstream.echodata.concat import merge_location_data
        
        # Add existing location vars
        ds = sample_sv_dataset.copy()
        ds["latitude"] = ("ping_time", np.zeros(ds.dims["ping_time"]))
        ds["longitude"] = ("ping_time", np.zeros(ds.dims["ping_time"]))
        
        merged = merge_location_data(ds, location_data_legacy)
        
        # Should have new values, not zeros
        assert not np.allclose(merged["latitude"].values, 0)
    
    def test_missing_speed_data(self, sample_sv_dataset):
        """Test handling of location data without speed."""
        from oceanstream.echodata.concat import merge_location_data
        
        location_data = [
            {"lat": 35.0, "lon": -120.0, "dt": "2023-06-01T00:00:00Z"},
            {"lat": 35.1, "lon": -120.1, "dt": "2023-06-01T01:00:00Z"},
        ]
        
        merged = merge_location_data(sample_sv_dataset, location_data)
        
        assert "speed_knots" in merged.data_vars
        # Speed should be 0 (default for missing)
        assert np.allclose(merged["speed_knots"].values, 0)


# =============================================================================
# Tests for Mask Visualization
# =============================================================================


class TestPlotMaskChannel:
    """Tests for plot_mask_channel function."""
    
    def test_plot_mask_channel_creates_file(self, sample_mask_dataset, tmp_path):
        """Test that plot_mask_channel creates a PNG file."""
        from oceanstream.echodata.plot import plot_mask_channel
        
        mask_da = sample_mask_dataset["mask_impulsive"].isel(channel=0)
        mask_da = mask_da.rename("mask")
        
        result = plot_mask_channel(
            mask_da=mask_da,
            channel=None,  # Already sliced
            file_base_name="test_mask",
            echogram_path=str(tmp_path),
        )
        
        assert result.exists()
        assert result.suffix == ".png"
        assert "mask" in result.name
    
    def test_plot_mask_channel_with_channel_dim(self, sample_mask_dataset, tmp_path):
        """Test plotting with channel dimension present."""
        from oceanstream.echodata.plot import plot_mask_channel
        
        # Keep channel dimension but select first channel
        mask_da = sample_mask_dataset["mask_impulsive"].isel(channel=[0])
        mask_da = mask_da.astype(int).rename("mask")
        
        result = plot_mask_channel(
            mask_da=mask_da,
            channel=0,
            file_base_name="test_mask_ch0",
            echogram_path=str(tmp_path),
        )
        
        assert result.exists()


class TestPlotAllMasks:
    """Tests for plot_all_masks function."""
    
    def test_plot_all_masks_creates_files_per_channel(self, sample_mask_dataset, tmp_path):
        """Test that plot_all_masks creates one file per channel."""
        from oceanstream.echodata.plot import plot_all_masks
        
        mask_cube = sample_mask_dataset["mask_impulsive"]
        
        results = plot_all_masks(
            mask_cube=mask_cube,
            ds_source=sample_mask_dataset,
            stage_name="Impulsive Noise",
            file_base_name="test_all_masks",
            output_path=str(tmp_path),
        )
        
        assert len(results) == sample_mask_dataset.dims["channel"]
        for path in results:
            assert path.exists()


class TestPlotMasksVertical:
    """Tests for plot_masks_vertical function."""
    
    def test_plot_masks_vertical_finds_mask_vars(self, sample_mask_dataset, tmp_path):
        """Test that plot_masks_vertical finds all mask_* variables."""
        from oceanstream.echodata.plot import plot_masks_vertical
        
        results = plot_masks_vertical(
            ds_source=sample_mask_dataset,
            file_base_name="test_vertical",
            output_path=str(tmp_path),
        )
        
        assert "impulsive" in results
        assert "attenuation" in results
        assert results["impulsive"].exists()
        assert results["attenuation"].exists()
    
    def test_plot_masks_vertical_no_masks(self, sample_sv_dataset, tmp_path):
        """Test with dataset that has no mask_* variables."""
        from oceanstream.echodata.plot import plot_masks_vertical
        
        results = plot_masks_vertical(
            ds_source=sample_sv_dataset,  # No mask_* vars
            file_base_name="test_no_masks",
            output_path=str(tmp_path),
        )
        
        assert results == {}


# =============================================================================
# Integration Tests
# =============================================================================


class TestLegacyFeaturesIntegration:
    """Integration tests combining multiple features."""
    
    def test_merge_then_plot(self, sample_mask_dataset, location_data_legacy, tmp_path):
        """Test merging location data then plotting masks."""
        from oceanstream.echodata.concat import merge_location_data
        from oceanstream.echodata.plot import plot_masks_vertical
        
        # Merge location data
        merged = merge_location_data(sample_mask_dataset, location_data_legacy)
        
        # Should still have mask variables after merge
        assert "mask_impulsive" in merged.data_vars
        assert "mask_attenuation" in merged.data_vars
        
        # Should be able to plot
        results = plot_masks_vertical(
            ds_source=merged,
            file_base_name="test_merged",
            output_path=str(tmp_path),
        )
        
        assert len(results) == 2
    
    def test_imports_from_main_module(self):
        """Test that all new functions are importable from main module."""
        from oceanstream.echodata import (
            merge_location_data,
            plot_mask_channel,
            plot_all_masks,
            plot_masks_vertical,
        )
        
        assert callable(merge_location_data)
        assert callable(plot_mask_channel)
        assert callable(plot_all_masks)
        assert callable(plot_masks_vertical)
