#!/usr/bin/env python3
"""Full-survey processing pipeline (38 kHz + 200 kHz).

Processes ALL days of the TPOS 2023 survey (141 days, May–Nov 2023) from
raw EK80 files through a complete product pipeline:

  Raw EK80 → EchoData → combine by day → compute Sv (+ depth + GPS) →
  denoising → per-day MVBS (zarr + netcdf) → per-day NASC (zarr + netcdf) →
  per-day echograms (Sv / denoised / MVBS) →
  campaign MVBS concatenation → campaign echograms

Products per day:
  - ``{day}--{category}.zarr``              — Sv dataset (with GPS + depth)
  - ``{day}--{category}--denoised.zarr``    — denoised Sv
  - ``{day}--{category}--mvbs.zarr/.nc``    — MVBS (1m × 10s)
  - ``{day}--{category}--nasc.zarr/.nc``    — NASC (10m × 0.5 nmi)
  - ``{day}--{type}--{freq}--{cmap}.png``   — echograms (combined pulse modes)

Campaign products:
  - ``campaign_mvbs_combined_{freq}.zarr``  — full-survey MVBS
  - ``campaign_mvbs_combined_{freq}_{cmap}.png`` — full-survey echograms
  - ``tiles/{campaign}_echodata.pmtiles``   — acoustic track vector tiles
  - ``nasc_biomass/{campaign}.geojson``     — NASC biomass points
  - ``heatmaps/{campaign}_nasc_*.tif``      — NASC spatial heatmap COGs
  - ``heatmaps/{campaign}_nasc_*.png``      — NASC heatmap PNG overlays

Frequencies:
  - **38 kHz** — from both short_pulse and long_pulse (full coverage)
  - **200 kHz** — from short_pulse only

VM provisioning:
    cd scripts/batch_processing/vm
    cp .env.example .env   # fill in secrets
    bash provision-batch-vm.sh

Usage (on Azure batch VM):
    # Full pipeline (all 141 days, both frequencies)
    python build_full_survey.py

    # Subset (e.g. June–July only)
    python build_full_survey.py --start-date 2023-06-01 --end-date 2023-07-31

    # Resume after interruption (skips already-processed days)
    python build_full_survey.py --resume

    # Echograms only (from existing combined zarrs)
    python build_full_survey.py --echogram-only

    # Skip raw→Sv stages; use existing Sv zarrs from output container
    python build_full_survey.py --skip-raw

    # Re-denoise from existing Sv zarrs (skip raw→Sv, re-run denoise→products)
    python build_full_survey.py --skip-sv

    # 38 kHz only (skip 200 kHz)
    python build_full_survey.py --freq 38

    # Skip GPS merge (use Phase0 lat/lon)
    python build_full_survey.py --skip-gps

    # Process 12 days concurrently (requires ~360 GB RAM, e.g. E48ds_v6)
    python build_full_survey.py --parallel-days 12

    # Skip NASC computation
    python build_full_survey.py --skip-nasc

    # Skip per-day echograms (only generate campaign echograms)
    python build_full_survey.py --skip-perday-echograms
"""

from __future__ import annotations

import argparse
import gc
import logging
import os
import re
import shutil
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

import matplotlib

matplotlib.use("Agg")
import matplotlib.colors as mcolors
import matplotlib.pyplot as plt
import numpy as np
import xarray as xr

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
    force=True,
)
log = logging.getLogger(__name__)

# Force line-buffered stdout for nohup/redirect compatibility
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(line_buffering=True)

for _noisy in (
    "azure.core.pipeline.policies.http_logging_policy",
    "azure.storage", "urllib3", "adlfs", "fsspec", "zarr",
    "distributed", "distributed.worker", "distributed.scheduler",
    "dask", "echopype",
):
    logging.getLogger(_noisy).setLevel(logging.WARNING)

import warnings

warnings.filterwarnings(
    "ignore",
    message="Running on a single-machine scheduler",
    category=UserWarning,
)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

OUTPUT_CONTAINER = "sd-tpos2023-full-v01"
GPS_CONTAINER = "gpsdata"
CRUISE_ID = "SD_TPOS2023_v03"
ACCOUNT = "ne1osvmdevtest"

# Raw EK80 source — Azure File Share
RAW_FILE_SHARE = "saildroneraw"
RAW_FILE_SHARE_PATH = "DATA"
SONAR_MODEL = "EK80"
WAVEFORM_MODE = "CW"
ENCODE_MODE = "complex"

_DATA_DISK = Path("/mnt/data/output")
OUTPUT_DIR = _DATA_DISK if _DATA_DISK.exists() else Path("/tmp/campaign_full_output")
_RAW_DIR = _DATA_DISK / "raw_downloads" if _DATA_DISK.exists() else Path("/tmp/oceanstream/raw_downloads")
_ECHODATA_DIR = _DATA_DISK / "echodata" if _DATA_DISK.exists() else Path("/tmp/oceanstream/echodata")

ECHOGRAM_DIR = OUTPUT_DIR / "campaign_echograms"

TRANSDUCER_DEPTH: float = 1.9
FREQ_38KHZ: float = 38000.0
FREQ_200KHZ: float = 200000.0
MAX_PLOT_DEPTH: float = 1200.0

# Frequency configs: (freq_hz, label, zarr_stem, categories_to_include)
# 200 kHz is only present in short_pulse mode.
FREQUENCY_CONFIGS: list[tuple[float, str, str, list[str] | None]] = [
    (FREQ_38KHZ, "38 kHz", "38kHz", None),           # all categories
    (FREQ_200KHZ, "200 kHz", "200kHz", ["short_pulse"]),  # short_pulse only
]

MVBS_RANGE_BIN = "1m"
MVBS_PING_TIME_BIN = "10s"

NASC_RANGE_BIN = "10m"
NASC_DIST_BIN = "0.5nmi"

SV_VMIN = -80.0
SV_VMAX = -50.0
GAP_THRESHOLD_S = 1800

CHUNKS = {"ping_time": 1000, "range_sample": -1}

# Denoise: 4-stage pipeline matching process_campaign.py
DENOISE_METHODS = ["background", "impulse", "transient", "attenuation"]

# Survey segments for campaign echograms (each is an independent plot).
# (label, start_date_inclusive, end_date_exclusive)
SURVEY_SEGMENTS: list[tuple[str, str, str]] = [
    ("segment_1", "2023-05-01", "2023-07-18"),
    ("segment_2", "2023-07-18", "2023-08-09"),
    ("segment_3", "2023-08-09", "2023-09-08"),
    ("segment_4", "2023-09-08", "2023-10-08"),
]


# ---------------------------------------------------------------------------
# EK500 colormap
# ---------------------------------------------------------------------------

_EK500_COLORS = [
    (1.000, 1.000, 1.000), (0.624, 0.624, 0.624),
    (0.373, 0.373, 0.686), (0.000, 0.000, 0.498),
    (0.000, 0.000, 0.749), (0.000, 0.498, 0.000),
    (0.000, 0.749, 0.000), (0.498, 0.749, 0.000),
    (0.749, 0.749, 0.000), (0.749, 0.498, 0.000),
    (0.749, 0.000, 0.000), (0.498, 0.000, 0.000),
]
EK500_CMAP = mcolors.LinearSegmentedColormap.from_list("EK500", _EK500_COLORS, N=256)
COLORMAPS: list[tuple[str, str | mcolors.Colormap]] = [
    ("ocean_r", "ocean_r"),
    ("jet", "jet"),
    ("EK500", EK500_CMAP),
]


# ---------------------------------------------------------------------------
# Memory management
# ---------------------------------------------------------------------------

def _release_memory() -> None:
    gc.collect()
    try:
        import ctypes
        libc = ctypes.CDLL("libc.so.6")
        libc.malloc_trim(0)
    except (OSError, AttributeError):
        pass


# ---------------------------------------------------------------------------
# Azure helpers
# ---------------------------------------------------------------------------

def _connection_string() -> str:
    cs = os.environ.get("AZURE_STORAGE_CONNECTION_STRING", "")
    if not cs:
        log.error("AZURE_STORAGE_CONNECTION_STRING not set")
        sys.exit(1)
    return cs


def _open_azure_zarr(zarr_path: str, container: str) -> xr.Dataset:
    """Open a zarr store — local disk if patched, otherwise Azure Blob.

    Routes through ``oceanstream.echodata.storage.open_sv_from_azure``
    so that ``local_storage.patch_storage()`` is respected.
    """
    from oceanstream.echodata.storage import open_sv_from_azure
    return open_sv_from_azure(zarr_path, container=container, chunks=CHUNKS)


def _save_to_azure(ds: xr.Dataset, zarr_path: str, container: str) -> None:
    """Save dataset to Azure as zarr."""
    from oceanstream.echodata.storage import save_dataset_to_azure
    save_dataset_to_azure(ds, zarr_path=zarr_path, container=container)


def _ensure_container(container: str) -> None:
    """Create the Azure container if it doesn't exist."""
    from oceanstream.echodata.storage import ensure_container_exists
    ensure_container_exists(container, public_access="container")


def _blob_exists(blob_path: str, container: str) -> bool:
    """Check if a blob (or zarr marker) exists in the container."""
    from azure.storage.blob import ContainerClient
    conn_str = _connection_string()
    client = ContainerClient.from_connection_string(conn_str, container)
    # Check for zarr.json (zarr v3) or .zmetadata (zarr v2) as marker
    for marker in ["zarr.json", ".zmetadata", ".zattrs"]:
        blob_name = f"{blob_path}/{marker}"
        blob_client = client.get_blob_client(blob_name)
        try:
            blob_client.get_blob_properties()
            return True
        except Exception:
            continue
    return False


# ---------------------------------------------------------------------------
# Raw filename datetime parsing
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Stage 1: Discover raw EK80 files from Azure File Share
# ---------------------------------------------------------------------------

def discover_raw_files(
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    file_share_name: str = RAW_FILE_SHARE,
    file_share_path: str = RAW_FILE_SHARE_PATH,
) -> list[tuple[str, dict]]:
    """List raw EK80 files from Azure File Share, filtered by date range.

    Returns a list of (filename, record_dict) sorted chronologically.
    """
    from azure.storage.fileshare import ShareServiceClient

    conn_str = _connection_string()
    svc = ShareServiceClient.from_connection_string(conn_str)
    share = svc.get_share_client(file_share_name)
    dc = share.get_directory_client(file_share_path)

    log.info("Listing raw files from file share %s/%s", file_share_name, file_share_path)
    all_items = list(dc.list_directories_and_files())

    raw_files = [
        item for item in all_items
        if item["name"].endswith(".raw") and not item.get("is_directory", False)
    ]
    log.info("Found %d .raw files in file share", len(raw_files))

    files_list: list[tuple[str, dict]] = []
    for item in raw_files:
        fname = item["name"]
        dt = _parse_raw_datetime(fname)

        if dt:
            if start_date and dt < start_date:
                continue
            if end_date:
                end = end_date
                if end.hour == 0 and end.minute == 0 and end.second == 0:
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
    log.info("After date filter: %d raw files", len(files_list))
    return files_list


def group_raw_by_day(
    files_list: list[tuple[str, dict]],
) -> dict[str, list[tuple[str, dict]]]:
    """Group raw files by day key (YYYY-MM-DD)."""
    by_day: dict[str, list[tuple[str, dict]]] = defaultdict(list)
    for fname, rec in files_list:
        dt = _parse_raw_datetime(fname)
        if dt:
            day_key = dt.strftime("%Y-%m-%d")
            by_day[day_key].append((fname, rec))
    return dict(sorted(by_day.items()))


# ---------------------------------------------------------------------------
# Stage 2: Download + convert raw → EchoData zarr
# ---------------------------------------------------------------------------

def _download_raw_file(
    filename: str,
    raw_dir: Path,
    file_share_name: str = RAW_FILE_SHARE,
    file_share_path: str = RAW_FILE_SHARE_PATH,
    directory_client=None,
) -> Path:
    """Download a single .raw file from Azure File Share to local disk."""
    raw_dir.mkdir(parents=True, exist_ok=True)
    local_path = raw_dir / filename

    if local_path.exists():
        log.info("  Raw file already cached: %s", local_path)
        return local_path

    if directory_client is None:
        from azure.storage.fileshare import ShareServiceClient
        conn_str = _connection_string()
        svc = ShareServiceClient.from_connection_string(conn_str)
        share = svc.get_share_client(file_share_name)
        directory_client = share.get_directory_client(file_share_path)

    fc = directory_client.get_file_client(filename)
    log.info("  Downloading %s...", filename)
    download = fc.download_file()
    with open(local_path, "wb") as f:
        download.readinto(f)

    size_mb = local_path.stat().st_size / 1024 / 1024
    log.info("  Downloaded %s (%.1f MB)", filename, size_mb)
    return local_path


