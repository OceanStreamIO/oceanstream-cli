"""Tests for the Phase 3.1-3.2 detectability products.

These are written against behaviour rather than against the plan, which is the
convention that has caught every latent defect in this port so far. The
properties that carry weight here:

* z_max must be *logarithmic* in the assumptions and *inverse-linear* in k. If
  that ever inverts, the sensitivity figures shipped alongside it become lies.
* A fitted k below the pure-water floor must produce a flagged upper bound, not
  a silently large z_max. That is the failure mode the Sesimbra scene actually
  exhibits.
* K_d interpolation must reconstruct the pure-water absorption edge past 600 nm
  from bands that stop at 561 nm. Interpolating the total misses it entirely,
  and the resulting seabed PAR is too high by a wide margin.
"""

from __future__ import annotations

import numpy as np
import pytest

from oceanstream.coastal import detectability as det
from oceanstream.coastal.config import DetectabilityConfig
from oceanstream.coastal.inversion import lee
from oceanstream.coastal.optics import water

# Sesimbra 2026-06-27, the scene the whole port is calibrated against.
MEASURED_K = {444.0: 0.1085, 489.0: 0.0778, 561.0: 0.0802, 667.0: 0.0772}
MEASURED_EPSILON = 0.003659  # 100 m block AC scatter
SOLAR_ZENITH_DEG = 21.0
VISIBLE_NM = (444.0, 489.0, 561.0)


class TestSubstrateContrast:
    def test_bare_versus_algae_is_the_large_contrast(self) -> None:
        bare_brown = det.substrate_contrast(("bare", "brown"), 561.0)
        brown_red = det.substrate_contrast(("brown", "red"), 561.0)
        # The prototype's headline: bare-vs-algae clears the noise floor by
        # roughly an order of magnitude, kelp-vs-turf barely at all.
        assert bare_brown > 8 * brown_red

    def test_contrast_is_symmetric(self) -> None:
        assert det.substrate_contrast(("bare", "red"), 489.0) == pytest.approx(
            det.substrate_contrast(("red", "bare"), 489.0)
        )

    def test_band_outside_the_library_returns_nan_not_a_neighbour(self) -> None:
        # 865 nm is a real Sentinel-2 band. Silently reusing the 667 nm contrast
        # there would fabricate a NIR detectability that cannot exist.
        assert np.isnan(det.substrate_contrast(("bare", "brown"), 865.0))

    def test_unknown_group_names_the_alternatives(self) -> None:
        with pytest.raises(KeyError, match="delta_rho_b"):
            det.substrate_contrast(("bare", "seagrass"), 561.0)


class TestZMaxFromK:
    def test_matches_the_closed_form(self) -> None:
        k, eps, contrast, t_aw = 0.1, 0.002, 0.19, 0.52
        assert det.z_max_from_k(k, eps, contrast, t_aw) == pytest.approx(
            np.log(t_aw * contrast / eps) / k
        )

    def test_round_trips_through_the_forward_attenuation(self) -> None:
        """At z_max the attenuated contrast must equal the noise floor."""
        k, eps, contrast = 0.08, 0.0037, 0.19
        z = float(det.z_max_from_k(k, eps, contrast))
        assert 0.52 * contrast * np.exp(-k * z) == pytest.approx(eps)

    def test_doubling_contrast_adds_log2_over_k(self) -> None:
        k, eps, contrast = 0.1085, 0.003659, 0.16
        base = float(det.z_max_from_k(k, eps, contrast))
        doubled = float(det.z_max_from_k(k, eps, 2 * contrast))
        assert doubled - base == pytest.approx(np.log(2.0) / k)

    def test_halving_k_doubles_z_max(self) -> None:
        eps, contrast = 0.003, 0.19
        assert float(det.z_max_from_k(0.05, eps, contrast)) == pytest.approx(
            2 * float(det.z_max_from_k(0.10, eps, contrast))
        )

    def test_k_dominates_the_assumptions(self) -> None:
        """The point of shipping sensitivities: a 30% k error outweighs a 2x
        contrast error at Sesimbra's attenuation."""
        eps, contrast = MEASURED_EPSILON, 0.19
        base = float(det.z_max_from_k(0.1085, eps, contrast))
        from_contrast = base - float(det.z_max_from_k(0.1085, eps, contrast / 2))
        from_k = float(det.z_max_from_k(0.1085 * 0.7, eps, contrast)) - base
        assert from_k > from_contrast

    def test_contrast_below_the_noise_floor_clamps_at_zero(self) -> None:
        # Not negative: a negative depth would sort as "shallowest" wherever
        # z_max is compared or reduced.
        assert float(det.z_max_from_k(0.1, 0.05, 0.001)) == 0.0

    @pytest.mark.parametrize(
        ("k", "eps", "contrast"),
        [(0.0, 0.002, 0.19), (-0.1, 0.002, 0.19), (0.1, 0.0, 0.19), (0.1, 0.002, 0.0)],
    )
    def test_non_physical_inputs_return_nan(
        self, k: float, eps: float, contrast: float
    ) -> None:
        assert np.isnan(float(det.z_max_from_k(k, eps, contrast)))

    def test_vectorises_over_a_spatially_varying_epsilon(self) -> None:
        eps = np.array([[0.001, 0.002], [0.004, 0.008]])
        z = det.z_max_from_k(0.1, eps, 0.19)
        assert isinstance(z, np.ndarray)
        assert z.shape == eps.shape
        # Each doubling of the noise floor costs the same ln(2)/k.
        steps = np.diff(z.ravel())
        assert np.allclose(steps, -np.log(2.0) / 0.1)


