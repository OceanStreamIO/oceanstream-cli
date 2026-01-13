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
    # Detect the dimension for range (always range_sample for echopype output)
    if "range_sample" in sv_dataset.dims:
        range_dim = "range_sample"
    elif "depth" in sv_dataset.dims:
        range_dim = "depth"
    elif "echo_range" in sv_dataset.dims:
        range_dim = "echo_range"
    else:
        # Fallback - take last non-time, non-channel dim
        range_dim = [d for d in sv_dataset["Sv"].dims if d not in ["channel", "ping_time"]][0]
    
    range_coord = params.get("range_coord", range_dim)
    
    sound_absorption = params.get("sound_absorption", 0.001)
    range_window = params.get("range_window", 20)
    ping_window = params.get("ping_window", 50)
    background_noise_max = _extract_db(params.get("background_noise_max", "-125.0dB"))
    snr_threshold = _extract_db(params.get("SNR_threshold", "3.0dB"))
    
    # Get Sv and range values
    Sv = sv_dataset["Sv"]
    
    # Get range values in METERS (critical for TVG computation!)
    # echo_range is the actual range in meters - check data_vars first, then coords
    if "echo_range" in sv_dataset.data_vars:
        # echopype stores echo_range as a 3D data_var (channel, ping_time, range_sample)
        range_values = sv_dataset["echo_range"]
        logger.debug("Using echo_range from data_vars (meters)")
    elif "echo_range" in sv_dataset.coords:
        range_values = sv_dataset["echo_range"]
        logger.debug("Using echo_range from coords (meters)")
    elif "depth" in sv_dataset.coords:
        range_values = sv_dataset["depth"]
        logger.debug("Using depth from coords (meters)")
    elif "depth" in sv_dataset.data_vars:
        range_values = sv_dataset["depth"]
        logger.debug("Using depth from data_vars (meters)")
    else:
        # Last resort: estimate range from range_sample
        # Typical EK80 sample interval is ~0.1m depending on sound speed
        n_samples = sv_dataset.sizes.get(range_dim, 1000)
        # Estimate sample spacing (approx 0.18m for 1300m over 7218 samples)
        sample_spacing = 0.18  # meters per sample (typical EK80 CW)
        range_values = xr.DataArray(
            np.arange(n_samples) * sample_spacing,
            dims=[range_dim]
        )
        logger.warning(f"No echo_range found, estimating from {range_dim} with {sample_spacing}m spacing")
    
    # Handle auto range window  
    if range_window == "auto":
        # Estimate range resolution
        if hasattr(range_values, 'diff'):
            try:
                dr = float(range_values.isel({range_dim: slice(0, 100)}).diff(range_dim).median())
                range_window = max(1, int(round(10.0 / dr)))  # ~10m window
            except Exception:
                range_window = 50  # fallback
        else:
            range_window = 50
    
    # Remove TVG: 20 log10(r) + 2αr
    r_safe = xr.where(range_values > 0, range_values, np.nan)
    tvg = 20.0 * np.log10(r_safe) + 2.0 * sound_absorption * r_safe
    Sv_flat_db = Sv - tvg
    
    # Convert to linear domain for block averaging
    power_lin = 10.0 ** (Sv_flat_db / 10.0)
    
    # Coarsen to block averages using the dimension name
    binned_lin = power_lin.coarsen(
        ping_time=ping_window,
        **{range_dim: range_window},
        boundary="pad",
    ).mean()
    
    # Convert to dB for depth statistic
    binned_db = 10.0 * np.log10(binned_lin.where(binned_lin > 0))
    
    # Rechunk for range operations
    if hasattr(binned_db, "chunk") and range_dim in binned_db.dims:
        binned_db = binned_db.chunk({range_dim: -1})
    
    # Get noise estimate from range minimum (deepest, quietest part)
    noise_1d_db = binned_db.min(dim=range_dim, skipna=True)
    
    # Align ping_time indices
    noise_1d_db = noise_1d_db.assign_coords(
        ping_time=ping_window * np.arange(noise_1d_db.sizes["ping_time"])
    )
    power_lin = power_lin.assign_coords(
        ping_time=np.arange(power_lin.sizes["ping_time"])
    )
    
    # Cap noise floor (more negative = gentler; less negative = more aggressive)
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
    
    # Broadcast noise to full shape using range_dim
    if range_dim in noise_interp.dims:
        noise_broadcast = noise_interp
    else:
        # Need to expand along range_dim
        n_range = sv_dataset.sizes[range_dim]
        noise_broadcast = noise_interp.expand_dims({range_dim: n_range})
        # Ensure dimension order matches power_lin
        if set(noise_broadcast.dims) == set(power_lin.dims):
            noise_broadcast = noise_broadcast.transpose(*power_lin.dims)
    
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
    
    # Broadcast back to original ping_time using integer indexing
    # (the mask was computed with integer ping_time indices)
    original_n_pings = sv_dataset.sizes["ping_time"]
    mask_n_pings = combined_mask.sizes["ping_time"]
    
    if mask_n_pings != original_n_pings:
        # Use integer indices for reindexing
        orig_indices = np.arange(original_n_pings)
        combined_mask_float = combined_mask.astype(float)
        combined_mask_float = combined_mask_float.interp(
            ping_time=orig_indices,
            method="nearest",
            kwargs={"fill_value": 1.0},
        )
        combined_mask = combined_mask_float > 0.5
    
    # Reassign original ping_time coordinate
    combined_mask = combined_mask.assign_coords(
        ping_time=sv_dataset.ping_time.values
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
