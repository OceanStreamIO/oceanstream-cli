"""Tests for the Phase 3.3 physical-floor QC gates.

The load-bearing property is that these gates fire on the *known-bad* Sesimbra
2026-06-27 fit. A gate that passes the one scene we already know is broken is
worse than no gate, because it would certify the artefact at every AOI it was
then pointed at.

The second property is that the two gates are not redundant. A depth-correlated
artefact that inflates every band equally leaves each pure-water floor intact
while pinning the ratio between bands near unity, so the ratio check has to
catch cases the floor check cannot see. That case is constructed explicitly
below.
"""

from __future__ import annotations

import json

import numpy as np
import pytest

from oceanstream.coastal.config import QCConfig
from oceanstream.coastal.inversion import lee
from oceanstream.coastal.qc import floors

# The fit that started all of this. 561 and 667 nm both come back below the
# attenuation pure water alone guarantees.
SESIMBRA_K = {444.0: 0.1085, 489.0: 0.0778, 561.0: 0.0802, 667.0: 0.0772}
BANDS_NM = (444.0, 489.0, 561.0, 667.0)


def _physical_k(scale: float = 3.0) -> dict[float, float]:
    """A fit that clears every floor with realistic spectral shape."""
    return {wl: scale * float(lee.kb_pure_water(wl)) for wl in BANDS_NM}


class _FakeCalibration:
    """Stands in for ``BandCalibration`` — only ``k_per_m`` is read."""

    def __init__(self, k_per_m: float) -> None:
        self.k_per_m = k_per_m
        self.k_effective = max(k_per_m, 1.0)  # must be ignored


class TestPureWaterFloorCheck:
    def test_sesimbra_667_violates_the_floor(self) -> None:
        """Regression on the known-bad case. If this ever passes, the gate has
        been loosened past the point of being a gate."""
        result = floors.pure_water_floor_check(SESIMBRA_K)
        assert result["passed"] is False
        assert result["bands"]["667"]["violated"] is True
        assert 667.0 in result["violating_bands_nm"]
        assert "k_below_pure_water_floor" in result["flags"]

    def test_the_tighter_lee_floor_also_catches_561(self) -> None:
        """The library's floor is the full two-way Lee k, not a_w alone, so it
        is roughly 2x tighter than the prototype's quoted number and rejects a
        band the prototype kept."""
        result = floors.pure_water_floor_check(SESIMBRA_K)
        assert result["bands"]["561"]["violated"] is True
        assert result["n_violations"] == 2

    def test_the_shortfall_is_reported_in_physical_units(self) -> None:
        band = floors.pure_water_floor_check(SESIMBRA_K)["bands"]["667"]
        assert band["shortfall_per_m"] == pytest.approx(
            band["floor_per_m"] - band["k_per_m"]
        )
        assert band["ratio"] == pytest.approx(band["k_per_m"] / band["floor_per_m"])

    def test_worst_ratio_identifies_the_worst_band(self) -> None:
        result = floors.pure_water_floor_check(SESIMBRA_K)
        assert result["worst_band_nm"] == 667.0
        ratios = [b["ratio"] for b in result["bands"].values()]
        assert result["worst_ratio"] == pytest.approx(min(ratios))

    def test_a_physical_fit_passes_cleanly(self) -> None:
        result = floors.pure_water_floor_check(_physical_k())
        assert result["passed"] is True
        assert result["n_violations"] == 0
        assert result["flags"] == []

    def test_k_exactly_at_the_floor_is_not_a_violation(self) -> None:
        result = floors.pure_water_floor_check(_physical_k(scale=1.0))
        assert result["passed"] is True

    def test_all_nan_input_fails_rather_than_vacuously_passing(self) -> None:
        result = floors.pure_water_floor_check({489.0: np.nan, 667.0: np.nan})
        assert result["passed"] is False
        assert "no_finite_k_to_check" in result["flags"]
        assert result["worst_band_nm"] is None

    def test_empty_input_fails(self) -> None:
        assert floors.pure_water_floor_check({})["passed"] is False

    def test_accepts_band_calibration_objects(self) -> None:
        calibrations = {wl: _FakeCalibration(k) for wl, k in SESIMBRA_K.items()}
        assert floors.pure_water_floor_check(calibrations)[
            "bands"
        ] == floors.pure_water_floor_check(SESIMBRA_K)["bands"]

    def test_reads_k_per_m_not_the_already_floored_k_effective(self) -> None:
        """Gating on ``k_effective`` would compare the floor with itself and
        report every scene as passing."""
        calibrations = {wl: _FakeCalibration(k) for wl, k in SESIMBRA_K.items()}
        assert floors.pure_water_floor_check(calibrations)["passed"] is False

    def test_solar_zenith_moves_the_floor_only_weakly(self) -> None:
        nadir = floors.pure_water_floor_check(SESIMBRA_K, 0.0)
        oblique = floors.pure_water_floor_check(SESIMBRA_K, 55.0)
        a = nadir["bands"]["667"]["floor_per_m"]
        b = oblique["bands"]["667"]["floor_per_m"]
        assert abs(b - a) / a < 0.20
        # Not enough to change the verdict on this scene.
        assert nadir["passed"] == oblique["passed"] is False

    def test_the_default_zenith_matches_the_band_calibration_property(self) -> None:
        result = floors.pure_water_floor_check(SESIMBRA_K)
        assert result["solar_zenith_deg"] == QCConfig().floor_solar_zenith_deg
        assert result["bands"]["667"]["floor_per_m"] == pytest.approx(
            float(lee.kb_pure_water(667.0))
        )


