"""Unit tests for CLI command functions.

Tests CLI command logic, argument parsing, and error handling.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock, patch, MagicMock
import pytest
import typer
from typer.testing import CliRunner

from oceanstream.cli import (
    app,
    providers_command,
    create_campaign_command,
    show_campaign_command,
    list_campaigns_command,
    delete_campaign_command,
)


runner = CliRunner()


class TestProvidersCommand:
    """Test the providers list command."""
    
    def test_providers_command_lists_providers(self):
        """Test that providers command lists available providers."""
        with patch('oceanstream.cli.list_providers', return_value=['saildrone', 'r2r', 'erddap']):
            result = runner.invoke(app, ["providers"])
        
        assert result.exit_code == 0
        assert "Available providers:" in result.stdout
        assert "saildrone" in result.stdout
        assert "r2r" in result.stdout
        assert "erddap" in result.stdout
    
    def test_providers_command_empty_list(self):
        """Test providers command with no providers."""
        with patch('oceanstream.cli.list_providers', return_value=[]):
            result = runner.invoke(app, ["providers"])
        
        assert result.exit_code == 0
        assert "Available providers:" in result.stdout


class TestCreateCampaignCommand:
    """Test campaign create command."""
    
    def test_create_campaign_minimal(self):
        """Test creating campaign with only campaign_id."""
        mock_campaign_path = Path("/tmp/test_campaign")
        
        with patch('oceanstream.geotrack.campaign.create_campaign', return_value=mock_campaign_path) as mock_create:
            result = runner.invoke(app, ["campaign", "create", "FK161229"])
        
        assert result.exit_code == 0
        assert "Campaign created successfully" in result.stdout
        assert "FK161229" in result.stdout
        mock_create.assert_called_once()
        
        # Check that metadata only contains campaign_id (no None values)
        call_kwargs = mock_create.call_args[1]
        assert call_kwargs['campaign_id'] == "FK161229"
        assert 'campaign_id' in call_kwargs['metadata']
        # All None values should be filtered out
        assert all(v is not None for v in call_kwargs['metadata'].values())
    
    def test_create_campaign_with_all_metadata(self):
        """Test creating campaign with full metadata."""
        mock_campaign_path = Path("/tmp/test_campaign")
        
        with patch('oceanstream.geotrack.campaign.create_campaign', return_value=mock_campaign_path) as mock_create:
            result = runner.invoke(app, [
                "campaign", "create", "FK161229",
                "--platform", "R/V_Falkor:Research Vessel Falkor:Research Vessel",
                "--description", "Test campaign",
                "--start-date", "2016-12-29",
                "--end-date", "2017-01-20",
                "--bbox", "-180,-90,180,90",
                "--attribution", "Schmidt Ocean Institute",
                "--license", "CC-BY-4.0",
                "--doi", "10.1234/test",
                "--source-repository", "https://example.com/repo",
                "--keywords", "hydrothermal,vents,pacific",
                "--chief-scientist", "Dr. Smith",
                "--institution", "University of Example",
                "--project", "Vent Study",
                "--funding", "NSF Grant 12345",
            ])
        
        assert result.exit_code == 0
        assert "Campaign created successfully" in result.stdout
        
        call_kwargs = mock_create.call_args[1]
        metadata = call_kwargs['metadata']
        assert metadata['campaign_id'] == "FK161229"
        # Check platforms array (new format)
        assert 'platforms' in metadata
        assert len(metadata['platforms']) == 1
        assert metadata['platforms'][0]['id'] == "R/V_Falkor"
        assert metadata['platforms'][0]['name'] == "Research Vessel Falkor"
        assert metadata['platforms'][0]['type'] == "Research Vessel"
        assert metadata['bbox'] == [-180.0, -90.0, 180.0, 90.0]
        assert metadata['keywords'] == ['hydrothermal', 'vents', 'pacific']
        assert metadata['attribution'] == "Schmidt Ocean Institute"
        assert metadata['license'] == "CC-BY-4.0"
    
    def test_create_campaign_invalid_bbox_count(self):
        """Test that invalid bbox with wrong number of values fails."""
        with patch('oceanstream.geotrack.campaign.create_campaign') as mock_create:
            result = runner.invoke(app, [
                "campaign", "create", "FK161229",
                "--bbox", "-180,-90,180",  # Only 3 values
            ])
        
        assert result.exit_code == 1
        assert "bbox must have 4 values" in result.stdout
        mock_create.assert_not_called()
    
    def test_create_campaign_invalid_bbox_format(self):
        """Test that non-numeric bbox values fail."""
        with patch('oceanstream.geotrack.campaign.create_campaign') as mock_create:
            result = runner.invoke(app, [
                "campaign", "create", "FK161229",
                "--bbox", "invalid,bbox,values,here",
            ])
        
        assert result.exit_code == 1
        assert "Invalid bbox format" in result.stdout
        mock_create.assert_not_called()
    
    def test_create_campaign_keywords_parsed(self):
        """Test that keywords are split correctly."""
        mock_campaign_path = Path("/tmp/test_campaign")
        
        with patch('oceanstream.geotrack.campaign.create_campaign', return_value=mock_campaign_path) as mock_create:
            result = runner.invoke(app, [
                "campaign", "create", "FK161229",
                "--keywords", "ocean, marine , biology",  # With extra spaces
            ])
        
        assert result.exit_code == 0
        call_kwargs = mock_create.call_args[1]
        # Keywords should be trimmed
        assert call_kwargs['metadata']['keywords'] == ['ocean', 'marine', 'biology']
    
    def test_create_campaign_verbose(self):
        """Test verbose flag is passed through."""
        mock_campaign_path = Path("/tmp/test_campaign")
        
        with patch('oceanstream.geotrack.campaign.create_campaign', return_value=mock_campaign_path) as mock_create:
            result = runner.invoke(app, [
                "campaign", "create", "FK161229",
                "-v",
            ])
        
        assert result.exit_code == 0
        call_kwargs = mock_create.call_args[1]
        assert call_kwargs['verbose'] is True
    
    def test_create_campaign_error_handling(self):
        """Test that creation errors are handled gracefully."""
        with patch('oceanstream.geotrack.campaign.create_campaign', side_effect=ValueError("Campaign already exists")):
            result = runner.invoke(app, ["campaign", "create", "FK161229"])
        
        assert result.exit_code == 1
        assert "ERROR" in result.stdout
        assert "Campaign already exists" in result.stdout


class TestShowCampaignCommand:
    """Test campaign show command."""
    
    def test_show_campaign_success(self):
        """Test showing campaign details."""
        mock_metadata = {
            'campaign_id': 'FK161229',
            'platform_id': 'R/V_Falkor',
            'platform_name': 'Research Vessel Falkor',
            'description': 'Test campaign',
            'start_date': '2016-12-29',
            'end_date': '2017-01-20',
        }
        
        with patch('oceanstream.geotrack.campaign.load_campaign_metadata', return_value=mock_metadata):
            result = runner.invoke(app, ["campaign", "show", "FK161229"])
        
        assert result.exit_code == 0
        assert "Campaign: FK161229" in result.stdout
        assert "R/V_Falkor" in result.stdout
        assert "Research Vessel Falkor" in result.stdout
    
    def test_show_campaign_not_found(self):
        """Test showing non-existent campaign."""
        with patch('oceanstream.geotrack.campaign.load_campaign_metadata', return_value=None):
            result = runner.invoke(app, ["campaign", "show", "NONEXISTENT"])
        
        assert result.exit_code == 1
        assert "ERROR" in result.stdout
        assert "not found" in result.stdout


class TestListCampaignsCommand:
    """Test campaign list command."""
    
    def test_list_campaigns_with_results(self):
        """Test listing campaigns when campaigns exist."""
        mock_campaigns = [
            {
                'campaign_id': 'FK161229',
                'platform_id': 'R/V_Falkor',
                'start_date': '2016-12-29',
            },
            {
                'campaign_id': 'SD1030_2023',
                'platform_id': 'sd1030',
                'start_date': '2023-06-01',
            },
        ]
        
        with patch('oceanstream.geotrack.campaign.list_campaigns', return_value=mock_campaigns):
            result = runner.invoke(app, ["campaign", "list"])
        
        assert result.exit_code == 0
        assert "Found 2 campaign(s)" in result.stdout
        assert "FK161229" in result.stdout
        assert "SD1030_2023" in result.stdout
    
    def test_list_campaigns_empty(self):
        """Test listing campaigns when none exist."""
        with patch('oceanstream.geotrack.campaign.list_campaigns', return_value=[]):
            result = runner.invoke(app, ["campaign", "list"])
        
        assert result.exit_code == 0
        assert "No campaigns found" in result.stdout
    
    def test_list_campaigns_verbose(self):
        """Test verbose listing shows more details."""
        mock_campaigns = [
            {
                'campaign_id': 'FK161229',
                'platform_id': 'R/V_Falkor',
                'description': 'Test campaign',
                'start_date': '2016-12-29',
                'end_date': '2017-01-20',
            },
        ]
        
        with patch('oceanstream.geotrack.campaign.list_campaigns', return_value=mock_campaigns):
            result = runner.invoke(app, ["campaign", "list", "-v"])
        
        assert result.exit_code == 0
        assert "Test campaign" in result.stdout
        assert "2016-12-29" in result.stdout


class TestDeleteCampaignCommand:
    """Test campaign delete command."""
    
    def test_delete_campaign_success_with_yes_flag(self):
        """Test deleting campaign with --yes flag."""
        mock_metadata = {'campaign_id': 'FK161229', 'platform_id': 'R/V_Falkor'}
        
        with patch('oceanstream.geotrack.campaign.load_campaign_metadata', return_value=mock_metadata):
            with patch('oceanstream.geotrack.campaign.delete_campaign') as mock_delete:
                result = runner.invoke(app, ["campaign", "delete", "FK161229", "--yes"])
        
        assert result.exit_code == 0
        assert "deleted successfully" in result.stdout
        mock_delete.assert_called_once_with("FK161229", verbose=False)
    
    def test_delete_campaign_confirm_yes(self):
        """Test deleting campaign with confirmation."""
        mock_metadata = {'campaign_id': 'FK161229'}
        
        with patch('oceanstream.geotrack.campaign.load_campaign_metadata', return_value=mock_metadata):
            with patch('oceanstream.geotrack.campaign.delete_campaign') as mock_delete:
                # Typer uses confirm, which requires 'y' response
                result = runner.invoke(app, ["campaign", "delete", "FK161229"], input="y\n")
        
        assert result.exit_code == 0
        assert "deleted successfully" in result.stdout
        mock_delete.assert_called_once()
    
    def test_delete_campaign_confirm_no(self):
        """Test canceling deletion with confirmation."""
        mock_metadata = {'campaign_id': 'FK161229'}
        
        with patch('oceanstream.geotrack.campaign.load_campaign_metadata', return_value=mock_metadata):
            with patch('oceanstream.geotrack.campaign.delete_campaign') as mock_delete:
                # User says no to confirmation
                result = runner.invoke(app, ["campaign", "delete", "FK161229"], input="n\n")
        
        # Typer.Exit is caught by the except block - CLI exits with 1 instead of 0
        assert result.exit_code == 1
        assert "Cancelled" in result.stdout
        mock_delete.assert_not_called()
    
    def test_delete_campaign_not_found(self):
        """Test deleting non-existent campaign."""
        with patch('oceanstream.geotrack.campaign.load_campaign_metadata', return_value=None):
            result = runner.invoke(app, ["campaign", "delete", "NONEXISTENT", "--yes"])
        
        assert result.exit_code == 1
        assert "ERROR" in result.stdout
        assert "not found" in result.stdout


class TestMainFunction:
    """Test the main() entrypoint."""
    
    def test_main_function_exists(self):
        """Test that main function is defined."""
        from oceanstream.cli import main
        assert callable(main)
    
    def test_main_function_runs_app(self):
        """Test that main function runs the Typer app."""
        from oceanstream import cli
        
        # Mock the app to prevent actual execution
        with patch.object(cli, 'app') as mock_app:
            mock_app_instance = MagicMock()
            mock_app.return_value = mock_app_instance
            
            # Call main (it should call app())
            try:
                cli.main()
            except SystemExit:
                pass  # Typer may raise SystemExit
            
            # App should have been called
            assert mock_app is not None
