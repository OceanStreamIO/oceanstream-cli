"""Detectability products — how deep the seabed is still worth looking at.

This is the part of the library that has no counterpart in the prototype, and
it is the part that makes the physics deployable without field data.

Why it is the deliverable
-------------------------
A habitat map answers "what is here". It cannot answer "where would I have
seen it if it were here", and without that second map the first one is
unreadable: an absence of kelp at 18 m and an absence of kelp at 4 m are not
the same observation, but a classifier reports them identically. Coverage and
usability are different maps.

Two products, both derived from the measured two-way attenuation ``k(lambda)``:

**z_max** — the depth at which a given substrate contrast falls to the scene's
reflectance noise floor. The Lee water-column term is substrate-independent, so
it cancels exactly in a substrate *difference*, leaving only the attenuated
bottom contrast::

    d_rhos(lambda, z) = t_aw * d_rho_b(lambda) * exp(-k(lambda) * z)

Setting that equal to the noise floor ``epsilon`` and solving::

    z_max = ln(t_aw * d_rho_b / epsilon) / k

No IOP decomposition, no deep-water QAA fit, no labels.

**Seabed PAR** — the fraction of surface photosynthetically active radiation
reaching the seabed, ``E_d(z) / E_d(0-)``, integrated over 400-700 nm. This
bounds where the habitat could exist at all, independently of whether the
sensor can see it.

Three things that are easy to get wrong
---------------------------------------
1. **epsilon is the spatially-varying scatter, not the additive offset.** A
   uniform offset shifts neighbouring pixels equally and cancels in their
   difference, so it does not limit discrimination. What limits it is the
   pixel-to-pixel scatter among pixels that share substrate and depth, which
   is what :func:`oceanstream.coastal.qc.ac_uncertainty.effective_threshold_rhos`
   measures. Passing the offset instead understates z_max by a large factor.

2. **k is two-way; K_d is not.** The fitted ``k`` is the round trip, down and
   back. Photosynthesis sees only the downward leg. The conversion factor is
   near 2, which is why "halve it" circulates, but it depends on solar geometry
   and on ``u`` — see :func:`oceanstream.coastal.inversion.lee.two_way_to_downwelling_factor`.
   Halving is not applied here; the geometric factor is.

3. **z_max is logarithmic in the assumptions and linear in k.** Doubling the
   assumed contrast, or halving the noise floor, moves z_max by only
   ``ln(2)/k`` — about 6 m at Sesimbra's k(444). Getting k wrong by 30% moves
   it by 30%. Every returned band therefore carries the two sensitivity terms
   alongside the headline value, because a z_max quoted without them invites
   being read as a measurement.

Contrast library
----------------
:data:`SUBSTRATE_ALBEDO` is band-integrated bottom irradiance reflectance for
three substrate groups, from a published, openly-licensed spectral library. It
is a *default*, not a site measurement, and it carries real caveats — see
:data:`CONTRAST_LIBRARY_CAVEATS`. Supply ``delta_rho_b`` directly when the AOI
has its own endmembers.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field, replace
from typing import Any

import numpy as np

from oceanstream.coastal.config import DetectabilityConfig
from oceanstream.coastal.inversion import lee

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Substrate contrast library
# ---------------------------------------------------------------------------

CONTRAST_LIBRARY_DOI = "10.1594/PANGAEA.971518"
CONTRAST_LIBRARY_CITATION = (
    "Vahtmae E, Argus L, Toming K, Ligi M, Kutser T (2024): Reflectance spectra "
    "of submerged aquatic vegetation (SAV) species and substrates from the "
    "Baltic Sea coastal waters. PANGAEA, https://doi.org/10.1594/PANGAEA.971518 "
    "(CC-BY 4.0). Published as Vahtmae et al. 2025, ESSD 17:1685-1692."
)

CONTRAST_LIBRARY_CAVEATS: tuple[str, ...] = (
    "Baltic species measured out of water. The library contains Fucus "
    "vesiculosus but not Laminaria ochroleuca or Saccorhiza polyschides, so it "
    "supports a group-level (bare / brown / red) argument only.",
    "Library Rrs converted to bottom irradiance reflectance assuming Q = pi "
    "(Lambertian). Vahtmae et al. note Q may range 0.3-6.5, and a canopy is "
    "not Lambertian in any case.",
    "Sentinel-2 spectral response approximated as a boxcar over each band's "
    "FWHM. Adequate for broad pigment features, not for narrow lines.",
)

#: Band-integrated bottom irradiance reflectance ``R_b`` per substrate group,
#: at the Sentinel-2 visible band centres. Values are the boxcar-averaged
#: library spectra; see :data:`CONTRAST_LIBRARY_CAVEATS` before quoting them.
SUBSTRATE_ALBEDO: dict[str, dict[float, float]] = {
    "bare": {444.0: 0.17938, 489.0: 0.20107, 561.0: 0.24235, 667.0: 0.26728},
    "brown": {444.0: 0.01981, 489.0: 0.02449, 561.0: 0.05192, 667.0: 0.03872},
    "red": {444.0: 0.01753, 489.0: 0.02142, 561.0: 0.03082, 667.0: 0.03300},
}


def substrate_contrast(
    pair: tuple[str, str],
    wavelength_nm: float,
    tolerance_nm: float = 15.0,
) -> float:
    """``|R_b(a) - R_b(b)|`` at the library band nearest ``wavelength_nm``.

    Returns NaN when no library band lands inside the tolerance, so a sensor
    band outside the library's coverage produces no z_max rather than one
    computed from a contrast measured somewhere else in the spectrum.
    """
    first, second = pair
    for group in pair:
        if group not in SUBSTRATE_ALBEDO:
            raise KeyError(
                f"Unknown substrate group {group!r}. Known groups: "
                f"{', '.join(sorted(SUBSTRATE_ALBEDO))}. Pass delta_rho_b "
                "directly to use endmembers measured at the AOI."
            )
    bands = SUBSTRATE_ALBEDO[first]
    nearest = min(bands, key=lambda w: abs(w - wavelength_nm))
    if abs(nearest - wavelength_nm) > tolerance_nm:
        return float("nan")
    return abs(bands[nearest] - SUBSTRATE_ALBEDO[second][nearest])


# ---------------------------------------------------------------------------
# z_max
# ---------------------------------------------------------------------------


def z_max_from_k(
    k_per_m: np.ndarray | float,
    epsilon_rhos: np.ndarray | float,
    delta_rho_b: np.ndarray | float,
    t_aw: float = 0.52,
) -> np.ndarray | float:
    """Depth at which a bottom contrast falls to the noise floor, metres.

    ``z_max = ln(t_aw * delta_rho_b / epsilon) / k``, clamped at zero.

    Every argument accepts a scalar or an array, so the same function produces
    the per-band scene summary and the per-pixel raster. A spatially varying
    ``epsilon_rhos`` (from block-wise AC scatter) or a spatially varying ``k``
    both give a spatially varying z_max; scalars give a constant one.

    A contrast already below the noise floor at the surface returns 0.0, not a
    negative depth: the band never resolves that pair anywhere in the scene.
    Non-physical inputs (``k <= 0``, ``epsilon <= 0``, ``delta_rho_b <= 0``)
    return NaN, because a value there would be indistinguishable from a real
    unlimited detection range.
    """
    k = np.asarray(k_per_m, dtype=np.float64)
    eps = np.asarray(epsilon_rhos, dtype=np.float64)
    contrast = np.asarray(delta_rho_b, dtype=np.float64)

    usable = (
        np.isfinite(k)
        & (k > 0.0)
        & np.isfinite(eps)
        & (eps > 0.0)
        & np.isfinite(contrast)
        & (contrast > 0.0)
    )
    with np.errstate(divide="ignore", invalid="ignore"):
        ratio = np.where(usable, t_aw * contrast / eps, np.nan)
        z = np.log(ratio) / np.where(usable, k, np.nan)
    z = np.where(usable, np.maximum(z, 0.0), np.nan)
    return float(z) if z.ndim == 0 else z


def detectability_margin(z_max_m: np.ndarray | float, depth_m: np.ndarray) -> np.ndarray:
    """``z_max - depth``, metres. Positive where the seabed is still resolvable.

    The signed margin is more useful than a boolean because it says *how far*
    a pixel is from the limit, which is what tells you whether a marginal
    detection is worth revisiting on a clearer scene.
    """
    depth = np.asarray(depth_m, dtype=np.float64)
    margin = np.asarray(z_max_m, dtype=np.float64) - depth
    valid = np.isfinite(depth) & (depth >= 0.0)
    out: np.ndarray = np.where(valid, margin, np.nan).astype(np.float32)
    return out


def detectable_mask(z_max_m: np.ndarray | float, depth_m: np.ndarray) -> np.ndarray:
    """True where the seabed is shallower than z_max.

    NaN depth and NaN z_max both give False. That is deliberate: this mask
    gates whether a detection is *informative*, and an unknown depth cannot
    make one informative.
    """
    margin = detectability_margin(z_max_m, depth_m)
    mask: np.ndarray = np.isfinite(margin) & (margin > 0.0)
    return mask


@dataclass(frozen=True)
class BandDetectability:
    """One band's detection limit for one substrate pair, in one scene."""

    wavelength_nm: float
    k_per_m: float
    k_source: str
    epsilon_rhos: float
    delta_rho_b: float
    z_max_m: float
    #: z_max if the assumed contrast were half what was used. The gap to
    #: ``z_max_m`` is ``ln(2)/k`` and is the honest width of the claim.
    z_max_contrast_halved_m: float
    #: z_max if the noise floor were twice what was used. Same gap, opposite
    #: reason.
    z_max_epsilon_doubled_m: float
    usable: bool
    flags: tuple[str, ...] = ()

    @property
    def sensitivity_span_m(self) -> float:
        """Metres between the pessimistic and headline z_max.

        Both perturbations are a factor of two and so move z_max by the same
        ``ln(2)/k``; this reports it once. A span comparable to ``z_max_m``
        itself means the number is dominated by its assumptions.
        """
        return float(self.z_max_m - self.z_max_contrast_halved_m)


