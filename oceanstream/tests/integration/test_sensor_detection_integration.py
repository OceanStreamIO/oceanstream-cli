"""Integration tests for sensor detection in the geotrack processor."""
import pytest
import pandas as pd
from pathlib import Path
from oceanstream.geotrack.processor import GeotrackProcessor
from oceanstream.providers.saildrone import SaildroneProvider
from oceanstream.sensors.catalogue import SensorType


class TestGeotrackProcessorSensorDetection:
    """Integration tests for sensor detection in GeotrackProcessor."""

    @pytest.fixture
    def sample_dataframe(self):
        """Create a sample DataFrame with Saildrone variables."""
        data = {
            'time': pd.date_range('2023-01-01', periods=100, freq='1min'),
            'latitude': [10.0 + i * 0.001 for i in range(100)],
            'longitude': [-170.0 + i * 0.001 for i in range(100)],
            'trajectory': [1030] * 100,  # Explorer platform
            'TEMP_SBE37_MEAN': [25.0 + i * 0.01 for i in range(100)],
            'SAL_SBE37_MEAN': [35.0 + i * 0.001 for i in range(100)],
            'O2_CONC_SBE37_MEAN': [200.0 + i * 0.1 for i in range(100)],
            'CHLOR_WETLABS_MEAN': [0.5 + i * 0.001 for i in range(100)],
            'UWND_MEAN': [5.0 + i * 0.01 for i in range(100)],
            'VWND_MEAN': [3.0 + i * 0.01 for i in range(100)],
            'SW_IRRAD_TOTAL_MEAN': [800.0 + i * 0.1 for i in range(100)],
        }
        return pd.DataFrame(data)

    @pytest.fixture
    def processor(self):
        """Create a GeotrackProcessor instance."""
        provider = SaildroneProvider()
        return GeotrackProcessor(provider, verbose=False)

    def test_detect_sensors_from_dataframe(self, processor, sample_dataframe):
        """Test detecting sensors from a DataFrame with Saildrone variables."""
        detected_sensors, platform_metadata = processor.detect_sensors_and_platform(sample_dataframe)
        
        # Should detect multiple sensors
        assert len(detected_sensors) > 0
        
        # Check for specific expected sensors
        sensor_ids = {s.id for s in detected_sensors}
        assert "sbe37-odo" in sensor_ids  # CTD
        assert "wetlabs-flbbcd" in sensor_ids  # Fluorometer
        assert "airmar-150wx" in sensor_ids  # Weather station
        assert "kipp-zonen-cmp" in sensor_ids  # Pyranometer

    def test_detect_platform_explorer(self, processor, sample_dataframe):
        """Test detecting Explorer platform from trajectory ID."""
        detected_sensors, platform_metadata = processor.detect_sensors_and_platform(sample_dataframe)
        
        assert platform_metadata is not None
        assert platform_metadata['type'] == 'Saildrone Explorer'
        assert platform_metadata['model'] == 'Explorer'
        assert platform_metadata['id'] == 'sd1030'
        assert platform_metadata['trajectory'] == 1030
        
        # Check specifications
        assert 'specifications' in platform_metadata
        specs = platform_metadata['specifications']
        assert 'length' in specs
        assert specs['length'] == '7m'

    def test_detect_platform_surveyor(self, processor):
        """Test detecting Surveyor platform from trajectory ID."""
        # Create DataFrame with Surveyor trajectory
        data = {
            'time': pd.date_range('2023-01-01', periods=10, freq='1min'),
            'latitude': [10.0] * 10,
            'longitude': [-170.0] * 10,
            'trajectory': [2001] * 10,  # Surveyor platform
            'TEMP_CTD_MEAN': [25.0] * 10,
        }
        df = pd.DataFrame(data)
        
        detected_sensors, platform_metadata = processor.detect_sensors_and_platform(df)
        
        assert platform_metadata['type'] == 'Saildrone Surveyor'
        assert platform_metadata['model'] == 'Surveyor'
        assert platform_metadata['id'] == 'sd2001'
        assert platform_metadata['specifications']['length'] in ['10m or 12m']

    def test_detect_sensors_minimal_variables(self, processor):
        """Test sensor detection with minimal variables."""
        data = {
            'time': pd.date_range('2023-01-01', periods=10, freq='1min'),
            'latitude': [10.0] * 10,
            'longitude': [-170.0] * 10,
            'trajectory': [1030] * 10,
        }
        df = pd.DataFrame(data)
        
        detected_sensors, platform_metadata = processor.detect_sensors_and_platform(df)
        
        # Should detect navigation sensor (has latitude, longitude)
        assert len(detected_sensors) > 0
        sensor_ids = {s.id for s in detected_sensors}
        assert "imu-navigation" in sensor_ids

    def test_detect_sensors_no_trajectory(self, processor):
        """Test sensor detection when trajectory column is missing."""
        data = {
            'time': pd.date_range('2023-01-01', periods=10, freq='1min'),
            'latitude': [10.0] * 10,
            'longitude': [-170.0] * 10,
            'TEMP_CTD_MEAN': [25.0] * 10,
        }
        df = pd.DataFrame(data)
        
        detected_sensors, platform_metadata = processor.detect_sensors_and_platform(df)
        
        # Should still detect sensors
        assert len(detected_sensors) > 0
        
        # Platform metadata should be empty dict
        assert platform_metadata == {}

    def test_detect_sensors_with_platform_id(self, processor):
        """Test that platform_id is included when available."""
        data = {
            'time': pd.date_range('2023-01-01', periods=10, freq='1min'),
            'latitude': [10.0] * 10,
            'longitude': [-170.0] * 10,
            'trajectory': [1030] * 10,
            'platform_id': ['tpos'] * 10,
            'TEMP_CTD_MEAN': [25.0] * 10,
        }
        df = pd.DataFrame(data)
        
        detected_sensors, platform_metadata = processor.detect_sensors_and_platform(df)
        
        assert 'platform_id' in platform_metadata
        assert platform_metadata['platform_id'] == 'tpos'

    def test_sensor_types_detected(self, processor, sample_dataframe):
        """Test that sensors of different types are detected."""
        detected_sensors, platform_metadata = processor.detect_sensors_and_platform(sample_dataframe)
        
        sensor_types = {s.sensor_type for s in detected_sensors}
        
        # Should have multiple sensor types
        assert SensorType.CTD in sensor_types
        assert SensorType.FLUOROMETER in sensor_types
        assert SensorType.METEOROLOGICAL in sensor_types
        assert SensorType.RADIATION in sensor_types

    def test_verbose_output(self, sample_dataframe, capsys):
        """Test that verbose mode prints sensor detection info."""
        provider = SaildroneProvider()
        verbose_processor = GeotrackProcessor(provider, verbose=True)
        
        verbose_processor.detect_sensors_and_platform(sample_dataframe)
        
        captured = capsys.readouterr()
        assert "Detected" in captured.out
        assert "sensors" in captured.out
        assert "Platform" in captured.out

    def test_sensor_stac_conversion(self, processor, sample_dataframe):
        """Test that detected sensors can be converted to STAC format."""
        detected_sensors, platform_metadata = processor.detect_sensors_and_platform(sample_dataframe)
        
        # Convert to STAC instruments
        stac_instruments = [s.to_stac_instrument() for s in detected_sensors]
        
        assert len(stac_instruments) > 0
        
        # Check STAC format
        for inst in stac_instruments:
            assert 'id' in inst
            assert 'name' in inst
            assert 'type' in inst
            assert 'manufacturer' in inst
            assert 'model' in inst
            assert 'description' in inst
            assert 'variables' in inst

    def test_detected_sensors_have_required_fields(self, processor, sample_dataframe):
        """Test that all detected sensors have required fields populated."""
        detected_sensors, platform_metadata = processor.detect_sensors_and_platform(sample_dataframe)
        
        for sensor in detected_sensors:
            assert sensor.id is not None
            assert sensor.name is not None
            assert sensor.manufacturer is not None
            assert sensor.model is not None
            assert sensor.sensor_type is not None
            assert sensor.description is not None
            assert len(sensor.variables) > 0


