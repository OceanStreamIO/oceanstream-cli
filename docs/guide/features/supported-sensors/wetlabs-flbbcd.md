# WET Labs ECO Puck FLBBCD

The WET Labs ECO Puck FLBBCD is a three-parameter optical fluorometer measuring chlorophyll-a fluorescence, CDOM (Colored Dissolved Organic Matter), and optical backscatter for ocean color and biological productivity studies.

## Overview

| Property | Value |
|----------|-------|
| **Sensor ID** | `wetlabs-flbbcd` |
| **Manufacturer** | Sea-Bird Scientific (WET Labs) |
| **Model** | ECO Puck FLBBCD |
| **Type** | Fluorometer |
| **Typical Depth** | 0.6m |
| **Typical Mount** | Integrated with CTD |
| **Sampling Rate** | 10-minute average |

## Description

The ECO Puck FLBBCD combines fluorescence and optical backscatter measurements in a compact, low-power sensor. It provides critical information about phytoplankton biomass, dissolved organic matter, and water turbidity.

**Key Features:**
- Chlorophyll-a fluorescence for phytoplankton biomass
- CDOM fluorescence for dissolved organic matter
- Optical backscatter at 650nm and 700nm
- Low power consumption (<1W)
- Integrated mounting with SBE 37 CTD

## Measured Variables

### Chlorophyll Fluorescence
- **CHLOR_WETLABS_MEAN**: Mean chlorophyll-a concentration (µg/L)
- **CHLOR_WETLABS_STDDEV**: Standard deviation of chlorophyll

*Note: While the sensor measures additional parameters (CDOM, backscatter), OceanStream currently tracks chlorophyll as the primary variable in Saildrone datasets.*

## Technical Specifications

### Chlorophyll Measurement
- **Range**: 0.03 to 75 µg/L
- **Resolution**: 0.01 µg/L
- **Sensitivity**: 0.03 µg/L
- **Wavelengths**: 470nm excitation, 695nm emission

### General
- **Sampling Rate**: 10-minute averaging
- **Power Consumption**: <1W
- **Depth Rating**: 600m
- **Temperature Range**: -2 to +35°C

## Deployment Configuration

### Saildrone Platforms

**Explorer (SD 1000-1999):**
- Standard sensor package
- Integrated with SBE 37 CTD at 0.6m depth
- Continuous sampling with 10-minute averages

**Surveyor (SD 2000+):**
- Standard sensor package
- Same configuration as Explorer

### Mounting Position

The FLBBCD is typically integrated with the SBE 37 CTD at 0.6m depth, ensuring:
- Co-located measurements with temperature and salinity
- Optimal water flow for representative sampling
- Protection from biofouling (forward hull position)

## Data Quality Considerations

### Calibration
- **Factory Calibration**: Valid for 1 year
- **Dark Counts**: Check in clean water before deployment
- **Scale Factor**: May require adjustment based on regional conditions

### Known Issues
- **Quenching**: Daytime fluorescence may be suppressed by high light levels
- **Non-Photochemical Quenching**: Reduces fluorescence in high light conditions
- **Biofouling**: Can affect readings in extended deployments
- **Regional Variability**: Fluorescence-to-chlorophyll ratio varies by phytoplankton community

### Best Practices
- Compare nighttime values for more accurate biomass estimates
- Use in conjunction with other bio-optical sensors (PAR, irradiance)
- Apply regional calibration factors when available

## Example Data

### Typical Values

```python
# Oligotrophic (low productivity) waters
CHLOR_WETLABS_MEAN: 0.1  # µg/L

# Mesotrophic waters
CHLOR_WETLABS_MEAN: 1.5  # µg/L

# Eutrophic (high productivity) waters
CHLOR_WETLABS_MEAN: 5.0  # µg/L

# Bloom conditions
CHLOR_WETLABS_MEAN: 15.0  # µg/L
```

### Detecting FLBBCD in Data

```python
from oceanstream.sensors import get_sensor_catalogue

catalogue = get_sensor_catalogue()

# Check for fluorometer variables
fluor_vars = {"CHLOR_WETLABS_MEAN"}

detected = catalogue.detect_sensors(fluor_vars)
for sensor in detected:
    if sensor.id == "wetlabs-flbbcd":
        print(f"Found {sensor.name}")
        print(f"Type: {sensor.sensor_type.value}")
```

## STAC Metadata

```json
{
  "instruments": [
    {
      "id": "wetlabs-flbbcd",
      "name": "WET Labs ECO Puck FLBBCD",
      "type": "fluorometer",
      "manufacturer": "Sea-Bird Scientific (WET Labs)",
      "model": "ECO Puck FLBBCD",
      "description": "Three-parameter fluorometer for chlorophyll-a, CDOM, and backscatter",
      "depth": "0.6m",
      "mount_position": "integrated with CTD",
      "variables": [
        "CHLOR_WETLABS_MEAN",
        "CHLOR_WETLABS_STDDEV"
      ],
      "specifications": {
        "chlorophyll_range": "0.03 to 75 µg/L",
        "chlorophyll_resolution": "0.01 µg/L",
        "sampling_rate": "10-minute average"
      },
      "documentation": "https://www.seabird.com/eco-puck-series/product?id=60762467726"
    }
  ]
}
```

## Scientific Applications

### Primary Productivity
- Estimates phytoplankton biomass distribution
- Tracks seasonal bloom dynamics
- Identifies upwelling and frontal zones

### Ecosystem Monitoring
- Assesses habitat quality for marine organisms
- Monitors harmful algal blooms (HABs)
- Tracks long-term productivity changes

### Ocean Color Validation
- Provides in-situ data for satellite validation
- Complements remote sensing observations
- Improves bio-optical algorithms

## Related Sensors

- [SBE 37-SMP-ODO](./sbe37-odo.md) - Co-deployed CTD sensor
- [LI-COR LI-190R PAR](./radiation-sensors.md#licor-li-190r-par-sensor) - Light availability for photosynthesis
- [Kipp & Zonen CMP](./radiation-sensors.md#kipp--zonen-cmp-pyranometer) - Incident solar radiation

## Documentation Links

- [Manufacturer Product Page](https://www.seabird.com/eco-puck-series/product?id=60762467726)
- [ECO Sensors User Guide](https://www.seabird.com/asset-get.download.jsa?id=54627862386)

## Provider Integration

### Saildrone Provider

```python
# Saildrone semantic mapping
"CHLOR_WETLABS_MEAN" → "chlorophyll_fluorescence_mean_ug_l"
```

See [Saildrone Provider](../data-providers/saildrone.md) for complete mappings.

## Further Reading

- [Sensor Catalogue Overview](./overview.md)
- [Data Providers](../data-providers/overview.md)
- [STAC Metadata](../stac-metadata.md)
