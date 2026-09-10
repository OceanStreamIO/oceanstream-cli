"""QAA v6 semi-analytical inversion — a(λ), b_b(λ), K_d(λ) from Rrs.

Follows Lee et al. 2002 (QAA v5) with the v6 update to the reference-wavelength
selection (Lee et al. 2013). One IOP set is fitted on optically-deep pixels for
the whole scene rather than running QAA per-pixel: over a coastal AOI of a few
tens of km² the water mass is close enough to uniform, and a scene-mean fit is
far more robust than a per-pixel one at the reflectance levels involved.

:func:`fit_scene_iops` is the entry point. Everything else is factored out so
QC panels can plot intermediates.

Ported from ``kelp_observe/tools/lee_demo/qaa.py`` in Phase 1.2, with two
deliberate changes:

- The Sesimbra-tuned constants (``RRS_670_TURBID_THRESHOLD``,
  ``A_CDM_443_MIN``) and every plausibility range now live in
  :class:`~oceanstream.coastal.config.RetrievalConfig` rather than as module
  globals, so a different AOI is a config change, not an edit to physics code.
- The prototype flagged negative ``a_cdm(443)`` against a hardcoded −0.03
  while ``SiteProfile.a_cdm_443_min`` carried a per-site bound that nothing
  read. Both are now wired: ``a_cdm_443_max_fatal`` reproduces the prototype's
  flag, ``a_cdm_443_min`` adds the softer AOI-specific one.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np

from oceanstream.coastal.config import RetrievalConfig
from oceanstream.coastal.optics import water

logger = logging.getLogger(__name__)

# Sensor centre wavelengths never land on the QAA nominal bands — Sentinel-2's
# green is 560 nm, Pléiades Neo's 555 nm — so band identification needs slack.
# The violet tolerance is tighter because the a_ph/a_dg split is only valid
# with a genuine ~411 nm band, not with whatever is nearest.
GREEN_REFERENCE_TOLERANCE_NM = 20.0
VIOLET_BAND_TOLERANCE_NM = 15.0

#: Reference wavelengths above this are red. Legal in QAA v6, but flagged.
RED_REFERENCE_BOUNDARY_NM = 600.0

# --------------------------------------------------------------------------
# Scene-mean IOP result
# --------------------------------------------------------------------------

@dataclass
class SceneIOPs:
    """Scene-uniform IOPs, indexed by wavelength.

    All arrays have length equal to the number of bands passed in.
    """

    wavelengths_nm: np.ndarray  # (n_bands,)
    a: np.ndarray               # total absorption a(λ), m^-1
    bb: np.ndarray              # total backscatter b_b(λ), m^-1
    kd: np.ndarray              # diffuse attenuation K_d(λ), m^-1
    a_cdm_443: float            # CDOM+detritus absorption at 443 nm
    bbp_555: float              # particle backscatter at 555 nm
    y: float                    # spectral slope of b_bp
    n_deep_pixels: int          # provenance: how many pixels fit was on
    ref_wavelength_nm: float    # reference wavelength picked by v6 rule
    qa_flags: list[str] = field(default_factory=list)  # empty = physically sane
    a_floor_violations: list[dict] = field(default_factory=list)  # a(λ) < a_w(λ), now floored


# --------------------------------------------------------------------------
# QAA v6 core
# --------------------------------------------------------------------------

def _pick_reference_wavelength(
    rrs_above: np.ndarray,
    wavelengths_nm: np.ndarray,
    turbid_threshold: float,
    switch_margin: float = 0.0,
) -> int:
    """QAA v6: 555 nm for clear water, ~670 nm for turbid.

    Rule (Lee et al.): the red reference is selected only when above-water
    Rrs(670) >= ``turbid_threshold`` — 0.0015 sr^-1 in the published rule.
    Over optically deep clear water Rrs(670) is essentially zero, so the green
    reference is the correct anchor.

    ``switch_margin`` is the uncertainty on Rrs(670) left by the atmospheric
    correction. Red is chosen only when the signal clears the threshold by
    more than that, because a red reference anchors the entire IOP spectrum
    in the band with the least signal and the most pure-water absorption —
    the one place a small error becomes a large one.

    ``rrs_above`` is (n_bands,), the above-water mean over the deep-water
    pixels. Returns an index into ``wavelengths_nm``.
    """
    red_idx = int(np.argmin(np.abs(wavelengths_nm - 670)))
    turbid = float(rrs_above[red_idx]) >= turbid_threshold + switch_margin
    target = 670.0 if turbid else 555.0
    return int(np.argmin(np.abs(wavelengths_nm - target)))


def _u_from_rrs(rrs_subsurface: np.ndarray) -> np.ndarray:
    """u = b_b / (a + b_b), inverted from Gordon 1988 quadratic.

    r_rs = g0 * u + g1 * u^2, solved for u ∈ [0,1]:
        u = (-g0 + sqrt(g0^2 + 4*g1*r_rs)) / (2*g1)

    Coefficients Lee 2002: g0 = 0.089, g1 = 0.125.
    """
    g0, g1 = 0.089, 0.125
    result = (-g0 + np.sqrt(g0 * g0 + 4.0 * g1 * rrs_subsurface)) / (2.0 * g1)
    return np.asarray(result)


def _kd_lee_2005(
    a: np.ndarray, bb: np.ndarray, solar_zenith_deg: float
) -> np.ndarray:
    """K_d from a and b_b — Lee et al. 2005 eq 11 (used by QAA v6 downstream).

    K_d(λ) = (1 + 0.005 * θ_a) * a(λ) + 4.18 * (1 - 0.52*exp(-10.8*a(λ))) * b_b(λ)
    θ_a is the above-water solar zenith in degrees.
    """
    theta_a = solar_zenith_deg
    result = (1 + 0.005 * theta_a) * a + 4.18 * (
        1 - 0.52 * np.exp(-10.8 * a)
    ) * bb
    return np.asarray(result)


def _a_at_reference(
    rrs_sub_443: float,
    rrs_sub_490: float,
    rrs_sub_555: float,
    rrs_sub_670: float,
    rrs_above_443: float,
    rrs_above_490: float,
    rrs_above_670: float,
    a_w_ref: float,
    ref_wavelength_nm: float,
) -> float:
    """QAA v6 empirical estimate of total absorption at the reference band.

    Green reference (Lee 2002 eq 12, subsurface r_rs)::

        chi    = log10((r443 + r490) / (r555 + 5 * (r670/r490) * r670))
        a(555) = a_w(555) + 10^(-1.146 - 1.366*chi - 0.469*chi^2)

    Red reference (Lee et al. QAA v6, above-water Rrs)::

        a(670) = a_w(670) + 0.39 * (R670 / (R443 + R490))^1.14
    """
    if abs(ref_wavelength_nm - 555.0) < GREEN_REFERENCE_TOLERANCE_NM:
        numer = rrs_sub_443 + rrs_sub_490
        denom = rrs_sub_555 + 5.0 * (rrs_sub_670 / max(rrs_sub_490, 1e-6)) * rrs_sub_670
        chi = np.log10(max(numer / max(denom, 1e-6), 1e-6))
        return float(a_w_ref + 10 ** (-1.146 - 1.366 * chi - 0.469 * chi * chi))
    ratio = rrs_above_670 / max(rrs_above_443 + rrs_above_490, 1e-6)
    return float(a_w_ref + 0.39 * max(ratio, 0.0) ** 1.14)


def _y_from_rrs(rrs_sub_443: float, rrs_sub_555: float) -> float:
    """QAA v6 empirical Y (spectral slope of b_bp)."""
    return float(
        2.0 * (1.0 - 1.2 * np.exp(-0.9 * rrs_sub_443 / max(rrs_sub_555, 1e-6)))
    )


def _a_cdm_at_443(
    a: np.ndarray,
    wavelengths_nm: np.ndarray,
    zeta: float,
    s_cdm: float,
) -> float:
    """CDOM+detritus absorption at 443 nm, or NaN when the split is impossible.

    Lee 2002 step 10 splits a into a_ph and a_dg using TWO blue bands, 411 and
    443 — zeta is *defined* as a_ph(411)/a_ph(443). Sentinel-2's bluest band is
    443, so the split is simply not computable from S2 alone. Substituting 555
    for 411 (as the prototype once did) is a different quantity and drives
    a_cdm systematically negative, because in clear water a(555) > a(443).
    """
    violet_idx = int(np.argmin(np.abs(wavelengths_nm - 411)))
    violet_nm = float(wavelengths_nm[violet_idx])
    if abs(violet_nm - 411.0) > VIOLET_BAND_TOLERANCE_NM:
        return float("nan")
    blue_idx = int(np.argmin(np.abs(wavelengths_nm - 443)))
    xi = float(np.exp(s_cdm * (443.0 - violet_nm)))
    denom = xi - zeta
    return float(
        (a[violet_idx] - zeta * a[blue_idx]) / denom
        - (
            float(water.a_water(violet_nm))
            - zeta * float(water.a_water(443.0))
        )
        / denom
    )


def _plausibility_flags(
    a_cdm_443: float,
    bbp_555: float,
    a_490: float,
    kd_490: float,
    y: float,
    ref_wavelength_nm: float,
    config: RetrievalConfig,
) -> list[str]:
    """Physical sanity checks on a fitted IOP set.

    Each failure means the inversion returned something that cannot occur in
    nature — most often because the reference band carried no usable signal.
    A red reference over a clear-water coastal site is the single most
    diagnostic symptom, so it is flagged even though it is legal in QAA v6.
    """
    flags: list[str] = []
    if ref_wavelength_nm > RED_REFERENCE_BOUNDARY_NM:
        flags.append(f"red_reference_selected({ref_wavelength_nm:.0f}nm)")
    if np.isnan(a_cdm_443):
        # Not an error: the a_ph/a_dg split needs a ~411 nm band the sensor
        # does not carry. Recorded so the absence is visible, not silent.
        flags.append("a_cdm_443_not_computable(no_411nm_band)")
    elif a_cdm_443 < config.a_cdm_443_max_fatal:
        flags.append(f"a_cdm_443_negative({a_cdm_443:.4f})")
    elif a_cdm_443 < config.a_cdm_443_min:
        flags.append(f"a_cdm_443_below_aoi_bound({a_cdm_443:.4f})")
    if not np.isfinite(bbp_555) or not (
        config.bbp_555_min < bbp_555 < config.bbp_555_max
    ):
        flags.append(f"bbp_555_implausible({bbp_555:.5f})")
    if not np.isfinite(a_490) or a_490 < float(water.a_water(490.0)):
        flags.append(f"a_490_below_pure_water({a_490:.4f})")
    if not np.isfinite(kd_490) or not (
        config.kd_490_min <= kd_490 <= config.kd_490_max
    ):
        flags.append(f"kd_490_out_of_range({kd_490:.3f})")
    if not np.isfinite(y) or not (config.y_min <= y <= config.y_max):
        flags.append(f"bbp_slope_out_of_range({y:.2f})")
    return flags


# --------------------------------------------------------------------------
# Public entry points
# --------------------------------------------------------------------------

def fit_scene_iops(
    rrs_above: np.ndarray,
    wavelengths_nm: np.ndarray,
    deep_water_valid: np.ndarray,
    solar_zenith_deg: float,
    config: RetrievalConfig | None = None,
    force_ref_wavelength_nm: float | None = None,
    ref_switch_margin: float = 0.0,
) -> SceneIOPs:
    """Fit scene-mean IOPs on optically-deep pixels.

    Parameters
    ----------
    rrs_above : (n_bands, H, W) float32
        Above-water Rrs (1/sr) — ACOLITE ``Rrs_*``, or
        :func:`oceanstream.coastal.rhos_to_rrs` applied to ``rhos_*`` if only
        surface reflectance is available.
    wavelengths_nm : (n_bands,)
        Sensor centre wavelengths, sorted or unsorted; QAA picks its own
        reference band.
    deep_water_valid : (H, W) bool
        True on pixels the QAA fit should use. Compute with
        :func:`oceanstream.coastal.masks.deep_water_pixels`.
    solar_zenith_deg : float
        Above-water solar zenith at scene acquisition. Read from the scene
        metadata; drives K_d only, not the IOP retrieval.
    config : RetrievalConfig, optional
        Reference-switch and plausibility thresholds. Defaults reproduce the
        Sesimbra prototype.
    force_ref_wavelength_nm : float, optional
        Override the v6 reference-band rule — use when the red band is known
        to carry only atmospheric-correction residual.
    ref_switch_margin : float
        Uncertainty on Rrs(670) left by the atmospheric correction; raises the
        bar for switching to a red reference.

    Returns
    -------
    SceneIOPs
        One IOP vector for the whole scene.
    """
    cfg = config or RetrievalConfig()
    n_bands = len(wavelengths_nm)
    if rrs_above.shape[0] != n_bands:
        raise ValueError(
            f"rrs_above has {rrs_above.shape[0]} bands but "
            f"wavelengths_nm has {n_bands}"
        )

    # Deep-water mean spectrum (above-water then subsurface). Require all bands
    # finite at each candidate pixel — otherwise nan propagates through the
    # QAA algebra.
    all_finite = np.all(np.isfinite(rrs_above), axis=0)
    valid_mask = deep_water_valid & all_finite
    n_deep = int(valid_mask.sum())
    if n_deep < cfg.min_deep_pixels:
        raise ValueError(
            f"Only {n_deep} deep-water pixels — need at least "
            f"{cfg.min_deep_pixels}. Check the AOI extends past the "
            "optically-deep shelf, or lower RetrievalConfig.min_deep_pixels."
        )

    rrs_mean_above = np.array(
        [rrs_above[b][valid_mask].mean() for b in range(n_bands)]
    )
    rrs_mean_sub = np.asarray(water.rrs_above_to_subsurface(rrs_mean_above))

    if force_ref_wavelength_nm is not None:
        ref_idx = int(np.argmin(np.abs(wavelengths_nm - force_ref_wavelength_nm)))
    else:
        ref_idx = _pick_reference_wavelength(
            rrs_mean_above,
            wavelengths_nm,
            turbid_threshold=cfg.rrs_670_turbid_threshold,
            switch_margin=ref_switch_margin,
        )
    ref_wavelength_nm = float(wavelengths_nm[ref_idx])

    blue_idx = int(np.argmin(np.abs(wavelengths_nm - 443)))
    green_idx = int(np.argmin(np.abs(wavelengths_nm - 490)))
    yellow_idx = int(np.argmin(np.abs(wavelengths_nm - 555)))
    red_idx = int(np.argmin(np.abs(wavelengths_nm - 670)))

    a_ref = _a_at_reference(
        rrs_sub_443=float(rrs_mean_sub[blue_idx]),
        rrs_sub_490=float(rrs_mean_sub[green_idx]),
        rrs_sub_555=float(rrs_mean_sub[yellow_idx]),
        rrs_sub_670=float(rrs_mean_sub[red_idx]),
        rrs_above_443=float(rrs_mean_above[blue_idx]),
        rrs_above_490=float(rrs_mean_above[green_idx]),
        rrs_above_670=float(rrs_mean_above[red_idx]),
        a_w_ref=float(water.a_water(ref_wavelength_nm)),
        ref_wavelength_nm=ref_wavelength_nm,
    )
    u_ref = float(_u_from_rrs(rrs_mean_sub[ref_idx]))
    bb_ref = u_ref * a_ref / max(1.0 - u_ref, 1e-6)
    bbp_ref = bb_ref - float(water.bb_water(ref_wavelength_nm))

    y = _y_from_rrs(
        float(rrs_mean_sub[blue_idx]), float(rrs_mean_sub[yellow_idx])
    )
    blue_yellow = float(rrs_mean_sub[blue_idx] / rrs_mean_sub[yellow_idx])
    zeta = 0.74 + 0.2 / (0.8 + blue_yellow)
    s_cdm = 0.015 + 0.002 / (0.6 + blue_yellow)

    bbp_all = bbp_ref * np.asarray(
        water.bbp_shape(wavelengths_nm, ref_wavelength_nm, y=y)
    )
    bb_all = bbp_all + np.asarray(water.bb_water(wavelengths_nm))

    u_all = _u_from_rrs(rrs_mean_sub)
    a_all = bb_all * (1.0 - u_all) / np.clip(u_all, 1e-6, None)

    # Total absorption cannot fall below pure water: a = a_w + a_ph + a_dg with
    # every term non-negative. A retrieval below a_w means the reflectance fed
    # in is too high — atmospheric over-correction, or a band whose signal is
    # already saturated by water absorption. Floor it and record which bands.
    a_water_all = np.asarray(water.a_water(wavelengths_nm), dtype=float)
    below_floor = a_all < a_water_all
    a_floor_violations = [
        {
            "wavelength_nm": float(wavelengths_nm[i]),
            "a_retrieved": round(float(a_all[i]), 5),
            "a_pure_water": round(float(a_water_all[i]), 5),
        }
        for i in np.flatnonzero(below_floor)
    ]
    a_all = np.maximum(a_all, a_water_all)

    a_cdm_443 = _a_cdm_at_443(a_all, wavelengths_nm, zeta, s_cdm)

    kd = _kd_lee_2005(a_all, bb_all, solar_zenith_deg)

    bbp_555 = float(bbp_ref * water.bbp_shape(555.0, ref_wavelength_nm, y=y))
    kd_490_idx = int(np.argmin(np.abs(wavelengths_nm - 490)))
    qa_flags = _plausibility_flags(
        a_cdm_443=a_cdm_443,
        bbp_555=bbp_555,
        a_490=float(a_all[kd_490_idx]),
        kd_490=float(kd[kd_490_idx]),
        y=float(y),
        ref_wavelength_nm=ref_wavelength_nm,
        config=cfg,
    )
    qa_flags.extend(
        "a_below_pure_water_floor({:.0f}nm: {:.4f} < {:.4f})".format(
            violation["wavelength_nm"],
            violation["a_retrieved"],
            violation["a_pure_water"],
        )
        for violation in a_floor_violations
    )
    if qa_flags:
        logger.warning(
            "QAA fit at ref=%.0f nm failed %d plausibility check(s): %s",
            ref_wavelength_nm,
            len(qa_flags),
            "; ".join(qa_flags),
        )

    return SceneIOPs(
        wavelengths_nm=np.asarray(wavelengths_nm, dtype=np.float32),
        a=a_all.astype(np.float32),
        bb=bb_all.astype(np.float32),
        kd=kd.astype(np.float32),
        a_cdm_443=a_cdm_443,
        bbp_555=bbp_555,
        y=float(y),
        n_deep_pixels=n_deep,
        ref_wavelength_nm=ref_wavelength_nm,
        qa_flags=qa_flags,
        a_floor_violations=a_floor_violations,
    )


def kd_map(
    rrs_above: np.ndarray,
    wavelengths_nm: np.ndarray,
    valid: np.ndarray,
    solar_zenith_deg: float,
    band_nm: float = 490.0,
) -> np.ndarray:
    """Per-pixel K_d at one wavelength, for the optical-depth score.

    Cheaper than a full per-pixel QAA — u(λ) at 490 brackets K_d within a
    factor of 2, which is all the optical-depth score needs: it gates a QC
    mask, it is not a science product.
    """
    band_idx = int(np.argmin(np.abs(wavelengths_nm - band_nm)))
    rrs_sub = np.asarray(water.rrs_above_to_subsurface(rrs_above))

    u = _u_from_rrs(rrs_sub[band_idx])

    # Scene-mean b_bp(555) extrapolated to the target band with Y = 1.
    yellow_idx = int(np.argmin(np.abs(wavelengths_nm - 555)))
    u_555_mean = float(_u_from_rrs(rrs_sub[yellow_idx][valid].mean()))
    a_555 = float(water.a_water(555.0))
    bb_555 = u_555_mean * a_555 / max(1.0 - u_555_mean, 1e-6)
    bbp_555 = max(bb_555 - float(water.bb_water(555.0)), 1e-4)
    bb_band = float(water.bb_water(band_nm)) + bbp_555 * float(
        water.bbp_shape(band_nm, 555.0, y=1.0)
    )

    # u = bb / (a + bb)  =>  a = bb * (1 - u) / u
    a_band = bb_band * (1.0 - u) / np.clip(u, 1e-6, None)
    kd = _kd_lee_2005(a_band, np.full_like(a_band, bb_band), solar_zenith_deg)
    return np.asarray(np.where(valid, kd, np.nan), dtype=np.float32)
