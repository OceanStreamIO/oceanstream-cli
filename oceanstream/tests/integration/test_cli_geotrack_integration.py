import shutil
from pathlib import Path
import pyarrow.parquet as pq
import pytest

@pytest.mark.integration
def test_cli_geotrack_end_to_end(tmp_path: Path, monkeypatch):
    # Use isolated metadata directory
    metadata_dir = tmp_path / "metadata"
    metadata_dir.mkdir(parents=True, exist_ok=True)
    from oceanstream.config.settings import Settings
    monkeypatch.setattr(Settings, "METADATA_DIR", metadata_dir)
    
    in_dir = tmp_path / "in"
    out_dir = tmp_path / "out"
    in_dir.mkdir(parents=True)
    out_dir.mkdir(parents=True)

    project_root = Path(__file__).resolve().parents[3]  # Go up 3 levels to reach project root
    diverse_csv = project_root / "oceanstream" / "tests" / "data" / "raw_data" / "sd_diverse_subset.csv"
    assert diverse_csv.exists(), "Expected test data file is missing"
    shutil.copy(diverse_csv, in_dir / diverse_csv.name)

    monkeypatch.setenv("PYTHONUTF8", "1")
    monkeypatch.setenv("PYTHONWARNINGS", "ignore")
    monkeypatch.setenv("NO_COLOR", "1")

    from typer.testing import CliRunner
    from oceanstream import cli as cli_module  # type: ignore

    runner = CliRunner()
    result = runner.invoke(
        cli_module.app,
        [
            "process",
            "geotrack",
            "convert",
            "--input-source", str(in_dir),
            "--output-dir", str(out_dir),
            "--force-reprocess",
            "--yes",
            "-v",
        ],
    )
    assert result.exit_code == 0, f"Typer CLI failed: {result.exit_code}\n{result.output}"
    # Assert progress bar and per-file markers present
    # Progress indicator text should include the filename of the processed CSV (rich or fallback).
    assert "sd_diverse_subset.csv" in result.output or "Processing files" in result.output

    parquet_files = list(out_dir.rglob("*.parquet"))
    assert parquet_files, f"CLI did not produce any parquet files. Output:\n{result.output}\n\nOutput dir contents: {list(out_dir.rglob('*'))}"
    sample_file = sorted(parquet_files)[0]

    lat_dirs = {p.parent.parent for p in parquet_files}
    lon_dirs = {p.parent for p in parquet_files}
    lat_partition_values = {d.name for d in lat_dirs if d.name.startswith("lat_bin=")}
    lon_partition_values = {d.name for d in lon_dirs if d.name.startswith("lon_bin=")}

    assert lat_partition_values and lon_partition_values

    tbl = pq.read_table(sample_file)
    assert {"latitude", "longitude"}.issubset(tbl.schema.names)

    from oceanstream.storage.local import load_geoparquet_locally, save_geoparquet_locally
    df_sample = tbl.to_pandas()
    roundtrip_path = out_dir / "local_roundtrip.parquet"
    save_geoparquet_locally(df_sample, roundtrip_path)
    df_loaded = load_geoparquet_locally(roundtrip_path)
    assert list(df_loaded.columns) == list(df_sample.columns)
    assert len(df_loaded) == len(df_sample)
    assert df_loaded[["latitude", "longitude"]].equals(df_sample[["latitude", "longitude"]])


