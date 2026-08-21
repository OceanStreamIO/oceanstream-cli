#!/usr/bin/env python3
"""Single-day EK80 batch processing with local EchoData caching.

Downloads raw .raw files for ONE day from the Azure File Share, converts
to EchoData Zarr (cached locally), then runs the full pipeline:

    raw → cached EchoData → Sv → denoise (4 stages) → seabed mask →
    MVBS / NASC → echograms

Denoise parameters are loaded from a TOML config file (per-frequency
sections, Ryan et al. 2015 defaults shipped).  Re-running for the same
day skips the raw→EchoData conversion when a valid cache exists.

Usage:
    # First run (download → convert → cache → full pipeline)
    python process_single_day.py --day 2023-08-10

    # Re-run (cache hit → Sv → denoise → products only)
    python process_single_day.py --day 2023-08-10

    # Custom denoise parameters
    python process_single_day.py --day 2023-08-10 \\
        --denoise-config my_tuning.toml

    # Force re-conversion from raw
    python process_single_day.py --day 2023-08-10 --force-reconvert

    # Skip heavy output stages
    python process_single_day.py --day 2023-08-10 --skip-echograms --skip-nasc

Requires:
    pip install -e ".[echodata,echodata-viz]"
    pip install azure-storage-file-share
"""

from __future__ import annotations

import argparse
import gc
import json
import logging
import os
import shutil
import sys
import time
import tomllib
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd
from dotenv import load_dotenv

load_dotenv()

# ── Logging setup ───────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
    force=True,
)
logger = logging.getLogger(__name__)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(line_buffering=True)

for _noisy in (
    "azure", "azure.core.pipeline.policies.http_logging_policy",
    "adlfs", "distributed", "distributed.worker", "distributed.scheduler",
    "distributed.nanny", "distributed.comm", "distributed.batched",
    "dask", "bokeh", "tornado.access", "echopype", "fsspec", "zarr",
):
    logging.getLogger(_noisy).setLevel(logging.WARNING)

import warnings
warnings.filterwarnings(
    "ignore", message="Running on a single-machine scheduler", category=UserWarning,
)

# ── Ensure sibling modules and oceanstream are importable ───────────────

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from config import PipelineConfig

# ═══════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════


def _release_memory() -> None:
    gc.collect()
    try:
        import ctypes
        libc = ctypes.CDLL("libc.so.6")
        libc.malloc_trim(0)
    except (OSError, AttributeError):
        pass


# ═══════════════════════════════════════════════════════════════════════════
# Dask cluster management
# ═══════════════════════════════════════════════════════════════════════════


def setup_dask_cluster(
    n_workers: int = 4,
    memory_limit: str = "12GB",
    scheduler_address: str | None = None,
):
    """Create or connect to a Dask distributed cluster.

    Resolution order for scheduler address:
      1. Explicit ``scheduler_address`` argument (--dask-address CLI)
      2. DASK_CLUSTER_ADDRESS environment variable
      3. None → start a local cluster

    Returns the ``dask.distributed.Client`` instance.
    """
    from dask.distributed import Client, LocalCluster

    address = scheduler_address or os.environ.get("DASK_CLUSTER_ADDRESS")

    if address:
        logger.info("Connecting to remote Dask scheduler: %s", address)
        client = Client(address)
    else:
        logger.info(
            "Starting Dask LocalCluster: %d workers × %s",
            n_workers, memory_limit,
        )
        cluster = LocalCluster(
            n_workers=n_workers,
            threads_per_worker=1,
            memory_limit=memory_limit,
            silence_logs=logging.WARNING,
        )
        client = Client(cluster)

    logger.info("Dask dashboard: %s", client.dashboard_link)
    return client


def shutdown_dask(client) -> None:
    """Gracefully shut down the Dask client and cluster."""
    if client is None:
        return
    try:
        client.close()
        if hasattr(client, "cluster") and client.cluster is not None:
            client.cluster.close()
        logger.info("Dask cluster shut down")
    except Exception as e:
        logger.warning("Dask shutdown error: %s", e)


# ═══════════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════════


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Process a single day of Saildrone EK80 data with local EchoData caching.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--day", default="2023-08-10",
                    help="Day to process (YYYY-MM-DD)")
    p.add_argument("--denoise-config",
                    default=str(Path(__file__).resolve().parent / "ryan2015_denoise_defaults.toml"),
                    help="Path to denoise TOML config (per-frequency sections)")
    p.add_argument("--cache-dir", default="./echodata_intermediate",
                    help="Directory for cached EchoData Zarr stores")
    p.add_argument("--output-dir", default="./output",
                    help="Directory for pipeline outputs")
    p.add_argument("--force-reconvert", action="store_true",
                    help="Invalidate cache and re-download/convert from raw")
    p.add_argument("--n-workers", type=int, default=4,
                    help="Dask LocalCluster worker count")
    p.add_argument("--memory-limit", default="12GB",
                    help="Dask per-worker memory limit")
    p.add_argument("--gebco-path", default=None,
                    help="Path to GEBCO NetCDF grid (overrides GEBCO_GRID_PATH env)")
    p.add_argument("--calibration-file", default=None,
                    help="Path to EK80 calibration file (.xlsx or .ecs)")
    p.add_argument("--category", default=None, choices=["short_pulse", "long_pulse"],
                    help="Process only this pulse category (default: both)")
    p.add_argument("--skip-echograms", action="store_true")
    p.add_argument("--skip-sv", action="store_true",
                    help="Skip Sv computation; reuse existing Sv Zarr from output dir")
    p.add_argument("--skip-nasc", action="store_true")
    p.add_argument("--skip-mvbs", action="store_true")
    p.add_argument("--skip-denoise", action="store_true")
    p.add_argument("--save-masks", action="store_true",
                    help="Save individual noise masks (impulse, attenuation, transient) as separate zarrs")
    p.add_argument("--surface-exclude", type=float, default=None,
                    help="Exclude surface data above this depth in metres (e.g. 10 to clip 0-10m)")

    p.add_argument("--prune-empty-pings", action="store_true",
                    help="Remove all-NaN pings before plotting echograms (saves pruned variants)")
    p.add_argument("--skip-seabed-mask", action="store_true")
    p.add_argument("--save-netcdf", action="store_true",
                    help="Also save MVBS and NASC as NetCDF (.nc) files alongside Zarr")
    p.add_argument("--ai-analysis", action="store_true",
                    help="Enable Stage 9: AI echogram analysis (requires Azure OpenAI credentials)")
    p.add_argument("--ai-model", default=None,
                    help="Azure OpenAI deployment name for AI analysis (default: env AZURE_OPENAI_DEPLOYMENT or gpt-5)")
    p.add_argument("--gps-container", default="gpsdata",
                    help="Azure blob container for GPS GeoParquet data")
    p.add_argument("--cruise-id", default="SD_TPOS2023_v03",
                    help="Cruise identifier")
    p.add_argument("--dask-address", default=None,
                    help="Dask scheduler address (e.g. tcp://10.0.10.4:8786). "
                         "If not set, checks DASK_CLUSTER_ADDRESS env var. "
                         "If neither is set, starts a LocalCluster.")
    p.add_argument("--dry-run", action="store_true",
                    help="Print configuration and discovered files, then exit without processing")
    p.add_argument("--cache-only", action="store_true",
                    help="Stop after Stage 2 (download + convert + combine EchoData cache)")
    return p.parse_args()


