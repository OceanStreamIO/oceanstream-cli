#!/usr/bin/env python3
"""Parallel NASC computation for all denoised zarrs.

Discovers denoised zarrs on local disk, skips those that already have
NASC results, and computes the remaining in parallel using
ProcessPoolExecutor.

Key optimisations vs build_full_survey.py stage-7 NASC:
  - Removes ``scheduler="synchronous"`` — lets dask use threaded scheduler
    so each worker exploits multiple CPU cores internally.
  - Processes multiple zarrs simultaneously via ProcessPoolExecutor.
  - Progress logging with ETA.

Usage:
    python run_nasc_parallel.py                     # default 12 workers
    python run_nasc_parallel.py --workers 8         # 8 workers
    python run_nasc_parallel.py --dry-run           # list work only
    python run_nasc_parallel.py --workers 16 --threads-per-worker 3
"""

from __future__ import annotations

import argparse
import gc
import logging
import multiprocessing
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

NASC_RANGE_BIN = "10m"
NASC_DIST_BIN = "0.5nmi"
OUTPUT_CONTAINER = "sd-tpos2023-full-v01"
CHUNKS = {"ping_time": 1000, "range_sample": -1}
_DATA_DISK = Path("/mnt/data/output")

log = logging.getLogger("nasc_parallel")


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

def discover_work(container_dir: Path) -> list[tuple[str, str, Path]]:
    """Return list of (day_key, category, denoised_zarr_path) needing NASC.

    Skips zarrs that already have a corresponding NASC zarr.
    """
    work: list[tuple[str, str, Path]] = []
    already_done = 0

    for day_dir in sorted(container_dir.iterdir()):
        if not day_dir.is_dir() or not day_dir.name.startswith("2023-"):
            continue
        day_key = day_dir.name

        for zarr_path in sorted(day_dir.glob("*--denoised.zarr")):
            # Parse category from filename: 2023-06-22--short_pulse--denoised.zarr
            parts = zarr_path.stem.split("--")
            if len(parts) < 3:
                continue
            category = parts[1]  # short_pulse or long_pulse

            # Check if NASC already computed
            nasc_zarr = day_dir / f"{day_key}--{category}--nasc.zarr"
            if nasc_zarr.exists():
                already_done += 1
                continue

            work.append((day_key, category, zarr_path))

    log.info(
        "Discovered %d denoised zarrs needing NASC (%d already done)",
        len(work), already_done,
    )
    return work


# ---------------------------------------------------------------------------
# Single-zarr NASC computation (runs in worker process)
# ---------------------------------------------------------------------------

def _compute_one_nasc(args: tuple[str, str, str, str, int]) -> tuple[str, str, bool, str]:
    """Compute NASC for a single denoised zarr.

    Args is a tuple: (day_key, category, denoised_zarr_str, output_container, threads)

    Returns: (day_key, category, success, message)
    """
    day_key, category, denoised_zarr_str, output_container, threads_per_worker = args

    # Configure dask to use limited threads within this worker
    import dask
    dask.config.set(num_workers=threads_per_worker)

    # Patch storage for local disk
    from local_storage import patch_storage
    patch_storage(_DATA_DISK)

    import numpy as np
    import xarray as xr

    logging.basicConfig(
        level=logging.INFO,
        format=f"%(asctime)s [{day_key}/{category}] %(message)s",
        datefmt="%H:%M:%S",
    )
    wlog = logging.getLogger(f"worker.{day_key}.{category}")

    t0 = time.time()
    try:
        from oceanstream.echodata.compute import compute_nasc
        from oceanstream.echodata.storage import open_sv_from_azure, save_dataset_to_azure

        wlog.info("Opening denoised zarr...")
        ds = open_sv_from_azure(
            f"{day_key}/{day_key}--{category}--denoised.zarr",
            container=output_container,
            chunks=CHUNKS,
        )

        # Verify required variables
        has_depth = "depth" in ds or "depth" in ds.coords
        has_lat = "latitude" in ds.data_vars or "latitude" in ds.coords
        has_lon = "longitude" in ds.data_vars or "longitude" in ds.coords

        if not has_depth:
            ds.close()
            return (day_key, category, False, "No depth variable")
        if not (has_lat and has_lon):
            ds.close()
            return (day_key, category, False, "No lat/lon variables")

        wlog.info("Computing NASC (range_bin=%s, dist_bin=%s)...", NASC_RANGE_BIN, NASC_DIST_BIN)

        # Use default dask scheduler (threaded) — NOT synchronous!
        ds_nasc = compute_nasc(ds, range_bin=NASC_RANGE_BIN, dist_bin=NASC_DIST_BIN)

        # Save zarr
        output_zarr = f"{day_key}/{day_key}--{category}--nasc.zarr"
        save_dataset_to_azure(ds_nasc, zarr_path=output_zarr, container=output_container)

        # Save netcdf
        nc_path = f"{day_key}/{day_key}--{category}--nasc.nc"
        _save_netcdf(ds_nasc, nc_path, output_container)

        elapsed = time.time() - t0
        msg = f"Done in {elapsed:.0f}s"
        wlog.info(msg)

        ds.close()
        ds_nasc.close()
        del ds, ds_nasc
        gc.collect()

        return (day_key, category, True, msg)

    except Exception as e:
        elapsed = time.time() - t0
        msg = f"Failed after {elapsed:.0f}s: {e}"
        wlog.error(msg)
        return (day_key, category, False, msg)


