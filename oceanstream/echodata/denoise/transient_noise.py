"""Transient noise detection algorithms.

Provides three methods for detecting transient interference in echosounder data:

1. **Fielding et al.** (default ``transient_noise_mask``): Upward-stepping
   kernel that detects noise propagating from the deep reference band upward.
   Uses Dask ``map_overlap`` for lazy parallel execution.

2. **Ryan et al. (2015), equation (3)** (``transient_noise_mask_ryan2015``): averages
   over a few metres per ping against a low percentile of the averages in a resampling
   cell of pings by metres. Selected per preset with ``detector = "ryan2015"``.

3. **Ryan-style bin variant** (``transient_noise_mask_ryan``): per depth bin, a ping's
   low percentile against the median of the same statistic over neighbouring pings.
   Selected per preset with ``detector = "ryan"``.

"""

from __future__ import annotations

import logging
import warnings
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
    """Index of the finite element of *r* closest to *x*."""
    return int(np.nanargmin(np.abs(np.asarray(r, dtype=float) - float(x))))


def _range_profile(range_da: "xr.DataArray") -> np.ndarray:
    """One range (m) per sample from a range variable, from the first ping that has them."""
    if "channel" in range_da.dims:
        range_da = range_da.isel(channel=0, drop=True)
    if "ping_time" in range_da.dims:
        values = range_da.transpose("ping_time", ...).values
        has = np.isfinite(values).any(axis=1)
        return np.asarray(values[int(np.argmax(has))] if has.any() else values[0], dtype=float)
    return np.asarray(range_da.values, dtype=float)


def _db2lin(x: np.ndarray) -> np.ndarray:
    return np.power(10.0, x / 10.0)


def _lin2db(x: np.ndarray) -> np.ndarray:
    with np.errstate(divide="ignore", invalid="ignore"):
        return 10.0 * np.log10(x)


def _mov_nanmedian_1d(y: np.ndarray, win: int) -> np.ndarray:
    """Centered moving NaN-median on 1-D array (length T).

    A window's median is that of its finite values, so a NaN ping (removed by an
    earlier filter under the cascade) does not blank the medians around it.
    Uses ``bottleneck`` when available for speed, falls back to
    ``numpy.lib.stride_tricks.sliding_window_view``.
    """
    from numpy.lib.stride_tricks import sliding_window_view

    try:
        import bottleneck as bn

        if win <= 1 or y.size == 0:
            return y.copy()
        out = bn.move_median(y, window=win, min_count=1)
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
    """Centered moving NaN-median along axis=1 for 2-D (S, T), NaN-tolerant like
    :func:`_mov_nanmedian_1d`."""
    from numpy.lib.stride_tricks import sliding_window_view

    try:
        import bottleneck as bn

        S, T = Y.shape
        if win <= 1 or T == 0:
            return Y.copy()
        out = np.empty_like(Y)
        for i in range(S):
            row = Y[i]
            tmp = bn.move_median(row, window=win, min_count=1)
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
            - thr_dB_stop (float): ``thr1``, the excess each step up must keep;
              without it ``max(2, thr_dB - 3)``
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

    # Every depth setting is in metres, so the samples' metres are needed. When `depth`
    # (or `echo_range`) is a data_var but not a dim/coord, the actual dimension is
    # likely `range_sample`, a sample index; its metres come from that variable, or
    # from whichever range variable the dataset has.
    _range_vals_1d = None  # Will hold 1-D metre values if available
    if rng not in sv_dataset.dims and rng not in sv_dataset.coords:
        if rng in sv_dataset.data_vars:
            _range_vals_1d = _range_profile(sv_dataset[rng])
        rng = "range_sample"
    if rng == "range_sample" and _range_vals_1d is None:
        for name in ("depth", "echo_range"):
            if name in sv_dataset.data_vars:
                _range_vals_1d = _range_profile(sv_dataset[name])
                break

    ping_win = int(params.get("ping_window", params.get("n_pings", 5)))

    # threshold / thr_dB (+ thr_dB_stop) → (thr0, thr1)
    thr = params.get("threshold", None)
    if thr is None:
        thr_db_val = params.get("thr_dB", None)
        if thr_db_val is not None:
            stop = params.get("thr_dB_stop", None)
            thr = (
                float(thr_db_val),
                float(stop) if stop is not None else max(2.0, float(thr_db_val) - 3.0),
            )
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
        max_r = float(np.nanmax(r))
        ref_min_m = max(excl_above + 50.0, min(150.0, max_r * 0.5))
        ref_max_m = min(max_r, ref_min_m + 200.0)

    up = _nearest_idx(r, float(ref_min_m))
    lw = _nearest_idx(r, float(ref_max_m)) + 1
    rmin = _nearest_idx(r, excl_above)
    # The step in samples is its height over the sample spacing; the index of the sample
    # nearest jumps_m is that only when the range starts at 0.
    steps = np.diff(r)
    dz = float(np.nanmedian(steps)) if np.isfinite(steps).any() else 1.0
    sf = max(1, int(round(jumps_m / dz))) if dz > 0 else 1

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

    # Restrict to zone >= exclude_above, in metres (not the sample index)
    in_zone = xr.DataArray(r >= excl_above, dims=(rng,), coords={rng: mask_T[rng]})
    mask_T = mask_T.where(in_zone, False)
    mask_U = mask_U.where(in_zone, False)

    pct = float(mask_T.mean()) * 100 if mask_T.size > 0 else 0.0
    logger.info(f"Transient noise mask (Fielding): {pct:.1f}% flagged")

    return mask_T, mask_U


