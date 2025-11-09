"""Tests for PMTiles generation pipeline."""
from __future__ import annotations

import json
import shutil
import struct
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pandas as pd
import pytest

from oceanstream.geotrack.tiling import (
    MissingDependencyError,
    generate_pmtiles_from_geoparquet,
    upload_pmtiles_to_azure,
)


def _wkb_point(lon: float, lat: float) -> bytes:
    """Create little-endian WKB for a 2D Point(lon, lat)."""
    return b"\x01" + struct.pack("<I", 1) + struct.pack("<dd", lon, lat)


@pytest.fixture
def sample_geoparquet(tmp_path: Path) -> Path:
    """Create a sample GeoParquet dataset for testing."""
    geoparquet_root = tmp_path / "geoparquet"
    geoparquet_root.mkdir()

    # Create a simple partitioned dataset
    partition_dir = geoparquet_root / "lat_bin=10_20" / "lon_bin=-20_-10"
    partition_dir.mkdir(parents=True)

    df = pd.DataFrame({
        "latitude": [15.0, 16.0, 17.0],
        "longitude": [-15.0, -14.0, -13.0],
        "platform_id": ["sd1030", "sd1030", "sd1030"],
        "time": pd.to_datetime(["2023-01-01T00:00:00Z", "2023-01-01T01:00:00Z", "2023-01-01T02:00:00Z"]),
        "TEMP_SBE37_MEAN": [18.5, 18.6, 18.7],
        "geometry": [
            _wkb_point(-15.0, 15.0),
            _wkb_point(-14.0, 16.0),
            _wkb_point(-13.0, 17.0),
        ],
    })

    parquet_file = partition_dir / "data.parquet"
    df.to_parquet(parquet_file)

    return geoparquet_root


def test_missing_dependency_error():
    """Test that MissingDependencyError is a RuntimeError."""
    error = MissingDependencyError("test error")
    assert isinstance(error, RuntimeError)
    assert str(error) == "test error"


@patch("oceanstream.geotrack.tiling.pmtiles.shutil.which")
def test_require_cli_missing(mock_which):
    """Test that missing CLI tools raise MissingDependencyError."""
    mock_which.return_value = None

    with pytest.raises(MissingDependencyError) as exc_info:
        from oceanstream.geotrack.tiling.pmtiles import _require_cli
        _require_cli("ogr2ogr")

    assert "ogr2ogr" in str(exc_info.value)
    assert "not found on PATH" in str(exc_info.value)


@patch("oceanstream.geotrack.tiling.pmtiles.shutil.which")
def test_require_cli_present(mock_which):
    """Test that present CLI tools don't raise errors."""
    mock_which.return_value = "/usr/bin/ogr2ogr"

    from oceanstream.geotrack.tiling.pmtiles import _require_cli
    # Should not raise
    _require_cli("ogr2ogr")


@patch("oceanstream.geotrack.tiling.pmtiles.subprocess.run")
@patch("oceanstream.geotrack.tiling.pmtiles.shutil.which")
def test_generate_pmtiles_basic(mock_which, mock_run, sample_geoparquet, tmp_path):
    """Test basic PMTiles generation flow with tippecanoe."""
    mock_which.return_value = "/usr/bin/tool"
    
    # Mock subprocess.run to create the pmtiles temp file when pmtiles convert is called
    def mock_subprocess_side_effect(cmd, **kwargs):
        if cmd[0] == "pmtiles" and cmd[1] == "convert":
            # Create the output file that pmtiles would create
            output_path = Path(cmd[3])
            output_path.write_bytes(b"fake pmtiles data")
        return MagicMock(returncode=0)
    
    mock_run.side_effect = mock_subprocess_side_effect

    pmtiles_output = tmp_path / "output.pmtiles"

    result = generate_pmtiles_from_geoparquet(
        geoparquet_root=sample_geoparquet,
        pmtiles_path=pmtiles_output,
        minzoom=0,
        maxzoom=10,
        layer_name="test_layer",
        use_tippecanoe=True,
    )

    # Verify CLI tools were checked (tippecanoe and pmtiles)
    assert mock_which.call_count == 2
    mock_which.assert_any_call("tippecanoe")
    mock_which.assert_any_call("pmtiles")

    # Verify subprocess calls (tippecanoe + pmtiles convert)
    assert mock_run.call_count == 2

    # Check tippecanoe call
    tippecanoe_call = mock_run.call_args_list[0]
    tippecanoe_cmd = tippecanoe_call[0][0]
    assert tippecanoe_cmd[0] == "tippecanoe"
    assert "-l" in tippecanoe_cmd
    assert "test_layer" in tippecanoe_cmd
    assert "-Z" in tippecanoe_cmd
    assert "-z" in tippecanoe_cmd

    # Check pmtiles convert call
    pmtiles_call = mock_run.call_args_list[1]
    pmtiles_cmd = pmtiles_call[0][0]
    assert pmtiles_cmd[0] == "pmtiles"
    assert pmtiles_cmd[1] == "convert"

    assert result == pmtiles_output