class TestMargin:
    def test_margin_is_signed_distance_to_the_limit(self) -> None:
        depth = np.array([5.0, 20.0])
        assert np.allclose(det.detectability_margin(15.0, depth), [10.0, -5.0])

    def test_unknown_depth_is_never_detectable(self) -> None:
        depth = np.array([np.nan, -1.0, 5.0])
        mask = det.detectable_mask(15.0, depth)
        assert mask.tolist() == [False, False, True]

    def test_spatially_varying_z_max_gates_per_pixel(self) -> None:
        z_max = np.array([[30.0, 5.0]])
        depth = np.array([[10.0, 10.0]])
        assert det.detectable_mask(z_max, depth).tolist() == [[True, False]]


class TestBandDetectability:
    def test_a_healthy_band_is_usable_and_unflagged(self) -> None:
        band = det.band_detectability(489.0, 0.0778, MEASURED_EPSILON)
        assert band.usable
        assert band.flags == ()
        assert band.k_source == "empirical"

    def test_sub_floor_k_is_substituted_and_marked_an_upper_bound(self) -> None:
        """The Sesimbra 667 nm case. Substituting the floor gives the *least*
        possible attenuation and so the *most* possible z_max."""
        band = det.band_detectability(667.0, 0.0772, MEASURED_EPSILON)
        assert band.k_source == "empirical_floored_at_pure_water"
        assert "k_below_pure_water_floor" in band.flags
        assert "z_max_is_upper_bound" in band.flags
        assert not band.usable
        assert band.k_per_m == pytest.approx(lee.kb_pure_water(667.0))
        assert band.k_per_m > 0.0772

    def test_the_substituted_z_max_is_shallower_than_the_bogus_one(self) -> None:
        floored = det.band_detectability(667.0, 0.0772, MEASURED_EPSILON).z_max_m
        as_fitted = float(det.z_max_from_k(0.0772, MEASURED_EPSILON, 0.2286))
        assert floored < as_fitted

    def test_sensitivity_span_is_ln2_over_k(self) -> None:
        band = det.band_detectability(489.0, 0.0778, MEASURED_EPSILON)
        assert band.sensitivity_span_m == pytest.approx(np.log(2.0) / 0.0778)
        # Both perturbations are a factor of two, so they must agree.
        assert band.z_max_contrast_halved_m == pytest.approx(
            band.z_max_epsilon_doubled_m
        )

    def test_explicit_contrast_overrides_the_library(self) -> None:
        band = det.band_detectability(489.0, 0.08, 0.002, delta_rho_b=0.5)
        assert band.delta_rho_b == 0.5

    def test_a_band_off_the_library_grid_is_flagged_not_guessed(self) -> None:
        band = det.band_detectability(865.0, 0.5, 0.002)
        assert "no_library_contrast_at_this_band" in band.flags
        assert np.isnan(band.z_max_m)
        assert not band.usable


