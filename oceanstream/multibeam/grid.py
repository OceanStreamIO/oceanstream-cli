"""Grid soundings into square tiles of Cloud-Optimized GeoTIFFs, with a VRT per variable.

Tiles are aligned to multiples of their size in the projected CRS, so the tiles of
different days line up and a campaign mosaic is a cell-by-cell combination.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

import numpy as np

# Variables written for each grid, with their storage type and nodata value.
VARIABLES: dict[str, tuple[str, float]] = {
    "depth": ("float32", float("nan")),
    "depth_std": ("float32", float("nan")),
    "backscatter": ("float32", float("nan")),
    "count": ("uint16", 0),
}
TILE_RE = re.compile(r"^E(\d+)_N(\d+)\.tif$")
_KEY_SHIFT = 1 << 24
MIN_STD_SOUNDINGS = 2  # soundings in a cell needed for a standard deviation


@dataclass(frozen=True)
class GridSpec:
    """CRS, cell size and tile size (in cells) of a grid."""

    epsg: int
    resolution: float = 1.0
    tile_px: int = 1000

    @property
    def tile_m(self) -> float:
        return self.resolution * self.tile_px

    def tile_name(self, ti: int, tj: int) -> str:
        """File name of the tile whose lower-left corner is (ti, tj) tile sizes from the origin."""
        return f"E{round(ti * self.tile_m):07d}_N{round(tj * self.tile_m):07d}.tif"

    def tile_index(self, name: str) -> tuple[int, int]:
        m = TILE_RE.match(name)
        if not m:
            raise ValueError(f"{name} is not a tile file name (E<easting>_N<northing>.tif).")
        return round(int(m.group(1)) / self.tile_m), round(int(m.group(2)) / self.tile_m)

    def transform(self, ti: int, tj: int) -> Any:
        from affine import Affine  # noqa: PLC0415 - rasterio's

        return Affine(self.resolution, 0.0, ti * self.tile_m, 0.0, -self.resolution,
                      (tj + 1) * self.tile_m)


@dataclass
class TileGrid:
    """Accumulated values of one tile, as sums so that grids can be combined."""

    depth_sum: np.ndarray
    depth_sq_sum: np.ndarray
    count: np.ndarray
    bs_lin_sum: np.ndarray
    bs_count: np.ndarray

    @classmethod
    def empty(cls, n: int) -> TileGrid:
        """A tile of n cells with nothing in it: 28 bytes per cell."""
        return cls(np.zeros(n), np.zeros(n), np.zeros(n, np.int32), np.zeros(n, np.float32),
                   np.zeros(n, np.int32))

    def add(self, other: TileGrid) -> None:
        self.depth_sum += other.depth_sum
        self.depth_sq_sum += other.depth_sq_sum
        self.count += other.count
        self.bs_lin_sum += other.bs_lin_sum
        self.bs_count += other.bs_count

    def arrays(self, tile_px: int) -> dict[str, np.ndarray]:
        """Mean depth, its standard deviation, backscatter (dB of the linear mean), count."""
        with np.errstate(invalid="ignore", divide="ignore"):
            n = self.count.astype(np.float64)
            mean = self.depth_sum / n
            var = np.maximum(self.depth_sq_sum / n - mean * mean, 0.0)
            std = np.where(self.count >= MIN_STD_SOUNDINGS,
                           np.sqrt(var * n / np.maximum(n - 1, 1)), np.nan)
            bs = 10.0 * np.log10(self.bs_lin_sum / self.bs_count)
        shape = (tile_px, tile_px)
        return {
            "depth": mean.astype(np.float32).reshape(shape),
            "depth_std": std.astype(np.float32).reshape(shape),
            "backscatter": np.where(self.bs_count > 0, bs, np.nan)
            .astype(np.float32).reshape(shape),
            "count": np.minimum(self.count, np.iinfo(np.uint16).max)
            .astype(np.uint16).reshape(shape),
        }


def grid_points(
    spec: GridSpec,
    easting: np.ndarray,
    northing: np.ndarray,
    depth: np.ndarray,
    backscatter_db: np.ndarray,
) -> Iterator[tuple[int, int, TileGrid]]:
    """Bin points into cells and yield the accumulated grid of each tile they touch.

    Cells are indexed with row 0 at the north edge of the tile. Backscatter is averaged
    in linear units; NaN backscatter does not count towards ``bs_count``.
    """
    res, tp = spec.resolution, spec.tile_px
    ix = np.floor(np.asarray(easting) / res).astype(np.int64)
    iy = np.floor(np.asarray(northing) / res).astype(np.int64)
    ti, tj = ix // tp, iy // tp
    cell = (tp - 1 - (iy - tj * tp)) * tp + (ix - ti * tp)
    key = ti * _KEY_SHIFT + tj
    order = np.argsort(key, kind="stable")
    keys, starts = np.unique(key[order], return_index=True)
    ends = np.append(starts[1:], order.size)
    depth = np.asarray(depth, dtype=np.float64)
    bs = np.asarray(backscatter_db, dtype=np.float64)
    n = tp * tp
    for k, s, e in zip(keys, starts, ends, strict=True):
        idx = order[s:e]
        c = cell[idx]
        d = depth[idx]
        b = bs[idx]
        ok = np.isfinite(b)
        yield int(k // _KEY_SHIFT), int(k % _KEY_SHIFT), TileGrid(
            depth_sum=np.bincount(c, weights=d, minlength=n),
            depth_sq_sum=np.bincount(c, weights=d * d, minlength=n),
            count=np.bincount(c, minlength=n).astype(np.int32),
            bs_lin_sum=np.bincount(c[ok], weights=10.0 ** (b[ok] / 10.0), minlength=n)
            .astype(np.float32),
            bs_count=np.bincount(c[ok], minlength=n).astype(np.int32),
        )


class GridAccumulator:
    """Running per-tile sums, so that soundings can be gridded a batch at a time.

    Memory grows with the number of tiles touched (28 MB per 1000 x 1000-cell tile), not
    with the number of soundings.
    """

    def __init__(self, spec: GridSpec) -> None:
        self.spec = spec
        self.grids: dict[tuple[int, int], TileGrid] = {}

    def add(self, easting: np.ndarray, northing: np.ndarray, depth: np.ndarray,
            backscatter_db: np.ndarray) -> None:
        for ti, tj, g in grid_points(self.spec, easting, northing, depth, backscatter_db):
            if (ti, tj) in self.grids:
                self.grids[(ti, tj)].add(g)
            else:
                self.grids[(ti, tj)] = g

    def drain(self) -> Iterator[tuple[int, int, TileGrid]]:
        """Yield every tile, releasing each one once it has been yielded."""
        for key in sorted(self.grids):
            yield key[0], key[1], self.grids.pop(key)


def write_cog(  # noqa: PLR0913
    path: Path, array: np.ndarray, spec: GridSpec, ti: int, tj: int, *, nodata: float
) -> None:
    """Write one band as a deflate-compressed Cloud-Optimized GeoTIFF."""
    from rasterio.io import MemoryFile  # noqa: PLC0415
    from rasterio.shutil import copy as rio_copy  # noqa: PLC0415

    profile = {
        "driver": "GTiff", "width": array.shape[1], "height": array.shape[0], "count": 1,
        "dtype": array.dtype.name, "crs": f"EPSG:{spec.epsg}",
        "transform": spec.transform(ti, tj), "nodata": nodata,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with MemoryFile() as mem:
        with mem.open(**profile) as dst:
            dst.write(array, 1)
        with mem.open() as src:
            rio_copy(src, str(path), driver="COG", compress="DEFLATE",
                     predictor=3 if array.dtype.kind == "f" else 2)


def write_tiles(out_dir: Path, spec: GridSpec, ti: int, tj: int, grid: TileGrid) -> None:
    """Write the four variables of a tile under ``out_dir/<variable>/``."""
    for name, array in grid.arrays(spec.tile_px).items():
        write_cog(out_dir / name / spec.tile_name(ti, tj), array, spec, ti, tj,
                  nodata=VARIABLES[name][1])


def read_tile(path: Path) -> np.ndarray:
    import rasterio  # noqa: PLC0415 - optional dependency

    with rasterio.open(path) as src:
        out: np.ndarray = src.read(1)
    return out


def tile_grid_from_files(directory: Path, name: str) -> TileGrid:
    """Rebuild a tile's sums from its written depth, depth_std, backscatter and count."""
    count = read_tile(directory / "count" / name).astype(np.int64).ravel()
    depth = read_tile(directory / "depth" / name).astype(np.float64).ravel()
    std = read_tile(directory / "depth_std" / name).astype(np.float64).ravel()
    bs = read_tile(directory / "backscatter" / name).astype(np.float64).ravel()
    n = count.astype(np.float64)
    has = count > 0
    var = np.where(count >= MIN_STD_SOUNDINGS,
                   np.nan_to_num(std) ** 2 * (n - 1) / np.maximum(n, 1), 0.0)
    has_bs = has & np.isfinite(bs)
    return TileGrid(
        depth_sum=np.where(has, depth * n, 0.0),
        depth_sq_sum=np.where(has, (var + depth * depth) * n, 0.0),
        count=count.astype(np.int32),
        bs_lin_sum=np.where(has_bs, 10.0 ** (np.nan_to_num(bs) / 10.0) * n, 0.0)
        .astype(np.float32),
        bs_count=np.where(has_bs, count, 0).astype(np.int32),
    )


