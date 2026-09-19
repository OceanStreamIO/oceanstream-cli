#!/usr/bin/env python3
"""Export portable Key West results and a standalone held-out depth comparison."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np


def summarize(reports: list[Path], output: Path, figure: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    figure.parent.mkdir(parents=True, exist_ok=True)
    comparisons = []
    fig, axes = plt.subplots(len(reports), 3, figsize=(13.5, 5 * len(reports)), squeeze=False)
    shown = [
        ("published_blue_red", "Published blue/red coefficients"),
        ("library_refit_blue_red", "Library blue/red: spatial recalibration"),
        ("library_default_blue_green", "Library blue/green: spatial recalibration"),
    ]
    for row, report_path in enumerate(reports):
        report = json.loads(report_path.read_text())
        if report.get("execution_success") is not True:
            raise ValueError(f"Report is not a successful execution: {report_path}")
        variant = report["config"]["paper_recipe"]["reconstruction_variant"]
        comparisons.append(
            {
                "variant": variant,
                "report": str(report_path.resolve()),
                "status": report["status"],
                "exact_paper_replication": report["exact_paper_replication"],
                "release_evidence_complete": report["release_evidence_complete"],
                "reference_coverage": report["reference_coverage"],
                "split": report["split"],
                "vertical_datum": report["vertical_datum"],
                "modes": report["modes"],
                "formula_comparison": report["formula_comparison"],
                "limitations": report["limitations"],
            }
        )
        with np.load(report_path.parent / "evaluation_samples.npz") as samples:
            reference = samples["reference_depth_m"]
            for col, (mode, title) in enumerate(shown):
                ax = axes[row, col]
                prediction = samples[mode]
                finite = np.isfinite(reference) & np.isfinite(prediction)
                displayed = finite & (prediction >= -1) & (prediction <= 7)
                if displayed.any():
                    ax.hexbin(
                        reference[displayed], prediction[displayed], gridsize=45,
                        extent=(0, 5, -1, 7), mincnt=1, bins="log", cmap="viridis",
                    )
                ax.plot([0, 5], [0, 5], color="#cf4b36", linestyle="--", linewidth=1)
                baseline = report["modes"]["training_mean_baseline"]["constant_depth_m"]
                ax.axhline(baseline, color="#767676", linestyle=":", linewidth=1)
                scores = report["modes"][mode]["scores"]["all"]
                if scores["n_scored"]:
                    label = (
                        f"Median AE {scores['median_ae_m']:.2f} m\n"
                        f"MAE {scores['mae_m']:.2f} m\n"
                        f"Scored {100 * scores['valid_fraction']:.1f}%"
                    )
                else:
                    label = "No valid predictions"
                outside = int((finite & ~displayed).sum())
                if outside:
                    label += f"\n{outside:,} predictions outside axes"
                ax.text(
                    0.03, 0.97, label, transform=ax.transAxes, va="top", fontsize=9,
                    bbox={"facecolor": "white", "alpha": 0.85, "edgecolor": "none"},
                )
                ax.set(xlim=(0, 5), ylim=(-1, 7), xlabel="Reference MLLW depth (m)")
                ax.set_title(title, fontsize=11)
                if col == 0:
                    correction = (
                        "EXP: published recipe reconstruction"
                        if variant == "exp_published" else "DSF: current correction"
                    )
                    ax.set_ylabel(f"{correction}\nUnblended satellite depth (m)")
                ax.grid(alpha=0.15)
    fig.suptitle("Key West • 8 February 2017 • withheld 500 m spatial blocks", fontsize=15)
    fig.text(
        0.5, 0.015,
        "Red dashed: identity. Grey dotted: training-mean baseline. "
        "Density is logarithmic; pixels are correlated. All results are diagnostic.",
        ha="center", fontsize=10,
    )
    fig.tight_layout(rect=(0, 0.04, 1, 0.95))
    fig.savefig(figure, dpi=170)
    plt.close(fig)
    summary = {
        "schema_version": "1.0",
        "experiment": "key-west-20170208-v2-transect",
        "purpose": "SDB component diagnostic against published successful reference case",
        "comparisons": comparisons,
        "figure": str(figure.resolve()),
    }
    output.write_text(json.dumps(summary, indent=2, allow_nan=False) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reports", type=Path, nargs="+")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--figure", type=Path, required=True)
    args = parser.parse_args()
    summarize(args.reports, args.output, args.figure)


if __name__ == "__main__":
    main()
