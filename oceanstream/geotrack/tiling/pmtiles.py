from __future__ import annotations

import datetime as dt
import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Iterable
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq

from ...storage.azure_blob import upload_to_azure_blob

import re

# Default patterns to exclude verbose/redundant columns
# These are typically statistical variants that inflate tile size
DEFAULT_EXCLUDE_PATTERNS = [
    r'.*_STDDEV$',      # Standard deviation columns
    r'.*_STD$',         # Alternative std suffix
    r'.*_MIN$',         # Minimum values
    r'.*_MAX$',         # Maximum values
    r'.*_PEAK$',        # Peak values
    r'^UWND_.*',        # U-component wind (derived)
    r'^VWND_.*',        # V-component wind (derived)
    r'^WWND_.*',        # W-component wind (derived)
    r'.*_WING_.*',      # Wing-specific sensors (redundant for most use cases)
    r'^WING_.*',        # Wing measurements
    r'^HDG$',           # Raw heading (HDG_FILTERED_MEAN is preferred)
    r'^SOG$',           # Raw speed over ground (SOG_FILTERED_MEAN is preferred)
    r'^COG$',           # Raw course over ground (COG_FILTERED_MEAN is preferred)
]

# System columns that should never be treated as measurements
SYSTEM_COLUMNS = {
    'time', 'latitude', 'longitude', 'geometry', 'platform_id',
    'campaign_id', 'trajectory', 'lat_bin', 'lon_bin',
}


def _discover_measurement_columns(
    parquet_file: Path,
    exclude_patterns: list[str] | None = None,
) -> list[str]:
    """
    Auto-discover measurement columns from a GeoParquet file.
    
    Args:
        parquet_file: Path to any parquet file in the dataset
        exclude_patterns: Regex patterns to exclude columns (None = use defaults)
        
    Returns:
        List of measurement column names (excluding system and filtered columns)
    """
    with open(parquet_file, 'rb') as f:
        pf = pq.ParquetFile(f)
        all_columns = set(pf.schema.names)
    
    # Start with all non-system columns
    measurement_cols = all_columns - {c.lower() for c in SYSTEM_COLUMNS} - SYSTEM_COLUMNS
    
    # Also filter out lowercase columns (system columns might have various cases)
    measurement_cols = {c for c in measurement_cols if not c.islower()}
    
    # Apply exclusion patterns
    patterns = exclude_patterns if exclude_patterns is not None else DEFAULT_EXCLUDE_PATTERNS
    compiled_patterns = [re.compile(p, re.IGNORECASE) for p in patterns]
    
    filtered_cols = []
    for col in measurement_cols:
        if not any(p.match(col) for p in compiled_patterns):
            filtered_cols.append(col)
    
    return sorted(filtered_cols)


class MissingDependencyError(RuntimeError):
    pass


def _require_cli(name: str) -> None:
    if shutil.which(name) is None:
        raise MissingDependencyError(
            f"Required CLI '{name}' not found on PATH. Install it and try again."
        )


