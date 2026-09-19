"""Safety checks for the fixed-depth optical reference experiment."""

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pytest

from oceanstream.coastal.optics.qaa import SceneIOPs

ROOT = Path(__file__).resolve().parents[4]
SCRIPTS = ROOT / "scripts" / "coastal"
sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location(
    "key_west_optical_experiment", SCRIPTS / "run_key_west_optics.py"
)
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)
sys.path.remove(str(SCRIPTS))


def test_fixed_lidar_depth_preserves_holes_and_exposure():
    source = np.array([[-2, np.nan], [0.5, -0.1]], dtype="float32")
    result = module.fixed_depth(source, 0.28)
    np.testing.assert_allclose(result, [[2.28, np.nan], [np.nan, 0.38]], atol=1e-6)
    with pytest.raises(ValueError, match="finite"):
        module.fixed_depth(source, np.nan)


def test_sensitivity_compares_identical_finite_pixels():
    result = module.comparison(
        np.array([1, 10, np.nan, 2.0]),
        np.array([2, np.nan, 100, 9.0]),
        np.array([True, True, True, False]),
    )
    assert result["common_pixels"] == 1
    assert result["absolute_difference"]["median"] == 1


def test_retained_nonsense_is_not_physical_coverage():
    rho = np.array([[0.2, -0.02], [1.2, np.nan]])
    depth = np.full((2, 2), 2.5)
    terrain = {key: np.zeros((2, 2), bool) for key in ("flat", "rugose")}
    result = module.bottom_summary(rho, depth, np.ones((2, 2), bool), terrain)
    assert result["physical_0_1_fraction"] == 0.25
    assert result["library_retained"]["fraction"] == 0.75
    assert result["negative_retained_pixels"] == 1
    assert result["above_one_retained_pixels"] == 1


def test_forward_layout_handles_nonsquare_multiband_grid():
    wl = np.array([443, 492, 560, 665], dtype="float32")
    iops = SceneIOPs(
        wl,
        np.array([0.1, 0.1, 0.12, 0.45]),
        np.full(4, 0.01),
        np.full(4, 0.15),
        0.0,
        0.008,
        1.0,
        100,
        560,
        [],
        [],
    )
    rho = np.full((4, 3, 7), 0.2)
    rho[:, 0, 0] = np.nan
    depth = np.linspace(1, 4, 21).reshape(3, 7)
    rrs = module.reconstruct_rrs(rho, depth, iops, 35)
    result = module.lee.invert_scene(rrs, wl, depth, iops, np.ones((3, 7), bool), 35)
    np.testing.assert_allclose(result, rho, rtol=1e-5, equal_nan=True)


def test_physical_flags_survive_diagnostic_filter():
    class Iops:
        qa_flags = [
            "a_cdm_443_not_computable",
            "bbp_555_implausible",
            "a_below_pure_water_floor(665nm)",
        ]

    assert module.qaa_flags(Iops()) == ["bbp_555_implausible", "a_below_pure_water_floor(665nm)"]


def test_changed_lock_prevents_any_optical_outputs(tmp_path):
    import json

    source = tmp_path / "source"
    source.write_text("changed")
    lock = tmp_path / "lock.json"
    lock.write_text(json.dumps({"schema_version": "1.0", "files": {str(source): "wrong"}}))
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"input_lock": "lock.json", "output_dir": "output"}))
    with pytest.raises(ValueError, match="checksum changed"):
        module.run(config)
    assert not (tmp_path / "output").exists()
