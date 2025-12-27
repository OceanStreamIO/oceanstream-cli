"""Unit tests for archive extraction functionality."""

from __future__ import annotations

import tarfile
from pathlib import Path

import pytest

from oceanstream.geotrack.processor import (
    _extract_archive,
    _find_data_files_in_archive,
    _is_tar_gz_archive,
)


class TestArchiveDetection:
    """Test archive file detection."""
    
    def test_is_tar_gz_archive_valid_extensions(self, tmp_path: Path) -> None:
        """Test detection of valid .tar.gz and .tgz archives."""
        # Create mock archive files
        tar_gz = tmp_path / "data.tar.gz"
        tar_gz.touch()
        
        tgz = tmp_path / "data.tgz"
        tgz.touch()
        
        assert _is_tar_gz_archive(tar_gz) is True
        assert _is_tar_gz_archive(tgz) is True
    
    def test_is_tar_gz_archive_invalid_extensions(self, tmp_path: Path) -> None:
        """Test that non-archive files are not detected as archives."""
        csv = tmp_path / "data.csv"
        csv.touch()
        
        zip_file = tmp_path / "data.zip"
        zip_file.touch()
        
        txt = tmp_path / "data.txt"
        txt.touch()
        
        assert _is_tar_gz_archive(csv) is False
        assert _is_tar_gz_archive(zip_file) is False
        assert _is_tar_gz_archive(txt) is False
    
    def test_is_tar_gz_archive_case_insensitive(self, tmp_path: Path) -> None:
        """Test that archive detection is case insensitive."""
        upper_tar_gz = tmp_path / "DATA.TAR.GZ"
        upper_tar_gz.touch()
        
        mixed_tgz = tmp_path / "Data.TgZ"
        mixed_tgz.touch()
        
        assert _is_tar_gz_archive(upper_tar_gz) is True
        assert _is_tar_gz_archive(mixed_tgz) is True
    
    def test_is_tar_gz_archive_nonexistent_file(self, tmp_path: Path) -> None:
        """Test that nonexistent files with .tar.gz extension still return True (checks extension only)."""
        nonexistent = tmp_path / "does_not_exist.tar.gz"
        # _is_tar_gz_archive only checks extension, not existence
        assert _is_tar_gz_archive(nonexistent) is True


class TestArchiveExtraction:
    """Test archive extraction functionality."""
    
    def test_extract_archive_success(self, tmp_path: Path) -> None:
        """Test successful extraction of a simple archive."""
        # Create a simple tar.gz archive with CSV file
        archive_path = tmp_path / "test_data.tar.gz"
        extract_dir = tmp_path / "extracted"
        
        # Create content to archive
        content_dir = tmp_path / "content"
        content_dir.mkdir()
        csv_file = content_dir / "data.csv"
        csv_file.write_text("time,latitude,longitude\n2024-01-01,40.0,-120.0\n")
        
        # Create archive
        with tarfile.open(archive_path, "w:gz") as tar:
            tar.add(csv_file, arcname="data.csv")
        
        # Extract archive
        result = _extract_archive(archive_path, extract_dir, verbose=False)
        
        # Verify extraction
        assert result.exists()
        assert result.is_dir()
        extracted_csv = result / "data.csv"
        assert extracted_csv.exists()
        assert "time,latitude,longitude" in extracted_csv.read_text()
    
    def test_extract_archive_with_subdirectories(self, tmp_path: Path) -> None:
        """Test extraction of archive with R2R-style subdirectory structure."""
        archive_path = tmp_path / "r2r_data.tar.gz"
        extract_dir = tmp_path / "extracted"
        
        # Create R2R-style structure: archive_name/data/*.csv
        content_dir = tmp_path / "content"
        data_subdir = content_dir / "RR2402_615519_r2rnav" / "data"
        data_subdir.mkdir(parents=True)
        
        csv_file = data_subdir / "navigation.csv"
        csv_file.write_text("time,latitude,longitude\n2024-01-01,40.0,-120.0\n")
        
        # Create archive
        with tarfile.open(archive_path, "w:gz") as tar:
            tar.add(content_dir / "RR2402_615519_r2rnav", arcname="RR2402_615519_r2rnav")
        
        # Extract archive
        result = _extract_archive(archive_path, extract_dir, verbose=False)
        
        # Verify extraction preserves structure
        # _extract_archive creates a subdirectory with the archive name
        assert result.exists()
        extracted_csv = result / "RR2402_615519_r2rnav" / "data" / "navigation.csv"
        assert extracted_csv.exists()
    
    def test_extract_archive_missing_file(self, tmp_path: Path) -> None:
        """Test that extracting nonexistent archive raises FileNotFoundError."""
        nonexistent = tmp_path / "missing.tar.gz"
        extract_dir = tmp_path / "extracted"
        
        with pytest.raises(FileNotFoundError):
            _extract_archive(nonexistent, extract_dir, verbose=False)
    
    def test_extract_archive_creates_work_directory(self, tmp_path: Path) -> None:
        """Test that extraction creates work directory if it doesn't exist."""
        archive_path = tmp_path / "test.tar.gz"
        extract_dir = tmp_path / "work" / "archives"  # Doesn't exist yet
        
        # Create simple archive
        content_dir = tmp_path / "content"
        content_dir.mkdir()
        csv_file = content_dir / "data.csv"
        csv_file.write_text("time,lat,lon\n2024-01-01,40,-120\n")
        
        with tarfile.open(archive_path, "w:gz") as tar:
            tar.add(csv_file, arcname="data.csv")
        
        # Extract (should create work directory)
        result = _extract_archive(archive_path, extract_dir, verbose=False)
        
        assert extract_dir.exists()
        assert result.exists()
        assert (result / "data.csv").exists()


