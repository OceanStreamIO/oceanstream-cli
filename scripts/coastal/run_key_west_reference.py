"""Run the preregistered Key West Sentinel-2 bathymetry component experiment.

Usage::

    PYTHONPATH=. python scripts/coastal/run_key_west_reference.py config.json

Required config keys are ``acolite_dir``, ``reference_elevation_raster`` and
``output_dir``. Paths are relative to the config file. ``vertical_datum`` records
``source``, ``target``, ``elevation_correction_m`` (target elevation minus source
elevation), ``correction_source`` and ``uncertainty_m``. An unknown correction is
left unapplied and explicitly reported; it is never inferred from test errors.

The experiment scores raw, unblended predictions. Its raster calibration and
buffered spatial evaluation differ from the paper's chart calibration. Therefore
this is a component diagnostic, not an exact paper reproduction or release gate.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import platform
from dataclasses import asdict, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
from scipy import ndimage

from oceanstream.coastal.bathymetry.stumpf import (
    choose_ratio_scale,
    fit_stumpf,
    stumpf_ratio,
)
from oceanstream.coastal.config import BathymetryConfig
from oceanstream.coastal.io.rasters import RasterGrid, write_cog
from oceanstream.coastal.products import write_json_document
from oceanstream.coastal.provenance import file_digest
from oceanstream.coastal.scene import Scene

SCHEMA_VERSION = "1.0"
DEPTH_BINS = (0.0, 1.0, 2.0, 3.0, 4.0, 5.0)


def median_supported(array: np.ndarray) -> np.ndarray:
    """Median 3x3, requiring all nine finite samples; never fill nodata holes."""
    finite = np.isfinite(array)
    support = ndimage.binary_erosion(finite, structure=np.ones((3, 3)), border_value=0)
    result = ndimage.median_filter(np.where(finite, array, 0.0), size=3, mode="constant")
    return np.where(support, result, np.nan)


def literal_ratio(blue: np.ndarray, red: np.ndarray, n: float = 1000.0) -> np.ndarray:
    """Independent paper equation, with only numerical-domain exclusions."""
    blue_scaled = n * np.asarray(blue, dtype=np.float64)
    red_scaled = n * np.asarray(red, dtype=np.float64)
    valid = (blue_scaled > 0) & (red_scaled > 0)
    with np.errstate(divide="ignore", invalid="ignore"):
        result = np.log(blue_scaled) / np.log(red_scaled)
    return np.where(valid & np.isfinite(result), result, np.nan)


def align_reference_coverage(
    path: Path,
    grid: RasterGrid,
    resampling: str = "nearest",
    minimum_coverage: float = 1.0,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Transfer elevations and measure valid input footprint coverage.

    ``average`` aggregates a finer DEM onto sensor pixels, requiring the entire
    footprint to have data by default. ``nearest`` transfers an already aggregated
    DEM. Neither fills a missing footprint. Identifiers track the native source
    cell at the sensor pixel centre; buffered splits keep averaged footprints
    apart, while repeated centre cells are additionally excluded across splits.
    """
    import rasterio
    from rasterio.transform import Affine
    from rasterio.warp import Resampling, reproject

    if resampling not in {"nearest", "average"}:
        raise ValueError("Reference resampling must be nearest or average.")
    if not 0 < minimum_coverage <= 1:
        raise ValueError("minimum_reference_coverage must be in (0, 1].")
    with rasterio.open(path) as source:
        if source.crs is None:
            raise ValueError("Reference elevation raster has no CRS.")
        source_grid = RasterGrid.from_dataset(source)
        if resampling == "average" and source_grid.pixel_size_m > grid.pixel_size_m:
            raise ValueError(
                "Average reference aggregation requires source pixels <= sensor pixels."
            )
        elevation = source.read(1, masked=True).astype(np.float32).filled(np.nan)
        finite = np.isfinite(elevation)
        source_ids = np.arange(1, elevation.size + 1, dtype=np.float64).reshape(elevation.shape)
        source_ids[~finite] = 0
        aligned = np.full(grid.shape, np.nan, dtype=np.float32)
        aligned_ids = np.zeros(grid.shape, dtype=np.float64)
        coverage = np.zeros(grid.shape, dtype=np.float32)
        options = {
            "src_transform": source.transform,
            "src_crs": source.crs,
            "dst_transform": Affine(*grid.transform),
            "dst_crs": grid.crs,
        }
        method = getattr(Resampling, resampling)
        reproject(
            elevation,
            aligned,
            src_nodata=np.nan,
            dst_nodata=np.nan,
            resampling=method,
            **options,
        )
        reproject(
            source_ids,
            aligned_ids,
            src_nodata=0,
            dst_nodata=0,
            resampling=Resampling.nearest,
            **options,
        )
        # Padding zeros includes the absent area immediately beyond the source
        # bounds in footprint coverage; GDAL otherwise averages only the overlap.
        coverage_options = {
            **options,
            "src_transform": Affine(
                source.transform.a,
                source.transform.b,
                source.transform.c - source.transform.a - source.transform.b,
                source.transform.d,
                source.transform.e,
                source.transform.f - source.transform.d - source.transform.e,
            ),
        }
        reproject(
            np.pad(finite.astype(np.float32), 1),
            coverage,
            src_nodata=None,
            dst_nodata=0,
            resampling=method,
            **coverage_options,
        )
    covered = (coverage >= minimum_coverage - 1e-6) & (aligned_ids > 0)
    aligned[~covered] = np.nan
    aligned_ids[~covered] = 0
    return aligned, aligned_ids.astype(np.int64), coverage


