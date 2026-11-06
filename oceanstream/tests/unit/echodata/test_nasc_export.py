"""Flattening NASC into GeoParquet rows (oceanstream.echodata.compute.nasc_export)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import xarray as xr

from oceanstream.echodata.compute import nasc_export


def _nasc(chunked: bool = False) -> xr.Dataset:
    rng = np.random.default_rng(0)
    nasc = rng.random((2, 3, 4)) * 100
    nasc[0, 1, 2] = np.nan
    nasc[1, :, 0] = np.nan
    times = np.array(["2022-06-25T00:00:01", "2022-06-25T00:10:00", "NaT"], dtype="datetime64[ns]")
    ds = xr.Dataset(
        {
            # The stores keep NASC as (depth, distance, channel) in some versions.
            "NASC": (("depth", "distance", "channel"), nasc.transpose(2, 1, 0)),
            "NASC_log": (("channel", "distance", "depth"), 10 * np.log10(nasc)),
            "latitude": ("distance", [21.0, 21.5, -0.5]),
            "longitude": ("distance", [-158.2, -158.0, 179.9]),
            "ping_time": ("distance", times),
        },
        coords={"channel": ["WBT 38", "WBT 70"], "distance": [0.0, 0.5, 1.0],
                "depth": [5.0, 15.0, 25.0, 35.0]},
    )
    return ds.chunk({"distance": 1}) if chunked else ds


def _expected(ds: xr.Dataset) -> pd.DataFrame:
    rows = []
    for c in range(ds.sizes["channel"]):
        for d in range(ds.sizes["distance"]):
            for z in range(ds.sizes["depth"]):
                cell = {"channel": c, "distance": d, "depth": z}
                value = float(ds.NASC.isel(cell))
                if np.isnan(value):
                    continue
                rows.append({
                    "campaign_id": "SE2204", "file_id": "2022-06-25_short_pulse",
                    "channel": str(ds.channel.values[c]),
                    "ping_time": str(ds.ping_time.values[d]),
                    "depth": float(ds.depth.values[z]), "nasc_value": value,
                    "latitude": float(ds.latitude.values[d]),
                    "longitude": float(ds.longitude.values[d]),
                    "nasc_log": float(ds.NASC_log.isel(cell)),
                })
    return pd.DataFrame(rows)


@pytest.mark.parametrize("chunked", [False, True])
def test_one_row_per_cell_in_channel_distance_depth_order(chunked: bool) -> None:
    ds = _nasc(chunked)
    got = nasc_export._nasc_frame(ds, "SE2204", "2022-06-25_short_pulse")
    assert len(got) == 2 * 3 * 4 - 1 - 3
    pd.testing.assert_frame_equal(got, _expected(_nasc()))
    assert got["ping_time"].iloc[-1] == "NaT"


def test_location_from_attributes_and_no_channel_dimension() -> None:
    ds = _nasc().isel(channel=0, drop=True).drop_vars(["latitude", "longitude", "ping_time"])
    ds.attrs.update(geospatial_lat_min=20.0, geospatial_lat_max=22.0,
                    geospatial_lon_min=-160.0, geospatial_lon_max=-158.0)
    got = nasc_export._nasc_frame(ds, "C", "f")
    assert set(got["channel"]) == {"single"} and got["ping_time"].isna().all()
    assert (got["latitude"] == 21.0).all() and (got["longitude"] == -159.0).all()
    assert len(got) == 11


def test_export_partitions_by_degree(tmp_path: Path) -> None:
    pytest.importorskip("geopandas")
    out = nasc_export.export_nasc_to_geoparquet(_nasc(), tmp_path, "SE2204", "day")
    parts = sorted(str(p.relative_to(out)) for p in out.rglob("*.parquet"))
    assert [p.rsplit("/", 1)[0] for p in parts] == ["SE2204/-1/179", "SE2204/21/-158",
                                                     "SE2204/21/-159"]
    back = pd.concat(pd.read_parquet(out / p) for p in parts)
    assert len(back) == 20 and "geometry" in back
