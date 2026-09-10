"""Tests for the Phase 1.5 empirical attenuation fitter.

The fitter is the most consequential function in the library, so the tests
are built around a synthetic scene where k is known exactly. That gives a
recovery test with a real answer rather than a self-consistency check.

The guard tests matter as much as the recovery one. An attenuation fit that
returns a clean number from bad data is worse than one that returns NaN,
because the number propagates into rho_b through ``exp(+k*H)``.
"""

from __future__ import annotations

import numpy as np
import pytest

from oceanstream.coastal.config import AttenuationConfig
from oceanstream.coastal.optics import attenuation

SHAPE = (120, 120)
DEEP_REFERENCE = 0.004
RHO_B_OVER_PI = 0.08


def _synthetic_band(
    k_per_m: float,
    depth: np.ndarray,
    noise: float = 0.0,
    seed: int = 0,
) -> np.ndarray:
    """Reflectance from a uniform bottom under a known attenuation.

    Built by forward-evaluating the model the fitter inverts:
    ``L(H) = L_inf + (rho_b / pi) * exp(-k H)``.
    """
    band = DEEP_REFERENCE + RHO_B_OVER_PI * np.exp(-k_per_m * depth)
    if noise:
        rng = np.random.default_rng(seed)
        band = band + rng.normal(0.0, noise, depth.shape)
    return band.astype(np.float64)


@pytest.fixture
def depth_field() -> np.ndarray:
    """Depth ramping 0.5 -> 24 m, with the last rows genuinely deep.

    The deep block has to be *optically* deep, not merely the deepest part of
    the ramp. At 24 m and k = 0.11 the bottom still contributes more than the
    whole deep-water baseline, so using the ramp's own tail as the reference
    would contaminate it and bias every k upward.
    """
    column = np.concatenate([np.linspace(0.5, 24.0, SHAPE[0] - 10), np.full(10, 200.0)])
    return np.repeat(column[:, None], SHAPE[1], axis=1)


@pytest.fixture
def deep_mask() -> np.ndarray:
    mask = np.zeros(SHAPE, dtype=bool)
    mask[-10:, :] = True
    return mask


class TestDeepWaterReference:
    def test_reference_is_the_median_over_deep_pixels(self, deep_mask: np.ndarray) -> None:
        band = np.full(SHAPE, 0.05)
        band[deep_mask] = 0.004
        out = attenuation.deep_water_reference({489.0: band}, deep_mask)
        assert out[489.0] == pytest.approx(0.004)

    def test_too_few_deep_pixels_is_rejected_with_an_actionable_message(
        self,
    ) -> None:
        band = np.full(SHAPE, 0.05)
        mask = np.zeros(SHAPE, dtype=bool)
        mask[0, :10] = True
        with pytest.raises(ValueError, match="Only 10 deep-water pixels"):
            attenuation.deep_water_reference({489.0: band}, mask)

    def test_non_finite_pixels_do_not_count_towards_the_minimum(
        self, deep_mask: np.ndarray
    ) -> None:
        band = np.full(SHAPE, 0.004)
        band[deep_mask] = np.nan
        with pytest.raises(ValueError, match="Only 0 deep-water pixels"):
            attenuation.deep_water_reference({489.0: band}, deep_mask)


class TestRecovery:
    @pytest.mark.parametrize("k_true", [0.05, 0.11, 0.25, 0.4])
    def test_recovers_a_known_attenuation(self, k_true: float, depth_field: np.ndarray) -> None:
        band = _synthetic_band(k_true, depth_field)
        valid = np.ones(SHAPE, dtype=bool)
        k, _intercept, r2, n_bins, _n_px = attenuation.fit_band_attenuation(
            band, depth_field, valid, DEEP_REFERENCE, quantile=0.90
        )
        assert k == pytest.approx(k_true, rel=0.02)
        assert r2 > 0.999
        assert n_bins >= 4

    def test_survives_moderate_noise(self, depth_field: np.ndarray) -> None:
        band = _synthetic_band(0.15, depth_field, noise=1e-4)
        valid = np.ones(SHAPE, dtype=bool)
        k, *_ = attenuation.fit_band_attenuation(
            band, depth_field, valid, DEEP_REFERENCE, quantile=0.90
        )
        assert k == pytest.approx(0.15, rel=0.10)

    def test_a_wrong_deep_reference_biases_k(self, depth_field: np.ndarray) -> None:
        # The point is that a *modest* reference error is silent: the fit
        # stays well-conditioned while k moves. Only a gross error degrades
        # R2 enough to be noticed, which is the case nobody needs warning
        # about.
        band = _synthetic_band(0.15, depth_field)
        valid = np.ones(SHAPE, dtype=bool)
        correct, *_ = attenuation.fit_band_attenuation(
            band, depth_field, valid, DEEP_REFERENCE, quantile=0.90
        )
        inflated, _i, r2, *_ = attenuation.fit_band_attenuation(
            band, depth_field, valid, DEEP_REFERENCE * 1.5, quantile=0.90
        )
        assert inflated > correct * 1.05
        assert r2 > 0.99  # still looks like a good fit