def align_reference(path: Path, grid: RasterGrid) -> tuple[np.ndarray, np.ndarray]:
    """Compatibility helper for a nearest transfer of an already aggregated DEM."""
    elevation, identifiers, _ = align_reference_coverage(path, grid)
    return elevation, identifiers


def reporting_mask(grid: RasterGrid, config: dict) -> np.ndarray:
    """Rasterize the preregistered reporting rectangle on pixel centres."""
    if "reporting_bounds" not in config:
        return np.ones(grid.shape, dtype=bool)
    from rasterio.features import rasterize
    from rasterio.transform import Affine
    from rasterio.warp import transform_geom

    west, south, east, north = map(float, config["reporting_bounds"])
    if not west < east or not south < north:
        raise ValueError("reporting_bounds must have positive extent.")
    polygon = {
        "type": "Polygon",
        "coordinates": [
            [[west, south], [east, south], [east, north], [west, north], [west, south]]
        ],
    }
    transformed = transform_geom(config["reporting_crs"], grid.crs, polygon)
    return rasterize(
        [(transformed, 1)],
        out_shape=grid.shape,
        transform=Affine(*grid.transform),
        fill=0,
        dtype="uint8",
        all_touched=False,
    ).astype(bool)


def spatial_split(
    shape: tuple[int, int],
    source_ids: np.ndarray,
    block_px: int = 50,
    buffer_px: int = 1,
) -> tuple[np.ndarray, np.ndarray]:
    """Fixed checkerboard blocks, eroded boundaries and no shared source cells."""
    if buffer_px < 1 or block_px <= 2 * buffer_px + 1:
        raise ValueError("Blocks must exceed the median-filter buffer; buffer_px must be >= 1.")
    if source_ids.shape != shape:
        raise ValueError("Reference cell IDs must match the split grid.")
    rows, cols = np.indices(shape)
    first = (rows // block_px + cols // block_px) % 2 == 0
    structure = np.ones((2 * buffer_px + 1, 2 * buffer_px + 1), dtype=bool)
    train = ndimage.binary_erosion(first, structure=structure, border_value=0)
    test = ndimage.binary_erosion(~first, structure=structure, border_value=0)
    train &= source_ids > 0
    test &= source_ids > 0
    # A coarse source cell can map into multiple satellite pixels. Remove all
    # evaluation copies when any copy belongs to calibration, even after erosion.
    shared = np.intersect1d(source_ids[train], source_ids[test])
    if shared.size:
        test &= ~np.isin(source_ids, shared)
    return train, test


def error_metrics(prediction: np.ndarray, reference: np.ndarray, mask: np.ndarray) -> dict:
    requested = int(mask.sum())
    scored = mask & np.isfinite(prediction) & np.isfinite(reference)
    residual = prediction[scored].astype(np.float64) - reference[scored]
    if residual.size:
        predicted_p5, predicted_p95 = np.percentile(prediction[scored], [5, 95])
        reference_p5, reference_p95 = np.percentile(reference[scored], [5, 95])
        prediction_span = float(predicted_p95 - predicted_p5)
        reference_span = float(reference_p95 - reference_p5)
        span_diagnostic = {
            "prediction_p5_m": float(predicted_p5),
            "prediction_p95_m": float(predicted_p95),
            "prediction_p5_p95_span_m": prediction_span,
            "reference_p5_m": float(reference_p5),
            "reference_p95_m": float(reference_p95),
            "reference_p5_p95_span_m": reference_span,
            "prediction_to_reference_span_ratio": (
                prediction_span / reference_span if reference_span > 0 else None
            ),
        }
    else:
        span_diagnostic = None
    return {
        "n_requested": requested,
        "n_scored": int(scored.sum()),
        "valid_fraction": float(scored.sum() / requested) if requested else None,
        "mae_m": float(np.mean(np.abs(residual))) if residual.size else None,
        "median_ae_m": float(np.median(np.abs(residual))) if residual.size else None,
        "rmse_m": float(np.sqrt(np.mean(residual**2))) if residual.size else None,
        "bias_m": float(np.mean(residual)) if residual.size else None,
        # Descriptive only: no new threshold, rejection or model selection is
        # introduced after preregistration. Both spans use identical scored pixels.
        "span_diagnostic": span_diagnostic,
    }


def score_depth(prediction: np.ndarray, depth: np.ndarray, evaluation: np.ndarray) -> dict:
    return {
        "all": error_metrics(prediction, depth, evaluation),
        "by_reference_depth": {
            f"{lo:g}-{hi:g}m": error_metrics(
                prediction, depth, evaluation & (depth >= lo) & (depth < hi)
            )
            for lo, hi in zip(DEPTH_BINS[:-1], DEPTH_BINS[1:])
        },
    }


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (float, np.floating)) and not np.isfinite(value):
        return None
    if isinstance(value, np.generic):
        return value.item()
    return value


