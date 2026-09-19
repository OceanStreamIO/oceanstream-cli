"""Export a compact, reviewable summary of an executed coastal benchmark."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

PRODUCTS = ("attenuation", "detectability", "sdb_depth", "rho_b", "seabed_par")


def summarize(report_path: Path, destination: Path) -> dict:
    report = json.loads(report_path.read_text())
    rows = []
    for identifier, run in report["runs"].items():
        diagnostics = run.get("diagnostics", {})
        depth = diagnostics.get("depth_diagnostics", {})
        scores = next(
            (s for s in depth.get("validation", []) if s["stratum_label"] == "all held-out blocks"),
            None,
        )
        bands = {}
        attenuation = run.get("products", {}).get("attenuation")
        if attenuation:
            payload = json.loads(Path(attenuation).read_text())
            payload = payload["attenuation"]
            for region, regional in payload["regions"].items():
                bands[region] = {
                    wavelength: {
                        key: band.get(key)
                        for key in (
                            "k_per_m",
                            "r_squared",
                            "n_bins",
                            "trust_failures",
                            "k_standard_error",
                        )
                    }
                    for wavelength, band in regional["bands"].items()
                }
        rows.append(
            {
                "id": identifier,
                "site": run["site"],
                "date": run["date"],
                "success": run["success"],
                "status": run["status"],
                "message": run.get("message"),
                "elapsed_seconds": run.get("elapsed_seconds"),
                "output_dir": run.get("output_dir"),
                "acolite_version": run.get("acolite_version"),
                "coverage": diagnostics.get("depth_reference", {}).get("coverage", {}),
                "product_verdicts": {
                    key: run.get("verdicts", {}).get(
                        key,
                        {
                            "passed": False,
                            "flags": [run["status"]],
                        },
                    )
                    for key in PRODUCTS
                },
                "sdb_reference_scores": scores,
                "sdb_reference_window_m": diagnostics.get("config", {}).get("bathymetry", {}).get(
                    "fit_reference_range_m"
                ),
                "sdb_depth_range_collapsed": depth.get("map", {}).get("collapsed"),
                "retrieval_depth_source": depth.get("retrieval_depth_source"),
                "attenuation_bands": bands,
            }
        )
    summary = {
        "source_report": str(report_path.resolve()),
        "lock_sha256": report["lock_sha256"],
        "purpose": report["purpose"],
        "gate": report["gate"],
        "n_scenes": len(rows),
        "n_completed": sum(row["success"] for row in rows),
        "n_execution_failed": sum(row["status"] == "execution_failed" for row in rows),
        "n_accepted_by_product": {
            key: sum(row["success"] and row["product_verdicts"][key]["passed"] for row in rows)
            for key in PRODUCTS
        },
        "sdb_score_scope": (
            "Unblended SDB versus spatially withheld native reference blocks, across the "
            "scene in the configured reference window. Pixel counts are correlated "
            "samples, not independent soundings. Tide is missing; these are conditional "
            "reference-agreement errors, not absolute SDB accuracy."
        ),
        "scenes": rows,
    }
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(summary, indent=2, allow_nan=False) + "\n")
    print(
        json.dumps(
            {
                key: summary[key]
                for key in (
                    "n_scenes",
                    "n_completed",
                    "n_execution_failed",
                    "n_accepted_by_product",
                )
            },
            indent=2,
        )
    )
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    summarize(args.report, args.output)
