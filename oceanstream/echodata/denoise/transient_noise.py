"""Transient noise detection using Fielding et al. algorithm.

Detects upward-stepping noise patterns typical of interference
from nearby acoustic sources.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    import xarray as xr

logger = logging.getLogger(__name__)


def transient_noise_mask(
    sv_dataset: "xr.Dataset",
    params: dict,
) -> "xr.DataArray":
    """
    Fielding et al. transient noise filter.
    
    Detects interference patterns that appear as elevated backscatter
    stepping upward from depth.
    
    Args:
        sv_dataset: Sv xarray Dataset
        params: Dictionary of parameters:
            - exclude_above (float): Exclude depths above this (m), default 250
            - depth_bin (float): Depth binning size (m), default 5
            - n_pings (int): Number of pings in running window, default 20
            - thr_dB (float): Threshold for detection (dB), default 6
            
    Returns:
        Boolean DataArray mask (True = noise to remove)
    """
    import xarray as xr
    
    # Parse parameters
    exclude_above = params.get("exclude_above", 250.0)
    depth_bin = params.get("depth_bin", 5.0)
    n_pings = params.get("n_pings", 20)
    threshold_db = params.get("thr_dB", 6.0)
    
    # Determine range coordinate - try depth, echo_range, range_sample in order
    if "depth" in sv_dataset.dims or "depth" in sv_dataset.coords:
        range_coord = "depth"
    elif "echo_range" in sv_dataset.dims or "echo_range" in sv_dataset.coords:
        range_coord = "echo_range"
    elif "range_sample" in sv_dataset.dims:
        range_coord = "range_sample"
    else:
        range_coord = "echo_range"
    
    Sv = sv_dataset["Sv"]
    
    # Get range values
    if range_coord in sv_dataset.coords:
        range_values = sv_dataset[range_coord]
    elif "echo_range" in sv_dataset.coords:
        range_values = sv_dataset["echo_range"]
    else:
        # Fall back to sample indices - assume 0.1m per sample
        import xarray as xr
        n_samples = sv_dataset.dims.get(range_coord, sv_dataset.dims.get("range_sample", 1000))
        range_values = xr.DataArray(
            np.arange(n_samples) * 0.1,
            dims=[range_coord if range_coord in sv_dataset.dims else "range_sample"]
        )
    
    # Exclude shallow water
    mask_depth = range_values > exclude_above
    Sv_deep = Sv.where(mask_depth)
    
    # Bin by depth
    n_depth_bins = int(np.ceil((float(range_values.max()) - exclude_above) / depth_bin))
    
    if n_depth_bins <= 0:
        logger.warning("No valid depth range for transient noise detection")
        return xr.DataArray(
            np.zeros(Sv.shape, dtype=bool),
            dims=Sv.dims,
            coords=Sv.coords,
        )
    
    # Compute running median in ping dimension
    Sv_median = Sv_deep.rolling(
        ping_time=n_pings,
        center=True,
        min_periods=n_pings // 2,
    ).median()
    
    # Detect samples significantly above median
    anomaly = Sv_deep - Sv_median
    mask_transient = anomaly > threshold_db
    
    # Fill NaN with False (no transient noise in excluded regions)
    mask_transient = mask_transient.fillna(False)
    
    logger.info(
        f"Transient noise mask: {float(mask_transient.mean().values) * 100:.1f}% flagged"
    )
    
    return mask_transient
