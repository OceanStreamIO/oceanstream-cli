"""Unit tests for Saildrone sensor definitions and platform detection."""
import pytest
from oceanstream.sensors.catalogue import SensorType, get_sensor_catalogue
from oceanstream.sensors.saildrone import (
    SENSORS,
    SAILDRONE_PLATFORM_SENSORS,
    detect_saildrone_platform,
    get_platform_sensors,
)


class TestSaildroneSensorDefinitions:
    """Tests for Saildrone sensor definitions."""

    def test_all_sensors_defined(self):
        """Test that all expected Saildrone sensors are defined."""
        expected_sensor_ids = [
            "sbe37-odo",
            "wetlabs-flbbcd",
            "airmar-150wx",
            "licor-li190r",
            "kipp-zonen-cmp",
            "apogee-si111",
            "thermistor-0.5m",
            "wave-imu",
            "imu-navigation",
        ]
        
        for sensor_id in expected_sensor_ids:
            assert sensor_id in SENSORS, f"Sensor {sensor_id} not found in SENSORS"

    def test_sbe37_odo_sensor(self):
        """Test SBE37-ODO CTD sensor definition."""
        sensor = SENSORS["sbe37-odo"]
        
        assert sensor.id == "sbe37-odo"
        assert sensor.name == "Sea-Bird SBE 37-SMP-ODO MicroCAT"
        assert sensor.manufacturer == "Sea-Bird Scientific"
        assert sensor.model == "SBE 37-SMP-ODO"
        assert sensor.sensor_type == SensorType.CTD
        assert "temperature" in sensor.description.lower()
        assert "conductivity" in sensor.description.lower()
        assert "oxygen" in sensor.description.lower()
        
        # Check key variables (actual Saildrone variable names)
        expected_vars = ["TEMP_SBE37_MEAN", "SAL_SBE37_MEAN", "O2_CONC_SBE37_MEAN"]
        for var in expected_vars:
            assert var in sensor.variables, f"Variable {var} not in SBE37 variables"
        
        assert sensor.typical_depth == "0.6m"
        assert sensor.specifications is not None
        assert "temperature_accuracy" in sensor.specifications

    def test_wetlabs_flbbcd_sensor(self):
        """Test WET Labs FLBBCD fluorometer definition."""
        sensor = SENSORS["wetlabs-flbbcd"]
        
        assert sensor.sensor_type == SensorType.FLUOROMETER
        assert "chlorophyll" in sensor.description.lower()
        
        # Check fluorescence variables (actual Saildrone variable names)
        assert "CHLOR_WETLABS_MEAN" in sensor.variables
        
        assert sensor.typical_depth == "0.6m"

    def test_airmar_150wx_sensor(self):
        """Test Airmar 150WX weather station definition."""
        sensor = SENSORS["airmar-150wx"]
        
        assert sensor.sensor_type == SensorType.METEOROLOGICAL
        assert "wind" in sensor.description.lower()
        
        # Check meteorological variables
        expected_vars = ["UWND_MEAN", "VWND_MEAN", "BARO_PRES_MEAN", "TEMP_AIR_MEAN"]
        for var in expected_vars:
            assert var in sensor.variables
        
        assert sensor.typical_mount == "wing"

    def test_licor_li190r_sensor(self):
        """Test LI-COR LI-190R PAR sensor definition."""
        sensor = SENSORS["licor-li190r"]
        
        assert sensor.sensor_type == SensorType.RADIATION
        assert "PAR" in sensor.description or "photosynthetically" in sensor.description.lower()
        assert "PAR_AIR_MEAN" in sensor.variables

    def test_kipp_zonen_cmp_sensor(self):
        """Test Kipp & Zonen CMP pyranometer definition."""
        sensor = SENSORS["kipp-zonen-cmp"]
        
        assert sensor.sensor_type == SensorType.RADIATION
        assert "SW_IRRAD_TOTAL_MEAN" in sensor.variables

    def test_apogee_si111_sensor(self):
        """Test Apogee SI-111 infrared radiometer definition."""
        sensor = SENSORS["apogee-si111"]
        
        assert sensor.sensor_type == SensorType.RADIATION
        assert "infrared" in sensor.description.lower()
        assert "TEMP_IR_SEA_WING_UNCOMP_MEAN" in sensor.variables

    def test_thermistor_sensor(self):
        """Test hull-mounted thermistor definition."""
        sensor = SENSORS["thermistor-0.5m"]
        
        assert sensor.sensor_type == SensorType.THERMISTOR
        assert "TEMP_DEPTH_HALFMETER_MEAN" in sensor.variables
        assert sensor.typical_depth == "0.5m"

    def test_airmar_150wx_sensor(self):
        """Test Airmar 150WX weather station definition."""
        sensor = SENSORS["airmar-150wx"]
        
        assert sensor.sensor_type == SensorType.METEOROLOGICAL
        assert "wind" in sensor.description.lower()
        
        # Check meteorological variables
        expected_vars = ["UWND_MEAN", "VWND_MEAN", "BARO_PRES_MEAN", "TEMP_AIR_MEAN"]
        for var in expected_vars:
            assert var in sensor.variables
        
        assert sensor.typical_mount == "wing"

    def test_licor_li190r_sensor(self):
        """Test LI-COR LI-190R PAR sensor definition."""
        sensor = SENSORS["licor-li190r"]
        
        assert sensor.sensor_type == SensorType.RADIATION
        assert "PAR" in sensor.description or "photosynthetically" in sensor.description.lower()
        assert "PAR_AIR_MEAN" in sensor.variables

    def test_kipp_zonen_cmp_sensor(self):
        """Test Kipp & Zonen CMP pyranometer definition."""
        sensor = SENSORS["kipp-zonen-cmp"]
        
        assert sensor.sensor_type == SensorType.RADIATION
        assert "SW_IRRAD_TOTAL_MEAN" in sensor.variables

    def test_apogee_si111_sensor(self):
        """Test Apogee SI-111 infrared radiometer definition."""
        sensor = SENSORS["apogee-si111"]
        
        assert sensor.sensor_type == SensorType.RADIATION
        assert "infrared" in sensor.description.lower()
        assert "TEMP_IR_SEA_WING_UNCOMP_MEAN" in sensor.variables

    def test_thermistor_sensor(self):
        """Test hull-mounted thermistor definition."""
        sensor = SENSORS["thermistor-0.5m"]
        
        assert sensor.sensor_type == SensorType.THERMISTOR
        assert "TEMP_DEPTH_HALFMETER_MEAN" in sensor.variables
        assert sensor.typical_depth == "0.5m"

    def test_kipp_zonen_cmp_sensor(self):
        """Test Kipp & Zonen CMP pyranometer definition."""
        sensor = SENSORS["kipp-zonen-cmp"]
        
        assert sensor.sensor_type == SensorType.RADIATION
        assert "SW_IRRAD_TOTAL_MEAN" in sensor.variables

    def test_apogee_si111_sensor(self):
        """Test Apogee SI-111 infrared radiometer definition."""
        sensor = SENSORS["apogee-si111"]
        
        assert sensor.sensor_type == SensorType.RADIATION
        assert "infrared" in sensor.description.lower()
        assert "TEMP_IR_SEA_WING_UNCOMP_MEAN" in sensor.variables

    def test_thermistor_sensor(self):
        """Test hull-mounted thermistor definition."""
        sensor = SENSORS["thermistor-0.5m"]
        
        assert sensor.sensor_type == SensorType.THERMISTOR
        assert "TEMP_DEPTH_HALFMETER_MEAN" in sensor.variables
        assert sensor.typical_depth == "0.5m"

    def test_wave_imu_sensor(self):
        """Test wave IMU sensor definition."""
        sensor = SENSORS["wave-imu"]
        
        assert sensor.sensor_type == SensorType.WAVE
        assert "wave" in sensor.description.lower()
        
        # Check for wave variables
        wave_vars = ["WAVE_SIGNIFICANT_HEIGHT", "WAVE_DOMINANT_PERIOD"]
        for var in wave_vars:
            assert var in sensor.variables

    def test_imu_navigation_sensor(self):
        """Test IMU & GPS navigation sensor definition."""
        sensor = SENSORS["imu-navigation"]
        
        assert sensor.sensor_type == SensorType.NAVIGATION
        
        # Check for IMU/navigation variables (Saildrone-specific filtered measurements)
        # Note: latitude/longitude are in gnss-navigation sensor, not IMU
        nav_vars = ["HDG", "SOG_FILTERED_MEAN", "COG_FILTERED_MEAN", 
                    "ROLL_FILTERED_MEAN", "PITCH_FILTERED_MEAN"]
        for var in nav_vars:
            assert var in sensor.variables

    def test_all_sensors_registered_in_global_catalogue(self):
        """Test that all Saildrone sensors are registered in the global catalogue."""
        catalogue = get_sensor_catalogue()
        
        for sensor_id in SENSORS.keys():
            registered = catalogue.get(sensor_id)
            assert registered is not None, f"Sensor {sensor_id} not registered in global catalogue"


