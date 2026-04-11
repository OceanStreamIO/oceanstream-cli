#!/usr/bin/env python3
"""Combine short_pulse + long_pulse products into single per-day zarrs
and generate per-day echograms with pulse-mode markings.

Input:  {day}/{day}--{pulse_mode}--{product}.zarr  (separate per pulse mode)
Output: {day}/{day}--combined--{product}.zarr       (merged per day)
        perday_echograms/{day}--{product}--{freq}--{cmap}.png

Products handled:
  - MVBS: concat along ping_time (depth already aligned at 1m bins)
  - NASC: concat along ping_time (distance bins vary, xarray auto-aligns)
  - raw Sv: interpolate to 0.5m common depth grid, concat along ping_time
  - denoised: same as raw Sv

Each combined dataset gets a pulse_mode variable (0=long, 1=short) and
frequency-labelled channels (38kHz, 200kHz).

Usage:
    python run_combine_daily.py                          # all products + echograms
    python run_combine_daily.py --products mvbs nasc     # only MVBS + NASC
    python run_combine_daily.py --skip-echograms         # combine only, no PNGs
    python run_combine_daily.py --limit 5                # first 5 days (testing)
    python run_combine_daily.py --workers 8              # parallel day processing
"""
from __future__ import annotations

import argparse
import gc
import logging
import os
import re
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from multiprocessing import get_context
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

import matplotlib
matplotlib.use("Agg")
import matplotlib.colors as mcolors
import matplotlib.pyplot as plt
import numpy as np
import xarray as xr

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BASE_DIR = Path("/mnt/data/output/sd-tpos2023-full-v01")
OUTPUT_DIR = Path("/mnt/data/output")
ECHOGRAM_DIR = OUTPUT_DIR / "perday_echograms"

FREQ_38KHZ = 38000.0
FREQ_200KHZ = 200000.0

# MVBS bins (must match stage 7)
MVBS_RANGE_BIN = "1m"
MVBS_PING_TIME_BIN = "10s"

# Echogram rendering
SV_VMIN = -85.0
SV_VMAX = -50.0
MAX_PLOT_DEPTH = 800.0
TRANSDUCER_DEPTH = 2.0

# Common depth grid for raw Sv / denoised interpolation
COMMON_DEPTH_STEP = 0.5  # metres
COMMON_DEPTH_MAX = 1305.0  # metres (generous ceiling)
COMMON_DEPTH = np.arange(0, COMMON_DEPTH_MAX, COMMON_DEPTH_STEP)

# Products and their file patterns
PRODUCT_PATTERNS = {
    "sv":       (""          , "Sv"),   # raw Sv: {day}--{mode}.zarr
    "denoised": ("--denoised", "Sv"),   # {day}--{mode}--denoised.zarr
    "mvbs":     ("--mvbs"    , "Sv"),   # MVBS stored as Sv variable
    "nasc":     ("--nasc"    , "NASC"),
}

# EK500 colormap
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
    ("EK500", EK500_CMAP),
]

log = logging.getLogger("combine_daily")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _release_memory() -> None:
    gc.collect()


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
                    str_vals = np.array(
                        [str(v) for v in vals.flat], dtype="U"
                    ).reshape(vals.shape)
                if name in ds.coords:
                    ds = ds.assign_coords({name: (arr.dims, str_vals)})
                else:
                    ds[name] = xr.DataArray(str_vals, dims=arr.dims)
    return ds


def _get_freq_channel_map(ds: xr.Dataset) -> dict[float, int]:
    """Map frequency_nominal → channel index."""
    if "frequency_nominal" not in ds.coords and "frequency_nominal" not in ds.data_vars:
        return {}
    freqs = ds["frequency_nominal"].values
    return {float(f): i for i, f in enumerate(freqs)}


def _clear_encoding(ds: xr.Dataset) -> xr.Dataset:
    """Clear encoding on all variables/coords to avoid concat conflicts."""
    for var in list(ds.data_vars) + list(ds.coords):
        if var in ds:
            ds[var].encoding.clear()
    return ds


