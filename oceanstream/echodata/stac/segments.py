"""GeoJSON segments for linking echograms to GPS track coordinates.

Provides utilities to create GeoJSON track segments that map echogram files
to their corresponding spatial positions on the vessel's GPS track.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional, Union

import numpy as np

if TYPE_CHECKING:
    import xarray as xr

logger = logging.getLogger(__name__)


def create_segments_geojson(
    echodata_dir: Path,
    output_path: Optional[Path] = None,
    *,
    sv_zarr_path: Optional[Path] = None,
    mvbs_zarr_path: Optional[Path] = None,
    gps_source: Optional[Path] = None,
    segment_by: str = "daily",
) -> Path:
    """
    Create GeoJSON FeatureCollection linking echogram files to GPS track segments.
    
    This enables displaying echograms in a spatial context - each echogram PNG
    can be associated with a track line segment on a map, allowing users to
    see where each echogram's data was collected.
    
    Args:
        echodata_dir: Directory containing echodata products
        output_path: Output path for segments.geojson (default: echodata_dir/segments.geojson)
        sv_zarr_path: Path to Sv Zarr with interpolated GPS
        mvbs_zarr_path: Path to MVBS Zarr with ping_time and position
        gps_source: Alternative GPS source (GeoParquet file)
        segment_by: Segmentation strategy ("daily", "file", "hour")
        
    Returns:
        Path to created segments.geojson
        
    Example:
        # Create segments from MVBS with embedded GPS
        segments = create_segments_geojson(
            echodata_dir=Path("./output/campaign/echodata"),
            mvbs_zarr_path=Path("./output/campaign/echodata/mvbs/mvbs.zarr"),
        )
        
        # Create segments from daily concatenated Sv
        segments = create_segments_geojson(
            echodata_dir=Path("./output/campaign/echodata"),
            segment_by="daily",
        )
    """
    echodata_dir = Path(echodata_dir)
    
    if output_path is None:
        output_path = echodata_dir / "segments.geojson"
    else:
        output_path = Path(output_path)
    
    # Find GPS-enriched Zarr (prefer Sv, then MVBS)
    zarr_path = None
    
    if sv_zarr_path and Path(sv_zarr_path).exists():
        zarr_path = Path(sv_zarr_path)
    elif mvbs_zarr_path and Path(mvbs_zarr_path).exists():
        zarr_path = Path(mvbs_zarr_path)
    else:
        # Auto-detect
        for subdir in ["sv", "mvbs", "nasc"]:
            product_dir = echodata_dir / subdir
            if product_dir.exists():
                zarr_stores = list(product_dir.glob("*.zarr"))
                if zarr_stores:
                    zarr_path = zarr_stores[0]
                    break
    
    if zarr_path is None:
        raise ValueError(
            f"No Zarr with GPS data found in {echodata_dir}. "
            "Provide sv_zarr_path or mvbs_zarr_path explicitly."
        )
    
    # Extract segment coordinates
    segments = extract_segment_coordinates(
        zarr_path,
        segment_by=segment_by,
    )
    
    # Build GeoJSON FeatureCollection
    geojson = _segments_to_geojson(segments)
    
    # Write output
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(geojson, f, indent=2)
    
    logger.info(f"Created segments GeoJSON at {output_path} with {len(segments)} segments")
    
    return output_path


def create_echogram_segment(
    echogram_path: Path,
    zarr_path: Path,
    *,
    date: Optional[str] = None,
    frequency_khz: Optional[float] = None,
) -> dict[str, Any]:
    """
    Create a GeoJSON Feature for a single echogram file.
    
    Links an echogram PNG to its track segment by extracting coordinates
    from the corresponding time period in the Zarr dataset.
    
    Args:
        echogram_path: Path to echogram PNG file
        zarr_path: Path to Zarr with ping_time and position
        date: Date string (YYYY-MM-DD) for the echogram (parsed from filename if None)
        frequency_khz: Frequency in kHz (parsed from filename if None)
        
    Returns:
        GeoJSON Feature dict with LineString geometry
        
    Example:
        feature = create_echogram_segment(
            echogram_path=Path("./echograms/2023-06-15_38kHz.png"),
            zarr_path=Path("./sv/sv_2023-06-15.zarr"),
        )
    """
    echogram_path = Path(echogram_path)
    zarr_path = Path(zarr_path)
    
    # Parse filename for date and frequency
    stem = echogram_path.stem
    parts = stem.rsplit("_", 1)
    
    if date is None and len(parts) >= 1:
        date = parts[0]
    
    if frequency_khz is None and len(parts) == 2:
        freq_str = parts[1].replace("kHz", "").replace("khz", "")
        try:
            frequency_khz = float(freq_str)
        except ValueError:
            pass
    
    # Extract coordinates for this date
    coords, props = _extract_date_coordinates(zarr_path, date)
    
    if not coords:
        logger.warning(f"No coordinates found for echogram {echogram_path.name}")
        return None
    
    # Build feature
    feature = {
        "type": "Feature",
        "geometry": {
            "type": "LineString",
            "coordinates": coords,
        },
        "properties": {
            "echogram": echogram_path.name,
            "date": date,
            **props,
        },
    }
    
    if frequency_khz:
        feature["properties"]["frequency_khz"] = frequency_khz
    
    return feature


def extract_segment_coordinates(
    zarr_path: Path,
    *,
    segment_by: str = "daily",
    lat_var: str = "latitude",
    lon_var: str = "longitude",
    time_var: str = "ping_time",
    max_points_per_segment: int = 1000,
) -> list[dict[str, Any]]:
    """
    Extract GPS coordinates grouped into segments from a Zarr dataset.
    
    Supports various segmentation strategies:
    - "daily": Group by day
    - "hourly": Group by hour
    - "file": One segment per source file (if file_idx available)
    
    Args:
        zarr_path: Path to Zarr store with position data
        segment_by: Segmentation strategy ("daily", "hourly", "file")
        lat_var: Name of latitude variable
        lon_var: Name of longitude variable
        time_var: Name of time variable
        max_points_per_segment: Max points to include (subsampled if exceeded)
        
    Returns:
        List of segment dicts with coordinates and properties
    """
    import xarray as xr
    
    zarr_path = Path(zarr_path)
    
    try:
        ds = xr.open_zarr(zarr_path)
    except Exception as e:
        logger.error(f"Could not open Zarr at {zarr_path}: {e}")
        return []
    
    segments = []
    
    try:
        # Check for required variables
        has_lat = lat_var in ds.data_vars or lat_var in ds.coords
        has_lon = lon_var in ds.data_vars or lon_var in ds.coords
        has_time = time_var in ds.dims or time_var in ds.coords
        
        if not (has_lat and has_lon and has_time):
            # Try alternate variable names
            alt_names = {
                "latitude": ["lat", "LATITUDE", "Latitude"],
                "longitude": ["lon", "LONGITUDE", "Longitude"],
                "ping_time": ["time", "TIME", "datetime"],
            }
            
            for primary, alts in alt_names.items():
                if primary == lat_var and not has_lat:
                    for alt in alts:
                        if alt in ds.data_vars or alt in ds.coords:
                            lat_var = alt
                            has_lat = True
                            break
                elif primary == lon_var and not has_lon:
                    for alt in alts:
                        if alt in ds.data_vars or alt in ds.coords:
                            lon_var = alt
                            has_lon = True
                            break
                elif primary == time_var and not has_time:
                    for alt in alts:
                        if alt in ds.dims or alt in ds.coords:
                            time_var = alt
                            has_time = True
                            break
        
        if not (has_lat and has_lon and has_time):
            logger.warning(
                f"Zarr missing required variables. Found: {list(ds.data_vars)} + {list(ds.coords)}"
            )
            ds.close()
            return []
        
        # Get all data
        times = ds[time_var].values
        lats = ds[lat_var].values
        lons = ds[lon_var].values
        
        # Flatten if multi-dimensional
        if lats.ndim > 1:
            # For MVBS, coords might be (ping_time,) or (ping_time, range_sample)
            # We want just (ping_time,)
            if "ping_time" in ds[lat_var].dims:
                ping_idx = ds[lat_var].dims.index("ping_time") if hasattr(ds[lat_var].dims, "index") else 0
                # Take first slice along other dims
                if lats.ndim == 2:
                    lats = lats[:, 0] if ping_idx == 0 else lats[0, :]
                    lons = lons[:, 0] if ping_idx == 0 else lons[0, :]
            else:
                lats = lats.flatten()
                lons = lons.flatten()
        
        # Ensure time array matches
        if len(times) != len(lats):
            min_len = min(len(times), len(lats), len(lons))
            times = times[:min_len]
            lats = lats[:min_len]
            lons = lons[:min_len]
        
        # Remove NaN values
        valid_mask = ~np.isnan(lats) & ~np.isnan(lons)
        times = times[valid_mask]
        lats = lats[valid_mask]
        lons = lons[valid_mask]
        
        if len(times) == 0:
            logger.warning("No valid GPS coordinates found in Zarr")
            ds.close()
            return []
        
        # Convert times to datetime
        times_dt = [np.datetime64(t, 'ns') for t in times]
        
        # Group by segment strategy
        if segment_by == "daily":
            segments = _group_by_day(times_dt, lats, lons, max_points_per_segment)
        elif segment_by == "hourly":
            segments = _group_by_hour(times_dt, lats, lons, max_points_per_segment)
        else:
            # Single segment for entire dataset
            segments = [_create_single_segment(times_dt, lats, lons, max_points_per_segment)]
        
    finally:
        ds.close()
    
    return segments


def _extract_date_coordinates(
    zarr_path: Path,
    date_str: str,
) -> tuple[list[list[float]], dict[str, Any]]:
    """Extract coordinates for a specific date from Zarr."""
    import xarray as xr
    
    try:
        ds = xr.open_zarr(zarr_path)
    except Exception:
        return [], {}
    
    coords = []
    props = {}
    
    try:
        # Find time and position variables
        time_var = None
        for var in ["ping_time", "time"]:
            if var in ds.dims or var in ds.coords:
                time_var = var
                break
        
        lat_var = None
        for var in ["latitude", "lat"]:
            if var in ds.data_vars or var in ds.coords:
                lat_var = var
                break
        
        lon_var = None
        for var in ["longitude", "lon"]:
            if var in ds.data_vars or var in ds.coords:
                lon_var = var
                break
        
        if not (time_var and lat_var and lon_var):
            return [], {}
        
        # Filter to date
        times = ds[time_var].values
        lats = ds[lat_var].values.flatten()
        lons = ds[lon_var].values.flatten()
        
        # Parse date
        target_date = np.datetime64(date_str, 'D')
        
        # Find matching indices
        dates = times.astype('datetime64[D]')
        mask = dates == target_date
        
        if not mask.any():
            return [], {}
        
        # Extract coordinates
        filtered_lats = lats[mask]
        filtered_lons = lons[mask]
        filtered_times = times[mask]
        
        # Remove NaNs
        valid = ~np.isnan(filtered_lats) & ~np.isnan(filtered_lons)
        filtered_lats = filtered_lats[valid]
        filtered_lons = filtered_lons[valid]
        filtered_times = filtered_times[valid]
        
        if len(filtered_lats) == 0:
            return [], {}
        
        # Subsample if too many points
        if len(filtered_lats) > 500:
            indices = np.linspace(0, len(filtered_lats) - 1, 500).astype(int)
            filtered_lats = filtered_lats[indices]
            filtered_lons = filtered_lons[indices]
            filtered_times = filtered_times[indices]
        
        # Build coordinate list [lon, lat]
        coords = [[float(lon), float(lat)] for lat, lon in zip(filtered_lats, filtered_lons)]
        
        # Properties
        props = {
            "start_datetime": str(np.datetime_as_string(filtered_times.min(), unit='s')) + "Z",
            "end_datetime": str(np.datetime_as_string(filtered_times.max(), unit='s')) + "Z",
            "point_count": len(coords),
        }
        
    finally:
        ds.close()
    
    return coords, props


def _group_by_day(
    times: list,
    lats: np.ndarray,
    lons: np.ndarray,
    max_points: int,
) -> list[dict[str, Any]]:
    """Group coordinates by day."""
    times_arr = np.array(times)
    dates = times_arr.astype('datetime64[D]')
    unique_dates = np.unique(dates)
    
    segments = []
    for date in unique_dates:
        mask = dates == date
        day_times = times_arr[mask]
        day_lats = lats[mask]
        day_lons = lons[mask]
        
        segment = _create_single_segment(
            day_times.tolist(),
            day_lats,
            day_lons,
            max_points,
        )
        segment["date"] = str(date)
        segments.append(segment)
    
    return segments


def _group_by_hour(
    times: list,
    lats: np.ndarray,
    lons: np.ndarray,
    max_points: int,
) -> list[dict[str, Any]]:
    """Group coordinates by hour."""
    times_arr = np.array(times)
    hours = times_arr.astype('datetime64[h]')
    unique_hours = np.unique(hours)
    
    segments = []
    for hour in unique_hours:
        mask = hours == hour
        hour_times = times_arr[mask]
        hour_lats = lats[mask]
        hour_lons = lons[mask]
        
        segment = _create_single_segment(
            hour_times.tolist(),
            hour_lats,
            hour_lons,
            max_points,
        )
        segment["hour"] = str(hour)
        segments.append(segment)
    
    return segments


def _create_single_segment(
    times: list,
    lats: np.ndarray,
    lons: np.ndarray,
    max_points: int,
) -> dict[str, Any]:
    """Create a single segment dict."""
    # Subsample if needed
    if len(lats) > max_points:
        indices = np.linspace(0, len(lats) - 1, max_points).astype(int)
        lats = lats[indices]
        lons = lons[indices]
        times = [times[i] for i in indices]
    
    times_arr = np.array(times)
    
    # Convert to datetime64[ns] if we have int64 nanoseconds
    if times_arr.dtype == np.int64:
        times_arr = times_arr.astype("datetime64[ns]")
    
    # Build coordinates [lon, lat]
    coords = [[float(lon), float(lat)] for lat, lon in zip(lats, lons)]
    
    return {
        "coordinates": coords,
        "start_datetime": str(np.datetime_as_string(times_arr.min(), unit='s')) + "Z",
        "end_datetime": str(np.datetime_as_string(times_arr.max(), unit='s')) + "Z",
        "point_count": len(coords),
        "start_lat": float(lats[0]),
        "start_lon": float(lons[0]),
        "end_lat": float(lats[-1]),
        "end_lon": float(lons[-1]),
    }


def _segments_to_geojson(segments: list[dict[str, Any]]) -> dict[str, Any]:
    """Convert segment list to GeoJSON FeatureCollection."""
    features = []
    
    for segment in segments:
        coords = segment.pop("coordinates", [])
        
        if len(coords) < 2:
            # Need at least 2 points for LineString
            continue
        
        feature = {
            "type": "Feature",
            "geometry": {
                "type": "LineString",
                "coordinates": coords,
            },
            "properties": segment,
        }
        features.append(feature)
    
    return {
        "type": "FeatureCollection",
        "features": features,
    }
