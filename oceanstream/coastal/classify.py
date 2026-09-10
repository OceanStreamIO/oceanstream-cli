"""k-means clustering on effective benthic reflectance — deliberately unsupervised.

Coastal campaigns produce tens of GPS quadrats, not thousands. That is far too
few to fit a supervised classifier anyone should defend, so this module
clusters on rho_b and leaves in-situ observations as an *independent overlay*.

Output labels are ``spectral_class_1..N``, ordered by brightness. They are
spectral clusters in bottom-reflectance space, not habitat classes. Nothing in
this module knows what kelp is, and nothing downstream should pretend it does:
the interpretation is the ecologist's, and it belongs in a caption rather than
in metadata.

Ported from ``kelp_observe/tools/lee_demo/classify.py`` in Phase 1.3. The
prototype's hard-wired 4-colour palette and legend builder were left behind —
they are presentation, not physics.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

#: Prefix for every cluster label. Deliberately meaningless.
CLUSTER_LABEL_PREFIX = "spectral_class"

#: Minimum pixels per cluster before a fit is attempted. Below this k-means
#: converges on noise and the centroid ordering stops being reproducible.
MIN_PIXELS_PER_CLUSTER = 100

#: Minimum feature bands. Two bands cannot separate brightness from colour.
MIN_FEATURE_BANDS = 3


@dataclass
class ClusterResult:
    """Cluster labels plus the per-cluster mean spectrum behind them."""

    labels: np.ndarray                 # (H, W) int8; -1 where masked
    n_clusters: int
    centroid_spectra: np.ndarray       # (n_clusters, n_feature_bands) rho_b means
    centroid_labels: list[str]         # spectral_class_1..N, brightness-ordered
    inertia: float                     # k-means inertia; QA only
    n_pixels_per_cluster: np.ndarray   # (n_clusters,) int64
    wavelengths_nm: np.ndarray         # (n_feature_bands,) for the legend


def classify_bottom(
    rho_b: np.ndarray,
    valid: np.ndarray,
    wavelengths_nm: np.ndarray,
    n_clusters: int = 4,
    max_pixels: int = 200_000,
    rng_seed: int = 42,
    max_cluster_wavelength_nm: float = 720.0,
) -> ClusterResult:
    """k-means on rho_b across the bands that carry benthic information.

    Parameters
    ----------
    rho_b
        (n_bands, H, W) effective benthic reflectance from
        :func:`oceanstream.coastal.inversion.lee.invert_scene`.
    valid
        (H, W) composite mask AND the optical-depth-score gate — pixels where
        the retrieval is defensible. Passing the composite mask alone will
        cluster on noise from optically-deep water.
    wavelengths_nm
        (n_bands,) band centres.
    n_clusters
        Number of spectral clusters. Four is a reasonable default for a rocky
        reef; adjust from the QA panel, not from expectation.
    max_pixels
        Subsample size for the fit. Prediction still runs on every valid
        pixel. 200 k is ample for a handful of clusters.
    rng_seed
        Seeds both the subsample and k-means, so a rerun reproduces the map.
    max_cluster_wavelength_nm
        Only bands at or below this centre drive the clustering. Water is
        opaque in NIR and SWIR, so their rho_b is retrieval noise rather than
        habitat signal — and, worse, it dominates the all-bands-finite gate.

    Returns
    -------
    ClusterResult
        Centroids are re-sorted by brightness (summed reflectance), which
        makes the colour ramp stable across reruns and comparable between
        sensors.
    """
    from sklearn.cluster import KMeans

    _n_bands, height, width = rho_b.shape

    feature_band_idx = np.where(wavelengths_nm <= max_cluster_wavelength_nm)[0]
    if feature_band_idx.size < MIN_FEATURE_BANDS:
        raise ValueError(
            f"Only {feature_band_idx.size} bands with centre <= "
            f"{max_cluster_wavelength_nm} nm — need at least "
            f"{MIN_FEATURE_BANDS} for stable clustering. Check the sensor "
            "wavelengths, or raise max_cluster_wavelength_nm if the sensor "
            "genuinely has usable red-edge bands."
        )

    rho_b_features = rho_b[feature_band_idx]

    valid_and_finite = valid & np.all(np.isfinite(rho_b_features), axis=0)
    n_valid = int(valid_and_finite.sum())
    if n_valid < n_clusters * MIN_PIXELS_PER_CLUSTER:
        raise ValueError(
            f"Only {n_valid} valid pixels — need at least "
            f"{n_clusters * MIN_PIXELS_PER_CLUSTER} for {n_clusters} "
            "clusters. Widen the mask, relax the optical-depth gate, or "
            "reduce n_clusters."
        )

    features = np.stack(
        [rho_b_features[b][valid_and_finite] for b in range(feature_band_idx.size)],
        axis=1,
    )

    rng = np.random.default_rng(rng_seed)
    if features.shape[0] > max_pixels:
        idx = rng.choice(features.shape[0], size=max_pixels, replace=False)
        fit_features = features[idx]
    else:
        fit_features = features

    kmeans = KMeans(
        n_clusters=n_clusters,
        random_state=rng_seed,
        n_init=10,
        max_iter=300,
    )
    kmeans.fit(fit_features)

    all_labels = kmeans.predict(features).astype(np.int8)

    # Reorder centroids by mean brightness so the label -> cluster mapping is
    # stable across reruns and comparable between sensors.
    centroid_order = np.argsort(kmeans.cluster_centers_.sum(axis=1))
    remap = np.zeros(n_clusters, dtype=np.int8)
    for new_idx, old_idx in enumerate(centroid_order):
        remap[old_idx] = new_idx
    all_labels = remap[all_labels]

    label_map = np.full((height, width), -1, dtype=np.int8)
    label_map[valid_and_finite] = all_labels

    return ClusterResult(
        labels=label_map,
        n_clusters=n_clusters,
        centroid_spectra=kmeans.cluster_centers_[centroid_order].astype(np.float32),
        centroid_labels=[
            f"{CLUSTER_LABEL_PREFIX}_{i + 1}" for i in range(n_clusters)
        ],
        inertia=float(kmeans.inertia_),
        n_pixels_per_cluster=np.bincount(
            all_labels, minlength=n_clusters
        ).astype(np.int64),
        wavelengths_nm=wavelengths_nm[feature_band_idx].astype(np.float32),
    )
