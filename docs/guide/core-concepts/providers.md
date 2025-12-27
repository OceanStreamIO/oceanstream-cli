# Data Providers

Data providers in OceanStream encapsulate source-specific logic for different oceanographic data sources. Each provider handles the unique characteristics of data from organizations like Saildrone, R2R (Rolling Deck to Repository), and other platforms.

## What is a Provider?

A **provider** is a plugin that knows how to:

- **Parse filenames**: Extract platform IDs and metadata
- **Normalize columns**: Map source-specific names to canonical vocabulary
- **Handle units**: Extract and standardize measurement units
- **Generate aliases**: Create cross-provider field names
- **Add metadata**: Embed provider information in output

**Key principle**: Each data source has unique conventions—providers abstract these differences.

## Available Providers

| Provider | Organization | Platform Type | Status |
|----------|--------------|---------------|--------|
| [Saildrone](../features/data-providers/saildrone.md) | Saildrone Inc. | Autonomous surface vehicles | ✅ Production |
| [R2R](../features/data-providers/r2r.md) | NSF R2R Program | Research vessels | ✅ Production |

See [Data Providers](../features/data-providers/overview.md) for complete documentation.

## Quick Start

### Using a Provider

```bash
# Saildrone data
oceanstream process geotrack convert \
  --provider saildrone \
  --input-source ./sd1030_tpos_2023.csv \
  --output-dir ./output

# R2R data
oceanstream process geotrack convert \
  --provider r2r \
  --input-source ./FK161229_r2rnav.geocsv \
  --output-dir ./output
```

### Listing Providers

```bash
oceanstream providers
```

**Output**:
```
Available providers:
  - r2r
  - saildrone
```

## Semantic Mappings

### What are Semantic Mappings?

Semantic mappings translate provider-specific abbreviations and conventions into a **canonical vocabulary** with explicit units. This enables cross-provider interoperability.

**Problem**: Different providers use different names for the same measurement:
- Saildrone: `SOG` (Speed Over Ground)
- R2R: `speed_made_good`
- Wave Glider: `speed_gnd`

**Solution**: All map to canonical field: `speed_over_ground_ms`

### Canonical Vocabulary

Canonical field names follow this pattern:
```
{measurement}_{statistic}_{unit}
```

**Examples**:
- `air_temperature_mean_c` - Air temperature mean in Celsius
- `wind_speed_stddev_ms` - Wind speed standard deviation in m/s
- `salinity_ctd_mean_psu` - Salinity from CTD, mean in PSU

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
| `_ms_cm` | mS/cm | MilliSiemens/cm (conductivity) |
| `_umol_l` | μmol/L | Micromoles per liter |
| `_ug_l` | μg/L | Micrograms per liter |
| `_umol_s_m2` | μmol/(s·m²) | PAR units |
| `_w_m2` | W/m² | Watts per square meter |

### Saildrone Semantic Mappings

**Navigation & Motion** (12 mappings):
- `SOG` → `speed_over_ground_ms`
- `COG` → `course_over_ground_deg`
- `HDG` → `heading_deg`
- `ROLL_FILTERED_MEAN` → `roll_filtered_mean_deg`
- `PITCH_FILTERED_MEAN` → `pitch_filtered_mean_deg`

**Meteorological** (15 mappings):
- `WIND_SPEED_MEAN` → `wind_speed_mean_ms`
- `WIND_FROM_MEAN` → `wind_direction_mean_deg`
- `TEMP_AIR_MEAN` → `air_temperature_mean_c`
- `RH_MEAN` → `relative_humidity_mean_percent`
- `BARO_PRES_MEAN` → `barometric_pressure_mean_hpa`
- `UWND_MEAN` → `wind_u_component_mean_ms`
- `VWND_MEAN` → `wind_v_component_mean_ms`

**Oceanographic** (10 mappings):
- `TEMP_SBE37_MEAN` → `water_temperature_ctd_mean_c`
- `SAL_SBE37_MEAN` → `salinity_ctd_mean_psu`
- `COND_SBE37_MEAN` → `conductivity_ctd_mean_ms_cm`
- `O2_CONC_SBE37_MEAN` → `oxygen_concentration_mean_umol_l`
- `O2_SAT_SBE37_MEAN` → `oxygen_saturation_mean_percent`
- `CHLOR_WETLABS_MEAN` → `chlorophyll_fluorescence_mean_ug_l`

**Radiation & Optical** (5 mappings):
- `PAR_AIR_MEAN` → `par_air_mean_umol_s_m2`
- `SW_IRRAD_TOTAL_MEAN` → `shortwave_irradiance_total_mean_w_m2`
- `SW_IRRAD_DIFFUSE_MEAN` → `shortwave_irradiance_diffuse_mean_w_m2`

**Waves** (2 mappings):
- `WAVE_DOMINANT_PERIOD` → `wave_dominant_period_s`
- `WAVE_SIGNIFICANT_HEIGHT` → `wave_significant_height_m`

**Complete list**: 60+ mappings in `oceanstream/providers/saildrone.py`

## Key Concepts

### Semantic Mappings

Providers translate source-specific column names to a **canonical vocabulary**:

**Example**:
- Saildrone `SOG` → `speed_over_ground_ms`
- R2R `speed_made_good` → `speed_over_ground`
- Both enable cross-provider queries using standard field names

**Canonical format**: `{measurement}_{statistic}_{unit}`
- `air_temperature_mean_c` - Air temp mean in Celsius
- `wind_speed_stddev_ms` - Wind speed std dev in m/s
- `salinity_ctd_mean_psu` - Salinity from CTD in PSU
df = pl.read_parquet("output/FK161229/**/*.parquet")
### Provider Selection