def _iter_partition_points(
    partition_path: Path,
    sample_rate: int = 1,
    measurement_columns: list[str] | None = None,
    exclude_patterns: list[str] | None = None,
) -> Iterable[tuple[float, float, dt.datetime, dict | None, str | None]]:
    """
    Iterate over (longitude, latitude, timestamp, measurements, platform_id) from a GeoParquet partition file.
    
    Args:
        partition_path: Path to parquet file
        sample_rate: Take every Nth point (1 = all points)
        measurement_columns: Specific columns to include (None = auto-discover)
        exclude_patterns: Regex patterns to exclude when auto-discovering (None = use defaults)
        
    Yields:
        Tuples of (lon, lat, timestamp, measurements_dict, platform_id)
    """
    # Build columns to read
    base_columns = ['longitude', 'latitude', 'time']
    
    with open(partition_path, 'rb') as f:
        pf = pq.ParquetFile(f)
        available_cols = set(pf.schema.names)
        
        # Determine measurement columns to use
        if measurement_columns is not None:
            # Use explicit list, filtering to available columns
            available_measurements = [c for c in measurement_columns if c in available_cols]
        else:
            # Auto-discover from this file
            available_measurements = _discover_measurement_columns(partition_path, exclude_patterns)
        
        read_columns = base_columns.copy()
        if available_measurements:
            read_columns.extend(available_measurements)
        
        # Also read platform_id if available
        has_platform_id = 'platform_id' in available_cols
        if has_platform_id:
            read_columns = list(set(read_columns + ['platform_id']))
        
        for rg in range(pf.num_row_groups):
            table = pf.read_row_group(rg, columns=read_columns)
            df = table.to_pandas()
            
            # Normalize column names
            df.columns = [c.lower() for c in df.columns]
            
            # Ensure proper types
            df['time'] = pd.to_datetime(df['time'], errors='coerce')
            df = df.dropna(subset=['longitude', 'latitude', 'time'])
            df = df.sort_values('time')
            
            # Apply sampling
            if sample_rate and sample_rate > 1:
                df = df.iloc[::sample_rate]
            
            for row in df.itertuples(index=False):
                lon = float(row.longitude)
                lat = float(row.latitude)
                t = pd.Timestamp(row.time).to_pydatetime()
                
                # Build measurements dict
                measurements = None
                if available_measurements:
                    measurements = {}
                    for col in available_measurements:
                        col_lower = col.lower()
                        if hasattr(row, col_lower):
                            val = getattr(row, col_lower)
                            if pd.notna(val):  # Skip NaN values
                                measurements[col] = float(val) if isinstance(val, (int, float)) else val
                
                # Get platform_id if available
                plat_id = None
                if has_platform_id and hasattr(row, 'platform_id'):
                    plat_id = str(row.platform_id) if pd.notna(row.platform_id) else None
                
                yield lon, lat, t, measurements, plat_id


def _segments_from_points(
    points: list[tuple[float, float, dt.datetime, dict | None, str | None]],
    time_gap_minutes: int = 60,
) -> list[dict]:
    """
    Split points into segments based on time gaps and platform_id.
    
    Args:
        points: List of (lon, lat, timestamp, measurements, platform_id) tuples
        time_gap_minutes: Minutes of gap to split segments
        
    Returns:
        List of segment dicts with coords, t_start, t_end, measurements_avg, platform_id
    """
    segments = []
    current = []
    gap = dt.timedelta(minutes=max(0, time_gap_minutes))
    last_t = None
    last_platform = None
    
    for lon, lat, t, measurements, platform_id in points:
        if not isinstance(t, dt.datetime):
            continue
        
        # Start new segment if gap is too large or platform changes
        should_split = (last_t is not None and (t - last_t) > gap) or (
            last_platform is not None and platform_id != last_platform
        )
        
        if should_split:
            if len(current) > 1:
                coords = [(float(x), float(y)) for x, y, _, _, _ in current]
                
                # Compute average measurements for segment
                avg_measurements = None
                if any(m for _, _, _, m, _ in current if m):
                    avg_measurements = {}
                    measurement_keys = set()
                    for _, _, _, m, _ in current:
                        if m:
                            measurement_keys.update(m.keys())
                    
                    for key in measurement_keys:
                        values = [m[key] for _, _, _, m, _ in current if m and key in m and isinstance(m[key], (int, float))]
                        if values:
                            avg_measurements[key] = sum(values) / len(values)
                
                # Use the platform_id from the segment
                seg_platform_id = current[0][4] if current else None
                
                segments.append({
                    "coords": coords,
                    "t_start": current[0][2],
                    "t_end": current[-1][2],
                    "measurements": avg_measurements,
                    "platform_id": seg_platform_id,
                })
            current = []
        
        current.append((float(lon), float(lat), t, measurements, platform_id))
        last_t = t
        last_platform = platform_id
    
    # Add final segment
    if len(current) > 1:
        coords = [(float(x), float(y)) for x, y, _, _, _ in current]
        
        # Compute average measurements for segment
        avg_measurements = None
        if any(m for _, _, _, m, _ in current if m):
            avg_measurements = {}
            measurement_keys = set()
            for _, _, _, m, _ in current:
                if m:
                    measurement_keys.update(m.keys())
            
            for key in measurement_keys:
                values = [m[key] for _, _, _, m, _ in current if m and key in m and isinstance(m[key], (int, float))]
                if values:
                    avg_measurements[key] = sum(values) / len(values)
        
        # Use the platform_id from the segment
        seg_platform_id = current[0][4] if current else None
        
        segments.append({
            "coords": coords,
            "t_start": current[0][2],
            "t_end": current[-1][2],
            "measurements": avg_measurements,
            "platform_id": seg_platform_id,
        })
    
    return segments