# ═══════════════════════════════════════════════════════════════════════════
# Denoise config loading (TOML → DenoiseConfig)
# ═══════════════════════════════════════════════════════════════════════════


def load_denoise_config(toml_path: str | Path):
    """Load a denoise TOML config and return an oceanstream DenoiseConfig.

    Implements fallback: any frequency not listed inherits from the 38 kHz
    section (or the first available frequency section).
    """
    from oceanstream.echodata.config import DenoiseConfig

    toml_path = Path(toml_path)
    if not toml_path.exists():
        logger.warning("Denoise config not found: %s — using library defaults", toml_path)
        return DenoiseConfig(
            methods=["impulse", "attenuation", "transient", "background"],
            use_frequency_specific=True,
        )

    with open(toml_path, "rb") as f:
        raw = tomllib.load(f)

    denoise_section = raw.get("echodata", {}).get("denoise", {})
    methods = denoise_section.get("methods", ["impulse", "attenuation", "transient", "background"])
    use_freq = denoise_section.get("use_frequency_specific", True)

    # Parse per-frequency params — TOML keys are strings, normalise to int
    freq_params_raw = denoise_section.get("frequency_params", {})
    freq_params: dict[int, dict[str, dict]] = {}
    for freq_key, method_dict in freq_params_raw.items():
        freq_hz = int(freq_key)
        freq_params[freq_hz] = dict(method_dict)

    # Fallback: ensure every method referenced in `methods` has entries
    # for frequencies that are missing.  Use 38 kHz (or first key) as base.
    if freq_params:
        base_freq = 38000 if 38000 in freq_params else next(iter(freq_params))
        base = freq_params[base_freq]
        for freq_hz, params in freq_params.items():
            for method in methods:
                if method not in params and method in base:
                    params[method] = dict(base[method])

    config = DenoiseConfig(
        methods=methods,
        use_frequency_specific=use_freq,
        frequency_params=freq_params if freq_params else None,
    )

    logger.info("Loaded denoise config from %s", toml_path)
    logger.info("  Methods: %s", methods)
    logger.info("  Frequencies: %s", sorted(freq_params.keys()) if freq_params else "presets")
    return config


# ═══════════════════════════════════════════════════════════════════════════
# Cache manifest
# ═══════════════════════════════════════════════════════════════════════════

MANIFEST_FILENAME = ".cache_manifest.json"


def _write_manifest(
    cache_day_dir: Path,
    files_list: list[tuple[str, dict]],
    category_map: dict[str, str],
) -> None:
    """Write cache manifest recording which files were converted."""
    import echopype

    manifest = {
        "timestamp": datetime.utcnow().isoformat(),
        "echopype_version": echopype.__version__,
        "file_count": len(files_list),
        "files": [
            {"name": rec["raw_filename"], "size": rec.get("file_size", 0)}
            for _, rec in files_list
        ],
        "category_map": category_map,  # {file_name: category}
    }
    manifest_path = cache_day_dir / MANIFEST_FILENAME
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2)
    logger.info("Cache manifest written: %s", manifest_path)


def _read_manifest(cache_day_dir: Path) -> dict | None:
    manifest_path = cache_day_dir / MANIFEST_FILENAME
    if not manifest_path.exists():
        return None
    with open(manifest_path) as f:
        return json.load(f)


def _validate_cache(
    cache_day_dir: Path,
    azure_files: list[tuple[str, dict]],
) -> bool:
    """Check whether the existing cache is valid and complete."""
    import echopype

    manifest = _read_manifest(cache_day_dir)
    if manifest is None:
        logger.info("No cache manifest found — cache miss")
        return False

    # Check echopype version
    cached_version = manifest.get("echopype_version", "")
    if cached_version != echopype.__version__:
        logger.info(
            "echopype version changed (%s → %s) — cache invalidated",
            cached_version, echopype.__version__,
        )
        return False

    # Check file count
    cached_count = manifest.get("file_count", 0)
    azure_count = len(azure_files)
    if azure_count > cached_count:
        logger.info(
            "Azure has more files (%d) than cache (%d) — cache miss",
            azure_count, cached_count,
        )
        return False

    # Check that combined zarrs exist and can be opened
    import xarray as xr

    category_map = manifest.get("category_map", {})
    categories = set(category_map.values())
    if not categories:
        logger.info("No categories in manifest — cache miss")
        return False

    for category in categories:
        combined = cache_day_dir / category / "combined.zarr"
        if not combined.exists():
            logger.info("Combined zarr missing for %s — cache miss", category)
            return False
        try:
            ds = xr.open_zarr(str(combined))
            ds.close()
        except Exception as e:
            logger.warning("Combined zarr unreadable for %s: %s — cache miss", category, e)
            return False

    logger.info("Cache valid (%d files, categories: %s)", cached_count, sorted(categories))
    return True


