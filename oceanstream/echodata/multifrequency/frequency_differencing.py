"""
Multi-frequency dB-differencing for acoustic target classification.

The dB-difference method computes Sv_1 − Sv_2 for a pair of frequencies
and masks regions where the difference falls within a specified range.
This is commonly used to discriminate species groups (e.g. krill vs fish)
based on their known frequency response.

Ported from echopy (Ariza et al., 2020) with bug fixes and xarray support.

References:
    - Kloser et al. (2002), ICES Journal of Marine Science
    - De Robertis & Higginbottom (2007)
    - echopy: https://github.com/open-ocean-sounding/echopy
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import numpy as np
import xarray as xr

logger = logging.getLogger(__name__)


@dataclass
class FrequencyDifferencingResult:
    """Result of frequency differencing analysis.

    Attributes:
        mask: Boolean DataArray where True = within threshold range.
        difference: DataArray of Sv_low − Sv_high values (dB).
        freq_low: Channel/frequency used as minuend.
        freq_high: Channel/frequency used as subtrahend.
        threshold: (low, high) dB threshold tuple applied.
        pixels_in_range: Number of pixels within threshold.
        pixels_total: Total non-NaN pixels.
        fraction_in_range: Fraction of pixels within threshold.
    """

    mask: xr.DataArray
    difference: xr.DataArray
    freq_low: str
    freq_high: str
    threshold: tuple[float, float]
    pixels_in_range: int
    pixels_total: int
    fraction_in_range: float


def _resolve_channel(
    ds: xr.Dataset,
    channel: str,
) -> str:
    """Resolve a channel identifier to a matching channel label in the dataset.

    Supports exact match or substring match (e.g. "38" matches
    "WBT 742057-15 ES38-18").

    Args:
        ds: Dataset with a ``channel`` dimension.
        channel: Exact or partial channel label.

    Returns:
        Matched channel label string.

    Raises:
        ValueError: If the channel cannot be found.
    """
    channels = [str(c) for c in ds.channel.values]

    # Exact match
    if channel in channels:
        return channel

    # Substring/frequency match
    matches = [c for c in channels if channel in c]
    if matches:
        return matches[0]

    raise ValueError(
        f"Channel '{channel}' not found. Available: {channels}"
    )


def db_difference(
    ds: xr.Dataset,
    freq_low: str,
    freq_high: str,
    thr: tuple[float, float] = (-12.0, -2.0),
    sv_var: str = "Sv",
) -> FrequencyDifferencingResult:
    """Compute dB difference between two frequencies and mask within threshold.

    Calculates ``Sv(freq_low) − Sv(freq_high)`` and returns a boolean mask
    where the difference falls within the inclusive range ``[thr[0], thr[1]]``.

    Args:
        ds: xarray Dataset with Sv data and a ``channel`` dimension.
        freq_low: Channel label (or substring like ``"38000"``) for the
            minuend frequency (lower frequency typically).
        freq_high: Channel label (or substring like ``"120000"``) for the
            subtrahend frequency (higher frequency typically).
        thr: Tuple of ``(lower_bound, upper_bound)`` in dB. Pixels where
            ``thr[0] <= (Sv_low - Sv_high) <= thr[1]`` are masked True.
            Default ``(-12, -2)`` is commonly used for krill identification.
        sv_var: Name of the Sv variable in the dataset. Default ``"Sv"``.

    Returns:
        FrequencyDifferencingResult with mask, difference array, and diagnostics.

    Raises:
        ValueError: If the dataset has no ``channel`` dimension, or if the
            requested channels cannot be found, or if ``thr[0] > thr[1]``.

    Example:
        >>> result = db_difference(sv_ds, "38000", "120000", thr=(-12, -2))
        >>> masked_sv = sv_ds[sv_var].where(~result.mask)
    """
    if thr[0] > thr[1]:
        raise ValueError(
            f"Lower threshold ({thr[0]}) must be <= upper threshold ({thr[1]}). "
            f"Supply thr as (lower_bound, upper_bound)."
        )

    if "channel" not in ds.dims:
        raise ValueError(
            "Dataset must have a 'channel' dimension for multi-frequency analysis. "
            "Got dimensions: " + str(list(ds.dims))
        )

    # Resolve channel labels
    ch_low = _resolve_channel(ds, freq_low)
    ch_high = _resolve_channel(ds, freq_high)

    if ch_low == ch_high:
        raise ValueError(
            f"freq_low and freq_high resolved to the same channel: '{ch_low}'. "
            f"Provide two distinct frequency channels."
        )

    logger.info(
        "Computing dB difference: %s (low) − %s (high), threshold=[%.1f, %.1f]",
        ch_low,
        ch_high,
        thr[0],
        thr[1],
    )

    # Extract single-channel Sv arrays
    sv_low = ds[sv_var].sel(channel=ch_low)
    sv_high = ds[sv_var].sel(channel=ch_high)

    # Compute difference
    difference = sv_low - sv_high

    # Build mask: True where difference is within [thr[0], thr[1]]
    # NOTE: echopy original had a bug where the second condition overwrote
    # the first. We fix this by properly combining both conditions.
    mask_low = difference >= thr[0]
    mask_high = difference <= thr[1]
    mask = mask_low & mask_high

    # Diagnostics
    valid = np.isfinite(difference.values)
    pixels_total = int(np.sum(valid))
    pixels_in_range = int(np.sum(mask.values & valid))
    fraction = pixels_in_range / pixels_total if pixels_total > 0 else 0.0

    logger.info(
        "dB difference: %d/%d pixels (%.1f%%) within threshold",
        pixels_in_range,
        pixels_total,
        fraction * 100,
    )

    return FrequencyDifferencingResult(
        mask=mask,
        difference=difference,
        freq_low=ch_low,
        freq_high=ch_high,
        threshold=thr,
        pixels_in_range=pixels_in_range,
        pixels_total=pixels_total,
        fraction_in_range=fraction,
    )
