"""Water columns from the OceanStream globe (environment/globe.py) and their use in Sv."""

from __future__ import annotations

import io
import json
import os
import sys
import urllib.error
from contextlib import contextmanager
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import xarray as xr

from oceanstream.echodata.environment import globe
from oceanstream.echodata.environment.ctd_casts import load_cchdo_ctd

SOURCES = {
    "models": [
        {"model": "anfc", "coverage": ["2022-06-01", "2026-11-30"]},
        {"model": "glorys", "coverage": ["1993-01-01", "2026-10-06"]},
    ]
}

#: Hours in the default track of :func:`fixes`, one water column each.
HOURS = 3
#: Pressure (dbar) above which the test casts are warm.
MIXED_DBAR = 100


def fixes(start="2023-10-09T00:00", minutes=180, lat=0.03, lon=-165.98):
    times = pd.date_range(start, periods=minutes, freq="1min")
    return pd.DataFrame(
        {"dt": times, "lat": lat + np.arange(minutes) * 1e-4, "lon": np.full(minutes, lon)}
    )


def casts_file(points, kinds=None) -> bytes:
    """A casts file laid out as the globe's ``format=cchdo``: warm above 100 dbar, 5 °C below."""
    n, pressure = len(points), np.arange(0.0, 1500.0, 10.0)
    temp = np.where(pressure < MIXED_DBAR, 28.0, 5.0)
    ds = xr.Dataset(
        {
            "pressure": (("N_PROF", "N_LEVELS"), np.tile(pressure, (n, 1))),
            "ctd_temperature": (("N_PROF", "N_LEVELS"), np.tile(temp, (n, 1))),
            "ctd_temperature_qc": (("N_PROF", "N_LEVELS"), np.full((n, pressure.size), 2, np.int8)),
            "ctd_salinity": (("N_PROF", "N_LEVELS"), np.full((n, pressure.size), 35.0)),
            "ctd_salinity_qc": (("N_PROF", "N_LEVELS"), np.full((n, pressure.size), 2, np.int8)),
            "latitude": (("N_PROF",), [p["lat"] for p in points]),
            "longitude": (("N_PROF",), [p["lon"] for p in points]),
            "time": (("N_PROF",), pd.to_datetime([p["time"] for p in points]).tz_localize(None)),
            "btm_depth": (("N_PROF",), np.full(n, 5000.0)),
        },
        attrs={"source": "CMEMS GLOBAL_MULTIYEAR_PHY_001_030 (202311)"},
    )
    if kinds is not None:
        ds["cast_source"] = (("N_PROF",), np.array(kinds, np.int8))
    return ds.to_netcdf()


class FakeGlobe:
    """Serves /api/env/sources and /api/env/track?format=cchdo, recording what it was asked."""

    def __init__(self, kinds=None, refuse=0):
        self.requests, self.kinds, self.refuse = [], kinds, refuse

    def __call__(self, req, timeout=None):
        self.requests.append(req)
        if self.refuse:
            self.refuse -= 1
            raise urllib.error.HTTPError(
                req.full_url, 429, "busy", {"Retry-After": "1"}, io.BytesIO(b"slow down")
            )
        if req.full_url.endswith("/api/env/sources"):
            return io.BytesIO(json.dumps(SOURCES).encode())
        body = json.loads(req.data)
        kinds = self.kinds(len(body["points"])) if self.kinds else None
        return io.BytesIO(casts_file(body["points"], kinds))


@pytest.fixture
def fake(monkeypatch):
    monkeypatch.delenv(globe.KEY_ENV, raising=False)
    monkeypatch.delenv(globe.URL_ENV, raising=False)
    server = FakeGlobe(kinds=lambda n: [1] * (n - 1) + [2])
    monkeypatch.setattr(globe.urllib.request, "urlopen", server)
    return server


class TestHourlyPoints:
    def test_one_mean_per_hour_at_its_middle(self):
        points = globe.hourly_points(fixes(minutes=150))
        assert [p["time"] for p in points] == [
            "2023-10-09T00:30:00Z",
            "2023-10-09T01:30:00Z",
            "2023-10-09T02:30:00Z",
        ]
        assert points[0]["lat"] == pytest.approx(0.03 + 29.5e-4)

    def test_longitude_mean_across_the_antimeridian(self):
        track = fixes(minutes=60, lon=179.99)
        track.loc[track.index % 2 == 1, "lon"] = -179.99
        (point,) = globe.hourly_points(track)
        assert abs(point["lon"]) == pytest.approx(180.0, abs=1e-6)

    def test_failed_fixes_are_dropped(self):
        track = fixes(minutes=60)
        track.loc[:9, ["lat", "lon"]] = 0.0
        (point,) = globe.hourly_points(track)
        assert point["lat"] == pytest.approx(0.03 + 34.5e-4)
        assert globe.hourly_points(track.iloc[:10]) == []


