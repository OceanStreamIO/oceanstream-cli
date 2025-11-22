"""Shared fixtures for integration tests."""
import pytest
from pathlib import Path


@pytest.fixture(autouse=True)
def isolated_metadata(tmp_path, monkeypatch):
    """
    Automatically isolate metadata directory for ALL integration tests.
    This prevents tests from interfering with each other via shared metadata.
    """
    from oceanstream.config.settings import Settings
    
    metadata_dir = tmp_path / "metadata"
    metadata_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(Settings, "METADATA_DIR", metadata_dir)
    
    return metadata_dir
