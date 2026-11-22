import json
import struct
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq
import pytest
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
    column = geo["columns"]["geometry"]
    assert column["encoding"] == "WKB"
    assert column["geometry_types"] == ["Point"]
    assert "crs" not in column  # GeoParquet default: OGC:CRS84 (lon/lat WGS84)

    # The geometry is rebuilt from latitude/longitude, matching the input WKB
    written = pq.read_table(out_dir).column("geometry").to_pylist()
    assert sorted(written) == sorted([_wkb_point(20.0, 10.0), _wkb_point(179.9, -5.5)])


def test_lat_lon_only_output_is_geoparquet(tmp_path: Path):
    """A track with only latitude/longitude columns reads back with geopandas."""
    gpd = pytest.importorskip("geopandas")
    df = pd.DataFrame(
        {
            "time": pd.date_range("2023-06-01", periods=3, freq="min"),
            "latitude": [10.0, -5.5, float("nan")],
            "longitude": [20.0, 179.9, 30.0],
            "temperature": [1.0, 2.0, 3.0],
        }
    )
    out_dir = tmp_path / "track"
    write_geoparquet(df, out_dir, [-90, 0, 90], [-180, 0, 180])

    gdf = gpd.read_parquet(out_dir).sort_values("temperature").reset_index(drop=True)

    assert gdf.geometry.name == "geometry"
    assert gdf.crs.to_string() == "OGC:CRS84"
    assert list(gdf.geometry.x[:2]) == [20.0, 179.9]
    assert list(gdf.geometry.y[:2]) == [10.0, -5.5]
    assert gdf.geometry.iloc[2] is None  # no position, no point
