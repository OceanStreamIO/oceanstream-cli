"""Unit tests for campaign metadata tracking."""
from __future__ import annotations
import json
from pathlib import Path
from datetime import datetime, timezone

import pytest

from oceanstream.geotrack.metadata import CampaignMetadata, _compute_file_hash


class TestComputeFileHash:
    """Tests for file hashing function."""
    
    def test_compute_file_hash(self, tmp_path: Path):
        """Test SHA256 hash computation."""
        test_file = tmp_path / "test.csv"
        test_file.write_text("time,latitude,longitude\n2024-01-01,10.0,20.0\n")
        
        hash1 = _compute_file_hash(test_file)
        
        # Hash should be consistent
        hash2 = _compute_file_hash(test_file)
        assert hash1 == hash2
        
        # Hash should be 64 chars (SHA256 hex)
        assert len(hash1) == 64
        assert all(c in '0123456789abcdef' for c in hash1)
    
    def test_different_content_different_hash(self, tmp_path: Path):
        """Test that different content produces different hashes."""
        file1 = tmp_path / "test1.csv"
        file1.write_text("time,latitude,longitude\n2024-01-01,10.0,20.0\n")
        
        file2 = tmp_path / "test2.csv"
        file2.write_text("time,latitude,longitude\n2024-01-01,15.0,25.0\n")
        
        hash1 = _compute_file_hash(file1)
        hash2 = _compute_file_hash(file2)
        
        assert hash1 != hash2


