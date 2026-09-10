"""AOI — geometry validation, derived CRS, serialisation round-trips."""

from __future__ import annotations

import json

import pytest

from oceanstream.coastal.aoi import AOI, BathymetryReference

# Sesimbra, matching the prototype's SESIMBRA site profile.
SESIMBRA_PROCESSING = (-9.300, 38.150, -8.800, 38.550)
SESIMBRA_HABITAT = (-9.212, 38.400, -9.192, 38.420)


def sesimbra(**overrides: object) -> AOI:
    kwargs: dict[str, object] = {
        "name": "sesimbra",
        "label": "Sesimbra / Arrábida",
        "processing_bbox": SESIMBRA_PROCESSING,
        "habitat_bbox": SESIMBRA_HABITAT,
    }
    kwargs.update(overrides)
    return AOI(**kwargs)  # type: ignore[arg-type]


class TestValidation:
    def test_minimal_aoi_needs_only_name_and_bbox(self) -> None:
        aoi = AOI(name="x", processing_bbox=SESIMBRA_PROCESSING)
        assert aoi.habitat_bbox is None
        assert aoi.display_label == "x"

    def test_label_falls_back_to_name(self) -> None:
        assert sesimbra(label=None).display_label == "sesimbra"

    def test_empty_name_rejected(self) -> None:
        with pytest.raises(ValueError, match="non-empty slug"):
            AOI(name="", processing_bbox=SESIMBRA_PROCESSING)

    @pytest.mark.parametrize(
        "bbox",
        [
            (-8.8, 38.15, -9.3, 38.55),  # west > east
            (-9.3, 38.55, -8.8, 38.15),  # south > north
        ],
    )
    def test_misordered_bbox_rejected(self, bbox: tuple[float, ...]) -> None:
        with pytest.raises(ValueError, match="west < east and south < north"):
            AOI(name="x", processing_bbox=bbox)  # type: ignore[arg-type]

    def test_out_of_range_longitude_rejected(self) -> None:
        with pytest.raises(ValueError, match=r"longitudes must lie in \[-180, 180\]"):
            AOI(name="x", processing_bbox=(-190.0, 38.0, -180.5, 39.0))

    def test_out_of_range_latitude_rejected(self) -> None:
        with pytest.raises(ValueError, match=r"latitudes must lie in \[-90, 90\]"):
            AOI(name="x", processing_bbox=(-9.0, -95.0, -8.0, -91.0))

    def test_habitat_bbox_must_sit_inside_processing_bbox(self) -> None:
        with pytest.raises(ValueError, match="not contained in processing_bbox"):
            sesimbra(habitat_bbox=(-9.5, 38.40, -9.4, 38.42))

    def test_habitat_bbox_may_equal_processing_bbox(self) -> None:
        aoi = sesimbra(habitat_bbox=SESIMBRA_PROCESSING)
        assert aoi.analysis_bbox == SESIMBRA_PROCESSING

    def test_inverted_depth_range_rejected(self) -> None:
        with pytest.raises(ValueError, match=r"\(min, max\) with min < max"):
            sesimbra(depth_valid_range_m=(40.0, 0.0))


class TestDerivedGeometry:
    def test_centroid(self) -> None:
        lon, lat = sesimbra().centroid
        assert lon == pytest.approx(-9.05)
        assert lat == pytest.approx(38.35)

    def test_utm_epsg_matches_prototype_hardcoded_zone(self) -> None:
        # The prototype hard-coded EPSG:32629 for Sesimbra; deriving it must
        # reproduce that, or every metric area in the port shifts.
        assert sesimbra().utm_epsg == 32629

    def test_utm_epsg_southern_hemisphere(self) -> None:
        southern = AOI(name="s", processing_bbox=(18.0, -35.0, 19.0, -34.0))
        assert southern.utm_epsg == 32734

    def test_acolite_limit_is_south_west_north_east(self) -> None:
        # ACOLITE's ordering is not the usual bbox ordering; a transposed limit
        # yields an empty subset rather than an error, so this is load-bearing.
        assert sesimbra().acolite_limit == (38.150, -9.300, 38.550, -8.800)

    def test_analysis_bbox_prefers_habitat(self) -> None:
        assert sesimbra().analysis_bbox == SESIMBRA_HABITAT

    def test_analysis_bbox_falls_back_to_processing(self) -> None:
        assert sesimbra(habitat_bbox=None).analysis_bbox == SESIMBRA_PROCESSING


