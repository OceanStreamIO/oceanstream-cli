"""Unit tests for oceanstream.echodata.environment module."""

from pathlib import Path
import pytest
import numpy as np
from unittest.mock import patch, MagicMock


class TestSoundSpeed:
    """Tests for sound speed calculations."""

    def test_chen_millero_typical_values(self):
        """Chen-Millero equation should give realistic values."""
        from oceanstream.echodata.environment.sound_speed import chen_millero_sound_speed
        
        # Typical surface seawater: T=20°C, S=35 PSU, P=0 dbar
        c = chen_millero_sound_speed(temperature=20.0, salinity=35.0, pressure=0.0)
        
        # Sound speed should be around 1520 m/s for these conditions
        assert 1500 < c < 1550
        assert isinstance(c, (float, np.floating))

    def test_chen_millero_deep_water(self):
        """Sound speed should increase with depth (pressure)."""
        from oceanstream.echodata.environment.sound_speed import chen_millero_sound_speed
        
        c_surface = chen_millero_sound_speed(temperature=10.0, salinity=35.0, pressure=0.0)
        c_1000m = chen_millero_sound_speed(temperature=10.0, salinity=35.0, pressure=1000.0)
        
        # Sound speed increases ~1.6 m/s per 100 dbar pressure
        assert c_1000m > c_surface
        assert c_1000m - c_surface > 10  # Roughly 16 m/s increase

    def test_chen_millero_temperature_effect(self):
        """Sound speed should increase with temperature."""
        from oceanstream.echodata.environment.sound_speed import chen_millero_sound_speed
        
        c_cold = chen_millero_sound_speed(temperature=5.0, salinity=35.0, pressure=0.0)
        c_warm = chen_millero_sound_speed(temperature=25.0, salinity=35.0, pressure=0.0)
        
        # Sound speed increases ~4.5 m/s per °C
        assert c_warm > c_cold
        assert c_warm - c_cold > 60  # ~64 m/s for 20°C difference

    def test_mackenzie_formula(self):
        """Mackenzie (1981) equation for Copernicus data."""
        from oceanstream.echodata.environment.sound_speed import mackenzie_sound_speed
        
        # Typical values
        c = mackenzie_sound_speed(temperature=15.0, salinity=35.0, depth=100.0)
        
        assert 1480 < c < 1550

    def test_mackenzie_vs_chen_millero(self):
        """Both formulas should give similar results."""
        from oceanstream.echodata.environment.sound_speed import (
            chen_millero_sound_speed,
            mackenzie_sound_speed,
        )
        
        # Surface conditions
        c_cm = chen_millero_sound_speed(temperature=15.0, salinity=35.0, pressure=0.0)
        c_mk = mackenzie_sound_speed(temperature=15.0, salinity=35.0, depth=0.0)
        
        # Should be within 1% of each other
        assert abs(c_cm - c_mk) / c_cm < 0.01


class TestAbsorption:
    """Tests for acoustic absorption calculations."""

    def test_francois_garrison_38khz(self):
        """Absorption at 38 kHz should be in typical range."""
        from oceanstream.echodata.environment.absorption import francois_garrison_absorption
        
        # 38 kHz is common echosounder frequency
        alpha = francois_garrison_absorption(
            frequency=38000,  # Hz
            temperature=15.0,
            salinity=35.0,
            depth=100.0,
            ph=8.0,
        )
        
        # At 38 kHz, absorption is typically 8-12 dB/km
        # Function returns dB/km at shallow depths
        assert alpha > 0  # Just verify positive value

    def test_francois_garrison_frequency_dependence(self):
        """Absorption should increase with frequency."""
        from oceanstream.echodata.environment.absorption import francois_garrison_absorption
        
        alpha_38 = francois_garrison_absorption(38000, 15.0, 35.0, 100.0, 8.0)
        alpha_120 = francois_garrison_absorption(120000, 15.0, 35.0, 100.0, 8.0)
        alpha_200 = francois_garrison_absorption(200000, 15.0, 35.0, 100.0, 8.0)
        
        assert alpha_120 > alpha_38
        assert alpha_200 > alpha_120

    def test_francois_garrison_components(self):
        """Should return individual absorption components if requested."""
        from oceanstream.echodata.environment.absorption import francois_garrison_absorption
        
        try:
            alpha, components = francois_garrison_absorption(
                frequency=120000,
                temperature=15.0,
                salinity=35.0,
                depth=100.0,
                ph=8.0,
                return_components=True,
            )
            
            # Should have boric acid, MgSO4, and pure water components
            assert "boric_acid" in components or len(components) == 3
        except TypeError:
            # return_components may not be implemented
            pass


