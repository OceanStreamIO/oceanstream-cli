#!/usr/bin/env python3
"""Build a combined 38 kHz campaign MVBS zarr from denoised daily data.

Reads all per-day denoised zarrs (both short_pulse and long_pulse) from
Azure, extracts the 38 kHz channel from each, re-computes MVBS, then
merges everything chronologically into a single campaign zarr.  Generates
gap-free echograms covering all ~20 days.

This addresses NOAA feedback: individual pulse-mode zarrs each contain
only ~10 days because the Saildrone EK80 alternates between short_pulse
(38+200 kHz, 1.024 ms) and long_pulse (38 kHz only, 2.048 ms).  Merging
the 38 kHz channel from both modes yields continuous ~20-day coverage.

VM provisioning:
    cd scripts/batch_processing/vm
    cp .env.example .env   # fill in secrets
    bash provision-batch-vm.sh

Usage (on Azure batch VM — SSH in as 'oceanstream'):
    python build_combined_38khz.py

Or locally with AZURE_STORAGE_CONNECTION_STRING set:
    python build_combined_38khz.py
"""

from __future__ import annotations

import argparse
import gc
import logging
import os
import re
import sys
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
# Configuration
# ---------------------------------------------------------------------------

CONTAINER = "sd-tpos2023-20day-v07"
ACCOUNT = "ne1osvmdevtest"
# Use data disk if available (Azure VM), fall back to /tmp locally.
_DATA_DISK = Path("/mnt/data/output")
OUTPUT_DIR = _DATA_DISK if _DATA_DISK.exists() else Path("/tmp/campaign_output")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

ECHOGRAM_DIR = OUTPUT_DIR / "campaign_echograms"
ECHOGRAM_DIR.mkdir(parents=True, exist_ok=True)

TRANSDUCER_DEPTH: float = 1.9
FREQ_38KHZ: float = 38000.0
MAX_PLOT_DEPTH: float = 1200.0

# MVBS parameters (match existing pipeline — config.py MVBSParams)
MVBS_RANGE_BIN = "1m"
MVBS_PING_TIME_BIN = "10s"

# Echogram visualisation
SV_VMIN = -80.0
SV_VMAX = -50.0
GAP_THRESHOLD_S = 1800  # 30 min — day-boundary detection

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# Suppress extremely verbose Azure SDK HTTP logging
for _noisy in ("azure.core.pipeline.policies.http_logging_policy", "azure.storage", "urllib3"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)

# ---------------------------------------------------------------------------
# EK500 colormap
# ---------------------------------------------------------------------------

_EK500_COLORS = [
    (1.000, 1.000, 1.000),
    (0.624, 0.624, 0.624),
    (0.373, 0.373, 0.686),
    (0.000, 0.000, 0.498),
    (0.000, 0.000, 0.749),
    (0.000, 0.498, 0.000),
    (0.000, 0.749, 0.000),
    (0.498, 0.749, 0.000),
    (0.749, 0.749, 0.000),
    (0.749, 0.498, 0.000),
    (0.749, 0.000, 0.000),
    (0.498, 0.000, 0.000),
]
EK500_CMAP = mcolors.LinearSegmentedColormap.from_list("EK500", _EK500_COLORS, N=256)
COLORMAPS: list[tuple[str, str | mcolors.Colormap]] = [
    ("ocean_r", "ocean_r"),
    ("jet", "jet"),
    ("EK500", EK500_CMAP),
]

# ---------------------------------------------------------------------------
# Azure helpers  (same patterns as rebuild_campaign.py)
# ---------------------------------------------------------------------------


def _connection_string() -> str:
    cs = os.environ.get("AZURE_STORAGE_CONNECTION_STRING", "")
    if not cs:
        log.error("AZURE_STORAGE_CONNECTION_STRING not set")
        sys.exit(1)
    return cs


def normalize_string_dtypes(ds: xr.Dataset) -> xr.Dataset:
    """Convert object / StringDType coords to fixed-length U strings."""
    for name in list(ds.coords) + list(ds.data_vars):
        arr = ds[name]
        if arr.dtype == object or (hasattr(arr.dtype, "kind") and arr.dtype.kind == "T"):
            vals = arr.values
            if isinstance(vals, np.ndarray):
                str_vals = vals.astype(str)
                if name in ds.coords:
                    ds = ds.assign_coords({name: (arr.dims, str_vals)})
                else:
                    ds[name] = xr.DataArray(str_vals, dims=arr.dims)
    return ds


