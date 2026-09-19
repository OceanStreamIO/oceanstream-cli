"""Regression cases from the beta review: acceptance and missing data."""

from __future__ import annotations

import json
from dataclasses import replace

import numpy as np
from typer.testing import CliRunner

from oceanstream.cli import app
from oceanstream.coastal.bathymetry.stumpf import StumpfFit, blend_stumpf
from oceanstream.coastal.detectability import scene_detectability
from oceanstream.coastal.optics.attenuation import BandCalibration
from oceanstream.coastal.qc.floors import scene_floor_verdict


def calibrations():
    return {
        wl: BandCalibration(
            wl, k, -2.0, 0.9, 19, 1000, (1.0, 20.0), 0.01, 0.2, {"0.75": k, "0.95": k}
        )
        for wl, k in [(444.0, 0.15), (489.0, 0.2), (561.0, 0.3), (667.0, 1.1)]
    }


def test_untrustworthy_fits_cannot_pass_or_select_a_detection_band():
    bands = {
        wl: replace(c, trust_failures=("scene_depth_gradient",)) for wl, c in calibrations().items()
    }
    assert not scene_floor_verdict(bands)["passed"]
    detect = scene_detectability(bands, epsilon_rhos=0.003)
    assert detect.status == "no_usable_band"
    assert detect.best_band_nm is None


def test_missing_sdb_preserves_reference_and_uncertainty():
    fit = StumpfFit(0.0, 1.0, 1000.0, "test", 1000, 1.0, 1.0, 0.0)
    ref = np.full((7, 7), 20.0)
    result, sigma = blend_stumpf(np.full_like(ref, np.nan), ref, fit)
    np.testing.assert_allclose(result, ref)
    np.testing.assert_allclose(sigma, 3.0)


def test_smoothing_does_not_create_observations_or_drag_edges_towards_zero():
    fit = StumpfFit(0.0, 1.0, 1000.0, "test", 1000, 1.0, 1.0, 0.0)
    sdb = np.full((9, 9), 20.0)
    sdb[:, :4] = np.nan
    ref = np.full_like(sdb, 20.0)
    ref[0, 0] = np.nan
    result, sigma = blend_stumpf(sdb, ref, fit)
    assert np.isnan(result[0, 0]) and np.isnan(sigma[0, 0])
    np.testing.assert_allclose(result[np.isfinite(result)], 20.0)
    np.testing.assert_allclose(sigma[1:, :4], 3.0)


def test_detectability_command_completes_for_a_band_mapping(tmp_path):
    path = tmp_path / "attenuation.json"
    path.write_text(
        json.dumps({"k_by_band": {str(w): c.k_per_m for w, c in calibrations().items()}})
    )
    result = CliRunner().invoke(
        app,
        [
            "process",
            "coastal",
            "detectability",
            "--attenuation",
            str(path),
            "-o",
            str(tmp_path / "out"),
        ],
    )
    assert result.exit_code == 0, result.exception
    assert (tmp_path / "out" / "detectability.json").is_file()


def test_unknown_or_empty_product_verdict_never_becomes_acceptance():
    from oceanstream.coastal.qc.verdicts import combine

    assert not combine({})["passed"]
    assert not combine({"depth": {"passed": False, "flags": []}})["passed"]
    assert not combine({"depth": {}})["passed"]


def test_reopened_calibration_preserves_rejections(tmp_path):
    from oceanstream.coastal.commands import reread_verdict
    from oceanstream.coastal.products import jsonable

    bands = {
        w: replace(c, trust_failures=("reference_suspect",)) for w, c in calibrations().items()
    }
    row = {"bands": jsonable(bands), "verdict": {"passed": True, "flags": []}}
    assert not reread_verdict(row)["passed"]
    # A historical raw-k-only file is never promoted to an accepted product.
    assert not reread_verdict({"k_by_band": {"489": 0.2}})["passed"]


def test_declared_quality_cannot_be_overridden_by_metadata(tmp_path):
    import pytest

    from oceanstream.coastal.io.rasters import RasterGrid
    from oceanstream.coastal.products import ProductWriter

    grid = RasterGrid(4, 4, (10.0, 0.0, 500000.0, 0.0, -10.0, 4256000.0), "EPSG:32629")
    writer = ProductWriter(tmp_path, grid, {"passed": False})
    with pytest.raises(ValueError, match="reserved"):
        writer.write_raster("seabed_par", np.ones(grid.shape), {"qc_passed": True})


