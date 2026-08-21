"""Unit tests for the NASC coordinate repair and schema normalisation.

Covers the two defects the 3-preset denoise comparison depends on:
per-distance-bin positions collapsing to a single point, and
``frequency_nominal`` arriving as a multi-GiB 3-D array with orphan dimensions.
"""

from __future__ import annotations

import numpy as np
import pytest

xr = pytest.importorskip("xarray")
pd = pytest.importorskip("pandas")

from oceanstream.echodata.compute.nasc import (  # noqa: E402
    _assign_distance_bins,
    _cumulative_distance_nmi,
    normalize_nasc_schema,
    repair_nasc_positions,
    validate_nasc_schema,
)


def _moving_track_source(n_pings: int = 600) -> xr.Dataset:
    """Synthetic Sv on a straight, steadily-moving track."""
    ping_time = pd.date_range("2023-10-10T02:00:00", periods=n_pings, freq="3s")
    lat = np.linspace(0.010, 0.030, n_pings)
    lon = np.linspace(-166.40, -166.10, n_pings)
    depth = np.arange(0, 300, 10, dtype=float)
    sv = np.full((1, n_pings, depth.size), -80.0)
    return xr.Dataset(
        {
            "Sv": (["channel", "ping_time", "depth"], sv),
            "latitude": ("ping_time", lat),
            "longitude": ("ping_time", lon),
            "frequency_nominal": ("channel", [38000.0]),
        },
        coords={"ping_time": ping_time, "depth": depth, "channel": ["ch38"]},
    )


def _collapsed_nasc(source: xr.Dataset, n_bins: int = 5) -> xr.Dataset:
    """A NASC product with the observed defects baked in."""
    distance = np.arange(n_bins, dtype=float) * 0.5
    depth = np.arange(0, 300, 10, dtype=float)
    ping_time = source["ping_time"].values[
        np.linspace(0, source.sizes["ping_time"] - 1, n_bins).astype(int)
    ]
    ds = xr.Dataset(
        {
            "NASC": (
                ["channel", "distance", "depth"],
                np.full((1, n_bins, depth.size), 12.0),
            ),
            # Defect 1: every bin collapsed to one position.
            "latitude": ("distance", np.full(n_bins, 0.0118)),
            "longitude": ("distance", np.full(n_bins, -166.3672)),
            "ping_time": ("distance", ping_time),
            # Defect 2: bogus 3-D frequency_nominal with orphan dims.
            "frequency_nominal": (
                ["channel", "distance_nmi", "range_sample"],
                np.full((1, 7, 11), 38000.0),
            ),
        },
        coords={
            "channel": ["ch38"],
            "distance": distance,
            "depth": depth,
            "distance_nmi": np.linspace(0.001, 2.0, 7),
            "range_sample": np.arange(11),
        },
    )
    ds["NASC"].attrs["units"] = "m2 nmi-2"
    ds["distance"].attrs["units"] = "nmi"
    return ds


class TestDistanceBinAssignment:
    def test_cumulative_distance_is_monotonic(self):
        source = _moving_track_source()
        dist = _cumulative_distance_nmi(
            source["latitude"].values, source["longitude"].values
        )
        assert dist[0] == pytest.approx(0.0, abs=1e-9)
        assert np.all(np.diff(dist) >= 0)
        assert dist[-1] > 1.0

    def test_assignment_covers_every_bin(self):
        dist = np.linspace(0.0, 2.4, 500)
        edges = np.arange(0.0, 2.5, 0.5)
        idx = _assign_distance_bins(dist, edges)
        assert set(np.unique(idx)) == set(range(edges.size))

    def test_out_of_range_and_nan_marked_minus_one(self):
        edges = np.array([0.0, 0.5, 1.0])
        idx = _assign_distance_bins(np.array([-0.1, 0.2, 99.0, np.nan]), edges)
        assert idx.tolist() == [-1, 0, -1, -1]


