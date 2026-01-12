"""Cloud-native GeoParquet loading for environmental data.

This module provides functions to load environmental parameters (temperature,
salinity, etc.) from cloud-hosted GeoParquet datasets for acoustic calibration.

Supports cloud storage URLs:
- Azure Blob: az://container/path or abfs://container@account.dfs.core.windows.net/path
- AWS S3: s3://bucket/path
- Google Cloud: gs://bucket/path
- HTTP(S): https://example.com/path.parquet
- Local paths: /path/to/data or ./relative/path

Example:
    >>> from oceanstream.echodata.environment.geoparquet import (
    ...     load_env_from_geoparquet,
    ...     EnvVarMapping,
    ... )
    >>> mapping = EnvVarMapping(
    ...     time="time",
    ...     latitude="latitude",
    ...     longitude="longitude",
    ...     temperature="TEMP_SBE37_MEAN",
    ...     salinity="SAL_SBE37_MEAN",
    ... )
    >>> env_data = load_env_from_geoparquet(
    ...     "az://oceanstream/tpos_2023/",
    ...     mapping=mapping,
    ...     time_range=("2023-08-13T09:00:00", "2023-08-13T11:00:00"),
    ... )
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional, Union
from urllib.parse import urlparse

import numpy as np

if TYPE_CHECKING:
    import pandas as pd
    import xarray as xr

logger = logging.getLogger(__name__)


@dataclass
class EnvVarMapping:
    """Mapping of standard variable names to dataset column names.
    
    This allows flexible column naming across different data sources.
    
    Attributes:
        time: Column name for timestamps (required)
        latitude: Column name for latitude (required)
        longitude: Column name for longitude (required)
        temperature: Column name for water temperature (°C)
        salinity: Column name for salinity (PSU)
        conductivity: Column name for conductivity (S/m)
        pressure: Column name for pressure (dbar)
        depth: Column name for depth (m)
        platform_id: Column name for platform identifier
    """
    time: str = "time"
    latitude: str = "latitude"
    longitude: str = "longitude"
    temperature: Optional[str] = None
    salinity: Optional[str] = None
    conductivity: Optional[str] = None
    pressure: Optional[str] = None
    depth: Optional[str] = None
    platform_id: Optional[str] = None
    
    # Additional custom mappings
    extra: dict[str, str] = field(default_factory=dict)
    
    @classmethod
    def saildrone(cls) -> "EnvVarMapping":
        """Default mapping for Saildrone SBE37 CTD data."""
        return cls(
            time="time",
            latitude="latitude",
            longitude="longitude",
            temperature="TEMP_SBE37_MEAN",
            salinity="SAL_SBE37_MEAN",
            conductivity="COND_SBE37_MEAN",
            platform_id="platform_id",
        )
    
    @classmethod
    def r2r(cls) -> "EnvVarMapping":
        """Default mapping for R2R ship data."""
        return cls(
            time="time",
            latitude="latitude",
            longitude="longitude",
            temperature="temperature",
            salinity="salinity",
            depth="depth",
        )
    
    def get_columns(self) -> list[str]:
        """Get list of all mapped column names."""
        cols = [self.time, self.latitude, self.longitude]
        for attr in ["temperature", "salinity", "conductivity", "pressure", "depth", "platform_id"]:
            val = getattr(self, attr)
            if val:
                cols.append(val)
        cols.extend(self.extra.values())
        return cols


@dataclass
class EnvData:
    """Container for environmental data extracted from GeoParquet.
    
    Attributes:
        time: Timestamps (UTC)
        latitude: Latitudes (degrees, -90 to 90)
        longitude: Longitudes (degrees, -180 to 180)
        temperature: Water temperature (°C), if available
        salinity: Salinity (PSU), if available
        sound_speed: Computed sound speed (m/s), if temperature and salinity available
        absorption: Computed absorption coefficient (dB/m), if computed
        source_url: Original data source URL
        platform_ids: Unique platform identifiers in the data
        n_records: Number of data records
        time_range: (start, end) datetime tuple
        spatial_bounds: (min_lat, max_lat, min_lon, max_lon) tuple
    """
    time: np.ndarray
    latitude: np.ndarray
    longitude: np.ndarray
    temperature: Optional[np.ndarray] = None
    salinity: Optional[np.ndarray] = None
    conductivity: Optional[np.ndarray] = None
    pressure: Optional[np.ndarray] = None
    depth: Optional[np.ndarray] = None
    sound_speed: Optional[np.ndarray] = None
    absorption: Optional[np.ndarray] = None
    source_url: Optional[str] = None
    platform_ids: Optional[list[str]] = None
    
    @property
    def n_records(self) -> int:
        return len(self.time)
    
    @property
    def time_range(self) -> tuple[datetime, datetime]:
        return (
            np.datetime_as_string(self.time.min(), unit='s'),
            np.datetime_as_string(self.time.max(), unit='s'),
        )
    
    @property
    def spatial_bounds(self) -> tuple[float, float, float, float]:
        return (
            float(self.latitude.min()),
            float(self.latitude.max()),
            float(self.longitude.min()),
            float(self.longitude.max()),
        )
    
    @property
    def has_ctd(self) -> bool:
        """Check if temperature and salinity are available."""
        return (
            self.temperature is not None 
            and self.salinity is not None
            and len(self.temperature) > 0
            and not np.all(np.isnan(self.temperature))
        )
    
    def compute_sound_speed(
        self,
        method: str = "mackenzie",
        depth: Optional[float] = None,
    ) -> np.ndarray:
        """Compute sound speed from temperature and salinity.
        
        Args:
            method: 'mackenzie' or 'chen_millero'
            depth: Depth in meters (default: use self.depth or 0.6m)
            
        Returns:
            Sound speed array (m/s)
        """
        if not self.has_ctd:
            raise ValueError("Temperature and salinity required for sound speed calculation")
        
        from oceanstream.echodata.environment.sound_speed import (
            mackenzie_sound_speed,
            chen_millero_sound_speed,
        )
        
        # Determine depth
        if depth is not None:
            d = np.full_like(self.temperature, depth)
        elif self.depth is not None:
            d = self.depth
        else:
            d = np.full_like(self.temperature, 0.6)  # Saildrone hull depth
        
        if method == "mackenzie":
            self.sound_speed = mackenzie_sound_speed(self.temperature, self.salinity, d)
        elif method == "chen_millero":
            self.sound_speed = chen_millero_sound_speed(self.temperature, self.salinity, d)
        else:
            raise ValueError(f"Unknown method: {method}. Use 'mackenzie' or 'chen_millero'")
        
        return self.sound_speed
    
    def compute_absorption(
        self,
        frequency_hz: float,
        depth: Optional[float] = None,
        ph: float = 8.1,
    ) -> np.ndarray:
        """Compute absorption coefficient from temperature, salinity, and frequency.
        
        Args:
            frequency_hz: Acoustic frequency in Hz
            depth: Depth in meters (default: use self.depth or 0.6m)
            ph: pH value (default: 8.1 for open ocean)
            
        Returns:
            Absorption coefficient array (dB/m)
        """
        if not self.has_ctd:
            raise ValueError("Temperature and salinity required for absorption calculation")
        
        from oceanstream.echodata.environment.absorption import francois_garrison_absorption
        
        # Determine depth
        if depth is not None:
            d = np.full_like(self.temperature, depth)
        elif self.depth is not None:
            d = self.depth
        else:
            d = np.full_like(self.temperature, 0.6)
        
        self.absorption = francois_garrison_absorption(
            frequency_hz=frequency_hz,
            temperature=self.temperature,
            salinity=self.salinity,
            depth=d,
            pH=ph,
        )
        
        return self.absorption
    
    def to_dataframe(self) -> "pd.DataFrame":
        """Convert to pandas DataFrame."""
        import pandas as pd
        
        data = {
            "time": self.time,
            "latitude": self.latitude,
            "longitude": self.longitude,
        }
        
        if self.temperature is not None:
            data["temperature"] = self.temperature
        if self.salinity is not None:
            data["salinity"] = self.salinity
        if self.conductivity is not None:
            data["conductivity"] = self.conductivity
        if self.depth is not None:
            data["depth"] = self.depth
        if self.sound_speed is not None:
            data["sound_speed"] = self.sound_speed
        if self.absorption is not None:
            data["absorption"] = self.absorption
        
        return pd.DataFrame(data)


def _get_filesystem(url: str) -> tuple[Any, str]:
    """Get fsspec filesystem and path from URL.
    
    Args:
        url: Cloud URL or local path
        
    Returns:
        (filesystem, path) tuple
    """
    import fsspec
    
    parsed = urlparse(url)
    scheme = parsed.scheme
    
    # Handle local paths
    if not scheme or scheme == "file":
        fs = fsspec.filesystem("file")
        path = url if not scheme else parsed.path
        return fs, path
    
    # Azure Blob Storage
    if scheme in ("az", "abfs", "abfss"):
        storage_options = _get_azure_storage_options()
        fs = fsspec.filesystem(scheme, **storage_options)
        # For az:// scheme, path is container/blob
        if scheme == "az":
            path = f"{parsed.netloc}{parsed.path}"
        else:
            path = url
        return fs, path
    
    # AWS S3
    if scheme == "s3":
        storage_options = _get_s3_storage_options()
        fs = fsspec.filesystem("s3", **storage_options)
        path = f"{parsed.netloc}{parsed.path}"
        return fs, path
    
    # Google Cloud Storage
    if scheme == "gs":
        storage_options = _get_gcs_storage_options()
        fs = fsspec.filesystem("gcs", **storage_options)
        path = f"{parsed.netloc}{parsed.path}"
        return fs, path
    
    # HTTP(S)
    if scheme in ("http", "https"):
        fs = fsspec.filesystem("https" if scheme == "https" else "http")
        return fs, url
    
    raise ValueError(f"Unsupported URL scheme: {scheme}. Supported: az, s3, gs, http(s), file, or local path")


def _get_azure_storage_options() -> dict[str, Any]:
    """Get Azure storage options from environment."""
    import os
    
    options: dict[str, Any] = {}
    
    # Try connection string first
    conn_str = os.environ.get("AZURE_STORAGE_CONNECTION_STRING")
    if conn_str:
        options["connection_string"] = conn_str
        return options
    
    # Try account key
    account_name = os.environ.get("AZURE_STORAGE_ACCOUNT")
    account_key = os.environ.get("AZURE_STORAGE_KEY")
    if account_name and account_key:
        options["account_name"] = account_name
        options["account_key"] = account_key
        return options
    
    # Try SAS token
    sas_token = os.environ.get("AZURE_STORAGE_SAS_TOKEN")
    if account_name and sas_token:
        options["account_name"] = account_name
        options["sas_token"] = sas_token
        return options
    
    # Try DefaultAzureCredential (Azure CLI, managed identity, etc.)
    options["anon"] = False
    return options


def _get_s3_storage_options() -> dict[str, Any]:
    """Get S3 storage options from environment."""
    import os
    
    options: dict[str, Any] = {}
    
    # AWS credentials from environment
    if os.environ.get("AWS_ACCESS_KEY_ID"):
        options["key"] = os.environ["AWS_ACCESS_KEY_ID"]
        options["secret"] = os.environ.get("AWS_SECRET_ACCESS_KEY", "")
    
    # Session token for temporary credentials
    if os.environ.get("AWS_SESSION_TOKEN"):
        options["token"] = os.environ["AWS_SESSION_TOKEN"]
    
    # Region
    if os.environ.get("AWS_DEFAULT_REGION"):
        options["client_kwargs"] = {"region_name": os.environ["AWS_DEFAULT_REGION"]}
    
    return options


def _get_gcs_storage_options() -> dict[str, Any]:
    """Get GCS storage options from environment."""
    import os
    
    options: dict[str, Any] = {}
    
    # Service account key file
    if os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"):
        options["token"] = os.environ["GOOGLE_APPLICATION_CREDENTIALS"]
    
    # Project ID
    if os.environ.get("GOOGLE_CLOUD_PROJECT"):
        options["project"] = os.environ["GOOGLE_CLOUD_PROJECT"]
    
    return options


def load_env_from_geoparquet(
    url: str,
    mapping: Optional[EnvVarMapping] = None,
    time_range: Optional[tuple[str, str]] = None,
    lat_range: Optional[tuple[float, float]] = None,
    lon_range: Optional[tuple[float, float]] = None,
    platform_filter: Optional[list[str]] = None,
    storage_options: Optional[dict[str, Any]] = None,
    compute_derived: bool = True,
    frequency_hz: float = 200_000.0,
) -> EnvData:
    """Load environmental data from cloud-hosted GeoParquet.
    
    Supports cloud storage URLs (az://, s3://, gs://) and local paths.
    
    Args:
        url: GeoParquet URL or local path. Can be a directory (for partitioned
            data) or a single file.
        mapping: Variable name mapping. Defaults to Saildrone SBE37 columns.
        time_range: Optional (start, end) ISO timestamps for filtering.
        lat_range: Optional (min, max) latitude range for filtering.
        lon_range: Optional (min, max) longitude range for filtering.
        platform_filter: Optional list of platform IDs to include.
        storage_options: Optional fsspec storage options (overrides auto-detection).
        compute_derived: If True, compute sound_speed and absorption.
        frequency_hz: Acoustic frequency for absorption calculation.
        
    Returns:
        EnvData container with loaded and optionally computed parameters.
        
    Raises:
        ValueError: If URL scheme is not supported or required columns missing.
        FileNotFoundError: If URL does not exist.
        
    Example:
        >>> env = load_env_from_geoparquet(
        ...     "az://oceanstream/tpos_2023/",
        ...     mapping=EnvVarMapping.saildrone(),
        ...     time_range=("2023-08-13T09:00:00", "2023-08-13T11:00:00"),
        ... )
        >>> print(f"Loaded {env.n_records} records, T={env.temperature.mean():.1f}°C")
    """
    import pandas as pd
    
    mapping = mapping or EnvVarMapping.saildrone()
    
    # Build filters for predicate pushdown
    filters = _build_parquet_filters(
        mapping=mapping,
        time_range=time_range,
        lat_range=lat_range,
        lon_range=lon_range,
        platform_filter=platform_filter,
    )
    
    # Load data
    df = _load_parquet_with_filters(
        url=url,
        columns=mapping.get_columns(),
        filters=filters,
        storage_options=storage_options,
    )
    
    if df.empty:
        raise ValueError(f"No data found at {url} matching filters")
    
    logger.info(f"Loaded {len(df)} records from {url}")
    
    # Extract arrays
    time_arr = pd.to_datetime(df[mapping.time]).values.astype("datetime64[ns]")
    lat_arr = df[mapping.latitude].values.astype(np.float64)
    lon_arr = df[mapping.longitude].values.astype(np.float64)
    
    # Extract optional columns
    def get_array(col: Optional[str]) -> Optional[np.ndarray]:
        if col and col in df.columns:
            arr = pd.to_numeric(df[col], errors="coerce").values.astype(np.float64)
            return arr
        return None
    
    temp_arr = get_array(mapping.temperature)
    sal_arr = get_array(mapping.salinity)
    cond_arr = get_array(mapping.conductivity)
    depth_arr = get_array(mapping.depth)
    pressure_arr = get_array(mapping.pressure)
    
    # Get platform IDs
    platform_ids = None
    if mapping.platform_id and mapping.platform_id in df.columns:
        platform_ids = df[mapping.platform_id].unique().tolist()
    
    # Create EnvData
    env_data = EnvData(
        time=time_arr,
        latitude=lat_arr,
        longitude=lon_arr,
        temperature=temp_arr,
        salinity=sal_arr,
        conductivity=cond_arr,
        depth=depth_arr,
        pressure=pressure_arr,
        source_url=url,
        platform_ids=platform_ids,
    )
    
    # Compute derived parameters
    if compute_derived and env_data.has_ctd:
        logger.info("Computing derived parameters (sound_speed, absorption)")
        env_data.compute_sound_speed(method="mackenzie")
        env_data.compute_absorption(frequency_hz=frequency_hz)
    
    return env_data


def _build_parquet_filters(
    mapping: EnvVarMapping,
    time_range: Optional[tuple[str, str]] = None,
    lat_range: Optional[tuple[float, float]] = None,
    lon_range: Optional[tuple[float, float]] = None,
    platform_filter: Optional[list[str]] = None,
) -> Optional[list[tuple]]:
    """Build pyarrow filter expressions for predicate pushdown."""
    import pandas as pd
    
    filters = []
    
    if time_range:
        start, end = time_range
        # Convert to pandas Timestamp with UTC for proper comparison
        start_ts = pd.Timestamp(start, tz="UTC")
        end_ts = pd.Timestamp(end, tz="UTC")
        filters.append((mapping.time, ">=", start_ts))
        filters.append((mapping.time, "<=", end_ts))
    
    if lat_range:
        min_lat, max_lat = lat_range
        filters.append((mapping.latitude, ">=", min_lat))
        filters.append((mapping.latitude, "<=", max_lat))
    
    if lon_range:
        min_lon, max_lon = lon_range
        filters.append((mapping.longitude, ">=", min_lon))
        filters.append((mapping.longitude, "<=", max_lon))
    
    if platform_filter and mapping.platform_id:
        filters.append((mapping.platform_id, "in", platform_filter))
    
    return filters if filters else None


def _load_parquet_with_filters(
    url: str,
    columns: list[str],
    filters: Optional[list[tuple]],
    storage_options: Optional[dict[str, Any]] = None,
) -> "pd.DataFrame":
    """Load parquet data with column selection and filter pushdown."""
    import pandas as pd
    
    parsed = urlparse(url)
    scheme = parsed.scheme
    
    # Use provided storage_options or auto-detect
    if storage_options is None:
        if scheme in ("az", "abfs", "abfss"):
            storage_options = _get_azure_storage_options()
        elif scheme == "s3":
            storage_options = _get_s3_storage_options()
        elif scheme == "gs":
            storage_options = _get_gcs_storage_options()
        else:
            storage_options = {}
    
    # Try to load - handle both directory and single file
    try:
        # Use pyarrow for better filter pushdown
        import pyarrow.parquet as pq
        import pyarrow.dataset as ds
        import fsspec
        
        # Get filesystem
        if scheme in ("az", "abfs", "abfss", "s3", "gs"):
            fs, path = _get_filesystem(url)
            
            # Create dataset for partitioned data
            dataset = ds.dataset(
                path,
                filesystem=fs,
                format="parquet",
            )
            
            # Apply filters and select columns
            available_cols = [c for c in columns if c in dataset.schema.names]
            
            if filters:
                # Convert to pyarrow expressions
                import pyarrow.compute as pc
                filter_expr = None
                for col, op, val in filters:
                    if col not in dataset.schema.names:
                        continue
                    
                    field = ds.field(col)
                    if op == ">=":
                        expr = field >= val
                    elif op == "<=":
                        expr = field <= val
                    elif op == "in":
                        expr = field.isin(val)
                    else:
                        continue
                    
                    filter_expr = expr if filter_expr is None else filter_expr & expr
                
                table = dataset.to_table(columns=available_cols, filter=filter_expr)
            else:
                table = dataset.to_table(columns=available_cols)
            
            return table.to_pandas()
        
        else:
            # Local path - use pyarrow dataset to handle partitioned data
            # and filter out non-parquet files
            from pathlib import Path
            
            path = Path(url)
            if path.is_dir():
                # Find all parquet files in directory (handles Hive partitioning)
                parquet_files = list(path.glob("**/*.parquet"))
                if not parquet_files:
                    raise FileNotFoundError(f"No parquet files found in {url}")
                
                # Use pyarrow dataset for proper partitioned reading
                dataset = ds.dataset(
                    str(path),
                    format="parquet",
                    partitioning="hive",
                    exclude_invalid_files=True,
                )
                
                available_cols = [c for c in columns if c in dataset.schema.names]
                
                if filters:
                    filter_expr = None
                    for col, op, val in filters:
                        if col not in dataset.schema.names:
                            continue
                        
                        field = ds.field(col)
                        if op == ">=":
                            expr = field >= val
                        elif op == "<=":
                            expr = field <= val
                        elif op == "in":
                            expr = field.isin(val)
                        else:
                            continue
                        
                        filter_expr = expr if filter_expr is None else filter_expr & expr
                    
                    table = dataset.to_table(columns=available_cols, filter=filter_expr)
                else:
                    table = dataset.to_table(columns=available_cols)
                
                return table.to_pandas()
            else:
                # Single file
                df = pd.read_parquet(
                    url,
                    columns=[c for c in columns if c],
                    filters=filters,
                )
                return df
            
    except Exception as e:
        logger.error(f"Failed to load parquet from {url}: {e}")
        raise


def interpolate_env_to_ping_times(
    env_data: EnvData,
    ping_times: np.ndarray,
) -> EnvData:
    """Interpolate environmental data to acoustic ping times.
    
    Args:
        env_data: Source environmental data
        ping_times: Target ping timestamps (datetime64)
        
    Returns:
        New EnvData with values interpolated to ping_times
    """
    import pandas as pd
    
    # Ensure consistent timezone handling
    env_times = env_data.time.astype("datetime64[ns]")
    
    if hasattr(ping_times, 'values'):
        ping_times_ns = ping_times.values.astype("datetime64[ns]")
    else:
        ping_times_ns = np.asarray(ping_times).astype("datetime64[ns]")
    
    # Convert to float for interpolation
    env_times_float = env_times.astype("int64").astype(float)
    ping_times_float = ping_times_ns.astype("int64").astype(float)
    
    def interp_array(arr: Optional[np.ndarray]) -> Optional[np.ndarray]:
        if arr is None:
            return None
        valid = ~np.isnan(arr)
        if valid.sum() < 2:
            return None
        return np.interp(ping_times_float, env_times_float[valid], arr[valid])
    
    return EnvData(
        time=ping_times_ns,
        latitude=np.interp(ping_times_float, env_times_float, env_data.latitude),
        longitude=np.interp(ping_times_float, env_times_float, env_data.longitude),
        temperature=interp_array(env_data.temperature),
        salinity=interp_array(env_data.salinity),
        conductivity=interp_array(env_data.conductivity),
        depth=interp_array(env_data.depth),
        pressure=interp_array(env_data.pressure),
        sound_speed=interp_array(env_data.sound_speed),
        absorption=interp_array(env_data.absorption),
        source_url=env_data.source_url,
        platform_ids=env_data.platform_ids,
    )


def get_env_params_for_calibration(
    env_data: EnvData,
    ping_times: np.ndarray,
    channel_ids: list[str],
    target_depth_m: Optional[float] = None,
    copernicus_profile: Optional[dict] = None,
    frequencies_hz: Optional[list[float]] = None,
) -> tuple["xr.DataArray", "xr.DataArray"]:
    """Build echopype-compatible env_params from EnvData.
    
    Creates sound_speed and sound_absorption DataArrays with proper
    dimensions for echopype's compute_Sv() function.
    
    Args:
        env_data: Environmental data (must have temperature and salinity)
        ping_times: Ping timestamps to interpolate to
        channel_ids: List of channel/frequency identifiers
        target_depth_m: If provided with copernicus_profile, use depth-weighted
            parameters for deep target calibration
        copernicus_profile: Dict with 'depth', 'temperature', 'salinity' arrays
            for depth-weighted calculation
        frequencies_hz: Acoustic frequencies for each channel (for absorption)
        
    Returns:
        (sound_speed_da, absorption_da) tuple of DataArrays
        
    Example:
        >>> env = load_env_from_geoparquet("az://data/campaign/")
        >>> ss, alpha = get_env_params_for_calibration(
        ...     env, ping_times, ["WBT 400040-15 ES200-7C"],
        ...     target_depth_m=100.0,
        ...     copernicus_profile=profile,
        ... )
        >>> ds_Sv = ep.calibrate.compute_Sv(
        ...     echodata, env_params={"sound_speed": ss, "sound_absorption": alpha}
        ... )
    """
    import xarray as xr
    
    # Interpolate to ping times
    env_interp = interpolate_env_to_ping_times(env_data, ping_times)
    
    if not env_interp.has_ctd:
        raise ValueError("Temperature and salinity required for calibration parameters")
    
    # Use depth-weighted if target depth and profile provided
    if target_depth_m is not None and copernicus_profile is not None:
        from oceanstream.echodata.environment.blended import (
            get_blended_env_params_for_calibration,
        )
        
        return get_blended_env_params_for_calibration(
            insitu_temp=env_interp.temperature,
            insitu_sal=env_interp.salinity,
            copernicus_profile=copernicus_profile,
            ping_times=ping_times,
            channel_ids=channel_ids,
            target_depth_m=target_depth_m,
            frequencies_hz=frequencies_hz,
        )
    
    # Surface-only parameters
    if env_interp.sound_speed is None:
        env_interp.compute_sound_speed(method="mackenzie")
    
    # Build 2D arrays [time1, channel]
    n_times = len(ping_times)
    n_channels = len(channel_ids)
    
    ss_2d = np.tile(env_interp.sound_speed.reshape(-1, 1), (1, n_channels))
    
    # Compute absorption per channel if frequencies provided
    if frequencies_hz:
        alpha_2d = np.zeros((n_times, n_channels))
        for i, freq in enumerate(frequencies_hz):
            env_interp.compute_absorption(frequency_hz=freq)
            alpha_2d[:, i] = env_interp.absorption
    else:
        # Use default 200 kHz
        if env_interp.absorption is None:
            env_interp.compute_absorption(frequency_hz=200_000.0)
        alpha_2d = np.tile(env_interp.absorption.reshape(-1, 1), (1, n_channels))
    
    # Create DataArrays
    sound_speed_da = xr.DataArray(
        ss_2d,
        dims=["time1", "channel"],
        coords={
            "time1": ping_times,
            "channel": channel_ids,
        },
        attrs={
            "units": "m/s",
            "long_name": "Sound speed in seawater",
            "method": "mackenzie",
            "source": env_data.source_url or "geoparquet",
        },
    )
    
    absorption_da = xr.DataArray(
        alpha_2d,
        dims=["time1", "channel"],
        coords={
            "time1": ping_times,
            "channel": channel_ids,
        },
        attrs={
            "units": "dB/m",
            "long_name": "Sound absorption coefficient",
            "method": "francois_garrison",
            "source": env_data.source_url or "geoparquet",
        },
    )
    
    return sound_speed_da, absorption_da
