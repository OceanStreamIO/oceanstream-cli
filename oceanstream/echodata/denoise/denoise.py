"""Main denoising pipeline for Sv datasets.

Provides high-level functions for applying multiple denoising
methods and combining masks.  Supports frequency-keyed parameter
dispatch and pulse-length selection.
"""

from __future__ import annotations

from oceanstream.echodata.products import open_product_uri
import json
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


def channels_ping_in_turn(sv: "xr.DataArray", range_dim: str = "range_sample") -> bool:
    """True when the channels transmit in turn rather than together.

    A multiplexed echosounder leaves each channel empty at the pings of the other
    channels. Every ping-window parameter then counts empty pings (a 50-ping window
    holds 10 real pings with five channels), and a ping-lag comparison meets an empty
    neighbour. Detected as fewer than half of the pings that hold any data holding data
    from two or more channels.
    """
    if "channel" not in sv.dims or sv.sizes["channel"] < 2:  # noqa: PLR2004
        return False
    channels_with_data = sv.notnull().any(range_dim).sum("channel").values
    used = channels_with_data > 0
    if not used.any():
        return False
    return bool((channels_with_data[used] >= 2).mean() < 0.5)  # noqa: PLR2004


#: Ping-count window parameters per filter, with the fewest pings each may be scaled
#: down to. Ping lags are not here: they compare neighbouring pings by definition.
PING_WINDOWS: dict[str, dict[str, int]] = {
    "attenuation": {"num_side_pings": 5},
    "transient": {"n_pings": 5},
    "background": {"ping_window": 10},
}


def split_sessions(ping_times: np.ndarray, gap_minutes: float | None) -> list[np.ndarray]:
    """Index arrays of the recording sessions in *ping_times*.

    A session ends where consecutive pings are more than *gap_minutes* apart, so no
    filter window reaches across a recording gap. Without a gap, one session.
    """
    times = np.asarray(ping_times, dtype="datetime64[ns]")
    if times.size == 0:
        return []
    index = np.arange(times.size)
    if not gap_minutes:
        return [index]
    gap = np.timedelta64(int(gap_minutes * 60e9), "ns")
    return np.split(index, np.flatnonzero(np.diff(times) > gap) + 1)


def ping_interval_s(ping_times: np.ndarray, sessions: list[np.ndarray] | None = None) -> float | None:
    """Median interval between consecutive pings in seconds, within sessions."""
    times = np.asarray(ping_times, dtype="datetime64[ns]")
    parts = sessions if sessions is not None else [np.arange(times.size)]
    steps = [np.diff(times[idx]) for idx in parts if idx.size > 1]
    if not steps:
        return None
    return float(np.median(np.concatenate(steps)) / np.timedelta64(1, "s"))


def scale_ping_windows(method: str, params: Mapping[str, Any], ratio: float | None) -> dict:
    """*params* with *method*'s ping-count windows multiplied by *ratio*.

    *ratio* is the reference ping interval divided by the channel's own, so a window
    spans the same time it was tuned for. Scaling down stops at the floor in
    :data:`PING_WINDOWS`, or at the original value when that is smaller.
    """
    out = dict(params)
    if ratio is None or method not in PING_WINDOWS:
        return out
    for name, floor in PING_WINDOWS[method].items():
        value = out.get(name)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            out[name] = max(min(floor, int(value)), int(round(value * ratio)))
    return out


def compact_channel(
    ch_ds: "xr.Dataset", var_name: str = "Sv", range_dim: str = "range_sample"
) -> tuple["xr.Dataset", np.ndarray, int]:
    """One channel restricted to the pings it transmitted in and the range it recorded.

    Returns the compacted dataset, the indices of the kept pings and the number of range
    samples kept, for :func:`expand_channel`.
    """
    finite = ch_ds[var_name].notnull()
    ping_has = finite.any([d for d in finite.dims if d != "ping_time"]).values
    range_has = finite.any([d for d in finite.dims if d != range_dim]).values
    ping_idx = np.flatnonzero(ping_has)
    n_range = int(np.flatnonzero(range_has).max()) + 1 if range_has.any() else 0
    compact = ch_ds.isel({"ping_time": ping_idx, range_dim: slice(0, n_range)})
    return compact, ping_idx, n_range


def expand_channel(
    values: "xr.DataArray",
    reference: "xr.DataArray",
    ping_idx: np.ndarray,
    n_range: int,
    fill_value,
    range_dim: str = "range_sample",
) -> "xr.DataArray":
    """Put a compacted channel's result back on *reference*'s pings and range."""
    import xarray as xr

    out = xr.DataArray(
        np.full(reference.shape, fill_value, dtype=np.asarray(values).dtype),
        dims=reference.dims,
        coords=reference.coords,
    )
    index = {"ping_time": ping_idx}
    if range_dim in reference.dims:
        index[range_dim] = slice(0, n_range)
    out[index] = values.transpose(*reference.dims).values
    return out


