"""Generic calibration interface for echosounder data.

Two execution modes:

* **Bake-time** (``apply_calibration``): mutates ``EchoData`` in place by
  writing into ``Vendor_specific`` and ``Sonar/Beam_group1``. Used by the
  Saildrone Excel path which encodes per-(frequency, pulse-mode) values.
  The resulting Zarr is "self-contained" — downstream ``compute_Sv`` calls
  need no extra args.

* **Sv-time** (``ecs.build_cal_params_from_ecs``): returns
  ``(env_params, cal_params)`` dicts to pass to
  ``echopype.calibrate.compute_Sv``. Preferred for ECS files because it
  preserves frequency-dependent broadband tables and lets a single
  converted Zarr be re-calibrated without reconversion.
  See :mod:`oceanstream.echodata.calibrate.ecs`.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from echopype.echodata import EchoData

logger = logging.getLogger(__name__)


def load_calibration(
    calibration_file: Path,
    provider: str = "auto",
) -> dict[str, Any]:
    """Load calibration values from a calibration file.

    Currently supports Excel (``.xlsx``) Saildrone calibration files and
    ``.json``. For Echoview ``.ecs`` files prefer
    :func:`oceanstream.echodata.calibrate.ecs.parse_ecs`, which uses
    echopype's parser and returns datasets compatible with
    ``compute_Sv(cal_params=..., env_params=...)``.
    """
    calibration_file = Path(calibration_file)

    if not calibration_file.exists():
        raise FileNotFoundError(f"Calibration file not found: {calibration_file}")

    suffix = calibration_file.suffix.lower()

    if suffix == ".xlsx":
        from oceanstream.echodata.calibrate.saildrone import load_saildrone_calibration
        return load_saildrone_calibration(calibration_file)

    if suffix == ".json":
        import json
        with open(calibration_file) as f:
            return json.load(f)

    if suffix == ".ecs":
        raise ValueError(
            ".ecs files are not loaded into a flat dict. Use "
            "oceanstream.echodata.calibrate.ecs.parse_ecs() or "
            "build_cal_params_from_ecs() and pass the result to "
            "echopype.calibrate.compute_Sv(env_params=..., cal_params=...)."
        )

    raise ValueError(
        f"Unsupported calibration file format: {suffix}. "
        "Supported: .xlsx (Saildrone), .json. For .ecs use ecs.parse_ecs."
    )


def apply_calibration(
    echodata: "EchoData",
    calibration: Path | dict[str, Any],
    provider: str = "auto",
) -> "EchoData":
    """Bake calibration into an ``EchoData`` object in place.

    Currently only the Saildrone Excel format is supported — it writes
    per-(frequency, pulse-mode) values directly into ``Vendor_specific``
    and ``Sonar/Beam_group1``.

    For Echoview ``.ecs`` files use
    :func:`oceanstream.echodata.calibrate.ecs.build_cal_params_from_ecs`
    and pass the result to ``compute_Sv`` instead.
    """
    if isinstance(calibration, (str, Path)):
        calibration = load_calibration(Path(calibration), provider)

    if not isinstance(calibration, dict):
        raise TypeError(
            f"calibration must be a Path or dict, got {type(calibration).__name__}"
        )

    if provider == "auto":
        if "dataframe" in calibration or "38k_short" in calibration:
            provider = "saildrone"
        else:
            raise ValueError(
                "Cannot infer calibration provider. Pass provider='saildrone' "
                "explicitly, or use ecs.build_cal_params_from_ecs() for ECS files."
            )

    logger.info("Applying %s calibration", provider)

    if provider == "saildrone":
        from oceanstream.echodata.calibrate.saildrone import calibrate_saildrone
        return calibrate_saildrone(echodata, calibration)

    raise ValueError(
        f"Unknown calibration provider: {provider}. "
        "Supported: 'saildrone'. For ECS files use ecs.build_cal_params_from_ecs."
    )


def validate_calibration_params(params: dict) -> bool:
    """Validate the structure of a freeform calibration parameters dict.

    Accepts either numeric (Hz) or string frequency keys whose values are
    themselves dicts.

    Raises
    ------
    TypeError
        If a frequency key is not numeric or string.
    ValueError
        If a top-level value is not a dict.
    """
    for freq_key, values in params.items():
        if not isinstance(freq_key, (int, float, str)):
            raise TypeError(
                f"Frequency key must be numeric or string, got {type(freq_key)}"
            )
        if not isinstance(values, dict):
            raise ValueError(f"Calibration values for {freq_key} must be a dict")
    return True