# ---------------------------------------------------------------------------
# Public API – Ryan method (alternative)
# ---------------------------------------------------------------------------

def transient_noise_mask_ryan(
    sv_dataset: "xr.Dataset",
    params: Dict,
) -> Tuple["xr.DataArray", "xr.DataArray"]:
    """Ryan et al. (2015) transient-noise filter for a single channel.

    Ryan, Downie, Kloser and Keith (2015), ICES J. Mar. Sci. 72(8): 2482-2493, section 2.3.
    Below ``exclude_above`` the water column is cut into depth bins of ``depth_bin`` metres.
    In each bin a ping's ``percentile``-th percentile of linear Sv is compared with the
    median of the same statistic over the ``n_pings`` pings either side; where the ping
    exceeds it by more than ``thr_dB`` the ping's bin is flagged. A ping whose whole column
    is raised, by interference or a noise burst, stands out in every bin, while a school
    or layer that persists over neighbouring pings does not.

    Args:
        sv_dataset: Single-channel Sv dataset with ``echo_range`` (1-D or per ping).
        params: Dictionary of parameters:
            - exclude_above (float): shallowest range filtered (m), default 250
            - depth_bin (float): bin height (m), default 20
            - n_pings (int): pings either side in the comparison block, default 50
              (``ping_window`` is accepted as an alias)
            - thr_dB (float): margin above the block statistic (dB), default 12
              (``threshold`` is accepted as an alias)
            - percentile (float): statistic within a bin, default 15

    Returns:
        Tuple of ``(transient_mask, unfeasible_mask)`` boolean DataArrays shaped like Sv.
        ``unfeasible_mask`` marks bins with data whose block statistic could not be formed.
    """
    import pandas as pd
    import xarray as xr

    excl_above = float(params.get("exclude_above", 250.0))
    depth_bin = float(params.get("depth_bin", 20.0))
    half = int(params.get("n_pings", params.get("ping_window", 50)))
    thr_db = float(params.get("thr_dB", params.get("threshold", 12.0)))
    perc = float(params.get("percentile", 15.0))

    sv = sv_dataset["Sv"]
    ping_dim = "ping_time"
    range_dim = next(d for d in sv.dims if d != ping_dim)
    data = sv.transpose(ping_dim, range_dim).values
    ranges = _ryan_ranges(sv_dataset, range_dim)

    in_zone = np.isfinite(ranges) & (ranges >= excl_above)
    bin_of = np.full(ranges.shape, -1, dtype=int)
    bin_of[in_zone] = np.floor((ranges[in_zone] - excl_above) / depth_bin).astype(int)
    n_bins = int(bin_of.max()) + 1 if in_zone.any() else 0

    mask = np.zeros(data.shape, dtype=bool)
    unfeasible = np.zeros(data.shape, dtype=bool)
    if n_bins and data.shape[0]:
        linear = 10.0 ** (data / 10.0)
        stat = np.full((data.shape[0], n_bins), np.nan)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)  # all-NaN bins stay NaN
            for b in range(n_bins):
                cols = bin_of == b
                if cols.any():
                    stat[:, b] = np.nanpercentile(linear[:, cols], perc, axis=1)
            block = (
                pd.DataFrame(stat)
                .rolling(2 * half + 1, center=True, min_periods=half + 1)
                .median()
                .to_numpy()
            )
            excess = 10.0 * np.log10(stat) - 10.0 * np.log10(block)
        flagged = np.nan_to_num(excess, nan=-np.inf) > thr_db
        cannot = np.isfinite(stat) & ~np.isfinite(block)
        cols = np.flatnonzero(in_zone)
        mask[:, cols] = flagged[:, bin_of[cols]]
        unfeasible[:, cols] = cannot[:, bin_of[cols]]
        mask &= np.isfinite(data)

    dims = (ping_dim, range_dim)
    coords = {d: sv[d] for d in dims if d in sv.coords}
    mask_da = xr.DataArray(mask, dims=dims, coords=coords).transpose(*sv.dims)
    unfeasible_da = xr.DataArray(unfeasible, dims=dims, coords=coords).transpose(*sv.dims)

    pct_val = float(mask.mean()) * 100 if mask.size else 0.0
    logger.info(f"Transient noise mask (Ryan): {pct_val:.2f}% flagged")
    return mask_da, unfeasible_da


