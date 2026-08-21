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
from oceanstream.echodata.compute.mvbs import SURFACE_BIN_INDEX, compute_mvbs
from oceanstream.echodata.compute.nasc import (
    compute_nasc,
    normalize_nasc_schema,
    repair_nasc_positions,
    validate_nasc_schema,
)
from oceanstream.echodata.compute.nasc_export import (
    export_nasc_to_geoparquet,
    load_nasc_geoparquet,
)

__all__ = [
    "compute_sv",
    "compute_sv_from_echodata",
    "enrich_sv_dataset",
    "swap_range_to_depth",
    "correct_echo_range",
    "apply_corrections_ds",
    "compute_mvbs",
    "SURFACE_BIN_INDEX",
    "compute_nasc",
    "normalize_nasc_schema",
    "repair_nasc_positions",
    "validate_nasc_schema",
    "export_nasc_to_geoparquet",
    "load_nasc_geoparquet",
]
