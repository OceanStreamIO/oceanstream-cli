"""R2R SSV (surface sound velocity) sensor processors.

Like :mod:`r2r_fluorometer`, this module defines both a
SensorDescriptor builder and a raw-data processor for SSV instruments
found in R2R archives.
"""

from __future__ import annotations

from pathlib import Path

from oceanstream.providers.r2r_metadata import R2RFileInfo, R2RSensorInfo
from oceanstream.sensors.processor_base import SensorDescriptor
from oceanstream.sensors.processors import (
	RawProcessor,
	SensorProcessor,
	register_raw_processor,
	register_sensor_processor,
)


SENSOR_TYPE_SSV = "ssv"
SENSOR_ID_SSV = "valeport-minisvs"


def ssv_descriptor_processor(
	data_dir: Path,
	file_info: R2RFileInfo,
	sensor_info: R2RSensorInfo,
	provider_id: str,
) -> SensorDescriptor:
	"""Build a SensorDescriptor for an R2R SSV instrument."""

	campaign_id = file_info.campaign_id or "unknown_campaign"
	platform_id = file_info.platform

	metadata: dict[str, str] = {}
	metadata.update(file_info.extra or {})
	metadata.update(sensor_info.extra or {})

	if sensor_info.sensor_id:
		metadata.setdefault("instrument_id", sensor_info.sensor_id)
	if sensor_info.description:
		metadata.setdefault("instrument_description", sensor_info.description)

	return SensorDescriptor(
		sensor_type=sensor_info.sensor_type or SENSOR_TYPE_SSV,
		sensor_id=SENSOR_ID_SSV,
		provider_id=provider_id,
		platform_id=platform_id,
		campaign_id=campaign_id,
		description=sensor_info.description,
		metadata=metadata,
	)


def ssv_raw_processor(
	data_dir: Path,
	file_info: R2RFileInfo,
	sensor_info: R2RSensorInfo,
	descriptor: SensorDescriptor,
) -> Path:
	"""Process R2R SSV raw data into a simple CSV file.

	R2R SSV ``*.Raw`` files contain lines of the form::

	    MM/DD/YYYY,HH:MM:SS.sss, velocity

	where velocity is the sound velocity in m/s. We parse these into a
	tabular structure with the following columns:

	- ``date`` — date in MM/DD/YYYY format
	- ``time`` — time in HH:MM:SS.sss format
	- ``sound_velocity`` — sound velocity in m/s

	The function writes a ``ssv.csv`` file in ``data_dir`` and returns
	its path. Malformed lines are skipped rather than raising.
	"""
	import csv

	rows: list[list[str]] = []

	for raw_path in sorted(data_dir.glob("*.Raw")):
		with raw_path.open("r", encoding="ascii", errors="replace") as f:
			for line in f:
				line = line.strip()
				if not line:
					continue

				# Split on comma: date, time, velocity
				parts = line.split(",")
				if len(parts) < 3:
					# Not enough columns
					continue

				date = parts[0].strip()
				time = parts[1].strip()
				velocity = parts[2].strip()

				# Basic validation: velocity should be numeric
				try:
					float(velocity)
				except ValueError:
					# Skip lines where velocity isn't a number
					continue

				rows.append([date, time, velocity])

	out_path = data_dir / "ssv.csv"
	with out_path.open("w", newline="") as f:
		writer = csv.writer(f)
		writer.writerow(["date", "time", "sound_velocity"])
		writer.writerows(rows)

	return out_path


register_sensor_processor(SENSOR_TYPE_SSV, ssv_descriptor_processor)
register_raw_processor(SENSOR_ID_SSV, ssv_raw_processor)