def _clean_echodata_encoding(ed) -> None:
    """Clear variable encoding on all groups in an EchoData's DataTree.

    Prevents zarr v3 'Cannot specify both compressor and compressors' error.
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


def _detect_pulse_category_echodata(echodata) -> str:
    """Detect short_pulse vs long_pulse from EchoData transmit_duration_nominal."""
    try:
        freqs = echodata["Sonar/Beam_group1"].frequency_nominal.values
        has_200k = any(np.isclose(f, 200_000.0) for f in freqs)
        if has_200k:
            return "short_pulse"
        td = echodata["Sonar/Beam_group1"].transmit_duration_nominal
        first_ping = td.isel(ping_time=0).values.astype(float)
        if any(d < 1.5e-3 for d in first_ping):
            return "short_pulse"
        return "long_pulse"
    except Exception:
        return "unknown"


def convert_raw_file(
    local_raw_path: Path,
    file_record: dict,
    echodata_dir: Path,
    calibration_file: str = "",
) -> tuple[str, str, str]:
    """Convert one raw file → EchoData → optionally calibrate → save zarr.

    Returns (pulse_category, echodata_zarr_path, file_name).
    """
    from echopype.convert.api import open_raw

    file_name = file_record["file_name"]
    log.info("  Converting %s → EchoData", file_name)

    echodata = open_raw(str(local_raw_path), sonar_model=SONAR_MODEL)

    if echodata.beam is None:
        log.warning("  No beam data in %s — skipping", file_name)
        return "unknown", "", file_name

    if calibration_file:
        from oceanstream.echodata.calibrate import apply_calibration
        log.info("  Applying calibration from %s", calibration_file)
        echodata = apply_calibration(echodata, Path(calibration_file))

    category = _detect_pulse_category_echodata(echodata)

    ed_zarr_path = echodata_dir / f"{file_name}.zarr"
    log.info("  Saving EchoData: %s [%s]", ed_zarr_path, category)
    _clean_echodata_encoding(echodata)
    echodata.to_zarr(str(ed_zarr_path), overwrite=True)

    del echodata
    _release_memory()

    return category, str(ed_zarr_path), file_name


def process_day_raw_files(
    day_key: str,
    day_files: list[tuple[str, dict]],
    raw_dir: Path,
    echodata_dir: Path,
    calibration_file: str = "",
    file_share_name: str = RAW_FILE_SHARE,
    file_share_path: str = RAW_FILE_SHARE_PATH,
) -> list[tuple[str, str, str]]:
    """Download and convert all raw files for one day.

    Returns list of (category, echodata_zarr_path, file_name).
    """
    from azure.storage.fileshare import ShareServiceClient

    conn_str = _connection_string()
    svc = ShareServiceClient.from_connection_string(conn_str)
    share = svc.get_share_client(file_share_name)
    dir_client = share.get_directory_client(file_share_path)

    day_echodata_dir = echodata_dir / day_key
    day_echodata_dir.mkdir(parents=True, exist_ok=True)
    day_raw_dir = raw_dir / day_key
    day_raw_dir.mkdir(parents=True, exist_ok=True)

    results: list[tuple[str, str, str]] = []

    for raw_filename, rec in day_files:
        try:
            local_path = _download_raw_file(
                raw_filename, day_raw_dir,
                file_share_name=file_share_name,
                file_share_path=file_share_path,
                directory_client=dir_client,
            )
            result = convert_raw_file(
                local_path, rec, day_echodata_dir,
                calibration_file=calibration_file,
            )
            results.append(result)

            # Delete raw file after conversion to free disk space
            local_path.unlink(missing_ok=True)
        except Exception as e:
            log.error("  FAILED: %s — %s", rec["file_name"], e)
            results.append(("unknown", "", rec["file_name"]))

    return results


# ---------------------------------------------------------------------------
# Stage 3: Combine EchoData per day/category
# ---------------------------------------------------------------------------

def combine_echodata_day(
    ed_zarr_paths: list[str],
    day_key: str,
    category: str,
    echodata_dir: Path,
) -> str:
    """Combine per-file EchoData Zarrs into one per-day EchoData Zarr.

    Returns the combined EchoData zarr path.
    """
    import echopype as ep

    log.info("  Combining %d EchoData files for %s/%s", len(ed_zarr_paths), day_key, category)

    ed_list = []
    for zarr_path in sorted(ed_zarr_paths):
        ed = ep.open_converted(zarr_path, chunks={})
        ed_list.append(ed)

    if len(ed_list) == 1:
        log.info("  Single file for %s/%s — no combine needed", day_key, category)
        return ed_zarr_paths[0]

    ed_combined = ep.combine_echodata(ed_list)

    combined_zarr = str(echodata_dir / day_key / f"{day_key}--{category}--combined.zarr")
    log.info("  Saving combined EchoData: %s", combined_zarr)
    _clean_echodata_encoding(ed_combined)
    ed_combined.to_zarr(combined_zarr, overwrite=True)

    del ed_combined, ed_list
    _release_memory()

    # Delete per-file EchoData Zarrs to free disk space
    for zarr_path in ed_zarr_paths:
        p = Path(zarr_path)
        if p.exists() and p.is_dir():
            shutil.rmtree(p, ignore_errors=True)
    log.info("  Cleaned up %d per-file EchoData Zarrs", len(ed_zarr_paths))

    return combined_zarr


# ---------------------------------------------------------------------------
# Stage 4: Compute Sv from combined EchoData (+ add_depth + GPS)
# ---------------------------------------------------------------------------

def compute_sv_day(
    ed_zarr_path: str,
    day_key: str,
    category: str,
    output_container: str,
    gps_df: "pd.DataFrame | None" = None,
) -> str:
    """Compute Sv from combined EchoData, add depth + GPS, save to Azure.

    Returns the Sv zarr path (in the output container).
    """
    import echopype as ep

    log.info("  Computing Sv for %s/%s", day_key, category)

    ed = ep.open_converted(ed_zarr_path, chunks={})
    ed = ed.chunk({"ping_time": CHUNKS["ping_time"], "range_sample": -1})

    compute_kwargs = {}
    if SONAR_MODEL == "EK80":
        compute_kwargs["waveform_mode"] = WAVEFORM_MODE
        compute_kwargs["encode_mode"] = ENCODE_MODE

    log.info("  compute_Sv (waveform=%s, encode=%s)", WAVEFORM_MODE, ENCODE_MODE)
    ds_Sv = ep.calibrate.compute_Sv(ed, **compute_kwargs)

    # Add depth variable from echo_range + transducer depth offset
    log.info("  add_depth (offset=%.1fm)", TRANSDUCER_DEPTH)
    ds_Sv = ep.consolidate.add_depth(ds_Sv, depth_offset=TRANSDUCER_DEPTH)

    # Merge GPS location data if available
    if gps_df is not None and not gps_df.empty:
        ds_Sv = interpolate_gps(ds_Sv, gps_df)
        log.info("  GPS interpolated onto %d pings", ds_Sv.sizes["ping_time"])

    # Clean encoding and chunk before saving
    for var in list(ds_Sv.data_vars) + list(ds_Sv.coords):
        if var in ds_Sv:
            ds_Sv[var].encoding.clear()

    chunk_spec = {"ping_time": CHUNKS["ping_time"], "range_sample": -1}
    ds_Sv = ds_Sv.chunk(chunk_spec)

    output_zarr = f"{day_key}/{day_key}--{category}.zarr"
    _save_to_azure(ds_Sv, output_zarr, output_container)
    log.info("  Saved Sv: %s/%s [%s]", output_container, output_zarr, category)

    ds_Sv.close()
    del ds_Sv, ed
    _release_memory()

    # Delete the combined EchoData Zarr to free disk space
    ed_path = Path(ed_zarr_path)
    if ed_path.exists() and ed_path.is_dir():
        shutil.rmtree(ed_path, ignore_errors=True)
        log.info("  Cleaned up combined EchoData: %s", ed_zarr_path)

    return output_zarr


# ---------------------------------------------------------------------------
# Stage 5: Denoise (reuses process_campaign.py pattern)
# ---------------------------------------------------------------------------

def denoise_day_zarr(
    zarr_path: str,
    output_container: str,
    day_key: str,
    category: str,
    dask_scheduler: str = "threads",
) -> str:
    """Apply 4-stage denoising to a day zarr.

    Pattern from process_campaign.py:denoise_day().

    The transient noise mask uses ``dask.array.map_overlap`` which
    re-chunks the data even after ``.load()``.  Using the threaded
    scheduler lets these chunks execute in parallel across CPU cores
    (numpy releases the GIL).  Set *dask_scheduler* to ``"synchronous"``
    to disable this if needed (e.g. debugging).
    """
    import dask
    from oceanstream.echodata.denoise import apply_denoising
    from oceanstream.echodata.config import DenoiseConfig

    log.info("Denoising %s/%s", day_key, category)

    ds = _open_azure_zarr(zarr_path, output_container)

    # Load into memory with synchronous scheduler to avoid distributed
    # overhead on the many small chunk fetches from Azure.
    with dask.config.set(scheduler="synchronous"):
        log.info("  Loading into memory ...")
        ds = ds.load()

    # Run denoising with the threaded scheduler so that
    # dask.array.map_overlap (used by transient_noise_mask) can
    # parallelise the kernel across CPU cores.
    with dask.config.set(scheduler=dask_scheduler):
        denoise_config = DenoiseConfig(methods=DENOISE_METHODS)

        # Step 1: Mask-based denoising (impulse, transient, attenuation)
        mask_methods = [m for m in DENOISE_METHODS if m != "background"]
        if mask_methods:
            ds_denoised = apply_denoising(
                ds, methods=mask_methods, config=denoise_config,
            )
        else:
            ds_denoised = ds

        # Step 2: echopype background noise removal per channel
        if "background" in DENOISE_METHODS:
            from echopype.clean import remove_background_noise as ep_remove_bgn

            parent_attrs = dict(ds_denoised.attrs)
            parent_attrs.setdefault("processing_level", "Level 2A")
            parent_attrs["input_processing_level"] = parent_attrs["processing_level"]

            def _remove_bgn_one_channel(ch_ds):
                ch_ds.attrs.update(parent_attrs)
                result = ep_remove_bgn(
                    ch_ds,
                    ping_num=50,
                    range_sample_num=20,
                    SNR_threshold="3.0dB",
                    background_noise_max="-125.0dB",
                )
                return result["Sv_corrected"] if "Sv_corrected" in result else result["Sv"]

            sv_clean = ds_denoised.groupby("channel").map(_remove_bgn_one_channel)
            sv_clean.name = "Sv"
            ds_denoised["Sv"] = sv_clean
            log.info("  Background noise removal applied")

        # Rechunk and save
        output_zarr = f"{day_key}/{day_key}--{category}--denoised.zarr"
        rechunk_spec = {"ping_time": CHUNKS.get("ping_time", 1000)}
        if "range_sample" in ds_denoised.dims:
            rechunk_spec["range_sample"] = -1
        ds_denoised = ds_denoised.chunk(rechunk_spec)
        for var in list(ds_denoised.data_vars) + list(ds_denoised.coords):
            if var in ds_denoised:
                ds_denoised[var].encoding.clear()
        _save_to_azure(ds_denoised, output_zarr, output_container)

    log.info("  Saved denoised: %s", output_zarr)

    ds.close()
    if hasattr(ds_denoised, "close"):
        ds_denoised.close()
    del ds, ds_denoised
    _release_memory()

    return output_zarr


# ---------------------------------------------------------------------------
# GPS download + merge
# ---------------------------------------------------------------------------

def download_gps_geoparquet(
    gps_container: str = GPS_CONTAINER,
    cruise_id: str = CRUISE_ID,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
) -> "pd.DataFrame | None":
    """Download GPS GeoParquet files from Azure and return a normalised DataFrame.

    Reads all .parquet files under ``{gps_container}/{cruise_id}/`` and
    normalises to columns: ``lat``, ``lon``, ``dt``.
    """
    import tempfile

    import pandas as pd
    from azure.storage.blob import BlobServiceClient

    conn_str = _connection_string()
    svc = BlobServiceClient.from_connection_string(conn_str)
    cc = svc.get_container_client(gps_container)

    blob_prefix = f"{cruise_id}/"
    log.info("Downloading GPS GeoParquet from %s/%s ...", gps_container, blob_prefix)

    blobs = [
        b for b in cc.list_blobs(name_starts_with=blob_prefix)
        if b.name.endswith(".parquet") or b.name.endswith(".geoparquet")
    ]
    if not blobs:
        log.warning("No parquet files in %s/%s", gps_container, blob_prefix)
        return None

    log.info("Found %d GPS parquet file(s)", len(blobs))

    frames: list[pd.DataFrame] = []
    for blob in blobs:
        log.info("  Downloading %s (%.1f MB)", blob.name, blob.size / 1024 / 1024)
        tmp = tempfile.NamedTemporaryFile(suffix=".parquet", delete=False)
        try:
            data = cc.download_blob(blob.name).readall()
            tmp.write(data)
            tmp.close()
            df = _read_gps_parquet(tmp.name)
            if df is not None and not df.empty:
                frames.append(df)
        except Exception as e:
            log.error("  Failed to read %s: %s", blob.name, e)
        finally:
            Path(tmp.name).unlink(missing_ok=True)

    if not frames:
        log.warning("No valid GPS data extracted")
        return None

    gps_df = pd.concat(frames, ignore_index=True).sort_values("dt").reset_index(drop=True)
    gps_df = gps_df.drop_duplicates(subset=["dt"])

    from datetime import timedelta

    if start_date:
        gps_df = gps_df[gps_df["dt"] >= start_date - timedelta(hours=2)]
    if end_date:
        end = end_date + timedelta(days=1, hours=2)
        gps_df = gps_df[gps_df["dt"] <= end]

    log.info(
        "GPS loaded: %d points, time: %s → %s, lat: [%.2f, %.2f], lon: [%.2f, %.2f]",
        len(gps_df),
        gps_df["dt"].min(), gps_df["dt"].max(),
        gps_df["lat"].min(), gps_df["lat"].max(),
        gps_df["lon"].min(), gps_df["lon"].max(),
    )
    return gps_df


def _read_gps_parquet(path: str) -> "pd.DataFrame | None":
    """Read a single GPS parquet file and normalise to lat/lon/dt columns."""
    import pandas as pd

    try:
        import geopandas as gpd
        gdf = gpd.read_parquet(path)
        df = pd.DataFrame(gdf)
    except Exception:
        import pyarrow.parquet as pq
        table = pq.read_table(path)
        df = table.to_pandas()

    col_map: dict[str, str] = {}
    columns_lower = {c.lower(): c for c in df.columns}

    for candidate in ("lat", "latitude", "lat_deg"):
        if candidate in columns_lower:
            col_map["lat"] = columns_lower[candidate]
            break
    for candidate in ("lon", "longitude", "lng", "lon_deg"):
        if candidate in columns_lower:
            col_map["lon"] = columns_lower[candidate]
            break
    for candidate in ("time", "dt", "datetime", "timestamp", "date_time"):
        if candidate in columns_lower:
            col_map["dt"] = columns_lower[candidate]
            break

    if ("lat" not in col_map or "lon" not in col_map) and "geometry" in df.columns:
        try:
            df["lat"] = df["geometry"].apply(lambda g: g.y if g else None)
            df["lon"] = df["geometry"].apply(lambda g: g.x if g else None)
            col_map["lat"] = "lat"
            col_map["lon"] = "lon"
        except Exception:
            pass

    if "lat" not in col_map or "lon" not in col_map or "dt" not in col_map:
        log.warning("Cannot identify lat/lon/time columns from: %s", list(df.columns))
        return None

    result = pd.DataFrame({
        "lat": pd.to_numeric(df[col_map["lat"]], errors="coerce"),
        "lon": pd.to_numeric(df[col_map["lon"]], errors="coerce"),
        "dt": pd.to_datetime(df[col_map["dt"]], utc=True, errors="coerce").dt.tz_localize(None),
    })
    result = result.dropna(subset=["lat", "lon", "dt"])
    return result


def interpolate_gps(ds: xr.Dataset, gps_df: "pd.DataFrame") -> xr.Dataset:
    """Linearly interpolate GPS lat/lon onto ds's ping_time."""
    import pandas as pd

    gps_time = pd.DatetimeIndex(gps_df["dt"].values)

    lat_da = xr.DataArray(
        data=gps_df["lat"].values, dims=["gps_time"],
        coords={"gps_time": gps_time},
    )
    lon_da = xr.DataArray(
        data=gps_df["lon"].values, dims=["gps_time"],
        coords={"gps_time": gps_time},
    )

    ping_time = ds["ping_time"]
    lat_interp = lat_da.interp(
        gps_time=ping_time, method="linear",
        kwargs={"fill_value": "extrapolate"},
    ).drop_vars("gps_time")
    lon_interp = lon_da.interp(
        gps_time=ping_time, method="linear",
        kwargs={"fill_value": "extrapolate"},
    ).drop_vars("gps_time")

    for v in ("latitude", "longitude"):
        if v in ds.data_vars:
            ds = ds.drop_vars(v)
        if v in ds.coords:
            ds = ds.reset_coords(v, drop=True)

    ds["latitude"] = lat_interp
    ds["longitude"] = lon_interp
    ds["latitude"].attrs = {"long_name": "Latitude", "units": "degrees_north"}
    ds["longitude"].attrs = {"long_name": "Longitude", "units": "degrees_east"}

    return ds


