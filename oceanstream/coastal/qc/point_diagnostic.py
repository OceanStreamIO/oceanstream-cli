"""Known-depth point diagnostic: does the inversion work where depth is known?

Ported from ``point_diagnostic.py`` in Phase 1.6.

The retrieval has three unknowns — depth, water IOPs, and benthic reflectance —
and it is easy to judge it in a way that confounds them. Where depth is
independently measured it can be held fixed and the inversion asked a single
question: given correct depth, does it return a plausible benthic reflectance?

Two design choices make the answer usable:

* **Uncensored retrievals.** The production inversion maps values outside a
  plausible range to NaN, which hides exactly the evidence needed here. This
  module computes the closed form directly and reports the raw algebraic value
  alongside a separate validity flag. A negative rho_b is a finding, not a
  defect to be swept up.
* **Perturbation sensitivity.** Depth and reflectance are swept over plausible
  error bars, so the result's fragility is measured rather than argued. The
  amplification term ``exp(+k_b * H)`` means a metre of depth error at 16 m
  can move rho_b further than the difference between two substrates.

No habitat or ecological label is produced. This is a physics check.
"""

from __future__ import annotations

import logging

import numpy as np

from oceanstream.coastal.config import QCConfig
from oceanstream.coastal.inversion import lee
from oceanstream.coastal.optics import water

logger = logging.getLogger(__name__)


def invert_uncensored(
    rrs_above: float,
    depth_m: float,
    a: float,
    bb: float,
    solar_zenith_deg: float,
    config: QCConfig | None = None,
) -> dict[str, object]:
    """Closed-form inversion with no clipping, plus every term behind it.

    Returning the intermediate terms is the point. When ``rho_b`` comes back
    negative the useful question is whether the column term was
    over-subtracted or the bottom signal was never there, and that is only
    answerable if ``column_term``, ``bottom_residual`` and ``transmission``
    are visible next to the answer.
    """
    cfg = config or QCConfig()
    lo, hi = cfg.plausible_rho_b

    u = bb / (a + bb)
    inv_cos = 1.0 / np.cos(
        np.deg2rad(lee._subsurface_solar_zenith_deg(solar_zenith_deg))
    )
    beam_c = a + bb
    k_c = (inv_cos + lee._du_column(u)) * beam_c
    k_b = (inv_cos + lee._du_bottom(u)) * beam_c
    rrs_deep = 0.089 * u + 0.125 * u * u

    rrs_sub = water.rrs_above_to_subsurface(np.asarray(rrs_above, dtype=float))
    column = rrs_deep * (1.0 - np.exp(-k_c * depth_m))
    transmission = float(np.exp(-k_b * depth_m))
    rho_b = float(np.pi * (rrs_sub - column) * np.exp(k_b * depth_m))

    return {
        "rho_b_uncensored": rho_b,
        "rrs_subsurface": float(rrs_sub),
        "column_term": float(column),
        "bottom_residual": float(rrs_sub - column),
        "transmission": transmission,
        # How much the retrieval multiplies any error in the bottom residual.
        "amplification": (
            round(1.0 / transmission, 1) if transmission > 0 else None
        ),
        "k_b": float(k_b),
        "k_c": float(k_c),
        "rrs_deep": float(rrs_deep),
        "is_plausible": bool(lo <= rho_b <= hi),
        "is_negative": bool(rho_b < 0),
        "flag": (
            "ok"
            if lo <= rho_b <= hi
            else "negative_column_over_subtracted"
            if rho_b < 0
            else "implausibly_bright"
        ),
    }


def point_diagnostic(
    rrs_above: float,
    depth_m: float,
    a: float,
    bb: float,
    solar_zenith_deg: float,
    config: QCConfig | None = None,
) -> dict[str, object]:
    """How far does the answer move under small, plausible input errors?

    Reports the retrieval alongside its sensitivity to depth and to an
    additive reflectance offset, and names which of the two dominates. A
    result whose spread under a plausible depth error exceeds the difference
    between the substrates being separated is not evidence about substrate,
    however clean the retrieval looks.
    """
    cfg = config or QCConfig()
    baseline = invert_uncensored(
        rrs_above, depth_m, a, bb, solar_zenith_deg, cfg
    )

    by_depth: dict[str, float] = {}
    for delta in cfg.depth_perturbations_m:
        if depth_m + delta <= 0:
            continue
        value = invert_uncensored(
            rrs_above, depth_m + delta, a, bb, solar_zenith_deg, cfg
        )
        by_depth[f"{delta:+.0f}m"] = round(
            float(value["rho_b_uncensored"]),  # type: ignore[arg-type]
            5,
        )

    by_reflectance: dict[str, float] = {}
    for delta in cfg.reflectance_perturbations_rhos:
        value = invert_uncensored(
            rrs_above + delta / np.pi, depth_m, a, bb, solar_zenith_deg, cfg
        )
        by_reflectance[f"{delta:+.3f}"] = round(
            float(value["rho_b_uncensored"]),  # type: ignore[arg-type]
            5,
        )

    spread_depth = (
        max(by_depth.values()) - min(by_depth.values())
        if by_depth
        else float("nan")
    )
    spread_refl = (
        max(by_reflectance.values()) - min(by_reflectance.values())
        if by_reflectance
        else float("nan")
    )

    return {
        "retrieval": baseline,
        "baseline_rho_b": round(
            float(baseline["rho_b_uncensored"]),  # type: ignore[arg-type]
            5,
        ),
        "by_depth_error": by_depth,
        "by_reflectance_offset": by_reflectance,
        "spread_from_depth": round(float(spread_depth), 5),
        "spread_from_reflectance": round(float(spread_refl), 5),
        "dominant_error_source": (
            "depth" if spread_depth > spread_refl else "reflectance_offset"
        ),
    }
