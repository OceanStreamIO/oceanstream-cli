"""Combined long+short pulse 24h echograms at 38 kHz.

The Saildrone EK80 alternates between two operating modes on the same
transducer:

    - short_pulse : 1.024 ms pulse, 38 + 200 kHz interleaved
    - long_pulse  : 2.048 ms pulse, 38 kHz only

Combining the 38 kHz channel from both modes gives continuous coverage on
a single 24-hour panel, with a pulse-mode indicator bar at the bottom
showing which mode was active at each ping.

Ported from ``scripts/batch_processing/run_combine_daily.py`` (the
"gold standard" batch script), simplified to the single per-day combined
38 kHz workflow that the current pipeline needs.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Optional, Union

import numpy as np

if TYPE_CHECKING:
    import matplotlib.colors as mcolors
    import xarray as xr

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

FREQ_38KHZ: float = 38000.0

# Common depth grid used to interpolate both pulse modes onto the same axis.
# The two modes have different range_sample counts (short: ~7218, long: ~3609)
# but cover the same ~0–1300 m physical range.
COMMON_DEPTH_STEP: float = 0.5   # metres
COMMON_DEPTH_MAX: float = 1305.0  # metres

# Sv render range (matches the per-category plots)
SV_VMIN: float = -80.0
SV_VMAX: float = -50.0

# Max depth to plot (metres). Deeper samples get trimmed off — pure background.
MAX_PLOT_DEPTH: float = 1200.0

# Horizontal sampling target for the canvas. Below ~2 pings per pixel the
# rasteriser aliases adjacent pings together, which makes a full-resolution
# Sv panel look indistinguishable from a gridded MVBS one.
PINGS_PER_PIXEL: float = 2.0

# Cap on figure width (inches). At dpi=150 this is 15 000 px.
MAX_WIDTH_IN: float = 100.0

# Pulse-mode indicator bar colours (matches legacy scripts)
_PULSE_COLORS = {
    0: "#2196F3",   # blue      — long_pulse
    1: "#FF9800",   # orange    — short_pulse
    -1: "#9E9E9E",  # grey      — unknown
}
_PULSE_LABELS = {
    0: "Long pulse",
    1: "Short pulse",
    -1: "Unknown",
}


# ---------------------------------------------------------------------------
# xarray helpers
# ---------------------------------------------------------------------------


def _get_freq_channel_index(ds: "xr.Dataset", target_hz: float, atol: float = 100.0) -> Optional[int]:
    """Return the channel index whose ``frequency_nominal`` matches ``target_hz``."""
    if "frequency_nominal" not in ds.coords and "frequency_nominal" not in ds.data_vars:
        return None
    freqs = ds["frequency_nominal"].values
    for i, f in enumerate(np.atleast_1d(freqs)):
        if not np.isnan(f) and np.isclose(float(f), target_hz, atol=atol):
            return i
    return None


def _clear_encoding(ds: "xr.Dataset") -> "xr.Dataset":
    for var in list(ds.data_vars) + list(ds.coords):
        if var in ds:
            ds[var].encoding.clear()
    return ds


def _get_ping_depth_axis(ds_ch: "xr.Dataset") -> np.ndarray:
    """Extract a per-``range_sample`` depth array (metres) for the channel."""
    if "echo_range" in ds_ch:
        er = ds_ch["echo_range"]
        if "channel" in er.dims:
            er = er.isel(channel=0)
        if "ping_time" in er.dims:
            return np.nanmedian(er.values, axis=0)
        return er.values
    if "depth" in ds_ch.coords:
        return ds_ch["depth"].values
    return np.arange(ds_ch.sizes.get("range_sample", 0), dtype=float)


# ---------------------------------------------------------------------------
# Core: combine 38 kHz channel across pulse modes
# ---------------------------------------------------------------------------


@dataclass
class CombinedDataset:
    """Container for a combined-pulse per-day Sv dataset."""

    dataset: "xr.Dataset"
    n_pings: int
    pulse_modes_present: list[str]  # e.g. ["long_pulse", "short_pulse"]


def combine_38khz_day(
    zarrs_by_category: dict[str, Union[str, Path, "xr.Dataset"]],
) -> Optional[CombinedDataset]:
    """Merge 38 kHz Sv from short_pulse and long_pulse for a single day.

    Parameters
    ----------
    zarrs_by_category : dict
        Mapping of pulse-mode name → zarr path or already-loaded ``xr.Dataset``.
        Expected keys are ``"short_pulse"`` and/or ``"long_pulse"``. Missing
        keys are simply skipped (single-mode days still return a valid
        combined dataset with just the present mode).

    Returns
    -------
    CombinedDataset or None
        ``None`` when neither pulse mode contains a valid 38 kHz channel.
    """
    import xarray as xr

    common_depth = np.arange(0.0, COMMON_DEPTH_MAX, COMMON_DEPTH_STEP)
    mode_datasets: list[xr.Dataset] = []
    modes_present: list[str] = []
    opened_paths: list[xr.Dataset] = []

    for mode in ("short_pulse", "long_pulse"):
        src = zarrs_by_category.get(mode)
        if src is None:
            continue

        try:
            if isinstance(src, xr.Dataset):
                ds = src
            else:
                ds = xr.open_zarr(str(src), chunks=None)
                opened_paths.append(ds)
            ds = _clear_encoding(ds)
        except Exception as exc:
            logger.warning("Combined: failed to open %s zarr %s: %s", mode, src, exc)
            continue

        if "Sv" not in ds:
            logger.info("Combined: %s zarr has no Sv variable — skipping", mode)
            continue

        ch_idx = _get_freq_channel_index(ds, FREQ_38KHZ)
        if ch_idx is None:
            logger.info("Combined: %s zarr has no 38 kHz channel — skipping", mode)
            continue

        ds_ch = ds.isel(channel=[ch_idx])
        sv_da = ds_ch["Sv"].isel(channel=0)  # (ping_time, range_sample)
        n_pings = int(sv_da.sizes["ping_time"])
        if n_pings == 0:
            continue

        depth_vals = _get_ping_depth_axis(ds_ch)
        valid = ~np.isnan(depth_vals)
        depth_valid = depth_vals[valid]
        if depth_valid.size < 2:
            logger.info("Combined: %s has fewer than 2 valid depth samples — skipping", mode)
            continue

        # Interpolate every ping onto the common depth grid
        sv_raw = sv_da.values  # (ping_time, range_sample)
        sv_interp = np.full((n_pings, common_depth.size), np.nan, dtype=np.float32)
        for i in range(n_pings):
            row = sv_raw[i, valid]
            m = ~np.isnan(row)
            if m.sum() > 1:
                sv_interp[i] = np.interp(
                    common_depth, depth_valid[m], row[m],
                    left=np.nan, right=np.nan,
                )

        mode_code = 0 if mode == "long_pulse" else 1
        ds_new = xr.Dataset(
            {
                "Sv": (["channel", "ping_time", "depth"], sv_interp[np.newaxis, :, :]),
                "pulse_mode": (["ping_time"], np.full(n_pings, mode_code, dtype=np.int8)),
            },
            coords={
                "ping_time": sv_da["ping_time"].values,
                "depth": common_depth,
                "channel": ["38kHz"],
            },
        )
        # Copy GPS if available (per-ping variables)
        for gps_var in ("latitude", "longitude"):
            if gps_var in ds_ch.data_vars and "ping_time" in ds_ch[gps_var].dims:
                gps = ds_ch[gps_var]
                if "channel" in gps.dims:
                    gps = gps.isel(channel=0)
                ds_new[gps_var] = gps

        mode_datasets.append(ds_new)
        modes_present.append(mode)

    # Cleanup zarrs we opened
    for ds in opened_paths:
        try:
            ds.close()
        except Exception:
            pass

    if not mode_datasets:
        return None

    combined: xr.Dataset
    if len(mode_datasets) == 1:
        combined = mode_datasets[0]
    else:
        combined = xr.concat(mode_datasets, dim="ping_time")
    combined = combined.sortby("ping_time")

    # Deduplicate ping_time (EK80 can log identical timestamps across modes
    # in rare cases; downstream renderers need unique index).
    idx = combined.get_index("ping_time")
    if idx.duplicated().any():
        combined = combined.sel(ping_time=~idx.duplicated())

    combined.attrs["combined_pulse_modes"] = "+".join(modes_present)
    combined.attrs["depth_grid"] = f"interpolated at {COMMON_DEPTH_STEP}m spacing"
    combined.attrs["depth_units"] = "metres"

    return CombinedDataset(
        dataset=combined,
        n_pings=int(combined.sizes["ping_time"]),
        pulse_modes_present=modes_present,
    )


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _build_hourly_ticks(x_hours: np.ndarray) -> tuple[list[float], list[str]]:
    h_min = int(np.floor(x_hours[0]))
    h_max = int(np.ceil(x_hours[-1]))
    ticks = [float(h) for h in range(h_min, h_max + 1)]
    labels = [f"{int(h) % 24:02d}:00" for h in ticks]
    return ticks, labels


def _draw_pulse_axis(ax_pulse, pulse_mode: np.ndarray, x_hours: np.ndarray) -> None:
    """Draw pulse-mode colour bar (blue = long, orange = short)."""
    from matplotlib.patches import Rectangle

    x_min, x_max = float(x_hours[0]), float(x_hours[-1])
    total_span = max(x_max - x_min, 1e-6)

    changes = np.where(np.diff(pulse_mode))[0] + 1
    starts = np.concatenate([[0], changes])
    ends = np.concatenate([changes, [len(pulse_mode)]])

    drawn: set[int] = set()
    for s, e in zip(starts, ends):
        mode = int(pulse_mode[s])
        x0 = float(x_hours[s])
        x1 = float(x_hours[min(e, len(x_hours)) - 1])
        w = x1 - x0
        rect = Rectangle(
            (x0, 0), w, 1,
            facecolor=_PULSE_COLORS[mode], alpha=0.85, edgecolor="none",
            label=_PULSE_LABELS[mode] if mode not in drawn else None,
        )
        ax_pulse.add_patch(rect)
        if w > total_span * 0.008:
            ax_pulse.text(
                x0 + w / 2, 0.5, _PULSE_LABELS[mode],
                ha="center", va="center", fontsize=7,
                fontweight="bold", color="white",
            )
        drawn.add(mode)

    ax_pulse.set_xlim(x_min, x_max)
    ax_pulse.set_ylim(0, 1)
    ax_pulse.set_yticks([])
    ax_pulse.set_ylabel("Pulse", fontsize=9, rotation=0, labelpad=30, va="center")
    ax_pulse.legend(
        loc="center left", bbox_to_anchor=(1.001, 0.5),
        fontsize=8, framealpha=0.9, handlelength=1.2,
    )


def render_combined_echogram(
    combined: CombinedDataset,
    *,
    day_key: str,
    variant_label: str,
    cmap_name: str,
    cmap: Union[str, "mcolors.Colormap"],
    output_dir: Union[str, Path],
    file_base_name: Optional[str] = None,
    qc_windows: Optional[list] = None,
    max_plot_depth: float = MAX_PLOT_DEPTH,
    vmin: float = SV_VMIN,
    vmax: float = SV_VMAX,
    dpi: int = 150,
    pings_per_pixel: float = PINGS_PER_PIXEL,
    max_width_in: float = MAX_WIDTH_IN,
) -> Optional[Path]:
    """Render a combined 24h echogram for the 38 kHz channel.

    The layout is a main echogram panel with a thin pulse-mode indicator
    bar underneath and a shared time axis. QC overlays (if any) are drawn
    on the main panel.

    Parameters
    ----------
    combined : CombinedDataset
        Output of :func:`combine_38khz_day`.
    day_key : str
        Day identifier (e.g. ``"2023-10-09"``) used in the title.
    variant_label : str
        Short label describing the input variant (e.g. ``"denoised"``,
        ``"denoised-pruned"``, ``"MVBS"``). Included in the title and
        the output filename.
    cmap_name : str
        Colormap name for the filename suffix (already sanitised).
    cmap : str or Colormap
        Colormap value passed to ``pcolormesh``.
    output_dir : Path
        Directory to save the PNG.
    file_base_name : str, optional
        Base filename (without ``.png`` extension). Defaults to
        ``"{day_key}--combined-38kHz--{variant_label}--{cmap_name}"``.
    qc_windows : list, optional
        Pre-filtered ``QCWindow`` objects to overlay on the main panel.
    max_plot_depth : float
        Trim depth axis at this value (metres).
    pings_per_pixel : float
        Target pings per horizontal pixel. The canvas is widened until this
        density is met, so a 29 000-ping Sv panel gets a much wider figure
        than an 8 500-bin MVBS one.
    max_width_in : float
        Upper bound on figure width (inches).

    Returns
    -------
    Path or None
        Path to the saved PNG, or ``None`` if the dataset was empty.
    """
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.gridspec import GridSpec

    ds = combined.dataset
    if "Sv" not in ds or ds.sizes.get("ping_time", 0) == 0:
        return None

    # Extract Sv (channel, ping_time, depth) → (ping_time, depth)
    sv_da = ds["Sv"]
    if "channel" in sv_da.dims:
        sv_da = sv_da.isel(channel=0)

    depth_vals = ds["depth"].values
    depth_mask = depth_vals <= max_plot_depth
    sv_arr = sv_da.values[:, depth_mask]  # (ping_time, depth)
    depth_plot = depth_vals[depth_mask]

    ping_time = ds["ping_time"].values
    n_pings = len(ping_time)
    if n_pings == 0:
        return None

    # Time-proportional x-axis: hours since midnight UTC of day_key
    try:
        import pandas as pd

        day_start = pd.Timestamp(day_key)
    except Exception:
        day_start = np.datetime64(day_key, "D")
    x_hours = (
        (ping_time - np.datetime64(day_start, "s"))
        .astype("timedelta64[s]")
        .astype(float)
        / 3600.0
    )

    pulse_mode = ds["pulse_mode"].values if "pulse_mode" in ds else None
    if pulse_mode is not None and pulse_mode.dtype.kind == "f":
        pulse_mode = np.where(np.isnan(pulse_mode), -1, pulse_mode).astype(np.int8)

    # Figure sizing — time-proportional width is the floor, then widened so the
    # rasteriser resolves individual pings instead of aliasing them together.
    time_span = float(x_hours[-1] - x_hours[0])
    width = float(
        min(
            max_width_in,
            max(14.0, time_span * 1.2, n_pings / max(pings_per_pixel * dpi, 1.0)),
        )
    )
    height = 7.0

    has_pulse = pulse_mode is not None
    cbar_frac = 0.3 / width

    if has_pulse:
        fig = plt.figure(figsize=(width, height))
        gs = GridSpec(
            2, 2, figure=fig,
            height_ratios=[40, 1], width_ratios=[1 - cbar_frac, cbar_frac],
            hspace=0.03, wspace=0.005,
        )
        ax = fig.add_subplot(gs[0, 0])
        ax_pulse = fig.add_subplot(gs[1, 0], sharex=ax)
        cax = fig.add_subplot(gs[0, 1])
    else:
        fig, ax = plt.subplots(figsize=(width, height))
        ax_pulse = None
        cax = None

    im = ax.pcolormesh(
        x_hours, depth_plot, sv_arr.T,
        shading="auto", cmap=cmap, vmin=vmin, vmax=vmax, rasterized=True,
    )
    ax.invert_yaxis()
    ax.set_ylabel("Depth [m]", fontsize=13)
    ax.set_facecolor("#f9f9f9")

    modes_str = " + ".join(combined.pulse_modes_present)
    ax.set_title(
        f"{day_key} — 38 kHz {variant_label} (combined: {modes_str}) | cmap: {cmap_name}",
        fontsize=14, fontweight="bold",
        # Extra pad so QC flag captions (drawn just above the axes at
        # y=1.0 + 4pt) don't overlap the title.
        pad=32,
    )

    # QC overlay uses datetime x-axis; here the axis is hours-since-midnight,
    # so we convert QC window start/end to the same units.
    if qc_windows:
        _draw_qc_overlay_hours(
            ax, qc_windows,
            day_start_dt=day_start,
            x_min=float(x_hours[0]), x_max=float(x_hours[-1]),
        )

    # Time ticks
    major_ticks, major_labels = _build_hourly_ticks(x_hours)
    tick_ax = ax_pulse if has_pulse else ax
    tick_ax.set_xticks(major_ticks)
    tick_ax.set_xticklabels(major_labels, rotation=45, ha="right", fontsize=9)
    tick_ax.set_xlabel("Time (UTC)", fontsize=12)
    ax.set_xlim(float(x_hours[0]) - 0.1, float(x_hours[-1]) + 0.1)

    if has_pulse:
        ax.tick_params(axis="x", labelbottom=False, which="both")
        _draw_pulse_axis(ax_pulse, pulse_mode, x_hours)
        cbar = fig.colorbar(im, cax=cax)
    else:
        cbar = fig.colorbar(im, ax=ax, fraction=0.015, pad=0.01)
    cbar.set_label("Sv (dB re 1 m⁻¹)", fontsize=11)

    if file_base_name is None:
        file_base_name = (
            f"{day_key}--combined-38kHz--{variant_label}--{cmap_name}"
        )

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / f"{file_base_name}.png"
    fig.savefig(out_path, dpi=dpi, bbox_inches="tight")
    plt.close(fig)
    return out_path


def _draw_qc_overlay_hours(
    ax,
    windows: list,
    *,
    day_start_dt,
    x_min: float,
    x_max: float,
) -> None:
    """QC overlay for the hours-since-midnight axis used in combined plots.

    The generic :func:`oceanstream.echodata.plot.qc.draw_qc_overlay` expects a
    datetime x-axis; the combined echogram uses hours-since-midnight instead
    so QC bounds must be converted.
    """
    import pandas as pd

    from oceanstream.echodata.plot.qc import TYPE_COLORS

    day_start = pd.Timestamp(day_start_dt)

    for w in windows:
        start_h = (w.start_utc - day_start).total_seconds() / 3600.0
        end_h = (w.end_utc - day_start).total_seconds() / 3600.0
        # Clip to visible range; skip windows fully outside
        if end_h < x_min or start_h > x_max:
            continue
        s = max(start_h, x_min)
        e = min(end_h, x_max)

        color = TYPE_COLORS.get(w.type, TYPE_COLORS["flagged"])
        ax.axvspan(s, e, facecolor=color, alpha=0.18, zorder=4, edgecolor="none")
        ax.axvline(start_h, color=color, lw=1.4, ls="--", alpha=0.85, zorder=5)
        ax.axvline(end_h, color=color, lw=1.4, ls="--", alpha=0.85, zorder=5)

        label = w.type.replace("_", " ")
        if w.cause:
            label = f"{label} — {w.cause.replace('_', ' ')}"
        mid = s + (e - s) / 2
        ax.annotate(
            label,
            xy=(mid, 1.0), xycoords=("data", "axes fraction"),
            xytext=(0, 4), textcoords="offset points",
            ha="center", va="bottom",
            fontsize=10, fontweight="bold", color=color,
            bbox=dict(
                boxstyle="round,pad=0.3",
                facecolor="white", edgecolor=color,
                linewidth=1.1, alpha=0.92,
            ),
            zorder=6,
        )
