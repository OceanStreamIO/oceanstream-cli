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
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import Any

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

    #: Names of the fit-quality checks this band failed, set by
    #: :func:`calibrate_bands`. Empty means the fit is usable.
    trust_failures: tuple[str, ...] = ()
    solar_zenith_deg: float = 35.0
    k_standard_error: float | None = None
    bin_support: list[dict[str, Any]] = field(default_factory=list)
    assessable: bool = True

    @property
    def trustworthy(self) -> bool:
        """False when any fit-quality check failed.

        Branch on this before using ``k_per_m``. The slope is always a number
        and a number is not a measurement; ``trust_failures`` is what separates
        the two.
        """
        return not self.trust_failures

    @property
    def k_pure_water_floor(self) -> float:
        """Physical lower bound on ``k_per_m``: pure water alone."""
        return float(lee.kb_pure_water(self.wavelength_nm, self.solar_zenith_deg))

    @property
    def k_below_pure_water_floor(self) -> bool:
        """True when the fit returned less attenuation than pure water has."""
        return bool(np.isfinite(self.k_per_m) and self.k_per_m < self.k_pure_water_floor)

    @property
    def k_effective(self) -> float:
        """``k_per_m`` floored at pure water — the value consumers should use.

        A fit below the floor is not a measurement of attenuation. Possible
        causes include a spatially mismatched
        deep-water reference or a band with no bottom signal left to fit. A
        uniform additive offset cancels when subtracted from both signal and
        reference. Reported alongside the raw fit rather
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

    edges = np.arange(cfg.depth_min_m, cfg.depth_max_m + cfg.depth_bin_m, cfg.depth_bin_m)
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


def _assess_band(calibration: BandCalibration, config: AttenuationConfig) -> tuple[str, ...]:
    """Which fit-quality checks a band failed.

    Every input here was already computed by the fit and stored on the
    calibration. Until now they were advisory, documented as something a
    caller ought to inspect — which is an instruction addressed to a human
    reading a docstring, in a pipeline that runs unattended.
    """
    if not np.isfinite(calibration.k_per_m):
        return ("no_fit",)

    failures: list[str] = []
    if not all(
        np.isfinite(v)
        for v in (
            calibration.r_squared,
            calibration.quantile_spread,
            calibration.frac_nonpositive_residual,
        )
    ):
        failures.append("fit_quality_unverified")
    if calibration.k_per_m <= 0.0:
        failures.append("k_nonpositive")  # attenuation cannot be negative

    if np.isfinite(calibration.r_squared) and calibration.r_squared < config.min_r_squared:
        failures.append("low_r_squared")

    spread = calibration.quantile_spread
    if (
        np.isfinite(spread)
        and calibration.k_per_m != 0.0
        and spread / abs(calibration.k_per_m) > config.max_quantile_spread_ratio
    ):
        failures.append("quantile_unstable")

    frac = calibration.frac_nonpositive_residual
    if np.isfinite(frac) and frac > config.max_nonpositive_residual:
        failures.append("reference_suspect")

    return tuple(failures)


def depth_correlated_artefact(
    calibrations: dict[float, BandCalibration],
    config: AttenuationConfig | None = None,
) -> str | None:
    """Detect a regression tracking something other than bottom light.

    Pure water alone extinguishes the bottom signal in the near-infrared
    within about a metre, so those bands cannot carry a real depth decay
    across the fit window. If one of them nevertheless fits *better* than
    every band that can, the regression has found a gradient that merely
    correlates with depth — land adjacency, an aerosol trend, residual glint.
    Such a gradient is common-mode, so it contaminates the usable bands too
    and the whole scene's fit is void, not just the offending band.

    Returns the reason, or None when the ordering is physically sound.
    """
    cfg = config or AttenuationConfig()
    opaque = {
        nm: c.r_squared
        for nm, c in calibrations.items()
        if c.k_pure_water_floor >= cfg.opaque_floor_min_per_m and np.isfinite(c.r_squared)
    }
    carrying = {
        nm: c.r_squared
        for nm, c in calibrations.items()
        if c.k_pure_water_floor < cfg.opaque_floor_min_per_m and np.isfinite(c.r_squared)
    }
    if not opaque or not carrying:
        return None

    opaque_nm = max(opaque, key=lambda nm: opaque[nm])
    carrying_nm = max(carrying, key=lambda nm: carrying[nm])
    if opaque[opaque_nm] <= carrying[carrying_nm]:
        return None

    return (
        f"{opaque_nm:.0f} nm fits better than any bottom-carrying band "
        f"(R2 {opaque[opaque_nm]:.3f} against {carrying[carrying_nm]:.3f} at "
        f"{carrying_nm:.0f} nm), but pure water extinguishes the bottom signal "
        "there. The regression is tracking a depth-correlated gradient rather "
        "than attenuation, and that gradient is common-mode across the scene."
    )


def calibrate_bands(
    reflectance: dict[float, np.ndarray],
    depth: np.ndarray,
    water_mask: np.ndarray,
    deep_mask: np.ndarray,
    config: AttenuationConfig | None = None,
    *,
    epsilon_rhos: float | None = None,
    solar_zenith_deg: float = 35.0,
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
        One :class:`BandCalibration` per input wavelength. Check
        ``trustworthy`` before using ``k_per_m`` — a fit can be numerically
        clean and physically void, and the checks behind that flag are the
        only thing separating the two.
    """
    cfg = config or AttenuationConfig()
    reference = deep_water_reference(reflectance, deep_mask, config=cfg)
    results: dict[float, BandCalibration] = {}

    for wavelength, band in reflectance.items():
        band_cfg = cfg
        fit_mask = water_mask.copy()
        # Restrict inference to depths where the band can retain a detectable
        # contrast. Beyond 700 nm the water table is not a physical reference;
        # those regressions remain negative controls only.
        if epsilon_rhos is not None and wavelength <= 700:
            floor = float(lee.kb_pure_water(wavelength, solar_zenith_deg))
            signal = band[water_mask & np.isfinite(band)] - reference[wavelength]
            amplitude = float(np.quantile(signal, 0.95)) if signal.size else 0.0
            max_depth = (
                np.log(amplitude / (2 * epsilon_rhos)) / floor
                if amplitude > 2 * epsilon_rhos
                else cfg.depth_min_m
            )
            band_cfg = replace(
                cfg, depth_max_m=max(cfg.depth_min_m, min(cfg.depth_max_m, max_depth))
            )
            fit_mask &= band - reference[wavelength] > 2 * epsilon_rhos
        k, intercept, r2, n_bins, n_px = fit_band_attenuation(
            band, depth, fit_mask, reference[wavelength], cfg.quantile, band_cfg
        )

        residual = band - reference[wavelength]
        considered = water_mask & np.isfinite(residual)
        nonpositive = (
            float((residual[considered] <= 0).mean()) if considered.any() else float("nan")
        )

        by_quantile: dict[str, float] = {}
        for q in cfg.sensitivity_quantiles:
            k_q, *_ = fit_band_attenuation(
                band, depth, fit_mask, reference[wavelength], q, band_cfg
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
            depth_range_m=(band_cfg.depth_min_m, band_cfg.depth_max_m),
            deep_water_reference=reference[wavelength],
            frac_nonpositive_residual=nonpositive,
            k_by_quantile=by_quantile,
            solar_zenith_deg=solar_zenith_deg,
            assessable=wavelength <= 700,
        )
        support = []
        for lo in np.arange(band_cfg.depth_min_m, band_cfg.depth_max_m, cfg.depth_bin_m):
            chosen = (
                water_mask & np.isfinite(residual) & (depth >= lo) & (depth < lo + cfg.depth_bin_m)
            )
            above = chosen & fit_mask & (residual > 0)
            support.append(
                {
                    "depth_m": float(lo + cfg.depth_bin_m / 2),
                    "n_total": int(chosen.sum()),
                    "n_used": int(above.sum()),
                    "n_censored": int((chosen & ~above).sum()),
                    "log_quantile": float(np.quantile(np.log(residual[above]), cfg.quantile))
                    if above.sum() >= cfg.min_pixels_per_bin
                    else None,
                }
            )
        results[wavelength].bin_support = support
        supported = [b for b in support if b["log_quantile"] is not None]
        if len(supported) > 2 and np.isfinite(k):
            x = np.array([b["depth_m"] for b in supported], dtype=float)
            y = np.array([b["log_quantile"] for b in supported], dtype=float)
            results[wavelength].k_standard_error = float(
                np.sqrt(
                    np.sum((y - (intercept - k * x)) ** 2)
                    / (len(x) - 2)
                    / np.sum((x - x.mean()) ** 2)
                )
            )
        results[wavelength].trust_failures = _assess_band(results[wavelength], cfg)
        logger.info(
            "%.0f nm: k = %.4f /m (R2 %.3f, %d bins, %d px, quantile spread %.4f)%s",
            wavelength,
            k,
            r2,
            n_bins,
            n_px,
            results[wavelength].quantile_spread,
            (
                ""
                if results[wavelength].trustworthy
                else " — REJECTED: " + ", ".join(results[wavelength].trust_failures)
            ),
        )

    artefact = depth_correlated_artefact(results, cfg)
    if artefact is not None:
        logger.warning("Scene-wide attenuation fit rejected: %s", artefact)
        for calibration in results.values():
            calibration.trust_failures = (
                *calibration.trust_failures,
                "scene_depth_gradient",
            )
    return results


