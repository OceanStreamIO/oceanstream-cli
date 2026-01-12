# Geotrack Convert: Comprehensive Guide

## Overview

The `oceanstream process geotrack convert` command is the **core data processing pipeline** of OceanStream. It converts raw CSV or NMEA sensor data into cloud-optimized, spatially-indexed GeoParquet datasets with rich STAC metadata and optional vector tiles.

This command handles everything from reading diverse data formats to enriching them with semantic metadata (CF Standard Names), organizing them spatially, and generating standardized metadata for discovery and interoperability.

## What It Does

```
Raw Data (CSV/NMEA)
         ↓
   [Read & Parse]
         ↓
   [Enrich & Normalize]
         ↓
   [Semantic Mapping] (CF Standard Names)
         ↓
   [Spatial Binning] (1° × 1° tiles)
         ↓
   [GeoParquet Output]
         ↓
   [STAC Metadata]
         ↓
   [Optional: PMTiles]
```

### Key Capabilities

- **Input Flexibility**: Processes single files, directories, CSV, GeoCSV, or NMEA raw data
- **Campaign-Based Organization**: All outputs organized by campaign identifier
- **Spatial Indexing**: Automatic binning into 1° × 1° latitude/longitude tiles
- **Semantic Enrichment**: Maps sensor variables to CF Standard Names with fuzzy matching
- **STAC Metadata**: Generates collection and item JSON for catalog integration
- **Vector Tiles**: Optional PMTiles generation for web visualization
- **Incremental Processing**: Append new data with automatic deduplication
- **Multi-Platform Support**: Handles multiple platforms within the same campaign

## Architecture

### Command Structure

```bash
oceanstream [global-options] \
  process \
    [--provider <provider-type>] \
    geotrack \
      convert \
        [input-options] \
        [output-options] \
        [processing-options] \
        [metadata-options] \
        [pmtiles-options]
```

**Nested Command Hierarchy:**
- `oceanstream`: Main CLI entry point
- `process`: Data processing command group (with `--provider` option)
- `geotrack`: Geotrack-specific processing
- `convert`: CSV/NMEA to GeoParquet conversion

### Data Flow

```
┌─────────────────────┐
│  Input Source       │
│  • CSV files        │
│  • GeoCSV files     │
│  • NMEA .txt files  │
│  • Directories      │
└──────────┬──────────┘
           ↓
┌─────────────────────┐
│  File Scanning      │
│  • Detect formats   │
│  • Convert NMEA     │
│  • Validate files   │
└──────────┬──────────┘
           ↓
┌─────────────────────┐
│  Data Reading       │
│  • Parse CSV        │
│  • Extract metadata │
│  • Validate schema  │
└──────────┬──────────┘
           ↓
┌─────────────────────┐
│  Enrichment         │
│  • Provider-specific│
│  • Add platform_id  │
│  • Add campaign_id  │
│  • Interpolation    │
└──────────┬──────────┘
           ↓
┌─────────────────────┐
│  Semantic Mapping   │
│  • CF Standard Names│
│  • Alias matching   │
│  • Units assignment │
└──────────┬──────────┘
           ↓
┌─────────────────────┐
│  Sensor Detection   │
│  • Identify sensors │
│  • Platform info    │
│  • Validation       │
└──────────┬──────────┘
           ↓
┌─────────────────────┐
│  Spatial Binning    │
│  • Calculate bins   │
│  • Partition data   │
│  • Hive structure   │
└──────────┬──────────┘
           ↓
┌─────────────────────┐
│  Campaign Tracking  │
│  • Metadata storage │
│  • File tracking    │
│  • Deduplication    │
└──────────┬──────────┘
           ↓
┌─────────────────────┐
│  Output Generation  │
│  • Write GeoParquet │
│  • Generate STAC    │
│  • Create PMTiles   │
└─────────────────────┘
```

## Input Formats

### CSV Files (.csv)

Standard comma-separated value files with header row.