def _resolve(value: str, base: Path) -> Path:
    path = Path(value).expanduser()
    return (base / path).resolve() if not path.is_absolute() else path.resolve()


def verify_input_lock(path: Path) -> dict:
    """Verify all registered files before creating any experiment output."""
    lock = json.loads(path.read_text())
    files = lock.get("files")
    if lock.get("schema_version") != "1.0" or not isinstance(files, dict) or not files:
        raise ValueError("Input lock requires schema_version 1.0 and a nonempty files mapping.")
    for filename, expected in files.items():
        registered = Path(filename)
        if not registered.is_absolute():
            raise ValueError(f"Input lock paths must be absolute: {filename}")
        if registered.resolve() == path.resolve():
            raise ValueError("Input lock cannot include its own checksum.")
        if not registered.is_file():
            raise FileNotFoundError(f"Locked input is missing: {filename}")
        actual = file_digest(registered)
        if actual != expected:
            raise ValueError(f"Locked input checksum changed: {filename}")
    return {
        "path": str(path),
        "sha256": file_digest(path),
        "schema_version": lock["schema_version"],
        "frozen_at": lock.get("frozen_at"),
        "n_verified_files": len(files),
    }


def _datum(config: dict) -> tuple[float, dict, list[str]]:
    datum = dict(config.get("vertical_datum", {}))
    source, target = datum.get("source"), datum.get("target", "MLLW")
    supplied_offset = datum.get("elevation_correction_m")
    reasons = []
    if not source:
        reasons.append("reference_vertical_datum_unknown")
    if supplied_offset is None:
        offset = 0.0
        if not source or source != target:
            reasons.append("reference_to_target_datum_correction_missing")
    else:
        offset = float(supplied_offset)
        if not np.isfinite(offset):
            raise ValueError("elevation_correction_m must be finite or null.")
        if not datum.get("correction_source"):
            reasons.append("datum_correction_source_missing")
    if datum.get("uncertainty_m") is None:
        reasons.append("reference_datum_uncertainty_missing")
    datum.update(
        {
            "source": source,
            "target": target,
            "applied_elevation_correction_m": offset,
            "depth_formula": "depth = -(source_elevation + applied_elevation_correction_m)",
            "evaluated_depth_datum": target
            if source == target or supplied_offset is not None
            else source,
            "instantaneous_water_depth": False,
        }
    )
    return offset, datum, reasons


