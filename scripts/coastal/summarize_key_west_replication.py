"""Verify the sealed Key West replication experiment and publish its comparison."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import rasterio
from run_key_west_reference import score_depth, verify_input_lock
from run_key_west_replication import OUTPUT, ROOT, blocks, require, validate_geography

from oceanstream.coastal.io.rasters import RasterGrid
from oceanstream.coastal.provenance import file_digest


def raster(name):
    with rasterio.open(OUTPUT / "evaluation" / f"{name}.tif") as src:
        return src.read(1, masked=True).filled(np.nan)


def verify():
    locks = {
        name: verify_input_lock(ROOT / "benchmarks/coastal-beta" / name / "input-lock.json")
        for name in ("key-west-v2", "key-west-optics-v1", "key-west-replication-v1")
    }
    result_path = OUTPUT / "evaluation/results.json"
    result = json.loads(result_path.read_text())
    manifest = json.loads((OUTPUT / "evaluation/manifest.json").read_text())
    fits = json.loads((OUTPUT / "fits.json").read_text())
    require(manifest["complete"], "Incomplete evaluation.")
    require(manifest["results_sha256"] == file_digest(result_path), "Results changed.")
    require(result["fits_sha256"] == file_digest(OUTPUT / "fits.json"), "Models changed.")
    require(manifest["products"] == result["products"], "Different product inventories.")
    spec = fits["registration"]
    validate_geography(spec)
    require(len(fits["nine_points"]) == 9, "Incorrect observation budget.")
    require(len({r["block_id"] for r in fits["nine_points"]}) == 9, "Repeated calibration block.")
    require(not fits["fresh_reference_read_during_fit"], "Fresh reference used for fitting.")
    require(not fits["test_based_model_selection"], "Test-based model selection.")
    depth = raster("reference_depth_mllw")
    valid = raster("evaluation_mask") > 0.5
    common = raster("common_support_mask") > 0.5
    require(np.array_equal(np.isfinite(depth), valid), "Depth support differs from evaluation.")
    require(int(common.sum()) == result["common_support_pixels"], "Common coverage changed.")
    for name, product in result["products"].items():
        path = OUTPUT / "evaluation" / product["path"]
        require(file_digest(path) == product["sha256"], f"Raster changed: {name}")
        with rasterio.open(path) as src:
            require(src.tags()["OCEANSTREAM_BLENDED"] == "false", "Blended prediction.")
            require(src.tags(ns="IMAGE_STRUCTURE").get("LAYOUT") == "COG", name)
    for variant, models in result["models"].items():
        for name, recorded in models.items():
            array = raster(f"{variant}_{name}")
            scores = score_depth(array, depth, valid)
            for group in ("all", *scores["by_reference_depth"]):
                actual = scores["all"] if group == "all" else scores["by_reference_depth"][group]
                expected = (
                    recorded["scores"]["all"]
                    if group == "all"
                    else recorded["scores"]["by_reference_depth"][group]
                )
                for metric in (
                    "n_requested",
                    "n_scored",
                    "mae_m",
                    "median_ae_m",
                    "rmse_m",
                    "bias_m",
                ):
                    require(
                        np.isclose(actual[metric], expected[metric], atol=1e-6),
                        f"{variant}/{name}/{group}/{metric}",
                    )
    grid = RasterGrid.open(OUTPUT / "evaluation/reference_depth_mllw.tif")
    ids = blocks(grid)
    require(
        not np.intersect1d(ids[valid], [p["block_id"] for p in fits["nine_points"]]).size,
        "Shared calibration/evaluation blocks.",
    )
    summary = {
        **result,
        "products": {
            "local_directory": str(OUTPUT / "evaluation"),
            "verified_rasters": len(result["products"]),
        },
        "verification": {
            "locks": locks,
            "recomputed_raster_scores_match": True,
            "shared_calibration_evaluation_blocks": 0,
            "summary_script_sha256": file_digest(Path(__file__)),
            "results_sha256": file_digest(result_path),
        },
        "registration": spec,
        "nine_calibration_points": fits["nine_points"],
        "dense_calibration_depth_bin_counts": fits["dense_depth_bin_counts"],
        "additional_interpretation_limits": [
            "Nine points were depth-selected using the full development DEM; "
            "nine fitted observations do not mean nine measurements supplied in total.",
            "Lidar observations do not reproduce chart rounding, sounding positions, "
            "survey lineage or selection uncertainty.",
            "Overall scores remain dominated by shallow pixels; "
            "compare every depth stratum and region.",
            "All prespecified candidates are reported. Their relative ranking is now "
            "development evidence for any subsequent model selection.",
        ],
    }
    destination = ROOT / "docs/coastal/key-west-replication-results.json"
    destination.write_text(json.dumps(summary, indent=2, allow_nan=False) + "\n")
    print(f"Verified {len(result['products'])} rasters, 24 model score sets and three input locks.")
    return summary


def plot(summary):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle

    plt.rcParams.update({"font.size": 10, "axes.spines.top": False, "axes.spines.right": False})
    fig, axes = plt.subplots(2, 2, figsize=(13, 10), layout="constrained")
    variants = ("exp_published", "dsf_current")
    colors = ("#007C91", "#B65B13")
    for ax, names, title in (
        (
            axes[0, 0],
            ("nine_linear", "nine_huber", "nine_quadratic", "nine_combined", "nine_linear_raw"),
            "Nine depth-selected lidar observations",
        ),
        (
            axes[0, 1],
            (
                "dense_linear",
                "dense_huber",
                "dense_quadratic",
                "dense_combined",
                "dense_linear_raw",
                "dense_depth_balanced",
            ),
            "10,000 observations · separate calibration budget",
        ),
    ):
        for i, variant in enumerate(variants):
            values = [
                summary["models"][variant][name]["scores_common_support"]["all"]["mae_m"]
                for name in names
            ]
            bars = ax.bar(
                np.arange(len(names)) + (i - 0.5) * 0.36,
                values,
                0.36,
                color=colors[i],
                label=("EXP", "DSF")[i],
            )
            ax.bar_label(bars, fmt="%.3f", fontsize=8, rotation=90, padding=3)
        labels = [name.split("_", 1)[1].replace("_", "\n") for name in names]
        ax.set(
            xticks=np.arange(len(names)),
            xticklabels=labels,
            ylabel="Mean absolute error (m)",
            title=title,
        )
        ax.set_ylim(0, ax.get_ylim()[1] * 1.17)
        ax.legend(fontsize=9)
    ax = axes[1, 0]
    for name, label, color in (
        ("dense_linear", "Linear", "#007C91"),
        ("dense_huber", "Robust", "#B65B13"),
        ("dense_combined", "Combined ratios", "#6852A3"),
        ("dense_depth_balanced", "Depth-balanced", "#3C8031"),
    ):
        bins = summary["models"]["dsf_current"][name]["scores_common_support"]["by_reference_depth"]
        ax.plot(
            np.arange(5), [s["mae_m"] for s in bins.values()], marker="o", label=label, color=color
        )
    ax.set(
        xticks=np.arange(5),
        xticklabels=["0–1", "1–2", "2–3", "3–4", "4–5"],
        xlabel="Reference depth below MLLW (m)",
        ylabel="Mean absolute error (m)",
        title="DSF: depth tradeoffs with 10,000 observations",
    )
    ax.legend(fontsize=9)
    ax.grid(axis="y", alpha=0.15)
    ax = axes[1, 1]
    spec = summary["registration"]
    rectangles = {"Development": spec["development_bounds"], **spec["fresh_evaluation_bounds"]}
    for label, bounds in rectangles.items():
        w, s, e, n = np.asarray(bounds) / 1000
        ax.add_patch(
            Rectangle(
                (w, s),
                e - w,
                n - s,
                color="#D8D8D8" if label == "Development" else "#71B2C3",
                alpha=0.6,
            )
        )
        ax.text((w + e) / 2, n + 0.3, label, ha="center", fontsize=9)
    points = summary["nine_calibration_points"]
    ax.scatter(
        [p["x"] / 1000 for p in points],
        [p["y"] / 1000 for p in points],
        marker="*",
        color="#B65B13",
        s=80,
        label="Nine calibration points",
    )
    ax.set(
        xlim=(420.5, 428.5),
        ylim=(2714.5, 2732),
        xlabel="UTM easting (km)",
        ylabel="UTM northing (km)",
        title="Fresh test strips · 500 m separation",
        aspect="equal",
    )
    ax.legend(loc="center left", bbox_to_anchor=(1.02, 0.5), fontsize=8)
    fig.suptitle(
        "Key West SDB · geographically withheld evaluation\n"
        f"{summary['common_support_pixels']:,} common pixels · one date · "
        "chart calibration not replicated",
        fontsize=15,
    )
    destination = ROOT / "docs/coastal/assets/key-west-replication.png"
    destination.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(destination, dpi=170)
    plt.close(fig)


if __name__ == "__main__":
    plot(verify())
