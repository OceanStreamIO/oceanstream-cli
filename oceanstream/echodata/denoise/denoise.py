"""Main denoising pipeline for Sv datasets.

Provides high-level functions for applying multiple denoising
methods and combining masks.

Ported from _echodata-legacy-code/saildrone-echodata-processing/denoise/
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any, Mapping, Optional, Union

import numpy as np

if TYPE_CHECKING:
    import xarray as xr
    from oceanstream.echodata.config import DenoiseConfig

logger = logging.getLogger(__name__)


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
    methods: list[str],
    config: "DenoiseConfig",
    var_name: str = "Sv",
    return_stage_masks: bool = True,
) -> Union["xr.DataArray", tuple["xr.DataArray", dict]]:
    """
    Build combined noise mask using per-channel processing.
    
    Processes each channel independently, building masks for each
    denoising method and combining them with OR logic.
    
    Args:
        ds: Sv xarray Dataset
        methods: List of method names
        config: DenoiseConfig with parameters
        var_name: Variable to mask (default: "Sv")
        return_stage_masks: If True, also return per-stage masks
        
    Returns:
        Combined mask DataArray, or tuple of (mask, stage_dict) if return_stage_masks
    """
    import xarray as xr
    
    from oceanstream.echodata.denoise.background_noise import background_noise_mask
    from oceanstream.echodata.denoise.transient_noise import transient_noise_mask
    from oceanstream.echodata.denoise.impulse_noise import impulse_noise_mask
    from oceanstream.echodata.denoise.attenuation import attenuation_mask
    
    n_ch = ds.dims.get("channel", 1)
    
    # Initialize stage tracking
    stage_masks = {method: [] for method in methods}
    ch_masks = []
    
    # Map method names to functions and param extractors
    method_specs = {
        "background": {
            "fn": background_noise_mask,
            "params": config.to_background_params(),
        },
        "transient": {
            "fn": transient_noise_mask,
            "params": config.to_transient_params(),
        },
        "impulse": {
            "fn": impulse_noise_mask,
            "params": config.to_impulse_params(),
        },
        "attenuation": {
            "fn": attenuation_mask,
            "params": config.to_attenuation_params(),
        },
    }
    
    # Process each channel
    for ch in range(n_ch):
        ch_ds = ds.isel(channel=ch)
        reference = ds[var_name].isel(channel=ch)
        stage_or = None
        
        for method in methods:
            if method not in method_specs:
                logger.warning(f"Unknown method {method}, skipping")
                continue
            
            spec = method_specs[method]
            fn = spec["fn"]
            params = spec["params"]
            
            try:
                result = fn(ch_ds, params)
                
                # Handle functions that return tuple (mask, unfeasible_mask, ...)
                if isinstance(result, (tuple, list)):
                    stage_mask = result[0]
                else:
                    stage_mask = result
                
                if stage_mask is None:
                    continue
                
                # Ensure mask matches reference coordinates
                stage_mask = stage_mask.broadcast_like(reference)
                stage_mask = stage_mask.reset_coords(drop=True)
                
                # Expand to channel dimension
                ch_value = ds["channel"].values[ch]
                stage_mask = stage_mask.expand_dims(channel=[ch_value])
                
                # Only mask where data is valid
                stage_mask = stage_mask & ~reference.isnull().expand_dims(channel=[ch_value])
                
                # Store per-stage
                stage_masks[method].append(stage_mask)
                
                # Combine with OR logic
                stage_or = stage_mask if stage_or is None else (stage_or | stage_mask)
                
            except Exception as e:
                logger.warning(f"Error computing {method} mask for channel {ch}: {e}")
                continue
        
        if stage_or is not None:
            ch_masks.append(stage_or)
    
    if not ch_masks:
        # Return all-False mask if nothing worked
        full_mask = xr.DataArray(
            np.zeros(ds[var_name].shape, dtype=bool),
            dims=ds[var_name].dims,
            coords=ds[var_name].coords,
        )
        if return_stage_masks:
            return full_mask, {}
        return full_mask
    
    # Combine channel masks
    full_mask = xr.concat(ch_masks, dim="channel")
    full_mask = full_mask.broadcast_like(ds[var_name])
    full_mask.name = "combined_mask"
    
    pct_flagged = float(full_mask.mean().values) * 100
    logger.info(f"Combined mask: {pct_flagged:.1f}% flagged as noise")
    
    if not return_stage_masks:
        return full_mask
    
    # Stitch per-stage lists into cubes
    stage_cubes = {}
    for method, m_list in stage_masks.items():
        if m_list:
            stage_cube = xr.concat(m_list, dim="channel")
            stage_cube = stage_cube.broadcast_like(ds[var_name])
            stage_cubes[method] = stage_cube
    
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
            mask = background_noise_mask(sv_dataset, config.to_background_params())
        
        elif method == "transient":
            from oceanstream.echodata.denoise.transient_noise import transient_noise_mask
            mask = transient_noise_mask(sv_dataset, config.to_transient_params())
        
        elif method == "impulse":
            from oceanstream.echodata.denoise.impulse_noise import impulse_noise_mask
            mask = impulse_noise_mask(sv_dataset, config.to_impulse_params())
        
        elif method == "attenuation":
            from oceanstream.echodata.denoise.attenuation import attenuation_mask
            mask = attenuation_mask(sv_dataset, config.to_attenuation_params())
        
        else:
            logger.warning(f"Unknown denoising method: {method}, skipping")
            continue
        
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
