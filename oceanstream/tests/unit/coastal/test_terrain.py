"""Tests for the Phase 1.4 terrain classification.

``terrain_classes`` is the library's only optics-independent substrate
discriminator, which makes two of its properties load-bearing: the slope must
be a true slope in degrees (so it has to scale with pixel size), and the two
classes must not partition the scene (an ambiguous pixel belongs to neither).
"""

from __future__ import annotations

import numpy as np
import pytest

from oceanstream.coastal.bathymetry.terrain import terrain_classes
from oceanstream.coastal.config import BathymetryConfig

SHAPE = (30, 30)


def _flat_seabed(depth_m: float = 10.0) -> np.ndarray:
    return np.full(SHAPE, depth_m, dtype=np.float64)


def _sloped_seabed(slope_deg: float, pixel_size_m: float = 10.0) -> np.ndarray:
    """A planar seabed dipping at a known angle along the x axis."""
    rise = np.tan(np.radians(slope_deg)) * pixel_size_m
    return np.arange(SHAPE[1], dtype=np.float64)[None, :] * rise + np.zeros(SHAPE)


class TestSlope:
    def test_a_flat_seabed_has_zero_slope(self) -> None:
        out = terrain_classes(_flat_seabed())
        assert np.allclose(out["slope_deg"], 0.0)
        assert np.allclose(out["relief_m"], 0.0)

    @pytest.mark.parametrize("slope_deg", [1.0, 3.5, 10.0, 30.0])
    def test_a_known_gradient_returns_that_slope(self, slope_deg: float) -> None:
        depth = _sloped_seabed(slope_deg)
        out = terrain_classes(depth, pixel_size_m=10.0)
        # Interior pixels only — np.gradient uses one-sided differences at the
        # edges, which is fine but not what the analytic value describes.
        interior = out["slope_deg"][1:-1, 1:-1]
        assert np.allclose(interior, slope_deg, atol=1e-6)

    def test_slope_scales_with_pixel_size(self) -> None:
        depth = _sloped_seabed(10.0, pixel_size_m=10.0)
        coarse = terrain_classes(depth, pixel_size_m=10.0)["slope_deg"]
        fine = terrain_classes(depth, pixel_size_m=5.0)["slope_deg"]
        # Halving the ground distance for the same rise doubles the gradient.
        assert fine[15, 15] > coarse[15, 15]


class TestClassAssignment:
    def test_flat_seabed_is_flat_and_not_rugose(self) -> None:
        out = terrain_classes(_flat_seabed())
        assert out["flat"].all()
        assert not out["rugose"].any()

    def test_steep_rough_seabed_is_rugose_and_not_flat(self) -> None:
        rng = np.random.default_rng(0)
        depth = _sloped_seabed(20.0) + rng.normal(0.0, 2.0, SHAPE)
        out = terrain_classes(depth)
        interior = (slice(1, -1), slice(1, -1))
        assert out["rugose"][interior].mean() > 0.95
        assert not out["flat"][interior].any()

    def test_the_classes_do_not_partition_the_scene(self) -> None:
        # 3.5 deg sits in the gap between flat (<2) and rugose (>5).
        out = terrain_classes(_sloped_seabed(3.5))
        interior = (slice(1, -1), slice(1, -1))
        assert not out["flat"][interior].any()
        assert not out["rugose"][interior].any()

    def test_classes_are_mutually_exclusive_everywhere(self) -> None:
        rng = np.random.default_rng(1)
        depth = _sloped_seabed(4.0) + rng.normal(0.0, 1.0, SHAPE)
        out = terrain_classes(depth)
        assert not (out["flat"] & out["rugose"]).any()


class TestConfigurability:
    def test_relaxing_the_flat_bound_admits_a_gentle_slope(self) -> None:
        depth = _sloped_seabed(3.5)
        strict = terrain_classes(depth)
        relaxed = terrain_classes(
            depth,
            config=BathymetryConfig(flat_max_slope_deg=5.0, flat_max_relief_m=5.0),
        )
        interior = (slice(1, -1), slice(1, -1))
        assert not strict["flat"][interior].any()
        assert relaxed["flat"][interior].all()

    def test_a_wider_relief_window_sees_more_relief(self) -> None:
        rng = np.random.default_rng(2)
        depth = _sloped_seabed(1.0) + rng.normal(0.0, 0.5, SHAPE)
        narrow = terrain_classes(depth, config=BathymetryConfig(relief_window_px=3))["relief_m"]
        wide = terrain_classes(depth, config=BathymetryConfig(relief_window_px=9))["relief_m"]
        assert wide.mean() > narrow.mean()


class TestMissingData:
    def test_non_finite_depths_are_excluded_from_both_classes(self) -> None:
        # Regression trap. np.gradient's central difference skips the centre
        # pixel and scipy's min/max filters do not propagate NaN, so without
        # an explicit check a lone hole in flat bathymetry is classified flat.
        depth = _flat_seabed()
        depth[10, 10] = np.nan
        out = terrain_classes(depth)
        assert not out["flat"][10, 10]
        assert not out["rugose"][10, 10]
        assert out["flat"][20, 20]  # the rest of the scene is unaffected

    def test_an_all_nan_scene_yields_no_classified_pixels(self) -> None:
        depth = np.full(SHAPE, np.nan)
        out = terrain_classes(depth)
        assert not out["flat"].any()
        assert not out["rugose"].any()

    def test_continuous_rasters_are_returned_for_retuning(self) -> None:
        out = terrain_classes(_sloped_seabed(3.5))
        assert set(out) == {"slope_deg", "relief_m", "flat", "rugose"}
        assert out["slope_deg"].shape == SHAPE
        assert out["relief_m"].shape == SHAPE
