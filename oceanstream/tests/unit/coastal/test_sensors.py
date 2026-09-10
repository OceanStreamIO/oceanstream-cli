"""SensorProfile — band roles, proximity matching, and the PNeo constraints."""

from __future__ import annotations

import pytest

from oceanstream.coastal.sensors import (
    PLEIADES_NEO,
    SENSORS,
    SENTINEL2,
    SensorProfile,
    get_sensor,
)

#: The band set ACOLITE actually emitted for the Sesimbra scenes the golden
#: regression is built from. Nominal S2A centres differ from these by up to
#: 11 nm, which is exactly why matching is by proximity.
OBSERVED_S2 = [444.0, 489.0, 561.0, 667.0, 707.0, 741.0, 785.0, 835.0, 866.0, 1612.0, 2191.0]


class TestRegistry:
    def test_ships_both_sensors(self) -> None:
        assert set(SENSORS) == {"sentinel2", "pleiades_neo"}

    @pytest.mark.parametrize(
        ("alias", "expected"),
        [
            ("sentinel2", "sentinel2"),
            ("S2", "sentinel2"),
            ("Sentinel-2", "sentinel2"),
            ("MSI", "sentinel2"),
            ("pneo", "pleiades_neo"),
            ("Pleiades-Neo", "pleiades_neo"),
            ("pleiades neo", "pleiades_neo"),
        ],
    )
    def test_aliases_resolve(self, alias: str, expected: str) -> None:
        assert get_sensor(alias).name == expected

    def test_unknown_sensor_names_the_alternatives(self) -> None:
        with pytest.raises(KeyError, match="Construct a SensorProfile directly"):
            get_sensor("landsat9")


class TestProfileInvariants:
    @pytest.mark.parametrize("profile", [SENTINEL2, PLEIADES_NEO], ids=lambda p: p.name)
    def test_bands_are_ascending(self, profile: SensorProfile) -> None:
        assert list(profile.bands_nm) == sorted(profile.bands_nm)

    @pytest.mark.parametrize("profile", [SENTINEL2, PLEIADES_NEO], ids=lambda p: p.name)
    def test_every_role_points_at_a_real_band(self, profile: SensorProfile) -> None:
        assert all(nm in profile.bands_nm for nm in profile.band_roles.values())

    @pytest.mark.parametrize("profile", [SENTINEL2, PLEIADES_NEO], ids=lambda p: p.name)
    def test_own_bands_validate_cleanly(self, profile: SensorProfile) -> None:
        assert profile.validate_scene_bands(profile.bands_nm) == []

    def test_non_ascending_bands_rejected(self) -> None:
        with pytest.raises(ValueError, match="must be ascending"):
            SensorProfile(
                name="broken",
                acolite_sensor="X",
                bands_nm=(560.0, 490.0, 660.0, 830.0),
                band_roles={"blue": 490.0, "green": 560.0, "red": 660.0, "nir": 830.0},
                native_gsd_m=10.0,
                deglint_reference_nm=830.0,
                deglint_reference_is_assumed_dark=False,
                deepwater_screen_bands=(),
                null_channel_nm=660.0,
            )

    def test_missing_required_role_rejected(self) -> None:
        with pytest.raises(ValueError, match="missing required band roles"):
            SensorProfile(
                name="broken",
                acolite_sensor="X",
                bands_nm=(490.0, 560.0, 660.0),
                band_roles={"blue": 490.0, "green": 560.0, "red": 660.0},
                native_gsd_m=10.0,
                deglint_reference_nm=660.0,
                deglint_reference_is_assumed_dark=False,
                deepwater_screen_bands=(),
                null_channel_nm=660.0,
            )

    def test_role_pointing_at_absent_band_rejected(self) -> None:
        with pytest.raises(ValueError, match="not in bands_nm"):
            SensorProfile(
                name="broken",
                acolite_sensor="X",
                bands_nm=(490.0, 560.0, 660.0, 830.0),
                band_roles={"blue": 490.0, "green": 560.0, "red": 660.0, "nir": 999.0},
                native_gsd_m=10.0,
                deglint_reference_nm=830.0,
                deglint_reference_is_assumed_dark=False,
                deepwater_screen_bands=(),
                null_channel_nm=660.0,
            )


