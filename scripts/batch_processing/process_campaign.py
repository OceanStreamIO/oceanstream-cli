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

        # Build per-frequency params when frequency-specific mode is on,
        # mirroring the Prefect flow's per-channel dispatch.
        if denoise_config.use_frequency_specific:
            bgn_freq_params = denoise_config.to_frequency_keyed_params("background")
        else:
            bgn_freq_params = None
        bgn_global = denoise_config.to_background_params()

        # Capture parent attrs for provenance propagation.
        # echopype's @add_processing_level decorator on remove_background_noise
        # requires input_processing_level when lat/lon are present.
        # compute_Sv is decorated @add_processing_level("L2A"), so the Sv dataset
        # should logically be "Level 2A".  It may be absent if lat/lon were added
        # after compute_Sv (e.g. external GPS interpolation).  Set explicitly.
        parent_attrs = dict(ds_denoised.attrs)
        parent_attrs.setdefault("processing_level", "Level 2A")
        parent_attrs["input_processing_level"] = parent_attrs["processing_level"]

        def _remove_bgn_one_channel(ch_ds):
            # Propagate processing-level attrs lost by groupby channel split
            ch_ds.attrs.update(parent_attrs)

            # Resolve params: per-frequency if available, else global
            if bgn_freq_params is not None:
                freq = str(int(ch_ds["frequency_nominal"]))
                opts = bgn_freq_params.get(freq, bgn_global)
            else:
                opts = bgn_global

            result = ep_remove_background_noise(
                ch_ds,
                ping_num=opts.get("ping_window", 50),
                range_sample_num=opts.get("range_window", 20),
                SNR_threshold=opts.get("SNR_threshold", "3.0dB"),
                background_noise_max=opts.get("background_noise_max"),
            )
            # Use Sv_corrected (background-noise-removed), not original Sv
            return result["Sv_corrected"] if "Sv_corrected" in result else result["Sv"]

        sv_clean = ds_denoised.groupby("channel").map(_remove_bgn_one_channel)
        sv_clean.name = "Sv"
        ds_denoised["Sv"] = sv_clean
        logger.info("  Background noise removal applied per channel")

    # range_sample is preserved as the dimension; depth is a separate variable
    # added by ep.consolidate.add_depth() in Stage 4. Both are preserved
    # through denoising for downstream compute_MVBS/compute_NASC.

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
    surface_exclusion_depth: float = 0.0,
) -> str:
    """Compute MVBS for a day Zarr.

    Uses a direct xarray resample+groupby implementation to avoid
    echopype compute_MVBS incompatibility with xarray 2026.x.

    Returns MVBS zarr path.
    """
    import dask
    import numpy as np
    import pandas as pd
    import xarray as xr
    from oceanstream.echodata.storage import open_sv_from_azure, save_dataset_to_azure

    logger.info("Computing MVBS %s/%s", day_key, category)

    ds = open_sv_from_azure(zarr_path=zarr_path, container=output_container)
    ds = _ensure_position_coords(ds)

    range_var = "depth" if "depth" in ds else "echo_range"
    logger.info("  range_var=%s, range_bin=%s, ping_time_bin=%s", range_var, range_bin, ping_time_bin)

    # Exclude near-surface bins that contaminate MVBS
    if surface_exclusion_depth > 0 and "depth" in ds:
        ds = ds.where(ds["depth"] >= surface_exclusion_depth)
        logger.info("  Surface exclusion: masked depth < %.1fm", surface_exclusion_depth)

    with dask.config.set(scheduler="synchronous"):
        sv_linear = 10.0 ** (ds["Sv"].compute() / 10.0)

        # Parse range_bin (e.g. "1m" → 1.0)
        range_bin_m = float(range_bin.replace("m", ""))
        range_vals = ds[range_var].values
        if range_vals.ndim > 1:
            range_vals = range_vals[0] if range_vals.ndim == 2 else range_vals[0, 0]
        range_min = float(np.nanmin(range_vals))
        range_max = float(np.nanmax(range_vals))
        range_edges = np.arange(range_min, range_max + range_bin_m, range_bin_m)
        range_labels = (range_edges[:-1] + range_edges[1:]) / 2.0

        # Assign depth bins
        range_1d = ds[range_var]
        if range_1d.ndim > 1:
            range_1d = range_1d.isel({d: 0 for d in range_1d.dims if d != "range_sample"})
        depth_bin = np.digitize(range_1d.values, range_edges) - 1
        depth_bin = np.clip(depth_bin, 0, len(range_labels) - 1)

        # Group by depth bins → mean linear Sv
        sv_linear = sv_linear.assign_coords(depth_bin=("range_sample", depth_bin))
        sv_depth_mean = sv_linear.groupby("depth_bin").mean(dim="range_sample")

        # Resample in time
        sv_time_depth = sv_depth_mean.resample(ping_time=ping_time_bin).mean()

        # Back to dB
        mvbs_sv = 10.0 * np.log10(sv_time_depth)
        mvbs_sv = mvbs_sv.rename({"depth_bin": range_var})
        mvbs_sv = mvbs_sv.assign_coords({range_var: range_labels[:mvbs_sv.sizes[range_var]]})

        ds_mvbs = xr.Dataset({"Sv": mvbs_sv})

        # Average lat/lon per time bin
        if "latitude" in ds:
            lat_mean = ds["latitude"].compute().resample(ping_time=ping_time_bin).mean()
            ds_mvbs["latitude"] = lat_mean
        if "longitude" in ds:
            lon_mean = ds["longitude"].compute().resample(ping_time=ping_time_bin).mean()
            ds_mvbs["longitude"] = lon_mean

    ds_mvbs.attrs["processing"] = "MVBS computed (direct resample+groupby)"
    ds_mvbs.attrs["range_bin"] = range_bin
    ds_mvbs.attrs["ping_time_bin"] = ping_time_bin

    output_zarr = f"{day_key}/{day_key}--{category}--mvbs.zarr"
    save_dataset_to_azure(ds_mvbs, zarr_path=output_zarr, container=output_container)

    if save_netcdf:
        _save_netcdf_to_blob(ds_mvbs, f"{day_key}/{day_key}--{category}--mvbs.nc", output_container)

    logger.info("  Saved MVBS: %s", output_zarr)

    ds.close()
    del ds, ds_mvbs
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
                surface_exclusion_depth=cfg.surface_exclusion_depth,
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
    surface_exclusion_depth: float = 0.0,
) -> str:
    """Compute NASC for a day Zarr.

    Uses a direct integration implementation to avoid
    echopype compute_NASC incompatibility with xarray 2026.x.

    NASC = 4π × 1852² × ∫ sv dz  (integrated over depth, binned by distance)

    Returns NASC zarr path.
    """
    import dask
    import numpy as np
    import xarray as xr
    from oceanstream.echodata.storage import open_sv_from_azure, save_dataset_to_azure

    logger.info("Computing NASC %s/%s", day_key, category)

    ds = open_sv_from_azure(zarr_path=zarr_path, container=output_container)
    ds = _ensure_position_coords(ds)

    if "depth" not in ds and "depth" not in ds.coords:
        logger.warning("  No depth variable in %s/%s — skipping NASC", day_key, category)
        ds.close()
        return ""
    has_lat_lon = ("latitude" in ds or "latitude" in ds.coords) and (
        "longitude" in ds or "longitude" in ds.coords
    )
    if not has_lat_lon:
        logger.warning("  No lat/lon in %s/%s — skipping NASC", day_key, category)
        ds.close()
        return ""

    # Exclude near-surface bins
    if surface_exclusion_depth > 0 and "depth" in ds:
        ds = ds.where(ds["depth"] >= surface_exclusion_depth)
        logger.info("  Surface exclusion: masked depth < %.1fm", surface_exclusion_depth)

    with dask.config.set(scheduler="synchronous"):
        sv_linear = 10.0 ** (ds["Sv"].compute() / 10.0)

        # Parse range_bin (e.g. "10m" → 10.0)
        range_bin_m = float(range_bin.replace("m", ""))

        # Parse dist_bin (e.g. "0.5nmi" → 0.5)
        dist_bin_nmi = float(dist_bin.replace("nmi", ""))

        # Get depth values
        depth = ds["depth"]
        if depth.ndim > 1:
            depth = depth.isel({d: 0 for d in depth.dims if d != "range_sample"})

        # Compute depth resolution (metres per range_sample)
        depth_vals = depth.values
        dz = np.nanmedian(np.diff(depth_vals))

        # Integrate sv over depth bins (sum × dz)
        range_edges = np.arange(
            float(np.nanmin(depth_vals)),
            float(np.nanmax(depth_vals)) + range_bin_m,
            range_bin_m,
        )
        range_labels = (range_edges[:-1] + range_edges[1:]) / 2.0

        depth_bin = np.digitize(depth_vals, range_edges) - 1
        depth_bin = np.clip(depth_bin, 0, len(range_labels) - 1)
        sv_linear = sv_linear.assign_coords(depth_bin=("range_sample", depth_bin))
        # Sum sv × dz per depth bin = partial NASC integral
        sv_integrated = sv_linear.groupby("depth_bin").sum(dim="range_sample") * abs(dz)

        # Compute cumulative distance along track (nautical miles)
        # Extract 1D lat/lon (ping_time only)
        lat_da = ds["latitude"].compute()
        lon_da = ds["longitude"].compute()
        if lat_da.ndim > 1:
            # Select first channel/range_sample to get 1D ping_time series
            lat_da = lat_da.isel({d: 0 for d in lat_da.dims if d != "ping_time"})
            lon_da = lon_da.isel({d: 0 for d in lon_da.dims if d != "ping_time"})
        lat = lat_da.values
        lon = lon_da.values
        dlat = np.diff(lat)
        dlon = np.diff(lon)
        # Haversine for nm distances
        R_nm = 3440.065  # Earth radius in nautical miles
        a = np.sin(np.radians(dlat / 2)) ** 2 + np.cos(np.radians(lat[:-1])) * np.cos(
            np.radians(lat[1:])
        ) * np.sin(np.radians(dlon / 2)) ** 2
        c = 2 * np.arctan2(np.sqrt(a), np.sqrt(1 - a))
        dist_nm = R_nm * c
        cum_dist = np.concatenate([[0], np.cumsum(dist_nm)])

        # Bin by distance
        dist_edges = np.arange(0, cum_dist[-1] + dist_bin_nmi, dist_bin_nmi)
        dist_labels = (dist_edges[:-1] + dist_edges[1:]) / 2.0
        if len(dist_labels) == 0:
            dist_labels = np.array([cum_dist[-1] / 2])
            dist_edges = np.array([0, cum_dist[-1]])

        dist_bin_idx = np.digitize(cum_dist, dist_edges) - 1
        dist_bin_idx = np.clip(dist_bin_idx, 0, len(dist_labels) - 1)

        sv_integrated = sv_integrated.assign_coords(dist_bin=("ping_time", dist_bin_idx))

        # NASC = 4π × 1852² × mean(sv_integrated) per distance bin
        NASC_COEFF = 4 * np.pi * 1852**2
        nasc_vals = sv_integrated.groupby("dist_bin").mean(dim="ping_time") * NASC_COEFF

        nasc_vals = nasc_vals.rename({"depth_bin": "depth", "dist_bin": "distance"})
        nasc_vals = nasc_vals.assign_coords(
            depth=range_labels[:nasc_vals.sizes["depth"]],
            distance=dist_labels[:nasc_vals.sizes["distance"]],
        )

        ds_nasc = xr.Dataset({"NASC": nasc_vals})

        # Mean lat/lon per distance bin
        lat_da = xr.DataArray(lat, dims=["ping_time"], coords={"dist_bin": ("ping_time", dist_bin_idx)})
        lon_da = xr.DataArray(lon, dims=["ping_time"], coords={"dist_bin": ("ping_time", dist_bin_idx)})
        ds_nasc["latitude"] = lat_da.groupby("dist_bin").mean().rename({"dist_bin": "distance"}).assign_coords(
            distance=dist_labels[:ds_nasc.sizes["distance"]]
        )
        ds_nasc["longitude"] = lon_da.groupby("dist_bin").mean().rename({"dist_bin": "distance"}).assign_coords(
            distance=dist_labels[:ds_nasc.sizes["distance"]]
        )

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
                surface_exclusion_depth=cfg.surface_exclusion_depth,
            )
            if nasc_path:
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
                if ds_pruned.sizes.get("ping_time", 0) == 0:
                    logger.info("  Denoised-pruned: all pings dropped — skipping echogram")
                else:
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
                    logger.info("  Denoised-pruned echograms: %d files", len(files))
                del ds_pruned
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


