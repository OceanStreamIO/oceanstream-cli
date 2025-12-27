"""Unit tests for report generation module.

Tests report generation functions, statistics calculation, and output formats.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock
import pytest

import pandas as pd
import numpy as np


class TestDatasetStats:
    """Test DatasetStats dataclass."""
    
    def test_dataset_stats_defaults(self):
        """Test DatasetStats has correct defaults."""
        from oceanstream.geotrack.report import DatasetStats
        
        stats = DatasetStats()
        assert stats.total_rows == 0
        assert stats.total_columns == 0
        assert stats.parquet_files == 0
        assert stats.platforms == {}
        assert stats.oceanographic == {}


class TestSTACMetadata:
    """Test STACMetadata dataclass."""
    
    def test_stac_metadata_defaults(self):
        """Test STACMetadata has correct defaults."""
        from oceanstream.geotrack.report import STACMetadata
        
        stac = STACMetadata()
        assert stac.collection_id == ""
        assert stac.stac_version == ""
        assert stac.instruments == []
        assert stac.item_count == 0


class TestFindParquetFiles:
    """Test find_parquet_files function."""
    
    def test_find_parquet_files_in_directory(self, tmp_path: Path):
        """Test finding parquet files in a directory structure."""
        from oceanstream.geotrack.report import find_parquet_files
        
        # Create test parquet files
        (tmp_path / "lat_bin=1" / "lon_bin=1").mkdir(parents=True)
        (tmp_path / "lat_bin=1" / "lon_bin=1" / "data.parquet").touch()
        (tmp_path / "lat_bin=2" / "lon_bin=2").mkdir(parents=True)
        (tmp_path / "lat_bin=2" / "lon_bin=2" / "data.parquet").touch()
        
        # Create stac directory (should be excluded)
        (tmp_path / "stac").mkdir()
        (tmp_path / "stac" / "collection.parquet").touch()
        
        files = find_parquet_files(tmp_path)
        
        assert len(files) == 2
        assert all(f.suffix == ".parquet" for f in files)
        assert all("stac" not in str(f) for f in files)
    
    def test_find_parquet_files_empty_directory(self, tmp_path: Path):
        """Test finding parquet files in empty directory."""
        from oceanstream.geotrack.report import find_parquet_files
        
        files = find_parquet_files(tmp_path)
        assert files == []


class TestCalculateStats:
    """Test calculate_stats function."""
    
    def test_calculate_stats_basic(self, tmp_path: Path):
        """Test basic statistics calculation."""
        from oceanstream.geotrack.report import calculate_stats
        
        # Create a sample DataFrame
        df = pd.DataFrame({
            "time": pd.date_range("2023-01-01", periods=100, freq="h", tz="UTC"),
            "latitude": np.random.uniform(-10, 10, 100),
            "longitude": np.random.uniform(-170, -150, 100),
            "trajectory": [1030] * 50 + [1033] * 50,
            "lat_bin": ["lat_0_2"] * 50 + ["lat_2_4"] * 50,
            "lon_bin": ["lon_-160_-158"] * 100,
            "TEMP_AIR_MEAN": np.random.uniform(25, 30, 100),
        })
        
        # Create a mock parquet file
        pf = tmp_path / "test.parquet"
        df.to_parquet(pf)
        
        stats = calculate_stats(df, [pf])
        
        assert stats.total_rows == 100
        assert stats.total_columns == 7
        assert stats.parquet_files == 1
        assert stats.duration_days >= 0
        assert stats.lat_bins == 2
        assert stats.lon_bins == 1
        assert "SD1030" in stats.platforms
        assert "SD1033" in stats.platforms
        assert stats.platforms["SD1030"] == 50
        assert stats.platforms["SD1033"] == 50
    
    def test_calculate_stats_with_oceanographic_data(self, tmp_path: Path):
        """Test statistics calculation with oceanographic measurements."""
        from oceanstream.geotrack.report import calculate_stats
        
        df = pd.DataFrame({
            "time": pd.date_range("2023-01-01", periods=10, freq="h", tz="UTC"),
            "latitude": [10.0] * 10,
            "longitude": [-160.0] * 10,
            "TEMP_SBE37_MEAN": [28.0, 28.5, 29.0, 28.2, np.nan, 27.5, 28.1, 29.2, 28.8, 27.9],
            "SAL_SBE37_MEAN": [35.0, 35.1, 35.2, np.nan, np.nan, 35.0, 35.1, 35.3, 35.2, 35.0],
        })
        
        pf = tmp_path / "test.parquet"
        df.to_parquet(pf)
        
        stats = calculate_stats(df, [pf])
        
        assert "TEMP_SBE37_MEAN" in stats.oceanographic
        assert stats.oceanographic["TEMP_SBE37_MEAN"]["valid_count"] == 9
        assert stats.oceanographic["TEMP_SBE37_MEAN"]["min"] == 27.5
        assert stats.oceanographic["TEMP_SBE37_MEAN"]["max"] == 29.2
        
        assert "SAL_SBE37_MEAN" in stats.oceanographic
        assert stats.oceanographic["SAL_SBE37_MEAN"]["valid_count"] == 8
    
    def test_calculate_stats_column_categorization(self, tmp_path: Path):
        """Test that columns are categorized correctly."""
        from oceanstream.geotrack.report import calculate_stats
        
        df = pd.DataFrame({
            "time": pd.date_range("2023-01-01", periods=5, freq="h", tz="UTC"),
            "latitude": [10.0] * 5,
            "longitude": [-160.0] * 5,
            # Navigation
            "SOG": [5.0] * 5,
            "COG": [180.0] * 5,
            "HDG": [90.0] * 5,
            "ROLL_FILTERED_MEAN": [0.5] * 5,
            # Meteorological
            "WIND_SPEED_MEAN": [6.0] * 5,
            "TEMP_AIR_MEAN": [28.0] * 5,
            "BARO_PRES_MEAN": [1010.0] * 5,
            # Oceanographic
            "TEMP_SBE37_MEAN": [28.0] * 5,
            "SAL_SBE37_MEAN": [35.0] * 5,
            "WAVE_SIGNIFICANT_HEIGHT": [2.0] * 5,
        })
        
        pf = tmp_path / "test.parquet"
        df.to_parquet(pf)
        
        stats = calculate_stats(df, [pf])
        
        assert "SOG" in stats.nav_columns
        assert "COG" in stats.nav_columns
        assert "HDG" in stats.nav_columns
        assert "ROLL_FILTERED_MEAN" in stats.nav_columns
        
        assert "WIND_SPEED_MEAN" in stats.met_columns
        assert "TEMP_AIR_MEAN" in stats.met_columns
        assert "BARO_PRES_MEAN" in stats.met_columns
        
        assert "TEMP_SBE37_MEAN" in stats.ocean_columns
        assert "SAL_SBE37_MEAN" in stats.ocean_columns
        assert "WAVE_SIGNIFICANT_HEIGHT" in stats.ocean_columns


class TestLoadSTACMetadata:
    """Test load_stac_metadata function."""
    
    def test_load_stac_metadata_success(self, tmp_path: Path):
        """Test loading STAC metadata from collection.json."""
        from oceanstream.geotrack.report import load_stac_metadata
        
        stac_dir = tmp_path / "stac"
        stac_dir.mkdir()
        items_dir = stac_dir / "items"
        items_dir.mkdir()
        
        # Create sample STAC collection
        collection = {
            "id": "test-collection",
            "stac_version": "1.0.0",
            "description": "Test collection",
            "license": "MIT",
            "keywords": ["temperature", "salinity"],
            "providers": [{"name": "test-provider", "roles": ["producer"]}],
            "summaries": {
                "instruments": [
                    {"name": "SBE37", "manufacturer": "Sea-Bird", "type": "ctd"}
                ],
                "platform": {"id": "sd1030", "type": "Saildrone Explorer"},
                "processing": {"software": "oceanstream", "version": "0.1.0"},
            },
        }
        (stac_dir / "collection.json").write_text(json.dumps(collection))
        
        # Create some item files
        (items_dir / "item-0.json").write_text("{}")
        (items_dir / "item-1.json").write_text("{}")
        
        stac = load_stac_metadata(tmp_path)
        
        assert stac is not None
        assert stac.collection_id == "test-collection"
        assert stac.stac_version == "1.0.0"
        assert stac.license == "MIT"
        assert len(stac.instruments) == 1
        assert stac.instruments[0]["name"] == "SBE37"
        assert stac.platform["id"] == "sd1030"
        assert stac.item_count == 2
    
    def test_load_stac_metadata_not_found(self, tmp_path: Path):
        """Test loading STAC metadata when file doesn't exist."""
        from oceanstream.geotrack.report import load_stac_metadata
        
        stac = load_stac_metadata(tmp_path)
        assert stac is None
    
    def test_load_stac_metadata_invalid_json(self, tmp_path: Path):
        """Test loading STAC metadata with invalid JSON."""
        from oceanstream.geotrack.report import load_stac_metadata
        
        stac_dir = tmp_path / "stac"
        stac_dir.mkdir()
        (stac_dir / "collection.json").write_text("not valid json")
        
        stac = load_stac_metadata(tmp_path)
        assert stac is None


