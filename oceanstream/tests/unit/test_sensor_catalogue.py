"""Unit tests for the sensor catalogue system."""
import pytest
from oceanstream.sensors.catalogue import (
    Sensor,
    SensorType,
    SensorCatalogue,
    get_sensor_catalogue,
)


class TestSensorDataclass:
    """Tests for the Sensor dataclass."""

    def test_sensor_creation_minimal(self):
        """Test creating a sensor with minimal required fields."""
        sensor = Sensor(
            id="test-sensor",
            name="Test Sensor",
            manufacturer="Test Corp",
            model="TS-100",
            sensor_type=SensorType.OTHER,
            description="A test sensor",
            variables=["test_var"],
        )
        assert sensor.id == "test-sensor"
        assert sensor.name == "Test Sensor"
        assert sensor.manufacturer == "Test Corp"
        assert sensor.model == "TS-100"
        assert sensor.sensor_type == SensorType.OTHER
        assert sensor.variables == ["test_var"]
        assert sensor.specifications == {}
        assert sensor.documentation_url is None
        assert sensor.typical_depth is None
        assert sensor.typical_mount is None

    def test_sensor_creation_full(self):
        """Test creating a sensor with all fields."""
        sensor = Sensor(
            id="full-sensor",
            name="Full Test Sensor",
            manufacturer="Test Corp",
            model="TS-200",
            sensor_type=SensorType.CTD,
            description="A fully specified test sensor",
            variables=["temp", "sal", "depth"],
            specifications={"accuracy": "±0.01°C", "range": "0-40°C"},
            documentation_url="https://example.com/docs",
            typical_depth="0.5m",
            typical_mount="bow",
        )
        assert sensor.specifications == {"accuracy": "±0.01°C", "range": "0-40°C"}
        assert sensor.documentation_url == "https://example.com/docs"
        assert sensor.typical_depth == "0.5m"
        assert sensor.typical_mount == "bow"

    def test_sensor_to_stac_instrument(self):
        """Test conversion of sensor to STAC instrument format."""
        sensor = Sensor(
            id="stac-sensor",
            name="STAC Test Sensor",
            manufacturer="STAC Corp",
            model="SC-300",
            sensor_type=SensorType.FLUOROMETER,
            description="A sensor for STAC testing",
            variables=["chl_a", "cdom"],
            specifications={"wavelengths": "470nm, 700nm"},
            documentation_url="https://example.com/stac",
            typical_depth="2.0m",
            typical_mount="starboard",
        )
        
        stac_inst = sensor.to_stac_instrument()
        
        assert stac_inst["id"] == "stac-sensor"
        assert stac_inst["name"] == "STAC Test Sensor"
        assert stac_inst["type"] == "fluorometer"
        assert stac_inst["manufacturer"] == "STAC Corp"
        assert stac_inst["model"] == "SC-300"
        assert stac_inst["description"] == "A sensor for STAC testing"
        assert stac_inst["variables"] == ["chl_a", "cdom"]
        assert stac_inst["mount_position"] == "starboard"
        assert stac_inst["depth"] == "2.0m"
        assert stac_inst["documentation"] == "https://example.com/stac"
        assert stac_inst["specifications"] == {"wavelengths": "470nm, 700nm"}

    def test_sensor_to_stac_instrument_minimal(self):
        """Test STAC conversion with minimal fields (no optional data)."""
        sensor = Sensor(
            id="minimal-sensor",
            name="Minimal Sensor",
            manufacturer="Min Corp",
            model="M-1",
            sensor_type=SensorType.OTHER,
            description="Minimal",
            variables=["var1"],
        )
        
        stac_inst = sensor.to_stac_instrument()
        
        assert stac_inst["id"] == "minimal-sensor"
        assert stac_inst["type"] == "other"
        assert "mount_position" not in stac_inst
        assert "depth" not in stac_inst
        assert "documentation" not in stac_inst
        assert "specifications" not in stac_inst


