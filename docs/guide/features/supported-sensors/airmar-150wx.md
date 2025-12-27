# Airmar 150WX WeatherStation

The Airmar 150WX WeatherStation is an integrated meteorological sensor providing comprehensive atmospheric measurements including wind, air temperature, humidity, and barometric pressure.

## Overview

| Property | Value |
|----------|-------|
| **Sensor ID** | `airmar-150wx` |
| **Manufacturer** | Airmar Technology Corporation |
| **Model** | 150WX |
| **Type** | Meteorological |
| **Typical Height** | ~5m above sea level |
| **Typical Mount** | Wing-mounted |
| **Sampling Rate** | 10-minute average |

## Description

The Airmar 150WX is a self-contained weather station designed for marine applications. It combines multiple sensors to provide accurate atmospheric measurements essential for air-sea interaction studies, weather monitoring, and marine operations.

**Key Features:**
- Ultrasonic wind sensor (no moving parts)
- High-accuracy temperature and humidity sensors
- Precision barometric pressure measurement
- Marine-grade construction
- Low power consumption

## Measured Variables

The Airmar 150WX measures 18 variables (9 parameters + standard deviations):

### Wind Direction
- **WIND_FROM_MEAN**: Mean wind direction (degrees from true north)
- **WIND_FROM_STDDEV**: Standard deviation of wind direction

### Wind Speed
- **WIND_SPEED_MEAN**: Mean wind speed (m/s)
- **WIND_SPEED_STDDEV**: Standard deviation of wind speed

### Wind Components (U/V)
- **UWND_MEAN**: Mean zonal wind component (eastward, m/s)
- **UWND_STDDEV**: Standard deviation of U-wind
- **VWND_MEAN**: Mean meridional wind component (northward, m/s)
- **VWND_STDDEV**: Standard deviation of V-wind

### Vertical Wind
- **WWND_MEAN**: Mean vertical wind component (m/s)
- **WWND_STDDEV**: Standard deviation of W-wind

### Wind Gusts
- **GUST_WND_MEAN**: Mean gust speed (m/s)
- **GUST_WND_STDDEV**: Standard deviation of gusts

### Air Temperature
- **TEMP_AIR_MEAN**: Mean air temperature (°C)
- **TEMP_AIR_STDDEV**: Standard deviation of air temperature

### Relative Humidity
- **RH_MEAN**: Mean relative humidity (%)
- **RH_STDDEV**: Standard deviation of relative humidity

### Barometric Pressure
- **BARO_PRES_MEAN**: Mean barometric pressure (hPa or mbar)
- **BARO_PRES_STDDEV**: Standard deviation of pressure

## Technical Specifications

### Wind Measurement
- **Speed Range**: 0-40 m/s
- **Speed Accuracy**: ±0.3 m/s or 3% (whichever is greater)
- **Direction Range**: 0-359°
- **Direction Accuracy**: ±2°
- **Update Rate**: 1 Hz

### Temperature
- **Range**: -40 to +80°C
- **Accuracy**: ±0.3°C
- **Resolution**: 0.1°C

### Humidity
- **Range**: 0-100% RH
- **Accuracy**: ±3% RH (10-90% range)
- **Resolution**: 0.1% RH

### Barometric Pressure
- **Range**: 300-1100 hPa
- **Accuracy**: ±0.5 hPa
- **Resolution**: 0.1 hPa

## Deployment Configuration

### Saildrone Platforms

**Explorer (SD 1000-1999):**
- Standard sensor package
- Wing-mounted, approximately 5m above sea level
- Clear exposure to atmospheric conditions

**Surveyor (SD 2000+):**
- Standard sensor package
- Higher wing mount (exact height varies)

### Mounting Position

The Airmar 150WX is mounted on the wing (vertical sail structure), providing:
- Elevated position above sea surface
- Minimal flow distortion from the vehicle
- Representative atmospheric measurements
- Clear 360° wind exposure

## Data Quality Considerations

### Calibration
- **Factory Calibration**: Pre-calibrated by manufacturer
- **Field Checks**: Verify against reference sensors periodically
- **Zero Offset**: Check wind sensor alignment