def list_denoised_zarrs() -> list[tuple[str, str, str]]:
    """List all denoised zarr paths from Azure.

    Returns list of ``(day_key, category, zarr_path)`` sorted by day then
    category.
    """
    from azure.storage.blob import ContainerClient

    conn_str = _connection_string()
    client = ContainerClient.from_connection_string(conn_str, CONTAINER)

    pattern = re.compile(
        r"(\d{4}-\d{2}-\d{2})/\d{4}-\d{2}-\d{2}--(\w+)--denoised\.zarr/zarr\.json$"
    )

    results: list[tuple[str, str, str]] = []
    for blob in client.list_blobs(name_starts_with="2023-"):
        m = pattern.search(blob.name)
        if m:
            day_key = m.group(1)
            category = m.group(2)
            zarr_path = blob.name.rsplit("/zarr.json", 1)[0]
            results.append((day_key, category, zarr_path))

    results.sort()
    return results


def open_azure_zarr(zarr_path: str) -> xr.Dataset:
    """Open a zarr store from Azure Blob Storage (lazy)."""
    conn_str = _connection_string()
    store = f"az://{CONTAINER}/{zarr_path}"
    return xr.open_zarr(store, storage_options={"connection_string": conn_str}, chunks={})


# ---------------------------------------------------------------------------
# 38 kHz extraction + MVBS
# ---------------------------------------------------------------------------


def select_38khz(ds: xr.Dataset) -> xr.Dataset:
    """Select only the 38 kHz channel from a dataset.

    Uses ``frequency_nominal`` if available, otherwise falls back to
    matching ``ES38`` in the channel label string.
    """
    if "frequency_nominal" in ds.coords or "frequency_nominal" in ds.data_vars:
        freq = ds["frequency_nominal"].values
        mask = np.isclose(freq, FREQ_38KHZ, atol=100)
        if mask.any():
            return ds.isel(channel=np.where(mask)[0])
    # Fallback: check channel label
    channels = ds.channel.values.astype(str)
    mask = np.array(["ES38" in ch for ch in channels])
    if mask.any():
        return ds.isel(channel=np.where(mask)[0])
    log.warning("No 38 kHz channel found — returning full dataset")
    return ds


def compute_mvbs_for_day(
    day_key: str, category: str, zarr_path: str
) -> xr.Dataset | None:
    """Open a denoised daily zarr, extract 38 kHz, compute MVBS."""
    # Import here to keep top-level imports light
    from oceanstream.echodata.compute.mvbs import compute_mvbs

    log.info(f"Processing {day_key}/{category} ...")
    try:
        ds = open_azure_zarr(zarr_path)
    except Exception as e:
        log.warning(f"  Failed to open {day_key}/{category}: {e}")
        return None

    ds = normalize_string_dtypes(ds)
    ds_38 = select_38khz(ds)

    n_pings = ds_38.sizes.get("ping_time", 0)
    if n_pings == 0:
        log.warning(f"  {day_key}/{category}: 0 pings after 38 kHz selection — skipping")
        ds.close()
        return None

    log.info(f"  {day_key}/{category}: {n_pings} pings, computing MVBS ...")

    try:
        ds_mvbs = compute_mvbs(
            ds_38,
            range_bin=MVBS_RANGE_BIN,
            ping_time_bin=MVBS_PING_TIME_BIN,
        )
    except Exception as e:
        log.warning(f"  MVBS failed for {day_key}/{category}: {e}")
        ds.close()
        return None

    # Tag every ping with its pulse mode so it survives xr.concat
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
    return ds_mvbs


# ---------------------------------------------------------------------------
# Campaign concatenation
# ---------------------------------------------------------------------------


def build_combined_campaign_zarr(
    entries: list[tuple[str, str, str]],
) -> xr.Dataset | None:
    """Compute per-day MVBS and concatenate chronologically."""
    datasets: list[xr.Dataset] = []

    for day_key, category, zarr_path in entries:
        ds_mvbs = compute_mvbs_for_day(day_key, category, zarr_path)
        if ds_mvbs is None:
            continue

        # Clear encodings, normalise strings
        ds_mvbs = normalize_string_dtypes(ds_mvbs)
        for var in list(ds_mvbs.data_vars) + list(ds_mvbs.coords):
            if var in ds_mvbs:
                ds_mvbs[var].encoding.clear()

        # Load into memory for concat (daily MVBS is small)
        ds_mvbs = ds_mvbs.load()
        datasets.append(ds_mvbs)
        log.info(f"  → {day_key}/{category}: {ds_mvbs.sizes['ping_time']} MVBS pings")

    if not datasets:
        log.error("No datasets to concatenate")
        return None

    log.info(f"Concatenating {len(datasets)} daily MVBS datasets ...")
    campaign = xr.concat(datasets, dim="ping_time")
    campaign = campaign.sortby("ping_time")

    # Close individual datasets
    for ds in datasets:
        ds.close()
    del datasets
    gc.collect()

    return campaign


