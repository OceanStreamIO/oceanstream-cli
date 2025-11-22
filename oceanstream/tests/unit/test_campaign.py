"""Tests for campaign creation and management."""
import pytest
import json
from pathlib import Path
from datetime import datetime

from oceanstream.geotrack.campaign import (
    create_campaign,
    load_campaign_metadata,
    update_campaign_metadata,
    list_campaigns,
    delete_campaign,
)


def test_create_campaign_basic(tmp_path, monkeypatch):
    """Test basic campaign creation with minimal metadata."""
    # Mock home directory to use tmp_path
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: fake_home)
    
    campaign_id = "TEST_CAMPAIGN"
    metadata = {
        "platform_id": "test_platform",
        "description": "Test campaign",
    }
    
    campaign_dir = create_campaign(
        campaign_id=campaign_id,
        metadata=metadata,
        verbose=False,
    )
    
    expected_dir = fake_home / ".oceanstream" / "campaigns" / campaign_id
    assert campaign_dir == expected_dir
    assert campaign_dir.exists()
    
    # Check metadata file
    metadata_file = campaign_dir / "campaign.json"
    assert metadata_file.exists()
    
    # Load and verify
    with open(metadata_file) as f:
        saved = json.load(f)
    
    assert saved["campaign_id"] == campaign_id
    assert saved["platform_id"] == "test_platform"
    assert saved["description"] == "Test campaign"
    assert "created_at" in saved
    assert "updated_at" in saved
    assert "oceanstream_version" in saved


def test_create_campaign_full_metadata(tmp_path, monkeypatch):
    """Test campaign creation with all metadata fields."""
    # Mock home directory to use tmp_path
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: fake_home)
    
    campaign_id = "FULL_CAMPAIGN"
    metadata = {
        "platform_id": "R/V Falkor",
        "platform_name": "Research Vessel Falkor",
        "platform_type": "Research Vessel",
        "description": "Comprehensive test campaign",
        "start_date": "2023-01-01",
        "end_date": "2023-12-31",
        "bbox": [-180.0, -90.0, 180.0, 90.0],
        "attribution": "Test Institution",
        "license": "CC-BY-4.0",
        "doi": "10.5281/zenodo.123456",
        "source_repository": "https://github.com/test/repo",
        "keywords": ["oceanography", "test"],
        "chief_scientist": "Dr. Test",
        "institution": "Test University",
        "project": "Test Project",
        "funding": "Test Grant",
    }
    
    campaign_dir = create_campaign(
        campaign_id=campaign_id,
        metadata=metadata,
        verbose=False,
    )
    
    # Load and verify all fields
    with open(campaign_dir / "campaign.json") as f:
        saved = json.load(f)
    
    for key, value in metadata.items():
        assert saved[key] == value


def test_create_campaign_duplicate_error(tmp_path, monkeypatch):
    """Test that creating duplicate campaign raises error."""
    # Mock home directory to use tmp_path
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: fake_home)
    
    campaign_id = "DUPLICATE_TEST"
    metadata = {"platform_id": "test"}
    
    # Create first campaign
    create_campaign(campaign_id, metadata)
    
    # Try to create again - should fail
    with pytest.raises(ValueError, match="already exists"):
        create_campaign(campaign_id, metadata)


def test_create_campaign_invalid_start_date(tmp_path, monkeypatch):
    """Test that invalid start date raises error."""
    # Mock home directory to use tmp_path
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: fake_home)
    
    campaign_id = "INVALID_DATE"
    metadata = {
        "platform_id": "test",
        "start_date": "invalid-date",
    }
    
    with pytest.raises(ValueError, match="Invalid start_date format"):
        create_campaign(campaign_id, metadata)


def test_create_campaign_invalid_end_date(tmp_path, monkeypatch):
    """Test that invalid end date raises error."""
    # Mock home directory to use tmp_path
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: fake_home)
    
    campaign_id = "INVALID_DATE"
    metadata = {
        "platform_id": "test",
        "end_date": "2023-13-45",  # Invalid month/day
    }
    
    with pytest.raises(ValueError, match="Invalid end_date format"):
        create_campaign(campaign_id, metadata)


def test_create_campaign_invalid_bbox_format(tmp_path, monkeypatch):
    """Test that invalid bbox format raises error."""
    # Mock home directory to use tmp_path
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: fake_home)
    
    campaign_id = "INVALID_BBOX"
    metadata = {
        "platform_id": "test",
        "bbox": [-180, -90, 180],  # Only 3 values
    }
    
    with pytest.raises(ValueError, match="bbox must be a list of 4 numbers"):
        create_campaign(campaign_id, metadata)


def test_create_campaign_invalid_bbox_longitude(tmp_path, monkeypatch):
    """Test that out-of-range longitude raises error."""
    # Mock home directory to use tmp_path
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: fake_home)
    
    campaign_id = "INVALID_LON"
    metadata = {
        "platform_id": "test",
        "bbox": [-200, -90, 180, 90],  # Longitude < -180
    }
    
    with pytest.raises(ValueError, match="Longitude values must be in range"):
        create_campaign(campaign_id, metadata)