@dataclass(frozen=True)
class SceneDetectability:
    """Per-band detection limits plus the band this scene should be read on."""

    contrast_pair: tuple[str, str]
    epsilon_rhos: float
    epsilon_source: str
    bands: dict[float, BandDetectability] = field(default_factory=dict)
    best_band_nm: float | None = None
    z_max_m: float = float("nan")
    status: str = "ok"
    flags: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """Plain-dict form for the STAC item and the run report."""
        return {
            "status": self.status,
            "contrast_pair": list(self.contrast_pair),
            "epsilon_rhos": self.epsilon_rhos,
            "epsilon_source": self.epsilon_source,
            "best_band_nm": self.best_band_nm,
            "z_max_m": self.z_max_m,
            "flags": list(self.flags),
            "bands": {
                f"{wl:.0f}": asdict(band) | {"flags": list(band.flags)}
                for wl, band in sorted(self.bands.items())
            },
            "contrast_library": {
                "doi": CONTRAST_LIBRARY_DOI,
                "citation": CONTRAST_LIBRARY_CITATION,
                "caveats": list(CONTRAST_LIBRARY_CAVEATS),
            },
        }


def band_detectability(
    wavelength_nm: float,
    k_per_m: float,
    epsilon_rhos: float,
    delta_rho_b: float | None = None,
    solar_zenith_deg: float = 35.0,
    config: DetectabilityConfig | None = None,
) -> BandDetectability:
    """Detection limit for one band, with the substituted-k failure made visible.

    A fitted ``k`` below the pure-water floor is not a measurement of
    attenuation — nothing can attenuate less than pure water. It is replaced by
    the floor, which is the *minimum possible* attenuation and therefore yields
    the *maximum possible* z_max. The result is an optimistic upper bound, not
    an estimate, and is flagged ``z_max_is_upper_bound`` so it cannot be quoted
    as one.
    """
    cfg = config or DetectabilityConfig()
    flags: list[str] = []

    if delta_rho_b is None:
        delta_rho_b = substrate_contrast(
            cfg.contrast_pair, wavelength_nm, cfg.contrast_band_tolerance_nm
        )
        if not np.isfinite(delta_rho_b):
            flags.append("no_library_contrast_at_this_band")

    floor = float(lee.kb_pure_water(wavelength_nm, solar_zenith_deg))
    k_used, k_source = float(k_per_m), "empirical"
    if not np.isfinite(k_per_m):
        k_used, k_source = floor, "pure_water_floor"
        flags += ["k_not_finite", "z_max_is_upper_bound"]
    elif k_per_m < floor:
        k_used, k_source = floor, "empirical_floored_at_pure_water"
        flags += ["k_below_pure_water_floor", "z_max_is_upper_bound"]

    eps = max(float(epsilon_rhos), cfg.min_epsilon_rhos)
    if float(epsilon_rhos) < cfg.min_epsilon_rhos:
        flags.append("epsilon_clamped_to_minimum")

    z_max = float(z_max_from_k(k_used, eps, delta_rho_b, cfg.t_aw))
    z_halved = float(z_max_from_k(k_used, eps, delta_rho_b / 2.0, cfg.t_aw))
    z_doubled = float(z_max_from_k(k_used, eps * 2.0, delta_rho_b, cfg.t_aw))

    if np.isfinite(z_max) and z_max <= 0.0:
        flags.append("contrast_below_noise_at_surface")

    usable = (
        np.isfinite(z_max)
        and z_max >= cfg.min_useful_z_max_m
        and "z_max_is_upper_bound" not in flags
    )
    return BandDetectability(
        wavelength_nm=float(wavelength_nm),
        k_per_m=k_used,
        k_source=k_source,
        epsilon_rhos=eps,
        delta_rho_b=float(delta_rho_b),
        z_max_m=z_max,
        z_max_contrast_halved_m=z_halved,
        z_max_epsilon_doubled_m=z_doubled,
        usable=usable,
        flags=tuple(flags),
    )


