"""Pydantic parameter models for echodata denoising and regridding.

These models provide validated, per-frequency parameter schemas with support
for pulse-length-aware configuration (short_pulse / long_pulse).  Missing
per-frequency entries are automatically inherited from the 38 kHz template
(or the first available frequency) via :func:`fill_missing_frequency_params`.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Base helpers
# ---------------------------------------------------------------------------

class DenoiseOptions(BaseModel):
    """Base mixin that adds a dict-style ``get`` accessor."""

    def get(self, key: str, default: Any = None) -> Any:  # noqa: ANN401
        return getattr(self, key, default)


# ---------------------------------------------------------------------------
# Per-method parameter models
# ---------------------------------------------------------------------------

class MaskImpulseNoise(BaseModel):
    """Per-frequency impulse-noise parameters.

    Each frequency key maps to *either*:
    - a flat parameter dict (applies to all pulse modes), or
    - a dict with ``"short_pulse"`` and/or ``"long_pulse"`` sub-dicts, or
    - ``None`` to disable that frequency entirely.

    Supported keys inside a config dict::

        range_coord, vertical_bin_size, ping_lags, threshold (alias threshold_db),
        exclude_shallow_above, vote_k_of_n, post_dilate
    """

    frequencies: dict[str, dict | None] = Field(
        ...,
        description="Per-frequency parameters (Hz).  "
        "Value may be a flat dict, nested short_pulse/long_pulse, or null.",
    )


class TransientNoiseMask(DenoiseOptions):
    """Per-frequency transient-noise parameters.

    Supported keys::

        range_coord, ping_window, range_window, threshold, exclude_above, percentile
    """

    frequencies: dict[str, dict] = Field(
        ...,
        description="Per-frequency parameters (Hz).",
    )


class RemoveBackgroundNoise(BaseModel):
    """Per-frequency background-noise parameters.

    Supported keys::

        range_coord, range_window, ping_window, background_noise_max,
        SNR_threshold, sound_absorption, minimal_linear
    """

    frequencies: dict[str, dict] = Field(
        ...,
        description="Per-frequency parameters (Hz).",
    )


class MaskAttenuatedSignal(BaseModel):
    """Per-frequency attenuation-signal parameters.

    Supported keys::

        range_coord, upper_limit_sl, lower_limit_sl, num_side_pings, threshold
    """

    frequencies: dict[str, dict] = Field(
        ...,
        description="Per-frequency parameters (Hz).",
    )


# ---------------------------------------------------------------------------
# Regridding option models
# ---------------------------------------------------------------------------

class MVBSComputeOptions(BaseModel):
    """Parameters for MVBS computation."""

    range_bin: str = Field(default="1m", description="Vertical bin size")
    ping_time_bin: str = Field(default="5s", description="Temporal bin size")


class NASCComputeOptions(BaseModel):
    """Parameters for NASC computation."""

    range_bin: str = Field(default="10m", description="Vertical bin size")
    dist_bin: str = Field(default="0.5nmi", description="Horizontal bin size")


# ---------------------------------------------------------------------------
# Parameter inheritance helper
# ---------------------------------------------------------------------------


def _extract_raw_map(freq_params: Mapping[str, Any] | Any) -> dict:
    """Unwrap *freq_params* into a plain dict."""
    if hasattr(freq_params, "frequencies"):
        return dict(freq_params.frequencies)
    if hasattr(freq_params, "model_dump"):
        return freq_params.model_dump()
    return dict(freq_params)


def _normalise_map(raw_map: dict) -> dict[str, dict | None]:
    """Coerce keys to ``str`` and values to ``dict | None``."""
    norm: dict[str, dict | None] = {}
    for k, v in raw_map.items():
        sk = str(k)
        if v is None:
            norm[sk] = None
        elif isinstance(v, Mapping):
            norm[sk] = dict(v)
        else:
            norm[sk] = {}
    return norm


def _derive_base(tval: dict) -> dict:
    """Extract a flat base template from a possibly-nested template value."""
    if isinstance(tval, Mapping) and ("short_pulse" in tval or "long_pulse" in tval):
        if isinstance(tval.get("short_pulse"), Mapping):
            return deepcopy(tval["short_pulse"])
        if isinstance(tval.get("long_pulse"), Mapping):
            return deepcopy(tval["long_pulse"])
        return {}
    return deepcopy(tval)


def _merge_nested(fval: dict, base: dict) -> dict:
    """Merge a nested short_pulse/long_pulse freq entry against *base*."""
    sp_user = fval.get("short_pulse")
    lp_user = fval.get("long_pulse")

    # short_pulse
    if "short_pulse" in fval and sp_user is None:
        sp: dict | None = None
    else:
        sp = deepcopy(base)
        if isinstance(sp_user, Mapping):
            sp.update(dict(sp_user))

    # long_pulse
    if "long_pulse" in fval and lp_user is None:
        lp: dict | None = None
    else:
        inherit_base = sp if isinstance(sp, Mapping) else deepcopy(base)
        lp = deepcopy(inherit_base)
        if isinstance(lp_user, Mapping):
            lp.update(dict(lp_user))

    return {"short_pulse": sp, "long_pulse": lp}


def fill_missing_frequency_params(
    freq_params: Mapping[str, Any] | Any,
) -> dict[str, dict | None]:
    """Build a complete per-frequency param map with template inheritance.

    Rules:
    - ``None`` value → frequency disabled (preserved).
    - Template frequency (prefer ``"38000"``) supplies default values.
    - Flat dicts: ``base ⊕ user``.
    - Nested ``short_pulse`` / ``long_pulse``::

        short_pulse:  base ⊕ user.short_pulse   (None → disabled)
        long_pulse:   (short_pulse or base) ⊕ user.long_pulse

    Args:
        freq_params: A model instance with ``.frequencies``, a mapping, or
            any object with ``.model_dump()``.

    Returns:
        Fully resolved ``{freq_str: params | None}`` dictionary.
    """
    raw_map = _extract_raw_map(freq_params)
    if not raw_map:
        return {}

    norm = _normalise_map(raw_map)
    enabled_keys = [k for k, v in norm.items() if v is not None]
    if not enabled_keys:
        return norm

    tkey = "38000" if "38000" in enabled_keys else enabled_keys[0]
    base = _derive_base(deepcopy(norm[tkey]) or {})

    filled: dict[str, dict | None] = {}
    for fk, fval in norm.items():
        if fval is None:
            filled[fk] = None
        elif "short_pulse" in fval or "long_pulse" in fval:
            filled[fk] = _merge_nested(fval, base)
        else:
            merged = deepcopy(base)
            merged.update(fval)
            filled[fk] = merged

    return filled
