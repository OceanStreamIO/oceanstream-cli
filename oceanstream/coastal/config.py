"""Configuration for coastal optical retrieval.

These dataclasses collect the roughly thirty magic numbers scattered through
the lee_demo prototype into named, typed groups. Every threshold that was
tuned on Sesimbra 2026-06-27 lives here so that a different AOI or a
differently-behaved scene can be configured without editing physics code.

Design rules
------------
- Nothing here has file-system defaults (paths are AOI-specific — see aoi.py).
- Nothing here is sensor-specific (bands / GSD / solar defaults belong to
  SensorProfile in sensors.py; solar geometry belongs to Scene).
- Everything is a plain dataclass so ``dataclasses.asdict`` produces a
  serialisable trace of the effective retrieval config for the STAC item.
"""

from __future__ import annotations

from dataclasses import dataclass

# ---------------------------------------------------------------------------
# QAA v6 / IOP retrieval
# ---------------------------------------------------------------------------


@dataclass
class RetrievalConfig:
    """Scene-mean IOP retrieval knobs.

    Defaults reproduce the Sesimbra prototype so existing regression outputs
    are byte-identical.
    """

    #: QAA v6 uses ~670 nm when Rrs(670) clears this; green (~555) otherwise.
    #: 0.0015 sr^-1 is the Lee et al. v6 threshold. Clear-water coastal AOIs
    #: (Sesimbra) rarely reach it; turbid AOIs (Ireland NW, upwelling in
    #: Galicia after a plume event) may cross it and need per-scene checks.
    rrs_670_turbid_threshold: float = 0.0015  # sr^-1

    #: Soft bound on retrieved a_cdm(443). Below this the fit is flagged
    #: ``a_cdm_443_below_aoi_bound``. -0.005 is the Sesimbra value, justified
    #: by CCMAR sonde casts (fDOM ~0.05 QSU, salinity flat at 36.7 psu): true
    #: a_cdm(443) there is ~0 but non-negative, so anything below this is
    #: retrieval error, usually atmospheric over-correction in the blue.
    #: AOIs without a sonde should relax this to the generic -0.03 guard.
    a_cdm_443_min: float = -0.005  # m^-1

    #: Hard bound on retrieved a_cdm(443). Below this the fit is flagged
    #: ``a_cdm_443_negative`` — the generic guard, unchanged from the
    #: prototype, applicable at any AOI.
    a_cdm_443_max_fatal: float = -0.03  # m^-1

    #: Physical plausibility range for K_d(490). Below 0.01 the water is
    #: sub-pure-water (never happens); above 2.0 the retrieval is bottom-
    #: blind in the blue.
    kd_490_min: float = 0.01
    kd_490_max: float = 2.0

    #: Physical plausibility range for bb_p(555). Above 0.02 m^-1 is highly
    #: turbid; below 0 is unphysical.
    bbp_555_min: float = 0.0
    bbp_555_max: float = 0.02

    #: Physical plausibility range for QAA Y (spectral slope of b_bp).
    y_min: float = 0.0
    y_max: float = 2.5

    #: Minimum optically-deep pixels required before a scene-mean fit is
    #: attempted. Below this the mean spectrum is dominated by whichever few
    #: pixels survived masking.
    min_deep_pixels: int = 50


# ---------------------------------------------------------------------------
# Masking
# ---------------------------------------------------------------------------