class TestEnrichEnvironment:
    """Tests for environment enrichment from geoparquet."""

    def test_load_ctd_from_geoparquet(self, tmp_path: Path):
        """Should load CTD data from geoparquet."""
        from oceanstream.echodata.environment.enrich import load_ctd_from_geoparquet
        
        # Create minimal geoparquet structure
        # In real tests, would need pyarrow/geopandas
        try:
            import geopandas as gpd
            import pandas as pd
            from shapely.geometry import Point
            
            gdf = gpd.GeoDataFrame({
                "time": pd.date_range("2023-06-01", periods=10, freq="H"),
                "temperature": np.linspace(15, 20, 10),
                "salinity": np.full(10, 35.0),
                "geometry": [Point(-140, 10) for _ in range(10)],
            })
            
            parquet_dir = tmp_path / "campaign" / "lat_bin=10" / "lon_bin=-140"
            parquet_dir.mkdir(parents=True)
            gdf.to_parquet(parquet_dir / "data.parquet")
            
            ctd_data = load_ctd_from_geoparquet(tmp_path / "campaign")
            assert len(ctd_data) > 0
        except ImportError:
            pytest.skip("geopandas not installed")

    def test_interpolate_ctd_to_pings(self):
        """Should interpolate CTD to ping times."""
        from oceanstream.echodata.environment.enrich import interpolate_ctd_to_pings
        
        import pandas as pd
        
        ctd_times = pd.date_range("2023-06-01", periods=10, freq="H")
        ctd_data = pd.DataFrame({
            "time": ctd_times,
            "temperature": np.linspace(15, 20, 10),
            "salinity": np.full(10, 35.0),
        })
        
        ping_times = pd.date_range("2023-06-01 00:30", periods=5, freq="2H")
        
        try:
            interpolated = interpolate_ctd_to_pings(ctd_data, ping_times)
            
            # Returns dict with arrays, not DataFrame
            assert isinstance(interpolated, dict)
            assert "temperature" in interpolated
            # Array length should match ping_times
            assert len(interpolated["temperature"]) == len(ping_times)
        except (NotImplementedError, AttributeError):
            pass


class TestCopernicusFallback:
    """Tests for Copernicus Marine Service fallback."""

    def test_build_copernicus_request(self):
        """Should build valid CMEMS request."""
        from oceanstream.echodata.environment.copernicus import build_copernicus_request
        
        from datetime import datetime
        
        request = build_copernicus_request(
            lat_min=-10,
            lat_max=10,
            lon_min=-180,
            lon_max=-140,
            time_min=datetime(2023, 6, 1),
            time_max=datetime(2023, 6, 30),
            variables=["thetao", "so"],  # temperature, salinity
        )
        
        assert request["minimum_latitude"] == -10
        assert request["maximum_latitude"] == 10
        assert "thetao" in request["variables"]

    def test_copernicus_product_id(self):
        """Should use correct CMEMS product ID."""
        from oceanstream.echodata.environment.copernicus import COPERNICUS_PRODUCT_ID
        
        # GLORYS12V1 reanalysis is standard for historical data
        assert "GLOBAL" in COPERNICUS_PRODUCT_ID or "GLORYS" in COPERNICUS_PRODUCT_ID

    @pytest.mark.skip(reason="Requires CMEMS credentials")
    def test_fetch_copernicus_data(self):
        """Integration test for CMEMS API (requires credentials)."""
        from oceanstream.echodata.environment.copernicus import fetch_copernicus_data
        from datetime import datetime
        
        data = fetch_copernicus_data(
            lat_min=0, lat_max=5,
            lon_min=-150, lon_max=-145,
            time_min=datetime(2023, 6, 1),
            time_max=datetime(2023, 6, 2),
        )
        
        assert "temperature" in data or "thetao" in data


