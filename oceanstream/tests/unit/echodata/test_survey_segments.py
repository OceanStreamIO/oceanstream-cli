"""Tests for station/transit segmentation from navigation."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from oceanstream.echodata.survey_segments import (
    STATION,
    TRANSIT,
    TRANSITION,
    SegmentRules,
    assign_segments,
    build_segments,
    sun_elevation_deg,
)


def _nav(speeds_by_minute: list[float], start: str = "2022-06-27T00:00:00") -> pd.DataFrame:
    times = pd.date_range(start, periods=len(speeds_by_minute), freq="1min")
    return pd.DataFrame({
        "time": times,
        "latitude": np.linspace(30.0, 30.5, len(times)),
        "longitude": np.full(len(times), -140.0),
        "speed_mps": speeds_by_minute,
    })


def test_transit_station_transit():
    speeds = [5.0] * 60 + [0.1] * 120 + [5.0] * 60
    seg = build_segments(_nav(speeds))
    assert seg["kind"].tolist() == [TRANSIT, STATION, TRANSIT]
    assert seg["minutes"].tolist()[1] == pytest.approx(120, abs=6)


def test_short_blip_on_station_is_absorbed():
    speeds = [0.1] * 60 + [3.0] * 2 + [0.1] * 60
    seg = build_segments(_nav(speeds))
    assert seg["kind"].tolist() == [STATION]


def test_short_runs_become_transitions():
    speeds = [5.0] * 60 + [0.1] * 10 + [5.0] * 60
    kinds = build_segments(_nav(speeds))["kind"].tolist()
    assert STATION not in kinds
    assert kinds == [TRANSIT, TRANSITION, TRANSIT]


def test_navigation_gap_splits_a_segment():
    nav = _nav([5.0] * 120)
    nav.loc[60:, "time"] += pd.Timedelta(minutes=30)
    seg = build_segments(nav, SegmentRules(max_gap_minutes=10))
    assert seg["kind"].tolist() == [TRANSIT, TRANSIT]
    assert seg["end"].iloc[0] < seg["start"].iloc[1]


def test_events_label_stations():
    nav = _nav([5.0] * 60 + [0.1] * 120 + [5.0] * 60)
    events = pd.DataFrame({
        "time": pd.to_datetime(["2022-06-27T01:30:00", "2022-06-27T02:10:00"]),
        "station": ["12", "12"],
        "cast": ["1", "2"],
    })
    seg = build_segments(nav, events=events)
    station = seg[seg["kind"] == STATION].iloc[0]
    assert (station["stations"], station["casts"]) == ("12", 2)


def test_every_ping_between_contiguous_segments_is_assigned():
    seg = build_segments(_nav([5.0] * 60 + [0.1] * 120 + [5.0] * 60), prefix="RR2205-")
    pings = pd.date_range("2022-06-27T00:00:00", "2022-06-27T03:59:00", freq="8s").to_numpy()
    ids = assign_segments(pings, seg)
    assert (ids != "").all()
    assert set(ids) == set(seg["segment_id"])


NEAR_ZENITH_DEG = 85


def test_sun_elevation():
    # Noon and midnight, local solar time, on the equator at the March equinox.
    times = pd.DatetimeIndex(["2023-03-20T12:00:00", "2023-03-20T00:00:00"])
    elevation = sun_elevation_deg(times, np.array([0.0, 0.0]), np.array([0.0, 0.0]))
    assert elevation[0] > NEAR_ZENITH_DEG
    assert elevation[1] < -NEAR_ZENITH_DEG
