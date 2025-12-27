"""Unit tests for processor utility functions.

Tests for utility functions in processor.py that aren't covered by integration tests.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch, Mock
import pandas as pd
import pytest

from oceanstream.geotrack.processor import (
    _format_file_size,
    _display_files_summary,
    _is_nmea_file,
    _group_files_by_campaign_id,
)


class TestFormatFileSize:
    """Test file size formatting."""
    
    def test_bytes(self):
        """Test formatting bytes."""
        assert _format_file_size(500) == "500.0 B"
    
    def test_kilobytes(self):
        """Test formatting KB."""
        assert _format_file_size(1536) == "1.5 KB"
    
    def test_megabytes(self):
        """Test formatting MB."""
        assert _format_file_size(5 * 1024 * 1024) == "5.0 MB"
    
    def test_gigabytes(self):
        """Test formatting GB."""
        assert _format_file_size(2 * 1024 * 1024 * 1024) == "2.0 GB"
    
    def test_terabytes(self):
        """Test formatting TB."""
        assert _format_file_size(1024 * 1024 * 1024 * 1024) == "1.0 TB"
    
    def test_petabytes(self):
        """Test formatting PB."""
        assert _format_file_size(1024 * 1024 * 1024 * 1024 * 1024) == "1.0 PB"


class TestDisplayFilesSummary:
    """Test interactive file summary display."""
    
    def test_single_file_accept(self, tmp_path: Path):
        """Test accepting single file confirmation."""
        test_file = tmp_path / "test.csv"
        test_file.write_text("data")
        
        # Mock user input - accept
        with patch('builtins.input', return_value='y'):
            result = _display_files_summary(tmp_path, [test_file])
        
        assert result is True
    
    def test_single_file_reject(self, tmp_path: Path):
        """Test rejecting single file confirmation."""
        test_file = tmp_path / "test.csv"
        test_file.write_text("data")
        
        # Mock user input - reject
        with patch('builtins.input', return_value='n'):
            result = _display_files_summary(tmp_path, [test_file])
        
        assert result is False
    
    def test_multiple_files_empty_input_accepts(self, tmp_path: Path):
        """Test that empty input (just Enter) accepts."""
        files = [tmp_path / f"file{i}.csv" for i in range(3)]
        for f in files:
            f.write_text("data")
        
        # Mock user input - empty (just Enter)
        with patch('builtins.input', return_value=''):
            result = _display_files_summary(tmp_path, files)
        
        assert result is True
    
    def test_keyboard_interrupt(self, tmp_path: Path):
        """Test handling keyboard interrupt (Ctrl+C)."""
        test_file = tmp_path / "test.csv"
        test_file.write_text("data")
        
        # Mock user input - KeyboardInterrupt
        with patch('builtins.input', side_effect=KeyboardInterrupt):
            result = _display_files_summary(tmp_path, [test_file])
        
        assert result is False
    
    def test_eof_error(self, tmp_path: Path):
        """Test handling EOF error (Ctrl+D or piped input)."""
        test_file = tmp_path / "test.csv"
        test_file.write_text("data")
        
        # Mock user input - EOFError
        with patch('builtins.input', side_effect=EOFError):
            result = _display_files_summary(tmp_path, [test_file])
        
        assert result is False
    
    def test_case_insensitive_yes(self, tmp_path: Path):
        """Test that 'yes', 'YES', 'Yes' all work."""
        test_file = tmp_path / "test.csv"
        test_file.write_text("data")
        
        for response in ['yes', 'YES', 'Yes', 'Y']:
            with patch('builtins.input', return_value=response):
                result = _display_files_summary(tmp_path, [test_file])
                assert result is True
    
    def test_case_insensitive_no(self, tmp_path: Path):
        """Test that 'no', 'NO', 'No', 'n' all work."""
        test_file = tmp_path / "test.csv"
        test_file.write_text("data")
        
        for response in ['no', 'NO', 'No', 'N']:
            with patch('builtins.input', return_value=response):
                result = _display_files_summary(tmp_path, [test_file])
                assert result is False
    
    def test_missing_file_size(self, tmp_path: Path):
        """Test handling files that can't be sized (OSError)."""
        test_file = tmp_path / "test.csv"
        # Don't create the file - it will trigger OSError
        
        with patch('builtins.input', return_value='y'):
            # Should not crash, should handle OSError gracefully
            result = _display_files_summary(tmp_path, [test_file])
        
        assert result is True


