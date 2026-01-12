# Geotrack Convert: CLI Reference

Complete reference for all command-line options and flags for the `oceanstream process geotrack convert` command.

## Command Syntax

```bash
oceanstream [GLOBAL_OPTIONS] \
  process [--provider PROVIDER] \
    geotrack convert [OPTIONS]
```

## Global Options

These options apply to the `oceanstream` command itself (before `process`):

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `--config-file` | Path | None | Path to TOML configuration file |
| `--help` | Flag | - | Show help message and exit |
| `--version` | Flag | - | Show version and exit |

**Example:**
```bash
oceanstream --config-file oceanstream.toml process geotrack convert ...
```

## Provider Options

These options apply to the `process` command group:

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `--provider` | String | `saildrone` | Data provider type (applies to all subcommands) |

**Available Providers:**
- `saildrone`: Saildrone USV data
- `r2r`: Rolling Deck to Repository (R2R) data
- `generic`: Generic CSV data

**Example:**
```bash
oceanstream process --provider r2r geotrack convert ...
```

## Input/Output Options

### Input Source

| Option | Type | Required | Default | Description |
|--------|------|----------|---------|-------------|
| `--input-source` | Path | No | `raw_data` | Path to data file (.csv, .geocsv, .txt NMEA) or directory containing data files |

**Behavior:**
- If a **file**: Process that single file
- If a **directory**: Process all `.csv`, `.geocsv`, and `.txt` (NMEA) files in the directory
- NMEA files are automatically converted to CSV before processing

**Supported Formats:**
- CSV: `.csv`
- GeoCSV: `.geocsv`
- NMEA: `.txt` (with NMEA sentences)

**Examples:**
```bash
# Single file
--input-source raw_data/sd1030_tpos_2023.csv

# Directory
--input-source raw_data/saildrone/

# NMEA file
--input-source raw_data/gnss_log.txt
```

### Output Directory

| Option | Type | Required | Default | Description |
|--------|------|----------|---------|-------------|
| `--output-dir` | Path | No | `out/geoparquet` | Base output directory for partitioned GeoParquet dataset |

**Behavior:**
- Campaign-based subdirectories are created automatically: `output_dir/{campaign_id}/`
- Hive partitioning applied: `lat_bin=X/lon_bin=Y/*.parquet`
- STAC metadata in: `output_dir/{campaign_id}/stac/`
- PMTiles in: `output_dir/{campaign_id}/pmtiles/`

**Examples:**
```bash
--output-dir out/geoparquet
--output-dir /data/oceanstream/processed
--output-dir ./data/campaigns
```

### Upload Flag

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `--upload` | Flag | `false` | Upload processed dataset to cloud storage (future feature) |

**Note:** Currently a placeholder for future cloud storage integration.

## Processing Options

### Verbose Mode

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `-v`, `--verbose` | Flag | `false` | Emit detailed progress information |

**Output when enabled:**
- File scanning details
- Per-file processing status
- Row counts per file
- Timing information for each stage
- Sensor detection results
- Spatial extent calculations
- STAC metadata generation

**Example:**
```bash
oceanstream process geotrack convert \
  --input-source raw_data/ \
  -v
```

### Confirmation Prompts

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `-y`, `--yes` | Flag | `false` | Skip confirmation prompts |

**Without flag:**
```
Detected 3 file(s) in raw_data/:
  Filename                              Size
  ------------------------------------  ----------
  sd1030_tpos_2023.csv                  1.2 MB
  sd1033_tpos_2023.csv                  1.1 MB
  sd1079_tpos_2023.csv                  1.3 MB
  ------------------------------------  ----------
  Total                                 3.6 MB

Proceed with processing? [Y/n]:
```

**With flag:** Skips the prompt, proceeds automatically.

## Inspection Options

These flags allow you to inspect input data without processing:

### List Columns

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `--list-columns` | Flag | `false` | List available columns from input CSV files and exit |