# ═══════════════════════════════════════════════════════════════════════════
# Stage 1: Discover raw files for the target day
# ═══════════════════════════════════════════════════════════════════════════


def discover_day_files(day: str, cfg: PipelineConfig) -> list[tuple[str, dict]]:
    """List raw files from Azure File Share for exactly one day."""
    from process_from_raw import discover_raw_files

    day_dt = datetime.fromisoformat(day)
    cfg.start_date = day_dt
    cfg.end_date = day_dt + timedelta(days=1) - timedelta(seconds=1)

    files = discover_raw_files(cfg)
    logger.info("Day %s: %d raw files on Azure File Share", day, len(files))
    return files


# ═══════════════════════════════════════════════════════════════════════════
# Stage 2: Download, convert, combine — with caching
# ═══════════════════════════════════════════════════════════════════════════


def ensure_echodata_cache(
    day: str,
    files_list: list[tuple[str, dict]],
    cfg: PipelineConfig,
    cache_dir: Path,
    force: bool = False,
) -> dict[str, str]:
    """Ensure combined EchoData Zarrs exist in the cache for the given day.

    Returns: {category: combined_zarr_path}
    """
    from process_from_raw import (
        download_all_raw_files,
        convert_and_save_echodata,
        combine_echodata_day,
        _release_memory as pfr_release,
    )

    cache_day_dir = cache_dir / day
    cache_day_dir.mkdir(parents=True, exist_ok=True)

    # ── Check cache ──────────────────────────────────────────────
    if force:
        logger.info("--force-reconvert: clearing cache for %s", day)
        shutil.rmtree(cache_day_dir, ignore_errors=True)
        cache_day_dir.mkdir(parents=True, exist_ok=True)
    elif _validate_cache(cache_day_dir, files_list):
        manifest = _read_manifest(cache_day_dir)
        categories = set(manifest["category_map"].values())
        result = {}
        for cat in categories:
            result[cat] = str(cache_day_dir / cat / "combined.zarr")
        logger.info("Cache hit — skipping conversion for %s", day)
        return result

    # ── Cache miss: download + convert + combine ─────────────────
    t0 = time.perf_counter()

    # Download
    logger.info("Downloading %d raw files …", len(files_list))
    local_paths = download_all_raw_files(files_list, cfg)

    # Convert each file to EchoData Zarr
    results: list[tuple[str, str, str]] = []
    for raw_filename, rec in files_list:
        if raw_filename not in local_paths:
            logger.error("File %s not downloaded — skipping", rec["file_name"])
            results.append(("unknown", "", rec["file_name"]))
            continue
        try:
            result = convert_and_save_echodata(
                local_raw_path=local_paths[raw_filename],
                file_record=rec,
                cfg=cfg,
                echodata_dir=cache_day_dir / "_per_file",
            )
            results.append(result)
        except Exception as e:
            logger.error("Conversion failed for %s: %s", rec["file_name"], e)
            results.append(("unknown", "", rec["file_name"]))
        finally:
            # Delete raw file to free disk
            if not getattr(cfg, "keep_raw", False) and raw_filename in local_paths:
                local_paths[raw_filename].unlink(missing_ok=True)

    # Group by category
    by_category: dict[str, list[str]] = defaultdict(list)
    category_map: dict[str, str] = {}
    for cat, ed_zarr, file_name in results:
        if ed_zarr and cat != "unknown":
            by_category[cat].append(ed_zarr)
            category_map[file_name] = cat

    # Combine per category
    combined_paths: dict[str, str] = {}
    for cat, ed_zarr_paths in by_category.items():
        cat_dir = cache_day_dir / cat
        cat_dir.mkdir(parents=True, exist_ok=True)

        if len(ed_zarr_paths) == 1:
            # Single file — just move/rename
            src = Path(ed_zarr_paths[0])
            dest = cat_dir / "combined.zarr"
            if dest.exists():
                shutil.rmtree(dest)
            shutil.move(str(src), str(dest))
            combined_paths[cat] = str(dest)
        else:
            combined_zarr = combine_echodata_day(
                ed_zarr_paths=ed_zarr_paths,
                day_key=day,
                category=cat,
                echodata_dir=cat_dir,
            )
            # Rename to standard path
            dest = cat_dir / "combined.zarr"
            if dest.exists():
                shutil.rmtree(dest)
            shutil.move(combined_zarr, str(dest))
            combined_paths[cat] = str(dest)

    # Clean up per-file dir
    per_file_dir = cache_day_dir / "_per_file"
    if per_file_dir.exists():
        shutil.rmtree(per_file_dir, ignore_errors=True)

    # Write manifest
    _write_manifest(cache_day_dir, files_list, category_map)

    elapsed = time.perf_counter() - t0
    logger.info("EchoData cache built in %.0fs: %s", elapsed, sorted(combined_paths.keys()))
    return combined_paths


# ═══════════════════════════════════════════════════════════════════════════
# Stage 3: Compute Sv
# ═══════════════════════════════════════════════════════════════════════════


def compute_sv(
    day: str,
    combined_paths: dict[str, str],
    cfg: PipelineConfig,
    output_dir: Path,
    gps_df: pd.DataFrame | None = None,
    category_filter: str | None = None,
) -> dict[str, str]:
    """Compute Sv + add_depth + GPS for each category.

    Returns {category: sv_zarr_path}.
    """
    from process_from_raw import compute_sv_day

    sv_paths: dict[str, str] = {}
    for cat, ed_zarr in combined_paths.items():
        if category_filter and cat != category_filter:
            continue
        t0 = time.perf_counter()

        sv_zarr = compute_sv_day(
            ed_zarr_path=ed_zarr,
            day_key=day,
            category=cat,
            cfg=cfg,
            output_container="",  # local storage, container is ignored
            gps_df=gps_df,
        )
        sv_paths[cat] = sv_zarr
        logger.info("Sv %s/%s done in %.0fs", day, cat, time.perf_counter() - t0)

    return sv_paths


