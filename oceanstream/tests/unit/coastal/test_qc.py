"""Tests for the Phase 1.6 QC diagnostics.

These modules exist to keep a retrieval auditable, so the tests are mostly
about honesty properties rather than numerical accuracy: a uniform offset must
not change the discrimination threshold, an implausible retrieval must be
flagged rather than censored, and a missing band must fail loudly instead of
silently substituting a nearby one.
"""

from __future__ import annotations

import numpy as np
import pytest

from oceanstream.coastal.config import QCConfig
from oceanstream.coastal.qc import ac_uncertainty, point_diagnostic

SHAPE = (100, 100)
SOLAR_ZENITH_DEG = 21.0


# ---------------------------------------------------------------------------
# Atmospheric-correction uncertainty
# ---------------------------------------------------------------------------


class TestNearestBand:
    def test_matches_a_band_that_drifted_a_few_nm(self) -> None:
        bands = {444.0: np.zeros(1), 561.0: np.zeros(1), 665.0: np.zeros(1)}
        assert ac_uncertainty.nearest_band(bands, 667.0) == 665.0

    def test_returns_none_when_nothing_is_close_enough(self) -> None:
        # Silently substituting 561 for a requested 667 would swap a
        # bottom-blind band for one that carries substrate signal.
        bands = {444.0: np.zeros(1), 561.0: np.zeros(1)}
        assert ac_uncertainty.nearest_band(bands, 667.0) is None

    def test_tolerance_is_configurable(self) -> None:
        bands = {650.0: np.zeros(1)}
        assert ac_uncertainty.nearest_band(bands, 667.0) is None
        assert ac_uncertainty.nearest_band(bands, 667.0, 20.0) == 650.0

    def test_empty_input_returns_none(self) -> None:
        assert ac_uncertainty.nearest_band({}, 667.0) is None


class TestWithinBlockScatter:
    def test_measures_local_scatter_not_scene_scatter(self) -> None:
        # A large-scale gradient with no local noise: scene-wide SD is big,
        # within-block SD is near zero. Only the latter limits a classifier.
        values = np.repeat(np.linspace(0.0, 1.0, SHAPE[0])[:, None], SHAPE[1], axis=1)
        mask = np.ones(SHAPE, dtype=bool)
        out = ac_uncertainty.within_block_scatter(values, mask, block_px=10)
        assert float(np.std(values)) > 0.25
        assert out["median_sd_rhos"] < 0.05

    def test_recovers_a_known_noise_level(self) -> None:
        rng = np.random.default_rng(0)
        values = rng.normal(0.01, 0.002, SHAPE)
        mask = np.ones(SHAPE, dtype=bool)
        out = ac_uncertainty.within_block_scatter(values, mask, block_px=10)
        assert out["median_sd_rhos"] == pytest.approx(0.002, rel=0.20)
        assert out["n_blocks"] == 100

    def test_sparse_blocks_are_skipped(self) -> None:
        rng = np.random.default_rng(1)
        values = rng.normal(0.01, 0.002, SHAPE)
        mask = np.zeros(SHAPE, dtype=bool)
        mask[:10, :10] = True  # exactly one full block
        out = ac_uncertainty.within_block_scatter(values, mask, block_px=10)
        assert out["n_blocks"] == 1

    def test_no_usable_block_reports_none_rather_than_zero(self) -> None:
        values = np.zeros(SHAPE)
        mask = np.zeros(SHAPE, dtype=bool)
        out = ac_uncertainty.within_block_scatter(values, mask, block_px=10)
        assert out == {"n_blocks": 0, "median_sd_rhos": None}

    def test_block_size_is_reported_in_metres(self) -> None:
        values = np.zeros(SHAPE)
        mask = np.ones(SHAPE, dtype=bool)
        out = ac_uncertainty.within_block_scatter(values, mask, block_px=10, pixel_size_m=2.0)
        assert out["block_size_m"] == 20.0


@pytest.fixture
def noisy_scene() -> tuple[dict[float, np.ndarray], np.ndarray, np.ndarray]:
    """Bottom-blind red with pure noise, green with noise plus real structure."""
    rng = np.random.default_rng(42)
    red = rng.normal(0.008, 0.0015, SHAPE)
    green = rng.normal(0.020, 0.0015, SHAPE)
    # Substrate patches: a real signal green carries and red cannot.
    green[::2, :] += 0.004
    depth = np.full(SHAPE, 15.0)
    return {667.0: red, 561.0: green}, depth, np.ones(SHAPE, dtype=bool)