**Example:**
```bash
oceanstream process geotrack convert \
  --input-source raw_data/sd1030_tpos_2023.csv \
  --list-columns
```

**Output:**
```
Columns in raw_data/sd1030_tpos_2023.csv:
  Standard Columns:
    time, latitude, longitude, trajectory
  
  Temperature Sensors:
    TEMP_SBE37_MEAN, TEMP_AIR_MEAN
  
  Salinity Sensors:
    SAL_SBE37_MEAN, COND_SBE37_MEAN
  
  ... (70+ columns total)
```

### Print Schema

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `--print-schema` | Flag | `false` | Print GeoParquet schema (column → dtype plus partition columns) and exit |

**Example:**
```bash
oceanstream process geotrack convert \
  --input-source raw_data/ \
  --print-schema
```

**Output:**
```
GeoParquet Schema:

Standard Columns:
  time                      datetime64[ns]
  latitude                  float64
  longitude                 float64
  geometry                  object (WKT)
  platform_id               string
  campaign_id               string

Sensor Measurements:
  TEMP_SBE37_MEAN          float64
  SAL_SBE37_MEAN           float64
  CHLOR_WETLABS_MEAN       float64
  ...

Partitioning Columns:
  lat_bin                   int64
  lon_bin                   int64
```

### Provider Metadata

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `--provider-metadata` | Flag | `false` | Print provider metadata snapshot inferred from data and exit |

**Example:**
```bash
oceanstream process geotrack convert \
  --input-source raw_data/ \
  --provider-metadata
```

**Output:**
```
Provider: saildrone
Platform Detected: SD1030 (Saildrone Explorer)
Campaign ID: tpos_2023
Spatial Extent: 0.0°N to 18.0°N, 170.0°W to 155.0°W
Temporal Extent: 2023-06-22 to 2023-11-05
Sensors Detected:
  - SBE37 CTD (temperature, salinity, conductivity)
  - Wetlabs Fluorometer (chlorophyll)
  - ASVCO2 pCO2 Sensor
  - Met Sensors (air temp, pressure, humidity, wind)
```

### Dry Run

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `--dry-run` | Flag | `false` | Analyze inputs and print derived bin info without writing any files |

**Behavior:**
- Reads and validates all input files
- Performs all processing stages (except output)
- Displays:
  - Files detected
  - Campaign and platform IDs
  - Spatial extent and bin counts
  - Temporal extent
  - Sensor detection results
  - Schema preview
- **No files written** (GeoParquet, STAC, PMTiles)

**Example:**
```bash
oceanstream process geotrack convert \
  --input-source raw_data/ \
  --campaign-id test \
  --dry-run
```

**Output:**
```
[geotrack] DRY RUN MODE - No files will be written

Detected Files: 3
  sd1030_tpos_2023.csv    1.2 MB
  sd1033_tpos_2023.csv    1.1 MB
  sd1079_tpos_2023.csv    1.3 MB

Campaign ID: tpos_2023
Platform IDs: 1030, 1033, 1079

Spatial Extent:
  Latitude:  0.0° to 18.0° N
  Longitude: 170.0° to 155.0° W

Temporal Extent:
  Start: 2023-06-22T00:00:00Z
  End:   2023-11-05T23:59:59Z

Spatial Bins:
  Latitude bins:  18 (0 to 17)
  Longitude bins: 16 (-170 to -155)
  Total bins:     288

Total Rows: 345,678

Sensors Detected:
  - SBE37 CTD
  - Wetlabs Fluorometer
  - ASVCO2 pCO2 Sensor
  - Meteorological Sensors

Schema Preview:
  time, latitude, longitude, platform_id, campaign_id
  TEMP_SBE37_MEAN, SAL_SBE37_MEAN, ...
  lat_bin, lon_bin

[geotrack] DRY RUN COMPLETE - No files written
```

## Metadata Options

### Campaign ID

| Option | Type | Required | Default | Description |
|--------|------|----------|---------|-------------|
| `--campaign-id` | String | No* | Auto-detected | Campaign/cruise identifier |

