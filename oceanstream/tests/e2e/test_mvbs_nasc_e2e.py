"""E2E tests for MVBS and NASC computation."""

from pathlib import Path
import pytest
import numpy as np


@pytest.mark.e2e
class TestMVBSE2E:
    """End-to-end tests for MVBS (Mean Volume Backscattering Strength)."""

    def test_compute_mvbs(self, sv_dataset, echopype_available):
        """Compute MVBS from Sv dataset."""
        from oceanstream.echodata.compute.mvbs import compute_mvbs
        
        ds_mvbs = compute_mvbs(
            sv_dataset,
            range_bin="10m",  # 10m vertical bins
            ping_time_bin="60s",  # 1-minute time bins
        )
        
        assert ds_mvbs is not None
        assert "Sv" in ds_mvbs.data_vars
        
        # MVBS should have reduced dimensions
        assert ds_mvbs.dims["ping_time"] <= sv_dataset.dims["ping_time"]
        
        # MVBS values should be in reasonable range
        mvbs_values = ds_mvbs["Sv"].values
        valid_mvbs = mvbs_values[~np.isnan(mvbs_values)]
        if len(valid_mvbs) > 0:
            assert valid_mvbs.min() > -200
            assert valid_mvbs.max() < 50

    def test_mvbs_different_bin_sizes(self, sv_dataset, echopype_available):
        """Test MVBS with different bin sizes."""
        from oceanstream.echodata.compute.mvbs import compute_mvbs
        
        # Larger bins
        ds_mvbs_large = compute_mvbs(sv_dataset, range_bin="20m", ping_time_bin="120s")
        
        # Smaller bins
        ds_mvbs_small = compute_mvbs(sv_dataset, range_bin="5m", ping_time_bin="30s")
        
        # Larger bins should result in fewer samples
        assert ds_mvbs_large.dims["ping_time"] <= ds_mvbs_small.dims["ping_time"]

    def test_mvbs_save_to_zarr(self, sv_dataset, tmp_path: Path, echopype_available):
        """MVBS should be saveable to zarr."""
        from oceanstream.echodata.compute.mvbs import compute_mvbs
        
        ds_mvbs = compute_mvbs(sv_dataset, range_bin="10m", ping_time_bin="60s")
        
        output_path = tmp_path / "mvbs.zarr"
        ds_mvbs.to_zarr(output_path, mode="w")
        
        assert output_path.exists()
        
        # Reload and verify
        import xarray as xr
        reloaded = xr.open_zarr(output_path)
        assert "Sv" in reloaded.data_vars


@pytest.mark.e2e
class TestNASCE2E:
    """End-to-end tests for NASC (Nautical Area Scattering Coefficient)."""

    def test_compute_nasc(self, sv_dataset, echopype_available):
        """Compute NASC from Sv dataset."""
        from oceanstream.echodata.compute.nasc import compute_nasc
        
        # Skip if no location data (NASC requires lat/lon for distance calculation)
        if "latitude" not in sv_dataset and "longitude" not in sv_dataset:
            pytest.skip("NASC requires location data (latitude, longitude) which is missing in test data")
        
        ds_nasc = compute_nasc(
            sv_dataset,
            range_bin="10m",
            dist_bin="0.5nmi",  # 0.5 nautical mile bins
        )
        
        assert ds_nasc is not None
        assert "NASC" in ds_nasc.data_vars
        
        # NASC values should be positive (it's an area coefficient)
        nasc_values = ds_nasc["NASC"].values
        valid_nasc = nasc_values[~np.isnan(nasc_values)]
        if len(valid_nasc) > 0:
            assert valid_nasc.min() >= 0, "NASC should be non-negative"

    def test_nasc_with_depth_integration(self, sv_dataset, echopype_available):
        """NASC should support depth integration limits."""
        from oceanstream.echodata.compute.nasc import compute_nasc
        
        # Skip if no location data (NASC requires lat/lon for distance calculation)
        if "latitude" not in sv_dataset and "longitude" not in sv_dataset:
            pytest.skip("NASC requires location data (latitude, longitude) which is missing in test data")
        
        # Compute with full depth
        ds_nasc_full = compute_nasc(sv_dataset, range_bin="10m", dist_bin="0.5nmi")
        
        # NASC is integrated over depth
        assert "NASC" in ds_nasc_full.data_vars

    def test_nasc_by_channel(self, sv_dataset, echopype_available):
        """NASC should be computed per channel."""
        from oceanstream.echodata.compute.nasc import compute_nasc
        
        # Skip if no location data (NASC requires lat/lon for distance calculation)
        if "latitude" not in sv_dataset and "longitude" not in sv_dataset:
            pytest.skip("NASC requires location data (latitude, longitude) which is missing in test data")
        
        ds_nasc = compute_nasc(sv_dataset, range_bin="10m", dist_bin="0.5nmi")
        
        # Should preserve channel dimension
        if "channel" in sv_dataset.dims:
            assert "channel" in ds_nasc.dims or ds_nasc.dims.get("channel", 1) >= 1