class TestSensorCatalogue:
    """Tests for the SensorCatalogue class."""

    def test_catalogue_creation(self):
        """Test creating an empty catalogue."""
        catalogue = SensorCatalogue()
        assert len(catalogue.list_all()) == 0

    def test_register_and_get_sensor(self):
        """Test registering and retrieving a sensor."""
        catalogue = SensorCatalogue()
        sensor = Sensor(
            id="reg-sensor",
            name="Registered Sensor",
            manufacturer="Reg Corp",
            model="R-1",
            sensor_type=SensorType.OXYGEN,
            description="A registered sensor",
            variables=["oxygen"],
        )
        
        catalogue.register(sensor)
        
        retrieved = catalogue.get("reg-sensor")
        assert retrieved is not None
        assert retrieved.id == "reg-sensor"
        assert retrieved.name == "Registered Sensor"
        assert retrieved.sensor_type == SensorType.OXYGEN

    def test_get_nonexistent_sensor(self):
        """Test getting a sensor that doesn't exist."""
        catalogue = SensorCatalogue()
        result = catalogue.get("nonexistent")
        assert result is None

    def test_list_all_sensors(self):
        """Test listing all sensors in the catalogue."""
        catalogue = SensorCatalogue()
        
        sensor1 = Sensor(
            id="sensor-1", name="Sensor 1", manufacturer="Corp", model="M1",
            sensor_type=SensorType.CTD, description="First", variables=["temp"]
        )
        sensor2 = Sensor(
            id="sensor-2", name="Sensor 2", manufacturer="Corp", model="M2",
            sensor_type=SensorType.FLUOROMETER, description="Second", variables=["chl"]
        )
        
        catalogue.register(sensor1)
        catalogue.register(sensor2)
        
        all_sensors = catalogue.list_all()
        assert len(all_sensors) == 2
        assert any(s.id == "sensor-1" for s in all_sensors)
        assert any(s.id == "sensor-2" for s in all_sensors)

    def test_find_by_type(self):
        """Test finding sensors by type."""
        catalogue = SensorCatalogue()
        
        ctd_sensor = Sensor(
            id="ctd-1", name="CTD", manufacturer="Corp", model="C1",
            sensor_type=SensorType.CTD, description="CTD", variables=["temp"]
        )
        fluor_sensor = Sensor(
            id="fluor-1", name="Fluorometer", manufacturer="Corp", model="F1",
            sensor_type=SensorType.FLUOROMETER, description="Fluor", variables=["chl"]
        )
        another_ctd = Sensor(
            id="ctd-2", name="Another CTD", manufacturer="Corp", model="C2",
            sensor_type=SensorType.CTD, description="CTD2", variables=["sal"]
        )
        
        catalogue.register(ctd_sensor)
        catalogue.register(fluor_sensor)
        catalogue.register(another_ctd)
        
        ctd_sensors = catalogue.find_by_type(SensorType.CTD)
        assert len(ctd_sensors) == 2
        assert all(s.sensor_type == SensorType.CTD for s in ctd_sensors)
        
        fluor_sensors = catalogue.find_by_type(SensorType.FLUOROMETER)
        assert len(fluor_sensors) == 1
        assert fluor_sensors[0].id == "fluor-1"

    def test_detect_sensors_from_variables(self):
        """Test detecting sensors based on available variables."""
        catalogue = SensorCatalogue()
        
        sensor1 = Sensor(
            id="temp-sensor", name="Temperature", manufacturer="Corp", model="T1",
            sensor_type=SensorType.CTD, description="Temp", variables=["TEMP_CTD_MEAN"]
        )
        sensor2 = Sensor(
            id="sal-sensor", name="Salinity", manufacturer="Corp", model="S1",
            sensor_type=SensorType.CTD, description="Sal", variables=["SAL_CTD_MEAN"]
        )
        sensor3 = Sensor(
            id="multi-sensor", name="Multi", manufacturer="Corp", model="M1",
            sensor_type=SensorType.CTD, description="Multi",
            variables=["OXYGEN_CONCENTRATION", "OXYGEN_SATURATION"]
        )
        
        catalogue.register(sensor1)
        catalogue.register(sensor2)
        catalogue.register(sensor3)
        
        # Variables that match sensor1 and sensor3
        available_vars = {"TEMP_CTD_MEAN", "OXYGEN_CONCENTRATION", "PRESSURE", "OTHER_VAR"}
        
        detected = catalogue.detect_sensors(available_vars)
        assert len(detected) == 2
        detected_ids = {s.id for s in detected}
        assert "temp-sensor" in detected_ids
        assert "multi-sensor" in detected_ids
        assert "sal-sensor" not in detected_ids

    def test_detect_sensors_no_matches(self):
        """Test detecting sensors when no variables match."""
        catalogue = SensorCatalogue()
        
        sensor = Sensor(
            id="sensor-1", name="Sensor", manufacturer="Corp", model="S1",
            sensor_type=SensorType.OTHER, description="Test", variables=["VAR_A", "VAR_B"]
        )
        catalogue.register(sensor)
        
        available_vars = {"VAR_C", "VAR_D"}
        detected = catalogue.detect_sensors(available_vars)
        assert len(detected) == 0

    def test_detect_sensors_empty_catalogue(self):
        """Test detecting sensors in an empty catalogue."""
        catalogue = SensorCatalogue()
        available_vars = {"VAR_A", "VAR_B"}
        detected = catalogue.detect_sensors(available_vars)
        assert len(detected) == 0

    def test_to_stac_instruments(self):
        """Test converting all sensors to STAC instrument format."""
        catalogue = SensorCatalogue()
        
        sensor1 = Sensor(
            id="s1", name="Sensor 1", manufacturer="Corp", model="M1",
            sensor_type=SensorType.CTD, description="First", variables=["v1"]
        )
        sensor2 = Sensor(
            id="s2", name="Sensor 2", manufacturer="Corp", model="M2",
            sensor_type=SensorType.OXYGEN, description="Second", variables=["v2"]
        )
        
        catalogue.register(sensor1)
        catalogue.register(sensor2)
        
        stac_instruments = catalogue.to_stac_instruments(["s1", "s2"])
        assert len(stac_instruments) == 2
        assert any(inst["id"] == "s1" for inst in stac_instruments)
        assert any(inst["id"] == "s2" for inst in stac_instruments)