def scene_detectability(
    k_by_band: Mapping[float, Any],
    epsilon_rhos: float | None = None,
    epsilon_source: str = "measured_spatial_scatter",
    solar_zenith_deg: float = 35.0,
    config: DetectabilityConfig | None = None,
) -> SceneDetectability:
    """Per-band detection limits and the band this scene should be read on.

    Parameters
    ----------
    k_by_band
        Band centre (nm) to fitted two-way attenuation (1/m). Take these from
        :class:`~oceanstream.coastal.optics.attenuation.BandCalibration` —
        ``k_per_m``, not ``k_effective``, so the floor substitution happens
        here where it can be flagged.
    epsilon_rhos
        Reflectance noise floor. Pass the measured spatially-varying scatter
        from :func:`~oceanstream.coastal.qc.ac_uncertainty.effective_threshold_rhos`;
        omitting it falls back to the configured sensor-noise floor and is
        flagged, because that floor is optimistic by roughly a factor of four
        against the two scenes where both were measured.
    epsilon_source
        Free-text provenance recorded in the result.
    solar_zenith_deg
        Scene solar zenith, used for the pure-water floor geometry.

    Returns
    -------
    SceneDetectability
        ``best_band_nm`` is the usable band with the deepest z_max. Band
        selection is per scene by design: which band wins depends on the
        atmosphere on the day, not on the site.

    Notes
    -----
    A band marked ``usable`` has cleared its own pure-water floor and the
    minimum z_max. It has *not* cleared the scene-level gates in
    :mod:`oceanstream.coastal.qc.floors`, which can fail a scene whose
    individual bands all look fine — a depth-correlated artefact inflating
    every band equally leaves each floor intact while pinning the k ratio
    between them near unity. Run
    :func:`~oceanstream.coastal.qc.floors.scene_floor_verdict` alongside this
    and publish the two together; a z_max from a scene that failed the gates
    is arithmetic, not a measurement.
    """
    cfg = config or DetectabilityConfig()
    flags: list[str] = []

    if epsilon_rhos is None or not np.isfinite(epsilon_rhos):
        epsilon_rhos = cfg.fallback_epsilon_rhos
        epsilon_source = "fallback_sensor_noise"
        flags.append("epsilon_not_measured")

    bands = {
        float(wl): band_detectability(
            float(wl), float(getattr(k, "k_per_m", k)), epsilon_rhos, None, solar_zenith_deg, cfg
        )
        for wl, k in k_by_band.items()
    }

    for wl, value in k_by_band.items():
        failures = tuple(getattr(value, "trust_failures", ()))
        if failures:
            bands[float(wl)] = replace(
                bands[float(wl)],
                usable=False,
                flags=(*bands[float(wl)].flags, *failures),
            )
    usable = [b for b in bands.values() if b.usable]
    if usable:
        best = max(usable, key=lambda b: b.z_max_m)
        best_nm: float | None = best.wavelength_nm
        z_max, status = best.z_max_m, "ok"
    else:
        best_nm, z_max, status = None, float("nan"), "no_usable_band"
        flags.append("every_band_failed_the_pure_water_floor_or_z_max_minimum")

    logger.info(
        "detectability: %d/%d bands usable, best %s, z_max %.1f m (eps %.5f, %s)",
        len(usable),
        len(bands),
        f"{best_nm:.0f} nm" if best_nm else "none",
        z_max,
        epsilon_rhos,
        epsilon_source,
    )
    return SceneDetectability(
        contrast_pair=cfg.contrast_pair,
        epsilon_rhos=float(epsilon_rhos),
        epsilon_source=epsilon_source,
        bands=bands,
        best_band_nm=best_nm,
        z_max_m=z_max,
        status=status,
        flags=tuple(flags),
    )


