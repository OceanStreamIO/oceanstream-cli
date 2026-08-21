#!/usr/bin/env python3
"""Multi-frequency classification on denoised Sv data.

Applies dB-differencing (Sv_38 − Sv_200) to classify acoustic targets:
  - Fish (swim-bladdered): strongly positive dSv (38 kHz dominant)
  - Krill/zooplankton: strongly negative dSv (200 kHz dominant)
  - Mixed/unclassified: intermediate dSv

Generates echograms for each class + a dSv heatmap.

Usage:
    DASK_CLUSTER_ADDRESS="" python multifrequency_classify.py

    # Custom thresholds
    DASK_CLUSTER_ADDRESS="" python multifrequency_classify.py \
        --fish-thr 2 15 --zoo-thr -20 -3
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


def parse_args():
    p = argparse.ArgumentParser(
        description="Multi-frequency classification on denoised Sv data.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--day", default="2023-08-10")
    p.add_argument("--category", default="short_pulse")
    p.add_argument("--surface-exclude", type=float, default=10.0)
    p.add_argument("--fish-thr", nargs=2, type=float, default=[2.0, 15.0],
                   metavar=("LOW", "HIGH"),
                   help="dSv threshold range for fish (Sv_38 - Sv_200)")
    p.add_argument("--zoo-thr", nargs=2, type=float, default=[-20.0, -3.0],
                   metavar=("LOW", "HIGH"),
                   help="dSv threshold range for zooplankton/krill (Sv_38 - Sv_200)")
    p.add_argument("--output-dir", default="./output")
    p.add_argument("--use-denoised", action="store_true", default=True,
                   help="Use denoised zarr (default: True)")
    p.add_argument("--dpi", type=int, default=150)
    return p.parse_args()


def main():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates
    import pandas as pd
    import xarray as xr
    from oceanstream.echodata.multifrequency import db_difference

    args = parse_args()
    output_dir = Path(args.output_dir)
    day = args.day
    category = args.category

    # Determine input zarr
    if args.use_denoised:
        zarr_path = output_dir / day / f"{day}--{category}--denoised.zarr"
    else:
        zarr_path = output_dir / day / f"{day}--{category}.zarr"

    if not zarr_path.exists():
        logger.error("Zarr not found: %s", zarr_path)
        sys.exit(1)

    # Output directory for classification echograms
    echogram_dir = output_dir / day / "echograms" / "multifrequency"
    echogram_dir.mkdir(parents=True, exist_ok=True)

    # --- Load data ---
    logger.info("Loading %s", zarr_path)
    ds = xr.open_zarr(str(zarr_path))
    ds = ds.load()
    logger.info("  Shape: %s", dict(ds.sizes))

    # Surface exclusion
    if args.surface_exclude and "depth" in ds.dims:
        ds = ds.sel(depth=slice(args.surface_exclude, None))
        logger.info("  After surface exclude (%.0fm): %s", args.surface_exclude, dict(ds.sizes))

    # Identify channels
    channels = [str(c) for c in ds.channel.values]
    logger.info("  Channels: %s", channels)

    # --- Run multi-frequency classification ---
    fish_thr = tuple(args.fish_thr)
    zoo_thr = tuple(args.zoo_thr)

    logger.info("Classifying targets:")
    logger.info("  Fish threshold:        dSv(38-200) in [%.1f, %.1f] dB", *fish_thr)
    logger.info("  Zooplankton threshold: dSv(38-200) in [%.1f, %.1f] dB", *zoo_thr)

    # Use substring matching — channel names contain both "38" and "200" since
    # these are combo transducers (ES38-18|200-18C). Use "07" and "08" to match
    # the unique serial numbers, or use index-based approach.
    t0 = time.perf_counter()

    # Identify channels by frequency_nominal if available, else by index
    ch_low = channels[0]   # 38 kHz channel
    ch_high = channels[1]  # 200 kHz channel
    logger.info("  Low-freq channel:  %s", ch_low)
    logger.info("  High-freq channel: %s", ch_high)

    fish_result = db_difference(ds, freq_low=ch_low, freq_high=ch_high, thr=fish_thr)
    zoo_result = db_difference(ds, freq_low=ch_low, freq_high=ch_high, thr=zoo_thr)

    logger.info("  Fish:        %d pixels (%.1f%%)",
                fish_result.pixels_in_range, fish_result.fraction_in_range * 100)
    logger.info("  Zooplankton: %d pixels (%.1f%%)",
                zoo_result.pixels_in_range, zoo_result.fraction_in_range * 100)
    logger.info("  Classification done in %.1fs", time.perf_counter() - t0)

    # --- Prepare data for plotting ---
    # Get 38 kHz Sv for the base echogram
    sv_38 = ds["Sv"].sel(channel=fish_result.freq_low)
    sv_200 = ds["Sv"].sel(channel=fish_result.freq_high)
    dsv = fish_result.difference  # Sv_38 - Sv_200

    # Determine spatial dimensions
    xdim = "ping_time"
    ydim = "depth" if "depth" in sv_38.dims else "range_sample"

    # Time axis
    time_vals = pd.to_datetime(sv_38[xdim].values)
    depth_vals = sv_38[ydim].values

    # --- Downsample for fast rendering ---
    # Target ~3000 pixels wide, ~1000 tall (enough for 150 dpi at ~20" wide)
    max_x, max_y = 3000, 1000
    n_pings, n_depth = len(time_vals), len(depth_vals)
    stride_x = max(1, n_pings // max_x)
    stride_y = max(1, n_depth // max_y)
    logger.info("  Downsampling for plots: stride_x=%d, stride_y=%d (from %dx%d to ~%dx%d)",
                stride_x, stride_y, n_pings, n_depth,
                n_pings // stride_x, n_depth // stride_y)

    # Downsampled axes
    time_ds = time_vals[::stride_x]
    depth_ds = depth_vals[::stride_y]

    def downsample(data):
        """Downsample 2D array (ping_time × depth) by striding."""
        return data[::stride_x, ::stride_y]

    # --- Plot helper using imshow (fast) ---
    def plot_echogram(data, title, filename, cmap="ocean_r", vmin=-80, vmax=-50,
                      cbar_label="Sv (dB re 1 m⁻¹)"):
        data_ds = downsample(data)
        hours = max(1.0, (time_ds[-1] - time_ds[0]).total_seconds() / 3600.0)
        width = min(max(14.0, hours * 1.0), 34.0)
        fig, ax = plt.subplots(figsize=(width, 10))

        im = ax.imshow(
            data_ds.T,
            aspect="auto",
            origin="upper",
            extent=[mdates.date2num(time_ds[0]), mdates.date2num(time_ds[-1]),
                    depth_ds[-1], depth_ds[0]],
            cmap=cmap, vmin=vmin, vmax=vmax, interpolation="nearest",
        )
        ax.set_facecolor("#f9f9f9")

        ax.xaxis_date()
        ax.xaxis.set_major_locator(mdates.AutoDateLocator(minticks=6, maxticks=18))
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
        plt.setp(ax.get_xticklabels(), rotation=30, ha="right")

        cbar = plt.colorbar(im, ax=ax, fraction=0.04, pad=0.02)
        cbar.set_label(cbar_label, fontsize=12)

        ax.set_xlabel("Time (UTC)", fontsize=14)
        ax.set_ylabel("Depth (m)" if ydim == "depth" else "Range sample", fontsize=14)
        ax.set_title(title, fontsize=16, fontweight="bold", pad=12)

        fig.tight_layout(pad=2)
        out = echogram_dir / filename
        fig.savefig(out, dpi=args.dpi)
        plt.close(fig)
        logger.info("  Saved: %s", out.name)
        return out

    # --- Plot 1: dSv heatmap ---
    logger.info("Generating echograms...")
    plot_echogram(
        dsv.values,
        title=f"dSv (38 kHz − 200 kHz) — {day}",
        filename=f"{day}--{category}--dsv-heatmap.png",
        cmap="RdBu_r",
        vmin=-30,
        vmax=30,
        cbar_label="dSv (dB)",
    )

    # --- Plot 2: 38 kHz denoised (reference) ---
    plot_echogram(
        sv_38.values,
        title=f"Denoised Sv — 38 kHz — {day}",
        filename=f"{day}--{category}--denoised-38kHz.png",
    )

    # --- Plot 3: Fish-only echogram (38 kHz, masked to fish regions) ---
    sv_fish = sv_38.values.copy()
    sv_fish[~fish_result.mask.values] = np.nan
    plot_echogram(
        sv_fish,
        title=f"Fish only (dSv ∈ [{fish_thr[0]:.0f}, {fish_thr[1]:.0f}] dB) — 38 kHz — {day}",
        filename=f"{day}--{category}--fish-only-38kHz.png",
    )

    # --- Plot 4: Zooplankton-only echogram (200 kHz, masked to zoo regions) ---
    sv_zoo = sv_200.values.copy()
    sv_zoo[~zoo_result.mask.values] = np.nan
    plot_echogram(
        sv_zoo,
        title=f"Zooplankton only (dSv ∈ [{zoo_thr[0]:.0f}, {zoo_thr[1]:.0f}] dB) — 200 kHz — {day}",
        filename=f"{day}--{category}--zooplankton-only-200kHz.png",
    )

    # --- Plot 5: Composite classification (RGB-style overlay) ---
    fig, ax = plt.subplots(figsize=(min(max(14.0, (time_ds[-1] - time_ds[0]).total_seconds() / 3600.0), 34.0), 10))

    # Base: denoised 38 kHz in grayscale (downsampled)
    sv_38_ds = downsample(sv_38.values)
    sv_norm = (sv_38_ds - (-80)) / ((-50) - (-80))  # normalize to [0, 1]
    sv_norm = np.clip(sv_norm, 0, 1)

    # Build RGB image
    h, w = sv_norm.shape
    rgb = np.zeros((w, h, 3))  # transposed for imshow

    # Gray background from 38 kHz
    gray = sv_norm.T
    rgb[..., 0] = gray * 0.7
    rgb[..., 1] = gray * 0.7
    rgb[..., 2] = gray * 0.7

    # Fish overlay in red (downsampled mask)
    fish_mask_2d = downsample(fish_result.mask.values).T
    rgb[fish_mask_2d, 0] = 1.0
    rgb[fish_mask_2d, 1] = 0.2
    rgb[fish_mask_2d, 2] = 0.1

    # Zooplankton overlay in blue (downsampled mask)
    zoo_mask_2d = downsample(zoo_result.mask.values).T
    rgb[zoo_mask_2d, 0] = 0.1
    rgb[zoo_mask_2d, 1] = 0.4
    rgb[zoo_mask_2d, 2] = 1.0

    ax.imshow(
        rgb,
        aspect="auto",
        extent=[mdates.date2num(time_ds[0]), mdates.date2num(time_ds[-1]),
                depth_ds[-1], depth_ds[0]],
        interpolation="nearest",
    )
    ax.xaxis_date()
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    plt.setp(ax.get_xticklabels(), rotation=30, ha="right")
    ax.set_xlabel("Time (UTC)", fontsize=14)
    ax.set_ylabel("Depth (m)" if ydim == "depth" else "Range sample", fontsize=14)
    ax.set_title(
        f"Multi-frequency classification — {day}\n"
        f"Red = Fish (dSv [{fish_thr[0]:.0f},{fish_thr[1]:.0f}]), "
        f"Blue = Zooplankton (dSv [{zoo_thr[0]:.0f},{zoo_thr[1]:.0f}]), "
        f"Gray = Unclassified",
        fontsize=14, fontweight="bold", pad=12,
    )

    fig.tight_layout(pad=2)
    composite_path = echogram_dir / f"{day}--{category}--classification-composite.png"
    fig.savefig(composite_path, dpi=args.dpi)
    plt.close(fig)
    logger.info("  Saved: %s", composite_path.name)

    # --- Summary ---
    logger.info("=" * 60)
    logger.info("Multi-frequency classification complete")
    logger.info("  Day: %s, Category: %s", day, category)
    logger.info("  Fish:        %.1f%% of valid pixels", fish_result.fraction_in_range * 100)
    logger.info("  Zooplankton: %.1f%% of valid pixels", zoo_result.fraction_in_range * 100)
    logger.info("  Unclassified: %.1f%%",
                100 - (fish_result.fraction_in_range + zoo_result.fraction_in_range) * 100)
    logger.info("  Output: %s", echogram_dir)
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
