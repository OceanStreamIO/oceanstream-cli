# Navigation & Motion Sensors

OceanStream supports two sensor systems for navigation, heading, and vehicle motion measurements.

## IMU & GPS Navigation System

### Overview

| Property | Value |
|----------|-------|
| **Sensor ID** | `imu-navigation` |
| **Manufacturer** | Multiple |
| **Model** | Various |
| **Type** | Navigation |
| **Typical Mount** | Hull and wing |
| **Update Rate** | 10 Hz |

### Description

Combined GPS and Inertial Measurement Unit (IMU) providing position, velocity, heading, and vehicle attitude. This sensor system delivers Saildrone-specific filtered measurements optimized for autonomous surface vehicle dynamics.

### Measured Variables

The IMU & GPS system provides 24 variables across multiple categories:

#### Platform Identification
- **trajectory**: Saildrone vehicle number (e.g., 1030)

#### Speed Over Ground (Filtered)
- **SOG_FILTERED_MEAN**: Mean speed over ground (m/s)
- **SOG_FILTERED_STDDEV**: Standard deviation of SOG

#### Course Over Ground (Filtered)
- **COG_FILTERED_MEAN**: Mean course over ground (degrees)
- **COG_FILTERED_STDDEV**: Standard deviation of COG

#### Heading (Hull)
- **HDG**: Instantaneous heading (degrees true)
- **HDG_FILTERED_MEAN**: Mean heading (degrees true)
- **HDG_FILTERED_STDDEV**: Standard deviation of heading

#### Roll & Pitch (Hull)
- **ROLL_FILTERED_MEAN**: Mean roll angle (degrees)
- **ROLL_FILTERED_STDDEV**: Standard deviation of roll
- **ROLL_FILTERED_PEAK**: Peak roll angle (degrees)
- **PITCH_FILTERED_MEAN**: Mean pitch angle (degrees)
- **PITCH_FILTERED_STDDEV**: Standard deviation of pitch
- **PITCH_FILTERED_PEAK**: Peak pitch angle (degrees)

#### Wing Heading
- **HDG_WING**: Instantaneous wing heading (degrees true)
- **WING_HDG_FILTERED_MEAN**: Mean wing heading (degrees)
- **WING_HDG_FILTERED_STDDEV**: Standard deviation of wing heading

#### Wing Roll & Pitch
- **WING_ROLL_FILTERED_MEAN**: Mean wing roll (degrees)
- **WING_ROLL_FILTERED_STDDEV**: Standard deviation of wing roll
- **WING_ROLL_FILTERED_PEAK**: Peak wing roll (degrees)
- **WING_PITCH_FILTERED_MEAN**: Mean wing pitch (degrees)
- **WING_PITCH_FILTERED_STDDEV**: Standard deviation of wing pitch
- **WING_PITCH_FILTERED_PEAK**: Peak wing pitch (degrees)

#### Wing Configuration
- **WING_ANGLE**: Wing angle relative to hull (degrees)

### Technical Specifications

- **GPS Accuracy**: 2.5m CEP (Circular Error Probable)
- **Heading Accuracy**: 0.5°
- **Attitude Accuracy**: 0.1° (roll/pitch)
- **Update Rate**: 10 Hz (raw), 10-minute averages in output
- **Position Update**: 1 Hz GPS
- **IMU Rate**: 100 Hz (internal)

### Deployment Configuration

The navigation system consists of:
- **Hull-mounted IMU**: Measures vehicle body motion
- **Wing-mounted IMU**: Measures sail/wing orientation
- **GPS Antenna**: Provides position and ground velocity

This dual-IMU configuration enables:
- Independent hull and wing motion tracking
- Accurate heading and course
- Wave-induced motion characterization

### Data Quality Considerations

#### Filtering
All measurements are Kalman-filtered to remove noise while preserving dynamics:
- **Filtered Mean**: Primary measurement (robust to noise)
- **Filtered StdDev**: Variability indicator
- **Filtered Peak**: Maximum excursion (roll/pitch only)

#### Known Issues
- **GPS Dropouts**: May occur in high seas or poor satellite geometry
- **Magnetic Interference**: Heading may be affected near magnetic structures
- **Dynamic Motion**: Extreme roll/pitch can temporarily affect accuracy

### Example Data

```python
# Typical sailing conditions
trajectory: 1030
SOG_FILTERED_MEAN: 2.5  # m/s (~5 knots)
COG_FILTERED_MEAN: 285.0  # degrees (WNW)
HDG_FILTERED_MEAN: 280.0  # degrees
ROLL_FILTERED_MEAN: 5.0  # degrees (slight heel)
PITCH_FILTERED_MEAN: 2.0  # degrees
WING_ANGLE: 15.0  # degrees relative to hull

# High-wind conditions
SOG_FILTERED_MEAN: 4.0  # m/s (~8 knots)
ROLL_FILTERED_MEAN: 12.0  # degrees
ROLL_FILTERED_PEAK: 18.0  # degrees
PITCH_FILTERED_MEAN: 3.5  # degrees
```

