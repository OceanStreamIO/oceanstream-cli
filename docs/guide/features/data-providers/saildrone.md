# Saildrone Provider

The Saildrone provider handles data from Saildrone autonomous surface vehicles, including Explorer (sd1000-sd1999) and Surveyor (sd2000+) platforms.

## Overview

**Organization**: Saildrone Inc.  
**Platform Type**: Autonomous surface vehicles  
**Data Format**: CSV with standardized column names  
**Supported Modules**: geotrack  
**Status**: ✅ Production

## Data Characteristics

### Filename Format

```
sd{id}_{mission}_{year}_{hash}_{hash}_{hash}.csv
```

**Examples**:
- `sd1030_tpos_2023_7ef2_e8f7_98f9.csv`
- `sd1033_tpos_2023_ec82_8b2b_3245.csv`
- `sd1079_atlantic_2024_a1b2_c3d4_e5f6.csv`

**Platform ID extraction**:
- Input: `sd1030_tpos_2023_7ef2_e8f7_98f9.csv`
- Output: `sd1030_tpos_2023`

### Column Naming

Saildrone uses abbreviated codes:
- **Navigation**: `SOG`, `COG`, `HDG`
- **Meteorological**: `TEMP_AIR_MEAN`, `WIND_SPEED_MEAN`, `RH_MEAN`
- **Oceanographic**: `TEMP_SBE37_MEAN`, `SAL_SBE37_MEAN`, `O2_CONC_SBE37_MEAN`
- **Statistics**: `_MEAN`, `_STDDEV`, `_MIN`, `_MAX`, `_PEAK`

### Data Source

Saildrone data is available from:
- **NOAA PMEL ERDDAP**: https://data.pmel.noaa.gov/pmel/erddap/
- **Saildrone directly**: Via data partnerships
- **NCEI archives**: Long-term storage

## Processing Examples

### Basic Processing

```bash
oceanstream process geotrack convert \
  --provider saildrone \
  --input-source ./sd1030_tpos_2023_7ef2_e8f7_98f9.csv \
  --output-dir ./output \
  --campaign-id sd1030_tpos_2023
```

### Directory Processing

```bash
oceanstream process geotrack convert \
  --provider saildrone \
  --input-source ./saildrone_data/ \
  --output-dir ./output \
  --campaign-id tpos_mission_2023
```

### With Campaign Metadata

```bash
# Create campaign first
oceanstream campaign create sd1030_tpos_2023 \
  --platform "sd1030:Saildrone Explorer 1030:Saildrone Explorer" \
  --attribution "Saildrone Inc." \
  --license "CC-BY-4.0"

# Process data
oceanstream process geotrack convert \
  --provider saildrone \
  --input-source ./data/ \
  --output-dir ./output \
  --campaign-id sd1030_tpos_2023
```

## Semantic Mappings

Saildrone provider includes 60+ semantic mappings to canonical vocabulary.

### Navigation & Platform Motion (12 mappings)

| Saildrone Column | Canonical Field | Unit | Description |
|------------------|-----------------|------|-------------|
| `SOG` | `speed_over_ground_ms` | m/s | Speed over ground |
| `SOG_FILTERED_MEAN` | `speed_over_ground_filtered_mean_ms` | m/s | Filtered mean speed |
| `COG` | `course_over_ground_deg` | ° | Course over ground |
| `HDG` | `heading_deg` | ° | Platform heading |
| `ROLL_FILTERED_MEAN` | `roll_filtered_mean_deg` | ° | Roll angle mean |
| `PITCH_FILTERED_MEAN` | `pitch_filtered_mean_deg` | ° | Pitch angle mean |
| `WING_HDG_FILTERED_MEAN` | `wing_heading_filtered_mean_deg` | ° | Wing heading |
| `WING_ANGLE` | `wing_angle_deg` | ° | Wing angle |

### Meteorological (15 mappings)