# ---------------------------------------------------------------------------
# Core: combine pulse modes for one day + one product
# ---------------------------------------------------------------------------

def combine_mvbs_or_nasc(
    day: str,
    product: str,
    suffix: str,
    data_var: str,
) -> xr.Dataset | None:
    """Combine MVBS or NASC zarrs (depth/distance already gridded)."""
    day_dir = BASE_DIR / day
    datasets: list[xr.Dataset] = []

    for mode in ["short_pulse", "long_pulse"]:
        zarr_path = day_dir / f"{day}--{mode}{suffix}.zarr"
        if not zarr_path.is_dir():
            continue
        try:
            ds = xr.open_zarr(str(zarr_path), chunks=None)
            ds = normalize_string_dtypes(ds)
            ds = _clear_encoding(ds)

            if data_var not in ds:
                log.warning("  %s/%s: no %s variable", day, mode, data_var)
                ds.close()
                continue

            # Rename channels to frequency labels
            if "frequency_nominal" in ds.coords or "frequency_nominal" in ds.data_vars:
                freqs = ds["frequency_nominal"].values
                new_labels = []
                for f in freqs:
                    if np.isclose(f, FREQ_38KHZ, atol=100):
                        new_labels.append("38kHz")
                    elif np.isclose(f, FREQ_200KHZ, atol=100):
                        new_labels.append("200kHz")
                    else:
                        new_labels.append(f"{f/1000:.0f}kHz")
                ds = ds.assign_coords(channel=("channel", new_labels))
            else:
                # Fallback: parse channel names
                chans = ds.channel.values.astype(str)
                new_labels = []
                for ch in chans:
                    if "ES200" in ch or "200" in ch:
                        new_labels.append("200kHz")
                    elif "ES38" in ch or "38" in ch:
                        new_labels.append("38kHz")
                    else:
                        new_labels.append(ch)
                ds = ds.assign_coords(channel=("channel", new_labels))

            # Add pulse_mode variable
            n_pings = ds.sizes.get("ping_time", 0)
            if n_pings == 0:
                ds.close()
                continue
            mode_code = 0 if mode == "long_pulse" else 1
            ds["pulse_mode"] = xr.DataArray(
                np.full(n_pings, mode_code, dtype=np.int8),
                dims=["ping_time"],
            )

            datasets.append(ds)
        except Exception as e:
            log.warning("  %s/%s: error loading %s: %s", day, mode, product, e)

    if not datasets:
        return None

    if len(datasets) == 1:
        combined = datasets[0]
    else:
        # Both pulse modes may have 38kHz channel — concat along ping_time
        # 200kHz only exists in short_pulse — xarray auto-fills with NaN
        combined = xr.concat(datasets, dim="ping_time")

    combined = combined.sortby("ping_time")
    combined.attrs["combined_pulse_modes"] = "short_pulse+long_pulse"

    for ds in datasets:
        ds.close()
    return combined