# ---------------------------------------------------------------------------
# NetCDF export helper
# ---------------------------------------------------------------------------

def _save_netcdf_to_blob(ds: xr.Dataset, nc_path: str, container: str) -> None:
    """Save an xarray Dataset as NetCDF and upload to Azure blob storage."""
    import tempfile

    from oceanstream.echodata.storage import upload_file_to_blob

    try:
        ds_computed = ds.compute()
        for var in list(ds_computed.data_vars):
            if ds_computed[var].dtype == bool:
                ds_computed[var] = ds_computed[var].astype(np.int8)

        encoding = {}
        for var in ds_computed.data_vars:
            if ds_computed[var].dtype.kind in {"U", "S", "O"}:
                encoding[var] = {}
            else:
                encoding[var] = {"zlib": True, "complevel": 5}

        with tempfile.NamedTemporaryFile(suffix=".nc", delete=True) as tmp:
            ds_computed.to_netcdf(
                tmp.name, engine="netcdf4", format="NETCDF4", encoding=encoding,
            )
            upload_file_to_blob(tmp.name, nc_path, container)
        log.info("  Saved NetCDF: %s", nc_path)
    except Exception as e:
        log.warning("NetCDF export failed for %s: %s", nc_path, e)


# ---------------------------------------------------------------------------
# Per-day MVBS
# ---------------------------------------------------------------------------

def compute_perday_mvbs(
    day_key: str,
    category: str,
    denoised_zarr: str,
    output_container: str,
) -> str:
    """Compute MVBS for one denoised day zarr and save to Azure.

    Returns the output zarr path.
    """
    import dask
    from oceanstream.echodata.compute import compute_mvbs

    log.info("  Per-day MVBS %s/%s", day_key, category)

    ds = _open_azure_zarr(denoised_zarr, output_container)

    with dask.config.set(scheduler="synchronous"):
        ds_mvbs = compute_mvbs(ds, range_bin=MVBS_RANGE_BIN, ping_time_bin=MVBS_PING_TIME_BIN)

    output_zarr = f"{day_key}/{day_key}--{category}--mvbs.zarr"
    _save_to_azure(ds_mvbs, output_zarr, output_container)
    _save_netcdf_to_blob(ds_mvbs, f"{day_key}/{day_key}--{category}--mvbs.nc", output_container)

    log.info("  Saved per-day MVBS: %s", output_zarr)

    ds.close()
    del ds, ds_mvbs
    _release_memory()
    return output_zarr


# ---------------------------------------------------------------------------
# Per-day NASC
# ---------------------------------------------------------------------------

def compute_perday_nasc(
    day_key: str,
    category: str,
    denoised_zarr: str,
    output_container: str,
) -> str:
    """Compute NASC for one denoised day zarr and save zarr + netcdf.

    Returns the output zarr path, or "" if skipped.
    """
    import dask
    from oceanstream.echodata.compute import compute_nasc

    log.info("  Per-day NASC %s/%s", day_key, category)

    ds = _open_azure_zarr(denoised_zarr, output_container)

    has_depth = "depth" in ds or "depth" in ds.coords
    has_lat = "latitude" in ds or "latitude" in ds.coords
    has_lon = "longitude" in ds or "longitude" in ds.coords

    if not has_depth:
        log.warning("  No depth in %s/%s — skipping NASC", day_key, category)
        ds.close()
        return ""
    if not (has_lat and has_lon):
        log.warning("  No lat/lon in %s/%s — skipping NASC", day_key, category)
        ds.close()
        return ""

    with dask.config.set(scheduler="synchronous"):
        ds_nasc = compute_nasc(ds, range_bin=NASC_RANGE_BIN, dist_bin=NASC_DIST_BIN)

    output_zarr = f"{day_key}/{day_key}--{category}--nasc.zarr"
    _save_to_azure(ds_nasc, output_zarr, output_container)
    _save_netcdf_to_blob(ds_nasc, f"{day_key}/{day_key}--{category}--nasc.nc", output_container)

    log.info("  Saved per-day NASC: %s", output_zarr)

    ds.close()
    del ds, ds_nasc
    _release_memory()
    return output_zarr


# ---------------------------------------------------------------------------
# Per-day echograms (combined: Sv, denoised, mvbs — both pulse modes)
# ---------------------------------------------------------------------------

def _plot_perday_echogram(
    ds: xr.Dataset,
    day_key: str,
    data_type: str,
    freq_label: str,
    freq_stem: str,
    cmap_name: str,
    cmap: str | mcolors.Colormap,
    output_dir: Path,
) -> Path | None:
    """Render a single per-day echogram for one data_type and frequency."""
    da = ds["Sv"]
    if da.sizes.get("channel", 0) == 0:
        return None
    da = da.isel(channel=0)

    ping_time = da.ping_time.values
    sv_raw = da.values

    range_var = "depth" if "depth" in ds.coords or "depth" in ds.dims else "echo_range"
    depth_vals = ds[range_var].values
    if range_var == "echo_range":
        depth_vals = depth_vals + TRANSDUCER_DEPTH

    has_data = (~np.isnan(sv_raw)).any(axis=0)
    last_valid = int(np.where(has_data)[0][-1]) if has_data.any() else 0
    max_depth = min(MAX_PLOT_DEPTH, depth_vals[last_valid] + 10)
    depth_mask = depth_vals <= max_depth
    depth_plot = depth_vals[depth_mask]
    sv_data = sv_raw[:, depth_mask]

    valid_pings = ~np.isnan(sv_data).all(axis=1)
    sv_data = sv_data[valid_pings]
    ping_time = ping_time[valid_pings]

    n_pings = len(ping_time)
    if n_pings == 0:
        return None

    pulse_mode = None
    if "pulse_mode" in ds:
        pulse_mode = ds["pulse_mode"].values[valid_pings]

    has_pulse = pulse_mode is not None
    width = min(60, max(12, n_pings * 0.003))

    if has_pulse:
        from matplotlib.gridspec import GridSpec

        cbar_frac = 0.3 / width
        fig = plt.figure(figsize=(width, 6))
        gs = GridSpec(
            2, 2, figure=fig,
            height_ratios=[40, 1], width_ratios=[1 - cbar_frac, cbar_frac],
            hspace=0.02, wspace=0.005,
        )
        ax = fig.add_subplot(gs[0, 0])
        ax_pulse = fig.add_subplot(gs[1, 0], sharex=ax)
        cax = fig.add_subplot(gs[0, 1])
    else:
        fig, ax = plt.subplots(figsize=(width, 6))

    x = np.arange(n_pings)
    im = ax.pcolormesh(
        x, depth_plot, sv_data.T,
        shading="auto", cmap=cmap, vmin=SV_VMIN, vmax=SV_VMAX, rasterized=True,
    )
    ax.invert_yaxis()
    ax.set_ylabel("Depth (m)", fontsize=11)
    ax.set_title(
        f"{day_key} \u2014 {data_type} {freq_label} (combined)\n"
        f"Colormap: {cmap_name} | {n_pings} pings",
        fontsize=12, fontweight="bold",
    )

    major_ticks, major_labels, minor_ticks, minor_labels = _build_hourly_ticks(
        ping_time, n_pings, hour_interval=1,
    )

    tick_ax = ax_pulse if has_pulse else ax
    tick_ax.set_xticks(major_ticks)
    tick_ax.set_xticklabels(major_labels, rotation=45, ha="right", fontsize=9, fontweight="bold")
    tick_ax.set_xticks(minor_ticks, minor=True)
    tick_ax.set_xticklabels(minor_labels, minor=True, rotation=45, ha="right", fontsize=7)
    tick_ax.set_xlabel("Time", fontsize=11)
    ax.set_xlim(0, n_pings)

    if has_pulse:
        ax.tick_params(axis="x", labelbottom=False, which="both")
        _draw_pulse_axis(ax_pulse, pulse_mode, n_pings)
        cbar = fig.colorbar(im, cax=cax)
    else:
        cbar = fig.colorbar(im, ax=ax, fraction=0.015, pad=0.01)
    cbar.set_label("Sv (dB re 1 m\u207b\u00b9)", fontsize=10)

    safe_cmap = cmap_name.lower().replace(" ", "_")
    fname = f"{day_key}--{data_type}--{freq_stem}--{safe_cmap}.png"
    out_path = output_dir / fname
    fig.savefig(out_path, dpi=120, bbox_inches="tight")
    plt.close(fig)
    return out_path


def generate_perday_echograms(
    day_key: str,
    concat_zarrs: dict[str, str],
    denoised_zarrs: dict[str, str],
    mvbs_zarrs: dict[str, str],
    output_container: str,
    output_dir: Path,
    freq_configs: list[tuple[float, str, str, list[str] | None]],
) -> list[Path]:
    """Generate combined per-day echograms for Sv, denoised, and MVBS.

    For each frequency, loads all pulse-mode categories, adds a pulse_mode
    variable, concatenates, and renders echograms.
    """
    echogram_dir = output_dir / "perday_echograms"
    echogram_dir.mkdir(parents=True, exist_ok=True)

    all_files: list[Path] = []

    # Pairs: (data_type, zarrs_dict)
    data_sources: list[tuple[str, dict[str, str]]] = [
        ("Sv", concat_zarrs),
        ("denoised", denoised_zarrs),
        ("mvbs", mvbs_zarrs),
    ]

    for freq_hz, freq_label, freq_stem, allowed_cats in freq_configs:
        for data_type, zarrs in data_sources:
            if not zarrs:
                continue

            # Collect and concat across pulse-mode categories
            datasets: list[xr.Dataset] = []
            for category, zarr_path in sorted(zarrs.items()):
                if allowed_cats and category not in allowed_cats:
                    continue
                try:
                    ds = _open_azure_zarr(zarr_path, output_container)
                    ds = normalize_string_dtypes(ds)
                    ds_freq = select_frequency(ds, freq_hz)
                    if ds_freq is None:
                        ds.close()
                        continue
                    ds_freq = ds_freq.load()
                    n_pings = ds_freq.sizes.get("ping_time", 0)
                    if n_pings == 0:
                        ds_freq.close()
                        continue
                    mode_code = 0 if category == "long_pulse" else 1
                    ds_freq["pulse_mode"] = xr.DataArray(
                        np.full(n_pings, mode_code, dtype=np.int8),
                        dims=["ping_time"],
                    )
                    for var in list(ds_freq.data_vars) + list(ds_freq.coords):
                        if var in ds_freq:
                            ds_freq[var].encoding.clear()
                    datasets.append(ds_freq)
                except Exception as e:
                    log.warning("  Skip %s/%s/%s @ %s: %s", day_key, category, data_type, freq_label, e)

            if not datasets:
                continue

            combined = xr.concat(datasets, dim="ping_time")
            combined = combined.sortby("ping_time")

            for ds in datasets:
                ds.close()
            del datasets

            for cmap_name, cmap_val in COLORMAPS[:1]:  # Use first colormap for per-day
                p = _plot_perday_echogram(
                    combined, day_key, data_type, freq_label, freq_stem,
                    cmap_name, cmap_val, echogram_dir,
                )
                if p:
                    all_files.append(p)

            combined.close()
            del combined
            _release_memory()

    return all_files


# ---------------------------------------------------------------------------
# Stage 4: 38 kHz extraction + MVBS (from build_combined_38khz.py)
# ---------------------------------------------------------------------------

