"""Integration tests for campaign create CLI command."""

import json
import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from oceanstream.cli import app

runner = CliRunner()


@pytest.fixture
def clean_campaign(request):
    """Fixture to clean up test campaigns after tests."""
    campaign_ids = []
    
    def register(campaign_id: str):
        campaign_ids.append(campaign_id)
        return campaign_id
    
    yield register
    
    # Cleanup after test
    for campaign_id in campaign_ids:
        campaign_dir = Path.home() / ".oceanstream" / "campaigns" / campaign_id
        if campaign_dir.exists():
            shutil.rmtree(campaign_dir)


class TestCampaignCreateCLI:
    """Tests for non-interactive campaign create command."""
    
    def test_create_campaign_minimal(self, clean_campaign):
        """Test creating campaign with just campaign_id."""
        campaign_id = clean_campaign("TEST_CLI_MINIMAL")
        
        result = runner.invoke(app, [
            "campaign", "create", campaign_id
        ])
        
        assert result.exit_code == 0
        assert "Campaign created successfully" in result.stdout
        assert campaign_id in result.stdout
        
        # Verify metadata file exists
        metadata_file = Path.home() / ".oceanstream" / "campaigns" / campaign_id / "campaign.json"
        assert metadata_file.exists()
        
        with open(metadata_file) as f:
            metadata = json.load(f)
        
        assert metadata["campaign_id"] == campaign_id
    
    def test_create_campaign_with_output_dir(self, clean_campaign):
        """Test creating campaign with output_dir (including cloud URI)."""
        campaign_id = clean_campaign("TEST_CLI_OUTPUT_DIR")
        
        result = runner.invoke(app, [
            "campaign", "create", campaign_id,
            "--output-dir", "az://mycontainer/campaigns"
        ])
        
        assert result.exit_code == 0
        assert "Campaign created successfully" in result.stdout
        assert "az://mycontainer/campaigns" in result.stdout
        
        # Verify output_dir in metadata
        metadata_file = Path.home() / ".oceanstream" / "campaigns" / campaign_id / "campaign.json"
        with open(metadata_file) as f:
            metadata = json.load(f)
        
        assert metadata["output_dir"] == "az://mycontainer/campaigns"
    
    def test_create_campaign_with_all_options(self, clean_campaign):
        """Test creating campaign with all CLI options."""
        campaign_id = clean_campaign("TEST_CLI_FULL")
        
        result = runner.invoke(app, [
            "campaign", "create", campaign_id,
            "--output-dir", "s3://bucket/path",
            "--platform", "R/V Falkor:Research Vessel Falkor:Research Vessel",
            "--description", "Test campaign description",
            "--start-date", "2024-01-01",
            "--end-date", "2024-12-31",
            "--bbox", "-180,-90,180,90",
            "--attribution", "Test Institution",
            "--license", "CC-BY-4.0",
            "--doi", "10.5281/zenodo.123456",
            "--source-repository", "https://github.com/test/repo",
            "--keywords", "oceanography,test,data",
            "--chief-scientist", "Dr. Test",
            "--institution", "Test University",
            "--project", "Test Project",
            "--funding", "Test Grant #12345",
            "-v"
        ])
        
        assert result.exit_code == 0
        assert "Campaign created successfully" in result.stdout
        
        # Verify all fields in metadata
        metadata_file = Path.home() / ".oceanstream" / "campaigns" / campaign_id / "campaign.json"
        with open(metadata_file) as f:
            metadata = json.load(f)
        
        assert metadata["campaign_id"] == campaign_id
        assert metadata["output_dir"] == "s3://bucket/path"
        # Check platforms array (new format)
        assert "platforms" in metadata
        assert len(metadata["platforms"]) == 1
        assert metadata["platforms"][0]["id"] == "R/V Falkor"
        assert metadata["platforms"][0]["name"] == "Research Vessel Falkor"
        assert metadata["platforms"][0]["type"] == "Research Vessel"
        assert metadata["description"] == "Test campaign description"
        assert metadata["start_date"] == "2024-01-01"
        assert metadata["end_date"] == "2024-12-31"
        assert metadata["bbox"] == [-180.0, -90.0, 180.0, 90.0]
        assert metadata["attribution"] == "Test Institution"
        assert metadata["license"] == "CC-BY-4.0"
        assert metadata["doi"] == "10.5281/zenodo.123456"
        assert metadata["source_repository"] == "https://github.com/test/repo"
        assert metadata["keywords"] == ["oceanography", "test", "data"]
        assert metadata["chief_scientist"] == "Dr. Test"
        assert metadata["institution"] == "Test University"
        assert metadata["project"] == "Test Project"
        assert metadata["funding"] == "Test Grant #12345"
    
    def test_create_campaign_duplicate_error(self, clean_campaign):
        """Test that creating duplicate campaign shows error."""
        campaign_id = clean_campaign("TEST_CLI_DUPLICATE")
        
        # Create first campaign
        result1 = runner.invoke(app, ["campaign", "create", campaign_id])
        assert result1.exit_code == 0
        
        # Try to create duplicate
        result2 = runner.invoke(app, ["campaign", "create", campaign_id])
        assert result2.exit_code != 0
        assert "already exists" in result2.stdout