# ═══════════════════════════════════════════════════════════════════════════
# Stage 4: Denoise
# ═══════════════════════════════════════════════════════════════════════════


def denoise(
    day: str,
    sv_paths: dict[str, str],
    denoise_config,
    cfg: PipelineConfig,
    output_dir: Path,
    save_masks: bool = False,
    surface_exclude: float | None = None,
) -> dict[str, str]:
    """Apply denoising to each Sv Zarr.

    Returns {category: denoised_zarr_path}.
    """
    from process_campaign import denoise_day

    chunks = cfg.chunks.as_dict()
    denoised_paths: dict[str, str] = {}

    for cat, sv_zarr in sv_paths.items():
        t0 = time.perf_counter()
        denoised_zarr = denoise_day(
            zarr_path=sv_zarr,
            output_container="",
            denoise_config=denoise_config,
            chunks=chunks,
            day_key=day,
            category=cat,
            cruise_id=cfg.cruise_id,
            save_masks=save_masks,
            surface_exclude=surface_exclude,
        )
        denoised_paths[cat] = denoised_zarr
        logger.info("Denoise %s/%s done in %.0fs", day, cat, time.perf_counter() - t0)

    return denoised_paths


# ═══════════════════════════════════════════════════════════════════════════
# Stage 5: Seabed masking
# ═══════════════════════════════════════════════════════════════════════════


def seabed_mask(
    day: str,
    denoised_paths: dict[str, str],
    cfg: PipelineConfig,
    output_dir: Path,
) -> dict[str, str]:
    """Apply seabed detection and masking.

    Returns {category: masked_zarr_path} (or denoised_paths unchanged if skipped).
    """
    from process_campaign import mask_seabed_day

    chunks = cfg.chunks.as_dict()
    masked_paths: dict[str, str] = {}

    for cat, zarr_path in denoised_paths.items():
        t0 = time.perf_counter()
        try:
            masked_zarr = mask_seabed_day(
                zarr_path=zarr_path,
                output_container="",
                chunks=chunks,
                day_key=day,
                category=cat,
                cruise_id=cfg.cruise_id,
            )
            masked_paths[cat] = masked_zarr
            logger.info("Seabed mask %s/%s done in %.0fs", day, cat, time.perf_counter() - t0)
        except Exception as e:
            logger.warning("Seabed mask failed for %s/%s: %s — using denoised", day, cat, e)
            masked_paths[cat] = zarr_path

    return masked_paths


def _save_netcdf_local(zarr_path: str, output_dir: Path) -> None:
    """Save a zarr dataset as NetCDF alongside the zarr store.

    Mirrors saildrone-data's save_dataset_to_netcdf pattern:
    engine='netcdf4', NETCDF4 format, zlib compression for numeric vars.
    """
    import numpy as np
    import xarray as xr

    # zarr_path is relative like "2023-08-10/2023-08-10--short_pulse--mvbs.zarr"
    zarr_abs = output_dir / zarr_path
    if not zarr_abs.exists():
        logger.warning("  NetCDF export skipped — zarr not found: %s", zarr_abs)
        return

    nc_path = zarr_abs.with_suffix(".nc")
    try:
        ds = xr.open_zarr(str(zarr_abs))
        ds_computed = ds.compute()
        ds.close()

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

        ds_computed.to_netcdf(
            str(nc_path),
            engine="netcdf4",
            format="NETCDF4",
            encoding=encoding,
        )
        logger.info("  Saved NetCDF: %s", nc_path)
        del ds_computed
    except Exception as e:
        logger.warning("  NetCDF export failed for %s: %s", zarr_abs, e)


# ═══════════════════════════════════════════════════════════════════════════
# Stage 6: MVBS
# ═══════════════════════════════════════════════════════════════════════════


def compute_mvbs(
    day: str,
    input_paths: dict[str, str],
    cfg: PipelineConfig,
    output_dir: Path,
    save_netcdf: bool = False,
) -> dict[str, str]:
    """Compute MVBS for each category.

    Returns {category: mvbs_zarr_path}.
    """
    from process_campaign import compute_mvbs_day

    chunks = cfg.chunks.as_dict()
    mvbs_paths: dict[str, str] = {}

    for cat, zarr_path in input_paths.items():
        t0 = time.perf_counter()
        mvbs_zarr = compute_mvbs_day(
            zarr_path=zarr_path,
            output_container="",
            chunks=chunks,
            range_bin=cfg.mvbs.range_bin,
            ping_time_bin=cfg.mvbs.ping_time_bin,
            day_key=day,
            category=cat,
            cruise_id=cfg.cruise_id,
            surface_exclusion_depth=cfg.surface_exclusion_depth,
        )
        mvbs_paths[cat] = mvbs_zarr

        # Save NetCDF alongside Zarr
        if save_netcdf and mvbs_zarr:
            _save_netcdf_local(mvbs_zarr, output_dir)

        logger.info("MVBS %s/%s done in %.0fs", day, cat, time.perf_counter() - t0)

    return mvbs_paths


# ═══════════════════════════════════════════════════════════════════════════
# Stage 7: NASC
# ═══════════════════════════════════════════════════════════════════════════


