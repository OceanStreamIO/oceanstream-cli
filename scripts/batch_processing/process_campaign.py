#!/usr/bin/env python3
"""Standalone Dask-based processing script for the Saildrone TPOS 2023 campaign.

Processes pre-computed Sv Zarr stores through:
  GPS merge → day concatenation → denoise → seabed mask →
  MVBS/NASC → echograms → PMTiles/COG → campaign-wide Zarr

Uses the **oceanstream** library for all processing and the Dask
distributed scheduler for parallelism (no Prefect dependency).

Usage:
    # Local test (2 days)
    python process_campaign.py --local-test

    # Local with date range
    python process_campaign.py --start-date 2023-06-22 --end-date 2023-06-28

    # Full campaign on remote Dask cluster
    python process_campaign.py --scheduler tcp://10.0.1.4:8786

    # On Azure VM (reads config from env vars)
    python process_campaign.py --from-env --auto-deallocate
"""

from __future__ import annotations

import argparse
import gc
import json
import logging
import sys
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

load_dotenv()


def _release_memory() -> None:
    """GC + force glibc to return freed pages to OS (Linux only)."""
    gc.collect()
    try:
        import ctypes
        libc = ctypes.CDLL("libc.so.6")
        libc.malloc_trim(0)
    except (OSError, AttributeError):
        pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
    force=True,
)
logger = logging.getLogger(__name__)

# Force line-buffered stdout for nohup/redirect compatibility
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(line_buffering=True)

# Suppress verbose Azure SDK HTTP logging
logging.getLogger("azure").setLevel(logging.WARNING)
logging.getLogger("azure.core.pipeline.policies.http_logging_policy").setLevel(logging.WARNING)
logging.getLogger("adlfs").setLevel(logging.WARNING)

# ---------------------------------------------------------------------------
# Imports from oceanstream and local config
# ---------------------------------------------------------------------------
from config import PipelineConfig

# Lazy imports for heavy libraries happen inside functions to keep startup fast.


def _save_netcdf_to_blob(ds, nc_path: str, container: str) -> None:
    """Save an xarray Dataset as NetCDF and upload to Azure blob storage.

    Mirrors the approach from saildrone-data's save_dataset_to_netcdf:
    use engine='netcdf4' + NETCDF4 format (native Unicode support) and
    skip compression for string/object variables.
    """
    import tempfile
    import numpy as np
    from oceanstream.echodata.storage import upload_file_to_blob

    try:
        ds_computed = ds.compute()
        # Convert booleans to int8 (NetCDF has no bool type)
        for var in list(ds_computed.data_vars):
            if ds_computed[var].dtype == bool:
                ds_computed[var] = ds_computed[var].astype(np.int8)

        # Build encoding: compress numeric vars, skip string/object vars
        encoding = {}
        for var in ds_computed.data_vars:
            if ds_computed[var].dtype.kind in {"U", "S", "O"}:
                encoding[var] = {}
            else:
                encoding[var] = {"zlib": True, "complevel": 5}

        with tempfile.NamedTemporaryFile(suffix=".nc", delete=True) as tmp:
            ds_computed.to_netcdf(
                tmp.name,
                engine="netcdf4",
                format="NETCDF4",
                encoding=encoding,
            )
            upload_file_to_blob(tmp.name, nc_path, container)
        logger.info("  Saved NetCDF: %s", nc_path)
    except Exception as e:
        logger.warning("NetCDF export failed for %s: %s", nc_path, e)


def _ensure_position_coords(ds) -> "xr.Dataset":
    """Ensure latitude/longitude exist as data vars for echopype compute_MVBS/NASC.

    echopype's _get_reduced_positions uses flox xarray_reduce on
    ds_Sv[["latitude", "longitude"]] which requires them as data variables.
    After concatenation/denoising they may end up as coords-only, and
    xarray's ds[["var"]] only selects data vars — not coords.

    TODO: Port this fix into oceanstream.echodata.compute (compute_mvbs/compute_nasc)
    so the library handles this transparently.
    """
    pos_vars = [v for v in ("latitude", "longitude") if v in ds.coords and v not in ds.data_vars]
    if pos_vars:
        ds = ds.reset_coords(pos_vars)
    return ds


# ═══════════════════════════════════════════════════════════════════════════
# STAGE 0: Dask cluster setup
# ═══════════════════════════════════════════════════════════════════════════

def setup_dask_client(cfg: PipelineConfig):
    """Create or connect to a Dask distributed client.

    When ``cfg.local_save_dir`` is set the pipeline runs all stages in the
    main process without ``client.submit()``.  A distributed scheduler adds
    huge overhead for in-memory numpy data (1+ GiB graph serialisation), so
    we skip it entirely and let Dask fall back to the default synchronous /
    threaded scheduler.
    """
    local_save_dir = getattr(cfg, "local_save_dir", None)
    if local_save_dir:
        logger.info("Local storage mode — skipping Dask distributed client")
        return None

    from dask.distributed import Client, LocalCluster

    if cfg.dask.scheduler_address:
        logger.info("Connecting to Dask scheduler at %s", cfg.dask.scheduler_address)
        client = Client(cfg.dask.scheduler_address)
    else:
        logger.info(
            "Starting LocalCluster with %d workers, %s memory each",
            cfg.dask.n_workers,
            cfg.dask.memory_limit,
        )
        cluster = LocalCluster(
            n_workers=cfg.dask.n_workers,
            threads_per_worker=cfg.dask.threads_per_worker,
            memory_limit=cfg.dask.memory_limit,
        )
        client = Client(cluster)

    logger.info("Dask dashboard: %s", client.dashboard_link)
    return client


# ═══════════════════════════════════════════════════════════════════════════
# STAGE 1: File discovery and grouping
# ═══════════════════════════════════════════════════════════════════════════

def discover_files(cfg: PipelineConfig) -> list[tuple[str, dict]]:
    """List Sv Zarr files and load GPS metadata.

    Supports three discovery modes:
    1. GPS data file (from DB export) — includes location data
    2. Pre-generated file list JSON (from generate_file_list.py) — fast
    3. Azure container scan (slow for large containers)
    """
    if cfg.gps_data_file and Path(cfg.gps_data_file).exists():
        from export_gps import load_gps_data

        files_list = load_gps_data(Path(cfg.gps_data_file))
        logger.info("Loaded %d file records from GPS export", len(files_list))

    elif cfg.file_list_file and Path(cfg.file_list_file).exists():
        # Pre-generated file list (from generate_file_list.py)
        with open(cfg.file_list_file) as f:
            records = json.load(f)
        files_list = []
        for rec in records:
            files_list.append((rec["zarr_path"], rec))
        logger.info("Loaded %d file records from file list", len(files_list))
    else:
        from oceanstream.echodata.storage import list_zarr_files
        from oceanstream.echodata.concat import extract_datetime
        import re

        all_zarr_paths = list_zarr_files(
            cfg.source_container, cruise_id=cfg.cruise_id,
        )
        logger.info("Found %d Zarr stores (raw) in %s/%s", len(all_zarr_paths), cfg.source_container, cfg.cruise_id)

        # Filter: keep only nested paths ({cruise}/{file_name}/{file_name}.zarr)
        # and exclude denoised copies.
        zarr_paths = []
        for zp in all_zarr_paths:
            parts = zp.parts
            if len(parts) >= 3 and parts[-2] == zp.stem and "_denoised" not in zp.stem:
                zarr_paths.append(zp)
        logger.info("After filtering (nested, non-denoised): %d files", len(zarr_paths))

        files_list = []
        for zp in sorted(zarr_paths):
            parts = zp.parts
            file_name = parts[-2]
            file_dt = None
            try:
                file_dt = extract_datetime(file_name)
            except (ValueError, AttributeError):
                pass

            rec = {
                "file_name": file_name,
                "file_freqs": None,  # will detect from data
                "file_start_time": file_dt.isoformat() if file_dt else None,
                "file_end_time": None,
                "id": file_name,
                "location_data": [],
            }
            files_list.append((str(zp), rec))

    # Apply date filter
    if cfg.start_date or cfg.end_date:
        files_list = _filter_by_date(files_list, cfg.start_date, cfg.end_date)
        logger.info("After date filter: %d files", len(files_list))

    return files_list