# ---------------------------------------------------------------------------
# Seabed PAR
# ---------------------------------------------------------------------------


def downwelling_kd(
    k_two_way: np.ndarray | float,
    wavelength_nm: np.ndarray | float,
    solar_zenith_deg: float = 35.0,
    u: np.ndarray | float | None = None,
) -> np.ndarray | float:
    """Convert fitted two-way ``k`` to one-way downwelling ``K_d``, m^-1.

    Divides by the geometric factor rather than by two — see
    :func:`~oceanstream.coastal.inversion.lee.two_way_to_downwelling_factor`.

    ``u = b_b / (a + b_b)`` comes from the QAA scene fit. When it is not
    available the pure-water value is used, which is right to within a percent
    or so in clear water and increasingly optimistic as turbidity rises, since
    ``u`` only grows and the factor grows with it.
    """
    if u is None:
        from oceanstream.coastal.optics import water

        bb_w = np.asarray(water.bb_water(wavelength_nm), dtype=float)
        a_w = np.asarray(water.a_water(wavelength_nm), dtype=float)
        u = bb_w / (a_w + bb_w)
    factor = lee.two_way_to_downwelling_factor(u, solar_zenith_deg)
    kd = np.asarray(k_two_way, dtype=float) / np.asarray(factor, dtype=float)
    return float(kd) if kd.ndim == 0 else kd


