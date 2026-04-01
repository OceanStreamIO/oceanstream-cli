"""Transient noise detection algorithms.

Provides two methods for detecting transient interference in echosounder data:

1. **Fielding et al.** (default ``transient_noise_mask``): Upward-stepping
   kernel that detects noise propagating from the deep reference band upward.
   Uses Dask ``map_overlap`` for lazy parallel execution.

2. **Ryan et al. (2015)** (``transient_noise_mask_ryan``): Rolling 2-D
   percentile block comparison—simpler, suitable as fallback.

Ported from saildrone-data/saildrone/denoise/transient_noise.py
"""

from __future__ import annotations

import logging
import warnings
from functools import partial
from typing import TYPE_CHECKING, Dict, Tuple

import numpy as np

if TYPE_CHECKING:
    import dask.array as da
    import xarray as xr

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _nearest_idx(r: np.ndarray, x: float) -> int:
    """Index of element in *r* closest to *x*."""
    x = float(x)
    if x <= r[0]:
        return 0
    if x >= r[-1]:
        return r.size - 1
    return int(np.abs(r - x).argmin())


def _db2lin(x: np.ndarray) -> np.ndarray:
    return np.power(10.0, x / 10.0)


def _lin2db(x: np.ndarray) -> np.ndarray:
    with np.errstate(divide="ignore", invalid="ignore"):
        return 10.0 * np.log10(x)


def _mov_nanmedian_1d(y: np.ndarray, win: int) -> np.ndarray:
    """Centered moving NaN-median on 1-D array (length T).

    Uses ``bottleneck`` when available for speed, falls back to
    ``numpy.lib.stride_tricks.sliding_window_view``.
    """
    from numpy.lib.stride_tricks import sliding_window_view

    try:
        import bottleneck as bn

        if win <= 1 or y.size == 0:
            return y.copy()
        out = bn.move_median(y, window=win, min_count=win)
        pre = win // 2
        post = win - 1 - pre
        core = out[win - 1:]
        centered = np.pad(core, (pre, post), constant_values=np.nan)
        return centered[: y.size]
    except Exception:
        pass

    T = y.shape[0]
    if win <= 1 or T == 0:
        return y.copy()
    if T < win:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", category=RuntimeWarning)
            m = np.nanmedian(y)
        return np.full(T, m)
    w = sliding_window_view(y, win)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", category=RuntimeWarning)
        core = np.nanmedian(w, axis=1)
    pre = win // 2
    post = win - 1 - pre
    return np.pad(core, (pre, post), mode="constant", constant_values=np.nan)


def _mov_nanmedian_rows(Y: np.ndarray, win: int) -> np.ndarray:
    """Centered moving NaN-median along axis=1 for 2-D (S, T)."""
    from numpy.lib.stride_tricks import sliding_window_view

    try:
        import bottleneck as bn

        S, T = Y.shape
        if win <= 1 or T == 0:
            return Y.copy()
        out = np.empty_like(Y)
        for i in range(S):
            row = Y[i]
            tmp = bn.move_median(row, window=win, min_count=win)
            pre = win // 2
            post = win - 1 - pre
            core = tmp[win - 1:]
            centered = np.pad(core, (pre, post), constant_values=np.nan)
            out[i] = centered[:T]
        return out
    except Exception:
        pass

    S, T = Y.shape
    if win <= 1 or T == 0:
        return Y.copy()
    if T < win:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", category=RuntimeWarning)
            m = np.nanmedian(Y, axis=1, keepdims=True)
        return np.broadcast_to(m, (S, T)).copy()
    W = sliding_window_view(Y, win, axis=1)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", category=RuntimeWarning)
        core = np.nanmedian(W, axis=2)
    pre = win // 2
    post = win - 1 - pre
    return np.pad(core, ((0, 0), (pre, post)), mode="constant", constant_values=np.nan)


# ---------------------------------------------------------------------------
# Fielding kernel (runs inside dask.map_overlap)
# ---------------------------------------------------------------------------

