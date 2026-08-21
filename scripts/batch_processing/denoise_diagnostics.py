"""Per-day denoise diagnostics (schema ``oceanstream.denoise-stats/v1``).

Phase 3 of ``plan-denoisePresetComparison``. Produces the numbers that make a
preset comparison interpretable rather than merely different:

* how much of the water column each filter actually flagged, against an
  **explicitly declared denominator** (source-finite cells inside the agreed
  per-frequency depth band, surface bin excluded);
* how much of that flagging was **unique** to a filter versus shared — the
  masks overlap, so a stacked "additive" breakdown would be wrong;
* the background correction accounted **separately**, because it is an Sv
  transform (a subtraction) and not a mask;
* the sanity clip, with its threshold and the count it newly removed;
* Sv percentile summaries at every stage.

Every numeric field is strict JSON: non-finite values become ``null`` and are
paired with a ``status`` field, never a ``NaN`` token.
"""

from __future__ import annotations

import itertools
import logging
from typing import TYPE_CHECKING, Any

import numpy as np

if TYPE_CHECKING:
    import xarray as xr

from experiment_contract import DENOISE_STATS_SCHEMA, TOLERANCES, json_safe

logger = logging.getLogger(__name__)

#: Percentiles reported for every Sv stage.
PERCENTILES = (1, 5, 25, 50, 75, 95, 99)

#: Mask stages that are genuinely masks (background is a transform).
MASK_STAGES = ("impulse", "attenuation", "transient")

#: Depth band used when a channel's frequency has no declared band.
DEFAULT_BAND_M = (10.0, 1000.0)

#: Upper bound on the number of samples fed to ``np.percentile``. A full
#: short-pulse channel is ~2x10^8 cells; sorting that costs gigabytes for
#: percentiles that are already stable at a few million samples.
PERCENTILE_SAMPLE_CAP = 4_000_000


# ── Resolved / unused parameters ───────────────────────────────────────────

#: Keys each mask function actually reads out of its params dict.
#:
#: ``transient`` lists the keys of :func:`transient_noise_mask`, the
#: **Fielding-style** deep-band detector that oceanstream wires up by default.
#: Ryan's own transient filter lives in ``transient_noise_mask_ryan`` and is not
#: wired in — which is why ``percentile`` (a Ryan-TN-only parameter) is present
#: in the preset TOMLs but silently ignored at runtime.
CONSUMED_PARAM_KEYS: dict[str, frozenset[str]] = {
    "impulse": frozenset(
        {
            "range_coord", "vertical_bin_size", "ping_lags", "threshold_db",
            "threshold", "exclude_shallow_above", "vote_k_of_n", "post_dilate",
        }
    ),
    "attenuation": frozenset(
        {"upper_limit_sl", "lower_limit_sl", "num_side_pings", "threshold", "range_coord"}
    ),
    "transient": frozenset(
        {
            "range_coord", "ping_window", "n_pings", "threshold", "thr_dB",
            "exclude_above", "ref_min", "ref_max", "jumps", "depth_bin", "maxts",
        }
    ),
    "background": frozenset(
        {
            "range_coord", "sound_absorption", "range_window", "ping_window",
            "background_noise_max", "SNR_threshold", "depth_stat",
            "depth_quantile", "guard_mode", "guard_depth", "guard_band",
        }
    ),
}

#: Keys known to be inert, with the reason — surfaced in the report rather than
#: silently dropped.
KNOWN_INERT_KEYS: dict[tuple[str, str], str] = {
    ("transient", "percentile"): (
        "Ryan-TN-only parameter; the wired detector is the Fielding-style "
        "transient_noise_mask, which ignores it"
    ),
}


