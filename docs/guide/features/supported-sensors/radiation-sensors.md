# Radiation Sensors

OceanStream supports three radiation sensors that measure different aspects of light and solar energy at the ocean surface.

## LI-COR LI-190R PAR Sensor

### Overview

| Property | Value |
|----------|-------|
| **Sensor ID** | `licor-li190r` |
| **Manufacturer** | LI-COR Biosciences |
| **Model** | LI-190R |
| **Type** | Radiation (PAR) |
| **Typical Mount** | Wing, upper side |
| **Wavelength Range** | 400-700 nm |

### Description

The LI-COR LI-190R measures Photosynthetically Active Radiation (PAR) - the portion of the solar spectrum (400-700 nm) used by plants and phytoplankton for photosynthesis.

### Measured Variables

- **PAR_AIR_MEAN**: Mean PAR (µmol s⁻¹ m⁻²)
- **PAR_AIR_STDDEV**: Standard deviation of PAR

### Technical Specifications

- **Range**: 0-10,000 µmol s⁻¹ m⁻²
- **Spectral Range**: 400-700 nm
- **Calibration Uncertainty**: ±5%
- **Response Time**: <1 ms
- **Cosine Response**: Within 5% (0-75° zenith angle)

### Scientific Applications

- **Primary Productivity**: Light availability for phytoplankton photosynthesis
- **Light Attenuation**: Surface PAR for vertical light profile models
- **Diel Cycles**: Day/night and seasonal light patterns
- **Cloud Detection**: Variability indicates cloud cover

[Documentation](https://www.licor.com/env/products/light/quantum)

---

## Kipp & Zonen CMP Pyranometer

### Overview

| Property | Value |
|----------|-------|
| **Sensor ID** | `kipp-zonen-cmp` |
| **Manufacturer** | Kipp & Zonen |
| **Model** | CMP Series |
| **Type** | Radiation (Broadband) |
| **Typical Mount** | Wing, upper side |
| **Wavelength Range** | 285-2800 nm |

### Description

The Kipp & Zonen CMP pyranometer measures total and diffuse shortwave irradiance across the full solar spectrum, providing comprehensive solar energy data.

### Measured Variables

- **SW_IRRAD_TOTAL_MEAN**: Mean total shortwave irradiance (W/m²)
- **SW_IRRAD_TOTAL_STDDEV**: Standard deviation of total irradiance
- **SW_IRRAD_DIFFUSE_MEAN**: Mean diffuse irradiance (W/m²)
- **SW_IRRAD_DIFFUSE_STDDEV**: Standard deviation of diffuse irradiance

### Technical Specifications

- **Spectral Range**: 285-2800 nm (full solar spectrum)
- **Sensitivity**: 5-20 µV/W/m²
- **Response Time**: <5 seconds (95% response)
- **Directional Error**: <10 W/m² (at 1000 W/m²)
- **Temperature Dependence**: <1% (-10 to +40°C)

### Scientific Applications

- **Solar Energy**: Total incoming solar radiation
- **Heat Budget**: Surface energy balance calculations
- **Atmospheric Studies**: Cloud cover and aerosol effects
- **Climate Monitoring**: Long-term radiation trends

[Documentation](https://www.kippzonen.com/Product/11/CMP-series)

---

## Apogee SI-111 Infrared Radiometer

### Overview

| Property | Value |
|----------|-------|
| **Sensor ID** | `apogee-si111` |
| **Manufacturer** | Apogee Instruments |
| **Model** | SI-111 |
| **Type** | Radiation (Infrared) |
| **Typical Mount** | Wing, lower side (viewing sea surface) |
| **Wavelength Range** | 8-14 µm |

### Description

The Apogee SI-111 is a narrow field-of-view infrared radiometer that measures sea surface temperature remotely via thermal infrared emission.

### Measured Variables

- **TEMP_IR_SEA_WING_UNCOMP_MEAN**: Mean uncompensated IR sea surface temperature (°C)
- **TEMP_IR_SEA_WING_UNCOMP_STDDEV**: Standard deviation of IR temperature

*Note: "Uncomp" indicates uncompensated measurement before skin temperature corrections*

### Technical Specifications

- **Temperature Range**: -40 to +80°C
- **Accuracy**: ±0.2°C (at 15-35°C)
- **Field of View**: 22° half angle
- **Spectral Range**: 8-14 µm (thermal infrared)
- **Response Time**: <1 second

### Scientific Applications

- **Sea Surface Temperature**: Non-contact SST measurement
- **Skin Layer Studies**: Surface thermal properties
- **Heat Flux**: Surface cooling/warming rates
- **Satellite Validation**: Ground truth for IR satellite sensors

[Documentation](https://www.apogeeinstruments.com/si-111-infrared-radiometer/)

---

## Radiation Sensor Comparison

| Sensor | Measurement | Wavelength | Typical Value | Units |
|--------|-------------|------------|---------------|-------|
| LI-190R | PAR (photosynthetic) | 400-700 nm | 1000 | µmol s⁻¹ m⁻² |
| CMP | Total solar irradiance | 285-2800 nm | 800 | W/m² |
| SI-111 | Sea surface temperature | 8-14 µm | 20 | °C |

## Detecting Radiation Sensors

```python
from oceanstream.sensors import get_sensor_catalogue

catalogue = get_sensor_catalogue()

# Check for radiation variables
rad_vars = {
    "PAR_AIR_MEAN",
    "SW_IRRAD_TOTAL_MEAN",
    "TEMP_IR_SEA_WING_UNCOMP_MEAN"
}

detected = catalogue.detect_sensors(rad_vars)
print(f"Detected {len(detected)} radiation sensors")
for sensor in detected:
    print(f"- {sensor.name} ({sensor.sensor_type.value})")
```

## STAC Metadata Example

```json
{
  "instruments": [
    {
      "id": "licor-li190r",
      "name": "LI-COR LI-190R PAR Sensor",
      "type": "radiation",
      "variables": ["PAR_AIR_MEAN"]
    },
    {
      "id": "kipp-zonen-cmp",
      "name": "Kipp & Zonen CMP Series Pyranometer",
      "type": "radiation",
      "variables": ["SW_IRRAD_TOTAL_MEAN", "SW_IRRAD_DIFFUSE_MEAN"]
    },
    {
      "id": "apogee-si111",
      "name": "Apogee SI-111 Infrared Radiometer",
      "type": "radiation",
      "variables": ["TEMP_IR_SEA_WING_UNCOMP_MEAN"]
    }
  ]
}
```

## Provider Integration

### Saildrone Provider

```python
# Saildrone semantic mappings
"PAR_AIR_MEAN" → "par_air_mean_umol_s_m2"
"SW_IRRAD_TOTAL_MEAN" → "shortwave_irradiance_total_mean_w_m2"
"TEMP_IR_SEA_WING_UNCOMP_MEAN" → "sea_surface_temperature_ir_mean_c"
```

## Related Sensors

- [Airmar 150WX](./airmar-150wx.md) - Meteorological data (cloud cover affects radiation)
- [WET Labs FLBBCD](./wetlabs-flbbcd.md) - Chlorophyll (PAR drives photosynthesis)

## Further Reading

- [Sensor Catalogue Overview](./overview.md)
- [Data Providers](../data-providers/overview.md)