**Required Columns:**
- `time` (or `TIME`, `date`, `datetime`, etc.)
- `latitude` / `longitude` (or `lat` / `lon`)

**Optional Columns:**
- `platform_id` / `trajectory`
- Any number of sensor measurement columns

**Example:**
```csv
time,latitude,longitude,temperature,salinity
2023-06-22T00:00:00Z,5.5,-170.0,28.5,35.1
2023-06-22T00:01:00Z,5.501,-170.0,28.4,35.1
```

### GeoCSV Files (.geocsv)

CSV files with metadata header prefixed by `# ` following the [R2R GeoCSV specification](http://www.rvdata.us/operators/spec).

**Metadata Header:**
```csv
# delimiter: ,
# cruise_id: FK161229
# ship_name: R/V Falkor
# cruise_start_date: 2016-12-29
# cruise_end_date: 2017-01-24
# column[time]: type: datetime
# column[time]: units: ISO8601
# column[latitude]: units: degrees_north
# column[longitude]: units: degrees_east
time,latitude,longitude,ship_latitude,ship_longitude
2016-12-29T00:00:00Z,,,18.5,-155.5
```

**Advantages:**
- Self-documenting (metadata embedded in file)
- Campaign/cruise ID from header
- Column-specific metadata (units, types)
- Provenance information

### NMEA Raw Data (.txt)

Raw NMEA 0183 sentences from GNSS receivers.

**Supported Sentences:**
- `$GPGGA` / `$GNGGA`: GPS Fix Data
- `$GPRMC` / `$GNRMC`: Recommended Minimum Navigation
- `$GPGNS` / `$GNGNS`: GNSS Fix Data
- `$GPVTG` / `$GNVTG`: Track Made Good and Ground Speed
- `$GPZDA` / `$GNZDA`: Time & Date

**Format:**
```
2023-06-22T00:00:00.000Z $GPGGA,000000.00,0530.000,N,17000.000,W,1,08,1.0,0.0,M,0.0,M,,*6A
2023-06-22T00:01:00.000Z $GPRMC,000100.00,A,0530.060,N,17000.000,W,0.0,0.0,220623,,,A*71
```

**Processing Options:**
- `--nmea-sentence-types`: Filter specific sentence types (default: all)
- `--nmea-sampling-interval`: Decimate data (e.g., 10.0 = 1 point per 10 seconds)

**Automatic Conversion:**
NMEA files are automatically converted to CSV format before processing, with the CSV stored in `.oceanstream_work/nmea_conversions/`.

### Directory Input

Point `--input-source` to a directory containing any mix of:
- CSV files (`.csv`)
- GeoCSV files (`.geocsv`)
- NMEA files (`.txt`)

**Behavior:**
- All compatible files in the directory are processed
- Files are concatenated into a single dataset
- Platform IDs are preserved per file (if different)
- Campaign ID applied to all files

## Output Structure

### Campaign-Based Organization

All outputs are organized under a **campaign directory**:

```
output_dir/
  └── {campaign_id}/
      ├── lat_bin=5/lon_bin=-170/
      │   ├── part-0.parquet
      │   └── part-1.parquet
      ├── lat_bin=5/lon_bin=-169/
      │   └── part-0.parquet
      ├── stac/
      │   ├── collection.json
      │   └── items/
      │       ├── sd1030_tpos_2023.json
      │       └── sd1033_tpos_2023.json
      └── pmtiles/
          └── {campaign_id}_track.pmtiles
```

**Campaign ID Detection (Priority Order):**
1. User-supplied via `--campaign-id` flag
2. From file metadata header (`cruise_id` in GeoCSV)
3. Derived from platform_id (if single platform)

### GeoParquet Format

**Hive Partitioning:**
- Directory structure: `lat_bin=X/lon_bin=Y/`
- Bin size: 1° × 1° (configurable)
- Bin calculation: `int(floor(latitude))`, `int(floor(longitude))`

**Schema:**
```
geometry: WKT (POINT)
time: datetime64[ns]
platform_id: string
campaign_id: string
[sensor_columns]: float64/int64/string
```

**Metadata Footer:**
- Original column names and units
- CF Standard Names mappings (if semantic enrichment enabled)
- Sensor information
- Provenance metadata

**Example Query (DuckDB):**
```sql
-- Read entire campaign
SELECT * FROM read_parquet('out/geoparquet/tpos_2023/**/*.parquet');

-- Spatial filter (single bin)
SELECT * FROM read_parquet('out/geoparquet/tpos_2023/lat_bin=5/lon_bin=-170/*.parquet');

-- Temporal filter
SELECT * FROM read_parquet('out/geoparquet/tpos_2023/**/*.parquet')
WHERE time BETWEEN '2023-06-22' AND '2023-06-25';
```

### STAC Metadata

**Collection (`collection.json`):**
```json
{
  "stac_version": "1.0.0",
  "type": "Collection",
  "id": "tpos_2023",
  "title": "TPOS 2023 Mission - Saildrone Data",
  "description": "Oceanographic data from TPOS 2023 mission...",
  "extent": {
    "spatial": {"bbox": [[-170, 0], [-155, 18]]},
    "temporal": {"interval": [["2023-06-22T00:00:00Z", "2023-11-05T23:59:59Z"]]}
  },
  "summaries": {
    "platform": ["1030", "1033", "1079"],
    "instruments": ["SBE37", "WETLABS", "ASVCO2"],
    "cf_standard_names": ["sea_water_temperature", "sea_water_salinity", ...]
  }
}
```

**Items (`items/{filename}.json`):**
- One item per input file
- Links to GeoParquet assets
- File-specific metadata
- Temporal extent per file

### PMTiles (Optional)

Vector tiles for web visualization.

**Generation Requirements:**
- GDAL with Parquet support (`ogr2ogr`)
- `pmtiles` CLI tool

**Output:**
- Single `.pmtiles` file per campaign
- Track segments split by time gaps
- Day markers (start of each day)
- Optional: Oceanographic measurements as properties

**Usage:**
```javascript
// Mapbox GL JS / MapLibre GL JS
map.addSource('tracks', {
  type: 'vector',
  url: 'pmtiles://{campaign_id}_track.pmtiles'
});
```

## Processing Pipeline

The convert command executes these stages:

### 1. File Scanning

- Detect input format (CSV, GeoCSV, NMEA)
- Convert NMEA to CSV if needed
- Validate file accessibility
- Display file summary (with confirmation prompt)

### 2. Data Reading

- Parse CSV with Pandas
- Extract GeoCSV metadata header
- Handle missing values (NaN, NULL, empty strings)
- Validate required columns

### 3. Provider Enrichment

- Provider-specific transformations (e.g., R2R → standard column names)
- Platform ID detection (from filename or metadata)
- Campaign ID assignment
- Custom data validation

### 4. Interpolation (if needed)

- For files without spatial coordinates
- Interpolate lat/lon from existing campaign data
- Temporal alignment (nearest, linear, forward/backward fill)
- Max time gap validation

### 5. Semantic Mapping

**IF** semantic enrichment is enabled (`SEMANTIC_ENABLE=true`):

- **Column Profiling**: Statistics and dtype classification
- **Candidate Identification**: Numeric columns with ≥80% non-null
- **Name Normalization**: `camelCase` → `snake_case`, lowercase
- **Alias Matching**: Lookup canonical names (e.g., `TEMP_CTD_RBR_MEAN` → `temperature`)
- **CF Standard Name Mapping**: Fuzzy matching with rapidfuzz
  - Levenshtein distance ≤ 2
  - Jaro-Winkler similarity ≥ 0.93
  - Token subset matching
- **Units Assignment**: From CF table
- **Metadata Assembly**: Aliases, units, CF names with confidence scores

### 6. Sensor Detection

- Identify sensors from column names
- Match against sensor catalogue (YAML definitions)
- Extract platform information
- Validate sensor configurations

### 7. Spatial Binning

- Calculate min/max latitude/longitude
- Determine bin boundaries
- Add `lat_bin` and `lon_bin` columns
- Group data by bins

### 8. Campaign Tracking

**Metadata Storage** (`~/.oceanstream/campaigns/{campaign_id}.json`):
```json
{
  "campaign_id": "tpos_2023",
  "platform_id": "1030",
  "created_at": "2024-11-17T12:00:00Z",
  "updated_at": "2024-11-17T13:00:00Z",
  "spatial_extent": {
    "min_lat": 0.0, "max_lat": 18.0,
    "min_lon": -170.0, "max_lon": -155.0
  },
  "temporal_extent": {
    "start": "2023-06-22T00:00:00Z",
    "end": "2023-11-05T23:59:59Z"
  }
}
```

**File Tracking** (`.oceanstream_metadata.json` in campaign dir):
```json
{
  "processed_files": {
    "sd1030_tpos_2023.csv": {
      "hash": "sha256...",
      "processed_at": "2024-11-17T12:00:00Z",
      "size": 1234567,
      "rows": 11523
    }
  }
}
```

### 9. Deduplication

**Automatic Row-Level Deduplication:**

Primary keys: `time`, `latitude`, `longitude`, `trajectory`

**Strategies:**
- **Prevent File Re-processing**: SHA256 hash tracking
- **Remove Duplicate Rows**: Keep first occurrence
- **Merge with Existing Data**: Append-only, no overwrites

**Flags:**
- `--deduplicate` (default: true): Remove duplicate rows
- `--allow-duplicates`: Allow processing same files multiple times
- `--force-reprocess`: Clear metadata and reprocess from scratch

### 10. Output Generation

**GeoParquet:**
- Write Parquet files partitioned by `lat_bin` and `lon_bin`
- Include semantic metadata in footer (if enabled)
- Apply compression (snappy)

**STAC:**
- Generate collection.json with campaign-level metadata
- Generate item JSON per input file
- Include links to GeoParquet assets

**PMTiles (if `--generate-pmtiles`):**
- Convert GeoParquet to GeoJSON
- Run tippecanoe for tile generation
- Create .pmtiles archive

## Key Features

### Incremental Processing

Multiple runs with the same `campaign_id` safely append data:

```bash
# Day 1: Process initial data
oceanstream process geotrack convert \
  --input-source day1_data/ \
  --campaign-id mission_2024

# Day 2: Append new data
oceanstream process geotrack convert \
  --input-source day2_data/ \
  --campaign-id mission_2024
# ✓ Data appended, duplicates removed automatically
```

### Dry Run Mode

Preview processing without writing files:

```bash
oceanstream process geotrack convert \
  --input-source raw_data/ \
  --campaign-id test \
  --dry-run
```

**Output:**
- Detected files and sizes
- Campaign ID detection result
- Platform IDs detected
- Spatial extent (min/max lat/lon)
- Bin counts and distribution
- Schema preview
- No files written

### Schema Inspection

Print GeoParquet schema without processing:

```bash
oceanstream process geotrack convert \
  --input-source raw_data/ \
  --print-schema
```

**Output:**
```
time                      datetime64[ns]
latitude                  float64
longitude                 float64
platform_id               string
campaign_id               string
temperature               float64
salinity                  float64
lat_bin                   int64
lon_bin                   int64
```

### Column Listing

List available columns from input CSV files:

```bash
oceanstream process geotrack convert \
  --input-source raw_data/ \
  --list-columns
```

**Output:**
```
Columns in raw_data/sd1030_tpos_2023.csv:
  time, latitude, longitude, trajectory
  TEMP_SBE37_MEAN, SAL_SBE37_MEAN, COND_SBE37_MEAN
  O2_CONC_SBE37_MEAN, CHLOR_WETLABS_MEAN
  [70+ more columns...]
```

## Configuration

### Environment Variables

**Metadata Storage:**
- `OCEANSTREAM_METADATA_DIR`: Campaign metadata location (default: `~/.oceanstream/`)

**Semantic Enrichment:**
- `SEMANTIC_ENABLE`: Enable CF Standard Name mapping (default: `false`)
- `SEMANTIC_ALIAS_TABLE`: Path to alias JSON file
- `SEMANTIC_CF_TABLE`: Path to CF Standard Names JSON
- `SEMANTIC_MIN_CONFIDENCE`: Minimum confidence score (default: `0.7`)
- `SEMANTIC_UNITS_CONVERSION`: Enable unit conversion (default: `false`)

**Storage:**
- `OCEANSTREAM_STORAGE_PROVIDER`: Storage backend (`local` or `azure`)
- Azure-specific: `AZURE_STORAGE_*` variables

### Configuration File

Use a TOML file for persistent settings:

```toml
# oceanstream.toml
[semantic]
enable = true
cf_table = "path/to/cf_standard_names.json"
alias_table = "path/to/aliases.json"
min_confidence = 0.8

[storage]
provider = "local"
```

**Usage:**
```bash
oceanstream --config-file oceanstream.toml process geotrack convert ...
```

## Examples

### Basic Processing

```bash
oceanstream process geotrack convert \
  --input-source raw_data/sd1030_tpos_2023.csv \
  --output-dir out/geoparquet \
  --campaign-id tpos_2023
```

### Directory Processing

```bash
oceanstream process geotrack convert \
  --input-source raw_data/saildrone/ \
  --output-dir out/geoparquet \
  --campaign-id tpos_2023
```

### With PMTiles

```bash
oceanstream process geotrack convert \
  --input-source raw_data/ \
  --campaign-id mission_2024 \
  --generate-pmtiles \
  --pmtiles-maxzoom 12 \
  --pmtiles-sample-rate 10
```

### NMEA Processing

```bash
oceanstream process geotrack convert \
  --input-source raw_data/gnss.txt \
  --campaign-id voyage_2024 \
  --nmea-sentence-types GGA RMC \
  --nmea-sampling-interval 10.0
```

### Force Reprocess

```bash
oceanstream process geotrack convert \
  --input-source raw_data/ \
  --campaign-id test_campaign \
  --force-reprocess
```

## Performance Considerations

### Large Datasets

**File Size Guidelines:**
- **Small** (< 100 MB): Process entire directory at once
- **Medium** (100 MB - 1 GB): Process in batches by date/platform
- **Large** (> 1 GB): Use incremental processing (one file at a time)

**Memory Usage:**
- Pandas loads entire CSV into memory
- Peak usage: ~3-5x file size
- For 1 GB CSV: expect 3-5 GB RAM usage

**Optimization Tips:**
- Use NMEA sampling interval to reduce data volume
- Process by platform (separate campaigns)
- Leverage spatial binning for efficient queries

### Semantic Enrichment Overhead

**Performance Impact:**
- Adds ~10-15% processing time
- Fuzzy matching is CPU-intensive
- Results are cached per column name

**Recommendations:**
- Enable only for production datasets
- Use pre-built alias tables when available
- Increase `SEMANTIC_MIN_CONFIDENCE` to reduce false positives

## Next Steps

- **CLI Reference**: Complete list of all command-line options
- **Processing Pipeline**: In-depth explanation of each stage
- **Semantic Enrichment**: CF Standard Names mapping algorithm
- **Examples**: Real-world processing workflows (Saildrone, R2R)

## Additional Resources

- [Geotrack Convert CLI Reference](./geotrack-convert-reference.md)
<!-- TODO: Add processing-pipeline.md -->
- **Processing Pipeline Details** - Detailed explanation of each processing step
- [Saildrone Tutorial](../examples/saildrone-basic.md)
- [Configuration Guide](../../getting-started/configuration.md)
- [STAC Specification](https://stacspec.org/)
- [GeoParquet Format](https://geoparquet.org/)
