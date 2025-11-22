"""Tests for spatial-temporal interpolation functionality."""
import pandas as pd
import geopandas as gpd
from pathlib import Path
from datetime import datetime, timedelta
import pytest
from shapely.geometry import Point

from oceanstream.geotrack.interpolation import (
    has_spatial_coordinates,
    interpolate_spatial_coordinates,
    enrich_sensor_data_from_campaign,
    create_geometry_from_coordinates,
)


def test_has_spatial_coordinates_with_coords():
    """Test detection of spatial coordinates when present."""
    df = pd.DataFrame({
        'time': pd.date_range('2023-01-01', periods=5, freq='1min'),
        'latitude': [10.0, 10.1, 10.2, 10.3, 10.4],
        'longitude': [20.0, 20.1, 20.2, 20.3, 20.4],
        'value': [1, 2, 3, 4, 5]
    })
    assert has_spatial_coordinates(df) is True


def test_has_spatial_coordinates_without_coords():
    """Test detection when spatial coordinates are missing."""
    df = pd.DataFrame({
        'time': pd.date_range('2023-01-01', periods=5, freq='1min'),
        'value': [1, 2, 3, 4, 5]
    })
    assert has_spatial_coordinates(df) is False


def test_has_spatial_coordinates_partial():
    """Test detection when only one coordinate is present."""
    df = pd.DataFrame({
        'time': pd.date_range('2023-01-01', periods=5, freq='1min'),
        'latitude': [10.0, 10.1, 10.2, 10.3, 10.4],
        'value': [1, 2, 3, 4, 5]
    })
    assert has_spatial_coordinates(df) is False


def test_interpolate_nearest():
    """Test nearest neighbor interpolation."""
    # Reference data with coordinates
    ref_times = pd.date_range('2023-01-01 00:00:00', periods=5, freq='10min')
    reference_gdf = gpd.GeoDataFrame({
        'time': ref_times,
        'latitude': [10.0, 10.1, 10.2, 10.3, 10.4],
        'longitude': [20.0, 20.1, 20.2, 20.3, 20.4],
        'geometry': [Point(20.0, 10.0), Point(20.1, 10.1), Point(20.2, 10.2), 
                     Point(20.3, 10.3), Point(20.4, 10.4)]
    })
    
    # Sensor data without coordinates (times between reference points)
    sensor_times = pd.date_range('2023-01-01 00:05:00', periods=3, freq='10min')
    sensor_df = pd.DataFrame({
        'time': sensor_times,
        'sensor_value': [100, 200, 300]
    })
    
    # Interpolate with Timedelta object
    result = interpolate_spatial_coordinates(
        sensor_df, reference_gdf, method='nearest', max_time_gap=pd.Timedelta(seconds=600)
    )
    
    # Check results
    assert 'latitude' in result.columns
    assert 'longitude' in result.columns
    assert not result['latitude'].isna().all()
    assert not result['longitude'].isna().all()
    assert len(result) == 3


def test_interpolate_linear():
    """Test linear interpolation."""
    # Reference data
    ref_times = pd.date_range('2023-01-01 00:00:00', periods=3, freq='10min')
    reference_gdf = gpd.GeoDataFrame({
        'time': ref_times,
        'latitude': [10.0, 10.1, 10.2],
        'longitude': [20.0, 20.1, 20.2],
        'geometry': [Point(20.0, 10.0), Point(20.1, 10.1), Point(20.2, 10.2)]
    })
    
    # Sensor data at exact midpoint
    sensor_df = pd.DataFrame({
        'time': [pd.Timestamp('2023-01-01 00:05:00')],
        'sensor_value': [100]
    })
    
    # Interpolate with Timedelta
    result = interpolate_spatial_coordinates(
        sensor_df, reference_gdf, method='linear', max_time_gap=pd.Timedelta(seconds=600)
    )
    
    # Should be approximately halfway between 10.0 and 10.1
    assert abs(result['latitude'].iloc[0] - 10.05) < 0.01
    assert abs(result['longitude'].iloc[0] - 20.05) < 0.01


