"""Unit tests for PMTiles generation in geotrack processor."""
import shutil
from pathlib import Path
from unittest.mock import Mock, patch
import pytest


def test_processor_generate_pmtiles_success(tmp_path):
    """Test successful PMTiles generation through processor."""
    from oceanstream.geotrack.processor import GeotrackProcessor
    from oceanstream.providers import get_provider
    
    provider = get_provider("saildrone")
    processor = GeotrackProcessor(provider, verbose=True)
    
    geoparquet_root = tmp_path / "geoparquet"
    geoparquet_root.mkdir()
    
    # Mock generate_pmtiles_from_geoparquet at the processor level
    pmtiles_output = tmp_path / "tiles" / "track.pmtiles"
    
    def mock_generate(*args, **kwargs):
        pmtiles_output.parent.mkdir(parents=True, exist_ok=True)
        pmtiles_output.touch()
        return pmtiles_output
    
    # Mock at the import site in processor.py (locally imported inside the method)
    with patch('oceanstream.geotrack.tiling.generate_pmtiles_from_geoparquet', side_effect=mock_generate):
        result = processor.generate_pmtiles_dataset(
            geoparquet_root=geoparquet_root,
            minzoom=0,
            maxzoom=10,
            layer_name="test_layer",
            sample_rate=5,
            time_gap_minutes=60,
            platform_id="sd1030",
        )
        
        # Check result
        assert result is not None
        assert result == tmp_path / "tiles" / "track.pmtiles"
        assert result.exists()


def test_processor_generate_pmtiles_missing_dependency(tmp_path):
    """Test graceful failure when dependencies are missing."""
    from oceanstream.geotrack.processor import GeotrackProcessor
    from oceanstream.geotrack.tiling import MissingDependencyError
    from oceanstream.providers import get_provider
    
    provider = get_provider("saildrone")
    processor = GeotrackProcessor(provider, verbose=True)
    
    geoparquet_root = tmp_path / "geoparquet"
    geoparquet_root.mkdir()
    
    # Mock the PMTiles generation to raise MissingDependencyError
    with patch('oceanstream.geotrack.tiling.generate_pmtiles_from_geoparquet') as mock_gen:
        mock_gen.side_effect = MissingDependencyError("ogr2ogr not found")
        
        result = processor.generate_pmtiles_dataset(
            geoparquet_root=geoparquet_root,
            sample_rate=5,
            time_gap_minutes=60,
        )
        
        # Should return None on failure
        assert result is None


def test_processor_generate_pmtiles_exception(tmp_path):
    """Test graceful failure on unexpected exceptions."""
    from oceanstream.geotrack.processor import GeotrackProcessor
    from oceanstream.providers import get_provider
    
    provider = get_provider("saildrone")
    processor = GeotrackProcessor(provider, verbose=True)
    
    geoparquet_root = tmp_path / "geoparquet"
    geoparquet_root.mkdir()
    
    # Mock the PMTiles generation to raise a generic exception
    with patch('oceanstream.geotrack.tiling.generate_pmtiles_from_geoparquet') as mock_gen:
        mock_gen.side_effect = RuntimeError("Unexpected error")
        
        result = processor.generate_pmtiles_dataset(
            geoparquet_root=geoparquet_root,
            sample_rate=10,
            time_gap_minutes=120,
        )
        
        # Should return None on failure
        assert result is None


def test_processor_generate_pmtiles_creates_tiles_dir(tmp_path):
    """Test that tiles directory is created if it doesn't exist."""
    from oceanstream.geotrack.processor import GeotrackProcessor
    from oceanstream.providers import get_provider
    
    provider = get_provider("saildrone")
    processor = GeotrackProcessor(provider, verbose=False)
    
    geoparquet_root = tmp_path / "geoparquet"
    geoparquet_root.mkdir()
    
    # Ensure tiles directory doesn't exist initially
    tiles_dir = tmp_path / "tiles"
    assert not tiles_dir.exists()
    
    # Mock generate_pmtiles_from_geoparquet
    pmtiles_output = tiles_dir / "track.pmtiles"
    
    def mock_generate(*args, **kwargs):
        pmtiles_output.parent.mkdir(parents=True, exist_ok=True)
        pmtiles_output.touch()
        return pmtiles_output
    
    with patch('oceanstream.geotrack.tiling.generate_pmtiles_from_geoparquet', side_effect=mock_generate):
        result = processor.generate_pmtiles_dataset(
            geoparquet_root=geoparquet_root,
            sample_rate=3,
            time_gap_minutes=90,
        )
        
        # Check that tiles directory was created
        assert tiles_dir.exists()
        assert result == tiles_dir / "track.pmtiles"