def test_rhos_and_rrs_require_matching_wavelengths_grids_and_identity(tmp_path):
    import pytest

    from oceanstream.coastal.io.rasters import RasterGrid, write_cog
    from oceanstream.coastal.scene import Scene

    grid = RasterGrid(4, 4, (10.0, 0.0, 500000.0, 0.0, -10.0, 4256000.0), "EPSG:32629")
    name = "S2A_MSI_2026_06_27_11_30_00_T29SMC"
    rhos = tmp_path / f"{name}_L2R_rhos_489.tif"
    rrs = tmp_path / f"{name}_L2W_Rrs_490.tif"
    write_cog(rhos, np.full(grid.shape, 0.03), grid)
    write_cog(rrs, np.full(grid.shape, 0.03 / np.pi), grid)
    with pytest.raises(ValueError, match="wavelength"):
        Scene.from_acolite_dir(tmp_path, solar_zenith_fallback_deg=20.0)
    rrs.unlink()
    rrs = tmp_path / f"{name}_L2W_Rrs_489.tif"
    shifted = replace(grid, transform=(10.0, 0.0, 500010.0, 0.0, -10.0, 4256000.0))
    write_cog(rrs, np.full(grid.shape, 0.03 / np.pi), shifted)
    with pytest.raises(ValueError, match="grid"):
        Scene.from_acolite_dir(tmp_path, solar_zenith_fallback_deg=20.0)
    rrs.unlink()
    write_cog(rrs, np.full(grid.shape, 0.03), grid)
    with pytest.raises(ValueError, match="convention"):
        Scene.from_acolite_dir(tmp_path, solar_zenith_fallback_deg=20.0)
    rrs.rename(tmp_path / rrs.name.replace("2026_06_27", "2026_06_28"))
    with pytest.raises(ValueError, match="identit"):
        Scene.from_acolite_dir(tmp_path, solar_zenith_fallback_deg=20.0)


def test_native_reference_cells_do_not_cross_sdb_partitions(tmp_path):
    from types import SimpleNamespace

    from oceanstream.coastal.config import BathymetryConfig
    from oceanstream.coastal.io.rasters import RasterGrid, write_cog
    from oceanstream.coastal.stages import reference_split

    native = RasterGrid(4, 4, (120.0, 0.0, 500000.0, 0.0, -120.0, 4256000.0), "EPSG:32629")
    target = replace(
        native, width=48, height=48, transform=(10.0, 0.0, 500000.0, 0.0, -10.0, 4256000.0)
    )
    path = tmp_path / "native.tif"
    write_cog(path, np.full(native.shape, 20.0), native, tags={"positive": "down"})
    prepared = SimpleNamespace(
        scene=SimpleNamespace(shape=target.shape, grid=target),
        valid=np.ones(target.shape, bool),
        provenance={
            "fine": {"available": False},
            "coarse": {
                "available": True,
                "source_grid": native.to_dict(),
                "uri": str(path),
                "max_reliable_depth_m": None,
            },
        },
    )
    train, evaluation = reference_split(prepared, BathymetryConfig(checkerboard_block_px=12))
    assert train.any() and evaluation.any() and not (train & evaluation).any()

    def contributors(mask):
        cells = set()
        for row, col in zip(*np.where(mask)):
            y, x = np.floor([(row + 0.5) / 12 - 0.5, (col + 0.5) / 12 - 0.5]).astype(int)
            cells.update(
                (max(0, min(3, yy)), max(0, min(3, xx))) for yy in (y, y + 1) for xx in (x, x + 1)
            )
        return cells

    assert not (contributors(train) & contributors(evaluation))


def test_detection_error_propagates_depth_and_fit_error():
    from oceanstream.coastal.config import UncertaintyConfig
    from oceanstream.coastal.uncertainty import detection_sigma

    bands = {w: replace(c, k_standard_error=0.01) for w, c in calibrations().items()}
    detected = scene_detectability(bands, epsilon_rhos=0.003)
    sigma, budget = detection_sigma(
        detected,
        bands,
        np.array([[2.0]]),
        UncertaintyConfig(empirical_relative_sigma=0.0, source="synthetic error model"),
        n_noise_blocks=101,
    )
    k = bands[detected.best_band_nm].k_per_m
    expected = np.sqrt(4 + (detected.z_max_m / k * 0.01) ** 2 + 1 / (200 * k**2))
    np.testing.assert_allclose(sigma, expected)
    assert not budget["missing_terms"]


def test_reopened_empty_flags_cannot_certify_missing_fit_statistics():
    from oceanstream.coastal.commands import reread_verdict

    payload = {
        "bands": {
            str(w): {"k_per_m": c.k_per_m, "trust_failures": []} for w, c in calibrations().items()
        },
        "verdict": {"passed": True, "flags": []},
    }
    assert not reread_verdict(payload)["passed"]


def test_missing_sdb_retains_spatially_varying_reference_uncertainty():
    fit = StumpfFit(0.0, 1.0, 1000.0, "test", 1000, 1.0, 1.0, 0.0)
    reference = np.full((4, 4), 20.0)
    sigma = np.tile([0.5, 1.0, 2.0, 3.0], (4, 1))
    depth, propagated = blend_stumpf(
        np.full_like(reference, np.nan), reference, fit, reference_sigma_m=sigma
    )
    np.testing.assert_allclose(depth, reference)
    np.testing.assert_allclose(propagated, sigma)
