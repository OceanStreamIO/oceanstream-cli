# R2R Provider

The R2R (Rolling Deck to Repository) provider handles data from NSF-funded research vessel cruises, supporting multi-sensor datasets in GeoCSV format with rich metadata.

!!! tip "Looking for examples?"
    See the [R2R Multi-Sensor Processing](../../examples/r2r-multi-sensor.md) guide for comprehensive examples of CTD, winch, navigation, and auxiliary sensor processing.

## Overview

**Organization**: NSF Rolling Deck to Repository program  
**Platform Type**: Research vessels  
**Data Format**: GeoCSV with metadata headers  
**Supported Modules**: geotrack  
**Status**: ✅ Production

## Data Characteristics

### Filename Format

```
{CruiseID}_{EventID}_{InstrumentType}.geocsv
```

**Examples**:
- `FK161229_607994_r2rnav.geocsv` - R/V Falkor navigation
- `AT42-10_000123_ctd.geocsv` - Atlantis CTD data
- `NBP1402_001_adcp.geocsv` - Palmer ADCP data

**Platform ID extraction**:
- Input: `FK161229_607994_r2rnav.geocsv`
- Output: `FK161229`

### File Format

R2R uses **GeoCSV** format with metadata headers:

```csv
# dataset: R/V Falkor Navigation
# cruise_id: FK161229
# vessel: R/V Falkor
# delimiter: ,
# field_unit[iso_time]: ISO_8601
# field_unit[ship_latitude]: degree_north
# field_unit[ship_longitude]: degree_east
iso_time,ship_latitude,ship_longitude,ship_depth,speed_made_good,course_made_good
2016-12-29T00:00:00Z,-43.2156,-170.4321,4521.2,5.2,045.3
2016-12-29T00:00:01Z,-43.2157,-170.4322,4521.5,5.3,045.4
```

### Metadata Files

R2R archives include additional metadata:

**bag-info.txt**:
```
Cruise-Id: FK161229
Vessel-Name: R/V Falkor
Chief-Scientist: Dr. Jane Smith
Start-Date: 2016-12-29
End-Date: 2017-01-20
```

**file-info.json**:
```json
{
  "filename": "FK161229_607994_r2rnav.geocsv",
  "device_make": "Seapath",
  "device_model": "330+",
  "sensor_type": "gnss_nav"
}
```

## Processing Examples

### Basic Processing

```bash
oceanstream process geotrack convert \
  --provider r2r \
  --input-source ./FK161229_607994_r2rnav.geocsv \
  --output-dir ./output \
  --campaign-id FK161229
```

### Directory Processing

```bash
oceanstream process geotrack convert \
  --provider r2r \
  --input-source ./r2r_data/ \
  --output-dir ./output \
  --campaign-id FK161229
```

### With Campaign Metadata

```bash
# Create campaign
oceanstream campaign create FK161229 \
  --platform-id "R/V Falkor" \
  --platform-name "Research Vessel Falkor" \
  --platform-type "Research Vessel" \
  --start-date "2016-12-29" \
  --end-date "2017-01-20" \
  --attribution "Schmidt Ocean Institute" \
  --license "CC-BY-4.0"

# Process data
oceanstream process geotrack convert \
  --provider r2r \
  --input-source ./FK161229_*.geocsv \
  --output-dir ./output \
  --campaign-id FK161229
```

### Processing Archives

R2R data often comes as compressed archives:

```bash
# Extract archive first
tar -xzf FK161229_r2rnav.tar.gz

# Process extracted data
oceanstream process geotrack convert \
  --provider r2r \
  --input-source ./FK161229_r2rnav/ \
  --output-dir ./output \
  --campaign-id FK161229
```

## Column Mappings

R2R provider includes standard column mappings:

### Position & Navigation

| R2R Column | Canonical Field | Unit | Description |
|------------|-----------------|------|-------------|
| `ship_longitude` | `longitude` | degree_east | Ship longitude |
| `ship_latitude` | `latitude` | degree_north | Ship latitude |
| `ship_depth` | `depth` | meters | Water depth |
| `iso_time` | `time` | ISO_8601 | Timestamp |
| `speed_made_good` | `speed_over_ground` | m/s | Speed over ground |
| `course_made_good` | `course_over_ground` | degree | Course over ground |

