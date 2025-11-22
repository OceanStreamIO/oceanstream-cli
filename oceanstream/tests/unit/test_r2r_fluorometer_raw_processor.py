from __future__ import annotations

from pathlib import Path

from oceanstream.providers.r2r_metadata import R2RFileInfo, R2RSensorInfo
from oceanstream.sensors.processor_base import SensorDescriptor
from oceanstream.sensors.processors.r2r_fluorometer import fluorometer_raw_processor


def test_fluorometer_raw_processor_creates_csv(tmp_path: Path) -> None:
	"""Fluorometer raw processor should parse .Raw files into a CSV.

	This uses a tiny synthetic RAW file with the same structure observed
	in the real R2R fluorometer data: a leading date/time pair followed
	by a NUL and a second date/time plus three numeric channels
	separated by tabs.
	"""

	data_dir = tmp_path / "data"
	data_dir.mkdir()

	raw_content = (
		"12/29/2016,01:57:00.908,\x0012/29/16\t02:03:48\t695\t868\t925\r\n"
		"12/29/2016,01:57:08.827,\x0012/29/16\t02:03:56\t695\t936\t541\r\n"
	)

	raw_file = data_dir / "COM25-Fluorometer-RAW_20161228-213221.Raw"
	raw_file.write_bytes(raw_content.encode("ascii"))

	file_info = R2RFileInfo(
		campaign_id="FK161229",
		cruise_id="FK161229",
		platform="TEST_PLATFORM",
		start_time=None,
		end_time=None,
		extra={},
	)
	
	sensor_info = R2RSensorInfo(
		sensor_type="fluorometer",
		sensor_id="TEST-FLUORO-001",
		description="Test fluorometer",
		extra={},
	)

	descriptor = SensorDescriptor(
		sensor_type="fluorometer",
		sensor_id="wetlabs-eco-flntu",
		provider_id="r2r",
		platform_id=file_info.platform,
		campaign_id=file_info.campaign_id or "UNKNOWN",
		description=sensor_info.description,
		metadata={},
	)

	out_path = fluorometer_raw_processor(data_dir, file_info, sensor_info, descriptor)

	assert out_path.exists()
	assert out_path.name == "fluorometer.csv"

	lines = out_path.read_text().splitlines()
	# Header + 2 data lines
	assert len(lines) == 3
	assert lines[0] == "local_date,local_time,data_date,data_time,ch1,ch2,ch3"
	assert lines[1] == "12/29/2016,01:57:00.908,12/29/16,02:03:48,695,868,925"
	assert lines[2] == "12/29/2016,01:57:08.827,12/29/16,02:03:56,695,936,541"
