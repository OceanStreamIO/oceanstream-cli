"""Integration tests for campaign inspect CLI command."""

from pathlib import Path

import pandas as pd
from typer.testing import CliRunner

from oceanstream.cli import app

runner = CliRunner()


def test_campaign_inspect_cli_with_data(tmp_path):
    """Test campaign inspect command with actual data."""
    output_dir = tmp_path / "output"
    campaign_dir = output_dir / "TEST_CLI"
    
    # Create campaign metadata
    from oceanstream.geotrack.campaign import create_campaign
    create_campaign(
        "TEST_CLI",
        metadata={
            "platforms": [{"id": "SD1030"}],
            "description": "CLI test campaign"
        }
    )
    
    # Create parquet data
    parquet_dir = campaign_dir / "lat_bin=lat_20_21" / "lon_bin=lon_-155_-154"
    parquet_dir.mkdir(parents=True)
    df = pd.DataFrame({
        'time': pd.date_range('2023-01-01', periods=10, freq='h'),
        'latitude': [20.5] * 10,
        'longitude': [-154.5] * 10,
        'trajectory': [1030] * 10,
    })
    df.to_parquet(parquet_dir / "data.parquet", index=False)
    
    # Create STAC metadata
    stac_dir = campaign_dir / "stac"
    stac_items_dir = stac_dir / "items"
    stac_items_dir.mkdir(parents=True)
    (stac_dir / "collection.json").write_text('{"type": "Collection"}')
    (stac_items_dir / "item-0.json").write_text('{"type": "Feature"}')
    
    # Run inspect command
    result = runner.invoke(app, [
        "campaign", "inspect", "TEST_CLI",
        "--output-dir", str(output_dir),
        "--limit", "5"
    ])
    
    # Clean up
    import shutil
    metadata_dir = Path.home() / ".oceanstream" / "campaigns" / "TEST_CLI"
    if metadata_dir.exists():
        shutil.rmtree(metadata_dir)
    
    assert result.exit_code == 0
    assert "Campaign: TEST_CLI" in result.stdout
    assert "Platform:         SD1030" in result.stdout
    assert "GeoParquet Dataset" in result.stdout
    assert "Total Rows:     10" in result.stdout
    assert "Columns:        4" in result.stdout
    assert "STAC Metadata" in result.stdout


def test_campaign_inspect_cli_no_data(tmp_path):
    """Test campaign inspect with campaign that has no data."""
    output_dir = tmp_path / "output"
    
    result = runner.invoke(app, [
        "campaign", "inspect", "NONEXISTENT",
        "--output-dir", str(output_dir)
    ])
    
    assert result.exit_code != 0
    assert "No processed data found" in result.stdout


def test_campaign_inspect_cli_verbose(tmp_path):
    """Test campaign inspect with verbose flag."""
    output_dir = tmp_path / "output"
    campaign_dir = output_dir / "TEST_VERBOSE"
    parquet_dir = campaign_dir / "lat_bin=lat_20_21" / "lon_bin=lon_-155_-154"
    parquet_dir.mkdir(parents=True)
    
    df = pd.DataFrame({'time': [1, 2, 3], 'latitude': [20.5, 20.6, 20.7]})
    df.to_parquet(parquet_dir / "data.parquet", index=False)
    
    result = runner.invoke(app, [
        "campaign", "inspect", "TEST_VERBOSE",
        "--output-dir", str(output_dir),
        "-v"
    ])
    
    assert result.exit_code == 0
    assert "[inspect] Found" in result.stdout
