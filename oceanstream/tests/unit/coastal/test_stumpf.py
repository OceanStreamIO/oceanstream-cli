"""Tests for the Phase 1.3 Stumpf bathymetry port.

Two properties matter more than the numbers here. First, the log ratio must
return NaN near the pole at ``n * R = 1`` rather than diverging — the
prototype's original failure mode was a depth map full of hundreds of metres.
Second, every guard that stops a degenerate fit from being reported as a good
one must actually fire, because a collapsed Stumpf map validates beautifully
against an imbalanced reference set.
"""

from __future__ import annotations

import numpy as np
import pytest

from oceanstream.coastal.bathymetry import stumpf
from oceanstream.coastal.config import BathymetryConfig

SHAPE = (60, 60)
TRUE_M0 = 12.0
TRUE_M1 = 20.0


def _synthetic_scene(
    n: float = 1000.0,
    depth_range_m: tuple[float, float] = (3.0, 24.0),
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Blue/green reflectance whose log ratio is linear in a known depth field.

    Built by inversion: pick the depth, derive the ratio the Stumpf model
    implies, then choose reflectances that produce it. Green is held constant
    and blue is solved for, so the recovered (m0, m1) must match the inputs.
    """
    lo, hi = depth_range_m
    depth = np.linspace(lo, hi, SHAPE[0] * SHAPE[1]).reshape(SHAPE)
    ratio = (depth + TRUE_M0) / TRUE_M1

    green = np.full(SHAPE, 0.02, dtype=np.float64)
    log_green = np.log(n * green)
    blue = np.exp(ratio * log_green) / n
    return blue.astype(np.float32), green.astype(np.float32), depth


class TestLogRatio:
    def test_pixels_on_the_pole_become_nan(self) -> None:
        # n * R just above 1 sits inside the pole guard.
        blue = np.array([[1.0001e-3]], dtype=np.float32)
        green = np.array([[0.02]], dtype=np.float32)
        assert np.isnan(stumpf.stumpf_ratio(blue, green, n=1000.0)).all()

    def test_ratios_outside_the_plausible_range_become_nan(self) -> None:
        blue, green, _ = _synthetic_scene()
        tight = BathymetryConfig(ratio_valid_range=(0.99, 1.01))
        ratio = stumpf.stumpf_ratio(blue, green, n=1000.0, config=tight)

        survivors = ratio[np.isfinite(ratio)]
        assert np.isnan(ratio).mean() > 0.9
        assert survivors.size > 0
        assert ((survivors > 0.99) & (survivors < 1.01)).all()

    def test_no_infinities_ever_escape(self) -> None:
        blue = np.array([[0.0, 1e-9, 0.02, 0.5]], dtype=np.float32)
        green = np.array([[0.02, 0.02, 0.02, 1e-9]], dtype=np.float32)
        ratio = stumpf.stumpf_ratio(blue, green, n=1000.0)
        assert not np.isinf(ratio).any()


class TestChooseRatioScale:
    def test_dark_water_forces_n_above_the_convention(self) -> None:
        dark = np.full(SHAPE, 5e-4, dtype=np.float32)
        valid = np.ones(SHAPE, dtype=bool)
        assert stumpf.choose_ratio_scale(dark, dark, valid) > 1000.0

    def test_bright_water_keeps_the_conventional_n(self) -> None:
        bright = np.full(SHAPE, 0.05, dtype=np.float32)
        valid = np.ones(SHAPE, dtype=bool)
        assert stumpf.choose_ratio_scale(bright, bright, valid) == 1000.0

    def test_too_few_samples_falls_back(self) -> None:
        arr = np.full(SHAPE, 5e-4, dtype=np.float32)
        valid = np.zeros(SHAPE, dtype=bool)
        assert stumpf.choose_ratio_scale(arr, arr, valid) == 1000.0


class TestFitAgainstAReferenceRaster:
    def test_recovers_the_generating_coefficients(self) -> None:
        blue, green, depth = _synthetic_scene()
        valid = np.ones(SHAPE, dtype=bool)
        fit = stumpf.fit_stumpf(blue, green, depth, valid, n=1000.0)

        assert fit.m1 == pytest.approx(TRUE_M1, rel=1e-3)
        assert fit.m0 == pytest.approx(TRUE_M0, rel=1e-3)
        assert fit.mae_m < 0.05

    def test_too_few_pixels_is_rejected_with_a_usable_message(self) -> None:
        blue, green, depth = _synthetic_scene()
        valid = np.zeros(SHAPE, dtype=bool)
        valid[0, :10] = True
        with pytest.raises(ValueError, match="need at least 200"):
            stumpf.fit_stumpf(blue, green, depth, valid, n=1000.0)

    def test_errors_come_from_the_held_out_split(self) -> None:
        blue, green, depth = _synthetic_scene()
        rng = np.random.default_rng(0)
        noisy = depth + rng.normal(0.0, 1.0, SHAPE)
        valid = np.ones(SHAPE, dtype=bool)
        fit = stumpf.fit_stumpf(blue, green, noisy, valid, n=1000.0)
        # An in-sample bias would be exactly zero by construction.
        assert fit.bias_m != 0.0
        assert fit.rmse_m == pytest.approx(1.0, abs=0.2)


class TestApply:
    def test_round_trips_the_depth_field(self) -> None:
        blue, green, depth = _synthetic_scene()
        valid = np.ones(SHAPE, dtype=bool)
        fit = stumpf.fit_stumpf(blue, green, depth, valid, n=1000.0)
        retrieved = stumpf.apply_stumpf(blue, green, fit, valid)
        assert np.nanmax(np.abs(retrieved - depth)) < 0.2

    def test_depths_outside_the_aoi_range_are_nan_not_clipped(self) -> None:
        blue, green, depth = _synthetic_scene()
        valid = np.ones(SHAPE, dtype=bool)
        fit = stumpf.fit_stumpf(blue, green, depth, valid, n=1000.0)
        narrow = BathymetryConfig(depth_valid_range_m=(0.0, 10.0))
        retrieved = stumpf.apply_stumpf(blue, green, fit, valid, config=narrow)
        finite = retrieved[np.isfinite(retrieved)]
        assert finite.size > 0
        assert finite.max() <= 10.0
        assert np.isnan(retrieved).any()


class TestFitOnPoints:
    def _points(self, depth: np.ndarray, n_points: int = 40):
        rng = np.random.default_rng(7)
        rows = rng.integers(0, SHAPE[0], n_points)
        cols = rng.integers(0, SHAPE[1], n_points)
        return rows, cols, depth[rows, cols]

    def test_recovers_the_coefficients_from_scattered_points(self) -> None:
        blue, green, depth = _synthetic_scene()
        valid = np.ones(SHAPE, dtype=bool)
        rows, cols, depths = self._points(depth)
        fit, diag = stumpf.fit_stumpf_on_points(blue, green, valid, rows, cols, depths, n=1000.0)
        assert fit.m1 == pytest.approx(TRUE_M1, rel=1e-2)
        assert diag["validation_scheme"].startswith("in-sample")

    def test_grouped_points_get_leave_one_group_out_cv(self) -> None:
        blue, green, depth = _synthetic_scene()
        valid = np.ones(SHAPE, dtype=bool)
        rows, cols, depths = self._points(depth)
        groups = np.arange(depths.size) % 3
        _, diag = stumpf.fit_stumpf_on_points(
            blue, green, valid, rows, cols, depths, groups=groups, n=1000.0
        )
        assert diag["validation_scheme"] == "leave-one-of-3-groups-out"

    def test_pseudo_replication_is_collapsed_per_pixel(self) -> None:
        blue, green, depth = _synthetic_scene()
        valid = np.ones(SHAPE, dtype=bool)
        rows, cols, depths = self._points(depth, n_points=30)
        # Duplicate every point; the fit must not see 60 observations.
        _, diag = stumpf.fit_stumpf_on_points(
            blue,
            green,
            valid,
            np.concatenate([rows, rows]),
            np.concatenate([cols, cols]),
            np.concatenate([depths, depths]),
            n=1000.0,
        )
        assert diag["n_points"] == 60
        assert diag["n_unique_pixels"] <= 30

    def test_a_narrow_calibration_range_is_refused(self) -> None:
        blue, green, depth = _synthetic_scene(depth_range_m=(10.0, 11.0))
        valid = np.ones(SHAPE, dtype=bool)
        rows, cols, depths = self._points(depth)
        with pytest.raises(ValueError, match="span only"):
            stumpf.fit_stumpf_on_points(blue, green, valid, rows, cols, depths, n=1000.0)


class TestBlend:
    def test_blend_sits_between_the_two_inputs(self) -> None:
        stumpf_depth = np.full(SHAPE, 10.0, dtype=np.float32)
        reference = np.full(SHAPE, 20.0, dtype=np.float32)
        fit = stumpf.StumpfFit(
            m0=0.0,
            m1=1.0,
            n=1000.0,
            ref_source="test",
            n_pixels=1000,
            mae_m=1.0,
            rmse_m=1.0,
            bias_m=0.0,
        )
        blended, sigma = stumpf.blend_stumpf(
            stumpf_depth,
            reference,
            fit,
            config=BathymetryConfig(blend_smooth_sigma_px=0.0),
        )
        assert (blended > 10.0).all() and (blended < 20.0).all()
        # Stumpf sigma 1 m beats the reference's 3 m, so the blend leans on it.
        assert blended.mean() < 15.0
        # Combining two estimates must not increase the uncertainty.
        assert (sigma < 1.0).all()

    def test_a_worse_stumpf_fit_defers_to_the_reference(self) -> None:
        stumpf_depth = np.full(SHAPE, 10.0, dtype=np.float32)
        reference = np.full(SHAPE, 20.0, dtype=np.float32)
        bad = stumpf.StumpfFit(
            m0=0.0,
            m1=1.0,
            n=1000.0,
            ref_source="test",
            n_pixels=1000,
            mae_m=9.0,
            rmse_m=9.0,
            bias_m=0.0,
        )
        blended, _ = stumpf.blend_stumpf(
            stumpf_depth,
            reference,
            bad,
            config=BathymetryConfig(blend_smooth_sigma_px=0.0),
        )
        assert blended.mean() > 15.0


class TestDiagnostics:
    def test_a_collapsed_map_is_flagged(self) -> None:
        flat = np.full(SHAPE, 12.0, dtype=np.float32)
        diag = stumpf.depth_map_diagnostics(flat, calibration_span_m=20.0)
        assert diag["collapsed"] is True
        assert "near zero" in diag["reason"]

    def test_a_varying_map_is_not_flagged(self) -> None:
        _, _, depth = _synthetic_scene()
        diag = stumpf.depth_map_diagnostics(depth.astype(np.float32), calibration_span_m=20.0)
        assert diag["collapsed"] is False
        assert diag["reason"] == ""
        assert diag["coverage_frac"] == 1.0

    def test_an_empty_map_is_collapsed_by_definition(self) -> None:
        empty = np.full(SHAPE, np.nan, dtype=np.float32)
        diag = stumpf.depth_map_diagnostics(empty, calibration_span_m=20.0)
        assert diag["collapsed"] is True
        assert diag["coverage_frac"] == 0.0

    def test_stratum_imbalance_is_flagged(self) -> None:
        validation = [
            stumpf.DepthValidation("shallow (<12 m)", 90, 1.0, 1.0, 0.0),
            stumpf.DepthValidation("deep (>=12 m)", 10, 1.0, 1.0, 0.0),
            stumpf.DepthValidation("all points", 100, 1.0, 1.0, 0.0),
        ]
        balance = stumpf.stratum_balance(validation)
        assert balance["balanced"] is False
        assert balance["dominant_frac"] == pytest.approx(0.9)
        # The pooled stratum must not be counted as a stratum.
        assert "all points" not in balance["counts"]

    def test_a_balanced_set_passes(self) -> None:
        validation = [
            stumpf.DepthValidation("shallow (<12 m)", 55, 1.0, 1.0, 0.0),
            stumpf.DepthValidation("deep (>=12 m)", 45, 1.0, 1.0, 0.0),
        ]
        assert stumpf.stratum_balance(validation)["balanced"] is True


class TestValidation:
    def test_scores_split_into_strata(self) -> None:
        predicted = np.full(SHAPE, 10.0, dtype=np.float32)
        rows = np.array([0, 1, 2, 3])
        cols = np.array([0, 1, 2, 3])
        measured = np.array([8.0, 9.0, 15.0, 20.0])
        results = stumpf.validate_against_points(predicted, rows, cols, measured)

        by_label = {r.stratum_label: r for r in results}
        assert len(results) == 3
        shallow = next(r for r in results if r.stratum_label.startswith("shallow"))
        deep = next(r for r in results if r.stratum_label.startswith("deep"))
        assert shallow.n == 2
        assert deep.n == 2
        assert shallow.bias_m == pytest.approx(1.5)  # over-predicts shallow
        assert deep.bias_m == pytest.approx(-7.5)  # under-predicts deep
        assert by_label["all points"].n == 4

    def test_empty_strata_report_nan_not_zero(self) -> None:
        predicted = np.full(SHAPE, 10.0, dtype=np.float32)
        rows = np.array([0, 1])
        cols = np.array([0, 1])
        measured = np.array([8.0, 9.0])  # both shallow
        results = stumpf.validate_against_points(predicted, rows, cols, measured)
        deep = next(r for r in results if r.stratum_label.startswith("deep"))
        assert deep.n == 0
        assert np.isnan(deep.mae_m)


class TestBlockedValidation:
    def test_checkerboard_splits_roughly_in_half(self) -> None:
        blocks = stumpf.checkerboard_blocks(
            (200, 200), config=BathymetryConfig(checkerboard_block_px=50)
        )
        assert blocks.mean() == pytest.approx(0.5)

    def test_validation_scores_only_the_held_out_blocks(self) -> None:
        _, _, depth = _synthetic_scene()
        predicted = depth.astype(np.float32) + 1.0
        valid = np.ones(SHAPE, dtype=bool)
        blocks = stumpf.checkerboard_blocks(
            SHAPE, config=BathymetryConfig(checkerboard_block_px=10)
        )
        results = stumpf.validate_against_reference_blocks(predicted, depth, valid, blocks)
        pooled = next(r for r in results if r.stratum_label == "all held-out blocks")
        assert pooled.bias_m == pytest.approx(1.0, abs=1e-4)
        assert pooled.n < int(valid.sum())

    def test_a_mismatched_checkerboard_is_refused(self) -> None:
        _, _, depth = _synthetic_scene()
        valid = np.ones(SHAPE, dtype=bool)
        with pytest.raises(ValueError, match="does not match"):
            stumpf.validate_against_reference_blocks(
                depth.astype(np.float32),
                depth,
                valid,
                np.ones((10, 10), dtype=bool),
            )
