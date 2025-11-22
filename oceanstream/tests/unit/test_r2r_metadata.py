from pathlib import Path

import pytest

from oceanstream.providers.r2r_metadata import (
	R2RFileInfo,
	R2RSensorInfo,
	parse_bag_info,
	parse_file_info,
)


def _write_tmp_file(tmp_path: Path, name: str, content: str) -> Path:
	path = tmp_path / name
	path.write_text(content)
	return path


def test_parse_file_info_basic(tmp_path: Path) -> None:
	path = _write_tmp_file(
		tmp_path,
		"file-info.txt",
		"""Campaign: FK161229
Cruise: FK161229
Platform: Falkor
Start time: 2016-12-29T00:00:00Z
End time: 2017-01-10T00:00:00Z
Comment: Example cruise
""",
	)

	info = parse_file_info(path)

	assert isinstance(info, R2RFileInfo)
	assert info.campaign_id == "FK161229"
	assert info.cruise_id == "FK161229"
	assert info.platform == "Falkor"
	assert info.start_time == "2016-12-29T00:00:00Z"
	assert info.end_time == "2017-01-10T00:00:00Z"
	assert info.extra is not None
	assert info.extra["Comment"] == "Example cruise"


def test_parse_file_info_missing_optional_fields(tmp_path: Path) -> None:
	path = _write_tmp_file(
		tmp_path,
		"file-info.txt",
		"""Campaign: FK161229
Comment: Only campaign provided
""",
	)

	info = parse_file_info(path)

	assert info.campaign_id == "FK161229"
	assert info.cruise_id is None
	assert info.platform is None
	assert info.start_time is None
	assert info.end_time is None
	assert info.extra is not None
	# Comment should still be preserved
	assert info.extra["Comment"] == "Only campaign provided"


def test_parse_bag_info_basic(tmp_path: Path) -> None:
	path = _write_tmp_file(
		tmp_path,
		"bag-info.txt",
		"""Sensor Type: ADCP
Sensor ID: ADCP-001
Description: Example ADCP deployment
Frequency: 300 kHz
""",
	)

	info = parse_bag_info(path)

	assert isinstance(info, R2RSensorInfo)
	assert info.sensor_type == "ADCP"
	assert info.sensor_id == "ADCP-001"
	assert info.description == "Example ADCP deployment"
	assert info.extra is not None
	assert info.extra["Frequency"] == "300 kHz"


def test_parse_bag_info_alternative_keys(tmp_path: Path) -> None:
	path = _write_tmp_file(
		tmp_path,
		"bag-info.txt",
		"""instrument: CTD
serial_number: CTD-123
description: Conductivity temperature depth sensor
""",
	)

	info = parse_bag_info(path)

	assert info.sensor_type == "CTD"
	assert info.sensor_id == "CTD-123"
	assert info.description == "Conductivity temperature depth sensor"


def test_parse_file_info_missing_file_raises(tmp_path: Path) -> None:
	missing = tmp_path / "does-not-exist.txt"
	with pytest.raises(FileNotFoundError):
		parse_file_info(missing)


def test_parse_bag_info_missing_file_raises(tmp_path: Path) -> None:
	missing = tmp_path / "does-not-exist.txt"
	with pytest.raises(FileNotFoundError):
		parse_bag_info(missing)

