"""
Seabed detection algorithms for echosounder data.

This module provides multiple algorithms for detecting seabed echoes:
- maxSv: Finds strongest echo within depth gate, searches upward for edge
- deltaSv: Finds first large Sv gradient (rapid change = seabed surface)
- ariza: Morphological approach using erosion/dilation - robust for automation

All algorithms operate on xarray Datasets with Sv data and return a
SeabedDetectionResult containing the seabed line (depth per ping) and
diagnostic information.

References:
    - echopy library: https://github.com/open-ocean-sounding/echopy
    - De Robertis & Higginbottom (2007), ICES Journal of Marine Science
"""

from dataclasses import dataclass
from typing import Literal, Optional

import numpy as np
import xarray as xr
from scipy.ndimage import median_filter, binary_erosion, binary_dilation
from scipy.signal import savgol_filter


@dataclass
class SeabedDetectionResult:
    """Result of seabed detection containing the seabed line and diagnostics.
    
    Attributes:
        seabed_depth: DataArray with seabed depth (m) per ping. NaN = no seabed.
        seabed_index: DataArray with range sample index per ping.
        method: Algorithm used for detection.
        channel: Channel used for detection.
        pings_detected: Number of pings where seabed was detected.
        pings_total: Total number of pings.
        detection_rate: Fraction of pings with detected seabed.
        params: Parameters used for detection.
    """
    seabed_depth: xr.DataArray
    seabed_index: xr.DataArray
    method: str
    channel: str
    pings_detected: int
    pings_total: int
    detection_rate: float
    params: dict


def _get_range_array(ds: xr.Dataset) -> tuple[xr.DataArray, str]:
    """Get the range array and dimension name from a dataset.
    
    Args:
        ds: Dataset with Sv data.
        
    Returns:
        Tuple of (range_array, range_dim_name).
    """
    # Check common range variable names
    if "echo_range" in ds.data_vars:
        range_arr = ds["echo_range"]
    elif "echo_range" in ds.coords:
        range_arr = ds.coords["echo_range"]
    elif "range" in ds.coords:
        range_arr = ds.coords["range"]
    elif "depth" in ds.coords:
        range_arr = ds.coords["depth"]
    else:
        raise ValueError("No range coordinate found (echo_range, range, or depth)")
    
    # Determine range dimension
    if "range_sample" in ds.dims:
        range_dim = "range_sample"
    elif "depth" in ds.dims:
        range_dim = "depth"
    else:
        # Try to infer from range array dimensions
        for dim in range_arr.dims:
            if dim not in ["ping_time", "channel"]:
                range_dim = str(dim)
                break
        else:
            raise ValueError("Cannot determine range dimension")
    
    return range_arr, range_dim


def _get_sv_2d(ds: xr.Dataset, channel: Optional[str] = None) -> tuple[np.ndarray, np.ndarray, str]:
    """Extract 2D Sv array and range for a single channel.
    
    Args:
        ds: Dataset with Sv data.
        channel: Channel to extract. If None, uses first/only channel.
        
    Returns:
        Tuple of (Sv_2d, range_1d, channel_label).
    """
    Sv = ds["Sv"]
    range_arr, range_dim = _get_range_array(ds)
    
    # Handle channel selection
    if "channel" in Sv.dims:
        if channel is not None:
            # Find matching channel
            channels = Sv.channel.values
            if channel in channels:
                Sv = Sv.sel(channel=channel)
            else:
                # Try partial match (e.g., "38" in "WBT 742057-15 ES38-18")
                matches = [c for c in channels if channel in str(c)]
                if matches:
                    Sv = Sv.sel(channel=matches[0])
                    channel = str(matches[0])
                else:
                    raise ValueError(f"Channel '{channel}' not found. Available: {list(channels)}")
        else:
            # Use first channel (typically 38 kHz for seabed)
            Sv = Sv.isel(channel=0)
            channel = str(Sv.channel.values) if "channel" in Sv.coords else "channel_0"
    else:
        channel = channel or "single_channel"
    
    # Get range array for this channel
    if "channel" in range_arr.dims:
        if channel in range_arr.channel.values:
            range_1d = range_arr.sel(channel=channel)
        else:
            range_1d = range_arr.isel(channel=0)
    else:
        range_1d = range_arr
    
    # Extract 1D range (take first ping if 2D)
    if range_1d.ndim > 1:
        range_1d = range_1d.isel(ping_time=0)
    
    # Compute arrays
    Sv_2d = Sv.values
    if Sv_2d.ndim == 1:
        raise ValueError("Sv must be at least 2D (ping_time x range_sample)")
    
    range_1d = range_1d.values
    
    # Ensure (ping_time, range_sample) order
    if "ping_time" in Sv.dims:
        ping_idx = list(Sv.dims).index("ping_time")
        if ping_idx != 0:
            Sv_2d = np.moveaxis(Sv_2d, ping_idx, 0)
    
    return Sv_2d, range_1d, channel


