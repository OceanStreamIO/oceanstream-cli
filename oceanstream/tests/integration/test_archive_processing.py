"""Integration tests for archive processing functionality.

Tests end-to-end archive extraction and processing workflows.
"""

from __future__ import annotations

import tarfile
from pathlib import Path

import pytest


@pytest.mark.integration
class TestArchiveExtractionWorkflow:
    """Test complete archive extraction workflows."""
    
    def test_extract_and_find_data_files(self, tmp_path: Path) -> None:
        """Test extraction of archive and finding data files."""
        from oceanstream.geotrack.processor import (
            _extract_archive,
            _find_data_files_in_archive,
        )
        
        # Create R2R-style archive
        archive_path = tmp_path / "test_data.tar.gz"
        content_dir = tmp_path / "content"
        data_dir = content_dir / "RR2402_test" / "data"
        data_dir.mkdir(parents=True)
        
        # Create CSV files
        csv1 = data_dir / "nav1.csv"
        csv1.write_text("time,latitude,longitude\n2024-01-01T00:00:00Z,40.0,-120.0\n")
        
        csv2 = data_dir / "nav2.csv"
        csv2.write_text("time,latitude,longitude\n2024-01-02T00:00:00Z,41.0,-121.0\n")
        
        # Create archive
        with tarfile.open(archive_path, "w:gz") as tar:
            tar.add(content_dir / "RR2402_test", arcname="RR2402_test")
        
        # Extract
        extract_dir = tmp_path / "extracted"
        result = _extract_archive(archive_path, extract_dir, verbose=False)
        
        assert result.exists()
        assert result.is_dir()
        
        # Find data files
        data_files = _find_data_files_in_archive(result)
        
        assert len(data_files) == 2
        assert any("nav1.csv" in str(f) for f in data_files)
        assert any("nav2.csv" in str(f) for f in data_files)
    
    def test_extract_multiple_archives(self, tmp_path: Path) -> None:
        """Test extracting multiple archives."""
        from oceanstream.geotrack.processor import (
            _extract_archive,
            _find_data_files_in_archive,
        )
        
        # Create 2 archives
        for i in range(1, 3):
            archive_path = tmp_path / f"archive{i}.tar.gz"
            content_dir = tmp_path / f"content{i}"
            data_dir = content_dir / f"data{i}"
            data_dir.mkdir(parents=True)
            
            csv = data_dir / f"file{i}.csv"
            csv.write_text("time,lat,lon\n2024-01-01T00:00:00Z,40.0,-120.0\n")
            
            with tarfile.open(archive_path, "w:gz") as tar:
                tar.add(content_dir / f"data{i}", arcname=f"data{i}")
        
        # Extract both
        extract_dir = tmp_path / "extracted"
        all_data_files = []
        
        for i in range(1, 3):
            archive_path = tmp_path / f"archive{i}.tar.gz"
            result = _extract_archive(archive_path, extract_dir, verbose=False)
            data_files, ctd_files = _find_data_files_in_archive(result)
            all_data_files.extend(data_files)
        
        # Should have 2 data files total
        assert len(all_data_files) == 2


@pytest.mark.integration
class TestPlatformDetectionWorkflow:
    """Test platform detection in real workflows."""
    
    def test_r2r_platform_lookup_all_vessels(self, tmp_path: Path) -> None:
        """Test that all major vessel codes are correctly looked up."""
        from oceanstream.providers.r2r.r2r import R2RProvider
        
        provider = R2RProvider()
        
        # Test different vessel codes
        test_cases = [
            ("RR2402", "R/V Roger Revelle"),
            ("FK161229", "R/V Falkor"),
            ("AT42-10", "R/V Atlantis"),
            ("NBP1402", "RVIB Nathaniel B. Palmer"),
            ("TN123", "R/V Thomas G. Thompson"),
            ("SA2301", "R/V Sikuliaq"),
        ]
        
        for cruise_id, expected_vessel in test_cases:
            vessel_name = provider.get_platform_from_cruise_id(cruise_id)
            assert vessel_name == expected_vessel, f"Expected {expected_vessel}, got {vessel_name} for {cruise_id}"
    
    def test_scan_input_with_archive(self, tmp_path: Path) -> None:
        """Test scan_input_source with an archive file."""
        from oceanstream.geotrack.processor import GeotrackProcessor
        from oceanstream.providers.r2r.r2r import R2RProvider
        
        # Create simple archive
        archive_path = tmp_path / "test.tar.gz"
        content_dir = tmp_path / "content"
        data_dir = content_dir / "test_data" / "data"
        data_dir.mkdir(parents=True)
        
        csv = data_dir / "nav.csv"
        csv.write_text(
            "# delimiter: ,\n"
            "time,latitude,longitude,trajectory\n"
            "2024-01-01T00:00:00Z,40.0,-120.0,TEST\n"
        )
        
        with tarfile.open(archive_path, "w:gz") as tar:
            tar.add(content_dir / "test_data", arcname="test_data")
        
        # Use processor to scan
        provider = R2RProvider()
        processor = GeotrackProcessor(provider=provider, verbose=False)
        
        csv_files = processor.scan_input_source(archive_path)
        
        # Should find the CSV file
        assert len(csv_files) > 0
        assert any("nav.csv" in str(f) for f in csv_files)
        
        # Files should be in .oceanstream_work directory
        for csv_file in csv_files:
            assert ".oceanstream_work" in str(csv_file)
    
    def test_scan_directory_with_archives(self, tmp_path: Path) -> None:
        """Test scan_input_source with a directory containing archives."""
        from oceanstream.geotrack.processor import GeotrackProcessor
        from oceanstream.providers.r2r.r2r import R2RProvider
        
        # Create directory with 2 archives
        archive_dir = tmp_path / "archives"
        archive_dir.mkdir()
        
        for i in range(1, 3):
            archive_path = archive_dir / f"archive{i}.tar.gz"
            content_dir = tmp_path / f"content{i}"
            data_dir = content_dir / f"data{i}"
            data_dir.mkdir(parents=True)
            
            csv = data_dir / f"file{i}.csv"
            csv.write_text(
                "# delimiter: ,\n"
                "time,latitude,longitude,trajectory\n"
                f"2024-01-0{i}T00:00:00Z,40.{i},-120.{i},TEST{i}\n"
            )
            
            with tarfile.open(archive_path, "w:gz") as tar:
                tar.add(content_dir / f"data{i}", arcname=f"data{i}")
        
        # Scan directory
        provider = R2RProvider()
        processor = GeotrackProcessor(provider=provider, verbose=False)
        
        csv_files = processor.scan_input_source(archive_dir)
        
        # Should find files from both archives
        assert len(csv_files) == 2
        assert any("file1.csv" in str(f) for f in csv_files)
        assert any("file2.csv" in str(f) for f in csv_files)
