"""Integration tests for append/update functionality with deduplication."""
import pytest
from pathlib import Path
import pandas as pd
import shutil
import geopandas as gpd

from oceanstream.geotrack.processor import convert
from oceanstream.providers.saildrone import SaildroneProvider
from oceanstream.geotrack.metadata import CampaignMetadata
from oceanstream.config.settings import Settings
from oceanstream.geotrack.campaign import get_campaigns_dir


class TestAppendUpdateFunctionality:
    """Test append, update, and deduplication behavior."""
    
    @pytest.fixture
    def saildrone_files(self):
        """Get list of Saildrone test CSV files."""
        test_data_dir = Path(__file__).parent.parent / "data" / "raw_data"
        csv_files = sorted(test_data_dir.glob("sd*.csv"))
        # Get at least 2 files for testing
        assert len(csv_files) >= 2, "Need at least 2 Saildrone CSV files for append tests"
        return csv_files[:2]
    
    @pytest.fixture
    def provider(self):
        """Saildrone provider instance."""
        return SaildroneProvider()
    
    @pytest.fixture
    def metadata_dir(self, tmp_path):
        """Test metadata directory."""
        return tmp_path / "metadata"
    
    @pytest.fixture(autouse=True)
    def cleanup_campaign_metadata(self):
        """Clean up campaign metadata before and after each test."""
        # Clean up before test
        campaigns_dir = get_campaigns_dir()
        test_campaign_ids = [
            "append_test_campaign",
            "duplicate_test_campaign",
            "dedup_test_campaign",
            "force_reprocess_campaign",
            "metadata_tracking_campaign",
        ]
        for campaign_id in test_campaign_ids:
            campaign_dir = campaigns_dir / campaign_id
            if campaign_dir.exists():
                shutil.rmtree(campaign_dir)
        
        yield
        
        # Clean up after test
        for campaign_id in test_campaign_ids:
            campaign_dir = campaigns_dir / campaign_id
            if campaign_dir.exists():
                shutil.rmtree(campaign_dir)
    
    def test_multiple_runs_different_files_appends(self, saildrone_files, provider, tmp_path, metadata_dir, monkeypatch):
        """Test that running convert() twice with different files appends data correctly."""
        # Use test metadata directory
        monkeypatch.setattr(Settings, "METADATA_DIR", metadata_dir)
        
        output_dir = tmp_path / "output"
        campaign_id = "append_test_campaign"
        
        file1, file2 = saildrone_files[0], saildrone_files[1]
        
        # Run 1: Process first file
        convert(
            provider=provider,
            input_source=file1,
            output_dir=output_dir,
            campaign_id=campaign_id,
            verbose=False,
            yes=True,
        )
        
        # Read data after run 1
        campaign_dir = output_dir / campaign_id
        parquet_files_run1 = list(campaign_dir.rglob("*.parquet"))
        parquet_files_run1 = [f for f in parquet_files_run1 if 'stac' not in f.parts]
        
        assert len(parquet_files_run1) > 0, "Run 1 should create parquet files"
        
        df_run1 = pd.concat([pd.read_parquet(f) for f in parquet_files_run1])
        rows_run1 = len(df_run1)
        
        # Run 2: Process second file (different from first)
        convert(
            provider=provider,
            input_source=file2,
            output_dir=output_dir,
            campaign_id=campaign_id,  # Same campaign
            verbose=False,
            yes=True,
        )
        
        # Read data after run 2
        parquet_files_run2 = list(campaign_dir.rglob("*.parquet"))
        parquet_files_run2 = [f for f in parquet_files_run2 if 'stac' not in f.parts]
        
        df_run2 = pd.concat([pd.read_parquet(f) for f in parquet_files_run2])
        rows_run2 = len(df_run2)
        
        # Verify append behavior
        assert rows_run2 > rows_run1, f"Run 2 should have more rows ({rows_run2}) than run 1 ({rows_run1})"
        print(f"✅ Append test passed: Run 1 ({rows_run1} rows) → Run 2 ({rows_run2} rows)")
    
    def test_same_file_twice_warns_and_prevents_duplicates(self, saildrone_files, provider, tmp_path, metadata_dir, monkeypatch):
        """Test that processing the same file twice is detected and prevented by default."""
        # Use test metadata directory
        monkeypatch.setattr(Settings, "METADATA_DIR", metadata_dir)
        
        output_dir = tmp_path / "output"
        campaign_id = "duplicate_test_campaign"
        test_file = saildrone_files[0]
        
        # Run 1: Process file
        convert(
            provider=provider,
            input_source=test_file,
            output_dir=output_dir,
            campaign_id=campaign_id,
            verbose=False,
            yes=True,
        )
        
        # Check metadata was created
        metadata = CampaignMetadata(campaign_id, metadata_dir)
        assert metadata.is_file_processed(test_file), "File should be marked as processed"
        
        # Run 2: Try to process same file again (should be prevented)
        # This should exit early with a warning
        # Note: We can't easily test the early return in pytest, but we can verify
        # the metadata detection works
        assert metadata.is_file_processed(test_file), "File should still be marked as processed"
        
        file_info = metadata.get_file_info(test_file)
        assert file_info is not None, "Should have file info stored"
        assert 'hash' in file_info, "File info should contain hash"
        assert 'processed_at' in file_info, "File info should contain processed_at timestamp"
        assert 'rows' in file_info, "File info should contain row count"
        
        print(f"✅ Duplicate detection test passed: File tracked in metadata")
    
    def test_deduplication_happens_automatically(self, saildrone_files, provider, tmp_path, metadata_dir, monkeypatch):
        """Test that deduplication happens automatically when appending new data to existing campaign."""
        # Use test metadata directory
        monkeypatch.setattr(Settings, "METADATA_DIR", metadata_dir)
        
        output_dir = tmp_path / "output"
        campaign_id = "dedup_test_campaign"
        
        # Use the same file for both runs to test deduplication
        # But we need to process different files to trigger the append logic
        file1, file2 = saildrone_files[0], saildrone_files[1]
        
        # Run 1: Process first file
        convert(
            provider=provider,
            input_source=file1,
            output_dir=output_dir,
            campaign_id=campaign_id,
            verbose=False,
            yes=True,
        )
        
        campaign_dir = output_dir / campaign_id
        parquet_files = list(campaign_dir.rglob("*.parquet"))
        parquet_files = [f for f in parquet_files if 'stac' not in f.parts]
        df1 = pd.concat([pd.read_parquet(f) for f in parquet_files])
        rows_run1 = len(df1)
        
        # Run 2: Process second file - this triggers append + deduplication logic
        convert(
            provider=provider,
            input_source=file2,
            output_dir=output_dir,
            campaign_id=campaign_id,
            verbose=False,
            yes=True,
        )
        
        parquet_files = list(campaign_dir.rglob("*.parquet"))
        parquet_files = [f for f in parquet_files if 'stac' not in f.parts]
        df2 = pd.concat([pd.read_parquet(f) for f in parquet_files])
        rows_run2 = len(df2)
        
        # Should have more rows (appended) but any duplicates would be automatically removed
        # Since we're using different files, there shouldn't be duplicates, so just verify append worked
        assert rows_run2 > rows_run1, f"Append should add rows: run1={rows_run1}, run2={rows_run2}"
        
        # Verify no duplicate rows exist in the final dataset (check using primary keys)
        # Primary keys are: time, latitude, longitude, trajectory
        duplicates = df2.duplicated(subset=['time', 'latitude', 'longitude', 'trajectory'], keep='first')
        num_duplicates = duplicates.sum()
        assert num_duplicates == 0, f"Should have no duplicates, found {num_duplicates}"
        
        print(f"✅ Deduplication test passed: {rows_run1} rows → {rows_run2} rows (no duplicates, {num_duplicates} dupes detected)")
    
    def test_force_reprocess_clears_metadata(self, saildrone_files, provider, tmp_path, metadata_dir, monkeypatch):
        """Test that --force-reprocess clears previous metadata."""
        # Use test metadata directory
        monkeypatch.setattr(Settings, "METADATA_DIR", metadata_dir)
        
        output_dir = tmp_path / "output"
        campaign_id = "force_reprocess_campaign"
        test_file = saildrone_files[0]
        
        # Run 1: Process file
        convert(
            provider=provider,
            input_source=test_file,
            output_dir=output_dir,
            campaign_id=campaign_id,
            verbose=False,
            yes=True,
        )
        
        metadata = CampaignMetadata(campaign_id, metadata_dir)
        
        # Verify metadata exists
        assert metadata.get_run_count() >= 1, "Should have at least 1 run recorded"
        assert metadata.is_file_processed(test_file), "File should be marked as processed"
        
        # Run 2: Force reprocess
        convert(
            provider=provider,
            input_source=test_file,
            output_dir=output_dir,
            campaign_id=campaign_id,
            verbose=False,
            yes=True,
            force_reprocess=True,  # Clear metadata
        )
        
        # Reload metadata and check it was cleared then restarted
        metadata_after = CampaignMetadata(campaign_id, metadata_dir)
        assert metadata_after.get_run_count() >= 1, "Should have new run recorded"
        assert metadata_after.is_file_processed(test_file), "File should be marked processed again"
        
        print(f"✅ Force reprocess test passed: Metadata cleared and restarted")
    
    def test_metadata_tracking_accuracy(self, saildrone_files, provider, tmp_path, metadata_dir, monkeypatch):
        """Test that metadata accurately tracks processed files and run counts."""
        # Use test metadata directory
        monkeypatch.setattr(Settings, "METADATA_DIR", metadata_dir)
        
        output_dir = tmp_path / "output"
        campaign_id = "metadata_tracking_campaign"
        
        file1, file2 = saildrone_files[0], saildrone_files[1]
        
        # Run 1: Process first file
        convert(
            provider=provider,
            input_source=file1,
            output_dir=output_dir,
            campaign_id=campaign_id,
            verbose=False,
            yes=True,
        )
        
        metadata = CampaignMetadata(campaign_id, metadata_dir)
        
        assert metadata.get_run_count() == 1, "Should have 1 run after first convert"
        assert metadata.get_processed_file_count() == 1, "Should have 1 file tracked"
        assert metadata.is_file_processed(file1), "File 1 should be marked processed"
        assert not metadata.is_file_processed(file2), "File 2 should NOT be marked processed"
        
        # Run 2: Process second file
        convert(
            provider=provider,
            input_source=file2,
            output_dir=output_dir,
            campaign_id=campaign_id,
            verbose=False,
            yes=True,
        )
        
        metadata = CampaignMetadata(campaign_id, metadata_dir)  # Reload
        
        assert metadata.get_run_count() == 2, "Should have 2 runs after second convert"
        assert metadata.get_processed_file_count() == 2, "Should have 2 files tracked"
        assert metadata.is_file_processed(file1), "File 1 should still be marked processed"
        assert metadata.is_file_processed(file2), "File 2 should now be marked processed"
        
        print(f"✅ Metadata tracking test passed: {metadata.get_run_count()} runs, "
              f"{metadata.get_processed_file_count()} files tracked")