class TestLyzengaRatioCheck:
    def test_sesimbra_blue_red_ratio_is_flagged_near_unity(self) -> None:
        """k(489)/k(667) = 1.008 where pure water alone demands 0.042. Two
        bands separated by 24x in water absorption came back attenuating at the
        same rate, which is the additive-offset signature."""
        result = floors.lyzenga_ratio_check(SESIMBRA_K)
        assert result["passed"] is False
        pair = result["pairs"]["489/667"]
        assert pair["observed"] == pytest.approx(1.008, abs=0.01)
        assert pair["expected_pure_water"] == pytest.approx(0.042, abs=0.005)
        assert pair["near_unity"] is True
        assert "ratio_near_unity" in result["flags"]

    def test_the_near_unity_case_is_above_the_floor_not_below_it(self) -> None:
        """Why the second failure mode exists. The observed ratio is 24x the
        pure-water floor, so the floor test alone sees nothing wrong."""
        pair = floors.lyzenga_ratio_check(SESIMBRA_K)["pairs"]["489/667"]
        assert pair["below_pure_water"] is False
        assert pair["ratio_to_expected"] > 20

    def test_only_spectrally_contrasted_pairs_are_tested(self) -> None:
        """Blue/green sits at a pure-water ratio near 0.3, where a ratio close
        to 1 carries no information and would just add false alarms."""
        pairs = floors.lyzenga_ratio_check(SESIMBRA_K)["pairs"]
        assert "489/667" in pairs
        assert "444/489" not in pairs
        assert "489/561" not in pairs

    def test_a_physical_fit_passes(self) -> None:
        result = floors.lyzenga_ratio_check(_physical_k())
        assert result["passed"] is True
        assert result["flags"] == []
        assert result["pairs"]["489/667"]["observed"] == pytest.approx(
            result["pairs"]["489/667"]["expected_pure_water"]
        )

    def test_a_ratio_below_pure_water_is_a_hard_failure(self) -> None:
        k = _physical_k()
        k[489.0] *= 0.5  # blue attenuating less than pure water relative to red
        result = floors.lyzenga_ratio_check(k)
        assert result["passed"] is False
        assert "489/667" in result["below_pure_water_pairs"]
        assert "ratio_below_pure_water" in result["flags"]

    def test_no_contrasted_pair_available_fails_rather_than_passing(self) -> None:
        """Two green bands cannot test anything, and reporting 'passed' there
        would be a gate that certifies by having nothing to look at."""
        result = floors.lyzenga_ratio_check({555.0: 0.2, 561.0: 0.21})
        assert result["passed"] is False
        assert "no_discriminating_band_pair" in result["flags"]

    def test_non_finite_and_zero_bands_are_skipped(self) -> None:
        result = floors.lyzenga_ratio_check({489.0: 0.05, 667.0: 0.0})
        assert result["n_pairs"] == 0
        result = floors.lyzenga_ratio_check({489.0: np.nan, 667.0: 1.0})
        assert result["n_pairs"] == 0

    def test_accepts_band_calibration_objects(self) -> None:
        calibrations = {wl: _FakeCalibration(k) for wl, k in SESIMBRA_K.items()}
        assert floors.lyzenga_ratio_check(calibrations)["passed"] is False


class TestSceneFloorVerdict:
    def test_sesimbra_fails_and_names_both_reasons(self) -> None:
        verdict = floors.scene_floor_verdict(SESIMBRA_K)
        assert verdict["passed"] is False
        assert "pure-water floor" in verdict["summary"]
        assert "common-mode additive artefact" in verdict["summary"]
        assert set(verdict["flags"]) == {
            "k_below_pure_water_floor",
            "ratio_near_unity",
        }

    def test_a_physical_fit_passes_both_gates(self) -> None:
        verdict = floors.scene_floor_verdict(_physical_k())
        assert verdict["passed"] is True
        assert verdict["flags"] == []
        assert "clears the pure-water floor" in verdict["summary"]

    def test_passed_is_the_conjunction_of_both_gates(self) -> None:
        k = _physical_k()
        k[489.0] *= 0.5  # ratio gate fails, floor gate still passes
        verdict = floors.scene_floor_verdict(k)
        assert verdict["checks"]["pure_water_floor"]["passed"] is True
        assert verdict["checks"]["lyzenga_ratio"]["passed"] is False
        assert verdict["passed"] is False

    def test_the_summary_is_never_empty_on_failure(self) -> None:
        for k in (SESIMBRA_K, {}, {489.0: np.nan}, {555.0: 0.2, 561.0: 0.21}):
            verdict = floors.scene_floor_verdict(k)
            assert verdict["passed"] is False
            assert verdict["summary"].strip()

    def test_the_verdict_is_json_serialisable_for_the_stac_item(self) -> None:
        payload = json.dumps(floors.scene_floor_verdict(_physical_k()))
        assert json.loads(payload)["passed"] is True

    def test_slack_can_characterise_a_near_miss_without_changing_the_default(
        self,
    ) -> None:
        k = _physical_k(scale=0.95)  # 5% below every floor
        assert floors.scene_floor_verdict(k)["passed"] is False
        lenient = QCConfig(k_floor_slack=0.10)
        assert floors.pure_water_floor_check(k, config=lenient)["passed"] is True


