"""Impulse noise detection using multi-lag difference algorithm.

Detects short-duration spikes that appear in single pings,
using forward/backward difference comparisons with voting,
post-dilation, and shallow-exclusion support.

Ported from saildrone-data/saildrone/denoise/impulse_noise.py
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Tuple

import numpy as np

if TYPE_CHECKING:
    import xarray as xr

logger = logging.getLogger(__name__)


def _resolve_range_coord(sv_dataset: "xr.Dataset") -> tuple[str, "xr.DataArray"]:
    """Resolve the range coordinate name and values from an Sv dataset.

    Tries ``echo_range``, ``depth``, ``range_sample`` in priority order,
    falling back to synthesised sample indices when nothing else is available.

    Returns:
        Tuple of (range_coord_name, range_values_DataArray).
    """
    import xarray as xr

    for candidate in ("echo_range", "depth", "range_sample"):
        if candidate in sv_dataset.data_vars:
            return candidate, sv_dataset[candidate]
        if candidate in sv_dataset.coords:
            return candidate, sv_dataset[candidate]
        if candidate in sv_dataset.dims:
            return candidate, sv_dataset[candidate]

    # Absolute fallback – synthesise from first available non-time/channel dim
    other_dims = [d for d in sv_dataset["Sv"].dims if d not in ("channel", "ping_time")]
    if other_dims:
        dim_name = other_dims[0]
        n = sv_dataset.sizes[dim_name]
        vals = xr.DataArray(np.arange(n) * 0.1, dims=[dim_name])
        return dim_name, vals

    raise ValueError(
        "Cannot determine range coordinate. "
        "Dataset must contain echo_range, depth, or range_sample."
    )


def _dilate_mask_shift_or(
    mask: "xr.DataArray",
    *,
    pings: int = 0,
    samples: int = 0,
) -> "xr.DataArray":
    """Cheap rectangular dilation via shifted OR.

    Separable (time then depth) so cost is O(pings + samples) shifts,
    not O(pings * samples).  Stays fully Dask-lazy when the input is
    backed by a Dask array.
    """
    import dask.array as da

    if pings <= 0 and samples <= 0:
        return mask

    ping_dim = "ping_time"
    range_dim = [d for d in mask.dims if d != ping_dim][0]
    out = mask

    # Time dilation (+-pings)
    if pings > 0:
        acc = out.data
        for dt in range(1, pings + 1):
            acc = da.logical_or(acc, out.shift({ping_dim: dt}, fill_value=False).data)
            acc = da.logical_or(acc, out.shift({ping_dim: -dt}, fill_value=False).data)
        out = out.copy(data=acc)

    # Depth dilation (+-samples)
    if samples > 0:
        acc = out.data
        for dz in range(1, samples + 1):
            acc = da.logical_or(acc, out.shift({range_dim: dz}, fill_value=False).data)
            acc = da.logical_or(acc, out.shift({range_dim: -dz}, fill_value=False).data)
        out = out.copy(data=acc)

    return out


def impulse_noise_mask(
    sv_dataset: "xr.Dataset",
    params: dict,
) -> Tuple["xr.DataArray", "xr.DataArray"]:
    """Multi-lag impulse noise filter.

    Detects single-ping spikes by comparing each sample to neighbouring
    pings using forward and backward differences.  A sample is flagged
    when it exceeds both its forward *and* backward neighbour at the
    same lag by more than ``threshold_db``.

    Multiple lags can be tested simultaneously; the ``vote_k_of_n``
    parameter controls how many lags must agree before a sample is
    marked as impulsive.

    Args:
        sv_dataset: Single-channel Sv xarray Dataset (already isel'd to
            one channel by the caller).
        params: Dictionary of parameters:
            - range_coord (str): vertical coordinate name (auto-detected
              if absent)
            - vertical_bin_size (str|float): vertical binning size,
              e.g. ``"2m"`` or ``2.0``.  Default ``"2m"``.
            - ping_lags (list[int]): ping lags to compare, default ``[1]``
            - threshold_db (float): detection threshold (dB), default 10
            - exclude_shallow_above (float|None): ignore depths shallower
              than this (metres).  Default ``None``.
            - vote_k_of_n (int|None): minimum number of lags that must
              agree.  ``None`` or ``1`` means any single lag is enough.
            - post_dilate (dict|None): rectangular dilation after
              detection, e.g. ``{"pings": 1, "samples": 2}``.

    Returns:
        Tuple of ``(impulse_mask, unfeasible_mask)`` where both are
        boolean DataArrays.  ``impulse_mask`` is ``True`` where impulse
        noise was detected; ``unfeasible_mask`` is ``True`` for edge
        pings and NaN regions where detection was not possible.
    """
    import xarray as xr

    # ------------------------------------------------------------------
    # 1. Unpack & validate parameters
    # ------------------------------------------------------------------
    range_coord_param = params.get("range_coord", None)
    bin_cfg = params.get("vertical_bin_size", "2m")
    lags = tuple(sorted(set(params.get("ping_lags", (1,)))))
    thr_db = float(params.get("threshold_db", params.get("threshold", 10.0)))
    cut_above = params.get("exclude_shallow_above", None)
    vote_k = params.get("vote_k_of_n", None)
    post = params.get("post_dilate", None)

    if cut_above is not None:
        cut_above = float(cut_above)

    if any(lag < 1 for lag in lags):
        raise ValueError("ping_lags must contain positive integers.")

    Sv_db = sv_dataset["Sv"]

    # Resolve range coordinate
    if range_coord_param and range_coord_param in sv_dataset:
        range_coord = range_coord_param
        range_values = sv_dataset[range_coord]
    else:
        range_coord, range_values = _resolve_range_coord(sv_dataset)

    ping_dim = "ping_time"
    # The vertical dim is the first dim of range_values, or a fallback
    if range_values.dims:
        range_dim = range_values.dims[0]
    else:
        range_dim = range_coord

    # ------------------------------------------------------------------
    # 2. Parse vertical bin size
    # ------------------------------------------------------------------
    if isinstance(bin_cfg, (float, str)):
        s = str(bin_cfg).strip()
        if not s.isdigit() or s.lower().endswith("m"):
            window_m = float(s.rstrip("mM "))
            dz_arr = range_values.diff(range_dim)
            dz = float(dz_arr.median()) if dz_arr.size > 0 else 0.1
            bin_sz = max(1, int(round(window_m / abs(dz))))
        else:
            bin_sz = max(1, int(s))
    else:
        bin_sz = max(1, int(bin_cfg))

    # ------------------------------------------------------------------
    # 3. Vertical pooling in linear domain (optional)
    # ------------------------------------------------------------------
    # Ensure ascending range order
    if range_values.size > 1 and float(range_values[-1]) < float(range_values[0]):
        Sv_db = Sv_db.sortby(range_coord)
        range_values = range_values.sortby(range_coord)

    Sv_lin = 10.0 ** (Sv_db / 10.0)

    if bin_sz > 1 and range_dim in Sv_lin.dims:
        Sv_lin = (
            Sv_lin
            .coarsen({range_dim: bin_sz}, boundary="trim")
            .mean(skipna=True)
            .interp({range_dim: range_values}, method="nearest")
        )

    Sv_sm_db = 10.0 * np.log10(Sv_lin)

    # ------------------------------------------------------------------
    # 4. Shallow exclusion guard
    # ------------------------------------------------------------------
    if cut_above is not None:
        valid_depth = xr.broadcast(range_values >= cut_above, Sv_sm_db)[0]
        Sv_sm_db = Sv_sm_db.where(valid_depth)
    else:
        valid_depth = xr.ones_like(Sv_sm_db, dtype=bool)

    # ------------------------------------------------------------------
    # 5. Multi-lag forward & backward differences
    # ------------------------------------------------------------------
    count = xr.zeros_like(Sv_sm_db, dtype="uint8")
    for lag in lags:
        fwd = Sv_sm_db - Sv_sm_db.shift({ping_dim: -lag}, fill_value=-np.inf)
        bwd = Sv_sm_db - Sv_sm_db.shift({ping_dim: lag}, fill_value=-np.inf)
        hit = ((fwd > thr_db) & (bwd > thr_db)).astype("uint8")
        count = count.copy(data=(count.data + hit.data))

    if vote_k is None or int(vote_k) <= 1:
        impulse_mask = count > 0
    else:
        impulse_mask = count >= np.uint8(int(vote_k))

    # ------------------------------------------------------------------
    # 6. Unfeasible mask (edge pings + NaN)
    # ------------------------------------------------------------------
    max_lag = max(lags)
    n_pings = Sv_sm_db[ping_dim].size
    edge_vec = np.zeros(n_pings, dtype=bool)
    edge_vec[:max_lag] = True
    edge_vec[-max_lag:] = True

    mask_edges = xr.DataArray(
        edge_vec,
        coords={ping_dim: Sv_sm_db[ping_dim]},
        dims=ping_dim,
    ).broadcast_like(Sv_sm_db)

    mask_nan = Sv_sm_db.isnull()
    mask_unfeasible = mask_edges | mask_nan

    # ------------------------------------------------------------------
    # 7. Final tidy-up: exclude edges/NaN, optional post-dilation
    # ------------------------------------------------------------------
    impulse_mask = impulse_mask & ~mask_unfeasible

    if isinstance(post, dict):
        pd_p = int(post.get("pings", 0))
        pd_s = int(post.get("samples", 0))
        if pd_p > 0 or pd_s > 0:
            impulse_mask = _dilate_mask_shift_or(impulse_mask, pings=pd_p, samples=pd_s)
            impulse_mask = impulse_mask & ~mask_unfeasible & valid_depth

    pct = float(impulse_mask.mean()) * 100 if impulse_mask.size > 0 else 0.0
    logger.info(f"Impulse noise mask: {pct:.1f}% flagged")

    return impulse_mask, mask_unfeasible