**Priority Order:**
1. User-supplied via `--campaign-id`
2. From file metadata header (`cruise_id` in GeoCSV)
3. Derived from `platform_id` (if single platform)

**Required:** If not auto-detectable from filenames or metadata, you **must** supply it.

**Examples:**
```bash
--campaign-id tpos_2023
--campaign-id FK161229
--campaign-id voyage_2024
```

### Platform ID

| Option | Type | Required | Default | Description |
|--------|------|----------|---------|-------------|
| `--platform-id` | String | No | Auto-detected | Platform identifier (overrides auto-detection). Note: For campaign creation, use `--platform` instead. |

**Priority Order:**
1. User-supplied via `--platform-id`
2. From file metadata header
3. Extracted from filename (e.g., `sd1030_*.csv` → `1030`)

**When to use:**
- Override auto-detection (e.g., wrong platform ID in filename)
- Process data from specific platform only
- Force consistent platform ID across files

**Examples:**
```bash
--platform-id 1030
--platform-id falkor
--platform-id ROV_jason
```

### Attribution

| Option | Type | Required | Default | Description |
|--------|------|----------|---------|-------------|
| `--attribution` | String | No | Provider/file metadata | Data attribution/citation |

**Overrides:** Provider default or file metadata.

**Used in:** STAC metadata, GeoParquet footer.

**Example:**
```bash
--attribution "Data collected by Saildrone Inc. during NOAA TPOS 2023 mission"
```

### Creation Date

| Option | Type | Required | Default | Description |
|--------|------|----------|---------|-------------|
| `--creation-date` | String | No | Current timestamp | Data creation date in ISO 8601 format |

**Format:** `YYYY-MM-DDTHH:MM:SSZ`

**Example:**
```bash
--creation-date 2023-06-22T00:00:00Z
```

### Source Dataset DOI

| Option | Type | Required | Default | Description |
|--------|------|----------|---------|-------------|
| `--source-dataset` | String | No | None | Source dataset DOI |

**Used in:** STAC metadata, provenance tracking.

**Example:**
```bash
--source-dataset "10.5067/XXXX-YYYY"
```

### Source Repository DOI

| Option | Type | Required | Default | Description |
|--------|------|----------|---------|-------------|
| `--source-repository` | String | No | None | Source repository DOI |

**Used in:** STAC metadata, provenance tracking.

**Example:**
```bash
--source-repository "10.5281/zenodo.1234567"
```

## Deduplication Options

### Force Reprocess

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `--force-reprocess` | Flag | `false` | Clear previous metadata and reprocess all files from scratch |

**Behavior:**
- Deletes `.oceanstream_metadata.json` in campaign directory
- Clears file tracking (SHA256 hashes)
- Reprocesses all files (even if previously processed)
- Useful for: Testing, fixing corrupted data, changing processing parameters

**Example:**
```bash
oceanstream process geotrack convert \
  --input-source raw_data/ \
  --campaign-id test \
  --force-reprocess
```

**Warning:** This does **not** delete existing GeoParquet files, only metadata. To fully reprocess, delete the campaign directory first:
```bash
rm -rf out/geoparquet/test_campaign
oceanstream process geotrack convert --campaign-id test_campaign --force-reprocess ...
```

## NMEA Processing Options

These options apply **only** to `.txt` files containing NMEA sentences:

### Sentence Types

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `--nmea-sentence-types` | List[String] | All supported | NMEA sentence types to process |

**Supported Types:**
- `GGA`: GPS Fix Data (position, altitude, fix quality)
- `RMC`: Recommended Minimum Navigation (position, speed, course)
- `GNS`: GNSS Fix Data (position, mode indicator)
- `VTG`: Track Made Good and Ground Speed
- `ZDA`: Time & Date

**Format:** Space-separated list without `$GP` / `$GN` prefix.

