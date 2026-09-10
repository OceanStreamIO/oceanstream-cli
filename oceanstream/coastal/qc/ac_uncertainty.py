"""Measure the atmospheric-correction uncertainty that actually limits discrimination.

Ported from ``ac_uncertainty.py`` in Phase 1.6.

It is tempting to take a scene's additive AC offset as the threshold below
which two substrates cannot be told apart. That is the wrong quantity. A
*uniform* additive offset does not impair class discrimination: it shifts both
pixels equally and cancels in their difference. What corrupts discrimination is
the *spatially varying* component, because that is what masquerades as a
substrate difference between neighbouring pixels.

So the threshold is the pixel-to-pixel scatter among pixels that share
substrate and depth. This module measures it without ground truth, using
bottom-blind pixels:

* At the null channel (~667 nm) below ~10 m, pure-water absorption makes bottom
  return impossible — two-way transmission of order 1e-7. Any spatial structure
  left there is atmospheric correction plus sensor noise, by elimination.
* At the discriminating band (~561 nm) the bottom *does* contribute. Comparing
  the two isolates how much real substrate signal green carries at depth, again
  with no ground truth.

The output is a diagnostic, not a gate. It answers "how small a reflectance
difference is it honest to call a substrate difference in this scene", which is
a question that otherwise gets settled by assertion.
"""

from __future__ import annotations

import logging

import numpy as np

from oceanstream.coastal.config import QCConfig
from oceanstream.coastal.inversion import lee

logger = logging.getLogger(__name__)


def nearest_band(
    reflectance: dict[float, np.ndarray],
    target_nm: float,
    tolerance_nm: float = 8.0,
) -> float | None:
    """Match a band on proximity — sensor centres drift a few nm per platform.

    Returns ``None`` rather than the nearest band when nothing lands inside
    the tolerance, so a missing band fails loudly instead of silently
    substituting a band that measures something else.
    """
    if not reflectance:
        return None
    best = min(reflectance, key=lambda w: abs(w - target_nm))
    return best if abs(best - target_nm) <= tolerance_nm else None


def within_block_scatter(
    values: np.ndarray,
    mask: np.ndarray,
    block_px: int,
    pixel_size_m: float = 10.0,
    min_pixels: int = 12,
) -> dict[str, object]:
    """Standard deviation *within* blocks, pooled across blocks.

    Within-block scatter is the quantity that matters: it is the disagreement
    between pixels close enough that a classifier would be asked to tell them
    apart. A scene-wide standard deviation would fold in large-scale gradients
    that cancel locally, overstating the noise floor.
    """
    rows, cols = values.shape
    sds: list[float] = []
    counts: list[int] = []
    for r0 in range(0, rows, block_px):
        for c0 in range(0, cols, block_px):
            block = values[r0 : r0 + block_px, c0 : c0 + block_px]
            block_mask = mask[r0 : r0 + block_px, c0 : c0 + block_px]
            sample = block[block_mask & np.isfinite(block)]
            if sample.size < min_pixels:
                continue
            sds.append(float(np.std(sample)))
            counts.append(int(sample.size))

    if not sds:
        return {"n_blocks": 0, "median_sd_rhos": None}

    sds_arr = np.asarray(sds)
    return {
        "n_blocks": len(sds),
        "n_pixels": int(np.sum(counts)),
        "median_sd_rhos": round(float(np.median(sds_arr)), 6),
        "p90_sd_rhos": round(float(np.percentile(sds_arr, 90)), 6),
        "block_size_m": block_px * pixel_size_m,
    }


def _median_sd(band_summary: dict[str, object], block_key: str) -> float | None:
    """Pull one block's median scatter out of the nested per-band summary.

    The summaries are plain dicts so they serialise straight into a STAC item;
    this keeps the unavoidable narrowing in one place.
    """
    by_block = band_summary["by_block"]
    assert isinstance(by_block, dict)
    value = by_block[block_key]["median_sd_rhos"]
    return None if value is None else float(value)