def combine_sv(
    day: str,
    suffix: str,
) -> xr.Dataset | None:
    """Combine raw Sv or denoised zarrs onto a common depth grid.

    Because short_pulse and long_pulse have different range_sample counts
    (7218 vs 3609) but cover the same physical range (~0-1300m), we
    interpolate Sv onto a common 0.5m depth grid using echo_range.
    """
    day_dir = BASE_DIR / day
    per_freq: dict[str, list[tuple[str, xr.Dataset]]] = {}  # freq_label → [(mode, ds)]

    for mode in ["short_pulse", "long_pulse"]:
        zarr_path = day_dir / f"{day}--{mode}{suffix}.zarr"
        if not zarr_path.is_dir():
            continue
        try:
            ds = xr.open_zarr(str(zarr_path), chunks=None)
            ds = normalize_string_dtypes(ds)
            ds = _clear_encoding(ds)

            if "Sv" not in ds:
                ds.close()
                continue

            freq_map = _get_freq_channel_map(ds)
            if not freq_map:
                ds.close()
                continue

            for freq_hz, ch_idx in freq_map.items():
                if np.isclose(freq_hz, FREQ_38KHZ, atol=100):
                    label = "38kHz"
                elif np.isclose(freq_hz, FREQ_200KHZ, atol=100):
                    label = "200kHz"
                else:
                    label = f"{freq_hz/1000:.0f}kHz"

                ds_ch = ds.isel(channel=[ch_idx])
                per_freq.setdefault(label, []).append((mode, ds_ch))

        except Exception as e:
            log.warning("  %s/%s: error loading Sv: %s", day, mode, e)

    if not per_freq:
        return None

    # For each frequency, interpolate to common depth and concat along ping_time
    freq_datasets: list[xr.Dataset] = []

    for freq_label in sorted(per_freq.keys()):
        mode_datasets: list[xr.Dataset] = []

        for mode, ds_ch in per_freq[freq_label]:
            sv_da = ds_ch["Sv"].isel(channel=0)  # (ping_time, range_sample)
            n_pings = sv_da.sizes["ping_time"]

            # Get physical depth per range_sample
            if "echo_range" in ds_ch:
                # echo_range may be (channel, ping_time, range_sample) or (range_sample,)
                er = ds_ch["echo_range"]
                if "channel" in er.dims:
                    er = er.isel(channel=0)
                if "ping_time" in er.dims:
                    # Use median across pings for stable depth mapping
                    er_vals = np.nanmedian(er.values, axis=0)
                else:
                    er_vals = er.values
            else:
                # No echo_range — use range_sample as-is (shouldn't happen)
                er_vals = np.arange(sv_da.sizes["range_sample"], dtype=float)

            # Interpolate each ping onto common depth grid
            sv_raw = sv_da.values  # (ping_time, range_sample)
            sv_interp = np.full((n_pings, len(COMMON_DEPTH)), np.nan, dtype=np.float32)

            valid_depth = ~np.isnan(er_vals)
            er_valid = er_vals[valid_depth]
            if len(er_valid) > 1:
                for i in range(n_pings):
                    sv_ping = sv_raw[i, valid_depth]
                    mask = ~np.isnan(sv_ping)
                    if mask.sum() > 1:
                        sv_interp[i] = np.interp(
                            COMMON_DEPTH, er_valid[mask], sv_ping[mask],
                            left=np.nan, right=np.nan,
                        )

            # Build dataset for this mode
            ping_time = sv_da.ping_time.values
            mode_code = 0 if mode == "long_pulse" else 1

            ds_new = xr.Dataset(
                {
                    "Sv": (["ping_time", "depth"], sv_interp),
                    "pulse_mode": (["ping_time"], np.full(n_pings, mode_code, dtype=np.int8)),
                },
                coords={
                    "ping_time": ping_time,
                    "depth": COMMON_DEPTH,
                    "channel": [freq_label],
                },
            )
            # Copy GPS if available
            for gps_var in ["latitude", "longitude"]:
                if gps_var in ds_ch.data_vars and "ping_time" in ds_ch[gps_var].dims:
                    ds_new[gps_var] = ds_ch[gps_var].isel(channel=0) if "channel" in ds_ch[gps_var].dims else ds_ch[gps_var]

            mode_datasets.append(ds_new)

        if not mode_datasets:
            continue

        if len(mode_datasets) == 1:
            freq_ds = mode_datasets[0]
        else:
            freq_ds = xr.concat(mode_datasets, dim="ping_time")

        freq_ds = freq_ds.sortby("ping_time")
        # Make channel a proper dimension
        freq_ds = freq_ds.expand_dims("channel")
        freq_datasets.append(freq_ds)

        for m in mode_datasets:
            m.close()
        del mode_datasets

    # Close original datasets
    for freq_modes in per_freq.values():
        for _, ds_ch in freq_modes:
            ds_ch.close()

    if not freq_datasets:
        return None

    if len(freq_datasets) == 1:
        combined = freq_datasets[0]
    else:
        combined = xr.concat(freq_datasets, dim="channel")

    combined.attrs["combined_pulse_modes"] = "short_pulse+long_pulse"
    combined.attrs["depth_grid"] = f"interpolated at {COMMON_DEPTH_STEP}m spacing"
    combined.attrs["depth_units"] = "metres"

    for ds in freq_datasets:
        ds.close()
    return combined


