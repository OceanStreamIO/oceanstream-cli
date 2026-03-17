import pytest
from typer.testing import CliRunner

from oceanstream.providers import list_providers


def test_list_providers_function():
    """Test the list_providers function returns expected providers."""
    providers = list_providers()
    assert isinstance(providers, list)
    assert len(providers) > 0
    assert "saildrone" not in providers  # alias, hidden from listing
    assert "noaa_pmel" in providers
    assert "generic" in providers
    # Verify sorted order
    assert providers == sorted(providers)


@pytest.mark.integration
def test_providers_cli_command():
    """Test the 'oceanstream providers' CLI command."""
    from oceanstream import cli as cli_module
    
    runner = CliRunner()
    result = runner.invoke(cli_module.app, ["providers"])
    
    assert result.exit_code == 0
    assert "Available providers:" in result.output
    assert "saildrone" not in result.output  # alias, hidden from listing


@pytest.mark.integration
def test_invalid_provider_error():
    """Test that using an invalid provider shows a helpful error."""
    from oceanstream import cli as cli_module
    
    runner = CliRunner()
    result = runner.invoke(
        cli_module.app,
        ["process", "--provider", "invalid_provider", "geotrack", "convert", "--dry-run"]
    )
    
    assert result.exit_code == 1
    assert "Unknown provider" in result.output
    assert "Available:" in result.output
