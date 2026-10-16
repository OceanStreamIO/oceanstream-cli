"""Load, check and choose denoise presets.

A preset file is an ordinary denoise config TOML (``[echodata.denoise]`` and one
``[echodata.denoise.frequency_params.<Hz>]`` table per frequency), so the pipeline, the HPC
scripts and the Denoise Lab all read it as they read any config. A ``[preset]`` table on
top records what the parameters were tuned for and which survey days they were checked
on. Unknown keys in ``[preset]`` and in the parameter tables are errors, not ignored.
"""

from __future__ import annotations

import hashlib
import json
import re
import tomllib
from collections.abc import Iterable
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import Any

from oceanstream.echodata.config import DenoiseConfig

from .schema import (
    GROUPS,
    GroupParams,
    PresetError,
    inert_in_batch,
    to_pipeline_value,
    validate_frequency,
)

LIBRARY_DIR = Path(__file__).resolve().parent / "library"

STATUSES = ("reference", "draft", "experimental", "production", "deprecated")
PLATFORMS = ("usv", "research-vessel", "fishing-vessel", "buoy", "mooring", "glider", "auv")
REGIMES = ("fjord", "coastal", "shelf", "slope", "open-ocean")
OUTCOMES = ("adopted", "rejected", "pending")
PULSE_MODES = ("CW", "FM", "")

KEY_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
DAYS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}(?:/\d{4}-\d{2}-\d{2})?$")


@dataclass(frozen=True)
class AppliesTo:
    """The conditions a preset was tuned for. Empty means unspecified."""

    platforms: tuple[str, ...] = ()
    regimes: tuple[str, ...] = ()
    water_depth_m: tuple[float, float] | None = None
    pulse_mode: str = ""
    echosounders: tuple[str, ...] = ()
    region: str = ""
    seasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class Validation:
    """One check of a preset against real survey days."""

    cruise: str
    days: tuple[str, ...]
    outcome: str
    notes: str = ""
    reference: str = ""


@dataclass(frozen=True)
class Preset:
    key: str
    name: str
    status: str
    summary: str
    description: str
    derived_from: str
    applies_to: AppliesTo
    validations: tuple[Validation, ...]
    methods: tuple[str, ...]
    #: {Hz: {filter: {parameter: value}}}, values as plain numbers.
    params: dict[int, GroupParams]
    source: Path | None = None
    #: What the loader dropped or would warn about; empty for a clean file.
    notes: tuple[str, ...] = field(default=())

    @property
    def frequencies(self) -> tuple[int, ...]:
        return tuple(sorted(self.params))

    @property
    def frequencies_khz(self) -> tuple[float, ...]:
        return tuple(_khz(f) for f in self.frequencies)

    @property
    def adopted_on(self) -> tuple[Validation, ...]:
        return tuple(v for v in self.validations if v.outcome == "adopted")

    @property
    def params_sha256(self) -> str:
        """Hash of the methods and parameters only, so comments and metadata do not move it."""
        canonical = json.dumps(
            {"methods": list(self.methods),
             "params": {str(f): self.params[f] for f in self.frequencies}},
            sort_keys=True, separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode()).hexdigest()

    def frequency_params(self) -> dict[int, dict[str, dict[str, Any]]]:
        """Parameters in the form ``DenoiseConfig.frequency_params`` holds."""
        return {
            freq: {
                group: {name: to_pipeline_value(group, name, value)
                        for name, value in self.params[freq][group].items()}
                for group in GROUPS
            }
            for freq in self.frequencies
        }

    def to_denoise_config(self) -> DenoiseConfig:
        return DenoiseConfig(
            methods=list(self.methods),
            use_frequency_specific=True,
            frequency_params=self.frequency_params(),
        )

    def uncovered(self, channel_frequencies: Iterable[float]) -> list[int]:
        """Channel frequencies (Hz) this preset has no parameters for."""
        return uncovered_frequencies(self.params, channel_frequencies)


@dataclass(frozen=True)
class Match:
    preset: Preset
    score: float
    missing_khz: tuple[float, ...]
    reasons: tuple[str, ...]


