"""Tests for the Phase 1.3 masks port.

The prototype shipped no mask tests. These pin the two things that are easy
to get subtly wrong in a rewrite: the polarity of every mask (True means
*keep*, and it is inverted in three of the five) and the fact that the
adjacency buffer grows land into water rather than the other way round.
"""

from __future__ import annotations

import numpy as np
import pytest

from oceanstream.coastal import masks
from oceanstream.coastal.config import MaskConfig

SHAPE = (20, 20)


@pytest.fixture
def dark_water() -> dict[str, np.ndarray]:
    """A uniformly dark, glint-free, cloud-free water scene."""
    return {
        "blue": np.full(SHAPE, 0.02, dtype=np.float32),
        "red": np.full(SHAPE, 0.008, dtype=np.float32),
        "nir": np.full(SHAPE, 0.004, dtype=np.float32),
        "swir": np.full(SHAPE, 0.001, dtype=np.float32),
    }


class TestIndividualMasks:
    def test_land_is_rejected_and_water_kept(self) -> None:
        nir = np.array([[0.004, 0.30]], dtype=np.float32)
        assert masks.land_mask(nir).tolist() == [[True, False]]

    def test_cloud_needs_both_blue_and_nir(self) -> None:
        # bright blue only = water; bright NIR only = land; both = cloud
        blue = np.array([[0.40, 0.02, 0.40]], dtype=np.float32)
        nir = np.array([[0.004, 0.40, 0.40]], dtype=np.float32)
        assert masks.cloud_mask(blue, nir).tolist() == [[True, True, False]]

    def test_glint_uses_nir_alone_without_swir(self) -> None:
        nir = np.array([[0.004, 0.09]], dtype=np.float32)
        assert masks.glint_mask(nir, None).tolist() == [[True, False]]

    def test_swir_tightens_the_glint_mask(self) -> None:
        nir = np.full((1, 2), 0.004, dtype=np.float32)
        swir = np.array([[0.001, 0.09]], dtype=np.float32)
        assert masks.glint_mask(nir, swir).tolist() == [[True, False]]

    def test_foam_needs_bright_and_flat(self) -> None:
        # column 0: dark water. column 1: bright but steep (land-like).
        # column 2: bright and flat (foam).
        rgb = np.array(
            [
                [[0.02, 0.20, 0.30]],
                [[0.01, 0.05, 0.30]],
                [[0.004, 0.60, 0.32]],
            ],
            dtype=np.float32,
        ).reshape(3, 1, 3)
        assert masks.foam_mask(rgb).tolist() == [[True, True, False]]

    def test_adjacency_buffer_eats_water_next_to_land(self) -> None:
        land = np.ones((1, 7), dtype=bool)  # True = water
        land[0, 3] = False  # one land pixel
        buffered = masks.adjacency_buffer(land, buffer_px=2)
        assert buffered.tolist() == [[True, False, False, False, False, False, True]]

    def test_zero_buffer_is_a_no_op(self) -> None:
        land = np.ones((1, 5), dtype=bool)
        land[0, 2] = False
        assert masks.adjacency_buffer(land, buffer_px=0).tolist() == land.tolist()


class TestCompositeMask:
    def test_clean_water_survives_every_mask(self, dark_water: dict) -> None:
        composite, components = masks.composite_mask(**dark_water)
        assert composite.all()
        assert set(components) == {"land", "cloud", "glint", "foam", "adjacency"}

    def test_components_stay_inspectable(self, dark_water: dict) -> None:
        dark_water["nir"] = dark_water["nir"].copy()
        dark_water["nir"][5, 5] = 0.50  # a land pixel
        composite, components = masks.composite_mask(**dark_water)

        assert not composite[5, 5]
        assert not components["land"][5, 5]
        # The adjacency buffer must have grown that single pixel outwards.
        assert not components["adjacency"][5, 5 + MaskConfig().adjacency_buffer_px]
        # ... but the failure is attributable: cloud and foam are untouched.
        assert components["cloud"][5, 5]
        assert components["foam"].all()

    def test_swir_is_optional(self, dark_water: dict) -> None:
        dark_water.pop("swir")
        composite, _ = masks.composite_mask(**dark_water)
        assert composite.all()

    def test_config_thresholds_are_honoured(self, dark_water: dict) -> None:
        strict = MaskConfig(land_nir_max=0.001, adjacency_buffer_px=0)
        composite, _ = masks.composite_mask(**dark_water, config=strict)
        assert not composite.any()


class TestDeepWaterPixels:
    def test_depth_gate_alone(self) -> None:
        valid = np.ones((1, 3), dtype=bool)
        depth = np.array([[10.0, 35.0, 100.0]])
        deep = masks.deep_water_pixels(valid, depth)
        assert deep.tolist() == [[False, True, True]]

    def test_optical_depth_gate_is_independent(self) -> None:
        valid = np.ones((1, 2), dtype=bool)
        depth = np.array([[100.0, 100.0]])
        # 100 m * 0.02 = 2 optical depths — deep in metres, not optically.
        kd = np.array([[0.02, 0.20]])
        deep = masks.deep_water_pixels(valid, depth, kd)
        assert deep.tolist() == [[False, True]]

    def test_composite_mask_is_respected(self) -> None:
        valid = np.array([[False, True]])
        depth = np.array([[100.0, 100.0]])
        assert masks.deep_water_pixels(valid, depth).tolist() == [[False, True]]

    def test_subsampling_caps_the_sample(self) -> None:
        valid = np.ones((100, 100), dtype=bool)
        depth = np.full((100, 100), 100.0)
        cfg = MaskConfig(deepwater_max_samples=500)
        deep = masks.deep_water_pixels(valid, depth, config=cfg)
        assert int(deep.sum()) == 500

    def test_subsampling_is_reproducible(self) -> None:
        valid = np.ones((100, 100), dtype=bool)
        depth = np.full((100, 100), 100.0)
        cfg = MaskConfig(deepwater_max_samples=500)
        first = masks.deep_water_pixels(valid, depth, config=cfg)
        second = masks.deep_water_pixels(valid, depth, config=cfg)
        assert np.array_equal(first, second)

    def test_a_different_seed_draws_a_different_sample(self) -> None:
        valid = np.ones((100, 100), dtype=bool)
        depth = np.full((100, 100), 100.0)
        first = masks.deep_water_pixels(
            valid, depth, config=MaskConfig(deepwater_max_samples=500, rng_seed=1)
        )
        second = masks.deep_water_pixels(
            valid, depth, config=MaskConfig(deepwater_max_samples=500, rng_seed=2)
        )
        assert not np.array_equal(first, second)