def detect_seabed_maxSv(
    ds: xr.Dataset,
    channel: Optional[str] = None,
    r0: float = 10,
    r1: float = 1000,
    roff: float = 0,
    thr: tuple[float, float] = (-40, -60),
    median_kernel: tuple[int, int] = (3, 3),
) -> SeabedDetectionResult:
    """Detect seabed using maximum Sv method.
    
    Finds the sample with strongest Sv within the depth gate. Then searches
    upward until Sv falls below a secondary threshold, where the seabed
    surface is set. Robust for strong, consistent seabed echoes.
    
    Args:
        ds: Dataset with Sv data.
        channel: Channel to use (default: first, typically 38 kHz).
        r0: Minimum search range (m).
        r1: Maximum search range (m).
        roff: Range offset to apply to detected seabed (m).
        thr: Tuple of (primary, secondary) Sv thresholds (dB).
            Primary: minimum Sv for initial detection.
            Secondary: Sv floor for upward search.
        median_kernel: Kernel size for median filtering (pings, samples).
        
    Returns:
        SeabedDetectionResult with seabed line and diagnostics.
        
    References:
        echopy.processing.mask_seabed.maxSv
    """
    # Extract 2D arrays
    Sv_2d, range_1d, ch_label = _get_sv_2d(ds, channel)
    n_pings, n_samples = Sv_2d.shape
    
    # Apply median filter to reduce noise
    Sv_filt = median_filter(Sv_2d, size=median_kernel)
    
    # Get range indices for search gate
    i0 = np.nanargmin(np.abs(range_1d - r0))
    i1 = np.nanargmin(np.abs(range_1d - r1)) + 1
    i1 = min(i1, n_samples)
    
    # Get offset as number of samples
    roff_samples = np.nanargmin(np.abs(range_1d - roff)) if roff > 0 else 0
    
    # Initialize output arrays
    seabed_idx = np.zeros(n_pings, dtype=np.int32)
    
    # For each ping, find max Sv in gate
    Sv_gate = Sv_filt[:, i0:i1]
    
    # Handle all-NaN pings
    valid_pings = ~np.isnan(Sv_gate).all(axis=1)
    
    # Get indices of max Sv within gate
    with np.errstate(invalid='ignore'):
        max_idx_in_gate = np.nanargmax(Sv_gate, axis=1)
    max_idx = max_idx_in_gate + i0
    
    # Get max Sv values
    max_sv = np.array([Sv_filt[p, max_idx[p]] if valid_pings[p] else np.nan 
                       for p in range(n_pings)])
    
    # Reject pings where max Sv is below primary threshold
    detected = valid_pings & (max_sv >= thr[0])
    
    # For detected pings, search upward until below secondary threshold
    for p in range(n_pings):
        if not detected[p]:
            continue
            
        i = max_idx[p]
        
        # Search upward, checking 5-sample mean
        while i >= 5:
            window = Sv_filt[p, max(0, i-5):i]
            if np.isnan(window).all():
                break
            mean_sv = 10 * np.log10(np.nanmean(10 ** (window / 10)))
            if mean_sv < thr[1]:
                break
            i -= 1
        
        # Apply offset and store
        i = max(0, i - roff_samples)
        seabed_idx[p] = i
    
    # Mark non-detected pings
    seabed_idx[~detected] = -1
    
    # Convert to depth
    seabed_depth = np.where(detected, range_1d[np.clip(seabed_idx, 0, n_samples-1)], np.nan)
    
    # Get ping times
    ping_times = ds.ping_time.values if "ping_time" in ds.coords else np.arange(n_pings)
    
    # Create DataArrays
    depth_da = xr.DataArray(
        seabed_depth,
        dims=["ping_time"],
        coords={"ping_time": ping_times},
        name="seabed_depth",
        attrs={"units": "m", "long_name": "Seabed depth"}
    )
    
    idx_da = xr.DataArray(
        seabed_idx,
        dims=["ping_time"],
        coords={"ping_time": ping_times},
        name="seabed_index"
    )
    
    n_detected = int(detected.sum())
    
    return SeabedDetectionResult(
        seabed_depth=depth_da,
        seabed_index=idx_da,
        method="maxSv",
        channel=ch_label,
        pings_detected=n_detected,
        pings_total=n_pings,
        detection_rate=n_detected / n_pings if n_pings > 0 else 0.0,
        params={"r0": r0, "r1": r1, "roff": roff, "thr": thr, "median_kernel": median_kernel}
    )