**Examples:**
```bash
# Only GGA and RMC
--nmea-sentence-types GGA RMC

# Only GGA (high-frequency position data)
--nmea-sentence-types GGA

# All supported types (default behavior)
# (no flag needed)
```

### Sampling Interval

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `--nmea-sampling-interval` | Float | None (all points) | Sampling interval in seconds for decimation |

**Behavior:**
- If not specified: Keep all data points
- If specified: Downsample to 1 point per N seconds
- Useful for: Reducing data volume, smoothing high-frequency noise

**Examples:**
```bash
# 1 point per 10 seconds
--nmea-sampling-interval 10.0

# 1 point per minute
--nmea-sampling-interval 60.0

# 1 point per 5 seconds
--nmea-sampling-interval 5.0
```

**Performance Impact:**
```
Raw NMEA: 1 Hz (3600 points/hour)
  ↓ [--nmea-sampling-interval 10.0]
CSV: 0.1 Hz (360 points/hour)  →  90% reduction
```

## PMTiles Options

These options control PMTiles vector tile generation (requires `ogr2ogr` and `pmtiles` CLI):

### Generate PMTiles

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `--generate-pmtiles` | Flag | `false` | Generate PMTiles vector tiles with track segments and day markers |

**Requirements:**
- GDAL with Parquet support (`ogr2ogr --formats | grep Parquet`)
- `pmtiles` CLI tool (`pmtiles --version`)

**Skipped if:** Tools not available (with warning message).

### Zoom Levels

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `--pmtiles-minzoom` | Integer (0-15) | `0` | Minimum zoom level for PMTiles |
| `--pmtiles-maxzoom` | Integer (0-15) | `10` | Maximum zoom level for PMTiles |

**Recommendations:**
- **Global view**: `minzoom=0`, `maxzoom=8`
- **Regional view**: `minzoom=4`, `maxzoom=10`
- **Detailed view**: `minzoom=6`, `maxzoom=12`

**Trade-offs:**
- **Higher maxzoom**: More detail, larger file size, longer generation time
- **Lower maxzoom**: Less detail, smaller file size, faster generation

**Example:**
```bash
--pmtiles-minzoom 0 --pmtiles-maxzoom 12
```

### Layer Name

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `--pmtiles-layer` | String | `track` | Layer name for PMTiles vector tiles |

**Used in:** Vector tile rendering (MapLibre/Mapbox GL JS).

**Example:**
```bash
--pmtiles-layer saildrone_tracks
```

### Sample Rate

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `--pmtiles-sample-rate` | Integer | `5` | Sample rate: take every Nth point |

**Behavior:**
- `1`: All points (no sampling)
- `5`: Every 5th point (80% reduction)
- `10`: Every 10th point (90% reduction)

**Use cases:**
- **Dense tracks** (1 Hz sampling): Use `5-10` to reduce tile size
- **Sparse tracks** (< 0.1 Hz): Use `1` to keep all points
- **Web visualization**: `5` is usually sufficient

**Example:**
```bash
--pmtiles-sample-rate 10  # 90% reduction
```

### Time Gap

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `--pmtiles-time-gap` | Integer | `60` | Time gap in minutes to split track segments |

**Behavior:**
- Splits continuous track into segments when time gap > threshold
- Creates separate features (LineStrings) per segment
- Improves rendering performance (fewer vertices per feature)

**Recommendations:**
- **Continuous sampling**: `60` (1 hour) for typical USV missions
- **Intermittent sampling**: `30` (30 minutes)
- **High-frequency sampling**: `120` (2 hours) to avoid too many segments

**Example:**
```bash
--pmtiles-time-gap 120  # Split segments with 2+ hour gaps
```

### Include Measurements

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `--pmtiles-include-measurements` | Flag | `true` | Include oceanographic measurements in PMTiles properties |

**Behavior:**
- **Enabled**: Measurements added as feature properties (larger tiles)
- **Disabled**: Only geometry and time (smaller tiles, faster rendering)

