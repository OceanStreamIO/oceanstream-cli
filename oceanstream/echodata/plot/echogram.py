"""Echogram plotting for Sv datasets.

Provides functions for generating publication-quality echogram plots
from acoustic backscatter (Sv) data.

Ported from _echodata-legacy-code/saildrone-echodata-processing/process/plot.py
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Optional, Union

import numpy as np

from oceanstream.echodata.products import open_product_uri

if TYPE_CHECKING:
    import matplotlib.pyplot as plt
    import xarray as xr

logger = logging.getLogger(__name__)


SQUEEZE_GAPS_S = 600.0
"""Recording gaps longer than this many seconds are drawn as a narrow hatched band."""

ECHOGRAM_FACECOLOR = "#f9f9f9"
"""Behind the data: what a cell without a value shows."""


@dataclass
class TimeAxis:
    """Ping time to x, in seconds along the drawn axis, with long recording gaps squeezed.

    *runs* hold ``(t0_ns, t1_ns, x0, x1)`` for each stretch drawn to scale, *gaps* the
    same for each squeezed gap between two runs, whose band is narrower than the gap.
    Without squeezing there is one run and x is seconds since the first ping.
    """

    runs: list[tuple[int, int, float, float]]
    gaps: list[tuple[int, int, float, float]] = field(default_factory=list)

    @property
    def span_s(self) -> float:
        return max(piece[3] for piece in self.runs + self.gaps)

    def _knots(self) -> tuple[np.ndarray, np.ndarray]:
        pieces = sorted(self.runs + self.gaps, key=lambda piece: (piece[0], piece[2]))
        t = np.array([v for piece in pieces for v in piece[:2]], dtype=float)
        x = np.array([v for piece in pieces for v in piece[2:]], dtype=float)
        return t, x

    def to_x(self, times: object) -> np.ndarray:
        """x for each time; inside a squeezed gap, x moves evenly across its band."""
        t = np.asarray(times, dtype="datetime64[ns]").astype(np.int64).astype(float)
        kt, kx = self._knots()
        return np.interp(t, kt, kx)

    def to_time(self, x: object) -> np.ndarray:
        kt, kx = self._knots()
        t = np.interp(np.asarray(x, dtype=float), kx, kt)
        return t.astype(np.int64).astype("datetime64[ns]")


def time_axis(
    times: object,
    squeeze_s: Optional[float] = SQUEEZE_GAPS_S,
    band_frac: float = 0.015,
    min_band_s: float = 60.0,
) -> TimeAxis:
    """The x-axis for pings at *times*: gaps longer than *squeeze_s* drawn narrow.

    Each squeezed gap gets a band of *band_frac* of the recorded time (at least
    *min_band_s*, never more than the gap). On NF2104 2021-06-17 the echosounder
    recorded 10.5 hours in three stretches, and the 8.5 hours between them took 37 %
    of the panel's width as blank space. With *squeeze_s* None nothing is squeezed.
    """
    t = np.sort(np.asarray(times, dtype="datetime64[ns]").astype(np.int64))
    if t.size == 0:
        return TimeAxis(runs=[(0, 0, 0.0, 0.0)])
    steps = np.diff(t)
    breaks = (
        np.flatnonzero(steps > squeeze_s * 1e9) if squeeze_s is not None else np.array([], int)
    )
    starts = np.r_[0, breaks + 1]
    ends = np.r_[breaks, t.size - 1]
    recorded_s = float(sum(t[e] - t[s] for s, e in zip(starts, ends))) / 1e9
    band_s = max(min_band_s, band_frac * recorded_s)
    runs: list[tuple[int, int, float, float]] = []
    gaps: list[tuple[int, int, float, float]] = []
    x = 0.0
    for k, (s, e) in enumerate(zip(starts, ends)):
        length = float(t[e] - t[s]) / 1e9
        runs.append((int(t[s]), int(t[e]), x, x + length))
        x += length
        if k < breaks.size:
            g0, g1 = int(t[e]), int(t[starts[k + 1]])
            width = min(band_s, (g1 - g0) / 1e9)
            gaps.append((g0, g1, x, x + width))
            x += width
    return TimeAxis(runs=runs, gaps=gaps)


def time_ticks(axis: TimeAxis, max_ticks: int = 16) -> tuple[list[float], list[str]]:
    """Tick positions and HH:MM labels at round times inside the drawn runs."""
    import pandas as pd

    span = max(axis.span_s, 1.0)
    step_min = next(
        (m for m in (1, 2, 5, 10, 15, 30, 60, 120, 180, 240, 360) if span / (m * 60) <= max_ticks),
        720,
    )
    step = int(step_min * 60 * 1e9)
    xs: list[float] = []
    labels: list[str] = []
    for t0, t1, _, _ in axis.runs:
        for tt in range(-(-t0 // step) * step, t1 + 1, step):
            x = float(axis.to_x(np.array([tt], dtype="datetime64[ns]"))[0])
            if xs and x - xs[-1] < 0.5 * step_min * 60:
                continue  # just after a squeezed gap, too close to the last tick
            xs.append(x)
            labels.append(pd.Timestamp(tt).strftime("%H:%M"))
    return xs, labels


def _gap_label(seconds: float) -> str:
    hours, rest = divmod(int(round(seconds / 60)), 60)
    return f"no data {hours} h {rest:02d} min" if hours else f"no data {rest} min"


def draw_gaps(ax: "plt.Axes", axis: TimeAxis) -> None:
    """Hatch each squeezed gap's band and write how long the gap was."""
    from matplotlib.transforms import blended_transform_factory

    trans = blended_transform_factory(ax.transData, ax.transAxes)
    for g0, g1, x0, x1 in axis.gaps:
        ax.axvspan(x0, x1, facecolor="#dddddd", edgecolor="#777777", hatch="///",
                   linewidth=0, zorder=3)
        for x in (x0, x1):
            ax.axvline(x, color="#444444", lw=0.9, zorder=3)
        ax.text((x0 + x1) / 2, 0.5, _gap_label((g1 - g0) / 1e9), transform=trans,
                rotation=90, ha="center", va="center", fontsize=13, color="#222222",
                bbox=dict(boxstyle="round,pad=0.25", facecolor="white", edgecolor="none",
                          alpha=0.9), zorder=6)


