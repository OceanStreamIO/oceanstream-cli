"""Calibration module for echosounder data.

Provides functions for applying calibration values to EchoData objects,
with support for Saildrone Excel calibration files.
"""

from oceanstream.echodata.calibrate.calibration import (
    apply_calibration,
    load_calibration,
)
from oceanstream.echodata.calibrate.saildrone import (
    calibrate_saildrone,
    load_saildrone_calibration,
    detect_pulse_mode,
)

__all__ = [
    "apply_calibration",
    "load_calibration",
    "calibrate_saildrone",
    "load_saildrone_calibration",
    "detect_pulse_mode",
]
