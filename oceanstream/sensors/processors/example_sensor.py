"""Example per-sensor processor.

This module illustrates how a concrete sensor implementation can
register itself with the sensor processor registry. It is wired to the
R2R metadata types but can be adapted for other providers as needed.

In real usage this file would be replaced or complemented by
domain-specific processors such as ``adcp.py``, ``ctd.py``, etc.
"""

from __future__ import annotations

from pathlib import Path

from oceanstream.providers.r2r_metadata import R2RFileInfo, R2RSensorInfo
from oceanstream.sensors.processor_base import SensorDescriptor
from oceanstream.sensors.processors import register_sensor_processor


def example_sensor_processor(
	data_dir: Path,
	file_info: R2RFileInfo,
	sensor_info: R2RSensorInfo,
	provider_id: str,
) -> SensorDescriptor:
	"""Very small example implementation.

	For now we simply echo a minimal descriptor based purely on
	metadata – the raw files in ``data_dir`` are not inspected.
	"""

	campaign_id = file_info.campaign_id or "unknown_campaign"
	platform_id = file_info.platform

	return SensorDescriptor(
		sensor_type=sensor_info.sensor_type or "example",
		sensor_id=sensor_info.sensor_id,
		provider_id=provider_id,
		platform_id=platform_id,
		campaign_id=campaign_id,
		description=sensor_info.description,
		metadata={
			**(file_info.extra or {}),
			**(sensor_info.extra or {}),
		},
	)


# Register under a placeholder type so that pipelines can be tested
# end-to-end without committing to a real sensor taxonomy yet.
register_sensor_processor("example", example_sensor_processor)