def transducer_offset(ds: "xr.Dataset") -> float:
    """Depth minus range at the first sample: the transducer's depth below the surface.

    The seabed line is stored as range below the transducer; echograms plot depth.
    0 when the dataset has no depth or no range.
    """
    if not {"depth", "echo_range"} <= set(getattr(ds, "data_vars", {})):
        return 0.0
    depth, rng = ds["depth"], ds["echo_range"]
    for name in ("channel",):
        if name in depth.dims:
            depth, rng = depth.isel({name: 0}), rng.isel({name: 0})
    vertical = next((d for d in depth.dims if d != "ping_time"), None)
    if vertical is None:
        return 0.0
    sel: dict = {vertical: slice(0, 4)}
    if "ping_time" in depth.dims:
        step = max(1, depth.sizes["ping_time"] // 64)
        sel["ping_time"] = slice(None, None, step)
    diff = (np.asarray(depth.isel(sel).values, dtype=float)
            - np.asarray(rng.isel(sel).values, dtype=float))
    return float(np.nanmedian(diff)) if np.isfinite(diff).any() else 0.0


def draw_seabed(ax: "plt.Axes", seabed_depth: "xr.DataArray", axis: TimeAxis) -> bool:
    """Draw the seabed line (depth, per ping) as a black line with a white edge.

    Black with a white edge stands out by lightness on every part of the colour scale,
    not by hue. The line breaks where pings are more than a minute apart.
    """
    import matplotlib.patheffects as pe

    times = np.asarray(seabed_depth["ping_time"].values, dtype="datetime64[ns]")
    depth = np.asarray(seabed_depth.values, dtype=float)
    if not np.isfinite(depth).any():
        return False
    step = max(1, times.size // 20000)
    times, depth = times[::step], depth[::step]
    apart = np.flatnonzero(np.diff(times.astype(np.int64)) > 60e9)
    x = np.insert(axis.to_x(times), apart + 1, np.nan)
    y = np.insert(depth, apart + 1, np.nan)
    ax.plot(x, y, color="black", lw=1.4, zorder=4, label="Seabed line",
            path_effects=[pe.Stroke(linewidth=3.6, foreground="white"), pe.Normal()])
    return True


def write_echogram_sidecar(
    png_path: Path,
    ax: "plt.Axes",
    axis: TimeAxis,
    *,
    vertical: str,
    cmap: object,
    vmin: float,
    vmax: float,
    variable: str,
    seabed_line: bool,
) -> Path:
    """Write ``<png>.json``: where the plot sits in the PNG and what its axes and colours mean.

    A viewer maps a pixel to a time through ``time.runs`` and ``time.gaps`` (the x-axis is
    not linear in time once gaps are squeezed), to depth linearly between
    ``vertical.top`` and ``vertical.bottom``, and to Sv by finding the pixel's colour in
    ``colour.lut``: entry *i* of *N* holds values from ``vmin + i * (vmax - vmin) / N``,
    the first also everything below *vmin* and the last everything above *vmax*.
    """
    import matplotlib as mpl
    from PIL import Image

    with Image.open(png_path) as im:
        width, height = im.size
    pos = ax.get_position()
    left, right = pos.x0 * width, pos.x1 * width
    top, bottom = (1 - pos.y1) * height, (1 - pos.y0) * height
    x_lo, x_hi = ax.get_xlim()

    def px(x: float) -> float:
        return round(left + (x - x_lo) / (x_hi - x_lo) * (right - left), 2)

    def iso(ns: int) -> str:
        return str(np.datetime_as_string(np.datetime64(int(ns), "ns"), unit="ms")) + "Z"

    y_bottom, y_top = ax.get_ylim()
    cm = mpl.colormaps[cmap] if isinstance(cmap, str) else cmap
    lut = np.round(np.asarray(cm(np.arange(cm.N)))[:, :3] * 255).astype(int)
    doc = {
        "format": "oceanstream-echogram",
        "version": 1,
        "image": {"width": width, "height": height},
        "plot_area": {"left": round(left, 2), "top": round(top, 2),
                      "right": round(right, 2), "bottom": round(bottom, 2)},
        "time": {
            "runs": [{"x0": px(x0), "x1": px(x1), "t0": iso(t0), "t1": iso(t1)}
                     for t0, t1, x0, x1 in axis.runs],
            "gaps": [{"x0": px(x0), "x1": px(x1), "t0": iso(t0), "t1": iso(t1)}
                     for t0, t1, x0, x1 in axis.gaps],
        },
        "vertical": {"name": vertical, "units": "m", "top": float(y_top),
                     "bottom": float(y_bottom)},
        "colour": {
            "variable": variable,
            "units": "dB re 1 m-1",
            "vmin": float(vmin),
            "vmax": float(vmax),
            "colormap": getattr(cm, "name", str(cmap)),
            "lut": [f"#{r:02x}{g:02x}{b:02x}" for r, g, b in lut],
            "background": ECHOGRAM_FACECOLOR,
        },
        "seabed_line": bool(seabed_line),
    }
    out = Path(png_path).with_suffix(".json")
    out.write_text(json.dumps(doc, separators=(",", ":")))
    return out


def _scratch_root() -> str:
    """Where echograms are rendered before upload: $TMPDIR, else /tmp.

    A Slurm job sets TMPDIR to its own directory; node /tmp can be cleared
    when another job on the node ends, mid-render.
    """
    return os.path.join(tempfile.gettempdir(), "osechograms")


def generate_echograms(
    sv_dataset: Union[Path, "xr.Dataset"],
    output_dir: Path,
    file_base_name: Optional[str] = None,
    channels: Optional[list[int]] = None,
    cmap: str = "ocean_r",
    vmin: float = -80,
    vmax: float = -50,
    dpi: int = 180,
) -> list[Path]:
    """
    Generate echogram PNG files for all channels in an Sv dataset.
    
    This is the main entry point for batch echogram generation.
    
    Args:
        sv_dataset: Sv xarray Dataset or path to Zarr
        output_dir: Directory to save echogram PNGs
        file_base_name: Base name for output files (default: from dataset attrs)
        channels: List of channel indices to plot (default: all)
        cmap: Matplotlib colormap name
        vmin: Minimum Sv value for color scale (dB)
        vmax: Maximum Sv value for color scale (dB)
        dpi: Output image resolution
        
    Returns:
        List of paths to generated echogram files
        
    Example:
        echograms = generate_echograms(sv_path, output_dir=Path("./echograms"))
    """
    
    # Load if path
    if isinstance(sv_dataset, (str, Path)):
        logger.info(f"Loading Sv from {sv_dataset}")
        sv_dataset = open_product_uri(sv_dataset)
    
    # Ensure channel labels exist
    sv_dataset = ensure_channel_labels(sv_dataset)
    
    # Determine base name
    if file_base_name is None:
        file_base_name = sv_dataset.attrs.get("file_name", "echogram")
    
    # Create output directory
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Determine channels to plot
    n_channels = sv_dataset.dims.get("channel", 1)
    if channels is None:
        channels = list(range(n_channels))
    
    # Generate echograms
    echogram_files = []
    for ch in channels:
        try:
            out_path = plot_sv_channel(
                sv_dataset,
                channel=ch,
                file_base_name=file_base_name,
                echogram_path=str(output_dir),
                cmap=cmap,
                vmin=vmin,
                vmax=vmax,
                dpi=dpi,
            )
            echogram_files.append(out_path)
            logger.info(f"Generated echogram: {out_path}")
        except Exception as e:
            logger.error(f"Error plotting echogram for channel {ch}: {e}")
    
    return echogram_files


def plot_sv_data(
    ds_Sv: "xr.Dataset",
    file_base_name: str,
    output_path: str = ".",
    cmap: str = "ocean_r",
    channel: Optional[int] = None,
    colorbar_orientation: str = "vertical",
    plot_var: str = "Sv",
    title_template: str = "{channel_label}",
    vmin: float = -80,
    vmax: float = -50,
    dpi: int = 180,
    qc_windows: Optional[list] = None,
    seabed_depth: Optional["xr.DataArray"] = None,
    squeeze_gaps_s: Optional[float] = SQUEEZE_GAPS_S,
) -> list[Path]:
    """
    Plot Sv data for each channel and save the echogram plots.
    
    Args:
        ds_Sv: xarray Dataset containing Sv data
        file_base_name: Base name for output files
        output_path: Path to save the output plots
        cmap: Colormap for plotting
        channel: Specific channel to plot (default: all channels)
        colorbar_orientation: 'vertical' or 'horizontal'
        plot_var: Variable name to plot (default: 'Sv')
        title_template: Template for plot title
        vmin: Minimum Sv value for color scale
        vmax: Maximum Sv value for color scale
        dpi: Output image resolution
        seabed_depth: Seabed line as depth below the surface per ping, drawn on every
            panel whose vertical axis is depth (see ``plot_sv_channel``)
        squeeze_gaps_s: Recording gaps longer than this are drawn narrow; None keeps
            the time axis linear

    Returns:
        List of file paths for the saved echograms (each with a ``.json`` sidecar)
    """
    import matplotlib
    matplotlib.use('Agg')  # Non-interactive backend
    
    ds_Sv = ensure_channel_labels(ds_Sv)
    echogram_files = []
    
    if channel is not None:
        # Plot specific channel
        try:
            path = plot_sv_channel(
                ds_Sv,
                channel=channel,
                file_base_name=file_base_name,
                echogram_path=output_path,
                colorbar_orientation=colorbar_orientation,
                cmap=cmap,
                plot_var=plot_var,
                title_template=title_template,
                vmin=vmin,
                vmax=vmax,
                dpi=dpi,
                qc_windows=qc_windows,
                seabed_depth=seabed_depth,
                squeeze_gaps_s=squeeze_gaps_s,
            )
            echogram_files.append(path)
        except Exception as e:
            logger.error(f"Error plotting echogram for channel {channel}: {e}")
    else:
        # Plot all channels
        n_ch = ds_Sv.dims.get("channel", 1)
        for ch in range(n_ch):
            try:
                path = plot_sv_channel(
                    ds_Sv,
                    channel=ch,
                    file_base_name=file_base_name,
                    echogram_path=output_path,
                    colorbar_orientation=colorbar_orientation,
                    cmap=cmap,
                    plot_var=plot_var,
                    title_template=title_template,
                    vmin=vmin,
                    vmax=vmax,
                    dpi=dpi,
                    qc_windows=qc_windows,
                    seabed_depth=seabed_depth,
                    squeeze_gaps_s=squeeze_gaps_s,
                )
                echogram_files.append(path)
            except Exception as e:
                logger.error(f"Error plotting echogram for channel {ch}: {e}")
    
    return echogram_files


def plot_sv_channel(
    ds_Sv: "xr.Dataset",
    channel: int,
    file_base_name: str,
    echogram_path: str = ".",
    cmap: str = "ocean_r",
    colorbar_orientation: str = "vertical",
    plot_var: str = "Sv",
    title_template: str = "{channel_label}",
    vmin: float = -80,
    vmax: float = -50,
    hour_grid: bool = False,
    inches_per_hour: float = 1.0,
    min_width: float = 14.0,
    max_width: float = 34.0,
    dpi: int = 180,
    height_in: float = 12.0,
    min_aspect_short: float = 1.8,
    target_aspect_24h: float = 3.2,
    qc_windows: Optional[list] = None,
    seabed_depth: Optional["xr.DataArray"] = None,
    squeeze_gaps_s: Optional[float] = SQUEEZE_GAPS_S,
    sidecar: bool = True,
) -> Path:
    """
    Plot and save echogram for a single channel.

    Args:
        ds_Sv: xarray Dataset containing Sv data
        channel: Channel index to plot
        file_base_name: Base name for output file
        echogram_path: Directory to save the echogram
        cmap: Colormap for plotting
        colorbar_orientation: 'vertical' or 'horizontal'
        plot_var: Variable name to plot
        title_template: Template for plot title (use {channel_label})
        vmin: Minimum Sv value for color scale (dB)
        vmax: Maximum Sv value for color scale (dB)
        hour_grid: Add vertical lines at hour boundaries
        inches_per_hour: Figure width scaling
        min_width: Minimum figure width
        max_width: Maximum figure width
        dpi: Output image resolution
        height_in: Base figure height (inches)
        min_aspect_short: Aspect ratio for short time series
        target_aspect_24h: Aspect ratio for 24h time series
        qc_windows: Optional list of QCWindow objects to overlay on the plot
            (already filtered for this channel/category). See
            ``oceanstream.echodata.plot.qc`` for schema and helpers.
        seabed_depth: Seabed line as depth below the surface, per ping. Drawn when the
            panel's vertical axis is depth; the stored line is range below the
            transducer, so add :func:`transducer_offset` first.
        squeeze_gaps_s: Recording gaps longer than this many seconds are drawn as a
            narrow hatched band labelled with their length (:func:`time_axis`); None
            keeps the time axis linear.
        sidecar: Also write ``<png>.json`` (:func:`write_echogram_sidecar`), which a
            viewer needs to read times, depths and Sv off the image.

    Returns:
        Path to the saved echogram file
    """
    import matplotlib.pyplot as plt
    import matplotlib.ticker as mticker
    import pandas as pd

    da_Sv, meta = prepare_channel_da(ds_Sv, channel, var_name=plot_var)

    # The x-axis: seconds along the drawn axis, long recording gaps squeezed.
    xdim = meta["xdim"]
    recorded = da_Sv[xdim].values
    if xdim in da_Sv.dims and da_Sv.sizes[xdim]:
        other = [d for d in da_Sv.dims if d != xdim]
        recorded = da_Sv[xdim].values[np.isfinite(da_Sv.values).any(
            axis=tuple(i for i, d in enumerate(da_Sv.dims) if d in other))]
        if recorded.size == 0:
            recorded = da_Sv[xdim].values
    axis = time_axis(recorded, squeeze_gaps_s)
    da_Sv = da_Sv.assign_coords(plot_x=(xdim, axis.to_x(da_Sv[xdim].values)))

    hours = max(1.0, axis.span_s / 3600.0)
    time_width = hours * inches_per_hour
    if hours <= 1.0:
        aspect_target = min_aspect_short
    elif hours >= 24.0:
        aspect_target = target_aspect_24h
    else:
        w0, w1 = min_aspect_short, target_aspect_24h
        aspect_target = w0 + (w1 - w0) * ((hours - 1.0) / (24.0 - 1.0))

    width_in = max(time_width, min_width, aspect_target * height_in)
    width_in = min(width_in, max_width)

    fig, ax = plt.subplots(figsize=(width_in, height_in))

    da_Sv.T.plot.pcolormesh(
        x="plot_x",
        y=meta["ydim"],
        shading="auto",
        yincrease=False,
        vmin=vmin,
        vmax=vmax,
        cmap=cmap,
        add_colorbar=False,
        ylim=(meta["bot"], meta["top"]),
        ax=ax,
        rasterized=True,
    )
    mesh = ax.collections[0]
    ax.set_xlim(0.0, max(axis.span_s, 1.0))
    draw_gaps(ax, axis)

    if hour_grid:
        for t0, t1, _, _ in axis.runs:
            for hour in pd.date_range(pd.Timestamp(t0).ceil("1h"), pd.Timestamp(t1), freq="1h"):
                ax.axvline(float(axis.to_x(np.array([hour.value], "datetime64[ns]"))[0]),
                           color="k", lw=0.6, alpha=0.18, zorder=3)

    if qc_windows and xdim == "ping_time":
        try:
            from oceanstream.echodata.plot.qc import draw_qc_overlay

            first, last = pd.Timestamp(recorded.min()), pd.Timestamp(recorded.max())
            n = draw_qc_overlay(
                ax, qc_windows, ping_time_min=first, ping_time_max=last,
                to_x=lambda when: float(axis.to_x(np.array([pd.Timestamp(when).value],
                                                           "datetime64[ns]"))[0]),
            )
            if n > 0:
                logger.info("Drew %d QC flag(s) on echogram", n)
        except Exception as exc:
            logger.warning("QC overlay failed: %s", exc)

    seabed_drawn = False
    if seabed_depth is not None and meta["ydim"] == "depth":
        try:
            seabed_drawn = draw_seabed(ax, seabed_depth, axis)
        except Exception as exc:
            logger.warning("Seabed line overlay failed: %s", exc)
    if seabed_drawn:
        ax.legend(loc="lower right", fontsize=13, framealpha=0.92)

    # Styling
    ax.set_facecolor(ECHOGRAM_FACECOLOR)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_linewidth(1.0)

    ticks, labels = time_ticks(axis)
    ax.xaxis.set_major_locator(mticker.FixedLocator(ticks))
    ax.xaxis.set_major_formatter(mticker.FixedFormatter(labels))

    cbar_kw = dict(pad=0.08, shrink=0.8)
    if colorbar_orientation == "horizontal":
        cbar_kw.update(orientation="horizontal", aspect=40)
    else:
        cbar_kw.update(fraction=0.04, pad=0.02)
    cbar = plt.colorbar(mesh, **cbar_kw)
    cbar.set_label("Volume backscattering strength (Sv re 1 m⁻¹)", fontsize=12)
    cbar.ax.tick_params(labelsize=10)

    x_label = meta["x_label"]
    if axis.gaps:
        minutes = int(round((squeeze_gaps_s or 0) / 60))
        x_label += f" — gaps of more than {minutes} min drawn narrow"
    ax.set_xlabel(x_label, fontsize=16, labelpad=14)
    ax.set_ylabel(
        "Depth [m]" if meta["ydim"] != "range_sample" else "Sample #",
        fontsize=16,
        labelpad=14,
    )
    ax.set_title(
        title_template.format(channel_label=meta["ch_label"]),
        fontsize=18,
        fontweight="bold",
        # Extra pad so QC flag captions (drawn just above the axes at
        # y=1.0 + 4pt) don't overlap the title text.
        pad=36,
    )
    ax.tick_params(which="major", length=6, width=1, labelsize=11)
    plt.setp(ax.get_xticklabels(), rotation=30, ha="right")

    fig.tight_layout(pad=2)

    out_path = Path(echogram_path) / f"{file_base_name}_{meta['safe_label']}.png"
    fig.savefig(out_path, dpi=dpi)
    if sidecar:
        try:
            write_echogram_sidecar(
                out_path, ax, axis, vertical=meta["ydim"], cmap=cmap, vmin=vmin,
                vmax=vmax, variable=plot_var, seabed_line=seabed_drawn,
            )
        except Exception as exc:
            logger.warning("Echogram sidecar failed for %s: %s", out_path.name, exc)
    plt.close(fig)

    return out_path


# Alias for backward compatibility
plot_echogram = plot_sv_channel


def break_at_gaps(
    da: "xr.DataArray", xdim: str = "ping_time", factor: float = 5.0
) -> "xr.DataArray":
    """Insert empty pings at the edges of every long gap in *xdim*.

    pcolormesh draws each ping up to the midpoint of its neighbour, so a ping before
    a gap of hours is stretched across half of it, and one ping's profile shows up
    as horizontal bands. Gaps longer than *factor* times the median ping interval get
    an empty ping one interval after the last ping and one before the next.
    """
    import xarray as xr

    if xdim not in da.dims or da.sizes[xdim] < 3:  # noqa: PLR2004
        return da
    times = da[xdim].values
    steps = np.diff(times)
    typical = np.median(steps)
    gaps = np.flatnonzero(steps > factor * typical)
    if gaps.size == 0:
        return da
    after = xr.full_like(da.isel({xdim: gaps}), np.nan).assign_coords(
        {xdim: times[gaps] + typical}
    )
    before = xr.full_like(da.isel({xdim: gaps + 1}), np.nan).assign_coords(
        {xdim: times[gaps + 1] - typical}
    )
    return xr.concat([da, after, before], dim=xdim).sortby(xdim)


def prepare_channel_da(
    ds: Union["xr.Dataset", "xr.DataArray"],
    channel: Optional[int],
    var_name: str = "Sv",
    depth_fallback: str = "depth",
) -> tuple["xr.DataArray", dict]:
    """
    Extract a single-channel DataArray and return it ready for plotting.
    
    Args:
        ds: echopype Dataset or DataArray containing var_name
        channel: Channel index to plot (ignored if ds has no 'channel' dim)
        var_name: Variable to extract (e.g., 'Sv', 'mask')
        depth_fallback: Use this name if neither 'depth' nor 'echo_range' exists
        
    Returns:
        Tuple of (cleaned DataArray, plotting metadata dict)
    """
    import xarray as xr
    import matplotlib.dates as mdates
    
    # Pick the DataArray
    if isinstance(ds, xr.Dataset):
        da = ds[var_name]
    else:
        da = ds
    
    # Slice by channel index
    if "channel" in da.dims:
        da = da.isel(channel=channel)
    
    # Get channel label
    if "channel_label" in da.coords:
        ch_label = str(da.coords["channel_label"].values)
        if hasattr(da.coords["channel_label"].values, "item"):
            ch_label = str(da.coords["channel_label"].values.item())
    elif "channel_label" in getattr(ds, "coords", {}):
        ch_label = str(ds["channel_label"].isel(channel=channel).values)
        if hasattr(ds["channel_label"].isel(channel=channel).values, "item"):
            ch_label = str(ds["channel_label"].isel(channel=channel).values.item())
    else:
        ch_label = f"Ch-{channel}"
    
    safe_label = ch_label.replace(" ", "-").replace("(", "").replace(")", "")
    
    # Promote a per-ping depth variable to a real vertical coordinate, so the
    # y-axis is metres rather than sample number.
    da = attach_depth_coord(ds, da, channel)

    # Choose vertical axis
    if "depth" in da.coords:
        ydim = "depth"
    elif "echo_range" in da.coords:
        ydim = "echo_range"
    else:
        ydim = depth_fallback
    
    # Ensure ydim exists in data
    if ydim not in da.dims and ydim not in da.coords:
        # Try to find any range-like dimension
        for dim in da.dims:
            if "range" in dim.lower() or "depth" in dim.lower():
                ydim = dim
                break
        else:
            ydim = da.dims[-1]  # Use last dimension as fallback
    
    # Tidy NaNs and sort
    if ydim in da.coords:
        valid = np.isfinite(da[ydim])
        da_clean = (
            da.isel({ydim: valid})
            .dropna(dim=ydim, how="all")
            .sortby(ydim)
        )
        
        # Compute depth limits
        try:
            top = float(da_clean[ydim].isel({ydim: 0}).values)
            bot = float(da_clean[ydim].isel({ydim: -1}).values)
        except Exception:
            top = 0.0
            bot = float(da_clean.sizes[ydim])
    else:
        da_clean = da.dropna(dim=ydim, how="all")
        top = 0.0
        bot = float(da_clean.sizes[ydim])

    # Plot the channel's own pings only (channels that ping in turn leave the others
    # empty), and leave recording gaps blank instead of stretching pings across them.
    if "ping_time" in da_clean.dims:
        da_clean = break_at_gaps(da_clean.dropna(dim="ping_time", how="all"))

    plotting_metadata = dict(
        ch_label=ch_label,
        safe_label=safe_label,
        xdim="ping_time",
        x_label="Ping time [UTC]",
        x_formatter=mdates.DateFormatter("%H:%M"),
        ydim=ydim,
        top=top,
        bot=bot,
    )
    
    return da_clean, plotting_metadata


def attach_depth_coord(
    ds: "xr.Dataset",
    da: "xr.DataArray",
    channel: Optional[int] = None,
    prefer: tuple[str, ...] = ("depth", "echo_range"),
) -> "xr.DataArray":
    """Give *da* a 1-D depth coordinate so echograms show metres, not samples.

    echopype stores ``depth`` and ``echo_range`` as
    ``(channel, ping_time, range_sample)`` data variables, so nothing
    downstream can use them as an axis and plots fall back to sample number.
    The profile is taken per channel because 38 and 200 kHz have different
    sample spacing, and as a median over pings because individual pings can
    carry NaNs.

    Returns *da* unchanged when no usable depth variable is present.
    """
    import xarray as xr

    if not isinstance(ds, xr.Dataset):
        return da

    for name in prefer:
        if name not in ds.data_vars:
            continue
        var = ds[name]
        vertical = next(
            (d for d in var.dims if d not in ("channel", "ping_time")), None
        )
        if vertical is None or vertical not in da.dims:
            continue

        profile = var
        if "channel" in profile.dims:
            profile = profile.isel(channel=channel or 0)
        if "ping_time" in profile.dims:
            # A handful of pings is plenty: transducer depth does not move.
            step = max(1, profile.sizes["ping_time"] // 64)
            profile = profile.isel(ping_time=slice(None, None, step)).median(
                dim="ping_time", skipna=True
            )

        values = np.asarray(profile.values, dtype=float)
        if values.ndim != 1 or values.size != da.sizes[vertical]:
            continue
        if not np.isfinite(values).any():
            continue

        return da.assign_coords({name: (vertical, values)}).swap_dims({vertical: name})

    return da


def ensure_channel_labels(
    ds: "xr.Dataset",
    chan_dim: str = "channel",
    label_coord: str = "channel_label",
    add_freq: bool = True,
) -> "xr.Dataset":
    """
    Ensure dataset has textual channel labels.
    
    Creates a 'channel_label' coordinate aligned with the channel dimension.
    Labels include frequency information if available.
    
    Args:
        ds: xarray Dataset with channel dimension
        chan_dim: Name of channel dimension
        label_coord: Name for the label coordinate to create
        add_freq: Whether to append frequency to labels
        
    Returns:
        Dataset with channel_label coordinate
    """
    # Already has labels
    if label_coord in ds.coords:
        return ds
    
    # Single-channel or no channel dimension
    if chan_dim not in ds.dims:
        return ds.assign_coords({label_coord: ("channel", ["single"])}) if "channel" in ds.dims else ds
    
    orig_names = [str(v) for v in ds.coords[chan_dim].values]
    
    # Try to get frequencies
    if "frequency_nominal" in ds:
        fn_hz = ds["frequency_nominal"].values
        if hasattr(fn_hz, "compute"):
            fn_hz = fn_hz.compute()
        # Coerce to 1-D list of scalars (frequency_nominal may have extra
        # dimensions after xr.concat, making each element an ndarray).
        fn_hz_scalar = []
        for v in fn_hz:
            if np.ndim(v) == 0:
                fn_hz_scalar.append(float(v))
            elif hasattr(v, "flat") and v.size > 0:
                fn_hz_scalar.append(float(v.flat[0]))
            else:
                fn_hz_scalar.append(np.nan)
        fn_hz = fn_hz_scalar
    else:
        # Try to parse frequency from channel name
        fn_hz = []
        for name in orig_names:
            m = re.search(r"(\d+(?:\.\d+)?)", name)
            if m:
                val = float(m.group(1))
                # Assume kHz if < 1000, else Hz
                fn_hz.append(val * 1e3 if val < 1e3 else val)
            else:
                fn_hz.append(np.nan)
    
    # Build labels
    labels = []
    for oname, hz in zip(orig_names, fn_hz):
        if add_freq and not np.isnan(hz):
            labels.append(f"{oname} ({hz / 1e3:.0f} kHz)")
        else:
            labels.append(oname)
    
    return ds.assign_coords({label_coord: (chan_dim, labels)})


# =============================================================================
# Mask Visualization Functions
# Ported from legacy _echodata-legacy-code/saildrone-echodata-processing/process/plot.py
# =============================================================================


def plot_mask_channel(
    mask_da: Union["xr.DataArray", "xr.Dataset"],
    channel: Optional[int],
    file_base_name: str,
    echogram_path: str = ".",
    cmap: str = "Greys",
    title_template: str = "{channel_label} – mask",
    dpi: int = 150,
) -> Path:
    """
    Plot and save a mask echogram for a single channel.
    
    Visualizes a Boolean mask (0/1) overlaid on the echogram grid,
    useful for QA of denoising and seabed detection.
    
    Args:
        mask_da: xarray DataArray or Dataset containing mask data
        channel: Channel index to plot
        file_base_name: Base name for output file
        echogram_path: Directory to save the echogram
        cmap: Colormap for plotting (default: "Greys")
        title_template: Template for plot title (use {channel_label})
        dpi: Output image resolution
        
    Returns:
        Path to the saved mask echogram file
    """
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates
    
    # Reuse the same preparation helper
    da_mask, meta = prepare_channel_da(mask_da, channel, var_name="mask")
    
    fig, ax = plt.subplots(figsize=(20, 12))
    da_mask.T.plot(
        x=meta["xdim"],
        y=meta["ydim"],
        yincrease=False,
        vmin=0,
        vmax=1,
        cmap=cmap,
        add_colorbar=False,
        ylim=(meta["bot"], meta["top"]),
        ax=ax,
    )
    
    # Match style from echogram plots
    ax.set_facecolor("#f9f9f9")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_linewidth(1.0)
    locator = mdates.AutoDateLocator(maxticks=12)
    ax.xaxis.set_major_locator(locator)
    ax.xaxis.set_major_formatter(meta["x_formatter"])
    
    # Colorbar
    cbar = plt.colorbar(
        ax.collections[0], pad=0.08, orientation="horizontal", aspect=40, shrink=0.8
    )
    cbar.set_label("Boolean mask (0=keep, 1=remove)", fontsize=12)
    cbar.ax.tick_params(labelsize=10)
    
    # Labels and title
    ax.set_xlabel(meta["x_label"], fontsize=16, labelpad=14)
    ax.set_ylabel(
        "Depth [m]" if meta["ydim"] != "range_sample" else "Sample #",
        fontsize=16,
        labelpad=14,
    )
    ax.set_title(
        title_template.format(channel_label=meta["ch_label"]),
        fontsize=18,
        fontweight="bold",
        pad=16,
    )
    ax.tick_params(which="major", length=6, width=1, labelsize=11)
    plt.setp(ax.get_xticklabels(), rotation=30, ha="right")
    
    fig.tight_layout(pad=2)
    out_path = Path(echogram_path) / f"{file_base_name}_{meta['safe_label']}_mask.png"
    fig.savefig(out_path, dpi=dpi)
    plt.close(fig)
    
    return out_path


def plot_all_masks(
    mask_cube: "xr.DataArray",
    ds_source: "xr.Dataset",
    stage_name: str,
    file_base_name: str,
    output_path: str = "./echograms",
    **plot_kw,
) -> list[Path]:
    """
    Draw one mask echogram per channel for a given denoising stage.
    
    Args:
        mask_cube: DataArray[bool] with dims (channel, ping_time, depth)
            The Boolean mask from denoising. True = cell to be removed.
        ds_source: Original Dataset providing channel coordinate and labels
        stage_name: Human-readable filter name ("Attenuation", "Impulsive noise", ...)
            that appears in every figure title
        file_base_name: Prefix for PNG filenames
        output_path: Directory to save PNGs (created if missing)
        **plot_kw: Additional kwargs forwarded to plot_mask_channel
        
    Returns:
        List of paths to the PNG files that were written
        
    Example:
        >>> paths = plot_all_masks(
        ...     mask_cube=denoise_result["mask_impulsive"],
        ...     ds_source=sv_dataset,
        ...     stage_name="Impulsive Noise",
        ...     file_base_name="TPOS2023_20230601",
        ...     output_path="./echograms/masks",
        ... )
    """
    import xarray as xr
    
    out_dir = Path(output_path)
    out_dir.mkdir(parents=True, exist_ok=True)
    
    n_ch = mask_cube.sizes["channel"]
    out_paths = []
    
    # Default title template can be overridden via **plot_kw
    default_tpl = "{channel_label} – " + stage_name
    title_tpl = plot_kw.pop("title_template", default_tpl)
    
    for idx in range(n_ch):
        # Isolate this channel's mask (2-D)
        mask_da = (
            mask_cube.isel(channel=idx)
            .expand_dims(channel=[ds_source.channel.values[idx]])
            .astype(int)  # 0/1 for plotting
            .rename("mask")
        )
        
        if "channel_label" in ds_source.coords:
            # Extract the scalar label and assign as length-1 coord
            lbl = ds_source["channel_label"].isel(channel=idx).values.item()
            mask_da = mask_da.assign_coords(channel_label=("channel", [lbl]))
        
        # Plot
        chan_idx = 0 if mask_da.sizes["channel"] == 1 else idx
        
        png_path = plot_mask_channel(
            mask_da=mask_da,
            channel=chan_idx,
            file_base_name=f"{file_base_name}_ch{idx}",
            echogram_path=str(out_dir),
            title_template=title_tpl,
            **plot_kw,
        )
        out_paths.append(Path(png_path))
    
    return out_paths


def plot_masks_vertical(
    ds_source: "xr.Dataset",
    file_base_name: str,
    output_path: str = "./echograms",
    cmap: str = "Greys",
    title_template: str = "{channel_label} – {cube_name}",
    dpi: int = 150,
) -> dict[str, Path]:
    """
    Plot all mask_* data variables from a dataset to separate PNGs.
    
    Scans the dataset for any variables starting with "mask_" and creates
    a multi-panel PNG for each (one panel per channel).
    
    Args:
        ds_source: Dataset containing mask_* data variables
        file_base_name: Prefix for output filenames
        output_path: Directory for PNG outputs
        cmap: Colormap for mask visualization
        title_template: Title template with {channel_label} and {cube_name}
        dpi: Output resolution
        
    Returns:
        Dict mapping mask name to PNG path, e.g.:
            {"impulsive": Path("./echograms/file_impulsive.png"), ...}
            
    Example:
        >>> # If ds has mask_impulsive, mask_attenuation data vars:
        >>> paths = plot_masks_vertical(ds, "TPOS2023_20230601")
        >>> print(paths)
        {'impulsive': Path('.../TPOS2023_20230601_impulsive.png'),
         'attenuation': Path('.../TPOS2023_20230601_attenuation.png')}
    """
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates
    
    out_dir = Path(output_path)
    out_dir.mkdir(parents=True, exist_ok=True)
    
    # Find all mask_* variables
    mask_cubes: dict[str, "xr.DataArray"] = {}
    for var in ds_source.data_vars:
        if var.startswith("mask_"):
            cube_name = var[len("mask_"):]  # e.g., "impulsive"
            mask_cubes[cube_name] = ds_source[var]
    
    if not mask_cubes:
        logger.warning("No mask_* variables found in dataset")
        return {}
    
    paths = {}
    for name, cube in mask_cubes.items():
        png = _plot_single_mask_cube(
            cube,
            ds_source,
            file_base_name,
            out_dir,
            cmap=cmap,
            title_tmpl=title_template,
            cube_name=name,
            dpi=dpi,
        )
        paths[name] = png
    
    return paths


def _plot_single_mask_cube(
    cube: "xr.DataArray",
    ds_source: "xr.Dataset",
    fname: str,
    out_dir: Path,
    cmap: str = "Greys",
    title_tmpl: str = "{channel_label} – {cube_name}",
    cube_name: str = "mask",
    dpi: int = 150,
) -> Path:
    """Render one mask cube (all channels) to a single PNG with stacked panels."""
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates
    
    n_ch = cube.sizes["channel"]
    fig_h = 6 * n_ch
    fig, axes = plt.subplots(n_ch, 1, figsize=(20, fig_h), sharex=True)
    axes = np.atleast_1d(axes)
    
    for ch, ax in enumerate(axes):
        da = (
            cube.isel(channel=ch)
            .expand_dims(channel=[ds_source.channel.values[ch]])
            .astype(int)
            .rename("mask")
        )
        lbl = (
            ds_source["channel_label"].isel(channel=ch).values.item()
            if "channel_label" in ds_source.coords
            else f"CH{ch}"
        )
        
        da_p, meta = prepare_channel_da(da, 0, var_name="mask")
        da_p.T.plot(
            x=meta["xdim"],
            y=meta["ydim"],
            yincrease=False,
            vmin=0,
            vmax=1,
            cmap=cmap,
            add_colorbar=False,
            ylim=(meta["bot"], meta["top"]),
            ax=ax,
        )
        ax.set_facecolor("#f9f9f9")
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_linewidth(1.0)
        ax.set_ylabel(
            "Depth [m]" if meta["ydim"] != "range_sample" else "Sample #",
            fontsize=14,
        )
        ax.set_title(
            title_tmpl.format(channel_label=lbl, cube_name=cube_name),
            fontsize=16,
            pad=8,
        )
        ax.tick_params(labelsize=10)
    
    # Configure x-axis on bottom panel
    axes[-1].xaxis.set_major_locator(mdates.AutoDateLocator(maxticks=12))
    axes[-1].xaxis.set_major_formatter(meta["x_formatter"])
    axes[-1].set_xlabel(meta["x_label"], fontsize=16)
    
    fig.tight_layout(pad=2)
    out_path = out_dir / f"{fname}_{cube_name}.png"
    fig.savefig(out_path, dpi=dpi)
    plt.close(fig)
    
    return out_path


# ---------------------------------------------------------------------------
# Interactive HoloViews echogram (ported from saildrone/process/plot.py)
# ---------------------------------------------------------------------------

def create_interactive_echogram(
    ds_Sv: "xr.Dataset",
    channel: int = 0,
    var: str = "Sv",
    title: Optional[str] = None,
    out_html: Union[str, Path] = "echogram.html",
    cmap: str = "ocean_r",
    vmin: float = -80,
    vmax: float = -50,
    time_split: Optional[str] = None,
    seabed_depth: Optional["xr.DataArray"] = None,
    constrain_pan: bool = True,
    lock_zoom_out: bool = True,
    auto_clip_shallow: bool = True,
    auto_clip_margin: float = 50.0,
    min_width: int = 600,
    max_width: int = 1600,
    min_height: int = 300,
    max_height: int = 800,
    px_per_meter: float = 0.8,
    tools: str = "pan,wheel_zoom,box_zoom,reset,hover",
    plot_bg_color: Optional[str] = "#f9f9f9",
) -> Path:
    """Create an interactive echogram as a standalone HTML file.

    Uses HoloViews/Panel with rasterized quadmesh for fast rendering
    of large datasets.  Optionally overlays a seabed detection line
    and supports time-split tabs for multi-day data.

    Requires optional dependencies: ``holoviews``, ``panel``, ``hvplot``,
    ``datashader``, ``bokeh``.

    Args:
        ds_Sv: Sv xarray Dataset (with ``channel`` dimension).
        channel: Channel index to plot.
        var: Variable name to plot (default ``"Sv"``).
        title: Plot title.
        out_html: Output HTML file path.
        cmap: Colormap name.
        vmin, vmax: Colour scale limits (dB).
        time_split: Pandas offset alias for splitting into tabs
            (e.g. ``"12h"``, ``"1D"``).  ``None`` = single plot.
        seabed_depth: Optional DataArray with seabed depth per ping
            (from ``detect_seabed``).  Overlaid as a red line.
        constrain_pan: Lock panning to data extents.
        lock_zoom_out: Prevent zoom-out beyond data extents.
        auto_clip_shallow: Clip shallow all-NaN regions.
        auto_clip_margin: Margin (m) below deepest valid data for
            auto-clip.
        min_width, max_width, min_height, max_height: Canvas size bounds (px).
        px_per_meter: Pixels per metre of depth range.
        tools: Bokeh tools string.
        plot_bg_color: Background colour for the plot area.

    Returns:
        Path to the generated HTML file.
    """
    try:
        import holoviews as hv
        import hvplot.xarray  # noqa: F401  – registers accessor
        import panel as pn
        from bokeh.resources import INLINE as bokeh_resources
    except ImportError as e:
        raise ImportError(
            "Interactive echograms require holoviews, hvplot, panel, "
            "datashader, and bokeh.  Install with: "
            "pip install holoviews hvplot panel datashader bokeh"
        ) from e

    import re

    import xarray as xr

    hv.extension("bokeh")
    out_html = Path(out_html)

    ds_Sv = ensure_channel_labels(ds_Sv)

    # Select channel
    da = ds_Sv[var].isel(channel=channel)
    da = attach_depth_coord(ds_Sv, da, channel)

    # Determine axes
    if "depth" in da.dims or "depth" in da.coords:
        ydim = "depth"
    elif "echo_range" in da.dims or "echo_range" in da.coords:
        ydim = "echo_range"
    elif "range_sample" in da.dims:
        ydim = "range_sample"
    else:
        ydim = [d for d in da.dims if d not in ("channel", "ping_time")][0]
    xdim = "ping_time"

    if da.dims != (xdim, ydim):
        da = da.transpose(xdim, ydim)

    if ydim == "depth" and np.any(np.diff(da[ydim].values) < 0):
        da = da.sortby(ydim)

    try:
        da = da.compute()
    except Exception:
        pass

    # Sanitize attrs
    da.name = "Sv"
    da.attrs.setdefault("units", "dB")

    # Auto-clip shallow
    if auto_clip_shallow and ydim == "depth":
        valid_by_depth = np.isfinite(da).any(dim=xdim)
        if bool(valid_by_depth.any()):
            depth_with_data = da[ydim].where(valid_by_depth, drop=True)
            dmin = float(depth_with_data.min().values)
            dmax_data = float(depth_with_data.max().values)
            dmax_full = float(da[ydim].max().values)
            if dmax_data < dmax_full - 1e-6:
                upper = dmax_data + auto_clip_margin
                da = da.sel({ydim: slice(dmin, upper)})

    def _canvas_size(chunk):
        n_x = chunk.sizes[xdim]
        w = int(np.clip(n_x * 0.05, min_width, max_width))
        yy = chunk[ydim].values
        y0, y1 = float(np.nanmin(yy)), float(np.nanmax(yy))
        span_m = max(abs(y1 - y0), 1.0)
        h = int(np.clip(span_m * px_per_meter, min_height, max_height))
        return w, h

    def _plot(chunk, label: str):
        w, h = _canvas_size(chunk)
        p = chunk.hvplot.quadmesh(
            x=xdim,
            y=ydim,
            rasterize=True,
            upsample=True,
            aggregator="mean",
            width=w,
            height=h,
            cmap=cmap,
            clim=(vmin, vmax),
            cnorm="linear",
            flip_yaxis=(ydim == "depth"),
            clabel="Sv (dB)",
            colorbar=True,
            title=label,
            tools=[],
        )

        # Overlay seabed line
        if seabed_depth is not None:
            sb_df = pd.DataFrame({
                xdim: seabed_depth["ping_time"].values,
                "seabed": seabed_depth.values,
            }).dropna()
            if not sb_df.empty:
                import pandas as pd_  # noqa: F811
                sb_curve = hv.Curve(sb_df, xdim, "seabed").opts(
                    color="red", line_width=1.5, line_dash="dashed",
                )
                p = p * sb_curve

        p = p.opts(
            active_tools=["xwheel_zoom"],
            tools=[],
            bgcolor=plot_bg_color,
            padding=0.02,
            fontsize=11,
        )
        return p

    import pandas as pd

    if time_split:
        t0 = pd.to_datetime(da[xdim].values[0]).floor(time_split)
        t1 = pd.to_datetime(da[xdim].values[-1]).ceil(time_split)
        edges = pd.date_range(t0, t1, freq=time_split)

        tabs = []
        for start, end in zip(edges[:-1], edges[1:]):
            sl = da.sel({xdim: slice(start, end)})
            if sl.size == 0:
                continue
            try:
                sl = sl.compute()
            except Exception:
                pass
            tabs.append(
                (f"{start:%Y-%m-%d %H:%M} – {end:%H:%M}", _plot(sl, ""))
            )

        panel_obj = (
            pn.Tabs(*tabs)
            if len(tabs) > 1
            else (pn.panel(tabs[0][1]) if tabs else pn.pane.Markdown("No data"))
        )
    else:
        panel_obj = pn.panel(_plot(da, title or ""))

    panel_obj.save(
        str(out_html), embed=True, resources=bokeh_resources, title=(title or "Echogram")
    )
    logger.info(f"Interactive echogram saved to {out_html}")
    return out_html


def plot_sv_with_seabed(
    ds_Sv: "xr.Dataset",
    seabed_depth: "xr.DataArray",
    channel: int = 0,
    file_base_name: str = "echogram",
    echogram_path: str = ".",
    cmap: str = "ocean_r",
    vmin: float = -80,
    vmax: float = -50,
    dpi: int = 180,
    seabed_color: str = "red",
    seabed_linewidth: float = 1.5,
) -> Path:
    """Plot Sv echogram with a seabed detection line overlay.

    A convenience wrapper around ``plot_sv_channel`` that adds the
    seabed line from a ``SeabedDetectionResult.seabed_depth`` DataArray.

    Args:
        ds_Sv: Sv xarray Dataset.
        seabed_depth: 1-D DataArray (indexed by ``ping_time``) with
            seabed depth in metres.
        channel: Channel index.
        file_base_name: Output file base name.
        echogram_path: Output directory.
        cmap: Colormap.
        vmin, vmax: Colour scale limits.
        dpi: Image resolution.
        seabed_color: Line colour for the seabed overlay.
        seabed_linewidth: Line width.

    Returns:
        Path to the saved PNG file.
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates

    ds_Sv = ensure_channel_labels(ds_Sv)

    da, meta = prepare_channel_da(ds_Sv, channel)
    import pandas as pd

    t = pd.to_datetime(da[meta["xdim"]].values)
    hours = max(1.0, (t[-1] - t[0]).total_seconds() / 3600.0)
    width_in = max(14.0, hours * 1.0)
    height_in = 12.0

    fig, ax = plt.subplots(figsize=(width_in, height_in))

    da.T.plot.pcolormesh(
        x=meta["xdim"],
        y=meta["ydim"],
        shading="auto",
        yincrease=False,
        vmin=vmin,
        vmax=vmax,
        cmap=cmap,
        add_colorbar=True,
        ylim=(meta["bot"], meta["top"]),
        ax=ax,
        rasterized=True,
    )

    # Overlay seabed line
    sb_times = pd.to_datetime(seabed_depth["ping_time"].values)
    sb_vals = seabed_depth.values.astype(float)
    valid = ~np.isnan(sb_vals)
    if valid.any():
        ax.plot(
            sb_times[valid],
            sb_vals[valid],
            color=seabed_color,
            linewidth=seabed_linewidth,
            linestyle="--",
            label="Seabed",
        )
        ax.legend(loc="lower right", fontsize=10)

    ax.set_facecolor("#f9f9f9")
    ax.xaxis.set_major_locator(mdates.AutoDateLocator(minticks=6, maxticks=18))
    ax.xaxis.set_major_formatter(meta["x_formatter"])
    ax.set_xlabel(meta["x_label"], fontsize=16, labelpad=14)
    ax.set_ylabel(
        "Depth [m]" if meta["ydim"] != "range_sample" else "Sample #",
        fontsize=16,
        labelpad=14,
    )
    ax.set_title(
        meta["ch_label"],
        fontsize=18,
        fontweight="bold",
        pad=16,
    )
    ax.tick_params(which="major", length=6, width=1, labelsize=11)
    plt.setp(ax.get_xticklabels(), rotation=30, ha="right")

    fig.tight_layout(pad=2)

    out_path = Path(echogram_path) / f"{file_base_name}_{meta['safe_label']}_seabed.png"
    fig.savefig(out_path, dpi=dpi)
    plt.close(fig)

    logger.info(f"Echogram with seabed overlay saved to {out_path}")
    return out_path


# ── Blob upload helpers (ported from saildrone.process.plot) ────────────


def plot_and_upload_echograms(
    sv_dataset: "xr.Dataset",
    *,
    cruise_id: str | None = None,
    file_base_name: str | None = None,
    save_to_blobstorage: bool = False,
    output_path: str | None = None,
    upload_path: str | None = None,
    container_name: str | None = None,
    export_filename=None,
    create_interactive_pages: bool = False,
    channel: int | None = None,
    cmap: str = "ocean_r",
    plot_var: str = "Sv",
    title_template: str = "{channel_label}",
    depth_var: str | None = None,
    connection_string: str | None = None,
    qc_windows: Optional[list] = None,
) -> list[str]:
    """Generate echograms and optionally upload them to Azure Blob Storage.

    When *save_to_blobstorage* is True, PNGs are written to a temp directory,
    the directory is uploaded to Azure Blob, and a list of blob-relative paths
    is returned.

    Parameters
    ----------
    sv_dataset : xr.Dataset
        Dataset containing Sv data.
    cruise_id : str, optional
        Cruise identifier (used for path construction).
    file_base_name : str
        Base name for the echogram files.
    save_to_blobstorage : bool
        Upload to Azure Blob after generation.
    output_path : str, optional
        Local directory for non-blob output.
    upload_path : str, optional
        Target path inside the blob container.
    container_name : str, optional
        Azure Blob container name.
    export_filename : callable, optional
        Transform echogram filenames for the returned list.
    create_interactive_pages : bool
        Generate interactive HTML echograms.
    channel : int, optional
        Plot only a specific channel index.
    cmap : str
        Matplotlib colormap name.
    plot_var : str
        Variable to plot (default ``"Sv"``).
    title_template : str
        Title template with ``{channel_label}`` placeholder.
    depth_var : str, optional
        Depth variable name override.
    connection_string : str, optional
        Azure connection string override.
    qc_windows : list, optional
        Pre-filtered list of ``QCWindow`` objects to overlay on the echogram
        (see :func:`oceanstream.echodata.plot.qc.load_qc_windows` and
        :func:`~oceanstream.echodata.plot.qc.filter_qc_windows`).

    Returns
    -------
    list[str]
        Paths of generated/uploaded echogram files.
    """
    import shutil

    if save_to_blobstorage:
        echograms_dir = f"{_scratch_root()}/{cruise_id or 'default'}/{file_base_name}"
    else:
        if output_path is None:
            raise ValueError("output_path is required when save_to_blobstorage is False")
        echograms_dir = f"{output_path}/echograms/{file_base_name}"

    os.makedirs(echograms_dir, exist_ok=True)

    echogram_files = plot_sv_data(
        sv_dataset,
        file_base_name=file_base_name or "echogram",
        output_path=echograms_dir,
        plot_var=plot_var,
        cmap=cmap,
        channel=channel,
        title_template=title_template,
        qc_windows=qc_windows,
    )

    if create_interactive_pages:
        try:
            n_ch = sv_dataset.sizes.get("channel", 1)
            for ch in range(n_ch):
                html_path = f"{echograms_dir}/{file_base_name}_{ch}.html"
                create_interactive_echogram(
                    sv_dataset, channel=ch, out_html=html_path,
                    var=plot_var, cmap=cmap,
                )
        except Exception as exc:
            logger.warning("Interactive echogram generation failed: %s", exc)

    if save_to_blobstorage:
        from oceanstream.echodata.storage import upload_file_to_blob

        upload_dest = upload_path or f"{cruise_id}/{file_base_name}"

        # Upload all files in the echograms directory
        echograms_path = Path(echograms_dir)
        for fpath in echograms_path.rglob("*"):
            if fpath.is_file():
                blob_name = f"{upload_dest}/{fpath.name}"
                upload_file_to_blob(
                    str(fpath), blob_name, container_name,
                    connection_string=connection_string,
                )

        shutil.rmtree(echograms_dir, ignore_errors=True)

        if export_filename is not None:
            uploaded_files = [export_filename(str(e)) for e in echogram_files]
        else:
            uploaded_files = [
                f"{cruise_id}/{file_base_name}/{Path(e).name}" for e in echogram_files
            ]
    else:
        uploaded_files = [str(Path(e).name) for e in echogram_files]

    return uploaded_files


def plot_and_upload_masks(
    ds: "xr.Dataset",
    *,
    file_base_name: str | None = None,
    upload_path: str | None = None,
    container_name: str | None = None,
    title_template: str = "{channel_label} – {cube_name}",
    connection_string: str | None = None,
) -> list[str]:
    """Plot mask cubes and upload to Azure Blob Storage.

    Returns
    -------
    list[str]
        Blob-relative paths of the uploaded images.
    """
    import shutil

    local_dir = f"{_scratch_root()}/{file_base_name}"
    os.makedirs(local_dir, exist_ok=True)

    try:
        paths = plot_masks_vertical(
            ds, file_base_name=file_base_name, output_path=local_dir,
            title_template=title_template,
        )
    except Exception as exc:
        logger.error("plot_masks_vertical failed: %s", exc)
        return []

    from oceanstream.echodata.storage import upload_file_to_blob

    for fpath in Path(local_dir).rglob("*"):
        if fpath.is_file():
            blob_name = f"{upload_path}/{fpath.name}"
            upload_file_to_blob(
                str(fpath), blob_name, container_name,
                connection_string=connection_string,
            )

    shutil.rmtree(local_dir, ignore_errors=True)

    return [f"{file_base_name}/{p.name}" for p in paths.values()]

