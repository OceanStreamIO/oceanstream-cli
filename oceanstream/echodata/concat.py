"""Concatenate EchoData files by day for batch processing.

Groups raw files by UTC date and concatenates them for daily processing,
enabling efficient denoising and MVBS/NASC computation over 24-hour periods.

Includes utilities for:
- Pulse-category splitting (short_pulse / long_pulse) based on frequency
  combinations in processed Zarr stores.
- Time-window batch grouping for multi-day concatenation windows.
"""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Optional

import pandas as pd

if TYPE_CHECKING:
    from echopype.echodata import EchoData
    import xarray as xr

logger = logging.getLogger(__name__)

# Filename patterns for extracting dates
# Saildrone: SD_TPOS2023_v03-Phase0-D20230601-T005958-0.raw
SAILDRONE_DATE_PATTERN = re.compile(r"-D(\d{8})-T(\d{6})")
# Generic ISO date: YYYYMMDD or YYYY-MM-DD
ISO_DATE_PATTERN = re.compile(r"(\d{4})[-_]?(\d{2})[-_]?(\d{2})")


def group_files_by_day(
    raw_files: list[Path],
    date_pattern: Optional[re.Pattern] = None,
) -> dict[str, list[Path]]:
    """
    Group raw files by UTC date from filename timestamp.
    
    Args:
        raw_files: List of raw file paths
        date_pattern: Optional regex pattern for extracting date.
                      Must have capture groups for YYYYMMDD or (YYYY, MM, DD).
                      Default: auto-detect Saildrone or ISO patterns.
    
    Returns:
        Dict mapping date strings (YYYYMMDD) to lists of file paths,
        sorted by timestamp within each day.
    
    Example:
        >>> files = [Path("SD_TPOS2023_v03-Phase0-D20230601-T005958-0.raw"),
        ...          Path("SD_TPOS2023_v03-Phase0-D20230601-T015958-0.raw"),
        ...          Path("SD_TPOS2023_v03-Phase0-D20230602-T005958-0.raw")]
        >>> groups = group_files_by_day(files)
        >>> list(groups.keys())
        ['20230601', '20230602']
    """
    groups: dict[str, list[Path]] = defaultdict(list)
    
    for f in raw_files:
        date_str = _extract_date(f.name, date_pattern)
        if date_str:
            groups[date_str].append(f)
        else:
            logger.warning(f"Could not extract date from filename: {f.name}")
    
    # Sort files within each day by name (which typically includes time)
    for date_str in groups:
        groups[date_str].sort()
    
    # Return sorted by date
    return dict(sorted(groups.items()))


def _extract_date(filename: str, pattern: Optional[re.Pattern] = None) -> Optional[str]:
    """Extract date string from filename."""
    if pattern:
        match = pattern.search(filename)
        if match:
            groups = match.groups()
            if len(groups) == 1:
                return groups[0]  # YYYYMMDD format
            elif len(groups) >= 3:
                return f"{groups[0]}{groups[1]}{groups[2]}"  # YYYY, MM, DD
        return None
    
    # Try Saildrone pattern first: -DYYYYMMDD-THHMMSS
    match = SAILDRONE_DATE_PATTERN.search(filename)
    if match:
        return match.group(1)  # YYYYMMDD
    
    # Try generic ISO date pattern
    match = ISO_DATE_PATTERN.search(filename)
    if match:
        return f"{match.group(1)}{match.group(2)}{match.group(3)}"
    
    return None


def extract_datetime(filename: str) -> Optional[datetime]:
    """
    Extract full datetime from filename.
    
    Args:
        filename: Raw file name
        
    Returns:
        datetime object or None if cannot parse
    """
    # Saildrone pattern: -DYYYYMMDD-THHMMSS
    match = SAILDRONE_DATE_PATTERN.search(filename)
    if match:
        date_str = match.group(1)  # YYYYMMDD
        time_str = match.group(2)  # HHMMSS
        return datetime.strptime(f"{date_str}{time_str}", "%Y%m%d%H%M%S")
    
    return None