def resolve_consumed_params(denoise_config, ds: xr.Dataset) -> dict:
    """Resolve the parameters each channel/method combination will actually use.

    Mirrors the dispatch in
    :func:`oceanstream.echodata.denoise.denoise._params_for_channel` so the
    report's parameter tables are generated from the *effective* values rather
    than transcribed from the TOML.
    """
    from oceanstream.echodata.denoise.denoise import _params_for_channel

    resolved: dict[str, Any] = {}
    n_ch = ds.sizes.get("channel", 1)
    for ch in range(n_ch):
        ch_ds = ds.isel(channel=ch)
        label = str(ds["channel"].values[ch]) if "channel" in ds.coords else str(ch)
        per_method: dict[str, Any] = {}
        for method in denoise_config.methods:
            try:
                if denoise_config.use_frequency_specific:
                    param_sets = denoise_config.to_frequency_keyed_params(method)
                else:
                    param_sets = denoise_config._get_global_params(method)
                pars = _params_for_channel(param_sets, ch_ds, None)
            except Exception as exc:  # resolution failure is itself a finding
                per_method[method] = {"status": f"unresolved: {exc}"}
                continue
            per_method[method] = (
                {"status": "skipped_no_params"} if pars is None
                else {"status": "resolved", "params": pars}
            )
        resolved[label] = {
            "frequency_hz": channel_frequency(ds, ch),
            "methods": per_method,
        }
    return resolved


def find_unused_toml_fields(toml_path) -> list[dict]:
    """Report TOML denoise keys that no wired filter consumes.

    Returns one entry per ``(frequency, method, key)`` that will be ignored at
    runtime, with a reason when the key is a known-inert one.
    """
    import tomllib
    from pathlib import Path

    path = Path(toml_path)
    if not path.exists():
        return []
    with open(path, "rb") as fh:
        doc = tomllib.load(fh)

    freq_params = (
        doc.get("echodata", {}).get("denoise", {}).get("frequency_params", {}) or {}
    )
    findings: list[dict] = []
    for freq, methods in freq_params.items():
        if not isinstance(methods, dict):
            continue
        for method, params in methods.items():
            if not isinstance(params, dict):
                continue
            consumed = CONSUMED_PARAM_KEYS.get(method)
            if consumed is None:
                continue
            for key in params:
                if key in consumed:
                    continue
                findings.append(
                    {
                        "frequency_hz": str(freq),
                        "method": method,
                        "key": key,
                        "reason": KNOWN_INERT_KEYS.get(
                            (method, key), "not read by the wired filter implementation"
                        ),
                    }
                )
    return findings


def band_for_frequency(freq_hz: float | None) -> tuple[float, float]:
    """Integration depth band (metres) declared for *freq_hz* in Phase 0."""
    if freq_hz is None:
        return DEFAULT_BAND_M
    key = str(int(round(freq_hz)))
    band = TOLERANCES.integration_bands_m.get(key)
    if band is None:
        logger.warning(
            "No declared integration band for %s Hz — falling back to %s m",
            key, DEFAULT_BAND_M,
        )
        return DEFAULT_BAND_M
    return float(band[0]), float(band[1])


def channel_frequency(ds: xr.Dataset, ch: int) -> float | None:
    """Nominal frequency in Hz for channel index *ch*, or ``None``."""
    if "frequency_nominal" not in ds:
        return None
    fn = ds["frequency_nominal"]
    try:
        if "channel" in fn.dims:
            fn = fn.isel(channel=ch)
        extra = [d for d in fn.dims if d != "channel"]
        if extra:
            fn = fn.isel({d: 0 for d in extra})
        value = float(np.asarray(fn.values).ravel()[0])
    except (IndexError, ValueError, TypeError):
        return None
    return value if np.isfinite(value) else None


def band_selection(
    ds: xr.Dataset, ch: int, band: tuple[float, float]
) -> np.ndarray:
    """Boolean array over the channel's ``Sv`` grid selecting the depth band.

    The band's upper edge already excludes the near-surface region, which
    covers the partial first vertical bin documented in
    ``oceanstream.echodata.compute.mvbs.SURFACE_BIN_INDEX``.

    The comparison is evaluated in xarray so the result materialises straight to
    ``bool``; converting the depth cube to float first would cost a full extra
    copy of a ~200M-cell array per channel.
    """
    sv = ds["Sv"].isel(channel=ch)
    if "depth" not in ds:
        return np.ones(sv.shape, dtype=bool)
    depth = ds["depth"]
    if "channel" in depth.dims:
        depth = depth.isel(channel=ch)
    top, bottom = band
    sel = ((depth >= top) & (depth <= bottom)).values
    if sel.shape != sv.shape:
        sel = np.broadcast_to(sel, sv.shape)
    return np.ascontiguousarray(sel)


