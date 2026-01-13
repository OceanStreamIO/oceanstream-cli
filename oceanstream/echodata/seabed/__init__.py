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
    mask_seabed,
    compute_seabed_stats,
    SeabedDetectionResult,
)

__all__ = [
    "detect_seabed",
    "detect_seabed_maxSv",
    "detect_seabed_deltaSv",
    "detect_seabed_ariza",
    "mask_seabed",
    "compute_seabed_stats",
    "SeabedDetectionResult",
]
