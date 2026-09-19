#!/usr/bin/env python3
"""Subset NOAA's published 1 m Key West DEM and aggregate observed cells.

The DEM contains NOAA's own interpolation of ground/bathymetric lidar returns.
This preparation performs no gap filling. Counts are valid DEM cells, not lidar
returns; within-cell standard deviations describe terrain variation, not the
uncertainty of the mean. Run in an environment with rasterio, numpy, and requests.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import rasterio
import requests
from rasterio.transform import from_origin
from rasterio.warp import Resampling, reproject
from rasterio.windows import from_bounds

SOURCE_URL = (
    "https://noaa-nos-coastal-lidar-pds.s3.amazonaws.com/dem/"
    "NGS_FL_key_west_DEM_2016_6366/2016_key_west_mosaic_m6366.tif"
)
BOUNDS = (423000, 2721000, 426000, 2724000)
NODATA = -9999.0


def sha256(path: Path) -> str:
    """Hash a local input or product without loading it wholly into memory."""
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_raster(
    path: Path, values: np.ndarray, resolution: int, bounds: tuple[int, ...], **tags: str
) -> None:
    """Write a raster at the native UTM origin with explicit source metadata."""
    transform = from_origin(bounds[0], bounds[3], resolution, resolution)
    crs = "EPSG:32617"
    if resolution == 10:
        # Preserve the originally registered NAD83 target grid through an explicit
        # transform, rather than relabeling WGS84. Here PROJ shifts <0.1 mm.
        destination = np.full(values.shape, np.nan, dtype="float32")
        reproject(
            values.astype("float32"),
            destination,
            src_transform=transform,
            src_crs=crs,
            src_nodata=np.nan,
            dst_transform=transform,
            dst_crs="EPSG:6346",
            dst_nodata=np.nan,
            resampling=Resampling.nearest,
        )
        values = destination
        crs = "EPSG:6346"
    units = tags.pop("units", "m")
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=values.shape[1],
        height=values.shape[0],
        count=1,
        dtype="float32",
        crs=crs,
        transform=transform,
        nodata=NODATA,
        compress="deflate",
        predictor=3,
        tiled=True,
    ) as dst:
        dst.write(np.where(np.isfinite(values), values, NODATA).astype("float32"), 1)
        dst.update_tags(
            source=SOURCE_URL,
            vertical_datum="NAVD88",
            geoid_model="GEOID12B",
            positive_direction="up",
            units=units,
            gap_filling="none during experiment preparation",
            **tags,
        )


def prepare(output: Path, bounds: tuple[int, ...] = BOUNDS) -> dict:
    """Read a range subset, keeping the provider's raster values and nodata."""
    if (output / "lidar_preparation.json").exists():
        raise FileExistsError("Reference already completed; use its products or a new directory.")
    output.mkdir(parents=True, exist_ok=True)
    if len(bounds) != 4 or any(value % 10 for value in bounds):
        raise ValueError("Bounds must contain four UTM coordinates aligned to 10m")
    height, width = bounds[3] - bounds[1], bounds[2] - bounds[0]
    if height <= 0 or width <= 0:
        raise ValueError("Bounds must enclose a positive-area rectangle")
    shape = (height // 10, width // 10)
    native = output / "elevation_navd88_1m.tif"
    headers_path = output / "dem_http_metadata.json"
    if not native.exists():
        response = requests.head(SOURCE_URL, timeout=60)
        response.raise_for_status()
        headers = {
            key: response.headers.get(key)
            for key in ("Content-Length", "ETag", "Last-Modified", "Content-Type")
        }
        headers_path.write_text(json.dumps(headers, indent=2) + "\n")
        with (
            rasterio.Env(
                GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR",
                CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif",
            ),
            rasterio.open(SOURCE_URL) as src,
        ):
            if src.crs != rasterio.crs.CRS.from_epsg(32617) or src.res != (1, 1):
                raise ValueError("Published DEM no longer has the expected CRS/resolution")
            # Native bounds snap exactly to the preregistered UTM rectangle.
            window = from_bounds(*bounds, transform=src.transform)
            if tuple(src.window_bounds(window)) != bounds:
                raise ValueError("Published DEM grid no longer matches frozen ROI")
            values = src.read(1, window=window, masked=True).filled(np.nan)
        write_raster(native, values, 1, bounds, product="native NOAA DEM subset")
    else:
        with rasterio.open(native) as src:
            if tuple(src.bounds) != bounds or src.shape != (height, width):
                raise ValueError("Cached native reference bounds do not match requested ROI")
            values = src.read(1, masked=True).filled(np.nan)

    blocks = values.reshape(shape[0], 10, shape[1], 10).transpose(0, 2, 1, 3)
    finite = np.isfinite(blocks)
    count = finite.sum(axis=(2, 3))
    total = np.where(finite, blocks, 0).sum(axis=(2, 3), dtype="float64")
    mean = np.divide(total, count, out=np.full(shape, np.nan), where=count > 0)
    differences = np.where(finite, blocks - mean[:, :, None, None], 0)
    variance = np.divide(
        np.square(differences).sum(axis=(2, 3)),
        count,
        out=np.full(shape, np.nan),
        where=count > 0,
    )
    # Require all 100 native cells: partial coverage never certifies a whole cell.
    full = count == 100
    mean[~full] = np.nan
    sd = np.sqrt(variance)
    sd[~full] = np.nan
    write_raster(
        output / "elevation_navd88_10m.tif",
        mean,
        10,
        bounds,
        product="mean of 100 native 1m DEM cells",
        required_native_coverage_fraction="1.0",
    )
    write_raster(
        output / "dem_valid_cell_count_10m.tif",
        count.astype(float),
        10,
        bounds,
        product="valid 1m DEM cells per 10m cell; not lidar point count",
        units="count",
    )
    write_raster(
        output / "dem_spatial_stddev_10m.tif",
        sd,
        10,
        bounds,
        product="population standard deviation of native DEM cells; terrain variation",
    )
    uncertainty = np.where(full, 0.15, np.nan)
    write_raster(
        output / "source_reported_vertical_accuracy_1sigma_m_10m.tif",
        uncertainty,
        10,
        bounds,
        product="NOAA reported survey vertical positional accuracy, 1 sigma",
        uncertainty_limitations="not full bathymetric, interpolation, or model uncertainty",
    )
    products = {}
    for path in sorted(output.glob("*.tif")):
        if "mllw" not in path.name:
            products[path.name] = {"sha256": sha256(path), "bytes": path.stat().st_size}
    report = {
        "schema_version": "1.0",
        "prepared_at": datetime.now(UTC).isoformat(),
        "source_url": SOURCE_URL,
        "source_catalog_url": "https://www.fisheries.noaa.gov/inport/item/48372",
        "source_survey_dates": ["2016-04-19", "2016-04-25"],
        "source_provider": "NOAA NGS / NOAA Office for Coastal Management",
        "source_instrument_metadata": "Riegl VQ820G",
        "paper_instrument_discrepancy": (
            "Paper specifies VQ-880-G; exact survey equivalence unconfirmed"
        ),
        "source_horizontal_crs": "EPSG:32617 as tagged in official DEM",
        "target_horizontal_crs": (
            "EPSG:6346; explicit nearest-cell coordinate transformation after block aggregation"
        ),
        "source_horizontal_lineage": (
            "Original survey metadata describe NAD83 / UTM17N; "
            "preserve provider raster CRS rather than silently relabel"
        ),
        "roi_bounds_native": list(bounds),
        "roi_original_selection_crs": (
            "EPSG:6346; current PROJ shifts selected bounds by less than 0.1mm to EPSG:32617; "
            "underlying frame uncertainty retained"
        ),
        "source_vertical_datum": "NAVD88",
        "source_geoid": "GEOID12B",
        "elevation_positive_direction": "up",
        "resolution_m": 10,
        "aggregation": "arithmetic mean of complete 10x10 native 1m blocks; no gap filling",
        "coverage_requirement": "100/100 native 1m cells valid",
        "native_dem_interpolation": (
            "NOAA ground and bathy points -> LAStools las2dem -> mosaic; "
            "point support not available"
        ),
        "standard_deviation_definition": (
            "Within-cell terrain variation; not uncertainty or standard error"
        ),
        "provider_accuracy": {"vertical_1sigma_m": 0.15, "horizontal_2sigma_m": 1.0},
        "provider_accuracy_limit": (
            "Survey positional specification; not independently assessed bathymetric uncertainty"
        ),
        "full_cells": int(full.sum()),
        "partial_cells": int(((count > 0) & ~full).sum()),
        "empty_cells": int((count == 0).sum()),
        "elevation_navd88_percentiles_m": dict(
            zip(
                ["0", "5", "50", "95", "100"],
                map(float, np.nanpercentile(mean, [0, 5, 50, 95, 100])),
            )
        ),
        "remote_object_headers": json.loads(headers_path.read_text()),
        "checksum_scope": (
            "SHA256 pins downloaded subset bytes and derived local products; "
            "full remote object not downloaded or SHA256 hashed"
        ),
        "products": products,
    }
    (output / "lidar_preparation.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, default=Path(".cache/coastal/reference/key-west-2017/lidar")
    )
    parser.add_argument("--bounds", type=int, nargs=4, default=BOUNDS)
    arguments = parser.parse_args()
    print(json.dumps(prepare(arguments.output, tuple(arguments.bounds)), indent=2))
