"""Integration tests for R2R winch (LCI-90i) processor.

These tests verify that the winch processor works end-to-end with
real archive data from R2R.
"""

from __future__ import annotations

import csv
import tarfile
from pathlib import Path

import pytest


# Skip all tests if real winch data is not available
WINCH_ARCHIVE = (
    Path(__file__).parent.parent.parent.parent / "raw_data" / "r2r" / "RR2205_151265_winch.tar.gz"
)


@pytest.fixture(scope="module")
def extracted_winch_data(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Extract a single day of winch data for testing.

    Returns the directory containing the extracted winch file.
    """
    if not WINCH_ARCHIVE.exists():
        pytest.skip(f"Winch archive not found: {WINCH_ARCHIVE}")

    work_dir = tmp_path_factory.mktemp("winch_test")

    # Extract just one day's data file
    with tarfile.open(WINCH_ARCHIVE, "r:gz") as tf:
        # Find and extract the data file
        for member in tf.getmembers():
            if "winch_lci90i_rr_trawl-2022-06-14" in member.name:
                member.name = Path(member.name).name  # Strip directory structure
                tf.extract(member, work_dir)
                break

    return work_dir


@pytest.mark.skipif(
    not WINCH_ARCHIVE.exists(), reason=f"Winch archive not available: {WINCH_ARCHIVE}"
)
class TestWinchIntegration:
    """Integration tests for winch processor with real R2R data."""

    def test_parse_winch_file_large(self, extracted_winch_data: Path) -> None:
        """Test parsing a full day's winch data file."""
        from oceanstream.sensors.processors.lci90i import parse_winch_file

        winch_file = extracted_winch_data / "winch_lci90i_rr_trawl-2022-06-14"
        assert winch_file.exists(), f"Winch file not found: {winch_file}"

        records = parse_winch_file(winch_file)

        # Should have over 1 million records for a full day at ~20Hz
        assert len(records) > 1_000_000

        # Verify record structure
        first = records[0]
        assert "time" in first
        assert "device_id" in first
        assert "wire_out_m" in first
        assert "tension_lbs" in first
        assert "wire_speed_mps" in first

        # Verify timestamps are parseable
        from datetime import datetime

        time_str = first["time"]
        # Handle both with and without 'Z' suffix
        if time_str.endswith("Z"):
            time_str = time_str[:-1]
        datetime.fromisoformat(time_str)

    def test_raw_processor_generates_csv(self, extracted_winch_data: Path) -> None:
        """Test that the raw processor generates a valid CSV file."""
        from oceanstream.sensors.processors.lci90i import (
            winch_raw_processor,
            SENSOR_TYPE_WINCH,
            SENSOR_ID_WINCH,
        )
        from oceanstream.providers.r2r_metadata import R2RFileInfo, R2RSensorInfo
        from oceanstream.sensors.processor_base import SensorDescriptor

        file_info = R2RFileInfo(
            campaign_id="RR2205",
            platform="revelle",
        )
        sensor_info = R2RSensorInfo(
            sensor_type="winch",
            sensor_id="lci90i-rr-trawl",
            description="R/V Revelle trawl winch",
        )
        descriptor = SensorDescriptor(
            sensor_type=SENSOR_TYPE_WINCH,
            sensor_id=SENSOR_ID_WINCH,
            provider_id="r2r",
            platform_id="revelle",
            campaign_id="RR2205",
            description="R/V Revelle trawl winch",
            metadata={},
        )

        csv_path = winch_raw_processor(extracted_winch_data, file_info, sensor_info, descriptor)

        assert csv_path.exists()
        assert csv_path.name == "winch.csv"

        # Verify CSV is readable and has correct structure
        with csv_path.open() as f:
            reader = csv.DictReader(f)
            headers = reader.fieldnames

            assert headers is not None
            assert "time" in headers
            assert "device_id" in headers
            assert "wire_out_m" in headers
            assert "tension_lbs" in headers
            assert "wire_speed_mps" in headers

            # Read first row to verify data
            first_row = next(reader)
            assert first_row["device_id"] == "03RD"
            assert float(first_row["tension_lbs"]) > 0

    def test_winch_data_values_reasonable(self, extracted_winch_data: Path) -> None:
        """Test that parsed winch data values are within reasonable ranges."""
        from oceanstream.sensors.processors.lci90i import parse_winch_file

        winch_file = extracted_winch_data / "winch_lci90i_rr_trawl-2022-06-14"
        records = parse_winch_file(winch_file)

        # Check a sample of records for reasonable values
        import random

        sample_size = min(1000, len(records))
        sample = random.sample(records, sample_size)

        for r in sample:
            # Wire out should be reasonable (within ±10km for deep sea winches)
            assert -10000 < r["wire_out_m"] < 10000

            # Tension should be positive and reasonable (0-50000 lbs for typical winches)
            assert 0 <= r["tension_lbs"] < 50000

            # Speed should be reasonable (±100 m/s max)
            assert -100 < r["wire_speed_mps"] < 100


@pytest.mark.skipif(
    not WINCH_ARCHIVE.exists(), reason=f"Winch archive not available: {WINCH_ARCHIVE}"
)
class TestWinchArchiveDetection:
    """Test R2R archive detection for winch data."""

    def test_bag_info_sensor_type_detection(self, tmp_path: Path) -> None:
        """Test that sensor type is correctly detected from bag-info.txt."""
        from oceanstream.providers.r2r.r2r_metadata import parse_bag_info

        # Create a mock bag-info.txt with R2R-DeviceType field
        bag_info = tmp_path / "bag-info.txt"
        bag_info.write_text(
            "R2R-DeviceType: winch\n"
            "R2R-DeviceModel: Markey DUTW-9-11\n"
            "Internal-Sender-Description: Fileset 151265 (winch data) from cruise RR2205\n"
        )

        sensor_info = parse_bag_info(bag_info)

        assert sensor_info.sensor_type == "winch"
        assert sensor_info.sensor_id == "Markey DUTW-9-11"
        assert sensor_info.description is not None
        assert "winch" in sensor_info.description.lower()
