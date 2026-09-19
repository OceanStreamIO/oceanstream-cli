"""Executable synthetic walkthrough; this is not a field-validation benchmark."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from oceanstream.coastal.aoi import AOI, BathymetryReference
from oceanstream.coastal.config import BathymetryConfig
from oceanstream.coastal.inversion.lee import forward_model
from oceanstream.coastal.io.rasters import RasterGrid, write_cog
from oceanstream.coastal.optics import water
from oceanstream.coastal.optics.qaa import SceneIOPs
from oceanstream.coastal.processor import CoastalProcessor, CoastalResult
from oceanstream.coastal.provenance import new_run_directory
from oceanstream.coastal.scene import Scene
from oceanstream.coastal.stac.coastal_emit import geographic_bounds


def run(output: Path) -> CoastalResult:
    directory = new_run_directory(output / "examples")
    inputs = directory / "inputs"
    inputs.mkdir()
    scene_dir = inputs / "acolite"
    scene_dir.mkdir()
    grid = RasterGrid(64, 64, (10.0, 0.0, 500000.0, 0.0, -10.0, 4256000.0), "EPSG:32629")
    wavelengths = np.array([444.0, 489.0, 561.0, 667.0, 707.0, 835.0, 1612.0, 2191.0])
    depth = np.tile(np.r_[np.linspace(1, 24, 48), np.linspace(40, 150, 16)], (64, 1))
    substrate = np.where(np.arange(64)[:, None] % 4 < 2, 0.2, 0.04)
    rng = np.random.default_rng(42)
    bands = []
    for wl in wavelengths:
        a = float(water.a_water(wl)) + 0.035 * np.exp(-0.014 * (wl - 443))
        bb = float(water.bb_water(wl)) + 0.006 * 555 / wl
        iops = SceneIOPs(
            np.array([wl]),
            np.array([a]),
            np.array([bb]),
            np.array([a + 4 * bb]),
            0.035,
            0.006,
            1.0,
            512,
            561.0,
        )
        bands.append(forward_model(substrate, depth, iops, 21.0) + rng.normal(0, 1e-5, grid.shape))
    rhos = np.maximum(np.stack(bands) * np.pi, 0).astype(np.float32)
    rhos[-2:] = 0.001
    for i, wl in enumerate(wavelengths):
        write_cog(
            scene_dir / f"S2C_MSI_2026_06_27_11_30_46_T29SMC_L2R_rhos_{int(wl)}.tif", rhos[i], grid
        )
    (scene_dir / "acolite_settings.txt").write_text(
        "sza=21\n# SYNTHETIC example, not ACOLITE processing\n"
    )
    reference = inputs / "bathymetry.tif"
    write_cog(reference, depth.astype(np.float32), grid, tags={"positive": "down"})
    aoi = AOI(
        "synthetic-example",
        geographic_bounds(grid),
        fine_bathymetry=BathymetryReference(str(reference), uncertainty_m=0.5),
        metadata={"synthetic": True},
    )
    (inputs / "aoi.json").write_text(json.dumps(aoi.to_dict(), indent=2) + "\n")
    scene = Scene.from_acolite_dir(scene_dir)
    scene.metadata["synthetic"] = True
    result = CoastalProcessor(bathymetry_config=BathymetryConfig(checkerboard_block_px=8)).run(
        aoi, scene, directory / "products"
    )
    print(
        json.dumps(
            {
                "success": result.success,
                "status": result.status,
                "message": result.message,
                "inputs": str(inputs),
                "run": str(result.output_dir),
            },
            indent=2,
        )
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("out/coastal-quickstart"))
    args = parser.parse_args()
    raise SystemExit(0 if run(args.output).success else 1)


if __name__ == "__main__":
    main()