@pytest.mark.integration
def test_cli_geotrack_nmea_file_processing(tmp_path: Path, monkeypatch):
    """Test CLI processing of NMEA .txt files through the geotrack convert command."""
    # Use isolated metadata directory
    metadata_dir = tmp_path / "metadata"
    metadata_dir.mkdir(parents=True, exist_ok=True)
    from oceanstream.config.settings import Settings
    monkeypatch.setattr(Settings, "METADATA_DIR", metadata_dir)
    
    in_dir = tmp_path / "in"
    out_dir = tmp_path / "out"
    in_dir.mkdir(parents=True)
    out_dir.mkdir(parents=True)

    # Create a sample NMEA file with valid sentences
    nmea_file = in_dir / "test_gnss.txt"
    nmea_content = """2024-02-17T00:00:00.000000Z $GPGGA,000000.00,3242.39160,N,11714.16410,W,1,10,0.8,10.0,M,,,*33
2024-02-17T00:00:00.000000Z $GPRMC,000000.00,A,3242.39160,N,11714.16410,W,0.0,0.0,170224,,,A*44
2024-02-17T00:00:05.000000Z $GPGGA,000005.00,3242.39200,N,11714.16500,W,1,10,0.8,9.5,M,,,*0E
2024-02-17T00:00:05.000000Z $GPRMC,000005.00,A,3242.39200,N,11714.16500,W,0.5,45.0,170224,,,A*70
2024-02-17T00:00:10.000000Z $GPGGA,000010.00,3242.39300,N,11714.16600,W,1,09,0.9,8.0,M,,,*05
2024-02-17T00:00:10.000000Z $GPRMC,000010.00,A,3242.39300,N,11714.16600,W,1.0,90.0,170224,,,A*71
"""
    nmea_file.write_text(nmea_content)

    monkeypatch.setenv("PYTHONUTF8", "1")
    monkeypatch.setenv("PYTHONWARNINGS", "ignore")
    monkeypatch.setenv("NO_COLOR", "1")

    from typer.testing import CliRunner
    from oceanstream import cli as cli_module  # type: ignore

    runner = CliRunner()
    result = runner.invoke(
        cli_module.app,
        [
            "process",
            "geotrack",
            "convert",
            "--input-source", str(nmea_file),
            "--output-dir", str(out_dir),
            "--force-reprocess",
            "--campaign-id", "TEST_NMEA_CLI",
            "--yes",
            "-v",
        ],
    )
    
    # Check that the command succeeded
    assert result.exit_code == 0, f"CLI failed with exit code {result.exit_code}\nOutput:\n{result.output}"
    
    # Verify NMEA conversion was mentioned in output
    assert "NMEA" in result.output or "test_gnss" in result.output, \
        f"NMEA processing not mentioned in output:\n{result.output}"
    
    # Verify GeoParquet files were created
    parquet_files = list(out_dir.rglob("*.parquet"))
    assert parquet_files, f"No parquet files created. Output:\n{result.output}\nDir contents: {list(out_dir.rglob('*'))}"
    
    # Verify the campaign directory was created
    campaign_dir = out_dir / "TEST_NMEA_CLI"
    assert campaign_dir.exists(), f"Campaign directory not created: {campaign_dir}"
    
    # Read one of the parquet files and verify structure
    sample_file = sorted(parquet_files)[0]
    tbl = pq.read_table(sample_file)
    schema_names = set(tbl.schema.names)
    
    # Verify essential columns are present
    assert "latitude" in schema_names, f"latitude column missing. Schema: {tbl.schema.names}"
    assert "longitude" in schema_names, f"longitude column missing. Schema: {tbl.schema.names}"
    assert "time" in schema_names, f"time column missing. Schema: {tbl.schema.names}"
    
    # Verify NMEA-specific columns are present
    nmea_columns = {"gps_quality", "num_satellites", "horizontal_dilution"}
    present_nmea_cols = nmea_columns.intersection(schema_names)
    assert present_nmea_cols, f"No NMEA-specific columns found. Schema: {tbl.schema.names}"
    
    # Verify data was actually written (we expect 3 merged rows from 6 NMEA sentences)
    df = tbl.to_pandas()
    assert len(df) > 0, "No data rows in parquet file"
    assert len(df) <= 6, f"Too many rows (expected ≤6, got {len(df)})"
    
    # Verify coordinates are in expected range (Southern California)
    assert df["latitude"].min() > 32.0 and df["latitude"].max() < 33.0, \
        f"Latitude out of range: {df['latitude'].min()}, {df['latitude'].max()}"
    assert df["longitude"].min() > -118.0 and df["longitude"].max() < -117.0, \
        f"Longitude out of range: {df['longitude'].min()}, {df['longitude'].max()}"
    
    # Verify STAC metadata was created
    stac_dir = campaign_dir / "stac"
    assert stac_dir.exists(), f"STAC directory not created: {stac_dir}"
    
    collection_file = stac_dir / "collection.json"
    assert collection_file.exists(), f"STAC collection.json not created: {collection_file}"
    
    # Verify STAC items were created
    items_dir = stac_dir / "items"
    if items_dir.exists():
        item_files = list(items_dir.glob("*.json"))
        assert item_files, "No STAC item files created"


