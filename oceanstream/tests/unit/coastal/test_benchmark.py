"""The evidence gate must fail closed; predictions come from actual products."""

from __future__ import annotations

import csv
import json

import numpy as np
import pytest

from oceanstream.coastal.benchmark import (
    PRODUCTS,
    freeze,
    release_gate,
    score_evidence,
    verify_lock,
)
from oceanstream.coastal.io.rasters import RasterGrid, write_cog
from oceanstream.coastal.provenance import file_digest


def write_observations(path, rows):
    fields = [
        "scene_id",
        "region",
        "product",
        "band_nm",
        "longitude",
        "latitude",
        "reference",
        "reference_sigma",
        "depth_m",
        "unit",
        "partition",
        "source_cell_id",
    ]
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


@pytest.fixture
def observations(tmp_path):
    grid = RasterGrid(4, 4, (0.001, 0.0, -9.0, 0.0, -0.001, 38.5), "EPSG:4326")
    depth, sigma = tmp_path / "sdb.tif", tmp_path / "sigma.tif"
    write_cog(depth, np.full(grid.shape, 12.0, np.float32), grid)
    write_cog(sigma, np.full(grid.shape, 2.0, np.float32), grid)
    rows = [
        {
            "scene_id": "s1",
            "region": "reef",
            "product": "sdb_depth",
            "band_nm": "",
            "longitude": -8.9995,
            "latitude": 38.4995,
            "reference": 10.0,
            "reference_sigma": 0.5,
            "depth_m": 10.0,
            "unit": "m",
            "partition": "evaluation",
            "source_cell_id": "native-cell-1",
        }
    ]
    csv_path = tmp_path / "reference.csv"
    write_observations(csv_path, rows)
    source = {
        "csv": str(csv_path),
        "independent": True,
        "source": "withheld soundings",
        "method": "independently surveyed depth below instantaneous surface",
    }
    spec = {"evidence": [source], "depth_bins_m": [0, 5, 15, 25]}
    runs = {
        "s1": {
            "site": "sesimbra",
            "date": "2026-06-27",
            "success": True,
            "verdicts": {"sdb_depth": {"passed": True}},
            "products": {"sdb_depth": str(depth), "sdb_depth_sigma": str(sigma)},
        }
    }
    return spec, runs, rows, csv_path


def test_errors_are_computed_from_unblended_sdb_product(observations):
    spec, runs, _, _ = observations
    scored = score_evidence(spec, runs)
    group = scored["groups"][0]
    assert group["mae"] == group["rmse"] == group["bias"] == 2.0
    assert group["mean_prediction_sigma"] == 2.0
    assert group["mean_reference_sigma"] == 0.5
    assert group["n_with_uncertainty"] == 1
    assert not release_gate(runs, scored)["evidence_complete"]


def test_source_cell_shared_across_partitions_is_rejected(observations):
    spec, runs, rows, path = observations
    write_observations(path, rows + [{**rows[0], "partition": "calibration"}])
    scored = score_evidence(spec, runs)
    assert not scored["groups"]
    assert scored["rejected_observations"][0]["reason"] == "shared_source_cell_across_splits"


def test_missing_independence_is_not_evidence(observations):
    spec, runs, _, _ = observations
    spec["evidence"][0]["independent"] = False
    assert not score_evidence(spec, runs)["groups"]


def test_reference_units_are_product_specific(observations):
    spec, runs, rows, path = observations
    write_observations(path, [{**rows[0], "unit": "PAR"}])
    assert (
        score_evidence(spec, runs)["rejected_observations"][0]["reason"]
        == "wrong_product_or_reference_units"
    )


def test_no_coverage_stays_unscored(observations):
    spec, runs, rows, path = observations
    write_observations(path, [{**rows[0], "longitude": 0.0}])
    group = score_evidence(spec, runs)["groups"][0]
    assert group["n_scored"] == 0 and group["mae"] is None


def test_all_products_and_two_dates_at_both_sites_are_required():
    runs = {
        f"{site}-{date}": {"site": site, "date": date}
        for site in ("sesimbra", "donegal", "summer_isles")
        for date in ("2026-06-01", "2026-07-01")
    }
    groups = [
        {
            "site": row["site"],
            "date": row["date"],
            "product": product,
            "n_scored": 10,
            "n_with_uncertainty": 10,
            "accepted_fraction": 1.0,
        }
        for row in runs.values()
        for product in PRODUCTS
    ]
    evidence = {"groups": groups, "rejected_observations": []}
    assert release_gate(runs, evidence)["evidence_complete"]
    assert not release_gate(runs, evidence)["release_ready"]  # scientific review is separate
    groups.pop(0)
    assert not release_gate(runs, evidence)["evidence_complete"]


def test_unknown_uncertainty_does_not_satisfy_gate(observations):
    spec, runs, _, _ = observations
    runs["s1"]["products"].pop("sdb_depth_sigma")
    scored = score_evidence(spec, runs)
    assert scored["groups"][0]["n_with_uncertainty"] == 0


