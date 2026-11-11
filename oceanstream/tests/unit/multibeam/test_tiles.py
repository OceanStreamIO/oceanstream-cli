"""Web map tiles of the multibeam grids: tile maths, colours, light and the PMTiles output."""

from __future__ import annotations

import io
import math
from pathlib import Path

import numpy as np
import pytest

from oceanstream.multibeam.tiles import (
    LUMA,
    SEA_RAMP,
    STRETCH_RAMP,
    LayerStyle,
    Shading,
    hillshade,
    lonlat_to_tile,
    native_zoom,
    ramp_colour,
    tile_bounds,
    tiles_for_bounds,
)


def test_tile_bounds_and_lonlat_to_tile_agree() -> None:
    x, y = lonlat_to_tile(-76.2288, 34.1708, 17)
    xmin, ymin, xmax, ymax = tile_bounds(17, x, y)
    r = 6378137.0
    mx = math.radians(-76.2288) * r
    my = r * math.asinh(math.tan(math.radians(34.1708)))
    assert xmin <= mx < xmax and ymin <= my < ymax
    assert xmax - xmin == pytest.approx(2 * 20037508.342789244 / 2**17)


def test_native_zoom_of_a_one_metre_grid() -> None:
    assert native_zoom(1.0, 34.5) == 17
    assert native_zoom(4.0, 34.5) == 15
    assert native_zoom(1.0, 70.0) == 16


def test_tiles_for_bounds_covers_the_box_at_each_zoom() -> None:
    tiles = tiles_for_bounds([(-76.24, 34.16, -76.22, 34.18)], [12, 15])
    assert {z for z, _, _ in tiles} == {12, 15}
    for z in (12, 15):
        (x0, y0), (x1, y1) = lonlat_to_tile(-76.24, 34.18, z), lonlat_to_tile(-76.22, 34.16, z)
        expected = {(x, y) for x in range(x0, x1 + 1) for y in range(y0, y1 + 1)}
        assert {(x, y) for zz, x, y in tiles if zz == z} == expected


def test_ramp_ends_clamp() -> None:
    rgb = ramp_colour(np.array([0.0, -11000.0, 100.0, -20000.0]))
    assert tuple(rgb[0]) == SEA_RAMP[-1][1] and tuple(rgb[2]) == SEA_RAMP[-1][1]
    assert tuple(rgb[1]) == SEA_RAMP[0][1] and tuple(rgb[3]) == SEA_RAMP[0][1]


def test_stretch_ramp_darkens_in_even_steps() -> None:
    # The basemap ramp itself lightens again between 175 and 350 m ...
    lum = ramp_colour(-np.linspace(0, 6500, 200)) @ LUMA
    assert (np.diff(lum) > 0).any()
    # ... the stretched one never does, and its steps are even (to the rounding of RGB).
    steps = np.diff([np.array(c) @ LUMA for _, c in reversed(STRETCH_RAMP)])
    assert np.all(steps < 0) and steps.max() - steps.min() < 1.5
    assert STRETCH_RAMP[-1] == (0.0, SEA_RAMP[-1][1])


def test_hillshade_of_flat_ground_is_one_and_lit_slope_brighter() -> None:
    flat = np.full((10, 10), -20.0)
    assert np.allclose(hillshade(flat, 1.0, Shading()), 1.0)
    # Ground rising towards the north-west faces the light (azimuth 315).
    yy, xx = np.mgrid[0:10, 0:10].astype(float)
    facing = -20.0 + 0.2 * (yy + xx)  # deeper to the south-east
    assert hillshade(facing, 1.0, Shading()).mean() > 1.0


def test_layer_style_alpha_fill_and_stretch() -> None:
    values = np.full((258, 258), 30.0, dtype=np.float32)
    values[:, 200:] = np.nan
    values[50, 50] = np.nan  # a one-cell hole
    style = LayerStyle("depth", "stretch", (20.0, 40.0))
    rgba = style.rgba(values, 1.0, fill_px=1.0)
    assert rgba.shape == (256, 256, 4)
    assert rgba[49, 49, 3] == 255  # hole filled at native zoom
    assert rgba[10, 10, 3] == 255 and rgba[10, 230, 3] == 0
    # Halfway down the stretch takes the stretched ramp's middle stop.
    assert tuple(rgba[10, 10, :3]) == STRETCH_RAMP[4][1]
    basemap = LayerStyle("depth", "basemap").rgba(values, 1.0, None)
    assert basemap[49, 49, 3] == 0  # no fill below native zoom
    assert tuple(basemap[10, 10, :3]) == tuple(np.round(ramp_colour(np.array(-30.0))).astype(int))


def test_backscatter_is_grey_from_low_to_high() -> None:
    values = np.full((258, 258), -40.0, dtype=np.float32)
    values[:, 129:] = -20.0
    rgba = LayerStyle("backscatter", value_range=(-38.0, -22.0)).rgba(values, 1.0, None)
    assert tuple(rgba[0, 0, :3]) == (0, 0, 0) and tuple(rgba[0, 255, :3]) == (255, 255, 255)


def test_render_tiles_writes_readable_pmtiles(tmp_path: Path) -> None:
    pytest.importorskip("rasterio")
    pytest.importorskip("pyproj")
    pytest.importorskip("scipy")
    pmtiles_reader = pytest.importorskip("pmtiles.reader")
    from PIL import Image  # noqa: PLC0415 - matplotlib's dependency

    from oceanstream.multibeam.processor import build_mosaic, process_files  # noqa: PLC0415
    from oceanstream.multibeam.tiles import render_tiles  # noqa: PLC0415

    from .test_multibeam import _batch  # noqa: PLC0415

    out = tmp_path / "out"
    process_files([], out, batches=[_batch(20000, depth=20.0)], tile_px=200, quicklook=False)
    build_mosaic(out, quicklook=False)
    results = render_tiles(out, depth_colours=("basemap", "stretch"), min_zoom=14)
    names = sorted(Path(r["path"]).name for r in results)
    assert names == ["backscatter.pmtiles", "depth-basemap.pmtiles", "depth-stretch.pmtiles"]
    for r in results:
        assert r["native_zoom"] == 17 and set(r["tiles_per_zoom"]) == {"14", "15", "16", "17"}
        with open(r["path"], "rb") as f:
            reader = pmtiles_reader.Reader(pmtiles_reader.MmapSource(f))
            header, meta = reader.header(), reader.metadata()
            assert header["min_zoom"] == 14 and header["max_zoom"] == 17
            assert meta["oceanstream"]["layer"] in ("depth", "backscatter")
            x, y = lonlat_to_tile(-76.4, 34.5, 17)
            img = np.asarray(Image.open(io.BytesIO(reader.get(17, x, y))).convert("RGBA"))
            assert img.shape == (256, 256, 4) and (img[..., 3] == 255).any()


def test_render_tiles_needs_the_mosaic(tmp_path: Path) -> None:
    from oceanstream.multibeam.tiles import render_tiles  # noqa: PLC0415

    with pytest.raises(FileNotFoundError, match="--mosaic"):
        render_tiles(tmp_path)
    with pytest.raises(ValueError, match="Depth colours"):
        render_tiles(tmp_path, depth_colours=("rainbow",))