def test_interpolate_with_time_gap():
    """Test that interpolation respects max_time_gap."""
    # Reference data
    ref_times = [
        pd.Timestamp('2023-01-01 00:00:00'),
        pd.Timestamp('2023-01-01 00:10:00'),
        pd.Timestamp('2023-01-01 01:00:00'),  # Large gap
    ]
    reference_gdf = gpd.GeoDataFrame({
        'time': ref_times,
        'latitude': [10.0, 10.1, 10.5],
        'longitude': [20.0, 20.1, 20.5],
        'geometry': [Point(20.0, 10.0), Point(20.1, 10.1), Point(20.5, 10.5)]
    })
    
    # Sensor data during the large gap
    sensor_df = pd.DataFrame({
        'time': [pd.Timestamp('2023-01-01 00:30:00')],
        'sensor_value': [100]
    })
    
    # Interpolate with 10 minute max gap
    result = interpolate_spatial_coordinates(
        sensor_df, reference_gdf, method='nearest', max_time_gap=pd.Timedelta(seconds=600)
    )
    
    # Should have NaN because gap is too large
    assert result['latitude'].isna().all()
    assert result['longitude'].isna().all()


def test_interpolate_ffill():
    """Test forward fill interpolation."""
    # Reference data
    ref_times = pd.date_range('2023-01-01 00:00:00', periods=3, freq='10min')
    reference_gdf = gpd.GeoDataFrame({
        'time': ref_times,
        'latitude': [10.0, 10.1, 10.2],
        'longitude': [20.0, 20.1, 20.2],
        'geometry': [Point(20.0, 10.0), Point(20.1, 10.1), Point(20.2, 10.2)]
    })
    
    # Sensor data between reference points
    sensor_df = pd.DataFrame({
        'time': [pd.Timestamp('2023-01-01 00:05:00')],
        'sensor_value': [100]
    })
    
    # Forward fill should use the most recent reference point (00:00:00)
    result = interpolate_spatial_coordinates(
        sensor_df, reference_gdf, method='ffill', max_time_gap=pd.Timedelta(seconds=600)
    )
    
    # Should use the first reference point's coordinates
    assert result['latitude'].iloc[0] == 10.0
    assert result['longitude'].iloc[0] == 20.0


def test_interpolate_bfill():
    """Test backward fill interpolation."""
    # Reference data
    ref_times = pd.date_range('2023-01-01 00:00:00', periods=3, freq='10min')
    reference_gdf = gpd.GeoDataFrame({
        'time': ref_times,
        'latitude': [10.0, 10.1, 10.2],
        'longitude': [20.0, 20.1, 20.2],
        'geometry': [Point(20.0, 10.0), Point(20.1, 10.1), Point(20.2, 10.2)]
    })
    
    # Sensor data between reference points
    sensor_df = pd.DataFrame({
        'time': [pd.Timestamp('2023-01-01 00:05:00')],
        'sensor_value': [100]
    })
    
    # Backward fill should use the next reference point (00:10:00)
    result = interpolate_spatial_coordinates(
        sensor_df, reference_gdf, method='bfill', max_time_gap=pd.Timedelta(seconds=600)
    )
    
    # Should use the second reference point's coordinates
    assert result['latitude'].iloc[0] == 10.1
    assert result['longitude'].iloc[0] == 20.1


def test_enrich_sensor_data_no_existing_campaign(tmp_path):
    """Test enrichment when no campaign data exists."""
    # Sensor data without coordinates
    sensor_df = pd.DataFrame({
        'time': pd.date_range('2023-01-01', periods=5, freq='1min'),
        'sensor_value': [1, 2, 3, 4, 5]
    })
    
    # Non-existent campaign directory
    campaign_dir = tmp_path / "non_existent_campaign"
    
    # Should return data with empty coordinates and success=False
    result, success = enrich_sensor_data_from_campaign(sensor_df, campaign_dir)
    
    assert success is False
    assert 'latitude' in result.columns
    assert 'longitude' in result.columns
    assert result['latitude'].isna().all()
    assert result['longitude'].isna().all()