### GPS Quality

| R2R Column | Canonical Field | Unit | Description |
|------------|-----------------|------|-------------|
| `nmea_quality` | `gps_quality` | - | NMEA quality indicator |
| `nsv` | `num_satellites` | - | Number of satellites |
| `hdop` | `horizontal_dilution` | - | Horizontal dilution |
| `antenna_height` | `gps_antenna_height` | meters | Antenna height |

### Standard Units

| Field | R2R Unit | Description |
|-------|----------|-------------|
| longitude | `degree_east` | Longitude |
| latitude | `degree_north` | Latitude |
| depth | `meters` | Depth/distance |
| time | `ISO_8601` | Timestamps |
| speed | `meters_per_second` | Velocity |
| course | `degree` | Direction |

## Cross-Provider Queries

Query R2R data using canonical field names:

```python
import polars as pl

# Read R2R GeoParquet
df = pl.read_parquet("output/FK161229/**/*.parquet")

# Query using canonical names
result = df.filter(
    pl.col("speed_over_ground") > 2.0
).select([
    "time",
    "latitude",
    "longitude",
    "speed_over_ground",
    "course_over_ground",
    "depth"
])
```

## Research Vessels

R2R supports data from numerous research vessels:

### UNOLS Fleet

**Major vessels**:
- R/V Atlantis (AT)
- R/V Roger Revelle (RR)
- R/V Thomas G. Thompson (TN)
- R/V Sikuliaq (SKQ)
- R/V Neil Armstrong (AR)

### International Vessels

- R/V Falkor (FK) - Schmidt Ocean Institute
- RVIB Nathaniel B. Palmer (NBP) - Antarctic research
- R/V Sally Ride (SR) - Scripps Institution

**Cruise ID format**: `{Vessel}{YearMonth}{Leg}`
- Example: `FK161229` = R/V Falkor, December 2016, leg 29

## Metadata Extraction

R2R provider automatically extracts metadata from headers:

### GeoCSV Headers

```python
# Automatically parsed:
# # cruise_id: FK161229
# # vessel: R/V Falkor
# # field_unit[latitude]: degree_north

metadata = {
    "cruise_id": "FK161229",
    "vessel": "R/V Falkor",
    "units": {
        "latitude": "degree_north",
        "longitude": "degree_east"
    }
}
```

### Archive Metadata

When processing archives, R2R provider reads:

**bag-info.txt** → Campaign metadata:
- `Cruise-Id` → `campaign_id`
- `Vessel-Name` → `platform_name`
- `Start-Date` → `start_date`
- `End-Date` → `end_date`

**file-info.json** → Sensor information:
- `device_make` → Sensor manufacturer
- `device_model` → Sensor model
- `sensor_type` → Sensor category

## Multi-Sensor Support

R2R archives often contain multiple sensor types:

### Navigation Data
- **Files**: `*_r2rnav.geocsv`
- **Contains**: Position, speed, course, heading
- **Sensors**: GPS, gyrocompass
- **Status**: ✅ Full support

### CTD Data
- **Files**: `*_ctd.tar.gz` (raw hex), `*_ctd.geocsv`
- **Contains**: Temperature, salinity, conductivity, pressure, oxygen
- **Sensors**: SBE 911plus/917plus
- **Status**: ✅ Full support (hex + GeoCSV)

### Winch Telemetry
- **Files**: `*_winch.tar.gz`
- **Contains**: Wire payout, tension, speed, drum turns
- **Sensors**: LCI-90i winch monitoring system
- **Status**: ✅ Full support

### Surface Sound Velocity
- **Files**: `*_ssv.tar.gz`
- **Contains**: Sound velocity measurements
- **Sensors**: Valeport MiniSVS
- **Status**: ✅ Basic support

### Fluorometer
- **Files**: `*_fluorometer.tar.gz`
- **Contains**: Chlorophyll fluorescence channels
- **Sensors**: WET Labs ECO-FLNTU
- **Status**: ✅ Basic support