class TestGenerateMarkdownReport:
    """Test generate_markdown_report function."""
    
    def test_generate_markdown_report_basic(self, tmp_path: Path):
        """Test basic markdown report generation."""
        from oceanstream.geotrack.report import (
            generate_markdown_report,
            DatasetStats,
            STACMetadata,
        )
        
        stats = DatasetStats(
            total_rows=1000,
            total_columns=50,
            parquet_files=10,
            total_size_mb=5.5,
            start_time=datetime(2023, 6, 1, tzinfo=timezone.utc),
            end_time=datetime(2023, 9, 1, tzinfo=timezone.utc),
            duration_days=92,
            lat_min=-10.0,
            lat_max=20.0,
            lon_min=-170.0,
            lon_max=-150.0,
            lat_bins=3,
            lon_bins=2,
            platforms={"SD1030": 500, "SD1033": 500},
            nav_columns=["SOG", "COG"],
            met_columns=["WIND_SPEED"],
            ocean_columns=["TEMP_SBE37_MEAN"],
            oceanographic={
                "TEMP_SBE37_MEAN": {"min": 25.0, "max": 30.0, "mean": 28.0, "valid_pct": 95.0}
            },
            meteorological={
                "WIND_SPEED_MEAN": {"min": 1.0, "max": 15.0, "mean": 6.0}
            },
        )
        
        stac = STACMetadata(
            collection_id="test-collection",
            stac_version="1.0.0",
            license="MIT",
            instruments=[{"name": "SBE37", "manufacturer": "Sea-Bird", "type": "ctd"}],
            item_count=10,
        )
        
        report = generate_markdown_report(tmp_path, stats, stac, "test_campaign")
        
        # Check report contains expected sections
        assert "# OceanStream Processing Report: test_campaign" in report
        assert "## Executive Summary" in report
        assert "1,000" in report  # formatted row count
        assert "## Platforms" in report
        assert "SD1030" in report
        assert "## Detected Sensors" in report
        assert "SBE37" in report
        assert "## Temporal Extent" in report
        assert "92 days" in report
        assert "## Spatial Extent" in report
        assert "-10.0000" in report
        assert "## Oceanographic Measurements" in report
        assert "TEMP_SBE37_MEAN" in report
        assert "## Meteorological Measurements" in report
        assert "## Column Categories" in report
        assert "## STAC Metadata" in report
        assert "## Usage Examples" in report
    
    def test_generate_markdown_report_no_stac(self, tmp_path: Path):
        """Test markdown report generation without STAC metadata."""
        from oceanstream.geotrack.report import generate_markdown_report, DatasetStats
        
        stats = DatasetStats(
            total_rows=100,
            total_columns=10,
            parquet_files=1,
        )
        
        report = generate_markdown_report(tmp_path, stats, None, "test_campaign")
        
        assert "# OceanStream Processing Report" in report
        assert "## STAC Metadata" not in report  # Should be omitted when no STAC


