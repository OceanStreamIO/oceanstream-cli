"""Tests for the Argovis client, Argo profile parsing and campaign Argo datasets.

No network: the Argovis client is replaced by a fake or ``urlopen`` is patched.
"""
from __future__ import annotations

import io
import json
import struct
import urllib.error
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd
import pyarrow.parquet as pq
import pytest
from typer.testing import CliRunner

from oceanstream.argo import (
    ArgoDataset,
    Track,
    apply_qc,
    build_argo_dataset,
    expand_bbox,
    load_track,
    parse_argovis_profile,
    track_matchups,
    variable_major,
)
from oceanstream.argo.profiles import least_processed_mode, profile_network
from oceanstream.cli import app
from oceanstream.geotrack.campaign import create_campaign, load_campaign_metadata
from oceanstream.sources.argovis import (
    ArgovisClient,
    bbox_polygons,
    fetch_profiles,
    time_windows,
)


def argovis_doc(  # noqa: PLR0913
    pid: str = "6903006_283",
    lon: float = -21.69,
    lat: float = 68.39,
    timestamp: str = "2021-07-23T10:08:30.000Z",
    keys: list[str] | None = None,
    data: list[list] | None = None,
    modes: list[str | None] | None = None,
    units: list[str | None] | None = None,
    sources: list[str] | None = None,
) -> dict:
    """An Argovis v2 profile document with variable-major data."""
    keys = keys or [
        "pressure", "pressure_argoqc", "salinity", "salinity_argoqc",
        "temperature", "temperature_argoqc",
    ]
    data = data or [
        [3.2, 10.0, 50.0, 400.0],
        [1, 1, 1, 1],
        [31.9, 34.1, 34.8, 34.9],
        [1, 4, 1, 1],
        [3.3, 2.0, 0.5, -0.2],
        [1, 1, 1, 1],
    ]
    modes = modes or ["D", None, "D", None, "D", None]
    units = units or ["decibar", None, "psu", None, "degree_Celsius", None]
    return {
        "_id": pid,
        "geolocation": {"type": "Point", "coordinates": [lon, lat]},
        "timestamp": timestamp,
        "cycle_number": int(pid.split("_")[1].rstrip("D")),
        "geolocation_argoqc": 1,
        "timestamp_argoqc": 1,
        "profile_direction": "A",
        "vertical_sampling_scheme": "Primary sampling: averaged",
        "source": [{
            "source": sources or ["argo_core"],
            "url": f"ftp://example/{pid}.nc",
            "date_updated": "2024-04-04T08:28:47.000Z",
        }],
        "metadata": [pid.split("_")[0] + "_m0"],
        "data_info": [keys, ["units", "data_keys_mode"], [[u, m] for u, m in zip(units, modes)]],
        "data": data,
    }


class FakeArgovis:
    """Returns the same documents for every window (exercises de-duplication)."""

    base_url = "https://argovis.example"

    def __init__(self, docs: list[dict]):
        self.docs = docs
        self.calls: list[dict] = []

    def search_profiles(self, **kwargs):
        self.calls.append(kwargs)
        return list(self.docs)


TRACK = Track(
    time=np.array(["2021-07-22T12:00", "2021-07-22T13:00"], dtype="datetime64[us]"),
    latitude=np.array([68.40, 68.40]),
    longitude=np.array([-21.70, -20.00]),
    source="test",
)

BBOX = [-31.0, 62.7, -6.5, 69.3]
START, END = datetime(2021, 7, 5, 15, 14), datetime(2021, 7, 26, 12, 56)


def build(docs: list[dict], **kwargs) -> ArgoDataset:
    return build_argo_dataset(BBOX, START, END, client=FakeArgovis(docs), **kwargs)


# ── Parsing ────────────────────────────────────────────────────────────────


def test_variable_major_keeps_argovis_v2_layout():
    data = [[1.0, 2.0, 3.0, 4.0], [5.0, 4.0, 3.0, 2.0], [34.0, 34.1, 34.2, 34.3]]
    assert variable_major(data, ["pressure", "temperature", "salinity"]) == data


