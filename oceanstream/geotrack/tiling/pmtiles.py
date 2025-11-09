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


# Auto-selected important oceanographic measurements
DEFAULT_MEASUREMENT_COLUMNS = [
    # Temperature
    'TEMP_AIR_MEAN',
    'TEMP_SBE37_MEAN',
    'TEMP_DEPTH_HALFMETER_MEAN',
    # Salinity
    'SAL_SBE37_MEAN',
    # Dissolved Oxygen
    'O2_CONC_SBE37_MEAN',
    'O2_SAT_SBE37_MEAN',
    # Chlorophyll
    'CHLOR_WETLABS_MEAN',
    # Wind
    'WIND_SPEED_MEAN',
    'WIND_FROM_MEAN',
    # Waves
    'WAVE_SIGNIFICANT_HEIGHT',
    'WAVE_DOMINANT_PERIOD',
    # Pressure
    'BARO_PRES_MEAN',
    # Additional
    'RH_MEAN',  # Relative humidity
    'PAR_AIR_MEAN',  # Photosynthetically active radiation
]


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
) -> Iterable[tuple[float, float, dt.datetime, dict | None]]:
    """
    Iterate over (longitude, latitude, timestamp, measurements) from a GeoParquet partition file.
    
    Args:
        partition_path: Path to parquet file
        sample_rate: Take every Nth point (1 = all points)
        measurement_columns: Measurement columns to include (None = only coords and time)
        
    Yields:
        Tuples of (lon, lat, timestamp, measurements_dict)
    """
    # Build columns to read
    base_columns = ['longitude', 'latitude', 'time']
    read_columns = base_columns.copy()
    if measurement_columns:
        read_columns.extend(measurement_columns)
    
    with open(partition_path, 'rb') as f:
        pf = pq.ParquetFile(f)
        
        # Check which requested measurement columns actually exist
        available_cols = set(pf.schema.names)
        available_measurements = [c for c in measurement_columns or [] if c in available_cols]
        actual_read_columns = base_columns + available_measurements if measurement_columns else base_columns
        
        for rg in range(pf.num_row_groups):
            table = pf.read_row_group(rg, columns=actual_read_columns)
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
                
                yield lon, lat, t, measurements


def _segments_from_points(
    points: list[tuple[float, float, dt.datetime, dict | None]],
    time_gap_minutes: int = 60,
) -> list[dict]:
    """
    Split points into segments based on time gaps.
    
    Args:
        points: List of (lon, lat, timestamp, measurements) tuples
        time_gap_minutes: Minutes of gap to split segments
        
    Returns:
        List of segment dicts with coords, t_start, t_end, measurements_avg
    """
    segments = []
    current = []
    gap = dt.timedelta(minutes=max(0, time_gap_minutes))
    last_t = None
    
    for lon, lat, t, measurements in points:
        if not isinstance(t, dt.datetime):
            continue
        
        # Start new segment if gap is too large
        if last_t is not None and (t - last_t) > gap:
            if len(current) > 1:
                coords = [(float(x), float(y)) for x, y, _, _ in current]
                
                # Compute average measurements for segment
                avg_measurements = None
                if any(m for _, _, _, m in current if m):
                    avg_measurements = {}
                    measurement_keys = set()
                    for _, _, _, m in current:
                        if m:
                            measurement_keys.update(m.keys())
                    
                    for key in measurement_keys:
                        values = [m[key] for _, _, _, m in current if m and key in m and isinstance(m[key], (int, float))]
                        if values:
                            avg_measurements[key] = sum(values) / len(values)
                
                segments.append({
                    "coords": coords,
                    "t_start": current[0][2],
                    "t_end": current[-1][2],
                    "measurements": avg_measurements,
                })
            current = []
        
        current.append((float(lon), float(lat), t, measurements))
        last_t = t
    
    # Add final segment
    if len(current) > 1:
        coords = [(float(x), float(y)) for x, y, _, _ in current]
        
        # Compute average measurements for segment
        avg_measurements = None
        if any(m for _, _, _, m in current if m):
            avg_measurements = {}
            measurement_keys = set()
            for _, _, _, m in current:
                if m:
                    measurement_keys.update(m.keys())
            
            for key in measurement_keys:
                values = [m[key] for _, _, _, m in current if m and key in m and isinstance(m[key], (int, float))]
                if values:
                    avg_measurements[key] = sum(values) / len(values)
        
        segments.append({
            "coords": coords,
            "t_start": current[0][2],
            "t_end": current[-1][2],
            "measurements": avg_measurements,
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
        measurement_columns: Specific columns to include (None = auto-select)
        
    Returns:
        Number of features written
    """
    # Determine which measurements to include
    actual_measurements = None
    if include_measurements:
        actual_measurements = measurement_columns if measurement_columns else DEFAULT_MEASUREMENT_COLUMNS.copy()
    
    # Read metadata to get partition list
    metadata_path = geoparquet_root / "metadata.parquet"
    if not metadata_path.exists():
        # Fallback: scan for parquet files
        parquet_files = list(geoparquet_root.rglob("*.parquet"))
        if not parquet_files:
            raise ValueError(f"No parquet files found in {geoparquet_root}")
        meta_df = pd.DataFrame({'partition_path': [str(p) for p in parquet_files]})
    else:
        meta_df = pd.read_parquet(metadata_path)
    
    seg_id = 0
    count_feats = 0
    day_stats = {}  # Track start/end per UTC day
    
    with open(output_path, 'w', encoding='utf-8') as out:
        for _, row in meta_df.iterrows():
            partition_path = Path(str(row['partition_path']))
            
            # Make path absolute if relative
            if not partition_path.is_absolute():
                partition_path = geoparquet_root / partition_path
            
            if not partition_path.exists():
                print(f"Warning: partition not found: {partition_path}", file=sys.stderr)
                continue
            
            try:
                points = list(_iter_partition_points(
                    partition_path,
                    sample_rate=sample_rate,
                    measurement_columns=actual_measurements,
                ))
            except Exception as e:
                print(f"Warning: failed to read {partition_path}: {e}", file=sys.stderr)
                continue
            
            if not points:
                continue
            
            # Update per-day stats
            for lon, lat, t, _ in points:
                t_ts = pd.Timestamp(t)
                day_key = t_ts.strftime("%Y-%m-%d")
                st = day_stats.get(day_key)
                if st is None:
                    day_stats[day_key] = {
                        "t_start": t_ts,
                        "t_end": t_ts,
                        "start_coord": (float(lon), float(lat)),
                        "end_coord": (float(lon), float(lat)),
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
                
                if platform_id:
                    props["platform_id"] = str(platform_id)
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
        
        # Add day markers: start and end point per UTC day
        for day_key, st in sorted(day_stats.items()):
            for kind, coord, t_iso in (
                ("start", st["start_coord"], pd.Timestamp(st["t_start"]).isoformat()),
                ("end", st["end_coord"], pd.Timestamp(st["t_end"]).isoformat()),
            ):
                props = {
                    "day": day_key,
                    "kind": kind,
                    "t": t_iso,
                }
                if platform_id:
                    props["platform_id"] = str(platform_id)
                
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
        measurement_columns: Specific columns to include (None = auto-select important ones)
        
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
