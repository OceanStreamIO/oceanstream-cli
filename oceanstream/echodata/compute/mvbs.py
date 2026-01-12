"""Compute MVBS (Mean Volume Backscattering Strength).

MVBS provides gridded acoustic backscatter data, averaged over
range (depth) and time bins for efficient analysis and storage.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Optional, Union

if TYPE_CHECKING:
    import xarray as xr

logger = logging.getLogger(__name__)


def compute_mvbs(
    sv_dataset: Union[Path, "xr.Dataset"],
    range_bin: str = "1m",
    ping_time_bin: str = "5s",
    output_path: Optional[Path] = None,
) -> "xr.Dataset":
    """
    Compute Mean Volume Backscattering Strength (MVBS).
    
    MVBS provides spatially and temporally averaged backscatter data,
    reducing noise and data volume while preserving biological patterns.
    
    Args:
        sv_dataset: Sv xarray Dataset or path to Sv Zarr
        range_bin: Vertical bin size (e.g., "1m", "5m", "10m")
        ping_time_bin: Temporal bin size (e.g., "5s", "10s", "1min")
        output_path: Optional path to save result
        
    Returns:
        xarray.Dataset with gridded MVBS
        
    Example:
        mvbs = compute_mvbs(sv_path, range_bin="1m", ping_time_bin="5s")
    """
    try:
        import echopype as ep
        import xarray as xr
    except ImportError as e:
        raise ImportError("echopype and xarray required for MVBS computation") from e
    
    # Load Sv if path provided
    if isinstance(sv_dataset, (str, Path)):
        logger.info(f"Loading Sv from {sv_dataset}")
        sv_dataset = xr.open_zarr(sv_dataset)
    
    logger.info(f"Computing MVBS with range_bin={range_bin}, ping_time_bin={ping_time_bin}")
    
    # echopype compute_MVBS expects range_var to be "echo_range" or "depth"
    # Determine which range variable is available
    range_var = "depth" if "depth" in sv_dataset.dims or "depth" in sv_dataset.coords else "echo_range"
    
    ds_MVBS = ep.commongrid.compute_MVBS(
        sv_dataset,
        range_var=range_var,
        range_bin=range_bin,
        ping_time_bin=ping_time_bin,
    )
    
    # Add attributes
    ds_MVBS.attrs["processing"] = "MVBS computed with oceanstream"
    ds_MVBS.attrs["range_bin"] = range_bin
    ds_MVBS.attrs["ping_time_bin"] = ping_time_bin
    
    # Save if output path provided
    if output_path:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        logger.info(f"Saving MVBS to {output_path}")
        ds_MVBS.to_zarr(output_path, mode="w")
        
        # Consolidate metadata
        import zarr
        zarr.consolidate_metadata(output_path)
    
    return ds_MVBS


def compute_mvbs_denoised(
    sv_dataset: Union[Path, "xr.Dataset"],
    noise_mask: "xr.DataArray",
    range_bin: str = "1m",
    ping_time_bin: str = "5s",
    output_path: Optional[Path] = None,
) -> "xr.Dataset":
    """
    Compute MVBS from denoised Sv data.
    
    Applies noise mask before computing MVBS to exclude
    contaminated samples.
    
    Args:
        sv_dataset: Sv xarray Dataset or path
        noise_mask: Boolean mask (True = noise to exclude)
        range_bin: Vertical bin size
        ping_time_bin: Temporal bin size
        output_path: Optional path to save result
        
    Returns:
        xarray.Dataset with gridded MVBS from denoised data
    """
    import xarray as xr
    
    # Load Sv if path provided
    if isinstance(sv_dataset, (str, Path)):
        sv_dataset = xr.open_zarr(sv_dataset)
    
    # Apply mask - set masked values to NaN
    import numpy as np
    sv_denoised = sv_dataset.copy()
    sv_denoised["Sv"] = sv_dataset["Sv"].where(~noise_mask, np.nan)
    
    return compute_mvbs(
        sv_denoised,
        range_bin=range_bin,
        ping_time_bin=ping_time_bin,
        output_path=output_path,
    )
