"""Unit tests for OceanStream configuration system."""

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from oceanstream.configuration import (
    ConfigurationError,
    OceanStreamConfig,
    _substitute_env_vars,
    get_config,
    reset_config,
)


@pytest.fixture(autouse=True)
def reset_configuration():
    """Automatically reset configuration before and after each test."""
    reset_config()
    yield
    reset_config()


class TestEnvVarSubstitution:
    """Tests for environment variable substitution."""

    def test_substitute_simple_form(self):
        """Test simple $VAR form."""
        with patch.dict(os.environ, {"TEST_VAR": "test_value"}):
            result = _substitute_env_vars("$TEST_VAR")
            assert result == "test_value"

    def test_substitute_braces_form(self):
        """Test ${VAR} form."""
        with patch.dict(os.environ, {"TEST_VAR": "test_value"}):
            result = _substitute_env_vars("${TEST_VAR}")
            assert result == "test_value"

    def test_substitute_with_default(self):
        """Test ${VAR:-default} form."""
        # Variable not set, should use default
        result = _substitute_env_vars("${MISSING_VAR:-default_value}")
        assert result == "default_value"

        # Variable set, should use actual value
        with patch.dict(os.environ, {"PRESENT_VAR": "actual_value"}):
            result = _substitute_env_vars("${PRESENT_VAR:-default_value}")
            assert result == "actual_value"

    def test_substitute_mixed_content(self):
        """Test mixed literal and variable content."""
        with patch.dict(os.environ, {"ENV": "prod"}):
            result = _substitute_env_vars("oceanstream-${ENV}-data")
            assert result == "oceanstream-prod-data"

    def test_substitute_missing_required_var(self):
        """Test error when required variable is missing."""
        with pytest.raises(ConfigurationError, match="MISSING_VAR"):
            _substitute_env_vars("${MISSING_VAR}")

    def test_substitute_dict(self):
        """Test recursive substitution in dictionaries."""
        with patch.dict(os.environ, {"VAR1": "value1", "VAR2": "value2"}):
            data = {"key1": "${VAR1}", "key2": "${VAR2}"}
            result = _substitute_env_vars(data)
            assert result == {"key1": "value1", "key2": "value2"}

    def test_substitute_list(self):
        """Test recursive substitution in lists."""
        with patch.dict(os.environ, {"VAR": "value"}):
            data = ["${VAR}", "literal", "${VAR}"]
            result = _substitute_env_vars(data)
            assert result == ["value", "literal", "value"]

    def test_substitute_nested_structures(self):
        """Test recursive substitution in nested structures."""
        with patch.dict(os.environ, {"VAR": "value"}):
            data = {
                "level1": {"level2": ["${VAR}", {"level3": "${VAR}"}]},
                "simple": "${VAR}",
            }
            result = _substitute_env_vars(data)
            assert result["level1"]["level2"][0] == "value"
            assert result["level1"]["level2"][1]["level3"] == "value"
            assert result["simple"] == "value"

    def test_substitute_non_string_values(self):
        """Test that non-string values are unchanged."""
        result = _substitute_env_vars(42)
        assert result == 42

        result = _substitute_env_vars(True)
        assert result is True

        result = _substitute_env_vars(None)
        assert result is None