class TestRepairNascPositions:
    def test_positions_vary_along_a_moving_track(self):
        source = _moving_track_source()
        nasc = _collapsed_nasc(source)

        assert np.unique(nasc["latitude"].values).size == 1  # precondition

        repaired = repair_nasc_positions(nasc, source)

        lat = repaired["latitude"].values
        lon = repaired["longitude"].values
        assert np.unique(lat).size > 1
        assert np.unique(lon).size > 1
        assert np.all(np.isfinite(lat))
        assert np.all(np.isfinite(lon))
        # Positions must stay inside the source track's envelope.
        assert lat.min() >= source["latitude"].values.min() - 1e-9
        assert lat.max() <= source["latitude"].values.max() + 1e-9

    def test_positions_increase_with_distance_on_a_northbound_track(self):
        source = _moving_track_source()
        repaired = repair_nasc_positions(_collapsed_nasc(source), source)
        lat = repaired["latitude"].values
        assert np.all(np.diff(lat) > 0)

    def test_ping_count_per_bin_is_recorded(self):
        source = _moving_track_source()
        repaired = repair_nasc_positions(_collapsed_nasc(source), source)
        assert "ping_count" in repaired
        assert repaired["ping_count"].dims == ("distance",)
        assert int(repaired["ping_count"].sum()) > 0

    def test_stationary_track_keeps_a_single_position(self):
        source = _moving_track_source()
        source["latitude"][:] = 0.02
        source["longitude"][:] = -166.2
        repaired = repair_nasc_positions(_collapsed_nasc(source), source)
        finite = repaired["latitude"].values[np.isfinite(repaired["latitude"].values)]
        assert np.unique(finite).size == 1

    def test_missing_source_positions_is_a_no_op(self):
        source = _moving_track_source().drop_vars(["latitude", "longitude"])
        nasc = _collapsed_nasc(_moving_track_source())
        repaired = repair_nasc_positions(nasc, source)
        assert np.unique(repaired["latitude"].values).size == 1


class TestNormalizeNascSchema:
    def test_frequency_nominal_reduced_to_channel(self):
        source = _moving_track_source()
        normalized = normalize_nasc_schema(_collapsed_nasc(source), source)
        assert tuple(normalized["frequency_nominal"].dims) == ("channel",)
        assert float(normalized["frequency_nominal"].values[0]) == 38000.0

    def test_orphan_dimensions_dropped(self):
        source = _moving_track_source()
        normalized = normalize_nasc_schema(_collapsed_nasc(source), source)
        assert "distance_nmi" not in normalized.dims
        assert "range_sample" not in normalized.dims

    def test_normalization_is_idempotent(self):
        source = _moving_track_source()
        once = normalize_nasc_schema(_collapsed_nasc(source), source)
        twice = normalize_nasc_schema(once, source)
        assert set(twice.dims) == set(once.dims)
        assert tuple(twice["frequency_nominal"].dims) == ("channel",)


class TestValidateNascSchema:
    def _clean(self) -> xr.Dataset:
        source = _moving_track_source()
        ds = repair_nasc_positions(_collapsed_nasc(source), source)
        return normalize_nasc_schema(ds, source)

    def test_clean_product_passes(self):
        assert validate_nasc_schema(self._clean()) == []

    def test_constant_positions_are_reported(self):
        ds = self._clean()
        ds["latitude"][:] = 0.02
        problems = validate_nasc_schema(ds)
        assert any("constant" in p for p in problems)

    def test_non_monotonic_distance_is_reported(self):
        ds = self._clean()
        ds = ds.assign_coords(distance=[0.0, 1.0, 0.5, 1.5, 2.0])
        ds["distance"].attrs["units"] = "nmi"
        problems = validate_nasc_schema(ds)
        assert any("not strictly increasing" in p for p in problems)

    def test_orphan_dimension_is_reported(self):
        source = _moving_track_source()
        ds = repair_nasc_positions(_collapsed_nasc(source), source)
        problems = validate_nasc_schema(ds)
        assert any("orphan dimensions" in p for p in problems)

    def test_strict_mode_raises(self):
        ds = self._clean()
        ds["latitude"][:] = 0.02
        with pytest.raises(ValueError, match="Invalid NASC schema"):
            validate_nasc_schema(ds, strict=True)

    def test_missing_units_is_reported(self):
        ds = self._clean()
        ds["NASC"].attrs.pop("units", None)
        assert any("units" in p for p in validate_nasc_schema(ds))