class TestIsNmeaFile:
    """Test NMEA file detection."""
    
    def test_valid_nmea_file_with_timestamp(self, tmp_path: Path):
        """Test detecting NMEA file with ISO8601 timestamp prefix."""
        nmea_file = tmp_path / "gps.txt"
        nmea_file.write_text("2023-06-01T12:00:00Z $GPGGA,120000,3751.123,N,12223.456,W,1,08,0.9,545.4,M,46.9,M,,*47\n")
        
        assert _is_nmea_file(nmea_file) is True
    
    def test_valid_nmea_file_without_timestamp(self, tmp_path: Path):
        """Test detecting NMEA file without timestamp."""
        nmea_file = tmp_path / "gps.txt"
        nmea_file.write_text("$GPGGA,120000,3751.123,N,12223.456,W,1,08,0.9,545.4,M,46.9,M,,*47\n")
        
        assert _is_nmea_file(nmea_file) is True
    
    def test_nmea_file_with_blank_lines(self, tmp_path: Path):
        """Test detecting NMEA file with leading blank lines."""
        nmea_file = tmp_path / "gps.txt"
        nmea_file.write_text("\n\n\n$GPGGA,120000,3751.123,N,12223.456,W,1,08,0.9,545.4,M,46.9,M,,*47\n")
        
        assert _is_nmea_file(nmea_file) is True
    
    def test_non_nmea_file(self, tmp_path: Path):
        """Test rejecting non-NMEA text file."""
        text_file = tmp_path / "notes.txt"
        text_file.write_text("This is just a regular text file with no NMEA data.\n")
        
        assert _is_nmea_file(text_file) is False
    
    def test_csv_file(self, tmp_path: Path):
        """Test rejecting CSV file."""
        csv_file = tmp_path / "data.txt"
        csv_file.write_text("latitude,longitude,time\n37.5,-122.5,2023-06-01T12:00:00Z\n")
        
        assert _is_nmea_file(csv_file) is False
    
    def test_empty_file(self, tmp_path: Path):
        """Test handling empty file."""
        empty_file = tmp_path / "empty.txt"
        empty_file.write_text("")
        
        assert _is_nmea_file(empty_file) is False
    
    def test_file_read_error(self, tmp_path: Path):
        """Test handling file read errors."""
        # Create a file path that doesn't exist
        nonexistent = tmp_path / "nonexistent.txt"
        
        # Should not crash, should return False
        assert _is_nmea_file(nonexistent) is False
    
    def test_nmea_rmc_sentence(self, tmp_path: Path):
        """Test detecting RMC NMEA sentence."""
        nmea_file = tmp_path / "gps.txt"
        nmea_file.write_text("$GPRMC,120000,A,3751.123,N,12223.456,W,0.0,0.0,010623,,,A*6B\n")
        
        assert _is_nmea_file(nmea_file) is True
    
    def test_false_positive_dollar_sign(self, tmp_path: Path):
        """Test rejecting file with $ but not NMEA format."""
        text_file = tmp_path / "prices.txt"
        text_file.write_text("The price is $50 for this item\n$100 for that item\n")
        
        # Should return False because $ is not followed by 2+ letters
        assert _is_nmea_file(text_file) is False


class TestGroupFilesByCampaignId:
    """Test campaign ID grouping logic."""
    
    def test_single_campaign_explicit(self, tmp_path: Path):
        """Test with user-supplied campaign_id - all files in one group."""
        files = [
            tmp_path / "sd1030_file1.csv",
            tmp_path / "sd1033_file2.csv",
            tmp_path / "sd1079_file3.csv",
        ]
        for f in files:
            f.touch()
        
        mock_provider = Mock()
        result = _group_files_by_campaign_id(files, mock_provider, user_campaign_id="my_campaign", verbose=False)
        
        assert len(result) == 1
        assert "my_campaign" in result
        assert len(result["my_campaign"]) == 3
    
    def test_multiple_campaigns_auto_detect(self, tmp_path: Path):
        """Test auto-detecting multiple campaigns from filenames."""
        files = [
            tmp_path / "sd1030_tpos_2023_abc.csv",
            tmp_path / "sd1030_tpos_2023_def.csv",
            tmp_path / "sd1033_arctic_2024_xyz.csv",
        ]
        for f in files:
            f.touch()
        
        # Mock provider that returns platform_id based on filename
        mock_provider = Mock()
        def identify_platform(filename: str) -> str | None:
            if "sd1030" in filename:
                return "sd1030_tpos_2023"
            elif "sd1033" in filename:
                return "sd1033_arctic_2024"
            return None
        
        mock_provider.identify_platform = identify_platform
        
        result = _group_files_by_campaign_id(files, mock_provider, user_campaign_id=None, verbose=False)
        
        assert len(result) == 2
        assert "sd1030_tpos_2023" in result
        assert "sd1033_arctic_2024" in result
        assert len(result["sd1030_tpos_2023"]) == 2
        assert len(result["sd1033_arctic_2024"]) == 1
    
    def test_fallback_to_filename_stem(self, tmp_path: Path):
        """Test fallback when campaign_id cannot be detected."""
        files = [
            tmp_path / "unknown_format_123.csv",
        ]
        for f in files:
            f.touch()
        
        # Mock provider that can't identify platform
        mock_provider = Mock()
        mock_provider.identify_platform = Mock(return_value=None)
        
        result = _group_files_by_campaign_id(files, mock_provider, user_campaign_id=None, verbose=False)
        
        assert len(result) == 1
        # extract_platform_id returns second underscore-separated part
        assert "format" in result
    
    def test_mixed_detection(self, tmp_path: Path):
        """Test mix of provider detection and fallback."""
        files = [
            tmp_path / "sd1030_file1.csv",
            tmp_path / "sd1030_file2.csv",
            tmp_path / "unknown_file.csv",
        ]
        for f in files:
            f.touch()
        
        # Mock provider that only identifies sd1030
        mock_provider = Mock()
        def identify_platform(filename: str) -> str | None:
            if "sd1030" in filename:
                return "sd1030"
            return None
        
        mock_provider.identify_platform = identify_platform
        
        result = _group_files_by_campaign_id(files, mock_provider, user_campaign_id=None, verbose=False)
        
        assert len(result) == 2
        assert "sd1030" in result
        # extract_platform_id returns second underscore-separated part
        assert "file" in result
        assert len(result["sd1030"]) == 2
        assert len(result["file"]) == 1
