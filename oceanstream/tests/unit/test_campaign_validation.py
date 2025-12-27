"""Tests for campaign validation functionality."""
import shutil
from pathlib import Path
from unittest.mock import patch

import pytest

from oceanstream.geotrack.validation import (
    validate_campaign_output,
    should_reprocess_campaign,
    clear_invalid_campaign_metadata,
)
from oceanstream.geotrack.campaign import create_campaign, load_campaign_metadata


@pytest.fixture
def mock_campaigns_dir(tmp_path, monkeypatch):
    """Mock the campaigns directory to use tmp_path for isolation."""
    campaigns_dir = tmp_path / "campaigns"
    campaigns_dir.mkdir()
    
    # Patch get_campaigns_dir to return our test directory
    def _get_test_campaigns_dir():
        return campaigns_dir
    
    monkeypatch.setattr(
        "oceanstream.geotrack.campaign.get_campaigns_dir",
        _get_test_campaigns_dir
    )
    
    return campaigns_dir


class TestValidateCampaignOutput:
    """Tests for validate_campaign_output function."""

    def test_no_output_directory_specified(self):
        """Test validation when no output_directory in metadata."""
        metadata = {"campaign_id": "test"}
        result = validate_campaign_output(metadata)
        
        assert result['valid'] is False
        assert result['output_exists'] is False
        assert "No output_directory specified" in result['issues'][0]

    def test_output_directory_not_exists(self, tmp_path):
        """Test validation when output directory doesn't exist."""
        metadata = {
            "campaign_id": "test",
            "output_directory": str(tmp_path / "nonexistent")
        }
        result = validate_campaign_output(metadata)
        
        assert result['valid'] is False
        assert result['output_exists'] is False
        assert "does not exist" in result['issues'][0]

    def test_output_directory_empty(self, tmp_path):
        """Test validation when output directory exists but is empty."""
        output_dir = tmp_path / "output"
        output_dir.mkdir()
        
        metadata = {
            "campaign_id": "test",
            "output_directory": str(output_dir)
        }
        result = validate_campaign_output(metadata)
        
        assert result['valid'] is False
        assert result['output_exists'] is True
        assert result['has_parquet'] is False
        assert result['parquet_count'] == 0
        assert "No parquet files found" in result['issues'][0]

    def test_output_directory_with_parquet_files(self, tmp_path):
        """Test validation when output directory has parquet files."""
        output_dir = tmp_path / "output"
        output_dir.mkdir()
        
        # Create some parquet files
        (output_dir / "data1.parquet").touch()
        (output_dir / "data2.parquet").touch()
        
        metadata = {
            "campaign_id": "test",
            "output_directory": str(output_dir)
        }
        result = validate_campaign_output(metadata)
        
        assert result['valid'] is True
        assert result['output_exists'] is True
        assert result['has_parquet'] is True
        assert result['parquet_count'] == 2
        assert result['has_stac'] is False  # No STAC yet

    def test_output_directory_with_nested_parquet(self, tmp_path):
        """Test validation finds parquet files in nested directories."""
        output_dir = tmp_path / "output"
        bin_dir = output_dir / "lat_bin=20" / "lon_bin=-150"
        bin_dir.mkdir(parents=True)
        
        # Create parquet files in nested structure
        (bin_dir / "part1.parquet").touch()
        (bin_dir / "part2.parquet").touch()
        
        metadata = {
            "campaign_id": "test",
            "output_directory": str(output_dir)
        }
        result = validate_campaign_output(metadata)
        
        assert result['valid'] is True
        assert result['parquet_count'] == 2

    def test_output_directory_with_stac(self, tmp_path):
        """Test validation detects STAC metadata."""
        output_dir = tmp_path / "output"
        output_dir.mkdir()
        
        # Create parquet file
        (output_dir / "data.parquet").touch()
        
        # Create STAC metadata
        stac_dir = output_dir / "stac"
        stac_dir.mkdir()
        (stac_dir / "collection.json").touch()
        
        metadata = {
            "campaign_id": "test",
            "output_directory": str(output_dir)
        }
        result = validate_campaign_output(metadata)
        
        assert result['valid'] is True
        assert result['has_stac'] is True
        assert len(result['issues']) == 0  # No issues


