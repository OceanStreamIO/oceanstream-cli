"""Library of denoise presets, each tuned for a kind of survey and checked on real days.

The preset files live in ``library/``. See ``docs/denoise-presets.md`` for the file format,
the statuses and how a preset is tuned and promoted.

    from oceanstream.echodata.presets import get_preset, match_presets

    preset = get_preset("tpos-sd-2023")
    config = preset.to_denoise_config()
    for match in match_presets([18, 38, 70, 120, 200], platform="research-vessel"):
        print(match.preset.key, match.score, match.missing_khz)
"""

from __future__ import annotations

from .registry import (
    LIBRARY_DIR,
    AppliesTo,
    Match,
    Preset,
    Validation,
    diff,
    get_preset,
    list_presets,
    load_preset,
    match_presets,
    parse_preset,
    preset_path,
    uncovered_frequencies,
)
from .schema import PresetError

__all__ = [
    "LIBRARY_DIR",
    "AppliesTo",
    "Match",
    "Preset",
    "PresetError",
    "Validation",
    "diff",
    "get_preset",
    "list_presets",
    "load_preset",
    "match_presets",
    "parse_preset",
    "preset_path",
    "uncovered_frequencies",
]