def normalize_string_dtypes(ds: xr.Dataset) -> xr.Dataset:
    """Convert object / StringDType coords to fixed-length U strings."""
    for name in list(ds.coords) + list(ds.data_vars):
        arr = ds[name]
        dtype_kind = getattr(arr.dtype, "kind", "")
        is_string = (
            arr.dtype == object
            or dtype_kind == "T"
            or "string" in str(arr.dtype).lower()
        )
        if is_string:
            vals = arr.values
            if isinstance(vals, np.ndarray):
                try:
                    str_vals = vals.astype("U")
                except (TypeError, ValueError):
                    str_vals = np.array([str(v) for v in vals.flat], dtype="U").reshape(vals.shape)
                if name in ds.coords:
                    ds = ds.assign_coords({name: (arr.dims, str_vals)})
                else:
                    ds[name] = xr.DataArray(str_vals, dims=arr.dims)
    return ds


def select_frequency(ds: xr.Dataset, freq_hz: float) -> xr.Dataset | None:
    """Select a single frequency channel. Returns None if not present."""
    if "frequency_nominal" in ds.coords or "frequency_nominal" in ds.data_vars:
        freq = ds["frequency_nominal"].values
        mask = np.isclose(freq, freq_hz, atol=100)
        if mask.any():
            return ds.isel(channel=np.where(mask)[0])
    # Fallback: match channel label
    label_key = "ES38" if np.isclose(freq_hz, FREQ_38KHZ) else "ES200"
    channels = ds.channel.values.astype(str)
    mask = np.array([label_key in ch for ch in channels])
    if mask.any():
        return ds.isel(channel=np.where(mask)[0])
    return None


def compute_mvbs_for_denoised(
    day_key: str,
    category: str,
    zarr_path: str,
    container: str,
    freq_hz: float = FREQ_38KHZ,
    freq_label: str = "38 kHz",
) -> xr.Dataset | None:
    """Open a denoised zarr, extract one frequency, compute MVBS."""
    from oceanstream.echodata.compute.mvbs import compute_mvbs

    log.info("  MVBS %s/%s @ %s ...", day_key, category, freq_label)
    try:
        ds = _open_azure_zarr(zarr_path, container)
    except Exception as e:
        log.warning("  Failed to open %s/%s: %s", day_key, category, e)
        return None

    ds = normalize_string_dtypes(ds)
    ds_freq = select_frequency(ds, freq_hz)

    if ds_freq is None:
        log.info("  %s/%s: no %s channel — skipping", day_key, category, freq_label)
        ds.close()
        return None

    n_pings = ds_freq.sizes.get("ping_time", 0)
    if n_pings == 0:
        log.warning("  %s/%s: 0 pings after %s selection", day_key, category, freq_label)
        ds.close()
        return None

    try:
        ds_mvbs = compute_mvbs(
            ds_freq, range_bin=MVBS_RANGE_BIN, ping_time_bin=MVBS_PING_TIME_BIN,
        )
    except Exception as e:
        log.warning("  MVBS failed for %s/%s @ %s: %s", day_key, category, freq_label, e)
        ds.close()
        return None

    n_mvbs = ds_mvbs.sizes["ping_time"]
    mode_code = 0 if category == "long_pulse" else 1
    ds_mvbs["pulse_mode"] = xr.DataArray(
        np.full(n_mvbs, mode_code, dtype=np.int8),
        dims=["ping_time"],
    )
    ds_mvbs["pulse_mode"].attrs["long_name"] = "Pulse mode (0=long, 1=short)"
    ds_mvbs.attrs["source_category"] = category
    ds_mvbs.attrs["source_day"] = day_key

    ds.close()
    log.info("  MVBS %s/%s @ %s: %d → %d pings", day_key, category, freq_label, n_pings, n_mvbs)
    return ds_mvbs


# ---------------------------------------------------------------------------
# Stage 5: Campaign concatenation + zarr save
# ---------------------------------------------------------------------------

def build_combined_zarr(
    mvbs_zarrs: dict[str, dict[str, str]],
    container: str,
    freq_hz: float = FREQ_38KHZ,
    freq_label: str = "38 kHz",
    freq_stem: str = "38kHz",
    allowed_categories: list[str] | None = None,
    local_save_dir: Path | None = None,
) -> xr.Dataset | None:
    """Concatenate existing per-day MVBS zarrs for one frequency.

    Reads the per-day MVBS zarrs produced in stage 7, selects the
    requested frequency channel, and concatenates chronologically.
    Much faster than recomputing MVBS from denoised data.

    If *local_save_dir* is set, reads from local disk; otherwise from Azure.
    If *allowed_categories* is set, only those pulse-mode categories are
    included (e.g. ["short_pulse"] for 200 kHz).
    """
    datasets: list[xr.Dataset] = []

    for day_key in sorted(mvbs_zarrs.keys()):
        for category, zarr_path in mvbs_zarrs[day_key].items():
            if allowed_categories and category not in allowed_categories:
                continue

            try:
                if local_save_dir is not None:
                    local_path = local_save_dir / container / zarr_path
                    if not local_path.exists():
                        log.warning("  Local MVBS not found: %s — skipping", local_path)
                        continue
                    ds = xr.open_zarr(str(local_path), chunks=None)
                else:
                    ds = _open_azure_zarr(zarr_path, container)
            except Exception as e:
                log.warning("  Failed to open MVBS %s/%s: %s", day_key, category, e)
                continue

            ds = normalize_string_dtypes(ds)
            ds_freq = select_frequency(ds, freq_hz)
            if ds_freq is None:
                log.info("  %s/%s: no %s channel — skipping", day_key, category, freq_label)
                ds.close()
                continue

            for var in list(ds_freq.data_vars) + list(ds_freq.coords):
                if var in ds_freq:
                    ds_freq[var].encoding.clear()

            ds_freq = ds_freq.load()
            datasets.append(ds_freq)
            ds.close()

    if not datasets:
        log.warning("No MVBS datasets to concatenate for %s", freq_label)
        return None

    log.info("Concatenating %d daily MVBS datasets for %s ...", len(datasets), freq_label)
    campaign = xr.concat(datasets, dim="ping_time")
    campaign = campaign.sortby("ping_time")

    for ds in datasets:
        ds.close()
    del datasets
    gc.collect()

    return campaign


# ---------------------------------------------------------------------------
# Echogram rendering (copied from build_combined_38khz.py)
# ---------------------------------------------------------------------------

def find_day_boundaries(
    ping_time: np.ndarray, threshold_s: float = GAP_THRESHOLD_S,
) -> tuple[list[int], list[str]]:
    diffs = np.diff(ping_time).astype("timedelta64[s]").astype(float)
    gap_indices = np.where(diffs > threshold_s)[0]
    seg_starts = [0] + [gi + 1 for gi in gap_indices]
    labels = [str(ping_time[si].astype("datetime64[D]")) for si in seg_starts]
    return seg_starts, labels


def _build_hourly_ticks(
    ping_time: np.ndarray,
    n_pings: int,
    hour_interval: int = 6,
) -> tuple[list[int], list[str], list[int], list[str]]:
    major_ticks: list[int] = []
    major_labels: list[str] = []
    minor_ticks: list[int] = []
    minor_labels: list[str] = []

    days = np.unique(ping_time.astype("datetime64[D]"))
    for day in days:
        day_mask = (ping_time >= day) & (
            ping_time < day + np.timedelta64(1, "D")
        )
        day_idxs = np.where(day_mask)[0]
        if len(day_idxs) == 0:
            continue
        major_ticks.append(int(day_idxs[0]))
        major_labels.append(str(day)[5:])

        day_times = ping_time[day_idxs]
        for h in range(0, 24, hour_interval):
            if h == 0:
                continue
            hour_ts = day + np.timedelta64(h, "h")
            after = day_times >= hour_ts
            if after.any():
                local_idx = int(np.argmax(after))
                minor_ticks.append(int(day_idxs[local_idx]))
                minor_labels.append(f"{h:02d}:00")

    return major_ticks, major_labels, minor_ticks, minor_labels


def _prepare_echogram_data(
    ds: xr.Dataset,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, float, np.ndarray | None]:
    da = ds["Sv"].isel(channel=0)
    ping_time = da.ping_time.values
    sv_raw = da.values

    range_var = "depth" if "depth" in ds.coords or "depth" in ds.dims else "echo_range"
    depth_vals = ds[range_var].values
    if range_var == "echo_range":
        depth_vals = depth_vals + TRANSDUCER_DEPTH

    has_data = (~np.isnan(sv_raw)).any(axis=0)
    last_valid = int(np.where(has_data)[0][-1]) if has_data.any() else 0
    max_depth = min(MAX_PLOT_DEPTH, depth_vals[last_valid] + 10)
    depth_mask = depth_vals <= max_depth
    depth_plot = depth_vals[depth_mask]
    sv_data = sv_raw[:, depth_mask]

    valid_pings = ~np.isnan(sv_data).all(axis=1)
    n_dropped = int((~valid_pings).sum())
    sv_data = sv_data[valid_pings]
    ping_time = ping_time[valid_pings]
    pulse_mode = None
    if "pulse_mode" in ds:
        pulse_mode = ds["pulse_mode"].values[valid_pings]
    log.info("  Max valid depth: %.0fm, dropped %d empty pings", max_depth, n_dropped)

    return sv_data, depth_plot, ping_time, max_depth, pulse_mode


def _draw_pulse_axis(
    ax_pulse: plt.Axes,
    pulse_mode: np.ndarray,
    n_pings: int,
) -> None:
    from matplotlib.patches import Rectangle

    colors = {0: "#2196F3", 1: "#FF9800"}
    labels_map = {0: "Long", 1: "Short"}

    changes = np.where(np.diff(pulse_mode))[0] + 1
    starts = np.concatenate([[0], changes])
    ends = np.concatenate([changes, [n_pings]])

    drawn_labels: set[int] = set()
    for s, e in zip(starts, ends):
        mode = int(pulse_mode[s])
        lbl = labels_map[mode] if mode not in drawn_labels else None
        rect = Rectangle(
            (s, 0), e - s, 1,
            facecolor=colors[mode], alpha=0.85, edgecolor="none",
            label=lbl,
        )
        ax_pulse.add_patch(rect)
        seg_width = e - s
        if seg_width > n_pings * 0.008:
            ax_pulse.text(
                s + seg_width / 2, 0.5, labels_map[mode][0],
                ha="center", va="center", fontsize=7,
                fontweight="bold", color="white",
            )
        drawn_labels.add(mode)

    ax_pulse.set_xlim(0, n_pings)
    ax_pulse.set_ylim(0, 1)
    ax_pulse.set_yticks([])
    ax_pulse.set_xticks([])
    ax_pulse.set_ylabel("Pulse", fontsize=9, rotation=0, labelpad=30, va="center")
    ax_pulse.legend(
        loc="center left", bbox_to_anchor=(1.001, 0.5),
        fontsize=8, framealpha=0.9, handlelength=1.2,
    )


def plot_combined_echogram(
    ds: xr.Dataset,
    cmap_name: str,
    cmap: str | mcolors.Colormap,
    freq_label: str = "38 kHz",
    freq_stem: str = "38kHz",
    segment_label: str | None = None,
) -> Path:
    sv_data, depth_plot, ping_time, _max_depth, pulse_mode = _prepare_echogram_data(ds)

    n_pings = len(ping_time)
    log.info("  %d pings", n_pings)

    t0 = str(ping_time[0])[:10]
    t1 = str(ping_time[-1])[:10]
    x = np.arange(n_pings)
    width = min(250, max(60, n_pings * 0.0025))

    has_pulse = pulse_mode is not None
    if has_pulse:
        from matplotlib.gridspec import GridSpec

        cbar_frac = 0.3 / width
        fig = plt.figure(figsize=(width, 8.6))
        gs = GridSpec(
            2, 2, figure=fig,
            height_ratios=[40, 1], width_ratios=[1 - cbar_frac, cbar_frac],
            hspace=0.02, wspace=0.005,
        )
        ax = fig.add_subplot(gs[0, 0])
        ax_pulse = fig.add_subplot(gs[1, 0], sharex=ax)
        cax = fig.add_subplot(gs[0, 1])
    else:
        fig, ax = plt.subplots(figsize=(width, 8))

    # Determine pulse description for title
    pulse_desc = "short + long pulse" if freq_label == "38 kHz" else "short pulse only"
    segment_title = f" — {segment_label}" if segment_label else ""

    im = ax.pcolormesh(
        x, depth_plot, sv_data.T,
        shading="auto", cmap=cmap, vmin=SV_VMIN, vmax=SV_VMAX, rasterized=True,
    )
    ax.invert_yaxis()
    ax.set_ylabel("Depth (m)", fontsize=14)
    ax.set_title(
        f"Campaign MVBS — Combined {freq_label} ({pulse_desc}){segment_title}\n"
        f"Colormap: {cmap_name} | {t0} to {t1} "
        f"({n_pings} pings) | transducer depth: {TRANSDUCER_DEPTH}m",
        fontsize=16, fontweight="bold",
    )

    major_ticks, major_labels, minor_ticks, minor_labels = _build_hourly_ticks(
        ping_time, n_pings, hour_interval=6,
    )

    tick_ax = ax_pulse if has_pulse else ax
    tick_ax.set_xticks(major_ticks)
    tick_ax.set_xticklabels(
        major_labels, rotation=45, ha="right", fontsize=10, fontweight="bold",
    )
    tick_ax.set_xticks(minor_ticks, minor=True)
    tick_ax.set_xticklabels(
        minor_labels, minor=True, rotation=45, ha="right", fontsize=8,
    )
    tick_ax.tick_params(axis="x", which="major", length=8, width=1.2)
    tick_ax.tick_params(axis="x", which="minor", length=4, width=0.8)
    tick_ax.set_xlabel("Date / Time", fontsize=14)
    ax.set_xlim(0, n_pings)

    if has_pulse:
        ax.tick_params(axis="x", labelbottom=False, which="both")
        _draw_pulse_axis(ax_pulse, pulse_mode, n_pings)
        cbar = fig.colorbar(im, cax=cax)
    else:
        cbar = fig.colorbar(im, ax=ax, fraction=0.015, pad=0.01)
    cbar.set_label("Sv (dB re 1 m\u207b\u00b9)", fontsize=12)

    safe_cmap = cmap_name.lower().replace(" ", "_")
    seg_suffix = f"_{segment_label}" if segment_label else ""
    fname = f"campaign_mvbs_combined_{freq_stem}{seg_suffix}_{safe_cmap}.png"
    out_path = ECHOGRAM_DIR / fname
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    log.info("  Saved: %s (%.1f MB)", fname, out_path.stat().st_size / 1e6)
    return out_path