def combine_one_day(
    day: str,
    products: list[str],
    skip_existing: bool = True,
) -> dict[str, Path]:
    """Combine all requested products for one day. Returns {product: zarr_path}."""
    results: dict[str, Path] = {}

    for product in products:
        suffix, data_var = PRODUCT_PATTERNS[product]
        out_path = BASE_DIR / day / f"{day}--combined--{product}.zarr"

        if skip_existing and out_path.is_dir():
            log.info("  %s/%s: already exists — skip", day, product)
            results[product] = out_path
            continue

        t0 = time.time()

        if product in ("mvbs", "nasc"):
            combined = combine_mvbs_or_nasc(day, product, suffix, data_var)
        else:
            combined = combine_sv(day, suffix)

        if combined is None:
            log.info("  %s/%s: no data", day, product)
            continue

        # Save
        combined.to_zarr(str(out_path), mode="w")
        dt = time.time() - t0

        n_pings = combined.sizes.get("ping_time", 0)
        chans = list(combined.channel.values) if "channel" in combined.coords else []
        log.info(
            "  %s/%s: %d pings, channels=%s (%.1fs)",
            day, product, n_pings, chans, dt,
        )

        results[product] = out_path
        combined.close()
        del combined
        _release_memory()

    return results


# ---------------------------------------------------------------------------
# Echogram rendering
# ---------------------------------------------------------------------------

def _build_hourly_ticks(
    ping_time: np.ndarray,
    n_pings: int,
) -> tuple[list[int], list[str], list[int], list[str]]:
    """Build hourly tick positions for a single-day echogram."""
    major_ticks: list[int] = []
    major_labels: list[str] = []

    pt_hours = (
        (ping_time - ping_time[0]).astype("timedelta64[s]").astype(float) / 3600
    )

    for h in range(0, 25):
        after = pt_hours >= h
        if after.any():
            idx = int(np.argmax(after))
            if idx < n_pings:
                major_ticks.append(idx)
                # Label as HH:00
                ts = ping_time[idx]
                hh = int((ts - ts.astype("datetime64[D]")).astype("timedelta64[h]").astype(int))
                major_labels.append(f"{hh:02d}:00")

    return major_ticks, major_labels, [], []


def _draw_pulse_axis(
    ax_pulse: plt.Axes,
    pulse_mode: np.ndarray,
    n_pings: int,
) -> None:
    """Draw pulse-mode colour bar (blue=Long, orange=Short)."""
    from matplotlib.patches import Rectangle

    colors = {0: "#2196F3", 1: "#FF9800"}
    labels_map = {0: "Long pulse", 1: "Short pulse"}

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
                s + seg_width / 2, 0.5, labels_map[mode],
                ha="center", va="center", fontsize=7,
                fontweight="bold", color="white",
            )
        drawn_labels.add(mode)

    ax_pulse.set_xlim(0, n_pings)
    ax_pulse.set_ylim(0, 1)
    ax_pulse.set_yticks([])
    ax_pulse.set_ylabel("Pulse", fontsize=9, rotation=0, labelpad=30, va="center")
    ax_pulse.legend(
        loc="center left", bbox_to_anchor=(1.001, 0.5),
        fontsize=8, framealpha=0.9, handlelength=1.2,
    )


