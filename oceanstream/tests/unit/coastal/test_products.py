"""Behaviour of the product writers.

The interesting claims are not "it writes a file". They are the ones that stop
a product being read as something it is not: that the QC verdict is
non-optional, that the ``rho_b`` caveat survives into the GeoTIFF's own tags,
that an integer raster does not turn NaN into a valid class, and that a JSON
document is parseable by a strict reader.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from oceanstream.coastal.io.rasters import RasterGrid
from oceanstream.coastal.products import (
    JSON_PRODUCTS,
    RASTER_PRODUCTS,
    STATUS_DIAGNOSTIC,
    STATUS_PUBLISHABLE,
    ProductWriter,
    get_spec,
    jsonable,
    raster_stats,
    write_json_document,
)

rasterio = pytest.importorskip("rasterio")


@pytest.fixture
def grid() -> RasterGrid:
    return RasterGrid(
        width=8,
        height=6,
        transform=(0.0001, 0.0, -9.05, 0.0, -0.0001, 38.46),
        crs="EPSG:4326",
    )


@pytest.fixture
def passing_verdict() -> dict:
    return {"passed": True, "summary": "all bands above the pure-water floor", "flags": []}


@pytest.fixture
def failing_verdict() -> dict:
    return {
        "passed": False,
        "summary": "k(667) is 12x below the pure-water floor",
        "flags": ["pure_water_floor_violated"],
    }


@pytest.fixture
def depth(grid: RasterGrid) -> np.ndarray:
    data = np.linspace(1.0, 30.0, grid.width * grid.height, dtype=np.float32)
    return data.reshape(grid.shape)


# ---------------------------------------------------------------------------
# The verdict is structural
# ---------------------------------------------------------------------------


class TestVerdictIsRequired:
    def test_writer_refuses_a_verdict_without_a_passed_key(self, tmp_path, grid):
        with pytest.raises(ValueError, match="passed"):
            ProductWriter(tmp_path, grid, {"summary": "looks fine"})

    def test_error_names_the_function_that_produces_a_verdict(self, tmp_path, grid):
        with pytest.raises(ValueError, match="scene_floor_verdict"):
            ProductWriter(tmp_path, grid, {})

    def test_a_passing_scene_is_publishable(self, tmp_path, grid, passing_verdict):
        writer = ProductWriter(tmp_path, grid, passing_verdict)
        assert writer.status == STATUS_PUBLISHABLE

    def test_a_failing_scene_is_diagnostic_only(self, tmp_path, grid, failing_verdict):
        writer = ProductWriter(tmp_path, grid, failing_verdict)
        assert writer.status == STATUS_DIAGNOSTIC

    def test_a_failed_verdict_marks_every_raster_it_writes(
        self, tmp_path, grid, failing_verdict, depth
    ):
        writer = ProductWriter(tmp_path, grid, failing_verdict)
        product = writer.write_raster("sdb_depth", depth)
        assert product.status == STATUS_DIAGNOSTIC
        assert not product.is_publishable
        with rasterio.open(product.href) as src:
            tags = src.tags()
        assert tags["OCEANSTREAM_STATUS"] == STATUS_DIAGNOSTIC
        assert tags["OCEANSTREAM_QC_PASSED"] == "False"
        assert "pure-water floor" in tags["OCEANSTREAM_QC_SUMMARY"]

    def test_a_failed_verdict_marks_every_document_it_writes(
        self, tmp_path, grid, failing_verdict
    ):
        writer = ProductWriter(tmp_path, grid, failing_verdict)
        product = writer.write_json("qc", {"anything": 1})
        payload = json.loads(Path(product.href).read_text())
        assert payload["oceanstream:status"] == STATUS_DIAGNOSTIC
        assert payload["oceanstream:qc_passed"] is False


# ---------------------------------------------------------------------------
# Caveats travel with the file
# ---------------------------------------------------------------------------


class TestCaveats:
    def test_rho_b_is_never_described_as_a_material_property(self):
        caveat = RASTER_PRODUCTS["rho_b"].caveat
        assert caveat is not None
        assert "not a material property" in caveat
        assert "substrate albedo" in caveat

    def test_rho_b_caveat_is_written_into_the_geotiff_itself(
        self, tmp_path, grid, passing_verdict
    ):
        writer = ProductWriter(tmp_path, grid, passing_verdict)
        product = writer.write_raster("rho_b_561", np.full(grid.shape, 0.05, np.float32))
        with rasterio.open(product.href) as src:
            caveat = src.tags()["OCEANSTREAM_CAVEAT"]
        assert "not a material property" in caveat

    def test_spectral_class_caveat_refuses_habitat_naming(self):
        caveat = RASTER_PRODUCTS["spectral_class"].caveat
        assert caveat is not None
        assert "not habitat labels" in caveat

    def test_band_suffixed_keys_share_the_base_spec(self):
        assert get_spec("rho_b_444") is RASTER_PRODUCTS["rho_b"]
        assert get_spec("rho_b_667") is RASTER_PRODUCTS["rho_b"]

    def test_every_band_of_rho_b_carries_the_same_caveat(
        self, tmp_path, grid, passing_verdict
    ):
        writer = ProductWriter(tmp_path, grid, passing_verdict)
        caveats = set()
        for nm in (444, 489, 561, 667):
            product = writer.write_raster(f"rho_b_{nm}", np.zeros(grid.shape, np.float32))
            with rasterio.open(product.href) as src:
                caveats.add(src.tags()["OCEANSTREAM_CAVEAT"])
        assert len(caveats) == 1

    def test_tag_keys_never_use_a_colon_because_gdal_collapses_them(
        self, tmp_path, grid, passing_verdict, depth
    ):
        """GDAL reads ':' as a domain separator and merges the keys, keeping one.

        This is how the caveat went missing the first time: every
        ``oceanstream:*`` tag collapsed into a single ``oceanstream`` tag.
        """
        writer = ProductWriter(tmp_path, grid, passing_verdict, {"aoi": "sesimbra"})
        product = writer.write_raster("rho_b_561", depth, {"band_nm": 561.0})
        with rasterio.open(product.href) as src:
            tags = src.tags()
        assert not any(":" in key for key in tags)
        assert "oceanstream" not in tags
        for expected in (
            "OCEANSTREAM_CAVEAT",
            "OCEANSTREAM_STATUS",
            "OCEANSTREAM_QC_PASSED",
            "OCEANSTREAM_AOI",
            "OCEANSTREAM_BAND_NM",
        ):
            assert expected in tags

    def test_write_cog_refuses_a_colon_key_outright(self, tmp_path, grid):
        from oceanstream.coastal.io.rasters import write_cog

        with pytest.raises(ValueError, match="underscores"):
            write_cog(
                tmp_path / "x.tif",
                np.zeros(grid.shape, np.float32),
                grid,
                tags={"oceanstream:caveat": "lost"},
            )

    def test_an_unknown_key_names_what_is_known(self):
        with pytest.raises(KeyError, match="sdb_depth"):
            get_spec("chlorophyll")


# ---------------------------------------------------------------------------
# Encoding
# ---------------------------------------------------------------------------


class TestEncoding:
    def test_nan_in_a_class_raster_becomes_nodata_not_class_zero(
        self, tmp_path, grid, passing_verdict
    ):
        labels = np.zeros(grid.shape, dtype=np.float32)
        labels[0, 0] = np.nan
        writer = ProductWriter(tmp_path, grid, passing_verdict)
        product = writer.write_raster("spectral_class", labels)
        with rasterio.open(product.href) as src:
            written = src.read(1)
            nodata = src.nodata
        assert written[0, 0] == RASTER_PRODUCTS["spectral_class"].nodata
        assert nodata == RASTER_PRODUCTS["spectral_class"].nodata
        assert written[0, 1] == 0

    def test_float_products_keep_nan(self, tmp_path, grid, passing_verdict, depth):
        data = depth.copy()
        data[2, 2] = np.nan
        writer = ProductWriter(tmp_path, grid, passing_verdict)
        product = writer.write_raster("sdb_depth", data)
        with rasterio.open(product.href) as src:
            written = src.read(1)
        assert np.isnan(written[2, 2])

    def test_a_mismatched_grid_is_refused_not_reprojected(
        self, tmp_path, grid, passing_verdict
    ):
        writer = ProductWriter(tmp_path, grid, passing_verdict)
        with pytest.raises(ValueError, match="reproject_to_grid"):
            writer.write_raster("sdb_depth", np.zeros((3, 3), np.float32))

    def test_a_raster_key_is_refused_by_the_json_writer(
        self, tmp_path, grid, passing_verdict
    ):
        writer = ProductWriter(tmp_path, grid, passing_verdict)
        with pytest.raises(ValueError, match="write_raster"):
            writer.write_json("sdb_depth", {})

    def test_a_json_key_is_refused_by_the_raster_writer(
        self, tmp_path, grid, passing_verdict
    ):
        writer = ProductWriter(tmp_path, grid, passing_verdict)
        with pytest.raises(ValueError, match="write_json"):
            writer.write_raster("qc", np.zeros(grid.shape, np.float32))


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------


class TestStatistics:
    def test_an_empty_product_is_distinguishable_from_a_full_one(self):
        empty = raster_stats(np.full((4, 4), np.nan), RASTER_PRODUCTS["sdb_depth"])
        full = raster_stats(np.ones((4, 4)), RASTER_PRODUCTS["sdb_depth"])
        assert empty["valid_fraction"] == 0.0
        assert full["valid_fraction"] == 1.0
        assert "median" not in empty

    def test_a_collapsed_map_is_visible_in_the_percentile_span(self):
        collapsed = np.full((20, 20), 10.0)
        collapsed[0, 0] = 0.0
        collapsed[0, 1] = 40.0
        stats = raster_stats(collapsed, RASTER_PRODUCTS["sdb_depth"])
        assert stats["min"] == 0.0
        assert stats["max"] == 40.0
        assert stats["p5"] == stats["p95"] == 10.0

    def test_categorical_products_report_class_counts_not_percentiles(self):
        labels = np.array([[0, 0, 1], [2, 2, 2]], dtype=float)
        stats = raster_stats(labels, RASTER_PRODUCTS["spectral_class"])
        assert stats["classes"] == {0: 2, 1: 1, 2: 3}
        assert "p95" not in stats


# ---------------------------------------------------------------------------
# JSON
# ---------------------------------------------------------------------------


class TestJson:
    def test_non_finite_floats_become_null_so_strict_parsers_can_read_the_file(
        self, tmp_path
    ):
        path = write_json_document(tmp_path / "d.json", {"k": float("nan"), "z": np.inf})
        text = Path(path).read_text()
        assert "NaN" not in text
        assert json.loads(text) == {"k": None, "z": None}

    def test_numpy_types_survive_the_round_trip(self):
        payload = jsonable(
            {
                "arr": np.array([1.0, 2.0]),
                "int": np.int32(3),
                "bool": np.bool_(True),
                "path": Path("/tmp/x.tif"),
            }
        )
        assert json.loads(json.dumps(payload)) == {
            "arr": [1.0, 2.0],
            "int": 3,
            "bool": True,
            "path": "/tmp/x.tif",
        }

    def test_a_document_is_written_atomically(self, tmp_path):
        target = tmp_path / "nested" / "d.json"
        write_json_document(target, {"a": 1})
        assert target.exists()
        assert not list(target.parent.glob("*.tmp"))


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------


class TestManifest:
    def test_the_manifest_carries_the_verdict_alongside_the_products(
        self, tmp_path, grid, failing_verdict, depth
    ):
        writer = ProductWriter(tmp_path, grid, failing_verdict, {"aoi": "sesimbra"})
        writer.write_raster("sdb_depth", depth)
        writer.write_json("qc", {"x": 1})
        manifest = writer.manifest()
        assert manifest["qc_passed"] is False
        assert manifest["status"] == STATUS_DIAGNOSTIC
        assert set(manifest["products"]) == {"sdb_depth", "qc"}
        assert manifest["provenance"]["aoi"] == "sesimbra"

    def test_provenance_is_written_into_every_raster(
        self, tmp_path, grid, passing_verdict, depth
    ):
        writer = ProductWriter(
            tmp_path, grid, passing_verdict, {"aoi": "sesimbra", "sensor": "sentinel2"}
        )
        product = writer.write_raster("sdb_depth", depth)
        with rasterio.open(product.href) as src:
            tags = src.tags()
        assert tags["OCEANSTREAM_AOI"] == "sesimbra"
        assert tags["OCEANSTREAM_SENSOR"] == "sentinel2"

    def test_every_registered_product_declares_a_media_type_and_extension(self):
        for spec in (*RASTER_PRODUCTS.values(), *JSON_PRODUCTS.values()):
            assert spec.media_type
            assert spec.extension in {".tif", ".json"}
