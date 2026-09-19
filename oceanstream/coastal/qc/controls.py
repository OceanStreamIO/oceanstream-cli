"""Independent-terrain controls and comparison of local and offshore optics.

Terrain is a substrate proxy, not ground truth. Thresholds are preserved from
lee_demo's preregistered control; do not tune them to the retrieved image.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from oceanstream.coastal.bathymetry.terrain import terrain_classes
from oceanstream.coastal.inversion import lee
from oceanstream.coastal.optics.attenuation import BandCalibration
from oceanstream.coastal.optics.qaa import SceneIOPs

PREDICTED_BARE_RHO_B = (0.179, 0.268)
PREDICTED_ALGAE_RHO_B = (0.02, 0.05)
MIN_BRIGHTNESS_RATIO = 3.0
MAX_DEPTH_TREND_PER_M = 0.010
DEPTH_BINS_M = ((2, 5), (5, 8), (8, 11), (11, 14), (14, 18))


def _summarise(values: np.ndarray) -> dict[str, Any]:
    values = values[np.isfinite(values)]
    if not values.size:
        return {"n": 0}
    q = np.percentile(values, [25, 50, 75])
    return {
        "n": int(values.size),
        "median_rho_b": float(q[1]),
        "p25": float(q[0]),
        "p75": float(q[2]),
    }


def _depth_trend(depth: np.ndarray, values: np.ndarray) -> dict[str, Any]:
    if depth.size < 20 or np.ptp(depth) < 1.0:
        return {"n": int(depth.size), "slope_per_m": None, "depth_invariant": None}
    slope, intercept = np.polyfit(depth, values, 1)
    return {
        "n": int(depth.size),
        "slope_per_m": float(slope),
        "intercept": float(intercept),
        "depth_invariant": bool(abs(slope) < MAX_DEPTH_TREND_PER_M),
    }


def sand_control(
    rho_b: np.ndarray,
    wavelengths_nm: np.ndarray,
    reference_depth: np.ndarray,
    valid: np.ndarray,
    pixel_size_m: float,
) -> dict[str, Any]:
    terrain = terrain_classes(reference_depth, pixel_size_m)
    in_range = (
        valid & np.isfinite(reference_depth) & (reference_depth > 2) & (reference_depth <= 18)
    )
    bands = {}
    for i, wl in enumerate(wavelengths_nm):
        if not 400 <= wl <= 700:
            continue
        classes = {}
        for label in ("flat", "rugose"):
            selected = in_range & terrain[label] & np.isfinite(rho_b[i])
            values, depths = rho_b[i][selected], reference_depth[selected]
            classes[label] = {
                **_summarise(values),
                "depth_trend": _depth_trend(depths, values),
                "by_depth_bin": {
                    f"{lo}-{hi}m": _summarise(values[(depths >= lo) & (depths < hi)])
                    for lo, hi in DEPTH_BINS_M
                },
            }
        flat, rugose = classes["flat"].get("median_rho_b"), classes["rugose"].get("median_rho_b")
        ratio = flat / rugose if flat is not None and rugose is not None and rugose > 0 else None
        bands[str(wl)] = {
            "classes": classes,
            "flat_in_bare_window": PREDICTED_BARE_RHO_B[0] <= flat <= PREDICTED_BARE_RHO_B[1]
            if flat is not None and abs(wl - 561.0) <= 15.0
            else None,
            "brightness_ratio": ratio,
            "ratio_meets_threshold": ratio >= MIN_BRIGHTNESS_RATIO if ratio is not None else None,
        }
    return {
        "status": "diagnostic_only",
        "bands": bands,
        "prediction": {
            "band_nm": 561.0,
            "bare_rho_b": PREDICTED_BARE_RHO_B,
            "algae_rho_b": PREDICTED_ALGAE_RHO_B,
            "min_brightness_ratio": MIN_BRIGHTNESS_RATIO,
            "max_depth_trend_per_m": MAX_DEPTH_TREND_PER_M,
        },
        "caveat": (
            "Terrain is an indirect substrate proxy. "
            "Baltic reference spectra are not site-specific ground truth."
        ),
    }


def compare_with_qaa(
    calibrations: dict[float, BandCalibration], iops: SceneIOPs, solar_zenith_deg: float
) -> dict[str, Any]:
    inv_cos = 1 / np.cos(np.deg2rad(lee._subsurface_solar_zenith_deg(solar_zenith_deg)))
    rows = []
    for wl, calibration in sorted(calibrations.items()):
        if wl > 700 or not calibration.trustworthy or not np.isfinite(calibration.k_per_m):
            continue
        idx = int(np.argmin(np.abs(iops.wavelengths_nm - wl)))
        a, bb = iops.a[idx], iops.bb[idx]
        model = float((inv_cos + lee._du_bottom(bb / (a + bb))) * (a + bb))
        rows.append(
            {
                "wavelength_nm": wl,
                "empirical_k": calibration.k_per_m,
                "qaa_k": model,
                "difference_per_m": calibration.k_per_m - model,
                "ratio": calibration.k_per_m / model if model > 0 else None,
            }
        )
    return {
        "per_band": rows,
        "status": "diagnostic_only",
        "caveat": (
            "Disagreement can reflect water-mass mismatch or either model's error; "
            "it does not identify the cause."
        ),
    }