def test_variable_major_transposes_row_major_layout():
    rows = [[1.0, 5.0], [2.0, 4.0], [3.0, 3.0]]
    assert variable_major(rows, ["pressure", "temperature"]) == [[1.0, 2.0, 3.0], [5.0, 4.0, 3.0]]


def test_variable_major_prefers_v2_when_ambiguous():
    data = [[1.0, 2.0, 3.0], [5.0, 4.0, 3.0], [34.0, 34.1, 34.2]]
    assert variable_major(data, ["pressure", "temperature", "salinity"])[0] == [1.0, 2.0, 3.0]


def test_variable_major_rejects_unknown_layout():
    with pytest.raises(ValueError):
        variable_major([[1.0, 2.0], [3.0]], ["pressure", "temperature", "salinity"])


def test_parse_profile_unpacks_every_level():
    p = parse_argovis_profile(argovis_doc())
    assert p["n_levels"] == 4
    assert p["measurements"]["pressure"] == [3.2, 10.0, 50.0, 400.0]
    assert p["qc_flags"]["salinity"] == [1, 4, 1, 1]
    assert (p["pressure_min"], p["pressure_max"]) == (3.2, 400.0)
    assert p["variables"] == ["salinity", "temperature"]


def test_parse_profile_identity_time_and_provenance():
    p = parse_argovis_profile(argovis_doc())
    assert (p["profile_id"], p["wmo_id"], p["cycle_number"]) == ("6903006_283", "6903006", 283)
    assert p["time"] == datetime(2021, 7, 23, 10, 8, 30)
    assert (p["latitude"], p["longitude"]) == (68.39, -21.69)
    assert p["units"]["temperature"] == "degree_Celsius"
    assert p["data_mode"] == "D"
    assert p["source_url"].endswith("6903006_283.nc")
    assert p["source_updated"] == datetime(2024, 4, 4, 8, 28, 47)


def test_parse_profile_sfile_only_fills_missing_core_variables():
    keys = [
        "pressure", "temperature", "temperature_sfile", "salinity_sfile",
        "salinity_sfile_argoqc", "doxy",
    ]
    data = [[5.0, 10.0], [7.0, 6.0], [99.0, 99.0], [35.0, 35.1], [1, 3], [250.0, 240.0]]
    doc = argovis_doc(
        keys=keys, data=data, modes=["R", "R", "A", "A", None, "A"],
        units=[None] * 6, sources=["argo_bgc"],
    )
    p = parse_argovis_profile(doc)
    assert p["measurements"]["temperature"] == [7.0, 6.0]
    assert p["measurements"]["salinity"] == [35.0, 35.1]
    assert p["qc_flags"]["salinity"] == [1, 3]
    assert p["measurements"]["doxy"] == [250.0, 240.0]
    assert (p["network"], p["data_mode"]) == ("bgc", "R")


def test_parse_profile_replaces_non_finite_values():
    data = [[1.0, 2.0], [1, 1], [float("nan"), 34.0], [1, 1], [None, 3.0], [1, 1]]
    p = parse_argovis_profile(argovis_doc(data=data))
    assert p["measurements"]["salinity"] == [None, 34.0]
    assert p["measurements"]["temperature"] == [None, 3.0]


@pytest.mark.parametrize("sources,expected", [
    (["argo_core"], "core"),
    (["argo_core", "argo_deep"], "deep"),
    (["argo_bgc"], "bgc"),
])
def test_profile_network(sources, expected):
    assert profile_network({"source": [{"source": sources}]}) == expected


def test_least_processed_mode():
    assert least_processed_mode({"pressure": "D", "temperature": "D", "salinity": "A"}) == "A"
    assert least_processed_mode({"doxy": "R"}) is None


# ── QC ─────────────────────────────────────────────────────────────────────


