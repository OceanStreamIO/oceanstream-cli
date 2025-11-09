import pandas as pd
import pyarrow.parquet as pq
from pathlib import Path

from oceanstream.geotrack.geoparquet_writer import write_geoparquet


def test_writer_coerces_object_numeric_columns(tmp_path: Path):
    # Mixed numeric-as-string column (TEMP_AIR_MEAN) with stray whitespace and non-numeric token
    df = pd.DataFrame(
        {
            "latitude": [10.0, 10.5, 11.0],
            "longitude": [-150.0, -149.5, -149.0],
            "TEMP_AIR_MEAN": ["25.35", " 26.1 ", "bad"],  # should coerce first two, NaN third
        }
    )

    lat_bins = [9.0, 10.0, 11.0, 12.0]
    lon_bins = [-151.0, -150.0, -149.0, -148.0]

    out_dir = tmp_path / "dataset"
    write_geoparquet(
        df,
        out_dir,
        lat_bins,
        lon_bins,
        units_metadata=None,
        alias_mapping=None,
        provider_metadata=None,
    )

    # Read all parquet pieces and verify schema/dtype
    files = list(out_dir.rglob("*.parquet"))
    assert files, "Expected parquet files to be written"
    tables = [pq.read_table(f) for f in files]
    import pyarrow as pa
    table = pa.concat_tables(tables)
    # Column should be float (numeric coercion succeeded for enough values)
    col = table.column("TEMP_AIR_MEAN")
    assert str(col.type) in {"double", "float64", "float32"}
    # Last row should be null due to 'bad'
    arr = col.to_pylist()
    # The third row was 'bad' -> should become None in the concatenated dataset
    assert arr.count(None) >= 1
