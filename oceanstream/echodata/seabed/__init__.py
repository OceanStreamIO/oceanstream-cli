"""
Seabed detection module for echosounder data.

Provides algorithms for detecting seabed echoes in Sv data and generating
masks to exclude seabed and sub-seabed data from analysis.

Based on algorithms from:
- echopy library (Alejandro Ariza et al.)
- De Robertis & Higginbottom (2007)
- Blackwell et al. (2019) for aliased seabed detection

Example usage:
    >>> from oceanstream.echodata.seabed import detect_seabed, mask_seabed
    >>> seabed_line = detect_seabed(sv_dataset, method="maxSv")
    >>> sv_masked = mask_seabed(sv_dataset, seabed_line, offset=10)
"""

from .detection import (
    detect_seabed,
    detect_seabed_maxSv,
    detect_seabed_deltaSv,
    detect_seabed_ariza,
    detect_seabed_composite,
    detect_seabed_blackwell,
    mask_seabed,
    compute_seabed_stats,
    find_optimal_seabed_channel,
    SeabedDetectionResult,
)
from .bathymetry import (
    get_bathymetry,
    estimate_seabed_depth,
)

__all__ = [
    "detect_seabed",
    "detect_seabed_maxSv",
    "detect_seabed_deltaSv",
    "detect_seabed_ariza",
    "detect_seabed_composite",
    "detect_seabed_blackwell",
    "mask_seabed",
    "compute_seabed_stats",
    "find_optimal_seabed_channel",
    "SeabedDetectionResult",
    "get_bathymetry",
    "estimate_seabed_depth",
]