def concatenate_daily(
    echodata_paths: list[Path],
    output_path: Path,
    use_dask: bool = True,
    chunks: Optional[dict] = None,
) -> Path:
    """
    Concatenate multiple EchoData Zarr stores into one daily dataset.
    
    Args:
        echodata_paths: List of paths to EchoData Zarr stores (from same day)
        output_path: Path for concatenated output Zarr
        use_dask: Enable Dask for lazy loading
        chunks: Chunk sizes for output
        
    Returns:
        Path to concatenated Zarr store
        
    Raises:
        ValueError: If echodata_paths is empty
    """
    if not echodata_paths:
        raise ValueError("No EchoData paths provided for concatenation")
    
    try:
        import echopype as ep
        from echopype.echodata import EchoData
    except ImportError as e:
        raise ImportError(
            "echopype is required. Install the fork: "
            "pip install git+https://github.com/OceanStreamIO/echopype.git@oceanstream-integration"
        ) from e
    
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    logger.info(f"Concatenating {len(echodata_paths)} EchoData files to {output_path}")
    
    # Sort paths by filename to ensure temporal order
    sorted_paths = sorted(echodata_paths)
    
    # Load all EchoData objects
    echodata_list = []
    for path in sorted_paths:
        ed = EchoData.from_file(path)
        echodata_list.append(ed)
    
    # Concatenate using echopype's combine_echodata
    combined = ep.combine_echodata(echodata_list)
    
    if use_dask and chunks:
        combined = combined.chunk(chunks)
    
    # Save concatenated result
    combined.to_zarr(output_path, overwrite=True)
    
    logger.info(f"Concatenated to {output_path}")
    return output_path


def concatenate_sv_datasets(
    sv_paths: list[Path],
    output_path: Path,
    dim: str = "ping_time",
    chunks: Optional[dict] = None,
) -> Path:
    """
    Concatenate multiple Sv xarray datasets.
    
    Used for combining Sv datasets from multiple files after compute_sv.
    
    Args:
        sv_paths: List of paths to Sv Zarr stores
        output_path: Output path for concatenated dataset
        dim: Dimension to concatenate along (default: ping_time)
        chunks: Output chunk sizes
        
    Returns:
        Path to concatenated Sv Zarr store
    """
    import xarray as xr
    import zarr
    
    if not sv_paths:
        raise ValueError("No Sv paths provided for concatenation")
    
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    logger.info(f"Concatenating {len(sv_paths)} Sv datasets")
    
    # Sort by path name for temporal order
    sorted_paths = sorted(sv_paths)
    
    datasets = []
    for path in sorted_paths:
        ds = xr.open_zarr(path, chunks=chunks)
        
        # Clean up problematic variables
        for var in ["source_filenames"]:
            if var in ds:
                ds = ds.drop_vars(var)
        
        # Demote coordinates to data vars for merging
        for v in ("latitude", "longitude", "speed_knots"):
            if v in ds.coords:
                ds = ds.reset_coords(v)
        
        datasets.append(ds)
    
    # Sort by ping_time
    datasets.sort(key=lambda ds: ds[dim].min().values)
    
    # Concatenate
    concatenated = xr.concat(
        datasets,
        dim=dim,
        data_vars="all",
        coords="minimal",
        compat="override",
        join="outer",
    )
    
    # Handle frequency_nominal dimension reduction
    if "frequency_nominal" in concatenated:
        freq_1d = concatenated["frequency_nominal"].mean("ping_time")
        concatenated = concatenated.drop_vars("frequency_nominal")
        concatenated["frequency_nominal"] = freq_1d
        concatenated = concatenated.assign_coords(
            frequency=("channel", freq_1d.values)
        )
    
    if chunks:
        concatenated = concatenated.chunk(chunks)
    
    # Save
    concatenated.to_zarr(output_path, mode="w")
    zarr.consolidate_metadata(output_path)
    
    logger.info(f"Concatenated Sv to {output_path}")
    return output_path


# Convenience aliases for test compatibility
def parse_date_from_filename(filename: str) -> datetime:
    """
    Parse date from filename (returns date portion only).
    
    Args:
        filename: Raw file name
        
    Returns:
        datetime object representing date (time set to 00:00:00)
        
    Raises:
        ValueError: If date cannot be parsed
    """
    date_str = _extract_date(filename)
    if date_str is None:
        raise ValueError(f"Could not parse date from filename: {filename}")
    return datetime.strptime(date_str, "%Y%m%d")


