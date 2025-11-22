"""Unit tests for NMEA GNSS raw processor."""

from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path

import pytest

from oceanstream.sensors.processors.nmea_gnss import (
    parse_nmea_line,
    process_nmea_raw,
)


@pytest.fixture
def sample_nmea_data(tmp_path: Path) -> Path:
    """Create a sample NMEA data file for testing."""
    nmea_file = tmp_path / "test_nmea.txt"
    
    # Sample NMEA sentences with ISO8601 timestamp prefix
    content = """2024-02-17T00:00:00.000000Z $GPGGA,000000.00,3242.39160,N,11714.16410,W,1,10,0.8,10.0,M,,,*33
2024-02-17T00:00:00.000000Z $GPRMC,000000.00,A,3242.39160,N,11714.16410,W,0.0,0.0,170224,,,A*44
2024-02-17T00:00:00.000000Z $GPGNS,000000.00,3242.39160,N,11714.16410,W,A,10,0.8,10.0,,,*39
2024-02-17T00:00:00.000000Z $GPVTG,0.0,T,,M,0.0,N,0.0,K,A*0D
2024-02-17T00:00:00.000000Z $GPZDA,000000.00,17,02,2024,00,00*66
2024-02-17T00:00:05.000000Z $GPGGA,000005.00,3242.39200,N,11714.16500,W,1,10,0.8,9.5,M,,,*0E
2024-02-17T00:00:05.000000Z $GPRMC,000005.00,A,3242.39200,N,11714.16500,W,0.5,45.0,170224,,,A*70
2024-02-17T00:00:10.000000Z $GPGGA,000010.00,3242.39300,N,11714.16600,W,1,09,0.9,8.0,M,,,*05
"""
    nmea_file.write_text(content)
    return nmea_file


@pytest.fixture
def malformed_nmea_data(tmp_path: Path) -> Path:
    """Create a NMEA file with malformed sentences for error handling tests."""
    nmea_file = tmp_path / "malformed_nmea.txt"
    
    content = """2024-02-17T00:00:00.000000Z $GPGGA,000000.00,3242.39160,N,11714.16410,W,1,10,0.8,10.0,M,,,*33
2024-02-17T00:00:01.000000Z $GPXXX,invalid,sentence,here*09
2024-02-17T00:00:02.000000Z This is not an NMEA sentence
2024-02-17T00:00:03.000000Z $GPGGA,corrupt
2024-02-17T00:00:05.000000Z $GPRMC,000005.00,A,3242.39200,N,11714.16500,W,0.5,45.0,170224,,,A*5C
"""
    nmea_file.write_text(content)
    return nmea_file


