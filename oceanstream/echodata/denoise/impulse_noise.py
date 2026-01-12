"""Impulse noise detection using multi-lag difference algorithm.

Detects short-duration spikes that appear in single pings,
using forward/backward difference comparisons.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    import xarray as xr

logger = logging.getLogger(__name__)


def impulse_noise_mask(
    sv_dataset: "xr.Dataset",
    params: dict,
) -> "xr.DataArray":
    """
    Multi-lag impulse noise filter.
    
    Detects single-ping spikes by comparing each sample to
    neighboring pings using forward and backward differences.
    
    Args:
        sv_dataset: Sv xarray Dataset
        params: Dictionary of parameters:
            - vertical_bin_size (float): Vertical binning size (m), default 2
            - ping_lags (list[int]): Ping lags to compare, default [1]
            - threshold_db (float): Detection threshold (dB), default 10
            
    Returns:
        Boolean DataArray mask (True = noise to remove)
    """
    import xarray as xr
    
    # Parse parameters
    vertical_bin = params.get("vertical_bin_size", 2.0)
    ping_lags = params.get("ping_lags", [1])
    threshold_db = params.get("threshold_db", 10.0)
    
    if isinstance(vertical_bin, str):
        vertical_bin = float(vertical_bin.replace("m", ""))
    
    Sv = sv_dataset["Sv"]
    
    # Determine range coordinate
    if "depth" in sv_dataset.dims or "depth" in sv_dataset.coords:
        range_coord = "depth"
    else:
        range_coord = "echo_range"
    
    # Bin vertically to reduce noise sensitivity
    range_values = sv_dataset[range_coord]
    dz = float(range_values.diff(range_coord).median())
    bin_size = max(1, int(vertical_bin / dz))
    
    if bin_size > 1:
        Sv_binned = Sv.coarsen(
            **{range_coord: bin_size},
            boundary="trim",
        ).mean()
    else:
        Sv_binned = Sv
    
    # Compute impulse mask from lag differences
    masks = []
    
    for lag in ping_lags:
        # Forward difference
        diff_fwd = Sv_binned.diff("ping_time", n=lag)
        mask_fwd = diff_fwd > threshold_db
        
        # Backward difference (shift forward then diff)
        Sv_shifted = Sv_binned.shift(ping_time=-lag)
        diff_bwd = Sv_binned - Sv_shifted
        mask_bwd = diff_bwd > threshold_db
        
        # Both forward and backward must exceed threshold
        # (impulse stands out from both neighbors)
        mask_lag = mask_fwd & mask_bwd.shift(ping_time=lag)
        masks.append(mask_lag)
    
    # Combine masks from all lags
    combined = masks[0]
    for mask in masks[1:]:
        combined = combined | mask
    
    # Fill NaN with False
    combined = combined.fillna(False)
    
    # Interpolate back to original resolution if binned
    if bin_size > 1:
        combined = combined.interp(
            **{range_coord: range_values},
            method="nearest",
            kwargs={"fill_value": False},
        )
    
    # Align ping_time
    combined = combined.reindex(
        ping_time=sv_dataset.ping_time,
        method="nearest",
        fill_value=False,
    )
    
    logger.info(
        f"Impulse noise mask: {float(combined.mean().values) * 100:.1f}% flagged"
    )
    
    return combined