def generate_nasc_echograms_day(
    nasc_zarr: str,
    output_container: str,
    day_key: str,
    category: str,
    cruise_id: str,
    surface_exclusion_depth: float = 10.0,
    max_depth: float = 500.0,
    vmin: float = -5,
    vmax: float = 30,
) -> list[str]:
    """Generate NASC echogram PNGs for a day/category.

    Produces two separate images per channel:
      - Depth-resolved echogram (distance × depth heatmap of NASC_log)
      - Depth-integrated NASC transect (sA bar chart along distance)

    Surface bins above *surface_exclusion_depth* are excluded (platform noise).
    """
    import gc

    import cmocean  # noqa: F401 — registers colormaps
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np
    from oceanstream.echodata.storage import open_sv_from_azure

    ds = open_sv_from_azure(zarr_path=nasc_zarr, container=output_container)
    ds = ds.load()

    depth = ds.depth.values
    dist = ds.distance.values
    depth_mask = (depth >= surface_exclusion_depth) & (depth <= max_depth)
    depth_plot = depth[depth_mask]

    all_files: list[str] = []
    n_channels = ds.sizes.get("channel", 1)
    base = f"{day_key}--{category}"

    for ch_idx in range(n_channels):
        ch_name = str(ds.channel.values[ch_idx])
        ch_label = ch_name.split("|")[0].strip() if "|" in ch_name else ch_name
        ch_suffix = f"_ch{ch_idx}" if n_channels > 1 else ""

        nasc_log = ds["NASC_log"].isel(channel=ch_idx).values[:, depth_mask]
        nasc_lin = ds["NASC"].isel(channel=ch_idx).values[:, depth_mask]

        # ── 1. Depth-resolved echogram ────────────────────────────
        fig, ax = plt.subplots(figsize=(14, 5))
        im = ax.pcolormesh(
            dist, depth_plot, nasc_log.T,
            shading="auto", cmap="cmo.haline", vmin=vmin, vmax=vmax,
        )
        ax.invert_yaxis()
        ax.set_xlabel("Distance (nmi)")
        ax.set_ylabel("Depth (m)")
        ax.set_title(
            f"NASC — {base} | {ch_label}\n"
            f"depth-resolved ({surface_exclusion_depth:.0f}–{max_depth:.0f} m)"
        )
        cb = fig.colorbar(im, ax=ax, pad=0.01, aspect=30)
        cb.set_label("NASC_log (dB re 1 m²/nmi²)")
        plt.tight_layout()

        fname1 = f"{base}--nasc{ch_suffix}.png"
        out1 = _save_echogram_png(fig, fname1, day_key, output_container, cruise_id)
        all_files.append(out1)
        plt.close(fig)

        # ── 2. Depth-integrated sA transect ───────────────────────
        integrated = np.nansum(nasc_lin, axis=1)
        bar_w = np.diff(dist).mean() * 0.9 if len(dist) > 1 else 0.5

        fig, ax = plt.subplots(figsize=(14, 3))
        ax.bar(dist, integrated, width=bar_w, color="steelblue", edgecolor="none", alpha=0.8)
        ax.set_xlabel("Distance (nmi)")
        ax.set_ylabel("sA (m²/nmi²)")
        ax.set_title(
            f"Depth-integrated NASC — {base} | {ch_label}\n"
            f"({surface_exclusion_depth:.0f}–{max_depth:.0f} m)"
        )
        ax.set_xlim(dist[0] - bar_w, dist[-1] + bar_w)
        plt.tight_layout()

        fname2 = f"{base}--nasc-integrated{ch_suffix}.png"
        out2 = _save_echogram_png(fig, fname2, day_key, output_container, cruise_id)
        all_files.append(out2)
        plt.close(fig)

    ds.close()
    del ds
    gc.collect()
    return all_files


