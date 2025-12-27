# Data Provider System

OceanStream's provider system enables seamless processing of oceanographic data from diverse sources. Each provider encapsulates source-specific conventions—filename patterns, column naming, units, metadata—allowing you to process heterogeneous datasets with a unified interface.

## What is a Provider?

A **provider** is a plugin that knows how to handle data from a specific organization or platform:

- **Parse filenames**: Extract platform IDs and metadata
- **Normalize columns**: Map source-specific names to canonical vocabulary
- **Handle units**: Extract and standardize measurement units
- **Generate aliases**: Create human-readable field names for cross-provider queries
- **Add metadata**: Embed provider information in output files

## Available Providers

| Provider | Organization | Platform Type | Data Format | Status |
|----------|--------------|---------------|-------------|--------|
| [Saildrone](saildrone.md) | Saildrone Inc. | Autonomous surface vehicles | CSV | ✅ Production |
| [R2R](r2r.md) | NSF Rolling Deck to Repository | Research vessels | GeoCSV | ✅ Production |

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

## Key Features

### 1. Semantic Mappings

Providers translate source-specific column names to a **canonical vocabulary**:

**Problem**: Different names for the same measurement:
- Saildrone: `SOG` (Speed Over Ground)
- R2R: `speed_made_good`

**Solution**: Both map to: `speed_over_ground_ms`

### 2. Cross-Provider Queries

Query data from multiple providers using canonical field names:

```python
import polars as pl

# Read data from different providers
saildrone_df = pl.read_parquet("output/sd1030/**/*.parquet")
r2r_df = pl.read_parquet("output/FK161229/**/*.parquet")

# Combine using canonical fields
combined = pl.concat([saildrone_df, r2r_df])

# Query works across all providers
result = combined.filter(
    pl.col("speed_over_ground_ms") > 2.0
).select([
    "platform_id",
    "time",
    "speed_over_ground_ms",
    "air_temperature_mean_c"
])
```

### 3. Canonical Vocabulary

Field names follow a standard pattern:
```
{measurement}_{statistic}_{unit}
```

**Examples**:
- `air_temperature_mean_c` - Air temperature mean in Celsius
- `wind_speed_stddev_ms` - Wind speed std dev in m/s
- `salinity_ctd_mean_psu` - Salinity from CTD in PSU

### 4. Provider Metadata

Each provider embeds its information in output files:

```json
{
  "oceanstream:provider": {
    "name": "saildrone",
    "columns": ["time", "latitude", "longitude", "SOG", "COG"]
  }
}
```

## Provider Architecture

Every provider implements the `ProviderBase` protocol:

```python
class ProviderBase(Protocol):
    name: str
    supported_modules: list[ProcessingModule]
    
    def identify_platform(self, filename: str) -> str | None:
        """Extract platform ID from filename."""
        
    def enrich_dataframe(self, df: pd.DataFrame) -> pd.DataFrame:
        """Apply provider-specific transformations."""
        
    def units_mapping(self, header, units_row) -> dict[str, Any]:
        """Extract units for each column."""
        
    def alias_mapping(self, columns) -> dict[str, str]:
        """Generate canonical field names."""
        
    def parquet_metadata(self, df: pd.DataFrame) -> dict[str, Any]:
        """Generate provider-specific metadata."""
        
    def supports_module(self, module: ProcessingModule) -> bool:
        """Check if provider supports processing module."""
```

## When to Use Each Provider

### Saildrone

✅ **Use for**:
- Saildrone Explorer/Surveyor data
- CSV files with Saildrone conventions
- Data from NOAA PMEL ERDDAP
- Platform IDs: `sd1030`, `sd1033`, etc.

**Example**: `sd1030_tpos_2023_7ef2_e8f7_98f9.csv`

### R2R

✅ **Use for**:
- Research vessel data from R2R archives
- GeoCSV format with metadata headers
- Multi-sensor datasets (nav, CTD, ADCP)
- NSF-funded cruise data

**Example**: `FK161229_607994_r2rnav.geocsv`

## Creating Custom Providers

<!-- TODO: Add guide for creating custom providers -->
For implementing custom providers for new data sources, you'll need to subclass `DataProvider` and implement required methods.

**When to create a provider**:
- New data source with unique conventions
- Multiple datasets from same organization
- Need custom enrichment or validation
- Want cross-provider interoperability

## Provider Selection

### Explicit (Recommended)

```bash
oceanstream process geotrack convert \
  --provider saildrone \
  --input-source ./data/
```

### Auto-Detection

OceanStream can detect providers based on:
- Filename patterns
- File format (CSV vs GeoCSV)
- Column names

**Note**: Explicit provider selection is more reliable.

## Python API

```python
from oceanstream.providers import get_provider, list_providers

# List all providers
providers = list_providers()
print(providers)  # ["r2r", "saildrone"]

# Get specific provider
provider = get_provider("saildrone")
print(provider.name)  # "saildrone"

# Use provider
platform_id = provider.identify_platform("sd1030_tpos_2023.csv")
print(platform_id)  # "sd1030_tpos_2023"

# Get semantic mappings
aliases = provider.alias_mapping(["SOG", "COG"])
print(aliases["SOG"])  # "speed_over_ground_ms"
```

## Future Providers

**Planned**:
- IMOS (Australia)
- BOOS (Baltic)
- NANOOS (Pacific Northwest)
- GCOOS (Gulf of Mexico)
- Generic CSV (configurable via YAML)

## Next Steps

- [Saildrone Provider](saildrone.md) - Detailed Saildrone documentation
- [R2R Provider](r2r.md) - Rolling Deck to Repository guide
<!-- TODO: Add these guides
- **Creating Providers** - Build custom providers
- **Semantic Mappings** - Cross-provider interoperability
-->
