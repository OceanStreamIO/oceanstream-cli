"""Integration tests for STAC emission with sensor metadata."""
import pytest
import json
import pandas as pd
from pathlib import Path
from oceanstream.stac.emit import emit_stac_collection_and_item, calculate_measurement_statistics
from oceanstream.sensors.catalogue import Sensor, SensorType, get_sensor_catalogue


class TestSTACEmissionWithSensors:
    """Integration tests for STAC emission with sensor instruments."""

    @pytest.fixture
    def sample_dataframe(self):
        """Create a sample DataFrame for STAC generation."""
        data = {
            'time': pd.date_range('2023-01-01', periods=100, freq='1H'),
            'latitude': [10.0 + i * 0.01 for i in range(100)],
            'longitude': [-170.0 + i * 0.01 for i in range(100)],
            'temperature': [25.0 + i * 0.1 for i in range(100)],
        }
        df = pd.DataFrame(data)
        df['time'] = pd.to_datetime(df['time'])
        return df

    @pytest.fixture
    def sample_sensors(self):
        """Create sample sensors for testing."""
        sensors = [
            Sensor(
                id="test-ctd",
                name="Test CTD",
                manufacturer="Test Corp",
                model="CTD-100",
                sensor_type=SensorType.CTD,
                description="Test CTD sensor",
                variables=["temperature", "salinity"],
                specifications={"accuracy": "±0.01°C"},
                typical_depth="0.5m",
                typical_mount="bow",
            ),
            Sensor(
                id="test-fluorometer",
                name="Test Fluorometer",
                manufacturer="Test Corp",
                model="FLUOR-200",
                sensor_type=SensorType.FLUOROMETER,
                description="Test fluorometer",
                variables=["chlorophyll"],
                typical_depth="0.5m",
            ),
        ]
        return sensors

    @pytest.fixture
    def sample_platform(self):
        """Create sample platform metadata."""
        return {
            'id': 'test-platform-1',
            'type': 'Test Platform',
            'model': 'TestModel',
            'trajectory': 1030,
            'specifications': {
                'length': '7m',
                'speed': '0-6 knots',
            }
        }

    @pytest.fixture
    def temp_output_dir(self, tmp_path):
        """Create a temporary output directory with a sample parquet file."""
        output_dir = tmp_path / "test_output"
        output_dir.mkdir()
        
        # Create a dummy parquet file
        data = {
            'latitude': [10.0, 10.1],
            'longitude': [-170.0, -170.1],
            'temperature': [25.0, 25.1],
        }
        df = pd.DataFrame(data)
        
        parquet_dir = output_dir / "lat_bin=0" / "lon_bin=0"
        parquet_dir.mkdir(parents=True)
        df.to_parquet(parquet_dir / "data.parquet")
        
        return output_dir

    def test_stac_emission_with_instruments(self, temp_output_dir, sample_dataframe, sample_sensors):
        """Test STAC collection includes instruments when provided."""
        collection_path, item_paths = emit_stac_collection_and_item(
            temp_output_dir,
            sample_dataframe,
            semantic_metadata=None,
            provider_name="test-provider",
            instruments=sample_sensors,
        )
        
        assert collection_path.exists()
        
        with open(collection_path, 'r') as f:
            collection = json.load(f)
        
        # Check that instruments are in summaries
        assert 'summaries' in collection
        assert 'instruments' in collection['summaries']
        
        instruments = collection['summaries']['instruments']
        assert len(instruments) == 2
        
        # Verify instrument structure
        instrument_ids = {inst['id'] for inst in instruments}
        assert 'test-ctd' in instrument_ids
        assert 'test-fluorometer' in instrument_ids
        
        # Check first instrument details
        ctd_inst = next(inst for inst in instruments if inst['id'] == 'test-ctd')
        assert ctd_inst['name'] == 'Test CTD'
        assert ctd_inst['type'] == 'ctd'
        assert ctd_inst['manufacturer'] == 'Test Corp'
        assert ctd_inst['model'] == 'CTD-100'
        assert ctd_inst['variables'] == ['temperature', 'salinity']
        assert ctd_inst['depth'] == '0.5m'
        assert ctd_inst['mount_position'] == 'bow'
        assert ctd_inst['specifications']['accuracy'] == '±0.01°C'

    def test_stac_emission_with_platform(self, temp_output_dir, sample_dataframe, sample_platform):
        """Test STAC collection includes platform metadata when provided."""
        collection_path, item_paths = emit_stac_collection_and_item(
            temp_output_dir,
            sample_dataframe,
            semantic_metadata=None,
            provider_name="test-provider",
            platform=sample_platform,
        )
        
        assert collection_path.exists()
        
        with open(collection_path, 'r') as f:
            collection = json.load(f)
        
        # Check that platform is in summaries
        assert 'summaries' in collection
        assert 'platform' in collection['summaries']
        
        platform = collection['summaries']['platform']
        assert platform['id'] == 'test-platform-1'
        assert platform['type'] == 'Test Platform'
        assert platform['model'] == 'TestModel'
        assert platform['specifications']['length'] == '7m'

    def test_stac_emission_with_instruments_and_platform(
        self, temp_output_dir, sample_dataframe, sample_sensors, sample_platform
    ):
        """Test STAC collection includes both instruments and platform."""
        collection_path, item_paths = emit_stac_collection_and_item(
            temp_output_dir,
            sample_dataframe,
            semantic_metadata=None,
            provider_name="test-provider",
            instruments=sample_sensors,
            platform=sample_platform,
        )
        
        with open(collection_path, 'r') as f:
            collection = json.load(f)
        
        assert 'summaries' in collection
        assert 'instruments' in collection['summaries']
        assert 'platform' in collection['summaries']
        
        # Both should be present
        assert len(collection['summaries']['instruments']) == 2
        assert collection['summaries']['platform']['id'] == 'test-platform-1'

    def test_stac_emission_without_instruments_and_platform(self, temp_output_dir, sample_dataframe):
        """Test STAC collection works without instruments and platform (backward compatibility)."""
        collection_path, item_paths = emit_stac_collection_and_item(
            temp_output_dir,
            sample_dataframe,
            semantic_metadata=None,
            provider_name="test-provider",
        )
        
        assert collection_path.exists()
        
        with open(collection_path, 'r') as f:
            collection = json.load(f)
        
        # Should work without error
        assert collection['type'] == 'Collection'
        assert collection['id'] == 'oceanstream-test-provider-geoparquet'
        
        # Summaries should not have instruments/platform if not provided
        if 'summaries' in collection:
            assert 'instruments' not in collection['summaries']
            assert 'platform' not in collection['summaries']

    def test_stac_emission_with_empty_instruments(self, temp_output_dir, sample_dataframe):
        """Test STAC collection with empty instruments list."""
        collection_path, item_paths = emit_stac_collection_and_item(
            temp_output_dir,
            sample_dataframe,
            semantic_metadata=None,
            provider_name="test-provider",
            instruments=[],
        )
        
        with open(collection_path, 'r') as f:
            collection = json.load(f)
        
        # Should still create collection, but instruments array should be empty
        if 'summaries' in collection and 'instruments' in collection['summaries']:
            assert collection['summaries']['instruments'] == []

    def test_stac_instrument_all_fields_present(self, temp_output_dir, sample_dataframe):
        """Test that STAC instruments include all expected fields."""
        sensor_with_all_fields = Sensor(
            id="complete-sensor",
            name="Complete Sensor",
            manufacturer="Complete Corp",
            model="COMP-999",
            sensor_type=SensorType.METEOROLOGICAL,
            description="A sensor with all fields",
            variables=["var1", "var2", "var3"],
            specifications={"field1": "value1", "field2": "value2"},
            documentation_url="https://example.com/docs",
            typical_depth="1.5m",
            typical_mount="starboard",
        )
        
        collection_path, item_paths = emit_stac_collection_and_item(
            temp_output_dir,
            sample_dataframe,
            semantic_metadata=None,
            provider_name="test-provider",
            instruments=[sensor_with_all_fields],
        )
        
        with open(collection_path, 'r') as f:
            collection = json.load(f)
        
        instrument = collection['summaries']['instruments'][0]
        
        # Verify all fields are present
        assert instrument['id'] == 'complete-sensor'
        assert instrument['name'] == 'Complete Sensor'
        assert instrument['type'] == 'meteorological'
        assert instrument['manufacturer'] == 'Complete Corp'
        assert instrument['model'] == 'COMP-999'
        assert instrument['description'] == 'A sensor with all fields'
        assert instrument['variables'] == ['var1', 'var2', 'var3']
        assert instrument['mount_position'] == 'starboard'
        assert instrument['depth'] == '1.5m'
        assert instrument['documentation'] == 'https://example.com/docs'
        assert instrument['specifications'] == {'field1': 'value1', 'field2': 'value2'}


