"""Conversions and the campaign record: what `campaign create` gave survives,
the data summary follows the data, and rewrites replace rather than add rows."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

DAY_1 = (
    "latitude,longitude,time,TEMP_SBE37_MEAN\n"
    "10.0,20.0,2024-01-01T00:00:00Z,18.5\n"
    "10.1,20.1,2024-01-01T01:00:00Z,18.6\n"
)
DAY_2 = (
    "latitude,longitude,time,TEMP_SBE37_MEAN\n"
    "10.2,20.2,2024-01-02T00:00:00Z,18.7\n"
    "10.3,20.3,2024-01-02T01:00:00Z,18.8\n"
    "10.4,20.4,2024-01-02T02:00:00Z,18.9\n"
)


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch):
    from oceanstream.config.settings import Settings

    monkeypatch.setenv("OCEANSTREAM_CAMPAIGNS_DIR", str(tmp_path / "campaigns"))
    monkeypatch.setattr(Settings, "METADATA_DIR", tmp_path / "metadata")
    (tmp_path / "in").mkdir()
    (tmp_path / "in" / "day1.csv").write_text(DAY_1)
    (tmp_path / "in" / "day2.csv").write_text(DAY_2)
    return tmp_path


def _convert(workspace: Path, name: str, **kwargs) -> None:
    from oceanstream.geotrack.processor import convert
    from oceanstream.providers import get_provider

    convert(
        provider=get_provider("saildrone"),
        input_source=workspace / "in" / name,
        output_dir=workspace / "out",
        campaign_id="C1",
        yes=True,
        **kwargs,
    )


def _record(workspace: Path) -> dict:
    return json.loads((workspace / "campaigns" / "C1" / "campaign.json").read_text())


def _rows(workspace: Path) -> int:
    files = (workspace / "out" / "C1").glob("lat_bin=*/lon_bin=*/*.parquet")
    return sum(len(pd.read_parquet(f)) for f in files)


def test_conversion_keeps_campaign_create_fields(workspace: Path):
    from oceanstream.geotrack.campaign import create_campaign

    create_campaign("C1", {"description": "Test cruise", "license": "CC-BY-4.0",
                           "platforms": [{"id": "sd1030", "name": "Saildrone 1030"}]})
    (workspace / "campaigns" / "C1" / "summary.md").write_text("from the catalogue")

    _convert(workspace, "day1.csv")

    record = _record(workspace)
    assert record["description"] == "Test cruise"
    assert record["license"] == "CC-BY-4.0"
    assert record["total_rows"] == 2
    assert record["output_directory"] == str((workspace / "out" / "C1").resolve())
    assert (workspace / "campaigns" / "C1" / "summary.md").exists()


def test_later_conversion_updates_record(workspace: Path):
    _convert(workspace, "day1.csv")
    created = _record(workspace)["created_at"]

    _convert(workspace, "day2.csv")

    record = _record(workspace)
    assert record["total_rows"] == 5
    assert record["total_files"] == 2
    assert record["partition_count"] > 0
    assert record["end_date"].startswith("2024-01-02T02:00")
    assert record["created_at"] == created
    assert _rows(workspace) == 5


def test_force_reprocess_replaces_rows(workspace: Path):
    _convert(workspace, "day1.csv")
    _convert(workspace, "day1.csv", force_reprocess=True)

    assert _rows(workspace) == 2
    assert _record(workspace)["total_rows"] == 2


def test_dry_run_leaves_existing_data(workspace: Path):
    _convert(workspace, "day1.csv")
    _convert(workspace, "day2.csv", dry_run=True)

    assert _rows(workspace) == 2


def test_register_merges_platforms():
    from oceanstream.geotrack.campaign import _merge_by_key

    given = [{"id": "sd1030", "name": "Saildrone 1030"}, {"id": "sd1033"}]
    found = [{"id": "sd1030", "name": None, "row_count": 10}, {"id": "sd1079", "row_count": 5}]

    assert _merge_by_key(given, found, "id") == [
        {"id": "sd1030", "name": "Saildrone 1030", "row_count": 10},
        {"id": "sd1033"},
        {"id": "sd1079", "row_count": 5},
    ]