def apply_denoising(
    sv_dataset: Union[Path, "xr.Dataset"],
    methods: list[str] = None,
    config: Optional["DenoiseConfig"] = None,
    output_path: Optional[Path] = None,
    merge_masks: bool = False,
    return_stage_masks: bool = False,
    return_status: bool = False,
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
        return_status: If True, also return the per-stage/per-channel
            execution status dict
        
    Returns:
        Denoised Sv dataset with noise masks applied.
        Extra elements are appended in the order
        ``(dataset, stage_masks, stage_status)`` for whichever of
        *return_stage_masks* / *return_status* are True.
        
    Example:
        denoised = apply_denoising(sv_path, methods=["background", "impulse"])
    """
    
    # Load if path
    if isinstance(sv_dataset, (str, Path)):
        logger.info(f"Loading Sv from {sv_dataset}")
        sv_dataset = open_product_uri(sv_dataset)
    
    # Default methods
    if methods is None:
        methods = ["background", "transient", "impulse", "attenuation"]
    
    # Default config
    if config is None:
        from oceanstream.echodata.config import DenoiseConfig
        config = DenoiseConfig()
    
    logger.info(f"Applying denoising methods: {methods}")
    
    # Build combined mask using per-channel approach
    combined_mask, stage_cubes, stage_status = build_full_mask(
        sv_dataset,
        methods=methods,
        config=config,
        return_stage_masks=True,
        return_status=True,
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
    
    if return_stage_masks and return_status:
        return denoised, stage_cubes, stage_status
    if return_stage_masks:
        return denoised, stage_cubes
    if return_status:
        return denoised, stage_status
    
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
    return_status: bool = False,
    compact_pings: bool | None = None,
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
        return_status: If True, also return a per-stage/per-channel execution
            status dict (``"ran"``, ``"skipped_no_params"``, ``"failed: ..."``).
        compact_pings: Run each filter on the channel's own pings and recorded range
            only, then expand the masks back. ``None`` (default) does this when the
            channels ping in turn (:func:`channels_ping_in_turn`), so data whose
            channels ping together is processed exactly as before.

    Returns:
        Combined mask DataArray. Extra elements are appended in the order
        ``(mask, stage_cubes, stage_status)`` for whichever of
        *return_stage_masks* / *return_status* are True.
    """
    import xarray as xr

    from oceanstream.echodata.denoise.background_noise import background_noise_mask
    from oceanstream.echodata.denoise.transient_noise import (
        transient_noise_mask,
        transient_noise_mask_ryan,
    )
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
    # Per-stage, per-channel execution status. "ran" and "skipped_no_params"
    # are NOT interchangeable: a stage that ran and found nothing contributes a
    # genuine all-False mask, while a stage that could not run contributes no
    # information at all. Downstream statistics must be able to tell them apart.
    stage_status: dict[str, dict[str, str]] = {name: {} for name in stages}
    ch_masks: list[xr.DataArray] = []

    range_dim = next((d for d in ds[var_name].dims if d not in ("channel", "ping_time")), None)
    if compact_pings is None:
        compact_pings = range_dim is not None and channels_ping_in_turn(ds[var_name], range_dim)
    if compact_pings:
        logger.info("Channels ping in turn: each channel is filtered on its own pings and range")
    reference_interval = getattr(config, "reference_ping_interval_s", None)
    session_gap = getattr(config, "session_gap_minutes", None)
    window_ratios: dict[str, float] = {}
    session_skips: dict[str, dict[str, int]] = {name: {} for name in stages}

    for ch in range(n_ch):
        ch_ds = ds.isel(channel=ch)
        reference = ds[var_name].isel(channel=ch)
        ch_label = str(ds["channel"].values[ch])
        stage_or = None

        work_ds, work_ref = ch_ds, reference
        if compact_pings:
            work_ds, ping_idx, n_range = compact_channel(ch_ds, var_name, range_dim)
            work_ref = work_ds[var_name]
            if work_ds.sizes["ping_time"] == 0:
                for stage_name in stages:
                    stage_status[stage_name][ch_label] = "no_data"

        sessions = split_sessions(work_ds["ping_time"].values, session_gap) if session_gap else None
        ratio = None
        if reference_interval:
            interval = ping_interval_s(work_ds["ping_time"].values, sessions)
            if interval:
                ratio = float(reference_interval) / interval
                window_ratios[ch_label] = round(ratio, 4)
        if sessions is not None or ratio is not None:
            logger.info(
                "  %s: %s sessions, ping-count windows scaled x%s",
                ch_label, len(sessions) if sessions is not None else 1,
                f"{ratio:.3f}" if ratio is not None else "1",
            )

        for stage_name, spec in stages.items():
            fn = spec["fn"]
            param_sets = spec["param_sets"]
            if stage_status[stage_name].get(ch_label) == "no_data":
                continue

            # Resolve parameters for this channel
            pars = _params_for_channel(param_sets, work_ds, pulse_length)
            if pars is None:
                stage_status[stage_name][ch_label] = "skipped_no_params"
                continue

            pars = scale_ping_windows(stage_name, pars, ratio)
            # The transient stage runs Fielding's detector unless the preset picks Ryan's.
            stage_fn = fn
            if stage_name == "transient" and pars.get("detector") == "ryan":
                stage_fn = transient_noise_mask_ryan
            stage_mask, error, skipped = _run_stage_by_session(
                stage_fn, work_ds, work_ref, pars, sessions, mask_unfeasible
            )
            if stage_mask is None:
                if error is None:
                    stage_status[stage_name][ch_label] = "returned_none"
                else:
                    logger.warning(f"Error computing {stage_name} mask for channel {ch}: {error}")
                    stage_status[stage_name][ch_label] = f"failed: {error}"
                continue
            if skipped:
                session_skips[stage_name][ch_label] = skipped
                logger.warning(
                    "%s on %s: %d of %d sessions too short or failed (%s); left unflagged",
                    stage_name, ch_label, skipped, len(sessions), error,
                )
            if compact_pings:
                stage_mask = expand_channel(
                    stage_mask.astype(bool), reference, ping_idx, n_range, False, range_dim
                ).reset_coords(drop=True)
            for cname in reference.coords:
                if cname in stage_mask.dims or cname == "channel":
                    continue
                stage_mask = stage_mask.assign_coords({cname: reference.coords[cname]})

            # Expand to channel dimension
            ch_value = ds["channel"].values[ch]
            stage_mask = stage_mask.expand_dims(channel=[ch_value])
            stage_mask = stage_mask & ~reference.isnull().expand_dims(channel=[ch_value])

            stage_masks[stage_name].append(stage_mask)
            stage_status[stage_name][ch_label] = "ran"
            stage_or = stage_mask if stage_or is None else (stage_or | stage_mask)

        if stage_or is not None:
            ch_masks.append(stage_or)
        else:
            # Channel was skipped (e.g. no frequency presets) — add all-False mask
            # to keep the channel dimension aligned with the input dataset.
            ch_value = ds["channel"].values[ch]
            false_mask = xr.DataArray(
                np.zeros(reference.shape, dtype=bool),
                dims=reference.dims,
                coords=reference.coords,
            ).expand_dims(channel=[ch_value])
            ch_masks.append(false_mask)

    if not ch_masks:
        full_mask = xr.DataArray(
            np.zeros(ds[var_name].shape, dtype=bool),
            dims=ds[var_name].dims,
            coords=ds[var_name].coords,
        )
        return _pack_mask_result(
            full_mask, {}, stage_status, return_stage_masks, return_status
        )

    full_mask = xr.concat(ch_masks, dim="channel")
    full_mask = full_mask.broadcast_like(ds[var_name]).astype(bool)
    full_mask.name = "combined_mask"

    pct_flagged = float(full_mask.mean().values) * 100
    logger.info(f"Combined mask: {pct_flagged:.1f}% flagged as noise")

    if not return_stage_masks and not return_status:
        return full_mask

    all_channels = ds["channel"].values
    stage_cubes: dict[str, xr.DataArray] = {}
    for name, m_list in stage_masks.items():
        if not m_list:
            continue
        cube = xr.concat(m_list, dim="channel")
        # Channels where this stage was skipped or failed must reindex to
        # False, not NaN. A NaN fill silently upcasts the cube to float and
        # then evaluates as True in every downstream boolean reduction.
        cube = cube.reindex(channel=all_channels, fill_value=False)
        cube = cube.broadcast_like(ds[var_name]).fillna(False).astype(bool)
        cube.attrs["channel_status"] = json.dumps(stage_status.get(name, {}))
        if window_ratios:
            cube.attrs["window_ratio"] = json.dumps(window_ratios)
        if session_skips.get(name):
            cube.attrs["session_skips"] = json.dumps(session_skips[name])
        stage_cubes[name] = cube

    return _pack_mask_result(
        full_mask, stage_cubes, stage_status, return_stage_masks, return_status
    )


def _run_stage_by_session(fn, work_ds, work_ref, pars, sessions, mask_unfeasible):
    """Run one filter on a channel, session by session, and join the masks.

    Without sessions the filter runs once on the whole channel, as before. Returns the
    mask aligned to *work_ref* (None when no session produced one), the last error and
    the number of sessions that failed, typically because they are shorter than a window.
    """
    import xarray as xr

    def one(part_ds, part_ref):
        result = fn(part_ds, pars)
        # All denoise functions now return (mask, unfeasible) tuples
        if isinstance(result, (tuple, list)):
            mask = result[0] | result[1] if mask_unfeasible and len(result) >= 2 else result[0]
        else:
            mask = result
        if mask is None:
            return None
        return mask.broadcast_like(part_ref).reset_coords(drop=True)

    if sessions is None:
        try:
            return one(work_ds, work_ref), None, 0
        except Exception as e:  # noqa: BLE001 - reported as the stage status
            return None, e, 0

    pieces, failed, error = [], 0, None
    for idx in sessions:
        part_ref = work_ref.isel(ping_time=idx)
        try:
            mask = one(work_ds.isel(ping_time=idx), part_ref)
        except Exception as e:  # noqa: BLE001 - counted, the session stays unflagged
            mask, error = None, e
        if mask is None:
            failed += 1
            pieces.append(np.zeros(part_ref.shape, dtype=bool))
        else:
            pieces.append(np.asarray(mask.transpose(*part_ref.dims).values, dtype=bool))
    if failed == len(sessions):
        return None, error, failed
    axis = work_ref.dims.index("ping_time")
    joined = xr.DataArray(
        np.concatenate(pieces, axis=axis),
        dims=work_ref.dims,
        coords={d: work_ref[d] for d in work_ref.dims if d in work_ref.coords},
    )
    return joined, error, failed


def _pack_mask_result(
    full_mask: "xr.DataArray",
    stage_cubes: dict,
    stage_status: dict,
    return_stage_masks: bool,
    return_status: bool,
):
    """Assemble the ``build_full_mask`` return value for the requested flags."""
    if return_stage_masks and return_status:
        return full_mask, stage_cubes, stage_status
    if return_stage_masks:
        return full_mask, stage_cubes
    if return_status:
        return full_mask, stage_status
    return full_mask


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
    multichannel_reduce: str = "min",
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
        multichannel_reduce: How to combine NaN fractions across channels when
                             multiple channels are present.
                             - "min" (default): drop only if ALL channels agree
                               the ping is dead. Correct for multi-frequency
                               data where high-frequency channels (e.g. 200 kHz)
                               naturally have many NaN samples at depth due to
                               absorption — using "max" would drop nearly every
                               ping.
                             - "max": drop if ANY channel is dead. Only sensible
                               for single-frequency or same-frequency arrays.
        
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
    if "echo_range" in ds and channels_ping_in_turn(ds[var_name], range_dim):
        # Count only the cells each channel recorded: on the shared grid a short-range
        # channel is mostly NaN by construction, and a channel is empty at the other
        # channels' pings, so those pings are left to the channels that transmitted.
        # Only cells where the channel usually keeps signal count: after background
        # removal a high-frequency channel is empty beyond its effective range in every
        # ping, and those cells would otherwise make every ping look dead. Without a
        # cross-channel minimum to fall back on, a ping is dropped only when it is empty
        # where its channel normally has data.
        recorded = ds["echo_range"].notnull()
        n_pinged = recorded.any(range_dim).sum("ping_time")
        typical = ds[var_name].notnull().sum("ping_time") / n_pinged.where(n_pinged > 0)
        usual = typical >= 0.5  # noqa: PLR2004
        signal = recorded & usual.where(usual.any(range_dim), True)
        n_signal = signal.sum(dim=range_dim)
        n_nan = (ds[var_name].isnull() & signal).sum(dim=range_dim)
        frac_nan = (n_nan / n_signal).where(n_signal > 0)
    else:
        n_nan = ds[var_name].isnull().sum(dim=range_dim)
        total = ds[var_name].sizes[range_dim]
        frac_nan = n_nan / total
    
    # Reduce across channels. For multi-frequency data, "min" is correct: drop
    # only pings where ALL channels agree the ping is dead. Using "max" would
    # drop nearly every ping because higher-frequency channels (e.g. 200 kHz)
    # naturally have most of the water column below their noise floor after
    # denoising, giving them near-100% NaN fractions per ping.
    if "channel" in frac_nan.dims:
        if multichannel_reduce == "min":
            frac_nan = frac_nan.min(dim="channel")
        elif multichannel_reduce == "max":
            frac_nan = frac_nan.max(dim="channel")
        else:
            raise ValueError(
                f"multichannel_reduce must be 'min' or 'max', got {multichannel_reduce!r}"
            )
    
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
