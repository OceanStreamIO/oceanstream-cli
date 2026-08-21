"""Echogram plotting module for acoustic data visualization.

Provides functions for generating echogram plots from Sv datasets,
including single-channel and multi-channel visualizations.

Ported from _echodata-legacy-code/saildrone-echodata-processing/process/plot.py
"""

from oceanstream.echodata.plot.echogram import (
    plot_sv_data,
    plot_sv_channel,
    plot_echogram,
    generate_echograms,
    prepare_channel_da,
    ensure_channel_labels,
    # Mask visualization
    plot_mask_channel,
    plot_all_masks,
    plot_masks_vertical,
    # Interactive & seabed overlay
    create_interactive_echogram,
    plot_sv_with_seabed,
)
from oceanstream.echodata.plot.qc import (
    QCWindow,
    load_qc_windows,
    filter_qc_windows,
    draw_qc_overlay,
)
from oceanstream.echodata.plot.colormaps import (
    CANONICAL_COLORMAP_NAMES,
    get_colormap,
    resolve_colormap_list,
)
from oceanstream.echodata.plot.combined import (
    CombinedDataset,
    combine_38khz_day,
    render_combined_echogram,
)

__all__ = [
    "plot_sv_data",
    "plot_sv_channel",
    "plot_echogram",
    "generate_echograms",
    "prepare_channel_da",
    "ensure_channel_labels",
    # Mask visualization
    "plot_mask_channel",
    "plot_all_masks",
    "plot_masks_vertical",
    # Interactive & seabed overlay
    "create_interactive_echogram",
    "plot_sv_with_seabed",
    # QC flag overlay
    "QCWindow",
    "load_qc_windows",
    "filter_qc_windows",
    "draw_qc_overlay",
    # Colormap presets
    "CANONICAL_COLORMAP_NAMES",
    "get_colormap",
    "resolve_colormap_list",
    # Combined long+short pulse 38 kHz
    "CombinedDataset",
    "combine_38khz_day",
    "render_combined_echogram",
]
