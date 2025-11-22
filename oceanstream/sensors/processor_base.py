"""Base types and interfaces for generic sensor processors.

These abstractions are intentionally light-weight so they can be used
by multiple providers (e.g. Saildrone, R2R, others) when they share
the same underlying sensor data format.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
	from oceanstream.providers.r2r_metadata import R2RFileInfo, R2RSensorInfo


@dataclass
class SensorDescriptor:
	"""Describes a sensor instance for the sensor catalogue.

	This is *not* the full STAC or GeoParquet schema – it is a
	compact description used to update the sensor catalogue entries
	for a given provider.
	"""

	sensor_type: str
	sensor_id: str | None
	provider_id: str
	platform_id: str | None
	campaign_id: str
	description: str | None
	metadata: dict[str, str]


class SensorProcessor(Protocol):
	"""Protocol for per-sensor processors.

	A processor receives a directory containing raw data for a sensor
	plus R2R metadata objects. In the future we can add other
	provider-specific metadata types and keep the public API stable by
	widening the accepted types.
	"""

	def __call__(
		self,
		data_dir: Path,
		file_info: "R2RFileInfo",
		sensor_info: "R2RSensorInfo",
		provider_id: str,
	) -> SensorDescriptor:  # pragma: no cover - structural protocol
		...


class RawProcessor(Protocol):
	"""Protocol for per-sensor raw data processors.

	A raw processor is responsible for converting provider-specific raw
	files for a *particular sensor* into a standardised intermediate
	representation (typically CSV/GeoCSV) that the rest of the pipeline
	can consume.

	The protocol is intentionally minimal so that the same raw processor
	can be re-used by different providers as long as they supply
	compatible metadata objects.
	"""

	def __call__(
		self,
		data_dir: Path,
		file_info: "R2RFileInfo",
		sensor_info: "R2RSensorInfo",
		descriptor: SensorDescriptor,
	) -> Path:  # pragma: no cover - structural protocol
		"""Process raw data and return path to standardised output file."""
		...