def compute_nasc(
    day: str,
    input_paths: dict[str, str],
    cfg: PipelineConfig,
    output_dir: Path,
    save_netcdf: bool = False,
) -> dict[str, str]:
    """Compute NASC for each category.

    Returns {category: nasc_zarr_path}.
    """
    from process_campaign import compute_nasc_day

    chunks = cfg.chunks.as_dict()
    nasc_paths: dict[str, str] = {}

    for cat, zarr_path in input_paths.items():
        t0 = time.perf_counter()
        try:
            nasc_zarr = compute_nasc_day(
                zarr_path=zarr_path,
                output_container="",
                chunks=chunks,
                range_bin=cfg.nasc.range_bin,
                dist_bin=cfg.nasc.dist_bin,
                day_key=day,
                category=cat,
                cruise_id=cfg.cruise_id,
                surface_exclusion_depth=cfg.surface_exclusion_depth,
            )
            if nasc_zarr:
                nasc_paths[cat] = nasc_zarr

                # Save NetCDF alongside Zarr
                if save_netcdf:
                    _save_netcdf_local(nasc_zarr, output_dir)

            logger.info("NASC %s/%s done in %.0fs", day, cat, time.perf_counter() - t0)
        except Exception as e:
            logger.warning("NASC failed for %s/%s: %s", day, cat, e)

    return nasc_paths


# ═══════════════════════════════════════════════════════════════════════════
# Stage 8: Echograms
# ═══════════════════════════════════════════════════════════════════════════


def generate_echograms(
    day: str,
    sv_paths: dict[str, str],
    denoised_paths: dict[str, str],
    mvbs_paths: dict[str, str],
    output_dir: Path,
    surface_exclude: float | None = None,
    prune_empty_pings: bool = False,
) -> None:
    """Generate source, denoised, and MVBS echogram PNGs.

    Args:
        surface_exclude: If set, exclude data shallower than this depth (m)
        prune_empty_pings: If True, also generate a pruned echogram with all-NaN
            pings removed (saves with '--pruned' suffix)
    """
    import xarray as xr
    from oceanstream.echodata.plot.echogram import plot_sv_data

    echogram_dir = output_dir / day / "echograms"
    echogram_dir.mkdir(parents=True, exist_ok=True)

    datasets = [
        ("source", sv_paths),
        ("denoised", denoised_paths),
        ("mvbs", mvbs_paths),
    ]

    for label, paths in datasets:
        for cat, zarr_path in paths.items():
            t0 = time.perf_counter()
            try:
                ds = xr.open_zarr(str(output_dir / zarr_path))

                # Apply surface exclusion
                if surface_exclude is not None:
                    ds = _exclude_surface(ds, surface_exclude)

                # Standard echogram
                files = plot_sv_data(
                    ds,
                    file_base_name=f"{day}--{cat}--{label}",
                    output_path=str(echogram_dir),
                    cmap="ocean_r",
                    vmin=-80,
                    vmax=-50,
                    dpi=150,
                )

                # Pruned echogram (empty pings removed)
                if prune_empty_pings:
                    ds_pruned = _prune_empty_pings(ds)
                    n_removed = ds.sizes["ping_time"] - ds_pruned.sizes["ping_time"]
                    if n_removed > 0:
                        logger.info(
                            "  Pruned %d empty pings (%.1f%%) for %s/%s",
                            n_removed,
                            100 * n_removed / ds.sizes["ping_time"],
                            cat, label,
                        )
                        pruned_files = plot_sv_data(
                            ds_pruned,
                            file_base_name=f"{day}--{cat}--{label}--pruned",
                            output_path=str(echogram_dir),
                            cmap="ocean_r",
                            vmin=-80,
                            vmax=-50,
                            dpi=150,
                        )
                        files.extend(pruned_files)

                ds.close()
                logger.info(
                    "Echogram %s/%s/%s: %d files (%.0fs)",
                    day, cat, label, len(files), time.perf_counter() - t0,
                )
            except Exception as e:
                logger.warning("Echogram failed %s/%s/%s: %s", day, cat, label, e)


def _exclude_surface(ds, depth_threshold: float):
    """Exclude data shallower than depth_threshold metres."""
    if "depth" in ds.dims:
        return ds.sel(depth=slice(depth_threshold, None))
    elif "depth" in ds.coords:
        # depth is a coordinate but not a dim — filter by value
        mask = ds["depth"] >= depth_threshold
        return ds.where(mask)
    return ds


def _prune_empty_pings(ds, vmin: float = -80.0):
    """Remove pings that appear as white vertical lines in echograms.

    A ping is dropped if ANY channel is entirely NaN (fully masked by
    denoising) or if the overall median Sv is below the colorbar minimum.

    Parameters
    ----------
    ds : xarray.Dataset
        Dataset with Sv variable.
    vmin : float
        Colorbar minimum (dB). Pings with median Sv below this are dropped.
    """
    import numpy as np
    sv = ds["Sv"]

    # Drop pings that are all-NaN in any individual channel
    if "channel" in sv.dims:
        # all_nan_per_channel: (channel, ping_time) — True if entire depth is NaN
        all_nan = sv.isnull().all(dim="depth")
        # A ping is bad if ANY channel is fully NaN
        any_channel_empty = all_nan.any(dim="channel")
        keep = ~any_channel_empty
    else:
        keep = ~sv.isnull().all(dim="depth")

    # Also drop pings where overall median Sv < vmin
    reduce_dims = [d for d in sv.dims if d != "ping_time"]
    median_sv = sv.median(dim=reduce_dims, skipna=True)
    keep = keep & (median_sv >= vmin)

    return ds.sel(ping_time=keep)


# ═══════════════════════════════════════════════════════════════════════════
# Stage 9: AI Echogram Analysis
# ═══════════════════════════════════════════════════════════════════════════


