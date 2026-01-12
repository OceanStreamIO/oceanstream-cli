"""Consolidation utilities for EchoData processing.

This module provides functions for adding depth, location, and other
derived variables to Sv datasets, following echopype patterns.
"""

from __future__ import annotations

from oceanstream.echodata.consolidate.depth import (
    add_depth_to_sv,
    choose_depth_flags,
)

__all__ = [
    "add_depth_to_sv",
    "choose_depth_flags",
]
