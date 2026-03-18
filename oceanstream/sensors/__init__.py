"""Sensor and instrument catalogue for oceanographic platforms."""

from .catalogue import (
    Sensor,
    SensorCatalogue,
    get_sensor_catalogue,
)
from .saildrone import SAILDRONE_SENSORS, get_saildrone_sensors

__all__ = [
    "Sensor",
    "SensorCatalogue",
    "get_sensor_catalogue",
    "get_saildrone_sensors",
    "SAILDRONE_SENSORS",
]
