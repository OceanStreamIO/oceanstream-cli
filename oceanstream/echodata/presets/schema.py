"""Denoise parameter schema: every parameter a preset may set, with its type and bounds.

The table follows the Denoise Lab's ``param_schema.py`` field for field, but is not tied to
two frequencies: a preset declares whichever frequencies its survey recorded.

Two facts about the pipeline shape this module:

* ``DenoiseConfig.to_frequency_keyed_params`` merges ``FREQUENCY_PRESETS`` underneath a
  preset, so a key a preset leaves out silently takes a built-in value. Presets therefore
  list every required parameter, and :func:`validate_params` rejects one that does not.
* The batch pipeline (``process_from_raw.py``, which the Denoise Lab also runs) removes
  background noise with echopype's arithmetic. It reads only ``range_window``,
  ``ping_window``, ``SNR_threshold`` and ``background_noise_max``, and takes absorption
  from the Sv data. The other background parameters only reach
  ``background_noise_mask``, which the batch pipeline never calls. They are accepted, but
  flagged by :func:`inert_in_batch`, so that nobody tunes a parameter with no effect.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

#: Filter groups in the order the pipeline applies them.
GROUPS: tuple[str, ...] = ("impulse", "attenuation", "transient", "background")


class PresetError(ValueError):
    """A preset failed validation. The message names the offending field."""


@dataclass(frozen=True)
class FieldSpec:
    name: str
    group: str
    type: str
    unit: str = ""
    required: bool = True
    min: float | None = None
    max: float | None = None
    options: tuple[Any, ...] = ()
    #: False when the batch pipeline never reads the parameter.
    batch: bool = True
    help: str = ""

    @property
    def where(self) -> str:
        return f"{self.group}.{self.name}"


# Bounds follow the Denoise Lab, widened where ship surveys record deeper than a Saildrone.
FIELDS: tuple[FieldSpec, ...] = (
    # impulse
    FieldSpec("vertical_bin_size", "impulse", "float", "m", min=0.1, max=50.0,
              help="Vertical pooling window applied before the lag comparison."),
    FieldSpec("ping_lags", "impulse", "int_list", "pings", min=1,
              help="Lags compared either side of each ping."),
    FieldSpec("threshold_db", "impulse", "float", "dB", min=0.0, max=40.0,
              help="A sample must exceed both neighbours at the same lag by this much."),
    FieldSpec("exclude_shallow_above", "impulse", "float", "m", required=False,
              min=0.0, max=500.0, help="Ignore depths shallower than this."),
    FieldSpec("vote_k_of_n", "impulse", "int", required=False, min=1, max=5,
              help="How many lags must agree. Unset means any single lag is enough."),
    FieldSpec("post_dilate", "impulse", "dilate", required=False,
              help="Rectangular dilation of the mask after detection."),
    # attenuation
    FieldSpec("upper_limit_sl", "attenuation", "float", "m", min=0.0, max=6000.0,
              help="Top of the reference band. The band must contain scatterers."),
    FieldSpec("lower_limit_sl", "attenuation", "float", "m", min=0.0, max=6000.0,
              help="Bottom of the reference band."),
    FieldSpec("num_side_pings", "attenuation", "int", "pings", min=1, max=500,
              help="Pings each side of the running median."),
    FieldSpec("threshold", "attenuation", "float", "dB", min=0.0, max=40.0,
              help="How far the ping median must fall below the block median."),
    # transient
    FieldSpec("exclude_above", "transient", "float", "m", min=0.0, max=6000.0,
              help="The filter is not applied above this depth."),
    FieldSpec("depth_bin", "transient", "float", "m", min=0.5, max=200.0,
              help="Vertical step of the upward-stepping comparison."),
    FieldSpec("n_pings", "transient", "int", "pings", min=1, max=500,
              help="Pings each side of the running median."),
    FieldSpec("thr_dB", "transient", "float", "dB", min=0.0, max=40.0,
              help="Primary threshold; the secondary one is max(2, thr - 3)."),
    FieldSpec("maxts", "transient", "float", "dB", min=-100.0, max=0.0,
              help="Upper bound on what counts as transient rather than signal."),
    FieldSpec("ref_min", "transient", "float", "m", required=False, min=0.0, max=6000.0,
              help="Deep reference band top. Unset derives it from the data."),
    FieldSpec("ref_max", "transient", "float", "m", required=False, min=0.0, max=6000.0,
              help="Deep reference band bottom. Unset derives it from the data."),
    FieldSpec("detector", "transient", "enum", required=False, options=("fielding", "ryan"),
              help="Fielding's upward-stepping detector (default) or Ryan et al.'s (2015) "
                   "per-depth-bin comparison with neighbouring pings."),
    FieldSpec("percentile", "transient", "float", required=False, min=0.0, max=100.0,
              help="Ryan detector only: the percentile compared within each depth bin."),
    # background
    FieldSpec("range_window", "background", "int_auto", "samples", min=1, max=500,
              options=("auto",), help="Range samples per noise-estimate block."),
    FieldSpec("ping_window", "background", "int", "pings", min=1, max=1000,
              help="Pings per noise-estimate block."),
    FieldSpec("SNR_threshold", "background", "float", "dB", min=0.0, max=30.0,
              help="Samples below this signal-to-noise ratio are discarded."),
    FieldSpec("background_noise_max", "background", "float", "dB", min=-200.0, max=-50.0,
              help="Ceiling on the estimated noise level."),
    FieldSpec("sound_absorption", "background", "float", "dB/m", required=False,
              min=0.0, max=0.2, batch=False,
              help="Mask filter only; the batch pipeline takes absorption from the Sv data."),
    FieldSpec("depth_stat", "background", "enum", required=False,
              options=("quantile", "min"), batch=False,
              help="Mask filter only; the batch pipeline takes the minimum over range."),
    FieldSpec("depth_quantile", "background", "float", required=False, min=0.0, max=1.0,
              batch=False, help="Mask filter, and the batch pipeline's experimental quantile "
                                "background estimator (--background-estimator quantile)."),
    FieldSpec("guard_mode", "background", "enum", required=False,
              options=("above", "outside_band"), batch=False, help="Mask filter only."),
    FieldSpec("guard_depth", "background", "float", "m", required=False,
              min=0.0, max=6000.0, batch=False, help="Mask filter only."),
    FieldSpec("guard_band", "background", "range", "m", required=False,
              min=0.0, max=6000.0, batch=False, help="Mask filter only."),
)

FIELDS_BY_GROUP: dict[str, tuple[FieldSpec, ...]] = {
    group: tuple(f for f in FIELDS if f.group == group) for group in GROUPS
}

#: Keys older presets carry that no filter reads. They are dropped on load, with a note,
#: rather than rejected as typos. ``transient.percentile`` is read by the Ryan detector
#: only, so it is dropped the same way when the preset keeps Fielding's.
RETIRED: dict[str, frozenset[str]] = {
    "impulse": frozenset({"num_lags"}),
    "transient": frozenset({"a", "n"}),
    "background": frozenset({"num_side_pings", "minimal_linear"}),
}

#: The only parameters the filters parse as unit-suffixed strings ("5.0dB", "2m"). Every
#: other value must be written bare: the filters call ``float()`` on it.
UNIT_SUFFIXED: frozenset[tuple[str, str]] = frozenset(
    {
        ("background", "SNR_threshold"),
        ("background", "background_noise_max"),
        ("impulse", "vertical_bin_size"),
    }
)

#: One frequency's parameters: {group: {name: value}}, values normalised to plain numbers.
GroupParams = dict[str, dict[str, Any]]


def _strip_unit(value: Any, unit: str) -> Any:
    if not isinstance(value, str) or not unit:
        return value
    text = value.strip()
    if text.lower().endswith(unit.lower()):
        text = text[: -len(unit)]
    try:
        return float(text)
    except ValueError:
        return value


def _number(spec: FieldSpec, value: Any, where: str, *, integer: bool) -> float | int:
    if isinstance(value, bool):
        raise PresetError(f"{where} must be a number, got {value!r}.")
    try:
        number: float | int = int(value) if integer else float(value)
    except (TypeError, ValueError) as e:
        kind = "an integer" if integer else "a number"
        raise PresetError(f"{where} must be {kind}, got {value!r}.") from e
    if integer and float(value) != number:
        raise PresetError(f"{where} must be an integer, got {value!r}.")
    if spec.min is not None and number < spec.min:
        raise PresetError(f"{where} must be >= {spec.min}, got {number}.")
    if spec.max is not None and number > spec.max:
        raise PresetError(f"{where} must be <= {spec.max}, got {number}.")
    return number


def coerce(spec: FieldSpec, value: Any, where: str) -> Any:  # noqa: PLR0911, PLR0912 - type dispatch
    """Normalise one raw value to a plain Python value of the field's type."""
    value = _strip_unit(value, spec.unit)
    if spec.type == "float":
        return float(_number(spec, value, where, integer=False))
    if spec.type == "int":
        return int(_number(spec, value, where, integer=True))
    if spec.type == "int_auto":
        if isinstance(value, str) and value.strip().lower() == "auto":
            return "auto"
        return int(_number(spec, value, where, integer=True))
    if spec.type == "enum":
        if value not in spec.options:
            raise PresetError(f"{where} must be one of {list(spec.options)}, got {value!r}.")
        return value
    if spec.type == "int_list":
        if not isinstance(value, (list, tuple)) or not value:
            raise PresetError(f"{where} must be a non-empty list of integers.")
        return [int(_number(spec, v, where, integer=True)) for v in value]
    if spec.type == "range":
        if not isinstance(value, (list, tuple)) or len(value) != 2:  # noqa: PLR2004
            raise PresetError(f"{where} must be a two-element [min, max] list.")
        low, high = (float(_number(spec, v, where, integer=False)) for v in value)
        if low >= high:
            raise PresetError(f"{where} needs min < max, got [{low}, {high}].")
        return [low, high]
    if spec.type == "dilate":
        if not isinstance(value, dict) or set(value) - {"pings", "samples"}:
            raise PresetError(f"{where} must be a table with 'pings' and 'samples'.")
        return {k: int(_number(spec, v, where, integer=True)) for k, v in sorted(value.items())}
    raise PresetError(f"{where} has an unknown field type {spec.type!r}.")


