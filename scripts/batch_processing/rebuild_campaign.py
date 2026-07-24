#!/usr/bin/env python3
"""Rebuild campaign MVBS zarrs from per-day MVBS zarrs in Azure.

Applies the StringDType normalization fix and builds full 20-day
campaign zarrs locally, then generates NetCDFs and echograms.

Usage:
    python rebuild_campaign.py
"""

from __future__ import annotations

import gc
import os
import re
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.colors as mcolors
import matplotlib.dates as mdates
import numpy as np
import xarray as xr

CONTAINER = "sd-tpos2023-20day-v07"
ACCOUNT = "ne1osvmdevtest"
OUTPUT_DIR = Path("/tmp/campaign_output")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# Transducer depth below waterline (metres).  depth = echo_range + offset.
TRANSDUCER_DEPTH: float = 1.9

# EK500 colormap
_EK500_COLORS = [
    (1.000, 1.000, 1.000),  # white
    (0.624, 0.624, 0.624),  # light grey
    (0.373, 0.373, 0.686),  # blue-grey
    (0.000, 0.000, 0.498),  # dark blue
    (0.000, 0.000, 0.749),  # blue
    (0.000, 0.498, 0.000),  # dark green
    (0.000, 0.749, 0.000),  # green
    (0.498, 0.749, 0.000),  # yellow-green
    (0.749, 0.749, 0.000),  # yellow
    (0.749, 0.498, 0.000),  # orange
    (0.749, 0.000, 0.000),  # red
    (0.498, 0.000, 0.000),  # dark red
]
EK500_CMAP = mcolors.LinearSegmentedColormap.from_list("EK500", _EK500_COLORS, N=256)


def normalize_string_dtypes(ds: xr.Dataset) -> xr.Dataset:
    """Convert object/StringDType to fixed-length U strings for zarr compat."""
    for name in list(ds.coords) + list(ds.data_vars):
        arr = ds[name]
        if arr.dtype == object or (hasattr(arr.dtype, 'kind') and arr.dtype.kind == 'T'):
            vals = arr.values
            if isinstance(vals, np.ndarray):
                str_vals = vals.astype(str)
                if name in ds.coords:
                    ds = ds.assign_coords({name: (arr.dims, str_vals)})
                else:
                    ds[name] = xr.DataArray(str_vals, dims=arr.dims)
    return ds


def list_mvbs_zarrs() -> dict[str, list[tuple[str, str]]]:
    """List all MVBS zarr paths from Azure, grouped by category."""
    from azure.storage.blob import ContainerClient

    conn_str = os.environ.get("AZURE_STORAGE_CONNECTION_STRING", "")
    client = ContainerClient.from_connection_string(conn_str, CONTAINER)

    # Find all --mvbs.zarr root zarr.json files
    by_category: dict[str, list[tuple[str, str]]] = {}
    pattern = re.compile(r"(\d{4}-\d{2}-\d{2})/\d{4}-\d{2}-\d{2}--(\w+)--mvbs\.zarr/zarr\.json$")

    for blob in client.list_blobs(name_starts_with="2023-"):
        m = pattern.search(blob.name)
        if m:
            day_key = m.group(1)
            category = m.group(2)
            zarr_path = blob.name.rsplit("/zarr.json", 1)[0]
            by_category.setdefault(category, []).append((day_key, zarr_path))

    for cat in by_category:
        by_category[cat].sort()

    return by_category


def open_azure_zarr(zarr_path: str) -> xr.Dataset:
    """Open a zarr from Azure Blob Storage."""
    conn_str = os.environ.get("AZURE_STORAGE_CONNECTION_STRING", "")
    store = f"az://{CONTAINER}/{zarr_path}"
    storage_options = {"connection_string": conn_str}
    # Use chunks={} (lazy) to avoid StringDType cast issues, then load
    ds = xr.open_zarr(store, storage_options=storage_options, chunks={})
    return ds


def build_campaign_zarr(
    category: str,
    paths: list[tuple[str, str]],
) -> xr.Dataset | None:
    """Concatenate all daily MVBS zarrs into one campaign dataset."""
    datasets = []
    for day_key, zarr_path in paths:
        try:
            ds = open_azure_zarr(zarr_path)
            ds = normalize_string_dtypes(ds)
            # Clear encoding to avoid chunk conflicts
            for var in list(ds.data_vars) + list(ds.coords):
                if var in ds:
                    ds[var].encoding.clear()
            datasets.append(ds)
            print(f"  Loaded {day_key}/{category} ({ds.sizes['ping_time']} pings)")
        except Exception as e:
            print(f"  WARNING: Failed to load {day_key}/{category}: {e}")

    if not datasets:
        return None

    print(f"  Concatenating {len(datasets)} days...")
    campaign = xr.concat(datasets, dim="ping_time")

    # Close individual datasets
    for ds in datasets:
        ds.close()

    return campaign