def as_channel_grid(arr: np.ndarray, shape: tuple[int, ...]) -> np.ndarray:
    """Drop the singleton ``channel`` axis that ``groupby("channel")`` leaves on.

    Recent xarray no longer squeezes groups, so a per-channel dataset arrives as
    ``(1, ping_time, range_sample)`` while the diagnostics support arrays are
    ``(ping_time, range_sample)``. Boolean indexing does not broadcast, so the
    mismatch has to be resolved explicitly rather than left to NumPy.
    """
    arr = np.asarray(arr)
    if arr.shape == shape:
        return arr
    if arr.size == int(np.prod(shape)):
        return arr.reshape(shape)
    raise ValueError(
        f"cannot align array of shape {arr.shape} with channel grid {shape}"
    )


def percentile_summary(values: np.ndarray, max_samples: int = PERCENTILE_SAMPLE_CAP) -> dict:
    """Percentiles of the finite entries of *values*, with an explicit status.

    Arrays larger than *max_samples* are strided down before sorting. A stride
    over ~10^8 cells still leaves millions of samples, which pins these
    percentiles far tighter than the 0.1 dB comparison tolerance, and it avoids
    the multi-gigabyte copy that ``np.percentile`` would otherwise make. The
    exact finite count is always reported from the full array.
    """
    flat = np.asarray(values).ravel()
    n_finite = int(np.count_nonzero(np.isfinite(flat)))
    if n_finite == 0:
        return {
            "status": "no_finite_values",
            "n_finite": 0,
            "n_sampled": 0,
            "sample_step": 1,
            "percentiles_db": {str(p): None for p in PERCENTILES},
            "mean_db": None,
        }

    step = 1
    if flat.size > max_samples:
        step = int(np.ceil(flat.size / max_samples))
        flat = flat[::step]
    sample = flat[np.isfinite(flat)].astype(np.float64, copy=False)
    if sample.size == 0:  # stride landed entirely on NaNs
        step = 1
        sample = np.asarray(values).ravel()
        sample = sample[np.isfinite(sample)].astype(np.float64, copy=False)

    pct = np.percentile(sample, PERCENTILES)
    return {
        "status": "ok",
        "n_finite": n_finite,
        "n_sampled": int(sample.size),
        "sample_step": step,
        "percentiles_db": {str(p): float(v) for p, v in zip(PERCENTILES, pct)},
        "mean_db": float(sample.mean()),
    }


def _fraction(numerator: int, denominator: int) -> float | None:
    return float(numerator) / denominator if denominator > 0 else None


def mask_interaction_stats(
    masks: dict[str, np.ndarray],
    valid: np.ndarray,
    denominator: int,
) -> dict:
    """Per-mask counts, unique contributions, pairwise overlaps and the union.

    *masks* maps stage name → boolean array on the channel grid. *valid* is the
    denominator support (source-finite cells inside the depth band); every count
    is restricted to it. Masks overlap, so ``sum(per-mask counts) != union`` —
    both are reported plus the unique-contribution decomposition, which does sum
    to the union.
    """
    restricted = {name: (m & valid) for name, m in masks.items()}

    per_mask = {}
    for name, m in restricted.items():
        n = int(m.sum())
        per_mask[name] = {
            "n_flagged": n,
            "fraction_of_valid": _fraction(n, denominator),
        }

    if restricted:
        union = np.zeros_like(valid, dtype=bool)
        for m in restricted.values():
            union |= m
    else:
        union = np.zeros_like(valid, dtype=bool)
    n_union = int(union.sum())

    unique = {}
    for name, m in restricted.items():
        others = np.zeros_like(valid, dtype=bool)
        for other_name, other in restricted.items():
            if other_name != name:
                others |= other
        n = int((m & ~others).sum())
        unique[name] = {
            "n_unique": n,
            "fraction_of_valid": _fraction(n, denominator),
        }

    overlaps = {}
    for a, b in itertools.combinations(sorted(restricted), 2):
        n = int((restricted[a] & restricted[b]).sum())
        overlaps[f"{a}|{b}"] = {
            "n_overlap": n,
            "fraction_of_valid": _fraction(n, denominator),
        }

    return {
        "denominator": denominator,
        "denominator_definition": (
            "source-finite Sv cells inside the declared per-frequency depth band"
        ),
        "per_mask": per_mask,
        "unique_contribution": unique,
        "pairwise_overlap": overlaps,
        "union": {
            "n_flagged": n_union,
            "fraction_of_valid": _fraction(n_union, denominator),
        },
        "note": (
            "Masks overlap: per-mask counts do not sum to the union. "
            "unique_contribution does."
        ),
    }