def _build_ndjson_from_geoparquet(
    geoparquet_root: Path,
    output_path: Path,
    *,
    sample_rate: int = 5,
    time_gap_minutes: int = 60,
    platform_id: str | None = None,
    include_measurements: bool = True,
    measurement_columns: list[str] | None = None,
    exclude_patterns: list[str] | None = None,
) -> int:
    """
    Build NDJSON file from GeoParquet partitions with segments and day markers.
    
    Args:
        geoparquet_root: Root directory of partitioned GeoParquet
        output_path: Where to write NDJSON
        sample_rate: Take every Nth point
        time_gap_minutes: Minutes of gap to split segments
        platform_id: Optional platform/cruise identifier
        include_measurements: Whether to include oceanographic measurements
        measurement_columns: Specific columns to include (None = auto-discover from data)
        exclude_patterns: Regex patterns to exclude when auto-discovering (None = use defaults)
        
    Returns:
        Number of features written
    """
    # Read metadata to get partition list
    metadata_path = geoparquet_root / "metadata.parquet"
    if not metadata_path.exists():
        # Fallback: scan for parquet files - store as absolute paths to avoid path joining issues
        parquet_files = [p.resolve() for p in geoparquet_root.rglob("*.parquet")]
        if not parquet_files:
            raise ValueError(f"No parquet files found in {geoparquet_root}")
        meta_df = pd.DataFrame({'partition_path': [str(p) for p in parquet_files]})
    else:
        meta_df = pd.read_parquet(metadata_path)
    
    # Determine measurement settings
    # If include_measurements is False, pass empty list to disable
    # If measurement_columns is provided, use those explicitly
    # Otherwise, let auto-discovery happen in _iter_partition_points
    if not include_measurements:
        actual_measurements: list[str] | None = []  # Empty list means no measurements
    else:
        actual_measurements = measurement_columns  # None means auto-discover
    
    seg_id = 0
    count_feats = 0
    day_stats = {}  # Track start/end per UTC day
    discovered_columns: list[str] | None = None  # Track discovered columns for logging
    
    with open(output_path, 'w', encoding='utf-8') as out:
        for _, row in meta_df.iterrows():
            partition_path = Path(str(row['partition_path']))
            
            # Make path absolute if relative
            if not partition_path.is_absolute():
                partition_path = geoparquet_root / partition_path
            
            if not partition_path.exists():
                print(f"Warning: partition not found: {partition_path}", file=sys.stderr)
                continue
            
            # Discover columns from first partition if auto-discovering
            if discovered_columns is None and actual_measurements is None:
                discovered_columns = _discover_measurement_columns(partition_path, exclude_patterns)
                print(f"Auto-discovered {len(discovered_columns)} measurement columns:")
                for col in discovered_columns:
                    print(f"  - {col}")
            
            try:
                points = list(_iter_partition_points(
                    partition_path,
                    sample_rate=sample_rate,
                    measurement_columns=actual_measurements,
                    exclude_patterns=exclude_patterns,
                ))
            except Exception as e:
                print(f"Warning: failed to read {partition_path}: {e}", file=sys.stderr)
                continue
            
            if not points:
                continue
            
            # Update per-day stats (now with platform_id tracking)
            for lon, lat, t, _, plat_id in points:
                t_ts = pd.Timestamp(t)
                day_key = t_ts.strftime("%Y-%m-%d")
                # Include platform_id in key for multi-platform support
                combined_key = f"{day_key}:{plat_id}" if plat_id else day_key
                st = day_stats.get(combined_key)
                if st is None:
                    day_stats[combined_key] = {
                        "t_start": t_ts,
                        "t_end": t_ts,
                        "start_coord": (float(lon), float(lat)),
                        "end_coord": (float(lon), float(lat)),
                        "platform_id": plat_id,
                        "day": day_key,
                    }
                else:
                    if t_ts < st["t_start"]:
                        st["t_start"] = t_ts
                        st["start_coord"] = (float(lon), float(lat))
                    if t_ts > st["t_end"]:
                        st["t_end"] = t_ts
                        st["end_coord"] = (float(lon), float(lat))
            
            # Create segments
            segments = _segments_from_points(points, time_gap_minutes=time_gap_minutes)
            
            for seg in segments:
                coords = seg["coords"]
                if len(coords) < 2:
                    continue
                
                # Extract grid info from partition path or row
                lon_grid = row.get('lon_grid') if isinstance(row, pd.Series) else None
                lat_grid = row.get('lat_grid') if isinstance(row, pd.Series) else None
                
                if pd.isna(lon_grid) if 'lon_grid' in row else True:
                    # Parse from path like .../lon_grid=X/lat_grid=Y/...
                    try:
                        parts = str(partition_path).split('/')
                        for p in parts:
                            if p.startswith('lon_grid='):
                                lon_grid = int(p.split('=', 1)[1])
                            elif p.startswith('lat_grid='):
                                lat_grid = int(p.split('=', 1)[1])
                    except Exception:
                        pass
                
                # Build segment properties
                day_str = pd.Timestamp(seg["t_start"]).strftime("%Y-%m-%d")
                props = {
                    "segment_id": int(seg_id),
                    "points": int(len(coords)),
                    "sample_rate": int(sample_rate),
                    "time_gap_min": int(time_gap_minutes),
                    "t_start": pd.Timestamp(seg["t_start"]).isoformat(),
                    "t_end": pd.Timestamp(seg["t_end"]).isoformat(),
                    "day": day_str,
                }
                
                # Use segment's platform_id (from data), or fallback to function parameter
                seg_platform_id = seg.get("platform_id") or platform_id
                if seg_platform_id:
                    props["platform_id"] = str(seg_platform_id)
                if lon_grid is not None:
                    props['lon_grid'] = int(lon_grid)
                if lat_grid is not None:
                    props['lat_grid'] = int(lat_grid)
                
                # Add averaged measurements to segment properties
                if seg.get("measurements"):
                    for key, value in seg["measurements"].items():
                        # Round to reasonable precision to reduce file size
                        if isinstance(value, float):
                            props[key] = round(value, 3)
                        else:
                            props[key] = value
                
                feat = {
                    "type": "Feature",
                    "properties": props,
                    "geometry": {"type": "LineString", "coordinates": coords},
                }
                out.write(json.dumps(feat) + "\n")
                count_feats += 1
                seg_id += 1
        
        # Add day markers: start and end point per UTC day (per platform)
        for combined_key, st in sorted(day_stats.items()):
            day_key = st.get("day", combined_key.split(":")[0] if ":" in combined_key else combined_key)
            marker_platform_id = st.get("platform_id") or platform_id
            
            for kind, coord, t_iso in (
                ("start", st["start_coord"], pd.Timestamp(st["t_start"]).isoformat()),
                ("end", st["end_coord"], pd.Timestamp(st["t_end"]).isoformat()),
            ):
                props = {
                    "day": day_key,
                    "kind": kind,
                    "t": t_iso,
                }
                if marker_platform_id:
                    props["platform_id"] = str(marker_platform_id)
                
                feat = {
                    "type": "Feature",
                    "properties": props,
                    "geometry": {"type": "Point", "coordinates": [float(coord[0]), float(coord[1])]},
                }
                out.write(json.dumps(feat) + "\n")
                count_feats += 1
    
    return count_feats