def detect_seabed_deltaSv(
    ds: xr.Dataset,
    channel: Optional[str] = None,
    r0: float = 10,
    r1: float = 1000,
    roff: float = 0,
    thr: float = 20,
    median_kernel: tuple[int, int] = (3, 3),
) -> SeabedDetectionResult:
    """Detect seabed using delta Sv (gradient) method.
    
    Examines the Sv difference over a moving window along each ping.
    Returns the range of the first value that exceeds a threshold,
    indicating a sharp transition (seabed surface).
    
    Args:
        ds: Dataset with Sv data.
        channel: Channel to use (default: first, typically 38 kHz).
        r0: Minimum search range (m).
        r1: Maximum search range (m).
        roff: Range offset to apply to detected seabed (m).
        thr: Threshold for Sv difference (dB). Higher = stricter.
        median_kernel: Kernel size for median filtering.
        
    Returns:
        SeabedDetectionResult with seabed line and diagnostics.
        
    References:
        echopy.processing.mask_seabed.deltaSv
    """
    # Extract 2D arrays
    Sv_2d, range_1d, ch_label = _get_sv_2d(ds, channel)
    n_pings, n_samples = Sv_2d.shape
    
    # Apply median filter
    Sv_filt = median_filter(Sv_2d, size=median_kernel)
    
    # Compute Sv difference along range
    Sv_diff = np.diff(Sv_filt, axis=1)
    # Pad to maintain shape
    Sv_diff = np.concatenate([np.full((n_pings, 1), np.nan), Sv_diff], axis=1)
    
    # Get range indices
    i0 = np.nanargmin(np.abs(range_1d - r0))
    i1 = np.nanargmin(np.abs(range_1d - r1)) + 1
    i1 = min(i1, n_samples)
    
    roff_samples = np.nanargmin(np.abs(range_1d - roff)) if roff > 0 else 0
    
    # Initialize output
    seabed_idx = np.full(n_pings, -1, dtype=np.int32)
    
    # For each ping, find first sample where diff exceeds threshold
    for p in range(n_pings):
        diff_slice = Sv_diff[p, i0:i1]
        
        # Find first index above threshold
        above_thr = np.where(diff_slice > thr)[0]
        
        if len(above_thr) > 0:
            idx = above_thr[0] + i0
            idx = max(0, idx - roff_samples)
            seabed_idx[p] = idx
    
    # Convert to depth
    detected = seabed_idx >= 0
    seabed_depth = np.where(detected, range_1d[np.clip(seabed_idx, 0, n_samples-1)], np.nan)
    
    # Get ping times
    ping_times = ds.ping_time.values if "ping_time" in ds.coords else np.arange(n_pings)
    
    # Create DataArrays
    depth_da = xr.DataArray(
        seabed_depth,
        dims=["ping_time"],
        coords={"ping_time": ping_times},
        name="seabed_depth",
        attrs={"units": "m", "long_name": "Seabed depth"}
    )
    
    idx_da = xr.DataArray(
        seabed_idx,
        dims=["ping_time"],
        coords={"ping_time": ping_times},
        name="seabed_index"
    )
    
    n_detected = int(detected.sum())
    
    return SeabedDetectionResult(
        seabed_depth=depth_da,
        seabed_index=idx_da,
        method="deltaSv",
        channel=ch_label,
        pings_detected=n_detected,
        pings_total=n_pings,
        detection_rate=n_detected / n_pings if n_pings > 0 else 0.0,
        params={"r0": r0, "r1": r1, "roff": roff, "thr": thr, "median_kernel": median_kernel}
    )


