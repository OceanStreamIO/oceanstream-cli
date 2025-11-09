# IMU-Derived Wave Sensor

## Overview

This proprietary Saildrone system derives wave characteristics from Inertial Measurement Unit (IMU) motion data through spectral analysis, providing wave height and period estimates.

## Key Features

- **Manufacturer**: Saildrone
- **Model**: Proprietary
- **Type**: Wave Sensor
- **Deployment**: Derived from hull IMU

## Measured Variables

- Wave dominant period (WAVE_DOMINANT_PERIOD)
- Significant wave height (WAVE_SIGNIFICANT_HEIGHT)

## Specifications

| Parameter | Range |
|-----------|-------|
| Period | 2-25 s |
| Height | 0-10 m |
| Processing | Spectral analysis of IMU data |

## Links

- [Saildrone](https://www.saildrone.com/)

## Deployment Notes

Wave parameters are computed from the vehicle's motion as measured by the IMU. Spectral analysis techniques extract wave characteristics from the pitch, roll, and heave motions of the platform.
