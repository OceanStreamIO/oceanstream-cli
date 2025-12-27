import importlib
import os
import sys
from pathlib import Path
from unittest.mock import patch

import dotenv
import pytest


def reload_settings_module(monkeypatch, env: dict[str, str] | None = None):
    """Helper to reload settings with a specific environment.

    We purge the module from sys.modules so class attributes are re-evaluated
    from environment variables on import.
    """
    if env is None:
        env = {}

    # First clear all modules to avoid any cached state
    modules_to_clear = [
        "config.settings",
        "oceanstream.config.settings",
        "oceanstream.configuration",
    ]
    for mod in list(sys.modules.keys()):
        if mod in modules_to_clear or mod.startswith("config.") or mod.startswith("oceanstream.config"):
            del sys.modules[mod]

    # Clear existing env vars that the settings consume
    # This must happen before we import the module, as load_dotenv() may have
    # already populated os.environ when pytest started
    env_vars_to_clear = [
        "AZURE_STORAGE_CONNECTION_STRING",
        "AZURE_CONTAINER_NAME",
        "OUTPUT_PATH",
        "RAW_DATA_PATH",
        "OCEANSTREAM_METADATA_DIR",
        "SEMANTIC_ENABLE",
        "SEMANTIC_GENERATE_STAC",
        "SEMANTIC_MIN_CONFIDENCE",
        "SEMANTIC_INFERENCE_ENDPOINT",
        "SEMANTIC_CF_TABLE",
        "SEMANTIC_ALIAS_TABLE",
    ]
    for key in env_vars_to_clear:
        # Remove directly from os.environ first
        os.environ.pop(key, None)
        # Then use monkeypatch for proper cleanup at test end
        monkeypatch.delenv(key, raising=False)

    # Set the provided overrides
    for k, v in env.items():
        os.environ[k] = v  # Set directly to ensure it's available at import time
        monkeypatch.setenv(k, v)  # Also use monkeypatch for cleanup

    # Reset the configuration system (may re-import modules)
    try:
        from oceanstream.configuration import reset_config
        reset_config()
    except ImportError:
        pass
    
    # NOTE: We don't call _reset_config_instance() here because importing
    # oceanstream.config.settings would trigger load_dotenv() again.
    # Since we're deleting the module anyway, this isn't needed.
    
    # Clear modules one final time before actual import
    for mod in list(sys.modules.keys()):
        if mod in modules_to_clear or mod.startswith("config.") or mod.startswith("oceanstream.config"):
            del sys.modules[mod]

    # Patch dotenv.load_dotenv BEFORE importing the settings module
    # This prevents .env from being loaded when the settings module is imported
    import dotenv
    original_load_dotenv = dotenv.load_dotenv
    dotenv.load_dotenv = lambda *args, **kwargs: True
    
    try:
        return importlib.import_module("config.settings")
    finally:
        dotenv.load_dotenv = original_load_dotenv


def test_settings_defaults(monkeypatch):
    mod = reload_settings_module(monkeypatch)
    Settings = mod.Settings

    # Defaults when env not set
    assert Settings.AZURE_STORAGE_CONNECTION_STRING is None
    assert Settings.AZURE_CONTAINER_NAME is None
    assert Settings.OUTPUT_PATH == "data/output"
    assert Settings.RAW_DATA_PATH == "data/raw_data"
    # METADATA_DIR should default to ~/.oceanstream/metadata
    assert Settings.METADATA_DIR == Path.home() / ".oceanstream" / "metadata"


def test_settings_env_overrides(monkeypatch, tmp_path):
    env = {
        "AZURE_STORAGE_CONNECTION_STRING": "UseDevelopmentStorage=true;",
        "AZURE_CONTAINER_NAME": "test-container",
        "OUTPUT_PATH": str(tmp_path / "out"),
        "RAW_DATA_PATH": str(tmp_path / "raw"),
        "OCEANSTREAM_METADATA_DIR": str(tmp_path / "metadata"),
    }

    mod = reload_settings_module(monkeypatch, env)
    Settings = mod.Settings

    assert Settings.AZURE_STORAGE_CONNECTION_STRING == env["AZURE_STORAGE_CONNECTION_STRING"]
    assert Settings.AZURE_CONTAINER_NAME == env["AZURE_CONTAINER_NAME"]
    assert Settings.OUTPUT_PATH == env["OUTPUT_PATH"]
    assert Settings.RAW_DATA_PATH == env["RAW_DATA_PATH"]
    assert Settings.METADATA_DIR == Path(env["OCEANSTREAM_METADATA_DIR"])


