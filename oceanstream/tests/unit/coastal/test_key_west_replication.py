"""Scientific split, calibration-budget and paired-comparison invariants."""

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pytest

SCRIPTS = Path(__file__).resolve().parents[4] / "scripts/coastal"
sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location(
    "key_west_replication", SCRIPTS / "run_key_west_replication.py"
)
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)
sys.path.remove(str(SCRIPTS))


def test_fresh_regions_require_geographical_separation():
    spec = {
        "development_bounds": [1000, 1000, 3000, 3000],
        "fresh_evaluation_bounds": {"fresh": [3500, 1000, 4500, 3000]},
    }
    assert module.validate_geography(spec)
    spec["fresh_evaluation_bounds"]["fresh"][0] = 3490
    with pytest.raises(ValueError, match="separation"):
        module.validate_geography(spec)


def test_nine_observations_use_distinct_blocks_and_no_prediction_input():
    depth = np.tile(np.linspace(0.1, 4.9, 20), (20, 1))
    blocks = np.repeat(np.arange(10), 40).reshape(20, 20)
    mask = np.ones_like(depth, bool)
    targets = np.linspace(0.5, 4.5, 9)
    first = module.select_nine(depth, mask, blocks, targets, 42)
    second = module.select_nine(depth, mask, blocks, targets, 42)
    np.testing.assert_array_equal(first, second)
    assert len(first) == len(np.unique(blocks.ravel()[first])) == 9
    with pytest.raises(ValueError, match="Insufficient"):
        module.select_nine(depth, mask, np.zeros_like(blocks), targets, 42)


@pytest.mark.parametrize("kind", ["linear", "quadratic", "combined"])
def test_portable_model_recovers_independently_defined_equations(kind):
    rng = np.random.default_rng(51)
    x = rng.uniform(0.5, 2, (25, 2))
    y = 1.1 + 2 * x[:, 0]
    if kind == "quadratic":
        y += 0.7 * x[:, 0] ** 2
    if kind == "combined":
        y -= 0.3 * x[:, 1]
    model = module.fit_model(x, y, kind)
    np.testing.assert_allclose(module.predict(x, model), y, atol=1e-10)


def test_huber_limits_one_bad_calibration_observation():
    x = np.column_stack([np.linspace(0.5, 2, 9), np.ones(9)])
    truth = 2 * x[:, 0] - 0.5
    observed = truth.copy()
    observed[4] += 3
    ordinary = module.predict(x, module.fit_model(x, observed))
    robust = module.predict(x, module.fit_model(x, observed, robust=True))
    assert np.mean(abs(robust - truth)) < np.mean(abs(ordinary - truth))


def test_predictions_outside_depth_domain_are_not_clipped():
    x = np.array([[0.1, 1], [3, 1], [np.nan, 1]])
    prediction = module.predict(x, {"kind": "published", "m1": 5.8, "m0": 5.9})
    assert prediction[0] < 0 and prediction[1] > 5 and np.isnan(prediction[2])


def test_block_bootstrap_pairs_only_identical_finite_pixels():
    depth = np.ones((4, 4))
    first = depth + 0.1
    baseline = depth + 0.5
    first[0, 0] = np.nan
    baseline[1, 1] = np.nan
    blocks = np.repeat(np.arange(4), 4).reshape(4, 4)
    result = module.bootstrap_difference(first, baseline, depth, np.ones((4, 4), bool), blocks, 21)
    assert result["n_pixels"] == 14
    assert result["n_blocks"] == 4
    np.testing.assert_allclose(result["ci95_mae_difference_m"], [-0.4, -0.4])


def test_changed_input_lock_prevents_fitting(tmp_path, monkeypatch):
    source = tmp_path / "input.txt"
    source.write_text("original")
    module.write_json(
        tmp_path / "input-lock.json",
        {"schema_version": "1.0", "files": {str(source): module.file_digest(source)}},
    )
    source.write_text("changed")
    monkeypatch.setattr(module, "REGISTRATION", tmp_path)
    monkeypatch.setattr(module, "OUTPUT", tmp_path / "outputs")
    with pytest.raises(ValueError, match="checksum changed"):
        module.fit()
    assert not (tmp_path / "outputs").exists()


def test_changed_sealed_models_prevent_evaluation(tmp_path, monkeypatch):
    source = tmp_path / "input.txt"
    source.write_text("input")
    module.write_json(
        tmp_path / "input-lock.json",
        {"schema_version": "1.0", "files": {str(source): module.file_digest(source)}},
    )
    output = tmp_path / "outputs"
    module.write_json(output / "fits.json", {"original": True})
    module.write_json(
        output / "fit-manifest.json",
        {"complete": True, "fits_sha256": module.file_digest(output / "fits.json")},
    )
    module.write_json(output / "fits.json", {"original": False})
    monkeypatch.setattr(module, "REGISTRATION", tmp_path)
    monkeypatch.setattr(module, "OUTPUT", output)
    monkeypatch.setattr(module, "load_spec", lambda: {})
    with pytest.raises(ValueError, match="Sealed fits changed"):
        module.evaluate()
    assert not (output / "evaluation").exists()
