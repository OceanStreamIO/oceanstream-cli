"""R2R metadata — re-exported from canonical location.

All classes and functions are defined in :mod:`oceanstream.providers.r2r_metadata`.
This module re-exports them for backward-compatible imports.
"""

from oceanstream.providers.r2r_metadata import (  # noqa: F401
    R2RFileInfo,
    R2RSensorInfo,
    parse_bag_info,
    parse_file_info,
)
from oceanstream.sensors.processor_base import FileInfo, SensorInfo  # noqa: F401
