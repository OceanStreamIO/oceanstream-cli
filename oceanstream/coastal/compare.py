"""Cross-AOI comparison of retrieved optics.

This harness compares regional empirical fits across water types using the same
input preparation and acceptance checks as the processor. The additive-offset
assessment is diagnostic: a spatially uniform offset cancels from L - L_inf and
cannot repair the empirical attenuation slope.

Results are keyed by **band role**, not wavelength. Sentinel-2A, 2B and 2C place
their band centres a few nm apart, so a table columned by wavelength would put
the same physical band in three different columns and make the sites look more
different than they are.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from oceanstream.coastal.config import DetectabilityConfig, QCConfig
from oceanstream.coastal.detectability import scene_detectability
from oceanstream.coastal.optics.attenuation import lyzenga_ratios
from oceanstream.coastal.scene import Scene

logger = logging.getLogger(__name__)

#: Column order for the comparison table. Roles the retrieval actually reads;
#: NIR and SWIR are masking bands and carry no bottom signal to fit.
REPORTED_ROLES: tuple[str, ...] = ("blue", "blue_green", "green", "red")

#: Marks a band whose fitted k fell below the pure-water floor and was replaced
#: by it. The number that follows is a bound, not a measurement.
FLOOR_MARK = "!"


@dataclass(frozen=True)
class SceneOptics:
    """One scene's fitted attenuation, detection limits and QC verdict."""

    label: str
    acolite_dir: str
    bathymetry: str
    scene: dict[str, Any]
    solar_zenith_deg: float
    epsilon_rhos: float | None
    epsilon_source: str
    n_water_px: int
    n_deep_px: int
    bands: dict[str, dict[str, Any]] = field(default_factory=dict)
    ratios: dict[str, float] = field(default_factory=dict)
    verdict: dict[str, Any] = field(default_factory=dict)
    detectability: dict[str, Any] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return bool(self.verdict.get("passed", False))

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "acolite_dir": self.acolite_dir,
            "bathymetry": self.bathymetry,
            "scene": self.scene,
            "solar_zenith_deg": self.solar_zenith_deg,
            "epsilon_rhos": self.epsilon_rhos,
            "epsilon_source": self.epsilon_source,
            "n_water_px": self.n_water_px,
            "n_deep_px": self.n_deep_px,
            "bands": self.bands,
            "lyzenga_ratios": self.ratios,
            "verdict": self.verdict,
            "detectability": self.detectability,
        }


