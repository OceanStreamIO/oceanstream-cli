"""The shipped reference AOIs.

These tests mostly guard geometry consistency, because that is what actually
broke. Sesimbra was first shipped with the KelpObserve *field-campaign* AOI —
the Arrábida strip east of Sesimbra town — instead of the geometry the
prototype's reference retrievals were produced over, which sits at the Cabo
Espichel end. The two barely touch. All 60 CCMAR diver quadrats fell outside
the shipped box, so the AOI excluded the only ground truth the site has, and
every date ranking taken from it described water no retrieval had been
validated in.

Nothing raised. Both boxes are valid bboxes over real Portuguese sea. What
distinguishes them is not well-formedness but whether they contain the
measurements, so that is what these tests check: the quadrats have to be inside
the declared habitat area, and each nested footprint inside the one that must
contain it.
"""

from __future__ import annotations

import json

import pytest

from oceanstream.coastal.aoi import AOI
from oceanstream.coastal.aois import (
    _raw,
    get_reference_aoi,
    list_reference_aois,
    load_reference_aois,
)

EXPECTED = {"donegal", "sesimbra", "summer_isles"}


def _contains(outer, inner) -> bool:
    return (
        outer[0] <= inner[0]
        and outer[1] <= inner[1]
        and inner[2] <= outer[2]
        and inner[3] <= outer[3]
    )


ALL = sorted(EXPECTED)

# Bounds of the 60 CCMAR diver quadrats surveyed in the June and July 2026
# campaigns (kelp_observe: data/ccmar-diving/transects/quadrats_with_cover.gpkg).
# A 111 m x 118 m box — the entire ground truth Sesimbra has. Recorded here
# rather than read from that repository, which the library cannot see.
SESIMBRA_QUADRATS = (-9.2027, 38.4086, -9.2015, 38.4096)


class TestDiscovery:
    def test_the_expected_sites_ship(self):
        assert set(list_reference_aois()) == EXPECTED

    def test_loading_them_all_keys_by_name(self):
        loaded = load_reference_aois()
        assert set(loaded) == EXPECTED
        assert all(name == aoi.name for name, aoi in loaded.items())

    def test_an_unknown_name_says_what_is_available(self):
        with pytest.raises(KeyError, match="sesimbra"):
            get_reference_aoi("atlantis")


class TestRoundTrip:
    @pytest.mark.parametrize("name", ALL)
    def test_the_shipped_file_survives_a_round_trip(self, name):
        aoi = get_reference_aoi(name)
        assert AOI.from_dict(aoi.to_dict()) == aoi

    @pytest.mark.parametrize("name", ALL)
    def test_the_file_on_disk_is_the_serialisation_of_to_dict(self, name):
        """Compared through JSON, since JSON has no tuple: bboxes come back lists."""
        expected = json.loads(json.dumps(get_reference_aoi(name).to_dict()))
        assert _raw(name) == expected


class TestGeometry:
    """The invariants that were violated in practice."""

    def test_sesimbra_contains_the_quadrats_it_is_validated_against(self):
        """The check that would have caught the wrong box on the first run.

        Ground truth is the only thing that distinguishes this AOI from the
        neighbouring stretch of coast, so it is the only thing worth asserting.
        """
        aoi = get_reference_aoi("sesimbra")
        assert aoi.habitat_bbox is not None
        assert _contains(aoi.habitat_bbox, SESIMBRA_QUADRATS), (
            f"habitat_bbox {aoi.habitat_bbox} excludes the CCMAR quadrats at "
            f"{SESIMBRA_QUADRATS} — this is not the validated site."
        )

    @pytest.mark.parametrize("name", ALL)
    def test_the_screening_window_sits_inside_the_processing_bbox(self, name):
        aoi = get_reference_aoi(name)
        screening = aoi.metadata["screening_bbox"]
        assert _contains(aoi.processing_bbox, screening), (
            f"{name}: screening window {screening} is not inside "
            f"processing_bbox {aoi.processing_bbox} — it measures other water."
        )

    @pytest.mark.parametrize("name", ALL)
    def test_a_declared_habitat_area_has_fine_bathymetry_under_it(self, name):
        """Benthic claims need depth, so the depth grid has to reach them.

        Deliberately *not* asserted of ``processing_bbox``: that grid extends
        offshore past the LiDAR on purpose, to reach the optically deep water
        the IOP fit needs. An earlier version of this test required the whole
        processing grid to sit inside the fine footprint, which contradicts what
        the grid is for and would reject the correct Sesimbra geometry.
        """
        aoi = get_reference_aoi(name)
        if aoi.habitat_bbox is None:
            pytest.skip("no habitat area declared")
        footprint = aoi.metadata["bathymetry_footprint"]
        assert _contains(footprint, aoi.habitat_bbox), (
            f"{name}: habitat_bbox {aoi.habitat_bbox} overhangs the depth grid "
            f"{footprint} — benthic claims where there is no bathymetry."
        )


class TestProvenance:
    @pytest.mark.parametrize("name", ALL)
    def test_bathymetry_uris_are_absolute(self, name):
        """A library AOI resolves nothing against a checkout."""
        uri = get_reference_aoi(name).fine_bathymetry.uri
        assert uri.startswith(("http://", "https://", "az://", "s3://")), uri

    @pytest.mark.parametrize("name", ALL)
    def test_the_release_year_is_always_known(self, name):
        """The WFS publishes it, so every shipped AOI has one."""
        assert get_reference_aoi(name).fine_bathymetry.product_year is not None

    @pytest.mark.parametrize("name", ALL)
    def test_survey_year_and_stability_are_set_together_or_not_at_all(self, name):
        """Both answer the same question: which survey is this, and how has it aged.

        EMODnet publishes neither — the WFS gives a product release. Knowing one
        therefore means knowing the source programme, which means knowing the
        other. A half-populated pair means something was inferred from the
        release year, and ``seabed_stability`` decides whether depth MAE may
        carry the accuracy claim at all.
        """
        fine = get_reference_aoi(name).fine_bathymetry
        assert (fine.survey_year is None) == (fine.seabed_stability is None)

    def test_sesimbra_records_the_survey_behind_the_release(self):
        """2011 DGT/APA airborne LiDAR over rocky reef — from the survey, not the WFS."""
        fine = get_reference_aoi("sesimbra").fine_bathymetry
        assert fine.survey_year == 2011
        assert fine.seabed_stability == "stable_rock"
        assert fine.product_year == 2020

    @pytest.mark.parametrize("name", sorted(EXPECTED - {"sesimbra"}))
    def test_a_habitat_area_is_claimed_only_where_there_is_ground_truth(self, name):
        """Absent means "no survey here yet", which is the truthful state."""
        assert get_reference_aoi(name).habitat_bbox is None

    @pytest.mark.parametrize("name", ALL)
    def test_attribution_carries_the_navigation_disclaimer(self, name):
        attribution = get_reference_aoi(name).fine_bathymetry.attribution
        assert "Not for navigation" in attribution