| Saildrone Column | Canonical Field | Unit | Description |
|------------------|-----------------|------|-------------|
| `WIND_SPEED_MEAN` | `wind_speed_mean_ms` | m/s | Wind speed mean |
| `WIND_FROM_MEAN` | `wind_direction_mean_deg` | ° | Wind direction |
| `TEMP_AIR_MEAN` | `air_temperature_mean_c` | °C | Air temperature |
| `RH_MEAN` | `relative_humidity_mean_percent` | % | Relative humidity |
| `BARO_PRES_MEAN` | `barometric_pressure_mean_hpa` | hPa | Barometric pressure |
| `UWND_MEAN` | `wind_u_component_mean_ms` | m/s | Wind U component |
| `VWND_MEAN` | `wind_v_component_mean_ms` | m/s | Wind V component |
| `WWND_MEAN` | `wind_w_component_mean_ms` | m/s | Wind W component |
| `GUST_WND_MEAN` | `wind_gust_mean_ms` | m/s | Wind gust |

### Oceanographic - CTD (10 mappings)

| Saildrone Column | Canonical Field | Unit | Description |
|------------------|-----------------|------|-------------|
| `TEMP_SBE37_MEAN` | `water_temperature_ctd_mean_c` | °C | Water temp from CTD |
| `SAL_SBE37_MEAN` | `salinity_ctd_mean_psu` | PSU | Salinity from CTD |
| `COND_SBE37_MEAN` | `conductivity_ctd_mean_ms_cm` | mS/cm | Conductivity |
| `O2_CONC_SBE37_MEAN` | `oxygen_concentration_mean_umol_l` | μmol/L | Oxygen concentration |
| `O2_SAT_SBE37_MEAN` | `oxygen_saturation_mean_percent` | % | Oxygen saturation |
| `CHLOR_WETLABS_MEAN` | `chlorophyll_fluorescence_mean_ug_l` | μg/L | Chlorophyll fluorescence |

### Radiation & Optical (5 mappings)

| Saildrone Column | Canonical Field | Unit | Description |
|------------------|-----------------|------|-------------|
| `PAR_AIR_MEAN` | `par_air_mean_umol_s_m2` | μmol/(s·m²) | PAR (air) |
| `SW_IRRAD_TOTAL_MEAN` | `shortwave_irradiance_total_mean_w_m2` | W/m² | Total shortwave |
| `SW_IRRAD_DIFFUSE_MEAN` | `shortwave_irradiance_diffuse_mean_w_m2` | W/m² | Diffuse shortwave |

### Waves (2 mappings)

| Saildrone Column | Canonical Field | Unit | Description |
|------------------|-----------------|------|-------------|
| `WAVE_DOMINANT_PERIOD` | `wave_dominant_period_s` | s | Dominant period |
| `WAVE_SIGNIFICANT_HEIGHT` | `wave_significant_height_m` | m | Significant height |

**Complete list**: See `oceanstream/providers/saildrone.py`

## Cross-Provider Queries

Query Saildrone data using canonical field names:

```python
import polars as pl

# Read Saildrone GeoParquet
df = pl.read_parquet("output/sd1030_2023/**/*.parquet")

# Query using canonical names
result = df.filter(
    (pl.col("speed_over_ground_ms") > 2.0) &
    (pl.col("air_temperature_mean_c") > 15.0)
).select([
    "time",
    "latitude",
    "longitude",
    "speed_over_ground_ms",
    "air_temperature_mean_c",
    "wind_speed_mean_ms",
    "water_temperature_ctd_mean_c",
    "salinity_ctd_mean_psu"
])
```

## Platform Types

### Saildrone Explorer (sd1000-sd1999)

**Characteristics**:
- Length: 7 meters
- Range: Coastal to open ocean
- Duration: Up to 12 months
- Sensors: Met, CTD, ADCP, echosounders

**Platform IDs**: `sd1000` to `sd1999`

### Saildrone Surveyor (sd2000+)

**Characteristics**:
- Length: 20 meters
- Range: Open ocean, high seas
- Duration: Extended missions
- Sensors: Advanced met, CTD, multibeam, ADCP

**Platform IDs**: `sd2000` and above

## Syntactic Normalization

For columns without semantic mappings, Saildrone provider applies syntactic normalization:

**Rules**:
- Convert to snake_case
- Replace non-alphanumeric with underscores
- Collapse multiple underscores
- Strip leading digits

**Examples**:
- `CustomField` → `custom_field`
- `UNKNOWN_SENSOR_123` → `unknown_sensor`
- `some-value` → `some_value`

## Provider Metadata

Saildrone provider embeds this metadata in Parquet files:

```json
{
  "oceanstream:provider": {
    "name": "saildrone",
    "columns": ["time", "latitude", "longitude", "SOG", "COG", "TEMP_AIR_MEAN", ...]
  }
}
```

## Best Practices

### 1. Use Campaign IDs Matching ERDDAP

Align with NOAA PMEL ERDDAP dataset IDs:

```bash
# ERDDAP dataset: sd1030_tpos_2023
oceanstream campaign create sd1030_tpos_2023 \
  --platform "sd1030"
```

### 2. Process Complete Missions

Process entire mission datasets together:

```bash
# All files for one mission
oceanstream process geotrack convert \
  --provider saildrone \
  --input-source ./sd1030_tpos_2023_*.csv \
  --campaign-id sd1030_tpos_2023
```

### 3. Preserve Original Column Names

OceanStream preserves original Saildrone column names alongside aliases:

```python
# Both work:
df.select("SOG")  # Original
df.select("speed_over_ground_ms")  # Canonical alias
```

### 4. Use Canonical Names for Multi-Provider Analysis

When combining with other providers, use canonical field names:

```python
# Works across Saildrone, R2R, Wave Glider, etc.
combined_df.filter(pl.col("speed_over_ground_ms") > 2.0)
```

## Python API

```python
from oceanstream.providers import get_provider
import pandas as pd

# Get Saildrone provider
provider = get_provider("saildrone")

# Extract platform ID
platform_id = provider.identify_platform("sd1030_tpos_2023_7ef2_e8f7_98f9.csv")
print(platform_id)  # "sd1030_tpos_2023"

# Read data
df = pd.read_csv("sd1030_data.csv")

# Enrich dataframe
enriched = provider.enrich_dataframe(df)

# Get semantic mappings
aliases = provider.alias_mapping(df.columns)
print(aliases["SOG"])  # "speed_over_ground_ms"
print(aliases["TEMP_AIR_MEAN"])  # "air_temperature_mean_c"

# Check module support
assert provider.supports_module("geotrack")
```

## Troubleshooting

### Platform ID Not Detected

**Problem**: `identify_platform()` returns `None`

**Solution**: Verify filename matches expected pattern

```python
provider = get_provider("saildrone")

# Valid formats
assert provider.identify_platform("sd1030_tpos_2023_7ef2.csv") == "sd1030_tpos_2023"

# Invalid formats
assert provider.identify_platform("saildrone_data.csv") is None
assert provider.identify_platform("sd_mission.csv") is None
```

### Missing Semantic Mappings

**Problem**: Column not in canonical vocabulary

**Solution**: Falls back to syntactic normalization

```python
# Custom column with no semantic mapping
aliases = provider.alias_mapping(["CUSTOM_SENSOR_VALUE"])
print(aliases["CUSTOM_SENSOR_VALUE"])  # "custom_sensor_value" (syntactic)
```

### Time Parsing Errors

**Problem**: Time column not recognized as datetime

**Solution**: Provider auto-converts time columns:

```python
enriched = provider.enrich_dataframe(df)
assert pd.api.types.is_datetime64_any_dtype(enriched["time"])
```

## Resources

- **Saildrone Website**: https://www.saildrone.com/
- **NOAA PMEL ERDDAP**: https://data.pmel.noaa.gov/pmel/erddap/
- **Platform Specs**: https://www.saildrone.com/technology
- **Data Documentation**: ERDDAP dataset info pages

## Next Steps

- [Provider Overview](overview.md) - Understand provider system
- [R2R Provider](r2r.md) - Research vessel data
<!-- TODO: Add these guides
- **Semantic Mappings** - Cross-provider interoperability
- **Creating Providers** - Build custom providers
-->
