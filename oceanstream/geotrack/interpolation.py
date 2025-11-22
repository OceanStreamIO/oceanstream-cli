"""Spatial-temporal interpolation utilities for sensor data.

This module provides functions to join sensor time-series data (without spatial
coordinates) with existing campaign trajectory data (with spatial coordinates)
using temporal interpolation.
"""
from __future__ import annotations

from pathlib import Path
from typing import Literal

import pandas as pd
import geopandas as gpd
from shapely.geometry import Point

from oceanstream.geotrack.deduplication import read_existing_campaign_data


InterpolationMethod = Literal["nearest", "linear", "ffill", "bfill"]


def has_spatial_coordinates(df: pd.DataFrame) -> bool:
    """Check if DataFrame has spatial coordinate columns.
    
    Args:
        df: DataFrame to check
        
    Returns:
        True if both latitude and longitude columns exist
    """
    return 'latitude' in df.columns and 'longitude' in df.columns


def interpolate_spatial_coordinates(
    sensor_df: pd.DataFrame,
    reference_gdf: gpd.GeoDataFrame | pd.DataFrame,
    time_column: str = "time",
    method: InterpolationMethod = "linear",
    max_time_gap: pd.Timedelta | None = None,
) -> pd.DataFrame:
    """Interpolate spatial coordinates for sensor data using reference trajectory.
    
    Args:
        sensor_df: Sensor data with time column but no spatial coordinates
        reference_gdf: Reference trajectory data with time, latitude, longitude
        time_column: Name of time column (default: "time")
        method: Interpolation method - "nearest", "linear", "ffill", "bfill"
        max_time_gap: Maximum allowed time gap for interpolation. 
                     Data points beyond this gap will have NaN coordinates.
        
    Returns:
        DataFrame with interpolated latitude and longitude columns
        
    Raises:
        ValueError: If required columns are missing
    """
    # Validate input
    if time_column not in sensor_df.columns:
        raise ValueError(f"Sensor data missing time column: {time_column}")
    
    if time_column not in reference_gdf.columns:
        raise ValueError(f"Reference data missing time column: {time_column}")
    
    if not has_spatial_coordinates(reference_gdf):
        raise ValueError("Reference data must have latitude and longitude columns")
    
    # Ensure time columns are datetime
    sensor_df = sensor_df.copy()
    reference_gdf = reference_gdf.copy()
    
    sensor_df[time_column] = pd.to_datetime(sensor_df[time_column])
    reference_gdf[time_column] = pd.to_datetime(reference_gdf[time_column])
    
    # Sort both dataframes by time
    sensor_df = sensor_df.sort_values(time_column)
    reference_gdf = reference_gdf.sort_values(time_column)
    
    # Perform merge_asof for temporal join
    if method == "nearest":
        # Nearest neighbor in time
        merged = pd.merge_asof(
            sensor_df,
            reference_gdf[[time_column, 'latitude', 'longitude']],
            on=time_column,
            direction='nearest',
            tolerance=max_time_gap,
        )
    elif method == "linear":
        # Linear interpolation requires more steps
        # First, create a combined timeline
        all_times = pd.concat([
            sensor_df[[time_column]],
            reference_gdf[[time_column, 'latitude', 'longitude']]
        ]).sort_values(time_column).drop_duplicates(subset=[time_column])
        
        # Interpolate reference data to all times
        all_times = all_times.set_index(time_column)
        all_times[['latitude', 'longitude']] = all_times[['latitude', 'longitude']].interpolate(
            method='linear',
            limit_direction='both'
        )
        all_times = all_times.reset_index()
        
        # Merge with sensor data
        merged = pd.merge_asof(
            sensor_df,
            all_times[[time_column, 'latitude', 'longitude']],
            on=time_column,
            direction='nearest',
            tolerance=max_time_gap,
        )
    elif method == "ffill":
        # Forward fill (use most recent reference point)
        merged = pd.merge_asof(
            sensor_df,
            reference_gdf[[time_column, 'latitude', 'longitude']],
            on=time_column,
            direction='backward',
            tolerance=max_time_gap,
        )
    elif method == "bfill":
        # Backward fill (use next reference point)
        merged = pd.merge_asof(
            sensor_df,
            reference_gdf[[time_column, 'latitude', 'longitude']],
            on=time_column,
            direction='forward',
            tolerance=max_time_gap,
        )
    else:
        raise ValueError(f"Unknown interpolation method: {method}")
    
    return merged