def test_apply_qc_blanks_rejected_values_only():
    measurements = {
        "pressure": [1.0, 2.0, 3.0], "salinity": [34.0, 30.0, 34.2], "temperature": [5.0, 4.0, 3.0],
    }
    flags = {"pressure": [1, 1, 1], "salinity": [1, 4, 2], "temperature": [1, 1, 8]}
    masked, rejected = apply_qc(measurements, flags)
    assert masked["salinity"] == [34.0, None, 34.2]
    assert masked["temperature"] == [5.0, 4.0, 3.0]
    assert rejected == 1


def test_apply_qc_bad_pressure_blanks_the_whole_level():
    masked, rejected = apply_qc(
        {"pressure": [1.0, 2.0], "temperature": [5.0, 4.0]},
        {"pressure": [1, 4], "temperature": [1, 1]},
    )
    assert masked == {"pressure": [1.0, None], "temperature": [5.0, None]}
    assert rejected == 2


# ── Bounds ─────────────────────────────────────────────────────────────────


def test_expand_bbox_scales_longitude_by_latitude():
    w, s, _, n = expand_bbox([-20.0, 64.0, -10.0, 66.0], 111.32)
    assert s == pytest.approx(63.0) and n == pytest.approx(67.0)
    assert w == pytest.approx(-20.0 - 1 / np.cos(np.radians(65.0)), rel=1e-6)


def test_bbox_polygons_split_across_antimeridian():
    assert len(bbox_polygons([-31.0, 62.0, -6.0, 69.0])) == 1
    rings = bbox_polygons([170.0, -10.0, -170.0, 10.0])
    assert len(rings) == 2
    assert rings[0][1][0] == 180.0 and rings[1][0][0] == -180.0


def test_time_windows_cover_range_without_gaps():
    windows = time_windows(START, END, 10)
    assert windows[0][0] == START and windows[-1][1] == END
    assert all(a[1] == b[0] for a, b in zip(windows, windows[1:]))
    assert len(windows) == 3


# ── Argovis client ─────────────────────────────────────────────────────────


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("https://argovis.example/argo", code, "err", {}, None)


def test_client_sends_query_and_api_key():
    seen = []

    def fake_urlopen(req, timeout):
        seen.append(req)
        return FakeResponse(json.dumps([{"_id": "1_1"}]).encode())

    client = ArgovisClient(base_url="https://argovis.example/", api_key="secret")
    with patch("urllib.request.urlopen", fake_urlopen):
        docs = client.search_profiles(
            polygon=bbox_polygons(BBOX)[0], start=START, end=END,
        )
    assert docs == [{"_id": "1_1"}]
    url = seen[0].full_url
    assert url.startswith("https://argovis.example/argo?")
    assert "startDate=2021-07-05T15%3A14%3A00Z" in url and "data=all" in url
    assert seen[0].get_header("X-argokey") == "secret"


def test_client_reads_api_key_from_environment(monkeypatch):
    monkeypatch.setenv("ARGOVIS_API_KEY", "from-env")
    assert ArgovisClient().api_key == "from-env"


def test_client_treats_404_as_no_profiles():
    with patch("urllib.request.urlopen", side_effect=http_error(404)):
        assert ArgovisClient().search_profiles(platform="1") == []


def test_client_retries_rate_limits():
    responses = [http_error(429), FakeResponse(b"[]")]

    def fake_urlopen(req, timeout):
        r = responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r

    with patch("urllib.request.urlopen", fake_urlopen), patch("time.sleep") as sleep:
        assert ArgovisClient().search_profiles(platform="1") == []
    sleep.assert_called_once()


def test_fetch_profiles_dedupes_across_windows():
    client = FakeArgovis([argovis_doc("1_1"), argovis_doc("1_2")])
    docs = fetch_profiles(BBOX, START, END, chunk_days=10, client=client)
    assert sorted(d["_id"] for d in docs) == ["1_1", "1_2"]
    assert len(client.calls) == 3


