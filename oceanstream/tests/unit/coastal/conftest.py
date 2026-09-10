"""Shared fixtures for :mod:`oceanstream.coastal` unit tests.

Optical properties come from a saved scene fit (Sesimbra 2026-06-27) rather
than invented numbers, so the synthetic experiments below exercise the same
regime as the real pipeline. See ``tests/data/coastal/README.md`` for
provenance.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from oceanstream.coastal.optics.qaa import SceneIOPs

DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "coastal"
SCENE_IOPS_JSON = DATA_DIR / "scene_iops_sesimbra_2026_06_27.json"
SOLAR_ZENITH_DEG = 21.0
GREEN_NM = 561.0


@pytest.fixture(scope="session")
def scene_iops_raw() -> dict:
    if not SCENE_IOPS_JSON.exists():
        pytest.skip(f"scene IOP fixture not available: {SCENE_IOPS_JSON}")
    return json.loads(SCENE_IOPS_JSON.read_text())


@pytest.fixture(scope="session")
def green_iops(scene_iops_raw: dict) -> SceneIOPs:
    """Single-band (561 nm) IOPs, shaped as the inversion expects."""
    wavelengths = np.asarray(scene_iops_raw["wavelengths_nm"], dtype=float)
    idx = int(np.argmin(np.abs(wavelengths - GREEN_NM)))
    a_cdm_443 = scene_iops_raw["a_cdm_443"]
    return SceneIOPs(
        wavelengths_nm=np.array([wavelengths[idx]]),
        a=np.array([scene_iops_raw["a"][idx]]),
        bb=np.array([scene_iops_raw["bb"][idx]]),
        kd=np.array([scene_iops_raw["kd"][idx]]),
        a_cdm_443=float("nan") if a_cdm_443 is None else float(a_cdm_443),
        bbp_555=float("nan"),
        y=float("nan"),
        n_deep_pixels=0,
        ref_wavelength_nm=GREEN_NM,
    )
