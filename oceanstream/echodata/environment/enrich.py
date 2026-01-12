"""Environmental data enrichment from geoparquet.

Loads CTD measurements from processed geoparquet data and interpolates
them to EchoData ping times for accurate acoustic processing.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

import numpy as np

if TYPE_CHECKING:
    import pandas as pd
    import xarray as xr
    from echopype.echodata import EchoData

logger = logging.getLogger(__name__)

# Available Saildrone measurements for acoustic enrichment
CTD_COLUMNS = {
    "TEMP_SBE37_MEAN": "temperature",      # Water temperature at ~0.6m (°C)
    "SAL_SBE37_MEAN": "salinity",          # Salinity (PSU)
    "COND_SBE37_MEAN": "conductivity",     # Conductivity (S/m)
}

# Core location columns
LOCATION_COLUMNS = ["time", "latitude", "longitude"]


def _resolve_campaign_dir(campaign_id: str) -> Path:
    """
    Resolve a campaign ID to its directory path.
    
    Looks up the campaign in ~/.oceanstream/campaigns/{campaign_id}/ and
    returns the path to its geoparquet data directory.
    
    Args:
        campaign_id: Campaign identifier (e.g., "TPOS2023", "FK161229")
        
    Returns:
        Path to campaign geoparquet data directory
        
    Raises:
        ValueError: If campaign not found or has no geoparquet data
    """
    from oceanstream.geotrack.campaign import get_campaigns_dir, load_campaign_metadata
    
    campaigns_dir = get_campaigns_dir()
    campaign_dir = campaigns_dir / campaign_id
    
    if not campaign_dir.exists():
        raise ValueError(
            f"Campaign '{campaign_id}' not found in {campaigns_dir}. "
            f"Create it with: oceanstream campaign create {campaign_id}"
        )
    
    # Load campaign metadata to find the output directory
    metadata = load_campaign_metadata(campaign_id)
    
    if metadata and "output_dir" in metadata:
        # Use the output directory from metadata
        output_dir = Path(metadata["output_dir"])
        if output_dir.exists():
            logger.info(f"Using output directory from campaign metadata: {output_dir}")
            return output_dir
    
    # Check if geoparquet data exists in the campaign directory itself
    parquet_files = list(campaign_dir.glob("**/*.parquet"))
    if parquet_files:
        logger.info(f"Found {len(parquet_files)} parquet files in campaign directory")
        return campaign_dir
    
    raise ValueError(
        f"Campaign '{campaign_id}' exists but has no geoparquet data. "
        f"Run: oceanstream process geotrack convert --campaign-id {campaign_id} --input-source <data_path>"
    )


def enrich_environment(
    echodata_path: Path,
    campaign_dir: Path,
    output_path: Optional[Path] = None,
    use_copernicus_fallback: bool = True,
    copernicus_days_back: int = 10,
) -> Path:
    """
    Enrich EchoData with environmental parameters from geoparquet.
    
    Loads CTD data from the processed geoparquet campaign directory,
    interpolates to ping times, and updates the EchoData Environment group.
    
    Falls back to Copernicus Marine Service data if CTD is unavailable.
    
    Args:
        echodata_path: Path to EchoData Zarr store
        campaign_dir: Path to geoparquet campaign directory
        output_path: Output path (default: overwrites input)
        use_copernicus_fallback: Use Copernicus if CTD unavailable
        copernicus_days_back: Days to average for Copernicus
        
    Returns:
        Path to enriched EchoData Zarr
    """
    try:
        from echopype.echodata import EchoData
    except ImportError as e:
        raise ImportError("echopype required for environment enrichment") from e
    
    echodata_path = Path(echodata_path)
    output_path = Path(output_path) if output_path else echodata_path
    
    # Load EchoData
    echodata = EchoData.from_zarr(echodata_path)
    
    # Get ping times
    ping_time = echodata["Sonar/Beam_group1"].ping_time.values
    
    # Try to get environment from geoparquet
    env_result = get_environment_with_fallback(
        campaign_dir=campaign_dir,
        ping_times=ping_time,
        use_copernicus_fallback=use_copernicus_fallback,
        copernicus_days_back=copernicus_days_back,
    )
    
    # Update EchoData with environment
    echodata = update_echodata_environment(echodata, env_result)
    
    # Update platform with interpolated GPS
    if "latitude" in env_result and "longitude" in env_result:
        echodata = update_echodata_platform(
            echodata,
            env_result["latitude"],
            env_result["longitude"],
        )
    
    # Save
    echodata.to_zarr(output_path, overwrite=True)
    
    logger.info(f"Enriched environment saved to {output_path}")
    return output_path


def get_environment_with_fallback(
    campaign_dir: Path,
    ping_times: np.ndarray,
    use_copernicus_fallback: bool = True,
    copernicus_days_back: int = 10,
    ctd_columns: Optional[list[str]] = None,
) -> dict[str, Any]:
    """
    Get environmental parameters, using CTD when available, Copernicus as fallback.
    
    Args:
        campaign_dir: Path to geoparquet campaign directory
        ping_times: Array of ping timestamps (datetime64)
        use_copernicus_fallback: Use Copernicus if CTD unavailable
        copernicus_days_back: Days to average for Copernicus
        ctd_columns: CTD column names to check (default: TEMP/SAL_SBE37_MEAN)
        
    Returns:
        Dict with temperature, salinity, sound_speed, absorption, source info
    """
    from oceanstream.echodata.environment.sound_speed import (
        chen_millero_sound_speed,
        mackenzie_sound_speed,
    )
    from oceanstream.echodata.environment.absorption import francois_garrison_absorption
    
    ctd_columns = ctd_columns or list(CTD_COLUMNS.keys())
    campaign_dir = Path(campaign_dir)
    
    # Try to load geoparquet CTD data
    env_df = load_geoparquet_environment(campaign_dir)
    
    if env_df is not None:
        # Check if we have CTD data
        has_ctd = all(
            col in env_df.columns and env_df[col].notna().any()
            for col in ctd_columns[:2]  # temperature and salinity
        )
        
        if has_ctd:
            logger.info("Using in-situ CTD data from geoparquet")
            
            # Interpolate to ping times
            env_interp = interpolate_environment_to_ping_time(env_df, ping_times)
            
            # Get interpolated values
            temperature = env_interp.get("TEMP_SBE37_MEAN", env_interp.get("temperature"))
            salinity = env_interp.get("SAL_SBE37_MEAN", env_interp.get("salinity"))
            
            # Estimate depth (Saildrone: ~0.6m hull depth)
            depth = np.full_like(temperature, 0.6)
            
            # Compute sound speed using Chen-Millero (best for in-situ CTD)
            sound_speed = chen_millero_sound_speed(temperature, salinity, depth)
            
            return {
                "source": "in_situ_ctd",
                "method": "chen_millero",
                "temperature": temperature,
                "salinity": salinity,
                "depth": depth,
                "sound_speed": sound_speed,
                "latitude": env_interp.get("latitude"),
                "longitude": env_interp.get("longitude"),
            }
    
    # Fallback to Copernicus Marine Service
    if use_copernicus_fallback:
        logger.info("CTD unavailable, falling back to Copernicus Marine Service")
        return _get_copernicus_environment(
            campaign_dir, ping_times, copernicus_days_back
        )
    
    logger.warning("No environmental data available")
    return {"source": "none"}


def _get_copernicus_environment(
    campaign_dir: Path,
    ping_times: np.ndarray,
    days_back: int,
) -> dict[str, Any]:
    """Get environment from Copernicus Marine Service."""
    from oceanstream.echodata.environment.copernicus import (
        fetch_copernicus_environment,
        compute_sound_speed_from_copernicus,
    )
    from oceanstream.echodata.environment.sound_speed import mackenzie_sound_speed
    
    # Load geoparquet to get location bounds
    env_df = load_geoparquet_environment(campaign_dir)
    
    if env_df is None or "latitude" not in env_df.columns:
        logger.warning("Cannot determine location for Copernicus query")
        return {"source": "none"}
    
    # Get center location
    lat = env_df["latitude"].mean()
    lon = env_df["longitude"].mean()
    
    # Get reference time from ping_times
    time_point = ping_times[len(ping_times) // 2]
    if hasattr(time_point, 'astype'):
        time_point = time_point.astype('datetime64[s]').astype(datetime)
    
    try:
        # Fetch Copernicus data
        env_ds = fetch_copernicus_environment(
            longitude=float(lon),
            latitude=float(lat),
            time_point=time_point,
            days_back=days_back,
        )
        
        # Get sound speed
        sound_speed, temp = compute_sound_speed_from_copernicus(env_ds, float(lat))
        
        # Interpolate location to ping times
        env_interp = interpolate_environment_to_ping_time(env_df, ping_times)
        
        # Use constant values from Copernicus (model data is typically averaged)
        n_pings = len(ping_times)
        
        return {
            "source": "copernicus_marine",
            "method": "mackenzie",
            "temperature": np.full(n_pings, temp),
            "salinity": np.full(n_pings, float(env_ds.sel(depth=5.0, method="nearest")["so"].values)),
            "depth": np.full(n_pings, 5.0),
            "sound_speed": np.full(n_pings, sound_speed),
            "latitude": env_interp.get("latitude"),
            "longitude": env_interp.get("longitude"),
        }
        
    except Exception as e:
        logger.error(f"Failed to fetch Copernicus data: {e}")
        return {"source": "none"}


def load_geoparquet_environment(campaign_dir: Path) -> Optional["pd.DataFrame"]:
    """
    Load environmental variables from geoparquet partitions.
    
    Args:
        campaign_dir: Path to campaign geoparquet directory
        
    Returns:
        DataFrame with time, lat, lon, and CTD columns, or None if not found
    """
    try:
        import geopandas as gpd
    except ImportError:
        logger.warning("geopandas required for loading geoparquet")
        return None
    
    campaign_dir = Path(campaign_dir)
    
    # Look for geoparquet files
    parquet_files = list(campaign_dir.glob("**/*.parquet"))
    
    if not parquet_files:
        logger.warning(f"No parquet files found in {campaign_dir}")
        return None
    
    try:
        gdf = gpd.read_parquet(campaign_dir)
    except Exception as e:
        logger.warning(f"Failed to read geoparquet: {e}")
        return None
    
    # Determine available columns
    available_cols = LOCATION_COLUMNS.copy()
    
    for col in CTD_COLUMNS:
        if col in gdf.columns:
            available_cols.append(col)
    
    # Filter to available columns
    gdf = gdf[[c for c in available_cols if c in gdf.columns]]
    
    return gdf.sort_values("time")


def interpolate_environment_to_ping_time(
    env_df: "pd.DataFrame",
    ping_times: np.ndarray,
) -> dict[str, np.ndarray]:
    """
    Interpolate all environmental variables to ping_time.
    
    Args:
        env_df: DataFrame with time and environment columns
        ping_times: Array of ping timestamps (datetime64 or DatetimeIndex)
        
    Returns:
        Dict mapping column names to interpolated arrays
    """
    import pandas as pd
    
    env_times = env_df["time"].values.astype("datetime64[ns]")
    
    # Handle both numpy arrays and pandas DatetimeIndex
    if hasattr(ping_times, 'values'):
        ping_times_ns = ping_times.values.astype("datetime64[ns]")
    else:
        ping_times_ns = np.asarray(ping_times).astype("datetime64[ns]")
    
    # Convert to float for interpolation (nanoseconds since epoch)
    env_times_float = env_times.astype("int64").astype(float)
    ping_times_float = ping_times_ns.astype("int64").astype(float)
    
    result = {}
    for col in env_df.columns:
        if col == "time":
            continue
        
        values = env_df[col].values
        
        # Handle missing values
        valid_mask = ~np.isnan(values) if np.issubdtype(values.dtype, np.floating) else np.ones(len(values), dtype=bool)
        
        if valid_mask.sum() < 2:
            logger.warning(f"Insufficient valid values for {col}, skipping")
            continue
        
        result[col] = np.interp(
            ping_times_float,
            env_times_float[valid_mask],
            values[valid_mask],
        )
    
    return result


def update_echodata_environment(
    echodata: "EchoData",
    env_result: dict[str, Any],
) -> "EchoData":
    """
    Update EchoData.Environment with measured/computed parameters.
    
    NOTE: This requires modifications to the echopype fork to use these
    values instead of built-in approximations during compute_Sv().
    
    Args:
        echodata: EchoData object to update
        env_result: Dict from get_environment_with_fallback
        
    Returns:
        Updated EchoData object
    """
    import xarray as xr
    
    if env_result.get("source") == "none":
        return echodata
    
    env = echodata["Environment"]
    source = env_result["source"]
    method = env_result.get("method", "unknown")
    
    if "temperature" in env_result and env_result["temperature"] is not None:
        env["temperature"] = xr.DataArray(
            env_result["temperature"],
            dims=["time1"],
            attrs={
                "units": "degrees_C",
                "long_name": "Sea water temperature",
                "source": f"{source} via oceanstream",
            },
        )
    
    if "salinity" in env_result and env_result["salinity"] is not None:
        env["salinity"] = xr.DataArray(
            env_result["salinity"],
            dims=["time1"],
            attrs={
                "units": "PSU",
                "long_name": "Sea water practical salinity",
                "source": f"{source} via oceanstream",
            },
        )
    
    if "sound_speed" in env_result and env_result["sound_speed"] is not None:
        env["sound_speed"] = xr.DataArray(
            env_result["sound_speed"],
            dims=["time1"],
            attrs={
                "units": "m/s",
                "long_name": "Sound speed in sea water",
                "source": f"Computed from {source} via {method} equation",
            },
        )
    
    logger.info(f"Updated EchoData Environment from {source}")
    return echodata


def update_echodata_platform(
    echodata: "EchoData",
    latitude: np.ndarray,
    longitude: np.ndarray,
) -> "EchoData":
    """
    Update EchoData.Platform with interpolated GPS coordinates.
    
    Args:
        echodata: EchoData object to update
        latitude: Interpolated latitude array
        longitude: Interpolated longitude array
        
    Returns:
        Updated EchoData object
    """
    import xarray as xr
    
    platform = echodata["Platform"]
    
    platform["latitude"] = xr.DataArray(
        latitude,
        dims=["time1"],
        attrs={"units": "degrees_north", "source": "Interpolated from geoparquet"},
    )
    
    platform["longitude"] = xr.DataArray(
        longitude,
        dims=["time1"],
        attrs={"units": "degrees_east", "source": "Interpolated from geoparquet"},
    )
    
    logger.info("Updated EchoData Platform with interpolated GPS")
    return echodata


# Aliases for test compatibility
def load_ctd_from_geoparquet(campaign_dir: Path) -> "pd.DataFrame":
    """
    Load CTD data from geoparquet campaign directory.
    
    Alias for load_geoparquet_environment for test compatibility.
    
    Args:
        campaign_dir: Path to geoparquet campaign directory
        
    Returns:
        DataFrame with CTD data
    """
    df = load_geoparquet_environment(campaign_dir)
    if df is None:
        raise FileNotFoundError(f"No geoparquet data found in {campaign_dir}")
    return df


def interpolate_ctd_to_pings(
    ctd_data: "pd.DataFrame",
    ping_times: np.ndarray,
) -> "pd.DataFrame":
    """
    Interpolate CTD data to ping times.
    
    Alias for interpolate_environment_to_ping_time for test compatibility.
    
    Args:
        ctd_data: DataFrame with time, temperature, salinity columns
        ping_times: Array of ping timestamps
        
    Returns:
        DataFrame with interpolated values at ping times
    """
    return interpolate_environment_to_ping_time(ctd_data, ping_times)


def enrich_sv_with_location(
    sv_dataset: "xr.Dataset",
    campaign_dir: Optional[Path] = None,
    campaign_id: Optional[str] = None,
    time_var: str = "ping_time",
) -> "xr.Dataset":
    """
    Enrich Sv dataset with GPS coordinates from geoparquet.
    
    This function adds latitude and longitude coordinates to an Sv dataset
    by interpolating from the campaign's geoparquet trajectory data. This is
    required for NASC computation which needs location data for distance
    calculations.
    
    Similar to how we enrich EchoData with sound speed and absorption from
    geoparquet CTD data, this enriches the Sv dataset with GPS data.
    
    Args:
        sv_dataset: Sv xarray Dataset (output of compute_Sv)
        campaign_dir: Path to geoparquet campaign directory (mutually exclusive with campaign_id)
        campaign_id: Campaign ID to look up in ~/.oceanstream/campaigns/ (mutually exclusive with campaign_dir)
        time_var: Name of time variable (default: "ping_time")
        
    Returns:
        Sv dataset with latitude/longitude coordinates added
        
    Raises:
        ValueError: If no location data found in geoparquet or neither/both campaign_dir/campaign_id provided
        
    Example:
        # Using campaign directory path
        sv_enriched = enrich_sv_with_location(ds_Sv, campaign_dir=Path("./output/TPOS2023"))
        
        # Using campaign ID (looks up in ~/.oceanstream/campaigns/)
        sv_enriched = enrich_sv_with_location(ds_Sv, campaign_id="TPOS2023")
        
        nasc = ep.commongrid.compute_NASC(sv_enriched, ...)
    """
    import xarray as xr
    
    # Resolve campaign directory from campaign_id if provided
    if campaign_id and campaign_dir:
        raise ValueError("Provide either campaign_dir or campaign_id, not both")
    
    if campaign_id:
        campaign_dir = _resolve_campaign_dir(campaign_id)
    elif campaign_dir:
        campaign_dir = Path(campaign_dir)
    else:
        raise ValueError("Must provide either campaign_dir or campaign_id")
    
    # Check if already has location data
    if "latitude" in sv_dataset.coords and "longitude" in sv_dataset.coords:
        lat = sv_dataset.coords["latitude"]
        lon = sv_dataset.coords["longitude"]
        
        # Check if they have valid data (not all NaN)
        if not np.all(np.isnan(lat.values)) and not np.all(np.isnan(lon.values)):
            logger.info("Sv dataset already has valid location data")
            return sv_dataset
    
    # Load geoparquet trajectory data
    env_df = load_geoparquet_environment(campaign_dir)
    
    if env_df is None:
        raise ValueError(f"No geoparquet data found in {campaign_dir}")
    
    if "latitude" not in env_df.columns or "longitude" not in env_df.columns:
        raise ValueError(f"Geoparquet data missing latitude/longitude columns")
    
    # Get ping times from Sv dataset
    ping_times = sv_dataset[time_var].values
    
    logger.info(f"Interpolating GPS data for {len(ping_times)} pings from geoparquet")
    
    # Interpolate GPS to ping times
    env_interp = interpolate_environment_to_ping_time(env_df, ping_times)
    
    if "latitude" not in env_interp or "longitude" not in env_interp:
        raise ValueError("Failed to interpolate location data")
    
    # Add as coordinates along ping_time dimension
    sv_dataset = sv_dataset.assign_coords({
        "latitude": (time_var, env_interp["latitude"]),
        "longitude": (time_var, env_interp["longitude"]),
    })
    
    # Add attributes
    sv_dataset["latitude"].attrs = {
        "units": "degrees_north",
        "long_name": "Latitude",
        "source": "Interpolated from geoparquet trajectory",
    }
    sv_dataset["longitude"].attrs = {
        "units": "degrees_east", 
        "long_name": "Longitude",
        "source": "Interpolated from geoparquet trajectory",
    }
    
    logger.info(f"Added location coordinates: lat=[{env_interp['latitude'].min():.3f}, {env_interp['latitude'].max():.3f}], "
                f"lon=[{env_interp['longitude'].min():.3f}, {env_interp['longitude'].max():.3f}]")
    
    return sv_dataset


def enrich_sv_with_location_from_url(
    sv_dataset: "xr.Dataset",
    url: str,
    time_col: str = "time",
    lat_col: str = "latitude",
    lon_col: str = "longitude",
    time_var: str = "ping_time",
    storage_options: Optional[dict] = None,
) -> "xr.Dataset":
    """
    Enrich Sv dataset with GPS coordinates from cloud-native geoparquet.
    
    This function adds latitude and longitude coordinates to an Sv dataset
    by loading from a cloud-hosted GeoParquet URL (az://, s3://, gs://) and
    interpolating to ping times.
    
    Similar to enrich_sv_with_location but supports cloud URLs with custom
    column mappings.
    
    Args:
        sv_dataset: Sv xarray Dataset (output of compute_Sv)
        url: GeoParquet URL (az://, s3://, gs://, https://, or local path)
        time_col: Column name for timestamps in geoparquet (default: "time")
        lat_col: Column name for latitude in geoparquet (default: "latitude")
        lon_col: Column name for longitude in geoparquet (default: "longitude")
        time_var: Name of time variable in Sv dataset (default: "ping_time")
        storage_options: Optional fsspec storage options for cloud access
        
    Returns:
        Sv dataset with latitude/longitude coordinates added
        
    Raises:
        ValueError: If no location data found or columns missing
        
    Example:
        # Load from Azure Blob Storage with custom column names
        sv_enriched = enrich_sv_with_location_from_url(
            ds_Sv,
            url="az://oceanstream/tpos_2023/",
            time_col="time",
            lat_col="ship_latitude",  # R2R column name
            lon_col="ship_longitude",
        )
        
        # Load from S3 with Saildrone defaults
        sv_enriched = enrich_sv_with_location_from_url(
            ds_Sv,
            url="s3://bucket/saildrone/campaign/",
        )
        
        nasc = ep.commongrid.compute_NASC(sv_enriched, ...)
    """
    import xarray as xr
    from oceanstream.echodata.environment.geoparquet import load_env_from_geoparquet, EnvVarMapping
    
    # Check if already has location data
    if "latitude" in sv_dataset.coords and "longitude" in sv_dataset.coords:
        lat = sv_dataset.coords["latitude"]
        lon = sv_dataset.coords["longitude"]
        
        # Check if they have valid data (not all NaN)
        if not np.all(np.isnan(lat.values)) and not np.all(np.isnan(lon.values)):
            logger.info("Sv dataset already has valid location data")
            return sv_dataset
    
    # Get ping times from Sv dataset
    ping_times = sv_dataset[time_var].values
    
    # Determine time range for efficient filtering
    ping_times_ns = np.asarray(ping_times).astype("datetime64[ns]")
    time_min = str(ping_times_ns.min())[:19]  # ISO format
    time_max = str(ping_times_ns.max())[:19]
    
    logger.info(f"Loading GPS data from {url} for time range {time_min} to {time_max}")
    
    # Create mapping with custom column names
    mapping = EnvVarMapping(
        time=time_col,
        latitude=lat_col,
        longitude=lon_col,
    )
    
    # Load from cloud geoparquet with time filtering
    try:
        env_data = load_env_from_geoparquet(
            url=url,
            mapping=mapping,
            time_range=(time_min, time_max),
            storage_options=storage_options,
            compute_derived=False,  # We only need location
        )
    except Exception as e:
        raise ValueError(f"Failed to load GPS data from {url}: {e}") from e
    
    logger.info(f"Loaded {env_data.n_records} GPS records from {url}")
    
    if env_data.n_records == 0:
        raise ValueError(f"No GPS data found at {url} for time range {time_min} to {time_max}")
    
    # Interpolate GPS to ping times
    ping_times_float = ping_times_ns.astype("int64").astype(float)
    env_times_float = env_data.time.astype("int64").astype(float)
    
    # Sort by time for interpolation
    sort_idx = np.argsort(env_times_float)
    env_times_sorted = env_times_float[sort_idx]
    lat_sorted = env_data.latitude[sort_idx]
    lon_sorted = env_data.longitude[sort_idx]
    
    interp_lat = np.interp(ping_times_float, env_times_sorted, lat_sorted)
    interp_lon = np.interp(ping_times_float, env_times_sorted, lon_sorted)
    
    # Add as coordinates along ping_time dimension
    sv_dataset = sv_dataset.assign_coords({
        "latitude": (time_var, interp_lat),
        "longitude": (time_var, interp_lon),
    })
    
    # Add attributes
    sv_dataset["latitude"].attrs = {
        "units": "degrees_north",
        "long_name": "Latitude",
        "source": f"Interpolated from {url}",
    }
    sv_dataset["longitude"].attrs = {
        "units": "degrees_east", 
        "long_name": "Longitude",
        "source": f"Interpolated from {url}",
    }
    
    logger.info(f"Added location coordinates: lat=[{interp_lat.min():.3f}, {interp_lat.max():.3f}], "
                f"lon=[{interp_lon.min():.3f}, {interp_lon.max():.3f}]")
    
    return sv_dataset


def enrich_echodata(
    echodata: "EchoData",
    temperature: float,
    salinity: float,
    pressure: float = 0.0,
    ph: float = 8.0,
) -> "EchoData":
    """
    Enrich EchoData with constant environmental values.
    
    Updates the EchoData Environment group with the provided values.
    
    Args:
        echodata: EchoData object to enrich
        temperature: Water temperature (°C)
        salinity: Salinity (PSU)
        pressure: Pressure (dbar) - converted to depth
        ph: pH (default 8.0)
        
    Returns:
        Updated EchoData object
    """
    import xarray as xr
    from oceanstream.echodata.environment.sound_speed import chen_millero_sound_speed
    
    # Estimate depth from pressure
    depth = pressure / 10.0  # Approximate
    
    # Compute sound speed
    sound_speed = chen_millero_sound_speed(
        temperature=np.asarray(temperature),
        salinity=np.asarray(salinity),
        depth=np.asarray(depth),
    )
    
    env_result = {
        "source": "user_provided",
        "method": "chen_millero",
        "temperature": np.asarray([temperature]),
        "salinity": np.asarray([salinity]),
        "depth": np.asarray([depth]),
        "sound_speed": np.asarray([sound_speed]),
    }
    
    return update_echodata_environment(echodata, env_result)