def verify_mask_union(
    combined: np.ndarray,
    masks: dict[str, np.ndarray],
    valid: np.ndarray,
) -> dict:
    """Check ``noise_mask == OR(impulse, attenuation, transient)``.

    Only cells where every contributing stage was successfully evaluated are
    checked; a stage that could not run contributes no information and would
    otherwise make the identity trivially fail.
    """
    if not masks:
        return {"status": "no_mask_stages", "n_mismatch": None}
    expected = np.zeros_like(valid, dtype=bool)
    for m in masks.values():
        expected |= m
    mismatch = int(((combined != expected) & valid).sum())
    return {
        "status": "ok" if mismatch == 0 else "mismatch",
        "n_mismatch": mismatch,
        "stages_checked": sorted(masks),
    }


def channel_diagnostics(
    *,
    ds_source: xr.Dataset,
    ch: int,
    channel_label: str,
    stage_masks: dict[str, np.ndarray],
    stage_status: dict[str, dict[str, str]],
    combined_mask: np.ndarray | None,
    sv_after_masks: np.ndarray,
) -> dict:
    """Diagnostics for one channel up to and including mask application."""
    freq = channel_frequency(ds_source, ch)
    band = band_for_frequency(freq)
    in_band = band_selection(ds_source, ch, band)

    # Native dtype throughout: forcing float64 would double a ~200M-cell array.
    sv_source = np.asarray(ds_source["Sv"].isel(channel=ch).values)
    source_finite = np.isfinite(sv_source)
    valid = source_finite
    valid &= in_band
    denominator = int(np.count_nonzero(valid))
    cells_in_band = int(np.count_nonzero(in_band))
    del in_band, source_finite

    ran_masks = {
        name: m
        for name, m in stage_masks.items()
        if stage_status.get(name, {}).get(channel_label) == "ran"
    }

    entry: dict[str, Any] = {
        "channel": channel_label,
        "frequency_hz": freq,
        "depth_band_m": list(band),
        "cells_total": int(sv_source.size),
        "cells_in_band": cells_in_band,
        "source_finite": denominator,
        "source_nan_in_band": cells_in_band - denominator,
        "filter_status": {
            name: stage_status.get(name, {}).get(channel_label, "not_configured")
            for name in stage_status
        },
        "masks": mask_interaction_stats(ran_masks, valid, denominator),
        "sv_percentiles": {
            "source": percentile_summary(sv_source[valid]),
            "after_masks": percentile_summary(
                as_channel_grid(sv_after_masks, valid.shape)[valid]
            ),
        },
    }
    del sv_source

    if combined_mask is not None:
        entry["mask_union_identity"] = verify_mask_union(
            as_channel_grid(combined_mask, valid.shape), ran_masks, valid
        )

    entry["_valid"] = valid  # stripped before serialisation; reused by later stages
    return entry


