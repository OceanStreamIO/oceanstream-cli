# Supported Sensors

OceanStream includes a comprehensive sensor catalogue that defines oceanographic sensors and instruments used across multiple platforms. The catalogue enables automatic sensor detection, metadata generation, and STAC-compliant instrument documentation.

## What is the Sensor Catalogue?

The sensor catalogue is a global registry of oceanographic sensors with:

- **Sensor Definitions**: Complete specifications, variables, and metadata
- **Automatic Detection**: Identifies sensors based on available data variables
- **STAC Integration**: Generates standardized instrument metadata
- **Platform Configuration**: Maps sensors to specific platforms (e.g., Saildrone Explorer, Surveyor)

## Sensor Categories

OceanStream supports the following sensor types:

| Category | Description | Example Sensors |
|----------|-------------|-----------------|
| **CTD** | Conductivity, Temperature, Depth | Sea-Bird SBE 37-SMP-ODO |
| **Fluorometer** | Chlorophyll and optical properties | WET Labs ECO Puck FLBBCD |
| **Meteorological** | Weather and atmospheric conditions | Airmar 150WX WeatherStation |
| **Radiation** | Light and solar radiation | LI-COR LI-190R PAR, Kipp & Zonen CMP |
| **Navigation** | GPS and IMU positioning | IMU & GPS Navigation System |
| **Wave** | Wave height and period | IMU-Derived Wave Sensor |
| **Thermistor** | High-precision temperature | Hull-Mounted Thermistor |

## Current Sensor Inventory

OceanStream currently supports **12 sensors** across 7 categories:

### CTD & Oceanographic
- [Sea-Bird SBE 37-SMP-ODO MicroCAT](./sbe37-odo.md) - CTD with integrated oxygen
- [WET Labs ECO Puck FLBBCD](./wetlabs-flbbcd.md) - Chlorophyll fluorometer
- [Hull-Mounted Thermistor](./thermistor.md) - High-accuracy temperature

### Meteorological
- [Airmar 150WX WeatherStation](./airmar-150wx.md) - Integrated weather station