class TestCampaignCreateInteractive:
    """Tests for interactive campaign create command."""
    
    def test_interactive_mode_minimal(self, clean_campaign):
        """Test interactive mode with minimal input (just campaign ID)."""
        campaign_id = clean_campaign("TEST_INTERACTIVE_MIN")
        
        # Simulate user input: campaign_id + Enter for all optional fields
        # Order: campaign_id, output_dir, platform_id, platform_name, platform_type,
        #        description, start_date, end_date, bbox, attribution, license,
        #        doi, source_repository, chief_scientist, institution, project, funding, keywords
        user_input = "\n".join([
            campaign_id,  # Campaign ID
            "",  # output_dir
            "",  # platform_id
            "",  # platform_name
            "",  # platform_type (skip)
            "",  # description
            "",  # start_date
            "",  # end_date
            "",  # bbox
            "",  # attribution
            "",  # license
            "",  # doi
            "",  # source_repository
            "",  # chief_scientist
            "",  # institution
            "",  # project
            "",  # funding
            "",  # keywords
            "",  # extra newline for safety
        ])
        
        result = runner.invoke(app, ["campaign", "create"], input=user_input)
        
        # Debug output on failure
        if result.exit_code != 0:
            print("STDOUT:", result.stdout)
            if result.exception:
                import traceback
                traceback.print_exception(type(result.exception), result.exception, result.exception.__traceback__)
        
        assert result.exit_code == 0
        assert "Create Campaign" in result.stdout
        assert "Campaign created successfully" in result.stdout
        
        # Verify campaign was created
        metadata_file = Path.home() / ".oceanstream" / "campaigns" / campaign_id / "campaign.json"
        assert metadata_file.exists()
    
    def test_interactive_mode_with_cloud_uri(self, clean_campaign):
        """Test interactive mode with cloud URI output."""
        campaign_id = clean_campaign("TEST_INTERACTIVE_CLOUD")
        
        # Simulate user input with cloud URI
        user_input = "\n".join([
            campaign_id,  # Campaign ID
            "az://mycontainer/campaigns",  # output_dir (cloud URI)
            "SD1030",  # platform_id
            "Saildrone Explorer 1030",  # platform_name
            "1",  # platform_type = USV
            "Test interactive campaign",  # description
            "2024-01-01",  # start_date
            "2024-12-31",  # end_date
            "",  # bbox (skip)
            "Saildrone Inc.",  # attribution
            "1",  # license = CC-BY-4.0
            "",  # doi (skip)
            "",  # source_repository (skip)
            "Dr. Ocean",  # chief_scientist
            "Ocean Institute",  # institution
            "Saildrone Mission",  # project
            "NSF Grant",  # funding
            "saildrone,oceanography",  # keywords
            "",  # extra newline for safety
        ])
        
        result = runner.invoke(app, ["campaign", "create"], input=user_input)
        
        assert result.exit_code == 0
        assert "Campaign created successfully" in result.stdout
        
        # Verify metadata
        metadata_file = Path.home() / ".oceanstream" / "campaigns" / campaign_id / "campaign.json"
        with open(metadata_file) as f:
            metadata = json.load(f)
        
        assert metadata["campaign_id"] == campaign_id
        assert metadata["output_dir"] == "az://mycontainer/campaigns"
        assert metadata["platform_id"] == "SD1030"
        assert metadata["platform_name"] == "Saildrone Explorer 1030"
        assert metadata["platform_type"] == "USV"
        assert metadata["description"] == "Test interactive campaign"
        assert metadata["attribution"] == "Saildrone Inc."
        assert metadata["license"] == "CC-BY-4.0"
        assert metadata["chief_scientist"] == "Dr. Ocean"
        assert metadata["keywords"] == ["saildrone", "oceanography"]
    
    def test_interactive_mode_research_vessel(self, clean_campaign):
        """Test interactive mode for research vessel campaign."""
        campaign_id = clean_campaign("TEST_INTERACTIVE_RV")
        
        user_input = "\n".join([
            campaign_id,  # Campaign ID
            "./data/output",  # output_dir (local)
            "FK",  # platform_id
            "R/V Falkor",  # platform_name
            "3",  # platform_type = Research Vessel
            "Deep sea exploration",  # description
            "",  # start_date (skip)
            "",  # end_date (skip)
            "-180,-90,180,90",  # bbox (global)
            "Schmidt Ocean Institute",  # attribution
            "2",  # license = CC0
            "10.5281/zenodo.123456",  # doi
            "https://doi.org/example",  # source_repository
            "Dr. Schmidt",  # chief_scientist
            "Schmidt Ocean Institute",  # institution
            "Falkor Expedition",  # project
            "",  # funding (skip)
            "deep-sea,exploration,marine",  # keywords
            "",  # extra newline for safety
        ])
        
        result = runner.invoke(app, ["campaign", "create"], input=user_input)
        
        assert result.exit_code == 0
        
        # Verify metadata
        metadata_file = Path.home() / ".oceanstream" / "campaigns" / campaign_id / "campaign.json"
        with open(metadata_file) as f:
            metadata = json.load(f)
        
        assert metadata["platform_type"] == "Research Vessel"
        assert metadata["license"] == "CC0"
        assert metadata["bbox"] == [-180.0, -90.0, 180.0, 90.0]
        assert metadata["doi"] == "10.5281/zenodo.123456"
    
    def test_interactive_mode_custom_platform_type(self, clean_campaign):
        """Test interactive mode with custom platform type."""
        campaign_id = clean_campaign("TEST_INTERACTIVE_CUSTOM")
        
        user_input = "\n".join([
            campaign_id,  # Campaign ID
            "",  # output_dir (skip)
            "AUV-01",  # platform_id
            "Custom AUV",  # platform_name
            "6",  # platform_type = Other (custom)
            "Underwater Glider",  # custom platform type
            "",  # description (skip)
            "",  # start_date (skip)
            "",  # end_date (skip)
            "",  # bbox (skip)
            "",  # attribution (skip)
            "4",  # license = Other (custom)
            "Apache-2.0",  # custom license
            "",  # doi (skip)
            "",  # source_repository (skip)
            "",  # chief_scientist (skip)
            "",  # institution (skip)
            "",  # project (skip)
            "",  # funding (skip)
            "",  # keywords (skip)
            "",  # extra newline for safety
        ])
        
        result = runner.invoke(app, ["campaign", "create"], input=user_input)
        
        assert result.exit_code == 0
        
        # Verify custom values
        metadata_file = Path.home() / ".oceanstream" / "campaigns" / campaign_id / "campaign.json"
        with open(metadata_file) as f:
            metadata = json.load(f)
        
        assert metadata["platform_type"] == "Underwater Glider"
        assert metadata["license"] == "Apache-2.0"


class TestCampaignCreateHelpText:
    """Tests for help text and prompts in campaign create."""
    
    def test_help_shows_interactive_example(self):
        """Test that help text mentions interactive mode."""
        result = runner.invoke(app, ["campaign", "create", "--help"])
        
        assert result.exit_code == 0
        assert "interactive wizard" in result.stdout.lower()
        assert "oceanstream campaign create" in result.stdout
    
    def test_interactive_shows_section_headers(self, clean_campaign):
        """Test that interactive mode shows organized sections."""
        campaign_id = clean_campaign("TEST_SECTIONS")
        
        # Minimal input to see prompts (need all 18 prompts)
        user_input = "\n".join([campaign_id] + [""] * 20)
        
        result = runner.invoke(app, ["campaign", "create"], input=user_input)
        
        # Check section headers are present (new minimalistic style)
        assert "Create Campaign" in result.stdout
        assert "Output" in result.stdout
        assert "Platform" in result.stdout
        assert "Details" in result.stdout
        assert "Attribution" in result.stdout
        assert "Team" in result.stdout
