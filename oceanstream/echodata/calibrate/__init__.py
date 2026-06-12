"""Calibration module for echosounder data.

Two paths:

* :func:`apply_calibration` (bake-time): mutates EchoData in place. Used by
  the Saildrone Excel format.
* :func:`build_cal_params_from_ecs` (Sv-time): returns echopype-compatible
  ``env_params`` / ``cal_params`` dicts from one or two Echoview ECS files
  (single- or dual-pulse).
"""

from oceanstream.echodata.calibrate.calibration import (
    apply_calibration,
    load_calibration,
    validate_calibration_params,
)
from oceanstream.echodata.calibrate.ecs import (
    build_cal_params_from_ecs,
    parse_ecs,
)
from oceanstream.echodata.calibrate.saildrone import (
    calibrate_saildrone,
    detect_pulse_mode,
    load_saildrone_calibration,
)

__all__ = [
    "apply_calibration",
    "build_cal_params_from_ecs",
    "calibrate_saildrone",
    "detect_pulse_mode",
    "load_calibration",
    "load_saildrone_calibration",
    "parse_ecs",
    "validate_calibration_params",
]