def evaluate_modes(scene: Scene, depth: np.ndarray, source_ids: np.ndarray, config: dict) -> dict:
    """Calculate modes and raw predictions without touching the filesystem."""
    split = config.get("spatial_split", {})
    block_px, buffer_px = int(split.get("block_px", 50)), int(split.get("buffer_px", 1))
    calibration, evaluation = spatial_split(scene.shape, source_ids, block_px, buffer_px)
    reporting = reporting_mask(scene.grid, config)
    domain = reporting & np.isfinite(depth) & (depth > 0.0) & (depth < 5.0)
    calibration &= domain
    evaluation &= domain
    if not calibration.any() or not evaluation.any():
        raise ValueError("No 0-5 m reference observations in one or both frozen spatial splits.")

    blue, green, red = (scene.band(wavelength) for wavelength in (490.0, 560.0, 665.0))
    blue_median, red_median = median_supported(blue), median_supported(red)
    literal = literal_ratio(blue_median, red_median)
    # Numerical-domain guard only. Negative logs and ratios >2 are not removed:
    # the production blue/green limits are separately measured, not silently
    # imposed on the published blue/red equation.
    permissive = BathymetryConfig(
        min_scaled_reflectance=0.0,
        ratio_valid_range=(-1e6, 1e6),
        fit_reference_range_m=(0.0, 5.0),
        min_depth_span_m=float(config.get("min_calibration_depth_span_m", 2.0)),
        min_fit_pixels=int(config.get("min_fit_pixels", 200)),
        checkerboard_block_px=block_px,
    )
    standard = replace(
        BathymetryConfig(),
        fit_reference_range_m=permissive.fit_reference_range_m,
        min_depth_span_m=permissive.min_depth_span_m,
        min_fit_pixels=permissive.min_fit_pixels,
        checkerboard_block_px=block_px,
    )
    library_ratio = stumpf_ratio(blue_median, red_median, 1000.0, permissive)
    guarded_ratio = stumpf_ratio(blue_median, red_median, 1000.0, standard)
    paper = config.get("published_coefficients", {"m0": 5.9, "m1": 5.8, "n": 1000.0})
    if float(paper.get("n", 1000.0)) != 1000.0:
        raise ValueError("The preregistered published ratio requires n=1000.")
    paper_m0, paper_m1 = float(paper["m0"]), float(paper["m1"])
    if not np.isfinite([paper_m0, paper_m1]).all():
        raise ValueError("Published coefficients must be finite.")
    predictions = {
        "published_blue_red": paper_m1 * literal - paper_m0,
        "library_published_blue_red": paper_m1 * library_ratio - paper_m0,
        "library_guarded_published_blue_red": paper_m1 * guarded_ratio - paper_m0,
    }
    modes: dict[str, Any] = {}
    for name in predictions:
        modes[name] = {
            "fit_status": "published_coefficients",
            "reflectance": "Rrs sr-1",
            "bands_nm": [490, 665],
            "filter": "3x3 median; complete finite neighborhood required",
            "coefficients": {"m0": paper_m0, "m1": paper_m1, "n": 1000.0},
            "ratio_safety": (
                asdict(standard)
                if name == "library_guarded_published_blue_red"
                else asdict(permissive)
                if name.startswith("library_")
                else "numerical_domain_only"
            ),
        }
    default_blue, default_green = scene.rhos_band(490.0), scene.rhos_band(560.0)
    # Fit adaptive n using calibration reflectances only. Evaluation observations
    # cannot influence parameters, including this unsupervised scaling choice.
    adaptive_n = choose_ratio_scale(default_blue, default_green, calibration, config=standard)
    variants = [
        ("library_refit_blue_red", blue_median, red_median, 1000.0, permissive, "Rrs sr-1", True),
        (
            "library_guarded_refit_blue_red",
            blue_median,
            red_median,
            1000.0,
            standard,
            "Rrs sr-1",
            True,
        ),
        (
            "library_default_blue_green",
            default_blue,
            default_green,
            adaptive_n,
            standard,
            "rhos",
            False,
        ),
    ]
    for name, first, second, n, settings, convention, filtered in variants:
        ratio = stumpf_ratio(first, second, n, settings)
        eligible = np.isfinite(ratio) & (calibration | evaluation)
        mode = {
            "reflectance": convention,
            "bands_nm": [490, 665 if filtered else 560],
            "filter": "3x3 median; complete finite neighborhood required" if filtered else "none",
            "ratio_safety": asdict(settings),
            "n_calibration_ratio_valid": int((calibration & np.isfinite(ratio)).sum()),
            "n_evaluation_ratio_valid": int((evaluation & np.isfinite(ratio)).sum()),
        }
        try:
            fit = fit_stumpf(
                first,
                second,
                depth,
                eligible,
                n=n,
                ref_source="spatially withheld NOAA lidar raster; no blending",
                config=settings,
                calibration_mask=calibration,
                evaluation_mask=evaluation,
            )
            predictions[name] = fit.m1 * ratio - fit.m0
            mode.update({"fit_status": "fitted", "coefficients": asdict(fit)})
        except ValueError as error:
            predictions[name] = np.full(scene.shape, np.nan, dtype=np.float32)
            mode.update({"fit_status": "rejected", "reason": str(error), "n": n})
        modes[name] = mode

    baseline = float(np.mean(depth[calibration], dtype=np.float64))
    predictions["training_mean_baseline"] = np.full(scene.shape, baseline, dtype=np.float32)
    modes["training_mean_baseline"] = {"fit_status": "fitted", "constant_depth_m": baseline}
    predictions = {name: np.where(domain, array, np.nan) for name, array in predictions.items()}
    common = evaluation.copy()
    for prediction in predictions.values():
        common &= np.isfinite(prediction)
    for name, prediction in predictions.items():
        modes[name]["scores"] = score_depth(prediction, depth, evaluation)
        modes[name]["scores_on_all_mode_common_support"] = score_depth(prediction, depth, common)
        modes[name]["baseline_on_mode_support"] = score_depth(
            predictions["training_mean_baseline"], depth, evaluation & np.isfinite(prediction)
        )
        modes[name]["predictions_outside_0_5m"] = int(
            (evaluation & np.isfinite(prediction) & ((prediction < 0) | (prediction > 5))).sum()
        )
    formula_common = evaluation & np.isfinite(literal) & np.isfinite(library_ratio)
    diff = np.abs(literal[formula_common] - library_ratio[formula_common])
    formula = {
        "n_identical_support": int(formula_common.sum()),
        "max_absolute_ratio_difference": float(diff.max()) if diff.size else None,
        "literal_valid_evaluation_pixels": int((evaluation & np.isfinite(literal)).sum()),
        "library_permissive_valid_evaluation_pixels": int(
            (evaluation & np.isfinite(library_ratio)).sum()
        ),
        "library_default_guard_valid_evaluation_pixels": int(
            (evaluation & np.isfinite(guarded_ratio)).sum()
        ),
        "literal_pixels_rejected_by_default_guard": int(
            (evaluation & np.isfinite(literal) & ~np.isfinite(guarded_ratio)).sum()
        ),
    }
    return {
        "modes": modes,
        "predictions": predictions,
        "calibration": calibration,
        "evaluation": evaluation,
        "reference_domain": domain,
        "reporting_mask": reporting,
        "formula_comparison": formula,
        "split": {
            "method": (
                "checkerboard; raster upper-left origin; shared native reference cells removed"
            ),
            "block_px": block_px,
            "block_m": block_px * scene.grid.pixel_size_m,
            "buffer_px_each_side": buffer_px,
            "n_calibration": int(calibration.sum()),
            "n_evaluation": int(evaluation.sum()),
            "n_calibration_source_cells": int(np.unique(source_ids[calibration]).size),
            "n_evaluation_source_cells": int(np.unique(source_ids[evaluation]).size),
            "masks_depend_on": "grid geometry, source-cell support, reference 0<depth<5m only",
            "evaluation_used_for_model_selection": False,
            "samples_are_spatially_correlated": True,
        },
    }


