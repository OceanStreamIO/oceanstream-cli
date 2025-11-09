import shutil
from pathlib import Path
import pyarrow.parquet as pq
import pytest

@pytest.mark.integration
def test_cli_geotrack_end_to_end(tmp_path: Path, monkeypatch):
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
            "--input-dir", str(in_dir),
            "--output-dir", str(out_dir),
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
