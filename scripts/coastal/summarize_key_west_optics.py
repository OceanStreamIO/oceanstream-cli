"""Verify frozen Key West optical outputs and publish a compact diagnostic report."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import rasterio
from run_key_west_reference import align_reference, verify_input_lock

from oceanstream.coastal.io.rasters import RasterGrid
from oceanstream.coastal.provenance import file_digest

VARIANTS = {"exp_published": "EXP reconstruction", "dsf_current": "Current DSF"}
VISIBLE = ("443.0", "492.0", "560.0", "665.0")
REGIONS = ("south", "basin", "north")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def read_raster(directory, name):
    with rasterio.open(directory / f"{name}.tif") as src:
        return src.read(1, masked=True).filled(np.nan)


def verify_result(root: Path, variant: str) -> tuple[dict, dict]:
    directory = root / "out/coastal-beta/key-west-optics-v1" / variant
    result_path = directory / "results.json"
    result = json.loads(result_path.read_text())
    manifest = json.loads((directory / "manifest.json").read_text())
    require(manifest["complete"] and result["execution_success"], "Run is incomplete.")
    require(result["status"] == "diagnostic_only" and not result["accepted"], "Invalid verdict.")
    require(not result["satellite_depth_used"], "Optical experiment must use fixed lidar depth.")
    require(manifest["results"]["sha256"] == file_digest(result_path), "Result checksum changed.")
    require(manifest["products"] == result["products"], "Product inventories disagree.")
    for name, product in result["products"].items():
        path = directory / product["path"]
        require(file_digest(path) == product["sha256"], f"Product checksum changed: {name}")
        with rasterio.open(path) as src:
            tags = src.tags()
            require(tags["OCEANSTREAM_STATUS"] == "diagnostic_only", f"Bad status: {name}")
            require(tags["OCEANSTREAM_OPTICAL_ACCURACY_VALIDATED"] == "false", name)
            require(src.tags(ns="IMAGE_STRUCTURE").get("LAYOUT") == "COG", name)
            if name.startswith("rho_b_"):
                case = name.removeprefix("rho_b_").rsplit("_", 1)[0]
                flags = result["references"][case]["qaa_physical_flags"]
                require(tags["OCEANSTREAM_QAA_FLAGS"] == ";".join(flags), name)
    reporting = read_raster(directory, "reporting_mask") > 0.5
    train = read_raster(directory, "calibration_mask") > 0.5
    test = read_raster(directory, "evaluation_mask") > 0.5
    depth = read_raster(directory, "depth_lidar_plus_gauge")
    grid = RasterGrid.open(directory / "depth_lidar_plus_gauge.tif")
    config_dir = root / "benchmarks/coastal-beta/key-west-optics-v1"
    elevation, ids = align_reference(config_dir / result["config"]["lidar_elevation"], grid)
    expected = -elevation.astype(np.float32) + result["tide"]["offset_m"]
    require(np.array_equal(np.isfinite(depth), reporting), "Depth coverage changed.")
    require(np.allclose(depth[reporting], expected[reporting], atol=1e-6), "Depth formula differs.")
    require(not np.any(reporting & ~np.isfinite(elevation)), "Lidar holes were filled.")
    require(not np.any(train & test), "Calibration/evaluation overlap.")
    shared = np.intersect1d(ids[train], ids[test])
    require(not shared.size, "Calibration/evaluation share reference source cells.")
    for key, mask in (
        ("eligible_reporting_pixels", reporting),
        ("calibration_pixels", train),
        ("heldout_pixels", test),
    ):
        require(int(mask.sum()) == result["coverage"][key], f"Coverage mismatch: {key}")
    green_by_depth = {}
    for case, reference in result["references"].items():
        for w, recorded in reference["bottom_reflectance"].items():
            rho = read_raster(directory, f"rho_b_{case}_{float(w):g}")
            finite = reporting & np.isfinite(rho)
            physical = finite & (rho >= 0) & (rho <= 1)
            require(int(finite.sum()) == recorded["library_retained"]["n"], "Retained count.")
            require(int(physical.sum()) == recorded["physical_0_1_pixels"], "Physical count.")
            require(
                np.isclose(np.median(rho[finite]), recorded["library_retained"]["median"]),
                "Reflectance median differs.",
            )
            if case == "pooled" and w == "560.0":
                for lo, hi in ((0.5, 2), (2, 5), (5, 7.5)):
                    chosen = reporting & (depth >= lo) & (depth < hi)
                    green_by_depth[f"{lo:g}-{hi:g}m"] = {
                        "requested": int(chosen.sum()),
                        "retained": int((chosen & finite).sum()),
                        "physical_0_1": int((chosen & physical).sum()),
                    }
    return result, {
        "verified_products": len(result["products"]),
        "source_cells_shared_between_splits": int(shared.size),
        "fixed_depth_formula_and_missingness_verified": True,
        "raster_statistics_and_diagnostic_flags_verified": True,
        "green_coverage_by_depth": green_by_depth,
        "results_sha256": file_digest(result_path),
    }


def compact(result: dict, verification: dict) -> dict:
    pooled = result["references"]["pooled"]
    regions = {}
    fit_fields = (
        "k_per_m",
        "k_pure_water_floor",
        "fit_checks_passed",
        "trust_failures",
        "r_squared",
        "n_bins",
        "n_pixels",
        "frac_nonpositive_residual",
        "k_standard_error",
        "bin_support",
        "assessable",
    )
    for name, region in pooled["regional_attenuation"].items():
        regions[name] = {
            key: region[key]
            for key in (
                "n_calibration",
                "n_heldout",
                "floor_verdict",
                "qaa_comparison",
                "heldout_prediction",
                "accepted",
                "acceptance_limits",
            )
        }
        for split in ("calibration", "heldout"):
            regions[name][split] = {
                w: {key: fit[key] for key in fit_fields if key in fit}
                for w, fit in region[split].items()
            }
    return {
        "status": result["status"],
        "accepted": result["accepted"],
        "coverage": result["coverage"],
        "verification": verification,
        "local_visible_band_fit_checks_passed": sum(
            regions[name]["calibration"][w]["fit_checks_passed"]
            for name in REGIONS
            for w in VISIBLE
        ),
        "local_visible_band_fit_checks_total": len(REGIONS) * len(VISIBLE),
        "regional_attenuation": regions,
        "bottom_reflectance": {
            w: {key: value for key, value in row.items() if key != "regions"}
            for w, row in pooled["bottom_reflectance"].items()
        },
        "references": {
            name: {
                key: row[key]
                for key in (
                    "sampled_pixels",
                    "offshore_depth_m",
                    "spectrum_rhos",
                    "offset_diagnostic",
                    "iops",
                    "qaa_physical_flags",
                )
            }
            for name, row in result["references"].items()
        },
        "product_verdicts": pooled["product_verdicts"],
        "reference_sensitivity": result["reference_sensitivity"],
        "sensitivity_scenarios": pooled["sensitivity_scenarios"],
    }


def plot(summary: dict, destination: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D

    plt.rcParams.update({"font.size": 10, "axes.spines.top": False, "axes.spines.right": False})
    fig, axes = plt.subplots(2, 2, figsize=(13, 9), layout="constrained")
    colors = ("#007C91", "#B65B13", "#6852A3")
    x = np.arange(len(VISIBLE))
    for ax, (variant, title) in zip(axes[0], VARIANTS.items()):
        row = summary["variants"][variant]
        for region, color in zip(REGIONS, colors):
            fit = row["regional_attenuation"][region]
            for split, style in (("calibration", "-"), ("heldout", ":")):
                values = [fit[split][w]["k_per_m"] for w in VISIBLE]
                ax.plot(
                    x, values, style, color=color, lw=1.8, label=region if style == "-" else None
                )
                for i, w in enumerate(VISIBLE):
                    passed = fit[split][w]["fit_checks_passed"]
                    ax.scatter(i, values[i], marker="o" if passed else "x", s=35, color=color)
        floor = [
            row["regional_attenuation"]["south"]["calibration"][w]["k_pure_water_floor"]
            for w in VISIBLE
        ]
        ax.plot(x, floor, "--", color="#525252", label="Pure-water minimum")
        ax.axhline(0, color="#BBBBBB", lw=0.8)
        ax.set(
            xticks=x,
            xticklabels=[int(float(w)) for w in VISIBLE],
            xlabel="Wavelength (nm)",
            ylabel="Effective two-way k (m⁻¹)",
            title=f"{title}: local attenuation",
            ylim=(-0.45, 1.05),
        )
        ax.grid(axis="y", alpha=0.15)
    axes[0, 0].legend(loc="upper left", ncol=2, fontsize=9)
    axes[0, 1].legend(
        handles=[
            Line2D([], [], color="#333333", label="Calibration"),
            Line2D([], [], color="#333333", ls=":", label="Held-out fit"),
            Line2D([], [], color="#333333", marker="o", ls="", label="Band checks pass"),
            Line2D([], [], color="#333333", marker="x", ls="", label="Band checks fail"),
        ],
        loc="upper left",
        ncol=2,
        fontsize=9,
    )
    ac_colors = ("#007C91", "#B65B13")
    ax = axes[1, 0]
    for i, (variant, title) in enumerate(VARIANTS.items()):
        values = [
            100 * summary["variants"][variant]["bottom_reflectance"][w]["physical_0_1_fraction"]
            for w in VISIBLE
        ]
        bars = ax.bar(x + (i - 0.5) * 0.36, values, 0.36, color=ac_colors[i], label=title)
        ax.bar_label(bars, fmt="%.1f", fontsize=9)
    ax.set(
        xticks=x,
        xticklabels=[int(float(w)) for w in VISIBLE],
        ylim=(0, 112),
        xlabel="Wavelength (nm)",
        ylabel="Eligible reporting pixels (%)",
        title="Bottom reflectance within 0–1; accuracy unverified",
    )
    ax.legend(loc="lower left", fontsize=9)
    ax = axes[1, 1]
    bins = ("0.5-2m", "2-5m", "5-7.5m")
    for i, (variant, title) in enumerate(VARIANTS.items()):
        coverage = summary["variants"][variant]["verification"]["green_coverage_by_depth"]
        values = [100 * coverage[b]["physical_0_1"] / coverage[b]["requested"] for b in bins]
        bars = ax.bar(
            np.arange(3) + (i - 0.5) * 0.36, values, 0.36, color=ac_colors[i], label=title
        )
        ax.bar_label(bars, fmt="%.1f", fontsize=9)
    ax.set(
        xticks=np.arange(3),
        xticklabels=bins,
        ylim=(0, 112),
        xlabel="Fixed lidar-plus-gauge depth",
        ylabel="Eligible pixels in each depth bin (%)",
        title="Green bottom reflectance within 0–1 by depth",
    )
    ax.legend(loc="upper right", fontsize=9)
    fig.suptitle(
        "Key West optical consistency experiment · 8 February 2017\n"
        "Both runs diagnostic only · QAA plausibility checks fail",
        fontsize=15,
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(destination, dpi=170)
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    args = parser.parse_args()
    root = args.root.resolve()
    locks = {
        name: verify_input_lock(root / "benchmarks/coastal-beta" / name / "input-lock.json")
        for name in ("key-west-v2", "key-west-optics-v1")
    }
    summary = {
        "schema_version": "1.0",
        "status": "diagnostic_only",
        "accepted": False,
        "input_locks": locks,
        "summary_script_sha256": file_digest(Path(__file__)),
        "variants": {},
    }
    for variant in VARIANTS:
        result, verification = verify_result(root, variant)
        summary["variants"][variant] = compact(result, verification)
        summary["registration"] = result["registration"]
        summary["tide"] = result["tide"]
        print(
            f"Verified {variant}: {verification['verified_products']} products; "
            "fixed depth and disjoint source cells confirmed."
        )
    path = root / "docs/coastal/key-west-optics-results.json"
    path.write_text(json.dumps(summary, indent=2, allow_nan=False) + "\n")
    plot(summary, root / "docs/coastal/assets/key-west-optics.png")


if __name__ == "__main__":
    main()
