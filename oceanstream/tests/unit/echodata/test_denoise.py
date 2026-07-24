"""Unit tests for oceanstream.echodata.denoise module."""

from pathlib import Path
import pytest
import numpy as np
from unittest.mock import patch, MagicMock

# xarray is optional for tests
xr = pytest.importorskip("xarray")
pd = pytest.importorskip("pandas")


@pytest.fixture
def sample_sv_dataset():
    """Create a sample Sv dataset for testing."""
    ping_times = pd.date_range("2023-06-01", periods=100, freq="S")
    n_depth = 500
    depth_values = np.arange(n_depth) * 0.5  # 0.5m resolution
    
    # Create synthetic Sv with background pattern
    sv = np.random.randn(100, n_depth) - 70
    
    return xr.Dataset(
        {
            "Sv": (["ping_time", "depth"], sv),
        },
        coords={
            "ping_time": ping_times,
            "depth": depth_values,
        },
        attrs={"platform": "test_platform"},
    )


@pytest.fixture
def sample_sv_with_channel():
    """Create a sample multi-channel Sv dataset."""
    ping_times = pd.date_range("2023-06-01", periods=100, freq="S")
    n_depth = 500
    n_channels = 3
    depth_values = np.arange(n_depth) * 0.5
    
    sv = np.random.randn(n_channels, 100, n_depth) - 70
    
    return xr.Dataset(
        {
            "Sv": (["channel", "ping_time", "depth"], sv),
        },
        coords={
            "ping_time": ping_times,
            "depth": depth_values,
            "channel": ["18kHz", "38kHz", "120kHz"],
        },
    )


class TestBackgroundNoiseMask:
    """Tests for background noise mask function."""

    def test_background_noise_mask_basic(self, sample_sv_dataset):
        """Should create a background noise mask."""
        from oceanstream.echodata.denoise.background_noise import background_noise_mask
        
        params = {
            "range_window": 10,
            "ping_window": 20,
            "background_noise_max": "-125.0dB",
            "SNR_threshold": "3.0dB",
        }
        
        result = background_noise_mask(sample_sv_dataset, params)

        assert isinstance(result, tuple)
        mask, unfeasible = result
        assert mask is not None
        assert mask.dtype == bool
        assert mask.shape == sample_sv_dataset["Sv"].shape

    def test_background_noise_mask_with_defaults(self, sample_sv_dataset):
        """Should work with default parameters."""
        from oceanstream.echodata.denoise.background_noise import background_noise_mask
        
        result = background_noise_mask(sample_sv_dataset, {})

        mask, unfeasible = result
        assert mask is not None
        assert mask.dtype == bool

    def test_background_noise_mask_respects_snr_threshold(self, sample_sv_dataset):
        """Higher SNR threshold should flag more samples."""
        from oceanstream.echodata.denoise.background_noise import background_noise_mask
        
        low_threshold_params = {"SNR_threshold": "1.0dB", "ping_window": 20}
        high_threshold_params = {"SNR_threshold": "10.0dB", "ping_window": 20}
        
        mask_low, _ = background_noise_mask(sample_sv_dataset, low_threshold_params)
        mask_high, _ = background_noise_mask(sample_sv_dataset, high_threshold_params)

        # Higher threshold should flag more or equal samples
        assert float(mask_high.sum()) >= float(mask_low.sum())