class TestSceneDetectability:
    def test_sesimbra_selects_blue_and_rejects_the_sub_floor_bands(self) -> None:
        scene = det.scene_detectability(
            MEASURED_K, MEASURED_EPSILON, solar_zenith_deg=SOLAR_ZENITH_DEG
        )
        assert scene.status == "ok"
        # 561 and 667 both fall below the library's pure-water floor.
        assert not scene.bands[561.0].usable
        assert not scene.bands[667.0].usable
        assert scene.best_band_nm == 489.0
        assert scene.z_max_m == pytest.approx(scene.bands[489.0].z_max_m)

    def test_the_deepest_usable_band_wins(self) -> None:
        scene = det.scene_detectability(MEASURED_K, MEASURED_EPSILON)
        usable = [b.z_max_m for b in scene.bands.values() if b.usable]
        assert scene.z_max_m == pytest.approx(max(usable))

    def test_omitting_epsilon_falls_back_and_says_so(self) -> None:
        scene = det.scene_detectability(MEASURED_K)
        assert "epsilon_not_measured" in scene.flags
        assert scene.epsilon_source == "fallback_sensor_noise"
        assert scene.epsilon_rhos == DetectabilityConfig().fallback_epsilon_rhos

    def test_the_optimistic_fallback_reports_a_deeper_limit(self) -> None:
        """Why the fallback is flagged: it is ~4x below the measured scatter,
        which buys about 12 m of z_max that the scene has not earned."""
        measured = det.scene_detectability(MEASURED_K, MEASURED_EPSILON)
        fallback = det.scene_detectability(MEASURED_K)
        assert fallback.z_max_m > measured.z_max_m + 10.0

    def test_all_bands_below_floor_gives_no_usable_band(self) -> None:
        scene = det.scene_detectability({561.0: 0.001, 667.0: 0.002}, 0.002)
        assert scene.status == "no_usable_band"
        assert scene.best_band_nm is None
        assert np.isnan(scene.z_max_m)

    def test_to_dict_is_json_serialisable_and_carries_provenance(self) -> None:
        import json

        payload = det.scene_detectability(MEASURED_K, MEASURED_EPSILON).to_dict()
        json.dumps(payload)  # must not raise
        assert payload["contrast_library"]["doi"] == det.CONTRAST_LIBRARY_DOI
        assert payload["contrast_library"]["caveats"]
        assert set(payload["bands"]) == {"444", "489", "561", "667"}


class TestDownwellingKd:
    def test_the_conversion_factor_is_near_two_but_not_two(self) -> None:
        factor = float(lee.two_way_to_downwelling_factor(0.002, 35.0))
        assert 1.9 < factor < 2.0
        assert factor != pytest.approx(2.0, abs=1e-3)

    def test_the_factor_grows_with_turbidity(self) -> None:
        clear = float(lee.two_way_to_downwelling_factor(0.002, 35.0))
        turbid = float(lee.two_way_to_downwelling_factor(0.20, 35.0))
        assert turbid > clear

    def test_the_factor_falls_with_solar_zenith(self) -> None:
        """A lower sun lengthens the downward path, so the down leg takes a
        larger share of the round trip and the ratio moves towards 1."""
        assert float(lee.two_way_to_downwelling_factor(0.002, 55.0)) < float(
            lee.two_way_to_downwelling_factor(0.002, 0.0)
        )

    def test_kd_is_roughly_half_the_two_way_k(self) -> None:
        kd = float(det.downwelling_kd(0.1, 489.0, 35.0))
        assert 0.045 < kd < 0.055

    def test_supplied_u_is_used_over_the_pure_water_assumption(self) -> None:
        assumed = float(det.downwelling_kd(0.1, 489.0, 35.0))
        turbid = float(det.downwelling_kd(0.1, 489.0, 35.0, u=0.3))
        assert turbid < assumed

    def test_pure_water_kd_is_the_lee_kb_divided_by_the_factor(self) -> None:
        for wl in VISIBLE_NM:
            kb = float(lee.kb_pure_water(wl, 35.0))
            assert float(det.downwelling_kd(kb, wl, 35.0)) == pytest.approx(
                float(lee.kd_pure_water(wl, 35.0)), rel=1e-9
            )


