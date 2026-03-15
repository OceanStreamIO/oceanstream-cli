"""Export NASC datasets to spatially-indexed GeoParquet.

Flattens the NASC xarray Dataset (channel × distance × depth) to a
tabular GeoDataFrame with one row per non-NaN integration cell and
writes Hive-partitioned GeoParquet files for cross-file spatial queries.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Optional

import numpy as np

if TYPE_CHECKING:
    import geopandas as gpd
    import xarray as xr

logger = logging.getLogger(__name__)


def export_nasc_to_geoparquet(
    ds_nasc: "xr.Dataset",
    output_dir: Path | str,
    campaign_id: str,
    file_id: str,
    lat_bin_size: float = 1.0,
    lon_bin_size: float = 1.0,
) -> Path:
    """Flatten NASC dataset and write as Hive-partitioned GeoParquet.

    Each row is one integration cell with its geographic location,
    enabling efficient spatial / temporal queries across many files
    without requiring a database.

    Parameters
    ----------
    ds_nasc : xr.Dataset
        NASC dataset from :func:`compute_nasc`, with variables ``NASC``,
        ``NASC_log``, ``latitude``, ``longitude``, ``ping_time``, and
        coordinates ``channel``, ``distance``, ``depth``.
    output_dir : Path
        Root directory for partitioned output.
    campaign_id : str
        Campaign identifier used for partitioning.
    file_id : str
        Source file identifier.
    lat_bin_size : float
        Latitude bin width in degrees (default 1°).
    lon_bin_size : float
        Longitude bin width in degrees (default 1°).

    Returns
    -------
    Path
        The output directory containing Hive-partitioned Parquet files.
    """
    import geopandas as gpd
    import pandas as pd
    from shapely.geometry import Point

    output_dir = Path(output_dir)

    records = _flatten_nasc(ds_nasc, campaign_id, file_id)

    if len(records) == 0:
        logger.warning("No non-NaN NASC values to export for file_id=%s", file_id)
        return output_dir

    df = pd.DataFrame(records)

    # Compute spatial bins (floor to grid)
    df["lat_bin"] = (np.floor(df["latitude"] / lat_bin_size) * lat_bin_size).astype(int)
    df["lon_bin"] = (np.floor(df["longitude"] / lon_bin_size) * lon_bin_size).astype(int)

    geometry = [Point(lon, lat) for lon, lat in zip(df["longitude"], df["latitude"])]
    gdf = gpd.GeoDataFrame(df, geometry=geometry, crs="EPSG:4326")

    # Write Hive-partitioned GeoParquet
    partition_cols = ["campaign_id", "lat_bin", "lon_bin"]

    gdf.to_parquet(
        output_dir / "nasc",
        engine="pyarrow",
        partition_cols=partition_cols,
        existing_data_behavior="overwrite_or_ignore",
    )

    logger.info(
        "Exported %d NASC points to GeoParquet at %s",
        len(gdf),
        output_dir / "nasc",
    )
    return output_dir / "nasc"


def _flatten_nasc(
    ds_nasc: "xr.Dataset",
    campaign_id: str,
    file_id: str,
) -> list[dict]:
    """Flatten NASC dataset dimensions into a list of row dicts."""
    import xarray as xr

    records: list[dict] = []

    nasc_var = ds_nasc["NASC"]
    has_log = "NASC_log" in ds_nasc.data_vars

    # Get latitude and longitude (may be per-distance or global attrs)
    lat, lon = _resolve_location(ds_nasc)

    channels = ds_nasc["channel"].values if "channel" in ds_nasc.dims else [None]
    distances = ds_nasc["distance"].values if "distance" in ds_nasc.dims else [0]
    depths = ds_nasc["depth"].values if "depth" in ds_nasc.dims else [0]

    # Get ping_time per distance bin (if available)
    ping_times = None
    if "ping_time" in ds_nasc.data_vars:
        ping_times = ds_nasc["ping_time"]

    for ch_idx, ch in enumerate(channels):
        for dist_idx, dist in enumerate(distances):
            for depth_idx, depth in enumerate(depths):
                sel = {}
                if ch is not None:
                    sel["channel"] = ch
                if "distance" in nasc_var.dims:
                    sel["distance"] = dist
                if "depth" in nasc_var.dims:
                    sel["depth"] = depth

                val = float(nasc_var.sel(sel).values)
                if np.isnan(val):
                    continue

                pt = None
                if ping_times is not None and "distance" in ping_times.dims:
                    pt = str(ping_times.sel(distance=dist).values)

                rec_lat = lat[dist_idx] if hasattr(lat, "__len__") else lat
                rec_lon = lon[dist_idx] if hasattr(lon, "__len__") else lon

                record = {
                    "campaign_id": campaign_id,
                    "file_id": file_id,
                    "channel": str(ch) if ch is not None else "single",
                    "ping_time": pt,
                    "depth": float(depth),
                    "nasc_value": val,
                    "latitude": float(rec_lat),
                    "longitude": float(rec_lon),
                }

                if has_log:
                    record["nasc_log"] = float(
                        ds_nasc["NASC_log"].sel(sel).values
                    )

                records.append(record)

    return records


def _resolve_location(ds_nasc: "xr.Dataset") -> tuple:
    """Extract latitude/longitude arrays from NASC dataset.

    Location may appear as:
    - data_vars with distance dim (from echopype _get_reduced_positions)
    - global attributes (geospatial_lat_min / geospatial_lat_max)
    """
    # Try data vars first
    if "latitude" in ds_nasc.data_vars:
        lat = ds_nasc["latitude"].values
        lon = ds_nasc["longitude"].values
        # Flatten if multi-dim
        if lat.ndim > 1:
            lat = np.nanmean(lat, axis=tuple(range(1, lat.ndim)))
            lon = np.nanmean(lon, axis=tuple(range(1, lon.ndim)))
        return lat, lon

    # Fall back to global attributes (midpoint)
    lat_min = ds_nasc.attrs.get("geospatial_lat_min")
    lat_max = ds_nasc.attrs.get("geospatial_lat_max")
    lon_min = ds_nasc.attrs.get("geospatial_lon_min")
    lon_max = ds_nasc.attrs.get("geospatial_lon_max")

    if lat_min is not None and lon_min is not None:
        lat = (float(lat_min) + float(lat_max)) / 2
        lon = (float(lon_min) + float(lon_max)) / 2
        return lat, lon

    raise ValueError(
        "Cannot determine location for NASC export. "
        "Dataset needs 'latitude'/'longitude' data_vars or "
        "geospatial_lat_min/lon_min global attributes."
    )


def load_nasc_geoparquet(
    parquet_dir: Path | str,
    bbox: Optional[tuple[float, float, float, float]] = None,
    time_range: Optional[tuple[str, str]] = None,
    channel: Optional[str] = None,
    campaign_id: Optional[str] = None,
) -> "gpd.GeoDataFrame":
    """Load NASC points from GeoParquet with optional spatial/temporal filters.

    Parameters
    ----------
    parquet_dir : Path
        Root of Hive-partitioned NASC parquet (the ``nasc/`` directory).
    bbox : tuple, optional
        ``(min_lon, min_lat, max_lon, max_lat)`` bounding box filter.
    time_range : tuple, optional
        ``(start, end)`` ISO-8601 strings for temporal filtering.
    channel : str, optional
        Filter to a specific channel.
    campaign_id : str, optional
        Filter to a specific campaign (leverages Hive partition pruning).

    Returns
    -------
    gpd.GeoDataFrame
        Filtered NASC points with geometry.
    """
    import geopandas as gpd

    parquet_dir = Path(parquet_dir)

    filters = []
    if campaign_id:
        filters.append(("campaign_id", "==", campaign_id))
    if channel:
        filters.append(("channel", "==", channel))

    # Use bbox if provided for Hive partition pruning
    if bbox:
        min_lon, min_lat, max_lon, max_lat = bbox
        filters.append(("lat_bin", ">=", int(np.floor(min_lat))))
        filters.append(("lat_bin", "<=", int(np.floor(max_lat))))
        filters.append(("lon_bin", ">=", int(np.floor(min_lon))))
        filters.append(("lon_bin", "<=", int(np.floor(max_lon))))

    gdf = gpd.read_parquet(
        parquet_dir,
        filters=filters if filters else None,
    )

    # Fine-grained bbox filter (partition bins are coarse 1° grid)
    if bbox:
        gdf = gdf.cx[min_lon:max_lon, min_lat:max_lat]

    # Time range filter
    if time_range and "ping_time" in gdf.columns:
        start, end = time_range
        gdf = gdf[
            (gdf["ping_time"] >= start) & (gdf["ping_time"] <= end)
        ]

    return gdf