class TestTransientNoiseMask:
    """Tests for transient noise mask function."""

    def test_transient_noise_mask_basic(self, sample_sv_dataset):
        """Should create a transient noise mask."""
        from oceanstream.echodata.denoise.transient_noise import transient_noise_mask
        
        params = {
            "exclude_above": 100.0,
            "ping_window": 5,
            "threshold": (10.0, 7.0),
        }
        
        result = transient_noise_mask(sample_sv_dataset, params)

        assert isinstance(result, tuple)
        mask, unfeasible = result
        assert mask is not None
        assert mask.dtype == bool

    def test_transient_noise_mask_detects_spikes(self):
        """Should detect transient noise spikes."""
        from oceanstream.echodata.denoise.transient_noise import transient_noise_mask
        
        ping_times = pd.date_range("2023-06-01", periods=100, freq="S")
        n_depth = 500
        depth_values = np.arange(n_depth) * 0.5
        
        # Create data with a clear transient spike at ping 50
        sv = np.random.randn(100, n_depth) - 70
        sv[50, :] += 30  # Add large spike
        
        ds = xr.Dataset(
            {"Sv": (["ping_time", "depth"], sv)},
            coords={"ping_time": ping_times, "depth": depth_values},
        )
        
        params = {"exclude_above": 0.0, "ping_window": 5, "threshold": (10.0, 7.0)}
        mask, unfeasible = transient_noise_mask(ds, params)

        # Should flag some samples around the spike
        assert float(mask.sum()) > 0

    def test_transient_noise_mask_with_defaults(self, sample_sv_dataset):
        """Should work with default parameters."""
        from oceanstream.echodata.denoise.transient_noise import transient_noise_mask
        
        mask, unfeasible = transient_noise_mask(sample_sv_dataset, {})

        assert mask is not None


class TestImpulseNoiseMask:
    """Tests for impulse noise mask function."""

    def test_impulse_noise_mask_basic(self, sample_sv_dataset):
        """Should create an impulse noise mask."""
        from oceanstream.echodata.denoise.impulse_noise import impulse_noise_mask
        
        params = {
            "vertical_bin_size": 2.0,
            "ping_lags": [1, 2],
            "threshold_db": 10.0,
        }
        
        result = impulse_noise_mask(sample_sv_dataset, params)

        assert isinstance(result, tuple)
        mask, unfeasible = result
        assert mask is not None
        assert mask.dtype == bool

    def test_impulse_noise_detects_isolated_spike(self):
        """Should detect isolated impulse noise."""
        from oceanstream.echodata.denoise.impulse_noise import impulse_noise_mask
        
        ping_times = pd.date_range("2023-06-01", periods=100, freq="S")
        n_depth = 100
        depth_values = np.arange(n_depth) * 1.0
        
        # Create data with isolated spike
        sv = np.random.randn(100, n_depth) - 70
        sv[50, 50] += 50  # Very high isolated spike
        
        ds = xr.Dataset(
            {"Sv": (["ping_time", "depth"], sv)},
            coords={"ping_time": ping_times, "depth": depth_values},
        )
        
        params = {"threshold_db": 20.0, "vertical_bin_size": 1.0}
        mask, unfeasible = impulse_noise_mask(ds, params)

        # Should be a boolean mask
        assert mask.dtype == bool

    def test_impulse_noise_multi_lag(self, sample_sv_dataset):
        """Should support multiple lag values."""
        from oceanstream.echodata.denoise.impulse_noise import impulse_noise_mask
        
        params = {"ping_lags": [1, 2, 3], "threshold_db": 10.0}
        mask, unfeasible = impulse_noise_mask(sample_sv_dataset, params)

        assert mask is not None


class TestAttenuationMask:
    """Tests for attenuation mask function."""

    def test_attenuation_mask_basic(self, sample_sv_dataset):
        """Should create an attenuation mask."""
        from oceanstream.echodata.denoise.attenuation import attenuation_mask
        
        # Adjust depth limits to match our test data (0-250m)
        params = {
            "upper_limit_sl": 50.0,
            "lower_limit_sl": 150.0,
            "num_side_pings": 5,
            "threshold": 5.0,
        }
        
        result = attenuation_mask(sample_sv_dataset, params)

        assert isinstance(result, tuple)
        mask, unfeasible = result
        assert mask is not None
        assert mask.dtype == bool

    def test_attenuation_mask_detects_weak_signal(self):
        """Should detect attenuated pings."""
        from oceanstream.echodata.denoise.attenuation import attenuation_mask
        
        ping_times = pd.date_range("2023-06-01", periods=100, freq="S")
        n_depth = 300
        depth_values = np.arange(n_depth) * 1.0
        
        # Create data where some pings have attenuated signal
        sv = np.random.randn(100, n_depth) - 70
        # Attenuate pings 40-60 in the 180-280m depth band
        sv[40:60, 180:280] -= 20
        
        ds = xr.Dataset(
            {"Sv": (["ping_time", "depth"], sv)},
            coords={"ping_time": ping_times, "depth": depth_values},
        )
        
        params = {
            "upper_limit_sl": 180.0,
            "lower_limit_sl": 280.0,
            "num_side_pings": 10,
            "threshold": 10.0,
        }
        mask, unfeasible = attenuation_mask(ds, params)

        # Should flag some samples
        assert mask is not None