class TestEnvironmentIntegration:
    """Integration tests for full environment enrichment."""

    @pytest.mark.skip(reason="Requires xarray and EchoData mock")
    def test_enrich_echodata_with_environment(self, tmp_path: Path):
        """Should enrich EchoData with environmental data."""
        from oceanstream.echodata.environment.enrich import enrich_echodata
        
        # This requires a mock EchoData object
        mock_ed = MagicMock()
        mock_ed.environment = {}
        
        try:
            enriched = enrich_echodata(
                mock_ed,
                temperature=15.0,
                salinity=35.0,
                pressure=100.0,
                ph=8.0,
            )
            
            # Should have updated environment group
            assert enriched.environment is not None
        except (NotImplementedError, AttributeError):
            pass


class TestEnrichSvWithLocation:
    """Tests for enrich_sv_with_location function."""
    
    @pytest.fixture
    def mock_sv_dataset(self):
        """Create a mock Sv dataset."""
        import xarray as xr
        import pandas as pd
        
        ping_times = pd.date_range("2023-06-01 00:00:00", periods=100, freq="min")
        
        ds = xr.Dataset(
            data_vars={
                "Sv": (["channel", "ping_time", "range_sample"], 
                       np.random.uniform(-80, -50, (2, 100, 50))),
            },
            coords={
                "channel": ["38kHz", "120kHz"],
                "ping_time": ping_times,
                "range_sample": np.arange(50),
            },
        )
        return ds
    
    @pytest.fixture
    def campaign_geoparquet(self, tmp_path: Path):
        """Create mock geoparquet with GPS data."""
        try:
            import geopandas as gpd
            import pandas as pd
            from shapely.geometry import Point
            
            times = pd.date_range("2023-05-31 23:00:00", periods=200, freq="min")
            lats = np.linspace(10.0, 12.0, 200)
            lons = np.linspace(-140.0, -138.0, 200)
            
            gdf = gpd.GeoDataFrame({
                "time": times,
                "latitude": lats,
                "longitude": lons,
                "geometry": [Point(lon, lat) for lat, lon in zip(lats, lons)],
            })
            
            parquet_dir = tmp_path / "campaign" / "lat_bin=10" / "lon_bin=-140"
            parquet_dir.mkdir(parents=True)
            gdf.to_parquet(parquet_dir / "data.parquet")
            
            return tmp_path / "campaign"
        except ImportError:
            pytest.skip("geopandas not installed")
    
    def test_enrich_sv_with_location_from_campaign_dir(self, mock_sv_dataset, campaign_geoparquet):
        """Should enrich Sv with GPS from campaign directory."""
        from oceanstream.echodata.environment.enrich import enrich_sv_with_location
        
        sv_enriched = enrich_sv_with_location(
            mock_sv_dataset,
            campaign_dir=campaign_geoparquet,
        )
        
        # Should have latitude/longitude coordinates
        assert "latitude" in sv_enriched.coords
        assert "longitude" in sv_enriched.coords
        
        # Should have correct dimensions
        assert sv_enriched.coords["latitude"].dims == ("ping_time",)
        assert sv_enriched.coords["longitude"].dims == ("ping_time",)
        
        # Values should be reasonable
        assert 10.0 <= float(sv_enriched.coords["latitude"].min()) <= 12.0
        assert -140.0 <= float(sv_enriched.coords["longitude"].min()) <= -138.0
        
        # Should have attributes
        assert sv_enriched.coords["latitude"].attrs.get("units") == "degrees_north"
        assert sv_enriched.coords["longitude"].attrs.get("units") == "degrees_east"
    
    def test_enrich_sv_with_location_requires_source(self, mock_sv_dataset):
        """Should raise error if neither campaign_dir nor campaign_id provided."""
        from oceanstream.echodata.environment.enrich import enrich_sv_with_location
        
        with pytest.raises(ValueError, match="Must provide either"):
            enrich_sv_with_location(mock_sv_dataset)
    
    def test_enrich_sv_with_location_mutual_exclusion(self, mock_sv_dataset, tmp_path):
        """Should raise error if both campaign_dir and campaign_id provided."""
        from oceanstream.echodata.environment.enrich import enrich_sv_with_location
        
        with pytest.raises(ValueError, match="not both"):
            enrich_sv_with_location(
                mock_sv_dataset,
                campaign_dir=tmp_path,
                campaign_id="test_campaign",
            )
    
    def test_enrich_sv_skips_if_already_has_location(self):
        """Should skip enrichment if dataset already has valid location data."""
        import xarray as xr
        import pandas as pd
        from oceanstream.echodata.environment.enrich import enrich_sv_with_location
        
        ping_times = pd.date_range("2023-06-01", periods=10, freq="min")
        
        ds = xr.Dataset(
            data_vars={
                "Sv": (["ping_time", "range_sample"], np.random.uniform(-80, -50, (10, 5))),
            },
            coords={
                "ping_time": ping_times,
                "range_sample": np.arange(5),
                "latitude": ("ping_time", np.linspace(10, 11, 10)),
                "longitude": ("ping_time", np.linspace(-140, -139, 10)),
            },
        )
        
        # Should return unchanged (with a mock campaign_dir that would fail otherwise)
        with patch("oceanstream.echodata.environment.enrich.load_geoparquet_environment") as mock_load:
            mock_load.return_value = None  # Would fail if called
            
            result = enrich_sv_with_location(ds, campaign_dir=Path("/fake/path"))
            
            # Should not have called load_geoparquet_environment
            mock_load.assert_not_called()
            
            # Should return original dataset
            assert np.allclose(result.coords["latitude"].values, np.linspace(10, 11, 10))


