# Sensor Metadata Verification

This document verifies the accuracy of sensor metadata in our catalogue against manufacturer datasheets and R2R archive metadata.

## Verification Date: 2025-11-17

---

## 1. WET Labs ECO-FLNTU Fluorometer

### Sensor ID: `wetlabs-eco-flntu`

### R2R Archive Metadata ✅
```
Source: FK161229_124688_fluorometer.tar.gz
R2R-DeviceModel: WET Labs ECO-FLNTU
R2R-DeviceType: fluorometer
```

### Current Catalogue Entry

```json
{
  "id": "wetlabs-eco-flntu",
  "name": "WET Labs ECO-FLNTU Fluorometer",
  "manufacturer": "WET Labs (Sea-Bird Scientific)",
  "model": "ECO-FLNTU",
  "sensor_type": "fluorometer",
  "variables": ["CHL_FLUOR", "TURBIDITY", "BACKSCATTER"],
  "specifications": {
    "chlorophyll_range": "0-50 μg/L",
    "chlorophyll_resolution": "0.025 μg/L",
    "turbidity_range": "0-25 NTU",
    "turbidity_resolution": "0.01 NTU",
    "sampling_rate": "6 Hz",
    "depth_rating": "600 m"
  }
}
```

### Verification Status

| Field | Status | Notes |
|-------|--------|-------|
| **Manufacturer** | ✅ CORRECT | WET Labs acquired by Sea-Bird Scientific (now Teledyne) |
| **Model** | ✅ CORRECT | ECO-FLNTU confirmed in R2R metadata |
| **Variables** | ⚠️ REVIEW | Need to verify exact channel mapping |
| **Chlorophyll Range** | ⚠️ NEEDS VERIFICATION | Common range, but model-specific |
| **Chlorophyll Resolution** | ⚠️ NEEDS VERIFICATION | Typical value, needs datasheet |
| **Turbidity Range** | ⚠️ NEEDS VERIFICATION | Standard NTU range |
| **Turbidity Resolution** | ⚠️ NEEDS VERIFICATION | Typical value |
| **Sampling Rate** | ⚠️ NEEDS VERIFICATION | 6 Hz is plausible but unconfirmed |
| **Depth Rating** | ⚠️ NEEDS VERIFICATION | 600m standard, but ECO-FLNTU may vary |

### Known ECO-FLNTU Variants

The ECO-FLNTU has multiple configurations:
- **Channels**: FL (Fluorescence) + NTU (Turbidity) + possibly backscatter
- **Ranges**: Configurable at time of order
- **Common CHL-a ranges**: 0-15, 0-30, 0-50, 0-125 μg/L
- **Common NTU ranges**: 0-25, 0-50, 0-250, 0-1000 NTU

### Recommendations

1. **✓ KEEP** manufacturer and model - confirmed
2. **⚠️ VERIFY** specifications from manufacturer datasheet or calibration sheet
3. **⚠️ ADD** note that ranges are configurable/sensor-specific
4. **⚠️ CONSIDER** making some specs optional or ranges

---

## 2. Valeport MiniSVS Sound Velocity Sensor

### Sensor ID: `valeport-minisvs`

### R2R Archive Metadata ✅
```
Source: FK161229_124690_ssv.tar.gz
R2R-DeviceModel: Valeport MiniSVS
R2R-DeviceType: ssv
```

### Current Catalogue Entry

```json
{
  "id": "valeport-minisvs",
  "name": "Valeport MiniSVS Sound Velocity Sensor",
  "manufacturer": "Valeport",
  "model": "MiniSVS",
  "sensor_type": "acoustic",
  "variables": ["SOUND_VELOCITY", "TEMPERATURE", "PRESSURE"],
  "specifications": {
    "velocity_range": "1375-1900 m/s",
    "velocity_accuracy": "±0.02 m/s",
    "velocity_resolution": "0.001 m/s",
    "temperature_range": "-5 to +35°C",
    "temperature_accuracy": "±0.01°C",
    "pressure_range": "0-6000 dbar",
    "pressure_accuracy": "±0.05% FS",
    "sampling_rate": "8 Hz"
  }
}
```

### Verification Against Valeport Datasheet ✅

**Source**: https://www.valeport.co.uk/products/minisvs

| Specification | Catalogue | Datasheet | Status |
|---------------|-----------|-----------|---------|
| **Velocity Range** | 1375-1900 m/s | **1375-1900 m/s** | ✅ CORRECT |
| **Velocity Resolution** | 0.001 m/s | **0.001 m/s** | ✅ CORRECT |
| **Velocity Accuracy** | ±0.02 m/s | ±0.017-0.020 m/s (size-dependent) | ⚠️ SIMPLIFIED |
| **Temperature Range** | -5 to +35°C | **-5 to +35°C** | ✅ CORRECT |
| **Temperature Accuracy** | ±0.01°C | **±0.01°C** | ✅ CORRECT |
| **Pressure Range** | 0-6000 dbar | 0-6000 Bar (600 Bar option) | ⚠️ UNITS ISSUE |
| **Pressure Accuracy** | ±0.05% FS | **±0.05% FS** | ✅ CORRECT |
| **Sampling Rate** | 8 Hz | Up to **8 Hz** (SV+P config) | ✅ CORRECT |
| **Depth Rating** | Not specified | **6000 m** (titanium) | ❌ MISSING |

