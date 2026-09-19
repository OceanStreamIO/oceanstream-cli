"""Physical-floor QC gates on the empirical k(lambda) fit.

Two gates, both derived from pure-water optics alone. Neither needs a diver, a
sonde, or a reference bathymetry, and that is the entire point: a check that
requires ground truth can only run where ground truth exists, which is nowhere
near most of the coastline this library is meant to cover.

**The pure-water floor.** Nothing attenuates less than pure water. The Lee
two-way attenuation for pure water at 667 nm is 0.43 m^-1, driven by the water
molecule's own absorption band. An empirical fit returning 0.077 m^-1 there has
not measured unusually clear water; it has measured something that is not
attenuation.

**The Lyzenga ratio.** ``k(blue)/k(red)`` for pure water is about 0.035, and
every natural constituent pushes it higher — CDOM, phytoplankton pigment and
particle backscatter all act more strongly in the blue. So the pure-water ratio
is a floor, and a ratio near 1.0 says both bands came back attenuating at the
same rate. A water mass does not do that. A single additive offset shared
between the two bands reproduces it exactly.

The second gate matters because it survives a case the first one misses. If a
depth-correlated artefact inflates *every* band, each band can clear its own
floor while the ratio between them stays pinned near unity. The floor check
sees nothing wrong; the ratio check does.

What a failure means
--------------------
Not "this water is unusual". A failed scene has an artefact in the
reflectance-versus-depth relationship that the fit has absorbed — residual
atmospheric correction, sun glint that tracks bathymetry, adjacency from a
nearby shore, or a depth reference misaligned with the imagery. Products built
on that k are not conservative, they are wrong in an unknown direction. Gate on
them rather than shipping them with a caveat.

Which bands may be scored
-------------------------
Both gates score only bands where a floor is a meaningful claim. Two limits
disqualify a band, for unrelated reasons:

*Beyond the tabulated absorption.* ``optics.water.a_water`` interpolates Pope &
Fry 1997, which stops at 700 nm, and extrapolates flat above it. So
``kb_pure_water(866)`` returns a number computed from a_w = 0.624 when the true
value is near 4.6 — about 7x too low. Asserting a floor there states a physical
limit we do not have.

*Opaque within the fit window.* At 707 nm pure water alone extinguishes the
bottom signal inside about 3.5 m, against a fit window running to 20 m. The
regression over such a band has no bottom decay to track, so its slope is not an
attenuation that can be under a floor — it is noise, or a gradient that merely
correlates with depth.

Excluded bands are listed in ``skipped_bands`` with a reason, never dropped
quietly: a band that vanishes from a report is indistinguishable from a band
that passed. They also keep being *fitted*, because
:func:`~oceanstream.coastal.optics.attenuation.depth_correlated_artefact`
detects a common-mode gradient precisely by noticing that an opaque band fits
better than a bottom-carrying one.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import numpy as np

from oceanstream.coastal.config import QCConfig
from oceanstream.coastal.inversion import lee

logger = logging.getLogger(__name__)

#: Accepted for every ``k_by_band`` argument below: either a plain
#: wavelength -> k mapping, or the ``calibrate_bands`` output directly.
KByBand = Mapping[float, Any]


def _as_k_map(k_by_band: KByBand) -> dict[float, float]:
    """Normalise ``{wl: k}`` or ``{wl: BandCalibration}`` to ``{wl: k}``.

    Reads the raw ``k_per_m``, never ``k_effective``. ``k_effective`` is already
    floored at pure water, so gating on it would compare the floor with itself
    and report every scene as passing.
    """
    out: dict[float, float] = {}
    for wavelength, value in k_by_band.items():
        k = getattr(value, "k_per_m", value)
        out[float(wavelength)] = float("nan") if k is None else float(k)
    return out


def _assessable_bands(
    k_map: Mapping[float, float],
    zenith: float,
    config: QCConfig,
) -> tuple[dict[float, float], dict[str, str]]:
    """Split bands into those a floor claim applies to, and those it does not.

    Returns ``(assessable, skipped)`` where ``skipped`` maps the band label to
    the reason it cannot be scored. See the module docstring for why each limit
    exists.
    """
    assessable: dict[float, float] = {}
    skipped: dict[str, str] = {}
    for wavelength, k in k_map.items():
        if wavelength > config.floor_max_wavelength_nm:
            skipped[f"{wavelength:.0f}"] = (
                f"no tabulated pure-water absorption above {config.floor_max_wavelength_nm:.0f} nm"
            )
            continue
        floor = float(lee.kb_pure_water(wavelength, zenith))
        if floor >= config.floor_opaque_min_per_m:
            skipped[f"{wavelength:.0f}"] = (
                f"pure water extinguishes the bottom signal within "
                f"{4.6 / floor:.1f} m; no decay to measure"
            )
            continue
        assessable[wavelength] = k
    return assessable, skipped


def pure_water_floor_check(
    k_by_band: KByBand,
    solar_zenith_deg: float | None = None,
    config: QCConfig | None = None,
) -> dict[str, Any]:
    """Verify every fitted ``k(lambda)`` clears the pure-water attenuation floor.

    Parameters
    ----------
    k_by_band
        Band centre (nm) to fitted two-way ``k`` (1/m), or the
        :func:`~oceanstream.coastal.optics.attenuation.calibrate_bands` output.
    solar_zenith_deg
        Scene solar zenith. Defaults to ``config.floor_solar_zenith_deg`` so the
        floor agrees with ``BandCalibration.k_pure_water_floor``. The floor is
        only weakly geometric — about 8% between nadir and 55 deg — so this
        rarely decides a verdict.

    Returns
    -------
    dict
        ``passed`` is False if any band is below its floor. ``bands`` carries
        per-band ``ratio = k / floor``; below 1 is unphysical. ``worst_ratio``
        is the headline number: 0.18 means the fit found under a fifth of the
        attenuation pure water alone guarantees. ``skipped_bands`` names the
        bands no floor claim applies to, with the reason for each.
    """
    cfg = config or QCConfig()
    zenith = cfg.floor_solar_zenith_deg if solar_zenith_deg is None else solar_zenith_deg
    k_map, skipped = _assessable_bands(_as_k_map(k_by_band), zenith, cfg)

    bands: dict[str, dict[str, Any]] = {}
    violations: list[float] = []
    for wavelength, k in sorted(k_map.items()):
        floor = float(lee.kb_pure_water(wavelength, zenith))
        ratio = float(k / floor) if floor > 0 and np.isfinite(k) else float("nan")
        violated = bool(np.isfinite(ratio) and ratio < (1.0 - cfg.k_floor_slack))
        if violated:
            violations.append(wavelength)
        bands[f"{wavelength:.0f}"] = {
            "wavelength_nm": wavelength,
            "k_per_m": k,
            "floor_per_m": floor,
            "ratio": ratio,
            "violated": violated,
            "shortfall_per_m": float(floor - k) if violated else 0.0,
        }

    finite = {wl: b for wl, b in bands.items() if np.isfinite(b["ratio"])}
    worst = min(finite.values(), key=lambda b: b["ratio"]) if finite else None
    flags: list[str] = []
    if not finite:
        flags.append("no_assessable_band" if skipped and not bands else "no_finite_k_to_check")
    if violations:
        flags.append("k_below_pure_water_floor")

    result: dict[str, Any] = {
        "passed": not violations and bool(finite),
        "solar_zenith_deg": zenith,
        "n_bands": len(bands),
        "n_violations": len(violations),
        "violating_bands_nm": violations,
        "worst_band_nm": float(worst["wavelength_nm"]) if worst else None,
        "worst_ratio": float(worst["ratio"]) if worst else float("nan"),
        "bands": bands,
        "skipped_bands": skipped,
        "flags": flags,
    }
    if violations:
        logger.warning(
            "pure-water floor violated in %d band(s): %s (worst ratio %.3f)",
            len(violations),
            ", ".join(f"{wl:.0f} nm" for wl in violations),
            result["worst_ratio"],
        )
    return result


def lyzenga_ratio_check(
    k_by_band: KByBand,
    solar_zenith_deg: float | None = None,
    config: QCConfig | None = None,
) -> dict[str, Any]:
    """Verify spectrally-contrasted ``k`` ratios stay above their pure-water value.

    Only band pairs whose pure-water ratio is below ``config.ratio_contrast_max``
    are tested, and only among bands a floor claim applies to at all (see the
    module docstring). Blue/red qualifies at about 0.035; blue/green does not, at
    about 0.3, and testing it would only add noise — when two bands are nearly
    equally attenuated by water, a ratio near 1 is the expected result and says
    nothing.

    Two ways to fail, recorded separately because they point at different
    problems:

    ``ratio_below_pure_water``
        Physically impossible. Every constituent raises the blue-to-red ratio.

    ``ratio_near_unity``
        Physically reachable only in the limit of heavy turbidity, where a
        spectrally flat particle term dominates. In the clear water this library
        targets it is instead the signature of an additive offset shared between
        bands, which cancels the spectral shape out of the ratio and drives it
        towards 1.
    """
    cfg = config or QCConfig()
    zenith = cfg.floor_solar_zenith_deg if solar_zenith_deg is None else solar_zenith_deg
    k_map, _ = _assessable_bands(_as_k_map(k_by_band), zenith, cfg)
    wavelengths = sorted(k_map)

    pairs: dict[str, dict[str, Any]] = {}
    hard: list[str] = []
    soft: list[str] = []
    for i, wl_i in enumerate(wavelengths):
        for wl_j in wavelengths[i + 1 :]:
            expected = float(lee.kb_pure_water(wl_i, zenith)) / float(
                lee.kb_pure_water(wl_j, zenith)
            )
            if expected > cfg.ratio_contrast_max:
                continue  # Not a discriminating pair; a ratio near 1 is normal.

            k_i, k_j = k_map[wl_i], k_map[wl_j]
            if not (np.isfinite(k_i) and np.isfinite(k_j)) or k_j <= 0.0:
                continue
            observed = float(k_i / k_j)

            below = bool(observed < expected * (1.0 - cfg.ratio_floor_slack))
            near_unity = bool(abs(observed - 1.0) <= cfg.ratio_unity_tolerance)
            key = f"{wl_i:.0f}/{wl_j:.0f}"
            if below:
                hard.append(key)
            if near_unity:
                soft.append(key)
            pairs[key] = {
                "wavelengths_nm": [wl_i, wl_j],
                "observed": observed,
                "expected_pure_water": expected,
                "ratio_to_expected": observed / expected,
                "below_pure_water": below,
                "near_unity": near_unity,
            }

    flags: list[str] = []
    if not pairs:
        flags.append("no_discriminating_band_pair")
    if hard:
        flags.append("ratio_below_pure_water")
    if soft:
        flags.append("ratio_near_unity")

    if hard or soft:
        logger.warning(
            "Lyzenga ratio check failed: %d below pure water, %d near unity",
            len(hard),
            len(soft),
        )
    return {
        "passed": not hard and not soft and bool(pairs),
        "solar_zenith_deg": zenith,
        "n_pairs": len(pairs),
        "below_pure_water_pairs": hard,
        "near_unity_pairs": soft,
        "pairs": pairs,
        "flags": flags,
    }


def _failure_reasons(floor: dict[str, Any], ratio: dict[str, Any]) -> list[str]:
    """Human-readable reasons a scene failed, in the order they matter."""
    reasons: list[str] = []
    if "no_assessable_band" in floor["flags"]:
        reasons.append(
            "no band could be scored against a floor ("
            + "; ".join(f"{nm} nm: {why}" for nm, why in floor["skipped_bands"].items())
            + ")"
        )
    if "no_finite_k_to_check" in floor["flags"]:
        reasons.append("no finite k values were fitted")
    if floor["n_violations"]:
        worst = floor["worst_band_nm"]
        reasons.append(
            f"{floor['n_violations']} band(s) below the pure-water floor"
            + (
                f" (worst {worst:.0f} nm at {floor['worst_ratio']:.2f}x)"
                if worst is not None
                else ""
            )
        )
    if ratio["below_pure_water_pairs"]:
        reasons.append("k ratio below pure water for " + ", ".join(ratio["below_pure_water_pairs"]))
    if ratio["near_unity_pairs"]:
        reasons.append(
            "k ratio near unity for "
            + ", ".join(ratio["near_unity_pairs"])
            + " (common-mode additive artefact)"
        )
    if "no_discriminating_band_pair" in ratio["flags"]:
        reasons.append("no spectrally contrasted band pair available to test")
    return reasons


def scene_floor_verdict(
    k_by_band: KByBand,
    solar_zenith_deg: float | None = None,
    config: QCConfig | None = None,
) -> dict[str, Any]:
    """Run both gates and combine them into the verdict carried by the STAC item.

    ``passed`` is the conjunction. A scene that fails should not have its
    downstream products published as measurements — the failure is in the
    reflectance-versus-depth relationship the whole retrieval rests on, so
    everything built above it inherits it.

    ``summary`` is a one-line reason so the verdict stays legible in a STAC
    browser without expanding the nested dicts.
    """
    floor = pure_water_floor_check(k_by_band, solar_zenith_deg, config)
    ratio = lyzenga_ratio_check(k_by_band, solar_zenith_deg, config)
    passed = bool(floor["passed"] and ratio["passed"])
    fit_failures = {
        f"{nm:g}": list(value.trust_failures)
        for nm, value in k_by_band.items()
        if hasattr(value, "trust_failures")
        and value.trust_failures
        and f"{nm:.0f}" not in floor["skipped_bands"]
    }
    if fit_failures:
        passed = False
    n_skipped = len(floor["skipped_bands"])
    # Named even on success, so "all bands passed" cannot be read as "all bands".
    skipped_note = (
        f" {n_skipped} band(s) carry no floor claim: " + ", ".join(floor["skipped_bands"]) + "."
        if n_skipped
        else ""
    )
    core = (
        f"k(lambda) clears the pure-water floor in all {floor['n_bands']} band(s) "
        f"and {ratio['n_pairs']} discriminating ratio(s) are physical"
        if passed
        else "; ".join(_failure_reasons(floor, ratio))
    )
    summary = core.rstrip(".") + "." + skipped_note
    if fit_failures:
        summary += (
            " Unreliable fits: "
            + "; ".join(f"{nm} nm ({', '.join(reasons)})" for nm, reasons in fit_failures.items())
            + "."
        )
    return {
        "passed": passed,
        "summary": summary,
        "flags": sorted(
            set(floor["flags"])
            | set(ratio["flags"])
            | {reason for reasons in fit_failures.values() for reason in reasons}
        ),
        "fit_failures": fit_failures,
        "checks": {"pure_water_floor": floor, "lyzenga_ratio": ratio},
    }
