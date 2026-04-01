"""Main denoising pipeline for Sv datasets.

Provides high-level functions for applying multiple denoising
methods and combining masks.  Supports frequency-keyed parameter
dispatch and pulse-length selection.

Ported from saildrone-data/saildrone/denoise/mask.py and workflow.py
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any, Hashable, Mapping, Optional, Union

import numpy as np

if TYPE_CHECKING:
    import xarray as xr
    from oceanstream.echodata.config import DenoiseConfig

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Frequency-keyed parameter dispatch (ported from saildrone/denoise/mask.py)
# ---------------------------------------------------------------------------

def _params_for_channel(
    param_sets: Mapping[Any, Any],
    ch_ds: "xr.Dataset",
    pulse_length: str | None = None,
) -> dict | None:
    """Return the parameter dictionary for a single channel.

    Behaviour:
      - If *param_sets* is a flat dict (no per-frequency sub-dicts),
        return it unchanged.
      - Otherwise pick the entry keyed by the channel's
        ``frequency_nominal`` (supports int/str keys like ``38000`` /
        ``"38000"``).
      - If *pulse_length* is supplied (``"short_pulse"`` or
        ``"long_pulse"``) and the per-frequency entry contains that
        sub-key, return the sub-dict.  The sub-dict may be ``None``
        (meaning "skip this channel for this stage").

    Returns ``None`` when the channel should be skipped entirely.
    """
    # Single-dict case: no per-frequency mappings
    if not any(isinstance(v, Mapping) for v in param_sets.values()):
        return dict(param_sets)

    # Resolve frequency key
    freq = float(ch_ds["frequency_nominal"].compute().item())
    key_str = str(int(freq))

    if freq in param_sets:
        entry = param_sets[freq]
    elif key_str in param_sets:
        entry = param_sets[key_str]
    else:
        logger.warning(f"{freq} Hz parameters missing, skipping channel")
        return None

    # Optional pulse-length selection
    if pulse_length and isinstance(entry, Mapping):
        pl = pulse_length.lower()
        if pl.startswith("short"):
            subkey = "short_pulse"
        elif pl.startswith("long"):
            subkey = "long_pulse"
        else:
            subkey = None

        if subkey and subkey in entry:
            return entry[subkey]  # may be None (explicitly disabled)

    return dict(entry) if isinstance(entry, Mapping) else entry


def apply_denoising(
    sv_dataset: Union[Path, "xr.Dataset"],
    methods: list[str] = None,
    config: Optional["DenoiseConfig"] = None,
    output_path: Optional[Path] = None,
    merge_masks: bool = False,
    return_stage_masks: bool = False,
) -> Union["xr.Dataset", tuple["xr.Dataset", dict]]:
    """
    Apply denoising pipeline to Sv dataset.
    
    Builds combined noise mask from specified methods and applies
    it to the Sv data, setting noisy samples to NaN.
    
    Args:
        sv_dataset: Input Sv data (Dataset or path to Zarr)
        methods: List of denoising methods to apply. Options:
            - "background": De Robertis & Higginbottom (2007)
            - "transient": Fielding et al. upward-stepping
            - "impulse": Multi-lag impulse noise
            - "attenuation": Attenuated signal detection
            Default: ["background", "transient", "impulse", "attenuation"]
        config: DenoiseConfig with method-specific parameters
        output_path: Optional path to save denoised dataset
        merge_masks: If True, include individual masks in output dataset
        return_stage_masks: If True, return (dataset, stage_masks_dict)
        
    Returns:
        Denoised Sv dataset with noise masks applied.
        If return_stage_masks=True, returns (dataset, stage_masks_dict).
        
    Example:
        denoised = apply_denoising(sv_path, methods=["background", "impulse"])
    """
    import xarray as xr
    
    # Load if path
    if isinstance(sv_dataset, (str, Path)):
        logger.info(f"Loading Sv from {sv_dataset}")
        sv_dataset = xr.open_zarr(sv_dataset)
    
    # Default methods
    if methods is None:
        methods = ["background", "transient", "impulse", "attenuation"]
    
    # Default config
    if config is None:
        from oceanstream.echodata.config import DenoiseConfig
        config = DenoiseConfig()
    
    logger.info(f"Applying denoising methods: {methods}")
    
    # Build combined mask using per-channel approach
    combined_mask, stage_cubes = build_full_mask(
        sv_dataset,
        methods=methods,
        config=config,
        return_stage_masks=True,
    )
    
    # Apply mask
    denoised = apply_noise_mask(sv_dataset, combined_mask)
    
    # Add processing metadata
    denoised.attrs["denoising_methods"] = methods
    denoised.attrs["denoising_applied"] = True
    
    # Optionally merge individual masks into dataset
    if merge_masks:
        mask_vars = {"mask_combined": combined_mask.astype(bool)}
        for stage_name, mask in stage_cubes.items():
            safe_name = f"mask_{stage_name.replace(' ', '_').replace('-', '_').lower()}"
            mask_vars[safe_name] = mask.astype(bool)
        
        # Broadcast and merge
        for name, arr in mask_vars.items():
            arr = arr.broadcast_like(denoised["Sv"])
            denoised[name] = arr.assign_attrs(
                long_name=f"{name} quality-control mask (True = bad)"
            )
    
    # Save if output path provided
    if output_path:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        logger.info(f"Saving denoised Sv to {output_path}")
        denoised.to_zarr(output_path, mode="w")
    
    if return_stage_masks:
        return denoised, stage_cubes
    
    return denoised


def build_full_mask(
    ds: "xr.Dataset",
    methods: list[str] | None = None,
    config: Optional["DenoiseConfig"] = None,
    stages: Mapping[str, dict] | None = None,
    var_name: str = "Sv",
    return_stage_masks: bool = True,
    mask_unfeasible: bool = False,
    pulse_length: str | None = None,
) -> Union["xr.DataArray", tuple["xr.DataArray", dict]]:
    """Build combined noise mask using per-channel processing.

    Processes each channel independently, building masks for each
    denoising method and combining them with OR logic.

    Two calling conventions are supported:

    1. **Config-based** (OceanStream native)::

           build_full_mask(ds, methods=["background", "impulse"], config=cfg)

    2. **Stages-dict** (saildrone-compatible)::

           build_full_mask(ds, stages={
               "impulsive": {"fn": impulse_noise_mask, "param_sets": {...}},
               "background": {"fn": background_noise_mask, "param_sets": {...}},
           })

       When using *stages*, each ``param_sets`` value may be:
       - A flat dict (used for every channel), **or**
       - A frequency-keyed dict (``{"38000": {...}, "200000": {...}}``),
         optionally with ``"short_pulse"`` / ``"long_pulse"`` sub-keys.

    Args:
        ds: Sv xarray Dataset.
        methods: List of method name strings (config-based API).
        config: DenoiseConfig with parameters (config-based API).
        stages: Dict mapping stage names to ``{"fn": callable, "param_sets": ...}``
            (stages-dict API).
        var_name: Variable to mask (default ``"Sv"``).
        return_stage_masks: If True, also return per-stage masks.
        mask_unfeasible: If True, OR the unfeasible mask into the stage mask.
        pulse_length: ``"short_pulse"`` or ``"long_pulse"`` for pulse-length
            dispatch.

    Returns:
        Combined mask DataArray, or ``(mask, stage_cubes_dict)`` if
        *return_stage_masks* is True.
    """
    import xarray as xr

    from oceanstream.echodata.denoise.background_noise import background_noise_mask
    from oceanstream.echodata.denoise.transient_noise import transient_noise_mask
    from oceanstream.echodata.denoise.impulse_noise import impulse_noise_mask
    from oceanstream.echodata.denoise.attenuation import attenuation_mask

    # Map method names → functions (config-based API)
    method_fns = {
        "background": background_noise_mask,
        "transient": transient_noise_mask,
        "impulse": impulse_noise_mask,
        "attenuation": attenuation_mask,
    }

    # ── Build a unified stages dict from whichever API was used ──────
    if stages is not None:
        # Already in stages-dict form – nothing to do
        pass
    elif methods is not None:
        if config is None:
            from oceanstream.echodata.config import DenoiseConfig
            config = DenoiseConfig()
        stages = {}
        for method in methods:
            if method not in method_fns:
                logger.warning(f"Unknown method {method}, skipping")
                continue
            # Build param_sets from config — use frequency-keyed dicts
            # when per-frequency dispatch is enabled so that
            # _params_for_channel resolves per-channel parameters.
            if config.use_frequency_specific:
                params = config.to_frequency_keyed_params(method)
            elif method == "background":
                params = config.to_background_params()
            elif method == "transient":
                params = config.to_transient_params()
            elif method == "impulse":
                params = config.to_impulse_params()
            elif method == "attenuation":
                params = config.to_attenuation_params()
            else:
                params = {}
            stages[method] = {"fn": method_fns[method], "param_sets": params}
        pulse_length = pulse_length or getattr(config, "pulse_length", None)
    else:
        raise ValueError("Either 'methods' or 'stages' must be provided.")

    n_ch = ds.sizes.get("channel", 1)
    stage_masks: dict[str, list] = {name: [] for name in stages}
    ch_masks: list[xr.DataArray] = []

    for ch in range(n_ch):
        ch_ds = ds.isel(channel=ch)
        reference = ds[var_name].isel(channel=ch)
        stage_or = None

        for stage_name, spec in stages.items():
            fn = spec["fn"]
            param_sets = spec["param_sets"]

            # Resolve parameters for this channel
            pars = _params_for_channel(param_sets, ch_ds, pulse_length)
            if pars is None:
                continue

            try:
                result = fn(ch_ds, pars)
            except Exception as e:
                logger.warning(
                    f"Error computing {stage_name} mask for channel {ch}: {e}"
                )
                continue

            # All denoise functions now return (mask, unfeasible) tuples
            if isinstance(result, (tuple, list)):
                if mask_unfeasible and len(result) >= 2:
                    stage_mask = result[0] | result[1]
                else:
                    stage_mask = result[0]
            else:
                stage_mask = result

            if stage_mask is None:
                continue

            # Align coordinates
            stage_mask = stage_mask.broadcast_like(reference)
            stage_mask = stage_mask.reset_coords(drop=True)
            for cname in reference.coords:
                if cname in stage_mask.dims or cname == "channel":
                    continue
                stage_mask = stage_mask.assign_coords({cname: reference.coords[cname]})

            # Expand to channel dimension
            ch_value = ds["channel"].values[ch]
            stage_mask = stage_mask.expand_dims(channel=[ch_value])
            stage_mask = stage_mask & ~reference.isnull().expand_dims(channel=[ch_value])

            stage_masks[stage_name].append(stage_mask)
            stage_or = stage_mask if stage_or is None else (stage_or | stage_mask)

        if stage_or is not None:
            ch_masks.append(stage_or)

    if not ch_masks:
        full_mask = xr.DataArray(
            np.zeros(ds[var_name].shape, dtype=bool),
            dims=ds[var_name].dims,
            coords=ds[var_name].coords,
        )
        if return_stage_masks:
            return full_mask, {}
        return full_mask

    full_mask = xr.concat(ch_masks, dim="channel")
    full_mask = full_mask.broadcast_like(ds[var_name])
    full_mask.name = "combined_mask"

    pct_flagged = float(full_mask.mean().values) * 100
    logger.info(f"Combined mask: {pct_flagged:.1f}% flagged as noise")

    if not return_stage_masks:
        return full_mask

    stage_cubes: dict[str, xr.DataArray] = {}
    for name, m_list in stage_masks.items():
        if m_list:
            cube = xr.concat(m_list, dim="channel")
            cube = cube.broadcast_like(ds[var_name])
            stage_cubes[name] = cube

    return full_mask, stage_cubes


def build_noise_mask(
    sv_dataset: "xr.Dataset",
    methods: list[str],
    config: "DenoiseConfig",
) -> "xr.DataArray":
    """
    Build combined noise mask from multiple denoising methods.
    
    This is a simpler version that processes all channels at once.
    For per-channel processing, use build_full_mask.
    
    Args:
        sv_dataset: Sv xarray Dataset
        methods: List of method names
        config: DenoiseConfig with parameters
        
    Returns:
        Boolean DataArray (True = noise to remove)
    """
    import xarray as xr
    
    masks = []
    
    for method in methods:
        logger.info(f"Computing {method} noise mask")
        
        if method == "background":
            from oceanstream.echodata.denoise.background_noise import background_noise_mask
            result = background_noise_mask(sv_dataset, config.to_background_params())

        elif method == "transient":
            from oceanstream.echodata.denoise.transient_noise import transient_noise_mask
            result = transient_noise_mask(sv_dataset, config.to_transient_params())

        elif method == "impulse":
            from oceanstream.echodata.denoise.impulse_noise import impulse_noise_mask
            result = impulse_noise_mask(sv_dataset, config.to_impulse_params())

        elif method == "attenuation":
            from oceanstream.echodata.denoise.attenuation import attenuation_mask
            result = attenuation_mask(sv_dataset, config.to_attenuation_params())

        else:
            logger.warning(f"Unknown denoising method: {method}, skipping")
            continue
        
        # All functions now return (mask, unfeasible) tuples
        if isinstance(result, (tuple, list)):
            mask = result[0]
        else:
            mask = result

        if mask is not None:
            masks.append(mask)
    
    if not masks:
        # Return all-False mask if no methods applied
        return xr.DataArray(
            np.zeros(sv_dataset["Sv"].shape, dtype=bool),
            dims=sv_dataset["Sv"].dims,
            coords=sv_dataset["Sv"].coords,
        )
    
    # Combine masks with OR (any mask marks as noise)
    combined = masks[0]
    for mask in masks[1:]:
        combined = combined | mask
    
    logger.info(f"Combined mask: {float(combined.mean().values) * 100:.1f}% flagged as noise")
    
    return combined


def apply_noise_mask(
    sv_dataset: "xr.Dataset",
    mask: "xr.DataArray",
    fill_value: float = np.nan,
) -> "xr.Dataset":
    """
    Apply noise mask to Sv dataset.
    
    Args:
        sv_dataset: Sv xarray Dataset
        mask: Boolean mask (True = noise)
        fill_value: Value to fill masked samples (default: NaN)
        
    Returns:
        Dataset with masked Sv values
    """
    result = sv_dataset.copy()
    result["Sv"] = sv_dataset["Sv"].where(~mask, fill_value)
    
    # Store mask as variable
    result["noise_mask"] = mask
    
    return result


def drop_noisy_pings(
    sv_dataset: "xr.Dataset",
    channel: int | float | None = None,
    var_name: str = "Sv",
    drop_threshold: float = 1.0,
    freq_coord: str = "frequency_nominal",
) -> "xr.Dataset":
    """
    Drop ping_time slices where the fraction of NaNs exceeds a threshold.
    
    This is useful for cleaning up data after applying noise masks,
    removing pings that are mostly or entirely noise.
    
    Ported from _echodata-legacy-code/saildrone-echodata-processing/denoise/mask.py
    
    Args:
        sv_dataset: Dataset with noise mask already applied (bad samples are NaN)
        channel: Optional channel to extract (by frequency value or index).
                 If None, applies to all channels.
        var_name: Variable to inspect for NaNs (default: "Sv")
        drop_threshold: Fraction (0-1) of NaNs above which a ping is removed.
                       - 1.0 = remove only fully-NaN pings
                       - 0.5 = remove pings with ≥50% NaN
                       - 0.0 = remove any ping with any NaN
        freq_coord: Name of the frequency coordinate (default: "frequency_nominal")
        
    Returns:
        Dataset with noisy pings removed along the ping_time axis.
        
    Example:
        # Remove pings that are >80% NaN
        clean = drop_noisy_pings(denoised_ds, drop_threshold=0.8)
        
        # Extract just 38 kHz channel and remove fully-NaN pings  
        clean_38 = drop_noisy_pings(denoised_ds, channel=38000, drop_threshold=1.0)
    """
    import xarray as xr
    
    ds = sv_dataset
    
    # Optionally select a single channel
    if channel is not None:
        if freq_coord in ds.coords:
            freqs = ds[freq_coord].values
            if hasattr(freqs, '__iter__') and (freqs == channel).any():
                ds = ds.sel(channel=ds[freq_coord] == channel)
            else:
                ds = ds.isel(channel=int(channel), drop=False)
        else:
            ds = ds.isel(channel=int(channel), drop=False)
    
    # Find the range dimension (last dim that's not channel/ping_time)
    range_dim = [d for d in ds[var_name].dims if d not in ["channel", "ping_time"]]
    if not range_dim:
        logger.warning("No range dimension found, returning unchanged")
        return ds
    range_dim = range_dim[0]
    
    # Compute fraction NaN per ping_time
    n_nan = ds[var_name].isnull().sum(dim=range_dim)
    total = ds[var_name].sizes[range_dim]
    frac_nan = n_nan / total
    
    # If there's a channel dim, take max across channels (most conservative)
    if "channel" in frac_nan.dims:
        frac_nan = frac_nan.max(dim="channel")
    
    # Compute which pings to keep
    if hasattr(frac_nan, 'compute'):
        keep_mask = (frac_nan < drop_threshold).compute().values
    else:
        keep_mask = (frac_nan < drop_threshold).values
    
    n_dropped = (~keep_mask).sum()
    n_total = len(keep_mask)
    pct_dropped = n_dropped / n_total * 100 if n_total > 0 else 0
    
    logger.info(f"Dropping {n_dropped}/{n_total} pings ({pct_dropped:.1f}%) with ≥{drop_threshold*100:.0f}% NaN")
    
    return ds.isel(ping_time=keep_mask)


def extract_channel(
    sv_dataset: "xr.Dataset",
    channel: int | float,
    freq_coord: str = "frequency_nominal",
) -> "xr.Dataset":
    """
    Extract a single channel from a multichannel dataset.
    
    Args:
        sv_dataset: Dataset with channel dimension
        channel: Channel to extract (by frequency value in Hz or by index)
        freq_coord: Name of the frequency coordinate
        
    Returns:
        Dataset with the specified channel (singleton channel dim preserved)
    """
    if freq_coord in sv_dataset.coords:
        freqs = sv_dataset[freq_coord].values
        if hasattr(freqs, '__iter__') and (freqs == channel).any():
            return sv_dataset.sel(channel=sv_dataset[freq_coord] == channel)
    
    return sv_dataset.isel(channel=int(channel), drop=False)


def create_multichannel_mask(
    masks: list["xr.Dataset"],
    sv_dataset: "xr.Dataset",
) -> "xr.Dataset":
    """
    Create multichannel mask from per-channel masks.
    
    Args:
        masks: List of single-channel masks
        sv_dataset: Source Sv dataset with channel dimension
        
    Returns:
        Combined mask with channel dimension
    """
    import xarray as xr
    
    channel_list = sv_dataset["channel"].values
    
    if len(masks) != len(channel_list):
        raise ValueError("Number of masks must match number of channels")
    
    updated_masks = []
    for i, mask in enumerate(masks):
        if "channel" not in mask.dims:
            mask = mask.expand_dims(dim={"channel": [channel_list[i]]})
        else:
            mask = mask.isel(channel=0).expand_dims(dim={"channel": [channel_list[i]]})
        updated_masks.append(mask)
    
    return xr.concat(
        updated_masks,
        dim="channel",
        data_vars="all",
        coords="all",
        join="exact",
    )