# ---------------------------------------------------------------------------
# List existing Sv / denoised zarrs
# ---------------------------------------------------------------------------

_SV_RE = re.compile(
    r"(\d{4}-\d{2}-\d{2})--(\w+)\.zarr$"
)
_DENOISED_RE = re.compile(
    r"(\d{4}-\d{2}-\d{2})--(\w+)--denoised\.zarr$"
)
_NASC_RE = re.compile(
    r"(\d{4}-\d{2}-\d{2})--(\w+)--nasc\.zarr$"
)


def _list_sv_local(local_root: Path) -> list[tuple[str, str, str]]:
    """Scan local disk for Sv zarr directories (not denoised/mvbs/nasc)."""
    results: list[tuple[str, str, str]] = []
    for day_dir in sorted(local_root.iterdir()):
        if not day_dir.is_dir() or not re.match(r"\d{4}-\d{2}-\d{2}$", day_dir.name):
            continue
        for item in day_dir.iterdir():
            # Match {day}--{category}.zarr but NOT {day}--{category}--denoised.zarr etc.
            if "--denoised" in item.name or "--mvbs" in item.name or "--nasc" in item.name:
                continue
            m = _SV_RE.match(item.name)
            if m and item.is_dir():
                day_key = m.group(1)
                category = m.group(2)
                zarr_path = f"{day_key}/{item.name}"
                results.append((day_key, category, zarr_path))
    results.sort()
    return results


def list_sv_zarrs(container: str) -> list[tuple[str, str, str]]:
    """List Sv zarr paths from local disk or Azure.

    Returns sorted list of (day_key, category, zarr_path).
    Only returns raw Sv zarrs, not denoised/mvbs/nasc variants.
    """
    # Try local disk first
    try:
        from local_storage import _OUTPUT_ROOT
        local_root = _OUTPUT_ROOT / container
        if local_root.exists():
            return _list_sv_local(local_root)
    except ImportError:
        pass

    # Fall back to Azure Blob
    from azure.storage.blob import ContainerClient

    conn_str = _connection_string()
    client = ContainerClient.from_connection_string(conn_str, container)

    # Match {day}/{day}--{category}.zarr/ but exclude denoised/mvbs/nasc
    pattern = re.compile(
        r"(\d{4}-\d{2}-\d{2})/\d{4}-\d{2}-\d{2}--(\w+)\.zarr/(zarr\.json|\.zmetadata|\.zattrs)$"
    )

    results: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    for blob in client.list_blobs(name_starts_with="2023-"):
        # Skip denoised / mvbs / nasc
        if "--denoised" in blob.name or "--mvbs" in blob.name or "--nasc" in blob.name:
            continue
        m = pattern.search(blob.name)
        if m:
            day_key = m.group(1)
            category = m.group(2)
            zarr_path = blob.name.rsplit("/", 1)[0]
            key = f"{day_key}/{category}"
            if key not in seen:
                seen.add(key)
                results.append((day_key, category, zarr_path))

    results.sort()
    return results


def _list_denoised_local(local_root: Path) -> list[tuple[str, str, str]]:
    """Scan local disk for denoised zarr directories."""
    results: list[tuple[str, str, str]] = []
    for day_dir in sorted(local_root.iterdir()):
        if not day_dir.is_dir() or not re.match(r"\d{4}-\d{2}-\d{2}$", day_dir.name):
            continue
        for item in day_dir.iterdir():
            m = _DENOISED_RE.match(item.name)
            if m and item.is_dir():
                day_key = m.group(1)
                category = m.group(2)
                zarr_path = f"{day_key}/{item.name}"
                results.append((day_key, category, zarr_path))
    results.sort()
    return results


def list_denoised_zarrs(container: str) -> list[tuple[str, str, str]]:
    """List denoised zarr paths from local disk or Azure.

    Returns sorted list of (day_key, category, zarr_path).
    Checks local disk first (if local_storage is patched), then falls
    back to Azure Blob.
    """
    # Try local disk first
    try:
        from local_storage import _OUTPUT_ROOT
        local_root = _OUTPUT_ROOT / container
        if local_root.exists():
            return _list_denoised_local(local_root)
    except ImportError:
        pass

    # Fall back to Azure Blob
    from azure.storage.blob import ContainerClient

    conn_str = _connection_string()
    client = ContainerClient.from_connection_string(conn_str, container)

    pattern = re.compile(
        r"(\d{4}-\d{2}-\d{2})/\d{4}-\d{2}-\d{2}--(\w+)--denoised\.zarr/(zarr\.json|\.zmetadata|\.zattrs)$"
    )

    results: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    for blob in client.list_blobs(name_starts_with="2023-"):
        m = pattern.search(blob.name)
        if m:
            day_key = m.group(1)
            category = m.group(2)
            zarr_path = blob.name.rsplit("/", 1)[0]
            key = f"{day_key}/{category}"
            if key not in seen:
                seen.add(key)
                results.append((day_key, category, zarr_path))

    results.sort()
    return results


def list_already_denoised_days(container: str) -> set[str]:
    """Return set of 'day_key/category' that already have denoised zarrs."""
    entries = list_denoised_zarrs(container)
    return {f"{day}/{cat}" for day, cat, _ in entries}


# ---------------------------------------------------------------------------
# Stage 11: Echodata PMTiles (track from Sv zarr lat/lon)
# ---------------------------------------------------------------------------

def _extract_track_from_local_zarr(
    zarr_path: Path,
    day_key: str,
    sample_rate: int = 100,
) -> Optional[dict]:
    """Extract lat/lon track from a local Sv zarr for one day.

    Returns a GeoJSON Feature (LineString) or None.
    """
    try:
        ds = xr.open_zarr(str(zarr_path), chunks={})
        has_lat = "latitude" in ds.coords or "latitude" in ds.data_vars
        has_lon = "longitude" in ds.coords or "longitude" in ds.data_vars
        if not (has_lat and has_lon):
            ds.close()
            return None

        lat = ds["latitude"].values
        lon = ds["longitude"].values
        ping_time = ds["ping_time"].values if "ping_time" in ds else None
        ds.close()

        # Flatten if multi-dimensional (e.g. latitude has (ping_time,) dim)
        lat = lat.ravel()
        lon = lon.ravel()

        # Sort by ping_time
        if ping_time is not None:
            sort_idx = np.argsort(ping_time)
            lat, lon, ping_time = lat[sort_idx], lon[sort_idx], ping_time[sort_idx]

        # Sample
        indices = np.arange(0, len(lat), sample_rate)
        lat_s, lon_s = lat[indices], lon[indices]

        # Filter NaN
        valid = ~(np.isnan(lat_s) | np.isnan(lon_s))
        lat_s, lon_s = lat_s[valid], lon_s[valid]
        if len(lat_s) < 2:
            return None

        # Remove outlier jumps (> 0.5° from median of 4 nearest neighbors)
        keep = np.ones(len(lat_s), dtype=bool)
        for i in range(len(lat_s)):
            lo, hi = max(0, i - 4), min(len(lat_s), i + 5)
            nb_lat = np.concatenate([lat_s[lo:i], lat_s[i + 1 : hi]])
            nb_lon = np.concatenate([lon_s[lo:i], lon_s[i + 1 : hi]])
            if len(nb_lat) == 0:
                continue
            if abs(lat_s[i] - np.median(nb_lat)) > 0.5 or abs(lon_s[i] - np.median(nb_lon)) > 0.5:
                keep[i] = False
        lat_s, lon_s = lat_s[keep], lon_s[keep]
        if len(lat_s) < 2:
            return None

        coords = [[float(lon_s[i]), float(lat_s[i])] for i in range(len(lat_s))]

        time_start = time_end = None
        if ping_time is not None:
            vt = ping_time[indices][valid]
            if len(vt) > 0:
                time_start = str(np.datetime_as_string(vt[0], unit="s"))
                time_end = str(np.datetime_as_string(vt[-1], unit="s"))

        feature = {
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": coords},
            "properties": {
                "date": day_key,
                "point_count": len(coords),
                "feature_type": "echodata_track",
            },
        }
        if time_start:
            feature["properties"]["time_start"] = time_start
        if time_end:
            feature["properties"]["time_end"] = time_end

        return feature
    except Exception as e:
        log.warning("  Track extraction failed for %s: %s", zarr_path.name, e)
        return None


def build_echodata_pmtiles(
    output_dir: Path,
    container_dir: Path,
    campaign_id: str = "saildrone_tpos_2023",
    sample_rate: int = 100,
    layer_name: str = "echodata",
    minzoom: int = 0,
    maxzoom: int = 14,
) -> Optional[Path]:
    """Build PMTiles for echodata acoustic track from local Sv zarrs.

    Reads lat/lon from each day's Sv zarrs, builds a GeoJSON
    FeatureCollection of LineStrings, then runs tippecanoe to produce
    a PMTiles file.

    Args:
        output_dir: Directory for output files.
        container_dir: Local directory mirroring the output container
                       (contains day folders with Sv zarrs).
        campaign_id: Campaign identifier for naming.
        sample_rate: Take every Nth point from the Sv zarr.
        layer_name: Name of the vector tile layer.
        minzoom: Minimum zoom level.
        maxzoom: Maximum zoom level.

    Returns:
        Path to the generated PMTiles file, or None on failure.
    """
    import json as json_mod
    import subprocess

    features = []
    day_dirs = sorted(
        d for d in container_dir.iterdir()
        if d.is_dir() and re.match(r"\d{4}-\d{2}-\d{2}$", d.name)
    )

    for day_dir in day_dirs:
        day_key = day_dir.name
        # Try denoised zarrs first (have GPS merged), then raw Sv
        for suffix in ("--short_pulse--denoised.zarr", "--long_pulse--denoised.zarr",
                       "--short_pulse.zarr", "--long_pulse.zarr"):
            zarr_path = day_dir / f"{day_key}{suffix}"
            if zarr_path.exists():
                feature = _extract_track_from_local_zarr(zarr_path, day_key, sample_rate)
                if feature:
                    feature["properties"]["campaign_id"] = campaign_id
                    features.append(feature)
                    break  # one feature per day

    if not features:
        log.error("No track features extracted from Sv zarrs")
        return None

    log.info("Extracted %d track features from %d day dirs", len(features), len(day_dirs))

    geojson = {"type": "FeatureCollection", "features": features}
    tiles_dir = output_dir / "tiles"
    tiles_dir.mkdir(parents=True, exist_ok=True)

    geojson_path = tiles_dir / f"{campaign_id}_echodata.geojson"
    with open(geojson_path, "w") as f:
        json_mod.dump(geojson, f)
    log.info("Wrote GeoJSON: %s (%.1f KB)", geojson_path, geojson_path.stat().st_size / 1024)

    # Run tippecanoe
    pmtiles_path = tiles_dir / f"{campaign_id}_echodata.pmtiles"
    cmd = [
        "tippecanoe",
        "-o", str(pmtiles_path),
        f"--layer={layer_name}",
        f"--minimum-zoom={minzoom}",
        f"--maximum-zoom={maxzoom}",
        "--force",
        "--no-feature-limit",
        "--no-tile-size-limit",
        "--no-line-simplification",
        "--no-tile-compression",
        str(geojson_path),
    ]

    try:
        subprocess.run(cmd, capture_output=True, text=True, check=True)
        log.info(
            "Generated PMTiles: %s (%.1f MB)",
            pmtiles_path, pmtiles_path.stat().st_size / 1e6,
        )
        return pmtiles_path
    except FileNotFoundError:
        log.error("tippecanoe not found — install with: sudo apt install tippecanoe  "
                  "or  brew install tippecanoe")
        log.info("GeoJSON still available at: %s", geojson_path)
        return None
    except subprocess.CalledProcessError as e:
        log.error("tippecanoe failed: %s", e.stderr)
        return None


# ---------------------------------------------------------------------------
# Stage 12: NASC Biomass GeoJSON
# ---------------------------------------------------------------------------

