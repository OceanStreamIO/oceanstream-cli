# Sea-Bird SBE 37-SMP-ODO MicroCAT

The Sea-Bird SBE 37-SMP-ODO MicroCAT is a high-accuracy CTD (Conductivity, Temperature, Depth) sensor with integrated dissolved oxygen measurement, providing comprehensive water property data.

## Overview

| Property | Value |
|----------|-------|
| **Sensor ID** | `sbe37-odo` |
| **Manufacturer** | Sea-Bird Scientific |
| **Model** | SBE 37-SMP-ODO |
| **Type** | CTD (Conductivity, Temperature, Depth) |
| **Typical Depth** | 0.6m |
| **Typical Mount** | Hull-mounted, forward of keel |
| **Sampling Rate** | 10-minute average |

## Description

The SBE 37-SMP-ODO MicroCAT combines a precision CTD with an optical dissolved oxygen sensor in a compact, low-power package. This instrument provides the foundational oceanographic measurements needed for water mass characterization and ocean health monitoring.

**Key Features:**
- High-accuracy temperature and conductivity measurements
- Integrated dissolved oxygen sensor (optical, no consumables)
- Calculated salinity from conductivity and temperature
- Low power consumption suitable for autonomous platforms
- Proven reliability in marine environments

## Measured Variables

The SBE 37-ODO measures 10 variables (5 parameters + standard deviations):

### Temperature
- **TEMP_SBE37_MEAN**: Mean water temperature (°C)
- **TEMP_SBE37_STDDEV**: Standard deviation of temperature

### Salinity
- **SAL_SBE37_MEAN**: Mean salinity (PSU - Practical Salinity Units)
- **SAL_SBE37_STDDEV**: Standard deviation of salinity

### Conductivity
- **COND_SBE37_MEAN**: Mean conductivity (S/m)
- **COND_SBE37_STDDEV**: Standard deviation of conductivity

### Dissolved Oxygen (Concentration)
- **O2_CONC_SBE37_MEAN**: Mean oxygen concentration (µmol/kg)
- **O2_CONC_SBE37_STDDEV**: Standard deviation of oxygen concentration

### Dissolved Oxygen (Saturation)
- **O2_SAT_SBE37_MEAN**: Mean oxygen saturation (%)
- **O2_SAT_SBE37_STDDEV**: Standard deviation of oxygen saturation

## Technical Specifications

### Temperature
- **Range**: -5 to +35°C
- **Accuracy**: ±0.002°C
- **Resolution**: 0.0001°C
- **Response Time**: 0.5 seconds

### Conductivity
- **Range**: 0 to 9 S/m
- **Accuracy**: ±0.0003 S/m
- **Resolution**: 0.00001 S/m

### Dissolved Oxygen
- **Range**: 120% of surface saturation (0-500 µmol/kg)
- **Accuracy**: ±2% of saturation or 2 µmol/kg
- **Response Time**: <8 seconds
- **Technology**: Optical (RINKO ARO-FT sensor)

### General
- **Sampling Rate**: 10-minute averaging on Saildrone platforms
- **Power Consumption**: <1W typical
- **Depth Rating**: 600m (standard), deeper options available
- **Operating Environment**: Marine, 0-35 PSU

## Deployment Configuration

### Saildrone Platforms

**Explorer (SD 1000-1999):**
- Standard sensor package
- Hull-mounted at 0.6m depth
- Forward of keel for optimal water flow
- Continuous sampling with 10-minute averages

**Surveyor (SD 2000+):**
- Standard sensor package
- Same mounting configuration as Explorer
- Enhanced data logging capabilities

### Mounting Position

The SBE 37-ODO is mounted on the hull approximately 0.6m below the waterline, forward of the keel. This position:
- Ensures consistent water flow across sensors
- Minimizes wake effects from the vehicle
- Provides representative near-surface measurements
- Protects sensor from fouling and damage

## Data Quality Considerations

### Calibration
- **Factory Calibration**: Valid for 1 year
- **Field Calibration**: Recommended every 6-12 months
- **Oxygen Calibration**: Check before and after deployments

### Known Issues
- **Biofouling**: Can affect conductivity and oxygen in extended deployments
- **Response Time**: Oxygen sensor slower than CTD in rapidly changing conditions
- **Salinity Spikes**: May occur in very shallow water or near freshwater inputs

### Quality Flags
OceanStream automatically applies quality checks:
- Range checks (temperature, salinity, oxygen within expected bounds)
- Spike detection for anomalous values
- Standard deviation thresholds for stability

## Example Data

### Typical Values

