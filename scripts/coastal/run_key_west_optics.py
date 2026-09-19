"""Fixed-lidar Key West attenuation and bottom-reflectance consistency experiment.

Uses shared library equations and masks. No satellite depth fit or blending is
performed. Offshore depths qualify reference samples only; reporting depths are
surveyed lidar plus an independent gauge observation. All outputs are diagnostic.
"""

from __future__ import annotations

import argparse
import json
import logging
from dataclasses import asdict, replace
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import rasterio
from rasterio.transform import Affine
from rasterio.warp import Resampling, reproject
from run_key_west_reference import (
    _json_safe,
    align_reference,
    reporting_mask,
    spatial_split,
    verify_input_lock,
)

from oceanstream.coastal.bathymetry.terrain import terrain_classes
from oceanstream.coastal.config import AttenuationConfig, MaskConfig, RetrievalConfig
from oceanstream.coastal.inversion import lee
from oceanstream.coastal.io.rasters import RasterGrid, write_cog
from oceanstream.coastal.masks import composite_mask, deep_water_pixels
from oceanstream.coastal.optics import water
from oceanstream.coastal.optics.attenuation import calibrate_bands
from oceanstream.coastal.optics.qaa import fit_scene_iops, kd_map
from oceanstream.coastal.products import write_json_document
from oceanstream.coastal.provenance import file_digest
from oceanstream.coastal.qc.ac_uncertainty import effective_threshold_rhos
from oceanstream.coastal.qc.controls import compare_with_qaa
from oceanstream.coastal.qc.floors import scene_floor_verdict
from oceanstream.coastal.qc.offset import deepwater_additive_offset
from oceanstream.coastal.scene import Scene
from oceanstream.coastal.stages import bbox_mask


def fixed_depth(elevation_mllw: np.ndarray, water_level_m: float) -> np.ndarray:
    """Positive-down water-column depth; source holes and exposed bed stay missing."""
    if not np.isfinite(water_level_m):
        raise ValueError("Acquisition-time water level must be finite.")
    result = -np.asarray(elevation_mllw, dtype=np.float32) + water_level_m
    return np.where(np.isfinite(elevation_mllw) & (result > 0), result, np.nan).astype("float32")


def coarse_depth(path: Path, grid: RasterGrid) -> np.ndarray:
    with rasterio.open(path) as src:
        result = np.full(grid.shape, np.nan, dtype="float32")
        values = src.read(1, masked=True).astype("float32").filled(np.nan)
        reproject(
            -values,
            result,
            src_transform=src.transform,
            src_crs=src.crs,
            src_nodata=np.nan,
            dst_transform=Affine(*grid.transform),
            dst_crs=grid.crs,
            dst_nodata=np.nan,
            resampling=Resampling.nearest,
        )
    return np.where(result > 0, result, np.nan)


def stats(values: np.ndarray, requested: int | None = None) -> dict:
    finite = values[np.isfinite(values)]
    result = {"n": int(finite.size)}
    if requested is not None:
        result.update(
            n_requested=requested, fraction=float(finite.size / requested) if requested else None
        )
    if finite.size:
        result.update(zip(("p05", "median", "p95"), map(float, np.percentile(finite, [5, 50, 95]))))
    return result


def comparison(first: np.ndarray, second: np.ndarray, valid: np.ndarray) -> dict:
    common = valid & np.isfinite(first) & np.isfinite(second)
    difference = second[common] - first[common]
    return {
        "common_pixels": int(common.sum()),
        "signed_difference": stats(difference),
        "absolute_difference": stats(np.abs(difference)),
        "interpretation": "Same-pixel sensitivity; not error against independent truth.",
    }


def band_record(calibration) -> dict:
    return {
        **asdict(calibration),
        "trustworthy": calibration.trustworthy,
        "k_pure_water_floor": calibration.k_pure_water_floor,
        "k_below_pure_water_floor": calibration.k_below_pure_water_floor,
        "fit_checks_passed": bool(
            calibration.assessable
            and calibration.trustworthy
            and np.isfinite(calibration.k_per_m)
            and not calibration.k_below_pure_water_floor
        ),
    }