# ---------------------------------------------------------------------------
# Echogram rendering (gap-free, sequential x-axis)
# ---------------------------------------------------------------------------


def find_day_boundaries(
    ping_time: np.ndarray, threshold_s: float = GAP_THRESHOLD_S
) -> tuple[list[int], list[str]]:
    """Find segment-start indices and date labels."""
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
    """Build major (day) and minor (hour) tick positions and labels.

    Major ticks: one per calendar day (label ``MM-DD``).
    Minor ticks: every *hour_interval* hours within each day (label ``HH:MM``).
    """
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
        # Major tick at start of each day
        major_ticks.append(int(day_idxs[0]))
        major_labels.append(str(day)[5:])  # "MM-DD"

        # Minor ticks at hour boundaries within this day
        day_times = ping_time[day_idxs]
        for h in range(0, 24, hour_interval):
            if h == 0:
                continue  # skip midnight — already a major tick
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
    """Extract Sv, depth, ping_time, max_depth, pulse_mode for valid pings."""
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
    pulse_mode = _get_pulse_mode_array(ds, valid_pings)
    log.info(f"  Max valid depth: {max_depth:.0f}m, dropped {n_dropped} empty pings")

    return sv_data, depth_plot, ping_time, max_depth, pulse_mode


def _get_pulse_mode_array(
    ds: xr.Dataset, valid_mask: np.ndarray,
) -> np.ndarray | None:
    """Return int8 pulse_mode array aligned to valid (non-empty) pings."""
    if "pulse_mode" not in ds:
        return None
    pm = ds["pulse_mode"].values
    return pm[valid_mask]


def _draw_pulse_axis(
    ax_pulse: plt.Axes,
    pulse_mode: np.ndarray,
    n_pings: int,
) -> None:
    """Draw pulse-mode coloured bars in a dedicated thin axes below the echogram."""
    from matplotlib.patches import Rectangle

    colors = {0: "#2196F3", 1: "#FF9800"}  # blue=long, orange=short
    labels_map = {0: "Long", 1: "Short"}

    # Detect contiguous runs
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
        # Label centred in each segment (if wide enough)
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
) -> Path:
    """Render a gap-free echogram for the combined 38 kHz dataset."""
    sv_data, depth_plot, ping_time, _max_depth, pulse_mode = _prepare_echogram_data(ds)

    n_pings = len(ping_time)
    log.info(f"  {n_pings} pings")

    t0 = str(ping_time[0])[:10]
    t1 = str(ping_time[-1])[:10]
    x = np.arange(n_pings)
    width = min(250, max(60, n_pings * 0.0025))

    # Layout: echogram + colorbar on top row, pulse bar below (no colorbar col)
    has_pulse = pulse_mode is not None
    if has_pulse:
        from matplotlib.gridspec import GridSpec

        # Fixed ~0.3-inch colorbar regardless of figure width
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
        # Leave gs[1,1] empty — pulse bar spans only data column
    else:
        fig, ax = plt.subplots(figsize=(width, 8))

    im = ax.pcolormesh(
        x, depth_plot, sv_data.T,
        shading="auto", cmap=cmap, vmin=SV_VMIN, vmax=SV_VMAX, rasterized=True,
    )
    ax.invert_yaxis()
    ax.set_ylabel("Depth (m)", fontsize=14)
    ax.set_title(
        f"Campaign MVBS \u2014 Combined 38 kHz (short + long pulse)\n"
        f"Colormap: {cmap_name} | {t0} to {t1} "
        f"({n_pings} pings) | transducer depth: {TRANSDUCER_DEPTH}m",
        fontsize=16, fontweight="bold",
    )

    # X-axis ticks: major = day, minor = every 6 hours
    major_ticks, major_labels, minor_ticks, minor_labels = _build_hourly_ticks(
        ping_time, n_pings, hour_interval=6,
    )

    # Put day/hour ticks on echogram axis (or pulse axis if present)
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
    fname = f"campaign_mvbs_combined_38kHz_{safe_cmap}.png"
    out_path = ECHOGRAM_DIR / fname
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    log.info(f"  Saved: {fname} ({out_path.stat().st_size / 1e6:.1f} MB)")
    return out_path


# ---------------------------------------------------------------------------
# Azure upload
# ---------------------------------------------------------------------------


def upload_to_azure(local_path: Path, blob_prefix: str = "SD_TPOS2023_v03") -> None:
    """Upload a file to the Azure container."""
    import subprocess

    blob_name = f"{blob_prefix}/{local_path.name}"
    subprocess.run(
        [
            "az", "storage", "blob", "upload",
            "--account-name", ACCOUNT,
            "--container-name", CONTAINER,
            "--name", blob_name,
            "--file", str(local_path),
            "--overwrite",
            "--only-show-errors",
        ],
        capture_output=True,
        check=False,
    )


