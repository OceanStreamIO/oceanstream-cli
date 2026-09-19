"""Reference experiment must preserve withheld support and incomplete-run state."""

from __future__ import annotations

import datetime as dt
import importlib.util
import json
from pathlib import Path

import numpy as np
import pytest
from scipy import ndimage

from oceanstream.coastal.io.rasters import RasterGrid, write_cog
from oceanstream.coastal.scene import Scene
from oceanstream.coastal.sensors import get_sensor

RUNNER = Path(__file__).resolve().parents[4] / "scripts/coastal/run_key_west_reference.py"
SPEC = importlib.util.spec_from_file_location("key_west_reference", RUNNER)
assert SPEC and SPEC.loader
experiment = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(experiment)


@pytest.fixture
def synthetic_case():
    shape = (60, 60)
    rows, cols = np.indices(shape)
    depth = (0.5 + 4.0 * (rows + cols) / (2 * (shape[0] - 1))).astype(np.float32)
    red = np.full(shape, 0.003, dtype=np.float32)
    blue = np.power(1000.0 * red, (depth + 5.9) / 5.8) / 1000.0
    green = np.full(shape, 0.004, dtype=np.float32)
    rrs = np.stack((blue, green, red))
    grid = RasterGrid(60, 60, (10.0, 0.0, 423000.0, 0.0, -10.0, 2724000.0), "EPSG:32617")
    scene = Scene(
        sensor=get_sensor("sentinel2"),
        acquisition_date=dt.date(2017, 2, 8),
        wavelengths_nm=np.array([492.0, 560.0, 665.0]),
        rrs_above=rrs,
        rhos=rrs * np.float32(np.pi),
        grid=grid,
        solar_zenith_deg=40.0,
    )
    source_ids = np.arange(1, depth.size + 1).reshape(shape)
    config = {
        "spatial_split": {"block_px": 10, "buffer_px": 1},
        "min_fit_pixels": 20,
        "min_calibration_depth_span_m": 1.0,
    }
    return scene, depth, source_ids, config


def test_spatial_split_is_buffered_and_cannot_share_reference_cells():
    shape = (30, 30)
    source_ids = np.arange(1, 901).reshape(shape)
    source_ids[5, 15] = source_ids[5, 5]
    train, test = experiment.spatial_split(shape, source_ids, block_px=10)
    assert train.any() and test.any()
    assert not (train & test).any()
    assert not (ndimage.binary_dilation(train, structure=np.ones((5, 5))) & test).any()
    assert not np.intersect1d(source_ids[train], source_ids[test]).size
    assert train[5, 5] and not test[5, 15]


def test_missing_reference_remains_missing_after_grid_alignment(tmp_path):
    source_grid = RasterGrid(2, 2, (20.0, 0.0, 0.0, 0.0, -20.0, 40.0), "EPSG:32617")
    destination_grid = RasterGrid(5, 4, (10.0, 0.0, 0.0, 0.0, -10.0, 40.0), "EPSG:32617")
    path = tmp_path / "elevation.tif"
    write_cog(path, np.array([[-2.0, np.nan], [-3.0, -4.0]], dtype=np.float32), source_grid)
    aligned, identifiers = experiment.align_reference(path, destination_grid)
    assert np.isnan(aligned[:2, 2:]).all()
    assert np.isnan(aligned[:, 4]).all()
    assert (identifiers[~np.isfinite(aligned)] == 0).all()
    np.testing.assert_array_equal(aligned[:2, :2], -2.0)
    assert np.unique(identifiers[:2, :2]).size == 1


def test_median_never_invents_data_inside_or_around_holes():
    array = np.ones((9, 9), dtype=np.float32)
    array[4, 4] = np.nan
    result = experiment.median_supported(array)
    assert np.isnan(result[3:6, 3:6]).all()
    assert np.isnan(result[0]).all()
    assert result[2, 2] == 1


