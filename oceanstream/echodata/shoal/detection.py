"""
Shoal (fish school) detection algorithms for echosounder data.

Provides two detection methods operating on xarray Datasets:

1. **Weill** (Weill et al., 1993 — MOVIES-B): Threshold-based detection with
   vertical/horizontal gap filling and minimum size filtering. Simple and fast.

2. **Echoview-style**: Three-stage pipeline — candidate detection by threshold
   and minimum size, spatial linking of nearby candidates, then post-link
   minimum size filtering. More configurable but slower.

Both algorithms operate on 2D Sv data (range × ping_time) from a single
channel. The xarray wrapper handles channel selection, coordinate extraction,
and result packaging.

Ported from echopy (Ariza et al., 2020) with xarray/dask support.

References:
    - Weill et al. (1993): MOVIES-B acoustic detection software
    - Echoview documentation: Schools detection algorithm
    - echopy: https://github.com/open-ocean-sounding/echopy
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Literal, Optional

import numpy as np
import scipy.ndimage as nd
import xarray as xr

logger = logging.getLogger(__name__)


@dataclass
class ShoalDetectionResult:
    """Result of shoal/school detection.

    Attributes:
        mask: Boolean DataArray (range × ping_time) where True = shoal pixel.
        edge_mask: Boolean DataArray where True = edge region that could not
            be fully evaluated (shoals may be truncated at boundaries).
        method: Algorithm used (``"weill"`` or ``"echoview"``).
        channel: Channel used for detection.
        num_shoals: Number of distinct shoals detected.
        shoal_fraction: Fraction of valid pixels classified as shoal.
        params: Parameters used for detection.
    """

    mask: xr.DataArray
    edge_mask: xr.DataArray
    method: str
    channel: str
    num_shoals: int
    shoal_fraction: float
    params: dict = field(default_factory=dict)


def _select_channel(
    ds: xr.Dataset, channel: Optional[str] = None
) -> tuple[xr.Dataset, str]:
    """Select a single channel from a multi-channel dataset.

    Args:
        ds: Dataset, possibly with a ``channel`` dimension.
        channel: Channel label to select. If *None*, the first channel is used.

    Returns:
        Tuple of (single-channel Dataset, channel label string).
    """
    if "channel" not in ds.dims:
        return ds, channel or "single_channel"

    channels = ds.channel.values
    if channel is not None:
        if channel in channels:
            return ds.sel(channel=channel), str(channel)
        matches = [c for c in channels if channel in str(c)]
        if matches:
            sel = str(matches[0])
            return ds.sel(channel=sel), sel
        raise ValueError(
            f"Channel '{channel}' not found. Available: {list(channels)}"
        )

    first = str(channels[0])
    return ds.sel(channel=first), first


def _get_sv_array(ds: xr.Dataset, sv_var: str = "Sv") -> np.ndarray:
    """Extract Sv as a 2D numpy array (range × ping_time).

    Loads dask arrays into memory and replaces NaN with -999 dB
    (effectively below any reasonable threshold).
    """
    sv = ds[sv_var]
    arr = sv.values if not hasattr(sv, "compute") else sv.compute().values

    # Ensure 2D: (range, ping_time)
    if arr.ndim != 2:
        raise ValueError(
            f"Expected 2D Sv array (range × ping_time), got shape {arr.shape}"
        )

    # Replace NaN with very low value so they don't trigger thresholds
    arr = np.where(np.isfinite(arr), arr, -999.0)
    return arr


def _get_dimension_arrays(
    ds: xr.Dataset,
) -> tuple[np.ndarray, np.ndarray, str, str]:
    """Extract range and ping_time arrays as 1D numpy arrays.

    Returns:
        (range_values, ping_time_indices, range_dim, time_dim)
    """
    # Identify range dimension
    range_dim = None
    for candidate in ("range_sample", "depth", "echo_range"):
        if candidate in ds.dims:
            range_dim = candidate
            break
    if range_dim is None:
        raise ValueError(
            "No range dimension found (range_sample, depth, echo_range)"
        )

    # Identify time dimension
    time_dim = None
    for candidate in ("ping_time", "time"):
        if candidate in ds.dims:
            time_dim = candidate
            break
    if time_dim is None:
        raise ValueError("No time dimension found (ping_time, time)")

    # Range values: use echo_range data var or coordinate
    if "echo_range" in ds.data_vars:
        range_vals = ds["echo_range"].values
        if range_vals.ndim == 2:
            # echo_range is (range_sample, ping_time) or (ping_time, range_sample)
            # Take the profile along the range dimension (first ping)
            dims = ds["echo_range"].dims
            range_axis = list(dims).index(range_dim)
            if range_axis == 0:
                range_vals = range_vals[:, 0]
            else:
                range_vals = range_vals[0, :]
        if range_vals.ndim > 1:
            range_vals = range_vals.ravel()[:ds.sizes[range_dim]]
    elif range_dim in ds.coords:
        range_vals = ds.coords[range_dim].values.astype(float)
    else:
        range_vals = np.arange(ds.sizes[range_dim], dtype=float)

    # Ping time: just use sequential indices for spatial linking
    ping_indices = np.arange(ds.sizes[time_dim], dtype=float)

    return range_vals, ping_indices, range_dim, time_dim


# =============================================================================
# Core numpy algorithms (ported from echopy)
# =============================================================================


def _weill_core(
    sv: np.ndarray,
    thr: float = -70.0,
    maxvgap: int = 5,
    maxhgap: int = 0,
    minvlen: int = 0,
    minhlen: int = 0,
) -> tuple[np.ndarray, np.ndarray]:
    """Core Weill et al. (1993) shoal detection on a 2D numpy array.

    Args:
        sv: 2D array with Sv data (dB), shape (n_range, n_pings).
        thr: Sv threshold above which samples are candidates (dB).
        maxvgap: Maximum vertical gap to bridge (samples).
        maxhgap: Maximum horizontal gap to bridge (pings).
        minvlen: Minimum vertical extent for a shoal (samples).
        minhlen: Minimum horizontal extent for a shoal (pings).

    Returns:
        Tuple of (shoal_mask, edge_mask) as boolean arrays.
    """
    # Threshold: mask Sv above threshold
    mask = sv > thr

    # Vertical gap filling: for each ping, bridge small gaps
    for jdx in range(mask.shape[1]):
        ping = mask[:, jdx]
        labelled = nd.label(~ping)[0]
        if (labelled == 0).all() or (labelled == 1).all():
            continue
        for label in range(1, labelled.max() + 1):
            gap = labelled == label
            if np.sum(gap) <= maxvgap:
                idx = np.where(gap)[0]
                # Don't fill at edges
                if 0 not in idx and (mask.shape[0] - 1) not in idx:
                    mask[idx, jdx] = True

    # Horizontal gap filling: for each range bin, bridge small gaps
    if maxhgap > 0:
        for idx in range(mask.shape[0]):
            row = mask[idx, :]
            labelled = nd.label(~row)[0]
            if (labelled == 0).all() or (labelled == 1).all():
                continue
            for label in range(1, labelled.max() + 1):
                gap = labelled == label
                if np.sum(gap) <= maxhgap:
                    jdx = np.where(gap)[0]
                    if 0 not in jdx and (mask.shape[1] - 1) not in jdx:
                        mask[idx, jdx] = True

    # Label connected components and apply size filtering
    labelled = nd.label(mask)[0]
    if labelled.max() > 0:
        for label in range(1, labelled.max() + 1):
            feature = labelled == label
            idx, jdx = np.where(feature)
            feature_vlen = idx.max() - idx.min() + 1
            feature_hlen = jdx.max() - jdx.min() + 1

            if feature_vlen < minvlen:
                mask[idx, jdx] = False
            elif feature_hlen < minhlen:
                mask[idx, jdx] = False

    # Edge mask: regions where shoals may be truncated at boundaries
    edge_mask = np.zeros_like(mask, dtype=bool)
    if minvlen > 0 or minhlen > 0:
        edge_mask[:minvlen, :] = True
        edge_mask[-minvlen:, :] = True
        edge_mask[:, :minhlen] = True
        edge_mask[:, -minhlen:] = True

    return mask, edge_mask


def _echoview_core(
    sv: np.ndarray,
    idim: np.ndarray,
    jdim: np.ndarray,
    thr: float = -70.0,
    mincan: tuple[float, float] = (3.0, 10.0),
    maxlink: tuple[float, float] = (3.0, 15.0),
    minsho: tuple[float, float] = (3.0, 15.0),
) -> tuple[np.ndarray, np.ndarray]:
    """Core Echoview-style shoal detection on a 2D numpy array.

    Three-stage algorithm:
    1. Candidate detection: Sv > threshold, filter by minimum candidate size
    2. Spatial linking: link neighbouring shoals within maxlink distance
    3. Post-link filtering: remove linked shoals smaller than minsho

    Args:
        sv: 2D array with Sv data (dB), shape (n_range, n_pings).
        idim: 1D array with vertical dimension values (range in m or samples).
        jdim: 1D array with horizontal dimension values (ping indices or distance).
        thr: Sv threshold (dB).
        mincan: (height, width) minimum candidate size in idim/jdim units.
        maxlink: (height, width) maximum linking distance in idim/jdim units.
        minsho: (height, width) minimum shoal size after linking in idim/jdim units.

    Returns:
        Tuple of (shoal_mask, edge_mask) as boolean arrays.
    """
    import pandas as pd

    if np.isnan(idim).any():
        raise ValueError("Cannot proceed with NaN values in range dimension")
    if np.isnan(jdim).any():
        raise ValueError("Cannot proceed with NaN values in ping dimension")

    # Stage 1: Candidate detection
    mask = sv > thr
    if isinstance(mask, np.bool_):
        mask = np.zeros_like(sv, dtype=bool)

    # Connectivity: 8-connected (3×3 structuring element)
    struct = np.ones((3, 3))
    candidates_labelled = nd.label(mask, struct)[0]

    if candidates_labelled.max() > 0:
        candidate_labels = pd.factorize(
            candidates_labelled[candidates_labelled != 0]
        )[1]
        for cl in candidate_labels:
            candidate = candidates_labelled == cl
            idx = np.where(candidate)[0]
            jdx = np.where(candidate)[1]

            # Measure in idim/jdim units
            height = idim[min(idx.max() + 1, len(idim) - 1)] - idim[idx.min()]
            width = jdim[min(jdx.max() + 1, len(jdim) - 1)] - jdim[jdx.min()]

            # Remove undersized candidates
            if height < mincan[0] or width < mincan[1]:
                mask[idx, jdx] = False

    # Stage 2: Spatial linking
    linked = np.zeros(mask.shape, dtype=int)
    shoals_labelled = nd.label(mask, struct)[0]

    if shoals_labelled.max() > 0:
        shoal_labels = pd.factorize(
            shoals_labelled[shoals_labelled != 0]
        )[1]

        for sl in shoal_labels:
            shoal = shoals_labelled == sl
            i0 = np.where(shoal)[0].min()
            i1 = np.where(shoal)[0].max()
            j0 = np.where(shoal)[1].min()
            j1 = np.where(shoal)[1].max()

            # Expand frame by linking distance
            i00 = np.nanargmin(np.abs(idim - (idim[i0] - (maxlink[0] + 1))))
            i11 = min(
                np.nanargmin(np.abs(idim - (idim[i1] + (maxlink[0] + 1)))) + 1,
                mask.shape[0],
            )
            j00 = np.nanargmin(np.abs(jdim - (jdim[j0] - (maxlink[1] + 1))))
            j11 = min(
                np.nanargmin(np.abs(jdim - (jdim[j1] + (maxlink[1] + 1)))) + 1,
                mask.shape[1],
            )

            # Find neighbours in the expanded frame
            around = np.zeros_like(mask, dtype=bool)
            around[i00:i11, j00:j11] = True
            neighbours = around & mask
            neighbour_labels = pd.factorize(
                shoals_labelled[neighbours]
            )[1]
            neighbour_labels = neighbour_labels[neighbour_labels != 0]
            neighbours = np.isin(shoals_labelled, neighbour_labels)

            # Assign same link label
            existing = linked[neighbours]
            existing_nonzero = existing[existing != 0]
            if len(existing_nonzero) == 0:
                linked[neighbours] = linked.max() + 1
            else:
                min_label = existing_nonzero.min()
                linked[neighbours] = min_label
                for fl in np.unique(existing_nonzero):
                    if fl != min_label:
                        linked[linked == fl] = min_label

    # Stage 3: Post-link size filtering
    if linked.max() > 0:
        linked_labels = pd.factorize(linked[linked != 0])[1]
        for ll in linked_labels:
            linked_shoal = linked == ll
            idx = np.where(linked_shoal)[0]
            jdx = np.where(linked_shoal)[1]

            height = idim[min(idx.max() + 1, len(idim) - 1)] - idim[idx.min()]
            width = jdim[min(jdx.max() + 1, len(jdim) - 1)] - jdim[jdx.min()]

            if height < minsho[0] or width < minsho[1]:
                mask[idx, jdx] = False

    # Edge mask: border region where shoals may be truncated
    edge_mask = np.ones(mask.shape, dtype=bool)
    edge_height = max(mincan[0], maxlink[0], minsho[0])
    edge_width = max(mincan[1], maxlink[1], minsho[1])

    # Find inner region that is fully evaluable
    i0 = np.searchsorted(idim - idim[0], edge_height)
    i1 = len(idim) - np.searchsorted((idim[-1] - idim)[::-1], edge_height)
    j0 = np.searchsorted(jdim - jdim[0], edge_width)
    j1 = len(jdim) - np.searchsorted((jdim[-1] - jdim)[::-1], edge_width)

    if i0 < i1 and j0 < j1:
        edge_mask[i0:i1, j0:j1] = False

    return mask, edge_mask


# =============================================================================
# Public xarray API
# =============================================================================


def detect_shoals_weill(
    ds: xr.Dataset,
    channel: Optional[str] = None,
    thr: float = -70.0,
    maxvgap: int = 5,
    maxhgap: int = 0,
    minvlen: int = 0,
    minhlen: int = 0,
    sv_var: str = "Sv",
) -> ShoalDetectionResult:
    """Detect shoals using the Weill et al. (1993) MOVIES-B algorithm.

    Threshold-based detection with vertical and horizontal gap filling,
    followed by minimum size filtering.

    Args:
        ds: xarray Dataset with Sv data.
        channel: Channel to use. If None, first channel is selected.
        thr: Sv threshold in dB. Samples above this are candidates.
        maxvgap: Maximum vertical gap to bridge (samples).
        maxhgap: Maximum horizontal gap to bridge (pings).
        minvlen: Minimum vertical extent for a shoal (samples).
        minhlen: Minimum horizontal extent for a shoal (pings).
        sv_var: Name of the Sv variable.

    Returns:
        ShoalDetectionResult with mask and diagnostics.
    """
    ds_ch, ch_label = _select_channel(ds, channel)
    sv_arr = _get_sv_array(ds_ch, sv_var)

    logger.info(
        "Detecting shoals (Weill): channel=%s, thr=%.0f dB, "
        "maxvgap=%d, maxhgap=%d, minvlen=%d, minhlen=%d",
        ch_label, thr, maxvgap, maxhgap, minvlen, minhlen,
    )

    shoal_mask, edge_mask = _weill_core(
        sv_arr,
        thr=thr,
        maxvgap=maxvgap,
        maxhgap=maxhgap,
        minvlen=minvlen,
        minhlen=minhlen,
    )

    # Count distinct shoals
    num_shoals = nd.label(shoal_mask)[1]

    # Fraction of valid pixels that are shoals
    valid = sv_arr > -900  # exclude the -999 fill value
    total_valid = int(np.sum(valid))
    shoal_pixels = int(np.sum(shoal_mask & valid))
    fraction = shoal_pixels / total_valid if total_valid > 0 else 0.0

    # Wrap as xarray DataArrays with original coordinates
    sv_da = ds_ch[sv_var]
    mask_da = xr.DataArray(
        shoal_mask,
        dims=sv_da.dims,
        coords=sv_da.coords,
        name="shoal_mask",
    )
    edge_da = xr.DataArray(
        edge_mask,
        dims=sv_da.dims,
        coords=sv_da.coords,
        name="edge_mask",
    )

    logger.info(
        "Weill detection: %d shoals, %.2f%% of pixels",
        num_shoals, fraction * 100,
    )

    return ShoalDetectionResult(
        mask=mask_da,
        edge_mask=edge_da,
        method="weill",
        channel=ch_label,
        num_shoals=num_shoals,
        shoal_fraction=fraction,
        params={
            "thr": thr,
            "maxvgap": maxvgap,
            "maxhgap": maxhgap,
            "minvlen": minvlen,
            "minhlen": minhlen,
        },
    )


def detect_shoals_echoview(
    ds: xr.Dataset,
    channel: Optional[str] = None,
    thr: float = -70.0,
    mincan: tuple[float, float] = (3.0, 10.0),
    maxlink: tuple[float, float] = (3.0, 15.0),
    minsho: tuple[float, float] = (3.0, 15.0),
    sv_var: str = "Sv",
) -> ShoalDetectionResult:
    """Detect shoals using the Echoview-style three-stage algorithm.

    1. Candidate detection: threshold + minimum candidate size
    2. Spatial linking: link neighbours within maxlink distance
    3. Post-link filtering: remove shoals smaller than minsho

    Args:
        ds: xarray Dataset with Sv data.
        channel: Channel to use. If None, first channel is selected.
        thr: Sv threshold in dB.
        mincan: (height, width) minimum candidate size. Height in range
            units (m or samples), width in ping units.
        maxlink: (height, width) maximum linking distance.
        minsho: (height, width) minimum shoal size after linking.
        sv_var: Name of the Sv variable.

    Returns:
        ShoalDetectionResult with mask and diagnostics.

    Notes:
        The height/width units in mincan, maxlink, minsho should match the
        units of the range and ping dimensions. If range is in metres,
        height values are in metres. Width is always in ping indices.
    """
    ds_ch, ch_label = _select_channel(ds, channel)
    sv_arr = _get_sv_array(ds_ch, sv_var)
    range_vals, ping_vals, range_dim, time_dim = _get_dimension_arrays(ds_ch)

    logger.info(
        "Detecting shoals (Echoview): channel=%s, thr=%.0f dB, "
        "mincan=%s, maxlink=%s, minsho=%s",
        ch_label, thr, mincan, maxlink, minsho,
    )

    shoal_mask, edge_mask = _echoview_core(
        sv_arr,
        idim=range_vals,
        jdim=ping_vals,
        thr=thr,
        mincan=mincan,
        maxlink=maxlink,
        minsho=minsho,
    )

    num_shoals = nd.label(shoal_mask)[1]
    valid = sv_arr > -900
    total_valid = int(np.sum(valid))
    shoal_pixels = int(np.sum(shoal_mask & valid))
    fraction = shoal_pixels / total_valid if total_valid > 0 else 0.0

    sv_da = ds_ch[sv_var]
    mask_da = xr.DataArray(
        shoal_mask,
        dims=sv_da.dims,
        coords=sv_da.coords,
        name="shoal_mask",
    )
    edge_da = xr.DataArray(
        edge_mask,
        dims=sv_da.dims,
        coords=sv_da.coords,
        name="edge_mask",
    )

    logger.info(
        "Echoview detection: %d shoals, %.2f%% of pixels",
        num_shoals, fraction * 100,
    )

    return ShoalDetectionResult(
        mask=mask_da,
        edge_mask=edge_da,
        method="echoview",
        channel=ch_label,
        num_shoals=num_shoals,
        shoal_fraction=fraction,
        params={
            "thr": thr,
            "mincan": mincan,
            "maxlink": maxlink,
            "minsho": minsho,
        },
    )


def detect_shoals(
    ds: xr.Dataset,
    method: Literal["weill", "echoview"] = "weill",
    channel: Optional[str] = None,
    sv_var: str = "Sv",
    **kwargs,
) -> ShoalDetectionResult:
    """Detect shoals/schools using the specified method.

    Dispatcher that delegates to method-specific functions.

    Args:
        ds: xarray Dataset with Sv data.
        method: Detection algorithm — ``"weill"`` or ``"echoview"``.
        channel: Channel to use.
        sv_var: Name of the Sv variable.
        **kwargs: Method-specific parameters (thr, maxvgap, mincan, etc.)

    Returns:
        ShoalDetectionResult with mask and diagnostics.
    """
    methods = {
        "weill": detect_shoals_weill,
        "echoview": detect_shoals_echoview,
    }

    if method not in methods:
        raise ValueError(
            f"Unknown shoal detection method '{method}'. "
            f"Available: {list(methods.keys())}"
        )

    return methods[method](ds, channel=channel, sv_var=sv_var, **kwargs)


def mask_shoals(
    ds: xr.Dataset,
    result: ShoalDetectionResult,
    sv_var: str = "Sv",
    fill_value: float = float("nan"),
) -> xr.Dataset:
    """Apply shoal detection mask to a dataset.

    Replaces Sv values at shoal pixels with fill_value (NaN by default),
    effectively removing detected shoals from the data.

    Args:
        ds: xarray Dataset with Sv data.
        result: ShoalDetectionResult from detect_shoals().
        sv_var: Name of the Sv variable.
        fill_value: Value to assign at masked pixels. Default NaN.

    Returns:
        New Dataset with shoal pixels masked.
    """
    ds_out = ds.copy()

    if "channel" in ds.dims and result.channel != "single_channel":
        # Apply mask to the specific channel
        sv = ds_out[sv_var].sel(channel=result.channel)
        masked = sv.where(~result.mask, other=fill_value)
        ds_out[sv_var].loc[dict(channel=result.channel)] = masked
    else:
        ds_out[sv_var] = ds_out[sv_var].where(~result.mask, other=fill_value)

    return ds_out
