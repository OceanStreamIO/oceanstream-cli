"""Background noise detection using De Robertis & Higginbottom (2007).

Estimates background noise level and flags samples with insufficient
signal-to-noise ratio.  Supports quantile-based depth statistics and
guard-mode DSL exclusion.

Ported from saildrone-data/saildrone/denoise/background_noise.py
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Tuple

import numpy as np

if TYPE_CHECKING:
    import xarray as xr

logger = logging.getLogger(__name__)


def _extract_db(value) -> float:
    """Extract dB value from string like ``'3.0dB'`` or numeric."""
    if isinstance(value, str):
        return float(value.replace("dB", "").strip())
    return float(value)


def background_noise_mask(
    sv_dataset: "xr.Dataset",
    params: dict,
) -> Tuple["xr.DataArray", "xr.DataArray"]:
    """De Robertis & Higginbottom (2007) background noise filter.

    Estimates background noise from TVG-removed signal using block
    statistics, then flags samples with insufficient SNR.

    Args:
        sv_dataset: Single-channel Sv xarray Dataset with ``"Sv"`` variable
            and range coordinate.
        params: Dictionary of parameters:
            - range_coord (str): Vertical coord name (auto-detected)
            - sound_absorption (float): α in dB/m, default 0.001
            - range_window (int|"auto"): range samples in block, default 20
            - ping_window (int): pings in block, default 50
            - background_noise_max (str|float): lowest allowed background
              Sv, default ``"-125.0dB"``
            - SNR_threshold (str|float): minimum SNR (dB), default ``"3.0dB"``
            - minimal_linear (float): floor for linear power, default 1e-30
            - depth_stat (str): ``"min"`` or ``"quantile"``, default ``"quantile"``
            - depth_quantile (float): quantile when depth_stat="quantile",
              default 0.15
            - guard_mode (str|None): ``None``, ``"above"``, or
              ``"outside_band"`` — excludes DSL from the depth statistic
            - guard_depth (float): required when guard_mode="above"
            - guard_band (list[float]): ``[z0, z1]`` when
              guard_mode="outside_band"

    Returns:
        Tuple of ``(mask_low_snr, mask_non_positive)`` boolean DataArrays.

    Reference:
        De Robertis, A., & Higginbottom, I. (2007). ICES J. Marine Sci.
    """
    import xarray as xr

    # ------------------------------------------------------------------
    # 1. Detect range dimension
    # ------------------------------------------------------------------
    if "range_sample" in sv_dataset.dims:
        range_dim = "range_sample"
    elif "depth" in sv_dataset.dims:
        range_dim = "depth"
    elif "echo_range" in sv_dataset.dims:
        range_dim = "echo_range"
    else:
        range_dim = [
            d for d in sv_dataset["Sv"].dims if d not in ("channel", "ping_time")
        ][0]

    # ------------------------------------------------------------------
    # 2. Unpack parameters
    # ------------------------------------------------------------------
    range_coord = params.get("range_coord", range_dim)
    sound_absorption = params.get("sound_absorption", 0.001)
    rng_win = params.get("range_window", 20)
    ping_win = params.get("ping_window", 50)
    background_noise_max = _extract_db(params.get("background_noise_max", "-125.0dB"))
    snr_threshold = _extract_db(params.get("SNR_threshold", "3.0dB"))
    depth_stat = params.get("depth_stat", "quantile")
    depth_quantile = float(params.get("depth_quantile", 0.15))

    guard_mode = params.get("guard_mode", None)
    guard_depth = params.get("guard_depth", None)
    guard_band = params.get("guard_band", None)

    # ------------------------------------------------------------------
    # 3. Resolve range values (metres)
    # ------------------------------------------------------------------
    Sv = sv_dataset["Sv"]

    range_values: xr.DataArray
    if "echo_range" in sv_dataset.data_vars:
        range_values = sv_dataset["echo_range"]
    elif "echo_range" in sv_dataset.coords:
        range_values = sv_dataset["echo_range"]
    elif "depth" in sv_dataset.coords:
        range_values = sv_dataset["depth"]
    elif "depth" in sv_dataset.data_vars:
        range_values = sv_dataset["depth"]
    else:
        n_samples = sv_dataset.sizes.get(range_dim, 1000)
        sample_spacing = 0.18
        range_values = xr.DataArray(
            np.arange(n_samples) * sample_spacing, dims=[range_dim]
        )
        logger.warning(
            f"No echo_range found, estimating from {range_dim} "
            f"with {sample_spacing}m spacing"
        )

    rng_var = range_coord  # name used for coarsen dim

    # ------------------------------------------------------------------
    # 4. Auto range window
    # ------------------------------------------------------------------
    if rng_win == "auto":
        if range_coord == "depth":
            dz = float(sv_dataset["depth"].diff("depth").median())
            rng_win = max(1, round(1.0 / dz))
        else:
            try:
                dr = float(
                    range_values.isel({range_dim: slice(0, 100)})
                    .diff(range_dim)
                    .median()
                )
                rng_win = max(1, int(round(10.0 / dr)))
            except Exception:
                rng_win = 50

    # ------------------------------------------------------------------
    # 5. Remove TVG: 20 log10(r) + 2 α r
    # ------------------------------------------------------------------
    r_safe = xr.where(range_values > 0, range_values, np.nan)
    tvg = 20.0 * np.log10(r_safe) + 2.0 * sound_absorption * r_safe
    Sv_flat_db = Sv - tvg

    # ------------------------------------------------------------------
    # 6. Block averaging in linear domain
    # ------------------------------------------------------------------
    power_lin = 10.0 ** (Sv_flat_db / 10.0)
    binned_lin = power_lin.coarsen(
        ping_time=ping_win,
        **{range_dim: rng_win},
        boundary="pad",
    ).mean()

    binned_db = 10.0 * np.log10(binned_lin.where(binned_lin > 0))

    if hasattr(binned_db, "chunk") and range_dim in binned_db.dims:
        binned_db = binned_db.chunk({range_dim: -1})

    # ------------------------------------------------------------------
    # 7. Optional guard: exclude DSL from depth statistic
    # ------------------------------------------------------------------
    if guard_mode:
        z = sv_dataset[range_coord]
        if guard_mode == "above":
            if guard_depth is None:
                raise ValueError(
                    "guard_depth required when guard_mode='above'."
                )
            region = z <= float(guard_depth)
        elif guard_mode == "outside_band":
            if not guard_band or len(guard_band) != 2:
                raise ValueError(
                    "guard_band=[z0, z1] required when guard_mode='outside_band'."
                )
            z0, z1 = sorted(map(float, guard_band))
            region = (z < z0) | (z > z1)
        else:
            raise ValueError("guard_mode must be None|'above'|'outside_band'.")
        binned_db = binned_db.where(region)

    # ------------------------------------------------------------------
    # 8. Depth statistic (noise estimate)
    # ------------------------------------------------------------------
    if depth_stat == "min":
        noise_1d_db = binned_db.min(dim=range_dim, skipna=True)
    elif depth_stat == "quantile":
        # Use skipna=False to avoid the xarray → dask → np.nanquantile
        # code path where dask 2024.12.x passes the removed 'interpolation'
        # kwarg to numpy 2.x's nanquantile.  We drop NaN slices ourselves
        # before computing the quantile.
        _binned = binned_db.fillna(0.0) if binned_db.isnull().any() else binned_db
        noise_1d_db = _binned.quantile(
            depth_quantile, dim=range_dim, method="linear", skipna=False
        )
        if "quantile" in noise_1d_db.dims:
            noise_1d_db = noise_1d_db.squeeze("quantile", drop=True)
    else:
        raise ValueError("depth_stat must be 'min' or 'quantile'.")

    # ------------------------------------------------------------------
    # 9. Align ping-time indices (first ping of each coarsened bin)
    # ------------------------------------------------------------------
    noise_1d_db = noise_1d_db.assign_coords(
        ping_time=ping_win * np.arange(noise_1d_db.sizes["ping_time"])
    )
    power_lin = power_lin.assign_coords(
        ping_time=np.arange(power_lin.sizes["ping_time"])
    )

    # Cap noise floor
    if background_noise_max is not None:
        noise_1d_db = noise_1d_db.where(
            noise_1d_db < background_noise_max, background_noise_max
        )

    # ------------------------------------------------------------------
    # 10. Restore TVG to noise and compute masks
    # ------------------------------------------------------------------
    Sv_noise_db = (
        noise_1d_db
        .reindex({"ping_time": power_lin["ping_time"]}, method="ffill")
        .assign_coords(ping_time=sv_dataset["ping_time"])
        + tvg
    )

    Sv_lin_tot = 10.0 ** (Sv / 10.0)
    bgn_lin_tot = 10.0 ** (Sv_noise_db / 10.0)
    lin_diff = Sv_lin_tot - bgn_lin_tot

    mask_non_positive = lin_diff <= 0
    Sv_clean_db = xr.where(mask_non_positive, np.nan, 10.0 * np.log10(lin_diff))
    snr_db = Sv_clean_db - Sv_noise_db
    mask_low_snr = snr_db < snr_threshold

    pct = float(mask_low_snr.mean()) * 100 if mask_low_snr.size > 0 else 0.0
    logger.info(f"Background noise mask: {pct:.1f}% flagged")

    return mask_low_snr, mask_non_positive