def save_netcdf(ds: xr.Dataset, name: str) -> Path:
    """Save dataset as NetCDF4."""
    ds_mem = ds.load() if hasattr(ds, 'load') else ds

    for var in list(ds_mem.data_vars) + list(ds_mem.coords):
        if var in ds_mem and ds_mem[var].dtype == bool:
            ds_mem[var] = ds_mem[var].astype(np.int8)
        if var in ds_mem and ds_mem[var].dtype == object:
            ds_mem[var] = ds_mem[var].astype(str)

    encoding = {}
    for var in ds_mem.data_vars:
        if ds_mem[var].dtype.kind in {"U", "S", "O"}:
            encoding[var] = {}
        else:
            encoding[var] = {"zlib": True, "complevel": 5}

    nc_path = OUTPUT_DIR / name
    ds_mem.to_netcdf(nc_path, engine="netcdf4", format="NETCDF4", encoding=encoding)
    print(f"  Saved NetCDF: {nc_path.name} ({nc_path.stat().st_size / 1e6:.1f} MB)")
    return nc_path


def plot_echogram(
    ds: xr.Dataset,
    category: str,
    ch_idx: int,
    cmap,
    cmap_name: str,
    vmin: float = -80,
    vmax: float = -50,
    max_depth: float = 500,
) -> Path:
    """Plot a campaign-wide echogram."""
    ch_label = str(ds.channel.values[ch_idx])
    freq_label = ch_label.split("|")[0].strip() if "|" in ch_label else ch_label

    da = ds["Sv"].isel(channel=ch_idx)
    ping_time = da.ping_time.values

    # Use depth if available, otherwise compute from echo_range + offset
    if "depth" in ds.coords or "depth" in ds.dims:
        depth_vals = ds.depth.values
    else:
        depth_vals = da.echo_range.values + TRANSDUCER_DEPTH

    # Limit depth
    depth_mask = depth_vals <= max_depth
    depth_plot = depth_vals[depth_mask]
    sv_data = da.values[:, depth_mask]

    # Insert NaN rows at temporal gaps to prevent pcolormesh stretching
    diffs = np.diff(ping_time).astype("timedelta64[s]").astype(float)
    gap_indices = np.where(diffs > 1800)[0]  # gaps > 30 min
    if len(gap_indices) > 0:
        new_times, new_sv = [], []
        prev = 0
        for gi in gap_indices:
            new_times.append(ping_time[prev:gi + 1])
            new_sv.append(sv_data[prev:gi + 1])
            mid = ping_time[gi] + (ping_time[gi + 1] - ping_time[gi]) // 2
            new_times.append(np.array([mid]))
            new_sv.append(np.full((1, sv_data.shape[1]), np.nan))
            prev = gi + 1
        new_times.append(ping_time[prev:])
        new_sv.append(sv_data[prev:])
        ping_time = np.concatenate(new_times)
        sv_data = np.concatenate(new_sv, axis=0)

    # Time range
    t0 = str(ping_time[0])[:10]
    t1 = str(ping_time[-1])[:10]

    hours = max(1.0, (ping_time[-1] - ping_time[0]).astype("timedelta64[s]").astype(float) / 3600)
    width = min(120, max(30, hours * 0.15))

    fig, ax = plt.subplots(figsize=(width, 8))
    im = ax.pcolormesh(
        ping_time, depth_plot, sv_data.T,
        shading="auto", cmap=cmap, vmin=vmin, vmax=vmax, rasterized=True,
    )
    ax.invert_yaxis()
    ax.set_ylabel("Depth (m)", fontsize=14)
    ax.set_xlabel("Date", fontsize=14)
    ax.set_title(
        f"Campaign MVBS — {category} | {freq_label}\n"
        f"Colormap: {cmap_name} | {t0} to {t1} ({ds.sizes['ping_time']} pings)",
        fontsize=16, fontweight="bold",
    )

    ax.xaxis.set_major_locator(mdates.DayLocator(interval=2))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %d"))
    ax.xaxis.set_minor_locator(mdates.DayLocator())
    plt.setp(ax.get_xticklabels(), rotation=30, ha="right")

    cbar = fig.colorbar(im, ax=ax, fraction=0.03, pad=0.02)
    cbar.set_label("Sv (dB re 1 m⁻¹)", fontsize=12)

    fig.tight_layout()

    safe_cmap = cmap_name.lower().replace(" ", "_")
    fname = f"campaign_mvbs_{category}_{safe_cmap}_ch{ch_idx}.png"
    out_path = OUTPUT_DIR / fname
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  Saved: {fname}")
    return out_path


