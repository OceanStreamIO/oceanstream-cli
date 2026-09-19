"""EMODnet bathymetry adapter — field-name tolerance, selection, subsetting."""

from __future__ import annotations

import zipfile
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
    discover_hr_areas,
    extract_netcdf,
    fetch_global_dtm,
    global_dtm_to_cog,
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


# One real feature, captured verbatim from
# https://ows.emodnet-bathymetry.eu/wfs on 2026-09-10. Kept literal because the
# invented fixture above (survey_year / product_year, neither of which the
# service emits) is what let a schema mismatch survive review.
LIVE_FEATURE = {
    "edmo_id": 590,
    "identifier": "HR_Lidar_Sul",
    "resolution": 128,
    "release": 2020,
    "download_url": "https://downloads.emodnet-bathymetry.eu/high_resolution/590_HR_Lidar_Sul.emo.zip",
    "metadata_url": "https://emodnet.ec.europa.eu/geonetwork/srv/eng/catalog.search#/metadata/x",
}


class _FakeRequests:
    """Records every GET and replays a scripted feature list per call."""

    def __init__(self, *responses: list[dict[str, object]]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, object]] = []

    def get(self, url, params=None, timeout=None, **kw):  # type: ignore[no-untyped-def]
        self.calls.append(dict(params or {}))
        features = self._responses.pop(0) if self._responses else []
        return _FakeResponse({"features": [{"properties": p} for p in features]})


class _FakeResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, object]:
        return self._payload


def _patch_requests(monkeypatch, fake: _FakeRequests) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(
        "oceanstream.coastal.bathymetry.emodnet._require_requests", lambda: fake
    )


