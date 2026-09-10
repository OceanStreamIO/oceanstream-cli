"""Closed-form constrained Lee inversion.

Given depth H and scene-mean IOPs (from :func:`oceanstream.coastal.fit_scene_iops`),
the Lee 1998/1999 semi-analytical shallow-water model reduces to a linear
equation in effective benthic reflectance ``rho_b``::

    r_rs(λ) = r_rs_deep(λ) * [1 - exp(-k_c * H)]
            + (rho_b(λ) / pi) * exp(-k_b * H)

where::

    k_c = (1/cos(theta_w) + D_u^C) * (a + b_b)
    k_b = (1/cos(theta_w) + D_u^B) * (a + b_b)

``theta_w`` is the subsurface solar zenith; ``D_u^C`` and ``D_u^B`` are Lee's
path-elongation factors for the water-column and bottom-scattered light paths.

Solve for ``rho_b``::

    rho_b(λ) = pi * [r_rs(λ) - r_rs_deep(λ) * (1 - exp(-k_c * H))] * exp(+k_b * H)

That is what :func:`invert_scene` returns. Uncertainty is propagated first-order
from ``sigma_H`` (bathymetry uncertainty) — the dominant term at the reference
Sesimbra site.

Terminology, per ``kelp_observe/docs/lee_inversion/conclusion.md`` §10: the
retrieved quantity is ``effective_benthic_reflectance`` (symbol ``rho_b``), not
"bottom reflectance". Kelp is a 3-D canopy, not a Lambertian bottom.
"""

from __future__ import annotations

import numpy as np

from oceanstream.coastal.optics import water
from oceanstream.coastal.optics.qaa import SceneIOPs

# --------------------------------------------------------------------------
# Path-elongation factors
# --------------------------------------------------------------------------
#
# Lee et al. 1999 eqs 4a–4b (single-band):
#     D_u^C = 1.03 * sqrt(1 + 2.4 * u)      # water-column
#     D_u^B = 1.04 * sqrt(1 + 5.4 * u)      # bottom-scattered
# where u = b_b / (a + b_b).


def _du_column(u: np.ndarray | float) -> np.ndarray | float:
    return 1.03 * np.sqrt(1.0 + 2.4 * u)


def _du_bottom(u: np.ndarray | float) -> np.ndarray | float:
    return 1.04 * np.sqrt(1.0 + 5.4 * u)


def _subsurface_solar_zenith_deg(solar_zenith_deg: float) -> float:
    """Snell's law under a flat surface, n_water = 1.34."""
    sin_theta_a = np.sin(np.deg2rad(solar_zenith_deg))
    return float(np.rad2deg(np.arcsin(sin_theta_a / 1.34)))


def kb_pure_water(
    wavelength_nm: np.ndarray | float,
    solar_zenith_deg: float = 35.0,
) -> np.ndarray | float:
    """Two-way bottom attenuation k_b for pure water alone, m^-1.

    This is the physical floor on any fitted or retrieved k_b: adding any
    dissolved or particulate constituent can only increase attenuation. An
    empirical fit that lands below this is measuring something other than
    attenuation — usually an uncorrected additive offset, or a band with no
    bottom signal left to fit.
    """
    a_w = np.asarray(water.a_water(wavelength_nm), dtype=float)
    bb_w = np.asarray(water.bb_water(wavelength_nm), dtype=float)
    beam_c = a_w + bb_w
    u = bb_w / beam_c
    theta_w = np.deg2rad(_subsurface_solar_zenith_deg(solar_zenith_deg))
    k_b = (1.0 / np.cos(theta_w) + _du_bottom(u)) * beam_c
    return float(k_b) if np.isscalar(wavelength_nm) or k_b.ndim == 0 else k_b


def two_way_to_downwelling_factor(
    u: np.ndarray | float,
    solar_zenith_deg: float = 35.0,
) -> np.ndarray | float:
    """Ratio ``k_b / K_d`` — how much of the two-way path is the downward leg.

    ``k_b = (1/cos(theta_w) + D_u^B) * (a + b_b)`` is the *round trip*: light
    down to the seabed and back up to the sensor. Photosynthesis only sees the
    downward leg, ``K_d = (a + b_b) / cos(theta_w)``. Dividing gives::

        k_b / K_d = (1/cos(theta_w) + D_u^B) * cos(theta_w)

    which lands near 2 for typical geometry, hence the common shorthand of
    halving. The shorthand is only approximate, and it errs in both directions:
    the factor *falls* with solar zenith as the lengthening downward path takes
    a larger share of the round trip (2.05 at nadir, 1.83 at 55 deg in clear
    water), and *rises* with ``u`` as backscatter strengthens the upward leg
    (2.36 at u = 0.2 and 35 deg). Use this instead of halving.
    """
    theta_w = np.deg2rad(_subsurface_solar_zenith_deg(solar_zenith_deg))
    inv_cos = 1.0 / np.cos(theta_w)
    factor = (inv_cos + _du_bottom(u)) / inv_cos
    return float(factor) if np.ndim(factor) == 0 else np.asarray(factor)


