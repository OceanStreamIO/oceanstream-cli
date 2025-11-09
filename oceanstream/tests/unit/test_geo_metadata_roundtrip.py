import json
import struct
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq
from oceanstream.geotrack.geoparquet_writer import write_geoparquet


def _wkb_point(lon: float, lat: float) -> bytes:
    """Create little-endian WKB for a 2D Point(lon, lat) without SRID."""
    # Byte order (1), WKB type for Point (1), then lon, lat as little-endian float64
    return b"\x01" + struct.pack("<I", 1) + struct.pack("<dd", lon, lat)


def test_geo_metadata_roundtrip(tmp_path: Path):
    # Build a tiny DataFrame with geometry as WKB bytes
    df = pd.DataFrame(
        {
            "latitude": [10.0, -5.5],
            "longitude": [20.0, 179.9],
            "geometry": [
                _wkb_point(20.0, 10.0),
                _wkb_point(179.9, -5.5),
            ],
        }
    )

    lat_bins = [-90, 0, 90]
    lon_bins = [-180, 0, 180]

    out_dir = tmp_path / "geoparquet-out"
    write_geoparquet(df, out_dir, lat_bins, lon_bins)

    # Find one parquet file written and inspect schema metadata
    parquet_files = list(out_dir.rglob("*.parquet"))
    assert parquet_files, "Expected at least one parquet file to be written"

    tbl = pq.read_table(parquet_files[0])
    meta = tbl.schema.metadata or {}
    assert b"geo" in meta, "GeoParquet 'geo' metadata was not embedded"

    geo = json.loads(meta[b"geo"].decode("utf-8"))
    assert geo.get("primary_column") == "geometry"
    assert geo.get("columns", {}).get("geometry", {}).get("encoding") == "WKB"
    assert geo.get("columns", {}).get("geometry", {}).get("geometry_type") == "Point"
    assert geo.get("columns", {}).get("geometry", {}).get("crs") == "EPSG:4326"