class TestOceanStreamConfig:
    """Tests for OceanStreamConfig class."""

    def test_default_config(self):
        """Test loading default configuration."""
        reset_config()
        config = OceanStreamConfig()
        # Default is ~/.oceanstream
        assert ".oceanstream" in str(config.metadata_dir)
        assert config.output_dir.name == "output"
        assert config.auto_register_campaigns is True

    def test_load_config_file(self, tmp_path):
        """Test loading configuration from file."""
        config_file = tmp_path / "test.toml"
        config_file.write_text(
            """
[paths]
metadata_dir = "/custom/metadata"
output_dir = "/custom/output"

[campaigns]
auto_register = false
"""
        )

        reset_config()
        config = OceanStreamConfig(config_file)
        assert str(config.metadata_dir) == "/custom/metadata"
        assert str(config.output_dir) == "/custom/output"
        assert config.auto_register_campaigns is False

    def test_config_file_not_found(self):
        """Test error when config file doesn't exist."""
        reset_config()
        with pytest.raises(ConfigurationError, match="not found"):
            OceanStreamConfig("nonexistent.toml")

    def test_auto_detect_config_file(self, tmp_path, monkeypatch):
        """Test auto-detection of oceanstream.toml."""
        # Change to temp directory
        monkeypatch.chdir(tmp_path)

        # Create oceanstream.toml
        config_file = tmp_path / "oceanstream.toml"
        config_file.write_text(
            """
[paths]
metadata_dir = "/auto/detected"
"""
        )

        reset_config()
        config = OceanStreamConfig()
        assert config.config_file == config_file
        assert str(config.metadata_dir) == "/auto/detected"

    def test_env_var_substitution_in_config(self, tmp_path):
        """Test environment variable substitution in config file."""
        config_file = tmp_path / "test.toml"
        config_file.write_text(
            """
[paths]
metadata_dir = "${HOME}/oceanstream"

[storage]
connection_string = "${AZURE_CONNECTION:-default_connection}"
"""
        )

        reset_config()
        with patch.dict(os.environ, {"HOME": "/home/user"}):
            config = OceanStreamConfig(config_file)
            # Should expand $HOME (may be resolved to absolute path)
            assert "oceanstream" in str(config.metadata_dir)
            # Should use default for missing AZURE_CONNECTION
            assert config.get("storage.connection_string") == "default_connection"

    def test_get_with_dot_notation(self, tmp_path):
        """Test getting nested values with dot notation."""
        config_file = tmp_path / "test.toml"
        config_file.write_text(
            """
[storage]
account_name = "test_account"
container_name = "test_container"
"""
        )

        reset_config()
        config = OceanStreamConfig(config_file)
        assert config.get("storage.account_name") == "test_account"
        assert config.get("storage.container_name") == "test_container"

    def test_get_with_default(self):
        """Test get() with default value."""
        reset_config()
        config = OceanStreamConfig()
        result = config.get("nonexistent.key", default="default_value")
        assert result == "default_value"

    def test_get_path_expansion(self, tmp_path):
        """Test get_path() expands ~ and resolves paths."""
        config_file = tmp_path / "test.toml"
        config_file.write_text(
            """
[paths]
metadata_dir = "~/oceanstream"
relative_dir = "./output"
"""
        )

        reset_config()
        config = OceanStreamConfig(config_file)

        # Should expand ~
        metadata_dir = config.get_path("paths.metadata_dir")
        assert not str(metadata_dir).startswith("~")
        assert metadata_dir.is_absolute()

        # Should resolve relative paths
        relative_dir = config.get_path("paths.relative_dir")
        assert relative_dir.is_absolute()

    def test_deep_merge(self, tmp_path):
        """Test that config values are merged with defaults."""
        config_file = tmp_path / "test.toml"
        config_file.write_text(
            """
[paths]
metadata_dir = "/custom/metadata"
# output_dir not specified, should use default
"""
        )

        reset_config()
        config = OceanStreamConfig(config_file)
        assert str(config.metadata_dir) == "/custom/metadata"
        assert config.output_dir.name == "output"  # Should have default


class TestGlobalConfig:
    """Tests for global config singleton."""

    def test_get_config_singleton(self):
        """Test that get_config() returns same instance."""
        reset_config()
        config1 = get_config()
        config2 = get_config()
        assert config1 is config2

    def test_get_config_with_file(self, tmp_path):
        """Test that get_config(file) creates new instance."""
        config_file = tmp_path / "test.toml"
        config_file.write_text(
            """
[paths]
metadata_dir = "/test"
"""
        )

        reset_config()
        config1 = get_config()
        config2 = get_config(config_file)
        assert config1 is not config2
        assert str(config2.metadata_dir) == "/test"

    def test_reset_config(self):
        """Test that reset_config() clears global instance."""
        reset_config()
        config1 = get_config()
        reset_config()
        config2 = get_config()
        assert config1 is not config2


class TestConfigurationIntegration:
    """Integration tests for configuration system."""

    def test_config_with_settings(self, tmp_path):
        """Test that Settings picks up config values."""
        # Create test config
        config_file = tmp_path / "test.toml"
        config_file.write_text(
            """
[paths]
metadata_dir = "/test/metadata"
"""
        )

        # Load config first
        reset_config()
        config = get_config(config_file)

        # Import settings (need to reload module to pick up new config)
        import sys

        if "oceanstream.config.settings" in sys.modules:
            del sys.modules["oceanstream.config.settings"]

        from oceanstream.config.settings import Settings

        # Settings should use config value
        assert "/test/metadata" in str(Settings.METADATA_DIR)

    def test_env_var_takes_precedence(self, tmp_path):
        """Test that environment variables override config file."""
        config_file = tmp_path / "test.toml"
        config_file.write_text(
            """
[paths]
metadata_dir = "/config/metadata"
"""
        )

        # Load config
        reset_config()
        config = get_config(config_file)

        # Import settings with env var set
        import sys

        if "oceanstream.config.settings" in sys.modules:
            del sys.modules["oceanstream.config.settings"]

        with patch.dict(os.environ, {"OCEANSTREAM_METADATA_DIR": "/env/metadata"}):
            from oceanstream.config.settings import Settings

            # Environment variable should take precedence
            assert str(Settings.METADATA_DIR) == "/env/metadata"