def _metres(value) -> float:
    """``"5m"`` or ``5`` → 5.0."""
    return float(str(value).strip().rstrip("mM ")) if isinstance(value, str) else float(value)


def transient_noise_mask_ryan2015(
    sv_dataset: "xr.Dataset",
    params: Dict,
) -> Tuple["xr.DataArray", "xr.DataArray"]:
    """Ryan et al. (2015) transient-noise filter, their equation (3), for a single channel.

    Ryan, Downie, Kloser and Keith (2015), ICES J. Mar. Sci. 72(8): 2482-2493, Table 5. The
    echogram is resampled to cells of ``block_pings`` pings by ``block_height`` metres, and
    the ``percentile``-th percentile in a cell is the reference Sv~mn; a value more than
    ``threshold_db`` above the reference of its cell is transient noise. Nothing above
    ``exclude_above`` is filtered.

    The values compared are Sv averaged (linearly) over ``vertical_bin_size`` metres in each
    ping, the impulse filter's resampling, and the reference is the percentile of those
    averages in the cell. Single samples would not do: their speckle is exponential, and
    12 dB over its 15th percentile is exceeded by 7.6 % of the samples of a perfectly
    steady layer, which would be cut from the top of every layer below ``exclude_above``.
    A flagged average removes the samples it was made from.

    Args:
        sv_dataset: Single-channel Sv dataset with ``echo_range`` (1-D or per ping).
        params: Dictionary of parameters:
            - exclude_above (float): shallowest range filtered (m), default 250
            - block_pings (int): cell width m, in pings, default 50
            - block_height (float): cell height n (m), default 20
            - percentile (float): reference percentile p, default 15
            - threshold_db (float): margin δ (dB), default 12
            - vertical_bin_size (float|str): averaging height (m), default 5

    Returns:
        Tuple of ``(transient_mask, unfeasible_mask)`` boolean DataArrays shaped like Sv.
        ``unfeasible_mask`` marks averages with data whose cell held too few values (under
        half) to give a reference.
    """
    import xarray as xr

    excl_above = float(params.get("exclude_above", 250.0))
    block_pings = max(1, int(params.get("block_pings", 50)))
    block_height = float(params.get("block_height", 20.0))
    perc = float(params.get("percentile", 15.0))
    thr_db = float(params.get("threshold_db", 12.0))
    bin_m = _metres(params.get("vertical_bin_size", 5.0))
    if bin_m <= 0 or block_height <= 0:
        raise ValueError("vertical_bin_size and block_height must be positive.")

    sv = sv_dataset["Sv"]
    ping_dim = "ping_time"
    range_dim = next(d for d in sv.dims if d != ping_dim)
    data = sv.transpose(ping_dim, range_dim).values
    ranges = _ryan_ranges(sv_dataset, range_dim)
    n_ping = data.shape[0]

    mask = np.zeros(data.shape, dtype=bool)
    unfeasible = np.zeros(data.shape, dtype=bool)
    in_zone = np.isfinite(ranges) & (ranges >= excl_above)
    if in_zone.any() and n_ping:
        # Averaging bins from exclude_above down; each is a run of samples (the columns
        # are put in range order first, so a bin's samples sit side by side).
        cols = np.flatnonzero(in_zone)
        cols = cols[np.argsort(ranges[cols], kind="stable")]
        bin_of = np.floor((ranges[cols] - excl_above) / bin_m).astype(int)
        starts = np.flatnonzero(np.r_[True, np.diff(bin_of) != 0])
        run_of_col = np.repeat(np.arange(starts.size), np.diff(np.r_[starts, cols.size]))

        # Linear means per ping and bin, a few thousand pings at a time to bound memory.
        means = np.full((n_ping, starts.size), np.nan)
        chunk = 4096
        with np.errstate(invalid="ignore", divide="ignore"):
            for p0 in range(0, n_ping, chunk):
                linear = 10.0 ** (data[p0:p0 + chunk][:, cols] / 10.0)
                finite = np.isfinite(linear)
                total = np.add.reduceat(np.where(finite, linear, 0.0), starts, axis=1)
                count = np.add.reduceat(finite.astype(np.int32), starts, axis=1)
                means[p0:p0 + chunk] = np.where(count > 0, total / np.maximum(count, 1), np.nan)

        # Reference cells: block_pings pings by block_height metres, a bin belonging to the
        # cell its top falls in. Each cell's reference is the percentile of its means.
        cell_of_run = np.floor(bin_of[starts] * bin_m / block_height).astype(int)
        n_cells_t = -(-n_ping // block_pings)
        padded = np.full((n_cells_t * block_pings, starts.size), np.nan)
        padded[:n_ping] = means
        by_cell = padded.reshape(n_cells_t, block_pings, starts.size)
        reference = np.full_like(means, np.nan)
        slots = np.minimum(block_pings, n_ping - np.arange(n_cells_t) * block_pings)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)  # all-NaN cells stay NaN
            for cell in np.unique(cell_of_run):
                runs = np.flatnonzero(cell_of_run == cell)
                values = by_cell[:, :, runs].reshape(n_cells_t, -1)
                stat = np.nanpercentile(values, perc, axis=1)
                enough = np.isfinite(values).sum(axis=1) >= 0.5 * slots * runs.size
                stat = np.where(enough & (stat > 0), stat, np.nan)
                reference[:, runs] = np.repeat(stat, block_pings)[:n_ping, None]

        with np.errstate(invalid="ignore", divide="ignore"):
            excess = 10.0 * np.log10(means) - 10.0 * np.log10(reference)
        flagged = np.nan_to_num(excess, nan=-np.inf) > thr_db
        cannot = np.isfinite(means) & ~np.isfinite(reference)
        mask[:, cols] = flagged[:, run_of_col]
        unfeasible[:, cols] = cannot[:, run_of_col]
        mask &= np.isfinite(data)

    dims = (ping_dim, range_dim)
    coords = {d: sv[d] for d in dims if d in sv.coords}
    mask_da = xr.DataArray(mask, dims=dims, coords=coords).transpose(*sv.dims)
    unfeasible_da = xr.DataArray(unfeasible, dims=dims, coords=coords).transpose(*sv.dims)

    pct_val = float(mask.mean()) * 100 if mask.size else 0.0
    logger.info(f"Transient noise mask (Ryan 2015): {pct_val:.2f}% flagged")
    return mask_da, unfeasible_da


def _ryan_ranges(sv_dataset: "xr.Dataset", range_dim: str) -> np.ndarray:
    """One range (m) per sample along *range_dim*, from the first ping that has them."""
    if "echo_range" in sv_dataset:
        er = sv_dataset["echo_range"]
        if "ping_time" in er.dims:
            values = er.transpose("ping_time", range_dim).values
            has = np.isfinite(values).any(axis=1)
            return values[int(np.argmax(has))] if has.any() else values[0]
        return np.asarray(er.values, dtype=float)
    if range_dim in sv_dataset.coords:
        return np.asarray(sv_dataset[range_dim].values, dtype=float)
    raise ValueError("transient_noise_mask_ryan needs echo_range or a range coordinate.")

