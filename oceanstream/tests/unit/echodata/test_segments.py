"""Tests for oceanstream.echodata.stac.segments.

Tests the pure-logic helper functions: _create_single_segment,
_group_by_day, _group_by_hour, _segments_to_geojson.
extract_segment_coordinates requires a Zarr store (tested with mock).
"""

from __future__ import annotations

import numpy as np
import pytest

from oceanstream.echodata.stac.segments import (
    _create_single_segment,
    _group_by_day,
    _group_by_hour,
    _segments_to_geojson,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def sample_track():
    """Generate a 2-day GPS track as numpy arrays (matching extract_segment_coordinates)."""
    n = 200
    base = np.datetime64("2023-08-13T00:00:00", "ns")
    times = np.array([base + np.timedelta64(i * 15, "m") for i in range(n)])
    lats = np.linspace(37.0, 38.0, n)
    lons = np.linspace(-122.0, -121.0, n)
    return times, lats, lons


# ---------------------------------------------------------------------------
# _create_single_segment
# ---------------------------------------------------------------------------

class TestCreateSingleSegment:
    """Tests for single-segment creation."""

    def test_basic_segment(self):
        times = [np.datetime64("2023-08-13T09:00:00"), np.datetime64("2023-08-13T10:00:00")]
        lats = np.array([37.0, 37.1])
        lons = np.array([-122.0, -121.9])
        seg = _create_single_segment(times, lats, lons, max_points=1000)

        assert "coordinates" in seg
        assert seg["point_count"] == 2
        assert seg["start_lat"] == 37.0
        assert seg["end_lon"] == pytest.approx(-121.9)

    def test_coordinates_are_lon_lat(self):
        """GeoJSON convention: [lon, lat]."""
        times = [np.datetime64("2023-01-01")]
        lats = np.array([37.5])
        lons = np.array([-122.5])
        seg = _create_single_segment(times, lats, lons, max_points=100)
        assert seg["coordinates"][0] == [-122.5, 37.5]

    def test_subsampling(self):
        n = 500
        times = [np.datetime64("2023-01-01") + np.timedelta64(i, "s") for i in range(n)]
        lats = np.linspace(37.0, 38.0, n)
        lons = np.linspace(-122.0, -121.0, n)
        seg = _create_single_segment(times, lats, lons, max_points=100)
        assert seg["point_count"] == 100

    def test_timestamps_in_output(self):
        times = [np.datetime64("2023-08-13T09:00:00"), np.datetime64("2023-08-13T12:00:00")]
        seg = _create_single_segment(times, np.array([37.0, 37.1]), np.array([-122.0, -121.9]), 1000)
        assert "2023-08-13" in seg["start_datetime"]
        assert "2023-08-13" in seg["end_datetime"]


# ---------------------------------------------------------------------------
# _group_by_day
# ---------------------------------------------------------------------------

class TestGroupByDay:
    """Tests for daily segmentation."""

    def test_two_day_track_produces_two_segments(self, sample_track):
        times, lats, lons = sample_track
        segments = _group_by_day(times, lats, lons, max_points=1000)
        # 200 points × 15min = 50h → spans 2-3 days
        assert len(segments) >= 2

    def test_each_segment_has_date(self, sample_track):
        times, lats, lons = sample_track
        segments = _group_by_day(times, lats, lons, max_points=1000)
        for seg in segments:
            assert "date" in seg

    def test_single_day_track(self):
        n = 10
        base = np.datetime64("2023-08-13T09:00:00", "ns")
        times = np.array([base + np.timedelta64(i, "m") for i in range(n)])
        segments = _group_by_day(times, np.zeros(n), np.zeros(n), max_points=1000)
        assert len(segments) == 1


# ---------------------------------------------------------------------------
# _group_by_hour
# ---------------------------------------------------------------------------

class TestGroupByHour:
    """Tests for hourly segmentation."""

    def test_multi_hour_track(self):
        n = 120
        base = np.datetime64("2023-08-13T09:00", "ns")
        times = np.array([base + np.timedelta64(i, "m") for i in range(n)])
        segments = _group_by_hour(times, np.zeros(n), np.zeros(n), max_points=1000)
        # 120 min → 2 hours
        assert len(segments) == 2

    def test_each_segment_has_hour(self):
        n = 60
        base = np.datetime64("2023-08-13T09:00", "ns")
        times = np.array([base + np.timedelta64(i, "m") for i in range(n)])
        segments = _group_by_hour(times, np.zeros(n), np.zeros(n), max_points=1000)
        for seg in segments:
            assert "hour" in seg


# ---------------------------------------------------------------------------
# _segments_to_geojson
# ---------------------------------------------------------------------------

class TestSegmentsToGeojson:
    """Tests for GeoJSON FeatureCollection creation."""

    def test_basic_structure(self):
        segments = [
            {
                "coordinates": [[-122.0, 37.0], [-121.9, 37.1]],
                "start_datetime": "2023-08-13T09:00:00Z",
                "end_datetime": "2023-08-13T10:00:00Z",
                "point_count": 2,
            },
        ]
        geojson = _segments_to_geojson(segments)
        assert geojson["type"] == "FeatureCollection"
        assert len(geojson["features"]) == 1
        feat = geojson["features"][0]
        assert feat["type"] == "Feature"
        assert feat["geometry"]["type"] == "LineString"

    def test_skips_single_point_segments(self):
        segments = [
            {"coordinates": [[-122.0, 37.0]], "a": 1},
            {"coordinates": [[-122.0, 37.0], [-121.9, 37.1]], "b": 2},
        ]
        geojson = _segments_to_geojson(segments)
        assert len(geojson["features"]) == 1

    def test_empty_segments(self):
        geojson = _segments_to_geojson([])
        assert geojson["type"] == "FeatureCollection"
        assert geojson["features"] == []

    def test_properties_preserved(self):
        segments = [
            {
                "coordinates": [[-122, 37], [-121, 38]],
                "date": "2023-08-13",
                "point_count": 2,
            },
        ]
        geojson = _segments_to_geojson(segments)
        props = geojson["features"][0]["properties"]
        assert props["date"] == "2023-08-13"
        assert props["point_count"] == 2