@patch("oceanstream.geotrack.tiling.pmtiles.subprocess.run")
@patch("oceanstream.geotrack.tiling.pmtiles.shutil.which")
def test_generate_pmtiles_with_measurements(mock_which, mock_run, sample_geoparquet, tmp_path):
    """Test PMTiles generation with measurement columns."""
    mock_which.return_value = "/usr/bin/tool"
    
    def mock_subprocess_side_effect(cmd, **kwargs):
        if cmd[0] == "pmtiles" and cmd[1] == "convert":
            Path(cmd[3]).write_bytes(b"fake pmtiles data")
        return MagicMock(returncode=0)
    
    mock_run.side_effect = mock_subprocess_side_effect

    pmtiles_output = tmp_path / "output.pmtiles"

    generate_pmtiles_from_geoparquet(
        geoparquet_root=sample_geoparquet,
        pmtiles_path=pmtiles_output,
        use_tippecanoe=True,
        include_measurements=True,
        measurement_columns=["TEMP_SBE37_MEAN"],
    )

    # Verify tippecanoe was called
    assert mock_run.call_count == 2
    tippecanoe_call = mock_run.call_args_list[0]
    tippecanoe_cmd = tippecanoe_call[0][0]
    assert tippecanoe_cmd[0] == "tippecanoe"


@patch("oceanstream.geotrack.tiling.pmtiles.subprocess.run")
@patch("oceanstream.geotrack.tiling.pmtiles.shutil.which")
def test_generate_pmtiles_with_segments(mock_which, mock_run, sample_geoparquet, tmp_path):
    """Test PMTiles generation with time-based segments."""
    mock_which.return_value = "/usr/bin/tool"
    
    def mock_subprocess_side_effect(cmd, **kwargs):
        if cmd[0] == "pmtiles" and cmd[1] == "convert":
            Path(cmd[3]).write_bytes(b"fake pmtiles data")
        return MagicMock(returncode=0)
    
    mock_run.side_effect = mock_subprocess_side_effect

    pmtiles_output = tmp_path / "output.pmtiles"

    generate_pmtiles_from_geoparquet(
        geoparquet_root=sample_geoparquet,
        pmtiles_path=pmtiles_output,
        use_tippecanoe=True,
        time_gap_minutes=60,
        sample_rate=1,
    )

    # Verify tippecanoe was called with correct parameters
    tippecanoe_call = mock_run.call_args_list[0]
    tippecanoe_cmd = tippecanoe_call[0][0]
    assert tippecanoe_cmd[0] == "tippecanoe"
    assert "-Z" in tippecanoe_cmd
    assert "-z" in tippecanoe_cmd


@patch("oceanstream.geotrack.tiling.pmtiles.subprocess.run")
@patch("oceanstream.geotrack.tiling.pmtiles.shutil.which")
def test_generate_pmtiles_creates_output_directory(mock_which, mock_run, sample_geoparquet, tmp_path):
    """Test that output directory is created if it doesn't exist."""
    mock_which.return_value = "/usr/bin/tool"
    
    def mock_subprocess_side_effect(cmd, **kwargs):
        if cmd[0] == "pmtiles" and cmd[1] == "convert":
            Path(cmd[3]).write_bytes(b"fake pmtiles data")
        return MagicMock(returncode=0)
    
    mock_run.side_effect = mock_subprocess_side_effect

    nested_output = tmp_path / "nested" / "dir" / "output.pmtiles"

    generate_pmtiles_from_geoparquet(
        geoparquet_root=sample_geoparquet,
        pmtiles_path=nested_output,
        use_tippecanoe=True,
    )

    # Verify parent directories were created
    assert nested_output.parent.exists()


@patch("oceanstream.geotrack.tiling.pmtiles.subprocess.run")
@patch("oceanstream.geotrack.tiling.pmtiles.shutil.which")
def test_generate_pmtiles_subprocess_error(mock_which, mock_run, sample_geoparquet, tmp_path):
    """Test that subprocess errors are propagated."""
    mock_which.return_value = "/usr/bin/tool"
    mock_run.side_effect = Exception("Command failed")

    pmtiles_output = tmp_path / "output.pmtiles"

    with pytest.raises(Exception) as exc_info:
        generate_pmtiles_from_geoparquet(
            geoparquet_root=sample_geoparquet,
            pmtiles_path=pmtiles_output,
            use_tippecanoe=True,
        )

    assert "Command failed" in str(exc_info.value)


@patch("oceanstream.geotrack.tiling.pmtiles.upload_to_azure_blob")
def test_upload_pmtiles_to_azure(mock_upload, tmp_path):
    """Test PMTiles upload to Azure Blob Storage."""
    pmtiles_file = tmp_path / "test.pmtiles"
    pmtiles_file.write_bytes(b"fake pmtiles data")

    upload_pmtiles_to_azure(
        pmtiles_path=pmtiles_file,
        container_name="test-container",
        blob_name="tiles/test.pmtiles",
    )

    # Verify upload was called with correct parameters
    mock_upload.assert_called_once_with(
        file_path=str(pmtiles_file),
        container_name="test-container",
        blob_name="tiles/test.pmtiles",
    )


def test_pmtiles_module_exports():
    """Test that the tiling module exports the expected functions."""
    from oceanstream.geotrack import tiling
    
    assert hasattr(tiling, "generate_pmtiles_from_geoparquet")
    assert hasattr(tiling, "upload_pmtiles_to_azure")
    assert hasattr(tiling, "MissingDependencyError")
    assert "generate_pmtiles_from_geoparquet" in tiling.__all__
    assert "upload_pmtiles_to_azure" in tiling.__all__
    assert "MissingDependencyError" in tiling.__all__
