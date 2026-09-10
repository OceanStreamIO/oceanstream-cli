"""Raster IO — grids as values, reprojection, atomic COG writes."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

rasterio = pytest.importorskip("rasterio")

from oceanstream.coastal.io.rasters import (  # noqa: E402
    RasterGrid,
    read_band,
    read_stack,
    reproject_to_grid,
    write_cog,
)

# ~10 m pixels at Sesimbra's latitude, in degrees.
DEG_10M = 10.0 / 111_320.0


def grid(width: int = 8, height: int = 6, res: float = DEG_10M) -> RasterGrid:
    return RasterGrid(
        width=width,
        height=height,
        transform=(res, 0.0, -9.212, 0.0, -res, 38.420),
        crs="EPSG:4326",
    )


def ramp(g: RasterGrid) -> np.ndarray:
    return np.arange(g.height * g.width, dtype=np.float32).reshape(g.shape)


class TestRasterGrid:
    def test_shape_is_numpy_ordering(self) -> None:
        assert grid(width=8, height=6).shape == (6, 8)

    def test_rejects_zero_extent(self) -> None:
        with pytest.raises(ValueError, match="positive extent"):
            RasterGrid(width=0, height=6, transform=(1, 0, 0, 0, -1, 0), crs="EPSG:4326")

    def test_rejects_wrong_transform_length(self) -> None:
        with pytest.raises(ValueError, match="6 affine coefficients"):
            RasterGrid(width=2, height=2, transform=(1, 0, 0), crs="EPSG:4326")  # type: ignore[arg-type]

    def test_is_hashable_so_it_can_key_a_cache(self) -> None:
        assert len({grid(), grid()}) == 1

    def test_bounds(self) -> None:
        g = grid(width=10, height=5)
        west, south, east, north = g.bounds
        assert west == pytest.approx(-9.212)
        assert north == pytest.approx(38.420)
        assert east == pytest.approx(-9.212 + 10 * DEG_10M)
        assert south == pytest.approx(38.420 - 5 * DEG_10M)

    def test_pixel_size_of_a_geographic_grid_is_in_metres(self) -> None:
        # The whole point: QC block sizes are configured in metres, so a
        # degree-based grid has to report metres or the blocks are wrong by a
        # factor of 10^5.
        size = grid().pixel_size_m
        assert 8.0 < size < 10.5

    def test_pixel_size_of_a_projected_grid_is_the_transform(self) -> None:
        utm = RasterGrid(
            width=4, height=4, transform=(10.0, 0, 5e5, 0, -10.0, 4.2e6), crs="EPSG:32629"
        )
        assert utm.pixel_size_m == pytest.approx(10.0)

    def test_matches_tolerates_float_noise(self) -> None:
        a = grid()
        b = RasterGrid(
            width=a.width,
            height=a.height,
            transform=(a.transform[0] + 1e-12, *a.transform[1:]),  # type: ignore[arg-type]
            crs=a.crs,
        )
        assert a.matches(b)

    def test_matches_rejects_a_different_crs(self) -> None:
        a = grid()
        assert not a.matches(RasterGrid(a.width, a.height, a.transform, "EPSG:32629"))

    def test_to_dict_is_json_safe(self) -> None:
        import json

        json.dumps(grid().to_dict())


class TestReadWrite:
    def test_round_trip(self, tmp_path: Path) -> None:
        g = grid()
        data = ramp(g)
        out = tmp_path / "x.tif"
        write_cog(out, data, g)
        read, read_grid = read_band(out)
        assert read_grid.matches(g)
        np.testing.assert_allclose(read, data)

    def test_nodata_comes_back_as_nan(self, tmp_path: Path) -> None:
        # NaN rather than a sentinel, because a forgotten "== nodata" produces a
        # plausible-looking number instead of an obvious failure.
        g = grid()
        data = ramp(g)
        data[0, 0] = -9999.0
        out = tmp_path / "x.tif"
        write_cog(out, data, g, nodata=-9999.0)
        read, _ = read_band(out)
        assert np.isnan(read[0, 0])
        assert np.isfinite(read[1, 1])

    def test_tags_survive_the_round_trip(self, tmp_path: Path) -> None:
        # Tags rather than a sidecar, so provenance survives the file being
        # copied somewhere the sidecar is not.
        g = grid()
        out = tmp_path / "x.tif"
        write_cog(out, ramp(g), g, tags={"vertical_datum": "LAT", "positive": "down"})
        with rasterio.open(out) as src:
            assert src.tags()["vertical_datum"] == "LAT"
            assert src.tags()["positive"] == "down"

    def test_creates_missing_parent_directories(self, tmp_path: Path) -> None:
        g = grid()
        out = tmp_path / "a" / "b" / "x.tif"
        write_cog(out, ramp(g), g)
        assert out.exists()

    def test_leaves_no_temporary_file_behind(self, tmp_path: Path) -> None:
        g = grid()
        write_cog(tmp_path / "x.tif", ramp(g), g)
        assert list(tmp_path.glob("*.tmp")) == []

    def test_shape_mismatch_is_caught_before_writing(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="does not match grid"):
            write_cog(tmp_path / "x.tif", np.zeros((3, 3), np.float32), grid())

    def test_rejects_a_3d_array(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="expects a 2-D array"):
            write_cog(tmp_path / "x.tif", np.zeros((2, 6, 8), np.float32), grid())

    def test_grid_can_be_read_without_reading_pixels(self, tmp_path: Path) -> None:
        g = grid()
        out = tmp_path / "x.tif"
        write_cog(out, ramp(g), g)
        assert RasterGrid.open(out).matches(g)


class TestReadStack:
    def test_stacks_bands_on_a_common_grid(self, tmp_path: Path) -> None:
        g = grid()
        paths = []
        for i in range(3):
            path = tmp_path / f"b{i}.tif"
            write_cog(path, ramp(g) + i, g)
            paths.append(path)
        stack, stack_grid = read_stack(paths)
        assert stack.shape == (3, *g.shape)
        assert stack_grid.matches(g)

    def test_refuses_to_stack_mismatched_grids(self, tmp_path: Path) -> None:
        # Numpy would happily stack equal-shaped arrays from different
        # footprints, and every band ratio afterwards would be meaningless.
        a, b = grid(), grid()
        shifted = RasterGrid(
            b.width, b.height, (b.transform[0], 0.0, -9.0, 0.0, b.transform[4], 38.4), b.crs
        )
        write_cog(tmp_path / "a.tif", ramp(a), a)
        write_cog(tmp_path / "b.tif", ramp(shifted), shifted)
        with pytest.raises(ValueError, match="different grid"):
            read_stack([tmp_path / "a.tif", tmp_path / "b.tif"])

    def test_empty_input_rejected(self) -> None:
        with pytest.raises(ValueError, match="at least one raster"):
            read_stack([])


class TestReproject:
    def test_identical_grid_is_a_passthrough(self) -> None:
        g = grid()
        data = ramp(g)
        out = reproject_to_grid(data, g, src_grid=g)
        np.testing.assert_allclose(out, data)

    def test_passthrough_does_not_alias_the_input(self) -> None:
        g = grid()
        data = ramp(g)
        out = reproject_to_grid(data, g, src_grid=g)
        out[0, 0] = -1.0
        assert data[0, 0] == 0.0

    def test_resamples_a_coarse_grid_onto_a_fine_one(self) -> None:
        coarse = RasterGrid(
            4, 3, (2 * DEG_10M, 0.0, -9.212, 0.0, -2 * DEG_10M, 38.420), "EPSG:4326"
        )
        fine = grid(width=8, height=6)
        out = reproject_to_grid(np.full(coarse.shape, 7.0, np.float32), fine, src_grid=coarse)
        assert out.shape == fine.shape
        assert np.nanmax(out) == pytest.approx(7.0)

    def test_unmapped_pixels_become_nan(self) -> None:
        source = RasterGrid(2, 2, (DEG_10M, 0.0, 0.0, 0.0, -DEG_10M, 10.0), "EPSG:4326")
        out = reproject_to_grid(np.ones(source.shape, np.float32), grid(), src_grid=source)
        assert np.isnan(out).all()

    def test_array_without_src_grid_is_an_error(self) -> None:
        with pytest.raises(ValueError, match="needs src_grid"):
            reproject_to_grid(np.zeros((6, 8), np.float32), grid())

    def test_unknown_resampling_names_the_options(self) -> None:
        g = grid()
        with pytest.raises(ValueError, match="Available:"):
            reproject_to_grid(ramp(g), g, src_grid=grid(width=4), resampling="magic")

    def test_nearest_preserves_class_labels(self) -> None:
        # Interpolating labels would invent classes that do not exist.
        coarse = RasterGrid(
            4, 3, (2 * DEG_10M, 0.0, -9.212, 0.0, -2 * DEG_10M, 38.420), "EPSG:4326"
        )
        labels = np.array([[0, 1, 2, 3]] * 3, dtype=np.float32)
        out = reproject_to_grid(labels, grid(), src_grid=coarse, resampling="nearest")
        assert set(np.unique(out[np.isfinite(out)])) <= {0.0, 1.0, 2.0, 3.0}

    def test_reads_from_a_file(self, tmp_path: Path) -> None:
        g = grid()
        path = tmp_path / "src.tif"
        write_cog(path, ramp(g), g)
        np.testing.assert_allclose(reproject_to_grid(path, g), ramp(g))