def detect_seabed_ariza(
    ds: xr.Dataset,
    channel: Optional[str] = None,
    r0: float = 10,
    r1: float = 1000,
    roff: float = 0,
    thr: float = -40,
    erosion_cycles: int = 1,
    erosion_kernel: tuple[int, int] = (3, 3),
    dilation_cycles: int = 3,
    dilation_kernel: tuple[int, int] = (3, 7),
    median_kernel: tuple[int, int] = (3, 3),
    smoothing: bool = True,
    smooth_window: int = 11,
) -> SeabedDetectionResult:
    """Detect seabed using Ariza's morphological method.
    
    Uses erosion to remove noise/spikes and dilation to fill gaps.
    More robust for automated processing than threshold-only methods.
    Recommended for pelagic surveys and unsupervised processing.
    
    Args:
        ds: Dataset with Sv data.
        channel: Channel to use (default: first).
        r0: Minimum search range (m).
        r1: Maximum search range (m).
        roff: Range offset below seabed (m).
        thr: Sv threshold above which seabed may occur (dB).
        erosion_cycles: Number of erosion iterations.
        erosion_kernel: Kernel size for erosion (range, ping).
        dilation_cycles: Number of dilation iterations.
        dilation_kernel: Kernel size for dilation (range, ping).
        median_kernel: Kernel size for pre-filtering.
        smoothing: Apply Savitzky-Golay smoothing to output.
        smooth_window: Window size for smoothing.
        
    Returns:
        SeabedDetectionResult with seabed line and diagnostics.
        
    References:
        echopy.processing.mask_seabed.ariza
        Ariza et al. (in prep) - Morphological seabed detection
    """
    # Extract 2D arrays
    Sv_2d, range_1d, ch_label = _get_sv_2d(ds, channel)
    n_pings, n_samples = Sv_2d.shape
    
    # Apply median filter
    Sv_filt = median_filter(Sv_2d, size=median_kernel)
    
    # Get range indices
    i0 = np.nanargmin(np.abs(range_1d - r0))
    i1 = np.nanargmin(np.abs(range_1d - r1)) + 1
    i1 = min(i1, n_samples)
    
    roff_samples = np.nanargmin(np.abs(range_1d - roff)) if roff > 0 else 0
    
    # Check if anything above threshold in search range
    Sv_search = Sv_filt[:, i0:i1].copy()
    if not (Sv_search > thr).any():
        # No seabed detected
        ping_times = ds.ping_time.values if "ping_time" in ds.coords else np.arange(n_pings)
        empty_depth = xr.DataArray(
            np.full(n_pings, np.nan),
            dims=["ping_time"],
            coords={"ping_time": ping_times},
            name="seabed_depth"
        )
        empty_idx = xr.DataArray(
            np.full(n_pings, -1, dtype=np.int32),
            dims=["ping_time"],
            coords={"ping_time": ping_times},
            name="seabed_index"
        )
        return SeabedDetectionResult(
            seabed_depth=empty_depth,
            seabed_index=empty_idx,
            method="ariza",
            channel=ch_label,
            pings_detected=0,
            pings_total=n_pings,
            detection_rate=0.0,
            params={"r0": r0, "r1": r1, "roff": roff, "thr": thr,
                    "erosion_cycles": erosion_cycles, "dilation_cycles": dilation_cycles}
        )
    
    # Create binary mask of potential seabed (Sv > threshold)
    # Replace values below threshold with very low value for morphology
    Sv_work = Sv_filt.copy()
    Sv_work[:, :i0] = -999
    Sv_work[:, i1:] = -999
    
    seabed = Sv_work.copy()
    seabed[seabed < thr] = -999
    
    # Run erosion cycles to remove noise/spikes
    erosion_struct = np.ones(erosion_kernel, dtype=bool)
    for _ in range(erosion_cycles):
        seabed_mask = seabed > -999
        seabed_mask = binary_erosion(seabed_mask, structure=erosion_struct)
        seabed[~seabed_mask] = -999
    
    # Run dilation cycles to fill gaps
    dilation_struct = np.ones(dilation_kernel, dtype=bool)
    for _ in range(dilation_cycles):
        seabed_mask = seabed > -999
        seabed_mask = binary_dilation(seabed_mask, structure=dilation_struct)
        seabed[seabed_mask & (Sv_work > -999)] = Sv_work[seabed_mask & (Sv_work > -999)]
    
    # Final mask
    mask = seabed > -999
    
    # Extract seabed line as first True value per ping
    seabed_idx = np.full(n_pings, -1, dtype=np.int32)
    
    for p in range(n_pings):
        ping_mask = mask[p, :]
        if ping_mask.any():
            idx = np.argmax(ping_mask)  # First True
            idx = max(0, idx - roff_samples)
            seabed_idx[p] = idx
    
    # Apply Savitzky-Golay smoothing if enabled
    if smoothing and (seabed_idx >= 0).sum() > smooth_window:
        valid = seabed_idx >= 0
        if valid.sum() >= smooth_window:
            # Interpolate gaps for smoothing
            idx_smooth = seabed_idx.astype(float).copy()
            idx_smooth[~valid] = np.nan
            
            # Savgol on valid segments
            valid_idx = np.where(valid)[0]
            if len(valid_idx) >= smooth_window:
                window = min(smooth_window, len(valid_idx))
                if window % 2 == 0:
                    window -= 1
                if window >= 3:
                    idx_smooth[valid] = savgol_filter(
                        seabed_idx[valid].astype(float), 
                        window, 
                        polyorder=1
                    )
                    seabed_idx[valid] = np.clip(idx_smooth[valid].astype(int), 0, n_samples-1)
    
    # Convert to depth
    detected = seabed_idx >= 0
    seabed_depth = np.where(detected, range_1d[np.clip(seabed_idx, 0, n_samples-1)], np.nan)
    
    # Get ping times
    ping_times = ds.ping_time.values if "ping_time" in ds.coords else np.arange(n_pings)
    
    # Create DataArrays
    depth_da = xr.DataArray(
        seabed_depth,
        dims=["ping_time"],
        coords={"ping_time": ping_times},
        name="seabed_depth",
        attrs={"units": "m", "long_name": "Seabed depth"}
    )
    
    idx_da = xr.DataArray(
        seabed_idx,
        dims=["ping_time"],
        coords={"ping_time": ping_times},
        name="seabed_index"
    )
    
    n_detected = int(detected.sum())
    
    return SeabedDetectionResult(
        seabed_depth=depth_da,
        seabed_index=idx_da,
        method="ariza",
        channel=ch_label,
        pings_detected=n_detected,
        pings_total=n_pings,
        detection_rate=n_detected / n_pings if n_pings > 0 else 0.0,
        params={"r0": r0, "r1": r1, "roff": roff, "thr": thr,
                "erosion_cycles": erosion_cycles, "erosion_kernel": erosion_kernel,
                "dilation_cycles": dilation_cycles, "dilation_kernel": dilation_kernel,
                "smoothing": smoothing, "smooth_window": smooth_window}
    )


