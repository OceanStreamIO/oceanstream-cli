"""Location data interpolation and GPS merging for echodata.

Provides functions for:
- Merging external GPS location data into Sv datasets
- Extracting and thinning GPS track data from GeoDataFrames
- Ramer-Douglas-Peucker track simplification

EK80 files from Saildrone often lack embedded NMEA GPS data.
These functions enable interpolating coordinates from external
geoparquet track files into Sv datasets keyed on ``ping_time``.

"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Optional, Union

import numpy as np
import pandas as pd

if TYPE_CHECKING:
    import geopandas as gpd
    import xarray as xr

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Merge location data into Sv dataset
# ---------------------------------------------------------------------------

def merge_location_data(
    ds: "xr.Dataset",
    location_data: Union[list[dict], pd.DataFrame],
) -> "xr.Dataset":
    """Merge external GPS location data into an Sv xarray Dataset.

    Interpolates latitude, longitude, and speed onto the dataset's
    ``ping_time`` axis using nearest-neighbour interpolation.

    Args:
        ds: Sv xarray Dataset with a ``ping_time`` dimension.
        location_data: Either a list of dicts with keys
            ``{"lat", "lon", "dt", "knt"}`` or a DataFrame with
            those columns.

    Returns:
        Dataset with ``latitude``, ``longitude``, and ``speed_knots``
        variables aligned to ``ping_time``.
    """
    import xarray as xr

    if isinstance(location_data, list):
        df = pd.DataFrame(location_data)
    else:
        df = location_data.copy()

    df["dt"] = pd.to_datetime(df["dt"], utc=True, errors="coerce").dt.tz_localize(None)
    df = df.set_index("dt").sort_index()

    nav = df[["lat", "lon", "knt"]].to_xarray()
    nav = nav.rename(
        {"dt": "ping_time", "lat": "latitude", "lon": "longitude", "knt": "speed_knots"}
    )

    if "ping_time" in ds.coords:
        nav = nav.interp(
            ping_time=ds["ping_time"],
            method="nearest",
            kwargs={"fill_value": "extrapolate"},
        )

    # Drop any pre-existing location variables to avoid conflicts
    for v in ("latitude", "longitude", "speed_knots"):
        if v in ds.data_vars:
            ds = ds.drop_vars(v)
        if v in ds.coords:
            ds = ds.reset_coords(v, drop=True)

    # Drop stray "time" variable that echopype sometimes leaves
    for stray in ("time",):
        if stray in ds:
            ds = ds.drop_vars(stray)
        if stray in ds.coords:
            ds = ds.reset_coords(stray, drop=True)

    merged = xr.merge([ds, nav], compat="override")
    merged = merged.reset_coords(
        ["latitude", "longitude", "speed_knots"],
        drop=False,
    )

    # Clean up stray time again after merge
    for stray in ("time",):
        if stray in merged:
            merged = merged.drop_vars(stray)
        if stray in merged.coords:
            merged = merged.reset_coords(stray, drop=True)

    return merged


def interpolate_location_from_dataframe(
    ds: "xr.Dataset",
    df: pd.DataFrame,
) -> "xr.Dataset":
    """Interpolate lat/lon from a DataFrame onto Sv ping_time.

    Uses linear interpolation on the integer timestamps for
    sub-second accuracy.

    Args:
        ds: Sv xarray Dataset with ``ping_time`` dimension.
        df: DataFrame with columns ``lat``, ``lon``, ``dt``.

    Returns:
        Dataset with ``latitude`` and ``longitude`` coordinates.
    """
    df = df.copy()
    df["dt"] = pd.to_datetime(df["dt"])

    times_sv = pd.to_datetime(ds["ping_time"].values)
    interp_lat = np.interp(
        times_sv.astype(np.int64), df["dt"].astype(np.int64), df["lat"]
    )
    interp_lon = np.interp(
        times_sv.astype(np.int64), df["dt"].astype(np.int64), df["lon"]
    )

    ds = ds.assign_coords(
        latitude=("ping_time", interp_lat),
        longitude=("ping_time", interp_lon),
    )
    return ds


# ---------------------------------------------------------------------------
# Extract and thin GPS track from GeoDataFrame
# ---------------------------------------------------------------------------

def extract_location_data(
    gdf: "gpd.GeoDataFrame",
    epsilon: float = 0.00001,
    min_distance: float = 0.01,
) -> pd.DataFrame:
    """Extract and thin GPS track data from a GeoDataFrame.

    Applies Savitzky-Golay smoothing, computes speed, applies
    Ramer-Douglas-Peucker simplification, and enforces a minimum
    point-to-point distance.

    Args:
        gdf: GeoDataFrame with ``geometry``, ``latitude``,
            ``longitude``, and ``time`` columns.
        epsilon: RDP tolerance (degrees).
        min_distance: Minimum inter-point distance (nmi).

    Returns:
        DataFrame with columns ``lat``, ``lon``, ``dt``, ``knt``.
    """
    from haversine import haversine
    from scipy.signal import savgol_filter

    required = {"geometry", "latitude", "longitude", "time"}
    missing = required - set(gdf.columns)
    if missing:
        raise ValueError(f"GeoDataFrame is missing required columns: {missing}")

    df = gdf.copy()
    df = df.rename(columns={"latitude": "lat", "longitude": "lon", "time": "dt"})
    df = df.dropna(subset=["lat", "lon", "dt"])
    df = df[(df["lat"] >= -90) & (df["lat"] <= 90) & (df["lon"] >= -180) & (df["lon"] <= 180)]

    if df.empty:
        return pd.DataFrame(columns=["lat", "lon", "dt", "knt"])

    window_size = min(11, len(df))
    poly_order = 2

    if len(df) > window_size:
        df["lat"] = savgol_filter(df["lat"], window_size, poly_order)
        df["lon"] = savgol_filter(df["lon"], window_size, poly_order)

    df["distance"] = [
        haversine(
            (df["lat"].iloc[i], df["lon"].iloc[i - 1]),
            (df["lat"].iloc[i - 1], df["lon"].iloc[i]),
            unit="nmi",
        )
        if i > 0
        else 0
        for i in range(len(df))
    ]
    df["time_interval"] = df["dt"] - df["dt"].shift()
    df["knt"] = (df["distance"] / df["time_interval"].dt.total_seconds()) * 3600
    df = df[["lat", "lon", "dt", "knt"]]

    # Remove unrealistic speed values
    df = df[df["knt"] < 100]

    # RDP thinning
    points = df[["lat", "lon"]].values
    thinned_points = ramer_douglas_peucker(points, epsilon)
    thinned_df = pd.DataFrame(thinned_points, columns=["lat", "lon"])

    try:
        thinned_df["dt"] = thinned_df.apply(
            lambda row: df.loc[
                (df["lat"] == row["lat"]) & (df["lon"] == row["lon"]), "dt"
            ].values[0],
            axis=1,
        )
        thinned_df["knt"] = thinned_df.apply(
            lambda row: df.loc[
                (df["lat"] == row["lat"]) & (df["lon"] == row["lon"]), "knt"
            ].values[0],
            axis=1,
        )
    except Exception:
        logger.warning("Error mapping time/speed to thinned points.")

    # Enforce minimum distance
    final_points = [thinned_df.iloc[0]]
    for i in range(1, len(thinned_df)):
        if (
            haversine(
                (final_points[-1]["lat"], final_points[-1]["lon"]),
                (thinned_df.iloc[i]["lat"], thinned_df.iloc[i]["lon"]),
                unit="nmi",
            )
            >= min_distance
        ):
            final_points.append(thinned_df.iloc[i])

    return pd.DataFrame(final_points)


# ---------------------------------------------------------------------------
# Ramer-Douglas-Peucker algorithm
# ---------------------------------------------------------------------------

def ramer_douglas_peucker(points: np.ndarray, epsilon: float) -> np.ndarray:
    """Simplify a polyline using the Ramer-Douglas-Peucker algorithm.

    Args:
        points: (N, 2) array of coordinates.
        epsilon: Distance threshold for point removal.

    Returns:
        Simplified (M, 2) array with M ≤ N.
    """
    if len(points) < 3:
        return points

    def _perp_dist(point, line_start, line_end):
        if np.allclose(line_start, line_end):
            return np.linalg.norm(point - line_start)
        line_vec = line_end - line_start
        point_vec = point - line_start
        line_len = np.linalg.norm(line_vec)
        t = np.clip(np.dot(point_vec, line_vec) / (line_len ** 2), 0, 1)
        nearest = line_start + t * line_vec
        return np.linalg.norm(point - nearest)

    max_distance = 0
    index = 0
    for i in range(1, len(points) - 1):
        d = _perp_dist(points[i], points[0], points[-1])
        if d > max_distance:
            index = i
            max_distance = d

    if max_distance > epsilon:
        left = ramer_douglas_peucker(points[: index + 1], epsilon)
        right = ramer_douglas_peucker(points[index:], epsilon)
        return np.vstack((left[:-1], right))

    return np.vstack((points[0], points[-1]))


# ---------------------------------------------------------------------------
# Start/end coordinate extraction
# ---------------------------------------------------------------------------

def extract_start_end_lat_lon(ds: "xr.Dataset") -> dict[str, float]:
    """Extract start and end latitude/longitude from an Sv dataset.

    Args:
        ds: xarray Dataset with ``latitude`` and ``longitude`` variables
            and a ``ping_time`` dimension.

    Returns:
        Dict with keys ``file_start_lat``, ``file_end_lat``,
        ``file_start_lon``, ``file_end_lon``.  Empty dict if
        coordinates are unavailable.
    """
    if not all(v in ds.data_vars or v in ds.coords for v in ("latitude", "longitude")):
        logger.warning("Dataset missing latitude or longitude variables")
        return {}

    if "ping_time" not in ds.dims:
        logger.warning("Dataset missing ping_time dimension")
        return {}

    lat = ds["latitude"]
    lon = ds["longitude"]

    # Skip NaN at edges
    start_idx = 0
    while start_idx < len(lat) and (
        np.isnan(lat[start_idx].values) or np.isnan(lon[start_idx].values)
    ):
        start_idx += 1

    end_idx = -1
    while abs(end_idx) <= len(lat) and (
        np.isnan(lat[end_idx].values) or np.isnan(lon[end_idx].values)
    ):
        end_idx -= 1

    if start_idx >= len(lat) or abs(end_idx) > len(lat):
        logger.warning("No valid coordinate pairs found in dataset")
        return {}

    result = {
        "file_start_lat": float(lat.isel(ping_time=start_idx).values),
        "file_end_lat": float(lat.isel(ping_time=end_idx).values),
        "file_start_lon": float(lon.isel(ping_time=start_idx).values),
        "file_end_lon": float(lon.isel(ping_time=end_idx).values),
    }

    # Validate
    for key, val in result.items():
        if "lat" in key and not (-90 <= val <= 90):
            logger.warning(f"Invalid {key}: {val}")
            return {}
        if "lon" in key and not (-180 <= val <= 180):
            logger.warning(f"Invalid {key}: {val}")
            return {}

    return result