def calibrations_from_payload(payload: Mapping[str, Any]) -> dict[float, BandCalibration]:
    """Read versioned fits without losing QC; legacy bare slopes are unverified."""
    nested = payload.get("attenuation", payload)
    if "regions" in nested:
        if len(nested["regions"]) != 1:
            raise ValueError("Select a region from this multi-region attenuation document.")
        nested = next(iter(nested["regions"].values()))
    section = nested.get("bands") or nested.get("k_by_band") or {}
    result = {}
    for nm, value in section.items():
        data = dict(value) if isinstance(value, dict) else {"k_per_m": value}
        failures = tuple(data.get("trust_failures", ("fit_quality_unverified",)))
        defaults: dict[str, Any] = dict(
            wavelength_nm=float(nm),
            k_per_m=float("nan"),
            intercept=float("nan"),
            r_squared=float("nan"),
            n_bins=0,
            n_pixels=0,
            depth_range_m=(0.0, 0.0),
            deep_water_reference=float("nan"),
            frac_nonpositive_residual=float("nan"),
        )
        defaults.update(
            {k: v for k, v in data.items() if k in BandCalibration.__dataclass_fields__}
        )
        for key in (
            "k_per_m",
            "intercept",
            "r_squared",
            "deep_water_reference",
            "frac_nonpositive_residual",
        ):
            if defaults[key] is None:
                defaults[key] = float("nan")
        defaults["depth_range_m"] = tuple(defaults["depth_range_m"])
        required_finite = (
            "k_per_m",
            "intercept",
            "r_squared",
            "deep_water_reference",
            "frac_nonpositive_residual",
        )
        if (
            any(not np.isfinite(defaults[key]) for key in required_finite)
            or defaults["n_bins"] < 2
            or defaults["n_pixels"] <= 0
        ):
            failures = tuple(sorted(set(failures) | {"fit_quality_unverified"}))
        defaults["trust_failures"] = failures
        result[float(nm)] = BandCalibration(**defaults)
    return result


def lyzenga_ratios(
    calibrations: dict[float, BandCalibration],
) -> dict[str, float]:
    """``k_i / k_j`` for every band pair — the depth-invariant index coefficients.

    Ratios provide a physical cross-check on the absolute slopes. A uniform
    additive offset cancels separately in every band when L_inf is subtracted;
    it does not preferentially preserve ratios while changing the fitted k.
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
