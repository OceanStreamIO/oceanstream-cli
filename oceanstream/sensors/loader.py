"""Load sensor definitions from JSON files."""

import json
from pathlib import Path
from typing import Optional
from .catalogue import Sensor, SensorType, get_sensor_catalogue


def load_sensor_from_json(json_path: Path) -> Optional[Sensor]:
    """Load a sensor definition from a JSON file.
    
    Args:
        json_path: Path to the sensor JSON file
        
    Returns:
        Sensor instance or None if loading fails
    """
    try:
        with open(json_path, 'r') as f:
            data = json.load(f)
        
        # Convert sensor_type string to enum
        sensor_type = SensorType(data['sensor_type'])
        
        return Sensor(
            id=data['id'],
            name=data['name'],
            manufacturer=data['manufacturer'],
            model=data['model'],
            sensor_type=sensor_type,
            description=data['description'],
            variables=data.get('variables', []),
            specifications=data.get('specifications', {}),
            documentation_url=data.get('documentation_url'),
            typical_depth=data.get('typical_depth'),
            typical_mount=data.get('typical_mount')
        )
    except Exception as e:
        print(f"Error loading sensor from {json_path}: {e}")
        return None


def load_all_sensors(definitions_dir: Optional[Path] = None) -> None:
    """Load all sensor definitions from the definitions directory.
    
    Args:
        definitions_dir: Path to the definitions directory. 
                        If None, uses the default location.
    """
    if definitions_dir is None:
        # Get the directory where this module is located
        module_dir = Path(__file__).parent
        definitions_dir = module_dir / 'definitions'
    
    if not definitions_dir.exists():
        print(f"Definitions directory not found: {definitions_dir}")
        return
    
    catalogue = get_sensor_catalogue()
    
    # Find all sensor.json files in subdirectories
    for sensor_dir in definitions_dir.iterdir():
        if sensor_dir.is_dir():
            json_path = sensor_dir / 'sensor.json'
            if json_path.exists():
                sensor = load_sensor_from_json(json_path)
                if sensor:
                    catalogue.register(sensor)


# Load all sensors when this module is imported
load_all_sensors()
