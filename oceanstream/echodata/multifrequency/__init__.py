"""Multi-frequency analysis module for echosounder data.

Provides frequency-differencing algorithms for classifying acoustic targets
based on their frequency response. The dB-difference method identifies targets
by comparing Sv at two frequencies and masking regions where the difference
falls within a specified threshold range.

Based on algorithms from:
- echopy library (Alejandro Ariza et al.)
- Kloser et al. (2002) — Species identification using dB differencing

Example usage:
    >>> from oceanstream.echodata.multifrequency import db_difference
    >>> mask = db_difference(sv_dataset, freq_low="38000", freq_high="120000", thr=(-12, -2))
"""

from .frequency_differencing import (
    db_difference,
    FrequencyDifferencingResult,
)

__all__ = [
    "db_difference",
    "FrequencyDifferencingResult",
]