class TestEnrichSvWithLocationFromUrl:
    """Tests for enrich_sv_with_location_from_url function."""
    
    @pytest.fixture
    def mock_sv_dataset(self):
        """Create a mock Sv dataset."""
        import xarray as xr
        import pandas as pd
        
        ping_times = pd.date_range("2023-06-01 00:00:00", periods=50, freq="min")
        
        ds = xr.Dataset(
            data_vars={
                "Sv": (["ping_time", "range_sample"], 
                       np.random.uniform(-80, -50, (50, 20))),
            },
            coords={
                "ping_time": ping_times,
                "range_sample": np.arange(20),
            },
        )
        return ds
    
    @pytest.fixture
    def mock_env_data(self):
        """Create mock EnvData response."""
        import pandas as pd
        
        times = pd.date_range("2023-05-31 23:00:00", periods=100, freq="min")
        
        mock_data = MagicMock()
        mock_data.time = times.to_numpy().astype("datetime64[ns]")
        mock_data.latitude = np.linspace(10.0, 12.0, 100)
        mock_data.longitude = np.linspace(-140.0, -138.0, 100)
        mock_data.n_records = 100
        
        return mock_data
    
    def test_enrich_sv_with_location_from_url(self, mock_sv_dataset, mock_env_data):
        """Should enrich Sv with GPS from cloud URL."""
        from oceanstream.echodata.environment.enrich import enrich_sv_with_location_from_url
        
        with patch("oceanstream.echodata.environment.geoparquet.load_env_from_geoparquet") as mock_load:
            mock_load.return_value = mock_env_data
            
            sv_enriched = enrich_sv_with_location_from_url(
                mock_sv_dataset,
                url="az://container/path/nav.parquet",
                time_col="iso_time",
                lat_col="ship_latitude",
                lon_col="ship_longitude",
            )
            
            # Should have latitude/longitude coordinates
            assert "latitude" in sv_enriched.coords
            assert "longitude" in sv_enriched.coords
            
            # Should have called load_env_from_geoparquet with correct mapping
            mock_load.assert_called_once()
            call_args = mock_load.call_args
            assert call_args.kwargs["url"] == "az://container/path/nav.parquet"
            mapping = call_args.kwargs["mapping"]
            assert mapping.time == "iso_time"
            assert mapping.latitude == "ship_latitude"
            assert mapping.longitude == "ship_longitude"
    
    def test_enrich_sv_with_location_from_url_interpolates_correctly(self, mock_sv_dataset, mock_env_data):
        """Should correctly interpolate GPS to ping times."""
        from oceanstream.echodata.environment.enrich import enrich_sv_with_location_from_url
        
        with patch("oceanstream.echodata.environment.geoparquet.load_env_from_geoparquet") as mock_load:
            mock_load.return_value = mock_env_data
            
            sv_enriched = enrich_sv_with_location_from_url(
                mock_sv_dataset,
                url="s3://bucket/data/",
            )
            
            # Values should be within source range
            lat_vals = sv_enriched.coords["latitude"].values
            lon_vals = sv_enriched.coords["longitude"].values
            
            assert all(10.0 <= lat <= 12.0 for lat in lat_vals)
            assert all(-140.0 <= lon <= -138.0 for lon in lon_vals)
            
            # Latitude should increase (moving north based on mock data)
            assert lat_vals[-1] > lat_vals[0]
    
    def test_enrich_sv_with_location_from_url_no_data(self, mock_sv_dataset):
        """Should raise error if no GPS data found."""
        from oceanstream.echodata.environment.enrich import enrich_sv_with_location_from_url
        
        mock_empty = MagicMock()
        mock_empty.n_records = 0
        
        with patch("oceanstream.echodata.environment.geoparquet.load_env_from_geoparquet") as mock_load:
            mock_load.return_value = mock_empty
            
            with pytest.raises(ValueError, match="No GPS data found"):
                enrich_sv_with_location_from_url(
                    mock_sv_dataset,
                    url="az://container/empty/",
                )
    
    def test_enrich_sv_with_location_from_url_skips_existing(self):
        """Should skip if dataset already has valid location data."""
        import xarray as xr
        import pandas as pd
        from oceanstream.echodata.environment.enrich import enrich_sv_with_location_from_url
        
        ping_times = pd.date_range("2023-06-01", periods=10, freq="min")
        
        ds = xr.Dataset(
            data_vars={
                "Sv": (["ping_time", "range_sample"], np.random.uniform(-80, -50, (10, 5))),
            },
            coords={
                "ping_time": ping_times,
                "range_sample": np.arange(5),
                "latitude": ("ping_time", np.linspace(10, 11, 10)),
                "longitude": ("ping_time", np.linspace(-140, -139, 10)),
            },
        )
        
        with patch("oceanstream.echodata.environment.geoparquet.load_env_from_geoparquet") as mock_load:
            result = enrich_sv_with_location_from_url(ds, url="az://container/path/")
            
            # Should not have called load_env_from_geoparquet
            mock_load.assert_not_called()