class TestTrackCasts:
    def test_asks_for_the_reanalysis_with_insitu_and_reads_back(self, fake, tmp_path, monkeypatch):
        monkeypatch.setenv(globe.KEY_ENV, "secret-key")
        casts = globe.track_casts(fixes(), tmp_path / "casts.nc", globe.GlobeSettings())
        sources, track = fake.requests
        assert sources.full_url == "https://globe.oceanstream.io/api/env/sources"
        assert track.full_url == "https://globe.oceanstream.io/api/env/track?format=cchdo"
        assert track.get_header("X-api-key") == "secret-key"
        body = json.loads(track.data)
        assert body["model"] == "glorys" and body["insitu"] == {} and len(body["points"]) == HOURS
        assert casts.kinds == {
            "model": 0,
            "model corrected by nearby profiles": 2,
            "measured profile": 1,
        }
        note = casts.describe("https://globe.oceanstream.io")
        assert "3 water columns" in note and "GLOBAL_MULTIYEAR_PHY_001_030" in note
        assert "secret-key" not in note
        # The file is one the CTD path reads.
        profiles = load_cchdo_ctd(casts.path, [38000.0], max_depth_m=500)
        assert profiles.time.size == HOURS

    def test_after_the_reanalysis_ends_the_analysis(self, fake, tmp_path):
        casts = globe.track_casts(
            fixes(start="2026-11-01T00:00"), tmp_path / "c.nc", globe.GlobeSettings()
        )
        assert casts.model == "anfc"
        assert json.loads(fake.requests[-1].data)["model"] == "anfc"

    def test_a_model_asked_for_and_no_insitu(self, fake, tmp_path):
        settings = globe.GlobeSettings(url="http://globe.local/", model="anfc", insitu=False)
        globe.track_casts(fixes(), tmp_path / "c.nc", settings)
        (track,) = fake.requests
        assert track.full_url == "http://globe.local/api/env/track?format=cchdo"
        assert "insitu" not in json.loads(track.data)
        assert track.get_header("X-api-key") is None

    def test_model_only_file_counts_as_model(self, monkeypatch, tmp_path):
        monkeypatch.setattr(globe.urllib.request, "urlopen", FakeGlobe())
        casts = globe.track_casts(fixes(), tmp_path / "c.nc", globe.GlobeSettings(model="glorys"))
        assert casts.kinds["model"] == HOURS

    def test_no_positions(self, fake, tmp_path):
        with pytest.raises(globe.GlobeError, match="no GPS positions"):
            globe.track_casts(fixes().iloc[:0], tmp_path / "c.nc", globe.GlobeSettings())

    def test_unknown_model(self):
        with pytest.raises(ValueError, match="globe model"):
            globe.GlobeSettings(model="hycom")


class TestRequest:
    def test_retries_what_is_refused_for_the_moment(self, monkeypatch, tmp_path):
        server = FakeGlobe(refuse=2)
        monkeypatch.setattr(globe.urllib.request, "urlopen", server)
        waits = []
        monkeypatch.setattr(globe.time, "sleep", waits.append)
        globe.track_casts(fixes(), tmp_path / "c.nc", globe.GlobeSettings(model="glorys"))
        assert waits == [1.0, 1.0] and len(server.requests) == 3  # noqa: PLR2004 - two refusals, then the answer

    def test_other_errors_are_reported(self, monkeypatch):
        def refuse(req, timeout=None):
            raise urllib.error.HTTPError(req.full_url, 404, "nf", {}, io.BytesIO(b"no water"))

        monkeypatch.setattr(globe.urllib.request, "urlopen", refuse)
        req = globe.urllib.request.Request("https://globe.oceanstream.io/api/env/sources")
        with pytest.raises(globe.GlobeError, match="HTTP 404: no water"):
            globe._request(req)


# The pipeline: _globe_casts asks about the EchoData's own stretch of the track.

_SCRIPTS = Path(__file__).resolve().parents[4] / "scripts" / "batch_processing"


@contextmanager
def scripts_importable():
    env, path, shadowed = dict(os.environ), list(sys.path), sys.modules.pop("config", None)
    sys.path.insert(0, str(_SCRIPTS))
    try:
        yield
    finally:
        sys.path[:] = path
        sys.modules.pop("config", None)
        if shadowed is not None:
            sys.modules["config"] = shadowed
        os.environ.clear()
        os.environ.update(env)


class TestPipeline:
    @pytest.fixture
    def pfr(self):
        pytest.importorskip("echopype")
        with scripts_importable():
            import process_from_raw  # noqa: PLC0415

            yield process_from_raw

    def test_track_around_the_pings(self, pfr, fake, tmp_path):
        cfg = pfr.PipelineConfig()
        cfg.raw.water_from_globe = True
        pings = pd.date_range("2023-10-09T10:00", "2023-10-09T11:30", freq="10s")
        ed = {"Sonar/Beam_group1": xr.Dataset(coords={"ping_time": pings})}
        day = fixes(start="2023-10-09T00:00", minutes=24 * 60)
        path, note = pfr._globe_casts(cfg, ed, str(tmp_path / "D20231009-T100000.zarr"), day)
        assert path == str(tmp_path / "D20231009-T100000--globe-casts.nc")
        hours = [p["time"][11:13] for p in json.loads(fake.requests[-1].data)["points"]]
        assert hours == ["09", "10", "11", "12"]
        assert note.startswith("Sound speed and absorption from 4 water columns")

    def test_nmea_when_there_is_no_gps_source(self, pfr, fake, tmp_path, monkeypatch):
        cfg = pfr.PipelineConfig()
        cfg.raw.water_from_globe = True
        pings = pd.date_range("2023-10-09T10:00", periods=10, freq="1min")
        ed = {"Sonar/Beam_group1": xr.Dataset(coords={"ping_time": pings})}
        monkeypatch.setattr(
            pfr, "_platform_gps", lambda ed: fixes(start="2023-10-09T10:00", minutes=10)
        )
        pfr._globe_casts(cfg, ed, str(tmp_path / "x.zarr"), None)
        assert len(json.loads(fake.requests[-1].data)["points"]) == 1
