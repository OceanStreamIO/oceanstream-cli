import os
import sys
import json
import tempfile
import subprocess
import fsspec
import pandas as pd
import pyarrow.parquet as pq
import geopandas as gpd

from adlfs import AzureBlobFileSystem
from shapely.geometry import Point
from saildrone.store import get_azure_blob_filesystem, open_geo_parquet


def consolidate_csv_to_geoparquet_partitioned(folder_path, output_path, storage_type='local'):
    """
    Consolidates multiple CSV files in a folder into partitioned GeoParquet files.

    Parameters:
    folder_path (str): Path to the folder containing CSV files.
    output_directory (str): Path where the partitioned GeoParquet files will be saved.
    """
    combined_data = []

    for file_name in os.listdir(folder_path):
        if file_name.endswith('.csv'):
            file_path = os.path.join(folder_path, file_name)

            # Read the CSV file into a DataFrame
            df = pd.read_csv(file_path)
            df['time'] = pd.to_datetime(df.iloc[:, 0])  # Convert the first column to datetime
            df = df.rename(columns={df.columns[1]: 'latitude', df.columns[2]: 'longitude'})  # Rename columns
            df = df.drop(columns=[df.columns[0]])  # Drop the original time column

            df = df.dropna(subset=['latitude', 'longitude'])

            # Ensure latitude and longitude are numeric
            df['latitude'] = pd.to_numeric(df['latitude'], errors='coerce')
            df['longitude'] = pd.to_numeric(df['longitude'], errors='coerce')

            # Create GeoDataFrame with geometry based on latitude and longitude
            df['geometry'] = df.apply(lambda row: Point(row['longitude'], row['latitude']), axis=1)
            gdf = gpd.GeoDataFrame(df, geometry='geometry', crs='EPSG:4326')
            combined_data.append(gdf)

    combined_gdf = pd.concat(combined_data, ignore_index=True)
    combined_gdf = combined_gdf.dropna(subset=['longitude', 'latitude'])

    save_to_partitioned_geoparquet(combined_gdf, output_path, storage_type)


