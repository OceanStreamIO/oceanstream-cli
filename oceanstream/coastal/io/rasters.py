"""Raster grids, reprojection and COG output.

The prototype reimplemented the same reproject-onto-the-sensor-grid block in
five files (``run_demo``, ``reef_calibration``, ``point_diagnostic``,
``sdb_comparison``, ``slide_gallery``), each opening the source, allocating a
NaN destination and calling :func:`rasterio.warp.reproject` with slightly
different defaults. Five copies of a resampling choice is five chances for a
depth grid to be nearest-neighboured into one product and bilinearly
interpolated into another, and for the difference to be invisible in the
output. It lives here once.

The grid is a value, not a handle
---------------------------------
:class:`RasterGrid` stores affine coefficients and a CRS string rather than
rasterio objects. That makes it hashable, comparable and JSON-serialisable, so
a run report can record exactly which grid a product was computed on and two
products can be checked for alignment by equality rather than by eyeballing
their bounds. It also keeps rasterio — an optional dependency — out of the
import path until an array is actually read.
"""

from __future__ import annotations

import logging
import math
import os
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

BBox = tuple[float, float, float, float]

#: Metres per degree of latitude. Longitude is scaled by cos(latitude).
_M_PER_DEG = 111_320.0

_RESAMPLING_FOR_CONTINUOUS = "bilinear"


def _require_rasterio() -> Any:
    try:
        import rasterio
    except ImportError as exc:  # pragma: no cover - exercised only without the extra
        raise ImportError(
            "Raster IO needs rasterio. Install the coastal extra:\n"
            '    pip install "oceanstream[coastal]"'
        ) from exc
    return rasterio


@dataclass(frozen=True)
class RasterGrid:
    """A raster's geometry — size, affine transform and CRS.

    ``transform`` holds the six affine coefficients in rasterio's ``(a, b, c,
    d, e, f)`` order: x-scale, x-shear, x-origin, y-shear, y-scale, y-origin.
    """

    width: int
    height: int
    transform: tuple[float, float, float, float, float, float]
    crs: str

    def __post_init__(self) -> None:
        if self.width <= 0 or self.height <= 0:
            raise ValueError(
                f"RasterGrid must have positive extent; got {self.width}x{self.height}."
            )
        if len(self.transform) != 6:
            raise ValueError(
                f"RasterGrid.transform needs 6 affine coefficients; got {self.transform}."
            )

    # -- Construction -------------------------------------------------------

    @classmethod
    def from_dataset(cls, src: Any) -> RasterGrid:
        """Build from an open rasterio dataset."""
        t = src.transform
        return cls(
            width=int(src.width),
            height=int(src.height),
            transform=(t.a, t.b, t.c, t.d, t.e, t.f),
            crs=str(src.crs),
        )

    @classmethod
    def from_profile(cls, profile: Mapping[str, Any]) -> RasterGrid:
        """Build from a rasterio profile dict."""
        t = profile["transform"]
        coeffs = tuple(t)[:6] if not hasattr(t, "a") else (t.a, t.b, t.c, t.d, t.e, t.f)
        return cls(
            width=int(profile["width"]),
            height=int(profile["height"]),
            transform=(
                float(coeffs[0]),
                float(coeffs[1]),
                float(coeffs[2]),
                float(coeffs[3]),
                float(coeffs[4]),
                float(coeffs[5]),
            ),
            crs=str(profile["crs"]),
        )

    @classmethod
    def open(cls, uri: str | Path) -> RasterGrid:
        """Read a raster's grid without reading its pixels."""
        rasterio = _require_rasterio()
        with rasterio.open(str(uri)) as src:
            return cls.from_dataset(src)

    # -- Derived properties -------------------------------------------------

    @property
    def shape(self) -> tuple[int, int]:
        """(height, width) — numpy's ordering, not rasterio's."""
        return (self.height, self.width)

    @property
    def is_geographic(self) -> bool:
        """Whether the CRS is in degrees. Determines the metre conversion."""
        return self.crs.upper().replace(" ", "") in {"EPSG:4326", "EPSG:4979", "OGC:CRS84"}

    @property
    def bounds(self) -> BBox:
        """(west, south, east, north) in the grid's own CRS."""
        a, b, c, d, e, f = self.transform
        xs = [c, c + a * self.width, c + b * self.height, c + a * self.width + b * self.height]
        ys = [f, f + d * self.width, f + e * self.height, f + d * self.width + e * self.height]
        return (min(xs), min(ys), max(xs), max(ys))

    @property
    def pixel_size_m(self) -> float:
        """Mean pixel size in metres.

        Needed because several diagnostics configure their window sizes in
        metres — a 100 m scatter block is 10 px on Sentinel-2 and 83 px on
        Pléiades Neo, and hard-coding either makes the diagnostic untransferable.
        For a geographic CRS the longitude scale is corrected by the cosine of
        the grid's centre latitude, which is exact enough over an AOI a few tens
        of kilometres across.
        """
        a, _, _, _, e, _ = self.transform
        x_size, y_size = abs(a), abs(e)
        if not self.is_geographic:
            return float((x_size + y_size) / 2.0)
        west, south, east, north = self.bounds
        centre_lat = (south + north) / 2.0
        x_m = x_size * _M_PER_DEG * math.cos(math.radians(centre_lat))
        y_m = y_size * _M_PER_DEG
        return float((x_m + y_m) / 2.0)

    def matches(self, other: RasterGrid, tolerance: float = 1e-6) -> bool:
        """Whether two grids are the same to within floating-point noise."""
        return (
            self.width == other.width
            and self.height == other.height
            and self.crs == other.crs
            and all(
                abs(x - y) <= tolerance for x, y in zip(self.transform, other.transform)
            )
        )

    def to_profile(
        self,
        dtype: str = "float32",
        count: int = 1,
        nodata: float | None = float("nan"),
        driver: str = "COG",
        compress: str = "deflate",
    ) -> dict[str, Any]:
        """A rasterio creation profile for this grid."""
        _require_rasterio()
        from rasterio.crs import CRS
        from rasterio.transform import Affine

        return {
            "driver": driver,
            "width": self.width,
            "height": self.height,
            "count": count,
            "dtype": dtype,
            "crs": CRS.from_string(self.crs),
            "transform": Affine(*self.transform),
            "nodata": nodata,
            "compress": compress,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "width": self.width,
            "height": self.height,
            "transform": list(self.transform),
            "crs": self.crs,
            "bounds": list(self.bounds),
            "pixel_size_m": round(self.pixel_size_m, 4),
        }


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