@dataclass
class MaskConfig:
    """Water / deep-water / cloud masking knobs.

    Every threshold applies to *surface* reflectance (ACOLITE ``rhos_*``, or
    the Hedley-deglinted equivalent), not to Rrs.
    """

    #: Land rejection: NIR reflectance above this is land or optically-shallow
    #: bright sand. Deliberately generous — tighter cuts eat into shallow
    #: water where the seafloor is bright, which is the signal we are after.
    land_nir_max: float = 0.10

    #: Cloud rejection: a pixel bright in *both* blue and NIR. Water is bright
    #: only in blue, land only in NIR. Not a Sen2Cor-grade detector.
    cloud_reflectance_max: float = 0.20

    #: Sun-glint rejection on NIR, and optionally on SWIR when the sensor
    #: carries it. ``glint_swir_max`` falls back to ``glint_nir_max``.
    glint_nir_max: float = 0.03
    glint_swir_max: float | None = None

    #: Foam / whitecap rejection: bright in all visible bands *and* spectrally
    #: flat (max/min band ratio below ``foam_flatness_max``).
    foam_reflectance_min: float = 0.15
    foam_flatness_max: float = 1.4

    #: Water pixels within this many pixels of land are dropped. Cliff-backed
    #: coasts (Arrábida limestone) are the adjacency case Castagna &
    #: Vanhellemont 2025 warn about; without RAdCor we exclude rather than
    #: correct. Choose so the physical distance is ~40 m for the sensor GSD.
    adjacency_buffer_px: int = 4

    #: Deep-water screen: SWIR reflectance below this indicates optically
    #: deep water when the sensor carries SWIR bands (Sentinel-2 B11/B12).
    #: PNeo has no SWIR and needs a NIR-based fallback (see SensorProfile).
    swir_max_rhos: float = 0.01

    #: SWIR wavelengths used for the deep-water screen when available.
    swir_bands_nm: tuple[float, ...] = (1612.0, 2191.0)

    #: Deep-water sampling for the QAA fit. A pixel qualifies when it is both
    #: deeper than ``deepwater_min_depth_m`` and optically deeper than
    #: ``deepwater_min_optical_depth`` (z * Kd; 6 leaves ~0.25% bottom
    #: transmission). The fit is then subsampled for speed.
    deepwater_min_depth_m: float = 30.0
    deepwater_min_optical_depth: float = 6.0
    deepwater_max_samples: int = 5_000

    #: Optical-depth score threshold (z * K_d) above which a pixel is
    #: bottom-invisible. Lee's rule of thumb: >4 = invisible, <2 = clean
    #: retrieval, 2–4 = degraded.
    optical_depth_max: float = 4.0
    optical_depth_clean: float = 2.0

    #: Seed for every subsampling draw, so a rerun of the same scene produces
    #: the same deep-water sample and the same fit.
    rng_seed: int = 42


# ---------------------------------------------------------------------------
# Satellite-derived bathymetry (Stumpf log-ratio)
# ---------------------------------------------------------------------------


@dataclass
class BathymetryConfig:
    """Stumpf log-ratio bathymetry bounds and fit windows.

    ``depth_valid_range_m`` and ``fit_reference_range_m`` are the AOI-specific
    entries: a retrieval outside the site's physical depth range is a failure,
    not a value, and the fit window has to sit inside the range where the
    bottom is actually visible at that site's clarity.
    """

    #: Stumpf needs ``n * R > 1`` on every pixel so both logs stay strictly
    #: positive. ``n * R = 1`` is a pole: the ratio diverges and the depth map
    #: fills with hundreds of metres. Reject pixels at or below this.
    min_scaled_reflectance: float = 1.05

    #: Physically plausible blue/green log ratio. Outside this the pixel is
    #: not carrying a bottom signal.
    ratio_valid_range: tuple[float, float] = (0.5, 2.0)

    #: Clamp on retrieved depth, in metres. Set from the AOI's real range.
    depth_valid_range_m: tuple[float, float] = (0.0, 40.0)

    #: Reference-depth window used to fit m0/m1. Deeper than the upper bound
    #: the bottom signal drops below the sensor floor; shallower than the
    #: lower bound the beach zone violates the log-ratio assumption (and the
    #: Hedley deglint's no-SWIR-water-leaving assumption).
    fit_reference_range_m: tuple[float, float] = (2.0, 25.0)

    #: Minimum pixels for a raster-referenced fit.
    min_fit_pixels: int = 200

    #: Fraction of fit pixels held out for error statistics. In-sample OLS
    #: residuals have a mean of exactly zero by construction, so an in-sample
    #: bias measures nothing.
    holdout_frac: float = 0.3

    #: Minimum depth span the calibration points must cover. A two-parameter
    #: fit over a 1 m range cannot be extrapolated across an AOI.
    min_depth_span_m: float = 5.0

    #: Checkerboard block size in pixels for spatially-blocked validation.
    #: Must exceed the depth correlation scale or held-out blocks leak. 50 px
    #: is 500 m at Sentinel-2 resolution.
    checkerboard_block_px: int = 50

    #: Boundary between the shallow and deep reporting strata, in metres.
    stratum_boundary_m: float = 12.0

    #: 1-sigma uncertainty on the coarse reference bathymetry used in the
    #: inverse-variance blend. EMODnet at ~115 m is worth about 3 m.
    reference_sigma_m: float = 3.0
    #: Gaussian smoothing applied to the raw Stumpf map before blending.
    #: Tames pixel noise while keeping geological structure. 0 disables.
    blend_smooth_sigma_px: float = 2.0

    #: A fitted slope near zero maps the whole scene into a few metres, and
    #: validation then looks excellent for the wrong reason. Flag the map as
    #: collapsed when its retrieved span falls below this fraction of the
    #: calibration span.
    min_span_fraction: float = 0.5

    #: Seed for the held-out split.
    rng_seed: int = 42

    # -- Terrain classification (slope + local relief) ----------------------
    #
    # Used to pick substrate classes from bathymetric *geometry* alone. The
    # point of doing it geometrically is that it is independent of optics:
    # selecting "sand" by its reflectance and then measuring that reflectance
    # is circular. Tyler et al. 2025 found slope, vector ruggedness and the
    # flat geomorphon to be the strongest discriminators of bare substrate.
    #
    # Defaults come from a lidar distribution over reef versus bay. They are
    # the entries most likely to need re-tuning at a new AOI, and the gap
    # between the flat and rugose bounds is deliberate — pixels in between
    # belong to neither class rather than being forced into one.

    #: A pixel is flat when slope and local relief are both below these.
    flat_max_slope_deg: float = 2.0
    flat_max_relief_m: float = 0.5

    #: A pixel is rugose when slope and local relief are both above these.
    rugose_min_slope_deg: float = 5.0
    rugose_min_relief_m: float = 1.5

    #: Window size in pixels for the local-relief (max - min) filter.
    relief_window_px: int = 3