def render_echogram(
    ds: xr.Dataset,
    day: str,
    product: str,
    freq_label: str,
    cmap_name: str,
    cmap,
    output_dir: Path,
) -> Path | None:
    """Render one echogram from a combined daily dataset."""
    # Select frequency channel
    if "channel" in ds.coords:
        chans = [str(c) for c in ds.channel.values]
        if freq_label in chans:
            ch_idx = chans.index(freq_label)
            ds_freq = ds.isel(channel=ch_idx)
        else:
            return None
    else:
        ds_freq = ds

    # Get Sv (or NASC)
    data_var = "Sv" if "Sv" in ds_freq else ("NASC" if "NASC" in ds_freq else None)
    if data_var is None:
        return None

    da = ds_freq[data_var]
    if da.ndim < 2:
        return None

    ping_time = da.ping_time.values
    sv_raw = da.values

    # Determine depth axis
    if "depth" in ds_freq.coords or "depth" in ds_freq.dims:
        depth_vals = ds_freq["depth"].values
    elif "echo_range" in ds_freq.coords:
        depth_vals = ds_freq["echo_range"].values + TRANSDUCER_DEPTH
    else:
        return None

    # Handle 2D echo_range (take first ping)
    if depth_vals.ndim > 1:
        depth_vals = depth_vals[0]

    # Trim to max plot depth
    has_data = (~np.isnan(sv_raw)).any(axis=0)
    if not has_data.any():
        return None
    last_valid = int(np.where(has_data)[0][-1])
    max_depth = min(MAX_PLOT_DEPTH, depth_vals[min(last_valid, len(depth_vals)-1)] + 10)
    depth_mask = depth_vals <= max_depth
    depth_plot = depth_vals[depth_mask]
    sv_data = sv_raw[:, :len(depth_plot)]

    # Remove fully-NaN pings
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

    # Figure sizing
    width = min(30, max(12, n_pings * 0.003))

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
        cax = None

    x = np.arange(n_pings)
    vmin, vmax = (SV_VMIN, SV_VMAX) if data_var == "Sv" else (0, None)
    im = ax.pcolormesh(
        x, depth_plot, sv_data.T,
        shading="auto", cmap=cmap, vmin=vmin, vmax=vmax, rasterized=True,
    )
    ax.invert_yaxis()
    ax.set_ylabel("Depth (m)", fontsize=11)

    product_label = {"sv": "Sv", "denoised": "Denoised Sv", "mvbs": "MVBS", "nasc": "NASC"}
    ax.set_title(
        f"{day} — {product_label.get(product, product)} {freq_label} (combined)\n"
        f"{n_pings} pings | {cmap_name}",
        fontsize=12, fontweight="bold",
    )

    # Time ticks
    major_ticks, major_labels, _, _ = _build_hourly_ticks(ping_time, n_pings)
    tick_ax = ax_pulse if has_pulse else ax
    tick_ax.set_xticks(major_ticks)
    tick_ax.set_xticklabels(major_labels, rotation=45, ha="right", fontsize=9)
    tick_ax.set_xlabel("Time (UTC)", fontsize=11)
    ax.set_xlim(0, n_pings)

    if has_pulse:
        ax.tick_params(axis="x", labelbottom=False, which="both")
        _draw_pulse_axis(ax_pulse, pulse_mode, n_pings)
        cbar = fig.colorbar(im, cax=cax)
    else:
        cbar = fig.colorbar(im, ax=ax, fraction=0.015, pad=0.01)

    unit = "Sv (dB re 1 m⁻¹)" if data_var == "Sv" else "NASC (m² nmi⁻²)"
    cbar.set_label(unit, fontsize=10)

    safe_cmap = cmap_name.lower().replace(" ", "_")
    fname = f"{day}--{product}--{freq_label}--{safe_cmap}.png"
    out_path = output_dir / fname
    fig.savefig(out_path, dpi=120, bbox_inches="tight")
    plt.close(fig)
    return out_path


def generate_echograms_for_day(
    day: str,
    combined_zarrs: dict[str, Path],
    output_dir: Path,
) -> list[Path]:
    """Generate echograms for all combined products of one day."""
    output_dir.mkdir(parents=True, exist_ok=True)
    files: list[Path] = []

    for product, zarr_path in combined_zarrs.items():
        if not zarr_path.is_dir():
            continue

        ds = xr.open_zarr(str(zarr_path), chunks=None)

        # Determine which frequencies are available
        if "channel" in ds.coords:
            freq_labels = [str(c) for c in ds.channel.values]
        else:
            freq_labels = ["38kHz"]

        for freq_label in freq_labels:
            for cmap_name, cmap_val in COLORMAPS:
                p = render_echogram(
                    ds, day, product, freq_label,
                    cmap_name, cmap_val, output_dir,
                )
                if p:
                    files.append(p)
                    log.info("    echogram: %s", p.name)

        ds.close()
        del ds
        _release_memory()

    return files


