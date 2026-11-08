"""Geotrack reading of PMEL ERDDAP Saildrone CSVs: units row, sparse sensors, fill values,
and rows whose measurements came without a position."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from oceanstream.geotrack.csv_reader import _sanitize_column_types
from oceanstream.geotrack.interpolation import fill_positions_in_time
from oceanstream.geotrack.processor import _read_single_csv
from oceanstream.providers import get_provider

FILL = "1.060738863E-314"  # 2147483647 read as a double, as PMEL serves it
FIXES = 4  # rows of ERDDAP_CSV with a position
PLACED = 2  # rows of ERDDAP_CSV that filling gives a position

# One fix every ten minutes; SBE37 readings at :03 and :17 come without a position, as
# on days a drone sent reduced data. Only half the rows carry SBE37 at all.
ERDDAP_CSV = "\n".join([
    "time,latitude,longitude,trajectory,COG_FILTERED_STDDEV,TEMP_SBE37_MEAN,SAL_SBE37_MEAN",
    "UTC,degrees_north,degrees_east,,degree,degree_C,1",
    f"2023-10-09T00:00:00Z,0.0,-166.0,1079.0,{FILL},NaN,NaN",
    "2023-10-09T00:03:00Z,NaN,NaN,1079.0,NaN,29.65,35.41",
    "2023-10-09T00:10:00Z,0.1,-166.1,1079.0,2.5,29.70,35.40",
    "2023-10-09T00:17:00Z,NaN,NaN,1079.0,NaN,29.74,35.42",
    "2023-10-09T00:20:00Z,0.2,-166.2,1079.0,NaN,NaN,NaN",
    f"2023-10-09T00:25:00Z,NaN,NaN,1079.0,{FILL},NaN,NaN",
    "2023-10-09T01:30:00Z,NaN,NaN,1079.0,NaN,29.80,35.43",
    "2023-10-09T02:00:00Z,0.5,-166.5,1079.0,NaN,NaN,NaN",
    "",
])


@pytest.fixture
def erddap_csv(tmp_path: Path) -> Path:
    path = tmp_path / "sd1079_tpos_2023.csv"
    path.write_text(ERDDAP_CSV, encoding="utf-8")
    return path


def _read(path: Path, gap: pd.Timedelta | None) -> pd.DataFrame:
    df, _ = _read_single_csv(path, path.name, get_provider("noaa_pmel"),
                             fill_positions_max_gap=gap)
    assert df is not None
    return df.sort_values("time").reset_index(drop=True)


def test_a_sparse_sensor_column_read_as_text_stays_numeric():
    df = pd.DataFrame({"TEMP": ["degree_C", "29.6", pd.NA, pd.NA, pd.NA, "29.7"],
                       "NOTE": ["a", "b", "c", "1", pd.NA, pd.NA]})
    out = _sanitize_column_types(df)
    assert pd.api.types.is_float_dtype(out["TEMP"])
    assert out["TEMP"].tolist()[1] == pytest.approx(29.6)
    assert pd.api.types.is_string_dtype(out["NOTE"])


def test_fill_values_become_missing_and_zero_stays():
    df = pd.DataFrame({"COG_FILTERED_STDDEV": [1.060738863e-314, -1.00909e-303, 0.0, 2.5]})
    out = _sanitize_column_types(df)
    assert out["COG_FILTERED_STDDEV"].isna().tolist() == [True, True, False, False]


def test_without_filling_measurements_without_a_position_are_dropped(erddap_csv: Path):
    df = _read(erddap_csv, None)
    assert len(df) == FIXES
    assert "position_interpolated" not in df.columns
    assert df["TEMP_SBE37_MEAN"].notna().sum() == 1
    assert pd.api.types.is_float_dtype(df["TEMP_SBE37_MEAN"])
    assert np.isnan(df.loc[0, "COG_FILTERED_STDDEV"])  # the fill value


def test_filling_places_measurements_between_close_fixes(erddap_csv: Path):
    df = _read(erddap_csv, pd.Timedelta(minutes=30))
    filled = df[df["position_interpolated"]]
    # 00:03 and 00:17 sit between fixes 10 min apart; 01:30 between fixes 100 min
    # apart stays out, and the 00:25 row holds only a fill value, so no measurement.
    assert filled["time"].dt.strftime("%H:%M").tolist() == ["00:03", "00:17"]
    assert filled["latitude"].tolist() == pytest.approx([0.03, 0.17])
    assert filled["longitude"].tolist() == pytest.approx([-166.03, -166.17])
    assert filled["TEMP_SBE37_MEAN"].tolist() == pytest.approx([29.65, 29.74])
    assert len(df) == FIXES + PLACED
    assert not df.loc[~df["position_interpolated"], "time"].dt.strftime("%H:%M").isin(
        ["00:25", "01:30"]).any()


def test_longitude_is_interpolated_across_the_antimeridian():
    df = pd.DataFrame({
        "time": pd.to_datetime(["2023-01-01T00:00Z", "2023-01-01T00:05Z",
                                "2023-01-01T00:10Z"]),
        "latitude": [1.0, np.nan, 2.0],
        "longitude": [179.8, np.nan, -179.8],
        "platform_id": "sd1",
        "TEMP": [np.nan, 25.0, np.nan],
    })
    out = fill_positions_in_time(df, pd.Timedelta(minutes=30))
    assert out.loc[1, "latitude"] == pytest.approx(1.5)
    assert abs(out.loc[1, "longitude"]) == pytest.approx(180.0)
    assert out["position_interpolated"].tolist() == [False, True, False]


def test_platforms_are_interpolated_from_their_own_fixes():
    df = pd.DataFrame({
        "time": pd.to_datetime(["2023-01-01T00:00Z", "2023-01-01T00:10Z",
                                "2023-01-01T00:05Z", "2023-01-01T00:00Z",
                                "2023-01-01T00:10Z"]),
        "latitude": [0.0, 1.0, np.nan, 10.0, 11.0],
        "longitude": [0.0, 1.0, np.nan, 10.0, 11.0],
        "platform_id": ["a", "a", "b", "b", "b"],
        "TEMP": [np.nan, np.nan, 20.0, np.nan, np.nan],
    })
    out = fill_positions_in_time(df, pd.Timedelta(minutes=30))
    assert out.loc[2, ["latitude", "longitude"]].tolist() == pytest.approx([10.5, 10.5])
