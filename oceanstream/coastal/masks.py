"""Masks — land, cloud, sun-glint, foam, cliff-adjacency buffer.

Every function returns a boolean array where True means *valid pixel* (keep).
Combine with ``&`` to produce the composite mask that gates every downstream
step. Downstream code must never touch pixels where the composite is False.

Ported from ``kelp_observe/tools/lee_demo/masks.py`` in Phase 1.3. The
prototype's per-function keyword thresholds are now defaults sourced from
:class:`~oceanstream.coastal.config.MaskConfig`, so one config object
configures the whole chain for a new AOI or sensor.
"""

from __future__ import annotations

import numpy as np
from scipy import ndimage

from oceanstream.coastal.config import MaskConfig

# --------------------------------------------------------------------------
# Individual masks — each returns True where the pixel is valid
# --------------------------------------------------------------------------

def land_mask(nir_reflectance: np.ndarray, threshold: float = 0.10) -> np.ndarray:
    """Water pixels only. NIR is strongly absorbed by clean water.

    The default threshold catches most sun-lit land and optically-shallow
    bright sand; for turbid or whitecap-heavy pixels the glint and foam masks
    pick up the residual. Deliberately generous — tighter thresholds cut into
    optically-shallow water where the seafloor is bright.
    """
    return np.asarray(nir_reflectance < threshold)


def cloud_mask(
    blue_reflectance: np.ndarray,
    nir_reflectance: np.ndarray,
    threshold: float = 0.20,
) -> np.ndarray:
    """Cloudy pixels have high reflectance in *both* blue and NIR.

    Water is only bright in blue; land is only bright in NIR; clouds are
    bright in both. Not a Sen2Cor-grade cloud detector — a fast, opinionated
    threshold for a single-scene retrieval.
    """
    return np.asarray(
        ~((blue_reflectance > threshold) & (nir_reflectance > threshold))
    )


def glint_mask(
    nir_reflectance: np.ndarray,
    swir_reflectance: np.ndarray | None = None,
    nir_threshold: float = 0.03,
    swir_threshold: float | None = None,
) -> np.ndarray:
    """Reject specular sun-glint.

    Water pixels with elevated NIR are almost always glint-contaminated.
    PNeo has no SWIR — pass ``swir_reflectance=None`` and rely on NIR alone.
    Sentinel-2 has B11/B12; combining them tightens the mask.

    ``swir_threshold`` defaults to ``nir_threshold`` when not supplied.
    """
    valid = nir_reflectance < nir_threshold
    if swir_reflectance is not None:
        thr_swir = nir_threshold if swir_threshold is None else swir_threshold
        valid = valid & (swir_reflectance < thr_swir)
    return np.asarray(valid)


def foam_mask(
    rgb_reflectance: np.ndarray,
    threshold: float = 0.15,
    flatness_max: float = 1.4,
) -> np.ndarray:
    """Whitecaps and surf-line foam — bright, near-flat spectrum.

    ``rgb_reflectance`` has shape (3, H, W) with the visible bands in any
    order. Rejects pixels where all three visible bands exceed ``threshold``
    and their max/min ratio is below ``flatness_max``.
    """
    high = np.all(rgb_reflectance > threshold, axis=0)
    with np.errstate(divide="ignore", invalid="ignore"):
        span = rgb_reflectance.max(axis=0) / np.clip(
            rgb_reflectance.min(axis=0), 1e-6, None
        )
    return np.asarray(~(high & (span < flatness_max)))


def adjacency_buffer(land: np.ndarray, buffer_px: int) -> np.ndarray:
    """Drop water pixels within ``buffer_px`` pixels of land.

    ``land`` is the land-*rejection* mask (True over water), matching what
    :func:`land_mask` returns.

    ``buffer_px <= 0`` disables the buffer. The guard is not cosmetic:
    ``scipy.ndimage.binary_dilation`` reads ``iterations=0`` as "dilate until
    nothing changes", so without it a configured zero buffer would flood the
    whole scene with land and silently mask everything.
    """
    if buffer_px <= 0:
        return np.asarray(land)
    dilated_land = ndimage.binary_dilation(
        ~land, iterations=buffer_px, border_value=0
    )
    return np.asarray(~dilated_land)


# --------------------------------------------------------------------------
# Composite
# --------------------------------------------------------------------------

def composite_mask(
    *,
    blue: np.ndarray,
    red: np.ndarray,
    nir: np.ndarray,
    swir: np.ndarray | None = None,
    config: MaskConfig | None = None,
) -> tuple[np.ndarray, dict[str, np.ndarray]]:
    """Combine individual masks. Returns ``(composite, components)``.

    All input arrays share the same shape and are *surface* reflectance
    (ACOLITE ``rhos_*`` or the Hedley-deglinted equivalent).

    The ``components`` dict keeps each individual mask inspectable — when a
    scene fails its rhos-sanity gate, look at the components before touching
    thresholds.
    """
    cfg = config or MaskConfig()
    components: dict[str, np.ndarray] = {
        "land": land_mask(nir, threshold=cfg.land_nir_max),
        "cloud": cloud_mask(blue, nir, threshold=cfg.cloud_reflectance_max),
        "glint": glint_mask(
            nir,
            swir,
            nir_threshold=cfg.glint_nir_max,
            swir_threshold=cfg.glint_swir_max,
        ),
        "foam": foam_mask(
            np.stack([blue, red, nir]),
            threshold=cfg.foam_reflectance_min,
            flatness_max=cfg.foam_flatness_max,
        ),
    }
    components["adjacency"] = adjacency_buffer(
        components["land"], buffer_px=cfg.adjacency_buffer_px
    )

    composite = np.logical_and.reduce(list(components.values()))
    return composite, components


# --------------------------------------------------------------------------
# Deep-water sampling for the QAA fit
# --------------------------------------------------------------------------

def deep_water_pixels(
    valid: np.ndarray,
    depth: np.ndarray,
    kd_490: np.ndarray | None = None,
    config: MaskConfig | None = None,
) -> np.ndarray:
    """Boolean mask of optically-deep pixels for QAA fitting.

    "Optically deep" means the bottom contribution to ``r_rs`` is below the
    sensor noise floor. Two independent gates, both of which a pixel must
    pass:

    - depth > ``config.deepwater_min_depth_m`` (from EMODnet or Stumpf)
    - z * Kd(490) > ``config.deepwater_min_optical_depth`` (rigorous, but
      requires a first Kd guess — see
      :func:`oceanstream.coastal.optics.qaa.kd_map`)

    Survivors are subsampled to ``config.deepwater_max_samples`` to keep the
    fit fast; the draw is seeded so a rerun reproduces the same sample.
    """
    cfg = config or MaskConfig()
    deep = valid & (depth > cfg.deepwater_min_depth_m)
    if kd_490 is not None:
        deep = deep & (depth * kd_490 > cfg.deepwater_min_optical_depth)

    if deep.sum() > cfg.deepwater_max_samples:
        rng = np.random.default_rng(cfg.rng_seed)
        idx = np.flatnonzero(deep.ravel())
        keep = rng.choice(idx, size=cfg.deepwater_max_samples, replace=False)
        subsampled = np.zeros_like(deep.ravel())
        subsampled[keep] = True
        deep = subsampled.reshape(deep.shape)
    return np.asarray(deep)