def test_fetch_profiles_halves_windows_argovis_rejects_as_too_large():
    calls = []

    class TooLarge(FakeArgovis):
        def search_profiles(self, **kwargs):
            calls.append((kwargs["start"], kwargs["end"]))
            if kwargs["end"] - kwargs["start"] > (END - START) / 2:
                raise http_error(413)
            return [argovis_doc(f"1_{len(calls)}")]

    docs = fetch_profiles(BBOX, START, END, chunk_days=30, client=TooLarge([]))
    assert len(docs) == 2
    assert calls[1][0] == START and calls[2][1] == END and calls[1][1] == calls[2][0]


# ── Track ──────────────────────────────────────────────────────────────────


def test_track_matchups_nearest_point_and_time_offset():
    profiles = [{"latitude": 68.39, "longitude": -21.69, "time": datetime(2021, 7, 23, 10, 0)}]
    (m,) = track_matchups(profiles, TRACK)
    assert m["nearest_track_lon"] == -21.70
    assert m["nearest_track_time"] == datetime(2021, 7, 22, 12, 0)
    assert m["nearest_track_km"] < 2
    assert m["time_offset_hours"] == pytest.approx(22.0)


def test_track_matchups_without_track_returns_empty():
    assert track_matchups([{"latitude": 1.0, "longitude": 1.0, "time": None}], None) == [{}]


def test_load_track_reads_partitioned_geotrack_output(tmp_path: Path):
    root = tmp_path / "FK161229"
    for i, (lat_bin, lon) in enumerate([("lat_60_65", -20.0), ("lat_65_70", -19.0)]):
        part = root / f"lat_bin={lat_bin}" / "lon_bin=lon_-20_-15"
        part.mkdir(parents=True)
        pd.DataFrame({
            "time": pd.to_datetime([f"2021-07-2{i}T00:00:00Z"]),
            "latitude": [64.0 + i * 2],
            "longitude": [lon],
            "SOG": [10.0],
        }).to_parquet(part / "part-0.parquet")
    (root / "stac").mkdir()
    (root / "stac" / "collection.json").write_text("{}")

    track = load_track(root)
    assert len(track) == 2
    assert list(track.longitude) == [-20.0, -19.0]
    assert track.time[0] == np.datetime64("2021-07-20T00:00:00")


def test_load_track_reads_csv_with_short_column_names(tmp_path: Path):
    path = tmp_path / "nav.csv"
    path.write_text("Lat,Lon,Time\n65.0,-20.0,2021-07-22T12:00:00Z\nbad,,\n")
    track = load_track(path)
    assert len(track) == 1 and track.latitude[0] == 65.0


def test_load_track_needs_positions(tmp_path: Path):
    path = tmp_path / "x.csv"
    path.write_text("a,b\n1,2\n")
    with pytest.raises(ValueError, match="latitude/longitude"):
        load_track(path)


# ── Dataset ────────────────────────────────────────────────────────────────

DOCS = [
    argovis_doc("6903006_283"),
    argovis_doc("6903006_284", timestamp="2021-07-27T20:00:00Z"),  # after the window
    argovis_doc("3902102_113", lon=-10.0, lat=63.0, timestamp="2021-07-10T00:00:00Z"),
]


def test_build_keeps_profiles_in_window_with_matchups():
    ds = build(DOCS, track=TRACK, campaign_id="iessns2021")
    assert [p["profile_id"] for p in ds.profiles] == ["3902102_113", "6903006_283"]
    near = ds.profiles[1]
    assert near["nearest_track_km"] < 2
    assert near["time_offset_hours"] == pytest.approx(22.14, abs=0.01)
    pv = ds.provenance
    assert (pv["n_profiles"], pv["n_floats"], pv["campaign_id"]) == (2, 2, "iessns2021")
    assert pv["matched_to_track"] and pv["track_points"] == 2
    assert pv["doi"] == "10.17882/42182" and pv["license"] == "CC-BY-4.0"


