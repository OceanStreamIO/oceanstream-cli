"""Behaviour of the coastal STAC emitter.

The claims worth pinning are the ones that decide whether a catalogue entry
can be trusted: that a projected grid is reprojected before it becomes a bbox
(a UTM easting read as a longitude puts Sesimbra in the Gulf of Guinea), that
the QC verdict rides in the item's properties rather than a log, that a failed
scene announces itself in its own description, and that re-running a scene
updates the collection instead of duplicating it.
"""

from __future__ import annotations

import json

import numpy as np
import pytest

from oceanstream.coastal.io.rasters import RasterGrid
from oceanstream.coastal.products import ProductWriter
from oceanstream.coastal.stac.coastal_emit import (
    STAC_VERSION,
    build_collection,
    build_item,
    collection_id_for,
    emit_stac,
    geographic_bounds,
    item_id,
    merge_item_into_collection,
)

rasterio = pytest.importorskip("rasterio")

SESIMBRA_BBOX = (-9.05, 38.40, -8.95, 38.48)


@pytest.fixture
def geographic_grid() -> RasterGrid:
    return RasterGrid(
        width=10,
        height=8,
        transform=(0.01, 0.0, -9.05, 0.0, -0.01, 38.48),
        crs="EPSG:4326",
    )


@pytest.fixture
def utm_grid() -> RasterGrid:
    """Sesimbra in UTM 29N — eastings near 495000, northings near 4256000."""
    return RasterGrid(
        width=100,
        height=80,
        transform=(10.0, 0.0, 495000.0, 0.0, -10.0, 4256000.0),
        crs="EPSG:32629",
    )


@pytest.fixture
def passing_verdict() -> dict:
    return {"passed": True, "summary": "all bands above the pure-water floor", "flags": []}


@pytest.fixture
def failing_verdict() -> dict:
    return {
        "passed": False,
        "summary": "k(667) sits 12x below the pure-water floor",
        "flags": ["pure_water_floor_violated"],
    }


def _products(tmp_path, grid, verdict) -> dict:
    writer = ProductWriter(tmp_path, grid, verdict, {"aoi": "sesimbra"})
    writer.write_raster("sdb_depth", np.full(grid.shape, 12.0, np.float32))
    writer.write_raster("rho_b_561", np.full(grid.shape, 0.04, np.float32))
    writer.write_json("qc", {"floors": verdict})
    return writer.products


# ---------------------------------------------------------------------------
# Geometry
# ---------------------------------------------------------------------------


class TestGeographicBounds:
    def test_a_geographic_grid_passes_through(self, geographic_grid):
        west, south, east, north = geographic_bounds(geographic_grid)
        assert west == pytest.approx(-9.05)
        assert north == pytest.approx(38.48)

    def test_a_utm_grid_is_reprojected_not_reinterpreted(self, utm_grid):
        west, south, east, north = geographic_bounds(utm_grid)
        assert -10.0 < west < -8.0, "UTM eastings must not be emitted as longitude"
        assert 38.0 < south < 39.0, "UTM northings must not be emitted as latitude"
        assert west < east and south < north

    def test_a_utm_grid_would_otherwise_catalogue_the_scene_off_africa(self, utm_grid):
        """The raw bounds are numerically valid lon/lat, which is why this matters."""
        raw_west, raw_south, _, _ = utm_grid.bounds
        assert raw_west > 180 or raw_south > 90, (
            "if raw UTM bounds ever fall inside lon/lat range the silent-failure "
            "mode this guards against becomes undetectable"
        )

    def test_an_item_built_from_a_utm_grid_has_a_lonlat_bbox(
        self, tmp_path, utm_grid, passing_verdict
    ):
        item = build_item(
            products=_products(tmp_path, utm_grid, passing_verdict),
            grid=utm_grid,
            qc_verdict=passing_verdict,
            aoi_name="sesimbra",
            sensor="sentinel2",
            acquisition_date="2026-06-27",
            stac_dir=tmp_path / "stac",
        )
        west, south, east, north = item["bbox"]
        assert -180 <= west < east <= 180
        assert -90 <= south < north <= 90
        assert item["geometry"]["type"] == "Polygon"


# ---------------------------------------------------------------------------
# The verdict rides in the item
# ---------------------------------------------------------------------------