class TestSaildronePlatformDetection:
    """Tests for Saildrone platform detection logic."""

    def test_detect_explorer_platform_range(self):
        """Test detecting Explorer platform for IDs 1000-1999."""
        assert detect_saildrone_platform(1000) == "Explorer"
        assert detect_saildrone_platform(1030) == "Explorer"
        assert detect_saildrone_platform(1500) == "Explorer"
        assert detect_saildrone_platform(1999) == "Explorer"

    def test_detect_surveyor_platform_range(self):
        """Test detecting Surveyor platform for IDs 2000+."""
        assert detect_saildrone_platform(2000) == "Surveyor"
        assert detect_saildrone_platform(2001) == "Surveyor"
        assert detect_saildrone_platform(3000) == "Surveyor"
        assert detect_saildrone_platform(9999) == "Surveyor"

    def test_detect_unknown_platform(self):
        """Test detecting unknown platform for IDs < 1000."""
        assert detect_saildrone_platform(500) == "Unknown"
        assert detect_saildrone_platform(999) == "Unknown"
        assert detect_saildrone_platform(0) == "Unknown"

    def test_platform_sensors_configuration(self):
        """Test that platform sensor configurations are defined."""
        assert "Explorer" in SAILDRONE_PLATFORM_SENSORS
        assert "Surveyor" in SAILDRONE_PLATFORM_SENSORS
        
        explorer_sensors = SAILDRONE_PLATFORM_SENSORS["Explorer"]
        surveyor_sensors = SAILDRONE_PLATFORM_SENSORS["Surveyor"]
        
        assert len(explorer_sensors) > 0
        assert len(surveyor_sensors) > 0

    def test_get_platform_sensors_explorer(self):
        """Test getting sensors for Explorer platform."""
        sensors = get_platform_sensors("Explorer")
        
        assert isinstance(sensors, list)
        assert len(sensors) > 0
        
        # Explorer should have standard sensors
        expected_ids = [
            "sbe37-odo",
            "wetlabs-flbbcd",
            "airmar-150wx",
        ]
        for sensor_id in expected_ids:
            assert sensor_id in sensors, f"Explorer missing {sensor_id}"

    def test_get_platform_sensors_surveyor(self):
        """Test getting sensors for Surveyor platform."""
        sensors = get_platform_sensors("Surveyor")
        
        assert isinstance(sensors, list)
        assert len(sensors) > 0
        
        # Surveyor should have all Explorer sensors plus additional ones
        explorer_sensors = get_platform_sensors("Explorer")
        for sensor_id in explorer_sensors:
            assert sensor_id in sensors, f"Surveyor missing Explorer sensor {sensor_id}"

    def test_get_platform_sensors_unknown(self):
        """Test getting sensors for unknown platform."""
        sensors = get_platform_sensors("Unknown")
        assert sensors == []

    def test_get_platform_sensors_invalid(self):
        """Test getting sensors for invalid platform name."""
        sensors = get_platform_sensors("InvalidPlatform")
        assert sensors == []


