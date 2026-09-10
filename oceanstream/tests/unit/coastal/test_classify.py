"""Tests for the Phase 1.3 unsupervised bottom classification port.

The scientifically load-bearing assertions here are the two that keep the
output honest: labels stay ``spectral_class_N`` and never acquire a habitat
name, and the brightness ordering is stable so a class index means the same
thing across scenes and sensors.
"""

from __future__ import annotations

import numpy as np
import pytest

from oceanstream.coastal import classify

SHAPE = (40, 40)
WAVELENGTHS = np.array([444.0, 489.0, 561.0, 667.0, 865.0, 1612.0])


@pytest.fixture
def three_substrate_scene() -> tuple[np.ndarray, np.ndarray]:
    """rho_b with three well-separated brightness levels plus noise.

    The NIR and SWIR bands are filled with NaN, which is what a real Lee
    inversion returns there — water is opaque, so no benthic signal survives.
    A classifier that includes them will reject the whole scene.
    """
    rng = np.random.default_rng(0)
    rho_b = np.full((WAVELENGTHS.size, *SHAPE), np.nan, dtype=np.float32)

    thirds = np.array_split(np.arange(SHAPE[0]), 3)
    for level, rows in zip((0.03, 0.12, 0.35), thirds, strict=True):
        for band in range(4):  # visible + red only
            rho_b[band, rows, :] = level + rng.normal(0.0, 0.002, (rows.size, SHAPE[1]))

    return rho_b, np.ones(SHAPE, dtype=bool)


class TestClustering:
    def test_separates_the_three_substrates(
        self, three_substrate_scene: tuple[np.ndarray, np.ndarray]
    ) -> None:
        rho_b, valid = three_substrate_scene
        result = classify.classify_bottom(rho_b, valid, WAVELENGTHS, n_clusters=3)

        assert result.labels.shape == SHAPE
        assert set(np.unique(result.labels)) == {0, 1, 2}
        # Each synthetic band occupies a third of the scene.
        assert (
            result.n_pixels_per_cluster.tolist()
            == [pytest.approx(SHAPE[0] * SHAPE[1] // 3, abs=SHAPE[1])] * 3
        )

    def test_clusters_are_ordered_by_brightness(
        self, three_substrate_scene: tuple[np.ndarray, np.ndarray]
    ) -> None:
        rho_b, valid = three_substrate_scene
        result = classify.classify_bottom(rho_b, valid, WAVELENGTHS, n_clusters=3)
        brightness = result.centroid_spectra.sum(axis=1)
        assert np.all(np.diff(brightness) > 0)

    def test_result_is_reproducible(
        self, three_substrate_scene: tuple[np.ndarray, np.ndarray]
    ) -> None:
        rho_b, valid = three_substrate_scene
        first = classify.classify_bottom(rho_b, valid, WAVELENGTHS, n_clusters=3)
        second = classify.classify_bottom(rho_b, valid, WAVELENGTHS, n_clusters=3)
        assert np.array_equal(first.labels, second.labels)


class TestLabelsStayNeutral:
    def test_labels_are_spectral_classes_not_habitats(
        self, three_substrate_scene: tuple[np.ndarray, np.ndarray]
    ) -> None:
        rho_b, valid = three_substrate_scene
        result = classify.classify_bottom(rho_b, valid, WAVELENGTHS, n_clusters=4)
        assert result.centroid_labels == [
            "spectral_class_1",
            "spectral_class_2",
            "spectral_class_3",
            "spectral_class_4",
        ]
        joined = " ".join(result.centroid_labels).lower()
        for forbidden in ("kelp", "sand", "rock", "seagrass", "algae"):
            assert forbidden not in joined


class TestMasking:
    def test_masked_pixels_get_the_sentinel_label(
        self, three_substrate_scene: tuple[np.ndarray, np.ndarray]
    ) -> None:
        rho_b, valid = three_substrate_scene
        valid = valid.copy()
        valid[:5, :] = False
        result = classify.classify_bottom(rho_b, valid, WAVELENGTHS, n_clusters=3)
        assert (result.labels[:5, :] == -1).all()
        assert (result.labels[5:, :] >= 0).all()

    def test_non_finite_visible_pixels_are_excluded(
        self, three_substrate_scene: tuple[np.ndarray, np.ndarray]
    ) -> None:
        rho_b, valid = three_substrate_scene
        rho_b = rho_b.copy()
        rho_b[0, 10, 10] = np.nan
        result = classify.classify_bottom(rho_b, valid, WAVELENGTHS, n_clusters=3)
        assert result.labels[10, 10] == -1


class TestFeatureBandSelection:
    def test_opaque_bands_are_dropped_from_the_features(
        self, three_substrate_scene: tuple[np.ndarray, np.ndarray]
    ) -> None:
        rho_b, valid = three_substrate_scene
        result = classify.classify_bottom(rho_b, valid, WAVELENGTHS, n_clusters=3)
        assert result.wavelengths_nm.tolist() == [444.0, 489.0, 561.0, 667.0]
        assert result.centroid_spectra.shape == (3, 4)

    def test_too_few_usable_bands_is_rejected(
        self, three_substrate_scene: tuple[np.ndarray, np.ndarray]
    ) -> None:
        rho_b, valid = three_substrate_scene
        with pytest.raises(ValueError, match="need at least 3"):
            classify.classify_bottom(rho_b, valid, WAVELENGTHS, max_cluster_wavelength_nm=500.0)

    def test_raising_the_cutoff_admits_more_bands(
        self, three_substrate_scene: tuple[np.ndarray, np.ndarray]
    ) -> None:
        rho_b, valid = three_substrate_scene
        rho_b = rho_b.copy()
        rho_b[4] = 0.001  # pretend the NIR band inverted cleanly
        result = classify.classify_bottom(
            rho_b,
            valid,
            WAVELENGTHS,
            n_clusters=3,
            max_cluster_wavelength_nm=900.0,
        )
        assert result.wavelengths_nm.size == 5


class TestGuards:
    def test_too_few_valid_pixels_is_rejected(
        self, three_substrate_scene: tuple[np.ndarray, np.ndarray]
    ) -> None:
        rho_b, valid = three_substrate_scene
        valid = valid.copy()
        valid[:] = False
        valid[0, :10] = True
        with pytest.raises(ValueError, match="Only 10 valid pixels"):
            classify.classify_bottom(rho_b, valid, WAVELENGTHS, n_clusters=3)
