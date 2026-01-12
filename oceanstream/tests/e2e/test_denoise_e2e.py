"""E2E tests for denoising pipeline."""

from pathlib import Path
import pytest
import numpy as np


@pytest.mark.skip(reason="Denoise tests deferred - will be enabled after denoise module is complete")
@pytest.mark.e2e
class TestDenoiseE2E:
    """End-to-end tests for denoising Sv data."""

    def test_remove_background_noise(self, sv_dataset, echopype_available):
        """Test background noise removal on real data."""
        from oceanstream.echodata.denoise.background_noise import remove_background_noise
        from oceanstream.echodata.config import DenoiseConfig
        
        config = DenoiseConfig(
            background_num_side_pings=25,
            background_noise_max=-125.0,
        )
        
        ds_cleaned = remove_background_noise(sv_dataset, config)
        
        assert ds_cleaned is not None
        assert "Sv" in ds_cleaned.data_vars
        
        # Cleaned data should have some NaN values where noise was removed
        # (unless there was no noise to remove)
        original_nans = np.isnan(sv_dataset["Sv"].values).sum()
        cleaned_nans = np.isnan(ds_cleaned["Sv"].values).sum()
        # At minimum, should not reduce NaN count
        assert cleaned_nans >= original_nans

    def test_detect_transient_noise(self, sv_dataset, echopype_available):
        """Test transient noise detection on real data."""
        from oceanstream.echodata.denoise.transient_noise import detect_transient_noise
        
        mask = detect_transient_noise(
            sv_dataset,
            a=2.0,
            n=5,
        )
        
        assert mask is not None
        # Mask should be boolean array with same ping_time dimension
        assert "ping_time" in mask.dims

    def test_detect_impulse_noise(self, sv_dataset, echopype_available):
        """Test impulse noise detection on real data."""
        from oceanstream.echodata.denoise.impulse_noise import detect_impulse_noise
        
        mask = detect_impulse_noise(
            sv_dataset,
            threshold_db=10.0,
            num_lags=3,
        )
        
        assert mask is not None

    def test_apply_denoising_full_pipeline(self, sv_dataset, echopype_available):
        """Test full denoising pipeline with all methods."""
        from oceanstream.echodata.denoise import apply_denoising
        from oceanstream.echodata.config import DenoiseConfig
        
        config = DenoiseConfig(
            background_num_side_pings=25,
            transient_a=2.0,
            transient_n=5,
            impulse_threshold_db=10.0,
            impulse_num_lags=3,
        )
        
        ds_denoised = apply_denoising(
            sv_dataset,
            config,
            methods=["background", "transient", "impulse"],
        )
        
        assert ds_denoised is not None
        assert "Sv" in ds_denoised.data_vars

    def test_build_full_mask(self, sv_dataset, echopype_available):
        """Test per-channel mask building."""
        from oceanstream.echodata.denoise import build_full_mask
        from oceanstream.echodata.config import DenoiseConfig
        
        config = DenoiseConfig(
            transient_a=2.0,
            transient_n=5,
            impulse_threshold_db=10.0,
        )
        
        mask, stage_masks = build_full_mask(
            sv_dataset,
            config,
            methods=["transient", "impulse"],
        )
        
        assert mask is not None
        # Should have same shape as Sv
        assert mask.shape == sv_dataset["Sv"].shape
        
        # Stage masks should be returned
        assert isinstance(stage_masks, dict)

    def test_create_multichannel_mask(self, sv_dataset, echopype_available):
        """Test multi-channel mask combination."""
        from oceanstream.echodata.denoise import create_multichannel_mask
        import xarray as xr
        
        # Create a simple mask for testing
        mask = xr.DataArray(
            np.random.choice([True, False], size=sv_dataset["Sv"].shape),
            dims=sv_dataset["Sv"].dims,
            coords=sv_dataset["Sv"].coords,
        )
        
        combined = create_multichannel_mask([mask], method="union")
        
        assert combined is not None

    def test_denoising_preserves_coordinates(self, sv_dataset, echopype_available):
        """Denoising should preserve all coordinates."""
        from oceanstream.echodata.denoise import apply_denoising
        from oceanstream.echodata.config import DenoiseConfig
        
        config = DenoiseConfig()
        
        ds_denoised = apply_denoising(
            sv_dataset,
            config,
            methods=["background"],
        )
        
        # All original coordinates should be preserved
        for coord in sv_dataset.coords:
            assert coord in ds_denoised.coords, f"Coordinate {coord} lost during denoising"
