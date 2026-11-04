"""Campaign metadata from a cruise catalogue row, and the sources it relies on."""

from __future__ import annotations

import csv
import io
import json
import tarfile
import zipfile
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from oceanstream.campaign import catalogue
from oceanstream.sources import gps, mirror, ncei

COLUMNS = ["cruise_id", "campaign_title", "programme_project", "cruise_type", "repository",
           "download_url", "landing_page_url", "dataset_doi", "cruise_doi", "licence", "citation",
           "platform_name", "platform_type", "platform_operator", "institution", "funding",
           "chief_scientist", "contact_scientists", "contact_emails", "start_date", "end_date",
           "data_first_date", "data_last_date", "region", "lon_min", "lon_max", "lat_min",
           "lat_max", "scientific_objectives", "hardware_class", "transceivers",
           "frequencies_khz", "pulse_mode", "sample_type", "positions",
           "transducer_depth_config", "calibration", "publications", "cruise_report_url",
           "homepage_url", "notes", "days_with_data"]
ROWS = [
    {"cruise_id": "HB2402", "campaign_title": "NEFSC 2024 Spring Bottom Trawl Survey",
     "repository": "NOAA NCEI Water Column Sonar Data",
     "download_url": "s3://noaa-wcsd-pds/data/raw/Henry_B._Bigelow/HB2402/EK80/",
     "dataset_doi": '"10.25921/cftq-6v25"',
     "licence": "US public domain (NCEI: 'Not subject to copyright protection within the "
                "United States'); cite the DOI",
     "citation": "NOAA NEFSC. 2024. EK80 Water Column Sonar Data Collected During HB2402.",
     "platform_name": "NOAA Ship Henry B. Bigelow", "platform_type": "fisheries survey vessel",
     "institution": "NEFSC; NOAA NMFS NEFSC", "chief_scientist": "A. Scientist",
     "contact_emails": "a.scientist@example.org", "start_date": "2024-03-04",
     "end_date": "2024-05-13", "data_first_date": "2024-03-04", "data_last_date": "2024-05-13",
     "region": "North Atlantic Ocean", "lon_min": "-76", "lon_max": "-65", "lat_min": "35",
     "lat_max": "45", "hardware_class": "WBT (EK80 wideband transceivers)",
     "frequencies_khz": "18; 38; 70; 120; 200", "pulse_mode": "CW",
     "sample_type": "power+angle", "positions": "NMEA in raw file: GGA",
     "transducer_depth_config": "TransducerOffsetZ 0 m; WaterLevelDraft 0 m",
     "publications": "Foley C. 2025, Spring survey report, doi:10.25923/5npn-ab12; Other 2024"},
    {"cruise_id": "SD1046-2020-01", "campaign_title": "USV-based acoustic survey of pollock",
     "repository": "NOAA NCEI Water Column Sonar Data",
     "download_url": "s3://noaa-wcsd-pds/data/raw/Saildrone/SD1046-2020-01/EK80/",
     "dataset_doi": "10.25921/ntmw-f777", "licence": "US public domain",
     "platform_name": "Saildrone Explorer (SD-10xx class)",
     "platform_type": "uncrewed surface vehicle", "institution": "AFSC",
     "start_date": "2020-06-23", "end_date": "2020-08-24", "frequencies_khz": "38; 200",
     "pulse_mode": "CW", "sample_type": "complex float32 (1 sector)",
     "positions": "no NMEA position sentences in sampled file",
     "calibration": "mounted in a gimbal on the keel at a depth of 1.9 m (draft)"},
    {"cruise_id": "Sailbuoy Echo PolarFront 2022", "campaign_title": "Sailbuoy Echo EK80",
     "repository": "Zenodo (Akvaplan-niva / PolarFront communities)",
     "download_url": "https://doi.org/10.5281/zenodo.14617117",
     "landing_page_url": "https://doi.org/10.5281/zenodo.14617117",
     "dataset_doi": "10.5281/zenodo.14617117", "licence": "CC0 1.0",
     "platform_name": "Sailbuoy Echo",
     "platform_type": "Sailbuoy (sail-driven uncrewed surface vehicle)",
     "platform_operator": "Akvaplan-niva", "institution": "Akvaplan-niva",
     "start_date": "2022-04-29", "end_date": "2022-05-13",
     "frequencies_khz": "200 (FM 185–255)", "pulse_mode": "FM", "sample_type": "complex",
     "cruise_report_url": "https://doi.org/10.5281/zenodo.7128746"},
]