class TestSaildroneSensorVariableCoverage:
    """Tests for ensuring comprehensive variable coverage across sensors."""

    def test_ctd_variables_coverage(self):
        """Test that CTD-related variables are properly assigned."""
        catalogue = get_sensor_catalogue()
        
        # Use actual Saildrone variable names
        ctd_vars = [
            "TEMP_SBE37_MEAN", "SAL_SBE37_MEAN", "COND_SBE37_MEAN",
            "O2_CONC_SBE37_MEAN", "O2_SAT_SBE37_MEAN"
        ]
        
        detected = catalogue.detect_sensors(set(ctd_vars))
        assert len(detected) > 0
        
        # Should detect the SBE37 CTD
        ctd_sensor_ids = {s.id for s in detected}
        assert "sbe37-odo" in ctd_sensor_ids

    def test_fluorometer_variables_coverage(self):
        """Test that fluorometer variables are properly assigned."""
        catalogue = get_sensor_catalogue()
        
        fluor_vars = ["CHLOR_WETLABS_MEAN", "BACKSCATTER_650_SCALED", "CDOM_WETLABS_MEAN"]
        
        detected = catalogue.detect_sensors(set(fluor_vars))
        assert len(detected) > 0
        
        # Should detect the WET Labs fluorometer
        sensor_ids = {s.id for s in detected}
        assert "wetlabs-flbbcd" in sensor_ids

    def test_meteorological_variables_coverage(self):
        """Test that meteorological variables are properly assigned."""
        catalogue = get_sensor_catalogue()
        
        met_vars = ["UWND_MEAN", "VWND_MEAN", "BARO_PRES_MEAN", "TEMP_AIR_MEAN"]
        
        detected = catalogue.detect_sensors(set(met_vars))
        assert len(detected) > 0
        
        # Should detect the Airmar weather station
        sensor_ids = {s.id for s in detected}
        assert "airmar-150wx" in sensor_ids

    def test_radiation_variables_coverage(self):
        """Test that radiation variables are properly assigned."""
        catalogue = get_sensor_catalogue()
        
        # Use actual Saildrone variable names - check each sensor individually
        rad_vars = {
            "SW_IRRAD_TOTAL_MEAN",  # Kipp & Zonen
            "PAR_AIR_MEAN",  # LI-COR
            "TEMP_IR_SEA_WING_UNCOMP_MEAN"  # Apogee
        }
        
        detected = catalogue.detect_sensors(rad_vars)
        assert len(detected) >= 3  # Should detect all 3 radiation sensors
        
        sensor_ids = {s.id for s in detected}
        assert "licor-li190r" in sensor_ids
        assert "kipp-zonen-cmp" in sensor_ids
        assert "apogee-si111" in sensor_ids

    def test_wave_variables_coverage(self):
        """Test that wave variables are properly assigned."""
        catalogue = get_sensor_catalogue()
        
        wave_vars = ["WAVE_SIGNIFICANT_HEIGHT", "WAVE_DOMINANT_PERIOD"]
        
        detected = catalogue.detect_sensors(set(wave_vars))
        assert len(detected) > 0
        
        sensor_ids = {s.id for s in detected}
        assert "wave-imu" in sensor_ids

    def test_navigation_variables_coverage(self):
        """Test that navigation variables are properly assigned."""
        catalogue = get_sensor_catalogue()
        
        nav_vars = ["latitude", "longitude", "COG", "SOG", "HDG"]
        
        detected = catalogue.detect_sensors(set(nav_vars))
        assert len(detected) > 0
        
        sensor_ids = {s.id for s in detected}
        assert "imu-navigation" in sensor_ids