---

## IMU-Derived Wave Sensor

### Overview

| Property | Value |
|----------|-------|
| **Sensor ID** | `wave-imu` |
| **Manufacturer** | Saildrone |
| **Model** | Proprietary |
| **Type** | Wave |
| **Typical Mount** | Hull IMU |

### Description

The wave sensor derives ocean wave characteristics from spectral analysis of hull IMU motion data. This indirect measurement provides valuable wave information without dedicated wave buoys or sensors.

### Measured Variables

- **WAVE_DOMINANT_PERIOD**: Dominant wave period (seconds)
- **WAVE_SIGNIFICANT_HEIGHT**: Significant wave height (meters)

**Note**: Significant wave height (Hs or H₁/₃) is defined as the mean height of the highest one-third of waves.

### Technical Specifications

- **Period Range**: 2-25 seconds
- **Height Range**: 0-10 meters
- **Processing**: Spectral analysis of IMU vertical acceleration
- **Resolution**: Limited by vehicle response characteristics

### Scientific Applications

- **Sea State Monitoring**: Real-time wave conditions
- **Wave Climate Studies**: Regional wave climatology
- **Model Validation**: Ground truth for wave models
- **Marine Operations**: Safety and operational planning

### Data Quality Considerations

#### Limitations
- **Vehicle Response**: Saildrone dynamics filter some wave energy
- **High Frequency**: May underestimate very short period waves (<3s)
- **Calm Conditions**: Less accurate in very low sea states
- **Platform Motion**: Affected by vehicle maneuvering

#### Best Practices
- Use in moderate to high sea states (Hs > 0.5m)
- Compare with nearby buoys when available
- Consider vehicle speed and heading relative to waves

### Example Data

```python
# Calm conditions
WAVE_DOMINANT_PERIOD: 8.0  # seconds
WAVE_SIGNIFICANT_HEIGHT: 0.8  # meters

# Moderate sea state
WAVE_DOMINANT_PERIOD: 6.5  # seconds
WAVE_SIGNIFICANT_HEIGHT: 2.0  # meters

# Rough seas
WAVE_DOMINANT_PERIOD: 10.0  # seconds
WAVE_SIGNIFICANT_HEIGHT: 4.5  # meters
```

---

## Navigation & Wave Sensor Detection

```python
from oceanstream.sensors import get_sensor_catalogue

catalogue = get_sensor_catalogue()

# Check for navigation and wave variables
nav_vars = {
    "SOG_FILTERED_MEAN",
    "HDG",
    "ROLL_FILTERED_MEAN",
    "WAVE_SIGNIFICANT_HEIGHT"
}

detected = catalogue.detect_sensors(nav_vars)
for sensor in detected:
    print(f"{sensor.name} ({sensor.sensor_type.value})")
# Output:
# IMU & GPS Navigation (navigation)
# IMU-Derived Wave Sensor (wave)
```

## STAC Metadata

```json
{
  "instruments": [
    {
      "id": "imu-navigation",
      "name": "Inertial Measurement Unit & GPS",
      "type": "navigation",
      "mount_position": "hull and wing",
      "specifications": {
        "gps_accuracy": "2.5m CEP",
        "heading_accuracy": "0.5°",
        "update_rate": "10 Hz"
      }
    },
    {
      "id": "wave-imu",
      "name": "IMU-Derived Wave Sensor",
      "type": "wave",
      "mount_position": "hull IMU",
      "specifications": {
        "period_range": "2-25 s",
        "height_range": "0-10 m"
      }
    }
  ]
}
```

## Provider Integration

### Saildrone Provider

```python
# Navigation mappings
"SOG_FILTERED_MEAN" → "speed_over_ground_mean_ms"
"COG_FILTERED_MEAN" → "course_over_ground_mean_deg"
"HDG_FILTERED_MEAN" → "heading_mean_deg"

# Motion mappings
"ROLL_FILTERED_MEAN" → "roll_mean_deg"
"PITCH_FILTERED_MEAN" → "pitch_mean_deg"

# Wave mappings
"WAVE_SIGNIFICANT_HEIGHT" → "wave_height_significant_m"
"WAVE_DOMINANT_PERIOD" → "wave_period_dominant_s"
```

## Related Sensors

- [Airmar 150WX](./airmar-150wx.md) - Wind data for air-sea interaction
- [SBE 37-SMP-ODO](./sbe37-odo.md) - Ocean measurements paired with position

## Further Reading

- [Sensor Catalogue Overview](./overview.md)
- [Data Providers](../data-providers/overview.md)