def qaa_flags(iops) -> list[str]:
    return [
        flag
        for flag in iops.qa_flags
        if not flag.startswith(("red_reference_selected", "a_cdm_443_not_computable"))
    ]


def reconstruct_rrs(rho: np.ndarray, depth: np.ndarray, iops, theta: float) -> np.ndarray:
    """Forward model broadcasts band-last; expose the library's band-first layout."""
    result = lee.forward_model(np.moveaxis(rho, 0, -1), depth[..., None], iops, theta)
    return np.moveaxis(result, -1, 0)


def heldout_residual(calibration, heldout) -> dict:
    points = [b for b in heldout.bin_support if b["log_quantile"] is not None]
    if not points or not np.isfinite(calibration.k_per_m):
        return {"n_bins": len(points), "rmse_log_residual": None}
    residual = np.array(
        [
            b["log_quantile"] - (calibration.intercept - calibration.k_per_m * b["depth_m"])
            for b in points
        ]
    )
    return {
        "n_bins": len(points),
        "rmse_log_residual": float(np.sqrt(np.mean(residual**2))),
        "interpretation": (
            "Held-out reflectance-depth quantile consistency; not attenuation accuracy."
        ),
    }


def bottom_summary(rho: np.ndarray, depth: np.ndarray, mask: np.ndarray, terrain: dict) -> dict:
    finite = mask & np.isfinite(rho)
    in_bounds = finite & (rho >= 0) & (rho <= 1)
    result = {
        "library_retained": stats(rho[mask], int(mask.sum())),
        "physical_0_1_pixels": int(in_bounds.sum()),
        "physical_0_1_fraction": float(in_bounds.sum() / mask.sum()) if mask.any() else None,
        "negative_retained_pixels": int((finite & (rho < 0)).sum()),
        "above_one_retained_pixels": int((finite & (rho > 1)).sum()),
        "by_depth": {},
        "terrain": {},
    }
    for lo, hi in ((0.5, 2), (2, 5), (5, 7.5)):
        chosen = mask & (depth >= lo) & (depth < hi)
        result["by_depth"][f"{lo:g}-{hi:g}m"] = stats(rho[chosen], int(chosen.sum()))
    for label in ("flat", "rugose"):
        chosen = finite & terrain[label]
        x, y = depth[chosen], rho[chosen]
        slope = float(np.polyfit(x, y, 1)[0]) if len(x) >= 20 and np.ptp(x) >= 1 else None
        result["terrain"][label] = {
            **stats(y),
            "depth_trend_per_m": slope,
            "depth_span_m": float(np.ptp(x)) if len(x) else None,
            "caveat": "Geometry class only; comparable substrate composition is unverified.",
        }
    return result