class TestParseNmeaLine:
    """Tests for parse_nmea_line function."""
    
    def test_parse_gga_sentence(self):
        """Test parsing GGA sentence."""
        line = "2024-02-17T00:00:00.000000Z $GPGGA,000000.00,3242.39160,N,11714.16410,W,1,10,0.8,10.0,M,,,*33"
        data = parse_nmea_line(line)
        
        assert data is not None
        assert "timestamp" in data
        assert data["timestamp"] == datetime.fromisoformat("2024-02-17T00:00:00.000000+00:00")
        assert "latitude" in data
        assert "longitude" in data
        assert abs(data["latitude"] - 32.706527) < 0.0001
        assert abs(data["longitude"] - (-117.236068)) < 0.0001
        assert data["gps_quality"] == 1
        assert data["num_satellites"] == 10
        assert data["horizontal_dilution"] == 0.8
        assert data["gps_antenna_height"] == 10.0
    
    def test_parse_rmc_sentence(self):
        """Test parsing RMC sentence."""
        line = "2024-02-17T00:00:00.000000Z $GPRMC,000000.00,A,3242.39160,N,11714.16410,W,0.5,45.0,170224,,,A*70"
        data = parse_nmea_line(line)
        
        assert data is not None
        assert data["timestamp"] == datetime.fromisoformat("2024-02-17T00:00:00.000000+00:00")
        assert "latitude" in data
        assert "longitude" in data
        assert abs(data["latitude"] - 32.706527) < 0.0001
        assert abs(data["longitude"] - (-117.236068)) < 0.0001
        # RMC speed is in knots, converted to m/s
        assert "speed_over_ground" in data
        assert data["course_over_ground"] == 45.0
    
    def test_parse_gns_sentence(self):
        """Test parsing GNS sentence."""
        line = "2024-02-17T00:00:00.000000Z $GPGNS,000000.00,3242.39160,N,11714.16410,W,A,10,0.8,10.0,,,*39"
        data = parse_nmea_line(line)
        
        assert data is not None
        assert data["timestamp"] == datetime.fromisoformat("2024-02-17T00:00:00.000000+00:00")
        assert "latitude" in data
        assert "longitude" in data
        assert data["num_satellites"] == 10
        assert data["horizontal_dilution"] == 0.8
        assert data["gps_antenna_height"] == 10.0
    
    def test_parse_vtg_sentence(self):
        """Test parsing VTG sentence."""
        line = "2024-02-17T00:00:00.000000Z $GPVTG,45.0,T,,M,0.5,N,0.926,K,A*34"
        data = parse_nmea_line(line)
        
        assert data is not None
        assert data["timestamp"] == datetime.fromisoformat("2024-02-17T00:00:00.000000+00:00")
        assert "course_over_ground" in data
        assert "speed_over_ground" in data
        assert data["course_over_ground"] == 45.0
    
    def test_parse_zda_sentence(self):
        """Test parsing ZDA sentence."""
        line = "2024-02-17T00:00:00.000000Z $GPZDA,000000.00,17,02,2024,00,00*66"
        data = parse_nmea_line(line)
        
        assert data is not None
        assert data["timestamp"] == datetime.fromisoformat("2024-02-17T00:00:00.000000+00:00")
        assert "gps_utc_time" in data
        # ZDA should extract GPS time
        assert "2024-02-17" in data["gps_utc_time"]
    
    def test_parse_malformed_sentence(self):
        """Test parsing malformed NMEA sentence."""
        line = "2024-02-17T00:00:00.000000Z $GPXXX,invalid,sentence*3F"
        data = parse_nmea_line(line)
        
        # Should return None for unrecognized sentence
        assert data is None
    
    def test_parse_corrupt_sentence(self):
        """Test parsing corrupt NMEA sentence."""
        line = "2024-02-17T00:00:00.000000Z $GPGGA,corrupt"
        data = parse_nmea_line(line)
        
        # pynmea2 is lenient - may parse with default values or return data
        # The sentence has a timestamp but incomplete fields
        assert data is not None  # Parser is lenient
        assert "timestamp" in data
        assert data["timestamp"] == datetime.fromisoformat("2024-02-17T00:00:00.000000+00:00")
    
    def test_parse_non_nmea_line(self):
        """Test parsing non-NMEA line."""
        line = "2024-02-17T00:00:00.000000Z This is not an NMEA sentence"
        data = parse_nmea_line(line)
        
        # Should return None for non-NMEA content
        assert data is None
    
    def test_parse_line_without_timestamp(self):
        """Test parsing line without ISO8601 timestamp prefix."""
        line = "$GPGGA,000000.00,3242.39160,N,11714.16410,W,1,10,0.8,10.0,M,,,*67"
        data = parse_nmea_line(line)
        
        # Should return None - missing timestamp
        assert data is None
    
    def test_coordinate_conversion_north_west(self):
        """Test coordinate conversion for North/West coordinates."""
        line = "2024-02-17T00:00:00.000000Z $GPGGA,000000.00,3242.39160,N,11714.16410,W,1,10,0.8,10.0,M,,,*33"
        data = parse_nmea_line(line)
        
        assert data is not None
        assert data["latitude"] > 0  # North is positive
        assert data["longitude"] < 0  # West is negative
    
    def test_coordinate_conversion_south_east(self):
        """Test coordinate conversion for South/East coordinates."""
        line = "2024-02-17T00:00:00.000000Z $GPGGA,000000.00,3242.39160,S,11714.16410,E,1,10,0.8,10.0,M,,,*3C"
        data = parse_nmea_line(line)
        
        assert data is not None
        assert data["latitude"] < 0  # South is negative
        assert data["longitude"] > 0  # East is positive