class TestSensorDetectionWithRealData:
    """Integration tests using actual test data files."""

    @pytest.fixture
    def test_data_dir(self):
        """Get the saildrone test data directory."""
        return Path(__file__).parent.parent.parent.parent / "raw_data" / "saildrone"

    def test_detect_sensors_from_real_csv(self, test_data_dir):
        """Test sensor detection with real Saildrone CSV files."""
        # Skip if test data not available
        if not test_data_dir.exists():
            pytest.skip("Test data directory not available")
        
        csv_files = list(test_data_dir.glob("sd*.csv"))
        if not csv_files:
            pytest.skip("No CSV files in test data directory")
        
        # Read first CSV
        df = pd.read_csv(csv_files[0], nrows=100)
        
        provider = SaildroneProvider()
        processor = GeotrackProcessor(provider, verbose=False)
        
        detected_sensors, platform_metadata = processor.detect_sensors_and_platform(df)
        
        # Should detect sensors from real data
        assert len(detected_sensors) > 0
        assert platform_metadata != {}
        
        # Should detect standard Saildrone sensors
        sensor_ids = {s.id for s in detected_sensors}
        
        # Most Saildrone missions have these core sensors
        core_sensors = [
            "sbe37-odo",
            "airmar-150wx",
            "imu-navigation",
        ]
        
        for sensor_id in core_sensors:
            assert sensor_id in sensor_ids, f"Core sensor {sensor_id} not detected in real data"

    def test_platform_detection_from_real_csv(self, test_data_dir):
        """Test platform detection with real Saildrone CSV files."""
        if not test_data_dir.exists():
            pytest.skip("Test data directory not available")
        
        csv_files = list(test_data_dir.glob("sd*.csv"))
        if not csv_files:
            pytest.skip("No CSV files in test data directory")
        
        # Read first CSV
        df = pd.read_csv(csv_files[0], nrows=10)
        
        provider = SaildroneProvider()
        processor = GeotrackProcessor(provider, verbose=False)
        
        detected_sensors, platform_metadata = processor.detect_sensors_and_platform(df)
        
        # Platform should be detected
        assert 'type' in platform_metadata
        assert 'Saildrone' in platform_metadata['type']
        assert platform_metadata['model'] in ['Explorer', 'Surveyor']
        assert 'specifications' in platform_metadata