class TestFitGuards:
    def test_too_few_usable_pixels_returns_nan(self, depth_field: np.ndarray) -> None:
        band = _synthetic_band(0.15, depth_field)
        valid = np.zeros(SHAPE, dtype=bool)
        valid[0, :5] = True
        k, intercept, r2, n_bins, n_px = attenuation.fit_band_attenuation(
            band, depth_field, valid, DEEP_REFERENCE, quantile=0.90
        )
        assert np.isnan(k) and np.isnan(intercept) and np.isnan(r2)
        assert n_bins == 0
        assert n_px <= 5

    def test_too_few_depth_bins_returns_nan_but_reports_the_count(
        self, depth_field: np.ndarray
    ) -> None:
        band = _synthetic_band(0.15, depth_field)
        valid = np.ones(SHAPE, dtype=bool)
        narrow = AttenuationConfig(depth_min_m=5.0, depth_max_m=7.0)
        k, _i, _r2, n_bins, n_px = attenuation.fit_band_attenuation(
            band, depth_field, valid, DEEP_REFERENCE, 0.90, config=narrow
        )
        assert np.isnan(k)
        assert n_bins < 4
        assert n_px > 0

    def test_pixels_with_no_bottom_signal_are_excluded(self, depth_field: np.ndarray) -> None:
        # Residual <= 0 means the bottom signal is gone; ln() is undefined
        # there and the pixels must be dropped rather than clamped.
        band = np.full(SHAPE, DEEP_REFERENCE - 0.001)
        valid = np.ones(SHAPE, dtype=bool)
        k, *_, n_px = attenuation.fit_band_attenuation(
            band, depth_field, valid, DEEP_REFERENCE, quantile=0.90
        )
        assert np.isnan(k)
        assert n_px == 0

    def test_depth_window_is_respected(self, depth_field: np.ndarray) -> None:
        band = _synthetic_band(0.15, depth_field)
        valid = np.ones(SHAPE, dtype=bool)
        wide = attenuation.fit_band_attenuation(band, depth_field, valid, DEEP_REFERENCE, 0.90)
        narrow = attenuation.fit_band_attenuation(
            band,
            depth_field,
            valid,
            DEEP_REFERENCE,
            0.90,
            config=AttenuationConfig(depth_min_m=5.0, depth_max_m=15.0),
        )
        assert narrow[4] < wide[4]  # fewer pixels
        assert narrow[0] == pytest.approx(wide[0], rel=0.05)  # same k


class TestPureWaterFloor:
    def test_a_physical_fit_sits_above_the_floor(self) -> None:
        calibration = attenuation.BandCalibration(
            wavelength_nm=489.0,
            k_per_m=0.11,
            intercept=0.0,
            r_squared=0.99,
            n_bins=10,
            n_pixels=5000,
            depth_range_m=(1.0, 20.0),
            deep_water_reference=DEEP_REFERENCE,
            frac_nonpositive_residual=0.0,
        )
        assert not calibration.k_below_pure_water_floor
        assert calibration.k_effective == pytest.approx(0.11)

    def test_a_sub_floor_fit_is_flagged_and_floored_not_silently_used(
        self,
    ) -> None:
        calibration = attenuation.BandCalibration(
            wavelength_nm=489.0,
            k_per_m=0.001,  # below pure water at 489 nm
            intercept=0.0,
            r_squared=0.99,
            n_bins=10,
            n_pixels=5000,
            depth_range_m=(1.0, 20.0),
            deep_water_reference=DEEP_REFERENCE,
            frac_nonpositive_residual=0.0,
        )
        assert calibration.k_below_pure_water_floor
        assert calibration.k_effective == calibration.k_pure_water_floor
        # The raw fit is preserved so the failure stays auditable.
        assert calibration.k_per_m == 0.001

    def test_a_nan_fit_stays_nan(self) -> None:
        calibration = attenuation.BandCalibration(
            wavelength_nm=489.0,
            k_per_m=float("nan"),
            intercept=float("nan"),
            r_squared=float("nan"),
            n_bins=0,
            n_pixels=0,
            depth_range_m=(1.0, 20.0),
            deep_water_reference=DEEP_REFERENCE,
            frac_nonpositive_residual=float("nan"),
        )
        assert not calibration.k_below_pure_water_floor
        assert np.isnan(calibration.k_effective)