class TestShouldReprocessCampaign:
    """Tests for should_reprocess_campaign function."""

    def test_force_reprocess(self, tmp_path):
        """Test that force_reprocess always returns True."""
        output_dir = tmp_path / "output"
        output_dir.mkdir()
        (output_dir / "data.parquet").touch()
        
        metadata = {
            "campaign_id": "test",
            "output_directory": str(output_dir)
        }
        
        should_reprocess, reasons = should_reprocess_campaign(
            metadata, force_reprocess=True
        )
        
        assert should_reprocess is True
        assert "Force reprocess" in reasons[0]

    def test_reprocess_when_output_missing(self, tmp_path):
        """Test reprocess when output directory is missing."""
        metadata = {
            "campaign_id": "test",
            "output_directory": str(tmp_path / "nonexistent")
        }
        
        should_reprocess, reasons = should_reprocess_campaign(metadata)
        
        assert should_reprocess is True
        assert len(reasons) > 0
        assert "does not exist" in reasons[0]

    def test_reprocess_when_parquet_missing(self, tmp_path):
        """Test reprocess when parquet files are missing."""
        output_dir = tmp_path / "output"
        output_dir.mkdir()
        
        metadata = {
            "campaign_id": "test",
            "output_directory": str(output_dir)
        }
        
        should_reprocess, reasons = should_reprocess_campaign(metadata)
        
        assert should_reprocess is True
        assert "No parquet files found" in reasons[0]

    def test_no_reprocess_when_valid(self, tmp_path):
        """Test no reprocess when output is valid."""
        output_dir = tmp_path / "output"
        output_dir.mkdir()
        (output_dir / "data.parquet").touch()
        
        metadata = {
            "campaign_id": "test",
            "output_directory": str(output_dir)
        }
        
        should_reprocess, reasons = should_reprocess_campaign(metadata)
        
        assert should_reprocess is False
        assert len(reasons) == 0


class TestClearInvalidCampaignMetadata:
    """Tests for clear_invalid_campaign_metadata function."""

    def test_clear_when_output_missing(self, tmp_path, mock_campaigns_dir):
        """Test clearing metadata when output is missing."""
        campaigns_dir = mock_campaigns_dir
        output_dir = tmp_path / "output"
        
        # Create campaign with non-existent output
        metadata = {
            "output_directory": str(output_dir),
            "platform_id": "test_platform"
        }
        create_campaign("test_campaign", metadata, verbose=False)
        
        # Verify campaign exists
        assert load_campaign_metadata("test_campaign") is not None
        
        # Clear invalid metadata
        cleared = clear_invalid_campaign_metadata(
            "test_campaign", campaigns_dir, verbose=False
        )
        
        assert cleared is True
        # Campaign metadata should be removed
        campaign_dir = campaigns_dir / "test_campaign"
        assert not campaign_dir.exists()

    def test_clear_when_parquet_deleted(self, tmp_path, mock_campaigns_dir):
        """Test clearing metadata when parquet files are deleted."""
        campaigns_dir = mock_campaigns_dir
        output_dir = tmp_path / "output"
        output_dir.mkdir()
        
        # Create parquet file initially
        parquet_file = output_dir / "data.parquet"
        parquet_file.touch()
        
        # Create campaign
        metadata = {
            "output_directory": str(output_dir),
            "platform_id": "test_platform"
        }
        create_campaign("test_campaign", metadata, verbose=False)
        
        # Now delete the output data (simulating user deleting output folder)
        parquet_file.unlink()
        
        # Clear invalid metadata
        cleared = clear_invalid_campaign_metadata(
            "test_campaign", campaigns_dir, verbose=False
        )
        
        assert cleared is True

    def test_no_clear_when_valid(self, tmp_path, mock_campaigns_dir):
        """Test no clearing when output is valid."""
        campaigns_dir = mock_campaigns_dir
        output_dir = tmp_path / "output"
        output_dir.mkdir()
        (output_dir / "data.parquet").touch()
        
        # Create campaign with valid output
        metadata = {
            "output_directory": str(output_dir),
            "platform_id": "test_platform"
        }
        create_campaign("test_campaign", metadata, verbose=False)
        
        # Try to clear (should not clear because output is valid)
        cleared = clear_invalid_campaign_metadata(
            "test_campaign", campaigns_dir, verbose=False
        )
        
        assert cleared is False
        # Campaign should still exist
        assert load_campaign_metadata("test_campaign") is not None

    def test_no_clear_when_campaign_not_exists(self, tmp_path, mock_campaigns_dir):
        """Test no clearing when campaign doesn't exist."""
        campaigns_dir = mock_campaigns_dir
        
        cleared = clear_invalid_campaign_metadata(
            "nonexistent_campaign", campaigns_dir, verbose=False
        )
        
        assert cleared is False


