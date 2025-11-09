import importlib
import os
import sys

import pytest


def reload_settings_module(monkeypatch, env: dict[str, str] | None = None):
    """Helper to reload settings with a specific environment.

    We purge the module from sys.modules so class attributes are re-evaluated
    from environment variables on import.
    """
    if env is None:
        env = {}

    # Clear existing env vars that the settings consume
    for key in [
        "AZURE_STORAGE_CONNECTION_STRING",
        "AZURE_CONTAINER_NAME",
        "LOCAL_OUTPUT_PATH",
        "RAW_DATA_PATH",
        "GEOPARQUET_OUTPUT_PATH",
    ]:
        monkeypatch.delenv(key, raising=False)

    # Set the provided overrides
    for k, v in env.items():
        monkeypatch.setenv(k, v)

    # Ensure a fresh import of the module
    if "config.settings" in sys.modules:
        del sys.modules["config.settings"]

    return importlib.import_module("config.settings")


def test_settings_defaults(monkeypatch):
    mod = reload_settings_module(monkeypatch)
    Settings = mod.Settings

    # Defaults when env not set
    assert Settings.AZURE_STORAGE_CONNECTION_STRING is None
    assert Settings.AZURE_CONTAINER_NAME is None
    assert Settings.LOCAL_OUTPUT_PATH == "data/output"
    assert Settings.RAW_DATA_PATH == "data/raw_data"
    assert Settings.GEOPARQUET_OUTPUT_PATH == "data/geoparquet"


def test_settings_env_overrides(monkeypatch, tmp_path):
    env = {
        "AZURE_STORAGE_CONNECTION_STRING": "UseDevelopmentStorage=true;",
        "AZURE_CONTAINER_NAME": "test-container",
        "LOCAL_OUTPUT_PATH": str(tmp_path / "out"),
        "RAW_DATA_PATH": str(tmp_path / "raw"),
        "GEOPARQUET_OUTPUT_PATH": str(tmp_path / "gpq"),
    }

    mod = reload_settings_module(monkeypatch, env)
    Settings = mod.Settings

    assert Settings.AZURE_STORAGE_CONNECTION_STRING == env["AZURE_STORAGE_CONNECTION_STRING"]
    assert Settings.AZURE_CONTAINER_NAME == env["AZURE_CONTAINER_NAME"]
    assert Settings.LOCAL_OUTPUT_PATH == env["LOCAL_OUTPUT_PATH"]
    assert Settings.RAW_DATA_PATH == env["RAW_DATA_PATH"]
    assert Settings.GEOPARQUET_OUTPUT_PATH == env["GEOPARQUET_OUTPUT_PATH"]