class TestSTACWithSaildroneSensors:
    """Integration tests for STAC with actual Saildrone sensors."""

    @pytest.fixture
    def temp_output_dir(self, tmp_path):
        """Create a temporary output directory with a sample parquet file."""
        output_dir = tmp_path / "saildrone_output"
        output_dir.mkdir()
        
        data = {
            'latitude': [10.0, 10.1],
            'longitude': [-170.0, -170.1],
            'TEMP_CTD_MEAN': [25.0, 25.1],
        }
        df = pd.DataFrame(data)
        
        parquet_dir = output_dir / "lat_bin=0" / "lon_bin=0"
        parquet_dir.mkdir(parents=True)
        df.to_parquet(parquet_dir / "data.parquet")
        
        return output_dir

    def test_stac_with_saildrone_sensors(self, temp_output_dir):
        """Test STAC emission with real Saildrone sensors from catalogue."""
        catalogue = get_sensor_catalogue()
        
        # Get some Saildrone sensors
        saildrone_sensors = [
            catalogue.get("sbe37-odo"),
            catalogue.get("wetlabs-flbbcd"),
            catalogue.get("airmar-150wx"),
        ]
        
        # Filter out None values in case sensors aren't registered
        saildrone_sensors = [s for s in saildrone_sensors if s is not None]
        
        assert len(saildrone_sensors) > 0, "Saildrone sensors should be in catalogue"
        
        # Create sample DataFrame
        data = {
            'time': pd.date_range('2023-01-01', periods=10, freq='1H'),
            'latitude': [10.0] * 10,
            'longitude': [-170.0] * 10,
            'TEMP_CTD_MEAN': [25.0] * 10,
        }
        df = pd.DataFrame(data)
        df['time'] = pd.to_datetime(df['time'])
        
        collection_path, item_paths = emit_stac_collection_and_item(
            temp_output_dir,
            df,
            semantic_metadata=None,
            provider_name="saildrone",
            instruments=saildrone_sensors,
        )
        
        with open(collection_path, 'r') as f:
            collection = json.load(f)
        
        instruments = collection['summaries']['instruments']
        assert len(instruments) == len(saildrone_sensors)
        
        # Verify Saildrone sensor details
        sensor_names = {inst['name'] for inst in instruments}
        assert any('SBE 37' in name for name in sensor_names)
        assert any('Airmar' in name or 'WET Labs' in name for name in sensor_names)

    def test_stac_with_saildrone_platform(self, temp_output_dir):
        """Test STAC emission with Saildrone platform metadata."""
        platform_metadata = {
            'id': 'sd1030',
            'type': 'Saildrone Explorer',
            'model': 'Explorer',
            'trajectory': 1030,
            'specifications': {
                'length': '7m',
                'draft': '2.5m',
                'speed_range': '0-6 knots',
            }
        }
        
        data = {
            'time': pd.date_range('2023-01-01', periods=10, freq='1H'),
            'latitude': [10.0] * 10,
            'longitude': [-170.0] * 10,
        }
        df = pd.DataFrame(data)
        df['time'] = pd.to_datetime(df['time'])
        
        collection_path, item_paths = emit_stac_collection_and_item(
            temp_output_dir,
            df,
            semantic_metadata=None,
            provider_name="saildrone",
            platform=platform_metadata,
        )
        
        with open(collection_path, 'r') as f:
            collection = json.load(f)
        
        platform = collection['summaries']['platform']
        assert platform['type'] == 'Saildrone Explorer'
        assert platform['specifications']['length'] == '7m'