class TestVerdictInProperties:
    def test_the_verdict_is_a_property_not_a_log_line(
        self, tmp_path, geographic_grid, failing_verdict
    ):
        item = build_item(
            products=_products(tmp_path, geographic_grid, failing_verdict),
            grid=geographic_grid,
            qc_verdict=failing_verdict,
            aoi_name="sesimbra",
            sensor="sentinel2",
            acquisition_date="2026-06-27",
            stac_dir=tmp_path / "stac",
        )
        assert item["properties"]["oceanstream:qc"]["passed"] is False
        assert "pure_water_floor_violated" in item["properties"]["oceanstream:qc"]["flags"]

    def test_a_failed_scene_says_so_in_its_own_description(
        self, tmp_path, geographic_grid, failing_verdict
    ):
        item = build_item(
            products=_products(tmp_path, geographic_grid, failing_verdict),
            grid=geographic_grid,
            qc_verdict=failing_verdict,
            aoi_name="sesimbra",
            sensor="sentinel2",
            acquisition_date="2026-06-27",
            stac_dir=tmp_path / "stac",
        )
        assert item["properties"]["description"].startswith("DIAGNOSTIC ONLY")
        assert "12x below" in item["properties"]["description"]

    def test_a_passing_scene_does_not_carry_the_warning(
        self, tmp_path, geographic_grid, passing_verdict
    ):
        item = build_item(
            products=_products(tmp_path, geographic_grid, passing_verdict),
            grid=geographic_grid,
            qc_verdict=passing_verdict,
            aoi_name="sesimbra",
            sensor="sentinel2",
            acquisition_date="2026-06-27",
            stac_dir=tmp_path / "stac",
        )
        assert not item["properties"]["description"].startswith("DIAGNOSTIC ONLY")

    def test_an_item_cannot_be_built_without_a_verdict(
        self, tmp_path, geographic_grid, passing_verdict
    ):
        with pytest.raises(ValueError, match="passed"):
            build_item(
                products=_products(tmp_path, geographic_grid, passing_verdict),
                grid=geographic_grid,
                qc_verdict={"summary": "no gate was run"},
                aoi_name="sesimbra",
                sensor="sentinel2",
                acquisition_date="2026-06-27",
                stac_dir=tmp_path / "stac",
            )


# ---------------------------------------------------------------------------
# Assets
# ---------------------------------------------------------------------------


class TestAssets:
    def test_every_product_becomes_an_asset(
        self, tmp_path, geographic_grid, passing_verdict
    ):
        products = _products(tmp_path, geographic_grid, passing_verdict)
        item = build_item(
            products=products,
            grid=geographic_grid,
            qc_verdict=passing_verdict,
            aoi_name="sesimbra",
            sensor="sentinel2",
            acquisition_date="2026-06-27",
            stac_dir=tmp_path / "stac",
        )
        assert set(item["assets"]) == set(products)

    def test_the_rho_b_caveat_reaches_the_asset_description(
        self, tmp_path, geographic_grid, passing_verdict
    ):
        item = build_item(
            products=_products(tmp_path, geographic_grid, passing_verdict),
            grid=geographic_grid,
            qc_verdict=passing_verdict,
            aoi_name="sesimbra",
            sensor="sentinel2",
            acquisition_date="2026-06-27",
            stac_dir=tmp_path / "stac",
        )
        description = item["assets"]["rho_b_561"]["description"]
        assert "not a material property" in description

    def test_local_asset_hrefs_are_relative_to_the_catalogue(
        self, tmp_path, geographic_grid, passing_verdict
    ):
        """Items sit below the products, so hrefs have to walk back up.

        An absolute href here produces a catalogue that resolves on the machine
        that wrote it and nowhere else.
        """
        item = build_item(
            products=_products(tmp_path, geographic_grid, passing_verdict),
            grid=geographic_grid,
            qc_verdict=passing_verdict,
            aoi_name="sesimbra",
            sensor="sentinel2",
            acquisition_date="2026-06-27",
            stac_dir=tmp_path / "stac" / "items",
        )
        for key, asset in item["assets"].items():
            assert not asset["href"].startswith("/")
            assert asset["href"].startswith("../../"), key
            resolved = (tmp_path / "stac" / "items" / asset["href"]).resolve()
            assert resolved.exists(), f"{key} href does not resolve to a file"

    def test_raster_assets_declare_the_cog_media_type(
        self, tmp_path, geographic_grid, passing_verdict
    ):
        item = build_item(
            products=_products(tmp_path, geographic_grid, passing_verdict),
            grid=geographic_grid,
            qc_verdict=passing_verdict,
            aoi_name="sesimbra",
            sensor="sentinel2",
            acquisition_date="2026-06-27",
            stac_dir=tmp_path / "stac",
        )
        assert "cloud-optimized" in item["assets"]["sdb_depth"]["type"]
        assert item["assets"]["qc"]["type"] == "application/json"


# ---------------------------------------------------------------------------
# Identity and collections
# ---------------------------------------------------------------------------


class TestIdentity:
    def test_the_item_id_is_deterministic_so_a_rerun_replaces_rather_than_duplicates(
        self,
    ):
        first = item_id("Sesimbra / Arrábida", "sentinel2", "2026-06-27")
        second = item_id("Sesimbra / Arrábida", "sentinel2", "2026-06-27")
        assert first == second

    def test_the_item_id_survives_punctuation_in_the_aoi_name(self):
        assert "/" not in item_id("Sesimbra / Arrábida", "sentinel2", "2026-06-27")
        assert " " not in item_id("Sesimbra / Arrábida", "sentinel2", "2026-06-27")

    def test_different_dates_are_different_items(self):
        assert item_id("sesimbra", "sentinel2", "2026-06-27") != item_id(
            "sesimbra", "sentinel2", "2026-07-05"
        )

    def test_the_collection_id_is_derived_from_the_aoi(self):
        assert collection_id_for("Sesimbra / Arrábida").startswith("coastal-")