def run_ai_echogram_analysis(
    day: str,
    output_dir: Path,
    denoised_paths: dict[str, str],
    model: str | None = None,
) -> None:
    """Run AI vision analysis on denoised echogram PNGs.

    Finds denoised echogram images generated by Stage 8 and sends each to
    Azure OpenAI for biological feature identification. Outputs analysis JSON
    and annotated overlay PNGs alongside the original echograms.
    """
    import asyncio
    from ai_echogram_analysis import analyze_echogram, render_overlay

    echogram_dir = output_dir / day / "echograms"
    if not echogram_dir.exists():
        logger.warning("Stage 9: No echograms directory found at %s", echogram_dir)
        return

    # Find denoised echogram PNGs (pattern: {day}--{cat}--denoised--*.png)
    # Exclude already-annotated files and multifrequency subdirectory
    denoised_pngs = sorted(
        p for p in echogram_dir.glob("*--denoised*.png")
        if "--annotated" not in p.name and "multifrequency" not in str(p)
    )

    if not denoised_pngs:
        logger.warning("Stage 9: No denoised echogram PNGs found in %s", echogram_dir)
        return

    logger.info("Stage 9: Found %d denoised echograms to analyze", len(denoised_pngs))

    for png_path in denoised_pngs:
        # Try to extract frequency from filename (e.g. "...--denoised--38kHz.png")
        frequency_khz = _extract_frequency_from_filename(png_path.name)

        logger.info("  Analyzing: %s (freq=%s kHz)", png_path.name, frequency_khz or "unknown")
        t0 = time.perf_counter()

        try:
            analysis = asyncio.run(
                analyze_echogram(
                    image_path=png_path,
                    frequency_khz=frequency_khz,
                    depth_range="10-1300m",
                    survey_context="Tropical Pacific, Saildrone TPOS 2023",
                    deployment=model,
                )
            )

            # Save analysis JSON
            json_path = png_path.with_name(png_path.stem + "--analysis.json")
            analysis_dict = {
                "summary": analysis.summary,
                "biological_features": [
                    {
                        "type": f.type,
                        "species_guess": f.species_guess,
                        "confidence": f.confidence,
                        "bounding_box": {
                            "x_percent": f.bounding_box.x_percent,
                            "y_percent": f.bounding_box.y_percent,
                        } if f.bounding_box else None,
                        "description": f.description,
                        "estimated_density": f.estimated_density,
                        "depth_range_m": f.depth_range_m,
                        "sv_range_db": f.sv_range_db,
                    }
                    for f in analysis.biological_features
                ],
                "seafloor": analysis.seafloor,
                "noise_assessment": analysis.noise_assessment,
                "annotations": analysis.annotations,
                "recommendations": analysis.recommendations,
                "model": analysis.model,
                "tokens": analysis.tokens,
            }
            with open(json_path, "w") as f:
                json.dump(analysis_dict, f, indent=2)

            # Render annotated overlay
            overlay_path = render_overlay(png_path, analysis)

            n_features = len(analysis.biological_features)
            logger.info(
                "  → %d features, %s quality, saved in %.0fs: %s",
                n_features,
                analysis.noise_assessment.get("overall_quality", "?") if analysis.noise_assessment else "?",
                time.perf_counter() - t0,
                json_path.name,
            )
        except Exception as e:
            logger.warning("  AI analysis failed for %s: %s", png_path.name, e)


def _extract_frequency_from_filename(filename: str) -> int | None:
    """Extract frequency in kHz from echogram filename.

    Looks for patterns like '38kHz', '38-kHz', '200kHz', '38000Hz' in the filename.
    """
    import re
    # Match NNN-kHz or NNNkHz pattern (with optional dash/space)
    m = re.search(r"(\d+)[-\s]*kHz", filename, re.IGNORECASE)
    if m:
        return int(m.group(1))
    # Match NNNNNHz pattern (convert to kHz)
    m = re.search(r"(\d{4,6})\s*Hz", filename, re.IGNORECASE)
    if m:
        return int(m.group(1)) // 1000
    return None


# ═══════════════════════════════════════════════════════════════════════════
# GPS
# ═══════════════════════════════════════════════════════════════════════════


def load_gps(cfg: PipelineConfig) -> pd.DataFrame | None:
    """Download GPS GeoParquet data if configured."""
    try:
        from process_from_raw import download_gps_geoparquet
        return download_gps_geoparquet(cfg)
    except Exception as e:
        logger.warning("GPS data not available: %s — proceeding without GPS", e)
        return None


# ═══════════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════════


