"""Unit tests for R2R SSV raw processor."""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from oceanstream.providers.r2r_metadata import R2RFileInfo, R2RSensorInfo
from oceanstream.sensors.processor_base import SensorDescriptor
from oceanstream.sensors.processors.r2r_ssv import (
	SENSOR_ID_SSV,
	SENSOR_TYPE_SSV,
	ssv_descriptor_processor,
	ssv_raw_processor,
)


@pytest.fixture
def sample_ssv_raw_data(tmp_path: Path) -> Path:
	"""Create a sample SSV raw data file for testing."""
	data_dir = tmp_path / "ssv_data"
	data_dir.mkdir()

	raw_file = data_dir / "COM19-MiniSVS-RAW_20170101-000001.Raw"
	raw_file.write_text(
		"01/01/2017,00:00:01.794, 1542.351 \n"
		"01/01/2017,00:00:02.309, 1542.348 \n"
		"01/01/2017,00:00:02.808, 1542.332 \n"
		"01/01/2017,00:00:03.307, 1542.337 \n"
		"01/01/2017,00:00:03.806, 1542.336 \n"
	)

	return data_dir


@pytest.fixture
def sample_file_info() -> R2RFileInfo:
	"""Create sample R2R file metadata."""
	return R2RFileInfo(
		campaign_id="FK161229",
		cruise_id="FK161229",
		platform="R/V Falkor",
		start_time="2017-01-01T00:00:00Z",
		end_time="2017-01-15T23:59:59Z",
		extra={
			"fileset_id": "124690",
			"device_type": "ssv",
			"description": "SSV data from cruise FK161229",
		},
	)


@pytest.fixture
def sample_sensor_info() -> R2RSensorInfo:
	"""Create sample R2R sensor metadata."""
	return R2RSensorInfo(
		sensor_type="ssv",
		sensor_id="valeport-minisvs",
		description="Valeport MiniSVS",
		extra={
			"device_model": "Valeport MiniSVS",
		},
	)


@pytest.fixture
def sample_descriptor() -> SensorDescriptor:
	"""Create sample sensor descriptor."""
	return SensorDescriptor(
		sensor_type=SENSOR_TYPE_SSV,
		sensor_id=SENSOR_ID_SSV,
		provider_id="r2r",
		platform_id="R/V Falkor",
		campaign_id="FK161229",
		description="Valeport MiniSVS",
		metadata={
			"device_model": "Valeport MiniSVS",
		},
	)


class TestSSVDescriptorProcessor:
	"""Test the SSV sensor descriptor processor."""

	def test_creates_valid_descriptor(
		self,
		sample_file_info: R2RFileInfo,
		sample_sensor_info: R2RSensorInfo,
	) -> None:
		"""Test that descriptor processor creates valid SensorDescriptor."""
		descriptor = ssv_descriptor_processor(
			data_dir=Path("/fake/path"),
			file_info=sample_file_info,
			sensor_info=sample_sensor_info,
			provider_id="r2r",
		)

		assert descriptor.sensor_type == SENSOR_TYPE_SSV
		assert descriptor.sensor_id == SENSOR_ID_SSV
		assert descriptor.provider_id == "r2r"
		assert descriptor.platform_id == "R/V Falkor"
		assert descriptor.campaign_id == "FK161229"
		assert descriptor.description == "Valeport MiniSVS"

	def test_includes_metadata_from_file_and_sensor_info(
		self,
		sample_file_info: R2RFileInfo,
		sample_sensor_info: R2RSensorInfo,
	) -> None:
		"""Test that metadata from both sources is merged."""
		descriptor = ssv_descriptor_processor(
			data_dir=Path("/fake/path"),
			file_info=sample_file_info,
			sensor_info=sample_sensor_info,
			provider_id="r2r",
		)

		assert "fileset_id" in descriptor.metadata
		assert "device_type" in descriptor.metadata
		assert "device_model" in descriptor.metadata
		assert descriptor.metadata["fileset_id"] == "124690"
		assert descriptor.metadata["device_model"] == "Valeport MiniSVS"

	def test_handles_missing_campaign_id(
		self,
		sample_sensor_info: R2RSensorInfo,
	) -> None:
		"""Test that missing campaign_id is handled gracefully."""
		file_info = R2RFileInfo(
			campaign_id=None,
			cruise_id=None,
			platform="R/V Falkor",
			start_time=None,
			end_time=None,
			extra={"fileset_id": "124690"},
		)

		descriptor = ssv_descriptor_processor(
			data_dir=Path("/fake/path"),
			file_info=file_info,
			sensor_info=sample_sensor_info,
			provider_id="r2r",
		)

		assert descriptor.campaign_id == "unknown_campaign"