# ---------------------------------------------------------------------------
# Empirical two-way attenuation (reef_calibration)
# ---------------------------------------------------------------------------


@dataclass
class AttenuationConfig:
    """Deep-water-referenced regression for empirical two-way k(λ) per band.

    Defaults reproduce the prototype's fit exactly, so the golden regression
    over the Sesimbra 2026-06-27 scene keeps its meaning. They are the
    prototype's values, not independently justified ones — re-tune per AOI.
    """

    #: Depth bin width (metres) for the ln(L - L_inf) versus depth regression.
    depth_bin_m: float = 1.0

    #: Minimum pixels per bin before that bin contributes a regression point.
    min_pixels_per_bin: int = 20

    #: Minimum depth bins required for a fit. Three points through a straight
    #: line says nothing about whether the decay is actually exponential.
    min_bins: int = 4

    #: Depth range over which the regression is fit. Below the shallow floor
    #: the bottom signal saturates the retrieval; above the deep bound it
    #: approaches the noise floor, where ln() amplifies the noise.
    depth_min_m: float = 1.0
    depth_max_m: float = 20.0

    #: Brightness quantile within each depth bin used as the substrate
    #: tracker. Substrate covaries with depth (kelp shallow, sediment deep),
    #: so regressing over all pixels would fold habitat stratification into k.
    #: Tracking a high quantile follows the brightest endmember instead.
    #: Two consequences are reported rather than hidden: the choice is a free
    #: parameter, so sensitivity across ``sensitivity_quantiles`` is always
    #: emitted; and favouring bright pixels favours positive noise, which
    #: biases k slightly low.
    quantile: float = 0.90

    #: Quantiles used for the sensitivity sweep reported alongside every fit.
    sensitivity_quantiles: tuple[float, ...] = (0.75, 0.80, 0.85, 0.90, 0.95)

    #: Minimum optically-deep pixels needed for a stable per-band reference.
    min_deep_reference_pixels: int = 50

    #: Wavelength (nm) considered the null channel — a_w is so large that
    #: any bottom signal is negligible. Sesimbra uses 667; PNeo can use
    #: red-edge ~710 for an even stronger null.
    null_channel_nm: float = 667.0

    # --- Fit-quality gates -------------------------------------------------
    # A regression always returns a slope, so these decide whether that slope
    # is a measurement. Calibrated against two runs of the same code: the
    # Sesimbra 2026-06-27 golden scene, where the bottom-carrying bands score
    # R2 0.87-0.91 with a quantile spread under 10% of k and a non-positive
    # residual fraction of 0.47-0.51 in every band; and a Lough Swilly control
    # fitted against a nearshore-only depth grid, which returned k = 0.0026 at
    # 665 nm from a contaminated deep-water reference while showing a spread of
    # 165% of k and residual fractions ranging from 0.21 to 0.80.

    #: Minimum R2 before a band's slope is treated as a measurement.
    min_r_squared: float = 0.5

    #: Maximum ``quantile_spread / |k|``. At or above this the fit is an
    #: artefact of the substrate tracker rather than a property of the water.
    max_quantile_spread_ratio: float = 0.35

    #: Upper bound on ``frac_nonpositive_residual``. The check is one-sided by
    #: necessity: ``ln`` needs a positive residual, so pixels below L_inf are
    #: dropped before fitting, and a reference contaminated by shallow water
    #: therefore discards most of the scene and fits the biased remnant. A
    #: *low* fraction is not evidence of anything — a wholly bottom-lit scene
    #: legitimately scores near zero.
    max_nonpositive_residual: float = 0.70

    #: Pure-water floor (per metre) at or above which a band cannot retain any
    #: bottom signal across the fit window. Used to detect a regression that is
    #: tracking a depth-correlated gradient instead of attenuation.
    opaque_floor_min_per_m: float = 1.0


