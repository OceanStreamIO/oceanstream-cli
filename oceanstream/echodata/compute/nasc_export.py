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
    import pandas as pd
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

    output_dir = Path(output_dir)

    df = _nasc_frame(ds_nasc, campaign_id, file_id)

    if len(df) == 0:
        logger.warning("No non-NaN NASC values to export for file_id=%s", file_id)
        return output_dir

    # Compute spatial bins (floor to grid)
    df["lat_bin"] = (np.floor(df["latitude"] / lat_bin_size) * lat_bin_size).astype(int)
    df["lon_bin"] = (np.floor(df["longitude"] / lon_bin_size) * lon_bin_size).astype(int)

    geometry = gpd.points_from_xy(df["longitude"], df["latitude"])
    gdf = gpd.GeoDataFrame(df, geometry=geometry, crs="EPSG:4326")

    # Write Hive-partitioned GeoParquet
    partition_cols = ["campaign_id", "lat_bin", "lon_bin"]

    import pyarrow.parquet as pq

    table = gdf.to_arrow()
    pq.write_to_dataset(
        table,
        root_path=str(output_dir / "nasc"),
        partitioning=partition_cols,
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
    return _nasc_frame(ds_nasc, campaign_id, file_id).to_dict("records")


def _nasc_frame(
    ds_nasc: "xr.Dataset",
    campaign_id: str,
    file_id: str,
) -> "pd.DataFrame":
    """One row per non-NaN cell, in channel, distance, depth order.

    The arrays are read once: selecting cell by cell from a lazily opened store
    reads the store again for every cell, which took minutes per day.
    """
    import pandas as pd

    lat, lon = _resolve_location(ds_nasc)

    channels = ds_nasc["channel"].values if "channel" in ds_nasc.dims else np.array([None])
    distances = ds_nasc["distance"].values if "distance" in ds_nasc.dims else np.array([0])
    depths = ds_nasc["depth"].values if "depth" in ds_nasc.dims else np.array([0])
    shape = (len(channels), len(distances), len(depths))

    def grid(name: str) -> np.ndarray:
        var = ds_nasc[name]
        dims = [d for d in ("channel", "distance", "depth") if d in var.dims]
        values = np.asarray(var.transpose(*dims).values, dtype=float)
        for axis, dim in enumerate(("channel", "distance", "depth")):
            if dim not in var.dims:
                values = np.expand_dims(values, axis)
        return np.broadcast_to(values, shape)

    nasc = grid("NASC")
    ch_idx, dist_idx, depth_idx = np.nonzero(~np.isnan(nasc))

    ping_time = np.full(len(distances), None, dtype=object)
    if "ping_time" in ds_nasc.data_vars and "distance" in ds_nasc["ping_time"].dims:
        ping_time = np.asarray(
            [str(t) for t in ds_nasc["ping_time"].values.reshape(len(distances))], dtype=object
        )
    lat_d = np.broadcast_to(np.asarray(lat, dtype=float), (len(distances),))
    lon_d = np.broadcast_to(np.asarray(lon, dtype=float), (len(distances),))

    df = pd.DataFrame({
        "campaign_id": campaign_id,
        "file_id": file_id,
        "channel": np.asarray(
            [str(c) if c is not None else "single" for c in channels], dtype=object
        )[ch_idx],
        "ping_time": ping_time[dist_idx],
        "depth": depths.astype(float)[depth_idx],
        "nasc_value": nasc[ch_idx, dist_idx, depth_idx],
        "latitude": lat_d[dist_idx],
        "longitude": lon_d[dist_idx],
    })
    if "NASC_log" in ds_nasc.data_vars:
        df["nasc_log"] = grid("NASC_log")[ch_idx, dist_idx, depth_idx]
    return df


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