@pytest.mark.integration
def test_cli_geotrack_mixed_csv_and_nmea(tmp_path: Path, monkeypatch):
    """Test CLI processing of directory with both CSV and NMEA files."""
    # Use isolated metadata directory
    metadata_dir = tmp_path / "metadata"
    metadata_dir.mkdir(parents=True, exist_ok=True)
    from oceanstream.config.settings import Settings
    monkeypatch.setattr(Settings, "METADATA_DIR", metadata_dir)
    
    in_dir = tmp_path / "in"
    out_dir = tmp_path / "out"
    in_dir.mkdir(parents=True)
    out_dir.mkdir(parents=True)

    # Create a CSV file
    csv_file = in_dir / "test_data.csv"
    csv_content = """time,latitude,longitude,temperature
2024-02-17T00:00:00Z,33.0,-118.0,15.5
2024-02-17T00:00:10Z,33.1,-118.1,15.6
"""
    csv_file.write_text(csv_content)
    
    # Create a NMEA file with valid checksums
    nmea_file = in_dir / "test_gnss.txt"
    nmea_content = """2024-02-17T00:00:20.000000Z $GPGGA,000020.00,3312.00000,N,11806.00000,W,1,08,1.0,5.0,M,,,*02
2024-02-17T00:00:20.000000Z $GPRMC,000020.00,A,3312.00000,N,11806.00000,W,0.0,0.0,170224,,,A*41
"""
    nmea_file.write_text(nmea_content)

    monkeypatch.setenv("PYTHONUTF8", "1")
    monkeypatch.setenv("PYTHONWARNINGS", "ignore")
    monkeypatch.setenv("NO_COLOR", "1")

    from typer.testing import CliRunner
    from oceanstream import cli as cli_module  # type: ignore

    runner = CliRunner()
    result = runner.invoke(
        cli_module.app,
        [
            "process",
            "geotrack",
            "convert",
            "--input-source", str(in_dir),
            "--output-dir", str(out_dir),
            "--force-reprocess",
            "--campaign-id", "TEST_MIXED",
            "--yes",
            "-v",
        ],
    )
    
    # Check that the command succeeded
    assert result.exit_code == 0, f"CLI failed with exit code {result.exit_code}\nOutput:\n{result.output}"
    
    # Verify both files were processed
    assert "2 file(s) to process" in result.output or "Processing files" in result.output, \
        f"Expected 2 files to be processed. Output:\n{result.output}"
    
    # Verify NMEA conversion happened
    assert "NMEA" in result.output or "converted from NMEA" in result.output, \
        f"NMEA conversion not mentioned. Output:\n{result.output}"
    
    # Verify GeoParquet files were created
    parquet_files = list(out_dir.rglob("*.parquet"))
    assert parquet_files, f"No parquet files created. Output:\n{result.output}"
    
    # Read all data and verify we have rows from both sources
    all_data = []
    for pf in parquet_files:
        tbl = pq.read_table(pf)
        all_data.append(tbl.to_pandas())
    
    import pandas as pd
    combined = pd.concat(all_data, ignore_index=True)
    
    # We expect at least 3 rows total (2 from CSV + 1 from NMEA merged)
    assert len(combined) >= 3, f"Expected at least 3 rows, got {len(combined)}"
    
    # Verify coordinates span both data sources
    # CSV has lat ~33, NMEA has lat ~33.2
    assert combined["latitude"].min() >= 33.0
    assert combined["latitude"].max() <= 33.3


