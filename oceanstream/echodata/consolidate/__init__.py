"""Consolidation utilities for EchoData processing.

This module provides functions for adding depth, location, and other
derived variables to Sv datasets, following echopype patterns.
"""

from __future__ import annotations

from oceanstream.echodata.consolidate.depth import (
    add_depth_to_sv,
    choose_depth_flags,
)
from oceanstream.echodata.consolidate.location import (
    merge_location_data,
    interpolate_location_from_dataframe,
    extract_location_data,
    ramer_douglas_peucker,
    extract_start_end_lat_lon,
)

__all__ = [
    "add_depth_to_sv",
    "choose_depth_flags",
    # Location / GPS
    "merge_location_data",
    "interpolate_location_from_dataframe",
    "extract_location_data",
    "ramer_douglas_peucker",
    "extract_start_end_lat_lon",
]