# ---------------------------------------------------------------------------
# Worker function for parallel processing
# ---------------------------------------------------------------------------

def process_one_day(args: tuple) -> tuple[str, int, int]:
    """Process one day: combine + echograms. Returns (day, n_zarrs, n_echograms)."""
    day, products, skip_echograms, skip_existing = args

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(message)s",
        datefmt="%H:%M:%S",
        force=True,
    )

    log.info("Processing %s ...", day)
    t0 = time.time()

    combined_zarrs = combine_one_day(day, products, skip_existing=skip_existing)

    n_echograms = 0
    if not skip_echograms and combined_zarrs:
        echogram_files = generate_echograms_for_day(day, combined_zarrs, ECHOGRAM_DIR)
        n_echograms = len(echogram_files)

    dt = time.time() - t0
    log.info(
        "  %s done: %d combined zarrs, %d echograms (%.1fs)",
        day, len(combined_zarrs), n_echograms, dt,
    )
    _release_memory()
    return day, len(combined_zarrs), n_echograms


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Combine pulse modes per day + render echograms")
    parser.add_argument(
        "--products", nargs="+",
        default=["mvbs", "nasc", "denoised", "sv"],
        choices=["mvbs", "nasc", "denoised", "sv"],
        help="Which products to combine (default: all)",
    )
    parser.add_argument("--skip-echograms", action="store_true", help="Skip echogram generation")
    parser.add_argument("--skip-existing", action="store_true", default=True, help="Skip if combined zarr exists")
    parser.add_argument("--force", action="store_true", help="Overwrite existing combined zarrs")
    parser.add_argument("--limit", type=int, default=0, help="Process only first N days")
    parser.add_argument("--workers", type=int, default=1, help="Parallel workers (1=sequential)")
    parser.add_argument("--day", type=str, default=None, help="Process a single day (e.g. 2023-07-15)")
    args = parser.parse_args()

    if args.force:
        args.skip_existing = False

    ECHOGRAM_DIR.mkdir(parents=True, exist_ok=True)

    # Discover days
    if args.day:
        days = [args.day]
    else:
        days = sorted([
            d.name for d in BASE_DIR.iterdir()
            if d.is_dir() and re.match(r"\d{4}-\d{2}-\d{2}$", d.name)
        ])
    if args.limit > 0:
        days = days[:args.limit]

    log.info(
        "Combining %d days, products=%s, workers=%d, echograms=%s",
        len(days), args.products, args.workers, "yes" if not args.skip_echograms else "no",
    )

    t_start = time.time()
    total_zarrs = 0
    total_echograms = 0

    task_args = [
        (day, args.products, args.skip_echograms, args.skip_existing)
        for day in days
    ]

    if args.workers > 1:
        ctx = get_context("spawn")
        with ProcessPoolExecutor(max_workers=args.workers, mp_context=ctx) as pool:
            for day, n_z, n_e in pool.map(process_one_day, task_args):
                total_zarrs += n_z
                total_echograms += n_e
    else:
        for task in task_args:
            day, n_z, n_e = process_one_day(task)
            total_zarrs += n_z
            total_echograms += n_e

    dt_total = time.time() - t_start
    log.info(
        "\n=== DONE: %d days, %d combined zarrs, %d echograms in %.0fs ===",
        len(days), total_zarrs, total_echograms, dt_total,
    )


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(message)s",
        datefmt="%H:%M:%S",
        force=True,
    )
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(line_buffering=True)
    for _noisy in ("zarr", "fsspec", "matplotlib"):
        logging.getLogger(_noisy).setLevel(logging.WARNING)

    main()
