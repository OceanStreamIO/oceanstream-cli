"""Unit tests for R2R winch (LCI-90i) processor."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from oceanstream.sensors.processors.lci90i import (
    SENSOR_ID_WINCH,
    SENSOR_TYPE_WINCH,
    LCI90I_PATTERN,
    parse_winch_file,
    winch_descriptor_processor,
    winch_raw_processor,
)
from oceanstream.providers.r2r_metadata import R2RFileInfo, R2RSensorInfo
from oceanstream.sensors.processor_base import SensorDescriptor


# Sample LCI-90i data lines for testing
SAMPLE_LINES = [
    "2022-06-14T06:25:23.876888Z \x1e\x0103RD,2022-05-14T16:17:36.502,-0000168,00000000,-00004.8,2839",
    "2022-06-14T06:25:23.927275Z \x1e\x0103RD,2022-05-14T16:17:36.552,-0000171,00000000,-00004.8,2838",
    "2022-06-14T06:25:23.976730Z \x1e\x0103RD,2022-05-14T16:17:36.602,-0000170,00000000,-00004.8,2833",
]


class TestLCI90IPattern:
    """Tests for the LCI-90i data line regex pattern."""

    def test_pattern_matches_standard_line(self) -> None:
        """Test pattern matches a standard winch data line."""
        line = "2022-06-14T06:25:23.876888Z \x1e\x0103RD,2022-05-14T16:17:36.502,-0000168,00000000,-00004.8,2839"
        match = LCI90I_PATTERN.match(line)
        assert match is not None
        groups = match.groups()
        assert groups[0] == "2022-06-14T06:25:23.876888Z"  # timestamp_logged
        assert groups[1] == "03RD"  # device_id
        assert groups[2] == "2022-05-14T16:17:36.502"  # timestamp_instrument
        assert groups[3] == "-0000168"  # wire_out
        assert groups[4] == "00000000"  # turns
        assert groups[5] == "-00004.8"  # speed
        assert groups[6] == "2839"  # tension

    def test_pattern_matches_negative_values(self) -> None:
        """Test pattern matches line with negative wire_out and speed (typical payout)."""
        line = (
            "2022-06-14T12:00:00Z \x1e\x01WINCH,2022-06-14T12:00:00,-0001234,00000100,-00012.5,5000"
        )
        match = LCI90I_PATTERN.match(line)
        assert match is not None
        groups = match.groups()
        assert groups[3] == "-0001234"  # negative wire_out (payout)
        assert groups[5] == "-00012.5"  # negative speed (paying out)

    def test_pattern_matches_space_separator(self) -> None:
        """Test pattern matches line with just space separator (no control chars)."""
        line = "2022-06-14T06:25:23.876888Z 03RD,2022-05-14T16:17:36.502,-0000168,00000000,-00004.8,2839"
        match = LCI90I_PATTERN.match(line)
        assert match is not None

    def test_pattern_no_match_invalid_line(self) -> None:
        """Test pattern does not match invalid lines."""
        invalid_lines = [
            "",
            "invalid data",
            "2022-06-14 not a valid timestamp",
            "# comment line",
        ]
        for line in invalid_lines:
            assert LCI90I_PATTERN.match(line) is None


class TestParseWinchFile:
    """Tests for the parse_winch_file function."""

    def test_parse_empty_file(self, tmp_path: Path) -> None:
        """Test parsing an empty file returns empty list."""
        empty_file = tmp_path / "empty.txt"
        empty_file.write_text("")
        records = parse_winch_file(empty_file)
        assert records == []

    def test_parse_file_with_valid_data(self, tmp_path: Path) -> None:
        """Test parsing a file with valid winch data."""
        # Create test file with sample data (using control chars)
        test_file = tmp_path / "winch_test"
        content = "\r\n\n".join(SAMPLE_LINES[:2]) + "\r\n\n"
        test_file.write_text(content)

        records = parse_winch_file(test_file)
        assert len(records) == 2

        # Check first record
        r = records[0]
        assert r["time"] == "2022-06-14T06:25:23.876888Z"
        assert r["device_id"] == "03RD"
        assert r["time_instrument"] == "2022-05-14T16:17:36.502"
        assert r["wire_out_m"] == pytest.approx(-0.168, rel=1e-3)
        assert r["turns"] == 0
        assert r["wire_speed_mps"] == pytest.approx(-4.8, rel=1e-3)
        assert r["tension_lbs"] == 2839

    def test_parse_file_skips_invalid_lines(self, tmp_path: Path) -> None:
        """Test that invalid lines are skipped."""
        test_file = tmp_path / "winch_mixed"
        content = (
            "# This is a comment\n"
            "2022-06-14T06:25:23.876888Z \x1e\x0103RD,2022-05-14T16:17:36.502,-0000168,00000000,-00004.8,2839\r\n\n"
            "invalid line\n"
            "2022-06-14T06:25:24.000000Z \x1e\x0103RD,2022-05-14T16:17:37.000,-0000200,00000001,-00005.0,3000\r\n\n"
        )
        test_file.write_text(content)

        records = parse_winch_file(test_file)
        assert len(records) == 2


class TestWinchDescriptorProcessor:
    """Tests for the winch descriptor processor."""

    def test_creates_descriptor_with_defaults(self, tmp_path: Path) -> None:
        """Test descriptor processor creates proper SensorDescriptor."""
        file_info = R2RFileInfo(
            campaign_id="RR2205",
            platform="revelle",
        )
        sensor_info = R2RSensorInfo(
            sensor_type="winch",
            sensor_id="lci90i-rr-trawl",
            description="R/V Revelle trawl winch",
        )

        descriptor = winch_descriptor_processor(tmp_path, file_info, sensor_info, "r2r")

        assert descriptor.sensor_type == SENSOR_TYPE_WINCH
        assert descriptor.sensor_id == SENSOR_ID_WINCH
        assert descriptor.provider_id == "r2r"
        assert descriptor.platform_id == "revelle"
        assert descriptor.campaign_id == "RR2205"
        assert descriptor.description == "R/V Revelle trawl winch"

    def test_uses_sensor_info_sensor_type_if_provided(self, tmp_path: Path) -> None:
        """Test that sensor_info.sensor_type is used if provided."""
        file_info = R2RFileInfo(campaign_id="TEST")
        sensor_info = R2RSensorInfo(sensor_type="custom_winch")

        descriptor = winch_descriptor_processor(tmp_path, file_info, sensor_info, "r2r")

        assert descriptor.sensor_type == "custom_winch"


class TestWinchRawProcessor:
    """Tests for the winch raw data processor."""

    def test_generates_csv_file(self, tmp_path: Path) -> None:
        """Test that raw processor generates a CSV file."""
        # Create test winch data file
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        winch_file = data_dir / "winch_lci90i_test-2022-06-14"
        content = "\r\n\n".join(SAMPLE_LINES) + "\r\n\n"
        winch_file.write_text(content)

        file_info = R2RFileInfo(campaign_id="TEST", platform="test_vessel")
        sensor_info = R2RSensorInfo(sensor_type="winch", sensor_id="lci90i-winch")
        descriptor = SensorDescriptor(
            sensor_type=SENSOR_TYPE_WINCH,
            sensor_id=SENSOR_ID_WINCH,
            provider_id="r2r",
            platform_id="test_vessel",
            campaign_id="TEST",
            description="Test winch",
            metadata={},
        )

        csv_path = winch_raw_processor(data_dir, file_info, sensor_info, descriptor)

        assert csv_path.exists()
        assert csv_path.name == "winch.csv"

        # Read and verify CSV content
        lines = csv_path.read_text().strip().split("\n")
        assert len(lines) == 4  # header + 3 data rows

        # Check header
        header = lines[0]
        assert "time" in header
        assert "device_id" in header
        assert "wire_out_m" in header
        assert "tension_lbs" in header

    def test_handles_empty_directory(self, tmp_path: Path) -> None:
        """Test processor handles directory with no winch files."""
        data_dir = tmp_path / "empty_data"
        data_dir.mkdir()

        file_info = R2RFileInfo(campaign_id="TEST")
        sensor_info = R2RSensorInfo(sensor_type="winch")
        descriptor = SensorDescriptor(
            sensor_type=SENSOR_TYPE_WINCH,
            sensor_id=SENSOR_ID_WINCH,
            provider_id="r2r",
            platform_id="test",
            campaign_id="TEST",
            description="Test",
            metadata={},
        )

        csv_path = winch_raw_processor(data_dir, file_info, sensor_info, descriptor)

        assert csv_path.exists()
        # Should have just header
        lines = csv_path.read_text().strip().split("\n")
        assert len(lines) == 1  # header only


class TestWinchProcessorRegistration:
    """Tests for processor registration."""

    def test_sensor_processor_is_registered(self) -> None:
        """Test that winch sensor processor is registered."""
        from oceanstream.sensors.processors import get_sensor_processor

        processor = get_sensor_processor(SENSOR_TYPE_WINCH)
        assert processor is not None
        assert processor is winch_descriptor_processor

    def test_raw_processor_is_registered(self) -> None:
        """Test that winch raw processor is registered."""
        from oceanstream.sensors.processors import get_raw_processor

        processor = get_raw_processor(SENSOR_ID_WINCH)
        assert processor is not None
        assert processor is winch_raw_processor