def test_build_max_track_km_drops_distant_profiles():
    ds = build(DOCS, track=TRACK, max_track_km=50)
    assert [p["profile_id"] for p in ds.profiles] == ["6903006_283"]


def test_build_max_track_km_needs_a_track():
    with pytest.raises(ValueError, match="track"):
        build(DOCS, max_track_km=50)


def test_build_buffer_days_widens_the_window():
    ds = build(DOCS, buffer_days=2)
    assert len(ds.profiles) == 3
    assert ds.provenance["time_end"] == "2021-07-28T12:56:00Z"


def test_build_without_track_leaves_matchup_fields_empty():
    ds = build(DOCS)
    assert all(p["nearest_track_km"] is None for p in ds.profiles)
    assert ds.provenance["nearest_track_km"] is None


def _point(wkb: bytes) -> tuple[float, float]:
    order, kind, x, y = struct.unpack("<BIdd", wkb)
    assert (order, kind) == (1, 1)
    return x, y


def test_profiles_layout_has_list_columns_and_geoparquet_metadata(tmp_path: Path):
    ds = build(DOCS, track=TRACK, campaign_id="iessns2021")
    path = ds.to_geoparquet(tmp_path / "argo.parquet")
    table = pq.read_table(path)
    assert table.num_rows == 2
    assert str(table.schema.field("temperature").type) == "list<element: double>"
    assert str(table.schema.field("salinity_qc").type) == "list<element: int8>"
    assert str(table.schema.field("time").type) == "timestamp[us, tz=UTC]"

    row = table.slice(1, 1).to_pylist()[0]
    assert row["campaign_id"] == "iessns2021"
    assert row["salinity"] == [31.9, 34.1, 34.8, 34.9]
    assert row["salinity_qc"] == [1, 4, 1, 1]
    assert json.loads(row["data_modes"]) == {"pressure": "D", "salinity": "D", "temperature": "D"}
    assert _point(row["geometry"]) == (-21.69, 68.39)

    meta = table.schema.metadata
    geo = json.loads(meta[b"geo"])
    assert geo["primary_column"] == "geometry"
    assert geo["columns"]["geometry"]["geometry_types"] == ["Point"]
    assert geo["columns"]["geometry"]["bbox"] == [-21.69, 63.0, -10.0, 68.39]
    argo = json.loads(meta[b"oceanstream:argo"])
    assert (argo["layout"], argo["qc"], argo["n_profiles"]) == ("profiles", "raw", 2)
    assert json.loads(meta[b"oceanstream:units"])["pressure"] == "decibar"


def test_profiles_layout_good_qc_blanks_flagged_values():
    ds = build([argovis_doc()])
    row = ds.to_table(qc="good").to_pylist()[0]
    assert row["salinity"] == [31.9, None, 34.8, 34.9]
    assert row["salinity_qc"] == [1, 4, 1, 1]
    assert json.loads(ds.to_table(qc="good").schema.metadata[b"oceanstream:argo"])[
        "rejected_values"
    ] == 1


def test_levels_layout_has_one_row_per_level():
    data = [[1.0, 2.0, 3.0], [1, 4, 1], [34.0, 34.1, 34.2], [1, 1, 1], [5.0, 4.0, 3.0], [1, 1, 1]]
    ds = build([argovis_doc(data=data)], track=TRACK)
    raw = ds.to_dataframe(layout="levels")
    assert len(raw) == 3
    assert list(raw["level"]) == [0, 1, 2]
    assert list(raw["temperature"]) == [5.0, 4.0, 3.0]
    assert raw["nearest_track_km"].notna().all()

    good = ds.to_dataframe(layout="levels", qc="good")
    assert list(good["level"]) == [0, 2]  # rejected pressure drops the level