def _fielding_mask_kernel(
    arr_dB: np.ndarray,
    up: int,
    lw: int,
    rmin: int,
    sf: int,
    n: int,
    thr0: float,
    thr1: float,
    maxts: float,
) -> np.ndarray:
    """NumPy kernel for the Fielding upward-stepping transient noise detector.

    Operates on a 2-D chunk of shape ``(Z, T)`` = ``(depth, time)``.

    Parameters
    ----------
    arr_dB : (Z, T) array in dB
    up, lw : deep reference band indices ``[up:lw)``
    rmin   : minimum depth index to consider masking (``exclude_above``)
    sf     : vertical step in samples
    n      : pings on each side (block width = ``2n+1``)
    thr0   : initial far-range threshold (dB)
    thr1   : upward stop threshold (dB)
    maxts  : max transient permitted (avoid seabed) (dB)

    Returns
    -------
    Boolean mask, shape ``(Z, T)``.  ``True`` = transient noise.
    """
    Z, T = arr_dB.shape
    up = max(0, min(up, Z - 1))
    lw = max(up + 1, min(lw, Z))
    rmin = max(0, min(rmin, Z - 1))
    sf = max(1, int(sf))
    n = max(1, int(n))
    win_t = 2 * n + 1

    arr_lin = _db2lin(arr_dB)

    # Deep-band ping statistics
    deep = arr_lin[up:lw, :]
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", category=RuntimeWarning)
        ping_med_lin = np.nanmedian(deep, axis=0)
        ping_p75_lin = np.nanpercentile(deep, 75, axis=0)

    ping_med_db = _lin2db(ping_med_lin)
    ping_p75_db = _lin2db(ping_p75_lin)
    blk_med_db = _lin2db(_mov_nanmedian_1d(ping_med_lin, win_t))

    # Initial flag at far range
    init_flag = (ping_p75_db < maxts) & ((ping_med_db - blk_med_db) > thr0)

    if not init_flag.any():
        return np.zeros((Z, T), dtype=bool)

    Smax = max((up - rmin) // sf, 0)
    if Smax == 0:
        cut = np.where(init_flag, up, Z)
        d = np.arange(Z)[:, None]
        return d >= cut[None, :]

    starts = up - sf * np.arange(1, Smax + 1)
    seg_med_lin = np.empty((Smax, T), dtype=arr_lin.dtype)
    for k, s0 in enumerate(starts):
        s1 = s0 + sf
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", category=RuntimeWarning)
            seg_med_lin[k] = np.nanmedian(arr_lin[s0:s1, :], axis=0)

    blk_seg_med_lin = _mov_nanmedian_rows(seg_med_lin, win_t)

    seg_med_db = _lin2db(seg_med_lin)
    blk_seg_med_db = _lin2db(blk_seg_med_lin)
    delta = seg_med_db - blk_seg_med_db

    meets = delta < thr1
    meets[:, ~init_flag] = False

    any_true = meets.any(axis=0)
    first_idx = np.argmax(meets, axis=0)
    s_stop = np.where(any_true, first_idx + 1, Smax)

    r0 = up - s_stop * sf
    r0 = np.maximum(r0, rmin)
    r0 = np.where(init_flag, r0, Z)

    d = np.arange(Z)[:, None]
    return d >= r0[None, :]


# ---------------------------------------------------------------------------
# Public API – Fielding method (default)
# ---------------------------------------------------------------------------

def transient_noise_mask(
    sv_dataset: "xr.Dataset",
    params: Dict,
) -> Tuple["xr.DataArray", "xr.DataArray"]:
    """Fielding et al. upward-stepping transient noise filter.

    Detects interference patterns that step upward from a deep reference
    band.  Runs lazily via ``dask.array.map_overlap`` so it scales to
    arbitrarily large files.

    Args:
        sv_dataset: Single-channel Sv xarray Dataset.
        params: Dictionary of parameters:
            - range_coord (str): vertical coordinate, default ``"depth"``
              (falls back to ``"echo_range"`` / ``"range_sample"``)
            - ping_window / n_pings (int): pings on each side, default 5
            - threshold / thr_dB (tuple|float): ``(thr0, thr1)`` or single
              value (dB), default ``(10.0, 7.0)``
            - exclude_above (float): min range to mask (m), default 250
            - ref_min / ref_max (float|None): deep-reference band limits (m).
              Auto-derived when ``None``.
            - jumps / depth_bin (float): vertical step size (m), default 5
            - maxts (float): max transient dB to accept, default −35

    Returns:
        Tuple of ``(transient_mask, unfeasible_mask)`` boolean DataArrays.
    """
    import dask.array as da
    import xarray as xr

    # Resolve range coordinate
    rng = params.get("range_coord", None)
    if rng is None:
        for candidate in ("depth", "echo_range", "range_sample"):
            if candidate in sv_dataset.dims or candidate in sv_dataset.coords:
                rng = candidate
                break
        if rng is None:
            rng = "depth"

    # When `depth` (or `echo_range`) is a data_var but not a dim/coord, the
    # actual dimension is likely `range_sample`.  We still want metre-based
    # range values for index calculations (exclude_above, ref band, jumps).
    _range_vals_1d = None  # Will hold 1-D metre values if available
    if rng not in sv_dataset.dims and rng not in sv_dataset.coords:
        # rng exists as a data_var — extract 1-D depth profile, use range_sample as dim
        if rng in sv_dataset.data_vars:
            range_da = sv_dataset[rng]
            if "ping_time" in range_da.dims:
                range_da = range_da.isel(ping_time=0, drop=True)
            if "channel" in range_da.dims:
                range_da = range_da.isel(channel=0, drop=True)
            _range_vals_1d = range_da.values.astype(float)
        rng = "range_sample"

    ping_win = int(params.get("ping_window", params.get("n_pings", 5)))

    # threshold / thr_dB → (thr0, thr1)
    thr = params.get("threshold", None)
    if thr is None:
        thr_db_val = params.get("thr_dB", None)
        if thr_db_val is not None:
            thr = (float(thr_db_val), max(2.0, float(thr_db_val) - 3.0))
        else:
            thr = (10.0, 7.0)

    excl_above = float(params.get("exclude_above", 250.0))
    ref_min_m = params.get("ref_min", None)
    ref_max_m = params.get("ref_max", None)
    jumps_m = float(params.get("jumps", params.get("depth_bin", 5.0)))
    maxts = float(params.get("maxts", -35.0))

    # Thresholds
    if isinstance(thr, (tuple, list)) and len(thr) == 2:
        thr0, thr1 = float(thr[0]), float(thr[1])
    else:
        thr0 = float(thr)
        thr1 = max(2.0, thr0 - 3.0)

    # Ensure 2-D (T,Z) and reasonable chunks
    Sv_db_TZ = sv_dataset["Sv"].transpose("ping_time", rng)
    Sv_db_ZT = Sv_db_TZ.transpose(rng, "ping_time")
    Sv_db_ZT = Sv_db_ZT.chunk(
        {"ping_time": max(1024, 4 * (2 * ping_win + 1)), rng: -1}
    )

    # Use metre-based depth profile when available, else raw dim values
    if _range_vals_1d is not None:
        r = _range_vals_1d
    else:
        r = sv_dataset[rng].values.astype(float)
    Z = r.size

    # Default deep reference band
    if ref_min_m is None or ref_max_m is None:
        max_r = float(r[-1])
        ref_min_m = max(excl_above + 50.0, min(150.0, max_r * 0.5))
        ref_max_m = min(max_r, ref_min_m + 200.0)

    up = _nearest_idx(r, float(ref_min_m))
    lw = _nearest_idx(r, float(ref_max_m)) + 1
    rmin = _nearest_idx(r, excl_above)
    sf = max(1, _nearest_idx(r, float(jumps_m)) or 1)

    # Run the kernel lazily with time overlap
    da_in = da.asarray(Sv_db_ZT.data)
    mask_da = da.map_overlap(
        _fielding_mask_kernel,
        da_in,
        depth={1: ping_win},
        boundary=np.nan,
        trim=True,
        meta=np.empty((0, 0), dtype=bool),
        up=up,
        lw=lw,
        rmin=rmin,
        sf=sf,
        n=ping_win,
        thr0=thr0,
        thr1=thr1,
        maxts=maxts,
    )

    mask_T = xr.DataArray(
        mask_da,
        dims=(rng, "ping_time"),
        coords={rng: Sv_db_ZT[rng], "ping_time": Sv_db_ZT["ping_time"]},
    )
    mask_T = mask_T.transpose("ping_time", rng)

    # Unfeasible mask: edge pings + deep-layer all-NaN
    edge = xr.zeros_like(Sv_db_TZ.isel({rng: 0}), dtype=bool)
    if ping_win > 0:
        edge[:ping_win] = True
        edge[-ping_win:] = True

    deep = sv_dataset["Sv"].transpose(rng, "ping_time").isel({rng: slice(up, lw)})
    deep_any = xr.apply_ufunc(
        np.isfinite, deep, dask="parallelized", output_dtypes=[bool]
    ).any(rng)
    deep_allnan = ~deep_any

    mask_U_1d = edge | deep_allnan
    mask_U = xr.broadcast(mask_U_1d, mask_T)[0]

    # Restrict to zone >= exclude_above
    in_zone = sv_dataset[rng] >= excl_above
    mask_T = mask_T.where(in_zone, False)
    mask_U = mask_U.where(in_zone, False)

    pct = float(mask_T.mean()) * 100 if mask_T.size > 0 else 0.0
    logger.info(f"Transient noise mask (Fielding): {pct:.1f}% flagged")

    return mask_T, mask_U


# ---------------------------------------------------------------------------
# Rolling-percentile helper for Ryan method
# ---------------------------------------------------------------------------

def _rolling_nanpercentile(arr, *, q: float, axis=None):
    """Percentile reducer for ``xarray.rolling.reduce``.

    Works with both NumPy and Dask arrays and properly drops rolling
    axes to avoid stray ``_rolling_dim_*`` dimensions.
    """
    import dask.array as da

    if isinstance(arr, da.Array):
        if axis is None:
            raise ValueError("rolling_nanpercentile requires 'axis' from xarray")
        if np.isscalar(axis):
            axis_tuple = (int(axis) % arr.ndim,)
        else:
            axis_tuple = tuple(int(a) % arr.ndim for a in axis)

        def _nanpct(x, q):
            with np.errstate(all="ignore"):
                return np.nanpercentile(x, q, axis=axis_tuple)

        return da.map_blocks(_nanpct, arr, q, dtype=arr.dtype, drop_axis=axis_tuple)

    with np.errstate(all="ignore"):
        return np.nanpercentile(arr, q, axis=axis)


# ---------------------------------------------------------------------------
# Public API – Ryan method (alternative)
# ---------------------------------------------------------------------------

def transient_noise_mask_ryan(
    sv_dataset: "xr.Dataset",
    params: Dict,
) -> Tuple["xr.DataArray", "xr.DataArray"]:
    """Ryan et al. (2015) transient-noise filter for a single channel.

    Uses a 2-D rolling-percentile block comparison.  Simpler than Fielding
    but effective as a secondary filter.

    Args:
        sv_dataset: Single-channel Sv xarray Dataset.
        params: Dictionary of parameters:
            - range_coord (str): vertical coordinate, default ``"echo_range"``
            - ping_window (int): half-width in pings, default 5
            - range_window (int): half-width in samples, default 3
            - threshold (float): dB above block statistic, default 6
            - exclude_above (float): min range to apply (m), default 250
            - percentile (float): percentile for block, default 15
            - min_pings / min_samples: override min_periods

    Returns:
        Tuple of ``(transient_mask, unfeasible_mask)`` boolean DataArrays.
    """
    import xarray as xr

    rng_var = params.get("range_coord", None)
    if rng_var is None:
        for candidate in ("echo_range", "depth", "range_sample"):
            if candidate in sv_dataset.dims or candidate in sv_dataset.coords:
                rng_var = candidate
                break
        if rng_var is None:
            rng_var = "echo_range"

    half_ping = params.get("ping_window", 5)
    half_range = params.get("range_window", 3)
    thr_db = params.get("threshold", 6.0)
    excl_above = params.get("exclude_above", 250.0)
    perc = params.get("percentile", 15)
    min_pings = params.get("min_pings", 2 * half_ping + 1)
    min_samples = params.get("min_samples", 2 * half_range + 1)

    Sv_db = sv_dataset["Sv"]
    range_values = sv_dataset[rng_var]
    ping_dim = "ping_time"
    range_dim = range_values.dims[0] if range_values.dims else rng_var

    block_ping = 2 * half_ping + 1
    block_range = 2 * half_range + 1
    min_periods = min_pings * min_samples

    in_zone = range_values >= excl_above

    Sv_lin = 10.0 ** (Sv_db / 10.0)

    rolled = Sv_lin.rolling(
        {ping_dim: block_ping, range_dim: block_range},
        center=True,
        min_periods=min_periods,
    )

    pct_func = partial(_rolling_nanpercentile, q=perc)
    block_lin = rolled.reduce(pct_func, keep_attrs=True)

    block_db = 10.0 * np.log10(block_lin)

    diff_db = Sv_db - block_db
    mask_transient = (diff_db > thr_db) & in_zone
    mask_unfeasible = block_db.isnull() & in_zone

    mask_transient = mask_transient.where(in_zone, False)
    mask_unfeasible = mask_unfeasible.where(in_zone, False)

    pct_val = float(mask_transient.mean()) * 100 if mask_transient.size > 0 else 0.0
    logger.info(f"Transient noise mask (Ryan): {pct_val:.1f}% flagged")

    return mask_transient, mask_unfeasible

