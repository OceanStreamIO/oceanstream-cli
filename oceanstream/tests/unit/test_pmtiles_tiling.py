"""Unit tests for PMTiles tiling module."""
import shutil
from pathlib import Path
import pytest
import geopandas as gpd
import pandas as pd
from shapely.geometry import Point


@pytest.fixture
def sample_geoparquet_data(tmp_path: Path):
    """Create a small sample GeoParquet dataset for testing."""
    # Create sample data with geometry
    data = {
        'latitude': [-42.0, -42.1, -42.2, -42.3],
        'longitude': [170.0, 170.1, 170.2, 170.3],
        'time': pd.date_range('2023-01-01', periods=4, freq='1h'),
        'platform_id': ['sd1030'] * 4,
        'temperature': [15.5, 15.6, 15.7, 15.8],
    }
    
    df = pd.DataFrame(data)
    
    # Create geometry column
    df['geometry'] = [Point(lon, lat) for lat, lon in zip(df['latitude'], df['longitude'])]
    
    # Convert to GeoDataFrame
    gdf = gpd.GeoDataFrame(df, geometry='geometry', crs='EPSG:4326')
    
    # Create partitioned structure
    geoparquet_dir = tmp_path / "geoparquet"
    partition_dir = geoparquet_dir / "lon_grid=170" / "lat_grid=-43"
    partition_dir.mkdir(parents=True)
    
    # Write parquet file
    parquet_file = partition_dir / "data.parquet"
    gdf.to_parquet(parquet_file)
    
    return geoparquet_dir


def test_missing_dependency_error():
    """Test MissingDependencyError exception."""
    from oceanstream.geotrack.tiling import MissingDependencyError
    
    with pytest.raises(MissingDependencyError, match="test message"):
        raise MissingDependencyError("test message")


def test_generate_pmtiles_missing_ogr2ogr(sample_geoparquet_data, tmp_path, monkeypatch):
    """Test PMTiles generation fails gracefully when ogr2ogr is missing."""
    from oceanstream.geotrack.tiling import generate_pmtiles_from_geoparquet, MissingDependencyError
    
    # Mock shutil.which to return None for ogr2ogr
    def mock_which(name):
        if name == "ogr2ogr":
            return None
        return "/usr/bin/" + name
    
    monkeypatch.setattr(shutil, "which", mock_which)
    
    pmtiles_path = tmp_path / "output.pmtiles"
    
    with pytest.raises(MissingDependencyError, match="ogr2ogr"):
        generate_pmtiles_from_geoparquet(
            geoparquet_root=sample_geoparquet_data,
            pmtiles_path=pmtiles_path,
            use_tippecanoe=False,  # Force ogr2ogr mode
        )


def test_generate_pmtiles_missing_pmtiles_cli(sample_geoparquet_data, tmp_path, monkeypatch):
    """Test PMTiles generation fails gracefully when pmtiles CLI is missing."""
    from oceanstream.geotrack.tiling import generate_pmtiles_from_geoparquet, MissingDependencyError
    
    # Mock shutil.which to return None for pmtiles
    def mock_which(name):
        if name == "pmtiles":
            return None
        return "/usr/bin/" + name
    
    monkeypatch.setattr(shutil, "which", mock_which)
    
    pmtiles_path = tmp_path / "output.pmtiles"
    
    with pytest.raises(MissingDependencyError, match="pmtiles"):
        generate_pmtiles_from_geoparquet(
            geoparquet_root=sample_geoparquet_data,
            pmtiles_path=pmtiles_path,
            use_tippecanoe=False,  # Force ogr2ogr mode which still needs pmtiles CLI
        )


def test_generate_pmtiles_success(sample_geoparquet_data, tmp_path):
    """Test successful PMTiles generation with mocked subprocess calls."""
    from oceanstream.geotrack.tiling import generate_pmtiles_from_geoparquet
    from unittest.mock import patch
    import subprocess
    
    pmtiles_path = tmp_path / "test_output.pmtiles"
    
    # Mock subprocess.run to prevent actual GDAL calls
    def mock_run(cmd, *args, **kwargs):
        if 'ogr2ogr' in cmd:
            # Create fake mbtiles file
            mbtiles_path = Path(cmd[3])
            mbtiles_path.touch()
        elif 'pmtiles' in cmd and 'convert' in cmd:
            # Create the .pmtiles.tmp file
            pmtiles_tmp = Path(cmd[3])
            pmtiles_tmp.touch()
        return subprocess.CompletedProcess(cmd, 0, stdout=b'', stderr=b'')
    
    with patch('subprocess.run', side_effect=mock_run):
        result_path = generate_pmtiles_from_geoparquet(
            geoparquet_root=sample_geoparquet_data,
            pmtiles_path=pmtiles_path,
            minzoom=0,
            maxzoom=5,
            layer_name="test_layer",
            sample_rate=5,
            time_gap_minutes=60,
            platform_id="sd1030",
            use_tippecanoe=False,  # Use ogr2ogr for simpler test
        )
        
        assert result_path == pmtiles_path
        assert pmtiles_path.exists()