def _filter_by_date(
    files_list: list[tuple[str, dict]],
    start: Optional[datetime],
    end: Optional[datetime],
) -> list[tuple[str, dict]]:
    """Filter files_list by file_start_time.

    Inclusive on both ends: start <= file_start_time <= end (at end of day).
    """
    from datetime import timedelta

    filtered = []
    # If end is midnight (no time component), make it end of day
    if end and end.hour == 0 and end.minute == 0 and end.second == 0:
        end = end + timedelta(days=1) - timedelta(seconds=1)

    for source_path, rec in files_list:
        ts_str = rec.get("file_start_time")
        if not ts_str:
            filtered.append((source_path, rec))  # keep files without timestamps
            continue
        ts = datetime.fromisoformat(ts_str) if isinstance(ts_str, str) else ts_str
        if start and ts < start:
            continue
        if end and ts > end:
            continue
        filtered.append((source_path, rec))
    return filtered


def group_files_by_day(
    files_list: list[tuple[str, dict]],
    days_to_combine: int = 1,
) -> dict[str, list[tuple[str, dict]]]:
    """Group files into day batches using batch_key from oceanstream."""
    from oceanstream.echodata.concat import batch_key

    by_batch: dict[str, list[tuple[str, dict]]] = defaultdict(list)

    for source_path, rec in files_list:
        ts_str = rec.get("file_start_time")
        if ts_str:
            ts = datetime.fromisoformat(ts_str) if isinstance(ts_str, str) else ts_str
            key = batch_key(ts, window_days=days_to_combine)
        else:
            key = "unknown"
        by_batch[key].append((source_path, rec))

    return dict(sorted(by_batch.items()))


# ═══════════════════════════════════════════════════════════════════════════
# STAGE 2: Per-file processing (GPS merge + save to output container)
# ═══════════════════════════════════════════════════════════════════════════

def _freqs_to_category(file_freqs: str | None) -> str | None:
    """Convert file_freqs string to pulse category without opening the dataset.

    Returns None if file_freqs is unknown/missing (caller should detect from data).
    """
    FREQ_TO_CATEGORY = {
        "38000.0,200000.0": "short_pulse",
        "38000.0": "long_pulse",
    }
    return FREQ_TO_CATEGORY.get(file_freqs) if file_freqs else None


def process_single_file(
    source_path: str,
    file_record: dict,
    cruise_id: str,
    source_container: str,
    output_container: str,
    chunks: dict,
    save_netcdf: bool = False,
    save_echograms: bool = False,
    colormap: str = "ocean_r",
) -> tuple[str, str, str]:
    """Process one Sv Zarr file: open, merge GPS, save to output container.

    Optionally exports per-file NetCDF and echogram PNGs to match
    the export76 per-file output structure.

    Returns (pulse_category, output_zarr_path, file_name).
    """
    import xarray as xr
    from oceanstream.echodata.storage import open_sv_from_azure, save_dataset_to_azure
    from oceanstream.echodata.concat import merge_location_data, detect_pulse_category

    file_name = file_record["file_name"]
    location_data = file_record.get("location_data", [])

    logger.info("Processing file: %s", file_name)

    # Open Sv from source container using discovered path
    # source_path is the full container-relative path: cruise_id/file_name/file_name.zarr
    ds = open_sv_from_azure(
        zarr_path=source_path,
        container=source_container,
        chunks=chunks,
    )

    # Merge GPS location data if available
    if location_data:
        ds = merge_location_data(ds, location_data)
        logger.info("  Merged GPS data (%d points)", len(location_data))

    # Detect pulse category: use file_freqs from DB if available, else inspect data
    category = _freqs_to_category(file_record.get("file_freqs"))
    if category is None:
        category = detect_pulse_category(ds)

    # Clear per-variable Zarr encoding that may conflict with Dask chunking.
    # Variables like echo_range carry encoding['chunks'] from the source store
    # that can mismatch our Dask chunks, causing ValueError on to_zarr().
    for var in ds.data_vars:
        ds[var].encoding.clear()
    for coord in ds.coords:
        ds[coord].encoding.clear()

    # Save to output container using the same path structure
    output_zarr = f"{cruise_id}/{file_name}/{file_name}.zarr"
    save_dataset_to_azure(ds, zarr_path=output_zarr, container=output_container)
    logger.info("  Saved to %s/%s [%s]", output_container, output_zarr, category)

    # Per-file NetCDF export
    if save_netcdf:
        nc_path = f"{cruise_id}/{file_name}/{file_name}.nc"
        _save_netcdf_to_blob(ds, nc_path, output_container)

    # Per-file echogram PNGs
    if save_echograms:
        try:
            from oceanstream.echodata.plot.echogram import plot_and_upload_echograms

            plot_and_upload_echograms(
                ds,
                cruise_id=cruise_id,
                file_base_name=file_name,
                save_to_blobstorage=True,
                upload_path=f"{cruise_id}/{file_name}",
                container_name=output_container,
                create_interactive_pages=False,
                cmap=colormap,
                plot_var="Sv",
                title_template=f"{file_name}" + " | {channel_label}",
            )
            logger.info("  Per-file echograms saved for %s", file_name)
        except Exception as e:
            logger.warning("  Per-file echogram failed for %s: %s", file_name, e)

    ds.close()
    del ds
    _release_memory()

    return category, output_zarr, file_name


def process_files_parallel(
    client,
    files_list: list[tuple[str, dict]],
    cfg: PipelineConfig,
    output_container: str,
) -> list[tuple[str, str, str]]:
    """Process all files in parallel with Dask, return list of (category, zarr_path, file_name)."""
    from dask.distributed import as_completed

    chunks = cfg.chunks.as_dict()
    in_flight = []
    all_results = []

    for idx, (source_path, rec) in enumerate(files_list):
        future = client.submit(
            process_single_file,
            source_path=source_path,
            file_record=rec,
            cruise_id=cfg.cruise_id,
            source_container=cfg.source_container,
            output_container=output_container,
            chunks=chunks,
            save_netcdf=cfg.per_file_netcdf,
            save_echograms=cfg.per_file_echograms,
            colormap=cfg.colormap,
            key=f"file-{idx}-{rec['file_name']}",
        )
        in_flight.append(future)

        # Throttle when batch_size reached
        if len(in_flight) >= cfg.batch_size:
            finished = next(as_completed(in_flight))
            in_flight.remove(finished)
            all_results.append(finished.result())

    # Drain remaining
    for fut in in_flight:
        all_results.append(fut.result())

    logger.info("Processed %d files", len(all_results))
    return all_results


# ═══════════════════════════════════════════════════════════════════════════
# STAGE 3: Day-level concatenation
# ═══════════════════════════════════════════════════════════════════════════

def concatenate_day(
    zarr_paths: list[str],
    day_key: str,
    category: str,
    cruise_id: str,
    output_container: str,
    chunks: dict,
    save_netcdf: bool = False,
) -> str:
    """Concatenate multiple per-file Zarrs into a single day Zarr.

    Returns the output zarr path (relative to container).
    """
    from oceanstream.echodata.storage import open_sv_from_azure, save_dataset_to_azure
    from oceanstream.echodata.utils.encoding import fix_chunking
    import xarray as xr

    logger.info("Concatenating %d files for %s/%s", len(zarr_paths), day_key, category)

    datasets = []
    for zp in sorted(zarr_paths):
        ds = open_sv_from_azure(zarr_path=zp, container=output_container, chunks=chunks)
        # Prepare for concatenation — demote coords that vary across files
        for v in ("latitude", "longitude", "speed_knots"):
            if v in ds.coords:
                ds = ds.reset_coords(v)
        if "source_filenames" in ds:
            ds = ds.drop_vars("source_filenames")
        datasets.append(ds)

    # Sort by ping_time
    datasets.sort(key=lambda d: d["ping_time"].min().values)

    concatenated = xr.concat(
        datasets,
        dim="ping_time",
        data_vars="all",
        coords="minimal",
        compat="override",
        join="outer",
    )

    # Collapse frequency_nominal over ping_time
    if "frequency_nominal" in concatenated and "ping_time" in concatenated["frequency_nominal"].dims:
        freq_1d = concatenated["frequency_nominal"].mean("ping_time")
        concatenated = concatenated.drop_vars("frequency_nominal")
        concatenated["frequency_nominal"] = freq_1d
        concatenated = concatenated.assign_coords(
            frequency=("channel", freq_1d.values)
        )

    # Filter chunks to only include dimensions present in the dataset
    # (e.g. chunks may have 'depth' but data still has 'range_sample' pre-denoising)
    valid_chunks = {k: v for k, v in chunks.items() if k in concatenated.dims}
    concatenated = concatenated.chunk(valid_chunks)
    concatenated = fix_chunking(concatenated)

    output_zarr = f"{day_key}/{day_key}--{category}.zarr"
    save_dataset_to_azure(concatenated, zarr_path=output_zarr, container=output_container)
    logger.info("  Saved concatenated day Zarr: %s", output_zarr)

    if save_netcdf:
        _save_netcdf_to_blob(concatenated, f"{day_key}/{day_key}--{category}.nc", output_container)

    # Cleanup — aggressively free memory so the worker doesn't get killed
    for ds in datasets:
        ds.close()
    concatenated.close()
    del datasets, concatenated
    _release_memory()

    return output_zarr