```python
# Tropical ocean surface
TEMP_SBE37_MEAN: 28.5  # °C
SAL_SBE37_MEAN: 35.2   # PSU
O2_CONC_SBE37_MEAN: 215.0  # µmol/kg
O2_SAT_SBE37_MEAN: 98.5    # %

# High-latitude surface
TEMP_SBE37_MEAN: 2.5   # °C
SAL_SBE37_MEAN: 33.8   # PSU
O2_CONC_SBE37_MEAN: 340.0  # µmol/kg
O2_SAT_SBE37_MEAN: 102.0   # %
```

### Detecting SBE 37-ODO in Data

```python
from oceanstream.sensors import get_sensor_catalogue

# Get sensor catalogue
catalogue = get_sensor_catalogue()

# Check if SBE37 variables are present
ctd_vars = {
    "TEMP_SBE37_MEAN",
    "SAL_SBE37_MEAN",
    "O2_CONC_SBE37_MEAN"
}

detected = catalogue.detect_sensors(ctd_vars)
for sensor in detected:
    if sensor.id == "sbe37-odo":
        print(f"Found {sensor.name}")
        print(f"Measures: {len(sensor.variables)} variables")
```

## STAC Metadata

The SBE 37-ODO sensor is included in STAC item metadata:

```json
{
  "instruments": [
    {
      "id": "sbe37-odo",
      "name": "Sea-Bird SBE 37-SMP-ODO MicroCAT",
      "type": "ctd",
      "manufacturer": "Sea-Bird Scientific",
      "model": "SBE 37-SMP-ODO",
      "description": "CTD (Conductivity, Temperature, Depth) with integrated dissolved oxygen sensor",
      "depth": "0.6m",
      "mount_position": "hull-mounted, forward of keel",
      "variables": [
        "TEMP_SBE37_MEAN",
        "TEMP_SBE37_STDDEV",
        "SAL_SBE37_MEAN",
        "SAL_SBE37_STDDEV",
        "COND_SBE37_MEAN",
        "COND_SBE37_STDDEV",
        "O2_CONC_SBE37_MEAN",
        "O2_CONC_SBE37_STDDEV",
        "O2_SAT_SBE37_MEAN",
        "O2_SAT_SBE37_STDDEV"
      ],
      "specifications": {
        "temperature_range": "-5 to +35°C",
        "temperature_accuracy": "±0.002°C",
        "conductivity_range": "0 to 9 S/m",
        "conductivity_accuracy": "±0.0003 S/m",
        "oxygen_range": "120% of surface saturation (0-500 µmol/kg)",
        "oxygen_accuracy": "±2% of saturation or 2 µmol/kg",
        "sampling_rate": "10-minute average"
      },
      "documentation": "https://www.seabird.com/sbe-37smp-odo-microcat-ctd/product?id=60762467708"
    }
  ]
}
```

## Scientific Applications

### Ocean Health Monitoring
- **Temperature**: Tracks ocean warming and thermal structure
- **Salinity**: Monitors freshwater inputs, mixing, and water masses
- **Oxygen**: Identifies hypoxic zones and biological activity

### Climate Studies
- Provides data for ocean heat content calculations
- Monitors thermohaline circulation components
- Tracks ocean acidification through oxygen-CO₂ relationships

### Ecosystem Research
- Dissolved oxygen indicates biological productivity
- Temperature and salinity define habitat boundaries
- Supports fisheries and marine mammal studies

## Related Sensors

- [WET Labs FLBBCD](./wetlabs-flbbcd.md) - Often deployed with CTD, measures chlorophyll
- [Hull-Mounted Thermistor](./thermistor.md) - Complementary temperature measurement at 0.5m

## Documentation Links

- [Manufacturer Product Page](https://www.seabird.com/sbe-37smp-odo-microcat-ctd/product?id=60762467708)
- [SBE 37-SMP-ODO Manual](https://www.seabird.com/asset-get.download.jsa?id=54627862387) (PDF)
- [Sea-Bird Dissolved Oxygen Sensors](https://www.seabird.com/dissolved-oxygen)

## Provider Integration

### Saildrone Provider

The Saildrone provider automatically maps SBE 37-ODO variables to canonical names:

```python
# Saildrone semantic mappings
"TEMP_SBE37_MEAN" → "water_temperature_ctd_mean_c"
"SAL_SBE37_MEAN" → "salinity_ctd_mean_psu"
"O2_CONC_SBE37_MEAN" → "oxygen_concentration_ctd_mean_umol_kg"
```

See [Saildrone Provider](../data-providers/saildrone.md) for complete mappings.

## Further Reading

- [Sensor Catalogue Overview](./overview.md) - Complete sensor inventory
- [Data Providers](../data-providers/overview.md) - How providers handle sensor data
- [STAC Metadata](../stac-metadata.md) - Instrument metadata format