def parse_datetime_from_filename(filename: str) -> datetime:
    """
    Parse full datetime from filename.
    
    Args:
        filename: Raw file name
        
    Returns:
        datetime object with full timestamp
        
    Raises:
        ValueError: If datetime cannot be parsed
    """
    dt = extract_datetime(filename)
    if dt is None:
        raise ValueError(f"Could not parse datetime from filename: {filename}")
    return dt


def merge_location_data(ds: "xr.Dataset", location_data: list[dict]) -> "xr.Dataset":
    """
    Merge location data (GPS nav) into an Sv dataset.
    
    Interpolates latitude, longitude, and speed_knots from a list of 
    GPS records to align with ping_time in the Sv dataset.
    
    Ported from legacy _echodata-legacy-code/saildrone-echodata-processing/process/concat.py
    
    Args:
        ds: xarray Dataset with ping_time coordinate (e.g., Sv dataset)
        location_data: List of dicts with keys:
            - 'lat' or 'latitude': latitude in degrees
            - 'lon' or 'longitude': longitude in degrees
            - 'dt' or 'time': timestamp (ISO string or datetime)
            - 'knt' or 'speed_knots': speed in knots (optional)
    
    Returns:
        xarray Dataset with latitude, longitude, speed_knots as data variables
        
    Example:
        >>> location_data = [
        ...     {"lat": 35.0, "lon": -120.0, "dt": "2023-06-01T00:00:00Z", "knt": 5.0},
        ...     {"lat": 35.1, "lon": -120.1, "dt": "2023-06-01T01:00:00Z", "knt": 5.2},
        ... ]
        >>> sv_with_nav = merge_location_data(sv_dataset, location_data)
    """
    import xarray as xr
    
    # Normalize column names (support both legacy and modern formats)
    normalized = []
    for record in location_data:
        norm = {
            "lat": record.get("lat") or record.get("latitude"),
            "lon": record.get("lon") or record.get("longitude"),
            "dt": record.get("dt") or record.get("time"),
            "knt": record.get("knt") or record.get("speed_knots") or record.get("speed"),
        }
        normalized.append(norm)
    
    df = pd.DataFrame(normalized)
    df["dt"] = (
        pd.to_datetime(df["dt"], utc=True, errors="coerce")
        .dt.tz_localize(None)  # Make tz-naive to match ping_time
    )
    df = df.set_index("dt").sort_index()
    
    # Handle missing speed data
    if "knt" not in df.columns or df["knt"].isna().all():
        df["knt"] = 0.0
    
    # Convert to xarray
    nav = df[["lat", "lon", "knt"]].to_xarray()
    nav = nav.rename({
        "dt": "ping_time",
        "lat": "latitude",
        "lon": "longitude",
        "knt": "speed_knots",
    })
    
    # Interpolate to ping_time if available
    if "ping_time" in ds.coords:
        nav = nav.interp(
            ping_time=ds["ping_time"],
            method="nearest",
            kwargs={"fill_value": "extrapolate"},
        )
    
    # Remove existing location variables to avoid conflicts
    for v in ["latitude", "longitude", "speed_knots"]:
        if v in ds.data_vars:
            ds = ds.drop_vars(v)
        if v in ds.coords:
            ds = ds.reset_coords(v, drop=True)
    
    # Remove time variable if it conflicts
    if "time" in ds:
        ds = ds.drop_vars("time")
    if "time" in ds.coords:
        ds = ds.reset_coords("time", drop=True)
    
    # Merge nav data
    merged = xr.merge([ds, nav], compat="override")
    
    # Keep location vars as data_vars, not coords
    merged = merged.reset_coords(
        ["latitude", "longitude", "speed_knots"],
        drop=False,
    )
    
    # Final cleanup
    if "time" in merged:
        merged = merged.drop_vars("time")
    if "time" in merged.coords:
        merged = merged.reset_coords("time", drop=True)
    
    return merged


# ============================================================================
# Pulse-category splitting utilities
# ============================================================================