### Detailed Findings

#### 1. Velocity Accuracy ⚠️
**Datasheet says**:
- 100 mm sensor: Total max theoretical error ±0.017 m/s
- 50 mm sensor: Total max theoretical error ±0.019 m/s  
- 25 mm sensor: Total max theoretical error ±0.020 m/s

**Our value**: ±0.02 m/s

**Action**: ✅ ACCEPTABLE - We used the worst-case (25mm) as a conservative estimate. Consider adding note about size-dependency.

#### 2. Pressure Range ⚠️
**Datasheet says**: Range options: 5, 10, 20, 30, 50, 100, or **600 Bar**

**Our value**: 0-6000 dbar

**Issue**: 
- 600 Bar = 6000 dbar ✅ (conversion correct)
- BUT this is only one option, not the full range
- Datasheet lists 7 different range options

**Action**: ⚠️ CLARIFY - Should specify "up to 6000 dbar" or list common ranges

#### 3. Variables ⚠️
**Datasheet says**: miniSVS can be configured as:
- SV only (sound velocity only)
- SV + P (sound velocity + pressure)
- SV + T (sound velocity + temperature)

**Our value**: `["SOUND_VELOCITY", "TEMPERATURE", "PRESSURE"]`

**Issue**: Not all miniSVS units have both T and P - it's **either/or** per the datasheet!

**Action**: ⚠️ FIX - Should clarify that T and P are optional, mutually exclusive configurations

#### 4. Depth Rating ❌
**Datasheet says**: **6000 m** (titanium housing)

**Our value**: Not specified in sensor.json

**Action**: ❌ ADD - Should add `"depth_rating": "6000 m"`

### Recommendations for Valeport MiniSVS

1. **✓ KEEP** most specifications - highly accurate
2. **⚠️ FIX** variables description - clarify T and P are optional/mutually exclusive
3. **⚠️ CLARIFY** pressure range - note it's configurable (multiple options available)
4. **❌ ADD** depth_rating: "6000 m"
5. **⚠️ ADD** note about accuracy being size-dependent (25mm, 50mm, 100mm sensors)

---

## Summary

### Overall Accuracy Assessment

| Sensor | Overall Status | Confidence Level |
|--------|---------------|------------------|
| **WET Labs ECO-FLNTU** | ⚠️ NEEDS VERIFICATION | Medium - manufacturer confirmed, specs need datasheet |
| **Valeport MiniSVS** | ✅ MOSTLY ACCURATE | High - verified against official datasheet |

### Required Actions

#### Priority 1 (Critical) ❌
1. **Add depth_rating for Valeport MiniSVS**: "6000 m"
2. **Fix Valeport variables**: Clarify T and P are optional, not always both present

#### Priority 2 (Important) ⚠️
3. **ECO-FLNTU specifications**: Obtain datasheet or note that ranges are configurable
4. **Valeport pressure range**: Clarify "up to 6000 dbar" with multiple options
5. **Valeport accuracy**: Add note about size-dependency

#### Priority 3 (Enhancement) 💡
6. Consider adding "configurable" flag for sensors with multiple range options
7. Add calibration/configuration notes field
8. Link to specific product datasheets where available

---

## References

### Valeport MiniSVS
- **Datasheet**: https://www.valeport.co.uk/content/uploads/2022/06/Valeport-miniSVS-Datasheet-1.pdf
- **Product Page**: https://www.valeport.co.uk/products/minisvs
- **Verified**: 2025-11-17

### WET Labs ECO-FLNTU
- **Manufacturer**: Sea-Bird Scientific (Teledyne Marine)
- **Product Page**: https://www.seabird.com/ (search for ECO-FLNTU)
- **Note**: Specific datasheet needed for exact specifications
- **Status**: Partial verification via R2R metadata

### R2R Archives
- **Fluorometer**: FK161229_124688_fluorometer.tar.gz (DOI: 10.7284/124688)
- **SSV**: FK161229_124690_ssv.tar.gz (DOI: 10.7284/124690)
- **Cruise**: FK161229 (R/V Falkor)

---

## Next Steps

1. **Immediate**: Fix critical issues for Valeport MiniSVS
2. **Short-term**: Obtain ECO-FLNTU datasheet for verification
3. **Medium-term**: Establish process for verifying all sensor metadata against datasheets
4. **Long-term**: Consider schema enhancements for configurable sensors