def test_create_campaign_invalid_bbox_latitude(tmp_path, monkeypatch):
    """Test that out-of-range latitude raises error."""
    # Mock home directory to use tmp_path
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: fake_home)
    
    campaign_id = "INVALID_LAT"
    metadata = {
        "platform_id": "test",
        "bbox": [-180, -95, 180, 90],  # Latitude < -90
    }
    
    with pytest.raises(ValueError, match="Latitude values must be in range"):
        create_campaign(campaign_id, metadata)


def test_create_campaign_invalid_bbox_order_lon(tmp_path, monkeypatch):
    """Test that minlon >= maxlon raises error."""
    # Mock home directory to use tmp_path
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: fake_home)
    
    campaign_id = "INVALID_ORDER"
    metadata = {
        "platform_id": "test",
        "bbox": [180, -90, -180, 90],  # minlon > maxlon
    }
    
    with pytest.raises(ValueError, match="minlon .* must be less than maxlon"):
        create_campaign(campaign_id, metadata)


def test_create_campaign_invalid_bbox_order_lat(tmp_path, monkeypatch):
    """Test that minlat >= maxlat raises error."""
    # Mock home directory to use tmp_path
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: fake_home)
    
    campaign_id = "INVALID_ORDER"
    metadata = {
        "platform_id": "test",
        "bbox": [-180, 90, 180, -90],  # minlat > maxlat
    }
    
    with pytest.raises(ValueError, match="minlat .* must be less than maxlat"):
        create_campaign(campaign_id, metadata)


def test_load_campaign_metadata(tmp_path, monkeypatch):
    """Test loading campaign metadata."""
    # Mock home directory to use tmp_path
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: fake_home)
    
    campaign_id = "LOAD_TEST"
    metadata = {
        "platform_id": "test_platform",
        "description": "Test",
    }
    
    campaign_dir = create_campaign(campaign_id, metadata)
    
    # Load metadata using campaign_id
    loaded = load_campaign_metadata(campaign_id)
    
    assert loaded is not None
    assert loaded["campaign_id"] == campaign_id
    assert loaded["platform_id"] == "test_platform"
    assert loaded["description"] == "Test"


def test_load_campaign_metadata_not_found(tmp_path, monkeypatch):
    """Test loading metadata from non-existent campaign."""
    # Mock home directory to use tmp_path
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: fake_home)
    
    result = load_campaign_metadata("nonexistent")
    assert result is None


def test_update_campaign_metadata(tmp_path, monkeypatch):
    """Test updating campaign metadata."""
    # Mock home directory to use tmp_path
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: fake_home)
    
    campaign_id = "UPDATE_TEST"
    metadata = {
        "platform_id": "original",
        "description": "Original description",
    }
    
    campaign_dir = create_campaign(campaign_id, metadata)
    
    # Update metadata using campaign_id
    updates = {
        "description": "Updated description",
        "new_field": "new value",
    }
    update_campaign_metadata(campaign_id, updates)
    
    # Load and verify
    loaded = load_campaign_metadata(campaign_id)
    assert loaded["platform_id"] == "original"  # Unchanged
    assert loaded["description"] == "Updated description"  # Updated
    assert loaded["new_field"] == "new value"  # New field added


def test_update_campaign_metadata_not_found(tmp_path, monkeypatch):
    """Test updating non-existent campaign raises error."""
    # Mock home directory to use tmp_path
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: fake_home)
    
    with pytest.raises(FileNotFoundError):
        update_campaign_metadata("nonexistent", {"test": "value"})


def test_create_campaign_valid_iso_dates(tmp_path, monkeypatch):
    """Test campaign creation with valid ISO 8601 dates."""
    # Mock home directory to use tmp_path
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: fake_home)
    
    campaign_id = "DATE_TEST"
    
    # Test various valid formats
    test_cases = [
        "2023-01-15",
        "2023-01-15T12:30:00",
        "2023-01-15T12:30:00Z",
        "2023-01-15T12:30:00+00:00",
    ]
    
    for i, date_str in enumerate(test_cases):
        metadata = {
            "platform_id": "test",
            "start_date": date_str,
        }
        
        campaign_dir = create_campaign(
            f"{campaign_id}_{i}",
            metadata,
        )
        
        loaded = load_campaign_metadata(f"{campaign_id}_{i}")
        assert loaded["start_date"] == date_str


def test_create_campaign_verbose_output(tmp_path, monkeypatch, capsys):
    """Test that verbose mode produces output."""
    # Mock home directory to use tmp_path
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: fake_home)
    
    campaign_id = "VERBOSE_TEST"
    metadata = {"platform_id": "test"}
    
    create_campaign(campaign_id, metadata, verbose=True)
    
    captured = capsys.readouterr()
    assert "Created campaign directory" in captured.out
    assert "Wrote metadata to" in captured.out
    assert "Metadata fields:" in captured.out


