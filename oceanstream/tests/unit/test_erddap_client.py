"""Tests for the ERDDAP REST client."""
from __future__ import annotations

import textwrap
from pathlib import Path
from unittest.mock import patch

from oceanstream.sources.erddap import (
    DatasetInfo,
    download_csv,
    get_dataset_metadata,
    list_datasets,
    read_erddap_csv,
)


_DATASET_CSV = textwrap.dedent("""\
    datasetID,title,summary,minTime,maxTime,minLatitude,maxLatitude,minLongitude,maxLongitude
    datasetID,,,,,,,,,
    EMSO_OBSEA_CTD,OBSEA CTD 30min,Temperature at OBSEA,2020-01-01,2024-01-01,41.18,41.18,1.75,1.75
    EMSO_E1M3A_SBE37,E1M3A SBE37,Cretan Sea mooring,2019-01-01,2024-06-01,35.7,35.7,25.1,25.1
""")

_INFO_CSV = textwrap.dedent("""\
    Row Type,Variable Name,Attribute Name,Data Type,Value
    attribute,,title,String,OBSEA CTD 30min
    variable,temperature,,,
    attribute,temperature,units,String,degree_Celsius
    variable,time,,,
    attribute,time,units,String,seconds since 1970-01-01
""")


def test_list_datasets():
    with patch("oceanstream.sources.erddap._fetch_text", return_value=_DATASET_CSV):
        datasets = list_datasets("https://erddap.emso.eu/erddap")
    assert len(datasets) == 2
    assert datasets[0].dataset_id == "EMSO_OBSEA_CTD"
    assert datasets[0].min_latitude == 41.18


def test_get_dataset_metadata():
    with patch("oceanstream.sources.erddap._fetch_text", return_value=_INFO_CSV):
        meta = get_dataset_metadata("https://erddap.emso.eu/erddap", "EMSO_OBSEA_CTD")
    assert "temperature" in meta["variables"]
    assert meta["variables"]["temperature"]["units"] == "degree_Celsius"
    assert meta["global"]["title"] == "OBSEA CTD 30min"


def test_download_csv(tmp_path: Path):
    csv_body = "time,temperature\nUTC,degree_C\n2024-01-01,15.3\n2024-01-02,15.5\n"
    with patch("oceanstream.sources.erddap._fetch_text", return_value=csv_body):
        out = download_csv(
            "https://erddap.emso.eu/erddap",
            "EMSO_OBSEA_CTD",
            output_path=tmp_path / "test.csv",
        )
    text = out.read_text()
    # Units row should be stripped
    assert "UTC" not in text
    assert "15.3" in text


def test_read_erddap_csv(tmp_path: Path):
    csv_path = tmp_path / "data.csv"
    csv_path.write_text("time,temperature\n2024-01-01,15.3\n2024-01-02,15.5\n")
    df = read_erddap_csv(csv_path)
    assert len(df) == 2
    assert "temperature" in df.columns
