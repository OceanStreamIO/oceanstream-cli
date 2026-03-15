"""
Bathymetric depth lookup for seabed masking decisions.

Queries ocean depth at a given lat/lon to determine whether acoustic
seabed detection is feasible (i.e. the seabed is within instrument range).

Two data sources, tried in order:
  1. Local GEBCO grid file (NetCDF) — fast, no network needed
  2. NOAA NCEI Global Relief Model via ArcGIS REST API — lightweight fallback
"""

from __future__ import annotations

import logging
import os

import numpy as np
import xarray as xr

logger = logging.getLogger(__name__)

GEBCO_GRID_PATH_ENV = "GEBCO_GRID_PATH"

_gebco_ds = None


def get_bathymetry(lat: float, lon: float) -> float | None:
    """
    Get ocean depth (positive meters) at the given coordinates.

    Tries local GEBCO grid first, falls back to web API.
    Returns None if neither source is available.

    Parameters
    ----------
    lat : float
        Latitude in decimal degrees.
    lon : float
        Longitude in decimal degrees.

    Returns
    -------
    float or None
        Positive depth in meters, 0.0 for land, or None if unavailable.
    """
    depth = _query_local_gebco(lat, lon)
    if depth is not None:
        return depth

    depth = _query_web_api(lat, lon)
    return depth


def _find_gebco_path() -> str | None:
    """Search for GEBCO grid via env var or default locations."""
    env_path = os.environ.get(GEBCO_GRID_PATH_ENV)
    if env_path and os.path.isfile(env_path):
        return env_path

    default_paths = [
        os.path.expanduser("~/data/gebco/GEBCO_2024.nc"),
        "/data/gebco/GEBCO_2024.nc",
    ]
    for path in default_paths:
        if os.path.isfile(path):
            return path

    return None


def _query_local_gebco(lat: float, lon: float) -> float | None:
    """Query a local GEBCO NetCDF grid for bathymetric depth."""
    global _gebco_ds

    if _gebco_ds is None:
        gebco_path = _find_gebco_path()
        if gebco_path is None:
            logger.debug("No local GEBCO grid file found")
            return None

        try:
            _gebco_ds = xr.open_dataset(gebco_path)
            logger.info(f"Opened GEBCO grid: {gebco_path}")
        except Exception as e:
            logger.warning(f"Failed to open GEBCO grid at {gebco_path}: {e}")
            return None

    try:
        # GEBCO elevation: negative = ocean depth, positive = land
        elevation = float(
            _gebco_ds["elevation"]
            .sel(lat=lat, lon=lon, method="nearest")
            .values
        )

        if elevation < 0:
            return abs(elevation)  # positive depth in meters
        else:
            return 0.0  # land or coastline
    except Exception as e:
        logger.warning(f"Failed to query GEBCO grid at ({lat}, {lon}): {e}")
        return None


def _query_web_api(lat: float, lon: float) -> float | None:
    """Query NOAA NCEI Global Relief Model for bathymetric depth."""
    try:
        import requests
    except ImportError:
        logger.debug("requests not available for web API fallback")
        return None

    url = (
        "https://gis.ngdc.noaa.gov/arcgis/rest/services/"
        "DEM_mosaics/DEM_global_mosaic/ImageServer/identify"
    )
    params = {
        "geometry": f'{{"x":{lon},"y":{lat}}}',
        "geometryType": "esriGeometryPoint",
        "returnGeometry": "false",
        "returnCatalogItems": "false",
        "f": "json",
    }

    try:
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()

        value_str = data.get("value")
        if value_str is None or value_str == "NoData":
            logger.warning(f"Web API returned no data for ({lat}, {lon})")
            return None

        elevation = float(value_str)
        if elevation < 0:
            return abs(elevation)
        else:
            return 0.0
    except Exception as e:
        logger.warning(f"Web API bathymetry query failed for ({lat}, {lon}): {e}")
        return None


def estimate_seabed_depth(sv_dataset: xr.Dataset) -> float | None:
    """
    Estimate seabed depth from bathymetric data using the dataset's
    median lat/lon coordinates.

    Parameters
    ----------
    sv_dataset : xr.Dataset
        Dataset with latitude/longitude coordinates.

    Returns
    -------
    float or None
        Positive depth in meters, or None if unavailable.
    """
    if "latitude" not in sv_dataset.coords and "latitude" not in sv_dataset.data_vars:
        logger.debug("No latitude on dataset — skipping bathymetric lookup")
        return None
    if "longitude" not in sv_dataset.coords and "longitude" not in sv_dataset.data_vars:
        logger.debug("No longitude on dataset — skipping bathymetric lookup")
        return None

    lat_data = sv_dataset["latitude"]
    lon_data = sv_dataset["longitude"]

    lat = float(np.nanmedian(lat_data.values))
    lon = float(np.nanmedian(lon_data.values))

    if np.isnan(lat) or np.isnan(lon):
        logger.warning("Median lat/lon is NaN — cannot query bathymetry")
        return None

    logger.info(f"Querying bathymetry at median position ({lat:.4f}, {lon:.4f})")
    depth = get_bathymetry(lat, lon)

    if depth is not None:
        logger.info(f"Estimated seabed depth: {depth:.0f} m")

    return depth
