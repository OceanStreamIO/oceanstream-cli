"""R2R (Rolling Deck to Repository) data provider.

Handles GeoCSV format data from the R2R program, which manages underway
oceanographic data from research vessels.

Key characteristics:
- GeoCSV format with rich metadata headers (# prefix lines)
- Per-instrument files (navigation, CTD, SVP, ADCP, etc.)
- Cruise ID based platform identification
- Standard column names: ship_longitude, ship_latitude, iso_time
- DOI links for data provenance.
"""

from pathlib import Path
import re
from typing import Any, Dict, List, Literal

import pandas as pd

from oceanstream.providers.r2r.r2r_archive import (
    R2RArchiveLayout,
    extract_r2r_archive,
    find_r2r_archives,
)
"""Backwards-compatibility wrapper for the R2R provider.

The real implementation now lives in :mod:`oceanstream.providers.r2r.r2r`.
This module re-exports :class:`R2RProvider` so that older imports from
``oceanstream.providers.r2r`` continue to work.
"""

from .r2r.r2r import R2RProvider