def _khz(hz: int) -> float:
    khz = hz / 1000
    return int(khz) if khz.is_integer() else khz


def _to_hz(value: float) -> int:
    """Accept kHz (below 1000) or Hz."""
    return int(round(value * 1000)) if value < 1000 else int(round(value))  # noqa: PLR2004


def uncovered_frequencies(
    frequency_params: dict[Any, Any] | Iterable[Any],
    channel_frequencies: Iterable[float],
) -> list[int]:
    """Channel frequencies (Hz) with no entry in *frequency_params*.

    The pipeline does not fall back to another frequency: a channel without parameters is
    left out of every mask filter with only a log warning. Callers use this to stop first.
    """
    covered = {int(round(float(k))) for k in frequency_params}
    present = {int(round(float(f))) for f in channel_frequencies}
    return sorted(present - covered)


# -- loading --------------------------------------------------------------------------


def _tuple_of(value: Any, where: str, allowed: tuple[str, ...] | None = None) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str) or not isinstance(value, list):
        raise PresetError(f"{where} must be a list of strings.")
    if allowed is not None:
        bad = [v for v in value if v not in allowed]
        if bad:
            raise PresetError(f"{where} has unknown value(s) {bad}; allowed: {list(allowed)}.")
    return tuple(str(v) for v in value)


def _expect_keys(table: dict[str, Any], allowed: set[str], where: str) -> None:
    stray = set(table) - allowed
    if stray:
        raise PresetError(f"{where}: unknown key(s) {sorted(stray)}.")


def _applies_to(raw: dict[str, Any], where: str) -> AppliesTo:
    _expect_keys(raw, {"platforms", "regimes", "water_depth_m", "pulse_mode",
                       "echosounders", "region", "seasons"}, where)
    depth = raw.get("water_depth_m")
    if depth is not None:
        if not isinstance(depth, list) or len(depth) != 2 or float(depth[0]) > float(depth[1]):  # noqa: PLR2004
            raise PresetError(f"{where}.water_depth_m must be [shallowest, deepest].")
        depth = (float(depth[0]), float(depth[1]))
    pulse = raw.get("pulse_mode", "")
    if pulse not in PULSE_MODES:
        raise PresetError(f"{where}.pulse_mode must be one of {list(PULSE_MODES)}.")
    return AppliesTo(
        platforms=_tuple_of(raw.get("platforms"), f"{where}.platforms", PLATFORMS),
        regimes=_tuple_of(raw.get("regimes"), f"{where}.regimes", REGIMES),
        water_depth_m=depth,
        pulse_mode=pulse,
        echosounders=_tuple_of(raw.get("echosounders"), f"{where}.echosounders"),
        region=str(raw.get("region", "")),
        seasons=_tuple_of(raw.get("seasons"), f"{where}.seasons"),
    )