def upload_to_azure(local_path: Path, blob_prefix: str = "SD_TPOS2023_v03") -> None:
    """Upload a file to the Azure container."""
    import subprocess
    blob_name = f"{blob_prefix}/{local_path.name}"
    subprocess.run(
        ["az", "storage", "blob", "upload",
         "--account-name", ACCOUNT,
         "--container-name", CONTAINER,
         "--name", blob_name,
         "--file", str(local_path),
         "--overwrite", "--only-show-errors"],
        capture_output=True,
    )


def main():
    colormaps = [
        ("ocean_r", "ocean_r"),
        ("jet", "jet"),
        ("EK500", EK500_CMAP),
    ]

    print("Listing MVBS zarrs in Azure...")
    by_category = list_mvbs_zarrs()
    for cat, paths in sorted(by_category.items()):
        print(f"  {cat}: {len(paths)} days")

    all_outputs: list[Path] = []

    for category, paths in sorted(by_category.items()):
        print(f"\n{'='*60}")
        print(f"Building campaign zarr: {category} ({len(paths)} days)")
        print(f"{'='*60}")

        campaign_ds = build_campaign_zarr(category, paths)
        if campaign_ds is None:
            continue

        # Load into memory
        print(f"  Loading into memory...")
        campaign_ds = campaign_ds.load()
        print(f"  Final shape: {dict(campaign_ds.sizes)}")
        print(f"  Time: {str(campaign_ds.ping_time.values[0])[:10]} → {str(campaign_ds.ping_time.values[-1])[:10]}")

        # Add depth coordinate (echo_range + transducer offset)
        if "depth" not in campaign_ds.coords and "echo_range" in campaign_ds.coords:
            campaign_ds = campaign_ds.assign_coords(
                depth=("echo_range", campaign_ds.echo_range.values + TRANSDUCER_DEPTH)
            )
            print(f"  Added depth coord (echo_range + {TRANSDUCER_DEPTH}m transducer offset)")

        # Save local zarr
        zarr_path = OUTPUT_DIR / f"campaign_mvbs_{category}.zarr"
        campaign_ds = normalize_string_dtypes(campaign_ds)
        campaign_ds.to_zarr(str(zarr_path), mode="w")
        print(f"  Saved zarr: {zarr_path.name}")

        # Save NetCDF
        nc_path = save_netcdf(campaign_ds, f"campaign_mvbs_{category}.nc")
        all_outputs.append(nc_path)

        # Plot echograms
        n_ch = campaign_ds.sizes.get("channel", 1)
        for ch_idx in range(n_ch):
            for cmap_name, cmap in colormaps:
                out = plot_echogram(campaign_ds, category, ch_idx, cmap, cmap_name)
                all_outputs.append(out)

        campaign_ds.close()
        del campaign_ds
        gc.collect()

    # Upload everything to Azure
    print(f"\n{'='*60}")
    print(f"Uploading {len(all_outputs)} files to Azure...")
    for p in all_outputs:
        upload_to_azure(p)
        print(f"  Uploaded: {p.name}")

    # Also upload the zarrs
    for cat in by_category:
        zarr_dir = OUTPUT_DIR / f"campaign_mvbs_{cat}.zarr"
        if zarr_dir.exists():
            import subprocess
            # Upload zarr directory blob-by-blob
            for root, dirs, files in os.walk(zarr_dir):
                for f in files:
                    local = Path(root) / f
                    rel = local.relative_to(OUTPUT_DIR)
                    blob_name = f"SD_TPOS2023_v03/{rel}"
                    subprocess.run(
                        ["az", "storage", "blob", "upload",
                         "--account-name", ACCOUNT,
                         "--container-name", CONTAINER,
                         "--name", blob_name,
                         "--file", str(local),
                         "--overwrite", "--only-show-errors"],
                        capture_output=True,
                    )
            print(f"  Uploaded zarr: campaign_mvbs_{cat}.zarr")

    print(f"\nDone! All files in {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
