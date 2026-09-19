"""Run outside the checkout after installing the built wheel with coastal extras."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from typer.testing import CliRunner

import oceanstream.coastal
from oceanstream.cli import app
from oceanstream.coastal.provenance import resolve_run
from oceanstream.coastal.quickstart import run

repo = Path(__file__).resolve().parents[2]
assert not Path(oceanstream.coastal.__file__).resolve().is_relative_to(repo), (
    "Imported checkout instead of wheel"
)
runner = CliRunner()
with tempfile.TemporaryDirectory(prefix="coastal-wheel-") as temporary:
    root = Path(temporary)
    result = run(root)
    assert result.success and not result.accepted, result.message
    inputs = result.output_dir.parents[2] / "inputs"
    assert inputs.is_dir(), inputs
    aoi = inputs / "aoi.json"
    assert (Path(oceanstream.coastal.__file__).parent / "aois" / "sesimbra.json").is_file()
    commands = [
        (["aoi", "example", "--describe", str(aoi)], 0),
        (
            [
                "detect",
                "--aoi",
                str(aoi),
                "--scene",
                str(inputs / "acolite"),
                "-o",
                str(root / "detect"),
            ],
            0,
        ),
        (
            [
                "attenuation",
                "--acolite-dir",
                str(inputs / "acolite"),
                "--aoi",
                str(aoi),
                "--bathymetry",
                str(inputs / "bathymetry.tif"),
                "-o",
                str(root / "attenuation"),
            ],
            0,
        ),
        (
            [
                "detectability",
                "--attenuation",
                result.products["attenuation"],
                "-o",
                str(root / "detection"),
            ],
            0,
        ),
        (["qc", "--run-dir", str(result.output_dir)], 3),
    ]
    for command, expected in commands:
        outcome = runner.invoke(app, ["process", "coastal", *command])
        assert outcome.exit_code == expected, (
            command,
            outcome.exit_code,
            outcome.output,
            outcome.exception,
        )
    document = json.loads((resolve_run(root / "detect") / "manifest.json").read_text())
    assert document["complete"] and document["status"] == "rejected"
print("Installed wheel: synthetic retrieval, AOI assets, and all five CLI commands passed.")