class TestDepthValidationPolicy:
    def test_stable_rock_makes_depth_the_primary_claim(self) -> None:
        aoi = sesimbra(
            fine_bathymetry=BathymetryReference(
                uri="hr.tif", survey_year=2011, seabed_stability="stable_rock"
            )
        )
        assert aoi.depth_validation_is_primary

    def test_mobile_sediment_does_not(self) -> None:
        # Depth error over moving seabed confounds retrieval error with real
        # bathymetric change, so bottom reflectance has to carry the claim.
        aoi = sesimbra(
            fine_bathymetry=BathymetryReference(
                uri="hr.tif", survey_year=2011, seabed_stability="mobile_sediment"
            )
        )
        assert not aoi.depth_validation_is_primary

    def test_unknown_stability_does_not(self) -> None:
        aoi = sesimbra(fine_bathymetry=BathymetryReference(uri="hr.tif"))
        assert not aoi.depth_validation_is_primary

    def test_no_bathymetry_does_not(self) -> None:
        assert not sesimbra().depth_validation_is_primary

    def test_falls_back_to_coarse_when_no_fine_grid(self) -> None:
        aoi = sesimbra(
            coarse_bathymetry=BathymetryReference(uri="dtm.tif", seabed_stability="stable_rock")
        )
        assert aoi.depth_validation_is_primary


class TestSerialisation:
    def test_round_trip_preserves_everything(self) -> None:
        aoi = sesimbra(
            fine_bathymetry=BathymetryReference(
                uri="az://products/hr.tif",
                resolution_m=11.4,
                survey_year=2011,
                product_year=2020,
                seabed_stability="stable_rock",
                max_reliable_depth_m=29.0,
            ),
            external_ids={"earthstudio": "a3d3ba9f-d30f-4f14-9df1-3f9f444e60e7"},
        )
        assert AOI.from_dict(aoi.to_dict()) == aoi

    def test_round_trip_survives_json(self) -> None:
        aoi = sesimbra()
        assert AOI.from_dict(json.loads(json.dumps(aoi.to_dict()))) == aoi

    def test_from_json_file(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        path = tmp_path / "aoi.json"
        path.write_text(json.dumps(sesimbra().to_dict()))
        assert AOI.from_json(path) == sesimbra()

    def test_unknown_field_rejected_with_actionable_message(self) -> None:
        payload = sesimbra().to_dict()
        payload["clarity_bbox"] = [0, 0, 1, 1]
        with pytest.raises(ValueError, match="Put anything else under 'metadata'"):
            AOI.from_dict(payload)


class TestEarthStudioAdapter:
    def test_uses_bbox_when_present(self) -> None:
        aoi = AOI.from_earthstudio(
            {
                "id": "a3d3ba9f-d30f-4f14-9df1-3f9f444e60e7",
                "slug": "sesimbra",
                "name": "Sesimbra / Arrábida",
                "bbox": [-9.3, 38.15, -8.8, 38.55],
            }
        )
        assert aoi.name == "sesimbra"
        assert aoi.label == "Sesimbra / Arrábida"
        assert aoi.processing_bbox == SESIMBRA_PROCESSING

    def test_preserves_the_uuid_so_products_stay_traceable(self) -> None:
        aoi = AOI.from_earthstudio({"id": "abc", "slug": "x", "bbox": [0, 0, 1, 1]})
        assert aoi.external_ids["earthstudio"] == "abc"

    def test_derives_bbox_from_polygon_geometry(self) -> None:
        aoi = AOI.from_earthstudio(
            {
                "id": "abc",
                "slug": "poly",
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [
                        [[-9.3, 38.15], [-8.8, 38.15], [-8.8, 38.55], [-9.3, 38.55], [-9.3, 38.15]]
                    ],
                },
            }
        )
        assert aoi.processing_bbox == SESIMBRA_PROCESSING

    def test_derives_bbox_from_multipolygon(self) -> None:
        aoi = AOI.from_earthstudio(
            {
                "slug": "multi",
                "geometry": {
                    "type": "MultiPolygon",
                    "coordinates": [
                        [[[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 0.0]]],
                        [[[2.0, 2.0], [3.0, 2.0], [3.0, 3.0], [2.0, 2.0]]],
                    ],
                },
            }
        )
        assert aoi.processing_bbox == (0.0, 0.0, 3.0, 3.0)

    def test_derives_bbox_from_geometry_collection(self) -> None:
        aoi = AOI.from_earthstudio(
            {
                "slug": "gc",
                "geometry": {
                    "type": "GeometryCollection",
                    "geometries": [
                        {"type": "Point", "coordinates": [-9.3, 38.15]},
                        {"type": "Point", "coordinates": [-8.8, 38.55]},
                    ],
                },
            }
        )
        assert aoi.processing_bbox == SESIMBRA_PROCESSING

    def test_no_geometry_and_no_bbox_is_an_error(self) -> None:
        with pytest.raises(ValueError, match="neither 'bbox' nor 'geometry'"):
            AOI.from_earthstudio({"id": "abc", "slug": "x"})

    def test_unnameable_record_is_an_error(self) -> None:
        with pytest.raises(ValueError, match="no 'slug', 'name' or 'id'"):
            AOI.from_earthstudio({"bbox": [0, 0, 1, 1]})
