"""Sensor and instrument catalogue for oceanographic platforms."""

from .catalogue import (
    Sensor,
    SensorCatalogue,
    get_sensor_catalogue,
)
from .saildrone import SAILDRONE_SENSORS

__all__ = [
    "Sensor",
    "SensorCatalogue",
    "get_sensor_catalogue",
    "SAILDRONE_SENSORS",
]
