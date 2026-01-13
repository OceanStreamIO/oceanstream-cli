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
    import numpy as np
    
    # Parse parameters
    vertical_bin = params.get("vertical_bin_size", 2.0)
    ping_lags = params.get("ping_lags", [1])
    threshold_db = params.get("threshold_db", 10.0)
    
    if isinstance(vertical_bin, str):
        vertical_bin = float(vertical_bin.replace("m", ""))
    
    Sv = sv_dataset["Sv"]
    
    # Determine range coordinate - try depth, echo_range, range_sample in order
    if "depth" in sv_dataset.dims or "depth" in sv_dataset.coords:
        range_coord = "depth"
    elif "echo_range" in sv_dataset.dims or "echo_range" in sv_dataset.coords:
        range_coord = "echo_range"
    elif "range_sample" in sv_dataset.dims:
        range_coord = "range_sample"
    else:
        range_coord = "echo_range"
    
    # Get range values for computing bin size
    if range_coord in sv_dataset.coords:
        range_values = sv_dataset[range_coord]
    elif "echo_range" in sv_dataset.coords:
        range_values = sv_dataset["echo_range"]
        range_coord = "echo_range"
    else:
        # Fall back to sample indices
        n_samples = sv_dataset.dims.get(range_coord, sv_dataset.dims.get("range_sample", 1000))
        range_values = xr.DataArray(
            np.arange(n_samples) * 0.1,
            dims=[range_coord if range_coord in sv_dataset.dims else "range_sample"]
        )
        range_coord = range_values.dims[0]
    
    # Compute bin size based on vertical_bin in meters
    if range_coord in sv_dataset.dims:
        dz_arr = range_values.diff(range_coord)
        dz = float(dz_arr.median()) if len(dz_arr) > 0 else 0.1
        bin_size = max(1, int(vertical_bin / abs(dz)))
    else:
        bin_size = 1
    
    if bin_size > 1 and range_coord in Sv.dims:
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
        # Convert to float for interpolation, then back to bool
        combined_float = combined.astype(float)
        combined_float = combined_float.interp(
            **{range_coord: range_values},
            method="nearest",
            kwargs={"fill_value": 0.0},
        )
        combined = combined_float > 0.5
    
    # Pad back to original ping_time dimension (diff reduces it by lag)
    # Create a full-size mask initialized to False
    original_n_pings = sv_dataset.sizes["ping_time"]
    full_mask = xr.DataArray(
        np.zeros((original_n_pings, combined.sizes[range_coord]), dtype=bool),
        dims=["ping_time", range_coord],
        coords={
            "ping_time": sv_dataset.ping_time,
            range_coord: combined.coords[range_coord],
        },
    )
    
    # Copy the computed mask values into the correct positions
    # diff removes the first `lag` values, so we start from position max_lag
    mask_n_pings = combined.sizes["ping_time"]
    max_lag = max(ping_lags)
    start_idx = max_lag
    end_idx = start_idx + mask_n_pings
    
    if end_idx <= original_n_pings:
        # Assign values - need to handle coordinate alignment
        full_mask.values[start_idx:end_idx, :] = combined.values
    
    logger.info(
        f"Impulse noise mask: {float(full_mask.mean().values) * 100:.1f}% flagged"
    )
    
    return full_mask