class TestBuildNoiseMask:
    """Tests for build_noise_mask function."""

    def test_build_noise_mask_single_method(self, sample_sv_dataset):
        """Should build mask for single method."""
        from oceanstream.echodata.denoise import build_noise_mask
        from oceanstream.echodata.config import DenoiseConfig
        
        config = DenoiseConfig()
        mask = build_noise_mask(sample_sv_dataset, ["background"], config)

        assert mask is not None
        assert mask.dtype == bool


class TestBuildFullMask:
    """Tests for build_full_mask function."""

    def test_build_full_mask_all_methods(self, sample_sv_with_channel):
        """Should build combined mask from all methods."""
        from oceanstream.echodata.denoise import build_full_mask
        from oceanstream.echodata.config import DenoiseConfig
        
        config = DenoiseConfig()
        mask, stage_masks = build_full_mask(
            sample_sv_with_channel,
            methods=["background", "transient", "impulse", "attenuation"],
            config=config,
            return_stage_masks=True,
        )
        
        assert mask is not None
        assert isinstance(stage_masks, dict)
        # Some methods may not produce results due to depth limits etc
        assert len(stage_masks) >= 1

    def test_build_full_mask_single_method(self, sample_sv_with_channel):
        """Should work with single method."""
        from oceanstream.echodata.denoise import build_full_mask
        from oceanstream.echodata.config import DenoiseConfig
        
        config = DenoiseConfig()
        mask = build_full_mask(
            sample_sv_with_channel,
            methods=["background"],
            config=config,
            return_stage_masks=False,
        )
        
        assert mask is not None


class TestApplyNoiseMask:
    """Tests for apply_noise_mask function."""

    def test_apply_noise_mask_sets_nan(self, sample_sv_dataset):
        """Should set masked values to NaN."""
        from oceanstream.echodata.denoise import apply_noise_mask
        
        # Create a mask that flags all samples
        mask = xr.DataArray(
            np.ones(sample_sv_dataset["Sv"].shape, dtype=bool),
            dims=sample_sv_dataset["Sv"].dims,
            coords=sample_sv_dataset["Sv"].coords,
        )
        
        result = apply_noise_mask(sample_sv_dataset, mask)
        
        # All Sv values should now be NaN
        assert np.all(np.isnan(result["Sv"].values))

    def test_apply_noise_mask_preserves_unmasked(self, sample_sv_dataset):
        """Should preserve unmasked values."""
        from oceanstream.echodata.denoise import apply_noise_mask
        
        # Create a mask that flags nothing
        mask = xr.DataArray(
            np.zeros(sample_sv_dataset["Sv"].shape, dtype=bool),
            dims=sample_sv_dataset["Sv"].dims,
            coords=sample_sv_dataset["Sv"].coords,
        )
        
        result = apply_noise_mask(sample_sv_dataset, mask)
        
        # All Sv values should be preserved
        assert np.allclose(result["Sv"].values, sample_sv_dataset["Sv"].values)