class TestIntegrationValidation:
    """Integration tests for validation with campaign management."""

    def test_workflow_delete_output_and_reprocess(self, tmp_path, mock_campaigns_dir):
        """Test complete workflow: create campaign, delete output, detect and reprocess."""
        campaigns_dir = mock_campaigns_dir
        output_dir = tmp_path / "output"
        output_dir.mkdir()
        
        # Step 1: Create campaign with valid output
        (output_dir / "data.parquet").touch()
        metadata = {
            "output_directory": str(output_dir),
            "platform_id": "test_platform",
            "total_rows": 1000
        }
        campaign_dir = create_campaign("test_campaign", metadata, verbose=False)
        assert campaign_dir.exists()
        
        # Step 2: User deletes output directory
        shutil.rmtree(output_dir)
        assert not output_dir.exists()
        
        # Step 3: Load campaign and validate
        loaded_metadata = load_campaign_metadata("test_campaign")
        assert loaded_metadata is not None
        
        validation = validate_campaign_output(loaded_metadata)
        assert validation['valid'] is False
        assert validation['output_exists'] is False
        
        # Step 4: Should reprocess
        should_reprocess, reasons = should_reprocess_campaign(loaded_metadata)
        assert should_reprocess is True
        assert len(reasons) > 0
        
        # Step 5: Clear invalid metadata
        cleared = clear_invalid_campaign_metadata(
            "test_campaign", campaigns_dir, verbose=False
        )
        assert cleared is True
        assert not campaign_dir.exists()
        
        # Step 6: Now can create fresh campaign
        output_dir.mkdir()
        (output_dir / "new_data.parquet").touch()
        new_metadata = {
            "output_directory": str(output_dir),
            "platform_id": "test_platform",
            "total_rows": 2000
        }
        new_campaign_dir = create_campaign("test_campaign", new_metadata, verbose=False)
        assert new_campaign_dir.exists()
        
        # Verify new metadata
        reloaded = load_campaign_metadata("test_campaign")
        assert reloaded['total_rows'] == 2000

    def test_workflow_delete_parquet_keep_stac(self, tmp_path, mock_campaigns_dir):
        """Test detection when parquet deleted but STAC remains."""
        campaigns_dir = mock_campaigns_dir
        output_dir = tmp_path / "output"
        output_dir.mkdir()
        
        # Create output with both parquet and STAC
        parquet_file = output_dir / "data.parquet"
        parquet_file.touch()
        
        stac_dir = output_dir / "stac"
        stac_dir.mkdir()
        (stac_dir / "collection.json").touch()
        
        # Create campaign
        metadata = {
            "output_directory": str(output_dir),
            "platform_id": "test_platform"
        }
        create_campaign("test_campaign", metadata, verbose=False)
        
        # Delete only parquet files (keep STAC)
        parquet_file.unlink()
        
        # Validate - should be invalid
        loaded = load_campaign_metadata("test_campaign")
        validation = validate_campaign_output(loaded)
        assert validation['valid'] is False
        assert validation['has_stac'] is True  # STAC still there
        assert "No parquet files found" in validation['issues'][0]
        
        # Should clear metadata
        cleared = clear_invalid_campaign_metadata(
            "test_campaign", campaigns_dir, verbose=False
        )
        assert cleared is True
