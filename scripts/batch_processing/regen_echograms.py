#!/usr/bin/env python3
"""Regenerate campaign echograms with gap-free sequential x-axis and depth."""
from __future__ import annotations

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.colors as mcolors
import numpy as np
import xarray as xr
from pathlib import Path

TRANSDUCER_DEPTH = 1.9
ZARR_DIR = Path("/tmp/campaign_output")
OUTPUT_DIR = Path("/Users/andrei/oceanstream/sd-data-ingest/raw_data/saildrone-ek80-echodata/campaign_echograms")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

GAP_THRESHOLD_S = 1800  # 30 min — detect day boundaries

_EK500_COLORS = [
    (1.000, 1.000, 1.000), (0.624, 0.624, 0.624), (0.373, 0.373, 0.686),
    (0.000, 0.000, 0.498), (0.000, 0.000, 0.749), (0.000, 0.498, 0.000),
    (0.000, 0.749, 0.000), (0.498, 0.749, 0.000), (0.749, 0.749, 0.000),
    (0.749, 0.498, 0.000), (0.749, 0.000, 0.000), (0.498, 0.000, 0.000),
]
EK500_CMAP = mcolors.LinearSegmentedColormap.from_list("EK500", _EK500_COLORS, N=256)
COLORMAPS = [("ocean_r", "ocean_r"), ("jet", "jet"), ("EK500", EK500_CMAP)]


def find_day_boundaries(ping_time, threshold_s=GAP_THRESHOLD_S):
    """Find indices and labels for day boundaries (gaps > threshold)."""
    diffs = np.diff(ping_time).astype("timedelta64[s]").astype(float)
    gap_indices = np.where(diffs > threshold_s)[0]
    # Each gap_index+1 is the start of a new day segment
    seg_starts = [0] + [gi + 1 for gi in gap_indices]
    labels = []
    for si in seg_starts:
        dt = ping_time[si].astype("datetime64[D]")
        labels.append(str(dt))
    return seg_starts, labels


for category in ["long_pulse", "short_pulse"]:
    zarr_path = ZARR_DIR / f"campaign_mvbs_{category}.zarr"
    print(f"Loading {zarr_path.name}...")
    ds = xr.open_zarr(str(zarr_path))
    ds = ds.load()
    print(f"  Shape: {dict(ds.sizes)}")

    depth_vals = ds.echo_range.values + TRANSDUCER_DEPTH

    n_ch = ds.sizes.get("channel", 1)
    for ch_idx in range(n_ch):
        ch_label = str(ds.channel.values[ch_idx])
        freq_label = ch_label.split("|")[0].strip() if "|" in ch_label else ch_label

        da = ds["Sv"].isel(channel=ch_idx)
        ping_time = da.ping_time.values
        sv_raw = da.values

        # Auto-detect max valid depth for this channel
        has_data = (~np.isnan(sv_raw)).any(axis=0)
        last_valid = int(np.where(has_data)[0][-1]) if has_data.any() else 0
        max_depth = min(1000, depth_vals[last_valid] + 10)  # small padding
        depth_mask = depth_vals <= max_depth
        depth_plot = depth_vals[depth_mask]
        sv_data = sv_raw[:, depth_mask]

        # Remove pings that are entirely NaN (show as empty vertical lines)
        valid_pings = ~np.isnan(sv_data).all(axis=1)
        sv_data = sv_data[valid_pings]
        ping_time = ping_time[valid_pings]
        print(f"    max valid depth: {max_depth:.0f}m, dropped {(~valid_pings).sum()} empty pings")

        # Find day boundaries
        seg_starts, seg_labels = find_day_boundaries(ping_time)
        n_pings = len(ping_time)
        print(f"  ch{ch_idx}: {n_pings} pings, {len(seg_starts)} day segments")

        t0 = str(ping_time[0])[:10]
        t1 = str(ping_time[-1])[:10]

        # Use sequential ping index as x-axis (no temporal gaps)
        x = np.arange(n_pings)
        width = min(250, max(60, n_pings * 0.0025))

        for cmap_name, cmap in COLORMAPS:
            fig, ax = plt.subplots(figsize=(width, 8))
            im = ax.pcolormesh(
                x, depth_plot, sv_data.T,
                shading="auto", cmap=cmap, vmin=-80, vmax=-50, rasterized=True,
            )
            ax.invert_yaxis()
            ax.set_ylabel("Depth (m)", fontsize=14)
            ax.set_xlabel("Date", fontsize=14)
            ax.set_title(
                f"Campaign MVBS \u2014 {category} | {freq_label}\n"
                f"Colormap: {cmap_name} | {t0} to {t1} "
                f"({n_pings} pings) | transducer depth: {TRANSDUCER_DEPTH}m",
                fontsize=16, fontweight="bold",
            )

            # Draw day boundaries as dashed lines + date labels
            for si, label in zip(seg_starts, seg_labels):
                ax.axvline(si, color="red", linewidth=0.8, linestyle="--", alpha=0.7)

            # Build per-day ticks: for each segment, add ticks at every
            # calendar-day boundary within that segment.
            all_ticks = []
            all_tick_labels = []
            for seg_i in range(len(seg_starts)):
                s = seg_starts[seg_i]
                e = seg_starts[seg_i + 1] if seg_i + 1 < len(seg_starts) else n_pings
                seg_times = ping_time[s:e]
                seg_days = np.unique(seg_times.astype("datetime64[D]"))
                for day in seg_days:
                    mask = (seg_times >= day) & (seg_times < day + np.timedelta64(1, "D"))
                    idxs = np.where(mask)[0]
                    if len(idxs) > 0:
                        all_ticks.append(s + idxs[0])
                        all_tick_labels.append(str(day)[5:])  # "MM-DD"

            ax.set_xticks(all_ticks)
            ax.set_xticklabels(
                all_tick_labels,
                rotation=45, ha="right", fontsize=10,
            )
            ax.set_xlim(0, n_pings)

            cbar = fig.colorbar(im, ax=ax, fraction=0.015, pad=0.01)
            cbar.set_label("Sv (dB re 1 m\u207b\u00b9)", fontsize=12)
            fig.tight_layout()

            safe_cmap = cmap_name.lower().replace(" ", "_")
            fname = f"campaign_mvbs_{category}_{safe_cmap}_ch{ch_idx}.png"
            fig.savefig(OUTPUT_DIR / fname, dpi=150, bbox_inches="tight")
            plt.close(fig)
            print(f"    Saved: {fname}")

    ds.close()

print("Done!")
