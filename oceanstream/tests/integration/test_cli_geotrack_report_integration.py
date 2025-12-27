"""Integration tests for the geotrack report CLI command.

Tests the report command end-to-end including campaign lookup.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path
import pytest
import pandas as pd


@pytest.mark.integration
def test_cli_geotrack_report_with_path(tmp_path: Path, monkeypatch):
    """Test report command with explicit dataset path."""
    # Use isolated campaigns directory
    campaigns_dir = tmp_path / "campaigns"
    campaigns_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        "oceanstream.geotrack.campaign.get_campaigns_dir",
        lambda: campaigns_dir
    )
    
    # Create a minimal parquet dataset (without Hive partitioning columns)
    data_dir = tmp_path / "campaign_data"
    data_dir.mkdir()
    
    df = pd.DataFrame({
        "time": pd.date_range("2023-01-01", periods=100, freq="h", tz="UTC"),
        "latitude": [1.0] * 100,
        "longitude": [-159.0] * 100,
        "trajectory": [1030] * 100,
    })
    df.to_parquet(data_dir / "data.parquet")
    
    from typer.testing import CliRunner
    from oceanstream import cli as cli_module
    
    runner = CliRunner()
    result = runner.invoke(
        cli_module.app,
        [
            "process",
            "geotrack",
            "report",
            str(data_dir),
        ],
    )
    
    assert result.exit_code == 0, f"CLI failed: {result.output}"
    assert "# OceanStream Processing Report" in result.output
    assert "100" in result.output  # row count


@pytest.mark.integration
def test_cli_geotrack_report_with_campaign_id(tmp_path: Path, monkeypatch):
    """Test report command with campaign ID lookup."""
    # Use isolated campaigns directory
    campaigns_dir = tmp_path / "campaigns"
    campaigns_dir.mkdir(parents=True, exist_ok=True)
    
    # Patch the campaigns directory
    monkeypatch.setattr(
        "oceanstream.geotrack.campaign.get_campaigns_dir",
        lambda: campaigns_dir
    )
    
    # Create a minimal parquet dataset
    data_dir = tmp_path / "output" / "test_campaign"
    data_dir.mkdir(parents=True)
    
    df = pd.DataFrame({
        "time": pd.date_range("2023-01-01", periods=50, freq="h", tz="UTC"),
        "latitude": [5.0] * 50,
        "longitude": [-155.0] * 50,
    })
    df.to_parquet(data_dir / "data.parquet")
    
    # Create campaign metadata with output_directory
    campaign_dir = campaigns_dir / "test_campaign"
    campaign_dir.mkdir()
    campaign_metadata = {
        "campaign_id": "test_campaign",
        "output_directory": str(data_dir),
        "platform_id": "sd1030",
    }
    (campaign_dir / "campaign.json").write_text(json.dumps(campaign_metadata))
    
    from typer.testing import CliRunner
    from oceanstream import cli as cli_module
    
    runner = CliRunner()
    result = runner.invoke(
        cli_module.app,
        [
            "process",
            "geotrack",
            "report",
            "--campaign-id", "test_campaign",
            "-v",
        ],
    )
    
    assert result.exit_code == 0, f"CLI failed: {result.output}"
    assert "Using dataset path from campaign" in result.output
    assert "# OceanStream Processing Report: test_campaign" in result.output


@pytest.mark.integration
def test_cli_geotrack_report_json_output(tmp_path: Path, monkeypatch):
    """Test report command with JSON output format."""
    campaigns_dir = tmp_path / "campaigns"
    campaigns_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        "oceanstream.geotrack.campaign.get_campaigns_dir",
        lambda: campaigns_dir
    )
    
    # Create a minimal parquet dataset
    data_dir = tmp_path / "campaign_data"
    data_dir.mkdir()
    
    df = pd.DataFrame({
        "time": pd.date_range("2023-01-01", periods=25, freq="h", tz="UTC"),
        "latitude": [10.0] * 25,
        "longitude": [-160.0] * 25,
    })
    df.to_parquet(data_dir / "data.parquet")
    
    from typer.testing import CliRunner
    from oceanstream import cli as cli_module
    
    runner = CliRunner()
    result = runner.invoke(
        cli_module.app,
        [
            "process",
            "geotrack",
            "report",
            str(data_dir),
            "--format", "json",
        ],
    )
    
    assert result.exit_code == 0, f"CLI failed: {result.output}"
    
    # Parse JSON output
    output_json = json.loads(result.output)
    assert output_json["summary"]["total_rows"] == 25
    assert "temporal_extent" in output_json
    assert "spatial_extent" in output_json


@pytest.mark.integration
def test_cli_geotrack_report_to_file(tmp_path: Path, monkeypatch):
    """Test report command writing to file."""
    campaigns_dir = tmp_path / "campaigns"
    campaigns_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        "oceanstream.geotrack.campaign.get_campaigns_dir",
        lambda: campaigns_dir
    )
    
    # Create a minimal parquet dataset
    data_dir = tmp_path / "campaign_data"
    data_dir.mkdir()
    
    df = pd.DataFrame({
        "time": pd.date_range("2023-01-01", periods=20, freq="h", tz="UTC"),
        "latitude": [5.0] * 20,
        "longitude": [-155.0] * 20,
    })
    df.to_parquet(data_dir / "data.parquet")
    
    output_file = tmp_path / "report.md"
    
    from typer.testing import CliRunner
    from oceanstream import cli as cli_module
    
    runner = CliRunner()
    result = runner.invoke(
        cli_module.app,
        [
            "process",
            "geotrack",
            "report",
            str(data_dir),
            "--output", str(output_file),
        ],
    )
    
    assert result.exit_code == 0, f"CLI failed: {result.output}"
    assert "Report written to" in result.output
    assert output_file.exists()
    
    content = output_file.read_text()
    assert "# OceanStream Processing Report" in content


@pytest.mark.integration
def test_cli_geotrack_report_no_args_error(tmp_path: Path, monkeypatch):
    """Test report command fails gracefully with no arguments."""
    campaigns_dir = tmp_path / "campaigns"
    campaigns_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        "oceanstream.geotrack.campaign.get_campaigns_dir",
        lambda: campaigns_dir
    )
    
    from typer.testing import CliRunner
    from oceanstream import cli as cli_module
    
    runner = CliRunner()
    result = runner.invoke(
        cli_module.app,
        [
            "process",
            "geotrack",
            "report",
        ],
    )
    
    assert result.exit_code == 1
    assert "Either dataset_path or --campaign-id must be provided" in result.output


@pytest.mark.integration
def test_cli_geotrack_report_campaign_not_found(tmp_path: Path, monkeypatch):
    """Test report command with non-existent campaign."""
    campaigns_dir = tmp_path / "campaigns"
    campaigns_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        "oceanstream.geotrack.campaign.get_campaigns_dir",
        lambda: campaigns_dir
    )
    
    from typer.testing import CliRunner
    from oceanstream import cli as cli_module
    
    runner = CliRunner()
    result = runner.invoke(
        cli_module.app,
        [
            "process",
            "geotrack",
            "report",
            "--campaign-id", "nonexistent_campaign",
        ],
    )
    
    assert result.exit_code == 1
    assert "not found" in result.output
    assert "oceanstream campaign list" in result.output


@pytest.mark.integration
def test_cli_geotrack_report_invalid_format(tmp_path: Path, monkeypatch):
    """Test report command with invalid output format."""
    campaigns_dir = tmp_path / "campaigns"
    campaigns_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        "oceanstream.geotrack.campaign.get_campaigns_dir",
        lambda: campaigns_dir
    )
    
    # Create a minimal parquet dataset
    data_dir = tmp_path / "campaign_data"
    data_dir.mkdir()
    
    df = pd.DataFrame({
        "time": pd.date_range("2023-01-01", periods=10, freq="h", tz="UTC"),
        "latitude": [1.0] * 10,
        "longitude": [-159.0] * 10,
    })
    df.to_parquet(data_dir / "data.parquet")
    
    from typer.testing import CliRunner
    from oceanstream import cli as cli_module
    
    runner = CliRunner()
    result = runner.invoke(
        cli_module.app,
        [
            "process",
            "geotrack",
            "report",
            str(data_dir),
            "--format", "xml",
        ],
    )
    
    assert result.exit_code == 1
    assert "Invalid format" in result.output


@pytest.mark.integration
def test_cli_geotrack_report_with_stac_metadata(tmp_path: Path, monkeypatch):
    """Test report command includes STAC metadata when available."""
    campaigns_dir = tmp_path / "campaigns"
    campaigns_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        "oceanstream.geotrack.campaign.get_campaigns_dir",
        lambda: campaigns_dir
    )
    
    # Create a parquet dataset with STAC metadata
    data_dir = tmp_path / "campaign_data"
    data_dir.mkdir()
    stac_dir = data_dir / "stac"
    stac_dir.mkdir()
    items_dir = stac_dir / "items"
    items_dir.mkdir()
    
    df = pd.DataFrame({
        "time": pd.date_range("2023-01-01", periods=50, freq="h", tz="UTC"),
        "latitude": [5.0] * 50,
        "longitude": [-155.0] * 50,
        "TEMP_SBE37_MEAN": [28.0] * 50,
    })
    df.to_parquet(data_dir / "data.parquet")
    
    # Create STAC collection
    collection = {
        "id": "test-collection",
        "stac_version": "1.0.0",
        "description": "Test dataset",
        "license": "MIT",
        "summaries": {
            "instruments": [
                {"name": "Sea-Bird SBE37", "manufacturer": "Sea-Bird Scientific", "type": "ctd"}
            ],
            "platform": {"id": "sd1030", "type": "Saildrone Explorer"},
            "processing": {"software": "oceanstream", "version": "0.1.0"},
        },
    }
    (stac_dir / "collection.json").write_text(json.dumps(collection))
    (items_dir / "item-0.json").write_text("{}")
    
    from typer.testing import CliRunner
    from oceanstream import cli as cli_module
    
    runner = CliRunner()
    result = runner.invoke(
        cli_module.app,
        [
            "process",
            "geotrack",
            "report",
            str(data_dir),
        ],
    )
    
    assert result.exit_code == 0, f"CLI failed: {result.output}"
    assert "## Detected Sensors" in result.output
    assert "Sea-Bird SBE37" in result.output
    assert "## STAC Metadata" in result.output
    assert "test-collection" in result.output
