"""EMODnet bathymetry adapter — field-name tolerance, selection, subsetting."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("rasterio")
xr = pytest.importorskip("xarray")

from oceanstream.coastal.aoi import AOI  # noqa: E402
from oceanstream.coastal.bathymetry.emodnet import (  # noqa: E402
    HRArea,
    _area_from_properties,
    _first,
    _to_int,
    discover_for_aoi,
    select_finest,
    subset_to_cog,
)
from oceanstream.coastal.io.rasters import read_band  # noqa: E402

# The Sesimbra HR LiDAR block the prototype validated against.
SESIMBRA_HR = {
    "identifier": "HR_Lidar_Sul",
    "resolution": "128",
    "download_url": "https://example.invalid/HR_Lidar_Sul.zip",
    "edmo_id": "590",
    "survey_year": "2011",
    "product_year": "2020",
}


def area(**overrides: object) -> HRArea:
    props = dict(SESIMBRA_HR)
    props.update(overrides)  # type: ignore[arg-type]
    return _area_from_properties(props)


class TestFieldNameTolerance:
    def test_first_returns_the_first_populated_key(self) -> None:
        assert _first({"b": "x"}, "a", "b", "c") == "x"

    def test_first_skips_empty_values(self) -> None:
        # EMODnet returns "" rather than omitting the key, and an empty string
        # would otherwise silently win over a populated later alias.
        assert _first({"a": "", "b": "x"}, "a", "b") == "x"

    def test_first_skips_none(self) -> None:
        assert _first({"a": None, "b": "x"}, "a", "b") == "x"

    def test_first_returns_none_when_nothing_matches(self) -> None:
        assert _first({"z": "x"}, "a", "b") is None

    def test_first_is_case_insensitive(self) -> None:
        assert _first({"Download_URL": "x"}, "download_url") == "x"

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [("128", 128), (128, 128), (128.0, 128), ("2011-06-01", 2011), ("", None), (None, None)],
    )
    def test_to_int(self, raw: object, expected: int | None) -> None:
        assert _to_int(raw) == expected

    def test_to_int_on_nonsense_is_none_not_an_exception(self) -> None:
        # A schema drift in one optional field should not abort discovery of
        # every other area in the response.
        assert _to_int("not a number") is None

    def test_area_survives_a_completely_unknown_schema(self) -> None:
        a = _area_from_properties({"totally": "different"})
        assert a.identifier == "unknown"
        assert a.download_url is None


class TestHRArea:
    def test_resolution_converts_arcminute_denominator_to_metres(self) -> None:
        # 1/128 arcminute is the Sesimbra LiDAR grid: 1852/128 ~ 14.5 m.
        assert area().resolution_m == pytest.approx(1852.0 / 128.0, rel=1e-6)

    def test_resolution_is_none_when_unknown(self) -> None:
        assert area(resolution="").resolution_m is None

    def test_to_reference_carries_the_survey_and_product_years(self) -> None:
        # Two different years: the survey is when the seabed was measured, the
        # product is when it was gridded. Depth-validation credibility depends
        # on the former.
        ref = area().to_reference("az://products/hr.tif")
        assert ref.survey_year == 2011
        assert ref.product_year == 2020

    def test_to_reference_defaults_to_lat(self) -> None:
        assert "LAT" in area().to_reference("hr.tif").vertical_datum

    def test_to_reference_propagates_stability(self) -> None:
        ref = area().to_reference("hr.tif", seabed_stability="stable_rock")
        assert ref.seabed_stability == "stable_rock"

    def test_to_reference_records_provenance(self) -> None:
        ref = area().to_reference("hr.tif")
        assert ref.provenance["identifier"] == "HR_Lidar_Sul"
        assert ref.attribution


class TestSelectFinest:
    def test_picks_the_largest_denominator(self) -> None:
        coarse = area(identifier="coarse", resolution="16")
        fine = area(identifier="fine", resolution="128")
        assert (
            select_finest(
                sorted([coarse, fine], key=lambda a: -(a.resolution_arcmin_denom or 0))
            ).identifier
            == "fine"
        )

    def test_skips_an_area_with_no_download_url(self) -> None:
        # A coverage polygon without a URL is a catalogue entry, not data.
        undownloadable = area(identifier="fine", resolution="128", download_url="")
        downloadable = area(identifier="coarse", resolution="16")
        assert select_finest([undownloadable, downloadable]).identifier == "coarse"

    def test_no_downloadable_area_suggests_the_standard_dtm(self) -> None:
        with pytest.raises(ValueError, match="standard EMODnet DTM"):
            select_finest([area(download_url="")])

    def test_empty_list(self) -> None:
        with pytest.raises(ValueError, match="No EMODnet HR area"):
            select_finest([])


class TestDiscoveryUsesTheProcessingExtent:
    def test_passes_the_processing_bbox_not_the_habitat_bbox(self, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        # Bathymetry has to cover the whole processing grid; fetching only the
        # habitat box would leave the deep-water reference without depths.
        seen: list[tuple[float, ...]] = []

        def fake(bbox, timeout_s=60.0):  # type: ignore[no-untyped-def]
            seen.append(bbox)
            return []

        monkeypatch.setattr("oceanstream.coastal.bathymetry.emodnet.discover_hr_areas", fake)
        aoi = AOI(
            name="sesimbra",
            processing_bbox=(-9.300, 38.150, -8.800, 38.550),
            habitat_bbox=(-9.212, 38.400, -9.192, 38.420),
        )
        discover_for_aoi(aoi)
        assert seen == [(-9.300, 38.150, -8.800, 38.550)]


class TestSubsetToCOG:
    def _dataset(self, path: Path) -> Path:
        """A 4x4 tile: south-to-north latitudes, elevation negative in water."""
        lat = np.array([38.40, 38.41, 38.42, 38.43])
        lon = np.array([-9.22, -9.21, -9.20, -9.19])
        elevation = np.array(
            [
                [-30.0, -25.0, -20.0, -15.0],
                [-24.0, -20.0, -16.0, -12.0],
                [-18.0, -14.0, -10.0, -6.0],
                [-12.0, -8.0, 5.0, 12.0],  # two land pixels
            ]
        )
        xr.Dataset(
            {"elevation": (("lat", "lon"), elevation)},
            coords={"lat": lat, "lon": lon},
        ).to_netcdf(path)
        return path

    def test_flips_elevation_into_positive_down_depth(self, tmp_path: Path) -> None:
        # EMODnet ships elevation (negative below sea level); every consumer
        # downstream expects depth (positive down). Getting this backwards makes
        # the whole scene "land".
        source = self._dataset(tmp_path / "src.nc")
        out = tmp_path / "depth.tif"
        subset_to_cog(source, out, bbox=(-9.23, 38.39, -9.18, 38.44))
        depth, _ = read_band(out)
        assert np.nanmin(depth) > 0.0
        assert np.nanmax(depth) == pytest.approx(30.0)

    def test_orients_rows_north_to_south(self, tmp_path: Path) -> None:
        # NetCDF is usually south-first; GeoTIFF rows run north-first. A missed
        # flip mirrors the bathymetry and every depth comparison with it.
        source = self._dataset(tmp_path / "src.nc")
        out = tmp_path / "depth.tif"
        subset_to_cog(source, out, bbox=(-9.23, 38.39, -9.18, 38.44))
        _, grid = read_band(out)
        assert grid.transform[4] < 0

    def test_deepest_water_ends_up_in_the_south(self, tmp_path: Path) -> None:
        source = self._dataset(tmp_path / "src.nc")
        out = tmp_path / "depth.tif"
        subset_to_cog(source, out, bbox=(-9.23, 38.39, -9.18, 38.44))
        depth, _ = read_band(out)
        assert np.nanmean(depth[-1, :]) > np.nanmean(depth[0, :])

    def test_land_becomes_nan_not_a_negative_depth(self, tmp_path: Path) -> None:
        source = self._dataset(tmp_path / "src.nc")
        out = tmp_path / "depth.tif"
        subset_to_cog(source, out, bbox=(-9.23, 38.39, -9.18, 38.44))
        depth, _ = read_band(out)
        assert np.isnan(depth).sum() == 2

    def test_returns_provenance(self, tmp_path: Path) -> None:
        source = self._dataset(tmp_path / "src.nc")
        result = subset_to_cog(
            source, tmp_path / "depth.tif", bbox=(-9.23, 38.39, -9.18, 38.44), area=area()
        )
        assert "LAT" in result["vertical_datum"]
        assert result["positive"] == "down"

    def test_writes_a_provenance_sidecar(self, tmp_path: Path) -> None:
        source = self._dataset(tmp_path / "src.nc")
        out = tmp_path / "depth.tif"
        subset_to_cog(source, out, bbox=(-9.23, 38.39, -9.18, 38.44), area=area())
        assert (tmp_path / "depth.tif.provenance.json").exists()

    def test_tags_travel_inside_the_geotiff(self, tmp_path: Path) -> None:
        import rasterio

        source = self._dataset(tmp_path / "src.nc")
        out = tmp_path / "depth.tif"
        subset_to_cog(source, out, bbox=(-9.23, 38.39, -9.18, 38.44), area=area())
        with rasterio.open(out) as src:
            assert "LAT" in src.tags()["vertical_datum"]

    def test_unknown_variable_names_what_is_available(self, tmp_path: Path) -> None:
        source = self._dataset(tmp_path / "src.nc")
        with pytest.raises(KeyError, match="elevation"):
            subset_to_cog(
                source, tmp_path / "d.tif", bbox=(-9.23, 38.39, -9.18, 38.44), variable="depth"
            )

    def test_bbox_outside_coverage_is_an_actionable_error(self, tmp_path: Path) -> None:
        source = self._dataset(tmp_path / "src.nc")
        with pytest.raises(ValueError, match="does not cover bbox"):
            subset_to_cog(source, tmp_path / "d.tif", bbox=(10.0, 10.0, 11.0, 11.0))