def test_create_campaign_existing_dir_no_metadata(tmp_path, monkeypatch):
    """Test creating campaign when directory exists but no metadata."""
    # Mock home directory to use tmp_path
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: fake_home)
    
    campaign_id = "EXISTING_DIR"
    campaign_dir = fake_home / ".oceanstream" / "campaigns" / campaign_id
    campaign_dir.mkdir(parents=True)
    
    # Should succeed and create metadata
    result = create_campaign(
        campaign_id,
        {"platform_id": "test"},
        verbose=False,
    )
    
    assert result == campaign_dir
    assert (campaign_dir / "campaign.json").exists()


def test_list_campaigns_empty(tmp_path, monkeypatch):
    """Test listing campaigns when none exist."""
    # Mock home directory to use tmp_path
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: fake_home)
    
    campaigns = list_campaigns()
    assert campaigns == []


def test_list_campaigns_multiple(tmp_path, monkeypatch):
    """Test listing multiple campaigns."""
    # Mock home directory to use tmp_path
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: fake_home)
    
    # Create multiple campaigns
    create_campaign("CAMPAIGN_A", {"platform_id": "platform_a"})
    create_campaign("CAMPAIGN_B", {"platform_id": "platform_b"})
    create_campaign("CAMPAIGN_C", {"platform_id": "platform_c", "description": "Test C"})
    
    campaigns = list_campaigns()
    
    assert len(campaigns) == 3
    
    # Should be sorted by campaign_id
    assert campaigns[0]["campaign_id"] == "CAMPAIGN_A"
    assert campaigns[1]["campaign_id"] == "CAMPAIGN_B"
    assert campaigns[2]["campaign_id"] == "CAMPAIGN_C"
    
    # Check metadata
    assert campaigns[0]["platform_id"] == "platform_a"
    assert campaigns[1]["platform_id"] == "platform_b"
    assert campaigns[2]["description"] == "Test C"


def test_list_campaigns_skip_invalid(tmp_path, monkeypatch):
    """Test that list_campaigns skips invalid metadata files."""
    # Mock home directory to use tmp_path
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: fake_home)
    
    campaigns_dir = fake_home / ".oceanstream" / "campaigns"
    campaigns_dir.mkdir(parents=True)
    
    # Create valid campaign
    create_campaign("VALID", {"platform_id": "test"})
    
    # Create invalid campaign (corrupted JSON)
    invalid_dir = campaigns_dir / "INVALID"
    invalid_dir.mkdir()
    with open(invalid_dir / "campaign.json", "w") as f:
        f.write("{ invalid json")
    
    # Create directory without metadata
    no_metadata_dir = campaigns_dir / "NO_METADATA"
    no_metadata_dir.mkdir()
    
    campaigns = list_campaigns()
    
    # Should only return the valid campaign
    assert len(campaigns) == 1
    assert campaigns[0]["campaign_id"] == "VALID"


def test_delete_campaign(tmp_path, monkeypatch):
    """Test deleting a campaign."""
    # Mock home directory to use tmp_path
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: fake_home)
    
    # Create campaign
    campaign_id = "DELETE_TEST"
    create_campaign(campaign_id, {"platform_id": "test"})
    
    campaigns_dir = fake_home / ".oceanstream" / "campaigns"
    campaign_dir = campaigns_dir / campaign_id
    
    # Verify it exists
    assert campaign_dir.exists()
    assert (campaign_dir / "campaign.json").exists()
    
    # Delete it
    delete_campaign(campaign_id)
    
    # Verify it's gone
    assert not campaign_dir.exists()


def test_delete_campaign_verbose(tmp_path, monkeypatch, capsys):
    """Test deleting a campaign with verbose output."""
    # Mock home directory to use tmp_path
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: fake_home)
    
    campaign_id = "VERBOSE_DELETE"
    create_campaign(campaign_id, {"platform_id": "test"})
    
    delete_campaign(campaign_id, verbose=True)
    
    captured = capsys.readouterr()
    assert "Deleted campaign" in captured.out
    assert campaign_id in captured.out


def test_delete_campaign_not_found(tmp_path, monkeypatch):
    """Test deleting non-existent campaign raises error."""
    # Mock home directory to use tmp_path
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: fake_home)
    
    with pytest.raises(FileNotFoundError, match="not found"):
        delete_campaign("NONEXISTENT")


def test_delete_campaign_removes_all_contents(tmp_path, monkeypatch):
    """Test that delete removes campaign directory and all contents."""
    # Mock home directory to use tmp_path
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: fake_home)
    
    campaign_id = "DELETE_ALL"
    campaign_dir = create_campaign(campaign_id, {"platform_id": "test"})
    
    # Add some extra files to the campaign directory
    (campaign_dir / "extra_file.txt").write_text("extra content")
    subdir = campaign_dir / "subdir"
    subdir.mkdir()
    (subdir / "nested_file.txt").write_text("nested content")
    
    # Delete campaign
    delete_campaign(campaign_id)
    
    # Verify entire directory is gone
    assert not campaign_dir.exists()