def run_day_concatenation(
    client,
    file_results: list[tuple[str, str, str]],
    files_list: list[tuple[str, dict]],
    cfg: PipelineConfig,
    output_container: str,
) -> dict[str, dict[str, str]]:
    """Group file results by day+category and concatenate.

    Returns: {day_key: {category: day_zarr_path}}
    """
    # Group files by day
    day_groups = group_files_by_day(files_list, cfg.days_to_combine)

    # Map file_name -> (category, zarr_path) from results
    file_info = {}
    for category, zarr_path, file_name in file_results:
        file_info[file_name] = (category, zarr_path)

    day_zarrs: dict[str, dict[str, str]] = {}
    chunks = cfg.chunks.as_dict()

    for day_key, day_files in day_groups.items():
        # Group by pulse category within the day
        by_category: dict[str, list[str]] = defaultdict(list)
        for _, rec in day_files:
            fn = rec["file_name"]
            if fn in file_info:
                cat, zp = file_info[fn]
                by_category[cat].append(zp)

        day_zarrs[day_key] = {}
        for category, zarr_paths in by_category.items():
            if len(zarr_paths) == 1:
                # Single file — no concatenation needed, just reference it
                day_zarrs[day_key][category] = zarr_paths[0]
                logger.info("Day %s/%s: single file, skipping concat", day_key, category)
            else:
                # Run concat on the main thread — not on Dask workers.
                # Short-pulse files (2 channels) can exceed 30 GB worker
                # memory when concatenating 23+ files. The main process
                # has access to the full 125 GB of system RAM.
                day_zarr = concatenate_day(
                    zarr_paths=zarr_paths,
                    day_key=day_key,
                    category=category,
                    cruise_id=cfg.cruise_id,
                    output_container=output_container,
                    chunks=chunks,
                    save_netcdf=cfg.save_to_netcdf,
                )
                day_zarrs[day_key][category] = day_zarr

    logger.info("Concatenation complete: %d days", len(day_zarrs))
    return day_zarrs


# ═══════════════════════════════════════════════════════════════════════════
# STAGE 4: Denoise
# ═══════════════════════════════════════════════════════════════════════════

def denoise_day(
    zarr_path: str,
    output_container: str,
    denoise_config,
    chunks: dict,
    day_key: str,
    category: str,
    cruise_id: str,
    save_netcdf: bool = False,
) -> str:
    """Apply denoising to a day Zarr and save the result.

    Matches the Prefect flow denoise pattern:
    1. Apply mask-based denoising (impulse, transient, attenuation)
    2. Apply echopype background noise removal per channel

    Returns the denoised zarr path.
    """
    from oceanstream.echodata.storage import open_sv_from_azure, save_dataset_to_azure
    from oceanstream.echodata.denoise import apply_denoising

    logger.info("Denoising %s/%s", day_key, category)

    ds = open_sv_from_azure(zarr_path=zarr_path, container=output_container, chunks=chunks)

    # Load into memory to avoid Dask distributed overhead during denoising.
    # Rolling operations in remove_background_noise create enormous task graphs
    # that the distributed scheduler struggles with.  The day-level zarrs
    # are small enough (~0.3–1.8 GB) to fit in the worker memory limit.
    logger.info("  Loading dataset into memory (%s/%s)", day_key, category)
    ds = ds.load()

    # Filter chunks to only include dimensions present in the dataset
    valid_chunks = {k: v for k, v in chunks.items() if k in ds.dims}

    # Step 1: Mask-based denoising (impulse, transient, attenuation — no background).
    # This matches the Prefect flow which applies masks first and handles
    # background noise removal separately via echopype.
    mask_methods = [m for m in denoise_config.methods if m != "background"]
    if mask_methods:
        ds_denoised = apply_denoising(ds, methods=mask_methods, config=denoise_config)
    else:
        ds_denoised = ds

    # Step 2: echopype background noise removal per channel.
    # This directly modifies Sv values (not just masking) and is applied
    # per-channel with frequency-specific parameters.
    if "background" in denoise_config.methods:
        from echopype.clean import remove_background_noise as ep_remove_background_noise

        bgn_params = denoise_config.to_background_params()

        def _remove_bgn_one_channel(ch_ds):
            return ep_remove_background_noise(
                ch_ds,
                ping_num=bgn_params.get("ping_window", 50),
                range_sample_num=bgn_params.get("range_window", 20),
                SNR_threshold=bgn_params.get("SNR_threshold", "3.0dB"),
                background_noise_max=bgn_params.get("background_noise_max"),
            )["Sv"]

        sv_clean = ds_denoised.groupby("channel").map(_remove_bgn_one_channel)
        sv_clean.name = "Sv"
        ds_denoised["Sv"] = sv_clean
        logger.info("  Background noise removal applied per channel")

    # Keep range_sample dimension as-is through all pipeline stages.
    # echopype functions (compute_MVBS, compute_NASC) expect range_sample.
    # Depth conversion can happen in final export / visualization.

    output_zarr = f"{day_key}/{day_key}--{category}--denoised.zarr"
    # Rechunk to uniform sizes — denoising/groupby operations produce
    # non-uniform chunks at concatenation boundaries that Zarr cannot write.
    rechunk_spec = {"ping_time": chunks.get("ping_time", 1000)}
    if "range_sample" in ds_denoised.dims:
        rechunk_spec["range_sample"] = -1
    ds_denoised = ds_denoised.chunk(rechunk_spec)
    for var in ds_denoised.data_vars:
        ds_denoised[var].encoding.clear()
    for coord in ds_denoised.coords:
        ds_denoised[coord].encoding.clear()
    save_dataset_to_azure(ds_denoised, zarr_path=output_zarr, container=output_container)

    if save_netcdf:
        _save_netcdf_to_blob(ds_denoised, f"{day_key}/{day_key}--{category}--denoised.nc", output_container)

    logger.info("  Saved denoised: %s", output_zarr)

    ds.close()
    if hasattr(ds_denoised, "close"):
        ds_denoised.close()
    del ds, ds_denoised
    _release_memory()

    return output_zarr


def run_denoising(
    client,
    day_zarrs: dict[str, dict[str, str]],
    cfg: PipelineConfig,
    output_container: str,
) -> dict[str, dict[str, str]]:
    """Denoise all day Zarrs. Returns updated day_zarrs with denoised paths."""
    if cfg.skip_denoising:
        logger.info("Skipping denoising (--skip-denoising)")
        return day_zarrs

    denoise_config = cfg.denoise.to_denoise_config()
    chunks = cfg.chunks.as_dict()
    denoised_zarrs: dict[str, dict[str, str]] = {}

    for day_key, categories in day_zarrs.items():
        denoised_zarrs[day_key] = {}
        for category, zarr_path in categories.items():
            # Run in main process — Dask handles lazy array computation
            # but orchestration stays here to avoid nested-task deadlock.
            denoised_path = denoise_day(
                zarr_path=zarr_path,
                output_container=output_container,
                denoise_config=denoise_config,
                chunks=chunks,
                day_key=day_key,
                category=category,
                cruise_id=cfg.cruise_id,
                save_netcdf=cfg.save_to_netcdf,
            )
            denoised_zarrs[day_key][category] = denoised_path

    logger.info("Denoising complete")
    return denoised_zarrs