def generate_pmtiles_from_geoparquet(
    geoparquet_root: str | os.PathLike,
    pmtiles_path: str | os.PathLike,
    *,
    minzoom: int = 0,
    maxzoom: int = 10,
    layer_name: str = "track",
    select_columns: Iterable[str] | None = None,
    sample_rate: int = 5,
    time_gap_minutes: int = 60,
    platform_id: str | None = None,
    tippecanoe_opts: str | None = None,
    keep_intermediate_files: bool = False,
    use_tippecanoe: bool = True,
    include_measurements: bool = True,
    measurement_columns: list[str] | None = None,
    exclude_patterns: list[str] | None = None,
) -> Path:
    """
    Generate a PMTiles file from a partitioned GeoParquet dataset.
    
    This function creates track segments with time-based splitting and day markers
    for efficient web map visualization.
    
    Args:
        geoparquet_root: Root directory of partitioned GeoParquet dataset
        pmtiles_path: Output path for PMTiles file
        minzoom: Minimum zoom level (0-15)
        maxzoom: Maximum zoom level (0-15)
        layer_name: Layer name in vector tiles
        select_columns: Columns to include (deprecated, for ogr2ogr compatibility)
        sample_rate: Take every Nth point (1=all, 5=every 5th point)
        time_gap_minutes: Minutes of gap to split track segments
        platform_id: Platform/cruise identifier to include in properties
        tippecanoe_opts: Custom tippecanoe options (overrides defaults)
        keep_intermediate_files: Keep NDJSON and MBTiles files for debugging
        use_tippecanoe: Use tippecanoe (True, recommended) or ogr2ogr (False, basic)
        include_measurements: Include oceanographic measurements in tiles
        measurement_columns: Specific columns to include (None = auto-discover from data)
        exclude_patterns: Regex patterns to exclude when auto-discovering (None = use defaults).
            Default excludes: _STDDEV, _STD, _MIN, _MAX, _PEAK suffixes and wind components.
            Pass empty list [] to include all columns.
        
    Returns:
        Path to generated PMTiles file
        
    Raises:
        MissingDependencyError: If required CLI tools not found
    """
    _require_cli("pmtiles")
    
    geoparquet_root = Path(geoparquet_root)
    pmtiles_path = Path(pmtiles_path)
    pmtiles_path.parent.mkdir(parents=True, exist_ok=True)
    
    if use_tippecanoe:
        _require_cli("tippecanoe")
        return _generate_with_tippecanoe(
            geoparquet_root=geoparquet_root,
            pmtiles_path=pmtiles_path,
            minzoom=minzoom,
            maxzoom=maxzoom,
            layer_name=layer_name,
            sample_rate=sample_rate,
            time_gap_minutes=time_gap_minutes,
            platform_id=platform_id,
            tippecanoe_opts=tippecanoe_opts,
            keep_intermediate_files=keep_intermediate_files,
            include_measurements=include_measurements,
            measurement_columns=measurement_columns,
            exclude_patterns=exclude_patterns,
        )
    else:
        # Fallback to ogr2ogr (basic conversion, no segments)
        _require_cli("ogr2ogr")
        return _generate_with_ogr2ogr(
            geoparquet_root=geoparquet_root,
            pmtiles_path=pmtiles_path,
            minzoom=minzoom,
            maxzoom=maxzoom,
            layer_name=layer_name,
            select_columns=select_columns,
        )


