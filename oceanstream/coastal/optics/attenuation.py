"""Empirical two-way attenuation k(λ), measured against known bathymetry.

Extracted from ``reef_calibration.py`` in Phase 1.5. Of everything the
prototype produced, this is the piece worth keeping: it measures the water the
retrieval actually happens in, rather than importing an estimate from
somewhere else.

Why the empirical route
-----------------------
Both the Lee inversion and a Lyzenga depth-invariant index need to know how
fast light is attenuated in the water column. The Lee path takes k_b from a
QAA fit over optically-deep water — often kilometres offshore and hundreds of
metres deep — and then applies it to a coastal column that may be a different
water mass entirely.

Two synthetic results frame why that matters (see
``tests/unit/coastal/test_lee_inversion.py``):

1. Vertical stratification is *not* the defect. The bottom path attenuates as
   ``exp(-∫k_b dz)``, so a depth-averaged IOP reproduces a two-layer column
   with 100% contrast to within a fraction of a percent.
2. Using IOPs from the wrong water *is* the defect. Coastal water 25% more
   absorbing than the offshore reference gives roughly -26% error at 7 m,
   growing to -109% at 20 m — the same depth-dependent trend that is easily
   misread as a k_b formulation error.

Regressing observed reflectance against *known* depth over one substrate
measures the column-integrated attenuation of the actual water. That absorbs
both the vertical structure and the water-mass difference at once, without
importing anything from offshore.

Method
------
For a uniform bottom, subsurface reflectance above a deep-water baseline decays
as ``L(H) - L_inf = (rho_b / pi) * exp(-k * H)``, so::

    ln(L - L_inf) = const - k * H

and the slope against depth is the effective two-way attenuation. Lyzenga's
coefficient is then simply the ratio of two slopes.

Substrate selection
-------------------
The regression needs *one* substrate across depths, and substrate is itself
depth-dependent (canopy shallow, sediment deep) — regressing over all pixels
would fold habitat stratification into k. Instead a high brightness quantile is
tracked within each depth bin, which follows the brightest endmember. Both
consequences are reported rather than hidden: the quantile is a free parameter,
so a sensitivity sweep is always emitted, and the selection favours positive
noise, so k is biased slightly low.

This is a calibration to be checked, not an accepted product. Confirming that
the tracked endmember really is a single substrate requires imagery finer than
the calibration grid.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np

from oceanstream.coastal.config import AttenuationConfig
from oceanstream.coastal.inversion import lee

logger = logging.getLogger(__name__)


@dataclass
class BandCalibration:
    """One band's effective two-way attenuation, fitted against known depth."""

    wavelength_nm: float
    k_per_m: float
    intercept: float
    r_squared: float
    n_bins: int
    n_pixels: int
    depth_range_m: tuple[float, float]
    deep_water_reference: float
    frac_nonpositive_residual: float
    k_by_quantile: dict[str, float] = field(default_factory=dict)

    @property
    def k_pure_water_floor(self) -> float:
        """Physical lower bound on ``k_per_m``: pure water alone."""
        return float(lee.kb_pure_water(self.wavelength_nm))

    @property
    def k_below_pure_water_floor(self) -> bool:
        """True when the fit returned less attenuation than pure water has."""
        return bool(
            np.isfinite(self.k_per_m) and self.k_per_m < self.k_pure_water_floor
        )

    @property
    def k_effective(self) -> float:
        """``k_per_m`` floored at pure water — the value consumers should use.

        A fit below the floor is not a measurement of attenuation. It usually
        means an uncorrected additive offset in the reflectance, or a band with
        no bottom signal left to fit. Reported alongside the raw fit rather
        than replacing it, so the failure stays visible.
        """
        if not np.isfinite(self.k_per_m):
            return float("nan")
        return max(float(self.k_per_m), self.k_pure_water_floor)

    @property
    def quantile_spread(self) -> float:
        """Range of k across quantile choices — the free-parameter sensitivity.

        A spread comparable to k itself means the fit is an artefact of the
        substrate tracker rather than a property of the water.
        """
        values = list(self.k_by_quantile.values())
        return float(max(values) - min(values)) if values else float("nan")


def deep_water_reference(
    reflectance: dict[float, np.ndarray],
    deep_mask: np.ndarray,
    config: AttenuationConfig | None = None,
) -> dict[float, float]:
    """Median reflectance over optically-deep pixels, per band.

    This is the ``L_inf`` subtracted before taking the logarithm. Getting it
    wrong shifts every residual and therefore biases every k, which is the
    single most common cause of a fit landing below the pure-water floor.
    """
    cfg = config or AttenuationConfig()
    reference: dict[float, float] = {}
    for wavelength, band in reflectance.items():
        values = band[deep_mask & np.isfinite(band)]
        if values.size < cfg.min_deep_reference_pixels:
            raise ValueError(
                f"Only {values.size} deep-water pixels for {wavelength:.0f} nm "
                f"— need >= {cfg.min_deep_reference_pixels} for a stable "
                "reference. Widen the deep-water mask, or lower "
                "AttenuationConfig.min_deep_reference_pixels if the AOI "
                "genuinely has little optically-deep water."
            )
        reference[wavelength] = float(np.median(values))
    return reference


