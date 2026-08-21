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
    
    # Also check if depth is already a dimension coordinate (e.g. from swap_dims)
    if "depth" in sv_dataset.dims:
        logger.debug("Dataset already has depth as a dimension — skipping recomputation")
        return sv_dataset
    
    from oceanstream.echodata.consolidate import add_depth_to_sv
    
    return add_depth_to_sv(
        sv_dataset,
        echodata=echodata,
        depth_offset=transducer_depth,
    )


def _ensure_location(sv_dataset: "xr.Dataset") -> "xr.Dataset":
    """
    Ensure latitude/longitude are 1D data variables along ping_time.
    
    echopype.commongrid.compute_NASC requires lat/lon as data_vars.
    Multi-dimensional lat/lon (e.g., channel × ping_time from EK80)
    are collapsed to 1D by selecting the first index along extra dims,
    since GPS position is physically identical across sonar channels.
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
    
    # Collapse multi-dimensional lat/lon to 1D along ping_time.
    # GPS position is identical across sonar channels; select index 0 on extras.
    # A plain ``isel(...=0)`` is not safe here: upstream ``Dataset.where()``
    # calls broadcast lat/lon across range_sample and can NaN out index 0, so
    # reduce over the extra dims with nanmean and only then take a scalar slice.
    for var in ("latitude", "longitude"):
        da = sv_dataset[var]
        extra_dims = [d for d in da.dims if d != "ping_time"]
        if extra_dims:
            sv_dataset[var] = da.mean(dim=extra_dims, skipna=True)
            logger.debug("Collapsed %s from %dD to 1D", var, da.ndim)
    
    return sv_dataset


# ---------------------------------------------------------------------------
# Per-distance-bin position reconstruction and schema normalisation
# ---------------------------------------------------------------------------

#: Variables that must exist on a NASC product with dims ``(distance,)``.
NASC_POSITION_VARS = ("latitude", "longitude")

#: Dimensions a well-formed NASC product is allowed to carry.
NASC_ALLOWED_DIMS = frozenset({"channel", "distance", "depth", "echo_range"})


def _cumulative_distance_nmi(lat: "np.ndarray", lon: "np.ndarray") -> "np.ndarray":
    """Cumulative great-circle distance along a track, in nautical miles.

    Mirrors ``echopype.commongrid.utils.get_distance_from_latlon`` so the bin
    assignment reconstructed here matches the bins echopype actually used.
    """
    lat = np.asarray(lat, dtype=float)
    lon = np.asarray(lon, dtype=float)
    # Same small-angle approximation echopype uses (nmi per degree = 60).
    dlat = np.diff(lat, prepend=lat[:1])
    dlon = np.diff(lon, prepend=lon[:1])
    dist = np.sqrt(dlat**2 + (dlon * np.cos(np.deg2rad(lat))) ** 2) * 60.0
    return np.nancumsum(dist)


def _assign_distance_bins(
    dist_nmi: "np.ndarray", bin_left_edges: "np.ndarray"
) -> "np.ndarray":
    """Map each ping onto the NASC distance bin that contains it.

    Returns an int array of bin indices; ``-1`` marks pings that fall outside
    the bin range (or have a non-finite cumulative distance).
    """
    dist_nmi = np.asarray(dist_nmi, dtype=float)
    edges = np.asarray(bin_left_edges, dtype=float)
    if edges.size == 0:
        return np.full(dist_nmi.shape, -1, dtype=int)
    width = float(edges[1] - edges[0]) if edges.size > 1 else np.inf
    full_edges = np.append(edges, edges[-1] + width)
    idx = np.searchsorted(full_edges, dist_nmi, side="right") - 1
    idx = np.where(np.isfinite(dist_nmi), idx, -1)
    idx = np.where((idx >= 0) & (idx < edges.size), idx, -1)
    return idx.astype(int)


def repair_nasc_positions(
    ds_NASC: "xr.Dataset",
    sv_dataset: "xr.Dataset",
) -> "xr.Dataset":
    """Recompute per-distance-bin latitude/longitude from the source GPS track.

    ``echopype``'s ``_get_reduced_positions`` collapses every distance bin to a
    single value under some flox/xarray version combinations, which silently
    destroys the geographic axis of the NASC product (all bins land on one
    point). This recomputes the positions deterministically from the source
    per-ping GPS by re-deriving the same cumulative-distance bin assignment
    echopype used, then averaging the source positions within each bin.

    Bins with no finite source position fall back to the nearest-in-time source
    ping, and are left as NaN only when the source itself has no position.

    Present-but-malformed ``latitude``/``longitude`` variables are repaired, not
    just absent ones.
    """
    import xarray as xr

    if "distance" not in ds_NASC.dims:
        return ds_NASC

    has_pos = all(
        v in sv_dataset.data_vars or v in sv_dataset.coords for v in NASC_POSITION_VARS
    )
    if not has_pos:
        logger.warning("Source Sv has no lat/lon — cannot repair NASC positions")
        return ds_NASC

    def _flat(name: str) -> "np.ndarray":
        da = sv_dataset[name]
        extra = [d for d in da.dims if d != "ping_time"]
        if extra:
            da = da.mean(dim=extra, skipna=True)
        return np.asarray(da.values, dtype=float)

    lat = _flat("latitude")
    lon = _flat("longitude")
    if lat.ndim != 1 or lat.shape != lon.shape:
        logger.warning(
            "Unexpected source position shape lat=%s lon=%s — skipping repair",
            lat.shape, lon.shape,
        )
        return ds_NASC

    dist_nmi = _cumulative_distance_nmi(lat, lon)
    edges = np.asarray(ds_NASC["distance"].values, dtype=float)
    assignment = _assign_distance_bins(dist_nmi, edges)

    n_bins = edges.size
    lat_binned = np.full(n_bins, np.nan)
    lon_binned = np.full(n_bins, np.nan)
    counts = np.zeros(n_bins, dtype=int)
    for i in range(n_bins):
        sel = assignment == i
        counts[i] = int(sel.sum())
        if counts[i] == 0:
            continue
        with np.errstate(invalid="ignore"):
            lat_binned[i] = np.nanmean(lat[sel])
            lon_binned[i] = np.nanmean(lon[sel])

    # Fallback for empty/all-NaN bins: nearest source ping in time.
    missing = ~np.isfinite(lat_binned) | ~np.isfinite(lon_binned)
    if missing.any() and "ping_time" in ds_NASC and "ping_time" in sv_dataset:
        src_times = np.asarray(sv_dataset["ping_time"].values)
        bin_times = np.asarray(ds_NASC["ping_time"].values)
        if src_times.size and bin_times.size == n_bins:
            for i in np.flatnonzero(missing):
                t = bin_times[i]
                if np.isnat(t):
                    continue
                j = int(np.argmin(np.abs(src_times - t)))
                if np.isfinite(lat[j]) and np.isfinite(lon[j]):
                    lat_binned[i] = lat[j]
                    lon_binned[i] = lon[j]

    lat_attrs = dict(sv_dataset["latitude"].attrs)
    lon_attrs = dict(sv_dataset["longitude"].attrs)
    provenance = "recomputed per distance bin from source GPS (oceanstream)"
    lat_attrs["comment"] = provenance
    lon_attrs["comment"] = provenance

    ds_NASC = ds_NASC.drop_vars(
        [v for v in NASC_POSITION_VARS if v in ds_NASC.coords], errors="ignore"
    )
    ds_NASC["latitude"] = xr.DataArray(lat_binned, dims=("distance",), attrs=lat_attrs)
    ds_NASC["longitude"] = xr.DataArray(lon_binned, dims=("distance",), attrs=lon_attrs)
    ds_NASC["ping_count"] = xr.DataArray(
        counts,
        dims=("distance",),
        attrs={"long_name": "Source pings contributing to each distance bin"},
    )

    n_unique = len(np.unique(lat_binned[np.isfinite(lat_binned)]))
    logger.info(
        "NASC positions repaired: %d bins, %d unique latitudes", n_bins, n_unique
    )
    return ds_NASC


def normalize_nasc_schema(
    ds_NASC: "xr.Dataset",
    sv_dataset: Optional["xr.Dataset"] = None,
) -> "xr.Dataset":
    """Coerce a NASC product to the canonical ``NASC(channel, distance, depth)`` schema.

    Two defects are repaired:

    1. ``frequency_nominal`` inherited from a ``Dataset.where()``-broadcast Sv
       arrives as ``(channel, distance_nmi, range_sample)`` — a multi-GiB array
       that carries one scalar per channel. It is reduced back to ``(channel,)``.
    2. ``distance_nmi`` / ``range_sample`` survive as orphan dimension
       coordinates once (1) is fixed. They are dropped.
    """
    if "frequency_nominal" in ds_NASC:
        fn = ds_NASC["frequency_nominal"]
        extra = [d for d in fn.dims if d != "channel"]
        if extra:
            fn = fn.isel({d: 0 for d in extra}, drop=True)
            logger.info("Normalized frequency_nominal %s → (channel,)", tuple(fn.dims))
        ds_NASC = ds_NASC.drop_vars("frequency_nominal")
        ds_NASC = ds_NASC.assign_coords(frequency_nominal=fn.reset_coords(drop=True))
    elif sv_dataset is not None and "frequency_nominal" in sv_dataset:
        fn = sv_dataset["frequency_nominal"]
        extra = [d for d in fn.dims if d != "channel"]
        if extra:
            fn = fn.isel({d: 0 for d in extra}, drop=True)
        ds_NASC = ds_NASC.assign_coords(frequency_nominal=fn.reset_coords(drop=True))

    used_dims = set()
    for var in list(ds_NASC.data_vars.values()) + list(ds_NASC.coords.values()):
        if var.name in ds_NASC.dims:
            continue  # dimension coordinate — only keeps its own dim alive
        used_dims.update(var.dims)
    orphan = [
        d for d in ds_NASC.dims
        if d not in NASC_ALLOWED_DIMS and d not in used_dims
    ]
    if orphan:
        ds_NASC = ds_NASC.drop_dims(orphan)
        logger.info("Dropped orphan NASC dimensions: %s", ", ".join(sorted(orphan)))

    return ds_NASC


def validate_nasc_schema(
    ds_NASC: "xr.Dataset",
    *,
    require_varying_positions: bool = True,
    strict: bool = False,
) -> list[str]:
    """Check a NASC product against the canonical schema.

    Returns a list of human-readable problems (empty when valid). With
    *strict* the first problem raises :class:`ValueError` instead.

    Gates:
      - ``NASC`` exists with dims ``(channel, distance, depth)``
      - ``latitude``/``longitude`` exist on ``(distance,)`` and are finite
      - positions actually vary along ``distance`` (unless the track is static)
      - ``distance`` is monotonically increasing
      - ``NASC`` and ``distance`` carry units
    """
    problems: list[str] = []

    if "NASC" not in ds_NASC:
        problems.append("missing NASC variable")
    else:
        expected = ("channel", "distance", "depth")
        if tuple(ds_NASC["NASC"].dims) != expected:
            problems.append(
                f"NASC dims {tuple(ds_NASC['NASC'].dims)} != {expected}"
            )
        if not ds_NASC["NASC"].attrs.get("units"):
            problems.append("NASC has no units attribute")

    if "distance" in ds_NASC.coords:
        dist = np.asarray(ds_NASC["distance"].values, dtype=float)
        if dist.size > 1 and not np.all(np.diff(dist) > 0):
            problems.append("distance coordinate is not strictly increasing")
        if not ds_NASC["distance"].attrs.get("units"):
            problems.append("distance has no units attribute")
    else:
        problems.append("missing distance coordinate")

    for var in NASC_POSITION_VARS:
        if var not in ds_NASC:
            problems.append(f"missing {var}")
            continue
        if tuple(ds_NASC[var].dims) != ("distance",):
            problems.append(f"{var} dims {tuple(ds_NASC[var].dims)} != ('distance',)")
            continue
        vals = np.asarray(ds_NASC[var].values, dtype=float)
        if not np.isfinite(vals).all():
            problems.append(f"{var} has {int((~np.isfinite(vals)).sum())} non-finite values")
        if require_varying_positions and vals.size > 1:
            finite = vals[np.isfinite(vals)]
            if finite.size > 1 and np.unique(finite).size == 1:
                problems.append(
                    f"{var} is constant across {vals.size} distance bins "
                    "(per-bin position reconstruction failed)"
                )

    if "frequency_nominal" in ds_NASC:
        if tuple(ds_NASC["frequency_nominal"].dims) not in ((), ("channel",)):
            problems.append(
                "frequency_nominal dims "
                f"{tuple(ds_NASC['frequency_nominal'].dims)} != ('channel',)"
            )

    orphan = sorted(d for d in ds_NASC.dims if d not in NASC_ALLOWED_DIMS)
    if orphan:
        problems.append(f"orphan dimensions present: {', '.join(orphan)}")

    if strict and problems:
        raise ValueError("Invalid NASC schema: " + "; ".join(problems))
    return problems


def compute_nasc(
    sv_dataset: Union[Path, "xr.Dataset"],
    range_bin: str = "10m",
    dist_bin: str = "0.5nmi",
    transducer_depth: float = 0.0,
    echodata: Optional["EchoData"] = None,
    output_path: Optional[Path] = None,
    validate_positions: bool = True,
    strict_schema: bool = False,
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
        validate_positions: Require per-bin lat/lon to vary along ``distance``
            (disable for genuinely stationary deployments)
        strict_schema: Raise instead of warning when the output fails the
            schema gates
        
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
    
    # Rebuild per-distance-bin positions from the source GPS. echopype's own
    # reduction is unreliable across flox/xarray versions and can collapse the
    # whole track to a single point, so always recompute rather than only
    # filling in absent variables.
    ds_NASC = repair_nasc_positions(ds_NASC, sv_dataset)
    ds_NASC = normalize_nasc_schema(ds_NASC, sv_dataset)
    
    # Add NASC_log for visualization (ported from legacy workflow.py:555)
    # Log transform provides better visual representation across dynamic range
    with np.errstate(divide="ignore", invalid="ignore"):
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
    
    problems = validate_nasc_schema(
        ds_NASC, require_varying_positions=validate_positions, strict=strict_schema
    )
    for problem in problems:
        logger.warning("NASC schema: %s", problem)
    
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
