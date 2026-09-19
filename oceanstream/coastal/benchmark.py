"""Frozen benchmark execution and independent matchup scoring.

Run with ``python -m oceanstream.coastal.benchmark --help``. A completed
benchmark is evidence, not a release approval: scientific review must also
accept its measured errors and documented operating limits.
"""

from __future__ import annotations

import argparse
import csv
import gc
import json
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np

from oceanstream.coastal.aoi import AOI
from oceanstream.coastal.commands import processor_options, read_tide
from oceanstream.coastal.products import write_json_document
from oceanstream.coastal.provenance import file_digest, new_run_directory

PRODUCTS = ("attenuation", "detectability", "sdb_depth", "rho_b", "seabed_par")
UNITS = {
    "attenuation": "m^-1",
    "detectability": "rhos",
    "sdb_depth": "m",
    "rho_b": "1",
    "seabed_par": "1",
}


def _path(base: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else (base / path).resolve()


def freeze(source: Path, target: Path) -> dict[str, Any]:
    """Seal declared inputs before evaluation; refuse missing or remote files."""
    if target.exists():
        raise FileExistsError("A frozen benchmark cannot be overwritten; use a new version.")
    spec = json.loads(source.read_text())
    if spec.get("schema_version") != "1.0" or not spec.get("scenes"):
        raise ValueError("Benchmark requires schema_version 1.0 and scenes.")
    if spec.get("purpose", "scientific_validation") not in {"scientific_validation", "diagnostic"}:
        raise ValueError("Benchmark purpose must be scientific_validation or diagnostic.")
    files: set[Path] = set()
    identifiers: set[str] = set()
    for uri in spec.get("supporting_files", []):
        files.add(_path(source.parent, uri))
    for row in spec["scenes"]:
        if row["id"] in identifiers:
            raise ValueError("Scene IDs must be unique.")
        identifiers.add(row["id"])
        for key in ("aoi", "scene", "config", "tide"):
            if row.get(key):
                row[key] = str(_path(source.parent, row[key]))
                path = Path(row[key])
                if key == "scene":
                    if not path.is_dir():
                        raise ValueError(f"ACOLITE directory missing: {path}")
                    files.update(path.glob("*.tif"))
                    files.update(path.glob("*settings*.txt"))
                else:
                    files.add(path)
        aoi = AOI.from_json(row["aoi"])
        for uri in (
            aoi.deepwater_reference_uri,
            aoi.attenuation_regions_uri,
            *(r.uri for r in (aoi.fine_bathymetry, aoi.coarse_bathymetry) if r),
        ):
            if uri:
                files.add(Path(uri))
    for evidence in spec.get("evidence", []):
        evidence["csv"] = str(_path(source.parent, evidence["csv"]))
        files.add(Path(evidence["csv"]))
    # Including source code prevents applying a lock to a different algorithm.
    files.update(Path(__file__).parent.rglob("*.py"))
    sealed = {
        **spec,
        "frozen": True,
        "checksums": {str(p.resolve()): file_digest(p) for p in sorted(files)},
    }
    write_json_document(target, sealed)
    return sealed


def verify_lock(path: Path) -> dict[str, Any]:
    spec = json.loads(path.read_text())
    if not spec.get("frozen") or not spec.get("checksums"):
        raise ValueError("Run requires a frozen benchmark lock.")
    for uri, expected in spec["checksums"].items():
        if file_digest(uri) != expected:
            raise ValueError(f"Input changed since registration: {uri}")
    return dict(spec)


def _sample(href: str, lon: float, lat: float) -> float:
    import rasterio
    from rasterio.warp import transform

    with rasterio.open(href) as source:
        x, y = transform("EPSG:4326", source.crs, [lon], [lat])
        value = next(source.sample([(x[0], y[0])], masked=True))[0]
        return float(value) if not np.ma.is_masked(value) else float("nan")


def _prediction(run: dict[str, Any], row: dict[str, str]) -> float:
    product = row["product"]
    if product in {"attenuation", "detectability"}:
        payload = json.loads(Path(run["products"]["attenuation"]).read_text())
        region = payload["attenuation"]["regions"][row["region"]]
        band = region["bands"][str(float(row["band_nm"]))]
        k = band["k_per_m"]
        if k is None:
            return float("nan")
        if product == "attenuation":
            return float(k)
        # Compare observed substrate contrast against the declared exponential
        # contrast model. Never validate a z_max against bathymetry alone.
        from oceanstream.coastal.config import DetectabilityConfig
        from oceanstream.coastal.detectability import band_detectability

        config = DetectabilityConfig(**region["detectability_config"])
        detection = band_detectability(
            float(row["band_nm"]),
            float(k),
            epsilon_rhos=region["epsilon_rhos"],
            solar_zenith_deg=region["solar_zenith_deg"],
            config=config,
        )
        return float((config.t_aw * detection.delta_rho_b) * np.exp(-k * float(row["depth_m"])))
    key = f"rho_b_{int(round(float(row['band_nm'])))}" if product == "rho_b" else product
    return _sample(run["products"][key], float(row["longitude"]), float(row["latitude"]))


def _prediction_sigma(run: dict[str, Any], row: dict[str, str]) -> float:
    product = row["product"]
    if product in {"attenuation", "detectability"}:
        payload = json.loads(Path(run["products"]["attenuation"]).read_text())["attenuation"]
        region = payload["regions"][row["region"]]
        band = region["bands"][str(float(row["band_nm"]))]
        sigma = band.get("k_standard_error")
        if sigma is None:
            return float("nan")
        if product == "attenuation":
            return float(sigma)
        # Conditional contrast sensitivity to k; reference uncertainty is
        # reported separately. This is not uncertainty in substrate identity.
        return float(abs(_prediction(run, row) * float(row["depth_m"]) * sigma))
    key = (
        f"rho_b_sigma_{int(round(float(row['band_nm'])))}"
        if product == "rho_b"
        else f"{product}_sigma"
    )
    if key not in run["products"]:
        return float("nan")
    return _sample(run["products"][key], float(row["longitude"]), float(row["latitude"]))


def score_evidence(spec: dict[str, Any], runs: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Sample actual products at frozen held-out observations and publish errors.

    Independence is declared by the evidence provider and audited here for
    source-cell overlap. That declaration still requires scientific review.
    """
    groups: dict[tuple[str, ...], list[dict[str, float]]] = defaultdict(list)
    rejected: list[dict[str, Any]] = []
    rows_with_source: list[tuple[dict[str, Any], dict[str, str]]] = []
    calibration_cells: set[tuple[str, str]] = set()
    for source in spec.get("evidence", []):
        with Path(source["csv"]).open(newline="") as handle:
            rows = list(csv.DictReader(handle))
        if (
            source.get("independent") is not True
            or not source.get("source")
            or not source.get("method")
        ):
            rejected.append(
                {
                    "source": source.get("source"),
                    "reason": "independence_or_measurement_method_undeclared",
                }
            )
            continue
        for row in rows:
            cell = row.get("source_cell_id", "")
            if not cell:
                raise ValueError("Every observation needs a source_cell_id to audit split leakage.")
            if row["partition"] == "calibration":
                calibration_cells.add((source["source"], cell))
            elif row["partition"] != "evaluation":
                raise ValueError("partition must be calibration or evaluation.")
            rows_with_source.append((source, row))
    for source, row in rows_with_source:
        if row["partition"] != "evaluation":
            continue
        reason = None
        if (source["source"], row["source_cell_id"]) in calibration_cells:
            reason = "shared_source_cell_across_splits"
        product, run = row["product"], runs.get(row["scene_id"])
        if product not in PRODUCTS or row["unit"] != UNITS.get(product):
            reason = "wrong_product_or_reference_units"
        if run is None or not run.get("success"):
            reason = "scene_execution_incomplete"
        if source.get("used_for_calibration"):
            if product != "sdb_depth":
                reason = "optical_evidence_used_for_calibration"
            elif run and run.get("success"):
                partition = run["products"].get("sdb_partition")
                if (
                    not partition
                    or _sample(partition, float(row["longitude"]), float(row["latitude"])) != 2
                ):
                    reason = "sdb_reference_not_spatially_held_out"
        if reason:
            rejected.append({"scene": row["scene_id"], "product": product, "reason": reason})
            continue
        assert run is not None
        value = _prediction(run, row)
        reference, uncertainty = float(row["reference"]), float(row["reference_sigma"])
        depth = float(row["depth_m"])
        if not np.isfinite([reference, uncertainty, depth]).all() or uncertainty < 0:
            raise ValueError(
                "Reference, depth and nonnegative reference uncertainty must be finite."
            )
        bounds = np.asarray(spec.get("depth_bins_m", [0, 5, 10, 20, 40]), float)
        if np.any(np.diff(bounds) <= 0):
            raise ValueError("Depth bins must increase.")
        index = int(np.searchsorted(bounds, depth, side="right") - 1)
        label = (
            f"{bounds[index]:g}-{bounds[index + 1]:g}"
            if 0 <= index < len(bounds) - 1
            else "outside_registered_depth_bins"
        )
        key = (run["site"], run["date"], row["region"], product, row.get("band_nm", ""), label)
        groups[key].append(
            {
                "predicted": value,
                "predicted_sigma": _prediction_sigma(run, row),
                "reference": reference,
                "reference_sigma": uncertainty,
                "accepted": bool(run["verdicts"].get(product, {}).get("passed", False)),
            }
        )
    summaries = []
    for group_key, values in sorted(groups.items()):
        available = [v for v in values if np.isfinite(v["predicted"])]
        residual = np.asarray([v["predicted"] - v["reference"] for v in available])
        with_sigma = [
            v for v in available if np.isfinite(v["predicted_sigma"]) and v["predicted_sigma"] >= 0
        ]
        summaries.append(
            dict(zip(("site", "date", "region", "product", "band_nm", "depth_bin_m"), group_key))
            | {
                "n_requested": len(values),
                "n_with_uncertainty": len(with_sigma),
                "mean_prediction_sigma": float(np.mean([v["predicted_sigma"] for v in with_sigma]))
                if with_sigma
                else None,
                "n_scored": len(available),
                "accepted_fraction": sum(v["accepted"] for v in available) / len(values),
                "finite_fraction": len(available) / len(values),
                "bias": float(np.mean(residual)) if residual.size else None,
                "mae": float(np.mean(abs(residual))) if residual.size else None,
                "rmse": float(np.sqrt(np.mean(residual**2))) if residual.size else None,
                "mean_reference_sigma": float(np.mean([v["reference_sigma"] for v in available]))
                if available
                else None,
            }
        )
    return {"groups": summaries, "rejected_observations": rejected}


def release_gate(runs: dict[str, dict[str, Any]], evidence: dict[str, Any]) -> dict[str, Any]:
    reasons = []
    for site in ("sesimbra", "donegal"):
        dates = {r["date"] for r in runs.values() if r["site"] == site}
        if len(dates) < 2:
            reasons.append(f"{site}:two_dates_required")
        for date in sorted(dates):
            for product in PRODUCTS:
                rows = [
                    g
                    for g in evidence["groups"]
                    if g["site"] == site and g["date"] == date and g["product"] == product
                ]
                if not rows or not all(
                    g["n_scored"]
                    and g["n_with_uncertainty"] == g["n_scored"]
                    and g["accepted_fraction"] > 0
                    for g in rows
                ):
                    reasons.append(f"{site}/{date}/{product}:accepted_independent_evidence_missing")
    if not any(r["site"] == "summer_isles" for r in runs.values()):
        reasons.append("summer_isles:stress_test_missing")
    if evidence["rejected_observations"]:
        reasons.append("rejected_evidence_requires_resolution")
    return {
        "evidence_complete": not reasons,
        "release_ready": False,
        "reasons": reasons
        + ["scientific_review_of_errors_uncertainty_and_operating_limits_required"],
    }


def run_benchmark(lock: Path, output: Path) -> dict[str, Any]:
    from oceanstream.coastal.processor import CoastalProcessor
    from oceanstream.coastal.scene import Scene

    spec = verify_lock(lock)
    directory = new_run_directory(output)
    runs = {}
    for row in spec["scenes"]:
        started = time.monotonic()
        print(f"Starting {row['id']}", flush=True)
        scene = None
        record = {
            "site": row["site"],
            "date": row["date"],
            "success": False,
            "status": "execution_failed",
            "verdicts": {},
            "products": {},
        }
        try:
            scene = Scene.from_acolite_dir(
                row["scene"], solar_zenith_fallback_deg=row.get("solar_zenith_deg")
            )
            if scene.acquisition_date.isoformat() != row["date"]:
                raise ValueError("Scene date differs from registered date.")
            observed_version = scene.metadata.get("acolite_version", row.get("acolite_version"))
            expected_version = (
                row.get("acolite_version")
                if spec.get("purpose") == "diagnostic"
                else spec.get("acolite_version")
            )
            record["acolite_version"] = observed_version
            if not observed_version or observed_version != expected_version:
                raise ValueError("Record the pinned ACOLITE version for every benchmark scene.")
            result = CoastalProcessor(
                **processor_options(Path(row["config"]) if row.get("config") else None)
            ).run(
                AOI.from_json(row["aoi"]),
                scene,
                directory / row["id"],
                tide_correction=read_tide(Path(row["tide"]) if row.get("tide") else None),
            )
            record.update(
                success=result.success,
                status=result.status,
                message=result.message,
                verdicts=result.product_verdicts,
                products=result.products,
                output_dir=str(result.output_dir) if result.output_dir else None,
                diagnostics=result.report,
            )
        except (ValueError, OSError, KeyError) as exc:
            record["message"] = str(exc)
        finally:
            # A new scene must not coexist with the preceding full reflectance cubes.
            del scene
            gc.collect()
        record["elapsed_seconds"] = round(time.monotonic() - started, 2)
        runs[row["id"]] = record
        write_json_document(directory / "progress.json", runs)
        print(f"{row['id']}: {record['status']} ({record['elapsed_seconds']} s)", flush=True)
    scores = score_evidence(spec, runs)
    report = {
        "schema_version": "1.0",
        "purpose": spec.get("purpose", "scientific_validation"),
        "output_dir": str(directory),
        "lock_sha256": file_digest(lock),
        "runs": runs,
        "evidence": scores,
        "gate": release_gate(runs, scores),
    }
    if spec.get("purpose") == "diagnostic":
        report["gate"]["evidence_complete"] = False
        report["gate"]["reasons"].append("diagnostic_benchmark_not_scientific_validation")
    write_json_document(directory / "benchmark.json", report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("freeze", "run"):
        command = commands.add_parser(name)
        command.add_argument("input", type=Path)
        command.add_argument("output", type=Path)
    args = parser.parse_args()
    if args.command == "freeze":
        freeze(args.input, args.output)
    else:
        report = run_benchmark(args.input, args.output)
        print(json.dumps(report["gate"], indent=2))
        raise SystemExit(0 if report["gate"]["evidence_complete"] else 2)


if __name__ == "__main__":
    main()