def _generate_with_tippecanoe(
    geoparquet_root: Path,
    pmtiles_path: Path,
    *,
    minzoom: int,
    maxzoom: int,
    layer_name: str,
    sample_rate: int,
    time_gap_minutes: int,
    platform_id: str | None,
    tippecanoe_opts: str | None,
    keep_intermediate_files: bool,
    include_measurements: bool = True,
    measurement_columns: list[str] | None = None,
    exclude_patterns: list[str] | None = None,
) -> Path:
    """Generate PMTiles using tippecanoe for better control over segments."""
    
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)
        
        # Step 1: Build NDJSON with segments and day markers
        ndjson_path = tmpdir_path / "track.ndjson"
        print(f"Building NDJSON with segments from {geoparquet_root}...")
        feat_count = _build_ndjson_from_geoparquet(
            geoparquet_root=geoparquet_root,
            output_path=ndjson_path,
            sample_rate=sample_rate,
            time_gap_minutes=time_gap_minutes,
            platform_id=platform_id,
            include_measurements=include_measurements,
            measurement_columns=measurement_columns,
            exclude_patterns=exclude_patterns,
        )
        print(f"Created {feat_count} features (segments + day markers)")
        
        # Step 2: Run tippecanoe to build MBTiles
        mbtiles_path = tmpdir_path / "track.mbtiles"
        
        if tippecanoe_opts:
            extra_opts = tippecanoe_opts.split()
        else:
            # Default options optimized for track data
            extra_opts = [
                "-zg",  # Auto-calculate zoom levels
                "--drop-densest-as-needed",  # Smart simplification
                "--no-tile-size-limit",  # Allow large tiles for detailed tracks
                "--read-parallel",  # Parallel reading
            ]
        
        cmd = [
            "tippecanoe",
            "-o", str(mbtiles_path),
            "-l", layer_name,
            "-Z", str(minzoom),
            "-z", str(maxzoom),
        ] + extra_opts + [str(ndjson_path)]
        
        print(f"Running tippecanoe: {' '.join(cmd)}")
        subprocess.run(cmd, check=True)
        
        # Step 3: Convert MBTiles to PMTiles
        pmtiles_tmp = pmtiles_path.with_suffix(".pmtiles.tmp")
        cmd = ["pmtiles", "convert", str(mbtiles_path), str(pmtiles_tmp)]
        print(f"Running pmtiles convert: {' '.join(cmd)}")
        subprocess.run(cmd, check=True)
        
        # Move to final location
        pmtiles_tmp.replace(pmtiles_path)
        
        # Optionally keep intermediate files
        if keep_intermediate_files:
            final_ndjson = pmtiles_path.with_suffix(".ndjson")
            final_mbtiles = pmtiles_path.with_suffix(".mbtiles")
            shutil.copy(ndjson_path, final_ndjson)
            shutil.copy(mbtiles_path, final_mbtiles)
            print(f"Kept intermediate files: {final_ndjson}, {final_mbtiles}")
    
    return pmtiles_path