def test_evaluation_values_cannot_affect_calibration_or_adaptive_scaling(synthetic_case):
    scene, depth, source_ids, config = synthetic_case
    initial = experiment.evaluate_modes(scene, depth, source_ids, config)
    evaluation = initial["evaluation"]
    changed_depth = depth.copy()
    changed_depth[evaluation] = 4.9 - 0.5 * changed_depth[evaluation]
    scene.rrs_above[:, evaluation] *= 1.2
    scene.rhos[:, evaluation] *= 1.2
    changed = experiment.evaluate_modes(scene, changed_depth, source_ids, config)
    for name in ("library_refit_blue_red", "library_default_blue_green"):
        for parameter in ("m0", "m1", "n"):
            assert initial["modes"][name]["coefficients"][parameter] == pytest.approx(
                changed["modes"][name]["coefficients"][parameter]
            )
    assert (
        initial["modes"]["training_mean_baseline"]["constant_depth_m"]
        == changed["modes"]["training_mean_baseline"]["constant_depth_m"]
    )
    assert (
        initial["modes"]["published_blue_red"]["scores"]
        != changed["modes"]["published_blue_red"]["scores"]
    )


def test_stock_ratio_guard_rejection_is_reported_without_hiding_literal_predictions(synthetic_case):
    scene, depth, source_ids, config = synthetic_case
    red = np.full(scene.shape, 0.0008, dtype=np.float32)
    blue = np.power(1000.0 * red, (depth + 5.9) / 5.8) / 1000.0
    scene.rrs_above[0], scene.rrs_above[2] = blue, red
    scene.rhos = scene.rrs_above * np.float32(np.pi)
    result = experiment.evaluate_modes(scene, depth, source_ids, config)
    formula = result["formula_comparison"]
    assert formula["literal_valid_evaluation_pixels"] > 0
    assert formula["library_default_guard_valid_evaluation_pixels"] == 0
    assert (
        formula["literal_pixels_rejected_by_default_guard"]
        == formula["literal_valid_evaluation_pixels"]
    )
    assert result["modes"]["library_guarded_refit_blue_red"]["fit_status"] == "rejected"
    assert result["modes"]["published_blue_red"]["scores"]["all"]["n_scored"] > 0
    assert formula["max_absolute_ratio_difference"] < 1e-6


def test_error_scores_include_unphysical_predictions_without_clipping():
    predictions = np.array([[12.0, -2.0, np.nan]])
    references = np.array([[2.0, 2.0, 2.0]])
    metrics = experiment.error_metrics(predictions, references, np.ones((1, 3), dtype=bool))
    assert metrics["n_requested"] == 3 and metrics["n_scored"] == 2
    assert metrics["mae_m"] == metrics["median_ae_m"] == 7.0
    assert metrics["bias_m"] == 3.0
    assert metrics["valid_fraction"] == pytest.approx(2 / 3)


def test_span_diagnostic_exposes_constant_map_on_identical_scored_support():
    predictions = np.array([[2.0, 2.0, 2.0, np.nan]])
    references = np.array([[1.0, 2.0, 3.0, 100.0]])
    metrics = experiment.error_metrics(predictions, references, np.ones((1, 4), dtype=bool))
    span = metrics["span_diagnostic"]
    assert span["prediction_p5_p95_span_m"] == 0
    assert span["prediction_to_reference_span_ratio"] == 0
    assert span["reference_p95_m"] < 3
    assert metrics["n_scored"] == 3


def test_average_reference_aggregation_rejects_partly_missing_footprints(tmp_path):
    source_grid = RasterGrid(20, 20, (1.0, 0.0, 0.0, 0.0, -1.0, 20.0), "EPSG:32617")
    destination_grid = RasterGrid(2, 2, (10.0, 0.0, 0.0, 0.0, -10.0, 20.0), "EPSG:32617")
    elevation = -np.arange(400, dtype=np.float32).reshape(20, 20)
    elevation[1, 1] = np.nan
    path = tmp_path / "elevation.tif"
    write_cog(path, elevation, source_grid)
    aligned, identifiers, coverage = experiment.align_reference_coverage(
        path, destination_grid, "average"
    )
    assert np.isnan(aligned[0, 0]) and identifiers[0, 0] == 0
    assert coverage[0, 0] == pytest.approx(0.99)
    assert aligned[1, 1] == pytest.approx(elevation[10:, 10:].mean())
    assert coverage[1, 1] == 1