def fit_band_attenuation(
    band: np.ndarray,
    depth: np.ndarray,
    valid: np.ndarray,
    reference: float,
    quantile: float,
    config: AttenuationConfig | None = None,
) -> tuple[float, float, float, int, int]:
    """Quantile-per-depth-bin regression of ``ln(L - L_inf)`` against depth.

    Returns ``(k_per_m, intercept, r_squared, n_bins, n_pixels)``. A fit that
    cannot be attempted returns NaN for the three floats rather than raising,
    so one dead band does not take the whole scene down; the counts still come
    back so the caller can see *how* it failed.
    """
    cfg = config or AttenuationConfig()
    residual = band - reference
    usable = (
        valid
        & np.isfinite(residual)
        & (residual > 0)  # ln() is undefined once the bottom signal is gone
        & (depth >= cfg.depth_min_m)
        & (depth <= cfg.depth_max_m)
    )
    if usable.sum() < cfg.min_pixels_per_bin:
        return (float("nan"),) * 3 + (0, int(usable.sum()))

    d = depth[usable]
    y = np.log(residual[usable])

    edges = np.arange(
        cfg.depth_min_m, cfg.depth_max_m + cfg.depth_bin_m, cfg.depth_bin_m
    )
    centres: list[float] = []
    quantiles: list[float] = []
    for lo, hi in zip(edges[:-1], edges[1:], strict=False):
        in_bin = (d >= lo) & (d < hi)
        if in_bin.sum() >= cfg.min_pixels_per_bin:
            centres.append((lo + hi) / 2.0)
            quantiles.append(float(np.quantile(y[in_bin], quantile)))

    if len(centres) < cfg.min_bins:
        return (float("nan"),) * 3 + (len(centres), int(usable.sum()))

    x = np.asarray(centres)
    y_binned = np.asarray(quantiles)
    slope, intercept = np.polyfit(x, y_binned, 1)
    predicted = slope * x + intercept
    ss_res = float(np.sum((y_binned - predicted) ** 2))
    ss_tot = float(np.sum((y_binned - y_binned.mean()) ** 2))
    r_squared = 1.0 - ss_res / ss_tot if ss_tot > 0 else float("nan")

    return (
        -float(slope),
        float(intercept),
        r_squared,
        len(centres),
        int(usable.sum()),
    )


def calibrate_bands(
    reflectance: dict[float, np.ndarray],
    depth: np.ndarray,
    water_mask: np.ndarray,
    deep_mask: np.ndarray,
    config: AttenuationConfig | None = None,
) -> dict[float, BandCalibration]:
    """Fit effective two-way attenuation per band against known bathymetry.

    Parameters
    ----------
    reflectance
        Mapping of band centre wavelength (nm) to a (H, W) subsurface
        reflectance raster.
    depth
        (H, W) bathymetry in metres. The accuracy of k is bounded by the
        accuracy of this surface — a depth bias propagates straight into the
        slope.
    water_mask
        (H, W) composite mask, True where the pixel is usable.
    deep_mask
        (H, W) optically-deep pixels, used only for the ``L_inf`` reference.
    config
        Bin width, depth window, quantile, and the sensitivity sweep.

    Returns
    -------
    dict
        One :class:`BandCalibration` per input wavelength. Always inspect
        ``k_below_pure_water_floor`` and ``quantile_spread`` before using
        ``k_per_m`` — a fit can be numerically clean and physically void.
    """
    cfg = config or AttenuationConfig()
    reference = deep_water_reference(reflectance, deep_mask, config=cfg)
    results: dict[float, BandCalibration] = {}

    for wavelength, band in reflectance.items():
        k, intercept, r2, n_bins, n_px = fit_band_attenuation(
            band, depth, water_mask, reference[wavelength], cfg.quantile, cfg
        )

        residual = band - reference[wavelength]
        considered = water_mask & np.isfinite(residual)
        nonpositive = (
            float((residual[considered] <= 0).mean())
            if considered.any()
            else float("nan")
        )

        by_quantile: dict[str, float] = {}
        for q in cfg.sensitivity_quantiles:
            k_q, *_ = fit_band_attenuation(
                band, depth, water_mask, reference[wavelength], q, cfg
            )
            if np.isfinite(k_q):
                by_quantile[f"{q:.2f}"] = round(k_q, 5)

        results[wavelength] = BandCalibration(
            wavelength_nm=wavelength,
            k_per_m=k,
            intercept=intercept,
            r_squared=r2,
            n_bins=n_bins,
            n_pixels=n_px,
            depth_range_m=(cfg.depth_min_m, cfg.depth_max_m),
            deep_water_reference=reference[wavelength],
            frac_nonpositive_residual=nonpositive,
            k_by_quantile=by_quantile,
        )
        logger.info(
            "%.0f nm: k = %.4f /m (R2 %.3f, %d bins, %d px, quantile spread %.4f)",
            wavelength,
            k,
            r2,
            n_bins,
            n_px,
            results[wavelength].quantile_spread,
        )
    return results


def lyzenga_ratios(
    calibrations: dict[float, BandCalibration],
) -> dict[str, float]:
    """``k_i / k_j`` for every band pair — the depth-invariant index coefficients.

    The ratio is the only part of the calibration that survives an additive
    offset in the reflectance, because a common bias largely cancels between
    bands. That makes it a useful cross-check on the absolute k values.
    """
    ratios: dict[str, float] = {}
    wavelengths = sorted(calibrations)
    for i, wl_i in enumerate(wavelengths):
        for wl_j in wavelengths[i + 1 :]:
            k_i = calibrations[wl_i].k_per_m
            k_j = calibrations[wl_j].k_per_m
            if np.isfinite(k_i) and np.isfinite(k_j) and k_j != 0:
                ratios[f"{wl_i:.0f}/{wl_j:.0f}"] = round(float(k_i / k_j), 4)
    return ratios
