"""Registry for per-sensor processors.

Each sensor type (e.g. ``adcp``, ``ctd``) can register a processor
implementation which can be invoked by provider-specific pipelines
such as the R2R archive inspector.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from oceanstream.sensors.processor_base import (
	RawProcessor,
	SensorDescriptor,
	SensorProcessor,
)

if TYPE_CHECKING:
	from oceanstream.providers.r2r_metadata import R2RFileInfo, R2RSensorInfo


SensorProcessorFunc = Callable[[Path, "R2RFileInfo", "R2RSensorInfo", str], SensorDescriptor]
RawProcessorFunc = Callable[[Path, "R2RFileInfo", "R2RSensorInfo", SensorDescriptor], Path]

SENSOR_PROCESSORS: dict[str, SensorProcessorFunc] = {}
RAW_PROCESSORS: dict[str, RawProcessorFunc] = {}


def register_sensor_processor(sensor_type: str, processor: SensorProcessor) -> None:
	"""Register a processor implementation for a given sensor type.

	Parameters
	----------
	sensor_type:
		Canonical sensor type identifier (e.g. ``"adcp"``).
	processor:
		Callable implementing :class:`SensorProcessor`.
	"""

	SENSOR_PROCESSORS[sensor_type] = processor


def get_sensor_processor(sensor_type: str) -> SensorProcessorFunc | None:
	"""Return the registered processor for ``sensor_type`` if any."""

	return SENSOR_PROCESSORS.get(sensor_type)


def register_raw_processor(sensor_id: str, processor: RawProcessor) -> None:
	"""Register a raw data processor for a concrete sensor ID.

	Parameters
	----------
	sensor_id:
		Canonical sensor identifier (e.g. ``"wetlabs-eco-flntu"``).
	processor:
		Callable implementing :class:`RawProcessor`.
	"""

	RAW_PROCESSORS[sensor_id] = processor


def get_raw_processor(sensor_id: str) -> RawProcessorFunc | None:
	"""Return the registered raw processor for ``sensor_id`` if any."""

	return RAW_PROCESSORS.get(sensor_id)


# Import processor modules to trigger registration
# NOTE: These must be imported AFTER the registry functions are defined
from . import r2r_fluorometer  # noqa: F401, E402
from . import r2r_ssv  # noqa: F401, E402
from . import r2r_ctd  # noqa: F401, E402
from . import r2r_winch  # noqa: F401, E402
from . import nmea_gnss  # noqa: F401, E402

# Register NMEA processor (deferred to avoid circular imports)
nmea_gnss._register_nmea_processor()
