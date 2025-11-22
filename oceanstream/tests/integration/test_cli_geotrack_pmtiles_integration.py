"""Integration tests for geotrack CLI with PMTiles generation."""
import shutil
import subprocess
from pathlib import Path
import pytest


def _gdal_supports_parquet() -> bool:
    """Check if GDAL/ogr2ogr supports Parquet format."""
    if shutil.which("ogr2ogr") is None:
        return False
    try:
        result = subprocess.run(
            ["ogr2ogr", "--formats"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return "Parquet" in result.stdout
    except Exception:
        return False


@pytest.mark.integration
@pytest.mark.skipif(
    not _gdal_supports_parquet() or shutil.which("pmtiles") is None,
    reason="Requires ogr2ogr (GDAL) with Parquet support and pmtiles CLI"
)
def test_cli_geotrack_with_pmtiles(tmp_path: Path, monkeypatch):
    """Test end-to-end CLI with PMTiles generation enabled."""
    in_dir = tmp_path / "in"
    out_dir = tmp_path / "out"
    in_dir.mkdir(parents=True)
    out_dir.mkdir(parents=True)

    # Copy test data
    project_root = Path(__file__).resolve().parents[3]
    diverse_csv = project_root / "oceanstream" / "tests" / "data" / "raw_data" / "sd_diverse_subset.csv"
    assert diverse_csv.exists(), "Expected test data file is missing"
    shutil.copy(diverse_csv, in_dir / diverse_csv.name)

    monkeypatch.setenv("PYTHONUTF8", "1")
    monkeypatch.setenv("PYTHONWARNINGS", "ignore")
    monkeypatch.setenv("NO_COLOR", "1")

    from typer.testing import CliRunner
    from oceanstream import cli as cli_module

    runner = CliRunner()
    result = runner.invoke(
        cli_module.app,
        [
            "process",
            "geotrack",
            "convert",
            "--input-source", str(in_dir),
            "--output-dir", str(out_dir / "geoparquet"),
            "--generate-pmtiles",
            "--pmtiles-minzoom", "0",
            "--pmtiles-maxzoom", "5",
            "--pmtiles-layer", "test_track",
            "--pmtiles-sample-rate", "5",
            "--pmtiles-time-gap", "60",
            "--yes",
            "-v",
        ],
    )
    
    assert result.exit_code == 0, f"CLI failed: {result.exit_code}\n{result.output}"
    
    # Check GeoParquet files were created
    geoparquet_dir = out_dir / "geoparquet"
    parquet_files = list(geoparquet_dir.rglob("*.parquet"))
    assert parquet_files, "No GeoParquet files were created"
    
    # Check PMTiles file was created in tiles/ directory
    tiles_dir = out_dir / "tiles"
    assert tiles_dir.exists(), f"Tiles directory not created. Output:\n{result.output}"
    
    pmtiles_file = tiles_dir / "track.pmtiles"
    assert pmtiles_file.exists(), f"PMTiles file not created at {pmtiles_file}"
    assert pmtiles_file.stat().st_size > 0, "PMTiles file is empty"
    
    # Verify output mentions PMTiles
    assert "PMTiles" in result.output or "pmtiles" in result.output.lower()
    assert "track.pmtiles" in result.output


@pytest.mark.integration
def test_cli_geotrack_without_pmtiles(tmp_path: Path, monkeypatch):
    """Test that PMTiles is not generated when flag is not provided."""
    # Use isolated metadata directory
    metadata_dir = tmp_path / "metadata"
    metadata_dir.mkdir(parents=True, exist_ok=True)
    from oceanstream.config.settings import Settings
    monkeypatch.setattr(Settings, "METADATA_DIR", metadata_dir)
    
    in_dir = tmp_path / "in"
    out_dir = tmp_path / "out"
    in_dir.mkdir(parents=True)
    out_dir.mkdir(parents=True)

    # Copy test data
    project_root = Path(__file__).resolve().parents[3]
    diverse_csv = project_root / "oceanstream" / "tests" / "data" / "raw_data" / "sd_diverse_subset.csv"
    assert diverse_csv.exists(), "Expected test data file is missing"
    shutil.copy(diverse_csv, in_dir / diverse_csv.name)

    monkeypatch.setenv("PYTHONUTF8", "1")
    monkeypatch.setenv("PYTHONWARNINGS", "ignore")
    monkeypatch.setenv("NO_COLOR", "1")

    from typer.testing import CliRunner
    from oceanstream import cli as cli_module

    runner = CliRunner()
    result = runner.invoke(
        cli_module.app,
        [
            "process",
            "geotrack",
            "convert",
            "--input-source", str(in_dir),
            "--output-dir", str(out_dir / "geoparquet"),
            "--yes",
            "-v",
        ],
    )
    
    assert result.exit_code == 0, f"CLI failed: {result.exit_code}\n{result.output}"
    
    # Check GeoParquet files were created
    geoparquet_dir = out_dir / "geoparquet"
    parquet_files = list(geoparquet_dir.rglob("*.parquet"))
    assert parquet_files, "No GeoParquet files were created"
    
    # Check PMTiles was NOT created
    tiles_dir = out_dir / "tiles"
    assert not tiles_dir.exists(), "Tiles directory should not exist when --generate-pmtiles not used"


@pytest.mark.integration
def test_cli_geotrack_pmtiles_missing_tools(tmp_path: Path, monkeypatch):
    """Test graceful failure when PMTiles tools are missing."""
    # Use isolated metadata directory
    metadata_dir = tmp_path / "metadata"
    metadata_dir.mkdir(parents=True, exist_ok=True)
    from oceanstream.config.settings import Settings
    monkeypatch.setattr(Settings, "METADATA_DIR", metadata_dir)
    
    in_dir = tmp_path / "in"
    out_dir = tmp_path / "out"
    in_dir.mkdir(parents=True)
    out_dir.mkdir(parents=True)

    # Copy test data
    project_root = Path(__file__).resolve().parents[3]
    diverse_csv = project_root / "oceanstream" / "tests" / "data" / "raw_data" / "sd_diverse_subset.csv"
    assert diverse_csv.exists(), "Expected test data file is missing"
    shutil.copy(diverse_csv, in_dir / diverse_csv.name)

    monkeypatch.setenv("PYTHONUTF8", "1")
    monkeypatch.setenv("PYTHONWARNINGS", "ignore")
    monkeypatch.setenv("NO_COLOR", "1")
    
    # Mock shutil.which to simulate missing tools
    original_which = shutil.which
    def mock_which(name):
        if name in ("ogr2ogr", "pmtiles"):
            return None
        return original_which(name)
    
    monkeypatch.setattr(shutil, "which", mock_which)

    from typer.testing import CliRunner
    from oceanstream import cli as cli_module

    runner = CliRunner()
    result = runner.invoke(
        cli_module.app,
        [
            "process",
            "geotrack",
            "convert",
            "--input-source", str(in_dir),
            "--output-dir", str(out_dir / "geoparquet"),
            "--generate-pmtiles",
            "--yes",
            "-v",
        ],
    )
    
    # Should still succeed (GeoParquet created), but PMTiles should fail gracefully
    assert result.exit_code == 0, f"CLI should not fail when PMTiles tools missing: {result.output}"
    
    # Check GeoParquet files were created
    geoparquet_dir = out_dir / "geoparquet"
    parquet_files = list(geoparquet_dir.rglob("*.parquet"))
    assert parquet_files, "GeoParquet files should still be created"
    
    # Check PMTiles was NOT created
    tiles_dir = out_dir / "tiles"
    if tiles_dir.exists():
        pmtiles_file = tiles_dir / "track.pmtiles"
        assert not pmtiles_file.exists(), "PMTiles should not be created when tools are missing"


@pytest.mark.integration
@pytest.mark.skipif(
    not _gdal_supports_parquet() or shutil.which("pmtiles") is None,
    reason="Requires ogr2ogr (GDAL) with Parquet support and pmtiles CLI"
)
def test_cli_geotrack_pmtiles_custom_params(tmp_path: Path, monkeypatch):
    """Test PMTiles generation with custom zoom levels and layer name."""
    in_dir = tmp_path / "in"
    out_dir = tmp_path / "out"
    in_dir.mkdir(parents=True)
    out_dir.mkdir(parents=True)

    # Copy test data
    project_root = Path(__file__).resolve().parents[3]
    diverse_csv = project_root / "oceanstream" / "tests" / "data" / "raw_data" / "sd_diverse_subset.csv"
    assert diverse_csv.exists(), "Expected test data file is missing"
    shutil.copy(diverse_csv, in_dir / diverse_csv.name)

    monkeypatch.setenv("PYTHONUTF8", "1")
    monkeypatch.setenv("PYTHONWARNINGS", "ignore")
    monkeypatch.setenv("NO_COLOR", "1")

    from typer.testing import CliRunner
    from oceanstream import cli as cli_module

    runner = CliRunner()
    result = runner.invoke(
        cli_module.app,
        [
            "process",
            "geotrack",
            "convert",
            "--input-source", str(in_dir),
            "--output-dir", str(out_dir / "geoparquet"),
            "--generate-pmtiles",
            "--pmtiles-minzoom", "1",
            "--pmtiles-maxzoom", "8",
            "--pmtiles-layer", "custom_oceanstream",
            "--pmtiles-sample-rate", "10",
            "--pmtiles-time-gap", "120",
            "--yes",
            "-v",
        ],
    )
    
    assert result.exit_code == 0, f"CLI failed: {result.exit_code}\n{result.output}"
    
    # Check PMTiles file was created
    tiles_dir = out_dir / "tiles"
    pmtiles_file = tiles_dir / "track.pmtiles"
    assert pmtiles_file.exists(), f"PMTiles file not created"
    
    # Verify custom parameters in output
    assert "1 - 8" in result.output or "Zoom levels" in result.output
    assert "custom_oceanstream" in result.output or "Layer name" in result.output