def _save_echogram_png(
    fig, filename: str, day_key: str, container: str, cruise_id: str,
) -> str:
    """Save a matplotlib figure and upload to blob storage (or local output).

    Returns the local file path.
    """
    import os
    from pathlib import Path

    tmp_dir = Path(f"/tmp/osechograms/{cruise_id}/{day_key}")
    tmp_dir.mkdir(parents=True, exist_ok=True)
    local_path = tmp_dir / filename
    fig.savefig(str(local_path), dpi=150, bbox_inches="tight")

    blob_path = f"{day_key}/{filename}"
    try:
        from oceanstream.echodata.storage import upload_file_to_blob
        upload_file_to_blob(str(local_path), blob_path, container=container)
        logger.info("  Saved %s", blob_path)
    except Exception as e:
        logger.debug("  Upload skipped for %s: %s", blob_path, e)

    return str(local_path)


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
    nasc_zarrs: dict[str, dict[str, str]] | None = None,
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

    # ── NASC echograms ────────────────────────────────────────────
    if nasc_zarrs:
        for day_key in sorted(nasc_zarrs):
            for category, nasc_zarr in sorted(nasc_zarrs[day_key].items()):
                try:
                    files = generate_nasc_echograms_day(
                        nasc_zarr=nasc_zarr,
                        output_container=output_container,
                        day_key=day_key,
                        category=category,
                        cruise_id=cfg.cruise_id,
                        surface_exclusion_depth=cfg.surface_exclusion_depth,
                    )
                    logger.info("  NASC echograms for %s/%s: %d files", day_key, category, len(files))
                except Exception as e:
                    logger.warning("  NASC echogram failed for %s/%s: %s", day_key, category, e)


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
            minzoom=2,
            maxzoom=12,
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
    """Concatenate all daily Zarrs into per-category campaign-wide Zarrs.

    Different pulse categories (long_pulse, short_pulse) have different
    channel dimensions, so they get separate campaign zarrs.

    Returns the first campaign zarr path, or None on failure.
    """
    import xarray as xr
    from oceanstream.echodata.storage import (
        open_sv_from_azure, get_azure_zarr_store,
    )
    from oceanstream.echodata.utils.encoding import fix_chunking

    # Group paths by category so each gets its own campaign zarr
    by_category: dict[str, list[tuple[str, str]]] = {}
    for day_key in sorted(day_zarrs.keys()):
        for category, zarr_path in day_zarrs[day_key].items():
            if product_suffix == "" or zarr_path.endswith(f"--{product_suffix.lstrip('_')}.zarr"):
                by_category.setdefault(category, []).append((day_key, zarr_path))

    if not by_category:
        logger.warning("No Zarr stores found for campaign aggregation")
        return None

    first_zarr = None
    for category, paths in sorted(by_category.items()):
        stem, ext = output_name.rsplit(".", 1)
        cat_name = f"{stem}_{category}.{ext}"
        campaign_zarr = f"{cruise_id}/{cat_name}"
        logger.info("Building campaign Zarr: %s (%d days)", campaign_zarr, len(paths))

        first = True
        for day_key, zarr_path in paths:
            try:
                ds = open_sv_from_azure(
                    zarr_path=zarr_path, container=output_container, chunks=None,
                )
                # Filter chunk spec to only dims present in this dataset
                ds_chunks = {k: v for k, v in chunks.items() if k in ds.dims}
                ds = ds.chunk(ds_chunks)
                # Clear stale encoding to prevent chunk overlap errors
                for var in ds.data_vars:
                    ds[var].encoding.clear()
                for coord in ds.coords:
                    ds[coord].encoding.clear()

                if first:
                    store = get_azure_zarr_store(campaign_zarr, container=output_container, mode="w")
                    ds.to_zarr(store, mode="w")
                    first = False
                else:
                    store = get_azure_zarr_store(campaign_zarr, container=output_container, mode="a")
                    ds.to_zarr(store, append_dim="ping_time", safe_chunks=False)

                ds.close()
                logger.info("  Appended %s/%s to campaign Zarr", day_key, category)
            except Exception as e:
                logger.warning("Failed to append %s/%s: %s", day_key, category, e)

        if first_zarr is None:
            first_zarr = campaign_zarr
        logger.info("Campaign Zarr complete: %s", campaign_zarr)

    return first_zarr