class TestDiscoveryRequestShape:
    """The request itself, which had no coverage until it silently broke.

    A spec-correct WFS 1.1.0 latitude-first bbox returns 200 OK with zero
    features from this server. Nothing raises, so the caller reads "no HR
    coverage" and falls back to the 115 m DTM.
    """

    def test_bbox_is_longitude_first(self, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        fake = _FakeRequests([LIVE_FEATURE])
        _patch_requests(monkeypatch, fake)
        discover_hr_areas((-9.15, 38.39, -8.90, 38.51))
        assert fake.calls[0]["bbox"] == "-9.15,38.39,-8.9,38.51"

    def test_uses_wfs_1_0_0_where_lon_first_is_unambiguous(self, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        fake = _FakeRequests([LIVE_FEATURE])
        _patch_requests(monkeypatch, fake)
        discover_hr_areas((-9.15, 38.39, -8.90, 38.51))
        assert fake.calls[0]["version"] == "1.0.0"

    def test_parses_the_live_schema(self, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        fake = _FakeRequests([LIVE_FEATURE])
        _patch_requests(monkeypatch, fake)
        (found,) = discover_hr_areas((-9.15, 38.39, -8.90, 38.51))
        assert found.identifier == "HR_Lidar_Sul"
        assert found.edmo_id == 590
        assert found.resolution_m == pytest.approx(1852 / 128)
        # "release", not "product_year" — the live field name.
        assert found.product_year == 2020

    def test_survey_year_is_none_rather_than_guessed(self, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        # The service does not publish it. Inferring 2011 from an identifier
        # would feed AOI.depth_validation_is_primary a number nobody measured.
        fake = _FakeRequests([LIVE_FEATURE])
        _patch_requests(monkeypatch, fake)
        (found,) = discover_hr_areas((-9.15, 38.39, -8.90, 38.51))
        assert found.survey_year is None

    def test_multipart_coverage_is_reported_once(self, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        # Lough Swilly/Foyle comes back six times for one 7 m dataset.
        fake = _FakeRequests([dict(LIVE_FEATURE) for _ in range(6)])
        _patch_requests(monkeypatch, fake)
        assert len(discover_hr_areas((-8.6, 54.8, -6.9, 55.5))) == 1

    def test_genuinely_empty_coverage_stays_empty(self, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        # Galicia has no HR tile. Both orderings return nothing, and that is
        # the correct answer, not a retry loop.
        fake = _FakeRequests([], [])
        _patch_requests(monkeypatch, fake)
        assert discover_hr_areas((-9.1, 42.1, -8.6, 42.7)) == []
        assert len(fake.calls) == 2

    def test_falls_back_to_lat_first_and_warns(self, monkeypatch, caplog) -> None:  # type: ignore[no-untyped-def]
        # If EMODnet ever becomes spec-compliant, the primary query goes empty.
        # Recovering silently would hide the drift until the next schema change.
        fake = _FakeRequests([], [LIVE_FEATURE])
        _patch_requests(monkeypatch, fake)
        with caplog.at_level("WARNING"):
            found = discover_hr_areas((-9.15, 38.39, -8.90, 38.51))
        assert len(found) == 1
        assert fake.calls[1]["bbox"] == "38.39,-9.15,38.51,-8.9,EPSG:4326"
        assert "axis-order" in caplog.text


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


class TestVerticalConvention:
    """EMODnet documents positive-up elevation. Not every contributor obeys it.

    The Lough Swilly / Foyle grid (INFOMAR, EDMO 366) stores positive-down depth
    under variable attributes byte-identical to Sesimbra's positive-up elevation
    — same ``SDN:P01::HGHTALAT``, same units, same long name. Negating it put
    every cell above the datum, the land mask then removed all of them, and the
    CLI reported a depth COG with zero valid cells as a success.
    """

    BBOX = (-7.66, 55.14, -7.61, 55.19)

    def _grid(self, path: Path, values: np.ndarray) -> Path:
        lat = np.array([55.15, 55.16, 55.17, 55.18])
        lon = np.array([-7.65, -7.64, -7.63, -7.62])
        xr.Dataset(
            {"elevation": (("lat", "lon"), values)}, coords={"lat": lat, "lon": lon}
        ).to_netcdf(path)
        return path

    def test_an_all_positive_grid_is_depth_and_survives(self, tmp_path: Path) -> None:
        source = self._grid(tmp_path / "depth_convention.nc", np.linspace(2, 34, 16).reshape(4, 4))
        out = tmp_path / "d.tif"
        result = subset_to_cog(source, out, bbox=self.BBOX)
        depth, _ = read_band(out)
        assert result["source_positive"] == "down"
        assert np.isfinite(depth).sum() == 16
        assert np.nanmax(depth) == pytest.approx(34.0)

    def test_a_mostly_negative_grid_is_elevation_and_is_flipped(self, tmp_path: Path) -> None:
        source = self._grid(tmp_path / "elev.nc", np.linspace(-34, -2, 16).reshape(4, 4))
        result = subset_to_cog(source, tmp_path / "d.tif", bbox=self.BBOX)
        assert result["source_positive"] == "up"

    def test_an_undecidable_mix_refuses_rather_than_guesses(self, tmp_path: Path) -> None:
        values = np.full(16, 10.0)
        values[0] = -1.0  # 6.25%: too many for depth, too few for a survey
        source = self._grid(tmp_path / "mixed.nc", values.reshape(4, 4))
        with pytest.raises(ValueError, match="Cannot tell whether"):
            subset_to_cog(source, tmp_path / "d.tif", bbox=self.BBOX)

    def test_the_caller_can_assert_the_convention(self, tmp_path: Path) -> None:
        values = np.full(16, 10.0)
        values[0] = -1.0
        source = self._grid(tmp_path / "mixed.nc", values.reshape(4, 4))
        result = subset_to_cog(
            source, tmp_path / "d.tif", bbox=self.BBOX, source_positive="down"
        )
        assert result["source_positive_evidence"] == "asserted by caller"

    def test_an_empty_result_is_an_error_not_an_empty_product(self, tmp_path: Path) -> None:
        # What the Donegal run did silently before: 1.9M cells in, zero out.
        source = self._grid(tmp_path / "elev.nc", np.linspace(-34, -2, 16).reshape(4, 4))
        with pytest.raises(ValueError, match="no valid depth cells"):
            subset_to_cog(source, tmp_path / "d.tif", bbox=self.BBOX, source_positive="down")


class TestArchiveExtraction:
    """``download_area`` returns the ``.emo.zip``; xarray cannot read one."""

    def _archive(self, tmp_path: Path, members: dict[str, bytes]) -> Path:
        archive = tmp_path / "366_Example.emo.zip"
        with zipfile.ZipFile(archive, "w") as bundle:
            for name, payload in members.items():
                bundle.writestr(name, payload)
        return archive

    def test_picks_the_netcdf_and_leaves_the_emo_grid_alone(self, tmp_path: Path) -> None:
        # The .emo member is the reason this is not a blanket extractall: at
        # Lough Swilly it is 307 MB against 14 MB for the NetCDF.
        archive = self._archive(
            tmp_path,
            {
                "366_Example.emo": b"x" * 4096,
                "366_Example.nc": b"CDF\x01payload",
                "SDN_CPRD_366_Example.zip": b"PK\x03\x04",
            },
        )
        extracted = extract_netcdf(archive)
        assert extracted.name == "366_Example.nc"
        assert extracted.read_bytes() == b"CDF\x01payload"
        assert not (tmp_path / "366_Example.emo").exists()

    def test_reuses_an_existing_extraction(self, tmp_path: Path) -> None:
        archive = self._archive(tmp_path, {"366_Example.nc": b"CDF\x01payload"})
        first = extract_netcdf(archive)
        first.write_bytes(b"CDF\x01edited")
        assert extract_netcdf(archive).read_bytes() == b"CDF\x01edited"

    def test_an_archive_without_a_grid_lists_what_it_holds(self, tmp_path: Path) -> None:
        archive = self._archive(tmp_path, {"readme.txt": b"nothing here"})
        with pytest.raises(ValueError, match="readme.txt"):
            extract_netcdf(archive)

    def test_subset_accepts_the_downloaded_archive_directly(self, tmp_path: Path) -> None:
        lat = np.array([38.40, 38.41])
        lon = np.array([-9.22, -9.21])
        nc = tmp_path / "590_Example.nc"
        xr.Dataset(
            {"elevation": (("lat", "lon"), np.array([[-30.0, -25.0], [-20.0, -15.0]]))},
            coords={"lat": lat, "lon": lon},
        ).to_netcdf(nc)
        archive = tmp_path / "590_Example.emo.zip"
        with zipfile.ZipFile(archive, "w") as bundle:
            bundle.write(nc, arcname="590_Example.nc")
        nc.unlink()

        out = tmp_path / "d.tif"
        subset_to_cog(archive, out, bbox=(-9.23, 38.39, -9.20, 38.42))
        depth, _ = read_band(out)
        assert np.nanmax(depth) == pytest.approx(30.0)


class _FakeWCS:
    """Replays one scripted byte payload and records the request parameters."""

    def __init__(self, payload: bytes) -> None:
        self.payload = payload
        self.calls: list[dict[str, object]] = []

    def get(self, url, params=None, **kw):  # type: ignore[no-untyped-def]
        self.calls.append({"url": url, **dict(params or {})})
        return _FakeStream(self.payload)


class _FakeStream:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def __enter__(self) -> _FakeStream:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def raise_for_status(self) -> None:
        return None

    def iter_content(self, chunk_size: int = 1):  # type: ignore[no-untyped-def]
        yield self._payload


class TestCoarseDTM:
    """The coarse DTM is fetched for one reason: to reach optically deep water.

    The HR LiDAR cannot. It stops returning around 29 m at Sesimbra and 34 m at
    Lough Swilly, and a deep-water reference read off a lit bottom is what sent
    every Lough Swilly k through the pure-water floor.
    """

    BBOX = (-9.30, 38.15, -8.80, 38.55)

    def _elevation_tif(self, path: Path, deepest_m: float) -> Path:
        import rasterio
        from rasterio.transform import from_origin

        # Positive-up elevation: land on top, seabed descending southwards.
        values = np.empty((40, 40), dtype="float32")
        values[:10, :] = 50.0
        values[10:, :] = -np.linspace(1.0, deepest_m, 30)[:, None]
        with rasterio.open(
            path, "w", driver="GTiff", height=40, width=40, count=1,
            dtype="float32", crs="EPSG:4326", nodata=float("nan"),
            transform=from_origin(-9.30, 38.55, 0.01, 0.01),
        ) as dst:
            dst.write(values, 1)
        return path

    def test_request_is_a_wcs_2_0_1_getcoverage(self, monkeypatch, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
        fake = _FakeWCS(b"II*\x00padding")
        _patch_requests(monkeypatch, fake)
        fetch_global_dtm(self.BBOX, tmp_path)
        call = fake.calls[0]
        assert call["version"] == "2.0.1"
        assert call["coverageId"] == "emodnet:mean"
        assert call["subset"] == ["Long(-9.3,-8.8)", "Lat(38.15,38.55)"]

    def test_an_xml_error_page_is_not_written_as_a_grid(self, monkeypatch, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
        # OWS services answer failure with HTTP 200 and an exception report.
        _patch_requests(
            monkeypatch,
            _FakeWCS(b'<?xml version="1.0"?><ExceptionReport>No such coverage</ExceptionReport>'),
        )
        with pytest.raises(ValueError, match="No such coverage"):
            fetch_global_dtm(self.BBOX, tmp_path)
        assert list(tmp_path.iterdir()) == []

    def test_reuses_an_existing_download(self, monkeypatch, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
        fake = _FakeWCS(b"II*\x00padding")
        _patch_requests(monkeypatch, fake)
        first = fetch_global_dtm(self.BBOX, tmp_path)
        again = fetch_global_dtm(self.BBOX, tmp_path)
        assert first == again
        assert len(fake.calls) == 1

    def test_elevation_is_flipped_and_terrain_removed(self, tmp_path: Path) -> None:
        source = self._elevation_tif(tmp_path / "raw.tif", deepest_m=1000.0)
        result = global_dtm_to_cog(source, tmp_path / "coarse.tif")
        depth, _ = read_band(tmp_path / "coarse.tif")
        assert result["source_positive"] == "up"
        # The 10 land rows are gone rather than present as negative depth.
        assert result["n_land_masked"] == 400
        assert np.nanmin(depth) > 0.0
        assert np.nanmax(depth) == pytest.approx(1000.0)

    def test_provenance_counts_the_deep_water_it_found(self, tmp_path: Path) -> None:
        source = self._elevation_tif(tmp_path / "raw.tif", deepest_m=1000.0)
        result = global_dtm_to_cog(source, tmp_path / "coarse.tif")
        assert result["deep_water_threshold_m"] == 50.0
        assert result["n_deep_water_cells"] > 200
        assert result["role"] == "coarse"

    def test_a_shelf_only_grid_refuses_rather_than_supplying_a_bad_reference(
        self, tmp_path: Path
    ) -> None:
        source = self._elevation_tif(tmp_path / "shallow.tif", deepest_m=30.0)
        with pytest.raises(ValueError, match="extended towards the shelf edge"):
            global_dtm_to_cog(source, tmp_path / "coarse.tif")

    def test_the_deep_water_floor_is_a_threshold_not_a_law(self, tmp_path: Path) -> None:
        # Lowering it is the wrong fix, but a caller who knows their site may.
        source = self._elevation_tif(tmp_path / "shallow.tif", deepest_m=60.0)
        result = global_dtm_to_cog(
            source, tmp_path / "coarse.tif", min_deep_water_cells=100
        )
        assert result["n_deep_water_cells"] >= 100