def enrich_sensor_data_from_campaign(
    sensor_df: pd.DataFrame,
    campaign_dir: Path,
    time_column: str = "time",
    method: InterpolationMethod = "linear",
    max_time_gap_seconds: float = 60.0,
) -> tuple[pd.DataFrame, bool]:
    """Enrich sensor data with spatial coordinates from existing campaign data.
    
    Args:
        sensor_df: Sensor data without spatial coordinates
        campaign_dir: Path to campaign directory with existing GeoParquet data
        time_column: Name of time column
        method: Interpolation method
        max_time_gap_seconds: Maximum time gap for valid interpolation (seconds)
        
    Returns:
        Tuple of (enriched_dataframe, interpolation_successful)
        - If successful: DataFrame with interpolated lat/lon
        - If failed: DataFrame with empty lat/lon columns
    """
    # Check if sensor data already has coordinates
    if has_spatial_coordinates(sensor_df):
        return sensor_df, True
    
    # Try to read existing campaign data
    try:
        reference_data = read_existing_campaign_data(
            campaign_dir,
            columns=[time_column, 'latitude', 'longitude']
        )
    except Exception as e:
        print(f"[geotrack] Could not read existing campaign data: {e}")
        reference_data = None
    
    # If no reference data, add empty spatial columns
    if reference_data is None or reference_data.empty:
        print("[geotrack] No existing campaign data found for interpolation")
        print("[geotrack] Adding empty latitude/longitude columns")
        sensor_df['latitude'] = pd.NA
        sensor_df['longitude'] = pd.NA
        return sensor_df, False
    
    # Attempt interpolation
    try:
        print(f"[geotrack] Found {len(reference_data)} reference trajectory points")
        print(f"[geotrack] Interpolating spatial coordinates using method: {method}")
        
        max_gap = pd.Timedelta(seconds=max_time_gap_seconds)
        enriched_df = interpolate_spatial_coordinates(
            sensor_df=sensor_df,
            reference_gdf=reference_data,
            time_column=time_column,
            method=method,
            max_time_gap=max_gap,
        )
        
        # Check how many points were successfully interpolated
        valid_coords = enriched_df[['latitude', 'longitude']].notna().all(axis=1).sum()
        total_points = len(enriched_df)
        success_rate = valid_coords / total_points if total_points > 0 else 0
        
        print(f"[geotrack] Interpolation complete: {valid_coords}/{total_points} points "
              f"({success_rate:.1%}) have valid coordinates")
        
        if success_rate < 0.5:
            print("[geotrack] Warning: Less than 50% of points have valid coordinates")
            print("[geotrack] This may indicate poor temporal overlap with reference data")
        
        return enriched_df, success_rate > 0
        
    except Exception as e:
        print(f"[geotrack] Interpolation failed: {e}")
        print("[geotrack] Adding empty latitude/longitude columns")
        sensor_df['latitude'] = pd.NA
        sensor_df['longitude'] = pd.NA
        return sensor_df, False


def create_geometry_from_coordinates(df: pd.DataFrame) -> gpd.GeoDataFrame:
    """Create geometry column from latitude/longitude coordinates.
    
    Handles NaN values by creating invalid points that will be filtered later.
    
    Args:
        df: DataFrame with latitude and longitude columns
        
    Returns:
        GeoDataFrame with geometry column
    """
    if not has_spatial_coordinates(df):
        raise ValueError("DataFrame must have latitude and longitude columns")
    
    # Create geometry, handling NaN values
    def make_point(row):
        if pd.isna(row['latitude']) or pd.isna(row['longitude']):
            return None
        return Point(row['longitude'], row['latitude'])
    
    df['geometry'] = df.apply(make_point, axis=1)
    
    # Create GeoDataFrame
    gdf = gpd.GeoDataFrame(df, geometry='geometry', crs="EPSG:4326")
    
    return gdf