class TestEffectiveThreshold:
    def test_threshold_tracks_the_bottom_blind_scatter(
        self, noisy_scene: tuple[dict[float, np.ndarray], np.ndarray, np.ndarray]
    ) -> None:
        reflectance, depth, water = noisy_scene
        out = ac_uncertainty.effective_threshold_rhos(reflectance, depth, water)
        assert out["status"] == "diagnostic_only"
        assert out["effective_threshold_rhos"] == pytest.approx(0.0015, rel=0.25)

    def test_a_uniform_offset_does_not_move_the_threshold(
        self, noisy_scene: tuple[dict[float, np.ndarray], np.ndarray, np.ndarray]
    ) -> None:
        # This is the whole argument of the module: a uniform additive offset
        # cancels between neighbouring pixels and so cannot limit
        # discrimination. If it moved the threshold, the module would be
        # measuring the wrong quantity.
        reflectance, depth, water = noisy_scene
        plain = ac_uncertainty.effective_threshold_rhos(reflectance, depth, water)
        shifted = {wl: band + 0.00555 for wl, band in reflectance.items()}
        offset = ac_uncertainty.effective_threshold_rhos(
            shifted, depth, water, additive_offset=0.00555
        )
        assert offset["effective_threshold_rhos"] == pytest.approx(
            plain["effective_threshold_rhos"]
        )

    def test_detects_that_green_carries_substrate_signal(
        self, noisy_scene: tuple[dict[float, np.ndarray], np.ndarray, np.ndarray]
    ) -> None:
        reflectance, depth, water = noisy_scene
        out = ac_uncertainty.effective_threshold_rhos(reflectance, depth, water)
        interp = out["interpretation"]
        assert interp["green_carries_substrate_signal"] is True
        assert interp["green_excess_over_ac"] > 0

    def test_pure_noise_in_green_reports_no_substrate_signal(self) -> None:
        rng = np.random.default_rng(7)
        reflectance = {
            667.0: rng.normal(0.008, 0.0015, SHAPE),
            561.0: rng.normal(0.020, 0.0015, SHAPE),
        }
        out = ac_uncertainty.effective_threshold_rhos(
            reflectance, np.full(SHAPE, 15.0), np.ones(SHAPE, dtype=bool)
        )
        assert out["interpretation"]["green_carries_substrate_signal"] is False

    def test_shallow_scene_has_no_bottom_blind_pixels(
        self, noisy_scene: tuple[dict[float, np.ndarray], np.ndarray, np.ndarray]
    ) -> None:
        reflectance, _depth, water = noisy_scene
        out = ac_uncertainty.effective_threshold_rhos(reflectance, np.full(SHAPE, 3.0), water)
        assert out["status"] == "insufficient_data"
        assert out["n_bottom_blind_pixels"] == 0
        assert "effective_threshold_rhos" not in out

    def test_missing_band_is_reported_not_substituted(
        self, noisy_scene: tuple[dict[float, np.ndarray], np.ndarray, np.ndarray]
    ) -> None:
        reflectance, depth, water = noisy_scene
        out = ac_uncertainty.effective_threshold_rhos({561.0: reflectance[561.0]}, depth, water)
        assert out["status"] == "missing_band"
        assert "effective_threshold_rhos" not in out

    def test_pure_water_transmission_confirms_the_band_is_blind(
        self, noisy_scene: tuple[dict[float, np.ndarray], np.ndarray, np.ndarray]
    ) -> None:
        reflectance, depth, water = noisy_scene
        out = ac_uncertainty.effective_threshold_rhos(reflectance, depth, water)
        blind = out["bands"]["bottom_blind"]
        transmission = float(blind["pure_water_transmission_at_blind_depth"])
        assert transmission < 1e-3

    def test_block_sizes_scale_with_pixel_size(
        self, noisy_scene: tuple[dict[float, np.ndarray], np.ndarray, np.ndarray]
    ) -> None:
        reflectance, depth, water = noisy_scene
        cfg = QCConfig(scatter_block_sizes_m=(100.0,))
        out = ac_uncertainty.effective_threshold_rhos(
            reflectance, depth, water, pixel_size_m=5.0, config=cfg
        )
        # 100 m at 5 m GSD is a 20 px block, still labelled 100 m.
        assert "100m" in out["bands"]["bottom_blind"]["by_block"]
        assert out["bands"]["bottom_blind"]["by_block"]["100m"]["n_blocks"] == 25


# ---------------------------------------------------------------------------
# Point diagnostic
# ---------------------------------------------------------------------------

# Roughly Sesimbra green: moderately clear coastal water.
A_GREEN = 0.10
BB_GREEN = 0.004