class TestBandMatching:
    def test_exact_match(self) -> None:
        assert SENTINEL2.band_index(OBSERVED_S2, 561.0) == 2

    def test_matches_across_platform_drift(self) -> None:
        # 2202 nm nominal against 2191 nm observed is an 11 nm gap. Matching on
        # equality would drop the SWIR screen on a platform swap.
        assert SENTINEL2.band_index(OBSERVED_S2, 2202.0) == 10

    def test_returns_none_past_the_tolerance(self) -> None:
        # Better a missing band than a silent substitution of one that measures
        # something else.
        assert SENTINEL2.band_index(OBSERVED_S2, 1000.0) is None

    def test_tolerance_is_overridable(self) -> None:
        assert SENTINEL2.band_index(OBSERVED_S2, 2202.0, tolerance_nm=5.0) is None
        assert SENTINEL2.band_index(OBSERVED_S2, 2202.0, tolerance_nm=20.0) == 10

    def test_default_tolerance_cannot_reach_an_adjacent_band(self) -> None:
        # The narrowest Sentinel-2 gap is 707 -> 741 nm at 34 nm; a 15 nm
        # half-width leaves 4 nm of margin on each side.
        gaps = [b - a for a, b in zip(OBSERVED_S2, OBSERVED_S2[1:])]
        assert min(gaps) > 2 * SENTINEL2.band_match_tolerance_nm

    def test_empty_band_list(self) -> None:
        assert SENTINEL2.band_index([], 561.0) is None

    def test_role_index(self) -> None:
        assert SENTINEL2.role_index(OBSERVED_S2, "green") == 2
        assert SENTINEL2.role_index(OBSERVED_S2, "swir2") == 10

    def test_role_the_sensor_does_not_declare(self) -> None:
        assert PLEIADES_NEO.role_index(PLEIADES_NEO.bands_nm, "swir1") is None

    def test_require_role_index_names_the_undeclared_role(self) -> None:
        with pytest.raises(KeyError, match="declares no 'swir1' band"):
            PLEIADES_NEO.require_role_index(PLEIADES_NEO.bands_nm, "swir1")

    def test_require_role_index_names_the_absent_band(self) -> None:
        with pytest.raises(KeyError, match="no observed band within"):
            SENTINEL2.require_role_index([444.0, 489.0, 561.0], "nir")

    def test_resolve_roles_skips_what_the_scene_lacks(self) -> None:
        resolved = SENTINEL2.resolve_roles([444.0, 489.0, 561.0, 667.0, 835.0])
        assert set(resolved) == {"blue", "blue_green", "green", "red", "nir"}
        assert resolved["red"] == 3


class TestSceneValidation:
    def test_missing_required_band_is_reported(self) -> None:
        problems = SENTINEL2.validate_scene_bands([444.0, 489.0, 667.0, 835.0])
        assert any("missing required 'green'" in p for p in problems)

    def test_missing_swir_costs_deglint_and_the_screen(self) -> None:
        problems = SENTINEL2.validate_scene_bands(
            [444.0, 489.0, 561.0, 667.0, 707.0, 741.0, 785.0, 835.0, 866.0]
        )
        assert any("deglint reference" in p for p in problems)
        assert any("deep-water screen" in p for p in problems)

    def test_missing_null_channel_names_the_gates_it_blocks(self) -> None:
        problems = SENTINEL2.validate_scene_bands([444.0, 489.0, 561.0, 835.0, 1612.0, 2191.0])
        assert any("null channel" in p and "AC-uncertainty" in p for p in problems)


class TestSentinel2:
    def test_observed_band_set_matches_the_golden_fixture(self) -> None:
        assert list(SENTINEL2.bands_nm) == OBSERVED_S2

    def test_has_swir(self) -> None:
        assert SENTINEL2.has_swir

    def test_deglint_reference_is_genuinely_dark(self) -> None:
        # SWIR 1612 nm has effectively zero water-leaving reflectance, which is
        # what Hedley's correction assumes.
        assert SENTINEL2.deglint_reference_nm == 1612.0
        assert SENTINEL2.deglint_reference_is_assumed_dark

    def test_null_channel_is_red(self) -> None:
        assert SENTINEL2.null_channel_nm == 667.0

    def test_deepwater_screen_uses_both_swir_bands(self) -> None:
        assert SENTINEL2.deepwater_screen_bands == (1612.0, 2191.0)


class TestPleiadesNeo:
    def test_six_multispectral_bands(self) -> None:
        assert len(PLEIADES_NEO.bands_nm) == 6

    def test_no_swir(self) -> None:
        assert not PLEIADES_NEO.has_swir

    def test_deglint_falls_back_to_nir_and_says_so(self) -> None:
        # NIR is not dark over bright shallow sand, so the correction
        # over-subtracts exactly where the bottom signal is strongest.
        assert PLEIADES_NEO.deglint_reference_nm == 825.0
        assert not PLEIADES_NEO.deglint_reference_is_assumed_dark
        assert "over-subtracts" in PLEIADES_NEO.notes["deglint"]

    def test_no_deepwater_screen_bands(self) -> None:
        assert PLEIADES_NEO.deepwater_screen_bands == ()

    def test_null_channel_is_red_edge_which_is_a_better_null(self) -> None:
        assert PLEIADES_NEO.null_channel_nm == 725.0
        assert PLEIADES_NEO.has_red_edge

    def test_gsd_is_multispectral_not_panchromatic(self) -> None:
        # 0.3 m is the PAN band; the retrieval runs on the 1.2 m MS bands.
        assert PLEIADES_NEO.native_gsd_m == 1.2

    def test_gsd_inverts_the_sentinel2_ratio_against_11m_bathymetry(self) -> None:
        lidar_gsd_m = 11.4
        assert SENTINEL2.native_gsd_m > lidar_gsd_m / 2
        assert PLEIADES_NEO.native_gsd_m < lidar_gsd_m
        assert "Aggregate to the bathymetry grid" in PLEIADES_NEO.notes["resolution"]

    def test_qaa_approximation_is_flagged(self) -> None:
        assert "approximation" in PLEIADES_NEO.notes["qaa"]
