"""Greyscale PNG quick-looks of multibeam grids, read from their VRT mosaics."""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np

MAX_PIXELS = 2000


def _read_decimated(vrt: Path) -> tuple[np.ndarray, tuple[float, float, float, float], float]:
    import rasterio  # noqa: PLC0415 - optional dependency
    from rasterio.enums import Resampling  # noqa: PLC0415

    with rasterio.open(vrt) as src:
        factor = max(1.0, max(src.width, src.height) / MAX_PIXELS)
        shape = (max(1, math.ceil(src.height / factor)), max(1, math.ceil(src.width / factor)))
        data = src.read(1, out_shape=shape, resampling=Resampling.average, masked=True)
        b = src.bounds
        cell = src.res[0] * src.width / shape[1]
    arr = data.filled(np.nan).astype(np.float64)
    rows = np.flatnonzero(np.isfinite(arr).any(axis=1))
    cols = np.flatnonzero(np.isfinite(arr).any(axis=0))
    if rows.size == 0:
        return arr, (b.left, b.right, b.bottom, b.top), cell
    # Crop to the cells with data, with a margin of 2 % of the larger side.
    pad = max(2, int(0.02 * max(arr.shape)))
    r0, r1 = max(rows[0] - pad, 0), min(rows[-1] + pad + 1, arr.shape[0])
    c0, c1 = max(cols[0] - pad, 0), min(cols[-1] + pad + 1, arr.shape[1])
    dx = (b.right - b.left) / arr.shape[1]
    dy = (b.top - b.bottom) / arr.shape[0]
    extent = (b.left + c0 * dx, b.left + c1 * dx, b.top - r1 * dy, b.top - r0 * dy)
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


def write_quicklooks(grid_dir: Path, out_dir: Path, title: str) -> list[Path]:
    """Write depth.png (greyscale depth with hillshade) and backscatter.png for a grid."""
    import matplotlib  # noqa: PLC0415 - optional dependency

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt  # noqa: PLC0415

    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    plt.rcParams.update({"font.size": 15})
    for name, label in (("depth", "Depth below the water line (m)"),
                        ("backscatter", "Backscatter, angular response removed (dB)")):
        vrt = grid_dir / f"{name}.vrt"
        if not vrt.exists():
            continue
        data, extent, cell = _read_decimated(vrt)
        if not np.isfinite(data).any():
            continue
        lo, hi = np.nanpercentile(data, [2, 98])
        fig, ax = plt.subplots(figsize=(12, 10), dpi=100)
        cmap = "Greys" if name == "depth" else "gray"
        im = ax.imshow(data, extent=extent, cmap=cmap, vmin=lo, vmax=hi, interpolation="nearest")
        if name == "depth":
            shade = np.where(np.isfinite(data), _hillshade(data, cell), np.nan)
            ax.imshow(shade, extent=extent, cmap="gray", alpha=0.35, interpolation="nearest")
        cb = fig.colorbar(im, ax=ax, shrink=0.8)
        cb.set_label(label)
        ax.set_title(f"{title}: {name} ({cell:.0f} m cells shown)")
        ax.set_xlabel("Easting (m)")
        ax.set_ylabel("Northing (m)")
        ax.ticklabel_format(style="plain", useOffset=False)
        fig.tight_layout()
        path = out_dir / f"{name}.png"
        fig.savefig(path)
        plt.close(fig)
        written.append(path)
    return written