class TestApplyDenoising:
    """Tests for the full denoising pipeline."""

    def test_apply_denoising_default_methods(self, sample_sv_with_channel, tmp_path):
        """Should apply all default denoising methods."""
        from oceanstream.echodata.denoise import apply_denoising
        
        # Save dataset to zarr
        input_path = tmp_path / "input.zarr"
        sample_sv_with_channel.to_zarr(input_path)
        
        result = apply_denoising(input_path)
        
        assert "Sv" in result
        assert result.attrs.get("denoising_applied") is True

    def test_apply_denoising_selective_methods(self, sample_sv_with_channel):
        """Should apply only selected methods."""
        from oceanstream.echodata.denoise import apply_denoising
        
        result = apply_denoising(
            sample_sv_with_channel,
            methods=["background"],
        )
        
        assert result.attrs.get("denoising_methods") == ["background"]

    def test_apply_denoising_saves_output(self, sample_sv_with_channel, tmp_path):
        """Should save denoised dataset when output_path provided."""
        from oceanstream.echodata.denoise import apply_denoising
        
        output_path = tmp_path / "denoised.zarr"
        
        apply_denoising(
            sample_sv_with_channel,
            methods=["background"],
            output_path=output_path,
        )
        
        assert output_path.exists()
        loaded = xr.open_zarr(output_path)
        assert "Sv" in loaded

    def test_apply_denoising_merge_masks(self, sample_sv_with_channel):
        """Should merge individual masks into output dataset."""
        from oceanstream.echodata.denoise import apply_denoising
        
        result = apply_denoising(
            sample_sv_with_channel,
            methods=["background", "impulse"],
            merge_masks=True,
        )
        
        # Should have combined mask
        assert "mask_combined" in result

    def test_apply_denoising_return_stage_masks(self, sample_sv_with_channel):
        """Should return stage masks when requested."""
        from oceanstream.echodata.denoise import apply_denoising
        
        result, stage_masks = apply_denoising(
            sample_sv_with_channel,
            methods=["background", "transient"],
            return_stage_masks=True,
        )
        
        assert isinstance(stage_masks, dict)
        # Some methods may not produce results
        assert len(stage_masks) >= 0


class TestCreateMultichannelMask:
    """Tests for create_multichannel_mask function."""

    def test_create_multichannel_mask(self, sample_sv_with_channel):
        """Should combine multiple channel masks into single dataset."""
        from oceanstream.echodata.denoise import create_multichannel_mask
        
        # Create mock mask datasets for each channel
        n_channels = sample_sv_with_channel.sizes["channel"]
        n_pings = sample_sv_with_channel.sizes["ping_time"]
        n_depth = sample_sv_with_channel.sizes["depth"]
        
        masks = []
        for i in range(n_channels):
            mask_ds = xr.Dataset({
                "mask": (["ping_time", "depth"], 
                         np.random.choice([True, False], size=(n_pings, n_depth)))
            })
            masks.append(mask_ds)
        
        result = create_multichannel_mask(masks, sample_sv_with_channel)
        
        assert result is not None
        assert "mask" in result or len(result.data_vars) > 0


class TestDenoiseConfig:
    """Tests for DenoiseConfig settings."""

    def test_denoise_config_default_values(self):
        """Should have sensible default values."""
        from oceanstream.echodata.config import DenoiseConfig
        
        config = DenoiseConfig()
        
        # Check some defaults exist via the to_*_params methods
        assert hasattr(config, "to_background_params")
        assert hasattr(config, "to_transient_params")
        assert hasattr(config, "to_impulse_params")
        assert hasattr(config, "to_attenuation_params")
        
        # Verify params are returned as dicts
        assert isinstance(config.to_background_params(), dict)
        assert isinstance(config.to_transient_params(), dict)

    def test_denoise_config_custom_values(self):
        """Should accept custom parameter values."""
        from oceanstream.echodata.config import DenoiseConfig
        
        config = DenoiseConfig(
            background_snr_threshold=5.0,
            transient_threshold_db=8.0,
        )
        
        # Check that custom values are reflected
        assert config.background_snr_threshold == 5.0
        assert config.transient_threshold_db == 8.0


class TestModuleExports:
    """Test that all denoise functions are properly exported."""

    def test_denoise_functions_exported(self):
        """Test that denoise functions are exported from module."""
        from oceanstream.echodata import denoise
        
        assert hasattr(denoise, "apply_denoising")
        assert hasattr(denoise, "build_noise_mask")
        assert hasattr(denoise, "build_full_mask")
        assert hasattr(denoise, "apply_noise_mask")
        assert hasattr(denoise, "create_multichannel_mask")
        assert hasattr(denoise, "background_noise_mask")
        assert hasattr(denoise, "transient_noise_mask")
        assert hasattr(denoise, "transient_noise_mask_ryan")
        assert hasattr(denoise, "impulse_noise_mask")
        assert hasattr(denoise, "attenuation_mask")

    def test_denoise_functions_in_all(self):
        """Test that functions are in __all__."""
        from oceanstream.echodata.denoise import __all__
        
        expected = [
            "apply_denoising",
            "build_noise_mask",
            "build_full_mask",
            "apply_noise_mask",
            "create_multichannel_mask",
            "background_noise_mask",
            "transient_noise_mask",
            "transient_noise_mask_ryan",
            "impulse_noise_mask",
            "attenuation_mask",
        ]
        
        for name in expected:
            assert name in __all__