# ---------------------------------------------------------------------------
# Quality-control diagnostics (ac_uncertainty, point_diagnostic)
# ---------------------------------------------------------------------------


@dataclass
class QCConfig:
    """Thresholds for the ground-truth-free diagnostics.

    None of these gate the retrieval. They exist to make a result auditable:
    every one of them turns a judgement that would otherwise be argued in prose
    into a number that can be recomputed.
    """

    # -- Atmospheric-correction uncertainty ---------------------------------
    #
    # A *uniform* additive offset does not impair class discrimination: it
    # shifts neighbouring pixels equally and cancels in their difference. What
    # corrupts discrimination is the *spatially varying* component, because
    # that is what masquerades as a substrate difference. So the threshold is
    # the pixel-to-pixel scatter among pixels that share substrate and depth,
    # not the scene's additive offset.

    #: Depth beyond which the null channel carries no recoverable bottom
    #: signal, so any remaining spatial structure is AC plus sensor noise.
    bottom_blind_depth_m: float = 10.0

    #: Band used as the bottom-blind reference (pure-water absorption makes
    #: bottom return impossible below ``bottom_blind_depth_m``).
    bottom_blind_nm: float = 667.0

    #: Band the classifier actually discriminates on. Comparing its scatter
    #: with the bottom-blind band isolates how much real substrate signal
    #: survives at depth — with no ground truth.
    discriminating_nm: float = 561.0

    #: Block sizes in metres for the within-block scatter sweep. Within-block
    #: scatter is the quantity that matters: it measures disagreement between
    #: pixels close enough that a classifier would be asked to tell them
    #: apart. Scene-wide SD would fold in large-scale gradients that cancel
    #: locally.
    scatter_block_sizes_m: tuple[float, ...] = (100.0, 300.0, 500.0, 1000.0)

    #: Minimum pixels in a block before it contributes a scatter estimate.
    min_pixels_per_block: int = 12

    #: Minimum bottom-blind pixels before the diagnostic is attempted.
    min_bottom_blind_pixels: int = 100

    #: Sensor band centres drift by a few nm between platforms, so bands are
    #: matched on proximity rather than equality.
    band_match_tolerance_nm: float = 8.0

    #: Green is taken to carry substrate signal when its scatter exceeds the
    #: bottom-blind scatter by this factor.
    substrate_signal_ratio: float = 1.5

    # -- Point diagnostic ---------------------------------------------------

    #: Plausible range for a natural seabed. Used only to FLAG, never to
    #: censor — censoring here would hide exactly the evidence the diagnostic
    #: exists to surface.
    plausible_rho_b: tuple[float, float] = (0.0, 0.5)

    #: Depth errors (m) swept to measure how fragile a retrieval is.
    depth_perturbations_m: tuple[float, ...] = (-2.0, -1.0, 0.0, 1.0, 2.0)

    #: Additive reflectance errors (rhos) swept for the same purpose.
    reflectance_perturbations_rhos: tuple[float, ...] = (-0.001, 0.0, 0.001)

    # -- Physical floor gates (Phase 3.3) -----------------------------------
    #
    # Both gates below run on the empirical k(λ) fit and need no ground truth,
    # which is what makes them deployable across hundreds of AOIs. They are
    # gates, not diagnostics: a scene that fails the pure-water floor has a
    # measurement problem, not a water-clarity result.

    #: Fractional slack allowed before a fitted k below the pure-water floor
    #: counts as a violation. 0.0 is strict, which is correct: the floor is a
    #: physical bound, not an estimate, so any margin here only hides a real
    #: failure. Raise it only to characterise near-misses.
    k_floor_slack: float = 0.0

    #: Solar zenith assumed when computing the pure-water floor and the
    #: expected k ratio, if the scene does not supply one. 35 deg matches
    #: ``BandCalibration.k_pure_water_floor`` so the two agree by default.
    floor_solar_zenith_deg: float = 35.0

    #: A band pair is worth testing for the common-mode signature only when
    #: pure water alone would put its k ratio below this. Blue/red qualifies
    #: (~0.035); blue/green does not (~0.3), because for a nearly flat pair a
    #: ratio near unity carries no information.
    ratio_contrast_max: float = 0.25

    #: |observed ratio - 1| below this flags the common-mode artefact: two
    #: bands that pure water alone separates by ~30x came back attenuating at
    #: the same rate, which an additive offset shared between them reproduces
    #: exactly and a water mass does not.
    ratio_unity_tolerance: float = 0.15

    #: Fractional slack on the ratio floor. An observed k ratio below the
    #: pure-water ratio is unphysical for a blue/red pair — every natural
    #: constituent absorbs or scatters more in the blue — so this is a hard
    #: bound with only measurement slack allowed.
    ratio_floor_slack: float = 0.10

    # -- Which bands the floor gates may score --------------------------------
    #
    # A floor is an assertion about physics, so it may only be made where the
    # physics is known and the measurement was possible. Two independent limits
    # disqualify a band, and both must be reported rather than silently applied,
    # because a band dropped without a reason looks like a band that passed.

    #: Upper wavelength (nm) at which pure-water absorption is still tabulated.
    #: ``optics.water.a_water`` extrapolates flat beyond the Pope & Fry table
    #: and says so; past this point ``kb_pure_water`` returns a number but not a
    #: physical floor, and at 866 nm that number is roughly 7x too low.
    floor_max_wavelength_nm: float = 700.0

    #: Pure-water floor (per metre) at or above which a band cannot retain bottom
    #: signal across the fit window, so its fitted k measures nothing to compare
    #: against a floor. Mirrors ``AttenuationConfig.opaque_floor_min_per_m``,
    #: which makes the same split for a different purpose: there it selects the
    #: bands used to *detect* a depth-correlated artefact, and those bands must
    #: keep being fitted for that check to work. Here it excludes them from being
    #: scored. Keep the two values in step.
    floor_opaque_min_per_m: float = 1.0

    # -- Additive offset (Phase 5) ------------------------------------------
    #
    # The offset is measured where water-leaving reflectance is physically
    # zero, so these thresholds bound the *validity of the sample*, not the
    # answer. None of them can be tuned to move the offset in a chosen
    # direction without also invalidating the band they are read from.

    #: Shortest wavelength taken to be black over optically deep water. Pure
    #: water absorption at 800 nm is ~2.1 m^-1, so the two-way transmission
    #: past the depths used here is negligible.
    offset_nir_min_nm: float = 800.0

    #: Minimum deep-water pixels before the offset is believable.
    min_offset_pixels: int = 50

    #: Bands at or above this are used only to check the deep-water sample for
    #: cloud and glint, never to estimate the offset itself.
    offset_swir_min_nm: float = 1500.0

    #: Median SWIR reflectance over the deep-water sample above which the
    #: sample is contaminated. Pure water absorbs ~670 m^-1 at 1600 nm, so any
    #: signal there is cloud, glint or foam rather than water.
    offset_swir_max: float = 0.01

    #: Spread across the NIR bands, as a fraction of the offset, beyond which
    #: a single scalar no longer describes the residual.
    offset_band_spread_max: float = 0.5


