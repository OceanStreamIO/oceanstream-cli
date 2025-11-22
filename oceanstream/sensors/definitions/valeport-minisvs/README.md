# Valeport MiniSVS Sound Velocity Sensor

## Overview

The Valeport MiniSVS is a high-precision sound velocity sensor (SVS) designed for oceanographic research and hydrographic surveying. It measures the speed of sound in seawater, which is critical for accurate acoustic positioning, bathymetric mapping, and understanding water column properties.

## Key Features

- Direct measurement of sound velocity (no computed approximation)
- High accuracy (±0.02 m/s)
- Fast sampling rate (8 Hz)
- Integrated temperature and pressure sensors
- Compact form factor suitable for hull mounting or CTD rosettes
- Digital RS-232/RS-485 output

## Measured Variables

- **Sound Velocity** (`SOUND_VELOCITY`): Speed of sound in seawater (m/s)
- **Temperature** (`TEMPERATURE`): Water temperature (°C)
- **Pressure** (`PRESSURE`): Depth pressure (dbar)

## Technical Specifications

| Parameter | Value |
|-----------|-------|
| Velocity Range | 1375-1900 m/s |
| Velocity Accuracy | ±0.02 m/s |
| Velocity Resolution | 0.001 m/s |
| Temperature Range | -5 to +35°C |
| Temperature Accuracy | ±0.01°C |
| Pressure Range | 0-6000 dbar |
| Pressure Accuracy | ±0.05% FS |
| Sampling Rate | 8 Hz |
| Depth Rating | 6000 m |

## Applications

- **Acoustic Oceanography**: Essential for multibeam echosounder calibration
- **Bathymetric Surveying**: Real-time sound velocity profiling for depth correction
- **Water Column Studies**: Investigating stratification and mixing
- **AUV/ROV Operations**: Accurate underwater positioning

## Deployment Notes

- Typically deployed as hull-mounted sensor on research vessels
- Can be integrated into CTD rosettes for vertical profiling
- Requires minimal maintenance; periodic factory calibration recommended
- Sound velocity is temperature-dependent; integrated temperature sensor ensures accurate measurements

## Manufacturer Documentation

- [Valeport MiniSVS Product Page](https://www.valeport.co.uk/products/minisvs)
- [MiniSVS User Manual](https://www.valeport.co.uk/content/uploads/2021/08/MiniSVS-Manual.pdf)

## Data Processing

R2R SSV raw data is processed by the `r2r_ssv` sensor processor, which:

1. Parses proprietary format into standardized CSV
2. Extracts sound velocity, temperature, and pressure channels
3. Applies timestamp corrections
4. Validates data quality

Processed data is then available for geotrack pipeline ingestion and integration with other oceanographic sensors.