### Known Issues
- **Platform Motion**: May affect measurements in high seas
- **Salt Spray**: Can affect humidity sensor in extreme conditions
- **Shadowing**: Vehicle structure may affect readings when downwind
- **Thermal Lag**: Temperature sensor may lag actual conditions

### Quality Checks
- Range validation (wind speed, temperature, pressure)
- Consistency checks (U/V wind components vs. speed/direction)
- Standard deviation thresholds for stability

## Example Data

### Typical Values

```python
# Trade wind conditions (tropical ocean)
WIND_SPEED_MEAN: 8.5  # m/s
WIND_FROM_MEAN: 85.0  # degrees (easterly winds)
TEMP_AIR_MEAN: 27.5   # °C
RH_MEAN: 75.0         # %
BARO_PRES_MEAN: 1013.0  # hPa

# High-latitude storm
WIND_SPEED_MEAN: 18.0  # m/s
GUST_WND_MEAN: 25.0    # m/s
TEMP_AIR_MEAN: 5.0     # °C
BARO_PRES_MEAN: 995.0  # hPa (low pressure system)
```

### Detecting Airmar 150WX in Data

```python
from oceanstream.sensors import get_sensor_catalogue

catalogue = get_sensor_catalogue()

# Check for meteorological variables
met_vars = {"UWND_MEAN", "VWND_MEAN", "TEMP_AIR_MEAN"}

detected = catalogue.detect_sensors(met_vars)
for sensor in detected:
    if sensor.id == "airmar-150wx":
        print(f"Found {sensor.name}")
```

## STAC Metadata

```json
{
  "instruments": [
    {
      "id": "airmar-150wx",
      "name": "Airmar 150WX WeatherStation",
      "type": "meteorological",
      "manufacturer": "Airmar Technology Corporation",
      "model": "150WX",
      "description": "Integrated weather station measuring wind, air temperature, humidity, and pressure",
      "mount_position": "wing",
      "variables": [
        "WIND_FROM_MEAN", "WIND_SPEED_MEAN",
        "UWND_MEAN", "VWND_MEAN", "WWND_MEAN",
        "TEMP_AIR_MEAN", "RH_MEAN", "BARO_PRES_MEAN"
      ],
      "specifications": {
        "wind_speed_range": "0-40 m/s",
        "wind_speed_accuracy": "±0.3 m/s or 3%",
        "temperature_accuracy": "±0.3°C",
        "pressure_accuracy": "±0.5 hPa"
      },
      "documentation": "https://www.airmar.com/weather-description.html?id=154"
    }
  ]
}
```

## Scientific Applications

### Air-Sea Interaction
- Calculates heat and momentum fluxes
- Studies evaporation and turbulent mixing
- Provides boundary conditions for ocean models

### Weather Monitoring
- Real-time weather observations
- Storm tracking and intensity
- Climate monitoring networks

### Marine Operations
- Weather routing for vessels
- Aviation support (wind conditions)
- Offshore operations planning

## Related Sensors

- [SBE 37-SMP-ODO](./sbe37-odo.md) - Ocean surface temperature for air-sea temperature difference
- [LI-COR LI-190R PAR](./radiation-sensors.md#licor-li-190r-par-sensor) - Cloud cover indication
- [Kipp & Zonen CMP](./radiation-sensors.md#kipp--zonen-cmp-pyranometer) - Solar radiation (weather conditions)

## Documentation Links

- [Manufacturer Product Page](https://www.airmar.com/weather-description.html?id=154)
- [150WX Installation Guide](https://www.airmar.com/uploads/150wx/150WX_InstallationInstructions.pdf)

## Provider Integration

### Saildrone Provider

```python
# Saildrone semantic mappings
"WIND_SPEED_MEAN" → "wind_speed_mean_ms"
"WIND_FROM_MEAN" → "wind_from_direction_mean_deg"
"TEMP_AIR_MEAN" → "air_temperature_mean_c"
"RH_MEAN" → "relative_humidity_mean_percent"
"BARO_PRES_MEAN" → "barometric_pressure_mean_hpa"
```

See [Saildrone Provider](../data-providers/saildrone.md) for complete mappings.

## Further Reading

- [Sensor Catalogue Overview](./overview.md)
- [Data Providers](../data-providers/overview.md)
- [STAC Metadata](../stac-metadata.md)
