# Provider Semantic Mappings

## Overview

Oceanstream uses a two-tier aliasing system to enable cross-provider interoperability:

1. **Semantic Mappings**: Provider-specific column names → Canonical vocabulary
2. **Syntactic Normalization**: Fallback snake_case conversion for unmapped columns

This approach allows data from different USV providers to be queried and analyzed using a common set of field names.

## Architecture

### Semantic Mappings (Priority 1)

Each provider defines a `SEMANTIC_MAPPINGS` dictionary that maps provider-specific abbreviations and conventions to a canonical vocabulary with explicit units.

**Example (Saildrone):**
```python
SEMANTIC_MAPPINGS = {
    "SOG": "speed_over_ground_ms",           # Speed Over Ground → canonical with unit
    "COG": "course_over_ground_deg",         # Course Over Ground → canonical with unit
    "TEMP_AIR_MEAN": "air_temperature_mean_c",  # Air temp → canonical with unit
    # ... etc
}
```

**Benefits:**
- ✅ Cross-provider queries: `speed_over_ground_ms` works across all providers
- ✅ Explicit units: `_ms`, `_deg`, `_c`, `_hpa` in field names
- ✅ Self-documenting: Field names indicate measurement and unit
- ✅ Type-safe: Static mappings catch typos at development time

### Syntactic Normalization (Priority 2)

For columns without semantic mappings, the system falls back to syntactic normalization using `_normalize_alias()`:

- `CustomColumn` → `custom_column`
- `UNKNOWN_FIELD` → `unknown_field`
- `camelCase` → `camel_case`

This ensures all field names are consistently formatted as snake_case.

## Canonical Vocabulary

### Naming Convention

Canonical field names follow this pattern:
```
{measurement}_{statistic}_{unit}
```

**Examples:**
- `air_temperature_mean_c` - Air temperature mean in Celsius
- `wind_speed_stddev_ms` - Wind speed standard deviation in meters/second
- `salinity_ctd_mean_psu` - Salinity from CTD, mean value in PSU

### Unit Suffixes

| Suffix | Unit | Description |
|--------|------|-------------|
| `_ms` | m/s | Meters per second (velocity) |
| `_deg` | ° | Degrees (angle) |
| `_c` | °C | Degrees Celsius (temperature) |
| `_percent` | % | Percentage |
| `_hpa` | hPa | Hectopascals (pressure) |
| `_m` | m | Meters (distance/height) |
| `_s` | s | Seconds (time) |
| `_psu` | PSU | Practical Salinity Units |
| `_ms_cm` | mS/cm | MilliSiemens per centimeter (conductivity) |
| `_umol_l` | μmol/L | Micromoles per liter (concentration) |
| `_ug_l` | μg/L | Micrograms per liter (concentration) |
| `_umol_s_m2` | μmol/(s·m²) | Micromoles per second per square meter (PAR) |
| `_w_m2` | W/m² | Watts per square meter (irradiance) |

## Adding a New Provider

When implementing a new USV provider, follow these steps:

### 1. Analyze Provider's Column Naming

Collect sample data and document the provider's naming conventions:

```python
# Example: Wave Glider provider
WAVE_GLIDER_COLUMNS = {
    "speed_gnd": "Speed over ground",
    "course_gnd": "Course over ground", 
    "air_temp_avg": "Air temperature average",
    # ... etc
}
```

### 2. Create Semantic Mappings

Map provider-specific names to the canonical vocabulary. If a canonical field doesn't exist, propose a new one following the naming convention.

```python
class WaveGliderProvider(ProviderBase):
    name = "waveglider"
    
    SEMANTIC_MAPPINGS = {
        # Map to existing canonical fields where possible
        "speed_gnd": "speed_over_ground_ms",
        "course_gnd": "course_over_ground_deg",
        "air_temp_avg": "air_temperature_mean_c",
        
        # New canonical fields (if needed)
        "battery_voltage": "battery_voltage_v",
        "solar_current": "solar_current_a",
    }
```

### 3. Document Provider-Specific Behavior

Create a provider-specific documentation file:

```markdown
# Wave Glider Provider

## Column Mappings

| Wave Glider Column | Canonical Field | Unit | Notes |
|--------------------|-----------------|------|-------|
| `speed_gnd` | `speed_over_ground_ms` | m/s | GPS-derived |
| `course_gnd` | `course_over_ground_deg` | ° | True heading |
| `air_temp_avg` | `air_temperature_mean_c` | °C | 1-minute average |
```

### 4. Write Tests

Test both semantic and syntactic mappings:

```python
def test_waveglider_semantic_mappings():
    provider = get_provider("waveglider")
    aliases = provider.alias_mapping(["speed_gnd", "course_gnd"])
    
    assert aliases["speed_gnd"] == "speed_over_ground_ms"
    assert aliases["course_gnd"] == "course_over_ground_deg"
```

## Cross-Provider Queries

With semantic mappings in place, you can query data from multiple providers using canonical field names:

```python
# Works across Saildrone, Wave Glider, Autonaut, etc.
df = parquet_dataset.filter(
    pl.col("speed_over_ground_ms") > 2.0
).select([
    "timestamp_utc",
    "latitude_deg",
    "longitude_deg", 
    "speed_over_ground_ms",
    "air_temperature_mean_c"
])
```

## Saildrone Semantic Mappings Reference

### Navigation & Platform Motion
- `SOG` → `speed_over_ground_ms`
- `COG` → `course_over_ground_deg`
- `HDG` → `heading_deg`
- `ROLL_FILTERED_MEAN` → `roll_filtered_mean_deg`
- `PITCH_FILTERED_MEAN` → `pitch_filtered_mean_deg`

### Meteorological
- `WIND_SPEED_MEAN` → `wind_speed_mean_ms`
- `WIND_FROM_MEAN` → `wind_direction_mean_deg`
- `TEMP_AIR_MEAN` → `air_temperature_mean_c`
- `RH_MEAN` → `relative_humidity_mean_percent`
- `BARO_PRES_MEAN` → `barometric_pressure_mean_hpa`

### Oceanographic (CTD)
- `TEMP_SBE37_MEAN` → `water_temperature_ctd_mean_c`
- `SAL_SBE37_MEAN` → `salinity_ctd_mean_psu`
- `COND_SBE37_MEAN` → `conductivity_ctd_mean_ms_cm`
- `O2_CONC_SBE37_MEAN` → `oxygen_concentration_mean_umol_l`
- `CHLOR_WETLABS_MEAN` → `chlorophyll_fluorescence_mean_ug_l`

### Radiation & Optical
- `PAR_AIR_MEAN` → `par_air_mean_umol_s_m2`
- `SW_IRRAD_TOTAL_MEAN` → `shortwave_irradiance_total_mean_w_m2`

### Waves
- `WAVE_DOMINANT_PERIOD` → `wave_dominant_period_s`
- `WAVE_SIGNIFICANT_HEIGHT` → `wave_significant_height_m`

See `oceanstream/providers/saildrone.py` for the complete list of 60+ semantic mappings.

## Migration Guide

### For Existing Code

If your code relies on the old syntactic-only aliasing:

**Before:**
```python
# Old: SOG → sog (lowercase only)
df.select(pl.col("sog"))
```

**After:**
```python
# New: SOG → speed_over_ground_ms (semantic + unit)
df.select(pl.col("speed_over_ground_ms"))
```

### Backward Compatibility

The system maintains backward compatibility:
- Original column names are **always preserved** in the dataset
- Aliases are **metadata only** (stored in Parquet key-value metadata)
- You can still query using original names: `SOG`, `TEMP_AIR_MEAN`, etc.
- Semantic aliases are **opt-in** for cross-provider queries

## Future Work

1. **CF Conventions Alignment**: Align canonical vocabulary with Climate & Forecast metadata conventions
2. **Ontology Integration**: Link to oceanographic ontologies (NERC, MMI)
3. **Automatic Unit Conversion**: Handle providers with different unit conventions
4. **Semantic Validation**: Validate that mapped fields have compatible units and ranges