def upload_zarr_to_azure(
    zarr_dir: Path, blob_prefix: str = "SD_TPOS2023_v03"
) -> None:
    """Upload a zarr directory tree to Azure blob-by-blob."""
    import subprocess

    for root, _dirs, files in os.walk(zarr_dir):
        for f in files:
            local = Path(root) / f
            rel = local.relative_to(OUTPUT_DIR)
            blob_name = f"{blob_prefix}/{rel}"
            subprocess.run(
                [
                    "az", "storage", "blob", "upload",
                    "--account-name", ACCOUNT,
                    "--container-name", CONTAINER,
                    "--name", blob_name,
                    "--file", str(local),
                    "--overwrite",
                    "--only-show-errors",
                ],
                capture_output=True,
                check=False,
            )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def _generate_echograms(campaign_ds: xr.Dataset) -> list[Path]:
    """Generate echograms for all colormaps and return output paths."""
    all_outputs: list[Path] = []
    for cmap_name, cmap in COLORMAPS:
        out = plot_combined_echogram(campaign_ds, cmap_name, cmap)
        all_outputs.append(out)
    return all_outputs


def main() -> None:
    parser = argparse.ArgumentParser(description="Build combined 38 kHz campaign MVBS")
    parser.add_argument(
        "--echogram-only", action="store_true",
        help="Skip MVBS recompute; load existing zarr and regenerate echograms only.",
    )
    parser.add_argument(
        "--output-dir", type=Path, default=None,
        help="Override output directory (default: /mnt/data/output or /tmp).",
    )
    args = parser.parse_args()

    if args.output_dir is not None:
        global OUTPUT_DIR, ECHOGRAM_DIR
        OUTPUT_DIR = args.output_dir
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        ECHOGRAM_DIR = OUTPUT_DIR / "campaign_echograms"
        ECHOGRAM_DIR.mkdir(parents=True, exist_ok=True)

    zarr_path = OUTPUT_DIR / "campaign_mvbs_combined_38kHz.zarr"

    if args.echogram_only:
        log.info("--echogram-only: loading existing zarr %s", zarr_path)
        if not zarr_path.exists():
            log.error("Zarr not found at %s — run without --echogram-only first.", zarr_path)
            sys.exit(1)
        campaign_ds = xr.open_zarr(str(zarr_path)).load()
        log.info("Loaded shape: %s", dict(campaign_ds.sizes))
        _generate_echograms(campaign_ds)
        campaign_ds.close()
        log.info("Done (echogram-only).")
        return

    log.info("Listing denoised zarrs in Azure container %s ...", CONTAINER)
    entries = list_denoised_zarrs()
    if not entries:
        log.error("No denoised zarrs found — check AZURE_STORAGE_CONNECTION_STRING")
        sys.exit(1)

    # Summarise what we found
    by_cat: dict[str, int] = {}
    for day_key, category, _ in entries:
        by_cat[category] = by_cat.get(category, 0) + 1
    for cat, n in sorted(by_cat.items()):
        log.info(f"  {cat}: {n} days")
    log.info(f"  Total: {len(entries)} denoised zarrs to process")

    # Build combined campaign MVBS
    campaign_ds = build_combined_campaign_zarr(entries)
    if campaign_ds is None:
        sys.exit(1)

    campaign_ds = campaign_ds.load()
    log.info(
        "Combined campaign shape: %s", dict(campaign_ds.sizes)
    )
    log.info(
        "Time: %s → %s",
        str(campaign_ds.ping_time.values[0])[:19],
        str(campaign_ds.ping_time.values[-1])[:19],
    )

    # Sv statistics
    sv = campaign_ds["Sv"].isel(channel=0).values
    sv_valid = sv[~np.isnan(sv)]
    log.info(
        "Sv stats — min: %.2f dB, max: %.2f dB, mean: %.2f dB",
        np.min(sv_valid),
        np.max(sv_valid),
        np.mean(sv_valid),
    )

    # Save combined zarr
    campaign_ds = normalize_string_dtypes(campaign_ds)
    campaign_ds.to_zarr(str(zarr_path), mode="w")
    log.info(f"Saved combined zarr: {zarr_path}")

    # Generate echograms for all colormaps
    all_outputs = _generate_echograms(campaign_ds)

    campaign_ds.close()
    del campaign_ds
    gc.collect()

    # Upload to Azure
    log.info("Uploading %d echogram files to Azure ...", len(all_outputs))
    for p in all_outputs:
        upload_to_azure(p)
        log.info(f"  Uploaded: {p.name}")

    log.info("Uploading combined zarr to Azure ...")
    upload_zarr_to_azure(zarr_path)

    log.info("Done.")


if __name__ == "__main__":
    main()
