#!/usr/bin/env python3
"""Zarr-v3-native pipeline: raw EK80 → EchoData → combine → Sv → denoise → MVBS/NASC → echograms.

Downloads raw .raw files from an Azure File Share, converts via echopype
(0.11.x + zarr 3), combines EchoData per day, computes Sv with depth, then
feeds into the standard post-processing stages (denoise → MVBS → NASC →
echograms → PMTiles).

Architecture (EchoData-first):
  Stage 1:  Discover raw files on Azure File Share
  Stage 2:  Download (parallel ThreadPool) + convert to EchoData Zarr
  Stage 3:  Combine EchoData per day+category (lazy via echopype)
  Stage 4:  Compute Sv + add_depth + merge GPS → save Sv Zarr
  Stage 5:  Denoise (background noise, impulse, transient, attenuation)
  Stage 6:  Seabed masking (optional)
  Stage 7:  MVBS (echopype compute_MVBS)
  Stage 8:  NASC (echopype compute_NASC)
  Stage 9:  Echograms
  Stage 10: PMTiles + COG
  Stage 11: Campaign aggregation

Usage:
    # Local test — single day (Jun 25, 24 files ≈ 2.9 GB)
    python process_from_raw.py --local-test

    # Custom date range
    python process_from_raw.py --local-test \\
        --start-date 2023-06-25 --end-date 2023-06-25

    # With custom calibration and output container
    python process_from_raw.py --local-test \\
        --calibration-file /path/to/calibration_values.xlsx \\
        --output-container my-test-run

Requires:
    pip install -e ".[echodata,echodata-viz]"   # echopype 0.11.x + zarr 3
    pip install azure-storage-file-share         # file share access
"""

from __future__ import annotations

import argparse
import gc
import logging
import os
import re
import sys
import tempfile
import time
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
    force=True,
)
logger = logging.getLogger(__name__)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(line_buffering=True)

# Suppress verbose Azure SDK logging
logging.getLogger("azure").setLevel(logging.WARNING)
logging.getLogger("azure.core.pipeline.policies.http_logging_policy").setLevel(
    logging.WARNING
)
logging.getLogger("adlfs").setLevel(logging.WARNING)

# Suppress verbose Dask / distributed logging — these produce millions of
# lines at INFO level (task events, heartbeats, memory status, serialisation)
# which overflow SSH sessions and crash the remote IDE.
for _noisy in (
    "distributed", "distributed.worker", "distributed.scheduler",
    "distributed.nanny", "distributed.comm", "distributed.batched",
    "dask", "bokeh", "tornado.access",
    "echopype", "fsspec", "zarr",
):
    logging.getLogger(_noisy).setLevel(logging.WARNING)

import warnings
warnings.filterwarnings(
    "ignore",
    message="Running on a single-machine scheduler",
    category=UserWarning,
)

from config import PipelineConfig

# Import post-processing stages from the existing pipeline
from process_campaign import (
    _release_memory,
    group_files_by_day,
    run_denoising,
    run_echogram_generation,
    run_mvbs_computation,
    run_nasc_computation,
    run_pruning,
    run_seabed_masking,
    run_tiles_and_cog,
    run_campaign_aggregation,
    setup_dask_client,
)


# ═══════════════════════════════════════════════════════════════════════════
# STAGE 1: Discover raw files from Azure File Share
# ═══════════════════════════════════════════════════════════════════════════

# Regex to extract date from raw filenames like SD_TPOS2023_v03-Phase0-D20230625-T005958-0.raw
_RAW_DATE_RE = re.compile(r"D(\d{4})(\d{2})(\d{2})")
_RAW_TIME_RE = re.compile(r"T(\d{2})(\d{2})(\d{2})")


def _parse_raw_datetime(filename: str) -> Optional[datetime]:
    """Extract datetime from a raw EK80 filename."""
    dm = _RAW_DATE_RE.search(filename)
    if not dm:
        return None
    y, m, d = int(dm.group(1)), int(dm.group(2)), int(dm.group(3))
    tm = _RAW_TIME_RE.search(filename)
    if tm:
        hh, mm, ss = int(tm.group(1)), int(tm.group(2)), int(tm.group(3))
    else:
        hh = mm = ss = 0
    return datetime(y, m, d, hh, mm, ss)


def discover_raw_files(cfg: PipelineConfig) -> list[tuple[str, dict]]:
    """List raw EK80 files from Azure File Share, filtered by date range.

    Returns a list of (filename, record_dict) tuples compatible with
    group_files_by_day() and the downstream processing stages.
    """
    from azure.storage.fileshare import ShareServiceClient

    conn_str = os.environ.get(
        "AZURE_STORAGE_CONNECTION_STRING",
        os.environ.get("AZ_SOURCE_CONNECTION_STRING", ""),
    )
    if not conn_str:
        raise ValueError(
            "Set AZURE_STORAGE_CONNECTION_STRING or AZ_SOURCE_CONNECTION_STRING "
            "to access the raw data file share."
        )

    svc = ShareServiceClient.from_connection_string(conn_str)
    share = svc.get_share_client(cfg.raw.file_share_name)
    dc = share.get_directory_client(cfg.raw.file_share_path)

    logger.info(
        "Listing raw files from file share %s/%s",
        cfg.raw.file_share_name,
        cfg.raw.file_share_path,
    )
    all_items = list(dc.list_directories_and_files())

    # Filter to .raw files only
    raw_files = [
        item
        for item in all_items
        if item["name"].endswith(".raw") and not item.get("is_directory", False)
    ]
    logger.info("Found %d .raw files in file share", len(raw_files))

    # Build records with datetime and apply date filter
    files_list: list[tuple[str, dict]] = []
    for item in raw_files:
        fname = item["name"]
        dt = _parse_raw_datetime(fname)

        # Date range filter
        if dt:
            if cfg.start_date and dt < cfg.start_date:
                continue
            if cfg.end_date:
                end = cfg.end_date
                # If end is midnight, extend to end of day
                if end.hour == 0 and end.minute == 0 and end.second == 0:
                    from datetime import timedelta

                    end = end + timedelta(days=1) - timedelta(seconds=1)
                if dt > end:
                    continue

        rec = {
            "file_name": Path(fname).stem,
            "raw_filename": fname,
            "file_start_time": dt.isoformat() if dt else None,
            "file_size": item.get("size", 0),
        }
        files_list.append((fname, rec))

    files_list.sort(key=lambda x: x[1].get("file_start_time") or "")
    logger.info("After date filter: %d raw files", len(files_list))
    return files_list


# ═══════════════════════════════════════════════════════════════════════════
# STAGE 2: Convert raw → EchoData → calibrate → compute Sv
# ═══════════════════════════════════════════════════════════════════════════


def _download_raw_file(
    filename: str,
    cfg: PipelineConfig,
    directory_client=None,
) -> Path:
    """Download a single .raw file from Azure File Share to local disk.

    Parameters
    ----------
    directory_client : ShareDirectoryClient, optional
        Pre-built directory client to reuse across downloads.
        When None, a new client is created per call (legacy behaviour).
    """
    local_dir = cfg.raw.local_raw_dir
    local_dir.mkdir(parents=True, exist_ok=True)
    local_path = local_dir / filename

    if local_path.exists():
        logger.info("  Raw file already cached: %s", local_path)
        return local_path

    if directory_client is None:
        from azure.storage.fileshare import ShareServiceClient

        conn_str = os.environ.get(
            "AZURE_STORAGE_CONNECTION_STRING",
            os.environ.get("AZ_SOURCE_CONNECTION_STRING", ""),
        )
        svc = ShareServiceClient.from_connection_string(conn_str)
        share = svc.get_share_client(cfg.raw.file_share_name)
        directory_client = share.get_directory_client(cfg.raw.file_share_path)

    fc = directory_client.get_file_client(filename)

    logger.info("  Downloading %s...", filename)
    download = fc.download_file()
    with open(local_path, "wb") as f:
        download.readinto(f)

    size_mb = local_path.stat().st_size / 1024 / 1024
    logger.info("  Downloaded %s (%.1f MB)", filename, size_mb)
    return local_path