@pytest.mark.integration
def test_cli_geotrack_nmea_with_sentence_filter(tmp_path: Path, monkeypatch):
    """Test CLI processing of NMEA files with sentence type filtering."""
    # Use isolated metadata directory
    metadata_dir = tmp_path / "metadata"
    metadata_dir.mkdir(parents=True, exist_ok=True)
    from oceanstream.config.settings import Settings
    monkeypatch.setattr(Settings, "METADATA_DIR", metadata_dir)
    
    in_dir = tmp_path / "in"
    out_dir = tmp_path / "out"
    in_dir.mkdir(parents=True)
    out_dir.mkdir(parents=True)

    # Create a NMEA file with multiple sentence types
    nmea_file = in_dir / "test_multi.txt"
    # Include GGA (position), RMC (position + speed), and VTG (speed only)
    # Using valid checksums
    nmea_content = """2024-02-17T00:00:00.000000Z $GPGGA,000000.00,3242.3912,N,11714.1643,W,1,08,1.0,5.0,M,,,*01
2024-02-17T00:00:00.000000Z $GPRMC,000000.00,A,3242.3912,N,11714.1643,W,0.5,45.0,170224,,,A*76
2024-02-17T00:00:00.000000Z $GPVTG,45.0,T,,M,0.5,N,0.9,K,A*30
2024-02-17T00:00:10.000000Z $GPGGA,000010.00,3242.3920,N,11714.1650,W,1,08,1.0,5.0,M,,,*03
2024-02-17T00:00:10.000000Z $GPRMC,000010.00,A,3242.3920,N,11714.1650,W,0.5,45.0,170224,,,A*74
2024-02-17T00:00:10.000000Z $GPVTG,45.0,T,,M,0.5,N,0.9,K,A*30
"""
    nmea_file.write_text(nmea_content)

    monkeypatch.setenv("PYTHONUTF8", "1")
    monkeypatch.setenv("PYTHONWARNINGS", "ignore")
    monkeypatch.setenv("NO_COLOR", "1")

    from typer.testing import CliRunner
    from oceanstream import cli as cli_module  # type: ignore

    runner = CliRunner()
    result = runner.invoke(
        cli_module.app,
        [
            "process",
            "geotrack",
            "convert",
            "--input-source", str(nmea_file),
            "--output-dir", str(out_dir),
            "--force-reprocess",
            "--campaign-id", "TEST_NMEA_FILTER",
            "--nmea-sentence-types", "GGA",
            "--nmea-sentence-types", "RMC",
            "--yes",
            "-v",
        ],
    )
    
    # Check that the command succeeded
    assert result.exit_code == 0, f"CLI failed with exit code {result.exit_code}\nOutput:\n{result.output}"
    
    # Verify NMEA conversion was mentioned
    assert "NMEA" in result.output or "Converting" in result.output, \
        f"NMEA processing not mentioned. Output:\n{result.output}"
    
    # Verify GeoParquet files were created
    parquet_files = list(out_dir.rglob("*.parquet"))
    assert parquet_files, f"No parquet files created. Output:\n{result.output}"
    
    # Read data and verify we got merged data (GGA + RMC should merge by timestamp)
    all_data = []
    for pf in parquet_files:
        tbl = pq.read_table(pf)
        all_data.append(tbl.to_pandas())
    
    import pandas as pd
    combined = pd.concat(all_data, ignore_index=True)
    
    # We should have 2 rows (2 timestamps with merged GGA+RMC data)
    # VTG should be filtered out
    assert len(combined) == 2, f"Expected 2 merged rows, got {len(combined)}"
    
    # Verify position data is present (from GGA/RMC)
    assert combined["latitude"].notna().all(), "Latitude should be present"
    assert combined["longitude"].notna().all(), "Longitude should be present"