def kd_pure_water(
    wavelength_nm: np.ndarray | float,
    solar_zenith_deg: float = 35.0,
) -> np.ndarray | float:
    """One-way downwelling attenuation ``K_d`` for pure water alone, m^-1.

    The floor on any downwelling attenuation, and the anchor used when
    interpolating a measured K_d across the PAR range: the pure-water term
    carries almost all of the spectral structure beyond 600 nm, so removing it
    before interpolating and adding it back afterwards is far more faithful
    than interpolating the total.
    """
    a_w = np.asarray(water.a_water(wavelength_nm), dtype=float)
    bb_w = np.asarray(water.bb_water(wavelength_nm), dtype=float)
    theta_w = np.deg2rad(_subsurface_solar_zenith_deg(solar_zenith_deg))
    k_d = (a_w + bb_w) / np.cos(theta_w)
    return float(k_d) if np.isscalar(wavelength_nm) or k_d.ndim == 0 else k_d


# --------------------------------------------------------------------------
# Forward model — synthetic scene generation and tests
# --------------------------------------------------------------------------

def forward_model(
    rho_b: np.ndarray,
    depth_m: np.ndarray,
    iops: SceneIOPs,
    solar_zenith_deg: float,
) -> np.ndarray:
    """Above-water Rrs from prescribed rho_b + depth + IOPs.

    The inverse of :func:`invert_scene`. Kept as a public function so tests
    and QC diagnostics can build synthetic scenes without reaching into
    private helpers.
    """
    u = iops.bb / (iops.a + iops.bb)
    inv_cos = 1.0 / np.cos(
        np.deg2rad(_subsurface_solar_zenith_deg(solar_zenith_deg))
    )
    beam_c = iops.a + iops.bb
    k_c = (inv_cos + _du_column(u)) * beam_c
    k_b = (inv_cos + _du_bottom(u)) * beam_c
    rrs_deep = 0.089 * u + 0.125 * u * u
    rrs_sub = rrs_deep * (1.0 - np.exp(-k_c * depth_m)) + (
        rho_b / np.pi
    ) * np.exp(-k_b * depth_m)
    return water.rrs_subsurface_to_above(rrs_sub)


# --------------------------------------------------------------------------
# Closed-form inversion
# --------------------------------------------------------------------------

def invert_scene(
    rrs_above: np.ndarray,
    wavelengths_nm: np.ndarray,
    depth_m: np.ndarray,
    iops: SceneIOPs,
    valid: np.ndarray,
    solar_zenith_deg: float,
) -> np.ndarray:
    """Closed-form rho_b per band, per pixel.

    Parameters
    ----------
    rrs_above : (n_bands, H, W) float32
        Above-water Rrs (1/sr).
    wavelengths_nm : (n_bands,)
        Sensor centre wavelengths. Must match ``iops.wavelengths_nm`` in
        order and content.
    depth_m : (H, W) float32
        Blended Stumpf+EMODnet depth in metres. NaN outside the mask.
    iops : SceneIOPs
        Scene-mean IOPs from :func:`oceanstream.coastal.fit_scene_iops`.
    valid : (H, W) bool
        Composite mask from :func:`oceanstream.coastal.masks.composite_mask`.
    solar_zenith_deg : float
        Above-water solar zenith at scene acquisition.

    Returns
    -------
    rho_b : (n_bands, H, W) float32
        Dimensionless. NaN outside the mask or where depth is NaN.
    """
    if not np.allclose(wavelengths_nm, iops.wavelengths_nm):
        raise ValueError("wavelengths_nm must match iops.wavelengths_nm")

    n_bands = len(wavelengths_nm)
    _, h, w = rrs_above.shape

    # Deep-water (optically-infinite) r_rs — reconstructed forward from IOPs
    # rather than re-averaging pixels, so the two curves are internally
    # consistent. Lee 2002 eq 2: r_rs_deep = g0*u + g1*u^2 with g0=0.089, g1=0.125.
    u_bands = iops.bb / (iops.a + iops.bb)
    g0, g1 = 0.089, 0.125
    rrs_deep_sub = g0 * u_bands + g1 * u_bands * u_bands  # (n_bands,)

    # Convert above-water to subsurface once per pixel
    rrs_sub = water.rrs_above_to_subsurface(rrs_above)

    # Subsurface solar zenith
    theta_w_deg = _subsurface_solar_zenith_deg(solar_zenith_deg)
    inv_cos_theta_w = 1.0 / np.cos(np.deg2rad(theta_w_deg))

    # Per-band k_c, k_b (scalar over the scene, since IOPs are scene-mean)
    du_c = _du_column(u_bands)
    du_b = _du_bottom(u_bands)
    beam_c = iops.a + iops.bb  # (a + b_b)
    k_c = (inv_cos_theta_w + du_c) * beam_c
    k_b = (inv_cos_theta_w + du_b) * beam_c

    # Broadcast depth: (H, W) -> (1, H, W)
    depth_valid = np.where(valid, depth_m, np.nan)[np.newaxis, :, :]

    # Per-band scaling
    kc_H = k_c[:, np.newaxis, np.newaxis] * depth_valid
    kb_H = k_b[:, np.newaxis, np.newaxis] * depth_valid
    rrs_deep = rrs_deep_sub[:, np.newaxis, np.newaxis]

    # Closed form
    with np.errstate(over="ignore", invalid="ignore"):
        column_term = rrs_deep * (1.0 - np.exp(-kc_H))
        residual = rrs_sub - column_term
        rho_b = np.pi * residual * np.exp(kb_H)

    rho_b = np.where(np.isnan(depth_valid), np.nan, rho_b)

    # Clip nonsense values — negative rho_b happens where the deep-water
    # subtraction over-corrects (mask leakage) and where kb*H is large
    # enough that exp inflates noise. Both are diagnostics; log the fraction.
    rho_b = np.where((rho_b < -0.05) | (rho_b > 1.5), np.nan, rho_b)

    _ = n_bands  # kept for symmetry with the earlier signature
    _ = h
    _ = w
    return rho_b.astype(np.float32)