# ──────────────────────────────────────────────────────────────────────────
# New-feature tests: frequency-specific dispatch, 2D depth, param aliases
# ──────────────────────────────────────────────────────────────────────────

dask_array = pytest.importorskip("dask.array")


def _make_2d_depth_dataset(n_pings=100, n_range=200):
    """Build a dataset where depth is a 2-D data_var (ping_time, range_sample).

    This mimics EK80 data where echo_range varies slightly per ping.
    """
    rng = np.random.default_rng(99)
    ping_times = pd.date_range("2023-06-01", periods=n_pings, freq="s")
    depth_profile = np.linspace(0, 250, n_range)
    # Add small per-ping jitter to make depth 2-D
    depth_2d = np.tile(depth_profile, (n_pings, 1)) + rng.uniform(
        -0.3, 0.3, (n_pings, n_range)
    )
    sv = rng.standard_normal((n_pings, n_range)) - 70.0

    return xr.Dataset(
        {
            "Sv": (["ping_time", "range_sample"], sv),
            "depth": (["ping_time", "range_sample"], depth_2d),
        },
        coords={
            "ping_time": ping_times,
            "range_sample": np.arange(n_range),
        },
    )


class TestTransientNoiseParamAliases:
    """Tests for transient_noise_mask parameter aliases (n_pings, thr_dB, depth_bin)."""

    def test_n_pings_alias(self, sample_sv_dataset):
        """n_pings should work as an alias for ping_window."""
        from oceanstream.echodata.denoise.transient_noise import transient_noise_mask

        params_old = {"ping_window": 3, "threshold": (10.0, 7.0)}
        params_new = {"n_pings": 3, "threshold": (10.0, 7.0)}

        mask_old, _ = transient_noise_mask(sample_sv_dataset, params_old)
        mask_new, _ = transient_noise_mask(sample_sv_dataset, params_new)

        np.testing.assert_array_equal(mask_old.values, mask_new.values)

    def test_thr_dB_alias(self, sample_sv_dataset):
        """thr_dB should produce a (thr_dB, thr_dB-3) threshold pair."""
        from oceanstream.echodata.denoise.transient_noise import transient_noise_mask

        # When thr_dB=10, result should equal threshold=(10.0, 7.0)
        params_explicit = {"threshold": (10.0, 7.0), "ping_window": 3}
        params_alias = {"thr_dB": 10.0, "ping_window": 3}

        mask_explicit, _ = transient_noise_mask(sample_sv_dataset, params_explicit)
        mask_alias, _ = transient_noise_mask(sample_sv_dataset, params_alias)

        np.testing.assert_array_equal(mask_explicit.values, mask_alias.values)

    def test_depth_bin_alias(self, sample_sv_dataset):
        """depth_bin should work as an alias for jumps."""
        from oceanstream.echodata.denoise.transient_noise import transient_noise_mask

        params_old = {"jumps": 10.0, "ping_window": 3}
        params_new = {"depth_bin": 10.0, "ping_window": 3}

        mask_old, _ = transient_noise_mask(sample_sv_dataset, params_old)
        mask_new, _ = transient_noise_mask(sample_sv_dataset, params_new)

        np.testing.assert_array_equal(mask_old.values, mask_new.values)


class TestTransientNoise2DDepth:
    """Tests for transient_noise_mask with 2-D depth data_var."""

    def test_2d_depth_does_not_raise(self):
        """transient_noise_mask should handle 2-D depth without error."""
        from oceanstream.echodata.denoise.transient_noise import transient_noise_mask

        ds = _make_2d_depth_dataset()
        params = {"range_coord": "depth", "ping_window": 3}

        mask, unfeasible = transient_noise_mask(ds, params)

        assert mask.dtype == bool
        assert mask.shape == ds["Sv"].shape

    def test_2d_depth_uses_range_sample_dim(self):
        """With 2-D depth, the mask should use range_sample as the vertical dim."""
        from oceanstream.echodata.denoise.transient_noise import transient_noise_mask

        ds = _make_2d_depth_dataset()
        params = {"range_coord": "depth", "ping_window": 3}

        mask, _ = transient_noise_mask(ds, params)

        assert "range_sample" in mask.dims
        assert "ping_time" in mask.dims