def test_enrich_sensor_data_with_existing_campaign(tmp_path):
    """Test enrichment with existing campaign data."""
    # Create mock campaign directory with parquet file
    campaign_dir = tmp_path / "test_campaign"
    partition_dir = campaign_dir / "lat_bin=10" / "lon_bin=20"
    partition_dir.mkdir(parents=True)
    
    # Create reference GeoParquet data
    ref_times = pd.date_range('2023-01-01 00:00:00', periods=5, freq='10min')
    ref_gdf = gpd.GeoDataFrame({
        'time': ref_times,
        'latitude': [10.0, 10.1, 10.2, 10.3, 10.4],
        'longitude': [20.0, 20.1, 20.2, 20.3, 20.4],
        'geometry': [Point(20.0 + i*0.1, 10.0 + i*0.1) for i in range(5)]
    }, crs="EPSG:4326")
    
    ref_gdf.to_parquet(partition_dir / "data.parquet")
    
    # Sensor data without coordinates (times match reference data)
    sensor_df = pd.DataFrame({
        'time': ref_times,
        'sensor_value': [100, 200, 300, 400, 500]
    })
    
    # Enrich
    result, success = enrich_sensor_data_from_campaign(
        sensor_df, campaign_dir, method='nearest'
    )
    
    # Check results
    assert success == True  # Use == instead of 'is' for numpy bool comparison
    assert 'latitude' in result.columns
    assert 'longitude' in result.columns
    assert not result['latitude'].isna().all()
    assert not result['longitude'].isna().all()
    # Values should be close to reference data
    assert abs(result['latitude'].iloc[0] - 10.0) < 0.01


def test_create_geometry_from_coordinates():
    """Test geometry creation from coordinates."""
    df = pd.DataFrame({
        'latitude': [10.0, 10.1, 10.2],
        'longitude': [20.0, 20.1, 20.2],
        'value': [1, 2, 3]
    })
    
    gdf = create_geometry_from_coordinates(df)
    
    assert isinstance(gdf, gpd.GeoDataFrame)
    assert 'geometry' in gdf.columns
    assert gdf.crs.to_string() == "EPSG:4326"
    assert all(isinstance(geom, Point) for geom in gdf.geometry)


def test_create_geometry_with_nan_coordinates():
    """Test geometry creation with NaN coordinates."""
    df = pd.DataFrame({
        'latitude': [10.0, None, 10.2],
        'longitude': [20.0, 20.1, None],
        'value': [1, 2, 3]
    })
    
    gdf = create_geometry_from_coordinates(df)
    
    assert isinstance(gdf, gpd.GeoDataFrame)
    # Rows with NaN coordinates should have None geometry
    assert gdf.geometry.iloc[0] is not None
    assert gdf.geometry.iloc[1] is None
    assert gdf.geometry.iloc[2] is None


def test_interpolate_invalid_method():
    """Test that invalid interpolation method raises error."""
    reference_gdf = gpd.GeoDataFrame({
        'time': pd.date_range('2023-01-01', periods=3, freq='1min'),
        'latitude': [10.0, 10.1, 10.2],
        'longitude': [20.0, 20.1, 20.2],
        'geometry': [Point(20.0, 10.0), Point(20.1, 10.1), Point(20.2, 10.2)]
    })
    
    sensor_df = pd.DataFrame({
        'time': pd.date_range('2023-01-01', periods=3, freq='1min'),
        'sensor_value': [1, 2, 3]
    })
    
    with pytest.raises(ValueError, match="Unknown interpolation method"):
        interpolate_spatial_coordinates(
            sensor_df, reference_gdf, method='invalid_method'
        )


def test_interpolate_missing_time_column():
    """Test that missing time column raises error."""
    reference_gdf = gpd.GeoDataFrame({
        'latitude': [10.0, 10.1, 10.2],
        'longitude': [20.0, 20.1, 20.2],
        'geometry': [Point(20.0, 10.0), Point(20.1, 10.1), Point(20.2, 10.2)]
    })
    
    sensor_df = pd.DataFrame({
        'time': pd.date_range('2023-01-01', periods=3, freq='1min'),
        'sensor_value': [1, 2, 3]
    })
    
    with pytest.raises(ValueError, match="Reference data missing time column"):
        interpolate_spatial_coordinates(sensor_df, reference_gdf, time_column='time')
