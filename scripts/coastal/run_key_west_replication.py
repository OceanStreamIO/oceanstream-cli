"""Frozen calibration-count comparison with fresh geographical SDB evaluation.

Commands are separate: freeze pins inputs, fit reads development lidar only,
and evaluate checks the sealed fit before opening fresh reference depths.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
from run_key_west_reference import (
    align_reference,
    literal_ratio,
    median_supported,
    reporting_mask,
    score_depth,
    verify_input_lock,
)
from scipy.optimize import least_squares

from oceanstream.coastal.io.rasters import write_cog
from oceanstream.coastal.provenance import file_digest
from oceanstream.coastal.scene import Scene

ROOT = Path(__file__).resolve().parents[2]
REGISTRATION = ROOT / "benchmarks/coastal-beta/key-west-replication-v1"
CACHE = ROOT / ".cache/coastal/reference/key-west-replication-v1"
OUTPUT = ROOT / "out/coastal-beta/key-west-replication-v1"
VARIANTS = ("exp_published", "dsf_current")
OLD = ROOT / ".cache/coastal/reference/key-west-2017"


def write_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")
    temp.replace(path)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def validate_geography(spec):
    a, b, c, d = spec["development_bounds"]
    for name, (w, s, e, n) in spec["fresh_evaluation_bounds"].items():
        require(w < e and s < n, f"Invalid bounds: {name}")
        dx, dy = max(a - e, w - c, 0), max(b - n, s - d, 0)
        require(np.hypot(dx, dy) >= 500, f"Fresh region lacks 500m separation: {name}")
    return True


def load_spec():
    spec = json.loads((REGISTRATION / "experiment.json").read_text())
    validate_geography(spec)
    return spec


def scenes_and_features():
    scenes, features = {}, {}
    for variant in VARIANTS:
        scene = Scene.from_acolite_dir(OLD / "acolite-transect" / variant)
        if scenes:
            require(scene.grid.matches(next(iter(scenes.values())).grid), "AC grids differ.")
        scenes[variant] = scene
        blue, green, red = (scene.band(w) for w in (490, 560, 665))
        features[variant] = {}
        for label, function in (("median", median_supported), ("raw", lambda a: a)):
            first, second, third = map(function, (blue, green, red))
            features[variant][label] = np.stack(
                [literal_ratio(first, third), literal_ratio(first, second)], axis=-1
            )
    return scenes, features


def region_mask(grid, bounds):
    return reporting_mask(grid, {"reporting_bounds": bounds, "reporting_crs": "EPSG:6346"})


def blocks(grid, block_m=500):
    rr, cc = np.indices(grid.shape)
    a, _, x0, _, e, y0 = grid.transform
    xs = np.floor((x0 + (cc + 0.5) * a) / block_m).astype("int64")
    ys = np.floor((y0 + (rr + 0.5) * e) / block_m).astype("int64")
    return ys * 100000 + xs


def select_nine(depth, eligible, block_ids, targets, seed):
    """Depth-stratified observations in distinct blocks; no prediction-based choices."""
    rng = np.random.default_rng(seed)
    order = rng.permutation(np.flatnonzero(eligible))
    picked, used = [], set()
    for target in targets:
        remaining = order[~np.isin(block_ids.ravel()[order], list(used))]
        require(remaining.size, "Insufficient independent calibration blocks for nine points.")
        index = int(remaining[np.argmin(np.abs(depth.ravel()[remaining] - target))])
        picked.append(index)
        used.add(int(block_ids.ravel()[index]))
    return np.array(picked)


def fit_model(features, depth, kind="linear", robust=False, weights=None):
    """Return a portable scaled design and coefficients, without access to evaluation data."""
    require(
        len(depth) >= 5 and np.isfinite(features).all() and np.isfinite(depth).all(),
        "Fit requires at least five finite observations.",
    )
    selected = features if kind == "combined" else features[:, :1]
    center, scale = selected.mean(axis=0), selected.std(axis=0)
    require(np.all(scale > 1e-8), "Degenerate calibration features.")
    z = (selected - center) / scale
    design = (
        np.column_stack([np.ones(len(z)), z, z**2])
        if kind == "quadratic"
        else np.column_stack([np.ones(len(z)), z])
    )
    require(np.linalg.matrix_rank(design) == design.shape[1], "Rank-deficient calibration.")
    weights = np.ones(len(depth)) if weights is None else np.asarray(weights)
    weights = weights / weights.mean()
    initial = np.linalg.lstsq(
        design * np.sqrt(weights[:, None]), depth * np.sqrt(weights), rcond=None
    )[0]
    if robust:
        optimized = least_squares(
            lambda coef: np.sqrt(weights) * (design @ coef - depth),
            initial,
            loss="huber",
            f_scale=0.25,
            max_nfev=500,
        )
        require(optimized.success, "Robust calibration failed to converge.")
        initial = optimized.x
    return {
        "kind": kind,
        "center": center.tolist(),
        "scale": scale.tolist(),
        "coefficients": initial.tolist(),
        "robust": robust,
        "n_calibration": len(depth),
        "weighting": "explicit" if not np.allclose(weights, 1) else "uniform",
    }


def predict(features, model):
    if model["kind"] == "published":
        return model["m1"] * features[..., 0] - model["m0"]
    selected = features if model["kind"] == "combined" else features[..., :1]
    z = (selected - np.array(model["center"])) / np.array(model["scale"])
    value = model["coefficients"][0] + np.sum(
        z * model["coefficients"][1 : 1 + z.shape[-1]], axis=-1
    )
    if model["kind"] == "quadratic":
        value += model["coefficients"][-1] * z[..., 0] ** 2
    return value


def freeze():
    load_spec()
    lock = REGISTRATION / "input-lock.json"
    require(not lock.exists(), "Input lock already exists; preserve the registered experiment.")
    paths = set((ROOT / "oceanstream/coastal").rglob("*.py"))
    paths.update((ROOT / "scripts/coastal").glob("*key_west*.py"))
    paths.update(REGISTRATION.glob("*.json"))
    paths.update(p for p in CACHE.rglob("*") if p.is_file())
    paths.update(p for p in (OLD / "lidar-transect").iterdir() if p.suffix in {".json", ".tif"})
    for variant in VARIANTS:
        paths.update(
            p
            for p in (OLD / "acolite-transect" / variant).iterdir()
            if p.suffix in {".tif", ".txt", ".json"}
        )
    paths.add(OLD / "scene/scene-manifest.json")
    paths.add(OLD / "lidar/reference-paper.pdf")
    for name in ("west", "east"):
        require(
            (CACHE / name / "lidar/elevation_mllw_10m.tif").exists(), f"Missing reference: {name}"
        )
    write_json(
        lock,
        {
            "schema_version": "1.0",
            "frozen_at": datetime.now(UTC).isoformat(),
            "files": {str(p.resolve()): file_digest(p) for p in sorted(paths)},
        },
    )
    print(f"Frozen {len(paths)} input/code files.", flush=True)


def fit():
    lock = verify_input_lock(REGISTRATION / "input-lock.json")
    spec = load_spec()
    require(not OUTPUT.exists() or not any(OUTPUT.iterdir()), "Use a new experiment output.")
    OUTPUT.mkdir(parents=True, exist_ok=True)
    scenes, features = scenes_and_features()
    grid = scenes[VARIANTS[0]].grid
    elevation, source_ids = align_reference(OLD / "lidar-transect/elevation_mllw_10m.tif", grid)
    depth = -elevation
    common = (
        region_mask(grid, spec["development_bounds"])
        & np.isfinite(depth)
        & (depth > 0)
        & (depth < 5)
    )
    for data in features.values():
        for array in data.values():
            common &= np.isfinite(array).all(axis=-1)
    terrain_sd, _ = align_reference(OLD / "lidar-transect/dem_spatial_stddev_10m.tif", grid)
    block_ids = blocks(grid)
    selected = select_nine(
        depth,
        common & (terrain_sd <= 0.15),
        block_ids,
        spec["nine_point_fallback"]["target_depths_m"],
        spec["seed"],
    )
    dense = np.random.default_rng(spec["seed"]).permutation(np.flatnonzero(common))[:10000]
    masks = {"nine": selected, "dense": dense}
    models = {}
    for variant in VARIANTS:
        models[variant] = {
            "published": {
                "kind": "published",
                "m1": 5.8,
                "m0": 5.9,
                "n_calibration": 0,
                "filter": "median",
                "budget": "historical_coefficients",
            }
        }
        for regime, indices in masks.items():
            y = depth.ravel()[indices]
            x = features[variant]["median"].reshape(-1, 2)[indices]
            cases = [
                ("linear", "linear", False),
                ("huber", "linear", True),
                ("quadratic", "quadratic", False),
                ("combined", "combined", False),
            ]
            for suffix, kind, robust in cases:
                models[variant][f"{regime}_{suffix}"] = {
                    **fit_model(x, y, kind, robust),
                    "filter": "median",
                    "budget": regime,
                }
            raw = features[variant]["raw"].reshape(-1, 2)[indices]
            models[variant][f"{regime}_linear_raw"] = {
                **fit_model(raw, y),
                "filter": "raw",
                "budget": regime,
            }
            if regime == "dense":
                labels = y.astype(int)
                counts = np.bincount(labels, minlength=5)
                weights = 1 / counts[labels]
                models[variant]["dense_depth_balanced"] = {
                    **fit_model(x, y, weights=weights),
                    "filter": "median",
                    "budget": regime,
                }
    rr, cc = np.unravel_index(selected, grid.shape)
    a, _, x0, _, e, y0 = grid.transform
    records = [
        {
            "grid_row": int(r),
            "grid_col": int(c),
            "x": float(x0 + (c + 0.5) * a),
            "y": float(y0 + (r + 0.5) * e),
            "depth_mllw_m": float(depth[r, c]),
            "source_cell_id": int(source_ids[r, c]),
            "block_id": int(block_ids[r, c]),
            "terrain_sd_m": float(terrain_sd[r, c]),
        }
        for r, c in zip(rr, cc)
    ]
    bundle = {
        "schema_version": "1.0",
        "fitted_at": datetime.now(UTC).isoformat(),
        "input_lock": lock,
        "registration": spec,
        "models": models,
        "calibration_source": "development_lidar_proxy_not_chart_soundings",
        "nine_points": records,
        "dense_flat_indices": dense.tolist(),
        "dense_depth_bin_counts": np.bincount(
            depth.ravel()[dense].astype(int), minlength=5
        ).tolist(),
        "fresh_reference_read_during_fit": False,
        "test_based_model_selection": False,
    }
    write_json(OUTPUT / "fits.json", bundle)
    verify_input_lock(REGISTRATION / "input-lock.json")
    write_json(
        OUTPUT / "fit-manifest.json",
        {"complete": True, "fits_sha256": file_digest(OUTPUT / "fits.json")},
    )
    print(
        f"Sealed {sum(map(len, models.values()))} models before fresh reference evaluation.",
        flush=True,
    )


def bootstrap_difference(prediction, baseline, depth, valid, block_ids, seed, count=1000):
    """Paired pixel-weighted MAE difference; resample whole 500m blocks."""
    common = valid & np.isfinite(prediction) & np.isfinite(baseline) & np.isfinite(depth)
    if not common.any():
        return {"n_pixels": 0, "n_blocks": 0, "ci95_mae_difference_m": None}
    _, ids = np.unique(block_ids[common], return_inverse=True)
    difference = np.abs(prediction[common] - depth[common]) - np.abs(
        baseline[common] - depth[common]
    )
    sums, counts = np.bincount(ids, weights=difference), np.bincount(ids)
    n = len(counts)
    draws = np.random.default_rng(seed).integers(0, n, (count, n))
    estimates = sums[draws].sum(axis=1) / counts[draws].sum(axis=1)
    return {
        "n_pixels": int(common.sum()),
        "n_blocks": n,
        "mae_difference_m": float(difference.mean()),
        "ci95_mae_difference_m": list(map(float, np.percentile(estimates, [2.5, 97.5]))),
        "interpretation": (
            "Candidate minus baseline MAE; negative favors candidate. "
            "Conditional spatial sampling interval, not full uncertainty."
        ),
    }


def evaluate():
    lock = verify_input_lock(REGISTRATION / "input-lock.json")
    spec = load_spec()
    manifest = json.loads((OUTPUT / "fit-manifest.json").read_text())
    require(
        manifest["complete"] and manifest["fits_sha256"] == file_digest(OUTPUT / "fits.json"),
        "Sealed fits changed.",
    )
    require(
        not (OUTPUT / "evaluation").exists(), "Evaluation already exists; preserve the first test."
    )
    fit_data = json.loads((OUTPUT / "fits.json").read_text())
    scenes, features = scenes_and_features()
    grid = scenes[VARIANTS[0]].grid
    depth = np.full(grid.shape, np.nan, dtype="float32")
    regions, coverage = {}, {}
    for name, bounds in spec["fresh_evaluation_bounds"].items():
        elevation, _ = align_reference(CACHE / name / "lidar/elevation_mllw_10m.tif", grid)
        requested = region_mask(grid, bounds)
        depth[requested] = -elevation[requested]
        regions[name] = requested & np.isfinite(elevation) & (-elevation > 0) & (-elevation < 5)
        coverage[name] = {
            "requested_rectangle_pixels": int(requested.sum()),
            "reference_covered_pixels": int((requested & np.isfinite(elevation)).sum()),
            "requested_0_5m_pixels": int(regions[name].sum()),
        }
    valid = np.logical_or.reduce(list(regions.values()))
    require(valid.any(), "No fresh reference coverage.")
    development = region_mask(grid, spec["development_bounds"])
    require(not np.any(valid & development), "Fresh evaluation overlaps development.")
    block_ids = blocks(grid)
    require(
        not np.intersect1d(block_ids[valid], [p["block_id"] for p in fit_data["nine_points"]]).size,
        "Calibration blocks overlap evaluation blocks.",
    )
    predictions = {
        v: {name: predict(features[v][m["filter"]], m) for name, m in models.items()}
        for v, models in fit_data["models"].items()
    }
    common = valid.copy()
    for data in predictions.values():
        for array in data.values():
            common &= np.isfinite(array)
    result = {
        "schema_version": "1.0",
        "experiment": spec["experiment_id"],
        "status": "reference_reconstruction",
        "exact_replication": False,
        "superiority_over_paper_established": False,
        "second_date_evaluated": False,
        "input_lock": lock,
        "fits_sha256": manifest["fits_sha256"],
        "coverage": coverage,
        "common_support_pixels": int(common.sum()),
        "calibration_source": fit_data["calibration_source"],
        "limitations": spec["claim_limits"],
        "models": {},
        "products": {},
    }
    directory = OUTPUT / "evaluation"
    directory.mkdir()

    def save(name, array):
        path = directory / f"{name}.tif"
        write_cog(
            path,
            array.astype("float32"),
            grid,
            tags={
                "OCEANSTREAM_STATUS": "reference_reconstruction",
                "OCEANSTREAM_DEPTH_DATUM": "MLLW",
                "OCEANSTREAM_BLENDED": "false",
                "OCEANSTREAM_CALIBRATION_SOURCE": fit_data["calibration_source"],
            },
        )
        result["products"][name] = {"path": path.name, "sha256": file_digest(path)}

    save("reference_depth_mllw", np.where(valid, depth, np.nan))
    save("evaluation_mask", valid)
    save("common_support_mask", common)
    for variant, data in predictions.items():
        result["models"][variant] = {}
        for name, array in data.items():
            model = fit_data["models"][variant][name]
            paired = {}
            for baseline_name in ("published", "nine_linear", "dense_linear"):
                paired[baseline_name] = bootstrap_difference(
                    array,
                    data[baseline_name],
                    depth,
                    valid,
                    block_ids,
                    spec["seed"],
                    spec["bootstrap_replicates"],
                )
            scores = score_depth(array, depth, valid)
            result["models"][variant][name] = {
                "model": model,
                "scores": scores,
                "scores_common_support": score_depth(array, depth, common),
                "scores_by_region": {
                    key: score_depth(array, depth, mask) for key, mask in regions.items()
                },
                "depth_balanced_mae_m": float(
                    np.mean([r["mae_m"] for r in scores["by_reference_depth"].values()])
                )
                if all(r["mae_m"] is not None for r in scores["by_reference_depth"].values())
                else None,
                "predictions_outside_0_5m": int(
                    (valid & np.isfinite(array) & ((array < 0) | (array > 5))).sum()
                ),
                "paired_block_bootstrap": paired,
            }
            save(f"{variant}_{name}", np.where(valid, array, np.nan))
    result["completed_at"] = datetime.now(UTC).isoformat()
    write_json(directory / "results.json", result)
    verify_input_lock(REGISTRATION / "input-lock.json")
    write_json(
        directory / "manifest.json",
        {
            "complete": True,
            "status": result["status"],
            "results_sha256": file_digest(directory / "results.json"),
            "products": result["products"],
        },
    )
    print(
        json.dumps(
            {
                "coverage": coverage,
                "common_support_pixels": int(common.sum()),
                "completed_models": sum(map(len, predictions.values())),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("freeze", "fit", "evaluate"))
    args = parser.parse_args()
    {"freeze": freeze, "fit": fit, "evaluate": evaluate}[args.command]()
