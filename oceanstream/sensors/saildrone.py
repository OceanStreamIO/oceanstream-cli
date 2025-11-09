"""Saildrone sensor definitions and platform configurations."""

from .catalogue import get_sensor_catalogue
from . import loader  # Load all sensor definitions from JSON files


def get_all_sensors() -> dict:
    """Get all registered sensors as a dictionary.
    
    Returns:
        Dictionary mapping sensor IDs to Sensor objects
    """
    catalogue = get_sensor_catalogue()
    return {sensor.id: sensor for sensor in catalogue.list_all()}


# For backward compatibility
SENSORS = get_all_sensors()



# Saildrone platform-sensor mappings
SAILDRONE_PLATFORM_SENSORS = {
    "Explorer": {
        "id_range": (1000, 1999),
        "standard_sensors": [
            "sbe37-odo",
            "wetlabs-flbbcd",
            "airmar-150wx",
            "licor-li190r",
            "kipp-zonen-cmp",
            "apogee-si111",
            "thermistor-0.5m",
            "wave-imu",
            "imu-navigation"
        ],
        "optional_sensors": []
    },
    "Surveyor": {
        "id_range": (2000, 9999),
        "standard_sensors": [
            "sbe37-odo",
            "wetlabs-flbbcd",
            "airmar-150wx",
            "licor-li190r",
            "kipp-zonen-cmp",
            "apogee-si111",
            "thermistor-0.5m",
            "wave-imu",
            "imu-navigation"
        ],
        "optional_sensors": [
            # Surveyor can have additional acoustic sensors
        ]
    }
}


def detect_saildrone_platform(trajectory_id: int) -> str:
    """Detect Saildrone platform type from trajectory ID.
    
    Args:
        trajectory_id: Saildrone trajectory number (e.g., 1030)
        
    Returns:
        Platform type name ('Explorer' or 'Surveyor')
    """
    if 1000 <= trajectory_id < 2000:
        return "Explorer"
    elif trajectory_id >= 2000:
        return "Surveyor"
    else:
        return "Unknown"


def get_platform_sensors(platform_type: str) -> list[str]:
    """Get standard sensor IDs for a Saildrone platform type.
    
    Args:
        platform_type: Platform type ('Explorer' or 'Surveyor')
        
    Returns:
        List of sensor IDs
    """
    config = SAILDRONE_PLATFORM_SENSORS.get(platform_type, {})
    return config.get("standard_sensors", [])


# Export for easy access
SAILDRONE_SENSORS = SENSORS
