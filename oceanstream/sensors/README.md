# Sensor Catalogue

The sensor catalogue provides a global registry of oceanographic instruments and sensors, with capabilities for automatic detection and metadata generation.

## Overview

The sensor catalogue enables:
- **Global registry** of sensors across all platforms and providers
- **Automatic sensor detection** based on available data variables
- **STAC metadata generation** with instrument information
- **Platform-sensor mappings** for specific configurations
- **Extensible architecture** for adding new sensors and platforms
- **JSON-based sensor definitions** with separate documentation

## Architecture

```
oceanstream/sensors/
├── __init__.py              # Package exports
├── catalogue.py             # Core catalogue system
├── loader.py                # JSON loader for sensor definitions
├── saildrone.py             # Saildrone platform configurations
├── definitions/             # Sensor definitions directory
│   ├── README.md            # Definitions directory guide
│   ├── sbe37-odo/
│   │   ├── sensor.json      # Sensor configuration
│   │   └── README.md        # Sensor documentation
│   ├── wetlabs-flbbcd/
│   │   ├── sensor.json
│   │   └── README.md
│   └── ...                  # Other sensor directories
└── README.md                # This file
```

## Core Components

### Sensor Class

Defines a sensor/instrument with all metadata:

```python
from oceanstream.sensors import Sensor, SensorType

sensor = Sensor(
    id="sbe37-odo",
    name="Sea-Bird SBE 37-SMP-ODO MicroCAT",
    manufacturer="Sea-Bird Scientific",
    model="SBE 37-SMP-ODO",
    sensor_type=SensorType.CTD,
    description="CTD with dissolved oxygen sensor",
    variables=["TEMP_SBE37_MEAN", "SAL_SBE37_MEAN", ...],
    specifications={
        "temperature_accuracy": "±0.002°C",
        "oxygen_accuracy": "±2% of saturation"
    },
    documentation_url="https://www.seabird.com/...",
    typical_depth="0.6m",
    typical_mount="hull-mounted"
)
```

### JSON-Based Sensor Definitions

Sensors are now defined in JSON files located in `definitions/<sensor-id>/sensor.json`. This makes it easier to:
- View and edit sensor configurations
- Add documentation alongside each sensor
- Version control sensor definitions
- Share sensor definitions across projects

Each sensor directory contains:
- `sensor.json` - Complete sensor configuration
- `README.md` - Human-readable documentation with links to manufacturer pages

See `definitions/README.md` for details on adding new sensors.

### SensorCatalogue

Global registry for managing sensors:

```python
from oceanstream.sensors import get_sensor_catalogue

catalogue = get_sensor_catalogue()

# Register a sensor
catalogue.register(sensor)

# Lookup by ID
sensor = catalogue.get("sbe37-odo")

# Find by type
ctd_sensors = catalogue.find_by_type(SensorType.CTD)

# Auto-detect from variables
variables = {"TEMP_SBE37_MEAN", "SAL_SBE37_MEAN"}
detected = catalogue.detect_sensors(variables)
```

## Registered Sensors

### Saildrone Sensors

Currently registered Saildrone instruments:

| ID | Name | Manufacturer | Type | Variables |
|----|------|--------------|------|-----------|
| `sbe37-odo` | Sea-Bird SBE 37-SMP-ODO MicroCAT | Sea-Bird Scientific | CTD | Temperature, Salinity, Conductivity, DO |
| `wetlabs-flbbcd` | WET Labs ECO Puck FLBBCD | Sea-Bird Scientific | Fluorometer | Chlorophyll-a |
| `airmar-150wx` | Airmar 150WX WeatherStation | Airmar Technology | Meteorological | Wind, Air temp, Humidity, Pressure |
| `licor-li190r` | LI-COR LI-190R PAR Sensor | LI-COR Biosciences | Radiation | PAR |
| `kipp-zonen-cmp` | Kipp & Zonen CMP Pyranometer | Kipp & Zonen | Radiation | Shortwave irradiance |
| `apogee-si111` | Apogee SI-111 IR Radiometer | Apogee Instruments | Radiation | IR sea surface temp |
| `thermistor-0.5m` | Hull-Mounted Thermistor | Sea-Bird Scientific | Thermistor | Temperature at 0.5m |
| `wave-imu` | IMU-Derived Wave Sensor | Saildrone | Wave | Wave height & period |
| `imu-navigation` | IMU & GPS Navigation | Multiple | Navigation | Position, Heading, Attitude |

## Platform-Sensor Mappings

### Saildrone Explorer (SD 1000-1999)

