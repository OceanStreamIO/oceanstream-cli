"""Global sensor catalogue system."""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional
from enum import Enum


class SensorType(str, Enum):
    """Sensor type classification."""
    CTD = "ctd"
    OXYGEN = "dissolved_oxygen"
    FLUOROMETER = "fluorometer"
    METEOROLOGICAL = "meteorological"
    RADIATION = "radiation"
    WAVE = "wave"
    NAVIGATION = "navigation"
    ACOUSTIC = "acoustic"
    CURRENT = "current"
    THERMISTOR = "thermistor"
    WINCH = "winch"
    OTHER = "other"


@dataclass
class Sensor:
    """Sensor/instrument definition.
    
    Attributes:
        id: Unique identifier (e.g., 'sbe37-odo')
        name: Full instrument name
        manufacturer: Manufacturer name
        model: Model number/name
        sensor_type: Type of sensor
        description: Brief description
        variables: Observable variables measured
        specifications: Technical specifications
        documentation_url: Link to manufacturer docs
        typical_depth: Typical deployment depth (if applicable)
        typical_mount: Typical mounting location
    """
    id: str
    name: str
    manufacturer: str
    model: str
    sensor_type: SensorType
    description: str
    variables: list[str] = field(default_factory=list)
    specifications: dict[str, str] = field(default_factory=dict)
    documentation_url: Optional[str] = None
    typical_depth: Optional[str] = None
    typical_mount: Optional[str] = None
    
    def matches_variables(self, available_vars: set[str]) -> bool:
        """Check if any of the sensor's variables are present in the dataset.
        
        Args:
            available_vars: Set of available variable names
            
        Returns:
            True if at least one variable matches
        """
        if not self.variables:
            return False
        return any(var in available_vars for var in self.variables)
    
    def to_stac_instrument(self, mount_position: Optional[str] = None) -> dict:
        """Convert to STAC instrument format.
        
        Args:
            mount_position: Override typical mount position
            
        Returns:
            Dictionary in STAC instrument format
        """
        instrument = {
            "id": self.id,
            "name": self.name,
            "type": self.sensor_type.value,
            "manufacturer": self.manufacturer,
        }
        
        if self.model:
            instrument["model"] = self.model
            
        if self.description:
            instrument["description"] = self.description
            
        if self.variables:
            instrument["variables"] = self.variables
            
        if mount_position or self.typical_mount:
            instrument["mount_position"] = mount_position or self.typical_mount
            
        if self.typical_depth:
            instrument["depth"] = self.typical_depth
            
        if self.documentation_url:
            instrument["documentation"] = self.documentation_url
            
        if self.specifications:
            instrument["specifications"] = self.specifications
            
        return instrument


class SensorCatalogue:
    """Global catalogue of sensors and instruments."""
    
    def __init__(self):
        self._sensors: dict[str, Sensor] = {}
    
    def register(self, sensor: Sensor) -> None:
        """Register a sensor in the catalogue.
        
        Args:
            sensor: Sensor to register
        """
        self._sensors[sensor.id] = sensor
    
    def get(self, sensor_id: str) -> Optional[Sensor]:
        """Get a sensor by ID.
        
        Args:
            sensor_id: Sensor identifier
            
        Returns:
            Sensor if found, None otherwise
        """
        return self._sensors.get(sensor_id)
    
    def list_all(self) -> list[Sensor]:
        """List all registered sensors.
        
        Returns:
            List of all sensors
        """
        return list(self._sensors.values())
    
    def find_by_type(self, sensor_type: SensorType) -> list[Sensor]:
        """Find sensors by type.
        
        Args:
            sensor_type: Type of sensor to find
            
        Returns:
            List of matching sensors
        """
        return [s for s in self._sensors.values() if s.sensor_type == sensor_type]
    
    def detect_sensors(self, available_variables: set[str]) -> list[Sensor]:
        """Detect which sensors are likely present based on available variables.
        
        Args:
            available_variables: Set of variable names in the dataset
            
        Returns:
            List of sensors that match the variables
        """
        detected = []
        for sensor in self._sensors.values():
            if sensor.matches_variables(available_variables):
                detected.append(sensor)
        return detected
    
    def to_stac_instruments(self, sensor_ids: list[str]) -> list[dict]:
        """Convert a list of sensor IDs to STAC instrument format.
        
        Args:
            sensor_ids: List of sensor identifiers
            
        Returns:
            List of STAC instrument dictionaries
        """
        instruments = []
        for sensor_id in sensor_ids:
            sensor = self.get(sensor_id)
            if sensor:
                instruments.append(sensor.to_stac_instrument())
        return instruments


# Global sensor catalogue instance
_CATALOGUE = SensorCatalogue()


def get_sensor_catalogue() -> SensorCatalogue:
    """Get the global sensor catalogue instance.
    
    Returns:
        Global SensorCatalogue instance
    """
    return _CATALOGUE