class TestGenerateJSONReport:
    """Test generate_json_report function."""
    
    def test_generate_json_report_basic(self, tmp_path: Path):
        """Test basic JSON report generation."""
        from oceanstream.geotrack.report import (
            generate_json_report,
            DatasetStats,
            STACMetadata,
        )
        
        stats = DatasetStats(
            total_rows=1000,
            total_columns=50,
            parquet_files=10,
            total_size_mb=5.5,
            start_time=datetime(2023, 6, 1, tzinfo=timezone.utc),
            end_time=datetime(2023, 9, 1, tzinfo=timezone.utc),
            duration_days=92,
            lat_min=-10.0,
            lat_max=20.0,
            lon_min=-170.0,
            lon_max=-150.0,
            platforms={"SD1030": 500},
        )
        
        stac = STACMetadata(
            collection_id="test-collection",
            stac_version="1.0.0",
        )
        
        report = generate_json_report(tmp_path, stats, stac, "test_campaign")
        
        assert report["campaign_id"] == "test_campaign"
        assert report["summary"]["total_rows"] == 1000
        assert report["summary"]["parquet_files"] == 10
        assert report["temporal_extent"]["duration_days"] == 92
        assert report["spatial_extent"]["latitude"]["min"] == -10.0
        assert report["platforms"]["SD1030"] == 500
        assert report["stac"]["collection_id"] == "test-collection"
    
    def test_generate_json_report_serializable(self, tmp_path: Path):
        """Test that JSON report is serializable."""
        from oceanstream.geotrack.report import generate_json_report, DatasetStats
        
        stats = DatasetStats(
            total_rows=100,
            start_time=datetime(2023, 6, 1, tzinfo=timezone.utc),
        )
        
        report = generate_json_report(tmp_path, stats, None, "test")
        
        # Should not raise
        json_str = json.dumps(report, default=str)
        assert "test" in json_str