def validate_frequency(freq: int, raw: Any) -> tuple[GroupParams, list[str]]:
    """Validate one frequency's parameter tables.

    Returns the normalised parameters and notes about dropped retired keys.

    Raises:
        PresetError: naming the frequency, filter and parameter that failed.
    """
    label = f"{freq} Hz"
    if not isinstance(raw, dict):
        raise PresetError(f"{label}: expected a table keyed by filter.")
    stray_groups = set(raw) - set(GROUPS)
    if stray_groups:
        raise PresetError(f"{label}: unknown filter(s) {sorted(stray_groups)}.")

    out: GroupParams = {}
    notes: list[str] = []
    for group in GROUPS:
        values = raw.get(group)
        if values is None:
            raise PresetError(
                f"{label}: no '{group}' table. Every frequency must list all four filters."
            )
        if not isinstance(values, dict):
            raise PresetError(f"{label} {group}: expected a table of parameters.")
        known = {spec.name for spec in FIELDS_BY_GROUP[group]}
        retired = set(values) & RETIRED.get(group, frozenset())
        stray = set(values) - known - retired
        if stray:
            raise PresetError(f"{label} {group}: unknown parameter(s) {sorted(stray)}.")
        notes += [f"{label} {group}.{name} is read by no filter and was dropped"
                  for name in sorted(retired)]

        resolved: dict[str, Any] = {}
        for spec in FIELDS_BY_GROUP[group]:
            where = f"{label} {spec.where}"
            if spec.name not in values or values[spec.name] in (None, ""):
                if spec.required:
                    raise PresetError(
                        f"{where} is missing. Presets list every required parameter, "
                        "because an omitted key silently takes a built-in value."
                    )
                continue
            resolved[spec.name] = coerce(spec, values[spec.name], where)
        out[group] = resolved

    transient = out["transient"]
    if "percentile" in transient and transient.get("detector") != "ryan":
        del transient["percentile"]
        notes.append(f"{label} transient.percentile is read only by the ryan detector and "
                     "was dropped")

    _check_cross_field(label, out)
    return out, notes


