"""Greyscale PNG quick-looks of multibeam grids, one per patch of adjacent tiles.

A day or a campaign often covers several survey boxes tens of kilometres apart, joined by
narrow transit swaths. One image of the whole extent would show each box as a few pixels,
so the tiles are grouped into patches (tiles touching, diagonals included) and each patch
gets its own depth and backscatter image, largest patch first.
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np

from .grid import GridSpec

MAX_PIXELS = 2000
MAX_PATCHES = 12


def tile_patches(tiles: set[tuple[int, int]]) -> list[list[tuple[int, int]]]:
    """Group tiles into patches of touching tiles, largest first."""
    remaining = set(tiles)
    patches = []
    while remaining:
        seed = min(remaining)
        remaining.remove(seed)
        patch, stack = [seed], [seed]
        while stack:
            ti, tj = stack.pop()
            for di in (-1, 0, 1):
                for dj in (-1, 0, 1):
                    nb = (ti + di, tj + dj)
                    if nb in remaining:
                        remaining.remove(nb)
                        patch.append(nb)
                        stack.append(nb)
        patches.append(sorted(patch))
    return sorted(patches, key=lambda p: (-len(p), p[0]))


def _read_patch(
    vrt: Path, spec: GridSpec, patch: list[tuple[int, int]]
) -> tuple[np.ndarray, tuple[float, float, float, float], float]:
    import rasterio  # noqa: PLC0415 - optional dependency
    from rasterio.enums import Resampling  # noqa: PLC0415
    from rasterio.windows import from_bounds  # noqa: PLC0415

    west = min(t[0] for t in patch) * spec.tile_m
    east = (max(t[0] for t in patch) + 1) * spec.tile_m
    south = min(t[1] for t in patch) * spec.tile_m
    north = (max(t[1] for t in patch) + 1) * spec.tile_m
    with rasterio.open(vrt) as src:
        window = from_bounds(west, south, east, north, transform=src.transform)
        width, height = round(window.width), round(window.height)
        factor = max(1.0, max(width, height) / MAX_PIXELS)
        shape = (max(1, math.ceil(height / factor)), max(1, math.ceil(width / factor)))
        data = src.read(1, window=window, out_shape=shape, resampling=Resampling.average,
                        masked=True)
    cell = (east - west) / shape[1]
    arr = data.filled(np.nan).astype(np.float64)
    rows = np.flatnonzero(np.isfinite(arr).any(axis=1))
    cols = np.flatnonzero(np.isfinite(arr).any(axis=0))
    if rows.size == 0:
        return arr, (west, east, south, north), cell
    # Crop to the cells with data, with a margin of 2 % of the larger side.
    pad = max(2, int(0.02 * max(arr.shape)))
    r0, r1 = max(rows[0] - pad, 0), min(rows[-1] + pad + 1, arr.shape[0])
    c0, c1 = max(cols[0] - pad, 0), min(cols[-1] + pad + 1, arr.shape[1])
    extent = (west + c0 * cell, west + c1 * cell, north - r1 * cell, north - r0 * cell)
    return arr[r0:r1, c0:c1], extent, cell


def _hillshade(depth: np.ndarray, cell: float, azimuth: float = 315.0,
               altitude: float = 45.0) -> np.ndarray:
    z = -np.nan_to_num(depth, nan=float(np.nanmedian(depth)) if np.isfinite(depth).any() else 0.0)
    dy, dx = np.gradient(z, cell)
    slope = np.arctan(np.hypot(dx, dy))
    aspect = np.arctan2(-dx, dy)
    az = np.radians(azimuth)
    alt = np.radians(altitude)
    shade = np.sin(alt) * np.cos(slope) + np.cos(alt) * np.sin(slope) * np.cos(az - aspect)
    out: np.ndarray = np.clip(shade, 0.0, 1.0)
    return out


def write_quicklooks(grid_dir: Path, out_dir: Path, title: str, spec: GridSpec) -> list[Path]:
    """Write depth-NN.png (greyscale depth with hillshade) and backscatter-NN.png per patch.

    NN numbers the patches from the largest. Earlier PNGs in ``out_dir`` are removed.
    """
    import matplotlib  # noqa: PLC0415 - optional dependency

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt  # noqa: PLC0415

    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("*.png"):
        old.unlink()
    tiles = {spec.tile_index(p.name) for p in (grid_dir / "count").glob("*.tif")}
    patches = tile_patches(tiles)
    written = []
    plt.rcParams.update({"font.size": 15})
    for k, patch in enumerate(patches[:MAX_PATCHES], 1):
        for name, label in (("depth", "Depth below the water line (m)"),
                            ("backscatter", "Backscatter, angular response removed (dB)")):
            vrt = grid_dir / f"{name}.vrt"
            if not vrt.exists():
                continue
            data, extent, cell = _read_patch(vrt, spec, patch)
            if not np.isfinite(data).any():
                continue
            lo, hi = np.nanpercentile(data, [2, 98])
            fig, ax = plt.subplots(figsize=(12, 10), dpi=100)
            cmap = "Greys" if name == "depth" else "gray"
            im = ax.imshow(data, extent=extent, cmap=cmap, vmin=lo, vmax=hi,
                           interpolation="nearest")
            if name == "depth":
                shade = np.where(np.isfinite(data), _hillshade(data, cell), np.nan)
                ax.imshow(shade, extent=extent, cmap="gray", alpha=0.35, interpolation="nearest")
            cb = fig.colorbar(im, ax=ax, shrink=0.8)
            cb.set_label(label)
            ax.set_title(f"{title}, patch {k} of {len(patches)}: {name} "
                         f"({cell:.1f} m cells shown)")
            ax.set_xlabel(f"Easting (m, EPSG:{spec.epsg})")
            ax.set_ylabel("Northing (m)")
            ax.ticklabel_format(style="plain", useOffset=False)
            fig.tight_layout()
            path = out_dir / f"{name}-{k:02d}.png"
            fig.savefig(path)
            plt.close(fig)
            written.append(path)
    return written
