"""Tests for GenericProvider auto-detection heuristics."""
from __future__ import annotations

import pandas as pd

from oceanstream.providers.generic import GenericProvider


def test_generic_provider_name():
    p = GenericProvider()
    assert p.name == "generic"
    assert p.is_stationary is False


def test_generic_identify_platform():
    p = GenericProvider()
    assert p.identify_platform("station_001_2024.csv") == "station"
    assert p.identify_platform("buoy.csv") == "buoy"


def test_generic_enrich_renames_lat_lon_time():
    p = GenericProvider()
    df = pd.DataFrame({
        "lat": [10.0],
        "lon": [-150.0],
        "datetime": ["2024-01-01T00:00:00Z"],
    })
    enriched = p.enrich_dataframe(df)
    assert "latitude" in enriched.columns
    assert "longitude" in enriched.columns
    assert "time" in enriched.columns


def test_generic_enrich_does_not_rename_canonical():
    p = GenericProvider()
    df = pd.DataFrame({
        "latitude": [10.0],
        "longitude": [-150.0],
        "time": ["2024-01-01T00:00:00Z"],
    })
    enriched = p.enrich_dataframe(df)
    assert list(enriched.columns) == ["latitude", "longitude", "time"]


def test_generic_alias_mapping():
    p = GenericProvider()
    aliases = p.alias_mapping(["CamelCase", "latitude", "UPPER"])
    assert aliases["CamelCase"] == "camel_case"
    assert aliases["UPPER"] == "upper"
    assert "latitude" not in aliases


def test_generic_detect_confidence_low():
    p = GenericProvider()
    assert p.detect_confidence(["lat", "lon", "time"], [], "data.csv") == 0.1


def test_generic_supports_geotrack():
    p = GenericProvider()
    assert p.supports_module("geotrack") is True
    assert p.supports_module("echodata") is False


def test_generic_units_mapping():
    p = GenericProvider()
    mapping = p.units_mapping(["temp", "sal"], ["degC", "PSU"])
    assert mapping["temp"] == "degC"
    assert mapping["sal"] == "PSU"


def test_generic_parquet_metadata():
    p = GenericProvider()
    df = pd.DataFrame({"latitude": [1.0], "longitude": [2.0]})
    meta = p.parquet_metadata(df)
    assert meta["oceanstream:provider"]["name"] == "generic"
