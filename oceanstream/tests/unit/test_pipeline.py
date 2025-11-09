import json
import struct
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq
from oceanstream.geotrack.geoparquet_writer import write_geoparquet


def _wkb_point(lon: float, lat: float) -> bytes:
    # Little-endian WKB Point
    return b"\x01" + struct.pack("<I", 1) + struct.pack("<dd", lon, lat)


def test_write_geoparquet_auto_bins_and_partitions(tmp_path: Path):
    # Minimal frame for auto-binning
    df = pd.DataFrame({
        'latitude': [-2.5, 1.2, 6.7],
        'longitude': [-170.1, -160.0, -150.3],
    })

    out = tmp_path / 'ds'
    write_geoparquet(df, out, None, None)

    # Expect partitioned dataset dirs
    assert list(out.glob('lat_bin=*')), 'No lat_bin partitions created'
    # At least one parquet file somewhere under the dataset
    assert list(out.rglob('*.parquet')), 'No parquet files written'


def test_write_geoparquet_embeds_units_and_aliases(tmp_path: Path):
    df = pd.DataFrame({
        'latitude': [0.0, 0.5],
        'longitude': [10.0, 10.5],
        'sea_water_temperature': [20.1, 20.3],
    })

    units = { 'sea_water_temperature': 'degC' }
    aliases = { 'sea_water_temperature': 'sst' }

    out = tmp_path / 'ds'
    write_geoparquet(df, out, None, None, units_metadata=units, alias_mapping=aliases)

    pf = next(iter(out.rglob('*.parquet')))
    tbl = pq.read_table(pf)
    meta = tbl.schema.metadata or {}
    assert b'oceanstream:units' in meta, 'Missing units metadata'
    assert b'oceanstream:aliases' in meta, 'Missing aliases metadata'
    assert json.loads(meta[b'oceanstream:units'].decode('utf-8')) == units
    assert json.loads(meta[b'oceanstream:aliases'].decode('utf-8')) == aliases


def test_write_geoparquet_geo_metadata_auto_and_override(tmp_path: Path):
    # With geometry column -> auto geo block
    df = pd.DataFrame({
        'latitude': [1.0],
        'longitude': [2.0],
        'geometry': [_wkb_point(2.0, 1.0)],
    })
    out_auto = tmp_path / 'auto'
    write_geoparquet(df, out_auto, None, None)
    pf_auto = next(iter(out_auto.rglob('*.parquet')))
    meta_auto = pq.read_table(pf_auto).schema.metadata or {}
    assert b'geo' in meta_auto
    geo_auto = json.loads(meta_auto[b'geo'].decode('utf-8'))
    assert geo_auto.get('columns', {}).get('geometry', {}).get('crs') == 'EPSG:4326'

    # Override geo block explicitly
    geo_override = {
        'version': '1.1.0',
        'primary_column': 'geometry',
        'columns': {
            'geometry': {
                'encoding': 'WKB',
                'geometry_type': 'Point',
                'crs': 'EPSG:4326',
            }
        },
    }
    out_override = tmp_path / 'override'
    write_geoparquet(df, out_override, None, None, geo_metadata=geo_override)
    pf_override = next(iter(out_override.rglob('*.parquet')))
    meta_override = pq.read_table(pf_override).schema.metadata or {}
    assert json.loads(meta_override[b'geo'].decode('utf-8')) == geo_override


def test_write_geoparquet_includes_provider_metadata(tmp_path: Path):
    df = pd.DataFrame({
        'latitude': [1.0, 2.0],
        'longitude': [3.0, 4.0],
        'temperature': [10.1, 11.2],
    })
    provider_meta = {"oceanstream:provider": {"name": "test", "columns": list(df.columns)}}
    out = tmp_path / 'provider'
    write_geoparquet(df, out, None, None, provider_metadata=provider_meta)
    pf = next(iter(out.rglob('*.parquet')))
    meta = pq.read_table(pf).schema.metadata or {}
    assert b'oceanstream:provider' in meta
    parsed = json.loads(meta[b'oceanstream:provider'].decode('utf-8'))
    assert parsed['name'] == 'test'
    assert set(parsed['columns']) == set(df.columns)