def _validations(raw: Any, where: str) -> tuple[Validation, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise PresetError(f"{where} must be an array of tables ([[preset.validations]]).")
    out = []
    for i, entry in enumerate(raw):
        here = f"{where}[{i}]"
        _expect_keys(entry, {"cruise", "days", "outcome", "notes", "reference"}, here)
        if not entry.get("cruise"):
            raise PresetError(f"{here}.cruise is required.")
        days = _tuple_of(entry.get("days", []), f"{here}.days")
        bad = [d for d in days if not DAYS_RE.match(d)]
        if bad:
            raise PresetError(
                f"{here}.days must be YYYY-MM-DD or YYYY-MM-DD/YYYY-MM-DD, got {bad}."
            )
        outcome = entry.get("outcome", "")
        if outcome not in OUTCOMES:
            raise PresetError(f"{here}.outcome must be one of {list(OUTCOMES)}.")
        out.append(Validation(cruise=str(entry["cruise"]), days=days, outcome=outcome,
                              notes=str(entry.get("notes", "")).strip(),
                              reference=str(entry.get("reference", ""))))
    return tuple(out)


def parse_preset(text: str, *, source: Path | None = None, key: str = "") -> Preset:
    """Parse and validate preset TOML text.

    A plain denoise config without a ``[preset]`` table loads too, as an ``external``
    preset keyed by *key* (or the file stem), so any TOML a run could use can be checked.

    Raises:
        PresetError: naming the file and the field that failed.
    """
    label = str(source) if source else (key or "<text>")
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        raise PresetError(f"{label}: not valid TOML ({e}).") from e

    try:
        return _build(data, source=source, key=key)
    except PresetError as e:
        raise PresetError(f"{label}: {e}") from e


def _build(data: dict[str, Any], *, source: Path | None, key: str) -> Preset:
    denoise = data.get("echodata", {}).get("denoise", {})
    raw_params = denoise.get("frequency_params")
    if not raw_params:
        raise PresetError("no [echodata.denoise.frequency_params.<Hz>] tables.")

    params: dict[int, GroupParams] = {}
    notes: list[str] = []
    for raw_freq, groups in raw_params.items():
        try:
            freq = int(raw_freq)
        except ValueError as e:
            raise PresetError(f"frequency key {raw_freq!r} is not a number in Hz.") from e
        params[freq], dropped = validate_frequency(freq, groups)
        notes += dropped
    notes += [f"{p} is ignored by the batch pipeline" for p in inert_in_batch(params)]

    methods = tuple(denoise.get("methods", GROUPS))
    unknown = [m for m in methods if m not in GROUPS]
    if unknown:
        raise PresetError(f"unknown denoise method(s) {unknown}.")

    meta = data.get("preset")
    stem = source.stem if source else ""
    if meta is None:
        return Preset(key=key or stem, name=key or stem, status="external", summary="",
                      description="", derived_from="", applies_to=AppliesTo(),
                      validations=(), methods=methods, params=params, source=source,
                      notes=tuple(notes))

    _expect_keys(meta, {"key", "name", "status", "summary", "description", "derived_from",
                        "applies_to", "validations"}, "[preset]")
    preset_key = str(meta.get("key", ""))
    if not KEY_RE.match(preset_key):
        raise PresetError(f"[preset].key {preset_key!r} must be lower-case and hyphenated.")
    if stem and stem != preset_key:
        raise PresetError(f"[preset].key {preset_key!r} does not match the file name {stem!r}.")
    status = meta.get("status", "")
    if status not in STATUSES:
        raise PresetError(f"[preset].status must be one of {list(STATUSES)}.")
    for required in ("name", "summary"):
        if not meta.get(required):
            raise PresetError(f"[preset].{required} is required.")

    return Preset(
        key=preset_key,
        name=str(meta["name"]),
        status=status,
        summary=str(meta["summary"]).strip(),
        description=str(meta.get("description", "")).strip(),
        derived_from=str(meta.get("derived_from", "")),
        applies_to=_applies_to(meta.get("applies_to", {}), "[preset.applies_to]"),
        validations=_validations(meta.get("validations"), "[[preset.validations]]"),
        methods=methods,
        params=params,
        source=source,
        notes=tuple(notes),
    )


def load_preset(path: Path) -> Preset:
    path = Path(path)
    if not path.is_file():
        raise PresetError(f"No preset file at {path}.")
    return parse_preset(path.read_text(encoding="utf-8"), source=path)


@cache
def _library() -> dict[str, Preset]:
    presets = {}
    for path in sorted(LIBRARY_DIR.glob("*.toml")):
        preset = load_preset(path)
        presets[preset.key] = preset
    for preset in presets.values():
        if preset.derived_from and preset.derived_from not in presets:
            raise PresetError(
                f"{preset.source}: derived_from {preset.derived_from!r} is not in the library."
            )
    return presets


def list_presets(*, include_deprecated: bool = True) -> list[Preset]:
    return [p for p in _library().values() if include_deprecated or p.status != "deprecated"]


def get_preset(key: str) -> Preset:
    try:
        return _library()[key]
    except KeyError:
        known = ", ".join(sorted(_library()))
        raise PresetError(f"No preset {key!r} in the library. Known presets: {known}.") from None


def preset_path(key: str) -> Path:
    """The library file for *key*, for tools that take a ``--denoise-config`` path."""
    preset = get_preset(key)
    assert preset.source is not None
    return preset.source


def diff(base: Preset, other: Preset) -> list[str]:
    """What *other* changes against *base*, one line per parameter or frequency."""
    lines = []
    for freq in sorted(set(base.params) | set(other.params)):
        label = f"{_khz(freq)} kHz"
        if freq not in other.params:
            lines.append(f"{label}: only in {base.key}")
            continue
        if freq not in base.params:
            lines.append(f"{label}: only in {other.key}")
            continue
        for group in GROUPS:
            old, new = base.params[freq][group], other.params[freq][group]
            for name in sorted(set(old) | set(new)):
                if old.get(name) != new.get(name):
                    lines.append(f"{label} {group}.{name}: {old.get(name, 'unset')} -> "
                                 f"{new.get(name, 'unset')}")
    if base.methods != other.methods:
        lines.append(f"methods: {list(base.methods)} -> {list(other.methods)}")
    return lines


# -- choosing ---------------------------------------------------------------------------


def match_presets(  # noqa: PLR0912, PLR0913 - one keyword and branch per survey property
    frequencies: Iterable[float],
    *,
    platform: str = "",
    regime: str = "",
    water_depth_m: tuple[float, float] | None = None,
    pulse_mode: str = "",
    include_drafts: bool = True,
) -> list[Match]:
    """Rank library presets as starting points for a survey.

    Out of 100: frequency coverage 40, platform 25, regime 20, water depth overlap 10,
    pulse mode 5. A preset adopted on real survey days gets a 5-point bonus, capped at 100.
    The score only orders candidates. It does not replace a check on the survey's own data.
    """
    wanted = sorted({_to_hz(f) for f in frequencies})
    if not wanted:
        raise PresetError("Give at least one frequency.")
    if platform and platform not in PLATFORMS:
        raise PresetError(f"platform must be one of {list(PLATFORMS)}.")
    if regime and regime not in REGIMES:
        raise PresetError(f"regime must be one of {list(REGIMES)}.")

    results = []
    for preset in list_presets(include_deprecated=False):
        if preset.status == "draft" and not include_drafts:
            continue
        reasons: list[str] = []
        missing = [f for f in wanted if f not in preset.params]
        score = 40 * (len(wanted) - len(missing)) / len(wanted)
        reasons.append(f"covers {len(wanted) - len(missing)} of {len(wanted)} frequencies")

        applies = preset.applies_to
        if platform:
            if platform in applies.platforms:
                score += 25
                reasons.append(f"tuned on a {platform}")
            elif not applies.platforms:
                score += 10
                reasons.append("no platform restriction")
            else:
                reasons.append(f"tuned on {', '.join(applies.platforms)}, not {platform}")
        if regime:
            if regime in applies.regimes:
                score += 20
                reasons.append(f"tuned for {regime}")
            else:
                reasons.append(f"tuned for {', '.join(applies.regimes) or 'no stated regime'}")
        if water_depth_m and applies.water_depth_m:
            overlap = _overlap(water_depth_m, applies.water_depth_m)
            score += 10 * overlap
            reasons.append(f"water depth overlap {overlap:.0%}")
        if pulse_mode and applies.pulse_mode:
            if pulse_mode == applies.pulse_mode:
                score += 5
            else:
                reasons.append(f"tuned on {applies.pulse_mode} pulses")
        if preset.adopted_on:
            score += 5
            reasons.append("adopted on " + ", ".join(v.cruise for v in preset.adopted_on))
        if preset.status == "draft":
            reasons.append("draft: not yet checked on survey data")

        results.append(Match(preset=preset, score=round(min(score, 100.0), 1),
                             missing_khz=tuple(_khz(f) for f in missing),
                             reasons=tuple(reasons)))
    return sorted(results, key=lambda m: (-m.score, m.preset.key))


def _overlap(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Share of range *a* that lies inside range *b*."""
    width = a[1] - a[0]
    inside = max(0.0, min(a[1], b[1]) - max(a[0], b[0]))
    if width <= 0:
        return 1.0 if b[0] <= a[0] <= b[1] else 0.0
    return inside / width