# Well-known pulse categories for Saildrone EK80.
# Key = friendly name, value = comma-joined sorted frequency_nominal strings.
PULSE_CATEGORY_CONFIG: dict[str, dict[str, Optional[str]]] = {
    "short_pulse": {"freq_key": "38000.0,200000.0"},
    "long_pulse": {"freq_key": "38000.0"},
    "exported_ds": {"freq_key": None},  # catch-all
}


def detect_pulse_category(ds: "xr.Dataset") -> str:
    """Classify a Sv dataset into a pulse category.

    Classification is based on the sorted frequency_nominal values present
    in the ``channel`` dimension, matching the Saildrone EK80 convention:

    - ``"short_pulse"`` → 38 kHz + 200 kHz (dual-frequency, short CW pulse)
    - ``"long_pulse"``  → 38 kHz only (single-frequency, long CW pulse)
    - ``"exported_ds"``  → anything else

    Args:
        ds: Sv xarray.Dataset with a ``frequency_nominal`` coordinate or
            variable.

    Returns:
        One of ``"short_pulse"``, ``"long_pulse"``, or ``"exported_ds"``.
    """
    import numpy as np

    if "frequency_nominal" in ds:
        freqs = np.sort(
            np.unique(ds["frequency_nominal"].values.astype(float))
        )
    elif "channel" in ds.dims:
        freqs = np.sort(
            np.unique(ds["channel"].values.astype(float))
        )
    else:
        return "exported_ds"

    freq_str = ",".join(f"{f:.1f}" for f in freqs)

    for category, cfg in PULSE_CATEGORY_CONFIG.items():
        if cfg["freq_key"] is None or freq_str == cfg["freq_key"]:
            return category

    return "exported_ds"


def group_by_pulse_category(
    paths: list[Path],
) -> dict[str, list[Path]]:
    """Group Zarr store paths by pulse category.

    Opens each Zarr lazily to read ``frequency_nominal`` and assigns the
    file to a pulse category.

    Args:
        paths: List of Sv Zarr store paths.

    Returns:
        ``{category: [path, ...]}`` mapping.
    """
    import xarray as xr

    groups: dict[str, list[Path]] = defaultdict(list)
    for p in paths:
        try:
            ds = xr.open_zarr(p)
            cat = detect_pulse_category(ds)
        except Exception:
            logger.warning(f"Could not classify {p}, assigning to exported_ds")
            cat = "exported_ds"
        groups[cat].append(p)
    return dict(groups)


# ============================================================================
# Time-window batch grouping
# ============================================================================

def batch_key(
    ts: datetime,
    window_days: int = 1,
) -> str:
    """Return a filename-safe key that anchors *ts* to a fixed time window.

    Args:
        ts: Timestamp (usually a file start time).
        window_days: Width of the batching window in days.

    Returns:
        ``"YYYY-MM-DD"`` for single-day windows, or
        ``"YYYY-MM-DD_to_YYYY-MM-DD"`` for multi-day windows.

    Examples:
        >>> batch_key(datetime(2023, 8, 10), 1)
        '2023-08-10'
        >>> batch_key(datetime(2023, 8, 10), 3)
        '2023-08-09_to_2023-08-11'
    """
    anchor = datetime(ts.year, ts.month, ts.day)

    if window_days <= 1:
        return f"{anchor:%Y-%m-%d}"

    # Floor to start of rolling window
    anchor -= timedelta(days=(anchor - datetime.min).days % window_days)
    end = anchor + timedelta(days=window_days - 1)
    return f"{anchor:%Y-%m-%d}_to_{end:%Y-%m-%d}"


def group_by_time_window(
    files: list[tuple[Path, datetime]],
    window_days: int = 1,
) -> dict[str, list[Path]]:
    """Group files into time-window batches.

    Args:
        files: List of ``(path, start_time)`` tuples.
        window_days: Width of each batch window in days.

    Returns:
        ``{batch_key_str: [path, ...]}`` mapping, sorted by key.
    """
    groups: dict[str, list[Path]] = defaultdict(list)
    for path, ts in files:
        key = batch_key(ts, window_days)
        groups[key].append(path)

    return dict(sorted(groups.items()))
