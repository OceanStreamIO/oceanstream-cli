from pathlib import Path

import pandas as pd
from oceanstream.geotrack.geoparquet_writer import write_geoparquet


def _detect_lat_lon(df: pd.DataFrame):
    cols = {c.lower(): c for c in df.columns}
    lat = cols.get('latitude') or cols.get('lat')
    lon = cols.get('longitude') or cols.get('lon')
    return lat, lon


def test_writer_with_sample_fixture(tmp_path: Path):
    # Use the small sample CSV with names + units row, skip the units row
    project_root = Path(__file__).resolve().parents[2]
    fixture = project_root / 'tests' / 'data' / 'raw_data' / 'sd_test_subset.csv'
    df = pd.read_csv(
        fixture,
        header=0,
        skiprows=[1],  # skip units row
        engine='python',
        on_bad_lines='skip',  # tolerate occasional malformed rows in sample
    )

    lat_col, lon_col = _detect_lat_lon(df)
    assert lat_col and lon_col, "Fixture must include latitude/longitude columns"

    # Normalize to expected column names for writer
    df = df.rename(columns={lat_col: 'latitude', lon_col: 'longitude'})

    out_dir = tmp_path / 'dataset'
    write_geoparquet(df[['latitude', 'longitude']], out_dir, None, None)

    # Verify output exists and is partitioned
    parquet_files = list(out_dir.rglob('*.parquet'))
    assert parquet_files, "No parquet files written from fixture"

    # Top-level lat_bin partitions
    lat_dirs = [p for p in out_dir.iterdir() if p.is_dir() and p.name.startswith('lat_bin=')]
    assert lat_dirs, "Missing lat_bin partition directories"