**Use cases:**
- **Enabled**: For interactive data exploration (tooltips, popups)
- **Disabled**: For navigation/track visualization only

**Example:**
```bash
--pmtiles-include-measurements=false  # Geometry only
```

### Measurement Columns

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `--pmtiles-measurement-columns` | List[String] | Auto-selected | Specific measurement columns to include in PMTiles |

**Default behavior:** Auto-selects important oceanographic variables (temperature, salinity, chlorophyll, etc.).

**When to use:** Limit tile size by including only specific variables.

**Format:** Space-separated list of column names.

**Example:**
```bash
# Only temperature and salinity
--pmtiles-measurement-columns TEMP_SBE37_MEAN SAL_SBE37_MEAN

# Temperature, chlorophyll, and oxygen
--pmtiles-measurement-columns TEMP_SBE37_MEAN CHLOR_WETLABS_MEAN O2_CONC_SBE37_MEAN
```

## Complete Examples

### Minimal Example

Process a single CSV file with auto-detected campaign ID:

```bash
oceanstream process geotrack convert \
  --input-source raw_data/sd1030_tpos_2023.csv
```

### Full Workflow with All Options

```bash
oceanstream process geotrack convert \
  --input-source raw_data/saildrone/ \
  --output-dir out/geoparquet \
  --campaign-id tpos_2023 \
  --attribution "NOAA PMEL Saildrone TPOS 2023 Mission" \
  --creation-date 2023-06-22T00:00:00Z \
  --source-dataset "10.5067/TPOS-2023" \
  --generate-pmtiles \
  --pmtiles-maxzoom 12 \
  --pmtiles-layer tpos_tracks \
  --pmtiles-sample-rate 5 \
  --pmtiles-time-gap 60 \
  --verbose \
  --yes
```

### NMEA Processing

```bash
oceanstream process geotrack convert \
  --input-source raw_data/gnss_logs/ \
  --campaign-id voyage_2024 \
  --nmea-sentence-types GGA RMC \
  --nmea-sampling-interval 10.0 \
  --verbose
```

### Dry Run Inspection

```bash
oceanstream process geotrack convert \
  --input-source raw_data/ \
  --campaign-id test \
  --dry-run \
  --verbose
```

### Force Reprocess

```bash
# Clean slate - delete campaign directory
rm -rf out/geoparquet/test_campaign

# Reprocess with new parameters
oceanstream process geotrack convert \
  --input-source raw_data/ \
  --campaign-id test_campaign \
  --force-reprocess \
  --verbose
```

### Incremental Processing (Day-by-Day)

```bash
# Day 1: Initial processing
oceanstream process geotrack convert \
  --input-source raw_data/2023-06-22/ \
  --campaign-id tpos_2023 \
  --verbose

# Day 2: Append new data
oceanstream process geotrack convert \
  --input-source raw_data/2023-06-23/ \
  --campaign-id tpos_2023 \
  --verbose

# Day N: Continue appending
# (automatic deduplication prevents duplicate rows)
```

## Exit Codes

| Code | Meaning | Description |
|------|---------|-------------|
| `0` | Success | Processing completed successfully |
| `1` | Error | Processing failed (see error message) |

**Common Error Scenarios:**
- File not found: `--input-source` path doesn't exist
- No usable data: All files skipped or empty
- Missing campaign ID: Cannot detect campaign ID from filenames/metadata
- Invalid NMEA: `.txt` file is not NMEA format
- Schema mismatch: New data incompatible with existing campaign data
- Tool not found: `ogr2ogr` or `pmtiles` not available (PMTiles only)

## See Also

- [Geotrack Convert Overview](./geotrack-convert-overview.md) - Comprehensive guide
<!-- TODO: Add processing-pipeline.md -->
- **Processing Pipeline** - In-depth pipeline explanation
- [Configuration Guide](../../getting-started/configuration.md) - Environment variables and config files
- [Saildrone Tutorial](../examples/saildrone-basic.md) - Step-by-step example