class TestDataFileDiscovery:
    """Test finding data files in extracted archives."""
    
    def test_find_data_files_csv_and_geocsv(self, tmp_path: Path) -> None:
        """Test finding CSV and GeoCSV files."""
        # Create test directory with CSV files
        test_dir = tmp_path / "test_data"
        test_dir.mkdir()
        
        csv1 = test_dir / "data1.csv"
        csv1.write_text("time,lat,lon\n")
        
        geocsv = test_dir / "data2.geocsv"
        geocsv.write_text("# delimiter: ,\ntime,lat,lon\n")
        
        csv2 = test_dir / "data3.csv"
        csv2.write_text("time,lat,lon\n")
        
        # Find data files (returns tuple of data_files, ctd_files)
        files, ctd_files = _find_data_files_in_archive(test_dir)
        
        assert len(files) == 3
        assert csv1 in files
        assert geocsv in files
        assert csv2 in files
        assert len(ctd_files) == 0
    
    def test_find_data_files_r2r_data_subdirectory(self, tmp_path: Path) -> None:
        """Test finding files in R2R 'data/' subdirectory."""
        # Create R2R-style structure
        archive_dir = tmp_path / "RR2402_615519_r2rnav"
        data_dir = archive_dir / "data"
        data_dir.mkdir(parents=True)
        
        csv_file = data_dir / "navigation.csv"
        csv_file.write_text("time,lat,lon\n")
        
        # Also create bag-info.txt in root (should not be found)
        bag_info = archive_dir / "bag-info.txt"
        bag_info.write_text("Bag-Size: 1 MB\n")
        
        # Find data files (returns tuple)
        files, ctd_files = _find_data_files_in_archive(archive_dir)
        
        assert len(files) == 1
        assert csv_file in files
        assert bag_info not in files
    
    def test_find_data_files_nmea_txt(self, tmp_path: Path) -> None:
        """Test finding .txt files (NMEA filtering happens later in pipeline)."""
        test_dir = tmp_path / "test_data"
        test_dir.mkdir()
        
        # Create NMEA file (starts with $)
        nmea = test_dir / "gps.txt"
        nmea.write_text("$GPGGA,123456,4000.00,N,12000.00,W,1,08,0.9,100.0,M,,M,,*47\n")
        
        # Create non-NMEA txt file (will also be found - filtering happens later)
        readme = test_dir / "readme.txt"
        readme.write_text("This is a readme file\n")
        
        # Find data files - includes ALL .txt files (returns tuple)
        files, ctd_files = _find_data_files_in_archive(test_dir)
        
        # Both .txt files are included (NMEA detection happens in later pipeline stage)
        assert len(files) == 2
        assert nmea in files
        assert readme in files
    
    def test_find_data_files_empty_directory(self, tmp_path: Path) -> None:
        """Test that empty directory returns empty list."""
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()
        
        files, ctd_files = _find_data_files_in_archive(empty_dir)
        
        assert len(files) == 0
        assert files == []
        assert len(ctd_files) == 0
    
    def test_find_data_files_no_processable_files(self, tmp_path: Path) -> None:
        """Test directory with only non-CSV/GeoCSV files."""
        test_dir = tmp_path / "test_data"
        test_dir.mkdir()
        
        # Create files that are not CSV/GeoCSV (but .txt is found)
        readme_txt = test_dir / "readme.txt"
        readme_txt.write_text("Readme\n")
        (test_dir / "data.json").write_text('{"key": "value"}\n')
        (test_dir / "image.png").write_bytes(b'\x89PNG\r\n')
        
        files, ctd_files = _find_data_files_in_archive(test_dir)
        
        # .txt files are included (even if not NMEA)
        assert len(files) == 1
        assert readme_txt in files
    
    def test_find_data_files_nested_directories(self, tmp_path: Path) -> None:
        """Test finding files in nested directory structure."""
        # Create nested structure
        root = tmp_path / "root"
        level1 = root / "level1"
        level2 = level1 / "level2"
        level2.mkdir(parents=True)
        
        csv1 = root / "data1.csv"
        csv1.write_text("time,lat,lon\n")
        
        csv2 = level1 / "data2.csv"
        csv2.write_text("time,lat,lon\n")
        
        csv3 = level2 / "data3.csv"
        csv3.write_text("time,lat,lon\n")
        
        # Find all files recursively (returns tuple)
        files, ctd_files = _find_data_files_in_archive(root)
        
        assert len(files) == 3
        assert csv1 in files
        assert csv2 in files
        assert csv3 in files
    
    def test_find_data_files_mixed_types(self, tmp_path: Path) -> None:
        """Test finding mixed CSV, GeoCSV, and NMEA files."""
        test_dir = tmp_path / "mixed_data"
        test_dir.mkdir()
        
        csv = test_dir / "data.csv"
        csv.write_text("time,lat,lon\n")
        
        geocsv = test_dir / "geo_data.geocsv"
        geocsv.write_text("# delimiter: ,\ntime,lat,lon\n")
        
        nmea = test_dir / "gps.txt"
        nmea.write_text("$GPGGA,123456,4000.00,N,12000.00,W,1,08,0.9,100.0,M,,M,,*47\n")
        
        # Non-data files
        readme = test_dir / "readme.txt"
        readme.write_text("Not NMEA\n")
        
        json_file = test_dir / "config.json"
        json_file.write_text('{"config": true}\n')
        
        # Find all data files (returns tuple)
        files, ctd_files = _find_data_files_in_archive(test_dir)
        
        # All .csv, .geocsv, and .txt files are included
        assert len(files) == 4
        assert csv in files
        assert geocsv in files
        assert nmea in files
        assert readme in files  # .txt files included (NMEA filtering is later)
        assert json_file not in files

    def test_find_data_files_ctd_hex_files(self, tmp_path: Path) -> None:
        """Test finding SeaBird CTD hex files."""
        test_dir = tmp_path / "ctd_data"
        data_dir = test_dir / "data"
        data_dir.mkdir(parents=True)
        
        # Create CTD hex files
        hex1 = data_dir / "RR2402_cast1.hex"
        hex1.write_bytes(b'\x00\x01\x02')  # Fake hex content
        
        hex2 = data_dir / "RR2402_cast2.hex"
        hex2.write_bytes(b'\x00\x01\x02')
        
        # Also create associated files
        hdr = data_dir / "RR2402_cast1.hdr"
        hdr.write_text("** Date: 2024-02-20\n")
        
        xmlcon = data_dir / "RR2402_cast1.xmlcon"
        xmlcon.write_text("<SBE_InstrumentConfiguration/>")
        
        # Find files
        files, ctd_files = _find_data_files_in_archive(test_dir)
        
        # Should find hex files in ctd_files
        assert len(ctd_files) == 2
        assert hex1 in ctd_files
        assert hex2 in ctd_files
        
        # .hdr and .xmlcon are not in either list (they're metadata)
        assert hdr not in files
        assert xmlcon not in files
        assert len(files) == 0


