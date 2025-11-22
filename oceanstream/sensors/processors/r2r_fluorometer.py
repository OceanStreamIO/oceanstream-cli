"""R2R fluorometer sensor processors.

This module provides both a SensorDescriptor builder and a raw-data
processor for fluorometer instruments discovered in R2R archives.

The raw processor is currently a placeholder that simply returns the
input ``data_dir``. Once the exact R2R fluorometer raw format is
understood we can implement real parsing and CSV/GeoCSV generation
here without changing provider-specific code.
"""

from __future__ import annotations

from pathlib import Path

import csv

from oceanstream.providers.r2r_metadata import R2RFileInfo, R2RSensorInfo
from oceanstream.sensors.processor_base import SensorDescriptor
from oceanstream.sensors.processors import (
	RawProcessor,
	SensorProcessor,
	register_raw_processor,
	register_sensor_processor,
)


SENSOR_TYPE_FLUOROMETER = "fluorometer"
SENSOR_ID_FLUOROMETER = "wetlabs-eco-flntu"


def fluorometer_descriptor_processor(
	data_dir: Path,
	file_info: R2RFileInfo,
	sensor_info: R2RSensorInfo,
	provider_id: str,
) -> SensorDescriptor:
	"""Build a SensorDescriptor for an R2R fluorometer.

	This concentrates identification and metadata in one place so that
	other providers sharing the same instrument can re-use the same
	canonical sensor ID and variables.
	"""

	campaign_id = file_info.campaign_id or "unknown_campaign"
	platform_id = file_info.platform

	metadata: dict[str, str] = {}
	metadata.update(file_info.extra or {})
	metadata.update(sensor_info.extra or {})

	# Provide some canonical keys that can be mapped directly into
	# sensor.json or other catalogues.
	if sensor_info.sensor_id:
		metadata.setdefault("instrument_id", sensor_info.sensor_id)
	if sensor_info.description:
		metadata.setdefault("instrument_description", sensor_info.description)

	return SensorDescriptor(
		sensor_type=sensor_info.sensor_type or SENSOR_TYPE_FLUOROMETER,
		sensor_id=SENSOR_ID_FLUOROMETER,
		provider_id=provider_id,
		platform_id=platform_id,
		campaign_id=campaign_id,
		description=sensor_info.description,
		metadata=metadata,
	)


def fluorometer_raw_processor(
	data_dir: Path,
	file_info: R2RFileInfo,
	sensor_info: R2RSensorInfo,
	descriptor: SensorDescriptor,
) -> Path:
	"""Process R2R fluorometer raw data into a simple CSV file.

	R2R fluorometer ``*.Raw`` files appear to contain lines of the form::

	    MM/DD/YYYY,HH:MM:SS.sss,\x00MM/DD/YY<TAB>HH:MM:SS<TAB>v1<TAB>v2<TAB>v3

	We parse these into a tabular structure with the following columns:

	- ``local_date`` / ``local_time`` — first timestamp pair
	- ``data_date`` / ``data_time`` — second timestamp pair
	- ``ch1``, ``ch2``, ``ch3`` — three numeric channels

	The function writes a ``fluorometer.csv`` file in ``data_dir`` and
	returns its path. Malformed lines are skipped rather than raising.
	"""

	rows: list[list[str]] = []

	for raw_path in sorted(data_dir.glob("*.Raw")):
		# Read as bytes then decode to be resilient to NUL characters.
		content = raw_path.read_bytes().decode("ascii", errors="replace")
		for line in content.splitlines():
			line = line.strip()
			if not line:
				continue

			# Split off the first timestamp part: MM/DD/YYYY,HH:MM:SS.sss
			# We expect two commas before reaching the NUL / second timestamp,
			# so split into at most three pieces and join the remainder back.
			first_parts = line.split(",", 2)
			if len(first_parts) < 3:
				# Not enough pieces for date, time and remainder
				continue
			local_date, local_time, rest = first_parts

			# ``rest`` typically begins with a NUL char then the second date.
			rest_clean = rest.lstrip("\x00, ")
			parts = rest_clean.split("\t")
			if len(parts) < 5:
				# Expect: data_date, data_time, ch1, ch2, ch3
				continue

			data_date, data_time, ch1, ch2, ch3 = parts[:5]
			rows.append([
				local_date,
				local_time,
				data_date,
				data_time,
				ch1,
				ch2,
				ch3,
			])

	out_path = data_dir / "fluorometer.csv"
	with out_path.open("w", newline="") as f:
		writer = csv.writer(f)
		writer.writerow(
			[
				"local_date",
				"local_time",
				"data_date",
				"data_time",
				"ch1",
				"ch2",
				"ch3",
			]
		)
		writer.writerows(rows)

	return out_path


register_sensor_processor(SENSOR_TYPE_FLUOROMETER, fluorometer_descriptor_processor)
register_raw_processor(SENSOR_ID_FLUOROMETER, fluorometer_raw_processor)
