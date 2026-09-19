"""Standalone coastal processing with independent inputs and product verdicts.

Every invocation writes an isolated run. Execution completion is independent of
scientific acceptance; only a complete run receives a manifest and latest pointer.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import numpy as np

from oceanstream.coastal.aoi import AOI
from oceanstream.coastal.bathymetry.tide import TideCorrection
from oceanstream.coastal.config import (
    AttenuationConfig,
    BathymetryConfig,
    DetectabilityConfig,
    MaskConfig,
    QCConfig,
    RetrievalConfig,
    UncertaintyConfig,
)
from oceanstream.coastal.scene import Scene

logger = logging.getLogger(__name__)
STATUS_PASSED = "passed"
STATUS_REJECTED = "rejected"
STATUS_INSUFFICIENT_DATA = "insufficient_data"
STATUS_EXECUTION_FAILED = "execution_failed"


@dataclass
class CoastalResult:
    campaign_id: str
    aoi_name: str
    scene_date: dt.date | None = None
    output_dir: Path | None = None
    products: dict[str, str] = field(default_factory=dict)
    qc_verdict: dict[str, Any] = field(default_factory=dict)
    status: str = STATUS_EXECUTION_FAILED
    success: bool = False
    message: str = ""
    stac_item: Path | None = None
    stac_collection: Path | None = None
    report: dict[str, Any] = field(default_factory=dict)
    product_verdicts: dict[str, dict[str, Any]] = field(default_factory=dict)

    @property
    def accepted(self) -> bool:
        return self.success and self.status == STATUS_PASSED


class CoastalProcessor:
    def __init__(
        self,
        config: RetrievalConfig | None = None,
        campaign_id: str | None = None,
        verbose: bool = False,
        *,
        mask_config: MaskConfig | None = None,
        bathymetry_config: BathymetryConfig | None = None,
        attenuation_config: AttenuationConfig | None = None,
        qc_config: QCConfig | None = None,
        detectability_config: DetectabilityConfig | None = None,
        uncertainty_config: UncertaintyConfig | None = None,
    ) -> None:
        self.config = config or RetrievalConfig()
        self.campaign_id = campaign_id or "coastal"
        self.verbose = verbose
        self.mask_config = mask_config or MaskConfig()
        self.bathymetry_config = bathymetry_config or BathymetryConfig()
        self.attenuation_config = attenuation_config or AttenuationConfig()
        self.qc_config = qc_config or QCConfig()
        self.detectability_config = detectability_config or DetectabilityConfig()
        self.uncertainty_config = uncertainty_config or UncertaintyConfig()

    def run(
        self,
        aoi: AOI,
        scene: Scene,
        output_dir: str | Path,
        *,
        emit_stac: bool = True,
        n_clusters: int = 4,
        tide_correction: TideCorrection | None = None,
    ) -> CoastalResult:
        from oceanstream.coastal.products import write_json_document
        from oceanstream.coastal.provenance import complete_run, new_run_directory

        result = CoastalResult(self.campaign_id, aoi.name, scene.acquisition_date)
        if self.verbose:
            logging.getLogger("oceanstream.coastal").setLevel(logging.INFO)
        try:
            result.output_dir = new_run_directory(output_dir)
            self._run(aoi, scene, result, emit_stac, n_clusters, tide_correction)
            complete_run(
                Path(output_dir),
                result.output_dir,
                {
                    "status": result.status,
                    "products": result.products,
                    "product_verdicts": result.product_verdicts,
                    "stac_item": str(result.stac_item) if result.stac_item else None,
                },
            )
            result.success = True
            result.message = (
                "Retrieval complete; products accepted."
                if result.accepted
                else "Retrieval complete; inspect per-product rejection reasons."
            )
        except InsufficientData as exc:
            result.status = STATUS_INSUFFICIENT_DATA
            result.message = str(exc)
        except Exception as exc:
            result.status = STATUS_EXECUTION_FAILED
            result.message = f"{type(exc).__name__}: {exc}"
            logger.exception("Coastal retrieval failed")
        if not result.success and result.output_dir is not None:
            # A failed manifest write must not leave a completed marker behind.
            marker = result.output_dir / "manifest.json"
            if marker.exists():
                marker.unlink()
            try:
                write_json_document(
                    result.output_dir / "failure.json",
                    {
                        "status": result.status,
                        "message": result.message,
                        "diagnostics": result.report,
                    },
                )
            except OSError:
                logger.warning("Could not persist failure report", exc_info=True)
        return result

    def _run(
        self,
        aoi: AOI,
        scene: Scene,
        result: CoastalResult,
        emit_stac_item: bool,
        n_clusters: int,
        tide: TideCorrection | None,
    ) -> None:
        from oceanstream.coastal.bathymetry.stumpf import (
            apply_stumpf,
            blend_stumpf,
            depth_map_diagnostics,
            fit_stumpf,
            validate_against_reference_blocks,
        )
        from oceanstream.coastal.classify import classify_bottom
        from oceanstream.coastal.detectability import scene_detectability, seabed_par_fraction
        from oceanstream.coastal.inversion.lee import invert_scene, optical_depth_score
        from oceanstream.coastal.optics.attenuation import lyzenga_ratios
        from oceanstream.coastal.optics.qaa import fit_scene_iops
        from oceanstream.coastal.products import ProductWriter, jsonable
        from oceanstream.coastal.provenance import input_provenance
        from oceanstream.coastal.qc.controls import compare_with_qaa, sand_control
        from oceanstream.coastal.qc.verdicts import combine, verdict
        from oceanstream.coastal.stages import (
            analyze_attenuation,
            attenuation_verdict,
            prepare_scene,
            reference_split,
        )
        from oceanstream.coastal.uncertainty import detection_sigma, propagate

        try:
            prepared = prepare_scene(
                aoi,
                scene,
                mask_config=self.mask_config,
                bathymetry_config=self.bathymetry_config,
                tide=tide,
            )
        except ValueError as exc:
            raise InsufficientData(str(exc)) from exc
        result.report = {
            "depth_reference": prepared.provenance,
            "input_flags": prepared.flags,
            "scene": scene.describe(),
        }
        if prepared.deep.sum() < self.config.min_deep_pixels:
            raise InsufficientData(
                f"Only {int(prepared.deep.sum())} independently screened deep-water pixels; "
                f"need {self.config.min_deep_pixels}."
            )
        try:
            regions = analyze_attenuation(
                prepared, config=self.attenuation_config, qc_config=self.qc_config
            )
        except ValueError as exc:
            raise InsufficientData(str(exc)) from exc
        result.report["attenuation"] = {
            key: {"bands": jsonable(row["calibrations"]), "noise": jsonable(row["noise"])}
            for key, row in regions.items()
        }
        if not any(
            np.isfinite(c.k_per_m) for row in regions.values() for c in row["calibrations"].values()
        ):
            raise InsufficientData(
                "No band produced a finite attenuation fit in the requested regions."
            )
        theta, grid = scene.solar_zenith_deg, scene.grid
        iops = fit_scene_iops(
            scene.rrs_above, scene.wavelengths_nm, prepared.deep, theta, config=self.config
        )
        roles = prepared.roles
        assert scene.rhos is not None
        blue = scene.rhos[roles.get("blue_green", roles["blue"])]
        green = scene.rhos[roles["green"]]
        bathy_config = replace(self.bathymetry_config, depth_valid_range_m=aoi.depth_valid_range_m)
        train, evaluation = reference_split(prepared, bathy_config)
        sdb = np.full(scene.shape, np.nan, np.float32)
        depth, depth_sigma = prepared.depth.copy(), prepared.depth_sigma.copy()
        fit = None
        depth_diagnostics: dict[str, Any] = {
            "validation_scheme": "native_reference_blocks_with_interpolation_buffer"
        }
        try:
            fit = fit_stumpf(
                blue,
                green,
                prepared.depth,
                prepared.valid,
                config=bathy_config,
                calibration_mask=train,
                evaluation_mask=evaluation,
            )
            sdb = apply_stumpf(blue, green, fit, prepared.valid, bathy_config)
            depth, blend_sigma = blend_stumpf(
                sdb, prepared.depth, fit, bathy_config, reference_sigma_m=prepared.depth_sigma
            )
            # Both estimates inherit reference error: don't claim that blending
            # correlated estimates removed that shared error.
            depth_sigma = np.maximum(blend_sigma, prepared.depth_sigma)
            scores = validate_against_reference_blocks(
                sdb, prepared.depth, prepared.valid & (train | evaluation), train, bathy_config
            )
            depth_diagnostics.update(
                {
                    "stumpf": "fitted",
                    "validation": jsonable(scores),
                    "n_evaluation_pixels": int((evaluation & np.isfinite(sdb)).sum()),
                    "map": depth_map_diagnostics(
                        sdb, np.ptp(bathy_config.fit_reference_range_m), bathy_config
                    ),
                }
            )
        except (ValueError, RuntimeError) as exc:
            depth_diagnostics.update(
                {"stumpf": "failed", "reason": str(exc), "n_evaluation_pixels": 0}
            )
        if (
            fit is None
            or not np.isfinite(fit.rmse_m)
            or depth_diagnostics.get("map", {}).get("collapsed", True)
        ):
            depth, depth_sigma = prepared.depth.copy(), prepared.depth_sigma.copy()
            depth_diagnostics["retrieval_depth_source"] = "reference_only"
        else:
            depth_diagnostics["retrieval_depth_source"] = "reference_sdb_blend"
        reporting = prepared.reporting & np.logical_or.reduce(list(prepared.regions.values()))
        optical_depth = optical_depth_score(depth, prepared.kd_490)
        invertible = (
            reporting & np.isfinite(depth) & (optical_depth <= self.mask_config.optical_depth_max)
        )
        rho_b = invert_scene(scene.rrs_above, scene.wavelengths_nm, depth, iops, invertible, theta)
        par = seabed_par_fraction(
            depth, iops.kd, scene.wavelengths_nm, theta, self.detectability_config
        )
        clusters = None
        cluster_failure = None
        try:
            clusters = classify_bottom(
                rho_b,
                invertible,
                scene.wavelengths_nm,
                n_clusters=n_clusters,
                rng_seed=self.mask_config.rng_seed,
            )
        except ValueError as exc:
            cluster_failure = str(exc)

        common = list(prepared.flags)
        if scene.sensor.name != "sentinel2":
            common.append("sensor_experimental")
        if not reporting.any():
            common.append("no_reporting_coverage")
        depth_flags = [
            f
            for f in common
            if f not in {"calibration_regions_not_registered", "reference_regions_not_registered"}
        ]
        if (
            fit is None
            or not np.isfinite(fit.rmse_m)
            or not depth_diagnostics.get("n_evaluation_pixels")
        ):
            depth_flags.append("sdb_validation_unavailable")
        if depth_diagnostics.get("map", {}).get("collapsed", True):
            depth_flags.append("sdb_depth_range_collapsed")
        model_flags = [
            f
            for f in iops.qa_flags
            if not f.startswith(
                ("red_reference_selected", "a_cdm_443_not_computable", "a_below_pure_water_floor")
            )
        ]
        model_flags += [
            f"a_below_pure_water_floor({r['wavelength_nm']}nm)"
            for r in iops.a_floor_violations
            if r["wavelength_nm"] <= 700
        ]
        if self.uncertainty_config.qaa_relative_sigma is None:
            model_flags.append("qaa_model_uncertainty_missing")
        margin = np.full(scene.shape, np.nan, np.float32)
        margin_sigma = np.full_like(margin, np.nan)
        rho_sigma = np.full_like(rho_b, np.nan)
        par_sigma = np.full_like(margin, np.nan)
        region_index = np.zeros(scene.shape, np.int16)
        regional_verdicts: dict[str, dict[str, dict[str, Any]]] = {}
        attenuation_payload: dict[str, Any] = {"regions": {}}
        detect_payload: dict[str, Any] = {"regions": {}}
        budgets = {}
        comparisons = {}
        for index, (key, row) in enumerate(regions.items(), 1):
            bands = row["calibrations"]
            detection = scene_detectability(
                bands,
                epsilon_rhos=row["epsilon"],
                solar_zenith_deg=theta,
                config=self.detectability_config,
            )
            region_mask = prepared.regions[key] & reporting
            region_index[region_mask] = index
            margin[region_mask] = detection.z_max_m - depth[region_mask]
            uncertainty_rho, uncertainty_par, optical_budget = propagate(
                rho_b,
                depth,
                depth_sigma,
                iops,
                scene.wavelengths_nm,
                reporting,
                theta,
                self.uncertainty_config,
                row["epsilon"],
                self.detectability_config,
            )
            rho_sigma[:, region_mask] = uncertainty_rho[:, region_mask]
            par_sigma[region_mask] = uncertainty_par[region_mask]
            blind = row["noise"].get("bands", {}).get("bottom_blind", {}).get("by_block", {})
            n_blocks = next(iter(blind.values())).get("n_blocks", 0) if blind else 0
            unc_margin, detect_budget = detection_sigma(
                detection, bands, depth_sigma, self.uncertainty_config, n_blocks
            )
            margin_sigma[region_mask] = unc_margin[region_mask]
            assessment = attenuation_verdict(
                prepared, row, uncertainty=self.uncertainty_config, qc_config=self.qc_config
            )
            flags = assessment["flags"]
            comparison = compare_with_qaa(bands, iops, theta)
            comparisons[key] = comparison
            qaa_flags = (
                common
                + model_flags
                + list(row["offset"].qa_flags)
                + optical_budget["missing_terms"]
            )
            if not comparison["per_band"]:
                qaa_flags.append("local_qaa_comparison_unavailable")
            rho_flags = qaa_flags + (
                [] if np.isfinite(rho_b[:, region_mask]).any() else ["no_invertible_pixels"]
            )
            par_flags = qaa_flags + (
                [] if np.isfinite(par[region_mask]).any() else ["no_par_coverage"]
            )
            regional_verdicts[key] = {
                "attenuation": assessment,
                "detectability": verdict(
                    flags
                    + detect_budget["missing_terms"]
                    + ([] if detection.status == "ok" else [detection.status])
                ),
                "rho_b": verdict(rho_flags),
                "seabed_par": verdict(par_flags),
            }
            attenuation_payload["regions"][key] = {
                "bands": jsonable(bands),
                "k_by_band": {str(nm): c.k_per_m for nm, c in bands.items()},
                "lyzenga_ratios": lyzenga_ratios(bands),
                "verdict": regional_verdicts[key]["attenuation"],
                "depth_source": "independent_reference",
                "epsilon_rhos": row["epsilon"],
                "detectability_config": jsonable(self.detectability_config),
                "detectability_verdict": regional_verdicts[key]["detectability"],
                "solar_zenith_deg": theta,
            }
            detect_payload["regions"][key] = detection.to_dict() | {
                "verdict": regional_verdicts[key]["detectability"],
                "numerical_status": detection.status,
                "status": detection.status
                if regional_verdicts[key]["detectability"]["passed"]
                else "diagnostic_only",
            }
            budgets[key] = {
                "optical": optical_budget,
                "detectability": detect_budget,
                "noise": row["noise"],
                "offset": row["offset"].to_dict(),
            }
        # Single-region convenience fields remain readable by v1 clients.
        if len(regions) == 1:
            attenuation_payload.update(next(iter(attenuation_payload["regions"].values())))
            detect_payload.update(next(iter(detect_payload["regions"].values())))
        pv = {
            product: combine({key: v[product] for key, v in regional_verdicts.items()})
            for product in ("attenuation", "detectability", "rho_b", "seabed_par")
        }
        pv["sdb_depth"] = verdict(depth_flags, validation=depth_diagnostics)
        core = dict(pv)
        overall = combine(core)
        result.qc_verdict = overall
        result.status = STATUS_PASSED if overall["passed"] else STATUS_REJECTED
        pv.update(
            {
                "detectability_margin": pv["detectability"],
                "detectable": pv["detectability"],
                "detectability_margin_sigma": pv["detectability"],
                "rho_b_sigma": pv["rho_b"],
                "seabed_par_sigma": pv["seabed_par"],
                "iops": pv["rho_b"],
                "optical_depth": pv["rho_b"],
                "depth_sigma": pv["sdb_depth"],
                "sdb_depth_sigma": pv["sdb_depth"],
                "retrieval_depth": pv["sdb_depth"],
                "spectral_class": verdict(["exploratory_spectral_classes"]),
                "valid_water": verdict([]),
                "sdb_partition": verdict([]),
                "analysis_region": verdict([]),
                "report": overall,
                "qc": overall,
            }
        )
        result.product_verdicts = pv
        provenance = input_provenance(aoi, scene)
        writer = ProductWriter(
            result.output_dir or "",
            grid,
            overall,
            {
                "campaign_id": self.campaign_id,
                "aoi": aoi.name,
                "sensor": scene.sensor.name,
                "acquisition_date": scene.acquisition_date,
                "solar_zenith_deg": theta,
                "solar_zenith_source": scene.solar_zenith_source,
            },
            product_verdicts=pv,
        )

        def write(key: str, values: np.ndarray, **properties: Any) -> None:
            writer.write_raster(key, np.where(reporting, values, np.nan), properties)

        write("sdb_depth", sdb, fit=jsonable(fit), depth_source="satellite_only")
        write("retrieval_depth", depth, depth_source=depth_diagnostics["retrieval_depth_source"])
        write("depth_sigma", depth_sigma, depth_source="retrieval_depth")
        raw_sigma = (
            np.sqrt(prepared.depth_sigma**2 + fit.rmse_m**2)
            if fit is not None
            else np.full(scene.shape, np.nan)
        )
        write(
            "sdb_depth_sigma",
            np.where(np.isfinite(sdb), raw_sigma, np.nan),
            depth_source="satellite_only",
        )
        write("optical_depth", optical_depth)
        write("seabed_par", par, kd_source="QAA", quantity="fraction_of_subsurface_PAR")
        write("seabed_par_sigma", par_sigma)
        write("detectability_margin", margin)
        write("detectability_margin_sigma", margin_sigma)
        write("detectable", np.where(np.isfinite(margin), (margin > 0).astype(float), np.nan))
        writer.write_raster("valid_water", prepared.valid.astype(float))
        write("sdb_partition", train.astype(float) + 2 * evaluation.astype(float))
        write("analysis_region", region_index.astype(float), regions=list(regions))
        if clusters is not None:
            write(
                "spectral_class",
                np.where(clusters.labels >= 0, clusters.labels.astype(float), np.nan),
                legend=list(clusters.centroid_labels),
            )
        for index, wl in enumerate(scene.wavelengths_nm):
            if 400 <= wl <= 700:
                write(f"rho_b_{int(round(wl))}", rho_b[index], band_nm=float(wl))
                write(f"rho_b_sigma_{int(round(wl))}", rho_sigma[index], band_nm=float(wl))
        writer.write_json("attenuation", attenuation_payload)
        writer.write_json("detectability", detect_payload)
        writer.write_json("iops", jsonable(iops))
        writer.write_json(
            "qc",
            {
                "floors": {
                    k: v["attenuation"]["evidence"].get("floors", {})
                    for k, v in regional_verdicts.items()
                },
                "ac_uncertainty": budgets,
                "product_verdicts": pv,
                "n_valid_pixels": int(prepared.valid.sum()),
                "n_deep_pixels": int(prepared.deep.sum()),
                "n_invertible_pixels": int(invertible.sum()),
                "mask": {k: int(v.sum()) for k, v in prepared.mask_components.items()},
                "sand_control": sand_control(
                    rho_b, scene.wavelengths_nm, prepared.depth, reporting, grid.pixel_size_m
                ),
                "empirical_vs_qaa": comparisons,
            },
        )
        cluster_report: dict[str, Any] = (
            {"labels": list(clusters.centroid_labels)}
            if clusters is not None
            else {"skipped": cluster_failure}
        )
        report = {
            "schema_version": "2.0",
            "campaign_id": self.campaign_id,
            "status": result.status,
            "aoi": aoi.to_dict(),
            "scene": scene.describe(),
            "provenance": provenance,
            "depth_reference": prepared.provenance,
            "depth_diagnostics": depth_diagnostics,
            "stumpf_fit": jsonable(fit),
            "uncertainty": budgets,
            "clusters": cluster_report,
            "config": {
                "retrieval": jsonable(self.config),
                "mask": jsonable(self.mask_config),
                "bathymetry": jsonable(bathy_config),
                "attenuation": jsonable(self.attenuation_config),
                "qc": jsonable(self.qc_config),
                "detectability": jsonable(self.detectability_config),
                "uncertainty": jsonable(self.uncertainty_config),
            },
            "manifest": writer.manifest(),
        }
        if emit_stac_item:
            from oceanstream.coastal.stac.coastal_emit import emit_stac

            result.stac_collection, result.stac_item = emit_stac(
                result.output_dir or "",
                products=writer.products,
                grid=grid,
                qc_verdict=overall,
                aoi=aoi,
                scene=scene,
                detectability=detect_payload,
                attenuation=attenuation_payload,
            )
        # Report is written only after required STAC assets succeeded.
        writer.write_json("report", report)
        result.report = report
        result.products = {key: p.href for key, p in writer.products.items()}


class InsufficientData(RuntimeError):
    """Raised when the inputs cannot support a verdict, let alone a product."""


#: Bands at or above this are atmospheric-correction diagnostics, not
#: water-leaving signal. They screen glint and land; they never carry a benthic
#: contribution, so they are excluded from k(λ), from the inversion and from
#: the clustering.
_SWIR_CUTOFF_NM = 1000.0

#: Below this, the median of a bathymetry grid cannot decide its sign.
_SIGN_DECISION_M = 1.0


def _depth_sign(data: np.ndarray, uri: str) -> tuple[float, str]:
    """Whether the reference is positive-down already, and how we know.

    Getting this backwards makes every depth negative, every mask empty and
    every product blank, so it is worth being explicit. The GeoTIFF tag written
    by :func:`~oceanstream.coastal.bathymetry.emodnet.subset_to_cog` is
    authoritative; failing that, the sign of the median is decisive for any real
    coastal grid, and a grid whose median sits within a metre of zero is
    reported as ambiguous rather than guessed at.
    """
    tag = _read_positive_tag(uri)
    if tag == "down":
        return 1.0, "geotiff tag positive=down"
    if tag == "up":
        return -1.0, "geotiff tag positive=up"

    finite = data[np.isfinite(data)]
    if finite.size == 0:
        raise InsufficientData(
            f"Reference bathymetry {uri} has no finite values on the scene grid. "
            "The AOI and the scene may not overlap."
        )
    median = float(np.median(finite))
    if abs(median) < _SIGN_DECISION_M:
        raise InsufficientData(
            f"Cannot tell whether {uri} is positive-up or positive-down: its "
            f"median value is {median:.3f} m, too close to zero to be decisive. "
            "Re-export it with a 'positive' GeoTIFF tag."
        )
    return (
        (1.0, "inferred from positive median")
        if median > 0
        else (
            -1.0,
            "inferred from negative median",
        )
    )


def _read_positive_tag(uri: str) -> str | None:
    try:
        import rasterio
    except ImportError:  # pragma: no cover - depends on the optional install
        return None
    try:
        with rasterio.open(uri) as src:
            value = src.tags().get("positive")
    except Exception:  # noqa: BLE001 - a missing or unreadable tag is not an error
        return None
    return str(value).lower() if value else None


def _epsilon_value(epsilon: dict[str, object]) -> float | None:
    """Pull the scalar noise threshold out of the AC-uncertainty diagnostic."""
    for key in ("effective_threshold_rhos", "epsilon_rhos", "threshold_rhos"):
        value = epsilon.get(key)
        if isinstance(value, (int, float)) and np.isfinite(float(value)):
            return float(value)
    return None


def _finite_median(data: np.ndarray) -> float | None:
    finite = data[np.isfinite(data)]
    return float(np.median(finite)) if finite.size else None
