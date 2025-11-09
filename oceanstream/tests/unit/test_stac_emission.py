import json
from pathlib import Path
import pandas as pd
import pyarrow.parquet as pq
import pytest

from oceanstream.geotrack.geoparquet_writer import write_geoparquet
from oceanstream.stac import emit_stac_collection_and_item
from oceanstream.stac.emit import calculate_measurement_statistics


def test_stac_collection_and_item_emission(tmp_path: Path):
    # Minimal dataframe with spatial + temporal coverage and a variable for CF keywords
    df = pd.DataFrame({
        "latitude": [10.0, 10.1, 10.2],
        "longitude": [20.0, 20.2, 20.4],
        "time": pd.to_datetime([
            "2024-01-01T00:00:00Z",
            "2024-01-02T00:00:00Z",
            "2024-01-03T00:00:00Z",
        ]),
        "TEMP_SBE37_MEAN": [18.5, 18.7, 18.6],
    })

    # Simple bins that cover points
    lat_bins = [9.0, 11.0]
    lon_bins = [19.0, 21.0]

    out_dir = tmp_path / "dataset"
    write_geoparquet(
        df,
        out_dir,
        lat_bins,
        lon_bins,
        units_metadata=None,
        alias_mapping=None,
        provider_metadata=None,
        semantic_metadata=None,
    )

    # Provide semantic metadata with CF mapping so keywords populate
    semantic_meta = {
        "oceanstream:cf_standard_names": {
            "TEMP_SBE37_MEAN": {"cf_standard_name": "sea_water_temperature", "confidence": 1.0}
        }
    }

    coll_path, item_paths = emit_stac_collection_and_item(
        out_dir,
        df,
        semantic_meta,
        provider_name="saildrone",
    )

    assert coll_path.exists(), "STAC collection.json was not created"
    assert item_paths, "No STAC item JSONs were created"
    item_path = item_paths[0]
    assert item_path.exists(), "STAC item JSON was not created"

    collection = json.loads(coll_path.read_text())
    item = json.loads(item_path.read_text())

    # Collection basics
    assert collection["type"] == "Collection"
    assert collection["stac_version"].startswith("1.")
    assert collection["id"].startswith("oceanstream-saildrone")
    assert "extent" in collection and "spatial" in collection["extent"]
    bbox = collection["extent"]["spatial"]["bbox"][0]
    assert len(bbox) == 4 and bbox[0] <= bbox[2] and bbox[1] <= bbox[3]
    assert "sea_water_temperature" in collection.get("keywords", [])

    # Item basics
    assert item["type"] == "Feature"
    assert item["collection"] == collection["id"]
    assert "geometry" in item and item["geometry"]["type"] == "Polygon"
    assert "assets" in item and "geoparquet" in item["assets"]
    # relative href pointing up from items/ to dataset root
    href = item["assets"]["geoparquet"]["href"]
    assert href.startswith(".."), f"Unexpected asset href: {href}"

    # Optional temporal properties
    props = item.get("properties", {})
    if "start_datetime" in props and "end_datetime" in props:
        assert props["start_datetime"] <= props["end_datetime"]


def test_calculate_measurement_statistics():
    """Test measurement statistics calculation."""
    df = pd.DataFrame({
        'latitude': [10.0, 10.5, 11.0],
        'longitude': [150.0, 150.5, 151.0],
        'time': pd.to_datetime(['2023-01-01', '2023-01-02', '2023-01-03']),
        'TEMP_AIR_MEAN': [25.0, 26.0, 24.5],
        'SAL_SBE37_MEAN': [35.0, 35.2, 34.8],
        'WIND_SPEED_MEAN': [5.0, 6.5, 4.2],
        'text_column': ['a', 'b', 'c'],  # Non-numeric, should be skipped
    })
    
    # Test with specific columns
    measurement_columns = ['TEMP_AIR_MEAN', 'SAL_SBE37_MEAN', 'WIND_SPEED_MEAN']
    stats = calculate_measurement_statistics(df, measurement_columns)
    
    assert len(stats) == 3, "Should calculate stats for 3 columns"
    
    # Check TEMP_AIR_MEAN statistics
    assert 'TEMP_AIR_MEAN' in stats
    temp_stats = stats['TEMP_AIR_MEAN']
    assert abs(temp_stats['min'] - 24.5) < 0.01
    assert abs(temp_stats['max'] - 26.0) < 0.01
    assert abs(temp_stats['mean'] - 25.166667) < 0.01
    assert temp_stats['count'] == 3
    
    # Check SAL_SBE37_MEAN statistics
    assert 'SAL_SBE37_MEAN' in stats
    sal_stats = stats['SAL_SBE37_MEAN']
    assert abs(sal_stats['min'] - 34.8) < 0.01
    assert abs(sal_stats['max'] - 35.2) < 0.01
    assert sal_stats['count'] == 3
    
    # Check WIND_SPEED_MEAN statistics
    assert 'WIND_SPEED_MEAN' in stats
    wind_stats = stats['WIND_SPEED_MEAN']
    assert abs(wind_stats['min'] - 4.2) < 0.01
    assert abs(wind_stats['max'] - 6.5) < 0.01
    assert wind_stats['count'] == 3


