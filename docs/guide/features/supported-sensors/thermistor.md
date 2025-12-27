# Hull-Mounted Thermistor

The Sea-Bird SBE 56 hull-mounted thermistor provides high-accuracy temperature measurements at 0.5m depth, complementing the CTD temperature sensor.

## Overview

| Property | Value |
|----------|-------|
| **Sensor ID** | `thermistor-0.5m` |
| **Manufacturer** | Sea-Bird Scientific |
| **Model** | SBE 56 |
| **Type** | Thermistor |
| **Typical Depth** | 0.5m |
| **Typical Mount** | Hull-mounted |
| **Sampling Rate** | 10-minute average |

## Description

The SBE 56 is a high-accuracy, fast-response temperature sensor providing an independent measurement at a slightly shallower depth than the CTD (0.5m vs 0.6m). This enables detection of fine-scale thermal structure in the upper ocean.

**Key Features:**
- Exceptional accuracy (±0.002°C)
- Very high resolution (0.0001°C)
- Fast response time (0.6 seconds)
- Independent from CTD measurement
- Low drift characteristics

## Measured Variables

- **TEMP_DEPTH_HALFMETER_MEAN**: Mean water temperature at 0.5m depth (°C)
- **TEMP_DEPTH_HALFMETER_STDDEV**: Standard deviation of temperature

## Technical Specifications

### Temperature Measurement
- **Range**: -5 to +35°C
- **Accuracy**: ±0.002°C
- **Resolution**: 0.0001°C (better than 1 millidegree!)
- **Stability**: 0.0002°C per year
- **Response Time**: 0.6 seconds (63% response)

### General
- **Sampling Rate**: 10-minute averaging on Saildrone
- **Power Consumption**: <1 mW (very low power)
- **Depth Rating**: 600m
- **Pressure Effect**: <0.002°C per 1000m

## Deployment Configuration

### Saildrone Platforms

**Explorer (SD 1000-1999):**
- Standard sensor package
- Hull-mounted at 0.5m depth
- Forward of keel, near CTD

**Surveyor (SD 2000+):**
- Standard sensor package
- Same configuration as Explorer

### Mounting Position

The thermistor is mounted on the hull at 0.5m depth, approximately 10cm forward of the SBE 37 CTD (0.6m depth). This configuration:
- Provides vertical temperature gradient information
- Enables detection of diurnal warming layers
- Offers redundant temperature measurement
- Samples slightly shallower mixed layer

## Data Quality Considerations

### Calibration
- **Factory Calibration**: Valid for several years due to exceptional stability
- **Field Calibration**: Rarely needed; drift is minimal
- **Inter-comparison**: Can be compared with CTD temperature

### Known Issues
- **Biofouling**: Can affect readings in very long deployments
- **Solar Heating**: Direct sunlight on hull may cause small bias in calm conditions
- **Depth Variation**: Actual depth varies slightly with vehicle trim

### Quality Checks
- Range validation (-2 to +40°C expected)
- Comparison with CTD temperature (should be similar)
- Standard deviation check (low values indicate stable conditions)

## Example Data

### Typical Temperature Profiles

```python
# Tropical ocean (warm, stratified)
TEMP_DEPTH_HALFMETER_MEAN: 28.8  # °C (at 0.5m)
TEMP_SBE37_MEAN: 28.5             # °C (at 0.6m)
# Difference: 0.3°C (diurnal warm layer)

# High-latitude (well-mixed)
TEMP_DEPTH_HALFMETER_MEAN: 2.1   # °C (at 0.5m)
TEMP_SBE37_MEAN: 2.1              # °C (at 0.6m)
# Difference: 0.0°C (homogeneous)

# Upwelling region (sharp gradient)
TEMP_DEPTH_HALFMETER_MEAN: 18.5  # °C (at 0.5m)
TEMP_SBE37_MEAN: 17.8             # °C (at 0.6m)
# Difference: 0.7°C (strong stratification)
```

### Detecting Thermistor in Data

```python
from oceanstream.sensors import get_sensor_catalogue

catalogue = get_sensor_catalogue()

# Check for thermistor variable
therm_vars = {"TEMP_DEPTH_HALFMETER_MEAN"}

detected = catalogue.detect_sensors(therm_vars)
for sensor in detected:
    if sensor.id == "thermistor-0.5m":
        print(f"Found {sensor.name}")
        print(f"Accuracy: {sensor.specifications['accuracy']}")
```

## STAC Metadata

```json
{
  "instruments": [
    {
      "id": "thermistor-0.5m",
      "name": "Hull-Mounted Thermistor",
      "type": "thermistor",
      "manufacturer": "Sea-Bird Scientific",
      "model": "SBE 56",
      "description": "High-accuracy temperature sensor at 0.5m depth",
      "depth": "0.5m",
      "mount_position": "hull-mounted",
      "variables": [
        "TEMP_DEPTH_HALFMETER_MEAN",
        "TEMP_DEPTH_HALFMETER_STDDEV"
      ],
      "specifications": {
        "temperature_range": "-5 to +35°C",
        "accuracy": "±0.002°C",
        "resolution": "0.0001°C",
        "response_time": "0.6 s"
      },
      "documentation": "https://www.seabird.com/sbe-56-temperature-sensor/product?id=60762467729"
    }
  ]
}
```

## Scientific Applications

### Fine-Scale Thermal Structure
- Detects diurnal warm layers (daytime solar heating)
- Measures vertical temperature gradients in upper 1m
- Studies near-surface stratification

### SST Measurements
- Provides near-surface temperature for satellite validation
- More representative of "skin temperature" than deeper CTD
- Useful for air-sea heat flux calculations

### Redundancy & Quality Control
- Independent check on CTD temperature
- Validates data quality
- Backup if CTD fails

## Temperature Gradient Analysis

The 0.1m vertical separation between thermistor (0.5m) and CTD (0.6m) enables gradient calculation:

```python
import polars as pl

# Calculate vertical temperature gradient
df = pl.read_parquet("saildrone_data.parquet")
df = df.with_columns([
    ((pl.col("TEMP_DEPTH_HALFMETER_MEAN") - pl.col("TEMP_SBE37_MEAN")) / 0.1)
    .alias("temp_gradient_c_per_m")
])

# Positive gradient indicates warmer water at surface
# Typical values: -0.5 to +5.0 °C/m
```

## Related Sensors

- [SBE 37-SMP-ODO](./sbe37-odo.md) - Co-located CTD at 0.6m depth
- [Apogee SI-111](./radiation-sensors.md) - IR sea surface temperature (non-contact)
- [LI-COR LI-190R](./radiation-sensors.md) - PAR sensor (solar heating indicator)

## Documentation Links

- [Manufacturer Product Page](https://www.seabird.com/sbe-56-temperature-sensor/product?id=60762467729)
- [SBE 56 User Manual](https://www.seabird.com/asset-get.download.jsa?id=54627862404) (PDF)

## Provider Integration

### Saildrone Provider

```python
# Saildrone semantic mapping
"TEMP_DEPTH_HALFMETER_MEAN" → "water_temperature_0.5m_mean_c"
```

See [Saildrone Provider](../data-providers/saildrone.md) for complete mappings.

## Further Reading

- [Sensor Catalogue Overview](./overview.md)
- [Data Providers](../data-providers/overview.md)
- [STAC Metadata](../stac-metadata.md)