# ═══════════════════════════════════════════════════════════════════════════
# STAGE 5: Seabed masking
# ═══════════════════════════════════════════════════════════════════════════

def mask_seabed_day(
    zarr_path: str,
    output_container: str,
    chunks: dict,
    day_key: str,
    category: str,
    cruise_id: str,
) -> str:
    """Detect and mask seabed for a day Zarr. Returns masked zarr path."""
    import dask
    from oceanstream.echodata.storage import open_sv_from_azure, save_dataset_to_azure
    from oceanstream.echodata.seabed import detect_seabed, mask_seabed

    logger.info("Seabed masking %s/%s", day_key, category)

    ds = open_sv_from_azure(zarr_path=zarr_path, container=output_container)

    with dask.config.set(scheduler="synchronous"):
        try:
            seabed_result = detect_seabed(ds, method="composite")
            ds_masked = mask_seabed(ds, seabed_result)
        except Exception as e:
            logger.warning("Seabed detection failed for %s/%s: %s — skipping", day_key, category, e)
            ds_masked = ds

    output_zarr = f"{day_key}/{day_key}--{category}--masked.zarr"
    save_dataset_to_azure(ds_masked, zarr_path=output_zarr, container=output_container)
    logger.info("  Saved masked: %s", output_zarr)

    ds.close()
    del ds, ds_masked
    _release_memory()

    return output_zarr


def run_seabed_masking(
    client,
    day_zarrs: dict[str, dict[str, str]],
    cfg: PipelineConfig,
    output_container: str,
) -> dict[str, dict[str, str]]:
    """Apply seabed masking to all denoised day Zarrs."""
    if not cfg.apply_seabed_mask:
        logger.info("Skipping seabed masking")
        return day_zarrs

    chunks = cfg.chunks.as_dict()
    masked_zarrs: dict[str, dict[str, str]] = {}

    for day_key, categories in day_zarrs.items():
        masked_zarrs[day_key] = {}
        for category, zarr_path in categories.items():
            # Run in main process to avoid nested-task deadlock.
            masked_path = mask_seabed_day(
                zarr_path=zarr_path,
                output_container=output_container,
                chunks=chunks,
                day_key=day_key,
                category=category,
                cruise_id=cfg.cruise_id,
            )
            masked_zarrs[day_key][category] = masked_path

    logger.info("Seabed masking complete")
    return masked_zarrs


# ═══════════════════════════════════════════════════════════════════════════
# STAGE 6: Compute MVBS
# ═══════════════════════════════════════════════════════════════════════════

