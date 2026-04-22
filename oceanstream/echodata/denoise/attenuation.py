"""Attenuated signal detection.

Detects depth layers with abnormally low backscatter compared
to surrounding pings, indicating signal attenuation from
bubbles, dense plankton, or equipment issues.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Tuple

import numpy as np

if TYPE_CHECKING:
    import xarray as xr

logger = logging.getLogger(__name__)


def attenuation_mask(
    sv_dataset: "xr.Dataset",
    params: dict,
) -> Tuple["xr.DataArray", "xr.DataArray"]:
    """Attenuated signal detector.

    Identifies pings where the median Sv in a reference depth band
    drops significantly below the block median of surrounding pings,
    indicating attenuation (e.g. from bubbles under the hull).

    Args:
        sv_dataset: Single-channel Sv xarray Dataset.
        params: Dictionary of parameters:
            - range_coord (str): vertical coordinate (auto-detected)
            - upper_limit_sl (float): upper depth limit (m), default 180
            - lower_limit_sl (float): lower depth limit (m), default 280
            - num_side_pings (int): pings on each side for median, default 15
            - threshold (float): detection threshold (dB), default 5

    Returns:
        Tuple of ``(attenuation_mask, unfeasible_mask)`` boolean DataArrays.
        ``attenuation_mask`` is ``True`` for entire pings that are attenuated.
        ``unfeasible_mask`` is ``True`` where the comparison could not be
        performed (NaN ping/block medians).
    """
    import xarray as xr
    import dask.array as da

    # ------------------------------------------------------------------
    # 1. Unpack parameters
    # ------------------------------------------------------------------
    upper_limit = params.get("upper_limit_sl", 180.0)
    lower_limit = params.get("lower_limit_sl", 280.0)
    num_side_pings = params.get("num_side_pings", 15)
    threshold = abs(params.get("threshold", 5.0))

    # ------------------------------------------------------------------
    # 2. Resolve range coordinate / dimension
    # ------------------------------------------------------------------
    range_var_name = params.get("range_coord", None)

    if range_var_name is None:
        for candidate in ("echo_range", "depth", "range_sample"):
            if candidate in sv_dataset.data_vars or candidate in sv_dataset.coords:
                range_var_name = candidate
                break
        if range_var_name is None:
            range_var_name = "echo_range"

    if "range_sample" in sv_dataset.dims:
        vertical_dim = "range_sample"
    elif "depth" in sv_dataset.dims:
        vertical_dim = "depth"
    elif "echo_range" in sv_dataset.dims:
        vertical_dim = "echo_range"
    else:
        vertical_dim = [
            d for d in sv_dataset["Sv"].dims if d not in ("channel", "ping_time")
        ][0]

    Sv = sv_dataset["Sv"]

    # Get range values in metres
    if range_var_name in sv_dataset.data_vars:
        range_values = sv_dataset[range_var_name]
    elif range_var_name in sv_dataset.coords:
        range_values = sv_dataset[range_var_name]
    else:
        n_samples = sv_dataset.sizes.get(vertical_dim, 1000)
        sample_spacing = 0.18
        range_values = xr.DataArray(
            np.arange(n_samples) * sample_spacing, dims=[vertical_dim]
        )
        logger.warning(f"No {range_var_name} found, estimating from {vertical_dim}")

    # ------------------------------------------------------------------
    # 3. Check range coverage
    # ------------------------------------------------------------------
    if (upper_limit > float(range_values.max())) or (
        lower_limit < float(range_values.min())
    ):
        empty = xr.zeros_like(Sv, dtype=bool)
        return empty, empty

    # ------------------------------------------------------------------
    # 4. Compute per-ping MEDIAN Sv in the reference depth band
    # ------------------------------------------------------------------
    in_layer = (range_values >= upper_limit) & (range_values <= lower_limit)
    ping_median = Sv.where(in_layer).median(dim=vertical_dim, skipna=True)

    # ------------------------------------------------------------------
    # 5. Block median across pings (centre-aligned)
    # ------------------------------------------------------------------
    block_width = 2 * num_side_pings + 1

    reducer = da.nanmedian if hasattr(Sv.data, "dask") else np.nanmedian

    block_median = (
        ping_median.rolling(
            ping_time=block_width, center=True, min_periods=block_width
        ).reduce(reducer)
    )

    # ------------------------------------------------------------------
    # 6. Build masks
    # ------------------------------------------------------------------
    diff_db = ping_median - block_median
    ping_flag = diff_db < -threshold

    ping_unfeasible = ping_median.isnull() | block_median.isnull()

    mask_as = ping_flag.broadcast_like(Sv)
    mask_failed = ping_unfeasible.broadcast_like(Sv)

    pct = float(ping_flag.mean()) * 100 if ping_flag.size > 0 else 0.0
    logger.info(f"Attenuation mask: {pct:.1f}% of pings flagged")

    return mask_as, mask_failed
