"""Tests for NoaaPmelProvider (renamed from SaildroneProvider)."""
from __future__ import annotations

import pandas as pd

from oceanstream.providers.saildrone import NoaaPmelProvider, SaildroneProvider
from oceanstream.providers import get_provider


def test_backward_compatible_alias_class():
    """SaildroneProvider is an alias for NoaaPmelProvider."""
    assert SaildroneProvider is NoaaPmelProvider


def test_get_provider_noaa_pmel():
    p = get_provider("noaa_pmel")
    assert p.name == "noaa_pmel"
    assert isinstance(p, NoaaPmelProvider)


def test_get_provider_saildrone_backward_compat():
    p = get_provider("saildrone")
    assert p.name == "noaa_pmel"
    assert isinstance(p, NoaaPmelProvider)


def test_noaa_pmel_is_not_stationary():
    p = NoaaPmelProvider()
    assert p.is_stationary is False


def test_noaa_pmel_identify_platform():
    p = NoaaPmelProvider()
    assert p.identify_platform("sd1030_tpos_2023_7ef2_e8f7_98f9.csv") == "sd1030_tpos_2023"


def test_noaa_pmel_detect_confidence_filename():
    p = NoaaPmelProvider()
    score = p.detect_confidence([], [], "sd1030_tpos_2023.csv")
    assert score >= 0.8


def test_noaa_pmel_detect_confidence_headers():
    p = NoaaPmelProvider()
    score = p.detect_confidence(
        ["trajectory", "SOG", "COG", "TEMP_SBE37_MEAN"], [], "data.csv"
    )
    assert score >= 0.6


def test_noaa_pmel_detect_confidence_no_match():
    p = NoaaPmelProvider()
    score = p.detect_confidence(["temperature", "salinity"], [], "random.csv")
    assert score == 0.0


def test_noaa_pmel_semantic_aliases():
    p = NoaaPmelProvider()
    aliases = p.alias_mapping(["SOG", "COG", "HDG"])
    assert aliases["SOG"] == "speed_over_ground_ms"
    assert aliases["COG"] == "course_over_ground_deg"


def test_noaa_pmel_enrich_converts_time():
    p = NoaaPmelProvider()
    df = pd.DataFrame({
        "platform_id": ["sd1030"],
        "latitude": [10.0],
        "longitude": [-150.0],
        "time": ["2025-11-07T12:00:00Z"],
    })
    enriched = p.enrich_dataframe(df)
    assert enriched["time"].dtype.kind == "M"
