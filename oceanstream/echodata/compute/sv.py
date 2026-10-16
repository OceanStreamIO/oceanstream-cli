"""Compute Sv (Volume Backscattering Strength) from calibrated EchoData.

Provides compute_sv function that wraps echopype's calibrate.compute_Sv
with support for environmental enrichment from the EchoData Environment group.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    import numpy as np
    import xarray as xr
    from echopype.echodata import EchoData

logger = logging.getLogger(__name__)

_MISSING_TRANSMIT_TYPES = {"", "nan", "none"}


def fill_transmit_type(echodata: "EchoData") -> list[str]:
    """Give every ping of a channel that channel's own transmit type.

    An EK80 that pings its channels in turn leaves each channel empty at the other
    channels' pings, and echopype records the transmit type there as "nan". echopype's
    ``CalibrateEK80._get_chan_dict`` then reads the transmit type of the first ping only,
    so every channel that did not transmit in that ping is dropped from Sv. Filling the
    gaps with each channel's own value restores what that code assumes: one transmit
    type per channel.

    Returns the beam groups that were changed.

    Raises:
        ValueError: if a channel's transmit type changes between pings.
    """
    import numpy as np

    changed = []
    for group in [g for g in echodata.group_paths if g.startswith("Sonar/Beam_group")]:
        beam = echodata[group]
        if beam is None or "transmit_type" not in beam:
            continue
        original = beam["transmit_type"]
        if "channel" not in original.dims or "ping_time" not in original.dims:
            continue
        tt = original.transpose("channel", ...).compute()
        values = np.asarray(tt.values, dtype=object).reshape(tt.sizes["channel"], -1)
        missing = np.array(
            [[v is None or str(v).strip().lower() in _MISSING_TRANSMIT_TYPES for v in row]
             for row in values],
            dtype=bool,
        )
        if not missing.any():
            continue
        filled = values.copy()
        for i, channel in enumerate(tt["channel"].values):
            kinds = {str(v) for v in values[i][~missing[i]]}
            if len(kinds) > 1:
                raise ValueError(
                    f"{group}: channel {channel} changes transmit type between pings "
                    f"({sorted(kinds)}). Split the data by transmit type before computing Sv."
                )
            if kinds:
                filled[i][missing[i]] = kinds.pop()
        beam["transmit_type"] = tt.copy(
            data=filled.reshape(tt.shape).astype(str)
        ).transpose(*original.dims)
        echodata[group] = beam
        changed.append(group)
        logger.info("Filled transmit_type gaps in %s (channels ping in turn)", group)
    return changed


def channel_pings(
    echodata: "EchoData", group: str = "Sonar/Beam_group1"
) -> dict[str, "np.ndarray"] | None:
    """The pings each channel transmitted in, or None when every channel pinged every time.

    Read from ``transmit_type`` before :func:`fill_transmit_type` runs: a channel's
    missing pings are the ones another channel transmitted in.
    """
    import numpy as np

    beam = echodata[group]
    if beam is None or "transmit_type" not in beam:
        return None
    tt = beam["transmit_type"].transpose("channel", "ping_time", ...).compute()
    values = np.asarray(tt.values, dtype=object).reshape(tt.sizes["channel"], tt.sizes["ping_time"], -1)
    pinged = np.array(
        [[any(v is not None and str(v).strip().lower() not in _MISSING_TRANSMIT_TYPES
              for v in cell) for cell in row] for row in values],
        dtype=bool,
    )
    if pinged.all():
        return None
    return {str(ch): pinged[i] for i, ch in enumerate(tt["channel"].values)}


def select_channel(
    echodata: "EchoData", channel: str, pings: "np.ndarray", group: str = "Sonar/Beam_group1"
) -> int:
    """Restrict a beam group to one channel, its own pings and its own recorded range.

    echopype stores channels that ping in turn on one grid, padded with NaN to the longest
    range and to every channel's pings; for RR2205 that is 12 times the real data. Sv
    computed on the selection is identical where the channel has data.

    Returns the number of range samples kept.
    """
    import numpy as np

    beam = echodata[group].sel(channel=[channel]).isel(ping_time=np.flatnonzero(pings))
    first = beam["backscatter_r"].isel(ping_time=0)
    valid = first.notnull().any([d for d in first.dims if d != "range_sample"]).values
    n_samples = int(np.flatnonzero(valid).max()) + 1 if valid.any() else beam.sizes["range_sample"]
    echodata[group] = beam.isel(range_sample=slice(0, n_samples))
    return n_samples


def compute_sv(
    echodata_path: Path,
    output_path: Optional[Path] = None,
    env_params: Optional[dict] = None,
    use_dask: bool = True,
    add_depth: bool = True,
    add_location: bool = True,
    depth_offset: float = 0.0,
    waveform_mode: str = "CW",
    encode_mode: str = "complex",
) -> "xr.Dataset":
    """
    Compute Sv (Volume backscattering strength) from calibrated EchoData.
    
    Wraps echopype's compute_Sv with support for environmental parameters
    from the EchoData Environment group (populated by enrich_environment).
    
    Args:
        echodata_path: Path to EchoData Zarr
        output_path: Optional path to save result (if None, returns in-memory)
        env_params: Optional dict with sound_speed, absorption overrides
        use_dask: Enable Dask for large files
        add_depth: Add depth coordinate to output
        add_location: Add lat/lon coordinates to output
        depth_offset: Transducer depth offset in metres (e.g. depth below
            waterline).  Passed to echopype ``add_depth`` when platform
            metadata doesn't already provide it.
        waveform_mode: Waveform mode for EK80 ('CW' for narrowband, 'BB' for broadband)
        encode_mode: Encode mode for EK80 ('complex' or 'power')
        
    Returns:
        xarray.Dataset with Sv values
        
    Example (Prefect):
        @task
        def compute_sv_task(echodata_path: Path) -> xr.Dataset:
            return compute_sv(echodata_path, use_dask=True)
    """
    try:
        import echopype as ep
    except ImportError as e:
        raise ImportError(
            "echopype required for Sv computation. Install with: pip install echopype"
        ) from e
    
    echodata_path = Path(echodata_path)
    
    # Load EchoData using echopype 0.8.x/0.9.x API
    logger.info(f"Loading EchoData from {echodata_path}")
    echodata = ep.open_converted(echodata_path)
    
    # Prepare env_params for compute_Sv
    compute_kwargs = {}
    
    # Check if Environment group has measured values (using 0.8.x/0.9.x attribute style)
    env = echodata.environment
    if env is not None and "sound_speed" in env:
        logger.info("Using measured sound_speed from Environment group")
        # For stock echopype, we pass as env_params
        if env_params is None:
            env_params = {}
        if "sound_speed" not in env_params:
            # Use mean sound speed if echopype doesn't support time-varying
            env_params["sound_speed"] = float(env["sound_speed"].mean().values)
    
    if env_params:
        compute_kwargs["env_params"] = env_params
    
    # EK80 requires waveform_mode and encode_mode
    if echodata.sonar_model == "EK80":
        compute_kwargs["waveform_mode"] = waveform_mode
        compute_kwargs["encode_mode"] = encode_mode
        fill_transmit_type(echodata)

    # Compute Sv
    logger.info("Computing Sv")
    ds_Sv = ep.calibrate.compute_Sv(echodata, **compute_kwargs)
    
    # Add depth coordinate using platform-aware flag selection
    if add_depth:
        logger.info("Adding depth coordinate")
        try:
            flags = _choose_depth_flags(echodata, depth_offset=depth_offset)
            ds_Sv = ep.consolidate.add_depth(ds_Sv, echodata, **flags)
        except (KeyError, ValueError, TypeError) as e:
            logger.warning(f"Depth-flag add_depth failed ({e}), using simple add_depth")
            ds_Sv = ep.consolidate.add_depth(ds_Sv)
    
    # Add location coordinates
    if add_location:
        logger.info("Adding location coordinates")
        try:
            ds_Sv = ep.consolidate.add_location(ds_Sv, echodata)
        except ValueError as e:
            if "all NaN" in str(e).lower() or "nan" in str(e).lower():
                logger.warning(f"Could not add location data: {e}")
            else:
                raise
    
    # Save if output path provided
    if output_path:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        logger.info(f"Saving Sv to {output_path}")
        ds_Sv.to_zarr(output_path, mode="w")
    
    return ds_Sv


def compute_sv_from_echodata(
    echodata: "EchoData",
    env_params: Optional[dict] = None,
    cal_params: Optional[dict] = None,
    add_depth: bool = True,
    add_location: bool = True,
    waveform_mode: str = "CW",
    encode_mode: str = "complex",
) -> "xr.Dataset":
    """
    Compute Sv from an in-memory EchoData object.
    
    Args:
        echodata: EchoData object (already loaded)
        env_params: Optional environment parameters override
        cal_params: Optional calibration parameters override (from ECS parsing)
        add_depth: Add depth coordinate
        add_location: Add lat/lon coordinates
        waveform_mode: Waveform mode for EK80 ('CW' for narrowband, 'BB' for broadband)
        encode_mode: Encode mode for EK80 ('complex' or 'power')
        
    Returns:
        xarray.Dataset with Sv values
    """
    try:
        import echopype as ep
    except ImportError as e:
        raise ImportError("echopype required") from e
    
    compute_kwargs = {}
    if env_params:
        compute_kwargs["env_params"] = env_params
    if cal_params:
        compute_kwargs["cal_params"] = cal_params
    
    # EK80 requires waveform_mode and encode_mode
    if echodata.sonar_model == "EK80":
        compute_kwargs["waveform_mode"] = waveform_mode
        compute_kwargs["encode_mode"] = encode_mode
        fill_transmit_type(echodata)

    ds_Sv = ep.calibrate.compute_Sv(echodata, **compute_kwargs)
    
    if add_depth:
        ds_Sv = ep.consolidate.add_depth(ds_Sv)
    
    if add_location:
        try:
            ds_Sv = ep.consolidate.add_location(ds_Sv, echodata)
        except ValueError as e:
            if "all NaN" in str(e).lower() or "nan" in str(e).lower():
                logger.warning(f"Could not add location data: {e}")
            else:
                raise
    
    return ds_Sv


def enrich_sv_dataset(
    ds_Sv: "xr.Dataset",
    echodata: "EchoData",
    add_depth: bool = True,
    add_location: bool = True,
    add_splitbeam_angle: bool = False,
    depth_offset: float = 0,
    waveform_mode: str = "CW",
    encode_mode: str = "complex",
) -> "xr.Dataset":
    """
    Enrich Sv dataset with depth, location, and optional splitbeam angles.
    
    Adds additional coordinates and variables to the Sv dataset for
    downstream analysis and visualization.
    
    Args:
        ds_Sv: Volume backscattering strength Dataset from compute_Sv
        echodata: Source EchoData object
        add_depth: Add depth coordinate derived from echo_range
        add_location: Add latitude/longitude coordinates from GPS
        add_splitbeam_angle: Add split-beam angle data (if available)
        depth_offset: Offset to add to depth values (e.g., transducer depth)
        waveform_mode: Waveform mode for splitbeam ('CW' or 'BB')
        encode_mode: Encode mode for splitbeam ('complex' or 'power')
        
    Returns:
        Enriched xarray Dataset with additional coordinates
        
    Example:
        ds_Sv = ep.calibrate.compute_Sv(echodata)
        ds_enriched = enrich_sv_dataset(ds_Sv, echodata, depth_offset=1.9)
    """
    try:
        import echopype as ep
        from echopype.consolidate import add_depth as ep_add_depth
        from echopype.consolidate import add_location as ep_add_location
        from echopype.consolidate import add_splitbeam_angle as ep_add_splitbeam
    except ImportError as e:
        raise ImportError("echopype required for Sv enrichment") from e
    
    import xarray as xr
    
    # Add depth coordinate
    if add_depth:
        try:
            flags = _choose_depth_flags(echodata, depth_offset=depth_offset)
            ds_Sv = ep_add_depth(ds_Sv, echodata, **flags)
            logger.info("Added depth coordinate")
        except (KeyError, ValueError) as e:
            logger.warning(f"Failed to add depth: {e}")
    
    # Add location coordinates
    if add_location:
        try:
            ds_Sv = ep_add_location(ds_Sv, echodata)
            logger.info("Added location coordinates")
        except (KeyError, ValueError) as e:
            logger.warning(f"Failed to add location: {e}")
    
    # Add splitbeam angle (optional, for single-target detection)
    if add_splitbeam_angle:
        try:
            ds_Sv = ep_add_splitbeam(
                ds_Sv,
                echodata,
                to_disk=False,
                pulse_compression=False,
                waveform_mode=waveform_mode,
                encode_mode=encode_mode,
            )
            logger.info("Added splitbeam angle")
        except (KeyError, ValueError) as e:
            logger.warning(f"Failed to add splitbeam angle: {e}")
    
    # Drop all-NaN pings
    ds_Sv = ds_Sv.dropna(dim="ping_time", how="all", subset=["Sv"])
    
    # Make echo_range a coordinate if it's a variable
    if "echo_range" in ds_Sv and "echo_range" not in ds_Sv.coords:
        # Extract a single 1-D vector (all slices are identical)
        if ds_Sv["echo_range"].ndim >= 2:
            er_1d = (
                ds_Sv["echo_range"]
                .isel(channel=0, ping_time=0)
                .data
            )
            ds_Sv = ds_Sv.assign_coords(echo_range=("range_sample", er_1d))
    
    return ds_Sv


def _choose_depth_flags(
    echodata: "EchoData",
    depth_offset: float = 0,
    downward: bool = True,
) -> dict:
    """
    Choose flags for echopype add_depth based on available platform data.
    
    Determines whether to use platform vertical offsets (transducer_offset_z,
    water_level, vertical_offset) or a user-supplied depth_offset, and
    whether to use platform angles or beam angles for tilt correction.
    
    Args:
        echodata: EchoData object with Platform group
        depth_offset: User-supplied depth offset (used if platform offsets unavailable)
        downward: Whether transducer is downward-looking
        
    Returns:
        Dictionary of flags for echopype add_depth function
    """
    platform = echodata["Platform"]
    
    # Check for vertical offset variables
    vert_vars = ["transducer_offset_z", "water_level", "vertical_offset"]
    have_all_vert = all(name in platform for name in vert_vars)
    
    use_platform_vertical_offsets = False
    if have_all_vert:
        import xarray as xr
        vert_values = platform[vert_vars].to_array()
        use_platform_vertical_offsets = not vert_values.isnull().any()
    
    # If we have platform offsets, ignore user-supplied depth_offset
    depth_offset_kwarg = None if use_platform_vertical_offsets else depth_offset
    
    # Check for tilt correction angles
    plat_angle_vars = ["platform_pitch", "platform_roll"]
    plat_angles_present = all(v in platform for v in plat_angle_vars) and not (
        platform[plat_angle_vars].to_array().isnull().any()
    )
    
    # Beam angles exist if beam_direction_x is in the Sonar groups
    beam_angles_present = any(
        "beam_direction_x" in str(var) for var in echodata["Sonar"].data_vars
    )
    
    use_platform_angles = plat_angles_present
    use_beam_angles = (not plat_angles_present) and beam_angles_present
    
    return {
        "depth_offset": depth_offset_kwarg,
        "use_platform_vertical_offsets": use_platform_vertical_offsets,
        "use_platform_angles": use_platform_angles,
        "use_beam_angles": use_beam_angles,
        "downward": bool(downward),
    }


def swap_range_to_depth(
    ds: "xr.Dataset",
    channel_idx: int = 0,
    ping_idx: int = 0,
) -> "xr.Dataset":
    """
    Swap range_sample dimension to depth coordinate.
    
    Extracts depth from the first channel/ping and uses it as
    a 1-D coordinate, then swaps dimensions from range_sample to depth.
    
    Args:
        ds: Dataset with range_sample dimension and depth variable
        channel_idx: Channel index to extract depth from
        ping_idx: Ping index to extract depth from
        
    Returns:
        Dataset with depth as the vertical dimension
    """
    if "range_sample" not in ds.dims:
        return ds
    
    if "depth" not in ds:
        logger.warning("No depth variable found, cannot swap to depth dimension")
        return ds
    
    # Extract 1-D depth vector
    depth_1d = ds["depth"].isel(channel=channel_idx, ping_time=ping_idx).data
    
    # Assign as coordinate and swap dimensions
    ds = ds.assign_coords(depth=("range_sample", depth_1d))
    ds = ds.swap_dims({"range_sample": "depth"})
    
    return ds


def correct_echo_range(
    ds: "xr.Dataset",
    depth_offset: float = 0.0,
    range_coord: str = "echo_range",
) -> "xr.Dataset":
    """
    Correct echo range values and convert to depth dimension.
    
    Applies a depth offset (e.g., transducer depth below waterline),
    filters invalid values, and renames range_sample to depth.
    
    This is essential for MVBS/NASC computation which require
    the vertical dimension to be in meters (depth).
    
    Args:
        ds: Dataset with range_sample dimension and echo_range variable
        depth_offset: Offset to add to echo_range (e.g., transducer depth in meters)
        range_coord: Name of the range coordinate variable
        
    Returns:
        Dataset with depth as the vertical dimension
        
    Notes:
        The function:
        1. Preserves original range_sample values
        2. Applies depth offset to echo_range values  
        3. Filters out invalid depth values
        4. Renames range_sample to depth
        
    Example:
        # Convert Sv dataset from range_sample to depth
        ds_with_depth = correct_echo_range(ds_Sv, depth_offset=5.0)
    """
    import numpy as np
    
    if "range_sample" not in ds.dims:
        return ds
    
    if range_coord not in ds:
        logger.warning(f"{range_coord} not found in dataset, cannot correct to depth")
        return ds
    
    # Store original range_sample values
    ds = ds.assign(original_range_sample=("range_sample", ds["range_sample"].values))
    
    # Get first channel and ping_time - assuming these are constant for the range
    if "channel" in ds[range_coord].dims:
        first_channel = ds["channel"].values[0]
        selected_echo_range = ds[range_coord].sel(channel=first_channel)
    else:
        selected_echo_range = ds[range_coord]
    
    if "ping_time" in selected_echo_range.dims:
        first_ping_time = ds["ping_time"].values[0]
        selected_echo_range = selected_echo_range.sel(ping_time=first_ping_time)
    
    # Extract and correct echo range values
    corrected_depth = selected_echo_range.values + depth_offset
    
    # Find valid range using numpy operations
    min_val = np.nanmin(corrected_depth)
    max_val = np.nanmax(corrected_depth)
    
    # Update coordinates and rename
    ds = ds.assign_coords(range_sample=corrected_depth)
    ds = ds.rename({"range_sample": "depth"})
    
    # Filter to valid depth range
    ds = ds.sel(depth=slice(min_val, max_val))
    
    # Remove any remaining NaN depths
    valid_depth_indices = ~np.isnan(ds["depth"].values)
    ds = ds.isel(depth=valid_depth_indices)
    
    # Restore original range_sample
    ds = ds.rename({"original_range_sample": "range_sample"})
    
    return ds


def apply_corrections_ds(
    ds: "xr.Dataset",
    depth_offset: Optional[float] = None,
    drop_empty_pings: bool = True,
) -> "xr.Dataset":
    """
    Apply standard corrections to Sv dataset.
    
    Removes empty pings and optionally corrects echo range to depth.
    This prepares the dataset for downstream analysis (MVBS, NASC, etc.).
    
    Args:
        ds: Sv dataset to correct
        depth_offset: Optional depth offset for echo_range correction
        drop_empty_pings: Remove pings where all Sv values are NaN
        
    Returns:
        Corrected dataset
        
    Example:
        ds_clean = apply_corrections_ds(ds_Sv, depth_offset=5.0)
    """
    # Remove empty pings
    if drop_empty_pings and "Sv" in ds:
        ds = ds.dropna(dim="ping_time", how="all", subset=["Sv"])
    
    # Correct echo range if offset provided
    if depth_offset is not None and "echo_range" in ds:
        try:
            ds = correct_echo_range(ds, depth_offset=depth_offset)
        except Exception as e:
            logger.warning(f"Error correcting echo range: {e}")
    
    return ds