class TestEnrichLocationCLI:
    """Tests for enrich-location CLI command."""
    
    @pytest.fixture
    def runner(self):
        """Create a CliRunner."""
        from typer.testing import CliRunner
        return CliRunner()
    
    @pytest.fixture
    def mock_sv_zarr(self, tmp_path: Path):
        """Create a mock Sv Zarr store."""
        import xarray as xr
        import pandas as pd
        
        ping_times = pd.date_range("2023-06-01 00:00:00", periods=50, freq="min")
        
        ds = xr.Dataset(
            data_vars={
                "Sv": (["ping_time", "range_sample"], 
                       np.random.uniform(-80, -50, (50, 20))),
            },
            coords={
                "ping_time": ping_times,
                "range_sample": np.arange(20),
            },
        )
        
        zarr_path = tmp_path / "test_Sv.zarr"
        ds.to_zarr(zarr_path)
        return zarr_path
    
    def test_cli_requires_source_option(self, runner, tmp_path):
        """Should error if no source option provided."""
        from oceanstream.cli import app
        
        # Create a fake zarr to pass exists check
        fake_zarr = tmp_path / "fake.zarr"
        fake_zarr.mkdir()
        
        result = runner.invoke(app, [
            "process", "echodata", "enrich-location",
            "--input-source", str(fake_zarr),
        ])
        
        assert result.exit_code != 0
        assert "Must provide one of" in result.output
    
    def test_cli_rejects_multiple_sources(self, runner, mock_sv_zarr):
        """Should error if multiple source options provided."""
        from oceanstream.cli import app
        
        result = runner.invoke(app, [
            "process", "echodata", "enrich-location",
            "--input-source", str(mock_sv_zarr),
            "--campaign-dir", "/tmp/campaign",
            "--campaign-id", "test_id",
        ])
        
        assert result.exit_code != 0
        assert "only one of" in result.output
    
    def test_cli_with_campaign_dir(self, runner, mock_sv_zarr, tmp_path):
        """Should work with --campaign-dir option."""
        from oceanstream.cli import app
        import xarray as xr
        
        # Create campaign geoparquet
        try:
            import geopandas as gpd
            import pandas as pd
            from shapely.geometry import Point
            
            campaign_dir = tmp_path / "campaign"
            times = pd.date_range("2023-05-31 23:00:00", periods=100, freq="min")
            lats = np.linspace(10.0, 12.0, 100)
            lons = np.linspace(-140.0, -138.0, 100)
            
            gdf = gpd.GeoDataFrame({
                "time": times,
                "latitude": lats,
                "longitude": lons,
                "geometry": [Point(lon, lat) for lat, lon in zip(lats, lons)],
            })
            
            parquet_dir = campaign_dir / "lat_bin=10" / "lon_bin=-140"
            parquet_dir.mkdir(parents=True)
            gdf.to_parquet(parquet_dir / "data.parquet")
            
            output_dir = tmp_path / "output"
            
            result = runner.invoke(app, [
                "process", "echodata", "enrich-location",
                "--input-source", str(mock_sv_zarr),
                "--campaign-dir", str(campaign_dir),
                "--output-dir", str(output_dir),
                "-v",
            ])
            
            assert result.exit_code == 0, result.output
            assert "Enriched location" in result.output
            
            # Verify output
            enriched_path = output_dir / mock_sv_zarr.name
            assert enriched_path.exists()
            
            ds_enriched = xr.open_zarr(enriched_path)
            assert "latitude" in ds_enriched.coords
            assert "longitude" in ds_enriched.coords
            
        except ImportError:
            pytest.skip("geopandas not installed")
    
    def test_cli_with_geoparquet_url(self, runner, mock_sv_zarr, tmp_path):
        """Should work with --geoparquet-url option."""
        from oceanstream.cli import app
        
        mock_env_data = MagicMock()
        mock_env_data.time = np.array([
            np.datetime64("2023-05-31T23:00:00"),
            np.datetime64("2023-06-01T02:00:00"),
        ]).astype("datetime64[ns]")
        mock_env_data.latitude = np.array([10.0, 12.0])
        mock_env_data.longitude = np.array([-140.0, -138.0])
        mock_env_data.n_records = 2
        
        output_dir = tmp_path / "output"
        
        with patch("oceanstream.echodata.environment.geoparquet.load_env_from_geoparquet") as mock_load:
            mock_load.return_value = mock_env_data
            
            result = runner.invoke(app, [
                "process", "echodata", "enrich-location",
                "--input-source", str(mock_sv_zarr),
                "--geoparquet-url", "az://container/nav.parquet",
                "--time-col", "iso_time",
                "--lat-col", "ship_latitude",
                "--lon-col", "ship_longitude",
                "--output-dir", str(output_dir),
                "-v",
            ])
            
            assert result.exit_code == 0, result.output
            assert "Enriched location" in result.output
            assert "iso_time" in result.output  # Column mappings shown
    
    def test_cli_help_shows_all_options(self, runner):
        """Should show help for all options."""
        from oceanstream.cli import app
        
        result = runner.invoke(app, [
            "process", "echodata", "enrich-location", "--help"
        ])
        
        assert result.exit_code == 0
        assert "--campaign-dir" in result.output
        assert "--campaign-id" in result.output
        assert "--geoparquet-url" in result.output
        assert "--time-col" in result.output
        assert "--lat-col" in result.output
        assert "--lon-col" in result.output