@pytest.mark.skipif(
    shutil.which("ogr2ogr") is None or shutil.which("pmtiles") is None,
    reason="Requires ogr2ogr (GDAL) and pmtiles CLI to be installed"
)
def test_generate_pmtiles_with_custom_params(sample_geoparquet_data, tmp_path):
    """Test PMTiles generation with custom parameters and mocked subprocess."""
    from oceanstream.geotrack.tiling import generate_pmtiles_from_geoparquet
    from unittest.mock import patch
    import subprocess
    
    pmtiles_path = tmp_path / "custom_track.pmtiles"
    
    # Mock subprocess.run to prevent actual GDAL calls
    def mock_run(cmd, *args, **kwargs):
        if 'ogr2ogr' in cmd:
            mbtiles_path = Path(cmd[3])
            mbtiles_path.touch()
        elif 'pmtiles' in cmd and 'convert' in cmd:
            pmtiles_tmp = Path(cmd[3])
            pmtiles_tmp.touch()
        return subprocess.CompletedProcess(cmd, 0, stdout=b'', stderr=b'')
    
    with patch('subprocess.run', side_effect=mock_run):
        result_path = generate_pmtiles_from_geoparquet(
            geoparquet_root=sample_geoparquet_data,
            pmtiles_path=pmtiles_path,
            minzoom=2,
            maxzoom=8,
            layer_name="custom_layer",
            sample_rate=10,
            time_gap_minutes=120,
            platform_id="test_platform",
            use_tippecanoe=False,  # Use ogr2ogr for simpler test
        )
        
        assert result_path.exists()
        assert result_path.name == "custom_track.pmtiles"


@pytest.mark.skipif(
    shutil.which("tippecanoe") is None or shutil.which("pmtiles") is None,
    reason="Requires tippecanoe and pmtiles CLI to be installed"
)
def test_generate_pmtiles_with_tippecanoe(sample_geoparquet_data, tmp_path):
    """Test PMTiles generation with tippecanoe mode (segments and day markers)."""
    from oceanstream.geotrack.tiling import generate_pmtiles_from_geoparquet
    from unittest.mock import patch
    import subprocess
    
    pmtiles_path = tmp_path / "tippecanoe_track.pmtiles"
    
    # Mock subprocess.run to prevent actual tippecanoe calls
    def mock_run(cmd, *args, **kwargs):
        if 'tippecanoe' in cmd:
            # Create fake mbtiles file
            for i, arg in enumerate(cmd):
                if arg == '-o' and i + 1 < len(cmd):
                    mbtiles_path = Path(cmd[i + 1])
                    mbtiles_path.touch()
                    break
        elif 'pmtiles' in cmd and 'convert' in cmd:
            # Create the .pmtiles.tmp file
            pmtiles_tmp = Path(cmd[3])
            pmtiles_tmp.touch()
        return subprocess.CompletedProcess(cmd, 0, stdout=b'', stderr=b'')
    
    with patch('subprocess.run', side_effect=mock_run):
        result_path = generate_pmtiles_from_geoparquet(
            geoparquet_root=sample_geoparquet_data,
            pmtiles_path=pmtiles_path,
            minzoom=0,
            maxzoom=10,
            layer_name="track",
            sample_rate=5,
            time_gap_minutes=60,
            platform_id="sd1030",
            use_tippecanoe=True,  # Test tippecanoe mode
        )
        
        assert result_path == pmtiles_path
        assert pmtiles_path.exists()



def test_upload_pmtiles_to_azure(tmp_path, monkeypatch):
    """Test PMTiles upload function signature."""
    from oceanstream.geotrack.tiling import upload_pmtiles_to_azure
    
    # Create a dummy PMTiles file
    pmtiles_path = tmp_path / "test.pmtiles"
    pmtiles_path.write_bytes(b"dummy pmtiles content")
    
    # Mock the upload function
    upload_called = []
    
    def mock_upload(file_path, container_name, blob_name):
        upload_called.append({
            'file_path': file_path,
            'container_name': container_name,
            'blob_name': blob_name,
        })
    
    # Monkeypatch at module level
    from oceanstream.geotrack.tiling import pmtiles as pmtiles_module
    monkeypatch.setattr(pmtiles_module, "upload_to_azure_blob", mock_upload)
    
    # Call upload function
    upload_pmtiles_to_azure(
        pmtiles_path=pmtiles_path,
        container_name="test-container",
        blob_name="tiles/test.pmtiles",
    )
    
    assert len(upload_called) == 1
    assert upload_called[0]['container_name'] == "test-container"
    assert upload_called[0]['blob_name'] == "tiles/test.pmtiles"
