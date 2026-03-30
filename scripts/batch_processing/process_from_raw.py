#!/usr/bin/env python3
"""Zarr-v3-native pipeline: raw EK80 → Sv → denoise → MVBS/NASC → echograms.

Downloads raw .raw files from an Azure File Share, converts via echopype
(0.11.x + zarr 3), applies Saildrone calibration, computes Sv, then feeds
into the standard post-processing stages (concat → denoise → MVBS → NASC →
echograms → PMTiles).

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
from datetime import datetime
from pathlib import Path
from typing import Optional

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

from config import PipelineConfig

# Import post-processing stages from the existing pipeline
from process_campaign import (
    _ensure_position_coords,
    _release_memory,
    _save_netcdf_to_blob,
    concatenate_day,
    group_files_by_day,
    run_denoising,
    run_echogram_generation,
    run_mvbs_computation,
    run_nasc_computation,
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
) -> Path:
    """Download a single .raw file from Azure File Share to local disk."""
    from azure.storage.fileshare import ShareServiceClient

    conn_str = os.environ.get(
        "AZURE_STORAGE_CONNECTION_STRING",
        os.environ.get("AZ_SOURCE_CONNECTION_STRING", ""),
    )
    svc = ShareServiceClient.from_connection_string(conn_str)
    share = svc.get_share_client(cfg.raw.file_share_name)
    fc = share.get_directory_client(cfg.raw.file_share_path).get_file_client(filename)

    local_dir = cfg.raw.local_raw_dir
    local_dir.mkdir(parents=True, exist_ok=True)
    local_path = local_dir / filename

    if local_path.exists():
        logger.info("  Raw file already cached: %s", local_path)
        return local_path

    logger.info("  Downloading %s...", filename)
    download = fc.download_file()
    with open(local_path, "wb") as f:
        download.readinto(f)

    size_mb = local_path.stat().st_size / 1024 / 1024
    logger.info("  Downloaded %s (%.1f MB)", filename, size_mb)
    return local_path


def _add_depth_from_echo_range(ds: "xr.Dataset", depth_offset: float = 0.0) -> "xr.Dataset":
    """Create a depth dimension from echo_range, replacing range_sample.

    Ported from saildrone-data/saildrone/process/sv_dataset.py:correct_echo_range().
    Selects echo_range for the first channel + first ping to get a 1D depth
    vector, adds the depth_offset, then replaces range_sample with depth.
    """
    import numpy as np

    if "range_sample" not in ds.dims:
        logger.warning("  No range_sample dim — skipping depth addition")
        return ds

    if "echo_range" not in ds:
        logger.warning("  No echo_range variable — skipping depth addition")
        return ds

    # echo_range is 3D (channel × ping_time × range_sample).
    # All slices are nearly identical; pick first channel + first ping.
    er_1d = (
        ds["echo_range"]
        .isel(channel=0, ping_time=0)
        .values
    )
    corrected_depth = er_1d + depth_offset

    # Filter NaN values
    valid_mask = ~np.isnan(corrected_depth)
    if not valid_mask.all():
        ds = ds.isel(range_sample=valid_mask)
        corrected_depth = corrected_depth[valid_mask]

    # Replace range_sample coordinate with depth values, rename dimension
    ds = ds.assign_coords(range_sample=corrected_depth)
    ds = ds.rename({"range_sample": "depth"})

    logger.info("  Added depth dimension: %d bins, %.1f–%.1f m",
                len(corrected_depth), np.nanmin(corrected_depth), np.nanmax(corrected_depth))
    return ds


def convert_and_compute_sv(
    raw_filename: str,
    file_record: dict,
    cfg: PipelineConfig,
    output_container: str,
) -> tuple[str, str, str]:
    """Convert one raw file → EchoData → calibrate → Sv dataset → save to Azure.

    Returns (pulse_category, output_zarr_path, file_name).
    """
    from echopype.convert.api import open_raw
    import echopype as ep

    file_name = file_record["file_name"]

    # Step 1: Download raw file
    local_raw = _download_raw_file(raw_filename, cfg)

    # Step 2: Convert to EchoData
    logger.info("  Converting %s → EchoData", file_name)
    echodata = open_raw(str(local_raw), sonar_model=cfg.raw.sonar_model)

    if echodata.beam is None:
        logger.warning("  No beam data in %s — skipping", file_name)
        local_raw.unlink(missing_ok=True)
        return "unknown", "", file_name

    # Step 3: Apply calibration
    if cfg.raw.calibration_file:
        from oceanstream.echodata.calibrate import apply_calibration

        logger.info("  Applying calibration from %s", cfg.raw.calibration_file)
        echodata = apply_calibration(echodata, Path(cfg.raw.calibration_file))

    # Step 4: Compute Sv
    logger.info("  Computing Sv (waveform=%s, encode=%s)", cfg.raw.waveform_mode, cfg.raw.encode_mode)
    compute_kwargs = {}
    if echodata.sonar_model == "EK80":
        compute_kwargs["waveform_mode"] = cfg.raw.waveform_mode
        compute_kwargs["encode_mode"] = cfg.raw.encode_mode

    ds_Sv = ep.calibrate.compute_Sv(echodata, **compute_kwargs)

    # Note: depth rename happens AFTER denoising (Stage 4), not here.
    # echopype's background noise removal expects range_sample, so we keep
    # the original dimension name through Stage 2-4.

    # Detect pulse category from transmit_duration_nominal
    category = _detect_pulse_category(echodata)

    # Step 5: Clean encoding and save to Azure
    for var in ds_Sv.data_vars:
        ds_Sv[var].encoding.clear()
    for coord in ds_Sv.coords:
        ds_Sv[coord].encoding.clear()

    # Chunk before saving
    chunk_spec = {"ping_time": cfg.chunks.ping_time, "range_sample": -1}
    ds_Sv = ds_Sv.chunk(chunk_spec)

    output_zarr = f"{cfg.cruise_id}/{file_name}/{file_name}.zarr"

    from oceanstream.echodata.storage import save_dataset_to_azure

    save_dataset_to_azure(ds_Sv, zarr_path=output_zarr, container=output_container)
    logger.info("  Saved Sv: %s/%s [%s]", output_container, output_zarr, category)

    # Cleanup
    ds_Sv.close()
    del ds_Sv, echodata
    local_raw.unlink(missing_ok=True)
    _release_memory()

    return category, output_zarr, file_name


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


def process_raw_files_sequential(
    files_list: list[tuple[str, dict]],
    cfg: PipelineConfig,
    output_container: str,
) -> list[tuple[str, str, str]]:
    """Convert raw files sequentially (memory-intensive, not distributed).

    Returns list of (category, zarr_path, file_name).
    """
    results = []
    total = len(files_list)

    for idx, (raw_filename, rec) in enumerate(files_list, 1):
        logger.info(
            "Processing file %d/%d: %s", idx, total, rec["file_name"]
        )
        try:
            result = convert_and_compute_sv(
                raw_filename, rec, cfg, output_container
            )
            results.append(result)
        except Exception as e:
            logger.error("  FAILED: %s — %s", rec["file_name"], e)
            results.append(("unknown", "", rec["file_name"]))

    successful = sum(1 for _, zp, _ in results if zp)
    logger.info("Converted %d/%d files successfully", successful, total)
    return results


# ═══════════════════════════════════════════════════════════════════════════
# STAGE 3: Day-level concatenation (reuse from process_campaign)
# ═══════════════════════════════════════════════════════════════════════════


def run_day_concatenation(
    client,
    file_results: list[tuple[str, str, str]],
    files_list: list[tuple[str, dict]],
    cfg: PipelineConfig,
    output_container: str,
) -> dict[str, dict[str, str]]:
    """Group file results by day+category and concatenate.

    Same logic as process_campaign.run_day_concatenation but adapted for
    the raw pipeline's record format.
    """
    day_groups = group_files_by_day(files_list, cfg.days_to_combine)

    file_info = {}
    for category, zarr_path, file_name in file_results:
        if zarr_path:  # skip failed files
            file_info[file_name] = (category, zarr_path)

    day_zarrs: dict[str, dict[str, str]] = {}
    chunks = cfg.chunks.as_dict()

    for day_key, day_files in day_groups.items():
        by_category: dict[str, list[str]] = defaultdict(list)
        for _, rec in day_files:
            fn = rec["file_name"]
            if fn in file_info:
                cat, zp = file_info[fn]
                by_category[cat].append(zp)

        day_zarrs[day_key] = {}
        for category, zarr_paths in by_category.items():
            if len(zarr_paths) == 1:
                day_zarrs[day_key][category] = zarr_paths[0]
            else:
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
    """Execute the raw-to-products pipeline."""
    from oceanstream.echodata.storage import ensure_container_exists, generate_container_name

    pipeline_start = time.time()

    # Output container
    output_container = cfg.output_container
    if not output_container:
        output_container = generate_container_name(cfg.cruise_id)
    ensure_container_exists(output_container, public_access="container")
    logger.info("Output container: %s", output_container)

    # Stage 0: Dask (for post-processing stages)
    client = setup_dask_client(cfg)

    resume = cfg.resume_stage

    try:
        # ── Stages 1-4: Convert → Concat → Denoise ──────────────
        if resume >= 5:
            # Reconstruct from existing Azure zarrs
            logger.info("RESUMING from stage %d — reconstructing paths from Azure", resume)
            source_day_zarrs = _reconstruct_day_zarrs(output_container, suffix=".zarr")
            denoised_day_zarrs = _reconstruct_day_zarrs(output_container, suffix="--denoised.zarr")
            # Remove denoised entries from source (source matching would include denoised)
            for dk in list(source_day_zarrs.keys()):
                source_day_zarrs[dk] = {
                    cat: zp for cat, zp in source_day_zarrs[dk].items()
                    if "--denoised" not in zp and "--mvbs" not in zp and "--nasc" not in zp
                }
            logger.info("Stages 1-4 skipped (resume=%d)", resume)
        elif resume >= 4:
            # Resume from Stage 4 (denoise): reconstruct source day zarrs
            logger.info("RESUMING from stage %d — reconstructing source zarrs", resume)
            source_day_zarrs = _reconstruct_day_zarrs(output_container, suffix=".zarr")
            for dk in list(source_day_zarrs.keys()):
                source_day_zarrs[dk] = {
                    cat: zp for cat, zp in source_day_zarrs[dk].items()
                    if "--denoised" not in zp and "--mvbs" not in zp and "--nasc" not in zp
                }
            logger.info("Stages 1-3 skipped (resume=%d)", resume)

            # Stage 4: Denoise (re-run)
            t0 = time.time()
            denoised_day_zarrs = run_denoising(
                client, source_day_zarrs, cfg, output_container
            )
            logger.info("STAGE 4 complete: denoising (%.1fs)", time.time() - t0)
        else:
            # Stage 1: Discover raw files
            t0 = time.time()
            files_list = discover_raw_files(cfg)
            if not files_list:
                logger.error("No raw files found — aborting")
                return
            total_size_gb = sum(r.get("file_size", 0) for _, r in files_list) / 1024**3
            logger.info(
                "STAGE 1 complete: %d raw files discovered (%.1f GB total, %.1fs)",
                len(files_list),
                total_size_gb,
                time.time() - t0,
            )

            # Stage 2: Convert raw → Sv (sequential — each file ~300 MB in memory)
            t0 = time.time()
            file_results = process_raw_files_sequential(
                files_list, cfg, output_container
            )
            logger.info(
                "STAGE 2 complete: %d files converted to Sv (%.1fs)",
                len(file_results),
                time.time() - t0,
            )

            # Stage 3: Day-level concatenation
            t0 = time.time()
            source_day_zarrs = run_day_concatenation(
                client, file_results, files_list, cfg, output_container
            )
            logger.info(
                "STAGE 3 complete: %d days concatenated (%.1fs)",
                len(source_day_zarrs),
                time.time() - t0,
            )

            # Stage 4: Denoise
            t0 = time.time()
            denoised_day_zarrs = run_denoising(
                client, source_day_zarrs, cfg, output_container
            )
            logger.info("STAGE 4 complete: denoising (%.1fs)", time.time() - t0)

        # Stage 5: Seabed masking
        t0 = time.time()
        if cfg.apply_seabed_mask:
            masked_day_zarrs = run_seabed_masking(
                client, denoised_day_zarrs, cfg, output_container
            )
            logger.info(
                "STAGE 5 complete: seabed masking (%.1fs)", time.time() - t0
            )
            mvbs_input = masked_day_zarrs
        else:
            logger.info(
                "STAGE 5 skipped: seabed masking disabled (%.1fs)",
                time.time() - t0,
            )
            mvbs_input = denoised_day_zarrs

        # Stage 6+7: MVBS + NASC
        t0 = time.time()
        if resume >= 8:
            mvbs_zarrs = _reconstruct_day_zarrs(output_container, suffix="--mvbs.zarr")
            nasc_zarrs = {}
            logger.info("STAGE 6+7 skipped (resume=%d), reconstructed %d MVBS zarrs",
                        resume, sum(len(v) for v in mvbs_zarrs.values()))
        else:
            mvbs_zarrs = run_mvbs_computation(
                client, mvbs_input, cfg, output_container
            )
            nasc_zarrs = run_nasc_computation(
                client, mvbs_input, cfg, output_container
            )
            logger.info(
                "STAGE 6+7 complete: MVBS + NASC (%.1fs)", time.time() - t0
            )

        # Stage 8: Echograms
        t0 = time.time()
        run_echogram_generation(
            client,
            source_day_zarrs,
            denoised_day_zarrs,
            mvbs_zarrs,
            cfg,
            output_container,
        )
        logger.info("STAGE 8 complete: echograms (%.1fs)", time.time() - t0)

        # Stage 9: PMTiles + COG
        t0 = time.time()
        run_tiles_and_cog(
            client, nasc_zarrs, mvbs_zarrs, cfg, output_container
        )
        logger.info(
            "STAGE 9 complete: PMTiles + COG (%.1fs)", time.time() - t0
        )

        # Stage 10: Campaign aggregation
        t0 = time.time()
        run_campaign_aggregation(
            client, mvbs_input, mvbs_zarrs, cfg, output_container
        )
        logger.info(
            "STAGE 10 complete: campaign Zarr (%.1fs)", time.time() - t0
        )

    finally:
        if client is not None:
            client.close()

    total_time = time.time() - pipeline_start
    logger.info(
        "Pipeline complete. Total time: %.1fs (%.1f min). Output: %s",
        total_time,
        total_time / 60,
        output_container,
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

    # Date range
    parser.add_argument("--start-date", help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end-date", help="End date (YYYY-MM-DD)")

    # Dask
    parser.add_argument("--scheduler", help="Dask scheduler address")
    parser.add_argument("--n-workers", type=int, default=2)
    parser.add_argument("--memory-limit", default="6GB")

    # Processing toggles
    parser.add_argument("--skip-denoising", action="store_true")
    parser.add_argument("--skip-echograms", action="store_true")
    parser.add_argument("--skip-pmtiles", action="store_true")
    parser.add_argument("--skip-nasc", action="store_true")
    parser.add_argument("--skip-mvbs", action="store_true")
    parser.add_argument(
        "--resume-stage", type=int, default=0,
        help="Resume from stage N (5=after denoising, 6=after MVBS). "
             "Reconstructs intermediate data from existing Azure zarrs.",
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

    # Storage mode
    parser.add_argument(
        "--local-save",
        type=str,
        default="",
        metavar="DIR",
        help="Save all outputs to local directory instead of Azure. "
             "Zarrs, echograms, NetCDFs all go under DIR/container/.",
    )

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

    # Local save mode
    cfg.local_save_dir = Path(args.local_save) if args.local_save else None

    return cfg


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
    logger.info(
        "Dask: %d workers, %s each", cfg.dask.n_workers, cfg.dask.memory_limit
    )
    logger.info(
        "Denoise: %s", "enabled" if not cfg.skip_denoising else "disabled"
    )
    if cfg.local_save_dir:
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


if __name__ == "__main__":
    main()
