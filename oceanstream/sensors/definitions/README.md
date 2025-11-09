# Sensor Definitions

This directory contains individual sensor definitions used by the OceanStream sensor catalogue system. Each sensor has its own subdirectory with configuration and documentation files.

## Directory Structure

```
definitions/
├── sbe37-odo/
│   ├── sensor.json     # Sensor configuration
│   └── README.md       # Sensor documentation
├── wetlabs-flbbcd/
│   ├── sensor.json
│   └── README.md
├── airmar-150wx/
│   ├── sensor.json
│   └── README.md
└── ...
```

## File Formats

### sensor.json

Each `sensor.json` file contains the complete sensor definition in JSON format:

```json
{
  "id": "sensor-id",
  "name": "Full Sensor Name",
  "manufacturer": "Manufacturer Name",
  "model": "Model Number",
  "sensor_type": "sensor_type_enum",
  "description": "Brief description",
  "variables": ["VAR1", "VAR2"],
  "specifications": {
    "spec_key": "spec_value"
  },
  "documentation_url": "https://...",
  "typical_depth": "0.6m",
  "typical_mount": "hull-mounted"
}
```

### README.md

Each `README.md` file provides human-readable documentation about the sensor, including:
- Overview and key features
- Measured variables
- Technical specifications
- Links to manufacturer documentation
- Deployment notes

## Sensor Types

The following sensor types are supported:
- `ctd` - Conductivity, Temperature, Depth
- `dissolved_oxygen` - Dissolved oxygen sensors
- `fluorometer` - Fluorescence and optical sensors
- `meteorological` - Weather and atmospheric sensors
- `radiation` - Light and radiation sensors
- `wave` - Wave measurement systems
- `navigation` - GPS and IMU systems
- `acoustic` - Acoustic sensors
- `current` - Current meters
- `thermistor` - Temperature sensors
- `other` - Other sensor types

## Adding New Sensors

To add a new sensor:

1. Create a new subdirectory with the sensor ID as the name
2. Create `sensor.json` with the sensor definition
3. Create `README.md` with sensor documentation
4. The sensor will be automatically loaded when the module is imported

## Current Sensors

| Sensor ID | Name | Type | Manufacturer |
|-----------|------|------|--------------|
| sbe37-odo | Sea-Bird SBE 37-SMP-ODO MicroCAT | CTD | Sea-Bird Scientific |
| wetlabs-flbbcd | WET Labs ECO Puck FLBBCD | Fluorometer | Sea-Bird Scientific |
| airmar-150wx | Airmar 150WX WeatherStation | Meteorological | Airmar Technology |
| licor-li190r | LI-COR LI-190R PAR Sensor | Radiation | LI-COR Biosciences |
| kipp-zonen-cmp | Kipp & Zonen CMP Pyranometer | Radiation | Kipp & Zonen |
| apogee-si111 | Apogee SI-111 Infrared Radiometer | Radiation | Apogee Instruments |
| thermistor-0.5m | Hull-Mounted Thermistor | Thermistor | Sea-Bird Scientific |
| wave-imu | IMU-Derived Wave Sensor | Wave | Saildrone |
| imu-navigation | IMU & GPS Navigation | Navigation | Multiple |
