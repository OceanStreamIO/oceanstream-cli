"""Attenuated signal detection.

Detects depth layers with abnormally low backscatter compared
to surrounding pings, indicating signal attenuation from
bubbles, dense plankton, or equipment issues.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    import xarray as xr

logger = logging.getLogger(__name__)


def attenuation_mask(
    sv_dataset: "xr.Dataset",
    params: dict,
) -> "xr.DataArray":
    """
    Attenuated signal detector.
    
    Identifies depth bands where signal is significantly lower than
    median of surrounding pings, indicating attenuation.
    
    Args:
        sv_dataset: Sv xarray Dataset
        params: Dictionary of parameters:
            - upper_limit_sl (float): Upper depth limit (m), default 180
            - lower_limit_sl (float): Lower depth limit (m), default 280
            - num_side_pings (int): Pings on each side for median, default 15
            - threshold (float): Detection threshold (dB), default 5
            
    Returns:
        Boolean DataArray mask (True = attenuated signal to remove)
    """
    import xarray as xr
    
    # Parse parameters
    upper_limit = params.get("upper_limit_sl", 180.0)
    lower_limit = params.get("lower_limit_sl", 280.0)
    num_side_pings = params.get("num_side_pings", 15)
    threshold = params.get("threshold", 5.0)
    
    Sv = sv_dataset["Sv"]
    
    # Determine range dimension (what we iterate over)
    if "range_sample" in sv_dataset.dims:
        range_dim = "range_sample"
    elif "depth" in sv_dataset.dims:
        range_dim = "depth"
    elif "echo_range" in sv_dataset.dims:
        range_dim = "echo_range"
    else:
        range_dim = [d for d in Sv.dims if d not in ["channel", "ping_time"]][0]
    
    # Get range values in METERS (check data_vars first, then coords)
    if "echo_range" in sv_dataset.data_vars:
        range_values = sv_dataset["echo_range"]
        logger.debug("Using echo_range from data_vars (meters)")
    elif "echo_range" in sv_dataset.coords:
        range_values = sv_dataset["echo_range"]
    elif "depth" in sv_dataset.data_vars:
        range_values = sv_dataset["depth"]
    elif "depth" in sv_dataset.coords:
        range_values = sv_dataset["depth"]
    else:
        # Fall back to estimated range from sample indices
        n_samples = sv_dataset.sizes.get(range_dim, 1000)
        sample_spacing = 0.18  # meters per sample (typical EK80 CW)
        range_values = xr.DataArray(
            np.arange(n_samples) * sample_spacing,
            dims=[range_dim]
        )
        logger.warning(f"No echo_range found, estimating from {range_dim}")
    
    # Select depth band for attenuation check
    depth_mask = (range_values >= upper_limit) & (range_values <= lower_limit)
    Sv_band = Sv.where(depth_mask)
    
    # Compute mean Sv in the depth band for each ping
    Sv_band_mean = Sv_band.mean(dim=range_dim, skipna=True)
    
    # Compute running median of band means
    window_size = 2 * num_side_pings + 1
    Sv_band_median = Sv_band_mean.rolling(
        ping_time=window_size,
        center=True,
        min_periods=num_side_pings,
    ).median()
    
    # Detect pings with abnormally low signal
    attenuation_diff = Sv_band_median - Sv_band_mean
    attenuation_pings = attenuation_diff > threshold
    
    # Expand ping mask to full data shape
    # When a ping is attenuated, flag all samples in that ping
    mask = attenuation_pings.broadcast_like(Sv)
    
    # Fill NaN with False
    mask = mask.fillna(False)
    
    logger.info(
        f"Attenuation mask: {float(attenuation_pings.mean().values) * 100:.1f}% of pings flagged"
    )
    
    return mask