def detect_seabed(
    ds: xr.Dataset,
    method: Literal["maxSv", "deltaSv", "ariza"] = "ariza",
    channel: Optional[str] = None,
    r0: float = 10,
    r1: float = 1000,
    **kwargs
) -> SeabedDetectionResult:
    """Detect seabed using the specified algorithm.
    
    This is the main entry point for seabed detection. Automatically handles
    the case where no seabed is present within the search range.
    
    Args:
        ds: Dataset with Sv data.
        method: Detection algorithm to use:
            - "maxSv": Best for strong, consistent seabed echoes
            - "deltaSv": Best for sharp seabed transitions
            - "ariza": Best for automation, handles noise/gaps well
        channel: Channel to use. If None, uses first channel (often 38 kHz).
            Lower frequencies (38 kHz) penetrate deeper, good for seabed.
        r0: Minimum search range in meters (default: 10).
        r1: Maximum search range in meters (default: 1000).
        **kwargs: Additional parameters passed to the specific algorithm.
        
    Returns:
        SeabedDetectionResult with:
            - seabed_depth: DataArray with depth (m) per ping, NaN if not detected
            - seabed_index: DataArray with range sample index
            - method: Algorithm used
            - channel: Channel used
            - pings_detected: Number of pings with detected seabed
            - detection_rate: Fraction of pings with seabed
            - params: Parameters used
            
    Examples:
        >>> # Basic usage
        >>> result = detect_seabed(sv_dataset)
        >>> print(f"Detected seabed in {result.detection_rate:.1%} of pings")
        
        >>> # Using specific method and parameters
        >>> result = detect_seabed(sv_dataset, method="maxSv", r1=500, thr=(-35, -55))
        
        >>> # Handle no seabed case
        >>> if result.pings_detected == 0:
        ...     print("No seabed detected - likely open ocean")
        
    Notes:
        - For open ocean data (depth > max range), detection_rate will be ~0
        - 38 kHz is typically best for seabed detection (deepest penetration)
        - r1 should match your expected maximum depth + margin
    """
    methods = {
        "maxSv": detect_seabed_maxSv,
        "deltaSv": detect_seabed_deltaSv,
        "ariza": detect_seabed_ariza,
    }
    
    if method not in methods:
        raise ValueError(f"Unknown method '{method}'. Choose from: {list(methods.keys())}")
    
    return methods[method](ds, channel=channel, r0=r0, r1=r1, **kwargs)


