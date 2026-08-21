#!/usr/bin/env python3
"""Render a human-readable ``parameters.txt`` from a run's ``run-manifest.json``.

The manifest already records everything that determines what a run produced —
resolved (post-TOML, post-inheritance) denoise parameters, the CLI, the code
revision and the package versions. This turns it into something readable
without a JSON viewer, and inlines the preset TOML so the folder is
self-contained.

    python write_run_parameters.py --run-root .../local-raw-10oct-echo-ryan
    python write_run_parameters.py --run-root DIR1 --run-root DIR2
"""

from __future__ import annotations

import argparse
import json
import shlex
import sys
from pathlib import Path

FLAG_VALUE_WIDTH = 24


def _kv(label: str, value: object) -> str:
    return f"{label:<{FLAG_VALUE_WIDTH}}: {value}"


def _rule(title: str) -> str:
    return f"\n{title}\n{'-' * len(title)}"


def _render_command(cli_args: list[str]) -> str:
    """Re-render sys.argv as a multi-line shell command, one flag per line."""
    lines = ["python process_from_raw.py"]
    i = 0
    while i < len(cli_args):
        token = cli_args[i]
        if token.startswith("--") and i + 1 < len(cli_args) and not cli_args[i + 1].startswith("--"):
            lines.append(f"  {token} {shlex.quote(cli_args[i + 1])}")
            i += 2
        else:
            lines.append(f"  {token}")
            i += 1
    return " \\\n".join(lines)


def _flag_value(cli_args: list[str], flag: str, default: str = "") -> str:
    if flag in cli_args:
        idx = cli_args.index(flag)
        if idx + 1 < len(cli_args) and not cli_args[idx + 1].startswith("--"):
            return cli_args[idx + 1]
    return default


def _render_denoise(denoise: dict) -> list[str]:
    out = [
        _kv("Enabled", denoise.get("enabled")),
        _kv("Methods", ", ".join(denoise.get("methods", []))),
        _kv("Per-frequency dispatch", denoise.get("use_frequency_specific")),
    ]
    freq_params = denoise.get("frequency_params") or {}
    if not freq_params:
        out.append(_kv("Global params", json.dumps(denoise.get("params", {}))))
        return out
    for freq in sorted(freq_params, key=lambda f: int(f)):
        out.append(f"\n  {int(freq) / 1000:g} kHz")
        for stage, params in freq_params[freq].items():
            rendered = ", ".join(f"{k}={v}" for k, v in params.items())
            out.append(f"    {stage:<12}: {rendered}")
    return out


def _render_artifacts(artifacts: dict) -> list[str]:
    out = []
    for kind in sorted(artifacts):
        for day in sorted(artifacts[kind]):
            for category, path in sorted(artifacts[kind][day].items()):
                out.append(f"  {kind:<10} {day}  {category:<12} {path}")
    return out