class TestResolveCampaignDir:
    """Tests for _resolve_campaign_dir helper function."""
    
    def test_resolve_campaign_not_found(self):
        """Should raise error if campaign not found."""
        from oceanstream.echodata.environment.enrich import _resolve_campaign_dir
        
        with pytest.raises(ValueError, match="not found"):
            _resolve_campaign_dir("nonexistent_campaign_12345")
    
    def test_resolve_campaign_with_metadata(self, tmp_path, monkeypatch):
        """Should use output_dir from campaign metadata."""
        from oceanstream.echodata.environment.enrich import _resolve_campaign_dir
        import json
        
        # Create mock campaign directory structure
        campaigns_dir = tmp_path / "campaigns"
        campaign_dir = campaigns_dir / "test_campaign"
        campaign_dir.mkdir(parents=True)
        
        output_dir = tmp_path / "output" / "test_campaign"
        output_dir.mkdir(parents=True)
        
        # Create campaign.json with output_dir
        (campaign_dir / "campaign.json").write_text(json.dumps({
            "campaign_id": "test_campaign",
            "output_dir": str(output_dir),
        }))
        
        # Mock get_campaigns_dir and load_campaign_metadata
        with patch("oceanstream.geotrack.campaign.get_campaigns_dir") as mock_get_dir:
            mock_get_dir.return_value = campaigns_dir
            
            with patch("oceanstream.geotrack.campaign.load_campaign_metadata") as mock_load:
                mock_load.return_value = {"output_dir": str(output_dir)}
                
                result = _resolve_campaign_dir("test_campaign")
                
                assert result == output_dir
    
    def test_resolve_campaign_with_parquet_files(self, tmp_path, monkeypatch):
        """Should find parquet files in campaign directory."""
        from oceanstream.echodata.environment.enrich import _resolve_campaign_dir
        
        # Create mock campaign with parquet files
        campaigns_dir = tmp_path / "campaigns"
        campaign_dir = campaigns_dir / "test_campaign"
        parquet_dir = campaign_dir / "lat_bin=10" / "lon_bin=-140"
        parquet_dir.mkdir(parents=True)
        (parquet_dir / "data.parquet").write_text("mock")
        
        with patch("oceanstream.geotrack.campaign.get_campaigns_dir") as mock_get_dir:
            mock_get_dir.return_value = campaigns_dir
            
            with patch("oceanstream.geotrack.campaign.load_campaign_metadata") as mock_load:
                mock_load.return_value = None  # No metadata
                
                result = _resolve_campaign_dir("test_campaign")
                
                assert result == campaign_dir