class TestInterpolateKd:
    def test_reproduces_the_pure_water_edge_beyond_the_measured_bands(self) -> None:
        """The reason for anchoring. Bands stop at 561 nm; a_w rises 25x between
        there and 700 nm, and a linear extrapolation of the total misses it."""
        kd_in = [float(lee.kd_pure_water(w, 35.0)) for w in VISIBLE_NM]
        out = det.interpolate_kd(kd_in, VISIBLE_NM, [700.0], 35.0)
        assert out[0] == pytest.approx(float(lee.kd_pure_water(700.0, 35.0)))

    def test_naive_interpolation_would_be_an_order_of_magnitude_low(self) -> None:
        kd_in = np.array([float(lee.kd_pure_water(w, 35.0)) for w in VISIBLE_NM])
        anchored = det.interpolate_kd(kd_in, VISIBLE_NM, [700.0], 35.0)[0]
        naive = np.interp(700.0, VISIBLE_NM, kd_in)  # flat extrapolation
        assert anchored > 5 * naive

    def test_the_constituent_residual_is_carried_across(self) -> None:
        excess = 0.05
        kd_in = [float(lee.kd_pure_water(w, 35.0)) + excess for w in VISIBLE_NM]
        out = det.interpolate_kd(kd_in, VISIBLE_NM, [700.0], 35.0)
        assert out[0] == pytest.approx(
            float(lee.kd_pure_water(700.0, 35.0)) + excess
        )

    def test_sub_water_input_clips_rather_than_going_negative(self) -> None:
        """A fit below the pure-water line is a failure, not negative absorption.
        Clipping stops it propagating as a negative K_d elsewhere."""
        out = det.interpolate_kd([1e-6, 1e-6, 1e-6], VISIBLE_NM, [489.0], 35.0)
        assert out[0] == pytest.approx(float(lee.kd_pure_water(489.0, 35.0)))
        assert out[0] > 0.0

    def test_unsorted_input_is_ordered_before_interpolating(self) -> None:
        wl = [561.0, 444.0, 489.0]
        kd = [0.09, 0.05, 0.06]
        shuffled = det.interpolate_kd(kd, wl, [489.0], 35.0)
        ordered = det.interpolate_kd([0.05, 0.06, 0.09], sorted(wl), [489.0], 35.0)
        assert shuffled[0] == pytest.approx(ordered[0])

    def test_nan_bands_are_dropped(self) -> None:
        with_nan = det.interpolate_kd(
            [0.05, np.nan, 0.09], VISIBLE_NM, [500.0], 35.0
        )
        without = det.interpolate_kd([0.05, 0.09], [444.0, 561.0], [500.0], 35.0)
        assert with_nan[0] == pytest.approx(without[0])

    def test_no_finite_input_raises_an_actionable_error(self) -> None:
        with pytest.raises(ValueError, match="min_bins"):
            det.interpolate_kd([np.nan, np.nan], [444.0, 561.0], [500.0])


class TestParWeights:
    def test_photon_weighting_favours_the_red(self) -> None:
        wl = np.array([400.0, 700.0])
        weights = det.par_weights(wl, photon_weighted=True)
        assert weights[1] / weights[0] == pytest.approx(700.0 / 400.0)

    def test_energy_weighting_is_flat(self) -> None:
        weights = det.par_weights(np.arange(400.0, 701.0, 10.0), False)
        assert np.allclose(weights, weights[0])

    def test_weights_normalise(self) -> None:
        assert det.par_weights(np.arange(400.0, 701.0, 10.0)).sum() == pytest.approx(
            1.0
        )