def save_to_partitioned_geoparquet(gdf: gpd.GeoDataFrame, output_path: str, storage_type='local', grid_size=1.0):
    # Calculate grid indices for partitioning
    if storage_type == 'azure':
        fs = get_azure_blob_filesystem()
    else:
        fs = fsspec.filesystem('file')

    gdf['lon_grid'] = (gdf['longitude'] // grid_size).astype('int32')
    gdf['lat_grid'] = (gdf['latitude'] // grid_size).astype('int32')

    # Group by partitioning columns and write each group to a separate Parquet file
    grouped = gdf.groupby(['lon_grid', 'lat_grid'])
    metadata_records = []

    for (lon_grid, lat_grid), group in grouped:
        if storage_type == 'azure':
            partition_path = f"{output_path}/lon_grid={lon_grid}/lat_grid={lat_grid}/data.parquet"
        else:
            partition_path = os.path.join(output_path, f'lon_grid={lon_grid}', f'lat_grid={lat_grid}', 'data.parquet')
            local_directory = os.path.dirname(partition_path)
            os.makedirs(local_directory, exist_ok=True)

        # Drop partitioning columns before saving to Parquet
        group_to_save = group.drop(columns=['lon_grid', 'lat_grid'])
        with fs.open(partition_path, 'wb') as f:
            group_to_save.to_parquet(f, index=False)

        # Save the partitioned data with fastparquet
        start_time = group['time'].min()
        end_time = group['time'].max()
        min_lat = group['latitude'].min()
        max_lat = group['latitude'].max()
        min_lon = group['longitude'].min()
        max_lon = group['longitude'].max()

        metadata_records.append({
            'partition_path': partition_path,
            'start_time': start_time,
            'end_time': end_time,
            'min_lat': min_lat,
            'max_lat': max_lat,
            'min_lon': min_lon,
            'max_lon': max_lon,
            'num_records': len(group),
            'lon_grid': int(lon_grid),
            'lat_grid': int(lat_grid),
        })

    metadata_df = pd.DataFrame(metadata_records)
    metadata_path = os.path.join(output_path, 'metadata.parquet')
    with fs.open(metadata_path, 'wb') as f:
        metadata_df.to_parquet(f, index=False)


def query_location_points_between_timestamps(file_start_time, file_end_time, geoparquet_path=None,
                                             container_name=None, survey_id=None):
    """
    Query location points between two timestamps by loading only relevant partitions based on metadata.

    Parameters:
    geoparquet_path (str): Path to the directory containing partitioned GeoParquet files and metadata.
    file_start_time (str or pd.Timestamp): Start timestamp for the query range.
    file_end_time (str or pd.Timestamp): End timestamp for the query range.
    survey_id (str, optional): Identifier for the survey in Azure Blob Storage.
    container_name (str, optional): Container name in Azure Blob Storage.

    Returns:
    GeoDataFrame: Combined GeoDataFrame with location points within the specified timestamp range.
    """
    # Convert input timestamps to pd.Timestamp if they are strings
    file_start_time = pd.to_datetime(file_start_time)
    file_end_time = pd.to_datetime(file_end_time)

    # Load the metadata file to identify relevant partitions
    if geoparquet_path is not None:
        metadata_path = f"{geoparquet_path}/metadata.parquet"
        metadata_df = pd.read_parquet(metadata_path)
    elif container_name is not None:
        metadata_df = open_geo_parquet('metadata.parquet', container_name=container_name, survey_id=survey_id,
                                       has_geometry=False)
    else:
        raise ValueError("Either 'geoparquet_path' or 'container_name' must be provided.")

    # Filter metadata to find partitions overlapping with the query timestamp range
    relevant_partitions = metadata_df[
        (metadata_df['end_time'] >= file_start_time) &
        (metadata_df['start_time'] <= file_end_time)
        ]

    # Check if any partitions match the time range
    if relevant_partitions.empty:
        return gpd.GeoDataFrame()

    # Load and filter relevant partitions
    combined_data = []

    for _, row in relevant_partitions.iterrows():
        partition_path = row['partition_path']

        try:
            if geoparquet_path is not None:
                partition_gdf = gpd.read_parquet(partition_path)
            else:
                partition_gdf = open_geo_parquet(partition_path)

            # Filter the partition to only include records within the specified timestamp range
            filtered_gdf = partition_gdf[
                (partition_gdf['time'] >= file_start_time) &
                (partition_gdf['time'] <= file_end_time)
                ]

            combined_data.append(filtered_gdf)
        except Exception as e:
            print(f"Error loading partition {partition_path}: {e}")
            continue

    # Combine all filtered GeoDataFrames into a single GeoDataFrame
    result_gdf = gpd.GeoDataFrame(pd.concat(combined_data, ignore_index=True))

    return result_gdf


def extract_start_end_coordinates(result_gdf):
    """
    Extracts the starting and ending latitude and longitude from a GeoDataFrame
    based on the earliest and latest timestamps.

    Parameters:
    result_gdf (GeoDataFrame): The GeoDataFrame returned by query_location_points_between_timestamps.

    Returns:
    dict: A dictionary containing start and end lat/lon coordinates.
    """
    # Ensure the GeoDataFrame is not empty
    if result_gdf.empty:
        raise ValueError("The provided GeoDataFrame is empty.")

    # Sort by 'time' to get the start and end coordinates
    sorted_gdf = result_gdf.sort_values(by='time')

    # Extract coordinates for the start (first row) and end (last row)
    file_start_lat = sorted_gdf.iloc[0]['latitude']
    file_start_lon = sorted_gdf.iloc[0]['longitude']
    file_end_lat = sorted_gdf.iloc[-1]['latitude']
    file_end_lon = sorted_gdf.iloc[-1]['longitude']

    # Return the coordinates in a dictionary
    return {
        'file_start_lat': file_start_lat,
        'file_start_lon': file_start_lon,
        'file_end_lat': file_end_lat,
        'file_end_lon': file_end_lon
    }


def read_metadata(container: str, metadata_path: str, storage_type='azure') -> pd.DataFrame:
    path = f"{container}/{metadata_path}"

    if storage_type == 'azure':
        fs = get_azure_blob_filesystem()
    else:
        fs = fsspec.filesystem('file')

    with fs.open(path, 'rb') as f:
        df = pd.read_parquet(f)

    if 'partition_path' not in df.columns:
        raise RuntimeError('metadata.parquet missing partition_path column')

    return df


def iter_partition_points(fs: AzureBlobFileSystem, partition_path: str, columns=None, sample_rate=5):
    # partition_path may include container already; if not, caller should prefix
    with fs.open(partition_path, 'rb') as f:
        pf = pq.ParquetFile(f)
        for rg in range(pf.num_row_groups):
            table = pf.read_row_group(rg, columns=columns)
            df = table.to_pandas(types_mapper=None)
            # normalize columns
            cols = {c.lower(): c for c in df.columns}
            lon = cols.get('longitude') or cols.get('lon') or cols.get('x')
            lat = cols.get('latitude') or cols.get('lat') or cols.get('y')
            tim = cols.get('time') or cols.get('timestamp') or cols.get('datetime') or cols.get('event_time')
            if not (lon and lat and tim):
                continue
            sdf = df[[lon, lat, tim]].rename(columns={lon: 'longitude', lat: 'latitude', tim: 'time'})
            # ensure types
            sdf['time'] = pd.to_datetime(sdf['time'], errors='coerce')
            sdf = sdf.dropna(subset=['longitude', 'latitude', 'time'])
            sdf = sdf.sort_values('time')
            if sample_rate and sample_rate > 1:
                sdf = sdf.iloc[::sample_rate]
            for row in sdf.itertuples(index=False):
                yield float(row.longitude), float(row.latitude), pd.Timestamp(row.time).to_pydatetime()


def segments_from_points(points, time_gap_min=60):
    import datetime as dt
    segments = []
    current = []
    gap = dt.timedelta(minutes=max(0, time_gap_min))
    last_t = None
    for lon, lat, t in points:
        if not isinstance(t, (dt.datetime,)):
            continue
        if last_t is not None and (t - last_t) > gap:
            if len(current) > 1:
                coords = [(float(x), float(y)) for x, y, _ in current]
                segments.append({
                    "coords": coords,
                    "t_start": current[0][2],
                    "t_end": current[-1][2],
                })
            current = []
        current.append((float(lon), float(lat), t))
        last_t = t
    if len(current) > 1:
        coords = [(float(x), float(y)) for x, y, _ in current]
        segments.append({
            "coords": coords,
            "t_start": current[0][2],
            "t_end": current[-1][2],
        })
    return segments


def write_ndjson(meta_df: pd.DataFrame, container: str, sample_rate: int, time_gap_min: int,
                 storage_type='azure', cruise_id: str | None = None) -> str:
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix='.ndjson')
    tmp_path = tmp.name
    tmp.close()
    count_feats = 0

    if storage_type == 'azure':
        fs = get_azure_blob_filesystem()
    else:
        fs = fsspec.filesystem('file')

    seg_id = 0
    # Collect per-day stats to emit lightweight markers (start/end) per UTC day
    day_stats = {}
    with open(tmp_path, 'w', encoding='utf-8') as out:
        for _, row in meta_df.iterrows():
            rel = str(row['partition_path'])
            # Ensure container prefix present
            if not rel.startswith(container + '/'):
                part_path = f"{container}/{rel}"
            else:
                part_path = rel
            try:
                points = list(iter_partition_points(fs, part_path, columns=['longitude', 'latitude', 'time'],
                                                    sample_rate=sample_rate))
            except Exception as e:
                print(f"Warning: failed to read {part_path}: {e}", file=sys.stderr)
                continue
            # Update per-day stats from raw points
            for lon, lat, t in points:
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
            segs = segments_from_points(points, time_gap_min=time_gap_min)
            for seg in segs:
                coords = seg["coords"]
                if len(coords) < 2:
                    continue
                # Parse grids from row or path
                lon_grid = row.get('lon_grid') if isinstance(row, pd.Series) else None
                lat_grid = row.get('lat_grid') if isinstance(row, pd.Series) else None
                if pd.isna(lon_grid) if 'lon_grid' in row else True:
                    # fallback: parse from partition path like .../lon_grid=X/lat_grid=Y/...
                    try:
                        parts = rel.split('/')
                        for p in parts:
                            if p.startswith('lon_grid='):
                                lon_grid = int(p.split('=', 1)[1])
                            elif p.startswith('lat_grid='):
                                lat_grid = int(p.split('=', 1)[1])
                    except Exception:
                        lon_grid = lon_grid or None
                        lat_grid = lat_grid or None
                # Build properties (add day tag from segment start time)
                day_str = pd.Timestamp(seg["t_start"]).strftime("%Y-%m-%d")
                props = {
                    "segment_id": int(seg_id),
                    "points": int(len(coords)),
                    "sample_rate": int(sample_rate),
                    "time_gap_min": int(time_gap_min),
                    "t_start": pd.Timestamp(seg["t_start"]).isoformat(),
                    "t_end": pd.Timestamp(seg["t_end"]).isoformat(),
                    "day": day_str,
                }
                if cruise_id:
                    props["cruise_id"] = str(cruise_id)
                if lon_grid is not None:
                    props['lon_grid'] = int(lon_grid)
                if lat_grid is not None:
                    props['lat_grid'] = int(lat_grid)
                feat = {
                    "type": "Feature",
                    "properties": props,
                    "geometry": {"type": "LineString", "coordinates": coords},
                }
                out.write(json.dumps(feat) + "\n")
                count_feats += 1
                seg_id += 1
        # Append minimal daily markers: start and end point per UTC day
        for day_key, st in sorted(day_stats.items()):
            for kind, coord, t_iso in (
                ("start", st["start_coord"], pd.Timestamp(st["t_start"]).isoformat()),
                ("end", st["end_coord"], pd.Timestamp(st["t_end"]).isoformat()),
            ):
                props = {
                    "day": day_key,
                    "kind": kind,  # 'start' or 'end'
                    "t": t_iso,
                }
                if cruise_id:
                    props["cruise_id"] = str(cruise_id)
                feat = {
                    "type": "Feature",
                    "properties": props,
                    "geometry": {"type": "Point", "coordinates": [float(coord[0]), float(coord[1])]},
                }
                out.write(json.dumps(feat) + "\n")
                count_feats += 1
    print(f"Wrote NDJSON features: {count_feats} -> {tmp_path}")
    return tmp_path


def check_cli(name):
    try:
        subprocess.run([name, '--help'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        return True
    except FileNotFoundError:
        return False


def run_tippecanoe(ndjson_path: str, mbtiles_path: str, extra_opts: str = ''):
    cmd = ['tippecanoe', '-o', mbtiles_path, '-l', 'track', '--read-parallel']
    if extra_opts:
        cmd += extra_opts.split()
    cmd.append(ndjson_path)
    print('Running:', ' '.join(cmd))
    subprocess.run(cmd, check=True)


def run_pmtiles_convert(mbtiles_path: str, pmtiles_path: str):
    cmd = ['pmtiles', 'convert', mbtiles_path, pmtiles_path]
    print('Running:', ' '.join(cmd))
    subprocess.run(cmd, check=True)


def ensure_container(fs: AzureBlobFileSystem, container: str):
    """Create the container if it does not exist (idempotent)."""
    try:
        # adlfs treats top-level path as container; mkdir on container creates it.
        if container and container not in ("/", "."):
            fs.mkdir(container)
    except Exception:
        # ignore errors (likely already exists)
        pass


def build_and_upload_track_pmtiles(src_container, dst_container: str, cruise_id: str, sample_rate: int, time_gap_min: int) -> str:
    """
    Build PMTiles of the GPS track for a cruise and upload to an Azure Blob container.

    Parameters:
    - cruise_id: Cruise identifier used to locate metadata and to name outputs
    - sample_rate: Subsample rate for points (take every Nth point)
    - time_gap_min: Minutes of gap to split segments

    Environment variables (optional):
    - METADATA_CONTAINER: source container for metadata.parquet (default 'gpsdata')
    - METADATA_PATH: path to metadata.parquet inside the container (default '{cruise_id}/metadata.parquet')
    - OUTPUT_CONTAINER: destination container for PMTiles (default 'gpstiles')
    - OUTPUT_BLOB: destination blob path (default '{cruise_id}/track.pmtiles')
    - TIPPECANOE_OPTS: extra options for tippecanoe

    Returns:
    - Azure blob path 'container/blobpath' where the PMTiles was uploaded.
    """

    metadata_path = os.getenv('METADATA_PATH', f'{cruise_id}/metadata.parquet')
    dst_blob = os.getenv('OUTPUT_BLOB', f'{cruise_id}/track.pmtiles')
    tippecanoe_opts = os.getenv('TIPPECANOE_OPTS', '-zg --drop-densest-as-needed --no-tile-size-limit')

    meta_df = read_metadata(src_container, metadata_path)
    ndjson_path = write_ndjson(meta_df, src_container, sample_rate, time_gap_min, cruise_id=cruise_id)

    have_tippecanoe = check_cli('tippecanoe')
    have_pmtiles = check_cli('pmtiles')
    if not have_tippecanoe:
        raise RuntimeError("tippecanoe is not installed; cannot build tiles")

    mb_fd, mbtiles_path = tempfile.mkstemp(suffix='.mbtiles')
    os.close(mb_fd)
    pm_fd, pmtiles_path = tempfile.mkstemp(suffix='.pmtiles')
    os.close(pm_fd)

    try:
        run_tippecanoe(ndjson_path, mbtiles_path, tippecanoe_opts)
        if not have_pmtiles:
            raise RuntimeError("pmtiles CLI is not installed. Install from https://github.com/protomaps/go-pmtiles")
        run_pmtiles_convert(mbtiles_path, pmtiles_path)

        # Upload to Azure container
        fs = get_azure_blob_filesystem()
        ensure_container(fs, dst_container)
        dest_path = f"{dst_container}/{dst_blob}"
        # Ensure parent 'directories' exist (virtual, but adlfs will handle writes)
        with fs.open(dest_path, 'wb') as out_f, open(pmtiles_path, 'rb') as in_f:
            out_f.write(in_f.read())

        return dest_path
    finally:
        for p in (ndjson_path, mbtiles_path, pmtiles_path):
            try:
                os.remove(p)
            except Exception:
                pass