def run(config_path: Path) -> dict:
    config_path = config_path.resolve()
    cfg = json.loads(config_path.read_text())
    base = config_path.parent

    def resolve(value):
        return (base / value).resolve()

    lock = verify_input_lock(resolve(cfg["input_lock"]))
    output = resolve(cfg["output_dir"])
    if output.exists() and any(output.iterdir()):
        raise FileExistsError("Use an empty optical experiment output directory.")
    output.mkdir(parents=True, exist_ok=True)
    write_json_document(output / "config.json", cfg)
    spec = json.loads(resolve(cfg["registration"]).read_text())
    tide = json.loads(resolve(cfg["tide"]).read_text())
    scene = Scene.from_acolite_dir(resolve(cfg["acolite_dir"]))
    if (
        scene.acquisition_datetime is None
        or abs((scene.acquisition_datetime - datetime.fromisoformat(tide["when"])).total_seconds())
        > 1
    ):
        raise ValueError("Gauge interpolation must refer to the actual tile sensing time.")
    roles = scene.sensor.resolve_roles(scene.wavelengths_nm)
    masks = replace(
        MaskConfig(),
        deepwater_min_depth_m=spec["offshore_min_depth_m"],
        deepwater_min_optical_depth=spec["offshore_min_optical_depth"],
        deepwater_max_samples=spec["reference_sample_max_per_patch"],
        rng_seed=spec["seed"],
    )
    valid, components = composite_mask(
        blue=scene.rhos[roles["blue"]],
        red=scene.rhos[roles["red"]],
        nir=scene.rhos[roles["nir"]],
        swir=scene.rhos[roles["swir1"]],
        config=masks,
    )
    vis = [
        int(np.argmin(np.abs(scene.wavelengths_nm - wl))) for wl in spec["reporting_wavelengths_nm"]
    ]
    wl_visible = scene.wavelengths_nm[vis]
    valid &= np.all(np.isfinite(scene.rhos), axis=0)
    valid &= np.all(np.isfinite(scene.rrs_above[vis]), axis=0)
    kd_proxy = kd_map(scene.rrs_above[vis], wl_visible, valid, scene.solar_zenith_deg)
    offshore_depth = coarse_depth(resolve(cfg["offshore_elevation"]), scene.grid)
    patches, candidates = {}, {}
    for key, bbox in spec["offshore_reference_bboxes"].items():
        patch = valid & bbox_mask(tuple(bbox), scene.grid)
        candidates[key] = patch & (offshore_depth > masks.deepwater_min_depth_m)
        candidates[key] &= offshore_depth * kd_proxy > masks.deepwater_min_optical_depth
        patches[key] = deep_water_pixels(patch, offshore_depth, kd_proxy, masks)
    patches["pooled"] = np.logical_or.reduce(list(patches.values()))
    candidates["pooled"] = np.logical_or.reduce(list(candidates.values()))
    # Keep offshore work on the wide grid, then crop retrievals to the reporting strip.
    report_wide = reporting_mask(scene.grid, spec)
    rows, cols = np.where(report_wide)
    if not rows.size:
        raise ValueError("No reporting pixels on the expanded scene grid.")
    row_slice, col_slice = slice(rows.min(), rows.max() + 1), slice(cols.min(), cols.max() + 1)

    def crop(array):
        return array[..., row_slice, col_slice]

    transform = Affine(*scene.grid.transform) * Affine.translation(int(cols.min()), int(rows.min()))
    grid = RasterGrid(
        int(cols.max() - cols.min() + 1),
        int(rows.max() - rows.min() + 1),
        tuple(transform)[:6],
        scene.grid.crs,
    )
    elevation, source_ids = align_reference(resolve(cfg["lidar_elevation"]), grid)
    depth = fixed_depth(elevation, tide["offset_m"])
    lo, hi = spec["reporting_depth_range_m"]
    reporting = crop(report_wide) & crop(valid) & np.isfinite(depth) & (depth >= lo) & (depth <= hi)
    train, test = spatial_split(grid.shape, source_ids, block_px=50, buffer_px=1)
    train &= reporting
    test &= reporting
    regions = {
        name: reporting_mask(
            grid, {"reporting_bounds": bounds, "reporting_crs": spec["reporting_crs"]}
        )
        for name, bounds in spec["attenuation_regions"].items()
    }
    regions["whole_reporting_diagnostic"] = reporting.copy()
    terrain = terrain_classes(depth, grid.pixel_size_m)
    target_rrs = crop(scene.rrs_above[vis]).copy()
    target_kd = crop(kd_proxy)
    invertible = reporting & (lee.optical_depth_score(depth, target_kd) <= masks.optical_depth_max)
    fitting_indices = [
        int(np.argmin(np.abs(scene.wavelengths_nm - wl))) for wl in spec["wavelengths_fit_nm"]
    ]
    target_rhos = {
        float(scene.wavelengths_nm[i]): crop(scene.rhos[i]).copy() for i in fitting_indices
    }
    all_rhos = {float(w): scene.rhos[i] for i, w in enumerate(scene.wavelengths_nm)}
    att_cfg = AttenuationConfig(**spec["attenuation_config"])
    references, products, rho_by_reference = {}, {}, {}
    raster_tags = {
        "OCEANSTREAM_STATUS": "diagnostic_only",
        "OCEANSTREAM_DEPTH_SOURCE": "lidar_plus_gauge",
        "OCEANSTREAM_OPTICAL_ACCURACY_VALIDATED": "false",
    }

    def write(name, array, raster_grid=grid, **tags):
        path = output / f"{name}.tif"
        write_cog(path, array, raster_grid, tags={**raster_tags, **tags})
        products[name] = {"path": path.name, "sha256": file_digest(path)}

    write("depth_lidar_plus_gauge", np.where(reporting, depth, np.nan))
    write("reference_coverage", np.isfinite(elevation).astype("float32"))
    write("reporting_mask", reporting.astype("float32"))
    write("calibration_mask", train.astype("float32"))
    write("evaluation_mask", test.astype("float32"))
    for name, mask in regions.items():
        write(f"region_{name}", mask.astype("float32"))
    for label in ("flat", "rugose"):
        write(f"terrain_{label}", (terrain[label] & reporting).astype("float32"))
    for case in spec["reference_cases"]:
        selected = patches[case]
        n_deep = int(selected.sum())
        row = {
            "candidate_pixels": int(candidates[case].sum()),
            "sampled_pixels": n_deep,
            "offshore_depth_m": stats(offshore_depth[selected]),
            "spectrum_rhos": {str(w): stats(a[selected]) for w, a in all_rhos.items()},
            "status": "diagnostic_only",
        }
        references[case] = row
        write(f"deep_reference_{case}", selected.astype("float32"), scene.grid)
        if n_deep < RetrievalConfig().min_deep_pixels:
            row.update(status="unassessable", reasons=["insufficient_deep_reference"])
            continue
        offset = deepwater_additive_offset(all_rhos, selected)
        row["offset_diagnostic"] = offset.to_dict()
        noise = effective_threshold_rhos(
            all_rhos,
            offshore_depth,
            candidates[case],
            pixel_size_m=scene.grid.pixel_size_m,
        )
        epsilon = noise.get("effective_threshold_rhos")
        epsilon = (
            float(epsilon) if epsilon is not None and np.isfinite(epsilon) and epsilon > 0 else None
        )
        row["noise"] = noise
        row["noise_source"] = (
            "offshore red-band scatter proxy; local reporting noise not independently measured"
        )
        samples = np.stack([scene.rrs_above[i][selected] for i in vis])[:, None, :]
        iops = fit_scene_iops(
            samples, wl_visible, np.ones((1, n_deep), bool), scene.solar_zenith_deg
        )
        physical_flags = qaa_flags(iops)
        row["iops"] = asdict(iops)
        row["qaa_physical_flags"] = physical_flags
        row["regional_attenuation"] = {}
        # Shape-only compaction preserves every sample but avoids repeatedly scanning offshore gaps.
        compact = {
            float(scene.wavelengths_nm[i]): np.concatenate(
                [
                    target_rhos[float(scene.wavelengths_nm[i])].ravel(),
                    scene.rhos[i][selected],
                ]
            )[None, :]
            for i in fitting_indices
        }
        compact_depth = np.concatenate([depth.ravel(), np.full(n_deep, np.nan)])[None, :]
        compact_deep = np.concatenate([np.zeros(depth.size, bool), np.ones(n_deep, bool)])[None, :]
        for name, region in regions.items():
            split_fits = {}
            for split_name, split_mask in (("calibration", train), ("heldout", test)):
                compact_valid = np.concatenate(
                    [(region & split_mask).ravel(), np.zeros(n_deep, bool)]
                )[None, :]
                split_fits[split_name] = calibrate_bands(
                    compact,
                    compact_depth,
                    compact_valid,
                    compact_deep,
                    config=att_cfg,
                    epsilon_rhos=epsilon,
                    solar_zenith_deg=scene.solar_zenith_deg,
                )
            calibration, heldout = split_fits["calibration"], split_fits["heldout"]
            row["regional_attenuation"][name] = {
                "n_calibration": int((region & train).sum()),
                "n_heldout": int((region & test).sum()),
                "calibration": {str(w): band_record(c) for w, c in calibration.items()},
                "heldout": {str(w): band_record(c) for w, c in heldout.items()},
                "heldout_prediction": {
                    str(w): heldout_residual(c, heldout[w]) for w, c in calibration.items()
                },
                "floor_verdict": scene_floor_verdict(calibration, scene.solar_zenith_deg),
                "qaa_comparison": compare_with_qaa(calibration, iops, scene.solar_zenith_deg),
                "accepted": False,
                "acceptance_limits": [
                    "independent_optical_validation_missing",
                    "local_noise_unverified",
                ],
            }
        rho = lee.invert_scene(
            target_rrs, wl_visible, depth, iops, invertible, scene.solar_zenith_deg
        )
        rho_by_reference[case] = rho
        reconstructed = reconstruct_rrs(rho, depth, iops, scene.solar_zenith_deg)
        row["bottom_reflectance"] = {}
        for i, wl in enumerate(wl_visible):
            physical = reporting & np.isfinite(rho[i]) & (rho[i] >= 0) & (rho[i] <= 1)
            physical_fraction = float(physical.sum() / reporting.sum()) if reporting.any() else 0.0
            row["bottom_reflectance"][str(wl)] = {
                **bottom_summary(rho[i], depth, reporting, terrain),
                "regions": {
                    key: bottom_summary(rho[i], depth, reporting & mask, terrain)
                    for key, mask in regions.items()
                },
                "algebraic_closure_absolute_rrs": stats(
                    np.abs(reconstructed[i][physical] - target_rrs[i][physical])
                ),
                "algebraic_closure_is_validation": False,
                "accepted": False,
            }
            write(
                f"rho_b_{case}_{wl:g}",
                rho[i],
                OCEANSTREAM_QAA_FLAGS=";".join(physical_flags),
                OCEANSTREAM_REFERENCE_FLAGS=";".join(offset.qa_flags),
                OCEANSTREAM_PHYSICAL_FRACTION=str(physical_fraction),
            )
        row["product_verdicts"] = {
            "attenuation": {
                "status": "diagnostic_only",
                "accepted": False,
                "reference_flags": list(offset.qa_flags),
            },
            "bottom_reflectance": {
                "status": "diagnostic_only",
                "accepted": False,
                "qaa_physical_checks_passed": not physical_flags,
                "qaa_flags": physical_flags,
                "reference_flags": list(offset.qa_flags),
                "missing": [
                    "independent_optical_accuracy",
                    "complete_model_uncertainty",
                    "local_water_mass_verification",
                ],
            },
        }
        if case == "pooled":
            sensitivity = {}
            for delta in spec["sensitivity_depth_offsets_m"]:
                scenario = np.where(depth + delta > 0, depth + delta, np.nan)
                alternative = lee.invert_scene(
                    target_rrs, wl_visible, scenario, iops, invertible, scene.solar_zenith_deg
                )
                sensitivity[f"depth_{delta:+g}m"] = {
                    str(w): comparison(rho[i], alternative[i], reporting)
                    for i, w in enumerate(wl_visible)
                }
            for factor in spec["sensitivity_iop_factors"]:
                aw, bbw = water.a_water(wl_visible), water.bb_water(wl_visible)
                scenario_iops = replace(
                    iops, a=aw + factor * (iops.a - aw), bb=bbw + factor * (iops.bb - bbw)
                )
                alternative = lee.invert_scene(
                    target_rrs, wl_visible, depth, scenario_iops, invertible, scene.solar_zenith_deg
                )
                sensitivity[f"nonwater_iops_x{factor:g}"] = {
                    str(w): comparison(rho[i], alternative[i], reporting)
                    for i, w in enumerate(wl_visible)
                }
            row["sensitivity_scenarios"] = sensitivity
    reference_sensitivity = {}
    if "pooled" in rho_by_reference:
        for case in ("offshore_west", "offshore_east"):
            if case in rho_by_reference:
                reference_sensitivity[case] = {
                    str(w): comparison(
                        rho_by_reference["pooled"][i], rho_by_reference[case][i], reporting
                    )
                    for i, w in enumerate(wl_visible)
                }
    result = _json_safe(
        {
            "schema_version": "1.0",
            "experiment": spec["experiment_id"],
            "variant": cfg["variant"],
            "execution_success": True,
            "status": "diagnostic_only",
            "accepted": False,
            "release_evidence_complete": False,
            "verified_input_lock": lock,
            "scene": scene.describe(),
            "config": cfg,
            "registration": spec,
            "depth_source": "fixed_lidar_plus_independent_gauge",
            "satellite_depth_used": False,
            "tide": tide,
            "grid": grid.to_dict(),
            "coverage": {
                "requested_reporting_pixels": int(crop(report_wide).sum()),
                "lidar_covered_pixels": int(np.isfinite(elevation).sum()),
                "eligible_reporting_pixels": int(reporting.sum()),
                "calibration_pixels": int(train.sum()),
                "heldout_pixels": int(test.sum()),
                "instantaneous_depth_m": stats(depth[reporting]),
            },
            "references": references,
            "reference_sensitivity": reference_sensitivity,
            "products": products,
            "limitations": spec["limitations"],
            "completed_at": datetime.now(UTC).isoformat(),
        }
    )
    write_json_document(output / "results.json", result)
    # Recheck the frozen inputs before announcing completion.
    verify_input_lock(resolve(cfg["input_lock"]))
    write_json_document(
        output / "manifest.json",
        {
            "schema_version": "1.0",
            "complete": True,
            "status": "diagnostic_only",
            "results": {"path": "results.json", "sha256": file_digest(output / "results.json")},
            "products": products,
        },
    )
    return result