def _check_cross_field(label: str, groups: GroupParams) -> None:
    att = groups["attenuation"]
    if att["upper_limit_sl"] >= att["lower_limit_sl"]:
        raise PresetError(
            f"{label} attenuation: the reference band needs upper_limit_sl < lower_limit_sl, "
            f"got {att['upper_limit_sl']} and {att['lower_limit_sl']}."
        )

    imp = groups["impulse"]
    if "vote_k_of_n" in imp and imp["vote_k_of_n"] > len(imp["ping_lags"]):
        raise PresetError(
            f"{label} impulse: vote_k_of_n ({imp['vote_k_of_n']}) exceeds the number of "
            f"ping lags ({len(imp['ping_lags'])})."
        )

    tra = groups["transient"]
    if ("ref_min" in tra) != ("ref_max" in tra):
        raise PresetError(
            f"{label} transient: set both ref_min and ref_max, or neither to derive the "
            "reference band from the data."
        )
    if "ref_min" in tra and tra["ref_min"] >= tra["ref_max"]:
        raise PresetError(f"{label} transient: ref_min must be below ref_max.")

    bg = groups["background"]
    mode = bg.get("guard_mode")
    if mode == "above" and "guard_depth" not in bg:
        raise PresetError(f"{label} background: guard_mode 'above' needs guard_depth.")
    if mode == "outside_band" and "guard_band" not in bg:
        raise PresetError(f"{label} background: guard_mode 'outside_band' needs guard_band.")


def inert_in_batch(params: dict[int, GroupParams]) -> list[str]:
    """Parameters set in *params* that the batch pipeline never reads."""
    return [
        f"{freq} Hz {spec.where}"
        for freq, groups in sorted(params.items())
        for spec in FIELDS
        if not spec.batch and spec.name in groups[spec.group]
    ]


def to_pipeline_value(group: str, name: str, value: Any) -> Any:
    """The value as the filters expect it: unit-suffixed only where they parse a suffix."""
    if (group, name) not in UNIT_SUFFIXED or not isinstance(value, (int, float)):
        return value
    if name == "vertical_bin_size":
        number = float(value)
        return f"{int(number)}m" if number.is_integer() else f"{number}m"
    return f"{float(value)}dB"