def read_band(uri: str | Path, band: int = 1) -> tuple[np.ndarray, RasterGrid]:
    """Read one band as float32 with nodata converted to NaN.

    NaN rather than a sentinel because every downstream operation is a
    floating-point reduction, and a forgotten ``== nodata`` comparison produces
    a plausible-looking number instead of an obvious failure.
    """
    rasterio = _require_rasterio()
    with rasterio.open(str(uri)) as src:
        data = src.read(band).astype(np.float32)
        grid = RasterGrid.from_dataset(src)
        nodata = src.nodatavals[band - 1]
    if nodata is not None and np.isfinite(nodata):
        data = np.where(data == np.float32(nodata), np.nan, data)
    return data, grid


def read_stack(uris: Sequence[str | Path]) -> tuple[np.ndarray, RasterGrid]:
    """Read several single-band rasters into one ``(n, H, W)`` cube.

    Refuses to stack rasters that are not on the same grid. Numpy would happily
    stack two arrays of equal shape from different footprints, and every
    per-pixel band ratio computed afterwards would be meaningless.
    """
    if not uris:
        raise ValueError("read_stack needs at least one raster URI.")
    bands = []
    reference: RasterGrid | None = None
    for uri in uris:
        data, grid = read_band(uri)
        if reference is None:
            reference = grid
        elif not grid.matches(reference):
            raise ValueError(
                f"{uri} is on a different grid from {uris[0]}:\n"
                f"  expected {reference.to_dict()}\n"
                f"  found    {grid.to_dict()}\n"
                "Reproject to a common grid with reproject_to_grid() first."
            )
        bands.append(data)
    assert reference is not None
    return np.stack(bands, axis=0), reference


# ---------------------------------------------------------------------------
# Reprojection
# ---------------------------------------------------------------------------