class TestProcessNmeaRaw:
    """Tests for process_nmea_raw function."""
    
    def test_process_basic_file(self, sample_nmea_data: Path, tmp_path: Path):
        """Test processing a basic NMEA file."""
        output_file = tmp_path / "output.csv"
        
        stats = process_nmea_raw(sample_nmea_data, output_file)
        
        assert output_file.exists()
        assert stats["lines_read"] == 8
        assert stats["lines_parsed"] > 0
        assert stats["data_points_written"] > 0
        assert stats["sampling_interval"] is None
        assert stats["decimation_ratio"] == 1.0
    
    def test_process_with_sentence_filtering(self, sample_nmea_data: Path, tmp_path: Path):
        """Test processing with specific sentence types."""
        output_file = tmp_path / "output.csv"
        
        # Only process GGA sentences
        stats = process_nmea_raw(sample_nmea_data, output_file, sentence_types=["GGA"])
        
        assert output_file.exists()
        assert stats["lines_parsed"] > 0
    
    def test_process_with_sampling(self, sample_nmea_data: Path, tmp_path: Path):
        """Test processing with sampling interval."""
        output_file = tmp_path / "output.csv"
        
        # Sample at 10 second intervals
        stats = process_nmea_raw(sample_nmea_data, output_file, sampling_interval=10.0)
        
        assert output_file.exists()
        assert stats["sampling_interval"] == 10.0
        # With small sample data (5 second span), may not see decimation
        assert stats["decimation_ratio"] <= 1.0
        assert stats["data_points_written"] <= stats["data_points_merged"]
    
    def test_csv_output_format(self, sample_nmea_data: Path, tmp_path: Path):
        """Test that CSV output has correct format and columns."""
        output_file = tmp_path / "output.csv"
        
        process_nmea_raw(sample_nmea_data, output_file)
        
        # Read and verify CSV structure
        with open(output_file, "r") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
            
            assert len(rows) > 0
            
            # Check expected columns exist
            expected_columns = [
                "time",
                "latitude",
                "longitude",
                "gps_quality",
                "num_satellites",
                "horizontal_dilution",
                "gps_antenna_height",
                "speed_over_ground",
                "course_over_ground",
                "gps_utc_time",
            ]
            
            for col in expected_columns:
                assert col in reader.fieldnames
            
            # Check data types and ranges
            for row in rows:
                # Time should be ISO8601
                assert "T" in row["time"]
                
                # Latitude/longitude in valid ranges
                if row["latitude"]:
                    lat = float(row["latitude"])
                    assert -90 <= lat <= 90
                
                if row["longitude"]:
                    lon = float(row["longitude"])
                    assert -180 <= lon <= 180
    
    def test_process_malformed_file(self, malformed_nmea_data: Path, tmp_path: Path):
        """Test processing file with malformed sentences."""
        output_file = tmp_path / "output.csv"
        
        # Should not raise exception, just skip bad lines
        stats = process_nmea_raw(malformed_nmea_data, output_file)
        
        assert output_file.exists()
        assert stats["lines_read"] == 5
        # Should parse at least 1 valid sentence (may be more depending on which succeed)
        assert stats["lines_parsed"] >= 1
    
    def test_data_merging_by_timestamp(self, tmp_path: Path):
        """Test that data from multiple sentence types is merged by timestamp."""
        nmea_file = tmp_path / "test_merge.txt"
        
        # Same timestamp, different sentence types
        content = """2024-02-17T00:00:00.000000Z $GPGGA,000000.00,3242.39160,N,11714.16410,W,1,10,0.8,10.0,M,,,*33
2024-02-17T00:00:00.000000Z $GPRMC,000000.00,A,3242.39160,N,11714.16410,W,0.5,45.0,170224,,,A*70
2024-02-17T00:00:00.000000Z $GPVTG,45.0,T,,M,0.5,N,0.926,K,A*34
"""
        nmea_file.write_text(content)
        
        output_file = tmp_path / "output.csv"
        stats = process_nmea_raw(nmea_file, output_file)
        
        # Should merge into single row
        with open(output_file, "r") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
            
            assert len(rows) == 1
            row = rows[0]
            
            # Should have data from GGA
            assert row["latitude"]
            assert row["gps_quality"]
            
            # Should have data from RMC/VTG
            assert row["speed_over_ground"]
            assert row["course_over_ground"]
    
    def test_empty_file(self, tmp_path: Path):
        """Test processing empty file."""
        empty_file = tmp_path / "empty.txt"
        empty_file.write_text("")
        
        output_file = tmp_path / "output.csv"
        
        # Should raise ValueError when no valid NMEA data
        with pytest.raises(ValueError, match="No valid NMEA data found"):
            process_nmea_raw(empty_file, output_file)
    
    def test_sampling_with_irregular_timestamps(self, tmp_path: Path):
        """Test sampling with irregular timestamp gaps."""
        nmea_file = tmp_path / "irregular.txt"
        
        # Irregular gaps: 0s, 1s, 15s, 16s, 30s
        content = """2024-02-17T00:00:00.000000Z $GPGGA,000000.00,3242.39160,N,11714.16410,W,1,10,0.8,10.0,M,,,*33
2024-02-17T00:00:01.000000Z $GPGGA,000001.00,3242.39160,N,11714.16410,W,1,10,0.8,10.0,M,,,*32
2024-02-17T00:00:15.000000Z $GPGGA,000015.00,3242.39160,N,11714.16410,W,1,10,0.8,10.0,M,,,*37
2024-02-17T00:00:16.000000Z $GPGGA,000016.00,3242.39160,N,11714.16410,W,1,10,0.8,10.0,M,,,*34
2024-02-17T00:00:30.000000Z $GPGGA,000030.00,3242.39160,N,11714.16410,W,1,10,0.8,10.0,M,,,*30
"""
        nmea_file.write_text(content)
        
        output_file = tmp_path / "output.csv"
        stats = process_nmea_raw(nmea_file, output_file, sampling_interval=10.0)
        
        assert output_file.exists()
        # With 10s interval, should get ~3 buckets
        assert stats["data_points_written"] <= 3
    
    def test_output_directory_creation(self, sample_nmea_data: Path, tmp_path: Path):
        """Test that output directory is created if it doesn't exist."""
        output_file = tmp_path / "subdir" / "output.csv"
        
        # Directory doesn't exist yet
        assert not output_file.parent.exists()
        
        process_nmea_raw(sample_nmea_data, output_file)
        
        # Should create directory
        assert output_file.parent.exists()
        assert output_file.exists()
    
    def test_statistics_accuracy(self, sample_nmea_data: Path, tmp_path: Path):
        """Test that returned statistics are accurate."""
        output_file = tmp_path / "output.csv"
        
        stats = process_nmea_raw(sample_nmea_data, output_file)
        
        # Count actual lines in input
        with open(sample_nmea_data, "r") as f:
            actual_lines = len(f.readlines())
        
        assert stats["lines_read"] == actual_lines
        
        # Count actual rows in output
        with open(output_file, "r") as f:
            reader = csv.DictReader(f)
            actual_rows = len(list(reader))
        
        assert stats["data_points_written"] == actual_rows
    
    def test_sampling_zero_interval(self, sample_nmea_data: Path, tmp_path: Path):
        """Test that zero sampling interval is ignored."""
        output_file = tmp_path / "output.csv"
        
        stats = process_nmea_raw(sample_nmea_data, output_file, sampling_interval=0.0)
        
        # Should process all data (no sampling)
        assert stats["decimation_ratio"] == 1.0
    
    def test_sampling_negative_interval(self, sample_nmea_data: Path, tmp_path: Path):
        """Test that negative sampling interval is ignored."""
        output_file = tmp_path / "output.csv"
        
        stats = process_nmea_raw(sample_nmea_data, output_file, sampling_interval=-10.0)
        
        # Should process all data (no sampling)
        assert stats["decimation_ratio"] == 1.0


