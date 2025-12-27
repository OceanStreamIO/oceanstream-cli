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
from oceanstream.geotrack.tiling.pmtiles import (
    _discover_measurement_columns,
    DEFAULT_EXCLUDE_PATTERNS,
    SYSTEM_COLUMNS,
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


# =============================================================================
# Auto-discovery tests
# =============================================================================

@pytest.fixture
def sample_geoparquet_with_many_columns(tmp_path: Path) -> Path:
    """Create a sample GeoParquet with many measurement columns for testing auto-discovery."""
    geoparquet_root = tmp_path / "geoparquet"
    partition_dir = geoparquet_root / "lat_bin=10_20" / "lon_bin=-20_-10"
    partition_dir.mkdir(parents=True)

    df = pd.DataFrame({
        # System columns (should be excluded)
        "latitude": [15.0, 16.0, 17.0],
        "longitude": [-15.0, -14.0, -13.0],
        "time": pd.to_datetime(["2023-01-01T00:00:00Z", "2023-01-01T01:00:00Z", "2023-01-01T02:00:00Z"]),
        "platform_id": ["sd1030", "sd1030", "sd1030"],
        "campaign_id": ["tpos_2023", "tpos_2023", "tpos_2023"],
        "trajectory": [1, 1, 1],
        # Measurement columns (should be included)
        "TEMP_AIR_MEAN": [25.0, 25.1, 25.2],
        "TEMP_SBE37_MEAN": [18.5, 18.6, 18.7],
        "SAL_SBE37_MEAN": [35.0, 35.1, 35.2],
        "WIND_SPEED_MEAN": [5.0, 5.1, 5.2],
        "WAVE_SIGNIFICANT_HEIGHT": [1.0, 1.1, 1.2],
        "CHLOR_WETLABS_MEAN": [0.1, 0.2, 0.3],
        # Columns that should be excluded by default patterns
        "TEMP_AIR_STDDEV": [0.1, 0.1, 0.1],  # _STDDEV suffix
        "WIND_SPEED_MIN": [4.0, 4.1, 4.2],   # _MIN suffix
        "WIND_SPEED_MAX": [6.0, 6.1, 6.2],   # _MAX suffix
        "PITCH_FILTERED_PEAK": [1.0, 1.0, 1.0],  # _PEAK suffix
        "UWND_MEAN": [3.0, 3.1, 3.2],        # UWND_ prefix
        "VWND_MEAN": [2.0, 2.1, 2.2],        # VWND_ prefix
        "WING_ROLL_FILTERED_MEAN": [0.5, 0.5, 0.5],  # WING_ prefix
        "HDG": [180.0, 181.0, 182.0],        # Raw HDG (no _FILTERED)
        "SOG": [2.5, 2.6, 2.7],              # Raw SOG (no _FILTERED)
        # Additional measurements that should be included
        "HDG_FILTERED_MEAN": [180.0, 181.0, 182.0],
        "SOG_FILTERED_MEAN": [2.5, 2.6, 2.7],
    })

    parquet_file = partition_dir / "data.parquet"
    df.to_parquet(parquet_file)

    return parquet_file


def test_discover_measurement_columns_with_defaults(sample_geoparquet_with_many_columns):
    """Test auto-discovery filters columns correctly with default patterns."""
    discovered = _discover_measurement_columns(sample_geoparquet_with_many_columns)
    
    # Should include measurement columns
    assert "TEMP_AIR_MEAN" in discovered
    assert "TEMP_SBE37_MEAN" in discovered
    assert "SAL_SBE37_MEAN" in discovered
    assert "WIND_SPEED_MEAN" in discovered
    assert "WAVE_SIGNIFICANT_HEIGHT" in discovered
    assert "CHLOR_WETLABS_MEAN" in discovered
    assert "HDG_FILTERED_MEAN" in discovered
    assert "SOG_FILTERED_MEAN" in discovered
    
    # Should exclude system columns (lowercase)
    assert "latitude" not in discovered
    assert "longitude" not in discovered
    assert "time" not in discovered
    assert "platform_id" not in discovered
    assert "campaign_id" not in discovered
    assert "trajectory" not in discovered
    
    # Should exclude by default patterns
    assert "TEMP_AIR_STDDEV" not in discovered  # _STDDEV
    assert "WIND_SPEED_MIN" not in discovered   # _MIN
    assert "WIND_SPEED_MAX" not in discovered   # _MAX
    assert "PITCH_FILTERED_PEAK" not in discovered  # _PEAK
    assert "UWND_MEAN" not in discovered        # UWND_
    assert "VWND_MEAN" not in discovered        # VWND_
    assert "WING_ROLL_FILTERED_MEAN" not in discovered  # WING_
    assert "HDG" not in discovered              # Raw HDG
    assert "SOG" not in discovered              # Raw SOG


def test_discover_measurement_columns_no_exclusions(sample_geoparquet_with_many_columns):
    """Test auto-discovery includes all columns when exclude_patterns is empty."""
    discovered = _discover_measurement_columns(
        sample_geoparquet_with_many_columns,
        exclude_patterns=[]  # Empty = no exclusions
    )
    
    # Should include all uppercase columns
    assert "TEMP_AIR_MEAN" in discovered
    assert "TEMP_AIR_STDDEV" in discovered  # Now included
    assert "WIND_SPEED_MIN" in discovered   # Now included
    assert "WIND_SPEED_MAX" in discovered   # Now included
    assert "PITCH_FILTERED_PEAK" in discovered  # Now included
    assert "UWND_MEAN" in discovered        # Now included
    assert "VWND_MEAN" in discovered        # Now included
    assert "WING_ROLL_FILTERED_MEAN" in discovered  # Now included
    assert "HDG" in discovered              # Now included
    assert "SOG" in discovered              # Now included
    
    # System columns still excluded (lowercase)
    assert "latitude" not in discovered
    assert "longitude" not in discovered


def test_discover_measurement_columns_custom_patterns(sample_geoparquet_with_many_columns):
    """Test auto-discovery with custom exclude patterns."""
    # Only exclude STDDEV columns
    discovered = _discover_measurement_columns(
        sample_geoparquet_with_many_columns,
        exclude_patterns=[r'.*_STDDEV$']
    )
    
    # Should include measurement columns
    assert "TEMP_AIR_MEAN" in discovered
    
    # Should exclude STDDEV
    assert "TEMP_AIR_STDDEV" not in discovered
    
    # Should include columns that would be excluded by defaults
    assert "WIND_SPEED_MIN" in discovered   # No longer excluded
    assert "WIND_SPEED_MAX" in discovered   # No longer excluded
    assert "UWND_MEAN" in discovered        # No longer excluded
    assert "HDG" in discovered              # No longer excluded


def test_default_exclude_patterns_valid_regex():
    """Test that default exclude patterns are valid regex patterns."""
    import re
    
    for pattern in DEFAULT_EXCLUDE_PATTERNS:
        # Should not raise
        compiled = re.compile(pattern, re.IGNORECASE)
        assert compiled is not None


def test_system_columns_contains_expected_values():
    """Test that SYSTEM_COLUMNS contains the expected system columns."""
    assert "time" in SYSTEM_COLUMNS
    assert "latitude" in SYSTEM_COLUMNS
    assert "longitude" in SYSTEM_COLUMNS
    assert "platform_id" in SYSTEM_COLUMNS
    assert "campaign_id" in SYSTEM_COLUMNS
    assert "trajectory" in SYSTEM_COLUMNS
    assert "geometry" in SYSTEM_COLUMNS


@patch("oceanstream.geotrack.tiling.pmtiles.subprocess.run")
@patch("oceanstream.geotrack.tiling.pmtiles.shutil.which")
def test_generate_pmtiles_auto_discovery(mock_which, mock_run, sample_geoparquet_with_many_columns, tmp_path):
    """Test PMTiles generation uses auto-discovery when measurement_columns=None."""
    mock_which.return_value = "/usr/bin/tool"
    
    ndjson_content = []
    
    def mock_subprocess_side_effect(cmd, **kwargs):
        if cmd[0] == "tippecanoe":
            # Capture the NDJSON file path to inspect later
            ndjson_path = cmd[-1]
            with open(ndjson_path) as f:
                ndjson_content.extend([json.loads(line) for line in f])
        if cmd[0] == "pmtiles" and cmd[1] == "convert":
            Path(cmd[3]).write_bytes(b"fake pmtiles data")
        return MagicMock(returncode=0)
    
    mock_run.side_effect = mock_subprocess_side_effect

    # Use the parent directory as geoparquet root
    geoparquet_root = sample_geoparquet_with_many_columns.parent.parent.parent
    pmtiles_output = tmp_path / "output.pmtiles"

    generate_pmtiles_from_geoparquet(
        geoparquet_root=geoparquet_root,
        pmtiles_path=pmtiles_output,
        use_tippecanoe=True,
        include_measurements=True,
        measurement_columns=None,  # Auto-discover
    )

    # Verify NDJSON was created with auto-discovered columns
    assert len(ndjson_content) > 0
    
    # Find a LineString feature (segment) to check properties
    segment = next((f for f in ndjson_content if f["geometry"]["type"] == "LineString"), None)
    if segment:
        props = segment["properties"]
        # Should have auto-discovered measurement columns
        assert "TEMP_AIR_MEAN" in props or "TEMP_SBE37_MEAN" in props
        # Should NOT have excluded columns
        assert "TEMP_AIR_STDDEV" not in props


@patch("oceanstream.geotrack.tiling.pmtiles.subprocess.run")
@patch("oceanstream.geotrack.tiling.pmtiles.shutil.which")
def test_generate_pmtiles_with_exclude_patterns(mock_which, mock_run, sample_geoparquet_with_many_columns, tmp_path):
    """Test PMTiles generation with custom exclude patterns."""
    mock_which.return_value = "/usr/bin/tool"
    
    def mock_subprocess_side_effect(cmd, **kwargs):
        if cmd[0] == "pmtiles" and cmd[1] == "convert":
            Path(cmd[3]).write_bytes(b"fake pmtiles data")
        return MagicMock(returncode=0)
    
    mock_run.side_effect = mock_subprocess_side_effect

    geoparquet_root = sample_geoparquet_with_many_columns.parent.parent.parent
    pmtiles_output = tmp_path / "output.pmtiles"

    # Should not raise with custom exclude patterns
    generate_pmtiles_from_geoparquet(
        geoparquet_root=geoparquet_root,
        pmtiles_path=pmtiles_output,
        use_tippecanoe=True,
        include_measurements=True,
        measurement_columns=None,  # Auto-discover
        exclude_patterns=[r'.*_STDDEV$'],  # Only exclude STDDEV
    )

    assert mock_run.call_count == 2  # tippecanoe + pmtiles convert


@patch("oceanstream.geotrack.tiling.pmtiles.subprocess.run")
@patch("oceanstream.geotrack.tiling.pmtiles.shutil.which")
def test_generate_pmtiles_explicit_columns_override_discovery(mock_which, mock_run, sample_geoparquet_with_many_columns, tmp_path):
    """Test that explicit measurement_columns bypasses auto-discovery."""
    mock_which.return_value = "/usr/bin/tool"
    
    ndjson_content = []
    
    def mock_subprocess_side_effect(cmd, **kwargs):
        if cmd[0] == "tippecanoe":
            ndjson_path = cmd[-1]
            with open(ndjson_path) as f:
                ndjson_content.extend([json.loads(line) for line in f])
        if cmd[0] == "pmtiles" and cmd[1] == "convert":
            Path(cmd[3]).write_bytes(b"fake pmtiles data")
        return MagicMock(returncode=0)
    
    mock_run.side_effect = mock_subprocess_side_effect

    geoparquet_root = sample_geoparquet_with_many_columns.parent.parent.parent
    pmtiles_output = tmp_path / "output.pmtiles"

    # Explicitly specify only one column
    generate_pmtiles_from_geoparquet(
        geoparquet_root=geoparquet_root,
        pmtiles_path=pmtiles_output,
        use_tippecanoe=True,
        include_measurements=True,
        measurement_columns=["TEMP_AIR_MEAN"],  # Explicit - bypasses auto-discovery
    )

    # Find a segment feature
    segment = next((f for f in ndjson_content if f["geometry"]["type"] == "LineString"), None)
    if segment:
        props = segment["properties"]
        # Should only have the explicitly requested column
        assert "TEMP_AIR_MEAN" in props
        # Should NOT have other columns (auto-discovery was bypassed)
        assert "TEMP_SBE37_MEAN" not in props
        assert "WIND_SPEED_MEAN" not in props