def interpolate_kd(
    kd_per_m: Sequence[float] | np.ndarray,
    wavelengths_nm: Sequence[float] | np.ndarray,
    target_nm: Sequence[float] | np.ndarray,
    solar_zenith_deg: float = 35.0,
) -> np.ndarray:
    """Interpolate ``K_d`` across the PAR range, anchored on pure water.

    Interpolating total ``K_d`` between 4-6 visible bands is wrong in the red:
    pure-water absorption rises by a factor of 25 between 560 and 700 nm, and a
    straight line between 561 and 667 nm misses almost all of it, then flat
    extrapolation past 667 nm misses the rest.

    So the pure-water term is removed first, the *constituent* residual —
    which is smooth and monotonic in the visible — is interpolated, and the
    pure-water term is added back at the target wavelengths::

        K_d(lambda) = K_d_water(lambda) + interp(K_d_meas - K_d_water)

    The residual is clipped at zero because constituents can only add
    attenuation; a measured value below the pure-water line is a fit failure,
    not negative absorption, and clipping keeps that failure from propagating
    as a negative K_d somewhere else in the spectrum.

    Extrapolation beyond the measured bands is flat *in the residual*, so the
    reconstructed total still carries the full pure-water shape out to 700 nm.
    """
    wl = np.asarray(wavelengths_nm, dtype=float)
    kd = np.asarray(kd_per_m, dtype=float)
    finite = np.isfinite(wl) & np.isfinite(kd)
    if not finite.any():
        raise ValueError(
            "No finite K_d values to interpolate. Every band's attenuation fit "
            "failed; check AttenuationConfig.min_bins and the depth window."
        )
    wl, kd = wl[finite], kd[finite]
    order = np.argsort(wl)
    wl, kd = wl[order], kd[order]

    residual = np.maximum(
        kd - np.asarray(lee.kd_pure_water(wl, solar_zenith_deg), dtype=float), 0.0
    )
    targets = np.asarray(target_nm, dtype=float)
    return np.asarray(lee.kd_pure_water(targets, solar_zenith_deg), dtype=float) + np.interp(
        targets, wl, residual
    )


