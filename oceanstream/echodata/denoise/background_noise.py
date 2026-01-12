"""Background noise detection using De Robertis & Higginbottom (2007).

Estimates background noise level and flags samples with insufficient
signal-to-noise ratio.

Ported from _echodata-legacy-code/saildrone-echodata-processing/denoise/background_noise.py
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Tuple

import numpy as np

if TYPE_CHECKING:
    import xarray as xr

logger = logging.getLogger(__name__)


def background_noise_mask(
    sv_dataset: "xr.Dataset",
    params: dict,
) -> "xr.DataArray":
    """
    De Robertis & Higginbottom (2007) background noise filter.
    
    Estimates background noise from TVG-removed signal using block
    statistics, then flags samples with insufficient SNR.
    
    Args:
        sv_dataset: Sv xarray Dataset with "Sv" variable and range coordinate
        params: Dictionary of parameters:
            - range_coord (str): Vertical coord name, default "depth" or "echo_range"
            - sound_absorption (float): Sound absorption α (dB/m), default 0.001
            - range_window (int): Range samples in blocking window, default 20
            - ping_window (int): Pings in blocking window, default 50
            - background_noise_max (str): Lowest allowed background Sv, default "-125.0dB"
            - SNR_threshold (str): Minimum SNR, default "3.0dB"
            
    Returns:
        Boolean DataArray mask (True = noise to remove)
        
    Reference:
        De Robertis, A., & Higginbottom, I. (2007). A post-processing 
        technique to estimate the signal-to-noise ratio and remove 
        echosounder background noise. ICES Journal of Marine Science, 
        64(6), 1282-1291.
    """
    import xarray as xr
    
    # Parse parameters with defaults
    if "depth" in sv_dataset.dims or "depth" in sv_dataset.coords:
        default_range_coord = "depth"
    else:
        default_range_coord = "echo_range"
    
    range_coord = params.get("range_coord", default_range_coord)
    sound_absorption = params.get("sound_absorption", 0.001)
    range_window = params.get("range_window", 20)
    ping_window = params.get("ping_window", 50)
    background_noise_max = _extract_db(params.get("background_noise_max", "-125.0dB"))
    snr_threshold = _extract_db(params.get("SNR_threshold", "3.0dB"))
    
    # Handle auto range window
    if range_window == "auto" and range_coord == "depth":
        dz = float(sv_dataset[range_coord].diff(range_coord).median())
        range_window = max(1, round(1.0 / dz))
    
    # Get Sv and range values
    Sv = sv_dataset["Sv"]
    range_values = sv_dataset[range_coord]
    
    # Remove TVG: 20 log10(r) + 2αr
    r_safe = xr.where(range_values > 0, range_values, np.nan)
    tvg = 20.0 * np.log10(r_safe) + 2.0 * sound_absorption * r_safe
    Sv_flat_db = Sv - tvg
    
    # Convert to linear domain for block averaging
    power_lin = 10.0 ** (Sv_flat_db / 10.0)
    
    # Coarsen to block averages
    binned_lin = power_lin.coarsen(
        ping_time=ping_window,
        **{range_coord: range_window},
        boundary="pad",
    ).mean()
    
    # Convert to dB for depth statistic
    binned_db = 10.0 * np.log10(binned_lin.where(binned_lin > 0))
    
    # Rechunk for depth operations
    if hasattr(binned_db, "chunk"):
        binned_db = binned_db.chunk({range_coord: -1})
    
    # Get noise estimate from depth minimum
    noise_1d_db = binned_db.min(dim=range_coord, skipna=True)
    
    # Align ping_time indices
    noise_1d_db = noise_1d_db.assign_coords(
        ping_time=ping_window * np.arange(noise_1d_db.sizes["ping_time"])
    )
    power_lin = power_lin.assign_coords(
        ping_time=np.arange(power_lin.sizes["ping_time"])
    )
    
    # Cap noise floor
    if background_noise_max is not None:
        noise_1d_db = noise_1d_db.where(
            noise_1d_db < background_noise_max, background_noise_max
        )
    
    # Restore TVG to noise estimate
    noise_lin = 10.0 ** (noise_1d_db / 10.0)
    noise_interp = noise_lin.interp(
        ping_time=power_lin.ping_time,
        method="nearest",
        kwargs={"fill_value": "extrapolate"},
    )
    
    # Broadcast noise to full shape
    if range_coord in noise_interp.dims:
        noise_broadcast = noise_interp
    else:
        noise_broadcast = noise_interp.expand_dims({range_coord: range_values})
    
    # Compute SNR
    signal_minus_noise = power_lin - noise_broadcast
    snr = 10.0 * np.log10(
        xr.where(signal_minus_noise > 0, signal_minus_noise, 1e-30)
    ) - 10.0 * np.log10(xr.where(noise_broadcast > 0, noise_broadcast, 1e-30))
    
    # Create masks
    mask_low_snr = snr < snr_threshold
    mask_non_positive = signal_minus_noise <= 0
    
    # Combine masks
    combined_mask = mask_low_snr | mask_non_positive
    
    # Ensure same shape as Sv
    combined_mask = combined_mask.interp(
        ping_time=sv_dataset.ping_time,
        method="nearest",
        kwargs={"fill_value": True},  # Default to mask if outside range
    )
    
    logger.info(
        f"Background noise mask: {float(combined_mask.mean().values) * 100:.1f}% flagged"
    )
    
    return combined_mask


def _extract_db(value) -> float:
    """Extract dB value from string like '3.0dB' or numeric."""
    if isinstance(value, str):
        return float(value.replace("dB", "").strip())
    return float(value)