def _generate_with_ogr2ogr(
    geoparquet_root: Path,
    pmtiles_path: Path,
    *,
    minzoom: int,
    maxzoom: int,
    layer_name: str,
    select_columns: Iterable[str] | None,
) -> Path:
    """
    Generate PMTiles using ogr2ogr (basic, no segments).
    
    This is a fallback method that converts raw points without segmentation.
    Use tippecanoe method for production.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        mbtiles_path = Path(tmpdir) / (pmtiles_path.stem + ".mbtiles")

        cmd = [
            "ogr2ogr",
            "-f",
            "MBTILES",
            str(mbtiles_path),
            str(geoparquet_root),
            "-dsco",
            f"MINZOOM={minzoom}",
            "-dsco",
            f"MAXZOOM={maxzoom}",
            "-dsco",
            "TILE_FORMAT=MVT",
            "-dsco",
            f"NAME={layer_name}",
            "-nln",
            layer_name,
        ]

        if select_columns:
            cmd.extend(["-select", ",".join(select_columns)])

        subprocess.run(cmd, check=True)

        pmtiles_tmp = pmtiles_path.with_suffix(".pmtiles.tmp")
        convert_cmd = [
            "pmtiles",
            "convert",
            str(mbtiles_path),
            str(pmtiles_tmp),
        ]
        subprocess.run(convert_cmd, check=True)

        pmtiles_tmp.replace(pmtiles_path)

    return pmtiles_path


def upload_pmtiles_to_azure(
    pmtiles_path: str | os.PathLike,
    *,
    container_name: str,
    blob_name: str,
) -> None:
    """Upload a PMTiles file to Azure Blob Storage using storage helper."""
    upload_to_azure_blob(
        file_path=str(pmtiles_path),
        container_name=container_name,
        blob_name=blob_name,
    )