@pytest.fixture
def csv_path(tmp_path: Path) -> Path:
    p = tmp_path / "cruises.csv"
    with p.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        for r in ROWS:
            w.writerow({c: r.get(c, "") for c in COLUMNS})
    return p


def test_ncei_ship_row(csv_path: Path, tmp_path: Path) -> None:
    row = catalogue.read_row(csv_path, "hb2402")
    d = tmp_path / "HB2402"
    catalogue.write_metadata(d, "HB2402", row)
    stac = json.loads((d / catalogue.STAC_METADATA).read_text())
    ingest = json.loads((d / catalogue.INGEST).read_text())
    assert stac["license"] == "other"
    assert {"rel": "cite-as", "href": "https://doi.org/10.25921/cftq-6v25",
            "title": "Source dataset (raw echosounder files)"} in stac["links"]
    assert stac["sci:publications"][0]["doi"] == "10.25923/5npn-ab12"
    assert stac["oceanstream:platform"]["kind"] == "research_vessel"
    assert ingest["campaignSlug"] == "ncei_hb2402" and ingest["provider"] == "other"
    assert ingest["platform"]["webPlatformType"] == "RESEARCH_VESSEL"
    assert catalogue.check_ingest(ingest) == []
    published = (d / catalogue.STAC_METADATA).read_text() + (d / catalogue.INGEST).read_text()
    assert "a.scientist@example.org" not in published  # e-mails stay local
    assert "a.scientist@example.org" in (d / catalogue.SUMMARY).read_text()
    toml = (d / catalogue.RUN_CONFIG).read_text()
    assert 'ship = "Henry_B._Bigelow"' in toml and '"power"' in toml
    assert "enabled = false" in toml  # NMEA in the files


def test_saildrone_and_sailbuoy_rows(csv_path: Path, tmp_path: Path) -> None:
    sd = catalogue.read_row(csv_path, "SD1046-2020-01")
    assert catalogue.slug("SD1046-2020-01", sd) == "saildrone_sd1046_2020_01"
    assert catalogue.web_provider(sd) == "saildrone"
    assert catalogue.depth_offset(sd) == 1.9
    toml = catalogue.run_config_toml("SD1046-2020-01", sd)
    assert "enabled = true" in toml and '"complex"' in toml
    sb = catalogue.read_row(csv_path, "Sailbuoy Echo PolarFront 2022")
    assert catalogue.platform_kind(sb) == "sailbuoy"
    assert catalogue.licence(sb)[0] == "CC0-1.0"
    assert catalogue.slug("SAILBUOY_ECHO_2022", sb) == "zenodo_sailbuoy_echo_2022"
    assert '"BB"' in catalogue.run_config_toml("SAILBUOY_ECHO_2022", sb)
    with pytest.raises(ValueError, match="letters, digits"):
        catalogue.write_metadata(tmp_path / "x", "Sailbuoy Echo 2022", sb)


def test_overrides_are_merged_last(csv_path: Path, tmp_path: Path) -> None:
    d = tmp_path / "SD1076"
    d.mkdir()
    (d / catalogue.OVERRIDES).write_text(json.dumps({
        "stac": {"title": "SWFSC 2022 (2207RL) Saildrone SD1076"},
        "notes": ["NCEI reuses the 2019 title; the 2022 report is doi:10.25923/66p9-hc28."]}))
    catalogue.write_metadata(d, "SD1076", catalogue.read_row(csv_path, "SD1046-2020-01"))
    assert json.loads((d / catalogue.STAC_METADATA).read_text())["title"].startswith("SWFSC 2022")
    assert "66p9-hc28" in (d / catalogue.SUMMARY).read_text()


def test_unknown_cruise_suggests_close_ids(csv_path: Path) -> None:
    with pytest.raises(KeyError, match="HB2402"):
        catalogue.read_row(csv_path, "HB2499")


