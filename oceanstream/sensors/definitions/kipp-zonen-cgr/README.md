# Kipp & Zonen CGR Series Pyrgeometer

## Overview

The CGR Series pyrgeometer measures downwelling and net longwave (infrared) radiation in the 4.5–42 µm spectral range. Used on Saildrone platforms for surface energy budget and air-sea heat flux studies.

## Key Features

- **Manufacturer**: Kipp & Zonen
- **Model**: CGR Series
- **Type**: Radiation Sensor (Pyrgeometer / LWR)
- **Deployment**: Mast or wing, 0.2 m above waterline

## Measured Variables

| Variable | Description | Units |
|----------|-------------|-------|
| `LW_IRRAD_MEAN` | Downwelling longwave radiation | W m⁻² |
| `LW_NET_IRRAD_MEAN` | Net longwave radiation | W m⁻² |
| `TEMP_LW_MEAN` | Sensor body temperature | °C |
| `LW_NET_IRRAD_STDDEV` | Std dev of net longwave radiation | W m⁻² |
| `TEMP_LW_STDDEV` | Std dev of sensor body temperature | °C |
| `LW_QC` | Quality control flag | — |

## Specifications

| Parameter | Value |
|-----------|-------|
| Spectral Range | 4500–42000 nm |
| Sensitivity | 5–15 µV/W/m² |
| Response Time | <18 s (95%) |
| Field of View | 180° (hemispherical) |

## Links

- [Manufacturer Product Page](https://www.kippzonen.com/Product/17/CGR-series)
- [Kipp & Zonen](https://www.kippzonen.com/)

## Deployment Notes

Mounted on Saildrone at 0.2 m height above the waterline. Measures thermal infrared radiation emitted by the atmosphere (downwelling) and the net balance between downwelling and upwelling longwave radiation at the ocean surface. Essential for computing the surface radiation budget alongside shortwave (CMP series) measurements.

## Data Source

NOAA PMEL OCS project — TPOS 2023 Saildrone missions. 1-minute resolution data from standalone logbox with light QC applied (NaN when net LWR is positive, despiking, regridding to uniform time axis).