class TestGenerateReport:
    """Test the main generate_report function."""
    
    def test_generate_report_markdown(self, tmp_path: Path):
        """Test generate_report with markdown output."""
        from oceanstream.geotrack.report import generate_report
        
        # Create a minimal parquet dataset
        data_dir = tmp_path / "campaign"
        data_dir.mkdir()
        (data_dir / "lat_bin=0_2" / "lon_bin=-160_-158").mkdir(parents=True)
        
        df = pd.DataFrame({
            "time": pd.date_range("2023-01-01", periods=10, freq="h", tz="UTC"),
            "latitude": [1.0] * 10,
            "longitude": [-159.0] * 10,
        })
        df.to_parquet(data_dir / "lat_bin=0_2" / "lon_bin=-160_-158" / "data.parquet")
        
        result = generate_report(data_dir, output_format="markdown")
        
        assert isinstance(result, str)
        assert "# OceanStream Processing Report" in result
        assert "10" in result  # row count
    
    def test_generate_report_json(self, tmp_path: Path):
        """Test generate_report with JSON output."""
        from oceanstream.geotrack.report import generate_report
        
        # Create a minimal parquet dataset
        data_dir = tmp_path / "campaign"
        data_dir.mkdir()
        
        df = pd.DataFrame({
            "time": pd.date_range("2023-01-01", periods=10, freq="h", tz="UTC"),
            "latitude": [1.0] * 10,
            "longitude": [-159.0] * 10,
        })
        df.to_parquet(data_dir / "data.parquet")
        
        result = generate_report(data_dir, output_format="json")
        
        assert isinstance(result, dict)
        assert result["summary"]["total_rows"] == 10
    
    def test_generate_report_to_file(self, tmp_path: Path):
        """Test generate_report writing to file."""
        from oceanstream.geotrack.report import generate_report
        
        # Create a minimal parquet dataset
        data_dir = tmp_path / "campaign"
        data_dir.mkdir()
        
        df = pd.DataFrame({
            "time": pd.date_range("2023-01-01", periods=10, freq="h", tz="UTC"),
            "latitude": [1.0] * 10,
            "longitude": [-159.0] * 10,
        })
        df.to_parquet(data_dir / "data.parquet")
        
        output_file = tmp_path / "report.md"
        generate_report(data_dir, output_path=output_file)
        
        assert output_file.exists()
        content = output_file.read_text()
        assert "# OceanStream Processing Report" in content
    
    def test_generate_report_not_found(self, tmp_path: Path):
        """Test generate_report with non-existent path."""
        from oceanstream.geotrack.report import generate_report
        
        with pytest.raises(FileNotFoundError):
            generate_report(tmp_path / "nonexistent")
    
    def test_generate_report_no_parquet_files(self, tmp_path: Path):
        """Test generate_report with directory containing no parquet files."""
        from oceanstream.geotrack.report import generate_report
        
        data_dir = tmp_path / "empty"
        data_dir.mkdir()
        
        with pytest.raises(ValueError, match="No parquet files"):
            generate_report(data_dir)
    
    def test_generate_report_invalid_format(self, tmp_path: Path):
        """Test generate_report with invalid output format."""
        from oceanstream.geotrack.report import generate_report
        
        data_dir = tmp_path / "campaign"
        data_dir.mkdir()
        
        df = pd.DataFrame({
            "time": pd.date_range("2023-01-01", periods=10, freq="h", tz="UTC"),
            "latitude": [1.0] * 10,
            "longitude": [-159.0] * 10,
        })
        df.to_parquet(data_dir / "data.parquet")
        
        with pytest.raises(ValueError, match="Invalid output format"):
            generate_report(data_dir, output_format="xml")
