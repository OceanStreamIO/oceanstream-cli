"""Multibeam geometry, gridding, backscatter flattening and the processor (no real files)."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from oceanstream.multibeam.backscatter import AngularResponse
from oceanstream.multibeam.grid import GridAccumulator, GridSpec, TileGrid, grid_points
from oceanstream.multibeam.processor import build_mosaic, find_all_files, process_files
from oceanstream.multibeam.soundings import (
    SoundingBatch,
    georeference,
    heading_frame_to_north_east,
    offset_latlon,
    utm_epsg,
)


def test_heading_frame_rotation() -> None:
    # Heading north: forward is north, starboard is east.
    n, e = heading_frame_to_north_east(np.array([0.0]), np.array([1.0]), np.array([2.0]))
    assert n[0] == pytest.approx(1.0) and e[0] == pytest.approx(2.0)
    # Heading east: forward is east, starboard is south.
    n, e = heading_frame_to_north_east(np.array([90.0]), np.array([1.0]), np.array([2.0]))
    assert n[0] == pytest.approx(-2.0) and e[0] == pytest.approx(1.0)


def test_offset_latlon_matches_pyproj_distance() -> None:
    pyproj = pytest.importorskip("pyproj")
    lat, lon = offset_latlon(np.array([34.5]), np.array([-76.4]), np.array([30.0]),
                             np.array([-40.0]))
    geod = pyproj.Geod(ellps="WGS84")
    _, _, dist = geod.inv(-76.4, 34.5, lon[0], lat[0])
    assert dist == pytest.approx(50.0, abs=1e-3)


def test_utm_epsg() -> None:
    assert utm_epsg(34.5, -76.4) == 32618
    assert utm_epsg(-33.9, 151.2) == 32756


def _batch(n: int, depth: float = 20.0, heading: float = 0.0) -> SoundingBatch:
    rng = np.random.default_rng(1)
    t0 = np.datetime64("2021-06-19T01:00:00", "ns")
    return SoundingBatch(
        ping_time=t0 + (np.arange(n) // 10).astype("timedelta64[s]"),
        beam=(np.arange(n) % 10).astype(np.uint16),
        ref_latitude=np.full(n, 34.5),
        ref_longitude=np.full(n, -76.4),
        heading_deg=np.full(n, heading),
        along_m=rng.uniform(-50, 50, n).astype(np.float32),
        across_m=rng.uniform(-30, 30, n).astype(np.float32),
        depth_below_tx_m=np.full(n, depth - 3.5, np.float32),
        transducer_depth_m=np.full(n, 3.5, np.float32),
        backscatter_db=np.full(n, -30.0, np.float32),
        beam_angle_deg=rng.uniform(-60, 60, n).astype(np.float32),
        quality_factor=np.full(n, 10, np.uint8),
    )


def test_georeference_adds_transducer_depth() -> None:
    _, _, depth = georeference(_batch(5))
    assert np.allclose(depth, 20.0)


def test_grid_points_mean_std_count_and_orientation() -> None:
    spec = GridSpec(epsg=32618, resolution=1.0, tile_px=10)
    # Three points in the cell at the north-west corner of tile (0, 0): x 0-1, y 9-10.
    e = np.array([0.2, 0.5, 0.8, 15.5])
    n = np.array([9.5, 9.5, 9.5, 3.5])
    d = np.array([10.0, 11.0, 12.0, 5.0])
    bs = np.array([-20.0, -20.0, np.nan, -30.0])
    tiles = {(ti, tj): g for ti, tj, g in grid_points(spec, e, n, d, bs)}
    assert set(tiles) == {(0, 0), (1, 0)}
    a = tiles[(0, 0)].arrays(10)
    assert a["count"][0, 0] == 3
    assert a["depth"][0, 0] == pytest.approx(11.0)
    assert a["depth_std"][0, 0] == pytest.approx(1.0)
    assert a["backscatter"][0, 0] == pytest.approx(-20.0)
    # Easting 15.5, northing 3.5: tile 1, column 5, row 10 - 1 - 3 = 6.
    b = tiles[(1, 0)].arrays(10)
    assert b["count"][6, 5] == 1 and b["depth"][6, 5] == pytest.approx(5.0)


def test_tile_grid_add_combines_means() -> None:
    spec = GridSpec(epsg=32618, resolution=1.0, tile_px=2)
    g1 = next(grid_points(spec, np.array([0.5]), np.array([0.5]), np.array([10.0]),
                          np.array([-20.0])))[2]
    g2 = next(grid_points(spec, np.array([0.5, 0.5]), np.array([0.5, 0.5]),
                          np.array([13.0, 13.0]), np.array([-30.0, -30.0])))[2]
    total = TileGrid.empty(4)
    total.add(g1)
    total.add(g2)
    a = total.arrays(2)
    assert a["count"][1, 0] == 3
    assert a["depth"][1, 0] == pytest.approx(12.0)
    lin = (10 ** -2 + 2 * 10 ** -3) / 3
    assert a["backscatter"][1, 0] == pytest.approx(10 * np.log10(lin), abs=1e-4)


def test_accumulator_in_batches_matches_one_pass() -> None:
    rng = np.random.default_rng(3)
    spec = GridSpec(epsg=32618, resolution=1.0, tile_px=50)
    e, n = rng.uniform(0, 120, 20_000), rng.uniform(0, 90, 20_000)
    d, b = rng.uniform(10, 20, e.size), rng.uniform(-40, -20, e.size)
    one = {(ti, tj): g.arrays(50) for ti, tj, g in grid_points(spec, e, n, d, b)}
    acc = GridAccumulator(spec)
    for s in (slice(0, 7000), slice(7000, 15000), slice(15000, None)):
        acc.add(e[s], n[s], d[s], b[s])
    batched = {(ti, tj): g.arrays(50) for ti, tj, g in acc.drain()}
    assert set(batched) == set(one) and not acc.grids
    for key, arrays in one.items():
        for var, a in arrays.items():
            np.testing.assert_allclose(batched[key][var], a, rtol=1e-5, equal_nan=True)


def test_angular_response_flattens_a_linear_trend() -> None:
    rng = np.random.default_rng(0)
    angle = rng.uniform(-70, 70, 200_000)
    bs = -20.0 - 0.3 * np.abs(angle) + rng.normal(0, 0.5, angle.size)
    ar = AngularResponse()
    ar.add(angle, bs)
    flat = ar.flatten(angle, bs)
    assert ar.reference_db() == pytest.approx(-20.0 - 0.3 * 40.0, abs=0.3)
    # After flattening, no trend with angle remains.
    near = np.nanmedian(flat[np.abs(angle) < 10])
    far = np.nanmedian(flat[np.abs(angle) > 60])
    assert abs(near - far) < 0.3
    d = ar.to_dict()
    assert d["reference_db"] is not None and len(d["angle_deg"]) == 70


def test_process_files_writes_grids_and_mosaic(tmp_path: Path) -> None:
    pytest.importorskip("rasterio")
    pytest.importorskip("pyproj")

    out = tmp_path / "out"
    day1 = process_files([], out, batches=[_batch(5000, depth=20.0)], tile_px=200,
                         quicklook=False)
    assert len(day1) == 1
    s = day1[0]
    assert s["day"] == "2021-06-19" and s["soundings"] == 5000 and s["pings"] == 500
    assert s["epsg"] == 32618
    assert s["cell_depth_m"]["p50"] == pytest.approx(20.0, abs=1e-3)
    grid = out / "grids" / "days" / "2021-06-19"
    for var in ("depth", "depth_std", "backscatter", "count"):
        assert (grid / f"{var}.vrt").exists()
        assert list((grid / var).glob("E*_N*.tif"))
    assert (out / "soundings" / "2021-06-19.parquet").exists()
    assert json.loads((out / "grid.json").read_text())["epsg"] == 32618

    # A second day one metre deeper over the same area: the mosaic averages them.
    b2 = _batch(5000, depth=21.0)
    b2.ping_time = b2.ping_time + np.timedelta64(1, "D")
    process_files([], out, batches=[b2], tile_px=200, quicklook=False)
    m = build_mosaic(out, quicklook=False)
    assert m["days"] == ["2021-06-19", "2021-06-20"]
    assert m["cell_depth_m"]["p50"] == pytest.approx(20.5, abs=0.3)

    rasterio = pytest.importorskip("rasterio")
    with rasterio.open(out / "grids" / "campaign" / "count.vrt") as src:
        assert src.crs.to_epsg() == 32618
        assert int(src.read(1).sum()) == 10000


def test_process_files_rejects_a_different_grid(tmp_path: Path) -> None:
    pytest.importorskip("rasterio")
    pytest.importorskip("pyproj")
    out = tmp_path / "out"
    process_files([], out, batches=[_batch(100)], tile_px=200, quicklook=False)
    with pytest.raises(ValueError, match="same --epsg, --resolution"):
        process_files([], out, batches=[_batch(100)], tile_px=200, resolution=2.0,
                      quicklook=False)


def test_find_all_files_explains_gzipped_input(tmp_path: Path) -> None:
    (tmp_path / "0000_20210616_163748_NancyFoster.all.mb58.gz").write_bytes(b"")
    with pytest.raises(FileNotFoundError, match="Unzip them first"):
        find_all_files(tmp_path)