class TestCampaignMetadata:
    """Tests for CampaignMetadata class."""
    
    def test_init_creates_empty_metadata(self, tmp_path: Path):
        """Test initialization creates empty metadata structure."""
        metadata_dir = tmp_path / "metadata"
        campaign_id = "test_campaign"
        
        metadata = CampaignMetadata(campaign_id, metadata_dir)
        
        assert metadata.campaign_id == campaign_id
        assert metadata.metadata_dir == metadata_dir
        assert metadata.metadata_file == metadata_dir / f"{campaign_id}.json"
        
        # Should not create file until save() is called
        assert not metadata.metadata_file.exists()
    
    def test_save_creates_metadata_file(self, tmp_path: Path):
        """Test save() creates metadata file."""
        metadata_dir = tmp_path / "metadata"
        campaign_id = "test_campaign"
        
        metadata = CampaignMetadata(campaign_id, metadata_dir)
        metadata.save()
        
        assert metadata.metadata_file.exists()
        assert metadata_dir.exists()
        
        # Check file contents
        with open(metadata.metadata_file) as f:
            data = json.load(f)
        
        assert data["version"] == "1.0"
        assert "campaign_created" in data
        assert "last_updated" in data
        assert data["processed_files"] == {}
        assert data["total_runs"] == 0
        assert data["total_files_processed"] == 0
    
    def test_load_existing_metadata(self, tmp_path: Path):
        """Test loading existing metadata from file."""
        metadata_dir = tmp_path / "metadata"
        metadata_dir.mkdir(parents=True)
        campaign_id = "test_campaign"
        
        # Create existing metadata file
        existing_data = {
            "version": "1.0",
            "campaign_created": "2024-01-01T00:00:00Z",
            "last_updated": "2024-01-01T01:00:00Z",
            "processed_files": {
                "test.csv": {
                    "hash": "abc123",
                    "processed_at": "2024-01-01T00:30:00Z",
                    "size": 1000,
                    "rows": 50
                }
            },
            "total_runs": 1,
            "total_files_processed": 1
        }
        
        metadata_file = metadata_dir / f"{campaign_id}.json"
        with open(metadata_file, 'w') as f:
            json.dump(existing_data, f)
        
        # Load metadata
        metadata = CampaignMetadata(campaign_id, metadata_dir)
        
        assert metadata._data["total_runs"] == 1
        assert metadata._data["total_files_processed"] == 1
        assert "test.csv" in metadata._data["processed_files"]
    
    def test_is_file_processed_new_file(self, tmp_path: Path):
        """Test is_file_processed returns False for new file."""
        metadata_dir = tmp_path / "metadata"
        campaign_id = "test_campaign"
        
        test_file = tmp_path / "data.csv"
        test_file.write_text("time,latitude,longitude\n2024-01-01,10.0,20.0\n")
        
        metadata = CampaignMetadata(campaign_id, metadata_dir)
        
        assert not metadata.is_file_processed(test_file)
    
    def test_is_file_processed_existing_file(self, tmp_path: Path):
        """Test is_file_processed returns True for processed file."""
        metadata_dir = tmp_path / "metadata"
        campaign_id = "test_campaign"
        
        test_file = tmp_path / "data.csv"
        test_file.write_text("time,latitude,longitude\n2024-01-01,10.0,20.0\n")
        
        metadata = CampaignMetadata(campaign_id, metadata_dir)
        
        # Mark file as processed
        metadata.mark_file_processed(test_file, 1)
        
        assert metadata.is_file_processed(test_file)
    
    def test_is_file_processed_detects_content_change(self, tmp_path: Path):
        """Test is_file_processed detects when file content changes."""
        metadata_dir = tmp_path / "metadata"
        campaign_id = "test_campaign"
        
        test_file = tmp_path / "data.csv"
        test_file.write_text("time,latitude,longitude\n2024-01-01,10.0,20.0\n")
        
        metadata = CampaignMetadata(campaign_id, metadata_dir)
        metadata.mark_file_processed(test_file, 1)
        
        # Modify file content
        test_file.write_text("time,latitude,longitude\n2024-01-01,15.0,25.0\n")
        
        # Should return False because hash changed
        assert not metadata.is_file_processed(test_file)
    
    def test_mark_file_processed(self, tmp_path: Path):
        """Test mark_file_processed stores file info."""
        metadata_dir = tmp_path / "metadata"
        campaign_id = "test_campaign"
        
        test_file = tmp_path / "data.csv"
        test_file.write_text("time,latitude,longitude\n2024-01-01,10.0,20.0\n")
        
        metadata = CampaignMetadata(campaign_id, metadata_dir)
        metadata.mark_file_processed(test_file, rows_processed=42)
        
        file_info = metadata.get_file_info(test_file)
        
        assert file_info is not None
        assert file_info["rows"] == 42
        assert "hash" in file_info
        assert "processed_at" in file_info
        assert "size" in file_info
    
    def test_increment_run_count(self, tmp_path: Path):
        """Test increment_run_count increases run counter."""
        metadata_dir = tmp_path / "metadata"
        campaign_id = "test_campaign"
        
        metadata = CampaignMetadata(campaign_id, metadata_dir)
        
        assert metadata._data["total_runs"] == 0
        
        metadata.increment_run_count()
        assert metadata._data["total_runs"] == 1
        
        metadata.increment_run_count()
        assert metadata._data["total_runs"] == 2
    
    def test_clear(self, tmp_path: Path):
        """Test clear() removes metadata."""
        metadata_dir = tmp_path / "metadata"
        campaign_id = "test_campaign"
        
        test_file = tmp_path / "data.csv"
        test_file.write_text("time,latitude,longitude\n2024-01-01,10.0,20.0\n")
        
        metadata = CampaignMetadata(campaign_id, metadata_dir)
        metadata.mark_file_processed(test_file, rows_processed=10)
        metadata.increment_run_count()
        metadata.save()
        
        assert metadata.metadata_file.exists()
        
        # Clear metadata
        metadata.clear()
        
        assert not metadata.metadata_file.exists()
        assert metadata._data["total_runs"] == 0
        assert metadata._data["processed_files"] == {}
    
    def test_metadata_persistence(self, tmp_path: Path):
        """Test metadata persists across instances."""
        metadata_dir = tmp_path / "metadata"
        campaign_id = "test_campaign"
        
        test_file = tmp_path / "data.csv"
        test_file.write_text("time,latitude,longitude\n2024-01-01,10.0,20.0\n")
        
        # First instance
        metadata1 = CampaignMetadata(campaign_id, metadata_dir)
        metadata1.mark_file_processed(test_file, rows_processed=100)
        metadata1.increment_run_count()
        metadata1.save()
        
        # Second instance (reload)
        metadata2 = CampaignMetadata(campaign_id, metadata_dir)
        
        assert metadata2._data["total_runs"] == 1
        assert metadata2.is_file_processed(test_file)
        assert metadata2.get_file_info(test_file)["rows"] == 100
    
    def test_metadata_dir_in_home_folder(self, tmp_path: Path, monkeypatch):
        """Test metadata can be stored in home folder."""
        # Mock home directory
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        monkeypatch.setattr(Path, "home", lambda: fake_home)
        
        metadata_dir = Path.home() / ".oceanstream" / "metadata"
        campaign_id = "test_campaign"
        
        metadata = CampaignMetadata(campaign_id, metadata_dir)
        metadata.save()
        
        expected_file = fake_home / ".oceanstream" / "metadata" / f"{campaign_id}.json"
        assert expected_file.exists()
    
    def test_multiple_campaigns_same_metadata_dir(self, tmp_path: Path):
        """Test multiple campaigns can share same metadata directory."""
        metadata_dir = tmp_path / "metadata"
        
        campaign1 = CampaignMetadata("campaign_a", metadata_dir)
        campaign2 = CampaignMetadata("campaign_b", metadata_dir)
        
        campaign1.increment_run_count()
        campaign1.save()
        
        campaign2.increment_run_count()
        campaign2.increment_run_count()
        campaign2.save()
        
        # Both files should exist
        assert (metadata_dir / "campaign_a.json").exists()
        assert (metadata_dir / "campaign_b.json").exists()
        
        # Each should have independent counts
        campaign1_reload = CampaignMetadata("campaign_a", metadata_dir)
        campaign2_reload = CampaignMetadata("campaign_b", metadata_dir)
        
        assert campaign1_reload._data["total_runs"] == 1
        assert campaign2_reload._data["total_runs"] == 2