class TestCollectionMerge:
    def _item(self, tmp_path, grid, verdict, date):
        return build_item(
            products=_products(tmp_path, grid, verdict),
            grid=grid,
            qc_verdict=verdict,
            aoi_name="sesimbra",
            sensor="sentinel2",
            acquisition_date=date,
            stac_dir=tmp_path / "stac",
        )

    def test_merging_the_same_item_twice_leaves_one_link(
        self, tmp_path, geographic_grid, passing_verdict
    ):
        collection = build_collection(
            collection=collection_id_for("sesimbra"),
            aoi_name="sesimbra",
            bbox=SESIMBRA_BBOX,
            interval=("2026-06-27T00:00:00Z", "2026-06-27T00:00:00Z"),
        )
        item = self._item(tmp_path, geographic_grid, passing_verdict, "2026-06-27")
        merge_item_into_collection(collection, item)
        merge_item_into_collection(collection, item)
        item_links = [link for link in collection["links"] if link["rel"] == "item"]
        assert len(item_links) == 1

    def test_a_later_scene_widens_the_temporal_extent(
        self, tmp_path, geographic_grid, passing_verdict
    ):
        collection = build_collection(
            collection=collection_id_for("sesimbra"),
            aoi_name="sesimbra",
            bbox=SESIMBRA_BBOX,
            interval=("2026-06-27T00:00:00Z", "2026-06-27T00:00:00Z"),
        )
        merge_item_into_collection(
            collection,
            self._item(tmp_path, geographic_grid, passing_verdict, "2026-09-02"),
        )
        _, end = collection["extent"]["temporal"]["interval"][0]
        assert end.startswith("2026-09-02")

    def test_a_rejected_scene_is_visible_in_the_collection_summary(
        self, tmp_path, geographic_grid, passing_verdict, failing_verdict
    ):
        collection = build_collection(
            collection=collection_id_for("sesimbra"),
            aoi_name="sesimbra",
            bbox=SESIMBRA_BBOX,
            interval=("2026-06-27T00:00:00Z", "2026-06-27T00:00:00Z"),
        )
        merge_item_into_collection(
            collection,
            self._item(tmp_path, geographic_grid, passing_verdict, "2026-06-27"),
        )
        merge_item_into_collection(
            collection,
            self._item(tmp_path, geographic_grid, failing_verdict, "2026-07-05"),
        )
        statuses = collection["summaries"]["oceanstream:status"]
        assert "publishable" in statuses
        assert "diagnostic_only" in statuses


# ---------------------------------------------------------------------------
# Emission
# ---------------------------------------------------------------------------


class TestEmitStac:
    def test_emitting_writes_a_catalogue_next_to_the_products(
        self, tmp_path, geographic_grid, passing_verdict
    ):
        collection_path, item_path = emit_stac(
            tmp_path,
            products=_products(tmp_path, geographic_grid, passing_verdict),
            grid=geographic_grid,
            qc_verdict=passing_verdict,
            aoi_name="sesimbra",
            sensor="sentinel2",
            acquisition_date="2026-06-27",
        )
        assert collection_path.exists()
        assert item_path.exists()
        item = json.loads(item_path.read_text())
        assert item["stac_version"] == STAC_VERSION
        assert item["type"] == "Feature"

    def test_a_second_scene_joins_the_existing_collection(
        self, tmp_path, geographic_grid, passing_verdict
    ):
        for date in ("2026-06-27", "2026-07-05"):
            collection_path, _ = emit_stac(
                tmp_path,
                products=_products(tmp_path, geographic_grid, passing_verdict),
                grid=geographic_grid,
                qc_verdict=passing_verdict,
                aoi_name="sesimbra",
                sensor="sentinel2",
                acquisition_date=date,
            )
        collection = json.loads(collection_path.read_text())
        item_links = [link for link in collection["links"] if link["rel"] == "item"]
        assert len(item_links) == 2

    def test_re_emitting_the_same_scene_does_not_duplicate_it(
        self, tmp_path, geographic_grid, passing_verdict
    ):
        for _ in range(3):
            collection_path, _ = emit_stac(
                tmp_path,
                products=_products(tmp_path, geographic_grid, passing_verdict),
                grid=geographic_grid,
                qc_verdict=passing_verdict,
                aoi_name="sesimbra",
                sensor="sentinel2",
                acquisition_date="2026-06-27",
            )
        collection = json.loads(collection_path.read_text())
        item_links = [link for link in collection["links"] if link["rel"] == "item"]
        assert len(item_links) == 1

    def test_a_cloud_output_dir_is_refused_rather_than_silently_racing(
        self, tmp_path, geographic_grid, passing_verdict
    ):
        with pytest.raises(ValueError, match="az://|object storage|local"):
            emit_stac(
                "az://products/coastal",
                products=_products(tmp_path, geographic_grid, passing_verdict),
                grid=geographic_grid,
                qc_verdict=passing_verdict,
                aoi_name="sesimbra",
                sensor="sentinel2",
                acquisition_date="2026-06-27",
            )