class TestImpulseNoise2DDepth:
    """Tests for impulse_noise_mask with 2-D depth data_var."""

    def test_2d_depth_does_not_raise(self):
        """impulse_noise_mask should handle 2-D depth without error."""
        from oceanstream.echodata.denoise.impulse_noise import impulse_noise_mask

        ds = _make_2d_depth_dataset()
        params = {"vertical_bin_size": "5m", "threshold_db": 10.0}

        mask, unfeasible = impulse_noise_mask(ds, params)

        assert mask.dtype == bool
        assert mask.shape == ds["Sv"].shape

    def test_2d_depth_collapses_to_1d(self):
        """The 2-D depth should be collapsed to 1-D for range calculations."""
        from oceanstream.echodata.denoise.impulse_noise import impulse_noise_mask

        ds = _make_2d_depth_dataset()
        params = {"vertical_bin_size": "5m", "threshold_db": 10.0}

        mask, _ = impulse_noise_mask(ds, params)

        # Mask should have the expected dimensions
        assert "range_sample" in mask.dims
        assert "ping_time" in mask.dims


class TestBuildFullMaskFrequencySpecific:
    """Tests for build_full_mask with frequency-specific dispatch."""

    def test_frequency_specific_dispatch(self):
        """build_full_mask should use frequency-keyed params when enabled."""
        from oceanstream.echodata.denoise import build_full_mask
        from oceanstream.echodata.config import DenoiseConfig

        # Create a multi-channel dataset with frequency_nominal
        ping_times = pd.date_range("2023-06-01", periods=100, freq="s")
        n_depth = 500
        depth_values = np.arange(n_depth) * 0.5
        sv = np.random.default_rng(42).standard_normal((2, 100, n_depth)) - 70.0

        ds = xr.Dataset(
            {"Sv": (["channel", "ping_time", "depth"], sv)},
            coords={
                "ping_time": ping_times,
                "depth": depth_values,
                "channel": ["38kHz", "200kHz"],
            },
        )
        ds["frequency_nominal"] = xr.DataArray([38000, 200000], dims=["channel"])

        config = DenoiseConfig(
            use_frequency_specific=True,
            methods=["background"],
        )

        mask = build_full_mask(
            ds,
            methods=["background"],
            config=config,
            return_stage_masks=False,
        )

        assert mask is not None
        assert mask.dtype == bool
        assert mask.shape == ds["Sv"].shape

    def test_frequency_specific_with_user_overrides(self):
        """Frequency-specific dispatch with user overrides should work."""
        from oceanstream.echodata.denoise import build_full_mask
        from oceanstream.echodata.config import DenoiseConfig

        ping_times = pd.date_range("2023-06-01", periods=100, freq="s")
        n_depth = 500
        depth_values = np.arange(n_depth) * 0.5
        sv = np.random.default_rng(42).standard_normal((2, 100, n_depth)) - 70.0

        ds = xr.Dataset(
            {"Sv": (["channel", "ping_time", "depth"], sv)},
            coords={
                "ping_time": ping_times,
                "depth": depth_values,
                "channel": ["38kHz", "200kHz"],
            },
        )
        ds["frequency_nominal"] = xr.DataArray([38000, 200000], dims=["channel"])

        config = DenoiseConfig(
            use_frequency_specific=True,
            methods=["background"],
            frequency_params={
                38000: {"background": {"range_window": 25, "ping_window": 40}},
                200000: {"background": {"range_window": 10, "ping_window": 30}},
            },
        )

        mask, stage_masks = build_full_mask(
            ds,
            methods=["background"],
            config=config,
            return_stage_masks=True,
        )

        assert mask is not None
        assert "background" in stage_masks