def _extract_nasc_from_local_zarr(
    zarr_path: Path,
    day_key: str,
) -> list[dict]:
    """Extract depth-integrated NASC from a local day NASC zarr.

    Depth-frequency merge strategy (matching os-webapp):
      - 200 kHz: sum NASC over 10–150m depth (shallow, reliable)
      - 38 kHz:  sum NASC over 150–500m depth (deep, where 200 kHz is noise)
      - combined = shallow (200 kHz) + deep (38 kHz)

    Returns a list of GeoJSON Feature dicts (Points).
    """
    try:
        ds = xr.open_zarr(str(zarr_path), consolidated=False)
    except Exception:
        try:
            ds = xr.open_zarr(str(zarr_path))
        except Exception as e:
            log.warning("  Failed to open NASC zarr %s: %s", zarr_path.name, e)
            return []

    try:
        if "NASC" not in ds:
            ds.close()
            return []

        nasc = ds["NASC"]
        depths = ds["depth"].values if "depth" in ds else None
        if depths is None:
            ds.close()
            return []

        frequencies = (
            ds["frequency"].values if "frequency" in ds
            else ds["channel"].values if "channel" in ds
            else None
        )

        lats = ds["latitude"].values if "latitude" in ds else None
        lons = ds["longitude"].values if "longitude" in ds else None
        ping_times = ds["ping_time"].values if "ping_time" in ds else None

        if lats is None or lons is None:
            ds.close()
            return []

        # Identify channel indices
        ch_38_idx = ch_200_idx = None
        if frequencies is not None:
            for i, freq in enumerate(frequencies):
                freq_str = str(freq)
                try:
                    f = float(freq)
                except (ValueError, TypeError):
                    # Channel name like "EKA 266972-07 ES38-18|200-18C"
                    f = None
                    if "ES38" in freq_str or "38" in freq_str.split("|")[0].split("-")[-1]:
                        ch_38_idx = i
                    if "ES200" in freq_str or "200" in freq_str:
                        ch_200_idx = i
                    continue
                if abs(f - 38000) < 1000:
                    ch_38_idx = i
                elif abs(f - 200000) < 1000:
                    ch_200_idx = i

        if ch_38_idx is None and ch_200_idx is None:
            ds.close()
            return []

        # Depth masks
        shallow_mask = (depths >= 10) & (depths <= 150)
        deep_mask = (depths > 150) & (depths <= 500)
        full_38_mask = (depths >= 10) & (depths <= 500)
        full_200_mask = (depths >= 10) & (depths <= 150)

        nasc_values = nasc.values  # (channel, distance, depth)
        n_dist = nasc_values.shape[1]

        features = []
        for i in range(n_dist):
            lat, lon = float(lats[i]), float(lons[i])
            if np.isnan(lat) or np.isnan(lon):
                continue

            nasc_shallow = 0.0
            if ch_200_idx is not None:
                vals = nasc_values[ch_200_idx, i, shallow_mask]
                vals = vals[~np.isnan(vals)]
                nasc_shallow = float(np.sum(vals))

            nasc_deep = 0.0
            if ch_38_idx is not None:
                vals = nasc_values[ch_38_idx, i, deep_mask]
                vals = vals[~np.isnan(vals)]
                nasc_deep = float(np.sum(vals))

            nasc_combined = nasc_shallow + nasc_deep
            if nasc_combined < 0.1:
                continue

            nasc_38_total = 0.0
            if ch_38_idx is not None:
                vals = nasc_values[ch_38_idx, i, full_38_mask]
                nasc_38_total = float(np.sum(vals[~np.isnan(vals)]))

            nasc_200_total = 0.0
            if ch_200_idx is not None:
                vals = nasc_values[ch_200_idx, i, full_200_mask]
                nasc_200_total = float(np.sum(vals[~np.isnan(vals)]))

            time_str = ""
            if ping_times is not None:
                t = ping_times[i]
                if not np.isnat(t):
                    time_str = str(np.datetime_as_string(t, unit="s")) + "Z"

            features.append({
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": [round(lon, 6), round(lat, 6)],
                },
                "properties": {
                    "nasc_combined": round(nasc_combined, 1),
                    "nasc_38": round(nasc_38_total, 1),
                    "nasc_200": round(nasc_200_total, 1),
                    "date": day_key,
                    "time": time_str,
                    "distance_bin": i,
                },
            })

        ds.close()
        return features

    except Exception as e:
        log.warning("  NASC extraction failed for %s: %s", zarr_path.name, e)
        try:
            ds.close()
        except Exception:
            pass
        return []