def test_metadata_dir_is_path_object(monkeypatch):
    """Test that METADATA_DIR is a Path object."""
    mod = reload_settings_module(monkeypatch)
    Settings = mod.Settings
    
    assert isinstance(Settings.METADATA_DIR, Path)


def test_semantic_enable_env_true(monkeypatch):
    """Test SEMANTIC_ENABLE can be enabled via environment."""
    env = {"SEMANTIC_ENABLE": "true"}
    mod = reload_settings_module(monkeypatch, env)
    Settings = mod.Settings
    
    assert Settings.SEMANTIC_ENABLE is True


def test_semantic_enable_env_false(monkeypatch):
    """Test SEMANTIC_ENABLE defaults to false."""
    mod = reload_settings_module(monkeypatch)
    Settings = mod.Settings
    
    assert Settings.SEMANTIC_ENABLE is False


def test_semantic_enable_env_variations(monkeypatch):
    """Test SEMANTIC_ENABLE accepts various truthy values."""
    for value in ["1", "True", "YES", "yes"]:
        env = {"SEMANTIC_ENABLE": value}
        mod = reload_settings_module(monkeypatch, env)
        Settings = mod.Settings
        assert Settings.SEMANTIC_ENABLE is True, f"Failed for value: {value}"


def test_semantic_generate_stac_env(monkeypatch):
    """Test SEMANTIC_GENERATE_STAC can be disabled via environment."""
    env = {"SEMANTIC_GENERATE_STAC": "false"}
    mod = reload_settings_module(monkeypatch, env)
    Settings = mod.Settings
    
    assert Settings.SEMANTIC_GENERATE_STAC is False


def test_semantic_min_confidence_env(monkeypatch):
    """Test SEMANTIC_MIN_CONFIDENCE can be set via environment."""
    env = {"SEMANTIC_MIN_CONFIDENCE": "0.85"}
    mod = reload_settings_module(monkeypatch, env)
    Settings = mod.Settings
    
    assert Settings.SEMANTIC_MIN_CONFIDENCE == 0.85


def test_semantic_min_confidence_default(monkeypatch):
    """Test SEMANTIC_MIN_CONFIDENCE defaults to 0.7."""
    mod = reload_settings_module(monkeypatch)
    Settings = mod.Settings
    
    assert Settings.SEMANTIC_MIN_CONFIDENCE == 0.7


def test_semantic_cf_table_env(monkeypatch):
    """Test SEMANTIC_CF_TABLE can be set via environment."""
    env = {"SEMANTIC_CF_TABLE": "/path/to/cf_table.csv"}
    mod = reload_settings_module(monkeypatch, env)
    Settings = mod.Settings
    
    assert Settings.SEMANTIC_CF_TABLE == "/path/to/cf_table.csv"


def test_semantic_cf_table_default(monkeypatch):
    """Test SEMANTIC_CF_TABLE defaults to empty string."""
    mod = reload_settings_module(monkeypatch)
    Settings = mod.Settings
    
    assert Settings.SEMANTIC_CF_TABLE == ""


def test_semantic_alias_table_env(monkeypatch):
    """Test SEMANTIC_ALIAS_TABLE can be set via environment."""
    env = {"SEMANTIC_ALIAS_TABLE": "/path/to/alias_table.csv"}
    mod = reload_settings_module(monkeypatch, env)
    Settings = mod.Settings
    
    assert Settings.SEMANTIC_ALIAS_TABLE == "/path/to/alias_table.csv"


def test_semantic_alias_table_default(monkeypatch):
    """Test SEMANTIC_ALIAS_TABLE defaults to empty string."""
    mod = reload_settings_module(monkeypatch)
    Settings = mod.Settings
    
    assert Settings.SEMANTIC_ALIAS_TABLE == ""


def test_metadata_dir_expands_user(monkeypatch, tmp_path):
    """Test that METADATA_DIR expands ~ properly."""
    # Use a path with ~ to test expansion
    home = Path.home()
    env = {"OCEANSTREAM_METADATA_DIR": "~/test_metadata"}
    mod = reload_settings_module(monkeypatch, env)
    Settings = mod.Settings
    
    assert Settings.METADATA_DIR == home / "test_metadata"
    assert "~" not in str(Settings.METADATA_DIR)
