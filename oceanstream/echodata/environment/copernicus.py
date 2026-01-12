"""Copernicus Marine Service environmental data fallback.

Provides temperature and salinity profiles from CMEMS global ocean models
when in-situ CTD data is unavailable in the geoparquet.

Reference: OceanStreamIO/sound-speed-profile notebook
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Optional, Tuple

if TYPE_CHECKING:
    import xarray as xr

logger = logging.getLogger(__name__)

# Copernicus dataset IDs
CMEMS_SALINITY_DATASET = "cmems_mod_glo_phy-so_anfc_0.083deg_P1D-m"
CMEMS_TEMPERATURE_DATASET = "cmems_mod_glo_phy-thetao_anfc_0.083deg_P1D-m"
COPERNICUS_PRODUCT_ID = "GLOBAL_ANALYSISFORECAST_PHY_001_024"  # GLORYS12V1 product ID


def fetch_copernicus_environment(
    longitude: float,
    latitude: float,
    time_point: datetime,
    days_back: int = 10,
    region_padding_degrees: float = 0.0,
) -> "xr.Dataset":
    """
    Fetch temperature and salinity profiles from Copernicus Marine Service.
    
    Downloads global ocean model data (CMEMS) for locations where in-situ
    CTD data is unavailable. Data is averaged over the specified time window.
    
    Args:
        longitude: Target longitude (degrees East)
        latitude: Target latitude (degrees North)
        time_point: Reference time for data query
        days_back: Number of days to average (default 10)
        region_padding_degrees: Spatial averaging radius (default 0 = single point)
        
    Returns:
        xarray.Dataset with depth profiles of:
        - so: Sea water salinity (PSU)
        - thetao: Sea water potential temperature (°C)
        
    Raises:
        ImportError: If copernicusmarine package not installed
        RuntimeError: If data fetch fails
        
    Note:
        Requires Copernicus Marine Service credentials:
        - Set COPERNICUSMARINE_SERVICE_USERNAME and COPERNICUSMARINE_SERVICE_PASSWORD
        - Or run: copernicusmarine login
    """
    try:
        import copernicusmarine
    except ImportError as e:
        raise ImportError(
            "copernicusmarine package required for Copernicus data. "
            "Install with: pip install copernicusmarine"
        ) from e
    
    start_time = (time_point - timedelta(days=days_back)).strftime("%Y-%m-%d")
    end_time = time_point.strftime("%Y-%m-%d")
    
    logger.info(
        f"Fetching Copernicus data for ({latitude:.2f}, {longitude:.2f}) "
        f"from {start_time} to {end_time}"
    )
    
    try:
        # Download salinity profile
        salinity_ds = copernicusmarine.open_dataset(
            dataset_id=CMEMS_SALINITY_DATASET,
            minimum_longitude=longitude - region_padding_degrees,
            maximum_longitude=longitude + region_padding_degrees,
            minimum_latitude=latitude - region_padding_degrees,
            maximum_latitude=latitude + region_padding_degrees,
            start_datetime=start_time,
            end_datetime=end_time,
            variables=["so"],
        )
        
        # Download temperature profile
        temp_ds = copernicusmarine.open_dataset(
            dataset_id=CMEMS_TEMPERATURE_DATASET,
            minimum_longitude=longitude - region_padding_degrees,
            maximum_longitude=longitude + region_padding_degrees,
            minimum_latitude=latitude - region_padding_degrees,
            maximum_latitude=latitude + region_padding_degrees,
            start_datetime=start_time,
            end_datetime=end_time,
            variables=["thetao"],
        )
        
        # Merge datasets
        salinity_ds["thetao"] = temp_ds["thetao"]
        
        # Average over time
        result = salinity_ds.mean(dim=["time"])
        
        logger.info(f"Retrieved Copernicus profiles with {len(result.depth)} depth levels")
        return result
        
    except Exception as e:
        logger.error(f"Failed to fetch Copernicus data: {e}")
        raise RuntimeError(f"Copernicus Marine Service fetch failed: {e}") from e


def convert_potential_to_insitu_temperature(
    potential_temp: "xr.DataArray",
    salinity: "xr.DataArray",
    depth: "xr.DataArray",
    latitude: float,
) -> "xr.DataArray":
    """
    Convert potential temperature to in-situ temperature using GSW toolbox.
    
    Copernicus provides potential temperature (thetao), but acoustic equations
    require in-situ temperature. Uses TEOS-10 equations via gsw library.
    
    Args:
        potential_temp: Potential temperature (°C)
        salinity: Practical salinity (PSU)
        depth: Depth levels (m)
        latitude: Latitude for pressure calculation
        
    Returns:
        In-situ temperature (°C)
        
    Raises:
        ImportError: If gsw package not installed
    """
    try:
        import gsw
    except ImportError as e:
        raise ImportError(
            "gsw package required for temperature conversion. "
            "Install with: pip install gsw"
        ) from e
    
    # Convert depth to pressure
    pressure = gsw.p_from_z(-depth, latitude)
    
    # Convert potential temperature to conservative temperature
    CT = gsw.CT_from_pt(salinity, potential_temp)
    
    # Convert conservative temperature to in-situ temperature
    return gsw.t_from_CT(salinity, CT, pressure)


# Re-export from sound_speed module for backwards compatibility
from oceanstream.echodata.environment.sound_speed import compute_sound_speed_from_copernicus


def get_copernicus_profile(
    longitude: float,
    latitude: float,
    time_point: datetime,
    days_back: int = 10,
    max_depth: float = 500.0,
) -> dict:
    """
    Get full depth profile of temperature, salinity, and sound speed.
    
    Useful for understanding vertical structure when transducer
    is mounted below surface.
    
    Args:
        longitude: Target longitude
        latitude: Target latitude
        time_point: Reference time
        days_back: Days to average
        max_depth: Maximum depth to retrieve (m)
        
    Returns:
        Dict with depth profiles
    """
    from oceanstream.echodata.environment.sound_speed import mackenzie_sound_speed
    import numpy as np
    
    env_ds = fetch_copernicus_environment(
        longitude, latitude, time_point, days_back
    )
    
    # Filter to max depth
    env_ds = env_ds.sel(depth=env_ds.depth <= max_depth)
    
    depths = env_ds.depth.values
    temperatures = []
    salinities = env_ds["so"].values
    sound_speeds = []
    
    for i, depth in enumerate(depths):
        env_at_depth = env_ds.sel(depth=depth)
        potential_temp = float(env_at_depth["thetao"].values)
        salinity = float(env_at_depth["so"].values)
        
        # Convert potential to in-situ temperature
        try:
            import gsw
            pressure = gsw.p_from_z(-depth, latitude)
            CT = gsw.CT_from_pt(salinity, potential_temp)
            temp_insitu = float(gsw.t_from_CT(salinity, CT, pressure))
        except ImportError:
            temp_insitu = potential_temp
        
        temperatures.append(temp_insitu)
        sound_speeds.append(float(mackenzie_sound_speed(temp_insitu, salinity, depth)))
    
    return {
        "depth": depths.tolist(),
        "temperature": temperatures,
        "salinity": salinities.tolist(),
        "sound_speed": sound_speeds,
        "source": "copernicus_marine",
        "location": {"latitude": latitude, "longitude": longitude},
        "time_range": {
            "start": (time_point - timedelta(days=days_back)).isoformat(),
            "end": time_point.isoformat(),
        },
    }


def build_copernicus_request(
    lat_min: float,
    lat_max: float,
    lon_min: float,
    lon_max: float,
    time_min: datetime,
    time_max: datetime,
    variables: list[str] = None,
) -> dict:
    """
    Build a Copernicus Marine Service request dictionary.
    
    Args:
        lat_min, lat_max: Latitude bounds
        lon_min, lon_max: Longitude bounds
        time_min, time_max: Time bounds
        variables: Variables to request (default: temperature, salinity)
        
    Returns:
        Request dictionary for copernicusmarine.open_dataset
    """
    if variables is None:
        variables = ["thetao", "so"]
    
    return {
        "minimum_latitude": lat_min,
        "maximum_latitude": lat_max,
        "minimum_longitude": lon_min,
        "maximum_longitude": lon_max,
        "start_datetime": time_min.strftime("%Y-%m-%d"),
        "end_datetime": time_max.strftime("%Y-%m-%d"),
        "variables": variables,
    }


def fetch_copernicus_data(
    lat_min: float,
    lat_max: float,
    lon_min: float,
    lon_max: float,
    time_min: datetime,
    time_max: datetime,
) -> "xr.Dataset":
    """
    Fetch data from Copernicus Marine Service.
    
    Args:
        lat_min, lat_max: Latitude bounds
        lon_min, lon_max: Longitude bounds
        time_min, time_max: Time bounds
        
    Returns:
        xarray Dataset with temperature and salinity
    """
    try:
        import copernicusmarine
    except ImportError as e:
        raise ImportError(
            "copernicusmarine required. Install with: pip install copernicusmarine"
        ) from e
    
    request = build_copernicus_request(
        lat_min, lat_max, lon_min, lon_max, time_min, time_max
    )
    
    return copernicusmarine.open_dataset(
        dataset_id=CMEMS_SALINITY_DATASET,
        **request,
    )
