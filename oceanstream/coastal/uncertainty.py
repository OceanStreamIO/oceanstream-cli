"""Conditional propagation, with missing model terms explicitly recorded."""

from __future__ import annotations

from typing import Any

import numpy as np

from oceanstream.coastal.config import DetectabilityConfig, UncertaintyConfig
from oceanstream.coastal.detectability import SceneDetectability, seabed_par_fraction
from oceanstream.coastal.inversion.lee import sigma_rho_b
from oceanstream.coastal.optics.attenuation import BandCalibration
from oceanstream.coastal.optics.qaa import SceneIOPs


def propagate(
    rho_b: np.ndarray,
    depth: np.ndarray,
    depth_sigma: np.ndarray,
    iops: SceneIOPs,
    wavelengths: np.ndarray,
    valid: np.ndarray,
    theta: float,
    config: UncertaintyConfig,
    epsilon: float | None,
    par_config: DetectabilityConfig,
) -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
    reflectance_sigma = epsilon / np.pi if epsilon is not None else None
    rho_sigma = sigma_rho_b(rho_b, depth, depth_sigma, iops, theta, reflectance_sigma)
    # Numerical depth derivative on the same PAR function, in fraction per m.
    step = 0.01
    upper = seabed_par_fraction(depth + step, iops.kd, wavelengths, theta, par_config)
    lower = seabed_par_fraction(
        np.maximum(0, depth - step), iops.kd, wavelengths, theta, par_config
    )
    par_sigma = np.abs(upper - lower) / (2 * step) * depth_sigma
    missing = []
    if epsilon is None:
        missing.append("reflectance_noise_unmeasured")
    if config.qaa_relative_sigma is None:
        missing.append("qaa_model_uncertainty_missing")
    else:
        relative = config.qaa_relative_sigma
        # A supplied model discrepancy is propagated as multiplicative output
        # error, not invented as a covariance between QAA a and bb parameters.
        rho_sigma = np.sqrt(rho_sigma**2 + (relative * rho_b) ** 2)
        par = seabed_par_fraction(depth, iops.kd, wavelengths, theta, par_config)
        par_sigma = np.sqrt(par_sigma**2 + (relative * par) ** 2)
    return (
        rho_sigma,
        np.where(valid, par_sigma, np.nan),
        {
            "kind": "conditional_one_sigma",
            "missing_terms": missing,
            "model_error_source": config.source,
            "assumption": (
                "Depth, reflectance and independently estimated model discrepancy are "
                "uncorrelated; no complete confidence interval is claimed."
            ),
        },
    )


def detection_sigma(
    detect: SceneDetectability,
    bands: dict[float, BandCalibration],
    depth_sigma: np.ndarray,
    config: UncertaintyConfig,
    n_noise_blocks: int = 0,
) -> tuple[np.ndarray, dict[str, Any]]:
    missing = []
    c = bands.get(detect.best_band_nm) if detect.best_band_nm is not None else None
    if c is None or c.k_standard_error is None or c.k_per_m <= 0:
        return np.full_like(depth_sigma, np.nan), {
            "missing_terms": ["attenuation_error_unavailable"]
        }
    sigma_k = c.k_standard_error
    if config.empirical_relative_sigma is None:
        missing.append("attenuation_model_uncertainty_missing")
    else:
        sigma_k = float(np.hypot(sigma_k, c.k_per_m * config.empirical_relative_sigma))
    variance_z = (detect.z_max_m / c.k_per_m * sigma_k) ** 2
    if n_noise_blocks > 1:
        variance_z += 1 / (2 * (n_noise_blocks - 1) * c.k_per_m**2)
    else:
        missing.append("noise_estimate_uncertainty_missing")
    # Endmember uncertainty is represented by explicit contrast scenarios,
    # separately from this conditional statistical budget.
    return np.sqrt(depth_sigma**2 + variance_z), {
        "kind": "conditional_one_sigma",
        "missing_terms": missing,
        "conditional_on": "declared substrate contrast",
        "z_max_sigma_m": float(np.sqrt(variance_z)),
        "model_error_source": config.source,
    }
