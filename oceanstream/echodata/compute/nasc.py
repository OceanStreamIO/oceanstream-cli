"""Compute NASC (Nautical Area Scattering Coefficient).

NASC provides integrated acoustic backscatter per nautical mile squared,
commonly used for biomass estimation in fisheries acoustics.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Optional, Union

import numpy as np

if TYPE_CHECKING:
    import xarray as xr
    from echopype.echodata import EchoData

logger = logging.getLogger(__name__)


def _ensure_depth(
    sv_dataset: "xr.Dataset",
    echodata: Optional["EchoData"] = None,
    transducer_depth: float = 0.0,
) -> "xr.Dataset":
    """
    Ensure Sv dataset has depth coordinate required for NASC.
    
    Uses the intelligent depth computation from consolidate module,
    which will:
    1. Use echopype.consolidate.add_depth with EchoData (if provided)
    2. Fall back to computing from echo_range + offset
    3. Last resort: use range_sample indices
    
    Args:
        sv_dataset: Sv xarray Dataset
        echodata: Optional EchoData for metadata-aware depth computation
        transducer_depth: Depth of transducer below surface (meters), default 0
        
    Returns:
        Dataset with 'depth' data variable
    """
    if "depth" in sv_dataset.data_vars:
        logger.debug("Dataset already has depth variable")
        return sv_dataset
    
    from oceanstream.echodata.consolidate import add_depth_to_sv
    
    return add_depth_to_sv(
        sv_dataset,
        echodata=echodata,
        depth_offset=transducer_depth,
    )


def _ensure_location(sv_dataset: "xr.Dataset") -> "xr.Dataset":
    """
    Ensure latitude/longitude are data variables (not just coords).
    
    echopype.commongrid.compute_NASC requires lat/lon as data_vars.
    """
    # Check if we have location data at all
    has_lat = "latitude" in sv_dataset.data_vars or "latitude" in sv_dataset.coords
    has_lon = "longitude" in sv_dataset.data_vars or "longitude" in sv_dataset.coords
    
    if not has_lat or not has_lon:
        raise ValueError(
            "NASC requires latitude and longitude. "
            "Use enrich_sv_with_location() to add GPS data from geoparquet."
        )
    
    # Promote coords to data_vars if needed
    if "latitude" in sv_dataset.coords and "latitude" not in sv_dataset.data_vars:
        sv_dataset = sv_dataset.assign(latitude=sv_dataset.coords["latitude"])
        logger.debug("Promoted latitude from coord to data_var")
    
    if "longitude" in sv_dataset.coords and "longitude" not in sv_dataset.data_vars:
        sv_dataset = sv_dataset.assign(longitude=sv_dataset.coords["longitude"])
        logger.debug("Promoted longitude from coord to data_var")
    
    return sv_dataset


def compute_nasc(
    sv_dataset: Union[Path, "xr.Dataset"],
    range_bin: str = "10m",
    dist_bin: str = "0.5nmi",
    transducer_depth: float = 0.0,
    echodata: Optional["EchoData"] = None,
    output_path: Optional[Path] = None,
) -> "xr.Dataset":
    """
    Compute Nautical Area Scattering Coefficient (NASC).
    
    NASC integrates acoustic backscatter over depth layers and
    horizontal distance, providing a measure of acoustic biomass
    per unit area (m² per nautical mile²).
    
    Args:
        sv_dataset: Sv xarray Dataset or path to Sv Zarr
        range_bin: Vertical bin size (e.g., "10m", "20m")
        dist_bin: Horizontal distance bin (e.g., "0.5nmi", "1nmi")
        transducer_depth: Depth of transducer below surface in meters
        echodata: Optional EchoData for accurate depth from platform metadata
        output_path: Optional path to save result
        
    Returns:
        xarray.Dataset with NASC values
        
    Note:
        NASC computation requires:
        - latitude, longitude: for distance calculations
        - depth: for vertical binning
        
        Use enrich_sv_with_location() first if location data is missing.
        
        For most accurate depth, provide the EchoData object which contains
        platform metadata (vertical offsets, pitch/roll) for tilt correction.
        
    Example:
        # Enrich with location first
        sv = enrich_sv_with_location(sv_ds, campaign_id="TPOS2023")
        
        # Compute NASC (with EchoData for accurate depth)
        nasc = compute_nasc(sv, echodata=ed, range_bin="10m", dist_bin="0.5nmi")
        
        # Or without EchoData (uses echo_range + offset)
        nasc = compute_nasc(sv, range_bin="10m", transducer_depth=0.6)
    """
    try:
        import echopype as ep
        import xarray as xr
    except ImportError as e:
        raise ImportError("echopype and xarray required for NASC computation") from e
    
    # Load Sv if path provided
    if isinstance(sv_dataset, (str, Path)):
        logger.info(f"Loading Sv from {sv_dataset}")
        sv_dataset = xr.open_zarr(sv_dataset)
    
    # Ensure required variables
    sv_dataset = _ensure_location(sv_dataset)
    sv_dataset = _ensure_depth(sv_dataset, echodata=echodata, transducer_depth=transducer_depth)
    
    logger.info(f"Computing NASC with range_bin={range_bin}, dist_bin={dist_bin}")
    logger.info(f"  latitude range: [{float(sv_dataset['latitude'].min()):.3f}, {float(sv_dataset['latitude'].max()):.3f}]")
    logger.info(f"  depth range: [{float(sv_dataset['depth'].min()):.1f}, {float(sv_dataset['depth'].max()):.1f}] m")
    
    ds_NASC = ep.commongrid.compute_NASC(
        sv_dataset,
        range_bin=range_bin,
        dist_bin=dist_bin,
    )
    
    # Add NASC_log for visualization (ported from legacy workflow.py:555)
    # Log transform provides better visual representation across dynamic range
    ds_NASC["NASC_log"] = 10 * np.log10(ds_NASC["NASC"])
    ds_NASC["NASC_log"].attrs = {
        "long_name": "Log10-transformed NASC",
        "units": "dB re 1 m² nmi⁻²",
        "description": "10 * log10(NASC) for visualization",
    }
    
    # Add attributes
    ds_NASC.attrs["processing"] = "NASC computed with oceanstream"
    ds_NASC.attrs["range_bin"] = range_bin
    ds_NASC.attrs["dist_bin"] = dist_bin
    ds_NASC.attrs["units"] = "m2 nmi-2"
    
    # Save if output path provided
    if output_path:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        logger.info(f"Saving NASC to {output_path}")
        ds_NASC.to_zarr(output_path, mode="w")
        
        # Consolidate metadata
        import zarr
        zarr.consolidate_metadata(output_path)
    
    return ds_NASC


def compute_nasc_denoised(
    sv_dataset: Union[Path, "xr.Dataset"],
    noise_mask: "xr.DataArray",
    range_bin: str = "10m",
    dist_bin: str = "0.5nmi",
    output_path: Optional[Path] = None,
) -> "xr.Dataset":
    """
    Compute NASC from denoised Sv data.
    
    Applies noise mask before computing NASC to exclude
    contaminated samples.
    
    Args:
        sv_dataset: Sv xarray Dataset or path
        noise_mask: Boolean mask (True = noise to exclude)
        range_bin: Vertical bin size
        dist_bin: Distance bin size
        output_path: Optional path to save result
        
    Returns:
        xarray.Dataset with NASC from denoised data
    """
    import xarray as xr
    import numpy as np
    
    # Load Sv if path provided
    if isinstance(sv_dataset, (str, Path)):
        sv_dataset = xr.open_zarr(sv_dataset)
    
    # Apply mask
    sv_denoised = sv_dataset.copy()
    sv_denoised["Sv"] = sv_dataset["Sv"].where(~noise_mask, np.nan)
    
    return compute_nasc(
        sv_denoised,
        range_bin=range_bin,
        dist_bin=dist_bin,
        output_path=output_path,
    )
