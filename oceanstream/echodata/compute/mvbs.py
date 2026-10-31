"""Compute MVBS (Mean Volume Backscattering Strength).

MVBS provides gridded acoustic backscatter data, averaged over
range (depth) and time bins for efficient analysis and storage.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Optional, Union

from oceanstream.echodata.products import ARTEFACT_BITS, NO_BACKSCATTER_DB, open_product_uri

if TYPE_CHECKING:
    import xarray as xr

logger = logging.getLogger(__name__)

#: Index of the first vertical bin in MVBS/NASC products.
#:
#: Range/depth bins are built with ``np.arange(0, max, bin)`` regardless of the
#: transducer depth offset, so bin 0 spans ``[0, bin_size)`` while the data only
#: starts at ``depth_offset`` (1.9 m on Saildrone). Bin 0 therefore averages
#: over fewer samples than a full bin and under-integrates — measured at ~19%
#: for a 10 m NASC bin with a 1.9 m offset.
#:
#: Policy: bin 0 is kept in the stored products (so the grid stays regular and
#: reproducible) but is **excluded from integrated comparisons**. Consumers
#: should slice it off via ``isel({range_var: slice(SURFACE_BIN_INDEX + 1, None)})``
#: before summing over depth.
SURFACE_BIN_INDEX = 0


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
        sv_dataset = open_product_uri(sv_dataset)

    logger.info(f"Computing MVBS with range_bin={range_bin}, ping_time_bin={ping_time_bin}")

    # Prefer depth (accounts for transducer offset) over echo_range
    range_var = "depth" if "depth" in sv_dataset else "echo_range"

    # --- Replicate echopype compute_MVBS setup (api.py lines 100-140) ---
    ds_Sv, range_bin_val = _setup_and_validate(sv_dataset, range_var, range_bin, "left")

    # Range bins start at 0 regardless of transducer depth, so the first bin is
    # partial when ``depth`` is used. See SURFACE_BIN_INDEX for the policy.
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

    # Propagate lat/lon to MVBS if present in input, averaged per ping_time bin
    for var in ("latitude", "longitude"):
        has_var = var in sv_dataset.data_vars or var in sv_dataset.coords
        if has_var:
            da = sv_dataset[var]
            # Collapse multi-dim lat/lon (e.g., channel × ping_time) to 1D
            extra_dims = [d for d in da.dims if d != "ping_time"]
            if extra_dims:
                da = da.isel({d: 0 for d in extra_dims})
            mean_pos = da.resample(ping_time=ping_time_bin).mean()
            # Align to the MVBS ping_time coordinate
            ds_MVBS[var] = mean_pos.reindex(
                ping_time=ds_MVBS.ping_time, method="nearest"
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


#: Default gap-filling window, in MVBS bins: (ping-time bins, range bins). Much wider
#: than thick, because backscatter varies far more with depth than along the track.
DEFAULT_FILL_WINDOW = (11, 3)


def artefact_marker(sv_dataset: "xr.Dataset") -> "xr.Dataset":
    """*sv_dataset* with Sv 0 dB on the cells removed as artefacts and NaN elsewhere.

    Its MVBS is finite exactly where a bin held such a cell. That tells a bin the
    denoisers emptied from one that never held a measurement: past the range, above
    the surface exclusion or under the seabed (whose cells have no depth to bin by).
    Needs ``denoise_flags``, as :func:`oceanstream.echodata.products.integration_view`
    keeps them.
    """
    import xarray as xr

    keep = [name for name in ("Sv", "depth", "echo_range") if name in sv_dataset.data_vars]
    marker = sv_dataset[keep]
    artefact = (sv_dataset["denoise_flags"] & ARTEFACT_BITS) != 0
    marker["Sv"] = xr.zeros_like(sv_dataset["Sv"]).where(artefact)
    return marker


def fill_gaps(
    sv: "xr.DataArray",
    gaps: "xr.DataArray",
    window: tuple[int, int] = DEFAULT_FILL_WINDOW,
) -> tuple["xr.DataArray", "xr.DataArray"]:
    """MVBS Sv with *gaps* replaced by the mean of the valid bins around them.

    The window is *window* = (ping-time bins, range bins), centred on the gap; both
    must be odd. The mean is taken in the linear domain over the bins in the window
    that hold a value: bins at ``NO_BACKSCATTER_DB`` count as zero, NaN bins (other
    gaps among them) are left out. A gap with no such bin in its window stays NaN, and
    one whose window holds only zeros gets ``NO_BACKSCATTER_DB``.

    Returns the filled Sv and a boolean mask of the bins that were filled.
    """
    import numpy as np
    from scipy.ndimage import correlate

    ping_bins, range_bins = window
    if any(size < 1 or size % 2 == 0 for size in window):
        raise ValueError(f"The fill window must be two odd sizes of at least 1, not {window}.")
    range_dim = next(d for d in sv.dims if d not in ("channel", "ping_time"))
    kernel = np.ones(
        [ping_bins if d == "ping_time" else range_bins if d == range_dim else 1 for d in sv.dims]
    )

    values = sv.values
    linear = np.where(values <= NO_BACKSCATTER_DB, 0.0, 10 ** (values / 10))
    valid = np.isfinite(linear)
    # A direct sum, not a running one, so a window of zeros sums to exactly zero.
    total = correlate(np.where(valid, linear, 0.0), kernel, mode="constant")
    count = correlate(valid.astype(float), kernel, mode="constant")

    # Sums of ones and zeros, so the counts are exact.
    filled = np.asarray(gaps.transpose(*sv.dims).values, dtype=bool) & (count >= 1)
    mean = np.divide(total, count, out=np.zeros_like(total), where=filled)
    with np.errstate(divide="ignore"):
        mean_db = np.where(mean > 0, 10 * np.log10(mean), NO_BACKSCATTER_DB)
    out = sv.copy(data=np.where(filled, mean_db, values))
    mask = sv.copy(data=filled).rename("gap_filled")
    mask.attrs = {"long_name": "Bin filled from the mean of its neighbours"}
    return out, mask


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
    
    # Load Sv if path provided
    if isinstance(sv_dataset, (str, Path)):
        sv_dataset = open_product_uri(sv_dataset)
    
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