def build_nasc_biomass_geojson(
    output_dir: Path,
    container_dir: Path,
    campaign_id: str = "saildrone_tpos_2023",
) -> Optional[Path]:
    """Build NASC Biomass GeoJSON from local per-day NASC zarrs.

    Reads all ``{day}--{pulse}--nasc.zarr`` files, applies the
    merged-frequency depth integration (200 kHz 10–150m + 38 kHz
    150–500m), removes sparse outlier bins, caps P99, and writes
    a GeoJSON FeatureCollection.

    Args:
        output_dir: Directory for output files.
        container_dir: Local directory containing day folders.
        campaign_id: Campaign identifier for naming.

    Returns:
        Path to the generated GeoJSON file, or None.
    """
    import json as json_mod
    from collections import Counter

    all_features: list[dict] = []
    day_dirs = sorted(
        d for d in container_dir.iterdir()
        if d.is_dir() and re.match(r"\d{4}-\d{2}-\d{2}$", d.name)
    )

    processed = skipped = 0
    for day_dir in day_dirs:
        day_key = day_dir.name
        day_features: list[dict] = []
        for item in day_dir.iterdir():
            if _NASC_RE.match(item.name) and item.is_dir():
                feats = _extract_nasc_from_local_zarr(item, day_key)
                day_features.extend(feats)
        if day_features:
            all_features.extend(day_features)
            processed += 1
        else:
            skipped += 1

    log.info(
        "Extracted %d NASC points from %d days (%d skipped)",
        len(all_features), processed, skipped,
    )

    if not all_features:
        log.error("No NASC features extracted")
        return None

    # Remove orphan points in sparse 1° bins (≤ 2 points)
    bin_counts = Counter(
        (round(f["geometry"]["coordinates"][1]),
         round(f["geometry"]["coordinates"][0]))
        for f in all_features
    )
    sparse_bins = {k for k, v in bin_counts.items() if v <= 2}
    if sparse_bins:
        before = len(all_features)
        all_features = [
            f for f in all_features
            if (round(f["geometry"]["coordinates"][1]),
                round(f["geometry"]["coordinates"][0])) not in sparse_bins
        ]
        log.info("Removed %d orphan points from %d sparse bins", before - len(all_features), len(sparse_bins))

    # Cap extreme outliers at P99
    nasc_vals = sorted(f["properties"]["nasc_combined"] for f in all_features)
    if nasc_vals:
        p99 = nasc_vals[int(len(nasc_vals) * 0.99)]
        capped = 0
        for f in all_features:
            if f["properties"]["nasc_combined"] > p99:
                f["properties"]["nasc_combined"] = round(p99, 1)
                capped += 1
        if capped:
            log.info("Capped %d values to P99=%.1f", capped, p99)

    # Stats
    vals = [f["properties"]["nasc_combined"] for f in all_features]
    log.info(
        "NASC stats — count=%d, min=%.0f, max=%.0f, mean=%.0f, median=%.0f",
        len(vals), min(vals), max(vals),
        sum(vals) / len(vals),
        vals[len(vals) // 2],
    )

    geojson = {"type": "FeatureCollection", "features": all_features}

    nasc_dir = output_dir / "nasc_biomass"
    nasc_dir.mkdir(parents=True, exist_ok=True)
    out_path = nasc_dir / f"{campaign_id}.geojson"
    with open(out_path, "w") as f:
        json_mod.dump(geojson, f)
    log.info("Wrote NASC GeoJSON: %s (%.1f KB)", out_path, out_path.stat().st_size / 1024)
    return out_path


# ---------------------------------------------------------------------------
# Stage 13: NASC Heatmap COGs
# ---------------------------------------------------------------------------

def build_nasc_heatmap_cogs(
    geojson_path: Path,
    output_dir: Path,
    campaign_id: str = "saildrone_tpos_2023",
    resolution_deg: float = 0.05,
    search_radius_deg: float = 0.5,
    nodata: float = -9999.0,
) -> list[Path]:
    """Rasterize NASC points to Cloud-Optimized GeoTIFF heatmaps.

    Generates COGs + PNG overlays for nasc_combined, nasc_38, and nasc_200
    using scipy interpolation and rasterio, matching the os-webapp heatmap
    pattern.

    Args:
        geojson_path: Path to NASC biomass GeoJSON.
        output_dir: Directory for output COG/PNG files.
        campaign_id: Campaign identifier.
        resolution_deg: Spatial resolution in degrees.
        search_radius_deg: Maximum distance from track for valid cells.
        nodata: NoData value for the GeoTIFF.

    Returns:
        List of generated file paths.
    """
    import json as json_mod

    try:
        import rasterio
        from rasterio.transform import from_bounds
        from scipy.interpolate import griddata
        from scipy.spatial import cKDTree
    except ImportError as e:
        log.error("Missing dependency for heatmaps: %s — install rasterio + scipy", e)
        return []

    with open(geojson_path) as f:
        geojson = json_mod.load(f)

    features = geojson.get("features", [])
    if not features:
        log.error("No features in GeoJSON")
        return []

    lons = np.array([f["geometry"]["coordinates"][0] for f in features])
    lats = np.array([f["geometry"]["coordinates"][1] for f in features])

    heatmap_dir = output_dir / "heatmaps"
    heatmap_dir.mkdir(parents=True, exist_ok=True)

    variables = {
        "nasc_combined": {"cmap": "YlOrRd", "label": "NASC Combined (m² nmi⁻²)"},
        "nasc_38":       {"cmap": "Blues",   "label": "NASC 38 kHz (m² nmi⁻²)"},
        "nasc_200":      {"cmap": "Greens",  "label": "NASC 200 kHz (m² nmi⁻²)"},
    }

    generated: list[Path] = []
    manifest_entries = []

    for var_name, var_cfg in variables.items():
        values = np.array([f["properties"].get(var_name, 0) for f in features], dtype=np.float64)
        valid = ~np.isnan(values) & (values > 0)
        if valid.sum() < 3:
            log.warning("  %s: not enough valid points (%d)", var_name, valid.sum())
            continue

        vlon, vlat, vval = lons[valid], lats[valid], values[valid]
        log.info("  %s: %d valid points", var_name, len(vval))

        # Grid bounds with buffer
        buf = resolution_deg * 2
        lon_min, lon_max = vlon.min() - buf, vlon.max() + buf
        lat_min, lat_max = vlat.min() - buf, vlat.max() + buf
        n_cols = int(np.ceil((lon_max - lon_min) / resolution_deg))
        n_rows = int(np.ceil((lat_max - lat_min) / resolution_deg))

        grid_lon = np.linspace(lon_min, lon_max, n_cols)
        grid_lat = np.linspace(lat_min, lat_max, n_rows)
        grid_lon_2d, grid_lat_2d = np.meshgrid(grid_lon, grid_lat)

        # Interpolate
        grid_values = griddata(
            np.column_stack([vlon, vlat]),
            vval,
            (grid_lon_2d, grid_lat_2d),
            method="linear",
            fill_value=np.nan,
        )

        # Mask cells too far from any data point
        tree = cKDTree(np.column_stack([vlon, vlat]))
        grid_pts = np.column_stack([grid_lon_2d.ravel(), grid_lat_2d.ravel()])
        dists, _ = tree.query(grid_pts)
        mask = dists.reshape(grid_values.shape) > search_radius_deg
        grid_values[mask] = np.nan

        # Write GeoTIFF
        transform = from_bounds(lon_min, lat_min, lon_max, lat_max, n_cols, n_rows)
        tif_path = heatmap_dir / f"{campaign_id}_{var_name}.tif"
        data_out = np.where(np.isnan(grid_values), nodata, grid_values).astype(np.float32)
        # Flip rows for north-up raster
        data_out = np.flipud(data_out)

        with rasterio.open(
            str(tif_path), "w", driver="GTiff",
            height=n_rows, width=n_cols, count=1, dtype="float32",
            crs="EPSG:4326", transform=transform, nodata=nodata,
        ) as dst:
            dst.write(data_out, 1)

        # Convert to COG
        cog_path = heatmap_dir / f"{campaign_id}_{var_name}_cog.tif"
        try:
            import subprocess
            subprocess.run(
                [
                    "gdal_translate", "-of", "COG",
                    "-co", "COMPRESS=DEFLATE",
                    str(tif_path), str(cog_path),
                ],
                capture_output=True, text=True, check=True,
            )
            tif_path.unlink()
            log.info("  COG: %s (%.1f KB)", cog_path.name, cog_path.stat().st_size / 1024)
            generated.append(cog_path)
        except (FileNotFoundError, subprocess.CalledProcessError) as e:
            log.warning("  gdal_translate not available (%s), keeping raw GeoTIFF", e)
            cog_path = tif_path
            generated.append(tif_path)

        # Render PNG overlay
        png_path = heatmap_dir / f"{campaign_id}_{var_name}.png"
        valid_data = grid_values[~np.isnan(grid_values)]
        if len(valid_data) > 0:
            vmin, vmax = np.percentile(valid_data, [2, 98])
            cmap_obj = plt.get_cmap(var_cfg["cmap"])
            norm = mcolors.Normalize(vmin=max(vmin, 0.1), vmax=vmax)

            rgba = cmap_obj(norm(grid_values))
            rgba[np.isnan(grid_values), 3] = 0  # transparent where no data

            fig, ax = plt.subplots(figsize=(12, 8))
            ax.imshow(
                rgba,
                extent=[lon_min, lon_max, lat_min, lat_max],
                origin="lower", aspect="auto",
            )
            ax.set_xlabel("Longitude")
            ax.set_ylabel("Latitude")
            ax.set_title(f"{var_cfg['label']} — {campaign_id}")
            sm = plt.cm.ScalarMappable(cmap=cmap_obj, norm=norm)
            sm.set_array([])
            fig.colorbar(sm, ax=ax, label=var_cfg["label"], shrink=0.8)
            fig.savefig(png_path, dpi=150, bbox_inches="tight")
            plt.close(fig)
            generated.append(png_path)
            log.info("  PNG: %s (%.1f KB)", png_path.name, png_path.stat().st_size / 1024)

        # Manifest entry
        manifest_entries.append({
            "variable": var_name,
            "label": var_cfg["label"],
            "cog": cog_path.name,
            "png": png_path.name if png_path.exists() else None,
            "bounds": [lon_min, lat_min, lon_max, lat_max],
            "resolution_deg": resolution_deg,
            "point_count": int(valid.sum()),
        })

    # Write manifest
    manifest_path = heatmap_dir / "manifest.json"
    with open(manifest_path, "w") as f:
        json_mod.dump({
            "campaign_id": campaign_id,
            "type": "nasc_heatmaps",
            "heatmaps": manifest_entries,
        }, f, indent=2)
    generated.append(manifest_path)
    log.info("Manifest: %s", manifest_path)

    return generated


# ---------------------------------------------------------------------------
# Per-day processing (top-level for ProcessPoolExecutor pickling)
# ---------------------------------------------------------------------------

def _process_single_day(
    day_key: str,
    day_raw: list,
    output_container: str,
    gps_df,
    calibration_file: str,
    file_share: str,
    file_share_path: str,
) -> dict:
    """Process one day: raw → EchoData → combine → Sv → denoise.

    Returns a dict with:
      - ``day_key``
      - ``sv_zarrs``:       {category: zarr_path}
      - ``denoised_zarrs``: {category: zarr_path}
      - ``denoised_list``:  [(day_key, category, zarr_path), ...]
      - ``error``:          str or None
    """
    import pandas as pd

    result = {
        "day_key": day_key,
        "sv_zarrs": {},
        "denoised_zarrs": {},
        "denoised_list": [],
        "error": None,
    }

    day_start = time.time()
    log.info("── Processing day: %s (%d raw files) ──", day_key, len(day_raw))

    # Stage 3: Download + convert raw → EchoData
    try:
        file_results = process_day_raw_files(
            day_key, day_raw, _RAW_DIR, _ECHODATA_DIR,
            calibration_file=calibration_file,
            file_share_name=file_share,
            file_share_path=file_share_path,
        )
    except Exception as e:
        log.error("  Raw conversion failed for %s: %s", day_key, e)
        result["error"] = f"raw conversion: {e}"
        return result

    # Stage 4: Group by category and combine EchoData
    by_cat: dict[str, list[str]] = defaultdict(list)
    for cat, zp, fn in file_results:
        if zp:
            by_cat[cat].append(zp)

    if not by_cat:
        log.error("  No EchoData produced for %s — skipping", day_key)
        result["error"] = "no echodata produced"
        return result

    combined_paths: dict[str, str] = {}
    for category, zarr_paths in by_cat.items():
        try:
            combined_zarr = combine_echodata_day(
                zarr_paths, day_key, category, _ECHODATA_DIR,
            )
            combined_paths[category] = combined_zarr
        except Exception as e:
            log.error("  Combine failed %s/%s: %s", day_key, category, e)

    # Stage 5: Compute Sv (+ add_depth + GPS) → save to Azure
    # Filter GPS to this day ± 1 hour buffer
    day_gps = None
    if gps_df is not None and not gps_df.empty:
        day_dt = datetime.fromisoformat(day_key)
        day_start_gps = day_dt - timedelta(hours=1)
        day_end_gps = day_dt + timedelta(days=1, hours=1)
        mask = (gps_df["dt"] >= day_start_gps) & (gps_df["dt"] <= day_end_gps)
        day_gps = gps_df[mask]
        if day_gps.empty:
            day_gps = None

    for category, ed_zarr in combined_paths.items():
        try:
            sv_path = compute_sv_day(
                ed_zarr, day_key, category, output_container,
                gps_df=day_gps,
            )
            result["sv_zarrs"][category] = sv_path
        except Exception as e:
            log.error("  Sv computation failed %s/%s: %s", day_key, category, e)

    # Stage 6: Denoise each Sv zarr
    for category, sv_path in result["sv_zarrs"].items():
        try:
            denoised_path = denoise_day_zarr(
                sv_path, output_container, day_key, category,
            )
            result["denoised_zarrs"][category] = denoised_path
            result["denoised_list"].append((day_key, category, denoised_path))
        except Exception as e:
            log.error("  Denoise failed %s/%s: %s", day_key, category, e)

    # Clean up raw downloads for this day
    day_raw_dir = _RAW_DIR / day_key
    if day_raw_dir.exists():
        shutil.rmtree(day_raw_dir, ignore_errors=True)

    elapsed = time.time() - day_start
    log.info(
        "  Day %s complete in %.1fs (%.1f min)",
        day_key, elapsed, elapsed / 60,
    )
    _release_memory()
    return result


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def run_full_pipeline(args: argparse.Namespace) -> None:
    """Execute the full survey pipeline: raw → echodata → Sv → denoise → products."""
    pipeline_start = time.time()

    output_container = args.output_container
    _ensure_container(output_container)
    log.info("Output container: %s", output_container)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    ECHOGRAM_DIR.mkdir(parents=True, exist_ok=True)
    _RAW_DIR.mkdir(parents=True, exist_ok=True)
    _ECHODATA_DIR.mkdir(parents=True, exist_ok=True)

    start_date = datetime.fromisoformat(args.start_date) if args.start_date else None
    end_date = datetime.fromisoformat(args.end_date) if args.end_date else None

    # Determine which frequencies to process
    freq_configs = FREQUENCY_CONFIGS
    if args.freq:
        requested = {f.strip() for f in args.freq.split(",")}
        freq_configs = [
            fc for fc in FREQUENCY_CONFIGS
            if any(f in fc[2] for f in requested)
        ]
        if not freq_configs:
            log.error("No matching frequencies for --freq %s", args.freq)
            sys.exit(1)

    # ── Stage 1: Discover raw EK80 files ────────────────────────
    day_files: dict = {}
    if not args.skip_sv and not args.skip_raw:
        log.info("=" * 70)
        log.info("STAGE 1: Discover raw EK80 files")
        log.info("=" * 70)
        t0 = time.time()

        raw_files = discover_raw_files(
            start_date, end_date,
            file_share_name=args.file_share,
            file_share_path=args.file_share_path,
        )
        if not raw_files:
            log.error("No raw files found — check file share")
            sys.exit(1)

        day_files = group_raw_by_day(raw_files)
        log.info(
            "Stage 1 complete: %d raw files across %d days (%.1fs)",
            len(raw_files), len(day_files), time.time() - t0,
        )
    else:
        log.info("Skipping Stage 1 (raw file discovery) — using existing zarrs")

    # ── Stage 2: Download GPS GeoParquet ────────────────────────
    gps_df = None
    if not args.skip_gps and not args.skip_sv:
        log.info("=" * 70)
        log.info("STAGE 2: Download GPS GeoParquet")
        log.info("=" * 70)
        t0 = time.time()
        gps_df = download_gps_geoparquet(
            gps_container=args.gps_container,
            cruise_id=CRUISE_ID,
            start_date=start_date,
            end_date=end_date,
        )
        if gps_df is not None:
            log.info("Stage 2 complete: %d GPS points (%.1fs)", len(gps_df), time.time() - t0)
        else:
            log.warning("No GPS data — Sv will not have lat/lon")
    else:
        log.info("--skip-gps: skipping GPS download")

    # ── Check what's already done (for --resume) ────────────────
    already_denoised: set[str] = set()
    if args.resume:
        log.info("Checking for existing denoised zarrs (--resume) ...")
        already_denoised = list_already_denoised_days(output_container)
        log.info("  Found %d already-denoised day/category entries", len(already_denoised))

    # ── Stage 3–6: Raw → EchoData → Sv → Denoise (per day) ────
    log.info("=" * 70)
    log.info("STAGE 3-6: Raw → EchoData → combine → Sv → denoise (per day)")
    log.info("=" * 70)
    t0 = time.time()

    all_denoised: list[tuple[str, str, str]] = []
    day_sv_zarrs: dict[str, dict[str, str]] = {}
    day_denoised_zarrs: dict[str, dict[str, str]] = {}
    total_days = len(day_files)

    if args.skip_raw:
        log.info("--skip-raw: using existing Sv + denoised zarrs from %s", output_container)
        all_denoised = list_denoised_zarrs(output_container)
        if start_date or end_date:
            all_denoised = [
                (d, c, p) for d, c, p in all_denoised
                if (not start_date or datetime.fromisoformat(d) >= start_date)
                and (not end_date or datetime.fromisoformat(d) <= end_date)
            ]
        log.info("  Found %d denoised zarrs", len(all_denoised))
        for day_key, category, zarr_path in all_denoised:
            day_denoised_zarrs.setdefault(day_key, {})[category] = zarr_path
            sv_path = f"{day_key}/{day_key}--{category}.zarr"
            day_sv_zarrs.setdefault(day_key, {})[category] = sv_path
    elif args.skip_sv:
        log.info("--skip-sv: loading existing Sv zarrs, re-running denoise → products")
        sv_entries = list_sv_zarrs(output_container)
        if start_date or end_date:
            sv_entries = [
                (d, c, p) for d, c, p in sv_entries
                if (not start_date or datetime.fromisoformat(d) >= start_date)
                and (not end_date or datetime.fromisoformat(d) <= end_date)
            ]
        log.info("  Found %d Sv zarrs to re-denoise", len(sv_entries))
        for day_key, category, sv_path in sv_entries:
            day_sv_zarrs.setdefault(day_key, {})[category] = sv_path

        # Re-denoise each Sv zarr
        for day_key, categories in sorted(day_sv_zarrs.items()):
            for category, sv_path in categories.items():
                try:
                    denoised_path = denoise_day_zarr(
                        sv_path, output_container, day_key, category,
                    )
                    day_denoised_zarrs.setdefault(day_key, {})[category] = denoised_path
                    all_denoised.append((day_key, category, denoised_path))
                except Exception as e:
                    log.error("  Denoise failed %s/%s: %s", day_key, category, e)
                _release_memory()
        log.info("  Re-denoised %d zarrs across %d days", len(all_denoised), len(day_denoised_zarrs))
    else:
        # Filter days for --resume
        days_to_process = []
        for day_key, day_raw in day_files.items():
            if args.resume:
                day_done = any(
                    f"{day_key}/{cat}" in already_denoised
                    for cat in ("short_pulse", "long_pulse")
                )
                if day_done:
                    log.info("  %s: already processed — skipping (--resume)", day_key)
                    for cat in ("short_pulse", "long_pulse"):
                        key = f"{day_key}/{cat}"
                        if key in already_denoised:
                            zarr_path = f"{day_key}/{day_key}--{cat}--denoised.zarr"
                            all_denoised.append((day_key, cat, zarr_path))
                            day_denoised_zarrs.setdefault(day_key, {})[cat] = zarr_path
                            day_sv_zarrs.setdefault(day_key, {})[cat] = (
                                f"{day_key}/{day_key}--{cat}.zarr"
                            )
                    continue
            days_to_process.append((day_key, day_raw))

        parallel_days = max(1, args.parallel_days)

        def _collect_day_result(res: dict) -> None:
            """Fold a _process_single_day result into the pipeline accumulators."""
            dk = res["day_key"]
            for category, sv_path in res["sv_zarrs"].items():
                day_sv_zarrs.setdefault(dk, {})[category] = sv_path
            for category, dp in res["denoised_zarrs"].items():
                day_denoised_zarrs.setdefault(dk, {})[category] = dp
            all_denoised.extend(res["denoised_list"])

        if parallel_days <= 1:
            # Sequential processing
            for idx, (day_key, day_raw) in enumerate(days_to_process, 1):
                log.info("── Day %d/%d ──", idx, len(days_to_process))
                res = _process_single_day(
                    day_key, day_raw, output_container, gps_df,
                    calibration_file=args.calibration_file,
                    file_share=args.file_share,
                    file_share_path=args.file_share_path,
                )
                _collect_day_result(res)
        else:
            # Parallel processing with ProcessPoolExecutor
            log.info(
                "Parallel mode: %d concurrent days (%d days to process)",
                parallel_days, len(days_to_process),
            )
            with ProcessPoolExecutor(max_workers=parallel_days) as executor:
                futures = {}
                for day_key, day_raw in days_to_process:
                    fut = executor.submit(
                        _process_single_day,
                        day_key, day_raw, output_container, gps_df,
                        calibration_file=args.calibration_file,
                        file_share=args.file_share,
                        file_share_path=args.file_share_path,
                    )
                    futures[fut] = day_key

                done_count = 0
                for fut in as_completed(futures):
                    done_count += 1
                    dk = futures[fut]
                    try:
                        res = fut.result()
                        _collect_day_result(res)
                        if res["error"]:
                            log.warning(
                                "  Day %s finished with error: %s (%d/%d)",
                                dk, res["error"], done_count, len(days_to_process),
                            )
                        else:
                            log.info(
                                "  Day %s done (%d/%d)",
                                dk, done_count, len(days_to_process),
                            )
                    except Exception as e:
                        log.error(
                            "  Day %s raised exception: %s (%d/%d)",
                            dk, e, done_count, len(days_to_process),
                        )

    log.info(
        "Stage 3-6 complete: %d denoised zarrs across %d days (%.1fs / %.1f min)",
        len(all_denoised), len(day_denoised_zarrs),
        time.time() - t0, (time.time() - t0) / 60,
    )

    if not all_denoised:
        log.error("No denoised data — cannot continue")
        sys.exit(1)

    # ── Stage 7: Per-day MVBS + NASC ───────────────────────────
    log.info("=" * 70)
    log.info("STAGE 7: Per-day MVBS + NASC")
    log.info("=" * 70)
    t0 = time.time()

    day_mvbs_zarrs: dict[str, dict[str, str]] = {}
    day_nasc_zarrs: dict[str, dict[str, str]] = {}

    for day_key, categories in sorted(day_denoised_zarrs.items()):
        for category, denoised_path in categories.items():
            try:
                mvbs_path = compute_perday_mvbs(
                    day_key, category, denoised_path, output_container,
                )
                day_mvbs_zarrs.setdefault(day_key, {})[category] = mvbs_path
            except Exception as e:
                log.error("  Per-day MVBS failed %s/%s: %s", day_key, category, e)

            if not args.skip_nasc:
                try:
                    nasc_path = compute_perday_nasc(
                        day_key, category, denoised_path, output_container,
                    )
                    if nasc_path:
                        day_nasc_zarrs.setdefault(day_key, {})[category] = nasc_path
                except Exception as e:
                    log.error("  Per-day NASC failed %s/%s: %s", day_key, category, e)

            _release_memory()

    log.info(
        "Stage 7 complete: %d MVBS, %d NASC zarrs (%.1fs)",
        sum(len(v) for v in day_mvbs_zarrs.values()),
        sum(len(v) for v in day_nasc_zarrs.values()),
        time.time() - t0,
    )

    # ── Stage 8: Per-day echograms ─────────────────────────────
    if not args.skip_perday_echograms:
        log.info("=" * 70)
        log.info("STAGE 8: Per-day echograms (Sv / denoised / MVBS, combined pulse modes)")
        log.info("=" * 70)
        t0 = time.time()

        total_echograms = 0
        for day_key in sorted(day_denoised_zarrs.keys()):
            try:
                files = generate_perday_echograms(
                    day_key,
                    concat_zarrs=day_sv_zarrs.get(day_key, {}),
                    denoised_zarrs=day_denoised_zarrs.get(day_key, {}),
                    mvbs_zarrs=day_mvbs_zarrs.get(day_key, {}),
                    output_container=output_container,
                    output_dir=OUTPUT_DIR,
                    freq_configs=freq_configs,
                )
                total_echograms += len(files)
                log.info("  %s: %d echogram files", day_key, len(files))
            except Exception as e:
                log.error("  Per-day echograms failed %s: %s", day_key, e)
            _release_memory()

        log.info("Stage 8 complete: %d echogram files (%.1fs)", total_echograms, time.time() - t0)

    # ── Stage 9+10: Combined campaign MVBS + echograms ─────────
    all_denoised.sort()

    for freq_hz, freq_label, freq_stem, allowed_cats in freq_configs:
        log.info("=" * 70)
        log.info("STAGE 9: Build combined %s MVBS campaign zarr", freq_label)
        log.info("=" * 70)
        t0 = time.time()

        campaign_ds = build_combined_zarr(
            day_mvbs_zarrs, output_container,
            freq_hz=freq_hz, freq_label=freq_label,
            freq_stem=freq_stem,
            allowed_categories=allowed_cats,
            local_save_dir=getattr(args, '_local_save_dir', None),
        )
        if campaign_ds is None:
            log.warning("No data for %s — skipping", freq_label)
            continue

        campaign_ds = campaign_ds.load()
        log.info("Combined %s shape: %s", freq_label, dict(campaign_ds.sizes))
        log.info(
            "Time: %s → %s",
            str(campaign_ds.ping_time.values[0])[:19],
            str(campaign_ds.ping_time.values[-1])[:19],
        )

        zarr_path = OUTPUT_DIR / f"campaign_mvbs_combined_{freq_stem}.zarr"
        campaign_ds = normalize_string_dtypes(campaign_ds)
        campaign_ds.to_zarr(str(zarr_path), mode="w")
        log.info("Saved combined zarr: %s", zarr_path)
        log.info("Stage 9 (%s) complete (%.1fs)", freq_label, time.time() - t0)

        log.info("=" * 70)
        log.info("STAGE 10: Generate %s campaign echograms (per segment)", freq_label)
        log.info("=" * 70)
        t0 = time.time()

        for seg_label, seg_start, seg_end in SURVEY_SEGMENTS:
            seg_start_np = np.datetime64(seg_start)
            seg_end_np = np.datetime64(seg_end)
            seg_ds = campaign_ds.sel(
                ping_time=(campaign_ds.ping_time >= seg_start_np)
                & (campaign_ds.ping_time < seg_end_np)
            )
            n_seg = seg_ds.sizes.get("ping_time", 0)
            if n_seg == 0:
                log.warning("  %s: no data in %s → %s — skipping", seg_label, seg_start, seg_end)
                continue

            log.info("  %s: %s → %s (%d pings)", seg_label, seg_start, seg_end, n_seg)
            for cmap_name, cmap in COLORMAPS:
                plot_combined_echogram(
                    seg_ds, cmap_name, cmap,
                    freq_label=freq_label, freq_stem=freq_stem,
                    segment_label=seg_label,
                )

        log.info("Stage 10 (%s) complete (%.1fs)", freq_label, time.time() - t0)

        campaign_ds.close()
        del campaign_ds
        gc.collect()

    # ── Stage 11: Echodata PMTiles ─────────────────────────────
    log.info("=" * 70)
    log.info("STAGE 11: Echodata PMTiles (acoustic track)")
    log.info("=" * 70)
    t0 = time.time()

    container_dir = getattr(args, '_local_save_dir', None)
    if container_dir is not None:
        container_dir = container_dir / output_container
    else:
        container_dir = OUTPUT_DIR / output_container
    if not container_dir.exists():
        container_dir = OUTPUT_DIR

    pmtiles_path = build_echodata_pmtiles(
        output_dir=OUTPUT_DIR,
        container_dir=container_dir,
        campaign_id="saildrone_tpos_2023",
        sample_rate=100,
    )
    if pmtiles_path:
        log.info("Stage 11 complete: %s (%.1fs)", pmtiles_path, time.time() - t0)
    else:
        log.warning("Stage 11: PMTiles generation failed or tippecanoe not installed (%.1fs)", time.time() - t0)

    # ── Stage 12: NASC Biomass GeoJSON ─────────────────────────
    if not args.skip_nasc:
        log.info("=" * 70)
        log.info("STAGE 12: NASC Biomass GeoJSON")
        log.info("=" * 70)
        t0 = time.time()

        nasc_geojson_path = build_nasc_biomass_geojson(
            output_dir=OUTPUT_DIR,
            container_dir=container_dir,
            campaign_id="saildrone_tpos_2023",
        )
        if nasc_geojson_path:
            log.info("Stage 12 complete: %s (%.1fs)", nasc_geojson_path, time.time() - t0)
        else:
            log.warning("Stage 12: No NASC GeoJSON produced (%.1fs)", time.time() - t0)

        # ── Stage 13: NASC Heatmap COGs ────────────────────────
        if nasc_geojson_path:
            log.info("=" * 70)
            log.info("STAGE 13: NASC Heatmap COGs")
            log.info("=" * 70)
            t0 = time.time()

            heatmap_files = build_nasc_heatmap_cogs(
                geojson_path=nasc_geojson_path,
                output_dir=OUTPUT_DIR,
                campaign_id="saildrone_tpos_2023",
            )
            log.info("Stage 13 complete: %d files (%.1fs)", len(heatmap_files), time.time() - t0)

    total = time.time() - pipeline_start
    log.info("=" * 70)
    log.info(
        "Pipeline complete. Total: %.1fs (%.1f min / %.1f hours)",
        total, total / 60, total / 3600,
    )
    log.info("Output: %s", OUTPUT_DIR)
    log.info("=" * 70)


def run_echogram_only(args: argparse.Namespace) -> None:
    """Load existing combined zarrs and regenerate echograms."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    ECHOGRAM_DIR.mkdir(parents=True, exist_ok=True)

    freq_configs = FREQUENCY_CONFIGS
    if args.freq:
        requested = {f.strip() for f in args.freq.split(",")}
        freq_configs = [
            fc for fc in FREQUENCY_CONFIGS
            if any(f in fc[2] for f in requested)
        ]

    for freq_hz, freq_label, freq_stem, _ in freq_configs:
        zarr_path = OUTPUT_DIR / f"campaign_mvbs_combined_{freq_stem}.zarr"
        if not zarr_path.exists():
            log.warning("%s zarr not found at %s — skipping", freq_label, zarr_path)
            continue

        log.info("--echogram-only: loading %s zarr %s", freq_label, zarr_path)
        campaign_ds = xr.open_zarr(str(zarr_path)).load()
        log.info("Loaded %s shape: %s", freq_label, dict(campaign_ds.sizes))

        for cmap_name, cmap in COLORMAPS:
            plot_combined_echogram(
                campaign_ds, cmap_name, cmap,
                freq_label=freq_label, freq_stem=freq_stem,
            )

        campaign_ds.close()
        del campaign_ds
        gc.collect()

    log.info("Done (echogram-only).")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Full-survey pipeline: raw EK80 → EchoData → Sv → denoise → products",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    parser.add_argument(
        "--echogram-only", action="store_true",
        help="Skip processing; load existing combined zarrs and regenerate echograms.",
    )
    parser.add_argument(
        "--skip-raw", action="store_true",
        help="Skip raw→Sv stages; use existing Sv + denoised zarrs from output container.",
    )
    parser.add_argument(
        "--skip-sv", action="store_true",
        help="Skip raw→Sv stages; re-run denoising from existing Sv zarrs, then MVBS/NASC/echograms.",
    )
    parser.add_argument(
        "--skip-gps", action="store_true",
        help="Skip GPS GeoParquet download.",
    )
    parser.add_argument(
        "--skip-nasc", action="store_true",
        help="Skip per-day NASC computation.",
    )
    parser.add_argument(
        "--skip-perday-echograms", action="store_true",
        help="Skip per-day echogram generation.",
    )
    parser.add_argument(
        "--resume", action="store_true",
        help="Skip days that already have denoised zarrs in output container.",
    )
    parser.add_argument(
        "--freq",
        help="Comma-separated frequencies to process: '38', '200', or '38,200' (default: both).",
    )
    parser.add_argument(
        "--start-date",
        help="Start date filter (YYYY-MM-DD). Inclusive.",
    )
    parser.add_argument(
        "--end-date",
        help="End date filter (YYYY-MM-DD). Inclusive.",
    )
    parser.add_argument(
        "--output-container", default=OUTPUT_CONTAINER,
        help=f"Azure output container name (default: {OUTPUT_CONTAINER}).",
    )
    parser.add_argument(
        "--gps-container", default=GPS_CONTAINER,
        help=f"Azure GPS GeoParquet container name (default: {GPS_CONTAINER}).",
    )
    parser.add_argument(
        "--file-share", default=RAW_FILE_SHARE,
        help=f"Azure File Share name for raw EK80 files (default: {RAW_FILE_SHARE}).",
    )
    parser.add_argument(
        "--file-share-path", default=RAW_FILE_SHARE_PATH,
        help=f"Directory within the file share (default: {RAW_FILE_SHARE_PATH}).",
    )
    parser.add_argument(
        "--calibration-file", default="",
        help="Path to calibration_values.xlsx (optional).",
    )
    parser.add_argument(
        "--output-dir", type=Path, default=None,
        help="Override local output directory.",
    )
    parser.add_argument(
        "--parallel-days", type=int, default=1,
        help="Number of days to process concurrently (default: 1 = sequential). "
             "Each concurrent day uses ~25-30 GB RAM. E.g. --parallel-days 12 on E48ds_v6 (384 GB).",
    )
    parser.add_argument(
        "--local-save", type=Path, default=None,
        help="Save all outputs to local directory instead of Azure. "
             "Uses local_storage.py to monkey-patch storage functions. "
             "Default on VMs with /mnt/data/output: auto-detected.",
    )
    parser.add_argument(
        "--background-sync", action="store_true",
        help="Enable background sync of local outputs to Azure Blob (requires --local-save). "
             "Uses background_sync.py with azcopy or threaded Python uploader.",
    )
    parser.add_argument(
        "--sync-interval", type=int, default=120,
        help="Seconds between background sync sweeps (default: 120).",
    )
    parser.add_argument(
        "--upload-after", action="store_true",
        help="Bulk upload local outputs to Azure after pipeline completes (requires --local-save). "
             "Alternative to --background-sync for a single upload at the end.",
    )

    args = parser.parse_args()

    # ── Local save setup ────────────────────────────────────────
    local_save_dir = args.local_save
    if local_save_dir is None and _DATA_DISK.exists():
        # Auto-detect on Azure VMs with data disk
        local_save_dir = _DATA_DISK
        log.info("Auto-detected local save dir: %s", local_save_dir)

    if local_save_dir is not None:
        local_save_dir = Path(local_save_dir)
        local_save_dir.mkdir(parents=True, exist_ok=True)
        from local_storage import patch_storage
        patch_storage(local_save_dir)
        log.info("Storage: LOCAL → %s", local_save_dir)
    else:
        log.info("Storage: Azure Blob (direct)")

    # Store on args for access in pipeline functions
    args._local_save_dir = local_save_dir

    if args.output_dir is not None:
        global OUTPUT_DIR, ECHOGRAM_DIR
        OUTPUT_DIR = args.output_dir
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        ECHOGRAM_DIR = OUTPUT_DIR / "campaign_echograms"
        ECHOGRAM_DIR.mkdir(parents=True, exist_ok=True)

    log.info("=" * 70)
    log.info("Saildrone TPOS 2023 — Full Survey Pipeline (raw → products)")
    log.info("=" * 70)

    # ── Background sync ─────────────────────────────────────────
    syncer = None
    if args.background_sync and local_save_dir is not None:
        from background_sync import BackgroundSync
        syncer = BackgroundSync(
            local_dir=local_save_dir,
            container=args.output_container,
            interval=args.sync_interval,
        )
        syncer.start()
        log.info("Background sync: every %ds → %s", args.sync_interval, args.output_container)
    elif args.background_sync:
        log.warning("--background-sync requires --local-save — ignoring")

    try:
        if args.echogram_only:
            run_echogram_only(args)
        else:
            run_full_pipeline(args)
    except KeyboardInterrupt:
        log.info("Pipeline interrupted by user")
    except Exception:
        log.exception("Pipeline failed")
        raise
    finally:
        if syncer is not None:
            syncer.stop()
            syncer.join(timeout=300)

    # Bulk upload after completion (if requested and not background-synced)
    if args.upload_after and local_save_dir is not None and not args.background_sync:
        from process_from_raw import _bulk_upload_to_azure
        _bulk_upload_to_azure(local_save_dir, args.output_container)


if __name__ == "__main__":
    main()
