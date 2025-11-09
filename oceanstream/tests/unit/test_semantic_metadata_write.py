import json
from pathlib import Path
import pandas as pd
import pyarrow.parquet as pq

from oceanstream.geotrack.geoparquet_writer import write_geoparquet
from oceanstream.semantic.semantic import SemanticResult


def test_geoparquet_writer_includes_semantic_metadata(tmp_path: Path):
    # Minimal dataframe
    df = pd.DataFrame({
        "latitude": [10.0, 10.1],
        "longitude": [20.0, 20.1],
        "TEMP_SBE37_MEAN": [18.5, 18.7],
    })

    lat_bins = [9.0, 11.0]
    lon_bins = [19.0, 21.0]

    # Build synthetic semantic metadata similar to helper output
    semantic_meta = {
        "oceanstream:aliases": {"TEMP_SBE37_MEAN": "sea_water_temperature"},
        "oceanstream:cf_standard_names": {
            "TEMP_SBE37_MEAN": {"cf_standard_name": "sea_water_temperature", "confidence": 1.0}
        },
        "oceanstream:units": {"sea_water_temperature": "degC"},
        "oceanstream:semantic_version": "sem-v0.1",
    }

    out_dir = tmp_path / "dataset"
    write_geoparquet(
        df,
        out_dir,
        lat_bins,
        lon_bins,
        units_metadata=None,
        alias_mapping=None,
        provider_metadata=None,
        semantic_metadata=semantic_meta,
    )

    # Read one file back and inspect metadata
    files = list(out_dir.rglob("*.parquet"))
    assert files, "Writer did not produce parquet files"
    sample = files[0]
    meta = pq.read_table(sample).schema.metadata
    # Validate presence of semantic keys
    assert b"oceanstream:cf_standard_names" in meta
    assert b"oceanstream:semantic_version" in meta
    assert b"oceanstream:aliases" in meta  # merged from semantic metadata
    cf_block = json.loads(meta[b"oceanstream:cf_standard_names"].decode("utf-8"))
    assert "TEMP_SBE37_MEAN" in cf_block
