"""Echogram plotting for Sv datasets.

Provides functions for generating publication-quality echogram plots
from acoustic backscatter (Sv) data.

Ported from _echodata-legacy-code/saildrone-echodata-processing/process/plot.py
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import TYPE_CHECKING, Optional, Union

import numpy as np

if TYPE_CHECKING:
    import matplotlib.pyplot as plt
    import xarray as xr

logger = logging.getLogger(__name__)


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
    import xarray as xr
    
    # Load if path
    if isinstance(sv_dataset, (str, Path)):
        logger.info(f"Loading Sv from {sv_dataset}")
        sv_dataset = xr.open_zarr(sv_dataset)
    
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
        
    Returns:
        List of file paths for the saved echograms
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
        
    Returns:
        Path to the saved echogram file
    """
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates
    import pandas as pd
    
    # Prepare data
    da_Sv, meta = prepare_channel_da(ds_Sv, channel, var_name=plot_var)
    
    # Calculate figure dimensions based on time span
    t = pd.to_datetime(da_Sv[meta["xdim"]].values)
    hours = max(1.0, (t[-1] - t[0]).total_seconds() / 3600.0)
    
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
    
    # Plot echogram
    da_Sv.T.plot.pcolormesh(
        x=meta["xdim"],
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
    
    # Add hour grid lines if requested
    if hour_grid and meta["xdim"] == "ping_time":
        start = t[0].floor("1H")
        end = t[-1].ceil("1H")
        for dt in pd.date_range(start, end, freq="1H"):
            ax.axvline(dt, color="k", lw=0.6, alpha=0.18, zorder=3)
    
    # Styling
    ax.set_facecolor("#f9f9f9")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_linewidth(1.0)
    
    ax.xaxis.set_major_locator(mdates.AutoDateLocator(minticks=6, maxticks=18))
    ax.xaxis.set_major_formatter(meta["x_formatter"])
    
    # Colorbar
    cbar_kw = dict(pad=0.08, shrink=0.8)
    if colorbar_orientation == "horizontal":
        cbar_kw.update(orientation="horizontal", aspect=40)
    else:
        cbar_kw.update(fraction=0.04, pad=0.02)
    cbar = plt.colorbar(ax.collections[0], **cbar_kw)
    cbar.set_label("Volume backscattering strength (Sv re 1 m⁻¹)", fontsize=12)
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
    
    # Save figure
    out_path = Path(echogram_path) / f"{file_base_name}_{meta['safe_label']}.png"
    fig.savefig(out_path, dpi=dpi)
    plt.close(fig)
    
    return out_path


# Alias for backward compatibility
plot_echogram = plot_sv_channel


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