def build_parameters_text(manifest: dict, run_root: Path) -> str:
    cli_args = manifest.get("cli_args", [])
    cfg = manifest.get("resolved_config", {})
    code = manifest.get("code", {})
    env = manifest.get("environment", {})

    lines: list[str] = [
        "OceanStream run parameters",
        "==========================",
        "",
        _kv("Preset", manifest.get("preset") or "(none)"),
        _kv("Output container", manifest.get("output_container")),
        _kv("Sv source container", f"{manifest.get('sv_source_container')} (read-only)"),
        _kv("Cruise", cfg.get("cruise_id")),
        _kv("Days", ", ".join(manifest.get("days", []))),
        _kv("Categories", ", ".join(manifest.get("categories", []))),
        _kv("Created (UTC)", manifest.get("created_utc")),
    ]

    lines.append(_rule("Resolved command"))
    lines.append(_render_command(cli_args))

    colormaps = _flag_value(cli_args, "--colormaps") or _flag_value(
        cli_args, "--colormap", "ocean_r"
    )
    lines.append(_rule("Echograms"))
    lines += [
        _kv("Colormaps", colormaps.replace(",", ", ")),
        _kv("Stages rendered", "raw (source Sv), denoised, pruned, mvbs, combined-38kHz, nasc"),
        _kv("QC overlay file", _flag_value(cli_args, "--qc-file") or "(none)"),
    ]

    lines.append(_rule("Pipeline"))
    lines += [
        _kv("Resume stage", manifest.get("resume_stage")),
        _kv("Stop after stage", manifest.get("stop_after_stage")),
        _kv("Strict mode", manifest.get("strict")),
        _kv("Parallel stage workers", cfg.get("parallel_workers")),
        _kv("Dask workers", _flag_value(cli_args, "--n-workers")),
        _kv("Dask memory limit", _flag_value(cli_args, "--memory-limit")),
        _kv("Chunks", ", ".join(f"{k}={v}" for k, v in (cfg.get("chunks") or {}).items())),
        _kv("Surface exclusion", f"{cfg.get('surface_exclusion_depth')} m"),
        _kv("Seabed mask", cfg.get("apply_seabed_mask")),
        _kv("Sv sanity clip", f"{_flag_value(cli_args, '--sv-clip-max-db')} dB"),
        _kv("Denoise diagnostics", "--emit-denoise-diagnostics" in cli_args),
    ]

    lines.append(_rule("Denoise (stage 5)"))
    lines += [
        _kv("Config TOML", manifest.get("preset_toml") or "(built-in defaults)"),
        _kv("TOML sha256", manifest.get("preset_toml_sha256") or "-"),
    ]
    lines += _render_denoise(cfg.get("denoise") or {})

    prune = cfg.get("prune") or {}
    lines.append(_rule("Pruning (stage 6b)"))
    lines += [
        _kv("Enabled", prune.get("enabled")),
        _kv("NaN drop threshold", prune.get("drop_threshold")),
        _kv("Cross-talk detector", prune.get("crosstalk_enabled")),
        _kv(
            "Cross-talk ref band",
            f"{prune.get('crosstalk_ref_depth_min')}-{prune.get('crosstalk_ref_depth_max')} m",
        ),
        _kv("Cross-talk threshold", f"{prune.get('crosstalk_threshold_db')} dB"),
    ]

    lines.append(_rule("MVBS (stage 7) / NASC (stage 8)"))
    mvbs, nasc = cfg.get("mvbs") or {}, cfg.get("nasc") or {}
    lines += [
        _kv("MVBS range bin", mvbs.get("range_bin")),
        _kv("MVBS ping-time bin", mvbs.get("ping_time_bin")),
        _kv("NASC range bin", nasc.get("range_bin")),
        _kv("NASC distance bin", nasc.get("dist_bin")),
    ]

    lines.append(_rule("Products"))
    lines += _render_artifacts(manifest.get("artifacts") or {})

    lines.append(_rule("Environment"))
    dirty = " (uncommitted changes present)" if code.get("dirty") else ""
    lines += [
        _kv("Python", f"{env.get('python')} on {env.get('platform')}"),
        _kv("Code revision", f"{code.get('branch')} @ {str(code.get('head'))[:12]}{dirty}"),
    ]
    for name, version in (env.get("packages") or {}).items():
        lines.append(f"  {name:<12} {version}")

    toml_path = manifest.get("preset_toml")
    if toml_path and Path(toml_path).is_file():
        lines.append(_rule(f"Preset TOML verbatim ({Path(toml_path).name})"))
        lines.append(Path(toml_path).read_text(encoding="utf-8").rstrip())

    lines.append("")
    lines.append(f"Generated from {run_root.name}/run-manifest.json")
    return "\n".join(lines) + "\n"


def write_for_run(run_root: Path) -> Path:
    manifest_path = run_root / "run-manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(
            f"{manifest_path} not found — the run did not complete, so its "
            "parameters were never validated."
        )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    out_path = run_root / "parameters.txt"
    out_path.write_text(build_parameters_text(manifest, run_root), encoding="utf-8")

    toml_path = manifest.get("preset_toml")
    if toml_path and Path(toml_path).is_file():
        copy = run_root / "denoise-config.toml"
        copy.write_text(Path(toml_path).read_text(encoding="utf-8"), encoding="utf-8")
    return out_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--run-root", action="append", required=True, type=Path,
        help="Output container directory holding run-manifest.json (repeatable).",
    )
    args = parser.parse_args(argv)

    failures = 0
    for run_root in args.run_root:
        try:
            path = write_for_run(run_root)
        except (FileNotFoundError, json.JSONDecodeError) as exc:
            print(f"SKIP {run_root}: {exc}", file=sys.stderr)
            failures += 1
            continue
        print(f"Wrote {path}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