def write_vrt(path: Path, spec: GridSpec, tiles: Iterable[tuple[int, int]], dtype: str,
              nodata: float) -> None:
    """Write a GDAL VRT mosaic of the tiles in ``path.parent/<path stem>/``."""
    from pyproj import CRS  # noqa: PLC0415 - optional dependency

    tiles = sorted(set(tiles))
    if not tiles:
        return
    tp = spec.tile_px
    ti0 = min(t[0] for t in tiles)
    ti1 = max(t[0] for t in tiles)
    tj0 = min(t[1] for t in tiles)
    tj1 = max(t[1] for t in tiles)
    gdal_type = {"float32": "Float32", "uint16": "UInt16"}[dtype]
    nd = "nan" if np.isnan(nodata) else str(nodata)
    sources = []
    for ti, tj in tiles:
        src = f"{path.stem}/{spec.tile_name(ti, tj)}"
        sources.append(
            f'    <SimpleSource>\n'
            f'      <SourceFilename relativeToVRT="1">{escape(src)}</SourceFilename>\n'
            f'      <SourceBand>1</SourceBand>\n'
            f'      <SrcRect xOff="0" yOff="0" xSize="{tp}" ySize="{tp}"/>\n'
            f'      <DstRect xOff="{(ti - ti0) * tp}" yOff="{(tj1 - tj) * tp}" '
            f'xSize="{tp}" ySize="{tp}"/>\n'
            f'    </SimpleSource>'
        )
    gt = (f"{ti0 * spec.tile_m!r}, {spec.resolution!r}, 0.0, "
          f"{(tj1 + 1) * spec.tile_m!r}, 0.0, {-spec.resolution!r}")
    xml = (
        f'<VRTDataset rasterXSize="{(ti1 - ti0 + 1) * tp}" rasterYSize="{(tj1 - tj0 + 1) * tp}">\n'
        f'  <SRS>{escape(CRS.from_epsg(spec.epsg).to_wkt())}</SRS>\n'
        f'  <GeoTransform>{gt}</GeoTransform>\n'
        f'  <VRTRasterBand dataType="{gdal_type}" band="1">\n'
        f'    <NoDataValue>{nd}</NoDataValue>\n'
        + "\n".join(sources)
        + "\n  </VRTRasterBand>\n</VRTDataset>\n"
    )
    path.write_text(xml)


def write_vrts(grid_dir: Path, spec: GridSpec) -> list[Path]:
    """Write one VRT per variable for the tiles found under ``grid_dir``."""
    out = []
    for name, (dtype, nodata) in VARIABLES.items():
        tiles = [spec.tile_index(p.name) for p in sorted((grid_dir / name).glob("*.tif"))]
        if tiles:
            vrt = grid_dir / f"{name}.vrt"
            write_vrt(vrt, spec, tiles, dtype, nodata)
            out.append(vrt)
    return out