def test_reporting_region_excludes_other_reference_observations(synthetic_case):
    scene, depth, source_ids, config = synthetic_case
    config.update(
        {
            "reporting_crs": scene.grid.crs,
            "reporting_bounds": [423000, 2723700, 423300, 2724000],
        }
    )
    result = experiment.evaluate_modes(scene, depth, source_ids, config)
    assert result["reporting_mask"].sum() == 900
    assert not result["reference_domain"][30:, :].any()
    assert not result["evaluation"][:, 30:].any()
    assert np.isnan(result["predictions"]["published_blue_red"][30:, :]).all()


@pytest.fixture
def run_config(tmp_path, synthetic_case):
    scene, depth, _, settings = synthetic_case
    acolite = tmp_path / "acolite"
    acolite.mkdir()
    for wavelength, array in zip(scene.wavelengths_nm, scene.rhos):
        write_cog(
            acolite / f"S2A_MSI_2017_02_08_15_55_22_T17RMH_L2R_rhos_{wavelength:g}.tif",
            array,
            scene.grid,
        )
    reference = tmp_path / "elevation.tif"
    write_cog(reference, -depth, scene.grid)
    config = {
        **settings,
        "acolite_dir": "acolite",
        "reference_elevation_raster": "elevation.tif",
        "output_dir": "run",
        "solar_zenith_fallback_deg": 40.0,
        "vertical_datum": {"source": "NAVD88", "target": "MLLW"},
    }
    path = tmp_path / "experiment.json"
    path.write_text(json.dumps(config))
    return path


def test_failed_write_has_no_completed_manifest(run_config, monkeypatch):
    def fail(*args, **kwargs):
        raise OSError("deliberate raster write failure")

    monkeypatch.setattr(experiment, "write_cog", fail)
    with pytest.raises(OSError, match="raster write failure"):
        experiment.run(run_config)
    assert not (run_config.parent / "run/manifest.json").exists()
    assert not (run_config.parent / "run/results.json").exists()
    with pytest.raises(FileExistsError, match="not empty"):
        experiment.run(run_config)


def test_completed_run_preserves_diagnostic_status_samples_and_checksums(run_config):
    report = experiment.run(run_config)
    output = run_config.parent / "run"
    manifest = json.loads((output / "manifest.json").read_text())
    assert manifest["complete"] and manifest["status"] == "diagnostic_only"
    assert report["execution_success"] and not report["exact_paper_replication"]
    assert "reference_to_target_datum_correction_missing" in report["limitations"]
    assert report["vertical_datum"]["evaluated_depth_datum"] == "NAVD88"
    with np.load(output / "evaluation_samples.npz") as samples:
        assert samples["reference_depth_m"].size == report["split"]["n_evaluation"]
        assert samples["source_cell_id"].size == report["split"]["n_evaluation"]
        assert "library_default_blue_green" in samples.files
    for entry in manifest["files"].values():
        assert experiment.file_digest(output / entry["path"]) == entry["sha256"]
    with pytest.raises(FileExistsError, match="not empty"):
        experiment.run(run_config)


def test_tampered_locked_input_aborts_before_output_creation(run_config):
    config = json.loads(run_config.read_text())
    config["input_lock"] = "input-lock.json"
    run_config.write_text(json.dumps(config))
    reference = run_config.parent / "elevation.tif"
    lock = {
        "schema_version": "1.0",
        "frozen_at": "2026-09-15T00:00:00Z",
        "files": {
            str(run_config): experiment.file_digest(run_config),
            str(reference): experiment.file_digest(reference),
        },
    }
    (run_config.parent / "input-lock.json").write_text(json.dumps(lock))
    with reference.open("ab") as stream:
        stream.write(b"changed after registration")
    with pytest.raises(ValueError, match="Locked input checksum changed"):
        experiment.run(run_config)
    assert not (run_config.parent / "run").exists()