# ---------------------------------------------------------------------------
# Detectability (Phase 3)
# ---------------------------------------------------------------------------


@dataclass
class DetectabilityConfig:
    """How deep a substrate difference stays visible, and how much light gets there.

    This is the product the library exists to ship. It answers two questions
    that a habitat map cannot answer for itself:

    * ``z_max`` — below what depth is the *absence* of a detection
      uninformative, because no substrate difference could have survived the
      water column anyway?
    * seabed PAR — how much photosynthetically active radiation reaches the
      seabed, which bounds where the habitat could exist at all.

    Both are derived from the measured k(λ), so both inherit its uncertainty.
    Neither needs a label, which is why they scale to AOIs with no field data.
    """

    #: Subsurface-to-above-water reflectance factor (Lee & Carder 1998).
    #: A bottom contrast ``d_rho_b`` reaches the sensor as
    #: ``t_aw * d_rho_b * exp(-k * z)`` in surface-reflectance units.
    t_aw: float = 0.52

    #: Substrate pair whose contrast defines "detectable". Names index
    #: :data:`oceanstream.coastal.detectability.SUBSTRATE_ALBEDO`. Bare versus
    #: brown algae is the default because it is the separation the prototype
    #: measured as comfortably above the noise floor; brown versus red is the
    #: one it measured as below it, and choosing it here would report a much
    #: shallower z_max for the same water.
    contrast_pair: tuple[str, str] = ("bare", "brown")

    #: Tolerance for matching a fitted band to the contrast library's bands.
    contrast_band_tolerance_nm: float = 15.0

    #: Reflectance noise floor used when no AC-uncertainty diagnostic is
    #: available for the scene. 0.001 rhos is the prototype's per-pixel sensor
    #: noise estimate — optimistic, because the measured spatially-varying AC
    #: scatter ran 0.0006–0.0037 rhos across two scenes. Prefer passing the
    #: measured value; this exists so a scene without the diagnostic still
    #: produces a number, clearly flagged as a floor rather than a measurement.
    fallback_epsilon_rhos: float = 0.001

    #: Guard against a division that would send z_max to infinity.
    min_epsilon_rhos: float = 1e-6

    #: Bands whose z_max falls below this contribute nothing usable.
    min_useful_z_max_m: float = 1.0

    #: PAR integration window (nm) and sampling step. 400–700 nm is the
    #: standard PAR definition.
    par_range_nm: tuple[float, float] = (400.0, 700.0)
    par_step_nm: float = 10.0

    #: Weight PAR in quanta rather than energy. PAR is defined as a photon
    #: flux, and photon energy goes as 1/lambda, so the quantum weight is
    #: proportional to lambda. True is the correct default; False is offered
    #: for comparison with energy-based irradiance products.
    par_photon_weighted: bool = True

    #: Depth beyond which the seabed PAR fraction is not reported. Not a
    #: physical bound — the K_d used is fitted over a limited depth window
    #: (see :class:`AttenuationConfig`), so extrapolating far past it reports
    #: precision the fit does not have.
    par_max_depth_m: float = 40.0


@dataclass
class UncertaintyConfig:
    """Independent model-error estimates from validation, not fitting knobs.

    None leaves conditional error propagation available but prevents a claim
    that the uncertainty budget is complete. Source must identify the reference
    evidence used for these one-sigma relative errors.
    """

    empirical_relative_sigma: float | None = None
    qaa_relative_sigma: float | None = None
    source: str | None = None

    def __post_init__(self) -> None:
        import math

        for value in (self.empirical_relative_sigma, self.qaa_relative_sigma):
            if value is not None and (not math.isfinite(value) or value < 0):
                raise ValueError("Model uncertainties must be finite nonnegative fractions.")
        if (
            any(v is not None for v in (self.empirical_relative_sigma, self.qaa_relative_sigma))
            and not self.source
        ):
            raise ValueError("Model uncertainty estimates require an independent evidence source.")
