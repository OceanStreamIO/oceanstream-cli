"""Depth computation for Sv datasets.

This module provides functions to add depth coordinates to Sv datasets,
using echopype's consolidate.add_depth with intelligent flag selection
based on available EchoData platform metadata.

Adapted from legacy saildrone-echodata-processing code.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional, Union

import numpy as np

if TYPE_CHECKING:
    import xarray as xr
    from echopype.echodata import EchoData

logger = logging.getLogger(__name__)


def choose_depth_flags(
    echodata: "EchoData",
    depth_offset: float = 0.0,
    downward: bool = True,
) -> dict[str, Any]:
    """
    Determine optimal flags for echopype.consolidate.add_depth().
    
    Intelligently selects depth computation method based on available
    metadata in the EchoData Platform group:
    
    1. **Platform vertical offsets**: If transducer_offset_z, water_level,
       and vertical_offset are all present, use them (most accurate)
    2. **Platform angles**: If pitch/roll are available, use for tilt correction
    3. **Beam angles**: Fall back to beam direction angles if platform angles unavailable
    4. **User depth_offset**: Used only if platform offsets unavailable
    
    Args:
        echodata: EchoData object containing platform metadata
        depth_offset: Fallback transducer depth below surface (meters)
        downward: True if transducer points downward (default)
        
    Returns:
        Dict of kwargs for echopype.consolidate.add_depth()
        
    Example:
        flags = choose_depth_flags(echodata, depth_offset=0.6)
        ds_Sv = ep.consolidate.add_depth(ds_Sv, echodata, **flags)
    """
    platform = echodata["Platform"]

    # ─────────────────────────────────────────────────────────────────
    # 1. Vertical offsets: transducer_offset_z − water_level − vertical_offset
    # ─────────────────────────────────────────────────────────────────
    vert_vars = ["transducer_offset_z", "water_level", "vertical_offset"]
    have_all_vert = all(name in platform for name in vert_vars)

    use_platform_vertical_offsets = False
    if have_all_vert:
        vert_values = platform[vert_vars].to_array()
        use_platform_vertical_offsets = not vert_values.isnull().any()
        if use_platform_vertical_offsets:
            logger.info("Using platform vertical offsets for depth computation")

    # If we can use the three offsets, ignore user-supplied depth_offset
    depth_offset_kwarg = None if use_platform_vertical_offsets else depth_offset
    if depth_offset_kwarg is not None:
        logger.info(f"Using user-supplied depth_offset={depth_offset}m")

    # ─────────────────────────────────────────────────────────────────
    # 2. Tilt correction: prefer platform angles, else beam angles
    # ─────────────────────────────────────────────────────────────────
    plat_angle_vars = ["platform_pitch", "platform_roll"]
    plat_angles_present = all(v in platform for v in plat_angle_vars) and not (
        platform[plat_angle_vars].to_array().isnull().any()
    )

    # Beam angles exist if beam_direction_x variable is in Sonar groups
    beam_angles_present = any(
        "beam_direction_x" in str(var) for var in echodata["Sonar"].data_vars
    )

    use_platform_angles = plat_angles_present
    use_beam_angles = (not plat_angles_present) and beam_angles_present

    if use_platform_angles:
        logger.info("Using platform pitch/roll for tilt correction")
    elif use_beam_angles:
        logger.info("Using beam angles for tilt correction")

    # ─────────────────────────────────────────────────────────────────
    # 3. Return packed flags
    # ─────────────────────────────────────────────────────────────────
    return {
        "depth_offset": depth_offset_kwarg,
        "use_platform_vertical_offsets": use_platform_vertical_offsets,
        "use_platform_angles": use_platform_angles,
        "use_beam_angles": use_beam_angles,
        "downward": bool(downward),
    }


def add_depth_to_sv(
    ds_Sv: "xr.Dataset",
    echodata: Optional["EchoData"] = None,
    depth_offset: float = 0.0,
    downward: bool = True,
    auto_flags: bool = True,
) -> "xr.Dataset":
    """
    Add depth variable to Sv dataset.
    
    This function provides multiple strategies for adding depth:
    
    1. **With EchoData** (recommended): Uses echopype.consolidate.add_depth
       with intelligent flag selection based on platform metadata
    2. **Without EchoData**: Computes depth from echo_range + offset
    
    Args:
        ds_Sv: Sv xarray Dataset (from compute_Sv)
        echodata: Optional EchoData object for metadata-aware computation
        depth_offset: Transducer depth below surface (meters)
        downward: True if transducer points downward
        auto_flags: If True, automatically select best flags based on metadata
        
    Returns:
        Dataset with 'depth' data variable added
        
    Example:
        # With EchoData (best accuracy)
        ds_Sv = add_depth_to_sv(ds_Sv, echodata, depth_offset=0.6)
        
        # Without EchoData (fallback)
        ds_Sv = add_depth_to_sv(ds_Sv, depth_offset=0.6)
    """
    import xarray as xr
    
    # Already has depth?
    if "depth" in ds_Sv.data_vars:
        logger.debug("Dataset already has depth variable")
        return ds_Sv
    
    # Strategy 1: Use echopype.consolidate.add_depth with EchoData
    if echodata is not None:
        try:
            from echopype.consolidate import add_depth
            
            if auto_flags:
                flags = choose_depth_flags(echodata, depth_offset, downward)
            else:
                flags = {
                    "depth_offset": depth_offset,
                    "downward": downward,
                }
            
            ds_Sv = add_depth(ds_Sv, echodata, **flags)
            logger.info("Added depth using echopype.consolidate.add_depth()")
            return ds_Sv
            
        except Exception as e:
            logger.warning(f"Failed to add depth via echopype: {e}. Falling back to manual computation.")
    
    # Strategy 2: Compute from echo_range
    if "echo_range" in ds_Sv:
        logger.info(f"Computing depth from echo_range (offset={depth_offset}m)")
        
        # echo_range shape: (channel, ping_time, range_sample)
        # For depth, we want a single 1D array on range_sample dimension
        # Take first channel/ping as representative (usually same across all)
        if "channel" in ds_Sv["echo_range"].dims and "ping_time" in ds_Sv["echo_range"].dims:
            echo_range_1d = ds_Sv["echo_range"].isel(channel=0, ping_time=0).values
        else:
            echo_range_1d = ds_Sv["echo_range"].values
        
        depth_values = depth_offset + echo_range_1d
        
        # Add as data variable (NASC requires data_var, not coord)
        # Broadcast to match Sv dimensions
        if len(ds_Sv["echo_range"].dims) == 3:
            # Full 3D depth matching Sv
            depth_3d = depth_offset + ds_Sv["echo_range"]
            ds_Sv = ds_Sv.assign(depth=depth_3d)
        else:
            ds_Sv = ds_Sv.assign(depth=(["range_sample"], depth_values))
        
        ds_Sv["depth"].attrs = {
            "long_name": "Depth below surface",
            "units": "m",
            "positive": "down",
            "computed_from": "echo_range",
            "transducer_depth": depth_offset,
        }
        return ds_Sv
    
    # Strategy 3: Use range_sample as proxy (least accurate)
    if "range_sample" in ds_Sv.dims:
        logger.warning(
            "No echo_range found. Using range_sample indices as depth proxy. "
            "This may be inaccurate - provide EchoData or add echo_range for better results."
        )
        range_vals = ds_Sv["range_sample"].values.astype(float)
        depth_vals = depth_offset + range_vals
        
        ds_Sv = ds_Sv.assign(depth=(["range_sample"], depth_vals))
        ds_Sv["depth"].attrs = {
            "long_name": "Approximate depth",
            "units": "m",
            "positive": "down",
            "note": "Computed from range_sample index - may be inaccurate",
        }
        return ds_Sv
    
    raise ValueError(
        "Cannot add depth: no 'echo_range' or 'range_sample' found. "
        "Provide EchoData object or ensure echo_range is in the dataset."
    )


def add_depth_from_echodata(
    ds_Sv: "xr.Dataset",
    echodata_path: Union[str, Path],
    depth_offset: float = 0.0,
) -> "xr.Dataset":
    """
    Add depth to Sv dataset by loading EchoData from path.
    
    Convenience function when you have the Sv dataset but need to
    load EchoData from a Zarr store to get accurate depth.
    
    Args:
        ds_Sv: Sv xarray Dataset
        echodata_path: Path to EchoData Zarr store
        depth_offset: Fallback transducer depth (meters)
        
    Returns:
        Dataset with depth added
        
    Example:
        ds_Sv = add_depth_from_echodata(ds_Sv, "./echodata/file.zarr", depth_offset=0.6)
    """
    try:
        from echopype.echodata import EchoData
        
        echodata_path = Path(echodata_path)
        if not echodata_path.exists():
            raise FileNotFoundError(f"EchoData not found: {echodata_path}")
        
        logger.info(f"Loading EchoData from {echodata_path}")
        echodata = EchoData.from_zarr(echodata_path)
        
        return add_depth_to_sv(ds_Sv, echodata, depth_offset=depth_offset)
        
    except ImportError:
        logger.warning("echopype not available, using fallback depth computation")
        return add_depth_to_sv(ds_Sv, echodata=None, depth_offset=depth_offset)