### Radiation & Optical
- [LI-COR LI-190R PAR Sensor](./radiation-sensors.md#licor-li-190r-par-sensor) - Photosynthetically Active Radiation
- [Kipp & Zonen CMP Pyranometer](./radiation-sensors.md#kipp--zonen-cmp-pyranometer) - Shortwave irradiance
<!-- TODO: Add Apogee SI-111 Infrared Radiometer page -->

### Navigation & Motion
<!-- TODO: Add IMU & GPS Navigation page -->
<!-- TODO: Add IMU-Derived Wave Sensor page -->

## How Sensor Detection Works

### Automatic Detection

OceanStream automatically detects sensors based on available variables in your dataset:

```python
from oceanstream.sensors import get_sensor_catalogue

# Get the global catalogue
catalogue = get_sensor_catalogue()

# Detect sensors from CSV columns
available_vars = {"TEMP_SBE37_MEAN", "SAL_SBE37_MEAN", "CHLOR_WETLABS_MEAN"}
detected_sensors = catalogue.detect_sensors(available_vars)

for sensor in detected_sensors:
    print(f"{sensor.name} ({sensor.sensor_type.value})")
# Output:
# Sea-Bird SBE 37-SMP-ODO MicroCAT (ctd)
# WET Labs ECO Puck FLBBCD (fluorometer)
```

### Platform-Based Configuration

For Saildrone platforms, sensors are automatically assigned based on platform type:

```python
from oceanstream.sensors.saildrone import (
    detect_saildrone_platform,
    get_platform_sensors
)

# Detect platform type from trajectory ID
platform = detect_saildrone_platform(1030)  # Returns "Explorer"

# Get standard sensors for this platform
sensors = get_platform_sensors(platform)
print(sensors)
# Output: ['sbe37-odo', 'wetlabs-flbbcd', 'airmar-150wx', ...]
```

**Platform Ranges:**
- **Explorer** (SD 1000-1999): Standard 9-sensor configuration
- **Surveyor** (SD 2000+): Standard 9-sensor configuration + optional acoustic sensors

## STAC Integration

Sensor metadata is automatically included in STAC items:

```python
# Sensor metadata in STAC item properties
{
  "instruments": [
    {
      "id": "sbe37-odo",
      "name": "Sea-Bird SBE 37-SMP-ODO MicroCAT",
      "type": "ctd",
      "manufacturer": "Sea-Bird Scientific",
      "model": "SBE 37-SMP-ODO",
      "depth": "0.6m",
      "mount_position": "hull-mounted, forward of keel",
      "variables": [
        "TEMP_SBE37_MEAN",
        "SAL_SBE37_MEAN",
        "O2_CONC_SBE37_MEAN"
      ],
      "specifications": {
        "temperature_accuracy": "±0.002°C",
        "conductivity_accuracy": "±0.0003 S/m"
      }
    }
  ]
}
```

## Sensor Definition Format

Each sensor is defined with comprehensive metadata:

```python
from oceanstream.sensors.catalogue import Sensor, SensorType

sensor = Sensor(
    id="sbe37-odo",
    name="Sea-Bird SBE 37-SMP-ODO MicroCAT",
    manufacturer="Sea-Bird Scientific",
    model="SBE 37-SMP-ODO",
    sensor_type=SensorType.CTD,
    description="CTD with integrated dissolved oxygen sensor",
    variables=["TEMP_SBE37_MEAN", "SAL_SBE37_MEAN", "O2_CONC_SBE37_MEAN"],
    specifications={
        "temperature_accuracy": "±0.002°C",
        "conductivity_accuracy": "±0.0003 S/m"
    },
    documentation_url="https://www.seabird.com/...",
    typical_depth="0.6m",
    typical_mount="hull-mounted"
)
```

## Using the Sensor Catalogue

### Query Sensors by Type

```python
from oceanstream.sensors import get_sensor_catalogue
from oceanstream.sensors.catalogue import SensorType

catalogue = get_sensor_catalogue()

# Find all CTD sensors
ctd_sensors = catalogue.find_by_type(SensorType.CTD)
for sensor in ctd_sensors:
    print(f"{sensor.name} - {sensor.model}")
```

### Get Sensor Details

```python
# Get specific sensor
sbe37 = catalogue.get("sbe37-odo")

print(f"Name: {sbe37.name}")
print(f"Variables: {', '.join(sbe37.variables)}")
print(f"Depth: {sbe37.typical_depth}")
print(f"Documentation: {sbe37.documentation_url}")
```

### Generate STAC Instruments

```python
# Convert sensor to STAC format
stac_instrument = sbe37.to_stac_instrument()

# Convert multiple sensors
sensor_ids = ["sbe37-odo", "wetlabs-flbbcd", "airmar-150wx"]
instruments = catalogue.to_stac_instruments(sensor_ids)
```

## Adding New Sensors

To add a new sensor to the catalogue:

1. **Create Sensor Directory**: `oceanstream/sensors/definitions/your-sensor-id/`

2. **Define Sensor JSON**: Create `sensor.json`:
   ```json
   {
     "id": "your-sensor-id",
     "name": "Full Sensor Name",
     "manufacturer": "Manufacturer Name",
     "model": "Model Number",
     "sensor_type": "ctd",
     "description": "Brief description",
     "variables": ["VAR1", "VAR2"],
     "specifications": {
       "accuracy": "±0.1°C"
     },
     "documentation_url": "https://...",
     "typical_depth": "1.0m",
     "typical_mount": "hull-mounted"
   }
   ```

3. **Document Sensor**: Create `README.md` with detailed documentation

4. **Test**: The sensor will be automatically loaded when the module is imported

## Variable Naming Conventions

Sensor variables follow Saildrone naming patterns:

- **Measurement + Sensor + Statistic**: `TEMP_SBE37_MEAN`, `SAL_SBE37_STDDEV`
- **Mean Values**: Primary measurement (10-minute average)
- **Standard Deviation**: Variability indicator
- **Peak Values**: Maximum values (for orientation/motion)

**Common Patterns:**
- `TEMP_*`: Temperature measurements
- `SAL_*`: Salinity
- `O2_*`: Dissolved oxygen
- `CHLOR_*`: Chlorophyll
- `WIND_*`: Wind measurements
- `PAR_*`: Photosynthetically Active Radiation
- `SW_IRRAD_*`: Shortwave irradiance

## Sensor Documentation Pages

For detailed information about each sensor, see:

- [Sea-Bird SBE 37-SMP-ODO MicroCAT](./sbe37-odo.md)
- [WET Labs ECO Puck FLBBCD](./wetlabs-flbbcd.md)
- [Airmar 150WX WeatherStation](./airmar-150wx.md)
- [LI-COR LI-190R PAR Sensor](./radiation-sensors.md#licor-li-190r-par-sensor)
- [Kipp & Zonen CMP Pyranometer](./radiation-sensors.md#kipp--zonen-cmp-pyranometer)
<!-- TODO: Add individual sensor pages: Apogee SI-111, IMU Navigation, Wave IMU -->
- [Hull-Mounted Thermistor](./thermistor.md)

## Best Practices

1. **Use Automatic Detection**: Let OceanStream detect sensors from data variables
2. **Verify Platform Configuration**: Check platform-specific sensor assignments
3. **Include STAC Metadata**: Sensor information enriches STAC items
4. **Document Custom Sensors**: Add new sensors to the catalogue for reusability
5. **Follow Naming Conventions**: Use consistent variable naming patterns

## Related Documentation

- [Data Providers](../data-providers/overview.md) - How providers map variables to sensors
- [STAC Metadata](../stac-metadata.md) - STAC instrument format
- [Saildrone Provider](../data-providers/saildrone.md) - Saildrone-specific sensor mappings
