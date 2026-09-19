"""Estimate the additive atmospheric-correction residual left on a scene.

Ported from ``reef_calibration.deepwater_additive_offset`` in Phase 5.

Beyond about 800 nm, pure water absorbs so strongly that water-leaving
reflectance over optically deep water is zero to within any usable precision.
Whatever a corrected scene still reports there is therefore residual
atmosphere, glint or sensor offset — measured directly, with no ground truth
and no free parameters. This is the "black pixel" assumption, and over deep
water it is about as close to an absolute reference as remote sensing offers.

Why this matters more than its size suggests: the residual is *additive*, so it
survives every subsequent step that assumes a multiplicative decay. At one
reference site it measures 0.0195 while the green water signal it sits on is
0.0074 — the artefact is 2.6x the quantity being measured. Left in, it flattens
the difference between bands, which is precisely the signature the empirical
attenuation fit and the QAA reference-band rule read as physics.

Two honest caveats, both reported rather than hidden:

* A scalar cannot represent a spatially varying residual. Glint in particular
  varies across a scene, and collapsing it to one number moves that structure
  into the residual rather than removing it. ``spectrally_varying`` flags the
  spectral half of this; the spatial half is what
  :mod:`oceanstream.coastal.qc.ac_uncertainty` measures.
* The estimate is only as good as the deep-water sample. Cloud and glint raise
  it — on unscreened patches the prototype measured it roughly 4x too high — so
  the sample is checked against SWIR, where any signal at all is contamination.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from oceanstream.coastal.config import QCConfig

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AdditiveOffset:
    """A scene's additive offset, with the evidence for trusting it."""

    value: float
    by_band: dict[float, float] = field(default_factory=dict)
    n_pixels: int = 0
    swir_median: float | None = None
    qa_flags: tuple[str, ...] = ()

    @property
    def trustworthy(self) -> bool:
        """True when no flag was raised. An untrustworthy offset is still
        returned — the caller decides whether to subtract it or to downgrade
        the scene."""
        return not self.qa_flags

    def to_dict(self) -> dict[str, Any]:
        return {
            "additive_offset_rhos": round(self.value, 6),
            "by_band_rhos": {
                f"{wl:g}": round(v, 6) for wl, v in sorted(self.by_band.items())
            },
            "n_deep_pixels": self.n_pixels,
            "swir_median_rhos": (
                None if self.swir_median is None else round(self.swir_median, 6)
            ),
            "qa_flags": list(self.qa_flags),
            "trustworthy": self.trustworthy,
        }


def _masked_median(band: np.ndarray, mask: np.ndarray) -> float | None:
    """Median over the mask, or None when nothing finite survives."""
    values = band[mask]
    finite = values[np.isfinite(values)]
    return float(np.median(finite)) if finite.size else None


def deepwater_additive_offset(
    reflectance: dict[float, np.ndarray],
    deep_mask: np.ndarray,
    config: QCConfig | None = None,
) -> AdditiveOffset:
    """Additive offset from the NIR black-pixel assumption.

    Parameters
    ----------
    reflectance
        Band centre (nm) to (H, W) above-water surface reflectance.
    deep_mask
        (H, W) bool, True over optically deep water that has already passed
        the composite cloud/glint/foam mask. Geometry alone is not enough:
        see the module docstring.
    config
        Wavelength cuts and thresholds.

    Returns
    -------
    AdditiveOffset
        ``value`` is the median across NIR bands of the per-band deep-water
        median, clamped at zero. Check ``trustworthy`` before subtracting.
    """
    cfg = config or QCConfig()
    n_pixels = int(np.count_nonzero(deep_mask))
    flags: list[str] = []

    # SWIR is excluded here so it stays an independent check on the sample.
    nir = sorted(
        wl
        for wl in reflectance
        if cfg.offset_nir_min_nm <= wl < cfg.offset_swir_min_nm
    )
    if not nir:
        return AdditiveOffset(0.0, {}, n_pixels, None, ("no_nir_band",))

    if n_pixels < cfg.min_offset_pixels:
        flags.append("insufficient_pixels")

    by_band = {
        wl: median
        for wl in nir
        if (median := _masked_median(reflectance[wl], deep_mask)) is not None
    }
    if not by_band:
        return AdditiveOffset(0.0, {}, n_pixels, None, ("no_finite_pixels",))

    raw = float(np.median(list(by_band.values())))
    value = max(0.0, raw)
    if raw < 0.0:
        flags.append("clamped_negative")

    spread = max(by_band.values()) - min(by_band.values())
    if value > 0.0 and spread > cfg.offset_band_spread_max * value:
        flags.append("spectrally_varying")

    swir_medians = [
        median
        for wl in sorted(reflectance)
        if wl >= cfg.offset_swir_min_nm
        and (median := _masked_median(reflectance[wl], deep_mask)) is not None
    ]
    swir_median = float(np.median(swir_medians)) if swir_medians else None
    if swir_median is not None and swir_median > cfg.offset_swir_max:
        flags.append("deep_mask_contaminated")

    logger.info(
        "deep-water additive offset %.5f rhos from %d NIR band(s), %d pixels%s",
        value,
        len(by_band),
        n_pixels,
        f" [{', '.join(flags)}]" if flags else "",
    )
    return AdditiveOffset(value, by_band, n_pixels, swir_median, tuple(flags))
