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

    Uses echopype's internal flox reduction but builds the result Dataset
    directly from the flox output, bypassing echopype's xr.Dataset()
    constructor which is incompatible with xarray >= 2026.

    Args:
        sv_dataset: Sv xarray Dataset or path to Sv Zarr
        range_bin: Vertical bin size (e.g., "1m", "5m", "10m")
        ping_time_bin: Temporal bin size (e.g., "5s", "10s", "1min")
        output_path: Optional path to save result

    Returns:
        xarray.Dataset with gridded MVBS
    """
    import numpy as np
    import pandas as pd
    import xarray as xr
    from echopype.commongrid.utils import (
        _convert_bins_to_interval_index,
        _setup_and_validate,
        compute_raw_MVBS,
    )

    # Load Sv if path provided
    if isinstance(sv_dataset, (str, Path)):
        logger.info(f"Loading Sv from {sv_dataset}")
        sv_dataset = xr.open_zarr(sv_dataset)

    logger.info(f"Computing MVBS with range_bin={range_bin}, ping_time_bin={ping_time_bin}")

    # echopype expects range_var to be "echo_range" or "depth"
    range_var = "echo_range" if "echo_range" in sv_dataset else "depth"

    # --- Replicate echopype compute_MVBS setup (api.py lines 100-140) ---
    ds_Sv, range_bin_val = _setup_and_validate(sv_dataset, range_var, range_bin, "left")

    # Range bins
    range_var_max = float(ds_Sv[range_var].max(skipna=True))
    range_interval = np.arange(0, range_var_max + range_bin_val, range_bin_val)

    # Ping-time bins
    d_index = (
        ds_Sv["ping_time"]
        .resample(ping_time=ping_time_bin, skipna=True)
        .first()
        .indexes["ping_time"]
    )
    ping_interval = d_index.union([d_index[-1] + pd.Timedelta(ping_time_bin)]).values

    ping_interval = _convert_bins_to_interval_index(ping_interval, closed="left")
    range_interval = _convert_bins_to_interval_index(range_interval, closed="left")

    # Flox reduction — this produces the correct binned means
    raw_MVBS = compute_raw_MVBS(
        ds_Sv,
        range_interval,
        ping_interval,
        range_var=range_var,
        method="map-reduce",
        reindex=False,
        skipna=True,
        fill_value=np.nan,
    )

    # --- Build result Dataset from flox output ---
    # raw_MVBS has dims (channel, ping_time_bins, {range_var}_bins) with
    # IntervalIndex coords.  echopype's api.py line 145 builds a new Dataset
    # using raw_MVBS["Sv"].data with renamed dim labels, but the underlying
    # ping_time coord can have a different size than the binned Sv data,
    # causing a ValueError in xarray >= 2026.  Build the result from
    # materialised numpy arrays instead.
    range_bins_dim = f"{range_var}_bins"
    dim_0 = list(raw_MVBS.sizes.keys())[0]

    # Extract left-edge scalars and the Sv data as numpy
    ping_time_vals = np.array([v.left for v in raw_MVBS.ping_time_bins.values])
    range_vals = np.array([v.left for v in raw_MVBS[range_bins_dim].values])
    sv_data = raw_MVBS["Sv"].values  # materialise to numpy

    ds_MVBS = xr.Dataset(
        data_vars={"Sv": ([dim_0, "ping_time", range_var], sv_data)},
        coords={
            dim_0: raw_MVBS[dim_0].values,
            "ping_time": ping_time_vals,
            range_var: range_vals,
        },
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