class TestSemanticConfiguration:
    """Tests for semantic configuration settings."""

    def test_semantic_defaults(self, tmp_path):
        """Test that semantic settings have correct defaults."""
        config_file = tmp_path / "test.toml"
        config_file.write_text(
            """
[paths]
metadata_dir = "/test/metadata"
"""
        )

        reset_config()
        config = get_config(config_file)
        
        # Test defaults via config.get()
        assert config.get("semantic.enable", None) is False
        assert config.get("semantic.generate_stac", None) is True
        assert config.get("semantic.min_confidence", None) == 0.7
        assert config.get("semantic.cf_table", None) == ""
        assert config.get("semantic.alias_table", None) == ""

    def test_semantic_from_config_file(self, tmp_path):
        """Test that semantic settings can be loaded from config file."""
        config_file = tmp_path / "test.toml"
        config_file.write_text(
            """
[semantic]
enable = true
generate_stac = false
min_confidence = 0.8
cf_table = "/path/to/cf.csv"
alias_table = "/path/to/aliases.csv"
"""
        )

        reset_config()
        config = get_config(config_file)
        
        # Test custom values
        assert config.get("semantic.enable") is True
        assert config.get("semantic.generate_stac") is False
        assert config.get("semantic.min_confidence") == 0.8
        assert config.get("semantic.cf_table") == "/path/to/cf.csv"
        assert config.get("semantic.alias_table") == "/path/to/aliases.csv"

    def test_semantic_settings_from_config(self, tmp_path):
        """Test that config file correctly loads semantic values."""
        config_file = tmp_path / "test.toml"
        config_file.write_text(
            """
[semantic]
enable = false
generate_stac = false
min_confidence = 0.95
cf_table = "/custom/cf.csv"
alias_table = "/custom/aliases.csv"
"""
        )

        reset_config()
        config = get_config(config_file)

        # Test config.get() directly (not affected by .env timing issues)
        assert config.get("semantic.enable") is False
        assert config.get("semantic.generate_stac") is False
        assert config.get("semantic.min_confidence") == 0.95
        assert config.get("semantic.cf_table") == "/custom/cf.csv"
        assert config.get("semantic.alias_table") == "/custom/aliases.csv"

    def test_semantic_env_overrides_config(self, tmp_path):
        """Test that environment variables override semantic config."""
        config_file = tmp_path / "test.toml"
        config_file.write_text(
            """
[semantic]
enable = false
generate_stac = true
min_confidence = 0.7
cf_table = "/config/cf.csv"
alias_table = "/config/aliases.csv"
"""
        )

        reset_config()
        config = get_config(config_file)

        # Reload settings with env vars
        import sys
        if "oceanstream.config.settings" in sys.modules:
            del sys.modules["oceanstream.config.settings"]

        with patch.dict(
            os.environ,
            {
                "SEMANTIC_ENABLE": "true",
                "SEMANTIC_GENERATE_STAC": "false",
                "SEMANTIC_MIN_CONFIDENCE": "0.9",
                "SEMANTIC_CF_TABLE": "/env/cf.csv",
                "SEMANTIC_ALIAS_TABLE": "/env/aliases.csv",
            },
        ):
            from oceanstream.config.settings import Settings

            # Environment variables should override config
            assert Settings.SEMANTIC_ENABLE is True
            assert Settings.SEMANTIC_GENERATE_STAC is False
            assert Settings.SEMANTIC_MIN_CONFIDENCE == 0.9
            assert Settings.SEMANTIC_CF_TABLE == "/env/cf.csv"
            assert Settings.SEMANTIC_ALIAS_TABLE == "/env/aliases.csv"

    def test_semantic_boolean_parsing(self, tmp_path):
        """Test that semantic boolean values are parsed correctly from TOML."""
        config_file = tmp_path / "test.toml"
        config_file.write_text(
            """
[semantic]
enable = false
generate_stac = true
"""
        )

        reset_config()
        config = get_config(config_file)

        # Test config.get() directly for type checking
        enable_val = config.get("semantic.enable")
        generate_stac_val = config.get("semantic.generate_stac")
        
        assert isinstance(enable_val, bool)
        assert isinstance(generate_stac_val, bool)
        assert enable_val is False
        assert generate_stac_val is True