# The nine Sentinel-2 bands the comparison harness actually fits. Only the four
# visible ones can be scored against a floor; 707-866 nm are both beyond the
# Pope & Fry table and opaque within the fit window.
S2_ALL_NM = (444.0, 489.0, 561.0, 667.0, 707.0, 741.0, 783.0, 835.0, 866.0)
S2_ASSESSABLE_NM = (444.0, 489.0, 561.0, 667.0)


class TestAssessableBands:
    """Which bands a floor claim may be made about at all."""

    def test_only_the_visible_bands_are_scored(self) -> None:
        k = dict.fromkeys(S2_ALL_NM, 0.05)
        result = floors.pure_water_floor_check(k)
        assert set(result["bands"]) == {f"{nm:.0f}" for nm in S2_ASSESSABLE_NM}
        assert set(result["skipped_bands"]) == {"707", "741", "783", "835", "866"}

    def test_the_sesimbra_violation_count_matches_the_reference_run(self) -> None:
        """The harness fits nine bands; the prototype reported two violations.

        Scoring the five unassessable bands is what inflated that to seven, and
        it is why no harness verdict was comparable with the reference run.
        """
        k = dict(SESIMBRA_K) | dict.fromkeys((707.0, 741.0, 783.0, 835.0, 866.0), 0.02)
        result = floors.pure_water_floor_check(k)
        assert result["n_violations"] == 2
        assert result["violating_bands_nm"] == [561.0, 667.0]
        assert result["worst_band_nm"] == 667.0
        assert result["worst_ratio"] == pytest.approx(0.083, abs=0.005)

    def test_a_skipped_band_cannot_fail_the_scene(self) -> None:
        """An absurd NIR k must not change the verdict, in either direction."""
        good = _physical_k()
        assert floors.scene_floor_verdict(good)["passed"] is True
        assert floors.scene_floor_verdict(good | {835.0: 1e-9})["passed"] is True

    def test_each_exclusion_states_its_reason(self) -> None:
        skipped = floors.pure_water_floor_check(
            dict.fromkeys((*S2_ALL_NM, 690.0), 0.05)
        )["skipped_bands"]
        assert "no tabulated pure-water absorption" in skipped["835"]
        assert "extinguishes" in skipped["690"]

    def test_an_opaque_band_inside_the_table_is_still_excluded(self) -> None:
        """690 nm is tabulated but its floor is above 1/m — the two limits are
        independent, so neither alone is sufficient."""
        assert float(lee.kb_pure_water(690.0)) >= 1.0
        result = floors.pure_water_floor_check({489.0: 0.2, 690.0: 0.2})
        assert "690" in result["skipped_bands"]
        assert "extinguishes" in result["skipped_bands"]["690"]

    def test_exclusions_are_visible_in_the_summary_even_when_passing(self) -> None:
        verdict = floors.scene_floor_verdict(_physical_k() | {835.0: 0.02})
        assert verdict["passed"] is True
        assert "835" in verdict["summary"]

    def test_a_scene_with_no_assessable_band_fails(self) -> None:
        verdict = floors.scene_floor_verdict(dict.fromkeys((835.0, 866.0), 0.02))
        assert verdict["passed"] is False
        assert "no_assessable_band" in verdict["checks"]["pure_water_floor"]["flags"]
        assert "no band could be scored" in verdict["summary"]

    def test_the_ratio_check_ignores_unassessable_bands(self) -> None:
        """444/835 clears ratio_contrast_max only because the extrapolated NIR
        floor is ~7x too low, so it was being tested on a fabricated basis."""
        pairs = floors.lyzenga_ratio_check(dict.fromkeys(S2_ALL_NM, 0.05))["pairs"]
        assert all(
            wl <= 700.0 for pair in pairs.values() for wl in pair["wavelengths_nm"]
        )

    def test_the_limits_are_configurable(self) -> None:
        k = {489.0: 0.2, 835.0: 0.2}
        permissive = QCConfig(floor_max_wavelength_nm=900.0, floor_opaque_min_per_m=99.0)
        assert "835" in floors.pure_water_floor_check(k, config=permissive)["bands"]