def generate_campaign_echograms(
    campaign_zarr: str,
    category: str,
    output_container: str,
    cruise_id: str,
    vmin: float = -90,
    vmax: float = -30,
    max_depth: float = 500.0,
) -> list[str]:
    """Generate full-campaign MVBS echogram PNGs from a campaign Zarr.

    Produces one Sv echogram (ping_time x depth) per channel.
    Time gaps are collapsed so data segments are plotted contiguously,
    with red dashed vertical lines marking segment boundaries.
    """
    import gc

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates
    import matplotlib.ticker as mticker
    import numpy as np
    from oceanstream.echodata.storage import open_sv_from_azure

    ds = open_sv_from_azure(zarr_path=campaign_zarr, container=output_container)
    ds = ds.load()

    ping_time = ds.ping_time.values
    depth = ds.depth.values
    depth_mask = depth <= max_depth
    depth_plot = depth[depth_mask]

    # ── Detect time gaps ──────────────────────────────────────────
    diffs = np.diff(ping_time).astype("timedelta64[s]").astype(float)
    median_dt = np.median(diffs)
    gap_threshold = max(5 * median_dt, 120)
    gap_indices = np.where(diffs > gap_threshold)[0]

    # Build contiguous segments
    seg_bounds = [0]
    for gi in gap_indices:
        seg_bounds.append(gi + 1)
    seg_bounds.append(len(ping_time))

    all_files: list[str] = []
    n_channels = ds.sizes.get("channel", 1)

    for ch_idx in range(n_channels):
        ch_name = str(ds.channel.values[ch_idx])
        ch_label = ch_name.split("|")[0].strip() if "|" in ch_name else ch_name
        ch_suffix = f"_ch{ch_idx}" if n_channels > 1 else ""

        sv = ds["Sv"].isel(channel=ch_idx).values[:, depth_mask]

        # Use sequential ping index as x-axis so gaps collapse
        x = np.arange(len(ping_time))

        # ── Date span for title ───────────────────────────────────
        t0_str = np.datetime_as_string(ping_time[0], unit="D")
        t1_str = np.datetime_as_string(ping_time[-1], unit="D")
        date_span = t0_str if t0_str == t1_str else f"{t0_str} — {t1_str}"

        fig, ax = plt.subplots(figsize=(18, 5))
        im = ax.pcolormesh(
            x, depth_plot, sv.T,
            shading="auto", cmap="viridis", vmin=vmin, vmax=vmax,
        )
        ax.invert_yaxis()
        ax.set_ylabel("Depth (m)")
        ax.set_title(
            f"Campaign MVBS — {category} | {ch_label}\n"
            f"{date_span}  (0–{max_depth:.0f} m, {len(ping_time)} pings)"
        )

        # Segment boundary lines
        for gi in gap_indices:
            mid = gi + 0.5
            ax.axvline(mid, color="red", lw=1.2, ls="--", alpha=0.8)

        # Custom x-tick labels: show real timestamps
        # Place ~12 ticks evenly, plus segment start labels
        n_ticks = 12
        tick_positions = np.linspace(0, len(ping_time) - 1, n_ticks, dtype=int)
        # Add segment starts (skip the very first)
        for sb in seg_bounds[1:-1]:
            tick_positions = np.append(tick_positions, sb)
        tick_positions = np.unique(np.sort(tick_positions))

        tick_labels = []
        for tp in tick_positions:
            ts = ping_time[tp]
            dt = ts.astype("datetime64[s]").astype("int64")
            from datetime import datetime, timezone
            t = datetime.fromtimestamp(dt, tz=timezone.utc)
            tick_labels.append(t.strftime("%b %d\n%H:%M"))

        ax.set_xticks(tick_positions)
        ax.set_xticklabels(tick_labels, fontsize=8)
        ax.set_xlim(0, len(ping_time) - 1)

        cb = fig.colorbar(im, ax=ax, pad=0.01, aspect=30)
        cb.set_label("Sv (dB re 1 m⁻¹)")
        plt.tight_layout()

        fname = f"campaign_mvbs_{category}{ch_suffix}.png"
        out = _save_echogram_png(
            fig, fname, "campaign", output_container, cruise_id,
        )
        all_files.append(out)
        plt.close(fig)
        logger.info("  Campaign echogram: %s (%d pings)", fname, len(ping_time))

    ds.close()
    del ds
    gc.collect()
    return all_files


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
    campaign_mvbs_zarrs: dict[str, str] = {}
    if mvbs_zarrs:
        build_campaign_zarr(
            day_zarrs=mvbs_zarrs,
            product_suffix="_mvbs",
            output_container=output_container,
            cruise_id=cfg.cruise_id,
            chunks=chunks,
            output_name="campaign_mvbs.zarr",
        )
        # Collect per-category campaign zarr paths for echograms
        categories = set()
        for day_cats in mvbs_zarrs.values():
            categories.update(day_cats.keys())
        for cat in sorted(categories):
            campaign_mvbs_zarrs[cat] = f"{cfg.cruise_id}/campaign_mvbs_{cat}.zarr"

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

    # Campaign MVBS echograms
    if campaign_mvbs_zarrs:
        logger.info("Generating campaign MVBS echograms (%d categories)", len(campaign_mvbs_zarrs))
        for category, zarr_path in sorted(campaign_mvbs_zarrs.items()):
            try:
                generate_campaign_echograms(
                    campaign_zarr=zarr_path,
                    category=category,
                    output_container=output_container,
                    cruise_id=cfg.cruise_id,
                )
            except Exception as e:
                logger.warning("Failed campaign echogram for %s: %s", category, e)


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
            nasc_zarrs=nasc_zarrs,
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
