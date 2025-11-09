import csv
import json
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq
from oceanstream.geotrack.geoparquet_writer import write_geoparquet


def _parse_units_row(csv_path: Path) -> dict[str, str | None]:
    with csv_path.open("r", encoding="utf-8") as f:
        reader = csv.reader(f)
        header = next(reader)
        units = next(reader)
    mapping: dict[str, str | None] = {}
    for name, unit in zip(header, units, strict=True):
        unit_norm = unit.strip()
        mapping[name] = unit_norm if unit_norm else None
    return mapping


def _make_minimal_df() -> pd.DataFrame:
    # Minimal dataframe sufficient for writer (latitude/longitude only)
    return pd.DataFrame({
        "latitude": [20.0, 20.001],
        "longitude": [-154.86, -154.861],
    })


def test_units_metadata_embedded_and_complete(tmp_path: Path):
    # Project root two levels up from this file (tests/unit -> tests -> package root)
    project_root = Path(__file__).resolve().parents[2]
    csv_path = project_root / "tests" / "data" / "raw_data" / "sd_test_subset.csv"
    assert csv_path.exists(), "Test CSV fixture missing"

    units_map = _parse_units_row(csv_path)

    # Minimal lat/lon bins to satisfy writer (single bin)
    lat_bins = [-90.0, 90.0]
    lon_bins = [-180.0, 180.0]

    df = _make_minimal_df()

    out_dir = tmp_path / "out"
    write_geoparquet(df, out_dir, lat_bins, lon_bins, units_metadata=units_map)

    # Find a parquet file written by write_to_dataset
    parquet_files = list(out_dir.rglob("*.parquet"))
    assert parquet_files, "No parquet files were written"

    # Read schema metadata from the first file
    meta = pq.read_table(parquet_files[0]).schema.metadata or {}
    assert b"oceanstream:units" in meta, "Missing 'oceanstream:units' in parquet metadata"

    embedded_units = json.loads(meta[b"oceanstream:units"].decode("utf-8"))

    # Every CSV column should be present in the units map (completeness)
    assert set(embedded_units.keys()) == set(units_map.keys())

    # Spot-check a few known fields
    assert embedded_units["latitude"] == "degrees_north"
    assert embedded_units["longitude"] == "degrees_east"
    assert embedded_units["SOG"] == "m s-1"
