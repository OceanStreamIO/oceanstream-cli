"""Numerical checks on the QAA v6 scene-mean fit.

The synthetic scenes below are generated from the saved Sesimbra 2026-06-27 IOP
fixture through the same optically-deep relation QAA inverts, so the round-trip
assertions are exact rather than tolerance-tuned.

Ported from ``kelp_observe/tools/lee_demo/qaa.py`` in Phase 1.2. The prototype
shipped no QAA tests; these are new, and two of them (``kd_map`` finiteness and
the reference-band switch) are regression traps for defects found during the
port audit — see Phase 0.2 in the plan.
"""

from __future__ import annotations

import numpy as np
import pytest

from oceanstream.coastal.config import RetrievalConfig
from oceanstream.coastal.optics import qaa, water

from .conftest import SOLAR_ZENITH_DEG

SCENE_SHAPE = (10, 10)


def _deep_water_rrs(a: np.ndarray, bb: np.ndarray) -> np.ndarray:
    """Above-water Rrs over optically deep water — no bottom term."""
    u = bb / (a + bb)
    rrs_sub = 0.089 * u + 0.125 * u * u
    return np.asarray(water.rrs_subsurface_to_above(rrs_sub))


@pytest.fixture
def deep_scene(scene_iops_raw: dict) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(rrs_above, wavelengths_nm, deep_water_valid) for a uniform deep scene."""
    wavelengths = np.asarray(scene_iops_raw["wavelengths_nm"], dtype=float)
    a = np.asarray(scene_iops_raw["a"], dtype=float)
    bb = np.asarray(scene_iops_raw["bb"], dtype=float)
    spectrum = _deep_water_rrs(a, bb)
    rrs_above = np.broadcast_to(spectrum[:, None, None], (spectrum.size, *SCENE_SHAPE)).copy()
    return rrs_above, wavelengths, np.ones(SCENE_SHAPE, dtype=bool)


class TestSceneFitRoundTrip:
    def test_retrieved_iops_reproduce_the_input_reflectance(self, deep_scene):
        """u = b_b/(a + b_b) must map the fit back onto the observed r_rs.

        This is the one exact invariant in QAA: the reference band is empirical
        and b_bp is extrapolated, but a(λ) is *defined* by the u(λ) equation, so
        the retrieved pair must regenerate the spectrum it was fitted to.
        """
        rrs_above, wavelengths, valid = deep_scene
        iops = qaa.fit_scene_iops(rrs_above, wavelengths, valid, SOLAR_ZENITH_DEG)

        floored = {v["wavelength_nm"] for v in iops.a_floor_violations}
        keep = np.array([wl not in floored for wl in iops.wavelengths_nm])

        reconstructed = _deep_water_rrs(iops.a.astype(float), iops.bb.astype(float))
        observed = rrs_above[:, 0, 0]
        assert np.allclose(reconstructed[keep], observed[keep], rtol=1e-4)

    def test_absorption_never_falls_below_pure_water(self, deep_scene):
        rrs_above, wavelengths, valid = deep_scene
        iops = qaa.fit_scene_iops(rrs_above, wavelengths, valid, SOLAR_ZENITH_DEG)
        a_w = np.asarray(water.a_water(iops.wavelengths_nm), dtype=float)
        assert np.all(iops.a.astype(float) >= a_w - 1e-6)

    def test_reports_deep_pixel_count(self, deep_scene):
        rrs_above, wavelengths, valid = deep_scene
        valid = valid.copy()
        valid[0, :] = False
        iops = qaa.fit_scene_iops(rrs_above, wavelengths, valid, SOLAR_ZENITH_DEG)
        assert iops.n_deep_pixels == int(valid.sum())

    def test_non_finite_pixels_are_excluded(self, deep_scene):
        rrs_above, wavelengths, valid = deep_scene
        rrs_above = rrs_above.copy()
        rrs_above[3, 0, 0] = np.nan
        iops = qaa.fit_scene_iops(rrs_above, wavelengths, valid, SOLAR_ZENITH_DEG)
        assert iops.n_deep_pixels == valid.size - 1
        assert np.all(np.isfinite(iops.a))


class TestGuards:
    def test_band_count_mismatch_is_rejected(self, deep_scene):
        rrs_above, wavelengths, valid = deep_scene
        with pytest.raises(ValueError, match="bands"):
            qaa.fit_scene_iops(rrs_above, wavelengths[:-1], valid, SOLAR_ZENITH_DEG)

    def test_too_few_deep_pixels_is_rejected(self, deep_scene):
        rrs_above, wavelengths, valid = deep_scene
        valid = np.zeros_like(valid)
        valid[0, :3] = True
        with pytest.raises(ValueError, match="deep-water pixels"):
            qaa.fit_scene_iops(rrs_above, wavelengths, valid, SOLAR_ZENITH_DEG)

    def test_min_deep_pixels_is_configurable(self, deep_scene):
        rrs_above, wavelengths, valid = deep_scene
        valid = np.zeros_like(valid)
        valid[0, :3] = True
        iops = qaa.fit_scene_iops(
            rrs_above,
            wavelengths,
            valid,
            SOLAR_ZENITH_DEG,
            config=RetrievalConfig(min_deep_pixels=3),
        )
        assert iops.n_deep_pixels == 3


class TestReferenceWavelength:
    """The red/green switch decides which band anchors the whole spectrum.

    Rrs(670) at Sesimbra is ~5e-4 sr^-1, a third of the v6 threshold, so green
    is correct. A red reference over clear water is the most diagnostic symptom
    of atmospheric-correction residual, which is why it carries its own flag.
    """

    def test_clear_water_selects_green(self, deep_scene):
        rrs_above, wavelengths, valid = deep_scene
        iops = qaa.fit_scene_iops(rrs_above, wavelengths, valid, SOLAR_ZENITH_DEG)
        assert iops.ref_wavelength_nm == 561.0
        assert not any(f.startswith("red_reference") for f in iops.qa_flags)

    def test_lowering_the_threshold_selects_red_and_flags_it(self, deep_scene):
        rrs_above, wavelengths, valid = deep_scene
        iops = qaa.fit_scene_iops(
            rrs_above,
            wavelengths,
            valid,
            SOLAR_ZENITH_DEG,
            config=RetrievalConfig(rrs_670_turbid_threshold=1e-6),
        )
        assert iops.ref_wavelength_nm == 667.0
        assert any(f.startswith("red_reference_selected") for f in iops.qa_flags)

    def test_switch_margin_holds_the_reference_in_the_green(self, deep_scene):
        """AC uncertainty on Rrs(670) must be able to veto the red switch."""
        rrs_above, wavelengths, valid = deep_scene
        iops = qaa.fit_scene_iops(
            rrs_above,
            wavelengths,
            valid,
            SOLAR_ZENITH_DEG,
            config=RetrievalConfig(rrs_670_turbid_threshold=1e-6),
            ref_switch_margin=0.01,
        )
        assert iops.ref_wavelength_nm == 561.0

    def test_force_overrides_the_rule(self, deep_scene):
        rrs_above, wavelengths, valid = deep_scene
        iops = qaa.fit_scene_iops(
            rrs_above,
            wavelengths,
            valid,
            SOLAR_ZENITH_DEG,
            force_ref_wavelength_nm=489.0,
        )
        assert iops.ref_wavelength_nm == 489.0


class TestCdmSplit:
    def test_absent_violet_band_is_recorded_not_silent(self, deep_scene):
        """S2's bluest band is 443, so the a_ph/a_dg split is not computable."""
        rrs_above, wavelengths, valid = deep_scene
        iops = qaa.fit_scene_iops(rrs_above, wavelengths, valid, SOLAR_ZENITH_DEG)
        assert np.isnan(iops.a_cdm_443)
        assert "a_cdm_443_not_computable(no_411nm_band)" in iops.qa_flags


