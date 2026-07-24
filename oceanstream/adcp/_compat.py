"""Compatibility shims for dolfyn with numpy 2.x and scipy 1.14+.

Import this module before importing dolfyn to apply necessary patches.
"""
from __future__ import annotations

import numpy as np

# numpy 2.0 removed np.NaN (use np.nan)
if not hasattr(np, "NaN"):
    np.NaN = np.nan  # type: ignore[attr-defined]

# numpy 2.0 moved RankWarning to np.exceptions
if not hasattr(np, "RankWarning"):
    np.RankWarning = np.exceptions.RankWarning  # type: ignore[attr-defined]

# scipy 1.14 renamed cumtrapz -> cumulative_trapezoid
import scipy.integrate  # noqa: E402

if not hasattr(scipy.integrate, "cumtrapz"):
    scipy.integrate.cumtrapz = scipy.integrate.cumulative_trapezoid  # type: ignore[attr-defined]