class TestArchiveExtractionEdgeCases:
    """Test edge cases and error handling for archive extraction."""
    
    def test_extract_archive_with_verbose_output(self, tmp_path: Path, capsys) -> None:
        """Test that verbose mode produces output."""
        archive_path = tmp_path / "test.tar.gz"
        extract_dir = tmp_path / "extracted"
        
        # Create simple archive
        content_dir = tmp_path / "content"
        content_dir.mkdir()
        csv = content_dir / "data.csv"
        csv.write_text("time,lat,lon\n")
        
        with tarfile.open(archive_path, "w:gz") as tar:
            tar.add(csv, arcname="data.csv")
        
        # Extract with verbose=True
        _extract_archive(archive_path, extract_dir, verbose=True)
        
        # Check that output was produced
        captured = capsys.readouterr()
        assert "Extracting" in captured.out or "extracting" in captured.out.lower()
    
    def test_extract_archive_preserves_permissions(self, tmp_path: Path) -> None:
        """Test that archive extraction preserves file structure."""
        archive_path = tmp_path / "test.tar.gz"
        extract_dir = tmp_path / "extracted"
        
        # Create archive with specific structure
        content_dir = tmp_path / "content"
        subdir = content_dir / "subdir"
        subdir.mkdir(parents=True)
        
        csv = subdir / "data.csv"
        csv.write_text("time,lat,lon\n")
        
        with tarfile.open(archive_path, "w:gz") as tar:
            tar.add(content_dir / "subdir", arcname="subdir")
        
        # Extract
        result = _extract_archive(archive_path, extract_dir, verbose=False)
        
        # Verify structure is preserved
        extracted_subdir = result / "subdir"
        extracted_csv = extracted_subdir / "data.csv"
        
        assert extracted_subdir.exists()
        assert extracted_subdir.is_dir()
        assert extracted_csv.exists()
        assert extracted_csv.is_file()