### ADCP Data
- **Files**: `*_adcp.geocsv`
- **Contains**: Current velocity profiles
- **Sensors**: Acoustic Doppler Current Profilers
- **Status**: 🟡 Planned

### Echosounder Data
- **Files**: `*_mb.geocsv`, `*_sb.geocsv`
- **Contains**: Bathymetry, backscatter
- **Sensors**: Multibeam, single-beam echosounders
- **Status**: 🟡 Planned

## Provider Metadata

R2R provider embeds this metadata in Parquet files:

```json
{
  "oceanstream:provider": {
    "name": "r2r",
    "columns": ["time", "latitude", "longitude", "depth", "speed_made_good"]
  }
}
```

## Best Practices

### 1. Use Cruise IDs as Campaign IDs

Align campaign IDs with R2R cruise identifiers:

```bash
# R2R cruise: FK161229
oceanstream campaign create FK161229 \
  --platform-id "R/V Falkor"
```

### 2. Process Complete Cruises

Process all sensor data from a cruise together:

```bash
# All navigation data
oceanstream process geotrack convert \
  --provider r2r \
  --input-source ./FK161229_*_r2rnav.geocsv \
  --campaign-id FK161229
```

### 3. Preserve Metadata Files

Keep bag-info.txt and file-info.json alongside data:

```
FK161229/
├── bag-info.txt
├── file-info.json
├── FK161229_607994_r2rnav.geocsv
├── FK161229_607995_r2rnav.geocsv
└── FK161229_607996_r2rnav.geocsv
```

### 4. Combine with Other Providers

R2R canonical mappings enable cross-provider queries:

```python
# Combine R2R with Saildrone
r2r_df = pl.read_parquet("output/FK161229/**/*.parquet")
saildrone_df = pl.read_parquet("output/sd1030_2023/**/*.parquet")

combined = pl.concat([r2r_df, saildrone_df])
```

## Python API

```python
from oceanstream.providers import get_provider

# Get R2R provider
provider = get_provider("r2r")

# Extract cruise ID
cruise_id = provider.identify_platform("FK161229_607994_r2rnav.geocsv")
print(cruise_id)  # "FK161229"

# Get column mappings
aliases = provider.alias_mapping([
    "ship_longitude",
    "ship_latitude",
    "iso_time",
    "speed_made_good"
])

print(aliases["ship_longitude"])  # "longitude"
print(aliases["iso_time"])  # "time"
print(aliases["speed_made_good"])  # "speed_over_ground"

# Check module support
assert provider.supports_module("geotrack")
```

## Troubleshooting

### Cruise ID Not Detected

**Problem**: `identify_platform()` returns `None`

**Solution**: Verify filename matches R2R pattern

```python
provider = get_provider("r2r")

# Valid formats
assert provider.identify_platform("FK161229_607994_r2rnav.geocsv") == "FK161229"
assert provider.identify_platform("AT42-10_123_ctd.geocsv") == "AT42-10"

# Invalid formats
assert provider.identify_platform("navigation_data.csv") is None
```

### Metadata Headers Not Parsed

**Problem**: GeoCSV headers not recognized

**Solution**: Ensure headers start with `#` and follow GeoCSV format:

```csv
# Valid GeoCSV header
# cruise_id: FK161229
# delimiter: ,
```

### Archive Extraction Issues

**Problem**: Can't find data files in archive

**Solution**: Check archive structure matches R2R conventions:

```bash
# Expected structure
FK161229_r2rnav/
├── bag-info.txt
├── file-info.json
└── data/
    └── FK161229_607994_r2rnav.geocsv
```

## Resources

- **R2R Website**: https://www.rvdata.us/
- **Data Catalog**: https://www.rvdata.us/catalog
- **File Formats**: https://www.rvdata.us/about/formats
- **UNOLS Fleet**: https://www.unols.org/fleet

## Next Steps

- [Provider Overview](overview.md) - Understand provider system
- [Saildrone Provider](saildrone.md) - Autonomous surface vehicles
<!-- TODO: Add these guides
- **Semantic Mappings** - Cross-provider interoperability
- **Creating Providers** - Build custom providers
-->