class TestPlausibilityConfig:
    """The prototype hardcoded -0.03 while SiteProfile carried a bound nothing
    read. Both tiers are now wired through RetrievalConfig."""

    def _flags(self, a_cdm_443: float, config: RetrievalConfig) -> list[str]:
        return qaa._plausibility_flags(
            a_cdm_443=a_cdm_443,
            bbp_555=0.0023,
            a_490=0.049,
            kd_490=0.067,
            y=1.71,
            ref_wavelength_nm=561.0,
            config=config,
        )

    def test_soft_bound_flags_aoi_specific_violation(self):
        flags = self._flags(-0.01, RetrievalConfig())
        assert any(f.startswith("a_cdm_443_below_aoi_bound") for f in flags)
        assert not any(f.startswith("a_cdm_443_negative") for f in flags)

    def test_hard_bound_reproduces_the_prototype_flag(self):
        flags = self._flags(-0.05, RetrievalConfig())
        assert any(f.startswith("a_cdm_443_negative") for f in flags)

    def test_relaxed_soft_bound_accepts_the_same_value(self):
        flags = self._flags(-0.01, RetrievalConfig(a_cdm_443_min=-0.03))
        assert not any(f.startswith("a_cdm_443") for f in flags)

    def test_ranges_are_configurable(self):
        config = RetrievalConfig(kd_490_max=0.05, y_max=1.0, bbp_555_max=0.001)
        flags = self._flags(float("nan"), config)
        assert any(f.startswith("kd_490_out_of_range") for f in flags)
        assert any(f.startswith("bbp_slope_out_of_range") for f in flags)
        assert any(f.startswith("bbp_555_implausible") for f in flags)


class TestKdMap:
    """Regression trap for the Phase 0.2 defect.

    ``kd_map`` raised ``NameError: a_band`` in the prototype because a comment
    had absorbed the assignment. Any test that calls it and inspects the result
    would have caught it, so these are deliberately cheap.
    """

    def test_returns_finite_positive_kd_inside_the_mask(self, deep_scene):
        rrs_above, wavelengths, valid = deep_scene
        kd = qaa.kd_map(rrs_above, wavelengths, valid, SOLAR_ZENITH_DEG)
        assert kd.shape == SCENE_SHAPE
        assert np.all(np.isfinite(kd))
        assert np.all(kd > 0.0)

    def test_masked_pixels_are_nan(self, deep_scene):
        rrs_above, wavelengths, valid = deep_scene
        valid = valid.copy()
        valid[0, 0] = False
        kd = qaa.kd_map(rrs_above, wavelengths, valid, SOLAR_ZENITH_DEG)
        assert np.isnan(kd[0, 0])
        assert np.isfinite(kd[1, 1])

    def test_brackets_the_scene_fit_within_a_factor_of_two(self, deep_scene):
        """kd_map trades accuracy for cost; its contract is only that it stays
        within ~2x of the full fit, which is all the optical-depth score needs.
        """
        rrs_above, wavelengths, valid = deep_scene
        iops = qaa.fit_scene_iops(rrs_above, wavelengths, valid, SOLAR_ZENITH_DEG)
        kd_full = float(iops.kd[int(np.argmin(np.abs(iops.wavelengths_nm - 490)))])
        kd_cheap = float(np.nanmean(qaa.kd_map(rrs_above, wavelengths, valid, SOLAR_ZENITH_DEG)))
        assert 0.5 <= kd_cheap / kd_full <= 2.0