def run(config_path: str | Path) -> dict:
    config_path = Path(config_path).resolve()
    config = json.loads(config_path.read_text())
    base = config_path.parent
    lock_path = _resolve(config["input_lock"], base) if config.get("input_lock") else None
    verified_lock = verify_input_lock(lock_path) if lock_path else None
    directory = _resolve(config["output_dir"], base)
    if directory.exists() and any(directory.iterdir()):
        raise FileExistsError(f"Output directory is not empty: {directory}")
    directory.mkdir(parents=True, exist_ok=True)
    # A failure can leave diagnostic partial outputs, but never a completion marker.
    write_json_document(directory / "config.json", config)
    acolite = _resolve(config["acolite_dir"], base)
    reference = _resolve(config["reference_elevation_raster"], base)
    scene = Scene.from_acolite_dir(
        acolite,
        solar_zenith_fallback_deg=config.get("solar_zenith_fallback_deg"),
    )
    reference_resampling = config.get("reference_resampling", "nearest")
    minimum_reference_coverage = float(config.get("minimum_reference_coverage", 1.0))
    elevation, source_ids, reference_coverage = align_reference_coverage(
        reference,
        scene.grid,
        reference_resampling,
        minimum_reference_coverage,
    )
    offset, datum, reasons = _datum(config)
    depth = -(elevation + offset)
    result = evaluate_modes(scene, depth, source_ids, config)
    reasons.extend(
        [
            "spatially_withheld_lidar_calibration_differs_from_published_nine_chart_points",
            "component_experiment_does_not_validate_other_optical_products",
            "no_confidence_intervals_or_independent_reference_uncertainty_budget",
        ]
    )
    recipe = config.get("paper_recipe", {})
    for key in (
        "atmospheric_correction_matched",
        "roi_matched",
        "calibration_points_matched",
        "lidar_survey_matched",
        "sentinel_processing_baseline_matched",
    ):
        if recipe.get(key) is not True:
            reasons.append(f"{key}_not_verified")
    tags = {
        "OCEANSTREAM_STATUS": "diagnostic_only",
        "OCEANSTREAM_EXPERIMENT": "key_west_2017_component_reference",
        "OCEANSTREAM_DEPTH_DATUM": str(datum.get("evaluated_depth_datum", "unknown")),
        "OCEANSTREAM_CAVEAT": "; ".join(reasons),
    }
    files = {}
    arrays = {
        "reference_depth": depth,
        "reference_coverage": np.isfinite(elevation).astype(np.float32),
        "reference_footprint_valid_fraction": reference_coverage,
        "reference_domain": result["reference_domain"].astype(np.float32),
        "reporting_mask": result["reporting_mask"].astype(np.float32),
        "calibration_mask": result["calibration"].astype(np.float32),
        "evaluation_mask": result["evaluation"].astype(np.float32),
        **result["predictions"],
    }
    for name, array in arrays.items():
        path = directory / f"{name}.tif"
        write_cog(path, array, scene.grid, tags={**tags, "OCEANSTREAM_PRODUCT": name})
        files[name] = {"path": path.name, "sha256": file_digest(path)}
    rows, cols = np.where(result["evaluation"])
    samples = {
        "row": rows,
        "col": cols,
        "source_cell_id": source_ids[rows, cols],
        "reference_depth_m": depth[rows, cols],
        **{name: array[rows, cols] for name, array in result["predictions"].items()},
    }
    samples_path = directory / "evaluation_samples.npz"
    np.savez_compressed(samples_path, **samples)
    files["evaluation_samples"] = {"path": samples_path.name, "sha256": file_digest(samples_path)}
    inputs = [config_path, reference]
    if lock_path:
        inputs.append(lock_path)
    inputs.extend(
        path
        for path in sorted(acolite.iterdir())
        if path.is_file() and (path.suffix.lower() in {".tif", ".json", ".txt"})
    )
    for extra in config.get("additional_provenance_files", []):
        inputs.append(_resolve(extra, base))
    versions = {"python": platform.python_version()}
    for package in ("oceanstream", "numpy", "scipy", "rasterio"):
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = "not-installed"
    source_root = Path(__file__).resolve().parents[2] / "oceanstream" / "coastal"
    code_hashes = {
        str(path.relative_to(source_root)): file_digest(path)
        for path in sorted(source_root.rglob("*.py"))
    }
    report = _json_safe(
        {
            "schema_version": SCHEMA_VERSION,
            "experiment": "key_west_2017_component_reference",
            "completed_at": datetime.now(UTC).isoformat(),
            "execution_success": True,
            "status": "diagnostic_only",
            "exact_paper_replication": False,
            "release_evidence_complete": False,
            "limitations": reasons,
            "paper_recipe": recipe,
            "vertical_datum": datum,
            "scene": scene.describe(),
            "acolite_version": scene.metadata.get("acolite_version"),
            "reference_resampling": reference_resampling,
            "minimum_reference_coverage": minimum_reference_coverage,
            "reference_coverage": {
                "grid_pixels": depth.size,
                "reporting_pixels": int(result["reporting_mask"].sum()),
                "covered_pixels": int(np.isfinite(elevation).sum()),
                "covered_reporting_pixels": int(
                    (np.isfinite(elevation) & result["reporting_mask"]).sum()
                ),
                "domain_0_5m_pixels": int(result["reference_domain"].sum()),
            },
            "split": result["split"],
            "formula_comparison": result["formula_comparison"],
            "modes": result["modes"],
            "files": files,
            "config": config,
            "verified_input_lock": verified_lock,
            "inputs": {str(path): {"sha256": file_digest(path)} for path in inputs},
            "software": versions,
            "runner_sha256": file_digest(Path(__file__)),
            "coastal_source_sha256": hashlib.sha256(
                json.dumps(code_hashes, sort_keys=True).encode()
            ).hexdigest(),
        }
    )
    write_json_document(directory / "results.json", report)
    # The manifest is the only completion marker and is written after all assets.
    write_json_document(
        directory / "manifest.json",
        {
            "schema_version": SCHEMA_VERSION,
            "complete": True,
            "status": "diagnostic_only",
            "results": {"path": "results.json", "sha256": file_digest(directory / "results.json")},
            "files": files,
        },
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", type=Path)
    args = parser.parse_args()
    report = run(args.config)
    print(
        json.dumps(
            {
                "status": report["status"],
                "modes": {key: value["scores"]["all"] for key, value in report["modes"].items()},
            },
            indent=2,
            allow_nan=False,
        )
    )


if __name__ == "__main__":
    main()