# --------------------------------------------------------------------------
# Uncertainty propagation — dominated by sigma_H
# --------------------------------------------------------------------------

def sigma_rho_b(
    rho_b: np.ndarray,
    depth_m: np.ndarray,
    sigma_h_m: np.ndarray,
    iops: SceneIOPs,
    solar_zenith_deg: float,
    sigma_rrs_above: np.ndarray | float | None = None,
) -> np.ndarray:
    """Propagate depth and reflectance uncertainty to rho_b (first order).

    Differentiating the closed form
    ``rho_b = pi * [r_rs - r_rs_deep * (1 - exp(-k_c*H))] * exp(+k_b*H)``::

        d(rho_b)/dH   = k_b * rho_b - pi * r_rs_deep * k_c * exp((k_b - k_c) * H)
        d(rho_b)/dr   = pi * exp(+k_b * H)
        dr/dRrs       = 0.52 / (0.52 + 1.7 * Rrs)^2        (~1.9, not 1)

    The two contributions are combined in quadrature. ``sigma_rrs_above`` is
    the above-water Rrs noise; omit it to obtain the depth-only term.

    IOP covariance is excluded: it would need a per-pixel QAA fit, and errors
    in the target and in the deep-water reference are correlated, so treating
    them as independent here would understate the total. Reported values are
    therefore a lower bound, not a complete budget.
    """
    u_bands = iops.bb / (iops.a + iops.bb)
    beam_c = iops.a + iops.bb
    inv_cos_theta_w = 1.0 / np.cos(
        np.deg2rad(_subsurface_solar_zenith_deg(solar_zenith_deg))
    )
    k_b = ((inv_cos_theta_w + _du_bottom(u_bands)) * beam_c)[:, None, None]
    k_c = ((inv_cos_theta_w + _du_column(u_bands)) * beam_c)[:, None, None]

    g0, g1 = 0.089, 0.125
    rrs_deep = (g0 * u_bands + g1 * u_bands * u_bands)[:, None, None]

    depth = depth_m[np.newaxis, :, :]
    with np.errstate(over="ignore", invalid="ignore"):
        d_rho_b_dH = k_b * rho_b - np.pi * rrs_deep * k_c * np.exp(
            (k_b - k_c) * depth
        )
        variance = (d_rho_b_dH * sigma_h_m[np.newaxis, :, :]) ** 2

        if sigma_rrs_above is not None:
            rrs = np.asarray(rho_b, dtype=np.float64) / np.pi
            dr_drrs = 0.52 / (0.52 + 1.7 * rrs) ** 2
            d_rho_b_dr = np.pi * np.exp(k_b * depth)
            variance = variance + (
                d_rho_b_dr * dr_drrs * np.asarray(sigma_rrs_above)
            ) ** 2

    return np.sqrt(variance).astype(np.float32)  # type: ignore[no-any-return]


# --------------------------------------------------------------------------
# Optical-depth score
# --------------------------------------------------------------------------

def optical_depth_score(
    depth_m: np.ndarray, kd_490: np.ndarray
) -> np.ndarray:
    """z * K_d(490) — dimensionless. NaN where depth is not physical.

    > 4 : bottom essentially invisible; retrieval unreliable
    2–4 : marginal; treat with caution
    < 2 : bottom clearly seen; retrieval reliable

    Depths <= 0 return NaN rather than a negative score. A negative score
    would pass a one-sided ``score < cutoff`` test automatically, letting land
    and bad-reference pixels through the gate as if they were shallow water.
    """
    score = (depth_m * kd_490).astype(np.float32)
    return np.where(np.isfinite(depth_m) & (depth_m > 0.0), score, np.nan)


# Backwards-compat alias: prototype called this ``invert_rho_b`` and existing
# tests / notebooks may still reach for that name. The plan's public API is
# ``invert_scene`` (see ``oceanstream.coastal.__all__``).
invert_rho_b = invert_scene
