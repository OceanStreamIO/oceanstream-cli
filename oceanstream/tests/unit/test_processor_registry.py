"""Unit tests for the sensor processor registry."""
from __future__ import annotations


class TestProcessorRegistry:
    """Tests for processor auto-registration after module import."""

    def test_sensor_processors_registered(self):
        """All expected sensor type processors are registered."""
        from oceanstream.sensors.processors import SENSOR_PROCESSORS

        expected_types = {"fluorometer", "acoustic", "ctd", "winch"}
        for sensor_type in expected_types:
            assert sensor_type in SENSOR_PROCESSORS, (
                f"No SENSOR_PROCESSORS entry for '{sensor_type}'"
            )

    def test_raw_processors_registered(self):
        """All expected raw processors are registered by sensor ID."""
        from oceanstream.sensors.processors import RAW_PROCESSORS

        expected_ids = {
            "wetlabs-eco-flntu",
            "valeport-minisvs",
            "sbe-911plus",
            "lci90i-winch",
            "gnss-navigation",
        }
        for sensor_id in expected_ids:
            assert sensor_id in RAW_PROCESSORS, (
                f"No RAW_PROCESSORS entry for '{sensor_id}'"
            )

    def test_get_sensor_processor_returns_callable(self):
        """get_sensor_processor returns a callable for known types."""
        from oceanstream.sensors.processors import get_sensor_processor

        proc = get_sensor_processor("ctd")
        assert proc is not None
        assert callable(proc)

    def test_get_sensor_processor_unknown_returns_none(self):
        """get_sensor_processor returns None for unknown types."""
        from oceanstream.sensors.processors import get_sensor_processor

        assert get_sensor_processor("nonexistent_sensor_type") is None

    def test_get_raw_processor_returns_callable(self):
        """get_raw_processor returns a callable for known sensor IDs."""
        from oceanstream.sensors.processors import get_raw_processor

        proc = get_raw_processor("sbe-911plus")
        assert proc is not None
        assert callable(proc)

    def test_get_raw_processor_unknown_returns_none(self):
        """get_raw_processor returns None for unknown sensor IDs."""
        from oceanstream.sensors.processors import get_raw_processor

        assert get_raw_processor("nonexistent_sensor_id") is None

    def test_example_sensor_not_auto_registered(self):
        """example_sensor is not imported by __init__.py, so it's not registered."""
        from oceanstream.sensors.processors import SENSOR_PROCESSORS

        assert "example" not in SENSOR_PROCESSORS


class TestR2RMetadataConsolidation:
    """Tests verifying the two r2r_metadata import paths resolve to the same classes."""

    def test_r2r_file_info_is_same_class(self):
        """Both import paths return the same R2RFileInfo class."""
        from oceanstream.providers.r2r_metadata import R2RFileInfo as TopLevel
        from oceanstream.providers.r2r.r2r_metadata import R2RFileInfo as SubPkg

        assert TopLevel is SubPkg

    def test_r2r_sensor_info_is_same_class(self):
        """Both import paths return the same R2RSensorInfo class."""
        from oceanstream.providers.r2r_metadata import R2RSensorInfo as TopLevel
        from oceanstream.providers.r2r.r2r_metadata import R2RSensorInfo as SubPkg

        assert TopLevel is SubPkg

    def test_extra_field_always_dict(self):
        """R2RFileInfo and R2RSensorInfo extra field defaults to empty dict, never None."""
        from oceanstream.providers.r2r_metadata import R2RFileInfo, R2RSensorInfo

        fi = R2RFileInfo()
        si = R2RSensorInfo()
        assert fi.extra == {}
        assert isinstance(fi.extra, dict)
        assert si.extra == {}
        assert isinstance(si.extra, dict)


class TestSensorExtraField:
    """Tests for the Sensor.extra field capturing unknown JSON keys."""

    def test_sbe_911plus_extra_has_raw_variables(self):
        """sbe-911plus sensor.json has extra fields like raw_variables."""
        from oceanstream.sensors.catalogue import get_sensor_catalogue

        sensor = get_sensor_catalogue().get("sbe-911plus")
        assert sensor is not None
        assert isinstance(sensor.extra, dict)
        assert "raw_variables" in sensor.extra

    def test_lci90i_winch_extra_has_data_format(self):
        """lci90i-winch sensor.json has extra fields like data_format."""
        from oceanstream.sensors.catalogue import get_sensor_catalogue

        sensor = get_sensor_catalogue().get("lci90i-winch")
        assert sensor is not None
        assert isinstance(sensor.extra, dict)
        assert "data_format" in sensor.extra

    def test_simple_sensor_extra_is_empty(self):
        """Sensors without extra JSON fields have an empty extra dict."""
        from oceanstream.sensors.catalogue import get_sensor_catalogue

        sensor = get_sensor_catalogue().get("wave-imu")
        assert sensor is not None
        assert sensor.extra == {}
