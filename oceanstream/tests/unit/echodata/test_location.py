"""Tests for oceanstream.echodata.consolidate.location.

Tests the pure-logic functions: merge_location_data, interpolate_location_from_dataframe,
ramer_douglas_peucker, and extract_start_end_lat_lon. No echopype dependency.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
import xarray as xr

from oceanstream.echodata.consolidate.location import (
    extract_start_end_lat_lon,
    interpolate_location_from_dataframe,
    merge_location_data,
    ramer_douglas_peucker,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def sv_dataset():
    """Minimal Sv-like Dataset with ping_time."""
    n = 10
    return xr.Dataset(
        {"Sv": (["ping_time", "range_sample"], np.zeros((n, 5)))},
        coords={
            "ping_time": pd.date_range("2023-08-13T09:00:00", periods=n, freq="10s"),
            "range_sample": np.arange(5),
        },
    )


@pytest.fixture()
def location_df():
    """GPS DataFrame matching the sv_dataset time range."""
    n = 10
    return pd.DataFrame({
        "lat": np.linspace(37.0, 37.1, n),
        "lon": np.linspace(-122.0, -121.9, n),
        "dt": pd.date_range("2023-08-13T09:00:00", periods=n, freq="10s"),
        "knt": np.full(n, 5.0),
    })


# ---------------------------------------------------------------------------
# merge_location_data
# ---------------------------------------------------------------------------

class TestMergeLocationData:
    """Tests for merging GPS data into Sv datasets."""

    def test_adds_location_vars(self, sv_dataset, location_df):
        result = merge_location_data(sv_dataset, location_df)
        assert "latitude" in result
        assert "longitude" in result
        assert "speed_knots" in result

    def test_dict_input(self, sv_dataset):
        loc = [
            {"lat": 37.0, "lon": -122.0, "dt": "2023-08-13T09:00:00", "knt": 5.0},
            {"lat": 37.1, "lon": -121.9, "dt": "2023-08-13T09:01:30", "knt": 5.0},
        ]
        result = merge_location_data(sv_dataset, loc)
        assert "latitude" in result

    def test_overwrites_existing_location(self, sv_dataset, location_df):
        # Add pre-existing vars that should be replaced
        sv_dataset["latitude"] = ("ping_time", np.zeros(10))
        sv_dataset["longitude"] = ("ping_time", np.zeros(10))
        result = merge_location_data(sv_dataset, location_df)
        # New values should not all be zero
        assert not np.allclose(result["latitude"].values, 0.0)

    def test_preserves_sv_data(self, sv_dataset, location_df):
        result = merge_location_data(sv_dataset, location_df)
        np.testing.assert_array_equal(result["Sv"].values, sv_dataset["Sv"].values)


# ---------------------------------------------------------------------------
# interpolate_location_from_dataframe
# ---------------------------------------------------------------------------

class TestInterpolateLocationFromDataframe:
    """Tests for linear location interpolation."""

    def test_adds_lat_lon_coords(self, sv_dataset, location_df):
        result = interpolate_location_from_dataframe(sv_dataset, location_df)
        assert "latitude" in result.coords
        assert "longitude" in result.coords

    def test_interpolated_values_in_range(self, sv_dataset, location_df):
        result = interpolate_location_from_dataframe(sv_dataset, location_df)
        lats = result["latitude"].values
        assert np.all(lats >= 36.9)
        assert np.all(lats <= 37.2)

    def test_exact_match_at_endpoints(self, sv_dataset, location_df):
        result = interpolate_location_from_dataframe(sv_dataset, location_df)
        np.testing.assert_allclose(result["latitude"].values[0], 37.0, atol=0.01)


# ---------------------------------------------------------------------------
# ramer_douglas_peucker
# ---------------------------------------------------------------------------

class TestRamerDouglasPeucker:
    """Tests for polyline simplification."""

    def test_straight_line_simplified(self):
        """Collinear points should be reduced to endpoints."""
        points = np.array([[0, 0], [1, 0], [2, 0], [3, 0], [4, 0]], dtype=float)
        result = ramer_douglas_peucker(points, epsilon=0.1)
        assert len(result) == 2  # just start and end

    def test_preserves_sharp_corner(self):
        """A sharp bend should be preserved."""
        points = np.array([[0, 0], [1, 0], [1, 1]], dtype=float)
        result = ramer_douglas_peucker(points, epsilon=0.01)
        assert len(result) == 3  # corner preserved

    def test_small_input_returned_as_is(self):
        points = np.array([[0, 0], [1, 1]], dtype=float)
        result = ramer_douglas_peucker(points, epsilon=0.1)
        assert len(result) == 2

    def test_single_point(self):
        points = np.array([[0, 0]], dtype=float)
        result = ramer_douglas_peucker(points, epsilon=0.1)
        assert len(result) == 1

    def test_large_epsilon_reduces_more(self):
        """Big tolerance → more aggressive simplification."""
        # L-shaped path
        points = np.array(
            [[0, 0], [0.5, 0.01], [1, 0], [1, 0.5], [1, 1]], dtype=float
        )
        tight = ramer_douglas_peucker(points, epsilon=0.001)
        loose = ramer_douglas_peucker(points, epsilon=1.0)
        assert len(loose) <= len(tight)


# ---------------------------------------------------------------------------
# extract_start_end_lat_lon
# ---------------------------------------------------------------------------

class TestExtractStartEndLatLon:
    """Tests for start/end coordinate extraction."""

    def test_returns_four_keys(self):
        ds = xr.Dataset(
            {
                "latitude": ("ping_time", [37.0, 37.1, 37.2]),
                "longitude": ("ping_time", [-122.0, -121.9, -121.8]),
            },
            coords={"ping_time": pd.date_range("2023-01-01", periods=3, freq="h")},
        )
        result = extract_start_end_lat_lon(ds)
        assert set(result.keys()) == {
            "file_start_lat", "file_end_lat",
            "file_start_lon", "file_end_lon",
        }

    def test_correct_values(self):
        ds = xr.Dataset(
            {
                "latitude": ("ping_time", [10.0, 20.0, 30.0]),
                "longitude": ("ping_time", [100.0, 110.0, 120.0]),
            },
            coords={"ping_time": np.arange(3)},
        )
        result = extract_start_end_lat_lon(ds)
        assert result["file_start_lat"] == 10.0
        assert result["file_end_lat"] == 30.0
        assert result["file_start_lon"] == 100.0

    def test_skips_nan_at_edges(self):
        ds = xr.Dataset(
            {
                "latitude": ("ping_time", [np.nan, 37.0, 37.1, np.nan]),
                "longitude": ("ping_time", [np.nan, -122.0, -121.9, np.nan]),
            },
            coords={"ping_time": np.arange(4)},
        )
        result = extract_start_end_lat_lon(ds)
        assert result["file_start_lat"] == 37.0
        assert result["file_end_lat"] == 37.1

    def test_all_nan_returns_empty(self):
        ds = xr.Dataset(
            {
                "latitude": ("ping_time", [np.nan, np.nan]),
                "longitude": ("ping_time", [np.nan, np.nan]),
            },
            coords={"ping_time": np.arange(2)},
        )
        result = extract_start_end_lat_lon(ds)
        assert result == {}

    def test_missing_vars_returns_empty(self):
        ds = xr.Dataset(
            {"Sv": ("ping_time", [1, 2, 3])},
            coords={"ping_time": np.arange(3)},
        )
        result = extract_start_end_lat_lon(ds)
        assert result == {}

    def test_missing_ping_time_returns_empty(self):
        ds = xr.Dataset({
            "latitude": ("x", [37.0]),
            "longitude": ("x", [-122.0]),
        })
        result = extract_start_end_lat_lon(ds)
        assert result == {}