def test_processor_generate_pmtiles_verbose_output(tmp_path, capsys):
    """Test verbose output during PMTiles generation."""
    from oceanstream.geotrack.processor import GeotrackProcessor
    from oceanstream.providers import get_provider
    
    provider = get_provider("saildrone")
    processor = GeotrackProcessor(provider, verbose=True)
    
    geoparquet_root = tmp_path / "geoparquet"
    geoparquet_root.mkdir()
    
    # Mock generate_pmtiles_from_geoparquet
    pmtiles_output = tmp_path / "tiles" / "track.pmtiles"
    
    def mock_generate(*args, **kwargs):
        pmtiles_output.parent.mkdir(parents=True, exist_ok=True)
        pmtiles_output.touch()
        return pmtiles_output
    
    with patch('oceanstream.geotrack.tiling.generate_pmtiles_from_geoparquet', side_effect=mock_generate):
        processor.generate_pmtiles_dataset(
            geoparquet_root=geoparquet_root,
            layer_name="verbose_test",
            sample_rate=2,
            time_gap_minutes=30,
        )
        
        captured = capsys.readouterr()
        assert "generating pmtiles" in captured.out.lower() or "pmtiles" in captured.out.lower()
        assert "track.pmtiles" in captured.out


def test_processor_generate_pmtiles_missing_dependency_verbose(tmp_path, capsys):
    """Test verbose error messages when dependencies are missing."""
    from oceanstream.geotrack.processor import GeotrackProcessor
    from oceanstream.geotrack.tiling import MissingDependencyError
    from oceanstream.providers import get_provider
    
    provider = get_provider("saildrone")
    processor = GeotrackProcessor(provider, verbose=True)
    
    geoparquet_root = tmp_path / "geoparquet"
    geoparquet_root.mkdir()
    
    with patch('oceanstream.geotrack.tiling.generate_pmtiles_from_geoparquet') as mock_gen:
        mock_gen.side_effect = MissingDependencyError("ogr2ogr not found")
        
        processor.generate_pmtiles_dataset(
            geoparquet_root=geoparquet_root,
            sample_rate=5,
            time_gap_minutes=60,
        )
        
        captured = capsys.readouterr()
        assert "PMTiles generation failed" in captured.out
        assert "ogr2ogr" in captured.out or "Install required tools" in captured.out


def test_processor_generate_pmtiles_with_custom_segmentation_params(tmp_path):
    """Test PMTiles generation with custom segmentation parameters."""
    from oceanstream.geotrack.processor import GeotrackProcessor
    from oceanstream.providers import get_provider
    
    provider = get_provider("saildrone")
    processor = GeotrackProcessor(provider, verbose=True)
    
    geoparquet_root = tmp_path / "geoparquet"
    geoparquet_root.mkdir()
    
    # Mock generate_pmtiles_from_geoparquet
    pmtiles_output = tmp_path / "tiles" / "track.pmtiles"
    
    def mock_generate(*args, **kwargs):
        # Verify parameters are passed correctly
        assert kwargs.get('sample_rate') == 10
        assert kwargs.get('time_gap_minutes') == 120
        assert kwargs.get('platform_id') == "test_sd1234"
        pmtiles_output.parent.mkdir(parents=True, exist_ok=True)
        pmtiles_output.touch()
        return pmtiles_output
    
    with patch('oceanstream.geotrack.tiling.generate_pmtiles_from_geoparquet', side_effect=mock_generate):
        result = processor.generate_pmtiles_dataset(
            geoparquet_root=geoparquet_root,
            minzoom=1,
            maxzoom=12,
            layer_name="custom_track",
            sample_rate=10,  # Sample every 10th point
            time_gap_minutes=120,  # 2-hour gaps split segments
            platform_id="test_sd1234",
        )
        
        # Check result
        assert result is not None
        assert result == tmp_path / "tiles" / "track.pmtiles"
        assert result.exists()

