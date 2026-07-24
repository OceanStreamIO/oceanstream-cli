"""Shoal/school detection module for echosounder data.

Provides algorithms for detecting fish schools (shoals) in Sv data:
- weill: Threshold + gap-filling + size filtering (Weill et al., 1993)
- echoview: Candidate → link → filter pipeline (Echoview-style)

Based on algorithms from:
- echopy library (Alejandro Ariza et al.)
- Weill et al. (1993): MOVIES-B acoustic detection software
- Echoview shoal detection documentation

Example usage:
    >>> from oceanstream.echodata.shoal import detect_shoals, mask_shoals
    >>> result = detect_shoals(sv_dataset, method="weill", thr=-70)
    >>> sv_masked = mask_shoals(sv_dataset, result)
"""

from .detection import (
    detect_shoals,
    detect_shoals_weill,
    detect_shoals_echoview,
    mask_shoals,
    ShoalDetectionResult,
)

__all__ = [
    "detect_shoals",
    "detect_shoals_weill",
    "detect_shoals_echoview",
    "mask_shoals",
    "ShoalDetectionResult",
]
