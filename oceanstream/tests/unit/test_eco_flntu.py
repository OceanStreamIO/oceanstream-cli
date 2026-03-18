"""Unit tests for R2R fluorometer sensor processor."""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from oceanstream.providers.r2r_metadata import R2RFileInfo, R2RSensorInfo
from oceanstream.sensors.processors.eco_flntu import (
    SENSOR_ID_FLUOROMETER,
    SENSOR_TYPE_FLUOROMETER,
    fluorometer_descriptor_processor,
    fluorometer_raw_processor,
)


class TestFluorometerDescriptorProcessor:
    """Tests for fluorometer descriptor processor."""

    def test_basic_descriptor_creation(self):
        """Test creating a basic fluorometer descriptor."""
        data_dir = Path("/tmp/test")
        file_info = R2RFileInfo(
            campaign_id="FK161229", platform="Falkor", extra={"cruise": "FK161229"}
        )
        sensor_info = R2RSensorInfo(
            sensor_type="fluorometer",
            sensor_id="wetlabs-eco-flntu",
            description="WetLabs ECO FLNTU Fluorometer",
        )

        descriptor = fluorometer_descriptor_processor(
            data_dir=data_dir, file_info=file_info, sensor_info=sensor_info, provider_id="r2r"
        )

        assert descriptor.sensor_type == "fluorometer"
        assert descriptor.sensor_id == SENSOR_ID_FLUOROMETER
        assert descriptor.provider_id == "r2r"
        assert descriptor.platform_id == "Falkor"
        assert descriptor.campaign_id == "FK161229"
        assert descriptor.description == "WetLabs ECO FLNTU Fluorometer"

    def test_descriptor_with_no_campaign_id(self):
        """Test descriptor creation when campaign_id is missing."""
        data_dir = Path("/tmp/test")
        file_info = R2RFileInfo(campaign_id=None, platform="Falkor")
        sensor_info = R2RSensorInfo(sensor_type="fluorometer", sensor_id=None, description=None)

        descriptor = fluorometer_descriptor_processor(
            data_dir=data_dir, file_info=file_info, sensor_info=sensor_info, provider_id="r2r"
        )

        assert descriptor.campaign_id == "unknown_campaign"

    def test_descriptor_metadata_merge(self):
        """Test that file_info.extra and sensor_info.extra are merged into metadata."""
        data_dir = Path("/tmp/test")
        file_info = R2RFileInfo(
            campaign_id="FK161229", platform="Falkor", extra={"cruise": "FK161229", "year": "2016"}
        )
        sensor_info = R2RSensorInfo(
            sensor_type="fluorometer",
            sensor_id="eco-flntu-001",
            description="Test Fluorometer",
            extra={"serial_number": "12345", "calibration_date": "2016-01-01"},
        )

        descriptor = fluorometer_descriptor_processor(
            data_dir=data_dir, file_info=file_info, sensor_info=sensor_info, provider_id="r2r"
        )

        assert "cruise" in descriptor.metadata
        assert "year" in descriptor.metadata
        assert "serial_number" in descriptor.metadata
        assert "calibration_date" in descriptor.metadata
        assert descriptor.metadata["instrument_id"] == "eco-flntu-001"
        assert descriptor.metadata["instrument_description"] == "Test Fluorometer"

    def test_descriptor_defaults_sensor_type(self):
        """Test that sensor_type defaults to SENSOR_TYPE_FLUOROMETER if not provided."""
        data_dir = Path("/tmp/test")
        file_info = R2RFileInfo(campaign_id="FK161229", platform="Falkor")
        sensor_info = R2RSensorInfo(
            sensor_type=None,  # Missing sensor_type
            sensor_id="test-id",
            description="Test",
        )

        descriptor = fluorometer_descriptor_processor(
            data_dir=data_dir, file_info=file_info, sensor_info=sensor_info, provider_id="r2r"
        )

        assert descriptor.sensor_type == SENSOR_TYPE_FLUOROMETER