def freeze(config_paths: list[Path], output: Path) -> None:
    if output.exists():
        raise FileExistsError("Use a new input lock.")
    root = Path(__file__).resolve().parents[2]
    paths = set((root / "oceanstream" / "coastal").rglob("*.py"))
    paths.update((root / "scripts" / "coastal").glob("*key_west*.py"))
    for config in config_paths:
        config = config.resolve()
        cfg = json.loads(config.read_text())
        base = config.parent
        paths.add(config)
        for name in ("registration", "tide", "offshore_elevation", "lidar_elevation"):
            paths.add((base / cfg[name]).resolve())
        for directory in (base / cfg["acolite_dir"], base / cfg["inputs_dir"]):
            paths.update(p for p in directory.iterdir() if p.suffix in {".tif", ".json", ".txt"})
        paths.update((base / p).resolve() for p in cfg["additional_provenance_files"])
    payload = {
        "schema_version": "1.0",
        "frozen_at": datetime.now(UTC).isoformat(),
        "files": {str(p.resolve()): file_digest(p) for p in sorted(paths)},
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x") as stream:
        json.dump(payload, stream, indent=2)
    print(f"Frozen {len(paths)} files")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("run", "freeze"))
    parser.add_argument("configs", type=Path, nargs="+")
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    logging.basicConfig(level=logging.WARNING)
    if arguments.command == "freeze":
        if arguments.output is None:
            parser.error("freeze requires --output")
        freeze(arguments.configs, arguments.output)
    else:
        for config_path in arguments.configs:
            report = run(config_path)
            print(
                json.dumps(
                    {
                        "variant": report["variant"],
                        "status": report["status"],
                        "coverage": report["coverage"],
                    },
                    indent=2,
                )
            )
