"""Golden regression: the ported attenuation fitter must reproduce the prototype.

What this asserts, and what it does not
---------------------------------------
This test asserts **faithful reproduction of the prototype**, not correctness.
The recorded k values are a known-biased calibration: the substrate tracker is
a free parameter (see ``k_by_quantile``), the deep-water reference was taken
from a separate region of the scene, and the whole calibration is scheduled to
be re-derived in Phase 5. Every number below is a refactor safety net over a
value we already believe to be wrong in the third decimal.

**This test must be re-baselined after Phase 5.** If a future change to the
fitter moves these numbers, that is not automatically a failure — it is a
prompt to check whether the change was intended and, if so, to update the
constants together with a note saying why.

The fixture is the real Sesimbra 2026-06-27 Sentinel-2 scene (ACOLITE rhos,
EMODnet HR lidar depth) clipped to the habitat AOI, extracted with the
prototype's own ``load_scene`` so the inputs are provably identical. Stored as
float32, which is why the tolerances below are 1e-6 rather than exact.

The deep-water references are hard-coded because the prototype takes them from
a deepwater AOI *outside* the habitat clip, on the full 4441 x 3605 scene. That
raster is far too large to vendor, and carrying it would test the loader rather
than the fitter.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from oceanstream.coastal.config import AttenuationConfig
from oceanstream.coastal.optics import attenuation

DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "coastal"
SCENE_NPZ = DATA_DIR / "scene_sesimbra_2026_06_27.npz"

#: Per-band deep-water reference recorded by the prototype run.
DEEP_WATER_REFERENCE: dict[float, float] = {
    444.0: 0.026041515171527863,
    489.0: 0.024063775315880775,
    561.0: 0.015003915876150131,
    667.0: 0.007837261073291302,
}

#: Prototype outputs from outputs/diagnostics/reef_calibration.json,
#: quantile 0.90. Known-biased; see the module docstring.
GOLDEN: dict[float, dict[str, float]] = {
    444.0: {
        "k_per_m": 0.1084753965076647,
        "intercept": -2.603548181684393,
        "r_squared": 0.9236638104815618,
    },
    489.0: {
        "k_per_m": 0.07775244503690487,
        "intercept": -2.8943447872212054,
        "r_squared": 0.4868035839855309,
    },
    561.0: {
        "k_per_m": 0.08018135125176955,
        "intercept": -2.6913218187658408,
        "r_squared": 0.557102424360997,
    },
    667.0: {
        "k_per_m": 0.07720399655793848,
        "intercept": -2.692755739312423,
        "r_squared": 0.5804177952643157,
    },
}

#: Prototype sensitivity sweep for the blue band, rounded to 5 dp on write.
GOLDEN_K_BY_QUANTILE_444: dict[float, float] = {
    0.75: 0.09222,
    0.80: 0.10194,
    0.85: 0.10377,
    0.90: 0.10848,
    0.95: 0.11192,
}

#: Prototype Lyzenga ratios, rounded to 4 dp on write.
GOLDEN_LYZENGA: dict[str, float] = {
    "444/489": 1.3951,
    "444/561": 1.3529,
    "444/667": 1.4050,
    "489/561": 0.9697,
    "489/667": 1.0071,
    "561/667": 1.0386,
}

GOLDEN_N_BINS = 19
GOLDEN_N_PIXELS = 4440
GOLDEN_WATER_PIXELS = 6167
GOLDEN_SHAPE = (222, 175)

WAVELENGTHS = (444.0, 489.0, 561.0, 667.0)


@pytest.fixture(scope="module")
def scene() -> dict[str, np.ndarray]:
    # Deliberately a failure, not a skip. The fixture is vendored in the repo,
    # so a missing file is a repo defect — and a skip here would silently
    # delete the entire regression, which is exactly how this path bug was
    # nearly missed the first time.
    if not SCENE_NPZ.exists():
        raise FileNotFoundError(
            f"Golden scene fixture missing: {SCENE_NPZ}. It is vendored in the "
            "repository; restore it rather than skipping, or the attenuation "
            "regression provides no cover at all."
        )
    with np.load(SCENE_NPZ) as handle:
        return {key: handle[key] for key in handle.files}


class TestFixtureIntegrity:
    """If the fixture drifts, every assertion below becomes meaningless."""

    def test_shape_and_water_count_match_the_recorded_run(
        self, scene: dict[str, np.ndarray]
    ) -> None:
        assert scene["depth_m"].shape == GOLDEN_SHAPE
        assert int(scene["water_mask"].sum()) == GOLDEN_WATER_PIXELS

    def test_all_four_calibration_bands_are_present(self, scene: dict[str, np.ndarray]) -> None:
        for wl in WAVELENGTHS:
            assert f"rhos_{int(wl)}" in scene


class TestGoldenAttenuation:
    @pytest.mark.parametrize("wavelength_nm", WAVELENGTHS)
    def test_reproduces_the_prototype_fit(
        self, scene: dict[str, np.ndarray], wavelength_nm: float
    ) -> None:
        expected = GOLDEN[wavelength_nm]
        k, intercept, r_squared, n_bins, n_pixels = attenuation.fit_band_attenuation(
            scene[f"rhos_{int(wavelength_nm)}"],
            scene["depth_m"],
            scene["water_mask"],
            DEEP_WATER_REFERENCE[wavelength_nm],
            0.90,
        )
        assert k == pytest.approx(expected["k_per_m"], abs=1e-6)
        assert intercept == pytest.approx(expected["intercept"], abs=1e-6)
        assert r_squared == pytest.approx(expected["r_squared"], abs=1e-6)
        assert n_bins == GOLDEN_N_BINS
        assert n_pixels == GOLDEN_N_PIXELS

    def test_blue_band_hits_the_headline_value(self, scene: dict[str, np.ndarray]) -> None:
        # The value quoted throughout the KelpObserve write-ups.
        k, *_ = attenuation.fit_band_attenuation(
            scene["rhos_444"],
            scene["depth_m"],
            scene["water_mask"],
            DEEP_WATER_REFERENCE[444.0],
            0.90,
        )
        assert k == pytest.approx(0.10848, abs=1e-4)

    def test_sensitivity_sweep_reproduces(self, scene: dict[str, np.ndarray]) -> None:
        # The sweep is the honest part of the calibration: it shows the answer
        # moves by ~20% across plausible substrate-tracker settings. A
        # regression that silently narrowed it would be hiding that.
        for quantile, expected in GOLDEN_K_BY_QUANTILE_444.items():
            k, *_ = attenuation.fit_band_attenuation(
                scene["rhos_444"],
                scene["depth_m"],
                scene["water_mask"],
                DEEP_WATER_REFERENCE[444.0],
                quantile,
            )
            assert k == pytest.approx(expected, abs=1e-5)

    def test_defaults_are_the_prototype_settings(self) -> None:
        # The regression is only meaningful because AttenuationConfig ships
        # the prototype's values. If a default drifts, the fits above stop
        # describing the recorded run.
        cfg = AttenuationConfig()
        assert cfg.depth_bin_m == 1.0
        assert cfg.min_pixels_per_bin == 20
        assert cfg.depth_min_m == 1.0
        assert cfg.depth_max_m == 20.0
        assert cfg.quantile == 0.90
        assert cfg.sensitivity_quantiles == (0.75, 0.80, 0.85, 0.90, 0.95)


class TestGoldenLyzengaRatios:
    def test_ratios_reproduce(self, scene: dict[str, np.ndarray]) -> None:
        calibrations = {}
        for wl in WAVELENGTHS:
            k, intercept, r_squared, n_bins, n_pixels = attenuation.fit_band_attenuation(
                scene[f"rhos_{int(wl)}"],
                scene["depth_m"],
                scene["water_mask"],
                DEEP_WATER_REFERENCE[wl],
                0.90,
            )
            calibrations[wl] = attenuation.BandCalibration(
                wavelength_nm=wl,
                k_per_m=k,
                intercept=intercept,
                r_squared=r_squared,
                n_bins=n_bins,
                n_pixels=n_pixels,
                depth_range_m=(1.0, 20.0),
                deep_water_reference=DEEP_WATER_REFERENCE[wl],
                frac_nonpositive_residual=0.0,
                k_by_quantile={},
            )
        ratios = attenuation.lyzenga_ratios(calibrations)
        for pair, expected in GOLDEN_LYZENGA.items():
            assert ratios[pair] == pytest.approx(expected, abs=1e-4)