class TestSeabedPar:
    @staticmethod
    def _clear_water_kd() -> tuple[list[float], tuple[float, ...]]:
        kd = [float(lee.kd_pure_water(w, 35.0)) for w in VISIBLE_NM]
        return kd, VISIBLE_NM

    def test_zero_depth_receives_all_the_light(self) -> None:
        kd, wl = self._clear_water_kd()
        out = det.seabed_par_fraction(np.array([[0.0]]), kd, wl)
        assert out[0, 0] == pytest.approx(1.0)

    def test_par_decreases_monotonically_with_depth(self) -> None:
        kd, wl = self._clear_water_kd()
        out = det.seabed_par_fraction(np.array([[0.0, 5.0, 10.0, 20.0]]), kd, wl)
        assert np.all(np.diff(out[0]) < 0)

    def test_invalid_depth_returns_nan(self) -> None:
        kd, wl = self._clear_water_kd()
        out = det.seabed_par_fraction(np.array([np.nan, -1.0, 5.0]), kd, wl)
        assert np.isnan(out[0]) and np.isnan(out[1]) and np.isfinite(out[2])

    def test_beyond_the_fit_window_returns_nan_not_extrapolation(self) -> None:
        kd, wl = self._clear_water_kd()
        cfg = DetectabilityConfig(par_max_depth_m=15.0)
        out = det.seabed_par_fraction(np.array([10.0, 20.0]), kd, wl, config=cfg)
        assert np.isfinite(out[0])
        assert np.isnan(out[1])

    def test_the_spectrum_reddens_out_of_the_water_column(self) -> None:
        """Not a single-exponential decay: the red is gone by a few metres, so
        the effective attenuation of the *integral* falls with depth."""
        kd, wl = self._clear_water_kd()
        depth = np.array([1.0, 2.0, 10.0, 20.0])
        frac = det.seabed_par_fraction(depth, kd, wl).astype(float)
        k_eff = -np.log(frac) / depth
        assert k_eff[0] > k_eff[-1]

    def test_passing_two_way_k_understates_the_light(self) -> None:
        """The mistake the API exists to prevent."""
        two_way = [0.1085, 0.0778, 0.0802]
        one_way = det.downwelling_kd(np.array(two_way), np.array(VISIBLE_NM), 35.0)
        depth = np.array([10.0])
        wrong = det.seabed_par_fraction(depth, two_way, VISIBLE_NM)
        right = det.seabed_par_fraction(depth, one_way, VISIBLE_NM)
        assert right[0] > wrong[0]

    def test_output_is_float32_and_bounded(self) -> None:
        kd, wl = self._clear_water_kd()
        out = det.seabed_par_fraction(np.linspace(0.0, 30.0, 50), kd, wl)
        assert out.dtype == np.float32
        assert np.all((out[np.isfinite(out)] >= 0.0) & (out[np.isfinite(out)] <= 1.0))

    def test_the_water_anchor_shows_up_as_red_starvation(self) -> None:
        """Sanity on the anchoring: pure-water a_w at 700 nm is 0.624, so the
        far red must be extinguished within a couple of metres."""
        assert water.a_water(700.0) > 0.6
        kd_700 = float(lee.kd_pure_water(700.0, 35.0))
        assert np.exp(-kd_700 * 5.0) < 0.15


class TestEuphoticDepth:
    # Pure water never reaches 1% inside the 40 m default window - its blue K_d
    # is 0.011 m^-1, giving a euphotic depth past 100 m. These use a turbid
    # coastal K_d so the solve has something to find.
    TURBID_KD = (0.25, 0.20, 0.18)

    def test_turbid_water_reaches_one_percent_within_the_window(self) -> None:
        z = det.euphotic_depth(self.TURBID_KD, VISIBLE_NM, 0.01, 35.0)
        assert np.isfinite(z)
        assert 0.0 < z < DetectabilityConfig().par_max_depth_m

    def test_clear_water_does_not_reach_one_percent_in_forty_metres(self) -> None:
        kd = [float(lee.kd_pure_water(w, 35.0)) for w in VISIBLE_NM]
        assert np.isnan(det.euphotic_depth(kd, VISIBLE_NM, 0.01, 35.0))

    def test_a_deeper_threshold_is_shallower(self) -> None:
        assert det.euphotic_depth(
            self.TURBID_KD, VISIBLE_NM, 0.10, 35.0
        ) < det.euphotic_depth(self.TURBID_KD, VISIBLE_NM, 0.01, 35.0)

    def test_unreached_within_the_window_returns_nan(self) -> None:
        cfg = DetectabilityConfig(par_max_depth_m=2.0)
        assert np.isnan(
            det.euphotic_depth(self.TURBID_KD, VISIBLE_NM, 0.01, 35.0, cfg)
        )

    def test_agrees_with_the_par_profile_it_is_solved_on(self) -> None:
        z = det.euphotic_depth(self.TURBID_KD, VISIBLE_NM, 0.01, 35.0)
        frac = det.seabed_par_fraction(np.array([z]), self.TURBID_KD, VISIBLE_NM, 35.0)
        assert frac[0] == pytest.approx(0.01, abs=5e-4)