def par_weights(wavelengths_nm: np.ndarray, photon_weighted: bool = True) -> np.ndarray:
    """Normalised PAR integration weights over ``wavelengths_nm``.

    PAR is defined as a photon flux, and photon energy goes as ``1/lambda``, so
    converting a spectrally flat irradiance to quanta gives a weight
    proportional to ``lambda``.

    The flat-irradiance assumption is the approximation here. A clear-sky
    ``E_d(0-)`` varies by roughly +-15% across 400-700 nm, which moves the
    integrated fraction by far less than the uncertainty already carried by the
    extrapolated ``K_d``. Weight the wavelengths yourself and pass the product
    if the AOI has a measured or modelled surface spectrum.
    """
    wl = np.asarray(wavelengths_nm, dtype=float)
    weights = wl.copy() if photon_weighted else np.ones_like(wl)
    normalised: np.ndarray = weights / weights.sum()
    return normalised


def seabed_par_fraction(
    depth_m: np.ndarray,
    kd_per_m: Sequence[float] | np.ndarray,
    wavelengths_nm: Sequence[float] | np.ndarray,
    solar_zenith_deg: float = 35.0,
    config: DetectabilityConfig | None = None,
) -> np.ndarray:
    """Fraction of surface PAR reaching the seabed, per pixel.

    ``sum_lambda w(lambda) exp(-K_d(lambda) z) / sum_lambda w(lambda)`` over
    400-700 nm.

    Parameters
    ----------
    depth_m
        (H, W) bathymetry in metres. NaN and negative depths return NaN.
    kd_per_m
        One-way downwelling ``K_d`` per band. Convert a fitted two-way ``k``
        with :func:`downwelling_kd` first — passing the two-way value directly
        roughly halves the reported light and is the single easiest mistake to
        make here.
    wavelengths_nm
        Band centres matching ``kd_per_m``.

    Returns
    -------
    np.ndarray
        (H, W) float32 in [0, 1]. NaN beyond ``config.par_max_depth_m``, which
        is not a physical bound but the point past which the fitted ``K_d`` is
        being extrapolated further than its own fit window justifies.
    """
    cfg = config or DetectabilityConfig()
    lo, hi = cfg.par_range_nm
    targets = np.arange(lo, hi + cfg.par_step_nm / 2.0, cfg.par_step_nm)
    kd_targets = interpolate_kd(kd_per_m, wavelengths_nm, targets, solar_zenith_deg)
    weights = par_weights(targets, cfg.par_photon_weighted)

    depth = np.asarray(depth_m, dtype=np.float64)
    valid = np.isfinite(depth) & (depth >= 0.0) & (depth <= cfg.par_max_depth_m)
    safe_depth = np.where(valid, depth, 0.0)

    # Accumulate band by band: the (n_lambda, H, W) intermediate would be ~30x
    # the scene, and a full Sentinel-2 tile at 10 m is already 2 GB in float32.
    fraction = np.zeros(depth.shape, dtype=np.float64)
    for k_d, weight in zip(kd_targets, weights, strict=True):
        fraction += weight * np.exp(-k_d * safe_depth)

    out: np.ndarray = np.where(valid, fraction, np.nan).astype(np.float32)
    return out


def euphotic_depth(
    kd_per_m: Sequence[float] | np.ndarray,
    wavelengths_nm: Sequence[float] | np.ndarray,
    fraction: float = 0.01,
    solar_zenith_deg: float = 35.0,
    config: DetectabilityConfig | None = None,
) -> float:
    """Depth at which seabed PAR falls to ``fraction`` of the surface value.

    Defaults to 1%, the conventional euphotic depth. Solved on the same
    spectrally-integrated PAR profile as :func:`seabed_par_fraction`, not on a
    single-band ``K_d``, because the spectrum reddens with depth and a
    single-band estimate drifts by metres.

    Returns NaN when the target is not reached inside
    ``config.par_max_depth_m`` — reporting a depth past the point where the
    fit is being extrapolated would be inventing precision.
    """
    cfg = config or DetectabilityConfig()
    probe = np.arange(0.0, cfg.par_max_depth_m + 0.01, 0.01)
    profile = seabed_par_fraction(probe, kd_per_m, wavelengths_nm, solar_zenith_deg, cfg)
    below = np.flatnonzero(profile <= fraction)
    return float(probe[below[0]]) if below.size else float("nan")