def reproject_to_grid(
    source: str | Path | np.ndarray,
    grid: RasterGrid,
    src_grid: RasterGrid | None = None,
    resampling: str = _RESAMPLING_FOR_CONTINUOUS,
    band: int = 1,
) -> np.ndarray:
    """Resample a raster onto ``grid``, filling unmapped pixels with NaN.

    ``source`` may be a URI or an in-memory array; an array requires
    ``src_grid``. The default is bilinear because every raster this is used on —
    bathymetry, reflectance, SDB — is continuous. Pass ``"nearest"`` for class
    labels, where interpolation would invent classes that do not exist.
    """
    _require_rasterio()
    from rasterio.crs import CRS
    from rasterio.transform import Affine
    from rasterio.warp import Resampling
    from rasterio.warp import reproject as _reproject

    try:
        method = Resampling[resampling]
    except KeyError:
        raise ValueError(
            f"Unknown resampling {resampling!r}. Available: "
            f"{', '.join(sorted(r.name for r in Resampling))}."
        ) from None

    if isinstance(source, np.ndarray):
        if src_grid is None:
            raise ValueError("reproject_to_grid needs src_grid when source is an array.")
        src_data = source.astype(np.float32)
        source_grid = src_grid
    else:
        src_data, source_grid = read_band(source, band=band)
        if src_grid is not None:
            source_grid = src_grid

    if source_grid.matches(grid):
        return src_data.copy()

    destination = np.full(grid.shape, np.nan, dtype=np.float32)
    _reproject(
        source=src_data,
        destination=destination,
        src_transform=Affine(*source_grid.transform),
        src_crs=CRS.from_string(source_grid.crs),
        src_nodata=np.nan,
        dst_transform=Affine(*grid.transform),
        dst_crs=CRS.from_string(grid.crs),
        dst_nodata=np.nan,
        resampling=method,
    )
    covered = float(np.isfinite(destination).mean())
    if covered < 0.5:
        logger.warning(
            "reprojection covered only %.1f%% of the target grid — check that the "
            "source footprint %s overlaps the target %s",
            100.0 * covered,
            source_grid.bounds,
            grid.bounds,
        )
    return destination


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------


def write_cog(
    uri: str | Path,
    data: np.ndarray,
    grid: RasterGrid,
    nodata: float = float("nan"),
    tags: Mapping[str, str] | None = None,
    dtype: str = "float32",
) -> str:
    """Write a Cloud-Optimized GeoTIFF, locally or to object storage.

    Cloud destinations (``az://``, ``s3://``, ``gs://``) are staged through a
    temporary local file, because GDAL's COG driver needs to seek while building
    overviews and object stores are append-only. Local writes go via a
    ``.tmp.tif`` and an atomic rename, so an interrupted run leaves no
    half-written raster that a later stage would happily read.

    ``tags`` are written into the GeoTIFF itself rather than a sidecar, so
    provenance survives the file being copied somewhere the sidecar is not.
    """
    rasterio = _require_rasterio()

    if data.ndim != 2:
        raise ValueError(f"write_cog expects a 2-D array; got shape {data.shape}.")
    if data.shape != grid.shape:
        raise ValueError(
            f"Array shape {data.shape} does not match grid {grid.shape}. "
            "Reproject with reproject_to_grid() before writing."
        )

    uri_str = str(uri)
    profile = grid.to_profile(dtype=dtype, count=1, nodata=nodata)

    def _write(target: Path) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        with rasterio.open(target, "w", **profile) as dst:
            dst.write(data.astype(dtype), 1)
            if tags:
                dst.update_tags(**{k: str(v) for k, v in tags.items()})

    if _is_cloud_uri(uri_str):
        with tempfile.TemporaryDirectory(prefix="oceanstream-coastal-") as tmpdir:
            staged = Path(tmpdir) / Path(uri_str).name
            _write(staged)
            _upload(staged, uri_str)
        logger.info("wrote %s (%dx%d)", uri_str, grid.width, grid.height)
        return uri_str

    out = Path(uri_str)
    tmp = out.with_suffix(out.suffix + ".tmp")
    _write(tmp)
    os.replace(tmp, out)
    logger.info("wrote %s (%dx%d)", out, grid.width, grid.height)
    return str(out)


def _is_cloud_uri(uri: str) -> bool:
    from oceanstream.storage.filesystem import parse_storage_uri

    scheme, bucket, _ = parse_storage_uri(uri)
    return scheme != "local" and bool(bucket)


def _upload(local_path: Path, uri: str) -> None:
    """Copy a staged local file to a cloud URI via the storage abstraction."""
    from oceanstream.storage.filesystem import resolve_output_path

    resolved = resolve_output_path(uri)
    with (
        local_path.open("rb") as src,
        resolved.filesystem.open_output_stream(resolved.path) as dst,
    ):
        while chunk := src.read(8 * 1024 * 1024):
            dst.write(chunk)
