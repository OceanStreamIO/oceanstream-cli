import shutil
from pathlib import Path
import json

import pytest


@pytest.mark.integration
def test_cli_geotrack_emits_stac_when_semantic_enabled(tmp_path: Path, monkeypatch):
    # Prepare input and output dirs
    in_dir = tmp_path / "in"
    out_dir = tmp_path / "out"
    in_dir.mkdir(parents=True)
    out_dir.mkdir(parents=True)

    # Create a minimal CSV that the CLI can ingest
    csv_path = in_dir / "sample.csv"
    csv_path.write_text(
        "latitude,longitude,time,TEMP_SBE37_MEAN\n"
        "10.0,20.0,2024-01-01T00:00:00Z,18.5\n"
        "10.1,20.1,2024-01-02T00:00:00Z,18.6\n"
    )

    # Create an alias table so the semantic mapper can resolve TEMP_SBE37_MEAN -> sea_water_temperature
    alias_table = tmp_path / "alias_table.json"
    alias_table.write_text(json.dumps({"sea_water_temperature": ["TEMP_SBE37_MEAN"]}))

    # Create a CF table that includes the canonical CF name so it will be picked up as a keyword
    cf_table = tmp_path / "cf_table.json"
    cf_table.write_text(json.dumps(["sea_water_temperature"]))

    # Env: enable semantic + STAC emission
    monkeypatch.setenv("PYTHONUTF8", "1")
    monkeypatch.setenv("PYTHONWARNINGS", "ignore")
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setenv("SEMANTIC_ENABLE", "true")
    monkeypatch.setenv("SEMANTIC_GENERATE_STAC", "true")
    monkeypatch.setenv("SEMANTIC_ALIAS_TABLE", str(alias_table))
    monkeypatch.setenv("SEMANTIC_CF_TABLE", str(cf_table))

    # Ensure settings are reloaded so Settings reads the monkeypatched env vars even
    # if other tests imported the modules earlier in the session.
    import importlib
    import sys

    if "oceanstream.config.settings" in sys.modules:
        importlib.reload(sys.modules["oceanstream.config.settings"])  # refresh Settings from env
    else:
        importlib.import_module("oceanstream.config.settings")
    
    # Reload geotrack submodule to pick up updated Settings
    if "oceanstream.geotrack.processor" in sys.modules:
        importlib.reload(sys.modules["oceanstream.geotrack.processor"])
    if "oceanstream.geotrack" in sys.modules:
        importlib.reload(sys.modules["oceanstream.geotrack"])

    if "oceanstream.cli" in sys.modules:
        importlib.reload(sys.modules["oceanstream.cli"])  # ensure CLI picks up updated Settings

    from typer.testing import CliRunner
    from oceanstream import cli as cli_module  # type: ignore

    runner = CliRunner()
    result = runner.invoke(
        cli_module.app,
        [
            "process",
            "geotrack",
            "convert",
            "--input-source",
            str(in_dir),
            "--output-dir",
            str(out_dir),
            "--yes",
            "-v",
        ],
    )

    assert result.exit_code == 0, f"CLI failed: {result.exit_code}\n{result.output}"

    # Find the campaign subdirectory (should be created based on campaign_id)
    campaign_dirs = [d for d in out_dir.iterdir() if d.is_dir()]
    assert len(campaign_dirs) == 1, f"Expected exactly one campaign directory, found {len(campaign_dirs)}"
    campaign_dir = campaign_dirs[0]

    stac_dir = campaign_dir / "stac"
    assert stac_dir.exists(), "STAC directory was not created in campaign folder"

    coll = stac_dir / "collection.json"
    items_dir = stac_dir / "items"
    item = items_dir / "item-0.json"

    assert coll.exists(), "collection.json missing"
    assert items_dir.exists(), "items directory missing"
    assert item.exists(), "item JSON missing"

    collection = json.loads(coll.read_text())
    assert "keywords" in collection
    assert "sea_water_temperature" in collection.get("keywords", []), "CF keyword missing from collection keywords"