def _clean_echodata_encoding(ed) -> None:
    """Clear variable encoding on all groups in an EchoData's DataTree.

    Prevents zarr v3 'Cannot specify both compressor and compressors' error
    when echopype's to_zarr adds new encoding on top of stale v2 encoding
    carried over from source zarr stores.
    """
    tree = ed._tree
    for group_path in tree.groups:
        node = tree[group_path] if group_path != "/" else tree
        ds = node.to_dataset(inherit=False)
        for var in ds.data_vars:
            ds[var].encoding.clear()
        for coord in ds.coords:
            ds[coord].encoding.clear()
        node.dataset = ds


def download_all_raw_files(
    files_list: list[tuple[str, dict]],
    cfg: PipelineConfig,
) -> dict[str, Path]:
    """Download all raw files in parallel using ThreadPoolExecutor.

    Creates a single Azure FileShare directory client and shares it
    across all download threads to avoid per-file client overhead.

    Returns dict mapping raw_filename → local_path.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    max_workers = min(cfg.raw.download_workers, len(files_list))
    local_paths: dict[str, Path] = {}

    # Create a shared directory client once for all downloads
    from azure.storage.fileshare import ShareServiceClient

    conn_str = os.environ.get(
        "AZURE_STORAGE_CONNECTION_STRING",
        os.environ.get("AZ_SOURCE_CONNECTION_STRING", ""),
    )
    svc = ShareServiceClient.from_connection_string(conn_str)
    share = svc.get_share_client(cfg.raw.file_share_name)
    dir_client = share.get_directory_client(cfg.raw.file_share_path)

    logger.info("Downloading %d raw files with %d threads", len(files_list), max_workers)

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {}
        for raw_filename, rec in files_list:
            fut = pool.submit(
                _download_raw_file, raw_filename, cfg, directory_client=dir_client
            )
            futures[fut] = raw_filename

        for fut in as_completed(futures):
            raw_filename = futures[fut]
            try:
                local_paths[raw_filename] = fut.result()
            except Exception as e:
                logger.error("Download failed %s: %s", raw_filename, e)

    logger.info("Downloaded %d/%d files", len(local_paths), len(files_list))
    return local_paths


def convert_and_save_echodata(
    local_raw_path: Path,
    file_record: dict,
    cfg: PipelineConfig,
    echodata_dir: Path,
) -> tuple[str, str, str]:
    """Convert one raw file → EchoData → calibrate → save EchoData Zarr.

    Does NOT compute Sv — that happens after day-level combine (Stage 4).
    Returns (pulse_category, echodata_zarr_path, file_name).
    """
    from echopype.convert.api import open_raw

    file_name = file_record["file_name"]

    # Step 1: Convert to EchoData
    logger.info("  Converting %s → EchoData", file_name)
    echodata = open_raw(str(local_raw_path), sonar_model=cfg.raw.sonar_model)

    if echodata.beam is None:
        logger.warning("  No beam data in %s — skipping", file_name)
        return "unknown", "", file_name

    # Step 2: Apply calibration (mutates EchoData beam groups in-place)
    if cfg.raw.calibration_file:
        from oceanstream.echodata.calibrate import apply_calibration

        logger.info("  Applying calibration from %s", cfg.raw.calibration_file)
        echodata = apply_calibration(echodata, Path(cfg.raw.calibration_file))

    # Detect pulse category from transmit_duration_nominal
    category = _detect_pulse_category(echodata)

    # Step 3: Save EchoData to local Zarr (intermediate artifact)
    ed_zarr_path = echodata_dir / f"{file_name}.zarr"
    logger.info("  Saving EchoData: %s [%s]", ed_zarr_path, category)
    _clean_echodata_encoding(echodata)
    echodata.to_zarr(str(ed_zarr_path), overwrite=True)

    del echodata
    _release_memory()

    return category, str(ed_zarr_path), file_name


def _detect_pulse_category(echodata) -> str:
    """Detect short_pulse vs long_pulse from transmit_duration_nominal."""
    import numpy as np

    try:
        td = echodata["Sonar/Beam_group1"].transmit_duration_nominal
        first_ping = td.isel(ping_time=0).values.astype(float)
        # If any channel has 200 kHz, it's short pulse
        freqs = echodata["Sonar/Beam_group1"].frequency_nominal.values
        has_200k = any(np.isclose(f, 200_000.0) for f in freqs)
        if has_200k:
            return "short_pulse"
        # Check duration: short ≈ 1.024ms, long ≈ 2.048ms
        if any(d < 1.5e-3 for d in first_ping):
            return "short_pulse"
        return "long_pulse"
    except Exception:
        return "unknown"


def process_raw_files(
    files_list: list[tuple[str, dict]],
    cfg: PipelineConfig,
    echodata_dir: Path,
) -> list[tuple[str, str, str]]:
    """Download and convert raw files to EchoData Zarrs.

    Downloads in batches to limit disk usage, then converts sequentially.
    Each raw file is deleted after conversion to free disk space.

    Returns list of (category, echodata_zarr_path, file_name).
    """
    results = []
    total = len(files_list)
    batch_size = min(cfg.raw.download_batch_size, total)

    for batch_start in range(0, total, batch_size):
        batch = files_list[batch_start:batch_start + batch_size]
        logger.info("Download batch %d-%d/%d", batch_start + 1, batch_start + len(batch), total)

        # Download this batch in parallel
        local_paths = download_all_raw_files(batch, cfg)

        # Convert each file in the batch
        for idx, (raw_filename, rec) in enumerate(batch, batch_start + 1):
            if raw_filename not in local_paths:
                logger.error("  File %s not downloaded — skipping", rec["file_name"])
                results.append(("unknown", "", rec["file_name"]))
                continue

            logger.info("Converting file %d/%d: %s", idx, total, rec["file_name"])
            try:
                result = convert_and_save_echodata(
                    local_raw_path=local_paths[raw_filename],
                    file_record=rec,
                    cfg=cfg,
                    echodata_dir=echodata_dir,
                )
                results.append(result)
            except Exception as e:
                logger.error("  FAILED: %s — %s", rec["file_name"], e)
                results.append(("unknown", "", rec["file_name"]))
            finally:
                # Delete raw file after conversion to free disk space
                if not cfg.keep_raw:
                    local_paths[raw_filename].unlink(missing_ok=True)

    successful = sum(1 for _, zp, _ in results if zp)
    logger.info("Converted %d/%d files to EchoData", successful, total)
    return results


# ═══════════════════════════════════════════════════════════════════════════
# STAGE 3: Combine EchoData per day (lazy)
# ═══════════════════════════════════════════════════════════════════════════


def combine_echodata_day(
    ed_zarr_paths: list[str],
    day_key: str,
    category: str,
    echodata_dir: Path,
) -> str:
    """Combine per-file EchoData Zarrs into one per-day EchoData Zarr.

    Follows echopype's recommended pattern:
    1. Open each EchoData Zarr lazily (chunks={})
    2. combine_echodata() — graph/metadata operation, no data loads
    3. Save combined EchoData to Zarr (materializes on write)

    Returns the combined EchoData zarr path.
    """
    import echopype as ep

    logger.info("Combining %d EchoData files for %s/%s", len(ed_zarr_paths), day_key, category)

    ed_list = []
    for zarr_path in sorted(ed_zarr_paths):
        ed = ep.open_converted(zarr_path, chunks={})
        ed_list.append(ed)

    if len(ed_list) == 1:
        logger.info("  Single file for %s/%s — no combine needed", day_key, category)
        return ed_zarr_paths[0]

    ed_combined = ep.combine_echodata(ed_list)

    combined_zarr = str(echodata_dir / f"{day_key}--{category}--combined.zarr")
    logger.info("  Saving combined EchoData: %s", combined_zarr)
    _clean_echodata_encoding(ed_combined)
    ed_combined.to_zarr(combined_zarr, overwrite=True)

    del ed_combined, ed_list
    _release_memory()

    # Delete per-file EchoData Zarrs to free disk space
    import shutil
    for zarr_path in ed_zarr_paths:
        p = Path(zarr_path)
        if p.exists() and p.is_dir():
            shutil.rmtree(p, ignore_errors=True)
    logger.info("  Cleaned up %d per-file EchoData Zarrs", len(ed_zarr_paths))

    return combined_zarr


def run_echodata_combine(
    file_results: list[tuple[str, str, str]],
    files_list: list[tuple[str, dict]],
    cfg: PipelineConfig,
    echodata_dir: Path,
) -> dict[str, dict[str, str]]:
    """Group file EchoData results by day+category and combine.

    Returns: {day_key: {category: combined_echodata_zarr_path}}
    """
    day_groups = group_files_by_day(files_list, cfg.days_to_combine)

    file_info = {}
    for category, zarr_path, file_name in file_results:
        if zarr_path:
            file_info[file_name] = (category, zarr_path)

    day_echodata: dict[str, dict[str, str]] = {}

    for day_key, day_files in day_groups.items():
        by_category: dict[str, list[str]] = defaultdict(list)
        for _, rec in day_files:
            fn = rec["file_name"]
            if fn in file_info:
                cat, zp = file_info[fn]
                by_category[cat].append(zp)

        day_echodata[day_key] = {}

        if cfg.category_parallel and len(by_category) > 1:
            from concurrent.futures import ThreadPoolExecutor, as_completed

            with ThreadPoolExecutor(max_workers=len(by_category)) as pool:
                futures = {}
                for category, zarr_paths in by_category.items():
                    fut = pool.submit(
                        combine_echodata_day,
                        ed_zarr_paths=zarr_paths,
                        day_key=day_key,
                        category=category,
                        echodata_dir=echodata_dir,
                    )
                    futures[fut] = category
                for fut in as_completed(futures):
                    cat = futures[fut]
                    day_echodata[day_key][cat] = fut.result()
        else:
            for category, zarr_paths in by_category.items():
                combined_zarr = combine_echodata_day(
                    ed_zarr_paths=zarr_paths,
                    day_key=day_key,
                    category=category,
                    echodata_dir=echodata_dir,
                )
                day_echodata[day_key][category] = combined_zarr

    logger.info("EchoData combine complete: %d days", len(day_echodata))
    return day_echodata


# ═══════════════════════════════════════════════════════════════════════════
# GPS: Download GeoParquet from Azure + linear interpolation
# ═══════════════════════════════════════════════════════════════════════════


def download_gps_geoparquet(cfg: PipelineConfig) -> pd.DataFrame | None:
    """Download GPS GeoParquet from Azure blob and return a normalised DataFrame.

    Reads all .parquet / .geoparquet files under ``{gps_container}/{gps_blob_path}``
    and normalises the schema to columns: ``lat``, ``lon``, ``dt``.

    Returns None if GPS is not configured or no data is found.
    """
    if not cfg.gps_container:
        return None

    from azure.storage.blob import BlobServiceClient

    conn_str = os.environ.get(
        "AZURE_STORAGE_CONNECTION_STRING",
        os.environ.get("AZ_SOURCE_CONNECTION_STRING", ""),
    )
    if not conn_str:
        logger.warning("No Azure connection string — cannot download GPS data")
        return None

    blob_prefix = cfg.gps_blob_path or f"{cfg.cruise_id}/"
    logger.info("Downloading GPS GeoParquet from gpsdata/%s", blob_prefix)

    svc = BlobServiceClient.from_connection_string(conn_str)
    container_client = svc.get_container_client(cfg.gps_container)

    # List parquet blobs under the prefix
    blobs = [
        b for b in container_client.list_blobs(name_starts_with=blob_prefix)
        if b.name.endswith(".parquet") or b.name.endswith(".geoparquet")
    ]
    if not blobs:
        logger.warning("No parquet files found in %s/%s", cfg.gps_container, blob_prefix)
        return None

    logger.info("Found %d GPS parquet file(s)", len(blobs))

    frames: list[pd.DataFrame] = []
    for blob in blobs:
        logger.info("  Downloading %s (%.1f MB)", blob.name, blob.size / 1024 / 1024)
        tmp = tempfile.NamedTemporaryFile(suffix=".parquet", delete=False)
        try:
            data = container_client.download_blob(blob.name).readall()
            tmp.write(data)
            tmp.close()
            df = _read_gps_parquet(tmp.name)
            if df is not None and not df.empty:
                frames.append(df)
        except Exception as e:
            logger.error("  Failed to read %s: %s", blob.name, e)
        finally:
            Path(tmp.name).unlink(missing_ok=True)

    if not frames:
        logger.warning("No valid GPS data extracted from parquet files")
        return None

    gps_df = pd.concat(frames, ignore_index=True).sort_values("dt").reset_index(drop=True)
    gps_df = gps_df.drop_duplicates(subset=["dt"])

    # Filter to pipeline date range if configured
    if cfg.start_date:
        gps_df = gps_df[gps_df["dt"] >= cfg.start_date - timedelta(hours=2)]
    if cfg.end_date:
        end = cfg.end_date + timedelta(days=1, hours=2)
        gps_df = gps_df[gps_df["dt"] <= end]

    logger.info(
        "GPS loaded: %d points, time: %s → %s, lat: [%.2f, %.2f], lon: [%.2f, %.2f]",
        len(gps_df),
        gps_df["dt"].min(),
        gps_df["dt"].max(),
        gps_df["lat"].min(),
        gps_df["lat"].max(),
        gps_df["lon"].min(),
        gps_df["lon"].max(),
    )
    return gps_df


def _read_gps_parquet(path: str) -> pd.DataFrame | None:
    """Read a single GPS parquet file and normalise to lat/lon/dt columns.

    Handles both GeoParquet (with geometry Point column) and plain parquet
    with separate latitude/longitude columns.
    """
    try:
        import geopandas as gpd

        gdf = gpd.read_parquet(path)
        df = pd.DataFrame(gdf)
    except Exception:
        # Fall back to plain pandas if geopandas fails
        import pyarrow.parquet as pq

        table = pq.read_table(path)
        df = table.to_pandas()

    # Detect and normalise column names
    col_map = {}
    columns_lower = {c.lower(): c for c in df.columns}

    # Latitude
    for candidate in ("lat", "latitude", "lat_deg"):
        if candidate in columns_lower:
            col_map["lat"] = columns_lower[candidate]
            break

    # Longitude
    for candidate in ("lon", "longitude", "lng", "lon_deg"):
        if candidate in columns_lower:
            col_map["lon"] = columns_lower[candidate]
            break

    # Time
    for candidate in ("time", "dt", "datetime", "timestamp", "date_time"):
        if candidate in columns_lower:
            col_map["dt"] = columns_lower[candidate]
            break

    # If lat/lon not found, try to extract from geometry column
    if "lat" not in col_map or "lon" not in col_map:
        if "geometry" in df.columns:
            try:
                df["lat"] = df["geometry"].apply(lambda g: g.y if g else None)
                df["lon"] = df["geometry"].apply(lambda g: g.x if g else None)
                col_map["lat"] = "lat"
                col_map["lon"] = "lon"
            except Exception:
                pass

    if "lat" not in col_map or "lon" not in col_map or "dt" not in col_map:
        logger.warning(
            "Cannot identify lat/lon/time columns from: %s", list(df.columns)
        )
        return None

    result = pd.DataFrame({
        "lat": pd.to_numeric(df[col_map["lat"]], errors="coerce"),
        "lon": pd.to_numeric(df[col_map["lon"]], errors="coerce"),
        "dt": pd.to_datetime(df[col_map["dt"]], utc=True, errors="coerce").dt.tz_localize(None),
    })
    result = result.dropna(subset=["lat", "lon", "dt"])
    return result


def _interpolate_gps_linear(ds_Sv, gps_df: pd.DataFrame):
    """Linearly interpolate GPS lat/lon onto ds_Sv's ping_time.

    Matches echopype's add_location best practice:
      xr.DataArray.interp(method="linear", fill_value="extrapolate")
    """
    import xarray as xr

    gps_time = pd.DatetimeIndex(gps_df["dt"].values)

    lat_da = xr.DataArray(
        data=gps_df["lat"].values,
        dims=["gps_time"],
        coords={"gps_time": gps_time},
    )
    lon_da = xr.DataArray(
        data=gps_df["lon"].values,
        dims=["gps_time"],
        coords={"gps_time": gps_time},
    )

    ping_time = ds_Sv["ping_time"]

    lat_interp = lat_da.interp(
        gps_time=ping_time, method="linear",
        kwargs={"fill_value": "extrapolate"},
    ).drop_vars("gps_time")
    lon_interp = lon_da.interp(
        gps_time=ping_time, method="linear",
        kwargs={"fill_value": "extrapolate"},
    ).drop_vars("gps_time")

    # Drop pre-existing location variables to avoid conflicts
    for v in ("latitude", "longitude"):
        if v in ds_Sv.data_vars:
            ds_Sv = ds_Sv.drop_vars(v)
        if v in ds_Sv.coords:
            ds_Sv = ds_Sv.reset_coords(v, drop=True)

    ds_Sv["latitude"] = lat_interp
    ds_Sv["longitude"] = lon_interp
    ds_Sv["latitude"].attrs = {"long_name": "Latitude", "units": "degrees_north"}
    ds_Sv["longitude"].attrs = {"long_name": "Longitude", "units": "degrees_east"}

    return ds_Sv


# ═══════════════════════════════════════════════════════════════════════════
# STAGE 4: Compute Sv + add_depth + merge GPS → save Sv Zarr
# ═══════════════════════════════════════════════════════════════════════════


def compute_sv_day(
    ed_zarr_path: str,
    day_key: str,
    category: str,
    cfg: PipelineConfig,
    output_container: str,
    gps_df: pd.DataFrame | None = None,
) -> str:
    """Compute Sv from combined EchoData, add depth + GPS, save Sv Zarr.

    Follows echopype's recommended pattern:
    1. Open combined EchoData lazily
    2. Chunk along ping_time for out-of-core processing
    3. compute_Sv (lazy computation)
    4. add_depth (adds depth variable from echo_range + offset)
    5. Interpolate GPS location data (linear, matching echopype add_location)
    6. Save to output storage

    Returns the Sv zarr path (in the output container).
    """
    import echopype as ep
    from oceanstream.echodata.storage import save_dataset_to_azure

    logger.info("Computing Sv for %s/%s", day_key, category)

    # Open combined EchoData lazily and chunk for out-of-core processing
    ed = ep.open_converted(ed_zarr_path, chunks={})
    ed = ed.chunk({"ping_time": cfg.chunks.ping_time, "range_sample": -1})

    # Compute Sv
    compute_kwargs = {}
    if cfg.raw.sonar_model == "EK80":
        compute_kwargs["waveform_mode"] = cfg.raw.waveform_mode
        compute_kwargs["encode_mode"] = cfg.raw.encode_mode

    logger.info("  compute_Sv (waveform=%s, encode=%s)", cfg.raw.waveform_mode, cfg.raw.encode_mode)
    ds_Sv = ep.calibrate.compute_Sv(ed, **compute_kwargs)

    # Add depth variable from echo_range + transducer depth offset
    depth_offset = cfg.raw.depth_offset
    logger.info("  add_depth (offset=%.1fm)", depth_offset)
    ds_Sv = ep.consolidate.add_depth(ds_Sv, depth_offset=depth_offset)

    # Merge GPS location data if available
    if gps_df is not None and not gps_df.empty:
        ds_Sv = _interpolate_gps_linear(ds_Sv, gps_df)
        logger.info("  GPS interpolated (linear) onto %d pings", ds_Sv.sizes["ping_time"])

    # Clean encoding and chunk before saving
    for var in ds_Sv.data_vars:
        ds_Sv[var].encoding.clear()
    for coord in ds_Sv.coords:
        ds_Sv[coord].encoding.clear()

    chunk_spec = {"ping_time": cfg.chunks.ping_time, "range_sample": -1}
    ds_Sv = ds_Sv.chunk(chunk_spec)

    output_zarr = f"{day_key}/{day_key}--{category}.zarr"
    save_dataset_to_azure(ds_Sv, zarr_path=output_zarr, container=output_container)
    logger.info("  Saved Sv: %s/%s [%s]", output_container, output_zarr, category)

    ds_Sv.close()
    del ds_Sv, ed
    _release_memory()

    # Delete the combined EchoData Zarr to free disk space
    import shutil
    ed_path = Path(ed_zarr_path)
    if ed_path.exists() and ed_path.is_dir():
        shutil.rmtree(ed_path, ignore_errors=True)
        logger.info("  Cleaned up combined EchoData: %s", ed_zarr_path)

    return output_zarr


def run_sv_computation(
    day_echodata: dict[str, dict[str, str]],
    files_list: list[tuple[str, dict]],
    cfg: PipelineConfig,
    output_container: str,
    gps_df: pd.DataFrame | None = None,
) -> dict[str, dict[str, str]]:
    """Compute Sv for all day EchoData. Returns {day_key: {category: sv_zarr_path}}."""
    sv_zarrs: dict[str, dict[str, str]] = {}

    for day_key, categories in day_echodata.items():
        sv_zarrs[day_key] = {}

        # Filter GPS to this day ±1 hour buffer
        day_gps = None
        if gps_df is not None and not gps_df.empty:
            day_dt = datetime.fromisoformat(day_key)
            day_start = day_dt - timedelta(hours=1)
            day_end = day_dt + timedelta(days=cfg.days_to_combine, hours=1)
            mask = (gps_df["dt"] >= day_start) & (gps_df["dt"] <= day_end)
            day_gps = gps_df.loc[mask]
            if day_gps.empty:
                logger.warning("  No GPS data for %s — Sv will lack coordinates", day_key)
                day_gps = None
            else:
                logger.info("  GPS for %s: %d points", day_key, len(day_gps))

        if cfg.category_parallel and len(categories) > 1:
            from concurrent.futures import ThreadPoolExecutor, as_completed

            with ThreadPoolExecutor(max_workers=len(categories)) as pool:
                futures = {}
                for category, ed_zarr_path in categories.items():
                    fut = pool.submit(
                        compute_sv_day,
                        ed_zarr_path=ed_zarr_path,
                        day_key=day_key,
                        category=category,
                        cfg=cfg,
                        output_container=output_container,
                        gps_df=day_gps,
                    )
                    futures[fut] = category
                for fut in as_completed(futures):
                    cat = futures[fut]
                    sv_zarrs[day_key][cat] = fut.result()
        else:
            for category, ed_zarr_path in categories.items():
                sv_path = compute_sv_day(
                    ed_zarr_path=ed_zarr_path,
                    day_key=day_key,
                    category=category,
                    cfg=cfg,
                    output_container=output_container,
                    gps_df=day_gps,
                )
                sv_zarrs[day_key][category] = sv_path

    logger.info("Sv computation complete: %d days", len(sv_zarrs))
    return sv_zarrs


def _reconstruct_day_zarrs(output_container: str, suffix: str = ".zarr") -> dict:
    """List zarrs in Azure container and reconstruct day→category→path dict.

    *suffix* controls what to match:
      - '.zarr'            → source (concatenated) day zarrs
      - '--denoised.zarr'  → denoised day zarrs
    """
    import re
    from oceanstream.echodata.storage import get_azure_filesystem

    fs = get_azure_filesystem()
    day_zarrs: dict[str, dict[str, str]] = {}

    # List day directories in container
    try:
        top_level = fs.ls(output_container, detail=False)
    except Exception as e:
        logger.error("Failed to list container %s: %s", output_container, e)
        return day_zarrs

    # Pattern: {day_key}/{day_key}--{category}{suffix}
    escaped_suffix = re.escape(suffix)
    pattern = re.compile(
        r"(\d{4}-\d{2}-\d{2})/\d{4}-\d{2}-\d{2}--(\w+)" + escaped_suffix + r"$"
    )

    for top_path in top_level:
        # Each top_path like: container/2023-06-25
        try:
            sub_items = fs.ls(top_path, detail=False)
        except Exception:
            continue
        for item_path in sub_items:
            # item_path like: container/2023-06-25/2023-06-25--short_pulse--denoised.zarr
            rel = item_path[len(output_container) + 1:]  # strip container/
            m = pattern.search(rel)
            if m:
                day_key, category = m.group(1), m.group(2)
                day_zarrs.setdefault(day_key, {})[category] = rel

    logger.info("Reconstructed %d days from %s/*%s", len(day_zarrs), output_container, suffix)
    for dk, cats in sorted(day_zarrs.items()):
        for cat, zp in sorted(cats.items()):
            logger.info("  %s/%s → %s", dk, cat, zp)
    return day_zarrs


# ═══════════════════════════════════════════════════════════════════════════
# MAIN PIPELINE ORCHESTRATOR
# ═══════════════════════════════════════════════════════════════════════════


def run_pipeline(cfg: PipelineConfig) -> None:
    """Execute the raw-to-products pipeline.

    New EchoData-first architecture:
      Stage 1:  Discover raw files
      Stage 2:  Download (parallel) + convert to EchoData Zarr (sequential)
      Stage 3:  Combine EchoData per day+category (lazy)
      Stage 4:  Compute Sv + add_depth + merge GPS → save Sv Zarr
      Stage 5:  Denoise
      Stage 6:  Seabed masking (optional)
      Stage 7:  MVBS
      Stage 8:  NASC
      Stage 9:  Echograms
      Stage 10: PMTiles + COG
      Stage 11: Campaign aggregation
    """
    from oceanstream.echodata.storage import ensure_container_exists, generate_container_name

    pipeline_start = time.time()

    # Output container
    output_container = cfg.output_container
    if not output_container:
        output_container = generate_container_name(cfg.cruise_id)
    ensure_container_exists(output_container, public_access="container")
    logger.info("Output container: %s", output_container)

    # EchoData intermediate directory — use local save dir when available
    if cfg.local_save_dir:
        echodata_dir = cfg.local_save_dir / "echodata_intermediate"
    else:
        echodata_dir = cfg.raw.local_raw_dir.parent / "echodata_intermediate"
    echodata_dir.mkdir(parents=True, exist_ok=True)

    # Stage 0: Dask (for post-processing stages)
    client = setup_dask_client(cfg)

    resume = cfg.resume_stage

    try:
        # ── Resume logic — skip early stages ──────────────────────
        if resume >= 6:
            # Reconstruct from existing output zarrs
            logger.info("RESUMING from stage %d — reconstructing paths", resume)
            source_day_zarrs = _reconstruct_day_zarrs(output_container, suffix=".zarr")
            denoised_day_zarrs = _reconstruct_day_zarrs(output_container, suffix="--denoised.zarr")
            for dk in list(source_day_zarrs.keys()):
                source_day_zarrs[dk] = {
                    cat: zp for cat, zp in source_day_zarrs[dk].items()
                    if all(marker not in zp for marker in (
                        "--denoised", "--masked", "--pruned", "--mvbs", "--nasc",
                    ))
                }
            logger.info("Stages 1-5 skipped (resume=%d)", resume)

        elif resume >= 5:
            # Resume from Stage 5 (denoise): reconstruct source Sv zarrs
            logger.info("RESUMING from stage %d — reconstructing source zarrs", resume)
            source_day_zarrs = _reconstruct_day_zarrs(output_container, suffix=".zarr")
            for dk in list(source_day_zarrs.keys()):
                source_day_zarrs[dk] = {
                    cat: zp for cat, zp in source_day_zarrs[dk].items()
                    if all(marker not in zp for marker in (
                        "--denoised", "--masked", "--pruned", "--mvbs", "--nasc",
                    ))
                }
            logger.info("Stages 1-4 skipped (resume=%d)", resume)

            # Stage 5: Denoise
            t0 = time.time()
            denoised_day_zarrs = run_denoising(
                client, source_day_zarrs, cfg, output_container
            )
            logger.info("STAGE 5 complete: denoising (%.1fs)", time.time() - t0)

        else:
            # ── Full pipeline from Stage 1 ────────────────────────

            # Download GPS GeoParquet (if configured)
            gps_df = None
            if cfg.gps_container:
                t0 = time.time()
                gps_df = download_gps_geoparquet(cfg)
                if gps_df is not None:
                    logger.info(
                        "GPS ready: %d points (%.1fs)", len(gps_df), time.time() - t0,
                    )
                else:
                    logger.warning("GPS download returned no data (%.1fs)", time.time() - t0)

            # Stage 1: Discover raw files
            t0 = time.time()
            files_list = discover_raw_files(cfg)
            if not files_list:
                logger.error("No raw files found — aborting")
                return
            total_size_gb = sum(r.get("file_size", 0) for _, r in files_list) / 1024**3
            logger.info(
                "STAGE 1 complete: %d raw files discovered (%.1f GB total, %.1fs)",
                len(files_list), total_size_gb, time.time() - t0,
            )

            # Stage 2: Download + convert to EchoData Zarr
            t0 = time.time()
            file_results = process_raw_files(files_list, cfg, echodata_dir)
            logger.info(
                "STAGE 2 complete: %d files converted to EchoData (%.1fs)",
                len(file_results), time.time() - t0,
            )

            # Stage 3: Combine EchoData per day+category
            t0 = time.time()
            day_echodata = run_echodata_combine(
                file_results, files_list, cfg, echodata_dir,
            )
            logger.info(
                "STAGE 3 complete: %d days combined (%.1fs)",
                len(day_echodata), time.time() - t0,
            )

            # Stage 4: Compute Sv + add_depth + merge GPS
            t0 = time.time()
            source_day_zarrs = run_sv_computation(
                day_echodata, files_list, cfg, output_container,
                gps_df=gps_df,
            )
            logger.info(
                "STAGE 4 complete: Sv computation (%d days, %.1fs)",
                len(source_day_zarrs), time.time() - t0,
            )

            # Cleanup any remaining intermediate EchoData Zarrs
            import shutil
            remaining = [f for f in echodata_dir.iterdir() if f.is_dir()]
            if remaining:
                for ed_file in remaining:
                    shutil.rmtree(ed_file, ignore_errors=True)
                logger.info("Cleaned up %d remaining intermediate files", len(remaining))

            # Stage 5: Denoise
            t0 = time.time()
            denoised_day_zarrs = run_denoising(
                client, source_day_zarrs, cfg, output_container
            )
            logger.info("STAGE 5 complete: denoising (%.1fs)", time.time() - t0)

        # Stage 6: Seabed masking
        t0 = time.time()
        if cfg.apply_seabed_mask:
            masked_day_zarrs = run_seabed_masking(
                client, denoised_day_zarrs, cfg, output_container
            )
            logger.info("STAGE 6 complete: seabed masking (%.1fs)", time.time() - t0)
            pre_prune_input = masked_day_zarrs
        else:
            logger.info("STAGE 6 skipped: seabed masking disabled (%.1fs)", time.time() - t0)
            pre_prune_input = denoised_day_zarrs

        # Stage 6b: Prune noisy pings (drops pings that are mostly NaN so MVBS/NASC
        # are computed on an analysis-ready Sv dataset)
        t0 = time.time()
        if cfg.prune.enabled:
            pruned_day_zarrs = run_pruning(
                client, pre_prune_input, cfg, output_container
            )
            logger.info("STAGE 6b complete: pruning (%.1fs)", time.time() - t0)
            mvbs_input = pruned_day_zarrs
        else:
            logger.info("STAGE 6b skipped: pruning disabled (%.1fs)", time.time() - t0)
            mvbs_input = pre_prune_input

        # Stage 7+8: MVBS + NASC
        t0 = time.time()
        if resume >= 9:
            mvbs_zarrs = _reconstruct_day_zarrs(output_container, suffix="--mvbs.zarr")
            nasc_zarrs = _reconstruct_day_zarrs(output_container, suffix="--nasc.zarr")
            logger.info("STAGE 7+8 skipped (resume=%d), reconstructed %d MVBS, %d NASC zarrs",
                        resume,
                        sum(len(v) for v in mvbs_zarrs.values()),
                        sum(len(v) for v in nasc_zarrs.values()))
        else:
            mvbs_zarrs = run_mvbs_computation(
                client, mvbs_input, cfg, output_container
            )
            nasc_zarrs = run_nasc_computation(
                client, mvbs_input, cfg, output_container
            )
            logger.info("STAGE 7+8 complete: MVBS + NASC (%.1fs)", time.time() - t0)

        # Stage 9: Echograms
        t0 = time.time()
        if resume >= 11:
            logger.info("STAGE 9 skipped (resume=%d)", resume)
        else:
            run_echogram_generation(
                client,
                source_day_zarrs,
                denoised_day_zarrs,
                mvbs_zarrs,
                cfg,
                output_container,
                nasc_zarrs=nasc_zarrs,
            )
            logger.info("STAGE 9 complete: echograms (%.1fs)", time.time() - t0)

        # Stage 10: PMTiles + COG
        t0 = time.time()
        if resume >= 11:
            logger.info("STAGE 10 skipped (resume=%d)", resume)
        else:
            run_tiles_and_cog(
                client, nasc_zarrs, mvbs_zarrs, cfg, output_container
            )
            logger.info("STAGE 10 complete: PMTiles + COG (%.1fs)", time.time() - t0)

        # Stage 11: Campaign aggregation
        t0 = time.time()
        run_campaign_aggregation(
            client, mvbs_input, mvbs_zarrs, cfg, output_container
        )
        logger.info("STAGE 11 complete: campaign Zarr (%.1fs)", time.time() - t0)

    finally:
        if client is not None:
            client.close()

    total_time = time.time() - pipeline_start
    logger.info(
        "Pipeline complete. Total time: %.1fs (%.1f min). Output: %s",
        total_time, total_time / 60, output_container,
    )


# ═══════════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════════


def parse_args() -> PipelineConfig:
    parser = argparse.ArgumentParser(
        description="Process Saildrone TPOS 2023 from raw EK80 files (zarr v3 pipeline)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    # Mode
    parser.add_argument(
        "--local-test",
        action="store_true",
        help="Quick local test (1 day, 2 workers, 6 GB each)",
    )

    # Data source
    parser.add_argument("--cruise-id", default="SD_TPOS2023_v03")
    parser.add_argument("--output-container", default="")
    parser.add_argument(
        "--file-share",
        default="saildroneraw",
        help="Azure File Share name containing raw .raw files",
    )
    parser.add_argument(
        "--file-share-path",
        default="DATA",
        help="Directory within the file share",
    )

    # Calibration
    parser.add_argument(
        "--calibration-file",
        default="",
        help="Path to calibration_values.xlsx (Saildrone Excel format)",
    )

    # GPS
    parser.add_argument(
        "--gps-container",
        default="",
        help="Azure blob container with GPS GeoParquet (e.g. 'gpsdata')",
    )
    parser.add_argument(
        "--gps-blob-path",
        default="",
        help="Path prefix within gps-container (default: {cruise_id}/)",
    )

    # Date range
    parser.add_argument("--start-date", help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end-date", help="End date (YYYY-MM-DD)")

    # Dask
    parser.add_argument("--scheduler", help="Dask scheduler address")
    parser.add_argument("--n-workers", type=int, default=2)
    parser.add_argument("--memory-limit", default="6GB")
    parser.add_argument(
        "--parallel-workers", type=int, default=0,
        help="Max parallel stage workers (denoise, NASC, echograms). "
             "0 = auto-detect from available RAM (default: 0).",
    )

    # Processing toggles
    parser.add_argument("--skip-denoising", action="store_true")
    parser.add_argument("--skip-echograms", action="store_true")
    parser.add_argument("--skip-pmtiles", action="store_true")
    parser.add_argument("--skip-nasc", action="store_true")
    parser.add_argument("--skip-mvbs", action="store_true")
    parser.add_argument(
        "--skip-pruning", action="store_true",
        help="Skip Stage 6b (drop noisy pings) — feed denoised/masked Sv directly to MVBS/NASC",
    )
    parser.add_argument(
        "--prune-threshold", type=float, default=0.8,
        help="NaN fraction above which a ping is dropped (default: 0.8 = drop pings ≥80%% NaN)",
    )
    parser.add_argument(
        "--no-crosstalk", action="store_true",
        help="Disable the cross-talk detector in Stage 6b (keeps NaN-fraction pruning)",
    )
    parser.add_argument(
        "--crosstalk-ref-depth-min", type=float, default=800.0,
        help="Start of the reference band used for cross-talk detection (metres). "
             "At 38 kHz in tropical open ocean, ~800m sits below the DSL and should be quiet. "
             "Default: 800",
    )
    parser.add_argument(
        "--crosstalk-ref-depth-max", type=float, default=1200.0,
        help="End of the reference band (metres). Default: 1200",
    )
    parser.add_argument(
        "--crosstalk-threshold-db", type=float, default=6.0,
        help="Elevation above the median reference-band Sv (in dB) that flags a ping as cross-talk. "
             "Default: 6 dB. Lower = stricter (drops more pings); higher = permissive.",
    )
    parser.add_argument(
        "--skip-campaign-echograms", action="store_true",
        help="Skip Stage 11's campaign MVBS echogram rendering (keeps campaign zarr build)",
    )
    parser.add_argument(
        "--sv-clip-max-db", type=float, default=-10.0,
        help="Sanity clip: mask Sv samples louder than this (dB) as NaN after denoising. "
             "Catches residual cross-talk/electrical noise the mask denoisers miss. "
             "Default: -10 dB. Pass a very high value (e.g. 100) to disable.",
    )
    parser.add_argument(
        "--denoise-config",
        help="TOML file with per-frequency denoise parameters (loads EchodataConfig.from_toml)",
    )
    parser.add_argument(
        "--keep-raw", action="store_true",
        help="Keep downloaded raw files for re-use (default: delete after conversion)",
    )
    parser.add_argument(
        "--raw-cache-dir", type=str, default="",
        help="Directory to cache raw .raw files (default: /tmp/oceanstream/raw_downloads)",
    )
    parser.add_argument(
        "--resume-stage", type=int, default=0,
        help="Resume from stage N (5=denoise, 6=after denoise, 9=after MVBS/NASC, 11=campaign zarr only). "
             "Reconstructs intermediate data from existing output zarrs.",
    )
    parser.add_argument("--save-netcdf", action="store_true")
    parser.add_argument("--save-nasc-netcdf", action="store_true")
    parser.add_argument("--save-mvbs-netcdf", action="store_true")

    # MVBS/NASC params
    parser.add_argument("--mvbs-range-bin", default="1m")
    parser.add_argument("--mvbs-ping-time-bin", default="10s")
    parser.add_argument("--nasc-range-bin", default="10m")
    parser.add_argument("--nasc-dist-bin", default="0.5nmi")

    # EK80 parameters
    parser.add_argument(
        "--waveform-mode",
        default="CW",
        choices=["CW", "BB"],
        help="EK80 waveform mode",
    )
    parser.add_argument(
        "--encode-mode",
        default="complex",
        choices=["complex", "power"],
        help="EK80 encode mode",
    )

    # Depth
    parser.add_argument(
        "--depth-offset",
        type=float,
        default=1.9,
        help="Transducer depth below waterline in metres (default: 1.9 for Saildrone)",
    )

    # Storage mode
    parser.add_argument(
        "--local-save",
        type=str,
        default="",
        metavar="DIR",
        help="Save all outputs to local directory instead of Azure. "
             "Zarrs, echograms, NetCDFs all go under DIR/container/.",
    )
    parser.add_argument(
        "--upload-after",
        action="store_true",
        help="Process locally for speed, then bulk-upload to Azure at the end. "
             "Uses --local-save dir (or /mnt/data/output) during processing.",
    )

    # Surface exclusion
    parser.add_argument(
        "--surface-exclusion-depth",
        type=float,
        default=1.9,
        help="Exclude depth bins above this value (metres) from MVBS/NASC "
             "(default: 1.9 for Saildrone transducer depth)",
    )

    # Azure VM
    parser.add_argument("--auto-deallocate", action="store_true",
                        help="Deallocate the Azure VM after pipeline completes")
    parser.add_argument("--vm-name", default="oceanstream-batch-vm")

    # Output
    parser.add_argument("--output-dir", default="/tmp/oceanstream/batch_output")
    parser.add_argument("--colormap", default="ocean_r")

    args = parser.parse_args()

    # Build config
    if args.local_test:
        cfg = PipelineConfig.for_local_test(
            start_date=args.start_date or "2023-06-25",
            end_date=args.end_date or "2023-06-25",
        )
        # Override Dask for local raw processing (lower memory per worker)
        cfg.dask.n_workers = args.n_workers
        cfg.dask.memory_limit = args.memory_limit
    else:
        cfg = PipelineConfig()
        cfg.cruise_id = args.cruise_id
        if args.start_date:
            cfg.start_date = datetime.fromisoformat(args.start_date)
        if args.end_date:
            cfg.end_date = datetime.fromisoformat(args.end_date)
        cfg.dask.scheduler_address = args.scheduler
        cfg.dask.n_workers = args.n_workers
        cfg.dask.memory_limit = args.memory_limit

    # Common overrides
    cfg.resume_stage = args.resume_stage
    cfg.output_container = args.output_container or cfg.output_container
    cfg.skip_denoising = args.skip_denoising
    cfg.skip_echograms = args.skip_echograms
    cfg.skip_pmtiles = args.skip_pmtiles
    cfg.skip_nasc = args.skip_nasc
    cfg.skip_mvbs = args.skip_mvbs
    cfg.prune.enabled = not args.skip_pruning
    cfg.prune.drop_threshold = args.prune_threshold
    cfg.prune.crosstalk_enabled = not args.no_crosstalk
    cfg.prune.crosstalk_ref_depth_min = args.crosstalk_ref_depth_min
    cfg.prune.crosstalk_ref_depth_max = args.crosstalk_ref_depth_max
    cfg.prune.crosstalk_threshold_db = args.crosstalk_threshold_db
    cfg.skip_campaign_echograms = args.skip_campaign_echograms
    cfg.denoise.sv_clip_max_db = args.sv_clip_max_db
    cfg.save_to_netcdf = args.save_netcdf
    cfg.save_nasc_to_netcdf = args.save_nasc_netcdf or cfg.save_nasc_to_netcdf
    cfg.save_mvbs_to_netcdf = args.save_mvbs_netcdf or cfg.save_mvbs_to_netcdf
    cfg.colormap = args.colormap
    cfg.local_output_dir = Path(args.output_dir)

    # MVBS / NASC
    cfg.mvbs.range_bin = args.mvbs_range_bin
    cfg.mvbs.ping_time_bin = args.mvbs_ping_time_bin
    cfg.nasc.range_bin = args.nasc_range_bin
    cfg.nasc.dist_bin = args.nasc_dist_bin

    # Raw conversion settings
    cfg.raw.file_share_name = args.file_share
    cfg.raw.file_share_path = args.file_share_path
    cfg.raw.calibration_file = args.calibration_file
    cfg.raw.waveform_mode = args.waveform_mode
    cfg.raw.encode_mode = args.encode_mode
    cfg.raw.depth_offset = args.depth_offset
    if args.raw_cache_dir:
        cfg.raw.local_raw_dir = Path(args.raw_cache_dir)
    cfg.keep_raw = args.keep_raw

    # Local save mode
    cfg.upload_after = args.upload_after
    if args.upload_after and not args.local_save:
        # Default to /mnt/data/output for fast local NVMe processing
        cfg.local_save_dir = Path("/mnt/data/output")
    else:
        cfg.local_save_dir = Path(args.local_save) if args.local_save else None

    # GPS
    cfg.gps_container = args.gps_container
    cfg.gps_blob_path = args.gps_blob_path

    # Surface exclusion
    cfg.surface_exclusion_depth = args.surface_exclusion_depth

    # Parallel stage workers (denoise, NASC, echograms)
    cfg.parallel_workers = args.parallel_workers

    # Azure VM
    cfg.azure_vm.auto_deallocate = args.auto_deallocate
    cfg.azure_vm.vm_name = args.vm_name

    # Denoise config from TOML (per-frequency params)
    if args.denoise_config:
        from oceanstream.echodata.config import EchodataConfig

        echo_cfg = EchodataConfig.from_toml(Path(args.denoise_config))
        cfg.denoise = type(cfg.denoise)(
            enabled=True,
            methods=echo_cfg.denoise.methods,
            use_frequency_specific=echo_cfg.denoise.use_frequency_specific,
            frequency_params=echo_cfg.denoise.frequency_params,
            background_num_side_pings=echo_cfg.denoise.background_num_side_pings,
            background_snr_threshold=echo_cfg.denoise.background_snr_threshold,
            impulse_threshold_db=echo_cfg.denoise.impulse_threshold_db,
            impulse_num_lags=echo_cfg.denoise.impulse_num_lags,
            transient_n=echo_cfg.denoise.transient_n,
            transient_exclude_above=echo_cfg.denoise.transient_exclude_above,
            attenuation_threshold=echo_cfg.denoise.attenuation_threshold,
            attenuation_upper_limit=echo_cfg.denoise.attenuation_upper_limit,
            attenuation_lower_limit=echo_cfg.denoise.attenuation_lower_limit,
        )

    return cfg


def _bulk_upload_to_azure(local_dir: Path, container: str) -> None:
    """Upload all files from local output directory to Azure Blob Storage.

    Uses a ThreadPoolExecutor to upload multiple blobs in parallel.
    Skips files that already exist in Azure with the same size.
    """
    import os
    from concurrent.futures import ThreadPoolExecutor, as_completed

    from azure.storage.blob import ContainerClient

    conn_str = os.environ.get("AZURE_STORAGE_CONNECTION_STRING", "")
    if not conn_str:
        logger.error("AZURE_STORAGE_CONNECTION_STRING not set — cannot upload")
        return

    source_dir = local_dir / container
    if not source_dir.exists():
        logger.error("Local output directory %s does not exist", source_dir)
        return

    client = ContainerClient.from_connection_string(conn_str, container)
    try:
        client.create_container()
    except Exception:
        pass  # already exists

    # Collect all files to upload
    files_to_upload = []
    for root, _dirs, files in os.walk(source_dir):
        for fname in files:
            local_path = Path(root) / fname
            blob_name = str(local_path.relative_to(source_dir))
            files_to_upload.append((local_path, blob_name))

    logger.info("Uploading %d files from %s to container '%s'", len(files_to_upload), source_dir, container)

    # Build set of existing blobs with their sizes for skip logic
    existing = {}
    try:
        for blob in client.list_blobs():
            existing[blob.name] = blob.size
    except Exception:
        pass

    uploaded = 0
    skipped = 0

    def _upload_one(local_path: Path, blob_name: str) -> bool:
        size = local_path.stat().st_size
        if blob_name in existing and existing[blob_name] == size:
            return False  # skip — same size
        with open(local_path, "rb") as f:
            client.upload_blob(blob_name, f, overwrite=True)
        return True

    t0 = time.time()
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {
            pool.submit(_upload_one, lp, bn): bn
            for lp, bn in files_to_upload
        }
        for fut in as_completed(futures):
            try:
                if fut.result():
                    uploaded += 1
                else:
                    skipped += 1
            except Exception as e:
                logger.warning("Upload failed for %s: %s", futures[fut], e)

    elapsed = time.time() - t0
    logger.info(
        "Upload complete: %d uploaded, %d skipped (same size) in %.1fs",
        uploaded, skipped, elapsed,
    )


def main():
    cfg = parse_args()

    logger.info("=" * 70)
    logger.info("Saildrone TPOS 2023 — Raw EK80 Pipeline (zarr v3)")
    logger.info("=" * 70)
    logger.info("Cruise: %s", cfg.cruise_id)
    logger.info("File share: %s/%s", cfg.raw.file_share_name, cfg.raw.file_share_path)
    logger.info(
        "Date range: %s → %s",
        cfg.start_date.date() if cfg.start_date else "start",
        cfg.end_date.date() if cfg.end_date else "end",
    )
    logger.info("Calibration: %s", cfg.raw.calibration_file or "(none)")
    logger.info(
        "EK80 mode: waveform=%s, encode=%s",
        cfg.raw.waveform_mode,
        cfg.raw.encode_mode,
    )
    logger.info("Depth offset: %.1fm", cfg.raw.depth_offset)
    logger.info(
        "Dask: %d workers, %s each", cfg.dask.n_workers, cfg.dask.memory_limit
    )
    logger.info(
        "Parallel stage workers: %d (0=auto → %d)",
        cfg.parallel_workers, cfg.effective_parallel_workers(),
    )
    logger.info(
        "Denoise: %s", "enabled" if not cfg.skip_denoising else "disabled"
    )
    if not cfg.skip_denoising and cfg.denoise.use_frequency_specific:
        freqs = list(cfg.denoise.frequency_params.keys()) if cfg.denoise.frequency_params else []
        logger.info("Denoise mode: per-frequency (%s)", ", ".join(str(f) for f in freqs) or "presets")
    elif not cfg.skip_denoising:
        logger.info("Denoise mode: global (methods=%s)", cfg.denoise.methods)
    logger.info(
        "GPS source: %s",
        f"{cfg.gps_container}/{cfg.gps_blob_path or cfg.cruise_id + '/'}" if cfg.gps_container else "(none)",
    )
    if cfg.local_save_dir and cfg.upload_after:
        logger.info("Storage: LOCAL → %s (will upload to Azure after)", cfg.local_save_dir)
    elif cfg.local_save_dir:
        logger.info("Storage: LOCAL → %s", cfg.local_save_dir)
    else:
        logger.info("Storage: Azure Blob")
    logger.info("=" * 70)

    # Patch storage before pipeline imports (function-level imports)
    if cfg.local_save_dir:
        from local_storage import patch_storage
        patch_storage(cfg.local_save_dir)

    try:
        run_pipeline(cfg)
    except KeyboardInterrupt:
        logger.info("Pipeline interrupted by user")
    except Exception:
        logger.exception("Pipeline failed with error")
        raise

    # Bulk upload to Azure after local processing
    if cfg.upload_after and cfg.local_save_dir:
        _bulk_upload_to_azure(cfg.local_save_dir, cfg.output_container)

    if cfg.azure_vm.auto_deallocate:
        from infra import deallocate_vm
        logger.info("Auto-deallocating VM...")
        try:
            deallocate_vm(cfg.azure_vm)
        except Exception as e:
            logger.warning("VM deallocation failed: %s", e)


if __name__ == "__main__":
    main()