class TestSSVRawProcessor:
	"""Test the SSV raw data processor."""

	def test_parses_simple_format(
		self,
		sample_ssv_raw_data: Path,
		sample_file_info: R2RFileInfo,
		sample_sensor_info: R2RSensorInfo,
		sample_descriptor: SensorDescriptor,
	) -> None:
		"""Test that processor parses SSV raw format correctly."""
		output_path = ssv_raw_processor(
			data_dir=sample_ssv_raw_data,
			file_info=sample_file_info,
			sensor_info=sample_sensor_info,
			descriptor=sample_descriptor,
		)

		assert output_path.exists()
		assert output_path.name == "ssv.csv"

		# Read and verify CSV content
		with output_path.open("r") as f:
			reader = csv.reader(f)
			header = next(reader)
			rows = list(reader)

		assert header == ["date", "time", "sound_velocity"]
		assert len(rows) == 5

		# Verify first row
		assert rows[0][0] == "01/01/2017"
		assert rows[0][1] == "00:00:01.794"
		assert rows[0][2] == "1542.351"

		# Verify last row
		assert rows[4][0] == "01/01/2017"
		assert rows[4][1] == "00:00:03.806"
		assert rows[4][2] == "1542.336"

	def test_handles_multiple_raw_files(
		self,
		tmp_path: Path,
		sample_file_info: R2RFileInfo,
		sample_sensor_info: R2RSensorInfo,
		sample_descriptor: SensorDescriptor,
	) -> None:
		"""Test that processor handles multiple *.Raw files."""
		data_dir = tmp_path / "ssv_data"
		data_dir.mkdir()

		# Create two raw files
		raw_file_1 = data_dir / "COM19-MiniSVS-RAW_20170101-000001.Raw"
		raw_file_1.write_text(
			"01/01/2017,00:00:01.794, 1542.351 \n"
			"01/01/2017,00:00:02.309, 1542.348 \n"
		)

		raw_file_2 = data_dir / "COM19-MiniSVS-RAW_20170102-000001.Raw"
		raw_file_2.write_text(
			"01/02/2017,00:00:01.500, 1543.120 \n"
			"01/02/2017,00:00:02.600, 1543.115 \n"
		)

		output_path = ssv_raw_processor(
			data_dir=data_dir,
			file_info=sample_file_info,
			sensor_info=sample_sensor_info,
			descriptor=sample_descriptor,
		)

		# Read and verify CSV content
		with output_path.open("r") as f:
			reader = csv.reader(f)
			header = next(reader)
			rows = list(reader)

		assert len(rows) == 4  # 2 rows from each file
		# Files processed in sorted order, so file 1 comes first
		assert rows[0][0] == "01/01/2017"
		assert rows[2][0] == "01/02/2017"

	def test_skips_malformed_lines(
		self,
		tmp_path: Path,
		sample_file_info: R2RFileInfo,
		sample_sensor_info: R2RSensorInfo,
		sample_descriptor: SensorDescriptor,
	) -> None:
		"""Test that processor skips malformed lines."""
		data_dir = tmp_path / "ssv_data"
		data_dir.mkdir()

		raw_file = data_dir / "COM19-MiniSVS-RAW_20170101-000001.Raw"
		raw_file.write_text(
			"01/01/2017,00:00:01.794, 1542.351 \n"
			"MALFORMED LINE\n"  # Missing columns
			"01/01/2017,00:00:02.309\n"  # Missing velocity
			"01/01/2017,00:00:03.307, INVALID \n"  # Non-numeric velocity
			"01/01/2017,00:00:04.500, 1542.340 \n"  # Valid
			"\n"  # Empty line
			"01/01/2017,00:00:05.600, 1542.345 \n"  # Valid
		)

		output_path = ssv_raw_processor(
			data_dir=data_dir,
			file_info=sample_file_info,
			sensor_info=sample_sensor_info,
			descriptor=sample_descriptor,
		)

		# Read and verify CSV content
		with output_path.open("r") as f:
			reader = csv.reader(f)
			header = next(reader)
			rows = list(reader)

		# Only 3 valid rows should be present
		assert len(rows) == 3
		assert rows[0][2] == "1542.351"
		assert rows[1][2] == "1542.340"
		assert rows[2][2] == "1542.345"

	def test_handles_extra_whitespace(
		self,
		tmp_path: Path,
		sample_file_info: R2RFileInfo,
		sample_sensor_info: R2RSensorInfo,
		sample_descriptor: SensorDescriptor,
	) -> None:
		"""Test that processor handles extra whitespace in data."""
		data_dir = tmp_path / "ssv_data"
		data_dir.mkdir()

		raw_file = data_dir / "COM19-MiniSVS-RAW_20170101-000001.Raw"
		raw_file.write_text(
			"  01/01/2017  ,  00:00:01.794  ,  1542.351  \n"
			"01/01/2017,00:00:02.309,1542.348\n"
		)

		output_path = ssv_raw_processor(
			data_dir=data_dir,
			file_info=sample_file_info,
			sensor_info=sample_sensor_info,
			descriptor=sample_descriptor,
		)

		# Read and verify CSV content
		with output_path.open("r") as f:
			reader = csv.reader(f)
			header = next(reader)
			rows = list(reader)

		assert len(rows) == 2
		# Whitespace should be stripped
		assert rows[0][0] == "01/01/2017"
		assert rows[0][1] == "00:00:01.794"
		assert rows[0][2] == "1542.351"

	def test_creates_output_in_data_dir(
		self,
		sample_ssv_raw_data: Path,
		sample_file_info: R2RFileInfo,
		sample_sensor_info: R2RSensorInfo,
		sample_descriptor: SensorDescriptor,
	) -> None:
		"""Test that output file is created in the data directory."""
		output_path = ssv_raw_processor(
			data_dir=sample_ssv_raw_data,
			file_info=sample_file_info,
			sensor_info=sample_sensor_info,
			descriptor=sample_descriptor,
		)

		assert output_path.parent == sample_ssv_raw_data
		assert output_path.name == "ssv.csv"

	def test_empty_data_dir(
		self,
		tmp_path: Path,
		sample_file_info: R2RFileInfo,
		sample_sensor_info: R2RSensorInfo,
		sample_descriptor: SensorDescriptor,
	) -> None:
		"""Test that processor handles empty data directory gracefully."""
		data_dir = tmp_path / "empty_ssv_data"
		data_dir.mkdir()

		output_path = ssv_raw_processor(
			data_dir=data_dir,
			file_info=sample_file_info,
			sensor_info=sample_sensor_info,
			descriptor=sample_descriptor,
		)

		assert output_path.exists()
		assert output_path.name == "ssv.csv"

		# Read and verify CSV content
		with output_path.open("r") as f:
			reader = csv.reader(f)
			header = next(reader)
			rows = list(reader)

		assert header == ["date", "time", "sound_velocity"]
		assert len(rows) == 0  # No data rows