class TestCalibrateBands:
    def test_fits_every_band_and_reports_sensitivity(
        self, depth_field: np.ndarray, deep_mask: np.ndarray
    ) -> None:
        truth = {444.0: 0.11, 489.0: 0.08, 561.0: 0.09}
        reflectance = {
            wl: _synthetic_band(k, depth_field, noise=5e-5, seed=int(wl)) for wl, k in truth.items()
        }
        water = np.ones(SHAPE, dtype=bool)

        out = attenuation.calibrate_bands(reflectance, depth_field, water, deep_mask)

        assert set(out) == set(truth)
        for wl, k_true in truth.items():
            assert out[wl].k_per_m == pytest.approx(k_true, rel=0.15)
            assert out[wl].wavelength_nm == wl
            assert out[wl].depth_range_m == (1.0, 20.0)
            # The quantile sweep is always emitted — it is not optional.
            assert len(out[wl].k_by_quantile) == 5
            assert np.isfinite(out[wl].quantile_spread)

    def test_quantile_spread_is_small_for_a_uniform_bottom(
        self, depth_field: np.ndarray, deep_mask: np.ndarray
    ) -> None:
        # One substrate everywhere is the case the tracker is designed for,
        # so the free parameter should barely matter.
        reflectance = {489.0: _synthetic_band(0.12, depth_field)}
        water = np.ones(SHAPE, dtype=bool)
        out = attenuation.calibrate_bands(reflectance, depth_field, water, deep_mask)
        assert out[489.0].quantile_spread < 0.01

    def test_nonpositive_residual_fraction_is_reported(
        self, depth_field: np.ndarray, deep_mask: np.ndarray
    ) -> None:
        band = _synthetic_band(0.15, depth_field)
        band[:20, :] = DEEP_REFERENCE - 0.001  # no bottom signal here
        water = np.ones(SHAPE, dtype=bool)
        out = attenuation.calibrate_bands({489.0: band}, depth_field, water, deep_mask)
        # 20 forced rows plus the 10 genuinely deep rows, which sit at the
        # reference and so also fail the strictly-positive test.
        assert out[489.0].frac_nonpositive_residual == pytest.approx(30 / SHAPE[0], abs=0.02)


class TestLyzengaRatios:
    def test_ratios_are_emitted_for_every_pair(self) -> None:
        calibrations = {
            wl: attenuation.BandCalibration(
                wavelength_nm=wl,
                k_per_m=k,
                intercept=0.0,
                r_squared=0.99,
                n_bins=10,
                n_pixels=5000,
                depth_range_m=(1.0, 20.0),
                deep_water_reference=DEEP_REFERENCE,
                frac_nonpositive_residual=0.0,
            )
            for wl, k in {444.0: 0.12, 489.0: 0.08, 561.0: 0.06}.items()
        }
        ratios = attenuation.lyzenga_ratios(calibrations)
        assert set(ratios) == {"444/489", "444/561", "489/561"}
        assert ratios["444/489"] == pytest.approx(1.5)

    def test_non_finite_bands_are_skipped(self) -> None:
        calibrations = {
            444.0: attenuation.BandCalibration(
                wavelength_nm=444.0,
                k_per_m=float("nan"),
                intercept=0.0,
                r_squared=0.0,
                n_bins=0,
                n_pixels=0,
                depth_range_m=(1.0, 20.0),
                deep_water_reference=DEEP_REFERENCE,
                frac_nonpositive_residual=0.0,
            ),
            489.0: attenuation.BandCalibration(
                wavelength_nm=489.0,
                k_per_m=0.08,
                intercept=0.0,
                r_squared=0.99,
                n_bins=10,
                n_pixels=5000,
                depth_range_m=(1.0, 20.0),
                deep_water_reference=DEEP_REFERENCE,
                frac_nonpositive_residual=0.0,
            ),
        }
        assert attenuation.lyzenga_ratios(calibrations) == {}
