#!/usr/bin/env python3
"""Convert campaign MVBS zarrs to NetCDF and generate echograms.

Generates echograms with three colormaps:
  - ocean_r (default oceanstream)
  - jet (matplotlib standard)
  - EK500 (Simrad EK500 echosounder standard)

Usage:
    python campaign_echograms.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.colors as mcolors
import matplotlib.dates as mdates
import numpy as np
import xarray as xr

ZARR_DIR = Path("/tmp/campaign_zarrs/SD_TPOS2023_v03")
OUTPUT_DIR = Path("/tmp/campaign_output")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

CONTAINER = "sd-tpos2023-20day-v07"


# ── EK500 colormap ──────────────────────────────────────────────────────
# Simrad EK500 echoview colour scheme: 12 discrete colours mapping
# dB ranges from -81 (white) to -34 (brown/red).

_EK500_COLORS = [
    (1.000, 1.000, 1.000),  # -81 to -78: white
    (0.624, 0.624, 0.624),  # -78 to -75: light grey
    (0.373, 0.373, 0.686),  # -75 to -72: blue-grey
    (0.000, 0.000, 0.498),  # -72 to -66: dark blue
    (0.000, 0.000, 0.749),  # -66 to -60: blue
    (0.000, 0.498, 0.000),  # -60 to -54: dark green
    (0.000, 0.749, 0.000),  # -54 to -48: green
    (0.498, 0.749, 0.000),  # -48 to -42: yellow-green
    (0.749, 0.749, 0.000),  # -42 to -36: yellow
    (0.749, 0.498, 0.000),  # -36 to -30: orange
    (0.749, 0.000, 0.000),  # -30 to -24: red
    (0.498, 0.000, 0.000),  # -24+: dark red
]

EK500_CMAP = mcolors.LinearSegmentedColormap.from_list(
    "EK500", _EK500_COLORS, N=256,
)


def save_netcdf(ds: xr.Dataset, zarr_name: str) -> Path:
    """Save campaign zarr as NetCDF4 with compression."""
    ds_mem = ds.load()

    # Fix boolean and object dtypes for NetCDF compat
    for var in list(ds_mem.data_vars) + list(ds_mem.coords):
        if var in ds_mem and ds_mem[var].dtype == bool:
            ds_mem[var] = ds_mem[var].astype(np.int8)
        if var in ds_mem and ds_mem[var].dtype == object:
            ds_mem[var] = ds_mem[var].astype(str)

    # Build encoding
    encoding = {}
    for var in ds_mem.data_vars:
        if ds_mem[var].dtype.kind in {"U", "S", "O"}:
            encoding[var] = {}
        else:
            encoding[var] = {"zlib": True, "complevel": 5}

    nc_name = zarr_name.replace(".zarr", ".nc")
    nc_path = OUTPUT_DIR / nc_name
    ds_mem.to_netcdf(nc_path, engine="netcdf4", format="NETCDF4", encoding=encoding)
    print(f"  Saved NetCDF: {nc_path} ({nc_path.stat().st_size / 1e6:.1f} MB)")
    return nc_path


def plot_campaign_echogram(
    ds: xr.Dataset,
    category: str,
    ch_idx: int,
    cmap,
    cmap_name: str,
    vmin: float = -80,
    vmax: float = -50,
) -> Path:
    """Plot a campaign-wide echogram for one channel."""
    ch_label = str(ds.channel.values[ch_idx])
    # Extract friendly freq label
    freq_label = ch_label.split("|")[0].strip() if "|" in ch_label else ch_label

    da = ds["Sv"].isel(channel=ch_idx)

    # Use echo_range as y-axis (depth in meters)
    ping_time = da.ping_time.values
    echo_range = da.echo_range.values
    sv_data = da.values  # (ping_time, echo_range)

    # Figure sizing — wide for 20-day campaign
    n_pings = len(ping_time)
    hours = max(1.0, (ping_time[-1] - ping_time[0]).astype("timedelta64[s]").astype(float) / 3600)

    width = min(40, max(18, hours * 0.05))
    height = 8

    fig, ax = plt.subplots(figsize=(width, height))

    im = ax.pcolormesh(
        ping_time, echo_range, sv_data.T,
        shading="auto",
        cmap=cmap,
        vmin=vmin,
        vmax=vmax,
        rasterized=True,
    )
    ax.invert_yaxis()
    ax.set_ylabel("Depth (m)", fontsize=14)
    ax.set_xlabel("Date", fontsize=14)
    ax.set_title(
        f"Campaign MVBS — {category} | {freq_label}\n"
        f"Colormap: {cmap_name} | {str(ping_time[0])[:10]} to {str(ping_time[-1])[:10]}",
        fontsize=16,
        fontweight="bold",
    )

    # Date formatting
    ax.xaxis.set_major_locator(mdates.DayLocator(interval=2))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %d"))
    ax.xaxis.set_minor_locator(mdates.DayLocator())
    plt.setp(ax.get_xticklabels(), rotation=30, ha="right")

    # Colorbar
    cbar = fig.colorbar(im, ax=ax, fraction=0.03, pad=0.02)
    cbar.set_label("Sv (dB re 1 m⁻¹)", fontsize=12)

    # Limit depth to meaningful range (skip deep empty bins)
    max_depth = min(500, echo_range[-1])
    ax.set_ylim(max_depth, 0)

    fig.tight_layout()

    safe_cmap = cmap_name.lower().replace(" ", "_")
    fname = f"campaign_mvbs_{category}_{safe_cmap}_ch{ch_idx}.png"
    out_path = OUTPUT_DIR / fname
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  Saved echogram: {out_path.name}")
    return out_path


def upload_to_azure(local_path: Path, blob_prefix: str = "SD_TPOS2023_v03") -> None:
    """Upload a file to the Azure container."""
    import subprocess
    blob_name = f"{blob_prefix}/{local_path.name}"
    cmd = [
        "az", "storage", "blob", "upload",
        "--account-name", "ne1osvmdevtest",
        "--container-name", CONTAINER,
        "--name", blob_name,
        "--file", str(local_path),
        "--overwrite",
        "--only-show-errors",
    ]
    subprocess.run(cmd, capture_output=True)


def main():
    colormaps = [
        ("ocean_r", "ocean_r"),
        ("jet", "jet"),
        ("EK500", EK500_CMAP),
    ]

    all_outputs: list[Path] = []

    for zarr_name in ["campaign_mvbs_long_pulse.zarr", "campaign_mvbs_short_pulse.zarr"]:
        zarr_path = ZARR_DIR / zarr_name
        if not zarr_path.exists():
            print(f"Skipping {zarr_name} — not found")
            continue

        category = zarr_name.replace("campaign_mvbs_", "").replace(".zarr", "")
        print(f"\n{'='*60}")
        print(f"Processing: {zarr_name} ({category})")
        print(f"{'='*60}")

        ds = xr.open_zarr(str(zarr_path))
        print(f"  Dims: {dict(ds.sizes)}")
        print(f"  Channels: {list(ds.channel.values)}")

        # 1. Save NetCDF
        nc_path = save_netcdf(ds, zarr_name)
        all_outputs.append(nc_path)

        # 2. Reload for plotting (already loaded by save_netcdf)
        ds_mem = xr.open_zarr(str(zarr_path)).load()

        n_ch = ds_mem.sizes.get("channel", 1)
        for ch_idx in range(n_ch):
            for cmap_name, cmap in colormaps:
                out = plot_campaign_echogram(
                    ds_mem, category, ch_idx, cmap, cmap_name,
                )
                all_outputs.append(out)

        ds.close()
        del ds_mem

    # Upload all outputs to Azure
    print(f"\n{'='*60}")
    print(f"Uploading {len(all_outputs)} files to Azure...")
    for p in all_outputs:
        upload_to_azure(p)
        print(f"  Uploaded: {p.name}")

    print(f"\nDone! {len(all_outputs)} files in {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