Standard sensor suite:
- CTD with DO (SBE 37-SMP-ODO)
- Fluorometer (WET Labs FLBBCD)
- Weather station (Airmar 150WX)
- PAR sensor (LI-COR LI-190R)
- Pyranometer (Kipp & Zonen CMP)
- IR radiometer (Apogee SI-111)
- Hull thermistor (0.5m)
- Wave sensor (IMU-derived)
- Navigation (GPS + IMU)

### Saildrone Surveyor (SD 2000+)

Same as Explorer, plus optional:
- Multibeam sonar
- Acoustic Doppler Current Profiler (ADCP)
- Additional deep sensors

## Usage Examples

### Detect Sensors from Dataset

```python
from oceanstream.sensors import get_sensor_catalogue
import pandas as pd

# Load data
df = pd.read_csv("saildrone_data.csv")
available_vars = set(df.columns)

# Detect sensors
catalogue = get_sensor_catalogue()
detected_sensors = catalogue.detect_sensors(available_vars)

print(f"Detected {len(detected_sensors)} sensors:")
for sensor in detected_sensors:
    print(f"  - {sensor.name} ({sensor.manufacturer})")
```

### Platform-Based Sensor Lookup

```python
from oceanstream.sensors.saildrone import (
    detect_saildrone_platform,
    get_platform_sensors
)

# Detect platform from trajectory ID
trajectory_id = 1030  # from filename or data
platform_type = detect_saildrone_platform(trajectory_id)
print(f"Platform: Saildrone {platform_type}")

# Get standard sensors for this platform
sensor_ids = get_platform_sensors(platform_type)
print(f"Standard sensors: {len(sensor_ids)}")
```

### Generate STAC Instrument Metadata

```python
from oceanstream.sensors import get_sensor_catalogue

catalogue = get_sensor_catalogue()
sensor_ids = ["sbe37-odo", "wetlabs-flbbcd", "airmar-150wx"]

# Convert to STAC format
stac_instruments = catalogue.to_stac_instruments(sensor_ids)

# Add to STAC collection
collection = {
    "type": "Collection",
    "instruments": stac_instruments,
    # ... other STAC fields
}
```

## Adding New Sensors

### 1. Define the Sensor

```python
from oceanstream.sensors import Sensor, SensorType, get_sensor_catalogue

new_sensor = Sensor(
    id="nortek-aquadopp",
    name="Nortek Aquadopp ADCP",
    manufacturer="Nortek",
    model="Aquadopp",
    sensor_type=SensorType.CURRENT,
    description="Acoustic Doppler Current Profiler",
    variables=["current_u", "current_v", "current_speed"],
    specifications={
        "frequency": "2 MHz",
        "range": "0.3-30m",
        "accuracy": "±0.5 cm/s"
    },
    documentation_url="https://www.nortekgroup.com/products/aquadopp",
    typical_depth="variable",
    typical_mount="hull-mounted"
)
```

### 2. Register in Catalogue

```python
# In your provider's sensor module
catalogue = get_sensor_catalogue()
catalogue.register(new_sensor)
```

### 3. Add to Platform Configuration

```python
# In your platform mapping
MY_PLATFORM_SENSORS = {
    "standard_sensors": [
        "sbe37-odo",
        "nortek-aquadopp",  # New sensor
        # ... other sensors
    ]
}
```

## Extending for New Providers

To add sensors for a new provider (e.g., OOI, ARGO):

1. Create `oceanstream/sensors/ooi.py`
2. Define sensors using the `Sensor` class
3. Register them in the global catalogue
4. Create platform-sensor mappings
5. Export in `oceanstream/sensors/__init__.py`

Example structure:

```python
# oceanstream/sensors/ooi.py
from .catalogue import Sensor, SensorType, get_sensor_catalogue

OOI_SENSORS = {
    "ctd-sbe16": Sensor(
        id="ctd-sbe16",
        name="Sea-Bird SBE 16plus V2 SEACAT",
        # ... sensor definition
    ),
    # ... more OOI sensors
}

# Register all
catalogue = get_sensor_catalogue()
for sensor in OOI_SENSORS.values():
    catalogue.register(sensor)

# Platform mappings
OOI_MOORING_SENSORS = {
    "Coastal_Surface": ["ctd-sbe16", ...],
    "Global_Profiler": [...],
}
```

## Integration with Processing

The sensor catalogue integrates with the processing pipeline:

1. **Detection**: Automatically detect sensors from CSV columns
2. **Metadata**: Include sensor info in GeoParquet metadata
3. **STAC**: Generate comprehensive STAC instrument records
4. **Validation**: Verify expected sensors are present
5. **Documentation**: Auto-generate data provenance

See `oceanstream/geotrack/processor.py` for integration examples.