class TestGlobalCatalogue:
    """Tests for the global catalogue instance."""

    def test_get_global_catalogue(self):
        """Test getting the global catalogue instance."""
        catalogue = get_sensor_catalogue()
        assert isinstance(catalogue, SensorCatalogue)
        
        # Should return the same instance
        catalogue2 = get_sensor_catalogue()
        assert catalogue is catalogue2

    def test_global_catalogue_has_saildrone_sensors(self):
        """Test that global catalogue includes Saildrone sensors."""
        catalogue = get_sensor_catalogue()
        
        # Should have sensors registered from saildrone.py
        all_sensors = catalogue.list_all()
        assert len(all_sensors) > 0
        
        # Check for specific Saildrone sensors
        sbe37 = catalogue.get("sbe37-odo")
        assert sbe37 is not None
        assert sbe37.name == "Sea-Bird SBE 37-SMP-ODO MicroCAT"
        
        wetlabs = catalogue.get("wetlabs-flbbcd")
        assert wetlabs is not None
        assert wetlabs.sensor_type == SensorType.FLUOROMETER


class TestSensorType:
    """Tests for the SensorType enum."""

    def test_sensor_type_values(self):
        """Test that all expected sensor types are defined."""
        expected_types = {
            "CTD": "ctd",
            "OXYGEN": "dissolved_oxygen",
            "FLUOROMETER": "fluorometer",
            "METEOROLOGICAL": "meteorological",
            "RADIATION": "radiation",
            "WAVE": "wave",
            "NAVIGATION": "navigation",
            "ACOUSTIC": "acoustic",
            "CURRENT": "current",
            "THERMISTOR": "thermistor",
            "OTHER": "other"
        }
        
        for type_name, value in expected_types.items():
            assert hasattr(SensorType, type_name)
            assert SensorType[type_name].value == value

    def test_sensor_type_string_conversion(self):
        """Test converting sensor types to strings."""
        assert SensorType.CTD.value == "ctd"
        assert SensorType.FLUOROMETER.value == "fluorometer"
        assert SensorType.METEOROLOGICAL.value == "meteorological"