# -- sources --------------------------------------------------------------------------------------
def test_echoview_and_erddap_style_tracks() -> None:
    ev = gps.read_csv(io.StringIO("GPS_fix,GPS_date,GPS_time,Latitude,Longitude\n"
                                  "1,2018-06-18,18:54:00,37.78,-122.30\n"
                                  "0,2018-06-18,18:49:00,37.77,-122.31\n"
                                  "2,2018-06-18,18:59:00,0,0\n"))
    assert list(ev.columns) == ["time", "latitude", "longitude"]
    assert len(ev) == 2 and str(ev.time.iloc[0]) == "2018-06-18 18:49:00+00:00"
    plain = gps.normalise(pd.DataFrame({"time": ["2019-07-10T00:00:00Z"], "latitude": [36.0],
                                        "longitude": [-122.0]}))
    assert gps.describe(plain)["fixes"] == 1
    assert "sd1045_west_coast_survey_2019.csv?time,latitude,longitude" in gps.erddap_url(
        "sd1045_west_coast_survey_2019", "2019-06-20", "2019-08-24")


def test_list_days_drops_other_dates_and_applies_a_clock_shift() -> None:
    listing = {"X-D20190710-T060000.raw": 10, "X-D20190710-T080000.raw": 20,
               "X-D20190710-T080000.idx": 1, "X-D20180101-T000000.raw": 5, "notes.txt": 1}
    days = ncei.list_days(listing, start="2019-07-01", end="2019-07-31")
    assert {d: (v.files, v.bytes) for d, v in days.items()} == {"2019-07-10": (2, 30)}
    shifted = ncei.list_days(listing, start="2019-07-01", shift_hours=-7)
    assert {d: v.files for d, v in shifted.items()} == {"2019-07-09": 1, "2019-07-10": 1}


class FakeS3:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def get_paginator(self, _: str) -> Any:
        objects = self.objects

        class P:
            def paginate(self, Bucket: str, Prefix: str) -> list[dict[str, Any]]:  # noqa: N803
                return [{"Contents": [{"Key": k, "Size": len(v)} for k, v in objects.items()
                                      if k.startswith(Prefix)]}]
        return P()

    def put_object(self, Bucket: str, Key: str, Body: bytes) -> None:  # noqa: N803
        self.objects[Key] = Body


def _tar_gz(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def test_mirror_archive_keeps_raw_members_and_resumes() -> None:
    files = {"FKt/data/EK80/a-D20260122-T000000.raw": b"a" * 10,
             "FKt/data/EK80/a-D20260122-T000000.idx": b"i",
             "FKt/data/docs/readme.txt": b"skip", "FKt/manifest-md5.txt": b"skip"}
    blob = _tar_gz(files)
    s3 = FakeS3()
    r = mirror.mirror_archive("https://x/FKt.tar.gz", "b", "r2r/FKt/raw",
                              opener=lambda u: io.BytesIO(blob), s3=s3)
    assert (r.copied, r.skipped, r.failed) == (2, 0, 0)
    assert sorted(s3.objects) == ["r2r/FKt/raw/a-D20260122-T000000.idx",
                                  "r2r/FKt/raw/a-D20260122-T000000.raw"]
    again = mirror.mirror_archive("https://x/FKt.tar.gz", "b", "r2r/FKt/raw",
                                  opener=lambda u: io.BytesIO(blob), s3=s3)
    assert (again.copied, again.skipped) == (0, 2)


def test_mirror_files_unpacks_zips() -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("day1/x-D20220501-T000000.raw", b"r" * 5)
        zf.writestr("day1/log.txt", b"l")
    s3 = FakeS3()
    data = buf.getvalue()
    r = mirror.mirror_files({"2022-05-01.zip": "https://z/2022-05-01.zip",
                             "calibration.xml": "https://z/calibration.xml"}, "b", "z/raw",
                            opener=lambda u: io.BytesIO(data if u.endswith(".zip") else b"<x/>"),
                            s3=s3, workers=1)
    assert r.failed == 0
    assert sorted(s3.objects) == ["z/raw/calibration.xml", "z/raw/x-D20220501-T000000.raw"]
