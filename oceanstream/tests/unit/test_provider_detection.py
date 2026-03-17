"""Tests for provider auto-detection."""
from __future__ import annotations

from pathlib import Path
import tempfile
import textwrap

from oceanstream.providers.factory import (
    detect_or_get_provider,
    detect_provider,
    get_provider,
    list_providers,
)
from oceanstream.providers.generic import GenericProvider
from oceanstream.providers.saildrone import NoaaPmelProvider


def _write_csv(tmp: Path, content: str) -> Path:
    p = tmp / "test.csv"
    p.write_text(textwrap.dedent(content), encoding="utf-8")
    return p


def test_list_providers_includes_all():
    providers = list_providers()
    for name in ["noaa_pmel", "r2r", "generic", "cmems", "emso", "emodnet",
                 "norsoop", "oceanlab", "ooi", "plocan"]:
        assert name in providers, f"Missing provider: {name}"
    # "saildrone" is a backward-compatible alias and should not appear in the list
    assert "saildrone" not in providers


def test_detect_provider_saildrone_headers(tmp_path: Path):
    csv = _write_csv(tmp_path, """\
        trajectory,latitude,longitude,time,SOG,COG,TEMP_SBE37_MEAN
        1,10,-150,2024-01-01T00:00:00Z,5.2,180,23.5
    """)
    p = detect_provider(csv)
    assert p.name == "noaa_pmel"


def test_detect_provider_saildrone_filename(tmp_path: Path):
    csv = tmp_path / "sd1030_tpos_2023.csv"
    csv.write_text("x,y,z\n1,2,3\n")
    p = detect_provider(csv)
    assert p.name == "noaa_pmel"


def test_detect_provider_r2r_geocsv(tmp_path: Path):
    csv = tmp_path / "RR2401_gnss.geocsv"
    csv.write_text(
        "# dataset: RR2401\n"
        "# source_repository: R2R\n"
        "# field_unit: degree_east,degree_north,ISO_8601\n"
        "ship_longitude,ship_latitude,iso_time\n"
        "-122.5,37.8,2024-01-01T00:00:00Z\n"
    )
    p = detect_provider(csv)
    assert p.name == "r2r"


def test_detect_provider_emso(tmp_path: Path):
    csv = tmp_path / "EMSO_OBSEA_CTD_30min.csv"
    csv.write_text("time,latitude,longitude,temperature\n2024-01-01,41.18,1.75,15.3\n")
    p = detect_provider(csv)
    assert p.name == "emso"


def test_detect_provider_generic_fallback(tmp_path: Path):
    csv = _write_csv(tmp_path, """\
        a,b,c
        1,2,3
    """)
    p = detect_provider(csv)
    assert p.name == "generic"


def test_detect_provider_cmems(tmp_path: Path):
    csv = tmp_path / "cmems_nws_northsea_insitu.csv"
    csv.write_text(
        "variable,platform_id,platform_type,time,longitude,latitude,"
        "depth,pressure,is_depth_from_producer,value,value_qc,"
        "institution,doi,product_doi\n"
        "TEMP,6200086,MO,2026-03-10T00:00:00Z,6.5,55.0,35.0,,1,"
        "5.1,1,BSH,,https://doi.org/10.48670/moi-00045\n"
    )
    p = detect_provider(csv)
    assert p.name == "cmems"


def test_detect_or_get_provider_explicit():
    p = detect_or_get_provider("r2r")
    assert p.name == "r2r"


def test_detect_or_get_provider_none():
    p = detect_or_get_provider(None)
    assert p.name == "generic"


def test_detect_or_get_provider_with_file(tmp_path: Path):
    csv = tmp_path / "sd1030_test.csv"
    csv.write_text("trajectory,SOG\n1,5.2\n")
    p = detect_or_get_provider(None, csv)
    assert p.name == "noaa_pmel"