def add_background_stats(
    entry: dict,
    sv_before: np.ndarray,
    sv_after: np.ndarray,
) -> None:
    """Account the background correction separately from the masks.

    ``remove_background_noise`` subtracts an estimated noise floor and NaNs
    samples that fall below the SNR threshold. It is a transform, not a mask,
    so it gets its own finite→NaN count and Sv-change summary.
    """
    valid = entry["_valid"]
    sv_before = as_channel_grid(sv_before, valid.shape)
    sv_after = as_channel_grid(sv_after, valid.shape)

    finite_before = np.isfinite(sv_before)
    finite_before &= valid
    finite_after = np.isfinite(sv_after)
    finite_after &= valid
    n_before = int(np.count_nonzero(finite_before))
    n_after = int(np.count_nonzero(finite_after))

    both = finite_before & finite_after
    n_both = int(np.count_nonzero(both))
    n_to_nan = n_before - n_both
    delta = (sv_after[both] - sv_before[both]).astype(np.float64, copy=False)
    del finite_before, finite_after, both

    if delta.size > PERCENTILE_SAMPLE_CAP:
        delta = delta[:: int(np.ceil(delta.size / PERCENTILE_SAMPLE_CAP))]

    entry["background_correction"] = {
        "kind": "sv_transform",
        "n_finite_before": n_before,
        "n_finite_after": n_after,
        "n_finite_to_nan": n_to_nan,
        "fraction_finite_to_nan": _fraction(n_to_nan, n_before),
        "sv_change_db": (
            {
                "status": "ok",
                "n": int(delta.size),
                "mean": float(delta.mean()),
                "median": float(np.median(delta)),
                "p5": float(np.percentile(delta, 5)),
                "p95": float(np.percentile(delta, 95)),
                "min": float(delta.min()),
                "max": float(delta.max()),
            }
            if delta.size
            else {"status": "no_overlapping_finite_cells", "n": 0}
        ),
    }
    entry["sv_percentiles"]["after_background"] = percentile_summary(sv_after[valid])


def add_clip_stats(
    entry: dict,
    sv_before: np.ndarray,
    threshold_db: float | None,
) -> None:
    """Record the post-denoise sanity clip from the *pre-clip* channel array.

    Takes the pre-clip values rather than a before/after pair: the clip is a
    pure threshold, so the cells it removes are exactly the finite ones above
    it. Deriving them avoids keeping a second full-resolution copy of Sv alive
    just to diff against.
    """
    valid = entry["_valid"]
    sv_before = as_channel_grid(sv_before, valid.shape)
    n_valid = int(np.count_nonzero(valid))

    if threshold_db is None:
        entry["sanity_clip"] = {
            "threshold_db": None,
            "n_newly_clipped": 0,
            "fraction_of_valid": _fraction(0, n_valid),
        }
        entry["sv_percentiles"]["after_clip"] = percentile_summary(sv_before[valid])
        return

    kept = sv_before <= threshold_db
    kept &= valid
    n_kept = int(np.count_nonzero(kept))
    n_finite = int(np.count_nonzero(np.isfinite(sv_before) & valid))

    entry["sanity_clip"] = {
        "threshold_db": threshold_db,
        "n_newly_clipped": n_finite - n_kept,
        "fraction_of_valid": _fraction(n_finite - n_kept, n_valid),
    }
    entry["sv_percentiles"]["after_clip"] = percentile_summary(sv_before[kept])


def finalise(
    *,
    day_key: str,
    category: str,
    preset_key: str | None,
    channels: list[dict],
    ping_counts: dict,
    resolved_params: dict,
    unused_toml_fields: list[dict] | None = None,
    source_reference: dict | None = None,
) -> dict:
    """Assemble the strict-JSON stats document."""
    for entry in channels:
        entry.pop("_valid", None)
    payload = {
        "schema": DENOISE_STATS_SCHEMA,
        "day": day_key,
        "category": category,
        "preset": preset_key,
        "source": source_reference,
        "ping_counts": ping_counts,
        "resolved_params": resolved_params,
        "unused_toml_fields": unused_toml_fields or [],
        "tolerances": TOLERANCES.to_dict(),
        "channels": channels,
    }
    return json_safe(payload)