def test_extra_variables_get_their_own_columns():
    keys = ["pressure", "temperature", "salinity", "doxy", "doxy_argoqc"]
    data = [[5.0, 10.0], [7.0, 6.0], [35.0, 35.1], [250.0, 240.0], [1, 1]]
    ds = build([argovis_doc(keys=keys, data=data, modes=["D"] * 5, units=[None] * 5)])
    names = ds.to_table().schema.names
    assert names.index("doxy") > names.index("salinity_qc")
    assert "doxy_qc" in names


def test_empty_dataset_still_writes_a_valid_file(tmp_path: Path):
    path = ArgoDataset(profiles=[]).to_geoparquet(tmp_path / "empty.parquet")
    table = pq.read_table(path)
    assert table.num_rows == 0
    assert {"pressure", "temperature", "salinity", "geometry"} <= set(table.schema.names)
    assert "bbox" not in json.loads(table.schema.metadata[b"geo"])["columns"]["geometry"]


def test_invalid_layout_is_rejected():
    with pytest.raises(ValueError, match="layout"):
        ArgoDataset(profiles=[]).to_table(layout="wide")


def test_geopandas_reads_the_output(tmp_path: Path):
    gpd = pytest.importorskip("geopandas")
    path = build(DOCS).to_geoparquet(tmp_path / "argo.parquet", layout="levels")
    gdf = gpd.read_parquet(path)
    assert len(gdf) == 8
    assert gdf.crs.to_string() in ("OGC:CRS84", "EPSG:4326")
    assert gdf.geometry.iloc[0].x == -10.0


# ── CLI ────────────────────────────────────────────────────────────────────

runner = CliRunner()


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home)
    return home


@pytest.fixture
def fake_argovis():
    def search(self, **kwargs):
        return list(DOCS)

    with patch.object(ArgovisClient, "search_profiles", search):
        yield


def test_cli_uses_campaign_extent_and_records_the_dataset(fake_home, fake_argovis, tmp_path):
    create_campaign("IESSNS2021", {
        "bbox": BBOX, "start_date": "2021-07-05T15:14:00Z", "end_date": "2021-07-26",
    })
    out = tmp_path / "argo.parquet"
    result = runner.invoke(app, ["campaign", "argo", "IESSNS2021", "-o", str(out)])
    assert result.exit_code == 0, result.output
    assert "2 profiles from 2 floats" in result.output
    assert "2021-07-27T00:00:00Z" in result.output  # a bare end date includes that day
    assert pq.read_table(out).num_rows == 2

    recorded = load_campaign_metadata("IESSNS2021")["argo_dataset"]
    assert recorded["path"] == str(out.resolve()) and recorded["n_profiles"] == 2

    shown = runner.invoke(app, ["campaign", "show", "IESSNS2021"])
    assert "Argo Profiles:      2 from 2 floats" in shown.output


def test_cli_without_campaign_needs_an_extent(fake_home):
    result = runner.invoke(app, ["campaign", "argo", "NOPE"])
    assert result.exit_code == 1
    assert "--bbox and --start-date" in result.output


def test_cli_explicit_extent_levels_layout_and_dry_run(fake_home, fake_argovis, tmp_path):
    args = [
        "campaign", "argo", "adhoc", "--bbox=-31,62.7,-6.5,69.3",
        "--start-date", "2021-07-05", "--end-date", "2021-07-26", "--layout", "levels",
    ]
    dry = runner.invoke(app, [*args, "--dry-run"], catch_exceptions=False)
    assert dry.exit_code == 0 and "Dry run" in dry.output

    out = tmp_path / "levels.parquet"
    result = runner.invoke(app, [*args, "-o", str(out)])
    assert result.exit_code == 0, result.output
    assert pq.read_table(out).num_rows == 8  # two 4-level profiles in the window


def test_cli_rejects_bad_options(fake_home):
    base = ["campaign", "argo", "x", "--bbox=1,2,3,4", "--start-date", "2021-01-01"]
    assert "--layout" in runner.invoke(app, [*base, "--layout", "wide"]).output
    assert "--max-track-km needs --track" in runner.invoke(
        app, [*base, "--max-track-km", "10"]
    ).output