def effective_threshold_rhos(
    reflectance: dict[float, np.ndarray],
    depth_m: np.ndarray,
    water_mask: np.ndarray,
    additive_offset: float = 0.0,
    pixel_size_m: float = 10.0,
    config: QCConfig | None = None,
) -> dict[str, object]:
    """Smallest reflectance difference this scene can honestly resolve.

    Parameters
    ----------
    reflectance
        Band centre (nm) to (H, W) above-water reflectance.
    depth_m
        (H, W) bathymetry, used only to select bottom-blind pixels.
    water_mask
        (H, W) composite mask, True where the pixel is usable.
    additive_offset
        Scene additive offset to remove before measuring scatter. Removing it
        changes nothing about the scatter itself — it is subtracted so the
        reported medians are interpretable.
    pixel_size_m
        Ground sample distance, used to convert the configured block sizes in
        metres into pixels.
    config
        Bands, depth cut, block sizes, and thresholds.

    Returns
    -------
    dict
        ``effective_threshold_rhos`` is the headline number: the median
        within-block scatter of the bottom-blind band at the smallest block
        size. ``status`` is ``insufficient_data`` when the scene has too few
        bottom-blind pixels to say anything.
    """
    cfg = config or QCConfig()

    result: dict[str, object] = {
        "additive_offset_rhos": round(float(additive_offset), 6),
        "bottom_blind_depth_m": cfg.bottom_blind_depth_m,
        "pixel_size_m": pixel_size_m,
    }

    blind_pixels = (
        water_mask & np.isfinite(depth_m) & (depth_m > cfg.bottom_blind_depth_m)
    )
    result["n_bottom_blind_pixels"] = int(blind_pixels.sum())
    if blind_pixels.sum() < cfg.min_bottom_blind_pixels:
        result["status"] = "insufficient_data"
        return result

    block_sizes_px = tuple(
        max(1, int(round(size_m / pixel_size_m)))
        for size_m in cfg.scatter_block_sizes_m
    )

    per_band: dict[str, dict[str, object]] = {}
    for label, target in (
        ("bottom_blind", cfg.bottom_blind_nm),
        ("discriminating", cfg.discriminating_nm),
    ):
        wl = nearest_band(reflectance, target, cfg.band_match_tolerance_nm)
        if wl is None:
            continue
        band = reflectance[wl] - additive_offset
        transmission = float(
            np.exp(-lee.kb_pure_water(wl) * cfg.bottom_blind_depth_m)
        )
        per_band[label] = {
            "wavelength_nm": wl,
            "pure_water_transmission_at_blind_depth": f"{transmission:.2e}",
            "median_rhos": round(float(np.median(band[blind_pixels])), 6),
            "by_block": {
                f"{int(px * pixel_size_m)}m": within_block_scatter(
                    band,
                    blind_pixels,
                    px,
                    pixel_size_m=pixel_size_m,
                    min_pixels=cfg.min_pixels_per_block,
                )
                for px in block_sizes_px
            },
        }
    result["bands"] = per_band

    blind = per_band.get("bottom_blind")
    disc = per_band.get("discriminating")
    if blind is None or disc is None:
        result["status"] = "missing_band"
        return result

    smallest = f"{int(block_sizes_px[0] * pixel_size_m)}m"
    sd_ac = _median_sd(blind, smallest)
    sd_green = _median_sd(disc, smallest)

    result["effective_threshold_rhos"] = sd_ac
    result["interpretation"] = {
        "block_size_m": smallest,
        "ac_noise_sd_rhos": sd_ac,
        "green_total_sd_rhos": sd_green,
        "green_excess_over_ac": (
            round(sd_green - sd_ac, 6)
            if sd_ac is not None and sd_green is not None
            else None
        ),
        "green_carries_substrate_signal": (
            bool(sd_green > cfg.substrate_signal_ratio * sd_ac)
            if sd_ac and sd_green
            else None
        ),
    }
    result["status"] = "diagnostic_only"
    return result