class TestInvertUncensored:
    def test_returns_the_terms_behind_the_answer(self) -> None:
        out = point_diagnostic.invert_uncensored(0.010, 8.0, A_GREEN, BB_GREEN, SOLAR_ZENITH_DEG)
        for key in (
            "rho_b_uncensored",
            "column_term",
            "bottom_residual",
            "transmission",
            "amplification",
            "k_b",
            "k_c",
            "rrs_deep",
        ):
            assert key in out

    def test_a_negative_retrieval_is_flagged_not_censored(self) -> None:
        # Reflectance below the deep-water asymptote over-subtracts the
        # column. The production inversion returns NaN here; the diagnostic
        # must return the number, because the number is the evidence.
        out = point_diagnostic.invert_uncensored(0.0005, 15.0, A_GREEN, BB_GREEN, SOLAR_ZENITH_DEG)
        assert out["rho_b_uncensored"] < 0
        assert not np.isnan(out["rho_b_uncensored"])
        assert out["is_negative"] is True
        assert out["is_plausible"] is False
        assert out["flag"] == "negative_column_over_subtracted"

    def test_an_implausibly_bright_retrieval_is_flagged(self) -> None:
        out = point_diagnostic.invert_uncensored(0.20, 18.0, A_GREEN, BB_GREEN, SOLAR_ZENITH_DEG)
        assert out["flag"] == "implausibly_bright"
        assert out["is_plausible"] is False

    def test_a_reasonable_retrieval_is_ok(self) -> None:
        out = point_diagnostic.invert_uncensored(0.012, 6.0, A_GREEN, BB_GREEN, SOLAR_ZENITH_DEG)
        assert out["flag"] == "ok"
        assert out["is_plausible"] is True

    def test_amplification_grows_with_depth(self) -> None:
        # exp(+k_b * H) is why depth error dominates at depth.
        shallow = point_diagnostic.invert_uncensored(
            0.012, 5.0, A_GREEN, BB_GREEN, SOLAR_ZENITH_DEG
        )
        deep = point_diagnostic.invert_uncensored(0.012, 20.0, A_GREEN, BB_GREEN, SOLAR_ZENITH_DEG)
        assert deep["amplification"] > shallow["amplification"]
        assert deep["transmission"] < shallow["transmission"]

    def test_the_plausible_window_is_configurable(self) -> None:
        strict = QCConfig(plausible_rho_b=(0.0, 0.01))
        out = point_diagnostic.invert_uncensored(
            0.012, 6.0, A_GREEN, BB_GREEN, SOLAR_ZENITH_DEG, config=strict
        )
        assert out["flag"] == "implausibly_bright"


class TestSensitivity:
    def test_sweeps_both_error_sources(self) -> None:
        out = point_diagnostic.point_diagnostic(0.012, 10.0, A_GREEN, BB_GREEN, SOLAR_ZENITH_DEG)
        assert set(out["by_depth_error"]) == {"-2m", "-1m", "+0m", "+1m", "+2m"}
        assert set(out["by_reflectance_offset"]) == {
            "-0.001",
            "+0.000",
            "+0.001",
        }
        assert out["dominant_error_source"] in ("depth", "reflectance_offset")

    def test_depth_dominates_in_deep_water(self) -> None:
        out = point_diagnostic.point_diagnostic(0.012, 18.0, A_GREEN, BB_GREEN, SOLAR_ZENITH_DEG)
        assert out["dominant_error_source"] == "depth"

    def test_spread_grows_with_depth(self) -> None:
        shallow = point_diagnostic.point_diagnostic(0.012, 5.0, A_GREEN, BB_GREEN, SOLAR_ZENITH_DEG)
        deep = point_diagnostic.point_diagnostic(0.012, 18.0, A_GREEN, BB_GREEN, SOLAR_ZENITH_DEG)
        assert deep["spread_from_depth"] > shallow["spread_from_depth"]

    def test_perturbations_never_go_to_nonpositive_depth(self) -> None:
        out = point_diagnostic.point_diagnostic(0.012, 1.5, A_GREEN, BB_GREEN, SOLAR_ZENITH_DEG)
        assert "-2m" not in out["by_depth_error"]
        assert "-1m" in out["by_depth_error"]

    def test_baseline_matches_the_zero_perturbation_entry(self) -> None:
        out = point_diagnostic.point_diagnostic(0.012, 10.0, A_GREEN, BB_GREEN, SOLAR_ZENITH_DEG)
        assert out["baseline_rho_b"] == out["by_depth_error"]["+0m"]
        assert out["baseline_rho_b"] == out["by_reflectance_offset"]["+0.000"]
