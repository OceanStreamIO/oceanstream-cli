"""E2E tests for Sv computation."""

from pathlib import Path
import pytest
import numpy as np


@pytest.mark.e2e
class TestComputeSvE2E:
    """End-to-end tests for computing Sv from EchoData."""

    def test_compute_sv_from_echodata(self, echodata_obj, echopype_available):
        """Compute Sv from in-memory EchoData object."""
        from oceanstream.echodata.compute.sv import compute_sv_from_echodata
        
        ds_Sv = compute_sv_from_echodata(
            echodata_obj,
            add_depth=True,
            add_location=False,  # Location may be NaN in test data
            waveform_mode="CW",
            encode_mode="complex",
        )
        
        assert ds_Sv is not None
        assert "Sv" in ds_Sv.data_vars
        assert "ping_time" in ds_Sv.dims
        assert "range_sample" in ds_Sv.dims
        
        # Check Sv values are in reasonable range (-120 to 0 dB typically)
        sv_values = ds_Sv["Sv"].values
        valid_sv = sv_values[~np.isnan(sv_values)]
        if len(valid_sv) > 0:
            assert valid_sv.min() > -200, "Sv values too low"
            assert valid_sv.max() < 50, "Sv values too high"

    def test_compute_sv_from_zarr(self, converted_zarr: Path, echopype_available):
        """Compute Sv from zarr file on disk."""
        from oceanstream.echodata.compute.sv import compute_sv
        
        ds_Sv = compute_sv(
            converted_zarr,
            add_depth=True,
            add_location=False,  # Location may be NaN in test data
        )
        
        assert ds_Sv is not None
        assert "Sv" in ds_Sv.data_vars

    def test_sv_has_channel_dimension(self, sv_dataset, echopype_available):
        """Sv should have channel dimension for multi-frequency data."""
        assert "channel" in sv_dataset.dims
        
        # EK80 typically has multiple frequencies
        n_channels = sv_dataset.dims["channel"]
        assert n_channels >= 1, "Expected at least one channel"

    def test_enrich_sv_dataset(self, sv_dataset, echodata_obj, echopype_available):
        """Test Sv enrichment with depth and location."""
        from oceanstream.echodata.compute.sv import enrich_sv_dataset
        
        ds_enriched = enrich_sv_dataset(
            sv_dataset,
            echodata_obj,
            add_depth=True,
            add_location=True,
            add_splitbeam_angle=False,  # May not be available
            depth_offset=1.9,  # Typical Saildrone transducer depth
        )
        
        assert ds_enriched is not None
        
        # Check depth was added
        if "depth" in ds_enriched:
            depth_vals = ds_enriched["depth"].values
            valid_depth = depth_vals[~np.isnan(depth_vals)]
            if len(valid_depth) > 0:
                assert valid_depth.min() >= 0, "Depth should be positive"

    def test_sv_save_to_zarr(self, sv_dataset, tmp_path: Path, echopype_available):
        """Sv dataset should be saveable to zarr."""
        output_path = tmp_path / "sv_output.zarr"
        
        sv_dataset.to_zarr(output_path, mode="w")
        
        assert output_path.exists()
        
        # Reload and verify
        import xarray as xr
        reloaded = xr.open_zarr(output_path)
        assert "Sv" in reloaded.data_vars