def scene_optics(
    label: str,
    acolite_dir: str | Path,
    bathymetry: str | Path,
    sensor: str = "sentinel2",
    solar_zenith_fallback_deg: float = 35.0,
    qc_config: QCConfig | None = None,
    detectability_config: DetectabilityConfig | None = None,
    *,
    aoi: Any = None,
    mask_config: Any = None,
    bathymetry_config: Any = None,
    attenuation_config: Any = None,
    tide_correction: Any = None,
    uncertainty_config: Any = None,
) -> SceneOptics:
    """Fit k, measure the noise floor, and judge one scene against the floors.

    This is the single-scene half of the Phase 4 comparison. It runs the same
    sequence as the ``coastal attenuation`` command and additionally measures
    the scene's own reflectance noise floor, because a detection limit computed
    from the configured fallback ε is optimistic by roughly a factor of four and
    would flatter every site equally.
    """
    scene = Scene.from_acolite_dir(
        acolite_dir, sensor=sensor, solar_zenith_fallback_deg=solar_zenith_fallback_deg
    )
    profile = scene.sensor
    roles = profile.resolve_roles(scene.wavelengths_nm)

    from oceanstream.coastal.aoi import AOI, BathymetryReference
    from oceanstream.coastal.stac.coastal_emit import geographic_bounds
    from oceanstream.coastal.stages import analyze_attenuation, attenuation_verdict, prepare_scene

    if aoi is None:
        aoi = AOI(
            name=label,
            processing_bbox=geographic_bounds(scene.grid),
            fine_bathymetry=BathymetryReference(uri=str(bathymetry)),
        )
    prepared = prepare_scene(
        aoi,
        scene,
        mask_config=mask_config,
        bathymetry_config=bathymetry_config,
        tide=tide_correction,
    )
    analyses = analyze_attenuation(prepared, config=attenuation_config, qc_config=qc_config)
    if len(analyses) != 1:
        raise ValueError(
            "scene_optics summarizes one region; use CoastalProcessor for multiple regions."
        )
    analysis = next(iter(analyses.values()))
    calibrations = analysis["calibrations"]
    water, deep = prepared.valid, prepared.deep
    zenith = float(scene.solar_zenith_deg)
    from oceanstream.coastal.config import UncertaintyConfig

    verdict = attenuation_verdict(
        prepared,
        analysis,
        uncertainty=uncertainty_config or UncertaintyConfig(),
        qc_config=qc_config,
    )
    epsilon = analysis["epsilon"]
    detect = scene_detectability(
        calibrations, epsilon_rhos=epsilon, solar_zenith_deg=zenith, config=detectability_config
    )

    nm_to_role = {float(scene.wavelengths_nm[i]): role for role, i in roles.items()}
    bands: dict[str, dict[str, Any]] = {}
    for nm, cal in calibrations.items():
        role = nm_to_role.get(float(nm), f"{nm:.0f}nm")
        band = detect.bands.get(float(nm))
        bands[role] = {
            "wavelength_nm": float(nm),
            "k_per_m": cal.k_per_m,
            "k_pure_water_floor": cal.k_pure_water_floor,
            "below_pure_water_floor": cal.k_below_pure_water_floor,
            "r_squared": cal.r_squared,
            "n_pixels": cal.n_pixels,
            "frac_nonpositive_residual": cal.frac_nonpositive_residual,
            "quantile_spread": cal.quantile_spread,
            "trustworthy": cal.trustworthy,
            "trust_failures": list(cal.trust_failures),
            "z_max_m": band.z_max_m if band else float("nan"),
            "usable": bool(band.usable) if band else False,
        }

    return SceneOptics(
        label=label,
        acolite_dir=str(acolite_dir),
        bathymetry=str(bathymetry),
        scene=scene.describe(),
        solar_zenith_deg=zenith,
        epsilon_rhos=epsilon,
        epsilon_source=detect.epsilon_source,
        n_water_px=int(water.sum()),
        n_deep_px=int(deep.sum()),
        bands=bands,
        ratios=lyzenga_ratios(calibrations),
        verdict=verdict,
        detectability=detect.to_dict()
        | {
            "numerical_status": detect.status,
            "status": detect.status if verdict["passed"] else "diagnostic_only",
        },
    )


def _scalar_epsilon(summary: dict[str, object]) -> float | None:
    """Pull the scalar noise threshold out of the AC-uncertainty diagnostic."""
    for key in ("effective_threshold_rhos", "epsilon_rhos", "threshold_rhos"):
        value = summary.get(key)
        if isinstance(value, (int, float)) and np.isfinite(float(value)):
            return float(value)
    return None


def comparison_table(results: Sequence[SceneOptics]) -> str:
    """Fixed-width table of k, z_max and QC verdict, one row per scene.

    Bands that were floored are marked, because the substituted value is the
    minimum possible attenuation and therefore yields the maximum possible
    z_max: reading it as a measurement inverts its meaning.
    """
    if not results:
        return "no scenes compared"

    roles = [r for r in REPORTED_ROLES if any(r in res.bands for res in results)]
    label_w = max(len(res.label) for res in results)
    label_w = max(label_w, len("scene"))

    header = f"{'scene':<{label_w}}  " + "".join(f"{r:>12}" for r in roles)
    header += f"{'best':>8}{'z_max':>9}{'eps':>10}  QC"
    lines = [header, "-" * len(header)]

    for res in results:
        row = f"{res.label:<{label_w}}  "
        for role in roles:
            band = res.bands.get(role)
            if band is None:
                row += f"{'-':>12}"
                continue
            mark = FLOOR_MARK if band["below_pure_water_floor"] else " "
            row += f"{band['k_per_m']:>11.5f}{mark}"
        best = res.detectability.get("best_band_nm")
        z_max = res.detectability.get("z_max_m", float("nan"))
        row += f"{best if best else '-':>8}"
        row += f"{z_max:>8.1f}m" if np.isfinite(z_max) else f"{'-':>9}"
        row += f"{res.epsilon_rhos:>10.5f}" if res.epsilon_rhos else f"{'-':>10}"
        row += "  PASS" if res.passed else "  FAIL"
        lines.append(row)

    lines.append("")
    lines.append(f"{FLOOR_MARK} = k below the pure-water floor; z_max is an upper bound.")
    lines.append("")
    for res in results:
        ratio = ", ".join(f"{k}={v:.3f}" for k, v in sorted(res.ratios.items()))
        lines.append(f"{res.label}: {res.verdict.get('summary', '')}")
        if ratio:
            lines.append(f"{' ' * len(res.label)}  ratios: {ratio}")
    return "\n".join(lines)