def mask_seabed(
    ds: xr.Dataset,
    seabed_result: SeabedDetectionResult,
    offset: float = 0,
    mask_below: bool = True,
) -> xr.Dataset:
    """Apply seabed mask to dataset, masking data at/below the seabed.
    
    Args:
        ds: Dataset with Sv data.
        seabed_result: Result from detect_seabed().
        offset: Additional offset below seabed to mask (m).
            Positive = mask more (safer), negative = mask less.
        mask_below: If True, mask everything below seabed. If False,
            only mask the seabed echo itself (thin band).
            
    Returns:
        Dataset with Sv masked below the seabed.
        
    Example:
        >>> result = detect_seabed(sv_dataset)
        >>> if result.detection_rate > 0.5:
        ...     sv_masked = mask_seabed(sv_dataset, result, offset=5)
    """
    ds_out = ds.copy(deep=True)
    range_arr, range_dim = _get_range_array(ds)
    
    # Get seabed depth with offset
    seabed_depth = seabed_result.seabed_depth
    
    # Fill NaN with bottom of range (no masking where no detection)
    if "channel" in range_arr.dims:
        bottom_depth = range_arr.isel({range_dim: -1}).max(dim="channel")
    else:
        if range_arr.ndim > 1:
            bottom_depth = range_arr.isel({range_dim: -1}).max()
        else:
            bottom_depth = range_arr.isel({range_dim: -1})
    
    seabed_safe = seabed_depth.fillna(float(bottom_depth.values) + 100)
    seabed_safe = seabed_safe - offset  # Apply offset (subtract to mask more)
    
    # Build mask
    if mask_below:
        # Mask everything below seabed
        # Need to broadcast seabed_safe to match range_arr shape
        if "channel" in range_arr.dims:
            # 3D case: (ping_time, channel, range_sample) or similar
            mask = range_arr < seabed_safe
        else:
            # 2D case
            mask = range_arr < seabed_safe
    else:
        # Mask only the seabed band
        band_thickness = 10  # meters
        mask = ~((range_arr >= seabed_safe) & (range_arr <= seabed_safe + band_thickness))
    
    # Apply mask
    ds_out["Sv"] = ds_out["Sv"].where(mask)
    
    # Add seabed info as coordinates
    ds_out = ds_out.assign_coords(
        seabed_depth=seabed_result.seabed_depth,
    )
    
    return ds_out


def compute_seabed_stats(ds: xr.Dataset, result: SeabedDetectionResult) -> dict:
    """Compute statistics about detected seabed.
    
    Args:
        ds: Dataset with Sv data.
        result: SeabedDetectionResult from detection.
        
    Returns:
        Dictionary with seabed statistics.
    """
    depth = result.seabed_depth.values
    valid = ~np.isnan(depth)
    
    if not valid.any():
        return {
            "detected": False,
            "detection_rate": 0.0,
            "mean_depth": np.nan,
            "std_depth": np.nan,
            "min_depth": np.nan,
            "max_depth": np.nan,
        }
    
    return {
        "detected": True,
        "detection_rate": result.detection_rate,
        "mean_depth": float(np.nanmean(depth)),
        "std_depth": float(np.nanstd(depth)),
        "min_depth": float(np.nanmin(depth)),
        "max_depth": float(np.nanmax(depth)),
        "method": result.method,
        "channel": result.channel,
    }