def main() -> None:
    args = parse_args()

    day = args.day
    cache_dir = Path(args.cache_dir).resolve()
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    # ── GEBCO path ───────────────────────────────────────────────
    if args.gebco_path:
        os.environ["GEBCO_GRID_PATH"] = args.gebco_path

    # ── Build PipelineConfig ─────────────────────────────────────
    dask_address = args.dask_address or os.environ.get("DASK_CLUSTER_ADDRESS")
    cfg = PipelineConfig(
        cruise_id=args.cruise_id,
        dask=__import__("config").DaskConfig(
            scheduler_address=dask_address,
            n_workers=args.n_workers,
            memory_limit=args.memory_limit,
        ),
        chunks=__import__("config").ChunkConfig(),
        local_save_dir=output_dir,
    )
    if args.calibration_file:
        cfg.raw.calibration_file = args.calibration_file
    cfg.gps_container = args.gps_container

    # ── Configure local storage backend ─────────────────────────
    from oceanstream.echodata.storage import use_local_storage
    use_local_storage(output_dir)

    # ── Start Dask cluster ───────────────────────────────────────
    dask_client = setup_dask_cluster(
        n_workers=args.n_workers,
        memory_limit=args.memory_limit,
        scheduler_address=dask_address,
    )

    # ── Load denoise config ──────────────────────────────────────
    denoise_config = load_denoise_config(args.denoise_config)

    # Also read MVBS/NASC params from the TOML if present
    toml_path = Path(args.denoise_config)
    if toml_path.exists():
        with open(toml_path, "rb") as f:
            raw_toml = tomllib.load(f)
        mvbs_section = raw_toml.get("echodata", {}).get("mvbs", {})
        nasc_section = raw_toml.get("echodata", {}).get("nasc", {})
        if mvbs_section:
            cfg.mvbs.range_bin = mvbs_section.get("range_bin", cfg.mvbs.range_bin)
            cfg.mvbs.ping_time_bin = mvbs_section.get("ping_time_bin", cfg.mvbs.ping_time_bin)
        if nasc_section:
            cfg.nasc.range_bin = nasc_section.get("range_bin", cfg.nasc.range_bin)
            cfg.nasc.dist_bin = nasc_section.get("dist_bin", cfg.nasc.dist_bin)

    # ── Banner ───────────────────────────────────────────────────
    logger.info("=" * 70)
    logger.info("Saildrone TPOS 2023 — Single-Day Pipeline")
    logger.info("=" * 70)
    logger.info("Day:            %s", day)
    logger.info("Cache dir:      %s", cache_dir)
    logger.info("Output dir:     %s", output_dir)
    logger.info("Denoise config: %s", args.denoise_config)
    logger.info("Force reconvert: %s", args.force_reconvert)
    logger.info("Cruise:         %s", cfg.cruise_id)
    logger.info("Dask:           %d workers, %s each", args.n_workers, args.memory_limit)
    logger.info("Dask address:   %s", dask_address or "LocalCluster")
    logger.info("Dask dashboard: %s", dask_client.dashboard_link)
    logger.info("Category:       %s", args.category or "all")
    logger.info("GEBCO:          %s", os.environ.get("GEBCO_GRID_PATH", "(not set)"))
    logger.info("=" * 70)

    pipeline_t0 = time.perf_counter()

    # ── Stage 1: Discover files ──────────────────────────────────
    if args.skip_sv:
        # When skipping Sv computation, we don't need raw file discovery
        # (we'll use existing zarrs from the output dir)
        logger.info("Stage 1: Discovering raw files — SKIPPED (--skip-sv)")
        files_list = []
    else:
        logger.info("Stage 1: Discovering raw files for %s", day)
        files_list = discover_day_files(day, cfg)
        if not files_list:
            logger.error("No raw files found for %s — exiting", day)
            shutdown_dask(dask_client)
            sys.exit(1)

    # ── Dry-run exit point ───────────────────────────────────────
    if args.dry_run:
        logger.info("─" * 70)
        logger.info("DRY RUN — would process %d raw files:", len(files_list))
        for i, (fname, rec) in enumerate(files_list[:20], 1):
            size_mb = rec.get("file_size", 0) / 1_048_576
            logger.info("  %3d. %s (%.1f MB)", i, fname, size_mb)
        if len(files_list) > 20:
            logger.info("  ... and %d more", len(files_list) - 20)
        total_mb = sum(r.get("file_size", 0) for _, r in files_list) / 1_048_576
        logger.info("Total size: %.1f MB", total_mb)
        logger.info("─" * 70)
        logger.info("Stages that would run:")
        logger.info("  2. EchoData cache (download + convert + combine)")
        logger.info("  3. Compute Sv: %s", "SKIP" if args.skip_sv else "yes")
        logger.info("  4. Denoise: %s (%s)%s",
                    "SKIP" if args.skip_denoise else "yes",
                    ", ".join(denoise_config.methods) if not args.skip_denoise else "—",
                    " + save masks" if args.save_masks else "")
        logger.info("  5. Seabed mask: %s", "SKIP" if args.skip_seabed_mask else "yes")
        logger.info("  6. MVBS: %s (range=%s, ping_time=%s)",
                    "SKIP" if args.skip_mvbs else "yes",
                    cfg.mvbs.range_bin, cfg.mvbs.ping_time_bin)
        logger.info("  7. NASC: %s (range=%s, dist=%s)",
                    "SKIP" if args.skip_nasc else "yes",
                    cfg.nasc.range_bin, cfg.nasc.dist_bin)
        logger.info("  8. Echograms: %s", "SKIP" if args.skip_echograms else "yes")
        logger.info("─" * 70)

        # Check cache status
        cache_day_dir = cache_dir / day
        manifest = _read_manifest(cache_day_dir)
        if manifest:
            logger.info("Cache exists: %d files, echopype %s, built %s",
                        manifest.get("file_count", 0),
                        manifest.get("echopype_version", "?"),
                        manifest.get("timestamp", "?"))
            logger.info("Categories: %s", sorted(set(manifest.get("category_map", {}).values())))
        else:
            logger.info("Cache: EMPTY (will download + convert)")

        logger.info("─" * 70)
        logger.info("DRY RUN complete — no data processed")
        shutdown_dask(dask_client)
        return

    # ── When --skip-sv, also skip Stage 2 (EchoData cache) since we
    #    only need the existing Sv zarr from the output dir. ───────
    if args.skip_sv:
        # Detect categories from existing Sv zarrs in output dir
        sv_paths: dict[str, str] = {}
        day_output = output_dir / day
        if args.category:
            candidates = [args.category]
        else:
            # Discover from files matching pattern: day--*.zarr
            candidates = []
            if day_output.exists():
                for p in day_output.iterdir():
                    if p.is_dir() and p.name.startswith(f"{day}--") and p.name.endswith(".zarr"):
                        cat = p.name.replace(f"{day}--", "").replace(".zarr", "")
                        if "--" not in cat:  # skip denoised/mvbs/nasc zarrs
                            candidates.append(cat)
            if not candidates:
                logger.error("--skip-sv: no Sv zarrs found in %s", day_output)
                sys.exit(1)

        for cat in candidates:
            sv_zarr_rel = f"{day}/{day}--{cat}.zarr"
            sv_zarr_abs = output_dir / sv_zarr_rel
            if sv_zarr_abs.exists():
                sv_paths[cat] = sv_zarr_rel
            else:
                logger.error("--skip-sv: Sv zarr not found: %s", sv_zarr_abs)
                sys.exit(1)
        logger.info("Stage 2: EchoData cache — SKIPPED (--skip-sv)")
        logger.info("Stage 3: Compute Sv — SKIPPED (reusing %s)", list(sv_paths.keys()))
        combined_paths = {cat: "" for cat in sv_paths}  # placeholder
        gps_df = None  # GPS only needed for compute_sv

    else:
        # ── Stage 2: EchoData cache ──────────────────────────────
        logger.info("Stage 2: Ensuring EchoData cache")
        combined_paths = ensure_echodata_cache(
            day=day,
            files_list=files_list,
            cfg=cfg,
            cache_dir=cache_dir,
            force=args.force_reconvert,
        )
        if not combined_paths:
            logger.error("No EchoData produced — exiting")
            sys.exit(1)

        # ── Cache-only exit point ─────────────────────────────────
        if args.cache_only:
            elapsed = time.perf_counter() - pipeline_t0
            logger.info("=" * 70)
            logger.info("Cache-only complete for %s in %.0fs (%.1f min)", day, elapsed, elapsed / 60)
            logger.info("Cached EchoData:")
            for cat, path in sorted(combined_paths.items()):
                logger.info("  %s: %s", cat, path)
            logger.info("=" * 70)
            shutdown_dask(dask_client)
            return

        # Filter categories if requested
        if args.category:
            combined_paths = {k: v for k, v in combined_paths.items() if k == args.category}
            if not combined_paths:
                logger.error("Category %s not found in data (available: %s)",
                             args.category, list(combined_paths.keys()))
                sys.exit(1)

        # ── GPS ──────────────────────────────────────────────────
        logger.info("Loading GPS data")
        gps_df = load_gps(cfg)

        # ── Stage 3: Compute Sv ──────────────────────────────────
        logger.info("Stage 3: Computing Sv")
        sv_paths = compute_sv(
            day=day,
            combined_paths=combined_paths,
            cfg=cfg,
            output_dir=output_dir,
            gps_df=gps_df,
            category_filter=args.category,
        )
    _release_memory()

    # ── Stage 4: Denoise ─────────────────────────────────────────
    if args.skip_denoise:
        logger.info("Stage 4: Denoising — SKIPPED")
        # Use existing denoised zarrs if available, otherwise fall back to source Sv
        denoised_paths = {}
        for cat, sv_zarr in sv_paths.items():
            denoised_zarr = sv_zarr.replace(".zarr", "--denoised.zarr")
            if (output_dir / denoised_zarr).exists():
                denoised_paths[cat] = denoised_zarr
            else:
                denoised_paths[cat] = sv_zarr
    else:
        logger.info("Stage 4: Denoising")
        denoised_paths = denoise(
            day=day,
            sv_paths=sv_paths,
            denoise_config=denoise_config,
            cfg=cfg,
            output_dir=output_dir,
            save_masks=args.save_masks,
            surface_exclude=args.surface_exclude,
        )
    _release_memory()

    # ── Stage 5: Seabed mask ─────────────────────────────────────
    if args.skip_seabed_mask:
        logger.info("Stage 5: Seabed masking — SKIPPED")
        masked_paths = denoised_paths
    else:
        logger.info("Stage 5: Seabed masking")
        masked_paths = seabed_mask(
            day=day,
            denoised_paths=denoised_paths,
            cfg=cfg,
            output_dir=output_dir,
        )
    _release_memory()

    # ── Stage 6: MVBS ────────────────────────────────────────────
    if args.skip_mvbs:
        logger.info("Stage 6: MVBS — SKIPPED")
        mvbs_paths = {}
    else:
        logger.info("Stage 6: Computing MVBS")
        mvbs_paths = compute_mvbs(
            day=day,
            input_paths=masked_paths,
            cfg=cfg,
            output_dir=output_dir,
            save_netcdf=args.save_netcdf,
        )
    _release_memory()

    # ── Stage 7: NASC ────────────────────────────────────────────
    if args.skip_nasc:
        logger.info("Stage 7: NASC — SKIPPED")
        nasc_paths = {}
    else:
        logger.info("Stage 7: Computing NASC")
        nasc_paths = compute_nasc(
            day=day,
            input_paths=masked_paths,
            cfg=cfg,
            output_dir=output_dir,
            save_netcdf=args.save_netcdf,
        )
    _release_memory()

    # ── Stage 8: Echograms ───────────────────────────────────────
    if args.skip_echograms:
        logger.info("Stage 8: Echograms — SKIPPED")
    else:
        logger.info("Stage 8: Generating echograms")
        generate_echograms(
            day=day,
            sv_paths=sv_paths,
            denoised_paths=denoised_paths,
            mvbs_paths=mvbs_paths,
            output_dir=output_dir,
            surface_exclude=args.surface_exclude,
            prune_empty_pings=args.prune_empty_pings,
        )

    # ── Stage 9: AI Echogram Analysis ────────────────────────────
    if not args.ai_analysis:
        logger.info("Stage 9: AI Echogram Analysis — SKIPPED (use --ai-analysis to enable)")
    elif args.skip_echograms:
        logger.info("Stage 9: AI Echogram Analysis — SKIPPED (no echograms generated)")
    else:
        logger.info("Stage 9: AI echogram analysis (Azure OpenAI)")
        run_ai_echogram_analysis(
            day=day,
            output_dir=output_dir,
            denoised_paths=denoised_paths,
            model=args.ai_model,
        )
    _release_memory()

    # ── Summary ──────────────────────────────────────────────────
    elapsed = time.perf_counter() - pipeline_t0
    logger.info("=" * 70)
    logger.info("Pipeline complete for %s in %.0fs (%.1f min)", day, elapsed, elapsed / 60)
    logger.info("=" * 70)

    logger.info("Outputs:")
    for cat in sorted(sv_paths):
        logger.info("  %s/", cat)
        if cat in sv_paths:
            logger.info("    Sv:       %s", sv_paths[cat])
        if cat in denoised_paths:
            logger.info("    Denoised: %s", denoised_paths[cat])
        if cat in masked_paths and masked_paths[cat] != denoised_paths.get(cat):
            logger.info("    Masked:   %s", masked_paths[cat])
        if cat in mvbs_paths:
            logger.info("    MVBS:     %s", mvbs_paths[cat])
        if cat in nasc_paths:
            logger.info("    NASC:     %s", nasc_paths[cat])

    echogram_dir = output_dir / day / "echograms"
    if echogram_dir.exists():
        pngs = list(echogram_dir.glob("*.png"))
        logger.info("  Echograms:  %d PNG files in %s", len(pngs), echogram_dir)

    # ── Shutdown Dask ────────────────────────────────────────────
    shutdown_dask(dask_client)


if __name__ == "__main__":
    main()