def test_calculate_measurement_statistics_auto_detect():
    """Test automatic detection of numeric columns."""
    df = pd.DataFrame({
        'latitude': [10.0, 10.5, 11.0],
        'longitude': [150.0, 150.5, 151.0],
        'time': pd.to_datetime(['2023-01-01', '2023-01-02', '2023-01-03']),
        'TEMP_AIR_MEAN': [25.0, 26.0, 24.5],
        'text_column': ['a', 'b', 'c'],
    })
    
    # Test with auto-detection (None)
    stats = calculate_measurement_statistics(df, None)
    
    # Should include TEMP_AIR_MEAN but exclude latitude, longitude, time
    assert 'TEMP_AIR_MEAN' in stats
    assert 'latitude' not in stats
    assert 'longitude' not in stats
    assert 'time' not in stats
    assert 'text_column' not in stats


def test_stac_with_processing_provenance(tmp_path: Path):
    """Test STAC collection includes processing provenance."""
    df = pd.DataFrame({
        "latitude": [10.0, 10.1, 10.2],
        "longitude": [20.0, 20.2, 20.4],
        "time": pd.to_datetime(["2024-01-01", "2024-01-02", "2024-01-03"]),
        "TEMP_AIR_MEAN": [25.0, 26.0, 24.5],
    })
    
    lat_bins = [9.0, 11.0]
    lon_bins = [19.0, 21.0]
    
    out_dir = tmp_path / "dataset"
    write_geoparquet(df, out_dir, lat_bins, lon_bins)
    
    coll_path, _ = emit_stac_collection_and_item(
        out_dir,
        df,
        None,
        provider_name="test",
        software_version="0.1.0-test",
    )
    
    collection = json.loads(coll_path.read_text())
    
    # Check processing provenance
    assert 'summaries' in collection
    assert 'processing' in collection['summaries']
    
    processing = collection['summaries']['processing']
    assert processing['software'] == 'oceanstream'
    assert processing['version'] == '0.1.0-test'
    assert 'processing_date' in processing
    assert processing['processing_level'] == 'L2'


def test_stac_with_measurement_statistics(tmp_path: Path):
    """Test STAC collection includes measurement statistics."""
    df = pd.DataFrame({
        "latitude": [10.0, 10.1, 10.2, 10.3],
        "longitude": [20.0, 20.2, 20.4, 20.6],
        "time": pd.to_datetime(["2024-01-01", "2024-01-02", "2024-01-03", "2024-01-04"]),
        "TEMP_AIR_MEAN": [25.0, 26.0, 24.5, 25.5],
        "SAL_SBE37_MEAN": [35.0, 35.2, 34.8, 35.1],
    })
    
    lat_bins = [9.0, 11.0]
    lon_bins = [19.0, 21.0]
    
    out_dir = tmp_path / "dataset"
    write_geoparquet(df, out_dir, lat_bins, lon_bins)
    
    # Calculate measurement stats
    measurement_columns = ['TEMP_AIR_MEAN', 'SAL_SBE37_MEAN']
    measurement_stats = calculate_measurement_statistics(df, measurement_columns)
    
    coll_path, _ = emit_stac_collection_and_item(
        out_dir,
        df,
        None,
        provider_name="test",
        measurement_stats=measurement_stats,
    )
    
    collection = json.loads(coll_path.read_text())
    
    # Check measurement statistics
    assert 'summaries' in collection
    assert 'measurements' in collection['summaries']
    
    measurements = collection['summaries']['measurements']
    assert 'TEMP_AIR_MEAN' in measurements
    assert 'SAL_SBE37_MEAN' in measurements
    
    temp_stats = measurements['TEMP_AIR_MEAN']
    assert 'min' in temp_stats
    assert 'max' in temp_stats
    assert 'mean' in temp_stats
    assert 'count' in temp_stats
    assert temp_stats['count'] == 4


def test_stac_with_pmtiles_asset(tmp_path: Path):
    """Test STAC item includes PMTiles asset when provided."""
    df = pd.DataFrame({
        "latitude": [10.0, 10.1],
        "longitude": [20.0, 20.2],
        "time": pd.to_datetime(["2024-01-01", "2024-01-02"]),
    })
    
    lat_bins = [9.0, 11.0]
    lon_bins = [19.0, 21.0]
    
    out_dir = tmp_path / "dataset"
    write_geoparquet(df, out_dir, lat_bins, lon_bins)
    
    # Create a fake PMTiles file
    tiles_dir = tmp_path / "tiles"
    tiles_dir.mkdir()
    pmtiles_path = tiles_dir / "track.pmtiles"
    pmtiles_path.write_text("fake pmtiles data")
    
    coll_path, item_paths = emit_stac_collection_and_item(
        out_dir,
        df,
        None,
        provider_name="test",
        pmtiles_path=pmtiles_path,
    )
    
    # Check first item has PMTiles asset
    item = json.loads(item_paths[0].read_text())
    
    assert 'assets' in item
    assert 'pmtiles' in item['assets']
    
    pmtiles_asset = item['assets']['pmtiles']
    assert pmtiles_asset['type'] == 'application/vnd.pmtiles'
    assert 'visual' in pmtiles_asset['roles']
    assert 'tiles' in pmtiles_asset['roles']
    assert 'title' in pmtiles_asset
    assert 'href' in pmtiles_asset