class TestEdgeCases:
    """Tests for edge cases and boundary conditions."""
    
    def test_single_sentence_file(self, tmp_path: Path):
        """Test file with only one sentence."""
        nmea_file = tmp_path / "single.txt"
        nmea_file.write_text(
            "2024-02-17T00:00:00.000000Z $GPGGA,000000.00,3242.39160,N,11714.16410,W,1,10,0.8,10.0,M,,,*33\n"
        )
        
        output_file = tmp_path / "output.csv"
        stats = process_nmea_raw(nmea_file, output_file)
        
        assert stats["lines_read"] == 1
        assert stats["lines_parsed"] == 1
        assert stats["data_points_written"] == 1
    
    def test_only_unsupported_sentences(self, tmp_path: Path):
        """Test file with only unsupported sentence types."""
        nmea_file = tmp_path / "unsupported.txt"
        content = """2024-02-17T00:00:00.000000Z $GPGLL,3242.39160,N,11714.16410,W,000000.00,A*1C
2024-02-17T00:00:01.000000Z $GPDTM,W84,,0.0,N,0.0,E,0.0,W84*6F
"""
        nmea_file.write_text(content)
        
        output_file = tmp_path / "output.csv"
        
        # Should raise ValueError when no supported sentences found
        with pytest.raises(ValueError, match="No valid NMEA data found"):
            process_nmea_raw(nmea_file, output_file)
    
    def test_gps_quality_values(self, tmp_path: Path):
        """Test different GPS quality indicator values."""
        nmea_file = tmp_path / "quality.txt"
        content = """2024-02-17T00:00:00.000000Z $GPGGA,000000.00,3242.39160,N,11714.16410,W,0,05,2.5,10.0,M,,,*39
2024-02-17T00:00:01.000000Z $GPGGA,000001.00,3242.39160,N,11714.16410,W,1,10,0.8,10.0,M,,,*32
2024-02-17T00:00:02.000000Z $GPGGA,000002.00,3242.39160,N,11714.16410,W,2,12,0.6,10.0,M,,,*3E
"""
        nmea_file.write_text(content)
        
        output_file = tmp_path / "output.csv"
        process_nmea_raw(nmea_file, output_file)
        
        with open(output_file, "r") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
            
            assert len(rows) == 3
            # Quality values: 0=invalid, 1=GPS fix, 2=DGPS fix
            qualities = [int(row["gps_quality"]) for row in rows if row["gps_quality"]]
            assert 0 in qualities
            assert 1 in qualities
            assert 2 in qualities
    
    def test_missing_optional_fields(self, tmp_path: Path):
        """Test sentences with missing optional fields."""
        nmea_file = tmp_path / "missing.txt"
        # GGA with minimal fields
        content = "2024-02-17T00:00:00.000000Z $GPGGA,000000.00,3242.39160,N,11714.16410,W,1,10,0.8,10.0,M,,,*33\n"
        nmea_file.write_text(content)
        
        output_file = tmp_path / "output.csv"
        stats = process_nmea_raw(nmea_file, output_file)
        
        assert stats["data_points_written"] == 1
        
        with open(output_file, "r") as f:
            reader = csv.DictReader(f)
            row = next(reader)
            
            # Should have required fields
            assert row["latitude"]
            assert row["longitude"]
            
            # Optional fields might be empty
            # But CSV should still be valid
            assert "speed_over_ground" in row
            assert "course_over_ground" in row
