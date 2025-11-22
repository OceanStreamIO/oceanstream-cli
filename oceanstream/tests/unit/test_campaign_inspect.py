"""Tests for campaign inspection functionality."""

from pathlib import Path

import pandas as pd
import pytest

from oceanstream.geotrack.campaign import inspect_campaign_data


def test_inspect_campaign_data_missing_directory(tmp_path):
    """Test inspecting a campaign that doesn't exist."""
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    
    with pytest.raises(FileNotFoundError, match="No processed data found"):
        inspect_campaign_data("NONEXISTENT", output_dir)


def test_inspect_campaign_data_empty_directory(tmp_path):
    """Test inspecting a campaign with no data."""
    output_dir = tmp_path / "output"
    campaign_dir = output_dir / "TEST_CAMPAIGN"
    campaign_dir.mkdir(parents=True)
    
    result = inspect_campaign_data("TEST_CAMPAIGN", output_dir)
    
    assert result['campaign_dir'] == campaign_dir
    assert result['has_geoparquet'] is False
    assert result['geoparquet_sample'] is None
    assert result['geoparquet_info'] is None
    assert result['stac_collection'] is None
    assert result['stac_items'] == []
    assert result['pmtiles'] == []


def test_inspect_campaign_data_with_parquet(tmp_path):
    """Test inspecting a campaign with GeoParquet data."""
    output_dir = tmp_path / "output"
    campaign_dir = output_dir / "TEST_CAMPAIGN"
    
    # Create partitioned parquet structure
    parquet_dir = campaign_dir / "lat_bin=lat_20_21" / "lon_bin=lon_-155_-154"
    parquet_dir.mkdir(parents=True)
    
    # Create sample parquet file
    df = pd.DataFrame({
        'time': pd.date_range('2023-01-01', periods=15, freq='h'),
        'latitude': [20.5] * 15,
        'longitude': [-154.5] * 15,
        'trajectory': [1030] * 15,
        'SOG': [2.0] * 15,
        'platform_id': ['sd1030'] * 15,
        'campaign_id': ['TEST_CAMPAIGN'] * 15,
    })
    parquet_file = parquet_dir / "data.parquet"
    df.to_parquet(parquet_file, index=False)
    
    # Inspect
    result = inspect_campaign_data("TEST_CAMPAIGN", output_dir, limit=5)
    
    assert result['campaign_dir'] == campaign_dir
    assert result['has_geoparquet'] is True
    assert result['geoparquet_info'] is not None
    assert result['geoparquet_info']['total_rows'] == 15
    assert result['geoparquet_info']['columns'] == df.columns.tolist()
    assert result['geoparquet_sample'] is not None
    assert len(result['geoparquet_sample']) == 5
    

def test_inspect_campaign_data_with_stac(tmp_path):
    """Test inspecting a campaign with STAC metadata."""
    output_dir = tmp_path / "output"
    campaign_dir = output_dir / "TEST_CAMPAIGN"
    
    # Create STAC structure
    stac_dir = campaign_dir / "stac"
    stac_items_dir = stac_dir / "items"
    stac_items_dir.mkdir(parents=True)
    
    # Create STAC files
    collection_file = stac_dir / "collection.json"
    collection_file.write_text('{"type": "Collection"}')
    
    (stac_items_dir / "item-0.json").write_text('{"type": "Feature"}')
    (stac_items_dir / "item-1.json").write_text('{"type": "Feature"}')
    
    # Inspect
    result = inspect_campaign_data("TEST_CAMPAIGN", output_dir)
    
    assert result['stac_collection'] == collection_file
    assert len(result['stac_items']) == 2


def test_inspect_campaign_data_limit_parameter(tmp_path):
    """Test that limit parameter controls sample size."""
    output_dir = tmp_path / "output"
    campaign_dir = output_dir / "TEST_CAMPAIGN"
    parquet_dir = campaign_dir / "lat_bin=lat_20_21" / "lon_bin=lon_-155_-154"
    parquet_dir.mkdir(parents=True)
    
    # Create parquet with 20 rows
    df = pd.DataFrame({
        'time': pd.date_range('2023-01-01', periods=20, freq='h'),
        'latitude': [20.5] * 20,
        'longitude': [-154.5] * 20,
    })
    df.to_parquet(parquet_dir / "data.parquet", index=False)
    
    # Test with limit=3
    result = inspect_campaign_data("TEST_CAMPAIGN", output_dir, limit=3)
    assert len(result['geoparquet_sample']) == 3
    
    # Test with limit=15
    result = inspect_campaign_data("TEST_CAMPAIGN", output_dir, limit=15)
    assert len(result['geoparquet_sample']) == 15
    
    # Test with limit > total rows
    result = inspect_campaign_data("TEST_CAMPAIGN", output_dir, limit=100)
    assert len(result['geoparquet_sample']) == 20


def test_inspect_campaign_data_excludes_stac_directory(tmp_path):
    """Test that STAC directory parquet files are excluded."""
    output_dir = tmp_path / "output"
    campaign_dir = output_dir / "TEST_CAMPAIGN"
    
    # Create data parquet
    data_dir = campaign_dir / "lat_bin=lat_20_21" / "lon_bin=lon_-155_-154"
    data_dir.mkdir(parents=True)
    df = pd.DataFrame({'time': [1, 2, 3], 'latitude': [20.5, 20.6, 20.7]})
    df.to_parquet(data_dir / "data.parquet", index=False)
    
    # Create STAC directory with a file that looks like parquet but isn't
    stac_dir = campaign_dir / "stac"
    stac_dir.mkdir(parents=True)
    (stac_dir / "collection.json").write_text('{"type": "Collection"}')
    
    # Should only read the data parquet, not try to read STAC files
    result = inspect_campaign_data("TEST_CAMPAIGN", output_dir)
    
    assert result['has_geoparquet'] is True
    assert result['geoparquet_info']['total_rows'] == 3
