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
]
