"""Integration tests for R2R CTD processing."""

from pathlib import Path

import pytest

# Skip all tests if seabirdscientific not installed
pytest.importorskip("seabirdscientific")


class TestR2RCTDProcessing:
    """Tests for SeaBird CTD processing from R2R archives."""

    @pytest.fixture
    def ctd_data_dir(self, tmp_path: Path) -> Path | None:
        """Get CTD test data directory if available.
        
        Uses extracted R2R archive data if available in /tmp.
        """
        # Check for extracted R2R archive
        test_dir = Path("/tmp/RR2402/160202/data")
        if test_dir.exists():
            return test_dir
        
        # Skip if no test data
        pytest.skip("R2R CTD test data not available - extract RR2402_160202_ctd.tar.gz to /tmp")
        return None

    def test_find_cast_files(self, ctd_data_dir: Path):
        """Test finding CTD cast files in directory."""
        from oceanstream.sensors.processors.r2r_ctd import find_cast_files
        
        casts = find_cast_files(ctd_data_dir)
        
        # Should find multiple casts
        assert len(casts) > 0
        
        # Each cast should have at least a hex file
        for cast in casts:
            assert cast.hex_file.exists()
            assert cast.hex_file.suffix == '.hex'
            assert cast.cast_id
            assert cast.cruise_id

    def test_parse_hdr_file(self, ctd_data_dir: Path):
        """Test parsing CTD header file."""
        from oceanstream.sensors.processors.r2r_ctd import find_cast_files, parse_hdr_file
        
        casts = find_cast_files(ctd_data_dir)
        
        # Find a cast with header file
        cast_with_hdr = next((c for c in casts if c.hdr_file), None)
        if cast_with_hdr is None:
            pytest.skip("No cast with header file found")
        
        hdr = parse_hdr_file(cast_with_hdr.hdr_file)
        
        # Should extract position
        assert 'latitude' in hdr
        assert 'longitude' in hdr
        assert -90 <= hdr['latitude'] <= 90
        assert -180 <= hdr['longitude'] <= 180
        
        # Should extract time
        assert 'start_time' in hdr
        assert hdr['start_time'] is not None

    def test_parse_xmlcon_file(self, ctd_data_dir: Path):
        """Test parsing CTD configuration file."""
        from oceanstream.sensors.processors.r2r_ctd import find_cast_files, parse_xmlcon_file
        
        casts = find_cast_files(ctd_data_dir)
        
        # Find a cast with XMLCON file
        cast_with_xmlcon = next((c for c in casts if c.xmlcon_file), None)
        if cast_with_xmlcon is None:
            pytest.skip("No cast with XMLCON file found")
        
        config = parse_xmlcon_file(cast_with_xmlcon.xmlcon_file)
        
        # Should have sensor configuration
        assert 'sensors' in config
        assert len(config['sensors']) > 0
        
        # First sensor should be temperature
        assert config['sensors'][0]['index'] == 0

    def test_process_ctd_cast(self, ctd_data_dir: Path):
        """Test processing a CTD cast to DataFrame."""
        from oceanstream.sensors.processors.r2r_ctd import find_cast_files, process_ctd_cast
        
        casts = find_cast_files(ctd_data_dir)
        
        # Process first cast
        cast = casts[0]
        df = process_ctd_cast(cast)
        
        assert df is not None
        assert len(df) > 0
        
        # Should have essential columns
        assert 'cast_id' in df.columns
        assert 'cruise_id' in df.columns
        assert 'scan' in df.columns
        
        # Should have raw data columns
        assert 'temperature_freq' in df.columns
        assert 'conductivity_freq' in df.columns
        assert 'pressure_freq' in df.columns

    def test_process_ctd_cast_output_csv(self, ctd_data_dir: Path, tmp_path: Path):
        """Test writing processed CTD data to CSV."""
        from oceanstream.sensors.processors.r2r_ctd import find_cast_files, process_ctd_cast
        
        casts = find_cast_files(ctd_data_dir)
        cast = casts[0]
        
        # Process with output
        output_dir = tmp_path / "ctd_output"
        df = process_ctd_cast(cast, output_dir=output_dir)
        
        assert df is not None
        
        # Check CSV was written
        csv_files = list(output_dir.glob("*.csv"))
        assert len(csv_files) == 1
        
        # Read CSV and verify
        import pandas as pd
        df_read = pd.read_csv(csv_files[0])
        assert len(df_read) == len(df)

    def test_ctd_descriptor_processor(self, ctd_data_dir: Path):
        """Test CTD sensor descriptor creation."""
        from oceanstream.sensors.processors.r2r_ctd import ctd_descriptor_processor
        from oceanstream.providers.r2r.r2r_metadata import R2RFileInfo, R2RSensorInfo
        
        file_info = R2RFileInfo(
            campaign_id="RR2402",
            platform="R/V Roger Revelle",
        )
        sensor_info = R2RSensorInfo(
            sensor_type="ctd",
            description="SeaBird SBE-911+",
        )
        
        descriptor = ctd_descriptor_processor(
            ctd_data_dir,
            file_info,
            sensor_info,
            "r2r",
        )
        
        assert descriptor.sensor_type == "ctd"
        assert descriptor.sensor_id == "sbe-911plus"
        assert descriptor.campaign_id == "RR2402"
        assert "cast_count" in descriptor.metadata