class TestSTACPriority1Enhancements:
    """Integration tests for STAC Priority 1 enhancements."""

    @pytest.fixture
    def sample_dataframe_with_measurements(self):
        """Create a sample DataFrame with measurement data."""
        data = {
            'time': pd.date_range('2023-01-01', periods=100, freq='1H'),
            'latitude': [10.0 + i * 0.01 for i in range(100)],
            'longitude': [-170.0 + i * 0.01 for i in range(100)],
            'TEMP_AIR_MEAN': [25.0 + i * 0.05 for i in range(100)],
            'SAL_SBE37_MEAN': [35.0 + i * 0.01 for i in range(100)],
            'WIND_SPEED_MEAN': [5.0 + i * 0.02 for i in range(100)],
        }
        df = pd.DataFrame(data)
        df['time'] = pd.to_datetime(df['time'])
        return df

    @pytest.fixture
    def temp_output_dir_with_data(self, tmp_path, sample_dataframe_with_measurements):
        """Create a temporary output directory with sample parquet file."""
        output_dir = tmp_path / "test_output"
        output_dir.mkdir()
        
        parquet_dir = output_dir / "lat_bin=0" / "lon_bin=0"
        parquet_dir.mkdir(parents=True)
        sample_dataframe_with_measurements.to_parquet(parquet_dir / "data.parquet")
        
        return output_dir

    def test_processing_provenance_in_collection(
        self, temp_output_dir_with_data, sample_dataframe_with_measurements
    ):
        """Test that processing provenance is included in STAC collection."""
        collection_path, _ = emit_stac_collection_and_item(
            temp_output_dir_with_data,
            sample_dataframe_with_measurements,
            semantic_metadata=None,
            provider_name="test-provider",
            software_version="0.1.0-test",
        )
        
        with open(collection_path, 'r') as f:
            collection = json.load(f)
        
        # Check processing provenance exists
        assert 'summaries' in collection
        assert 'processing' in collection['summaries']
        
        processing = collection['summaries']['processing']
        assert processing['software'] == 'oceanstream'
        assert processing['version'] == '0.1.0-test'
        assert 'processing_date' in processing
        assert processing['processing_level'] == 'L2'
        
        # Verify processing_date is ISO format
        from datetime import datetime
        datetime.fromisoformat(processing['processing_date'])

    def test_measurement_statistics_in_collection(
        self, temp_output_dir_with_data, sample_dataframe_with_measurements
    ):
        """Test that measurement statistics are included in STAC collection."""
        # Calculate measurement statistics
        measurement_columns = ['TEMP_AIR_MEAN', 'SAL_SBE37_MEAN', 'WIND_SPEED_MEAN']
        measurement_stats = calculate_measurement_statistics(
            sample_dataframe_with_measurements, 
            measurement_columns
        )
        
        collection_path, _ = emit_stac_collection_and_item(
            temp_output_dir_with_data,
            sample_dataframe_with_measurements,
            semantic_metadata=None,
            provider_name="test-provider",
            measurement_stats=measurement_stats,
        )
        
        with open(collection_path, 'r') as f:
            collection = json.load(f)
        
        # Check measurement statistics exist
        assert 'summaries' in collection
        assert 'measurements' in collection['summaries']
        
        measurements = collection['summaries']['measurements']
        
        # Verify all measurement columns are present
        assert 'TEMP_AIR_MEAN' in measurements
        assert 'SAL_SBE37_MEAN' in measurements
        assert 'WIND_SPEED_MEAN' in measurements
        
        # Verify structure of each measurement stat
        for col in measurement_columns:
            assert 'min' in measurements[col]
            assert 'max' in measurements[col]
            assert 'mean' in measurements[col]
            assert 'count' in measurements[col]
            
            # Verify values are numeric
            assert isinstance(measurements[col]['min'], (int, float))
            assert isinstance(measurements[col]['max'], (int, float))
            assert isinstance(measurements[col]['mean'], (int, float))
            assert isinstance(measurements[col]['count'], int)
            
            # Verify min <= mean <= max
            assert measurements[col]['min'] <= measurements[col]['mean']
            assert measurements[col]['mean'] <= measurements[col]['max']

    def test_pmtiles_asset_in_item(
        self, temp_output_dir_with_data, sample_dataframe_with_measurements, tmp_path
    ):
        """Test that PMTiles asset is included in STAC item."""
        # Create a fake PMTiles file
        tiles_dir = tmp_path / "tiles"
        tiles_dir.mkdir(exist_ok=True)
        pmtiles_path = tiles_dir / "track.pmtiles"
        pmtiles_path.write_bytes(b"fake pmtiles binary data")
        
        collection_path, item_paths = emit_stac_collection_and_item(
            temp_output_dir_with_data,
            sample_dataframe_with_measurements,
            semantic_metadata=None,
            provider_name="test-provider",
            pmtiles_path=pmtiles_path,
        )
        
        # Check first item has PMTiles asset
        assert len(item_paths) > 0
        with open(item_paths[0], 'r') as f:
            item = json.load(f)
        
        # Verify PMTiles asset
        assert 'assets' in item
        assert 'pmtiles' in item['assets']
        
        pmtiles_asset = item['assets']['pmtiles']
        assert pmtiles_asset['type'] == 'application/vnd.pmtiles'
        assert 'visual' in pmtiles_asset['roles']
        assert 'tiles' in pmtiles_asset['roles']
        assert 'title' in pmtiles_asset
        assert 'PMTiles' in pmtiles_asset['title']

    def test_all_priority1_enhancements_together(
        self, temp_output_dir_with_data, sample_dataframe_with_measurements, tmp_path
    ):
        """Test all Priority 1 enhancements work together."""
        # Setup measurements
        measurement_columns = ['TEMP_AIR_MEAN', 'SAL_SBE37_MEAN']
        measurement_stats = calculate_measurement_statistics(
            sample_dataframe_with_measurements,
            measurement_columns
        )
        
        # Setup PMTiles
        tiles_dir = tmp_path / "tiles"
        tiles_dir.mkdir(exist_ok=True)
        pmtiles_path = tiles_dir / "track.pmtiles"
        pmtiles_path.write_text("fake data")
        
        # Setup sensors and platform
        sensors = [
            Sensor(
                id="test-ctd",
                name="Test CTD",
                manufacturer="Test Corp",
                model="CTD-100",
                sensor_type=SensorType.CTD,
                description="Test CTD",
                variables=["temperature", "salinity"],
            )
        ]
        
        platform = {
            'id': 'test-platform',
            'type': 'Test Platform',
            'model': 'V1',
        }
        
        # Generate STAC with all enhancements
        collection_path, item_paths = emit_stac_collection_and_item(
            temp_output_dir_with_data,
            sample_dataframe_with_measurements,
            semantic_metadata=None,
            provider_name="test-provider",
            instruments=sensors,
            platform=platform,
            pmtiles_path=pmtiles_path,
            measurement_stats=measurement_stats,
            software_version="0.1.0-integration-test",
        )
        
        # Load and verify collection
        with open(collection_path, 'r') as f:
            collection = json.load(f)
        
        summaries = collection['summaries']
        
        # Verify all enhancements are present
        assert 'processing' in summaries, "Missing processing provenance"
        assert 'measurements' in summaries, "Missing measurement statistics"
        assert 'instruments' in summaries, "Missing instruments"
        assert 'platform' in summaries, "Missing platform"
        
        # Verify processing
        assert summaries['processing']['software'] == 'oceanstream'
        assert summaries['processing']['version'] == '0.1.0-integration-test'
        
        # Verify measurements
        assert len(summaries['measurements']) == 2
        assert 'TEMP_AIR_MEAN' in summaries['measurements']
        
        # Verify instruments
        assert len(summaries['instruments']) == 1
        assert summaries['instruments'][0]['id'] == 'test-ctd'
        
        # Verify platform
        assert summaries['platform']['id'] == 'test-platform'
        
        # Load and verify item
        with open(item_paths[0], 'r') as f:
            item = json.load(f)
        
        # Verify PMTiles asset
        assert 'pmtiles' in item['assets']
