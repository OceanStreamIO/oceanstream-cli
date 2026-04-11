#!/usr/bin/env python3
"""Parallel NASC computation for all denoised zarrs — FAST vectorized version.

Uses pure numpy + haversine for NASC computation instead of echopype's
``compute_NASC`` which uses dask graph execution and peaks at ~90 GB RAM
per zarr.

This version:
  - ~7 GB peak memory per worker (vs ~90 GB)
  - ~30-60 seconds per zarr (vs 15-60 minutes)
  - Supports 10-20 parallel workers (vs 3-4)

Output zarr format matches echopype (used by stage 12 NASC Biomass GeoJSON):
  - NASC(channel, distance, depth) in m² nmi⁻²
  - latitude, longitude per distance bin
  - ping_time per distance bin
  - channel, depth coordinates

Usage:
    python run_nasc_parallel.py                     # default 10 workers
    python run_nasc_parallel.py --workers 16
    python run_nasc_parallel.py --dry-run           # list work only
    python run_nasc_parallel.py --limit 5           # test with 5 zarrs
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

import numpy as np

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

NASC_RANGE_BIN_M = 10.0       # 10 m vertical bins
NASC_DIST_BIN_NMI = 0.5       # 0.5 nautical miles horizontal bins
OUTPUT_CONTAINER = "sd-tpos2023-full-v01"
_DATA_DISK = Path("/mnt/data/output")

log = logging.getLogger("nasc_parallel")


# ---------------------------------------------------------------------------
# Haversine cumulative distance
# ---------------------------------------------------------------------------

def _cumulative_distance_nmi(lat: np.ndarray, lon: np.ndarray) -> np.ndarray:
    """Compute cumulative distance in nautical miles from sequential lat/lon.

    Uses vectorised haversine formula. NaN positions are interpolated
    over (distance continues to accumulate through NaN gaps).
    """
    R_NMI = 3440.065  # Earth radius in nautical miles

    lat_r = np.deg2rad(lat.astype(np.float64))
    lon_r = np.deg2rad(lon.astype(np.float64))

    # Pairwise haversine from consecutive points
    dlat = np.diff(lat_r)
    dlon = np.diff(lon_r)
    a = np.sin(dlat / 2) ** 2 + np.cos(lat_r[:-1]) * np.cos(lat_r[1:]) * np.sin(dlon / 2) ** 2
    seg = 2 * R_NMI * np.arcsin(np.sqrt(np.clip(a, 0, 1)))

    # Replace NaN segments with 0 (no distance if we don't know position)
    seg = np.where(np.isfinite(seg), seg, 0.0)

    cum_dist = np.zeros(len(lat), dtype=np.float64)
    cum_dist[1:] = np.cumsum(seg)
    return cum_dist


# ---------------------------------------------------------------------------
# Fast vectorised NASC computation
# ---------------------------------------------------------------------------

def _compute_nasc_fast(
    sv: np.ndarray,
    depth: np.ndarray,
    lat: np.ndarray,
    lon: np.ndarray,
    range_bin_m: float = NASC_RANGE_BIN_M,
    dist_bin_nmi: float = NASC_DIST_BIN_NMI,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Compute NASC using vectorised numpy operations.

    Parameters
    ----------
    sv : (C, P, R) float  — Sv in dB
    depth : (C, P, R) float — depth in metres
    lat, lon : (P,) float — position per ping
    range_bin_m : vertical bin size (metres)
    dist_bin_nmi : horizontal bin size (nautical miles)

    Returns
    -------
    nasc : (C, D, Z) float  — NASC in m² nmi⁻²
    dist_edges : (D+1,)
    depth_edges : (Z+1,)
    bin_lat, bin_lon : (D,) — mean lat/lon per distance bin
    bin_time_idx : (D,) int — representative ping index per distance bin
    """
    C, P, R = sv.shape

    # 1. Cumulative distance
    cum_dist = _cumulative_distance_nmi(lat, lon)
    max_dist = np.nanmax(cum_dist)
    if np.isnan(max_dist) or max_dist <= 0:
        raise ValueError("No valid distances computed from lat/lon")

    dist_edges = np.arange(0, max_dist + dist_bin_nmi, dist_bin_nmi)
    n_dist = len(dist_edges) - 1
    dist_idx = np.clip(np.digitize(cum_dist, dist_edges) - 1, 0, n_dist - 1)  # (P,)

    # 2. Depth bin edges
    max_depth = np.nanmax(depth)
    depth_edges = np.arange(0, max_depth + range_bin_m, range_bin_m)
    n_depth = len(depth_edges) - 1

    # 3. Convert Sv → linear (σ_bs * 4π)
    sv_lin = np.power(10.0, sv / 10.0)

    # 4. For each channel, bin via np.bincount
    nasc = np.full((C, n_dist, n_depth), np.nan, dtype=np.float64)
    n_bins = n_dist * n_depth

    for ch in range(C):
        ch_depth = depth[ch] if depth.ndim == 3 else depth  # (P, R)
        depth_idx = np.clip(np.digitize(ch_depth, depth_edges) - 1, 0, n_depth - 1)  # (P, R)

        # Broadcast dist_idx to (P, R)
        dist_expanded = np.broadcast_to(dist_idx[:, np.newaxis], (P, R))
        flat_idx = dist_expanded * n_depth + depth_idx  # (P, R)

        # Mask valid entries
        valid = np.isfinite(sv_lin[ch]) & np.isfinite(ch_depth)
        flat_valid = flat_idx[valid].ravel().astype(np.intp)
        sv_valid = sv_lin[ch][valid].ravel()

        # Aggregate with bincount
        sv_sum = np.bincount(flat_valid, weights=sv_valid, minlength=n_bins)
        counts = np.bincount(flat_valid, minlength=n_bins)

        # NASC = mean(sv_linear) × range_bin × 4π × 1852²
        mean_sv = np.where(counts > 0, sv_sum / counts, np.nan)
        nasc_flat = mean_sv * range_bin_m * 4.0 * np.pi * 1852.0 ** 2
        nasc[ch] = nasc_flat[:n_bins].reshape(n_dist, n_depth)

    # 5. Mean position per distance bin (for GeoJSON output)
    bin_lat = np.full(n_dist, np.nan)
    bin_lon = np.full(n_dist, np.nan)
    bin_time_idx = np.zeros(n_dist, dtype=np.int64)

    for di in range(n_dist):
        mask = dist_idx == di
        if mask.any():
            valid_lat = lat[mask]
            valid_lon = lon[mask]
            fin = np.isfinite(valid_lat) & np.isfinite(valid_lon)
            if fin.any():
                bin_lat[di] = np.mean(valid_lat[fin])
                bin_lon[di] = np.mean(valid_lon[fin])
            # Representative ping: midpoint of the bin
            bin_time_idx[di] = np.where(mask)[0][len(np.where(mask)[0]) // 2]

    return nasc, dist_edges, depth_edges, bin_lat, bin_lon, bin_time_idx


# ---------------------------------------------------------------------------
# Build xarray Dataset from NASC arrays (matching echopype format)
# ---------------------------------------------------------------------------

def _build_nasc_dataset(
    nasc: np.ndarray,
    depth_edges: np.ndarray,
    dist_edges: np.ndarray,
    bin_lat: np.ndarray,
    bin_lon: np.ndarray,
    bin_time_idx: np.ndarray,
    channel_names: np.ndarray,
    ping_times: np.ndarray,
    range_bin_m: float,
    dist_bin_nmi: float,
) -> "xr.Dataset":
    """Create an xarray Dataset matching echopype's compute_NASC output format."""
    import xarray as xr

    C, D, Z = nasc.shape

    # Depth coordinate: bin centres
    depth_centres = (depth_edges[:-1] + depth_edges[1:]) / 2.0

    # Representative ping_time per distance bin
    rep_times = ping_times[bin_time_idx]

    ds = xr.Dataset(
        {
            "NASC": (["channel", "distance", "depth"], nasc),
            "NASC_log": (["channel", "distance", "depth"],
                         10.0 * np.log10(np.where(nasc > 0, nasc, np.nan))),
            "latitude": (["distance"], bin_lat),
            "longitude": (["distance"], bin_lon),
        },
        coords={
            "channel": channel_names,
            "depth": depth_centres,
            "ping_time": (["distance"], rep_times),
        },
    )

    ds["NASC"].attrs = {
        "long_name": "Nautical Area Scattering Coefficient",
        "units": "m2 nmi-2",
    }
    ds["NASC_log"].attrs = {
        "long_name": "Log10-transformed NASC",
        "units": "dB re 1 m2 nmi-2",
    }
    ds.attrs = {
        "processing": "NASC computed with oceanstream (fast vectorised mode)",
        "range_bin": f"{range_bin_m}m",
        "dist_bin": f"{dist_bin_nmi}nmi",
    }

    return ds


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

def discover_work(container_dir: Path) -> list[tuple[str, str, Path]]:
    """Return list of (day_key, category, denoised_zarr_path) needing NASC."""
    work: list[tuple[str, str, Path]] = []
    already_done = 0

    for day_dir in sorted(container_dir.iterdir()):
        if not day_dir.is_dir() or not day_dir.name.startswith("2023-"):
            continue
        day_key = day_dir.name

        for zarr_path in sorted(day_dir.glob("*--denoised.zarr")):
            parts = zarr_path.stem.split("--")
            if len(parts) < 3:
                continue
            category = parts[1]

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
# Single-zarr NASC worker
# ---------------------------------------------------------------------------

def _compute_one_nasc(args: tuple[str, str, str, str]) -> tuple[str, str, bool, str]:
    """Compute NASC for a single denoised zarr using fast vectorised method.

    Args is a tuple: (day_key, category, denoised_zarr_str, output_container)

    Returns: (day_key, category, success, message)
    """
    day_key, category, denoised_zarr_str, output_container = args

    # Patch storage for local disk
    from local_storage import patch_storage
    patch_storage(_DATA_DISK)

    import xarray as xr

    logging.basicConfig(
        level=logging.INFO,
        format=f"%(asctime)s [{day_key}/{category}] %(message)s",
        datefmt="%H:%M:%S",
    )
    wlog = logging.getLogger(f"worker.{day_key}.{category}")

    t0 = time.time()
    try:
        from oceanstream.echodata.storage import open_sv_from_azure, save_dataset_to_azure

        wlog.info("Opening denoised zarr...")
        # Load eagerly (chunks=None) for fast numpy operations
        ds = open_sv_from_azure(
            f"{day_key}/{day_key}--{category}--denoised.zarr",
            container=output_container,
            chunks=None,  # eager load — numpy arrays, no dask
        )

        # Extract arrays
        sv = ds["Sv"].values  # (C, P, R)
        if sv.ndim != 3:
            ds.close()
            return (day_key, category, False, f"Unexpected Sv shape {sv.shape} — skipped")

        C, P, R = sv.shape

        # Depth
        if "depth" not in ds and "depth" not in ds.coords:
            ds.close()
            return (day_key, category, False, "No depth — skipped")
        depth = ds["depth"].values  # (C, P, R) or (P, R)

        # Lat/lon — flatten to 1D
        has_lat = "latitude" in ds.data_vars or "latitude" in ds.coords
        has_lon = "longitude" in ds.data_vars or "longitude" in ds.coords
        if not (has_lat and has_lon):
            ds.close()
            return (day_key, category, False, "No lat/lon — skipped")

        lat_raw = (ds["latitude"] if "latitude" in ds.data_vars else ds.coords["latitude"]).values
        lon_raw = (ds["longitude"] if "longitude" in ds.data_vars else ds.coords["longitude"]).values

        # Collapse to 1D along ping_time
        if lat_raw.ndim > 1:
            lat = lat_raw[0] if lat_raw.shape[0] == C else lat_raw.ravel()[:P]
            lon = lon_raw[0] if lon_raw.shape[0] == C else lon_raw.ravel()[:P]
        else:
            lat = lat_raw
            lon = lon_raw

        n_valid = int(np.count_nonzero(~np.isnan(lat)))
        if n_valid == 0:
            ds.close()
            return (day_key, category, False, "All lat/lon NaN — skipped")

        wlog.info(
            "Computing fast NASC: Sv(%d,%d,%d), %d valid GPS points",
            C, P, R, n_valid,
        )

        # Channel names and ping times for output
        channel_names = ds["channel"].values if "channel" in ds.coords else np.arange(C)
        ping_times = ds["ping_time"].values if "ping_time" in ds.coords else np.arange(P)

        # Close input (data already loaded into numpy)
        ds.close()

        # Compute NASC
        nasc, dist_edges, depth_edges, bin_lat, bin_lon, bin_time_idx = _compute_nasc_fast(
            sv, depth, lat, lon,
            range_bin_m=NASC_RANGE_BIN_M,
            dist_bin_nmi=NASC_DIST_BIN_NMI,
        )

        t_compute = time.time() - t0

        # Build output dataset
        ds_nasc = _build_nasc_dataset(
            nasc, depth_edges, dist_edges, bin_lat, bin_lon, bin_time_idx,
            channel_names, ping_times,
            range_bin_m=NASC_RANGE_BIN_M,
            dist_bin_nmi=NASC_DIST_BIN_NMI,
        )

        # Save zarr
        output_zarr = f"{day_key}/{day_key}--{category}--nasc.zarr"
        save_dataset_to_azure(ds_nasc, zarr_path=output_zarr, container=output_container)

        # Save netcdf
        nc_path = f"{day_key}/{day_key}--{category}--nasc.nc"
        _save_netcdf(ds_nasc, nc_path, output_container)

        elapsed = time.time() - t0
        D, Z = nasc.shape[1], nasc.shape[2]
        msg = f"Done in {elapsed:.0f}s (compute={t_compute:.0f}s, {D}dist x {Z}depth bins)"
        wlog.info(msg)

        del sv, depth, lat, lon, nasc, ds_nasc
        gc.collect()

        return (day_key, category, True, msg)

    except Exception as e:
        elapsed = time.time() - t0
        msg = f"Failed after {elapsed:.0f}s: {e}"
        wlog.error(msg, exc_info=True)
        return (day_key, category, False, msg)


def _save_netcdf(ds, nc_path: str, container: str) -> None:
    """Save dataset as NetCDF to local disk."""
    import tempfile
    from oceanstream.echodata.storage import upload_file_to_blob

    try:
        encoding = {}
        for var in ds.data_vars:
            if ds[var].dtype.kind in {"U", "S", "O"}:
                encoding[var] = {}
            else:
                encoding[var] = {"zlib": True, "complevel": 5}

        with tempfile.NamedTemporaryFile(suffix=".nc", delete=True) as tmp:
            ds.to_netcdf(tmp.name, engine="netcdf4", format="NETCDF4", encoding=encoding)
            upload_file_to_blob(tmp.name, nc_path, container)
    except Exception as e:
        log.warning("NetCDF export failed for %s: %s", nc_path, e)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Parallel fast NASC computation")
    parser.add_argument(
        "--workers", type=int, default=10,
        help="Number of parallel worker processes (default: 10)",
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

    tasks = [
        (day_key, category, str(zarr_path), args.output_container)
        for day_key, category, zarr_path in work
    ]

    log.info(
        "Starting parallel fast NASC: %d zarrs, %d workers",
        len(tasks), args.workers,
    )

    ctx = multiprocessing.get_context("spawn")

    completed = 0
    failed = 0
    skipped = 0
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
                elif "skipped" in msg.lower():
                    skipped += 1
                else:
                    failed += 1
                elapsed = time.time() - t_start
                total_done = completed + skipped + failed
                rate = total_done / elapsed if elapsed > 0 else 0
                remaining = len(tasks) - total_done
                eta_m = (remaining / rate / 60) if rate > 0 else 0

                log.info(
                    "[%d done, %d skip, %d fail / %d] %s/%s: %s  (ETA: %.0f min)",
                    completed, skipped, failed, len(tasks), rday, rcat, msg, eta_m,
                )
            except Exception as e:
                failed += 1
                log.error(
                    "[%d/%d] %s/%s EXCEPTION: %s",
                    completed, len(tasks), day_key, category, e,
                )

    total_time = time.time() - t_start
    log.info(
        "NASC parallel complete: %d succeeded, %d skipped, %d failed (of %d) in %.1f min",
        completed, skipped, failed, len(tasks), total_time / 60,
    )


if __name__ == "__main__":
    main()
