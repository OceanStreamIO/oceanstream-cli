"""Compute module for Sv, MVBS, and NASC calculations.

Provides wrappers around echopype's compute functions with
configuration from EchodataConfig.
"""

from oceanstream.echodata.compute.sv import (
    compute_sv,
    compute_sv_from_echodata,
    enrich_sv_dataset,
    swap_range_to_depth,
    correct_echo_range,
    apply_corrections_ds,
)
from oceanstream.echodata.compute.mvbs import compute_mvbs
from oceanstream.echodata.compute.nasc import compute_nasc

__all__ = [
    "compute_sv",
    "compute_sv_from_echodata",
    "enrich_sv_dataset",
    "swap_range_to_depth",
    "correct_echo_range",
    "apply_corrections_ds",
    "compute_mvbs",
    "compute_nasc",
]