def test_freeze_is_immutable_and_verifies_data_hashes(tmp_path):
    data = tmp_path / "depth.tif"
    data.write_bytes(b"reference")
    aoi = tmp_path / "aoi.json"
    aoi.write_text(
        json.dumps(
            {
                "name": "test",
                "processing_bbox": [-9, 38, -8, 39],
                "fine_bathymetry": {"uri": str(data)},
            }
        )
    )
    scene = tmp_path / "scene"
    scene.mkdir()
    (scene / "rhos.tif").write_bytes(b"scene")
    source, lock = tmp_path / "source.json", tmp_path / "frozen.json"
    source.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "scenes": [
                    {
                        "id": "s1",
                        "site": "sesimbra",
                        "date": "2026-06-27",
                        "aoi": str(aoi),
                        "scene": str(scene),
                    }
                ],
            }
        )
    )
    freeze(source, lock)
    assert verify_lock(lock)["checksums"][str(data)] == file_digest(data)
    with pytest.raises(FileExistsError):
        freeze(source, lock)
    data.write_bytes(b"changed reference")
    with pytest.raises(ValueError, match="changed since"):
        verify_lock(lock)


def test_comparison_harness_empty_table_and_failed_rows():
    from oceanstream.coastal.compare import SceneOptics, comparison_table

    assert comparison_table([]) == "no scenes compared"
    row = SceneOptics(
        "failed",
        "input",
        "depth",
        {},
        20.0,
        None,
        "unknown",
        0,
        0,
        verdict={"passed": False, "summary": "no valid reference"},
    )
    table = comparison_table([row])
    assert "FAIL" in table and "no valid reference" in table


def test_shared_calibration_bathymetry_requires_the_processors_heldout_mask(observations):
    spec, runs, _, _ = observations
    spec["evidence"][0]["used_for_calibration"] = True
    scored = score_evidence(spec, runs)
    assert not scored["groups"]
    assert scored["rejected_observations"][0]["reason"] == "sdb_reference_not_spatially_held_out"


def test_frozen_runner_records_both_completed_and_failed_scenes(tmp_path):
    from oceanstream.coastal.benchmark import run_benchmark
    from oceanstream.coastal.quickstart import run

    example = run(tmp_path / "example")
    assert example.success
    inputs = example.output_dir.parents[2] / "inputs"
    row = {
        "id": "synthetic",
        "site": "sesimbra",
        "date": "2026-06-27",
        "aoi": str(inputs / "aoi.json"),
        "scene": str(inputs / "acolite"),
        "acolite_version": "synthetic-test-only",
    }
    source, lock = tmp_path / "benchmark.json", tmp_path / "lock.json"
    source.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "acolite_version": "synthetic-test-only",
                "scenes": [row, {**row, "id": "wrong-date", "date": "2026-06-28"}],
            }
        )
    )
    freeze(source, lock)
    report = run_benchmark(lock, tmp_path / "results")
    assert report["runs"]["synthetic"]["success"]
    assert not report["runs"]["wrong-date"]["success"]
    assert "date" in report["runs"]["wrong-date"]["message"]
    assert not report["gate"]["release_ready"]
    assert list((tmp_path / "results").rglob("benchmark.json"))

    # Historical caches can have distinct build strings in an explicitly
    # diagnostic registration, but cannot become scientific evidence.
    spec = json.loads(source.read_text())
    spec["purpose"] = "diagnostic"
    spec["acolite_version"] = "different-global-version"
    spec["scenes"] = [row]
    source.write_text(json.dumps(spec))
    diagnostic_lock = tmp_path / "diagnostic.lock.json"
    freeze(source, diagnostic_lock)
    diagnostic = run_benchmark(diagnostic_lock, tmp_path / "diagnostic")
    assert diagnostic["runs"]["synthetic"]["success"]
    assert diagnostic["runs"]["synthetic"]["diagnostics"]["depth_reference"]
    assert not diagnostic["gate"]["evidence_complete"]
    assert "diagnostic_benchmark_not_scientific_validation" in diagnostic["gate"]["reasons"]

    spec["scenes"][0]["acolite_version"] = "incorrect-version"
    # Synthetic fixture has no metadata: supply a real observed value so a
    # caller's registration cannot override the scene metadata.
    import rasterio

    for raster in (inputs / "acolite").glob("*.tif"):
        with rasterio.open(raster, "r+", IGNORE_COG_LAYOUT_BREAK="YES") as dataset:
            dataset.update_tags(**{"NC_GLOBAL#acolite_version": "synthetic-test-only"})
    source.write_text(json.dumps(spec))
    mismatch_lock = tmp_path / "mismatch.lock.json"
    freeze(source, mismatch_lock)
    mismatch = run_benchmark(mismatch_lock, tmp_path / "mismatch")
    assert not mismatch["runs"]["synthetic"]["success"]
    assert "ACOLITE version" in mismatch["runs"]["synthetic"]["message"]