def _save_netcdf(ds, nc_path: str, container: str) -> None:
    """Save dataset as NetCDF to local disk."""
    import numpy as np
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
    except Exception as e:
        log.warning("NetCDF export failed for %s: %s", nc_path, e)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Parallel NASC computation")
    parser.add_argument(
        "--workers", type=int, default=12,
        help="Number of parallel worker processes (default: 12)",
    )
    parser.add_argument(
        "--threads-per-worker", type=int, default=4,
        help="Dask threads per worker process (default: 4)",
    )
    parser.add_argument(
        "--output-container", default=OUTPUT_CONTAINER,
        help=f"Output container name (default: {OUTPUT_CONTAINER})",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="List work items without computing",
    )
    parser.add_argument(
        "--limit", type=int, default=0,
        help="Process at most N zarrs (0 = all)",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s  %(message)s",
        datefmt="%H:%M:%S",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(_DATA_DISK / "nasc-parallel.log"),
        ],
    )

    # Patch storage in main process too (for discovery)
    sys.path.insert(0, str(Path(__file__).parent))
    from local_storage import patch_storage
    patch_storage(_DATA_DISK)

    container_dir = _DATA_DISK / args.output_container
    if not container_dir.exists():
        log.error("Container dir not found: %s", container_dir)
        sys.exit(1)

    work = discover_work(container_dir)
    if not work:
        log.info("Nothing to compute — all NASC zarrs present!")
        return

    if args.limit > 0:
        work = work[:args.limit]
        log.info("Limited to %d items", args.limit)

    if args.dry_run:
        log.info("Dry run — %d items:", len(work))
        for day_key, category, path in work:
            log.info("  %s / %s  (%s)", day_key, category, path.name)
        return

    # Build task args
    tasks = [
        (day_key, category, str(zarr_path), args.output_container, args.threads_per_worker)
        for day_key, category, zarr_path in work
    ]

    log.info(
        "Starting parallel NASC: %d zarrs, %d workers, %d threads/worker",
        len(tasks), args.workers, args.threads_per_worker,
    )

    # Use spawn context to avoid fork + dask conflicts
    ctx = multiprocessing.get_context("spawn")

    completed = 0
    failed = 0
    t_start = time.time()

    with ProcessPoolExecutor(max_workers=args.workers, mp_context=ctx) as executor:
        futures = {
            executor.submit(_compute_one_nasc, task): (task[0], task[1])
            for task in tasks
        }

        for future in as_completed(futures):
            day_key, category = futures[future]
            try:
                rday, rcat, success, msg = future.result()
                if success:
                    completed += 1
                else:
                    failed += 1
                elapsed = time.time() - t_start
                rate = completed / elapsed if elapsed > 0 else 0
                remaining = len(tasks) - completed - failed
                eta_s = remaining / rate if rate > 0 else 0
                eta_m = eta_s / 60

                log.info(
                    "[%d/%d done, %d failed] %s/%s: %s  (ETA: %.0f min)",
                    completed, len(tasks), failed, rday, rcat, msg, eta_m,
                )
            except Exception as e:
                failed += 1
                log.error("[%d/%d] %s/%s EXCEPTION: %s", completed, len(tasks), day_key, category, e)

    total_time = time.time() - t_start
    log.info(
        "NASC parallel complete: %d/%d succeeded, %d failed in %.1f min",
        completed, len(tasks), failed, total_time / 60,
    )


if __name__ == "__main__":
    main()
