"""``oceanstream process echodata presets``: browse, choose and check denoise presets."""

from __future__ import annotations

from pathlib import Path
from typing import NoReturn

import typer

from .registry import (
    PLATFORMS,
    REGIMES,
    diff,
    get_preset,
    list_presets,
    load_preset,
    match_presets,
    uncovered_frequencies,
)
from .schema import GROUPS, PresetError

presets_app = typer.Typer(
    help="Browse, choose and check the denoise preset library.",
    no_args_is_help=True,
)


def _fail(message: str) -> NoReturn:
    typer.echo(f"Error: {message}", err=True)
    raise typer.Exit(1)


def _khz_list(text: str) -> list[float]:
    try:
        return [float(v) for v in text.replace(" ", "").split(",") if v]
    except ValueError:
        _fail(f"--frequencies must be comma-separated numbers in kHz, got {text!r}.")


@presets_app.command("list", help="List the presets in the library.")
def list_command(
    show_all: bool = typer.Option(False, "--all", help="Include deprecated presets."),
) -> None:
    for preset in list_presets(include_deprecated=show_all):
        khz = ", ".join(str(f) for f in preset.frequencies_khz)
        adopted = ", ".join(v.cruise for v in preset.adopted_on) or "-"
        typer.echo(f"{preset.key}  [{preset.status}]  {khz} kHz  adopted on: {adopted}")
        typer.echo(f"    {preset.summary}")


@presets_app.command("show", help="Show a preset's conditions, validations and parameters.")
def show_command(  # noqa: PLR0912 - one branch per optional section
    key: str = typer.Argument(..., help="Preset key."),
    toml: bool = typer.Option(False, "--toml", help="Print the preset file instead."),
) -> None:
    try:
        preset = get_preset(key)
    except PresetError as e:
        _fail(str(e))
    if toml:
        assert preset.source is not None
        typer.echo(preset.source.read_text(encoding="utf-8"), nl=False)
        return

    a = preset.applies_to
    depth = f"{a.water_depth_m[0]:g}-{a.water_depth_m[1]:g} m" if a.water_depth_m else ""
    typer.echo(f"{preset.name} ({preset.key}), {preset.status}")
    typer.echo(preset.summary)
    typer.echo("")
    for label, value in (
        ("Platforms", ", ".join(a.platforms)),
        ("Regimes", ", ".join(a.regimes)),
        ("Water depth", depth),
        ("Pulse", a.pulse_mode),
        ("Echosounders", ", ".join(a.echosounders)),
        ("Region", a.region),
        ("Seasons", ", ".join(a.seasons)),
        ("Derived from", preset.derived_from),
        ("Windows tuned at", f"{preset.reference_ping_interval_s:g} s ping interval"
         if preset.reference_ping_interval_s else ""),
        ("Sessions split at", f"{preset.session_gap_minutes:g} min gaps"
         if preset.session_gap_minutes else ""),
        ("Params SHA-256", preset.params_sha256),
    ):
        if value:
            typer.echo(f"{label + ':':19s}{value}")
    if preset.description:
        typer.echo("")
        typer.echo(preset.description)
    for v in preset.validations:
        typer.echo("")
        typer.echo(f"Validation: {v.cruise} {', '.join(v.days) or '(no days yet)'}: {v.outcome}")
        if v.notes:
            typer.echo(f"    {v.notes}")
        if v.reference:
            typer.echo(f"    {v.reference}")
    if preset.derived_from:
        changes = diff(get_preset(preset.derived_from), preset)
        typer.echo("")
        typer.echo(f"Changes against {preset.derived_from}: {len(changes) or 'none'}")
        for line in changes:
            typer.echo(f"    {line}")
    typer.echo("")
    for freq in preset.frequencies:
        typer.echo(f"{freq / 1000:g} kHz")
        for group in GROUPS:
            values = ", ".join(f"{k}={v}" for k, v in preset.params[freq][group].items())
            typer.echo(f"    {group:12s}{values}")
    for note in preset.notes:
        typer.echo(f"Note: {note}")


@presets_app.command("match", help="Rank presets as starting points for a survey.")
def match_command(  # noqa: PLR0913, PLR0917 - one parameter per option
    frequencies: str = typer.Option(..., "--frequencies", "-f",
                                    help="Channel frequencies in kHz, e.g. 18,38,70,120,200."),
    platform: str = typer.Option("", help=f"One of: {', '.join(PLATFORMS)}."),
    regime: str = typer.Option("", help=f"One of: {', '.join(REGIMES)}."),
    depth: str = typer.Option("", help="Water depth range in metres, e.g. 1000-5500."),
    pulse: str = typer.Option("", help="CW or FM."),
    no_drafts: bool = typer.Option(False, "--no-drafts", help="Leave draft presets out."),
) -> None:
    depth_range = None
    if depth:
        try:
            low, high = (float(v) for v in depth.split("-", 1))
            depth_range = (low, high)
        except ValueError:
            _fail(f"--depth must look like 1000-5500, got {depth!r}.")
    try:
        matches = match_presets(_khz_list(frequencies), platform=platform, regime=regime,
                                water_depth_m=depth_range, pulse_mode=pulse,
                                include_drafts=not no_drafts)
    except PresetError as e:
        _fail(str(e))
    for m in matches:
        missing = f"  NO PARAMETERS FOR {', '.join(f'{f:g}' for f in m.missing_khz)} kHz" \
            if m.missing_khz else ""
        typer.echo(f"{m.score:5.1f}  {m.preset.key} [{m.preset.status}]{missing}")
        typer.echo(f"       {'; '.join(m.reasons)}")


@presets_app.command(
    "check",
    help="Validate a preset key or TOML file, and optionally its coverage of a survey's channels.",
)
def check_command(
    target: str = typer.Argument(..., help="Library key or path to a denoise TOML."),
    frequencies: str = typer.Option("", "--frequencies", "-f", help="Channel frequencies in kHz."),
    sv: Path = typer.Option(None, "--sv", exists=True,
                            help="Sv Zarr store whose channels the preset must cover."),
) -> None:
    try:
        path = Path(target)
        preset = load_preset(path) if path.suffix == ".toml" else get_preset(target)
    except PresetError as e:
        _fail(str(e))

    channels: list[float] = []
    if frequencies:
        channels = [f * 1000 if f < 1000 else f for f in _khz_list(frequencies)]  # noqa: PLR2004
    if sv is not None:
        import xarray as xr  # noqa: PLC0415 - only needed for --sv

        ds = xr.open_zarr(sv)
        channels += [float(f) for f in ds["frequency_nominal"].values.ravel()]

    typer.echo(f"{preset.key}: valid, {len(preset.frequencies)} frequencies "
               f"({', '.join(f'{f:g}' for f in preset.frequencies_khz)} kHz)")
    for note in preset.notes:
        typer.echo(f"Note: {note}")
    if channels:
        missing = uncovered_frequencies(preset.params, channels)
        if missing:
            _fail(f"no parameters for {', '.join(f'{f / 1000:g}' for f in missing)} kHz. "
                  "The pipeline would leave those channels un-denoised.")
        typer.echo("Covers every channel.")


@presets_app.command("path", help="Print a library preset's file path, for --denoise-config.")
def path_command(key: str = typer.Argument(..., help="Preset key.")) -> None:
    try:
        preset = get_preset(key)
    except PresetError as e:
        _fail(str(e))
    typer.echo(str(preset.source))
