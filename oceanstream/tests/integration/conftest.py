"""Shared fixtures for integration tests."""
import pytest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
RAW_DATA_DIR = PROJECT_ROOT / "raw_data"


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

    # Also isolate campaign storage so tests don't pollute ~/.oceanstream/campaigns/
    campaigns_dir = tmp_path / "campaigns"
    campaigns_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        "oceanstream.geotrack.campaign.get_campaigns_dir",
        lambda: campaigns_dir,
    )
    
    return metadata_dir


@pytest.fixture
def require_raw_data():
    """Skip a test if a required raw_data file is missing.

    Usage::

        def test_something(require_raw_data):
            csv_path = require_raw_data("norsoop/color_fantasy_norsoop_2017.csv")
    """
    def _require(relative_path: str) -> Path:
        full = RAW_DATA_DIR / relative_path
        if not full.exists():
            pytest.skip(
                f"Test data missing: raw_data/{relative_path}  "
                f"(run: python scripts/fetch_test_data.py --tier small)"
            )
        return full
    return _require
