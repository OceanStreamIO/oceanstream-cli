"""Example application entrypoint demonstrating end-to-end ingestion.

Steps:
1. Read raw CSV files from RAW_DATA_PATH (defaults to data/raw_data).
2. Automatically suggest latitude/longitude bin edges from data.
3. Write a partitioned GeoParquet dataset embedding geo + optional metadata.
4. Optionally upload one of the produced parquet files to Azure Blob Storage if
   AZURE_CONTAINER_NAME and AZURE_STORAGE_CONNECTION_STRING are set.

Environment variables (see .env.example):
  RAW_DATA_PATH, OUTPUT_PATH,
  AZURE_STORAGE_CONNECTION_STRING, AZURE_CONTAINER_NAME

This module is for illustrative / manual experimentation. For production use
the CLI (`python -m cli`) which offers flags and dry-run capabilities.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from .config.settings import Settings
from .pipeline.csv_reader import read_csv_files
from .pipeline.geoparquet_writer import write_geoparquet
from .pipeline.binning import suggest_lat_lon_bins_from_data
from .storage.azure_blob import upload_to_azure_blob


def main() -> None:
    settings = Settings()

    raw_data_path = Path(settings.RAW_DATA_PATH)
    output_root = Path(settings.OUTPUT_PATH)

    df = read_csv_files(str(raw_data_path))
    lat_bins, lon_bins = suggest_lat_lon_bins_from_data(df)

    # Example placeholders for metadata; extend or load from JSON files if desired.
    units: Optional[dict[str, str]] = None  # e.g., {"sea_water_temperature": "degC"}
    aliases: Optional[dict[str, str]] = None  # e.g., {"sea_water_temperature": "sst"}

    write_geoparquet(
        df,
        output_root,
        lat_bins,
        lon_bins,
        units_metadata=units,
        alias_mapping=aliases,
    )

    # Upload the first parquet file produced (if any) as a demonstration.
    if settings.AZURE_CONTAINER_NAME and settings.AZURE_STORAGE_CONNECTION_STRING:
        first_parquet = next(iter(output_root.rglob("*.parquet")), None)
        if first_parquet is not None:
            upload_to_azure_blob(
                file_path=str(first_parquet),
                container_name=settings.AZURE_CONTAINER_NAME,
                blob_name=first_parquet.name,
            )


if __name__ == "__main__":  # pragma: no cover - manual invocation
    main()
