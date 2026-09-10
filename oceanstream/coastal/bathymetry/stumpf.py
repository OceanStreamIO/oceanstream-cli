"""Stumpf log-ratio Satellite-Derived Bathymetry, blended with a coarse reference.

Stumpf, Holderied & Sinclair (2003)::

    H = m1 * log(n * R_blue) / log(n * R_green) - m0

The log ratio linearises the exponential attenuation of bottom-reflected light
across two visible bands with different absorption coefficients. Two free
parameters, fitted against depth soundings or a reference bathymetric surface.

Accuracy is reported as absolute error per depth stratum — MAE, RMSE, signed
bias — and never as R². A correlation coefficient over a depth gradient is
almost guaranteed to look good and says nothing about whether the retrieved
metres are the right metres.

Ported from ``kelp_observe/tools/lee_demo/bathymetry.py`` in Phase 1.3. The
prototype's module-level constants moved into
:class:`~oceanstream.coastal.config.BathymetryConfig`, and the
quadrat/lidar-specific function names were generalised: the physics does not
care whether the reference depths came from a diver, a sounder or a DTM.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy import ndimage

from oceanstream.coastal.config import BathymetryConfig

# --------------------------------------------------------------------------
# Fit result
# --------------------------------------------------------------------------

@dataclass
class StumpfFit:
    """Result of a Stumpf regression against a reference depth surface."""

    m0: float          # depth offset, metres
    m1: float          # slope
    n: float           # ratio scale factor, chosen per scene
    ref_source: str    # human-readable provenance
    n_pixels: int      # how many observations the fit used
    mae_m: float       # mean absolute error against the reference, metres
    rmse_m: float      # root-mean-square error, metres
    bias_m: float      # signed mean residual on the held-out split


@dataclass
class DepthValidation:
    """Per-stratum depth accuracy. Absolute error, not R²."""

    stratum_label: str
    n: int
    mae_m: float
    rmse_m: float
    bias_m: float


# --------------------------------------------------------------------------
# The log ratio
# --------------------------------------------------------------------------

def choose_ratio_scale(
    blue: np.ndarray,
    green: np.ndarray,
    valid: np.ndarray,
    margin: float = 1.5,
    config: BathymetryConfig | None = None,
) -> float:
    """Pick ``n`` so that ``n * R`` clears the pole at 1 on essentially every pixel.

    Stumpf's ``n`` is conventionally quoted as 1000, which assumes
    reflectances of order 0.01–0.1. ACOLITE ``rhos`` over dark coastal water
    routinely goes below 0.001, where ``n = 1000`` puts the denominator on the
    pole. Scale off the 1st percentile of the darker band instead, ignoring
    the extreme tail.
    """
    cfg = config or BathymetryConfig()
    sample = np.concatenate([
        blue[valid & np.isfinite(blue)].ravel(),
        green[valid & np.isfinite(green)].ravel(),
    ])
    sample = sample[sample > 0]
    if sample.size < 100:
        return 1000.0
    floor = float(np.percentile(sample, 1))
    return float(
        max(1000.0, margin * cfg.min_scaled_reflectance / max(floor, 1e-6))
    )


def stumpf_ratio(
    blue: np.ndarray,
    green: np.ndarray,
    n: float = 1000.0,
    config: BathymetryConfig | None = None,
) -> np.ndarray:
    """``log(n * R_blue) / log(n * R_green)``, NaN where the ratio is unsafe.

    Returns NaN rather than ±inf on the pole, so invalid pixels propagate as
    missing data instead of poisoning the least-squares fit downstream.
    """
    cfg = config or BathymetryConfig()
    nb = n * np.asarray(blue, dtype=np.float64)
    ng = n * np.asarray(green, dtype=np.float64)
    ok = (nb > cfg.min_scaled_reflectance) & (ng > cfg.min_scaled_reflectance)

    ratio = np.full(nb.shape, np.nan, dtype=np.float32)
    with np.errstate(invalid="ignore", divide="ignore"):
        np.divide(
            np.log(nb, where=ok, out=np.zeros_like(nb)),
            np.log(ng, where=ok, out=np.ones_like(ng)),
            out=ratio,
            where=ok,
            casting="unsafe",
        )

    lo, hi = cfg.ratio_valid_range
    return np.where(
        np.isfinite(ratio) & (ratio > lo) & (ratio < hi), ratio, np.nan
    )


# --------------------------------------------------------------------------
# Fitting
# --------------------------------------------------------------------------

def fit_stumpf(
    blue_reflectance: np.ndarray,
    green_reflectance: np.ndarray,
    reference_depth_m: np.ndarray,
    valid: np.ndarray,
    n: float | None = None,
    ref_source: str = "reference bathymetry",
    config: BathymetryConfig | None = None,
) -> StumpfFit:
    """Fit m0, m1 by least squares against a reference depth *raster*.

    The fit is restricted to ``config.fit_reference_range_m``. ``n`` defaults
    to a per-scene value from :func:`choose_ratio_scale`. Errors are reported
    on a held-out split, because in-sample OLS residuals have a mean of
    exactly zero by construction.
    """
    cfg = config or BathymetryConfig()
    if n is None:
        n = choose_ratio_scale(
            blue_reflectance, green_reflectance, valid, config=cfg
        )

    ratio = stumpf_ratio(blue_reflectance, green_reflectance, n, config=cfg)
    lo_ref, hi_ref = cfg.fit_reference_range_m
    fit_valid = (
        valid
        & np.isfinite(ratio)
        & (reference_depth_m > lo_ref)
        & (reference_depth_m < hi_ref)
    )

    n_pixels = int(fit_valid.sum())
    if n_pixels < cfg.min_fit_pixels:
        raise ValueError(
            f"Only {n_pixels} pixels for the Stumpf fit — need at least "
            f"{cfg.min_fit_pixels} (n={n:.0f}, ratio range "
            f"{cfg.ratio_valid_range}). Widen the AOI, check the reference "
            "depth surface, or check whether the atmospheric correction has "
            "left the visible bands too dark."
        )

    x = ratio[fit_valid].astype(np.float64)
    y = reference_depth_m[fit_valid].astype(np.float64)

    rng = np.random.default_rng(cfg.rng_seed)
    test = rng.random(x.size) < cfg.holdout_frac
    train = ~test
    if train.sum() < 100 or test.sum() < 50:
        train = np.ones_like(test)
        test = train

    # H = m1 * ratio - m0, solved as a linear least squares.
    m1, minus_m0 = np.polyfit(x[train], y[train], deg=1)
    residuals = (m1 * x[test] - (-minus_m0)) - y[test]

    return StumpfFit(
        m0=float(-minus_m0),
        m1=float(m1),
        n=float(n),
        ref_source=ref_source,
        n_pixels=n_pixels,
        mae_m=float(np.mean(np.abs(residuals))),
        rmse_m=float(np.sqrt(np.mean(residuals * residuals))),
        bias_m=float(np.mean(residuals)),
    )


def fit_stumpf_on_points(
    blue_reflectance: np.ndarray,
    green_reflectance: np.ndarray,
    valid: np.ndarray,
    rows: np.ndarray,
    cols: np.ndarray,
    depths_m: np.ndarray,
    groups: np.ndarray | None = None,
    n: float | None = None,
    ref_source: str = "in-situ point depths",
    config: BathymetryConfig | None = None,
) -> tuple[StumpfFit, dict]:
    """Fit m0, m1 against in-situ point depths instead of a reference raster.

    Preferred over :func:`fit_stumpf` when the reference DTM is coarser than
    the depth structure being retrieved — EMODnet at ~116 m cannot resolve a
    cliff-backed reef where the sample points span 7–16 m over ~100 m.

    Two things make this fragile, and both are handled explicitly:

    * **Pseudo-replication.** Several points fall in one pixel, so they are
      collapsed to one observation per pixel (mean depth) before fitting.
      Otherwise repeated sampling of the same pixel inflates n.
    * **Circularity.** Fitting and validating on the same handful of points
      would make the accuracy number meaningless. When ``groups`` is given
      (e.g. transect ids), error statistics come from leave-one-group-out
      cross-validation, which is the only non-circular number available.

    Returns ``(fit, diagnostics)``; ``diagnostics`` carries the caveats —
    unique pixel count, distinct depth levels, and the CV scheme actually
    used, which is stated plainly as optimistic when no groups were supplied.
    """
    cfg = config or BathymetryConfig()
    if n is None:
        n = choose_ratio_scale(
            blue_reflectance, green_reflectance, valid, config=cfg
        )

    ratio_map = stumpf_ratio(blue_reflectance, green_reflectance, n, config=cfg)
    ratio = ratio_map[rows, cols]
    ok = np.isfinite(ratio) & np.isfinite(depths_m) & valid[rows, cols]
    if ok.sum() < 4:
        raise ValueError(
            f"Only {int(ok.sum())} points have a usable log ratio "
            f"(n={n:.0f}). The visible bands may still be too dark, or the "
            "points may fall on masked pixels."
        )

    # Collapse to one observation per pixel — several points share a pixel.
    keys = (rows[ok].astype(np.int64) << 32) | cols[ok].astype(np.int64)
    uniq, inverse = np.unique(keys, return_inverse=True)
    counts = np.bincount(inverse)
    x = np.bincount(inverse, weights=ratio[ok]) / counts
    y = np.bincount(inverse, weights=depths_m[ok]) / counts
    grp = (
        np.bincount(inverse, weights=groups[ok]) / counts
        if groups is not None
        else np.zeros_like(y)
    )

    levels = np.unique(np.round(y, 1))
    if levels.size < 2:
        raise ValueError(
            f"Point depths collapse to {levels.size} distinct level(s) — "
            "cannot fit a two-parameter Stumpf regression."
        )
    span = float(levels.max() - levels.min())
    if span < cfg.min_depth_span_m:
        raise ValueError(
            f"Calibration depths span only {span:.1f} m (levels "
            f"{list(levels)}) — need >= {cfg.min_depth_span_m} m. A "
            "two-parameter fit over a narrow range cannot be extrapolated "
            "across the AOI. Usually means the shallow points fell on masked "
            "or clipped pixels."
        )

    m1, minus_m0 = np.polyfit(x, y, deg=1)

    # Leave-one-group-out CV. With transects as groups the held-out fold is
    # spatially disjoint from the training folds.
    cv_resid: list[float] = []
    scheme = "none"
    unique_groups = np.unique(grp)
    if groups is not None and unique_groups.size >= 2:
        scheme = f"leave-one-of-{unique_groups.size}-groups-out"
        for g in unique_groups:
            tr, te = grp != g, grp == g
            if tr.sum() < 2 or te.sum() < 1:
                continue
            gm1, gminus_m0 = np.polyfit(x[tr], y[tr], deg=1)
            cv_resid.extend((gm1 * x[te] - (-gminus_m0)) - y[te])
    if not cv_resid:
        scheme = "in-sample (no groups) — errors are optimistic"
        cv_resid = list((m1 * x - (-minus_m0)) - y)

    resid = np.asarray(cv_resid, dtype=np.float64)
    fit = StumpfFit(
        m0=float(-minus_m0),
        m1=float(m1),
        n=float(n),
        ref_source=ref_source,
        n_pixels=int(uniq.size),
        mae_m=float(np.mean(np.abs(resid))),
        rmse_m=float(np.sqrt(np.mean(resid**2))),
        bias_m=float(np.mean(resid)),
    )
    diagnostics = {
        "n_points": int(ok.sum()),
        "n_unique_pixels": int(uniq.size),
        "distinct_depth_levels": [float(v) for v in levels],
        "depth_span_m": span,
        "validation_scheme": scheme,
        "caveat": (
            "Local calibration extrapolated across the AOI — the fit only "
            "saw the depth range and seabed types the points cover."
        ),
    }
    return fit, diagnostics


def apply_stumpf(
    blue_reflectance: np.ndarray,
    green_reflectance: np.ndarray,
    fit: StumpfFit,
    valid: np.ndarray,
    config: BathymetryConfig | None = None,
) -> np.ndarray:
    """Apply a fitted Stumpf regression to produce a per-pixel depth map.

    Depths outside ``config.depth_valid_range_m`` are returned as NaN — a
    retrieval that lands outside the AOI's physical range is a failure, not a
    value.
    """
    cfg = config or BathymetryConfig()
    ratio = stumpf_ratio(blue_reflectance, green_reflectance, fit.n, config=cfg)
    depth = fit.m1 * ratio - fit.m0

    lo, hi = cfg.depth_valid_range_m
    usable = valid & np.isfinite(depth) & (depth >= lo) & (depth <= hi)
    return np.where(usable, depth, np.nan).astype(np.float32)


# --------------------------------------------------------------------------
# Blend with a coarse reference surface
# --------------------------------------------------------------------------

def blend_stumpf(
    stumpf_depth: np.ndarray,
    reference_depth_m: np.ndarray,
    fit: StumpfFit,
    config: BathymetryConfig | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """Inverse-variance weighted blend of Stumpf and a coarse reference DTM.

    Reduces Stumpf noise in optically-good pixels and prevents nonsense depths
    where the log ratio degenerates — bright reflective bottoms, and sun-glint
    pixels that survived the mask.

    Returns ``(blended_depth, sigma_H)``.
    """
    cfg = config or BathymetryConfig()

    # Stumpf uncertainty taken as the RMSE of the fit, uniform across the map.
    stumpf_sigma = np.full_like(stumpf_depth, fit.rmse_m, dtype=np.float32)

    if cfg.blend_smooth_sigma_px > 0:
        stumpf_depth = ndimage.gaussian_filter(
            np.nan_to_num(stumpf_depth, nan=0.0), sigma=cfg.blend_smooth_sigma_px
        )

    inv_var_stumpf = 1.0 / (stumpf_sigma**2)
    inv_var_ref = 1.0 / (cfg.reference_sigma_m**2)
    w_total = inv_var_stumpf + inv_var_ref

    blended = (
        stumpf_depth * inv_var_stumpf + reference_depth_m * inv_var_ref
    ) / w_total
    sigma_h = np.sqrt(1.0 / w_total)
    return blended.astype(np.float32), sigma_h.astype(np.float32)


# --------------------------------------------------------------------------
# Diagnostics
# --------------------------------------------------------------------------

def depth_map_diagnostics(
    depth_m: np.ndarray,
    calibration_span_m: float,
    config: BathymetryConfig | None = None,
) -> dict:
    """Check that the retrieved depth map actually varies.

    A fit calibrated on a few pixels over a narrow depth range can come back
    with a near-zero slope, mapping the whole scene into a few metres.
    Validation then looks excellent for the wrong reason: if most reference
    points sit in one stratum, predicting the modal depth scores well. This
    makes that failure visible instead of flattering.
    """
    cfg = config or BathymetryConfig()
    finite = depth_m[np.isfinite(depth_m)]
    if finite.size == 0:
        return {
            "coverage_frac": 0.0,
            "collapsed": True,
            "reason": "no valid pixels",
        }

    p5, p95 = float(np.percentile(finite, 5)), float(np.percentile(finite, 95))
    span = p95 - p5
    collapsed = span < cfg.min_span_fraction * calibration_span_m
    return {
        "coverage_frac": float(finite.size / depth_m.size),
        "retrieved_p5_m": p5,
        "retrieved_p95_m": p95,
        "retrieved_span_m": span,
        "calibration_span_m": float(calibration_span_m),
        "collapsed": bool(collapsed),
        "reason": (
            f"retrieved depth spans only {span:.1f} m against a "
            f"{calibration_span_m:.1f} m calibration range — the fitted slope "
            "is near zero, so the map is effectively constant"
            if collapsed
            else ""
        ),
    }


def stratum_balance(
    depth_validation: list[DepthValidation],
    max_dominant_frac: float = 0.75,
) -> dict:
    """Flag when one depth stratum dominates the validation set.

    With an imbalanced set a constant predictor scores well, so pooled MAE
    stops being evidence that the retrieval tracks depth at all.
    """
    counts = {
        r.stratum_label: r.n
        for r in depth_validation
        if not r.stratum_label.lower().startswith("all")
    }
    total = sum(counts.values())
    if total == 0:
        return {"balanced": False, "counts": counts, "dominant_frac": 1.0}
    dominant = max(counts.values()) / total
    return {
        "balanced": bool(dominant <= max_dominant_frac),
        "counts": counts,
        "dominant_frac": float(dominant),
    }


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------

def _score_strata(
    residuals: np.ndarray,
    strata: list[tuple[str, np.ndarray]],
) -> list[DepthValidation]:
    """Reduce residuals to per-stratum MAE / RMSE / bias."""
    out: list[DepthValidation] = []
    for label, mask in strata:
        r = residuals[mask & np.isfinite(residuals)]
        if r.size == 0:
            out.append(
                DepthValidation(
                    label, 0, float("nan"), float("nan"), float("nan")
                )
            )
            continue
        out.append(
            DepthValidation(
                stratum_label=label,
                n=int(r.size),
                mae_m=float(np.mean(np.abs(r))),
                rmse_m=float(np.sqrt(np.mean(r * r))),
                bias_m=float(np.mean(r)),
            )
        )
    return out


def validate_against_points(
    predicted_depth: np.ndarray,
    point_rows: np.ndarray,
    point_cols: np.ndarray,
    point_depth_m: np.ndarray,
    config: BathymetryConfig | None = None,
) -> list[DepthValidation]:
    """Sample the depth map at in-situ point pixels and score per stratum.

    Parameters
    ----------
    predicted_depth
        (H, W) retrieved depth in metres.
    point_rows, point_cols
        (N,) pixel indices of the in-situ positions, in the same raster.
    point_depth_m
        (N,) measured depth at each point.
    config
        ``stratum_boundary_m`` sets the shallow/deep cutoff.
    """
    cfg = config or BathymetryConfig()
    boundary = cfg.stratum_boundary_m
    residuals = predicted_depth[point_rows, point_cols] - point_depth_m

    return _score_strata(
        residuals,
        [
            (f"shallow (<{boundary:g} m measured)", point_depth_m < boundary),
            (f"deep (>={boundary:g} m measured)", point_depth_m >= boundary),
            ("all points", np.ones_like(point_depth_m, dtype=bool)),
        ],
    )


def checkerboard_blocks(
    shape: tuple[int, int],
    config: BathymetryConfig | None = None,
) -> np.ndarray:
    """Checkerboard of square blocks; True = calibration, False = held out.

    A random per-pixel split would leak: neighbouring pixels see the same
    seabed, so the held-out set would not be independent. Block size comes
    from ``config.checkerboard_block_px`` and must exceed the depth
    correlation scale at the site.
    """
    cfg = config or BathymetryConfig()
    rows, cols = np.indices(shape)
    block = cfg.checkerboard_block_px
    return np.asarray(((rows // block) + (cols // block)) % 2 == 0)


def validate_against_reference_blocks(
    predicted_depth: np.ndarray,
    reference_depth_m: np.ndarray,
    valid: np.ndarray,
    calibration_blocks: np.ndarray,
    config: BathymetryConfig | None = None,
) -> list[DepthValidation]:
    """Score a depth map on the blocks the fit never saw.

    Pass the *same* ``calibration_blocks`` mask that restricted the fit. This
    function inverts it, so a caller cannot accidentally validate on the
    calibration set and report a circular result.

    Scoring is restricted to ``config.fit_reference_range_m``; the lower bound
    matters because the Hedley deglint assumes no SWIR water-leaving signal,
    which bright sand in very shallow water violates.
    """
    cfg = config or BathymetryConfig()
    if calibration_blocks.shape != predicted_depth.shape:
        raise ValueError(
            f"calibration_blocks {calibration_blocks.shape} does not match "
            f"the depth grid {predicted_depth.shape} — the checkerboard must "
            "be built from the same raster the fit used."
        )

    lo, hi = cfg.fit_reference_range_m
    boundary = cfg.stratum_boundary_m
    scored = (
        valid
        & ~calibration_blocks
        & np.isfinite(predicted_depth)
        & np.isfinite(reference_depth_m)
        & (reference_depth_m >= lo)
        & (reference_depth_m <= hi)
    )

    residuals = np.where(scored, predicted_depth - reference_depth_m, np.nan)
    return _score_strata(
        residuals,
        [
            (
                f"shallow (<{boundary:g} m reference)",
                scored & (reference_depth_m < boundary),
            ),
            (
                f"deep (>={boundary:g} m reference)",
                scored & (reference_depth_m >= boundary),
            ),
            ("all held-out blocks", scored),
        ],
    )
