"""Environmental enrichment for acoustic data processing.

This module provides functions for enriching EchoData with environmental
parameters (temperature, salinity, sound speed, absorption) from:
1. In-situ CTD data from geoparquet (Saildrone SBE37 sensors)
2. Copernicus Marine Service model data (fallback when CTD unavailable)
"""

from oceanstream.echodata.environment.enrich import (
    enrich_environment,
    enrich_sv_with_location,
    enrich_sv_with_location_from_url,
    get_environment_with_fallback,
    load_geoparquet_environment,
    interpolate_environment_to_ping_time,
    update_echodata_environment,
    update_echodata_platform,
)
from oceanstream.echodata.environment.sound_speed import (
    compute_sound_speed,
    chen_millero_sound_speed,
    mackenzie_sound_speed,
)
from oceanstream.echodata.environment.absorption import (
    compute_absorption_coefficient,
    francois_garrison_absorption,
)
from oceanstream.echodata.environment.copernicus import (
    fetch_copernicus_environment,
    compute_sound_speed_from_copernicus,
    get_copernicus_profile,
)
from oceanstream.echodata.environment.blended import (
    build_blended_profile,
    compute_depth_weighted_env_params,
    get_blended_env_params_for_calibration,
    print_profile_comparison,
)
from oceanstream.echodata.environment.geoparquet import (
    EnvVarMapping,
    EnvData,
    load_env_from_geoparquet,
    interpolate_env_to_ping_times,
    get_env_params_for_calibration,
)

__all__ = [
    # High-level functions
    "enrich_environment",
    "enrich_sv_with_location",
    "enrich_sv_with_location_from_url",
    "get_environment_with_fallback",
    # Cloud-native GeoParquet loading (az://, s3://, gs://)
    "EnvVarMapping",
    "EnvData",
    "load_env_from_geoparquet",
    "interpolate_env_to_ping_times",
    "get_env_params_for_calibration",
    # Legacy geoparquet loading (local paths)
    "load_geoparquet_environment",
    "interpolate_environment_to_ping_time",
    "update_echodata_environment",
    "update_echodata_platform",
    # Sound speed
    "compute_sound_speed",
    "chen_millero_sound_speed",
    "mackenzie_sound_speed",
    # Absorption
    "compute_absorption_coefficient",
    "francois_garrison_absorption",
    # Copernicus
    "fetch_copernicus_environment",
    "compute_sound_speed_from_copernicus",
    "get_copernicus_profile",
    # Blended profiles (in-situ surface + Copernicus depth)
    "build_blended_profile",
    "compute_depth_weighted_env_params",
    "get_blended_env_params_for_calibration",
    "print_profile_comparison",
]