def compute_mvbs_day(
    zarr_path: str,
    output_container: str,
    chunks: dict,
    range_bin: str,
    ping_time_bin: str,
    day_key: str,
    category: str,
    cruise_id: str,
    save_netcdf: bool = False,
) -> str:
    """Compute MVBS for a day Zarr. Returns MVBS zarr path.

    Uses pure-numpy index-based coarsening to avoid the OOM issues caused
    by echopype's flox-based MVBS (which creates 3D label arrays the same
    size as Sv).  Processes one channel at a time to keep memory bounded.
    """
    import re
    import numpy as np
    import xarray as xr
    from oceanstream.echodata.storage import open_sv_from_azure, save_dataset_to_azure

    logger.info("Computing MVBS %s/%s", day_key, category)

    # Open lazily to inspect metadata
    ds = open_sv_from_azure(zarr_path=zarr_path, container=output_container)
    ds = _ensure_position_coords(ds)

    nc = ds.sizes["channel"]
    n_ping = ds.sizes["ping_time"]
    n_range = ds.sizes["range_sample"]

    # Derive bin sizes from physical specs and data sampling rates
    range_m = float(re.match(r"([\d.]+)", range_bin).group(1))
    ping_s = float(re.match(r"([\d.]+)", ping_time_bin).group(1))

    er0 = ds.echo_range.isel(channel=0, ping_time=0).values
    sample_spacing = float(np.nanmedian(np.diff(er0)))
    range_sample_num = max(1, round(range_m / sample_spacing))

    pt = ds.ping_time.values
    dt_ms = float(np.nanmedian(np.diff(pt[:100]).astype("timedelta64[ms]").astype(float)))
    ping_num = max(1, round(ping_s * 1000 / dt_ms))

    freq = ds.frequency_nominal.values

    np_trim = (n_ping // ping_num) * ping_num
    nr_trim = (n_range // range_sample_num) * range_sample_num

    logger.info("  Index binning: range_sample_num=%d (~%.1fm), ping_num=%d (~%.1fs), channels=%d",
                range_sample_num, range_sample_num * sample_spacing,
                ping_num, ping_num * dt_ms / 1000, nc)

    # Process one channel at a time to limit peak memory
    mvbs_channels = []
    for ch in range(nc):
        logger.info("  Channel %d/%d: loading Sv...", ch + 1, nc)
        sv_ch = ds["Sv"].isel(channel=ch).values  # (n_ping, n_range)
        sv_ch = sv_ch[:np_trim, :nr_trim]
        sv_lin = 10.0 ** (sv_ch / 10.0)
        del sv_ch
        sv_lin = sv_lin.reshape(np_trim // ping_num, ping_num,
                                nr_trim // range_sample_num, range_sample_num)
        ch_mean = np.nanmean(sv_lin, axis=(1, 3))
        del sv_lin
        ch_mvbs = 10.0 * np.log10(ch_mean)
        del ch_mean
        mvbs_channels.append(ch_mvbs)
        logger.info("  Channel %d/%d: done, shape=%s", ch + 1, nc, ch_mvbs.shape)

    ds.close()
    del ds

    sv_mvbs = np.stack(mvbs_channels, axis=0)  # (nc, n_ping_binned, n_range_binned)
    del mvbs_channels

    # Build xarray Dataset
    ping_time_binned = pt[:np_trim:ping_num]
    er_binned = er0[:nr_trim:range_sample_num]

    ds_mvbs = xr.Dataset(
        data_vars={"Sv": (["channel", "ping_time", "echo_range"], sv_mvbs)},
        coords={
            "channel": freq,
            "ping_time": ping_time_binned,
            "echo_range": er_binned,
        },
    )
    ds_mvbs.attrs["processing"] = "MVBS computed with oceanstream (index binning)"
    ds_mvbs.attrs["range_bin"] = range_bin
    ds_mvbs.attrs["ping_time_bin"] = ping_time_bin

    output_zarr = f"{day_key}/{day_key}--{category}--mvbs.zarr"
    save_dataset_to_azure(ds_mvbs, zarr_path=output_zarr, container=output_container)

    if save_netcdf:
        _save_netcdf_to_blob(ds_mvbs, f"{day_key}/{day_key}--{category}--mvbs.nc", output_container)

    logger.info("  Saved MVBS: %s", output_zarr)

    del ds_mvbs, sv_mvbs
    _release_memory()

    return output_zarr


def run_mvbs_computation(
    client,
    day_zarrs: dict[str, dict[str, str]],
    cfg: PipelineConfig,
    output_container: str,
) -> dict[str, dict[str, str]]:
    """Compute MVBS for all day Zarrs."""
    if cfg.skip_mvbs:
        logger.info("Skipping MVBS computation")
        return {}

    chunks = cfg.chunks.as_dict()
    mvbs_zarrs: dict[str, dict[str, str]] = {}

    for day_key, categories in day_zarrs.items():
        mvbs_zarrs[day_key] = {}
        for category, zarr_path in categories.items():
            # Run in main process to avoid nested-task deadlock.
            mvbs_path = compute_mvbs_day(
                zarr_path=zarr_path,
                output_container=output_container,
                chunks=chunks,
                range_bin=cfg.mvbs.range_bin,
                ping_time_bin=cfg.mvbs.ping_time_bin,
                day_key=day_key,
                category=category,
                cruise_id=cfg.cruise_id,
                save_netcdf=cfg.save_mvbs_to_netcdf or cfg.save_to_netcdf,
            )
            mvbs_zarrs[day_key][category] = mvbs_path

    logger.info("MVBS computation complete")
    return mvbs_zarrs


# ═══════════════════════════════════════════════════════════════════════════
# STAGE 7: Compute NASC
# ═══════════════════════════════════════════════════════════════════════════

def compute_nasc_day(
    zarr_path: str,
    output_container: str,
    chunks: dict,
    range_bin: str,
    dist_bin: str,
    day_key: str,
    category: str,
    cruise_id: str,
    save_netcdf: bool = False,
) -> str:
    """Compute NASC for a day Zarr. Returns NASC zarr path."""
    import dask
    from oceanstream.echodata.storage import open_sv_from_azure, save_dataset_to_azure

    logger.info("Computing NASC %s/%s", day_key, category)

    ds = open_sv_from_azure(zarr_path=zarr_path, container=output_container)

    # Promote lat/lon from coords to data vars for echopype
    ds = _ensure_position_coords(ds)

    import echopype as ep
    import numpy as np

    with dask.config.set(scheduler="synchronous"):
        ds_nasc = ep.commongrid.compute_NASC(ds, range_bin=range_bin, dist_bin=dist_bin)

    # Add NASC_log for visualization (same as oceanstream wrapper)
    ds_nasc["NASC_log"] = 10 * np.log10(ds_nasc["NASC"])
    ds_nasc["NASC_log"].attrs = {
        "long_name": "Log10-transformed NASC",
        "units": "dB re 1 m² nmi⁻²",
    }

    # Add NASC_log for visualization (same as oceanstream wrapper)
    ds_nasc["NASC_log"] = 10 * np.log10(ds_nasc["NASC"])
    ds_nasc["NASC_log"].attrs = {
        "long_name": "Log10-transformed NASC",
        "units": "dB re 1 m² nmi⁻²",
    }

    output_zarr = f"{day_key}/{day_key}--{category}--nasc.zarr"
    save_dataset_to_azure(ds_nasc, zarr_path=output_zarr, container=output_container)

    if save_netcdf:
        _save_netcdf_to_blob(ds_nasc, f"{day_key}/{day_key}--{category}--nasc.nc", output_container)

    logger.info("  Saved NASC: %s", output_zarr)

    ds.close()
    del ds, ds_nasc
    _release_memory()

    return output_zarr


def run_nasc_computation(
    client,
    day_zarrs: dict[str, dict[str, str]],
    cfg: PipelineConfig,
    output_container: str,
) -> dict[str, dict[str, str]]:
    """Compute NASC for all day Zarrs."""
    if cfg.skip_nasc:
        logger.info("Skipping NASC computation")
        return {}

    chunks = cfg.chunks.as_dict()
    nasc_zarrs: dict[str, dict[str, str]] = {}

    for day_key, categories in day_zarrs.items():
        nasc_zarrs[day_key] = {}
        for category, zarr_path in categories.items():
            # Run in main process to avoid nested-task deadlock.
            nasc_path = compute_nasc_day(
                zarr_path=zarr_path,
                output_container=output_container,
                chunks=chunks,
                range_bin=cfg.nasc.range_bin,
                dist_bin=cfg.nasc.dist_bin,
                day_key=day_key,
                category=category,
                cruise_id=cfg.cruise_id,
                save_netcdf=cfg.save_nasc_to_netcdf or cfg.save_to_netcdf,
            )
            nasc_zarrs[day_key][category] = nasc_path

    logger.info("NASC computation complete")
    return nasc_zarrs


# ═══════════════════════════════════════════════════════════════════════════
# STAGE 8: Echograms
# ═══════════════════════════════════════════════════════════════════════════

def _load_zarr_to_memory(zarr_path: str, container: str) -> "xr.Dataset":
    """Open a zarr from Azure and load fully into memory (numpy-backed).

    Loading into memory avoids Dask scheduler / matplotlib GIL contention
    that causes hangs during pcolormesh rendering.
    """
    from oceanstream.echodata.storage import open_sv_from_azure

    ds = open_sv_from_azure(zarr_path=zarr_path, container=container)
    ds = ds.load()  # materialise all dask arrays → numpy
    return ds


def generate_echograms_day(
    source_zarr: str,
    denoised_zarr: str | None,
    mvbs_zarr: str | None,
    output_container: str,
    chunks: dict,
    day_key: str,
    category: str,
    cruise_id: str,
    colormap: str,
) -> list[str]:
    """Generate echogram PNGs for a day and upload to blob storage.

    Matches the export76 output pattern per day/category:
      - source: {day}--{category}_{channel}.png
      - denoised: {day}--{category}--denoised_{channel}.png
      - denoised-pruned: {day}--{category}--denoised-pruned_{channel}.png
        (denoised with noisy pings removed via drop_noisy_pings)
      - MVBS: {category}--mvbs_{channel}.png
    """
    import gc

    from oceanstream.echodata.plot.echogram import plot_and_upload_echograms

    all_files = []

    # Source Sv echograms
    try:
        logger.info("  Loading source zarr into memory: %s", source_zarr)
        ds = _load_zarr_to_memory(source_zarr, output_container)
        files = plot_and_upload_echograms(
            ds,
            cruise_id=cruise_id,
            file_base_name=f"{day_key}--{category}",
            save_to_blobstorage=True,
            upload_path=day_key,
            container_name=output_container,
            create_interactive_pages=False,
            cmap=colormap,
            plot_var="Sv",
            title_template=f"{day_key} ({category})" + " | {channel_label}",
        )
        all_files.extend(files)
        ds.close()
        del ds
        gc.collect()
        logger.info("  Source echograms: %d files", len(files))
    except Exception as e:
        logger.warning("Source echogram failed for %s/%s: %s", day_key, category, e)

    # Denoised Sv echograms + denoised-pruned (reuse same dataset)
    if denoised_zarr:
        try:
            logger.info("  Loading denoised zarr into memory: %s", denoised_zarr)
            ds = _load_zarr_to_memory(denoised_zarr, output_container)

            # --- denoised echograms ---
            files = plot_and_upload_echograms(
                ds,
                cruise_id=cruise_id,
                file_base_name=f"{day_key}--{category}--denoised",
                save_to_blobstorage=True,
                upload_path=day_key,
                container_name=output_container,
                create_interactive_pages=False,
                cmap=colormap,
                plot_var="Sv",
                title_template=f"{day_key} ({category}, denoised)" + " | {channel_label}",
            )
            all_files.extend(files)
            logger.info("  Denoised echograms: %d files", len(files))

            # --- denoised-pruned echograms (reuse loaded dataset) ---
            try:
                from oceanstream.echodata.denoise import drop_noisy_pings

                ds_pruned = drop_noisy_pings(ds, drop_threshold=0.8)
                files = plot_and_upload_echograms(
                    ds_pruned,
                    cruise_id=cruise_id,
                    file_base_name=f"{day_key}--{category}--denoised-pruned",
                    save_to_blobstorage=True,
                    upload_path=day_key,
                    container_name=output_container,
                    create_interactive_pages=False,
                    cmap=colormap,
                    plot_var="Sv",
                    title_template=f"{day_key} ({category}, pruned)" + " | {channel_label}",
                )
                all_files.extend(files)
                del ds_pruned
                logger.info("  Denoised-pruned echograms: %d files", len(files))
            except Exception as e:
                logger.warning("Denoised-pruned echogram failed for %s/%s: %s", day_key, category, e)

            ds.close()
            del ds
            gc.collect()
        except Exception as e:
            logger.warning("Denoised echogram failed for %s/%s: %s", day_key, category, e)

    # MVBS echograms
    if mvbs_zarr:
        try:
            logger.info("  Loading MVBS zarr into memory: %s", mvbs_zarr)
            ds = _load_zarr_to_memory(mvbs_zarr, output_container)
            files = plot_and_upload_echograms(
                ds,
                cruise_id=cruise_id,
                file_base_name=f"{category}--mvbs",
                save_to_blobstorage=True,
                upload_path=day_key,
                container_name=output_container,
                create_interactive_pages=False,
                cmap=colormap,
                plot_var="Sv",
                title_template=f"{day_key} ({category})" + " | MVBS | {channel_label}",
            )
            all_files.extend(files)
            ds.close()
            del ds
            gc.collect()
            logger.info("  MVBS echograms: %d files", len(files))
        except Exception as e:
            logger.warning("MVBS echogram failed for %s/%s: %s", day_key, category, e)

    return all_files


def _count_existing_echograms(container: str, day_key: str, category: str) -> int:
    """Return the number of PNG echograms already uploaded for a day/category."""
    import os

    try:
        from azure.storage.blob import BlobServiceClient

        conn = os.environ.get(
            "AZURE_STORAGE_CONNECTION_STRING",
            os.environ.get("AZ_SOURCE_CONNECTION_STRING", ""),
        )
        svc = BlobServiceClient.from_connection_string(conn)
        cc = svc.get_container_client(container)
        prefix = f"{day_key}/{day_key}--{category}"
        pngs = [b.name for b in cc.list_blobs(name_starts_with=prefix) if b.name.endswith(".png")]
        # Also count MVBS echograms (different naming pattern)
        mvbs_prefix = f"{day_key}/{category}--mvbs"
        pngs += [b.name for b in cc.list_blobs(name_starts_with=mvbs_prefix) if b.name.endswith(".png")]
        return len(pngs)
    except Exception:
        return 0


def run_echogram_generation(
    client,
    source_day_zarrs: dict[str, dict[str, str]],
    denoised_day_zarrs: dict[str, dict[str, str]],
    mvbs_zarrs: dict[str, dict[str, str]],
    cfg: PipelineConfig,
    output_container: str,
) -> None:
    """Generate echograms for all days."""
    if cfg.skip_echograms:
        logger.info("Skipping echogram generation")
        return

    chunks = cfg.chunks.as_dict()

    # Build union of all day/category combos across source, denoised, and
    # MVBS zarrs so we don't skip combos that are missing a source zarr.
    all_keys: dict[str, set[str]] = {}
    for mapping in (source_day_zarrs, denoised_day_zarrs, mvbs_zarrs):
        for dk, cats in mapping.items():
            all_keys.setdefault(dk, set()).update(cats.keys())

    # Run on main thread — day datasets can exceed 30GB worker memory
    for day_key in sorted(all_keys):
        for category in sorted(all_keys[day_key]):
            # Skip if echograms already exist in Azure
            existing = _count_existing_echograms(output_container, day_key, category)
            if existing > 0:
                logger.info("  Skipping %s/%s — %d echograms already exist", day_key, category, existing)
                continue

            source_zarr = source_day_zarrs.get(day_key, {}).get(category)
            denoised_zarr = denoised_day_zarrs.get(day_key, {}).get(category)
            mvbs_zarr = mvbs_zarrs.get(day_key, {}).get(category)

            # Use denoised as source fallback when base source zarr is missing
            if not source_zarr and denoised_zarr:
                logger.info("  No source zarr for %s/%s — using denoised as source", day_key, category)
                source_zarr = denoised_zarr

            if not source_zarr:
                logger.warning("  No source or denoised zarr for %s/%s — skipping", day_key, category)
                continue

            try:
                result = generate_echograms_day(
                    source_zarr=source_zarr,
                    denoised_zarr=denoised_zarr,
                    mvbs_zarr=mvbs_zarr,
                    output_container=output_container,
                    chunks=chunks,
                    day_key=day_key,
                    category=category,
                    cruise_id=cfg.cruise_id,
                    colormap=cfg.colormap,
                )
                logger.info("  Echograms completed for %s/%s: %d files", day_key, category, len(result))
            except Exception as e:
                logger.warning("  Echogram failed for %s/%s: %s", day_key, category, e)


# ═══════════════════════════════════════════════════════════════════════════
# STAGE 9: PMTiles + COG heatmaps
# ═══════════════════════════════════════════════════════════════════════════

def export_nasc_and_generate_pmtiles(
    nasc_zarrs: dict[str, dict[str, str]],
    output_container: str,
    cruise_id: str,
    local_output_dir: Path,
) -> Path | None:
    """Export NASC to GeoParquet then generate track PMTiles.

    Returns path to generated PMTiles file, or None on failure.
    """
    from oceanstream.echodata.storage import open_sv_from_azure
    from oceanstream.echodata.compute import export_nasc_to_geoparquet

    geoparquet_dir = local_output_dir / "nasc_geoparquet"
    geoparquet_dir.mkdir(parents=True, exist_ok=True)

    for day_key, categories in nasc_zarrs.items():
        for category, zarr_path in categories.items():
            try:
                ds = open_sv_from_azure(zarr_path=zarr_path, container=output_container)
                export_nasc_to_geoparquet(
                    ds,
                    output_dir=geoparquet_dir,
                    campaign_id=cruise_id,
                    file_id=f"{day_key}_{category}",
                )
                ds.close()
            except Exception as e:
                logger.warning("NASC export failed for %s/%s: %s", day_key, category, e)

    # Generate PMTiles from the accumulated GeoParquet
    nasc_parquet_dir = geoparquet_dir / "nasc"
    if not nasc_parquet_dir.exists():
        logger.warning("No NASC GeoParquet data generated — skipping PMTiles")
        return None

    pmtiles_path = local_output_dir / "tiles" / "nasc_track.pmtiles"
    pmtiles_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        from oceanstream.geotrack.tiling import generate_pmtiles_from_geoparquet

        generate_pmtiles_from_geoparquet(
            geoparquet_root=nasc_parquet_dir,
            pmtiles_path=pmtiles_path,
            min_zoom=2,
            max_zoom=12,
        )
        logger.info("Generated PMTiles: %s", pmtiles_path)
    except ImportError:
        logger.warning("geotrack tiling not available (missing geopandas?) — skipping PMTiles")
        pmtiles_path = None
    except Exception as e:
        logger.warning("PMTiles generation failed: %s", e)
        pmtiles_path = None

    return pmtiles_path


def generate_cog_heatmaps(
    mvbs_zarrs: dict[str, dict[str, str]],
    output_container: str,
    cruise_id: str,
    local_output_dir: Path,
) -> list[Path]:
    """Generate Cloud-Optimized GeoTIFF heatmaps from daily MVBS data.

    Returns list of generated COG file paths.
    """
    cog_files = []
    cog_dir = local_output_dir / "cog_heatmaps"
    cog_dir.mkdir(parents=True, exist_ok=True)

    try:
        import numpy as np
        import rasterio
        from rasterio.transform import from_bounds
    except ImportError:
        logger.warning("rasterio not installed — skipping COG heatmap generation")
        return cog_files

    from oceanstream.echodata.storage import open_sv_from_azure

    for day_key, categories in mvbs_zarrs.items():
        for category, zarr_path in categories.items():
            try:
                ds = open_sv_from_azure(zarr_path=zarr_path, container=output_container)
                ds = ds.compute()

                if "latitude" not in ds or "longitude" not in ds:
                    logger.warning("No lat/lon in MVBS for %s/%s — skipping COG", day_key, category)
                    continue

                # For each channel, create a COG raster
                channels = ds["channel"].values if "channel" in ds.dims else [None]
                sv_var = "Sv" if "Sv" in ds else list(ds.data_vars)[0]

                for ch_idx, ch in enumerate(channels):
                    if ch is not None:
                        data_2d = ds[sv_var].isel(channel=ch_idx).values
                    else:
                        data_2d = ds[sv_var].values

                    if data_2d.ndim != 2:
                        continue

                    lat = ds["latitude"].values if "latitude" in ds else None
                    lon = ds["longitude"].values if "longitude" in ds else None
                    if lat is None or lon is None:
                        continue

                    lat_valid = lat[~np.isnan(lat)]
                    lon_valid = lon[~np.isnan(lon)]
                    if len(lat_valid) == 0 or len(lon_valid) == 0:
                        continue

                    ch_label = str(ch) if ch is not None else "all"
                    cog_path = cog_dir / f"{day_key}_{category}_{ch_label}_mvbs.tif"

                    height, width = data_2d.shape
                    transform = from_bounds(
                        float(lon_valid.min()), float(lat_valid.min()),
                        float(lon_valid.max()), float(lat_valid.max()),
                        width, height,
                    )

                    with rasterio.open(
                        cog_path,
                        "w",
                        driver="GTiff",
                        height=height,
                        width=width,
                        count=1,
                        dtype="float32",
                        crs="EPSG:4326",
                        transform=transform,
                        compress="deflate",
                        tiled=True,
                        blockxsize=256,
                        blockysize=256,
                    ) as dst:
                        dst.write(np.nan_to_num(data_2d, nan=-9999).astype("float32"), 1)
                        dst.update_tags(ns="rio_overview", resampling="average")

                    cog_files.append(cog_path)
                    logger.info("  COG heatmap: %s", cog_path)

                ds.close()
            except Exception as e:
                logger.warning("COG generation failed for %s/%s: %s", day_key, category, e)

    return cog_files


def run_tiles_and_cog(
    client,
    nasc_zarrs: dict[str, dict[str, str]],
    mvbs_zarrs: dict[str, dict[str, str]],
    cfg: PipelineConfig,
    output_container: str,
) -> None:
    """Generate PMTiles from NASC and COG heatmaps from MVBS."""
    if cfg.skip_pmtiles:
        logger.info("Skipping PMTiles/COG generation")
        return

    # PMTiles from NASC
    if nasc_zarrs:
        export_nasc_and_generate_pmtiles(
            nasc_zarrs=nasc_zarrs,
            output_container=output_container,
            cruise_id=cfg.cruise_id,
            local_output_dir=cfg.local_output_dir,
        )

    # COG from MVBS
    if mvbs_zarrs:
        generate_cog_heatmaps(
            mvbs_zarrs=mvbs_zarrs,
            output_container=output_container,
            cruise_id=cfg.cruise_id,
            local_output_dir=cfg.local_output_dir,
        )


# ═══════════════════════════════════════════════════════════════════════════
# STAGE 10: Campaign-wide Zarr aggregation
# ═══════════════════════════════════════════════════════════════════════════

def build_campaign_zarr(
    day_zarrs: dict[str, dict[str, str]],
    product_suffix: str,
    output_container: str,
    cruise_id: str,
    chunks: dict,
    output_name: str,
) -> str | None:
    """Concatenate all daily Zarrs into a single campaign-wide Zarr.

    Uses incremental append to avoid memory pressure for large datasets.

    Returns the campaign zarr path, or None on failure.
    """
    import xarray as xr
    from oceanstream.echodata.storage import (
        open_sv_from_azure, get_azure_zarr_store,
    )
    from oceanstream.echodata.utils.encoding import fix_chunking

    campaign_zarr = f"{cruise_id}/{output_name}"
    logger.info("Building campaign Zarr: %s", campaign_zarr)

    # Collect all day zarr paths (across categories, sorted by day)
    all_paths = []
    for day_key in sorted(day_zarrs.keys()):
        for category, zarr_path in day_zarrs[day_key].items():
            if zarr_path.endswith(f"{product_suffix}.zarr") or product_suffix == "":
                all_paths.append((day_key, category, zarr_path))

    if not all_paths:
        logger.warning("No Zarr stores found for campaign aggregation")
        return None

    first = True
    for day_key, category, zarr_path in all_paths:
        try:
            ds = open_sv_from_azure(zarr_path=zarr_path, container=output_container, chunks=chunks)
            ds = ds.chunk(chunks)
            ds = fix_chunking(ds)

            if first:
                store = get_azure_zarr_store(campaign_zarr, container=output_container, mode="w")
                ds.to_zarr(store, mode="w")
                first = False
            else:
                store = get_azure_zarr_store(campaign_zarr, container=output_container, mode="a")
                ds.to_zarr(store, append_dim="ping_time")

            ds.close()
            logger.info("  Appended %s/%s to campaign Zarr", day_key, category)
        except Exception as e:
            logger.warning("Failed to append %s/%s: %s", day_key, category, e)

    logger.info("Campaign Zarr complete: %s", campaign_zarr)
    return campaign_zarr


def run_campaign_aggregation(
    client,
    day_zarrs: dict[str, dict[str, str]],
    mvbs_zarrs: dict[str, dict[str, str]],
    cfg: PipelineConfig,
    output_container: str,
) -> None:
    """Build campaign-wide Zarr stores."""
    if not cfg.build_campaign_zarr:
        logger.info("Skipping campaign Zarr aggregation")
        return

    chunks = cfg.chunks.as_dict()

    # Campaign MVBS Zarr
    if mvbs_zarrs:
        build_campaign_zarr(
            day_zarrs=mvbs_zarrs,
            product_suffix="_mvbs",
            output_container=output_container,
            cruise_id=cfg.cruise_id,
            chunks=chunks,
            output_name="campaign_mvbs.zarr",
        )

    # Campaign Sv Zarr (experimental — large data)
    if cfg.build_campaign_sv_zarr:
        logger.info("Building campaign Sv Zarr (experimental)")
        build_campaign_zarr(
            day_zarrs=day_zarrs,
            product_suffix="",
            output_container=output_container,
            cruise_id=cfg.cruise_id,
            chunks=chunks,
            output_name="campaign_sv.zarr",
        )


# ═══════════════════════════════════════════════════════════════════════════
# MAIN PIPELINE ORCHESTRATOR
# ═══════════════════════════════════════════════════════════════════════════

def run_pipeline(cfg: PipelineConfig) -> None:
    """Execute the full processing pipeline."""
    from oceanstream.echodata.storage import ensure_container_exists, generate_container_name

    pipeline_start = time.time()

    # Output container
    output_container = cfg.output_container
    if not output_container:
        output_container = generate_container_name(cfg.cruise_id)
    ensure_container_exists(output_container, public_access="container")
    logger.info("Output container: %s", output_container)

    # Stage 0: Dask
    client = setup_dask_client(cfg)

    try:
        # Stage 1: Discover files
        t0 = time.time()
        files_list = discover_files(cfg)
        if not files_list:
            logger.error("No files found — aborting")
            return
        logger.info("STAGE 1 complete: %d files discovered (%.1fs)", len(files_list), time.time() - t0)

        # Stage 2: Per-file processing (GPS merge)
        t0 = time.time()
        file_results = process_files_parallel(client, files_list, cfg, output_container)
        logger.info("STAGE 2 complete: %d files processed (%.1fs)", len(file_results), time.time() - t0)

        # Stage 3: Day-level concatenation
        t0 = time.time()
        source_day_zarrs = run_day_concatenation(client, file_results, files_list, cfg, output_container)
        logger.info("STAGE 3 complete: %d days concatenated (%.1fs)", len(source_day_zarrs), time.time() - t0)

        # Stage 4: Denoise
        t0 = time.time()
        denoised_day_zarrs = run_denoising(client, source_day_zarrs, cfg, output_container)
        logger.info("STAGE 4 complete: denoising (%.1fs)", time.time() - t0)

        # Stage 5: Seabed masking (optional — not used in export76 pipeline)
        t0 = time.time()
        if cfg.apply_seabed_mask:
            masked_day_zarrs = run_seabed_masking(client, denoised_day_zarrs, cfg, output_container)
            logger.info("STAGE 5 complete: seabed masking (%.1fs)", time.time() - t0)
            mvbs_input = masked_day_zarrs
        else:
            logger.info("STAGE 5 skipped: seabed masking disabled (%.1fs)", time.time() - t0)
            mvbs_input = denoised_day_zarrs

        # Stage 6 & 7: MVBS + NASC
        t0 = time.time()
        mvbs_zarrs = run_mvbs_computation(client, mvbs_input, cfg, output_container)
        nasc_zarrs = run_nasc_computation(client, mvbs_input, cfg, output_container)
        logger.info("STAGE 6+7 complete: MVBS + NASC (%.1fs)", time.time() - t0)

        # Stage 8: Echograms
        t0 = time.time()
        run_echogram_generation(
            client, source_day_zarrs, denoised_day_zarrs, mvbs_zarrs, cfg, output_container,
        )
        logger.info("STAGE 8 complete: echograms (%.1fs)", time.time() - t0)

        # Stage 9: PMTiles + COG
        t0 = time.time()
        run_tiles_and_cog(client, nasc_zarrs, mvbs_zarrs, cfg, output_container)
        logger.info("STAGE 9 complete: PMTiles + COG (%.1fs)", time.time() - t0)

        # Stage 10: Campaign aggregation
        t0 = time.time()
        run_campaign_aggregation(client, mvbs_input, mvbs_zarrs, cfg, output_container)
        logger.info("STAGE 10 complete: campaign Zarr (%.1fs)", time.time() - t0)

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
        description="Batch process Saildrone TPOS 2023 echodata campaign",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    # Mode shortcuts
    parser.add_argument(
        "--local-test", action="store_true",
        help="Quick local test (2 days, 4 workers, no PMTiles)",
    )
    parser.add_argument(
        "--from-env", action="store_true",
        help="Load config from environment variables",
    )

    # Data source
    parser.add_argument("--cruise-id", default="SD_TPOS2023_v03")
    parser.add_argument("--source-container", default="processed")
    parser.add_argument("--output-container", default="")
    parser.add_argument("--gps-data-file", help="Path to exported GPS JSON")
    parser.add_argument("--file-list", help="Path to pre-generated file list JSON (from generate_file_list.py)")

    # Date range
    parser.add_argument("--start-date", help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end-date", help="End date (YYYY-MM-DD)")

    # Dask
    parser.add_argument("--scheduler", help="Dask scheduler address (tcp://...)")
    parser.add_argument("--n-workers", type=int, default=4)
    parser.add_argument("--memory-limit", default="12GB")
    parser.add_argument("--batch-size", type=int, default=6)

    # Processing toggles
    parser.add_argument("--skip-denoising", action="store_true")
    parser.add_argument("--skip-echograms", action="store_true")
    parser.add_argument("--skip-pmtiles", action="store_true")
    parser.add_argument("--skip-nasc", action="store_true")
    parser.add_argument("--skip-mvbs", action="store_true")
    parser.add_argument("--skip-seabed-mask", action="store_true")
    parser.add_argument("--save-netcdf", action="store_true", help="Save all products as NetCDF")
    parser.add_argument("--save-nasc-netcdf", action="store_true", help="Save NASC as NetCDF")
    parser.add_argument("--save-mvbs-netcdf", action="store_true", help="Save MVBS as NetCDF")
    parser.add_argument("--per-file-netcdf", action="store_true", help="Export per-file NetCDF in Stage 2")
    parser.add_argument("--per-file-echograms", action="store_true", help="Generate per-file echograms in Stage 2")
    parser.add_argument("--build-campaign-zarr", action="store_true", default=True)
    parser.add_argument("--build-campaign-sv-zarr", action="store_true")

    # Denoise config file
    parser.add_argument(
        "--denoise-config",
        help="TOML file with denoise parameters (loads EchodataConfig.from_toml)",
    )

    # MVBS/NASC params
    parser.add_argument("--mvbs-range-bin", default="1m")
    parser.add_argument("--mvbs-ping-time-bin", default="10s")
    parser.add_argument("--nasc-range-bin", default="10m")
    parser.add_argument("--nasc-dist-bin", default="0.5nmi")

    # Azure VM
    parser.add_argument("--auto-deallocate", action="store_true")
    parser.add_argument("--vm-name", default="oceanstream-batch-vm")

    # Output
    parser.add_argument("--output-dir", default="/tmp/oceanstream/batch_output")
    parser.add_argument("--colormap", default="ocean_r")

    args = parser.parse_args()

    # Build config
    if args.local_test:
        cfg = PipelineConfig.for_local_test()
        if args.start_date:
            cfg.start_date = datetime.fromisoformat(args.start_date)
        if args.end_date:
            cfg.end_date = datetime.fromisoformat(args.end_date)
        cfg.file_list_file = getattr(args, 'file_list', None)
        cfg.output_container = args.output_container or cfg.output_container
        cfg.save_to_netcdf = args.save_netcdf
        cfg.save_nasc_to_netcdf = args.save_nasc_netcdf or cfg.save_nasc_to_netcdf
        cfg.save_mvbs_to_netcdf = args.save_mvbs_netcdf or cfg.save_mvbs_to_netcdf
        cfg.per_file_netcdf = args.per_file_netcdf or cfg.per_file_netcdf
        cfg.per_file_echograms = args.per_file_echograms or cfg.per_file_echograms
        cfg.skip_denoising = args.skip_denoising
        cfg.skip_echograms = args.skip_echograms
        cfg.skip_pmtiles = args.skip_pmtiles
        cfg.skip_nasc = args.skip_nasc
        cfg.skip_mvbs = args.skip_mvbs
        cfg.colormap = args.colormap
        return cfg

    if args.from_env:
        cfg = PipelineConfig.from_env()
    else:
        cfg = PipelineConfig()

    # Override from CLI args
    cfg.cruise_id = args.cruise_id
    cfg.source_container = args.source_container
    cfg.output_container = args.output_container
    cfg.gps_data_file = args.gps_data_file
    cfg.file_list_file = getattr(args, 'file_list', None)
    cfg.colormap = args.colormap
    cfg.local_output_dir = Path(args.output_dir)
    cfg.batch_size = args.batch_size

    if args.start_date:
        cfg.start_date = datetime.fromisoformat(args.start_date)
    if args.end_date:
        cfg.end_date = datetime.fromisoformat(args.end_date)

    # Dask
    cfg.dask.scheduler_address = args.scheduler
    cfg.dask.n_workers = args.n_workers
    cfg.dask.memory_limit = args.memory_limit

    # Processing toggles
    cfg.skip_denoising = args.skip_denoising
    cfg.skip_echograms = args.skip_echograms
    cfg.skip_pmtiles = args.skip_pmtiles
    cfg.skip_nasc = args.skip_nasc
    cfg.skip_mvbs = args.skip_mvbs
    cfg.apply_seabed_mask = not args.skip_seabed_mask
    cfg.save_to_netcdf = args.save_netcdf
    cfg.save_nasc_to_netcdf = args.save_nasc_netcdf
    cfg.save_mvbs_to_netcdf = args.save_mvbs_netcdf
    cfg.per_file_netcdf = args.per_file_netcdf
    cfg.per_file_echograms = args.per_file_echograms
    cfg.build_campaign_zarr = args.build_campaign_zarr
    cfg.build_campaign_sv_zarr = args.build_campaign_sv_zarr

    # MVBS / NASC
    cfg.mvbs.range_bin = args.mvbs_range_bin
    cfg.mvbs.ping_time_bin = args.mvbs_ping_time_bin
    cfg.nasc.range_bin = args.nasc_range_bin
    cfg.nasc.dist_bin = args.nasc_dist_bin

    # Denoise config from TOML
    if args.denoise_config:
        from oceanstream.echodata.config import EchodataConfig

        echo_cfg = EchodataConfig.from_toml(Path(args.denoise_config))
        # Replace denoise params from the TOML-loaded config
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

    # Azure VM
    cfg.azure_vm.auto_deallocate = args.auto_deallocate
    cfg.azure_vm.vm_name = args.vm_name

    return cfg


def main():
    cfg = parse_args()

    logger.info("=" * 70)
    logger.info("Saildrone TPOS 2023 Batch Processing Pipeline")
    logger.info("=" * 70)
    logger.info("Cruise: %s", cfg.cruise_id)
    logger.info("Source: %s", cfg.source_container)
    logger.info("Date range: %s → %s", cfg.start_date, cfg.end_date)
    logger.info("Dask: %d workers, %s each", cfg.dask.n_workers, cfg.dask.memory_limit)
    logger.info("Denoise: %s", "enabled" if not cfg.skip_denoising else "disabled")
    logger.info("=" * 70)

    try:
        run_pipeline(cfg)
    except KeyboardInterrupt:
        logger.info("Pipeline interrupted by user")
    except Exception:
        logger.exception("Pipeline failed with error")
        raise
    finally:
        if cfg.azure_vm.auto_deallocate:
            from infra import deallocate_vm
            logger.info("Auto-deallocating VM...")
            try:
                deallocate_vm(cfg.azure_vm)
            except Exception as e:
                logger.warning("VM deallocation failed: %s", e)


if __name__ == "__main__":
    main()