```bash
# Explicit (recommended)
oceanstream process geotrack convert \
  --provider saildrone \
  --input-source ./data/

# Auto-detection (based on filename/format)
oceanstream process geotrack convert \
  --input-source ./data/
```
- Multi-sensor datasets (nav, CTD, ADCP, echosounder)
- NSF-funded cruise data

⚠️ **Considerations**:
- Requires R2R filename conventions
- Works with bag-info.txt metadata
- Supports unpacked archives

### When to Create a Custom Provider

Consider creating a new provider when:

1. **New data source**: Working with a new organization's data
2. **Unique conventions**: Source has specific naming or formatting
3. **Special processing**: Need custom enrichment or validation
4. **Reusability**: Will process multiple datasets from same source

## Creating a Custom Provider

### Step 1: Analyze Data Source

Collect sample files and document:

**Filename patterns**:
```
waveglider_123_pacific_2024_001.csv
waveglider_124_pacific_2024_002.csv
```

**Column naming**:
```
timestamp, lat_deg, lon_deg, speed_gnd, course_gnd, air_temp_avg
```

**Metadata format**:
- Units in header row?
- Separate metadata file?
- Embedded JSON/XML?

### Step 2: Implement Provider Class
## Cross-Provider Queries

Semantic mappings enable querying data from multiple providers using standard field names:

```python
import polars as pl

# Combine data from different providers
saildrone_df = pl.read_parquet("output/sd1030/**/*.parquet")
r2r_df = pl.read_parquet("output/FK161229/**/*.parquet")

combined = pl.concat([saildrone_df, r2r_df])

# Query using canonical field names
result = combined.filter(
    pl.col("speed_over_ground_ms") > 2.0
).select([
    "platform_id",
    "time",
    "speed_over_ground_ms",
    "air_temperature_mean_c"
])
```

```python
import pytest
from oceanstream.providers import get_provider

def test_waveglider_platform_identification():
    provider = get_provider("waveglider")
    platform_id = provider.identify_platform("waveglider_123_pacific_2024_001.csv")
    assert platform_id == "waveglider_123_pacific_2024"

def test_waveglider_semantic_mappings():
    provider = get_provider("waveglider")
    aliases = provider.alias_mapping(["speed_gnd", "course_gnd"])
    
    assert aliases["speed_gnd"] == "speed_over_ground_ms"
    assert aliases["course_gnd"] == "course_over_ground_deg"

def test_waveglider_supports_geotrack():
    provider = get_provider("waveglider")
    assert provider.supports_module("geotrack")
```

### Step 5: Document Provider

Create `docs-site/guide/examples/waveglider-data.md`:

```markdown
# Processing Wave Glider Data

## Overview
Wave Gliders are wave-propelled autonomous surface vehicles...

## Data Format
- **Filename**: `waveglider_{id}_{mission}_{year}_{sequence}.csv`
- **Columns**: timestamp, lat_deg, lon_deg, speed_gnd, course_gnd, air_temp_avg

## Processing Example
\`\`\`bash
oceanstream process geotrack convert \
  --provider waveglider \
  --input-source ./waveglider_data/ \
  --output-dir ./output \
  --campaign-id WG123_2024
\`\`\`

## Semantic Mappings
| Wave Glider | Canonical | Unit |
|-------------|-----------|------|
| speed_gnd | speed_over_ground_ms | m/s |
| course_gnd | course_over_ground_deg | ° |
| air_temp_avg | air_temperature_mean_c | °C |
```

## Best Practices

### 1. Always Specify Provider

**Explicit is better than implicit**:
```bash
# Good
oceanstream process geotrack convert --provider saildrone --input-source ./data/

# Avoid (relies on auto-detection)
oceanstream process geotrack convert --input-source ./data/
```

### 2. Validate Semantic Mappings

When creating a provider, test all semantic mappings:

```python
def test_all_semantic_mappings():
    provider = get_provider("mysite")
## Provider Architecture

Every provider implements the `ProviderBase` protocol:

```python
class ProviderBase(Protocol):
    name: str
    supported_modules: list[ProcessingModule]
    
    def identify_platform(filename: str) -> str | None
    def enrich_dataframe(df: pd.DataFrame) -> pd.DataFrame
    def units_mapping(header, units_row) -> dict[str, Any]
    def alias_mapping(columns) -> dict[str, str]
    def parquet_metadata(df: pd.DataFrame) -> dict[str, Any]
    def supports_module(module: ProcessingModule) -> bool
```

**Processing modules**:
- `geotrack` - Navigation and trajectory data ✅
- `echodata` - Echosounder data (planned)
- `multibeam` - Multibeam sonar (planned)
- `adcp` - ADCP current profiles (planned)## Python API

```python
from oceanstream.providers import get_provider, list_providers

# List providers
providers = list_providers()
print(providers)  # ["r2r", "saildrone"]

# Get specific provider
provider = get_provider("saildrone")
platform_id = provider.identify_platform("sd1030_tpos_2023.csv")
print(platform_id)  # "sd1030_tpos_2023"

# Get semantic mappings
aliases = provider.alias_mapping(["SOG", "COG"])
print(aliases["SOG"])  # "speed_over_ground_ms"
```## Next Steps

- [Data Providers Overview](../features/data-providers/overview.md) - Complete provider documentation
- [Saildrone Provider](../features/data-providers/saildrone.md) - Detailed Saildrone guide
- [R2R Provider](../features/data-providers/r2r.md) - Research vessel data
<!-- TODO: Add creating-providers guide -->
- **Creating Providers** - Build custom providers
- [Geotrack Processing](geotrack-convert-overview.md) - Navigation data pipeline