class TestFluorometerRawProcessor:
    """Tests for fluorometer raw data processor."""

    def test_parse_valid_raw_data(self, tmp_path):
        """Test parsing valid fluorometer raw data."""
        # Create a test Raw file with valid data
        raw_file = tmp_path / "test.Raw"
        # Use real null byte and tab characters
        raw_content = "01/15/2023,10:30:45.123,\x0001/15/23\t10:30:45\t1.234\t5.678\t9.012\n01/15/2023,10:30:46.456,\x0001/15/23\t10:30:46\t2.345\t6.789\t0.123\n"
        raw_file.write_bytes(raw_content.encode("ascii"))

        file_info = R2RFileInfo(campaign_id="FK161229", platform="Falkor")
        sensor_info = R2RSensorInfo(
            sensor_type="fluorometer", sensor_id="wetlabs-eco-flntu", description="Test Fluorometer"
        )

        # Use the descriptor processor to create a valid descriptor
        descriptor = fluorometer_descriptor_processor(
            data_dir=tmp_path, file_info=file_info, sensor_info=sensor_info, provider_id="r2r"
        )

        output_path = fluorometer_raw_processor(
            data_dir=tmp_path, file_info=file_info, sensor_info=sensor_info, descriptor=descriptor
        )

        assert output_path.exists()
        assert output_path.name == "fluorometer.csv"

        # Verify CSV contents
        with output_path.open("r") as f:
            reader = csv.DictReader(f)
            rows = list(reader)

        assert len(rows) == 2
        assert rows[0]["local_date"] == "01/15/2023"
        assert rows[0]["local_time"] == "10:30:45.123"

    def test_skip_malformed_lines(self, tmp_path):
        """Test that malformed lines are skipped gracefully."""
        raw_file = tmp_path / "test.Raw"
        raw_content = "01/15/2023,10:30:45.123,\x0001/15/23\t10:30:45\t1.234\t5.678\t9.012\ninvalid line\n01/15/2023\n01/15/2023,10:30:46.456,\x0001/15/23\t10:30:46\t2.345\t6.789\t0.123\n"
        raw_file.write_bytes(raw_content.encode("ascii"))

        file_info = R2RFileInfo(campaign_id="FK161229", platform="Falkor")
        sensor_info = R2RSensorInfo(
            sensor_type="fluorometer", sensor_id="wetlabs-eco-flntu", description="Test"
        )

        descriptor = fluorometer_descriptor_processor(
            data_dir=tmp_path, file_info=file_info, sensor_info=sensor_info, provider_id="r2r"
        )

        output_path = fluorometer_raw_processor(
            data_dir=tmp_path, file_info=file_info, sensor_info=sensor_info, descriptor=descriptor
        )

        # Should still create file with only valid rows
        with output_path.open("r") as f:
            reader = csv.DictReader(f)
            rows = list(reader)

        assert len(rows) == 2  # Only 2 valid rows

    def test_empty_raw_file(self, tmp_path):
        """Test processing empty raw file."""
        raw_file = tmp_path / "test.Raw"
        raw_file.write_text("")

        file_info = R2RFileInfo(campaign_id="FK161229", platform="Falkor")
        sensor_info = R2RSensorInfo(
            sensor_type="fluorometer", sensor_id="wetlabs-eco-flntu", description="Test"
        )

        descriptor = fluorometer_descriptor_processor(
            data_dir=tmp_path, file_info=file_info, sensor_info=sensor_info, provider_id="r2r"
        )

        output_path = fluorometer_raw_processor(
            data_dir=tmp_path, file_info=file_info, sensor_info=sensor_info, descriptor=descriptor
        )

        # Should create file with header only
        with output_path.open("r") as f:
            reader = csv.DictReader(f)
            rows = list(reader)

        assert len(rows) == 0
