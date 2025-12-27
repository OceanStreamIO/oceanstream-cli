# NMEA Data Processing Guide

Complete guide for processing raw NMEA 0183 GNSS data with OceanStream, including CLI usage and Jupyter notebook workflows.

## Overview

OceanStream can process raw NMEA 0183 sentences from GNSS receivers and convert them into standardized GeoParquet datasets. This is particularly useful for:

- **Ship Navigation Data**: Extract position tracks from vessel GNSS logs
- **Autonomous Platforms**: Process navigation data from USVs, AUVs, gliders
- **Field Campaigns**: Convert raw GNSS logs into analysis-ready formats
- **Legacy Data**: Modernize archived NMEA data for cloud-native workflows

## NMEA 0183 Format

### What is NMEA 0183?

NMEA 0183 is a standard protocol for marine electronic devices to communicate with each other. GNSS receivers output position and timing data as ASCII text sentences.

**Example NMEA Sentences:**
```
$GPGGA,123519.00,4807.038,N,01131.000,E,1,08,0.9,545.4,M,46.9,M,,*47
$GPRMC,123519.00,A,4807.038,N,01131.000,E,022.4,084.4,230394,003.1,W*6A
$GPGNS,123519.00,4807.038,N,01131.000,E,AA,08,0.9,545.4,46.9,,*4A
```

### Supported Sentence Types

OceanStream supports the most common GNSS sentence types:

| Type | Description | Data Provided |
|------|-------------|---------------|
| **GGA** | GPS Fix Data | Position, altitude, fix quality, satellites, HDOP |
| **RMC** | Recommended Minimum | Position, speed, course, date/time |
| **GNS** | GNSS Fix Data | Position, fix mode, satellites, HDOP |
| **VTG** | Track Made Good | Course and speed over ground |
| **ZDA** | Time & Date | Authoritative GPS UTC time (critical for live streams) |

**Prefix Variants:**
- `GP` = GPS only
- `GN` = Multi-GNSS (GPS + GLONASS + Galileo, etc.)
- Both are supported automatically

**Data Merging:**
Multiple NMEA sentences with the same timestamp are automatically merged into a single record. For example, at timestamp `2024-02-17T00:00:00.111585Z`:
- **GGA** provides: position, altitude, fix quality, satellites, HDOP
- **RMC** provides: speed, course
- **VTG** provides: additional speed/course data
- **Result**: Complete navigation record with all available fields

### Input File Format

NMEA files are plain text (`.txt`) with one sentence per line. OceanStream supports two formats:

**1. Timestamped NMEA** (Recommended):
```
2023-06-22T00:00:00.000Z $GPGGA,000000.00,0530.000,N,17000.000,W,1,08,1.0,0.0,M,0.0,M,,*6A
2023-06-22T00:00:01.000Z $GPRMC,000001.00,A,0530.000,N,17000.000,W,0.0,0.0,220623,,,A*71
```

**2. Standalone NMEA** (No external timestamps):
```
$GPGGA,000000.00,0530.000,N,17000.000,W,1,08,1.0,0.0,M,0.0,M,,*6A
$GPRMC,000001.00,A,0530.000,N,17000.000,W,0.0,0.0,220623,,,A*71
```

**Timestamp Format:**
- ISO 8601: `YYYY-MM-DDTHH:MM:SS.sssZ`
- Separator: Single space between timestamp and sentence
- If no external timestamp: Extracted from sentence (GGA time + RMC/ZDA date)

## CLI Usage

### Basic NMEA Processing

Process a single NMEA file:

```bash
oceanstream process geotrack convert \
  --input-source raw_data/gnss_log.txt \
  --campaign-id voyage_2024 \
  --output-dir out/geoparquet \
  --verbose
```

**What Happens:**
1. Detects `.txt` file contains NMEA sentences
2. Automatically converts to CSV format
3. Processes CSV into GeoParquet
4. Generates STAC metadata

**Output:**
```
[geotrack] Converting NMEA file: gnss_log.txt → gnss_log.csv
[geotrack]   ✓ Converted 3,600 NMEA sentences → 3,600 CSV rows
[geotrack] Processing 1 file(s)...
[geotrack]   ✓ gnss_log.txt rows=3,600
[geotrack] Writing GeoParquet to out/geoparquet/voyage_2024/
[geotrack] ✓ Processing complete
```

### Filter Sentence Types

Process only specific NMEA sentence types:

```bash
oceanstream process geotrack convert \
  --input-source raw_data/gnss_log.txt \
  --campaign-id voyage_2024 \
  --nmea-sentence-types GGA RMC \
  --verbose
```

**Use Cases:**
- **GGA only**: High-frequency position data (1-10 Hz)
- **RMC only**: Position + speed + course
- **GGA + RMC**: Position + navigation data
- **All types**: Maximum data extraction (default)

### Sampling/Decimation

Reduce data volume by downsampling:

```bash
oceanstream process geotrack convert \
  --input-source raw_data/gnss_log.txt \
  --campaign-id voyage_2024 \
  --nmea-sampling-interval 10.0 \
  --verbose
```

**Effect:**
```
Raw NMEA:    1 Hz → 3,600 points/hour
Decimated:   0.1 Hz → 360 points/hour (90% reduction)
```

**Common Intervals:**
- `5.0`: 1 point per 5 seconds (good for tracks)
- `10.0`: 1 point per 10 seconds (typical for analysis)
- `60.0`: 1 point per minute (summary tracks)

### Directory Processing

Process multiple NMEA files at once:

```bash
oceanstream process geotrack convert \
  --input-source raw_data/gnss_logs/ \
  --campaign-id voyage_2024 \
  --nmea-sentence-types GGA RMC \
  --nmea-sampling-interval 10.0 \
  --verbose
```

**Behavior:**
- Processes all `.txt` files containing NMEA sentences
- Skips non-NMEA `.txt` files
- Converts each to CSV, then merges into single campaign
- Automatic deduplication

### Complete Example

Full workflow with all options:

```bash
oceanstream process geotrack convert \
  --input-source raw_data/gnss_logs/ \
  --output-dir out/geoparquet \
  --campaign-id arctic_expedition_2024 \
  --platform-id rv_polarstern \
  --nmea-sentence-types GGA RMC GNS \
  --nmea-sampling-interval 5.0 \
  --attribution "Alfred Wegener Institute - RV Polarstern Arctic Expedition 2024" \
  --generate-pmtiles \
  --pmtiles-maxzoom 12 \
  --verbose \
  --yes
```

**Output Structure:**
```
out/geoparquet/
  └── arctic_expedition_2024/
      ├── lat_bin=70/lon_bin=10/*.parquet
      ├── lat_bin=71/lon_bin=10/*.parquet
      ├── stac/
      │   ├── collection.json
      │   └── items/gnss_log.json
      └── pmtiles/
          └── arctic_expedition_2024_track.pmtiles
```

## Jupyter Notebook Workflows

### Setup

First, install OceanStream in your Python environment:

```python
# In notebook cell
!pip install oceanstream
```

### Example 1: Basic NMEA Processing

Process a single NMEA file programmatically:

```python
import os
from pathlib import Path
from oceanstream.geotrack.processor import convert
from oceanstream.providers import get_provider

# Configuration
input_file = Path("raw_data/gnss_log.txt")
output_dir = Path("out/geoparquet")
campaign_id = "voyage_2024"

# Get provider
provider = get_provider("generic")

# Process NMEA file
convert(
    provider=provider,
    input_source=input_file,
    output_dir=output_dir,
    campaign_id=campaign_id,
    verbose=True,
    yes=True  # Skip confirmation prompt
)

print(f"\n✓ Processing complete!")
print(f"Output: {output_dir / campaign_id}")
```

### Example 2: NMEA with Filtering

Process with sentence type filtering and decimation:

```python
from pathlib import Path
from oceanstream.geotrack.processor import convert
from oceanstream.providers import get_provider

# Configuration
input_file = Path("raw_data/gnss_log.txt")
output_dir = Path("out/geoparquet")
campaign_id = "voyage_2024"

# Get provider
provider = get_provider("generic")

# Process with filtering
convert(
    provider=provider,
    input_source=input_file,
    output_dir=output_dir,
    campaign_id=campaign_id,
    nmea_sentence_types=["GGA", "RMC"],  # Only GGA and RMC
    nmea_sampling_interval=10.0,          # 1 point per 10 seconds
    verbose=True,
    yes=True
)

# Display results
import geopandas as gpd
campaign_dir = output_dir / campaign_id
gdf = gpd.read_parquet(campaign_dir)

print(f"\nDataset Summary:")
print(f"  Total rows: {len(gdf):,}")
print(f"  Time range: {gdf['time'].min()} to {gdf['time'].max()}")
print(f"  Spatial extent: {gdf.total_bounds}")
print(f"  Columns: {list(gdf.columns)}")
```

### Example 3: Inspect NMEA Before Processing

Check NMEA file contents before processing:

```python
from pathlib import Path

# Read NMEA file
nmea_file = Path("raw_data/gnss_log.txt")

print("First 20 lines of NMEA file:\n")
with open(nmea_file, 'r') as f:
    for i, line in enumerate(f, 1):
        if i > 20:
            break
        print(f"{i:3d}: {line.strip()}")

# Count sentence types
sentence_counts = {}
total_lines = 0

with open(nmea_file, 'r') as f:
    for line in f:
        total_lines += 1
        # Extract sentence type (e.g., GGA, RMC)
        if '$' in line:
            parts = line.split('$', 1)[1].split(',', 1)
            if parts:
                sentence_type = parts[0][2:]  # Remove GP/GN prefix
                sentence_counts[sentence_type] = sentence_counts.get(sentence_type, 0) + 1

print(f"\n\nNMEA Sentence Statistics:")
print(f"  Total lines: {total_lines:,}")
print(f"\n  Sentence Types:")
for sentence_type, count in sorted(sentence_counts.items()):
    percentage = (count / total_lines) * 100
    print(f"    {sentence_type:6s}: {count:6,} ({percentage:5.1f}%)")
```

### Example 4: Process and Visualize

Complete workflow with visualization:

```python
from pathlib import Path
import geopandas as gpd
import matplotlib.pyplot as plt
from oceanstream.geotrack.processor import convert
from oceanstream.providers import get_provider

# 1. Process NMEA file
print("Processing NMEA data...")
provider = get_provider("generic")

convert(
    provider=provider,
    input_source=Path("raw_data/gnss_log.txt"),
    output_dir=Path("out/geoparquet"),
    campaign_id="voyage_2024",
    nmea_sampling_interval=10.0,
    verbose=True,
    yes=True
)

# 2. Load processed data
print("\nLoading GeoParquet...")
gdf = gpd.read_parquet("out/geoparquet/voyage_2024")

# 3. Display statistics
print(f"\nDataset Summary:")
print(f"  Rows: {len(gdf):,}")
print(f"  Time range: {gdf['time'].min()} to {gdf['time'].max()}")
print(f"  Spatial extent:")
print(f"    Latitude:  {gdf['latitude'].min():.4f}° to {gdf['latitude'].max():.4f}°")
print(f"    Longitude: {gdf['longitude'].min():.4f}° to {gdf['longitude'].max():.4f}°")

# 4. Plot track
fig, ax = plt.subplots(figsize=(12, 8))
gdf.plot(ax=ax, marker='o', markersize=1, color='blue', alpha=0.6)
ax.set_xlabel('Longitude')
ax.set_ylabel('Latitude')
ax.set_title('GNSS Track from NMEA Data')
ax.grid(True, alpha=0.3)
plt.tight_layout()
plt.show()

# 5. Sample data
print("\nFirst 5 rows:")
print(gdf.head())
```

### Example 5: Batch Process Multiple Files

Process multiple NMEA files programmatically:

```python
from pathlib import Path
import pandas as pd
from oceanstream.geotrack.processor import convert
from oceanstream.providers import get_provider

# Configuration
input_dir = Path("raw_data/gnss_logs")
output_dir = Path("out/geoparquet")
campaign_id = "multi_day_cruise"

# Get NMEA files
nmea_files = list(input_dir.glob("*.txt"))
print(f"Found {len(nmea_files)} NMEA files:\n")
for f in nmea_files:
    size_mb = f.stat().st_size / 1024 / 1024
    print(f"  {f.name:30s} ({size_mb:6.2f} MB)")

# Process directory (all files at once)
print(f"\nProcessing all files into campaign '{campaign_id}'...")
provider = get_provider("generic")

convert(
    provider=provider,
    input_source=input_dir,
    output_dir=output_dir,
    campaign_id=campaign_id,
    nmea_sentence_types=["GGA", "RMC"],
    nmea_sampling_interval=5.0,
    verbose=True,
    yes=True
)

# Load and summarize
import geopandas as gpd
gdf = gpd.read_parquet(output_dir / campaign_id)

print(f"\nFinal Dataset:")
print(f"  Total rows: {len(gdf):,}")
print(f"  Duration: {(gdf['time'].max() - gdf['time'].min()).total_seconds() / 86400:.1f} days")
print(f"  Distance: {gdf.length.sum() / 1000:.1f} km (approx)")
```

### Example 6: Custom NMEA Parsing

For advanced users who need custom parsing:

```python
from pathlib import Path
import pandas as pd
from oceanstream.sensors.processors.nmea_gnss import process_nmea_raw

# Input/output paths
input_file = Path("raw_data/gnss_log.txt")
output_file = Path("out/temp/gnss_processed.csv")
output_file.parent.mkdir(parents=True, exist_ok=True)

# Custom processing
stats = process_nmea_raw(
    input_path=input_file,
    output_path=output_file,
    sentence_types=["GGA", "RMC"],  # Filter sentences
    sampling_interval=10.0           # Decimate to 10-second intervals
)

print("NMEA Processing Statistics:")
print(f"  Lines parsed: {stats['lines_parsed']:,}")
print(f"  Data points written: {stats['data_points_written']:,}")
print(f"  Reduction: {(1 - stats['data_points_written']/stats['lines_parsed'])*100:.1f}%")

# Load CSV and inspect
df = pd.read_csv(output_file)
print(f"\nCSV Schema:")
print(df.dtypes)
print(f"\nFirst 10 rows:")
print(df.head(10))
```

## Technical Details

### Coordinate Conversion

NMEA uses a special format for coordinates that requires conversion:

- **NMEA Format**: `3242.3912,N` = 32° 42.3912' North (degrees + decimal minutes)
- **Decimal Degrees**: `32.70652°` (standard GIS format)

**Good news**: The `pynmea2` library automatically handles this conversion! OceanStream receives coordinates already in decimal degrees format, so no manual conversion is needed.

### Unit Conversions

OceanStream automatically converts units to standard SI/metric:

- **Speed**: Knots → m/s (multiply by 0.514444)
- **Course**: Degrees (0-360°) - no conversion needed
- **Altitude**: Meters (already in meters)

### Data Merging Strategy

Multiple NMEA sentences with the same timestamp are merged into a single record:

**Example at `2024-02-17T00:00:00.111585Z`:**
- **GGA** provides: lat, lon, altitude, quality, satellites, HDOP
- **RMC** provides: speed, course, date
- **VTG** provides: additional speed/course data
- **Result**: Complete navigation record with all available fields

This merging happens automatically based on timestamps, ensuring no data loss while maintaining one record per unique time point.

### Data Quality & Performance

Typical processing statistics from real NMEA files (606k lines, 24 hours):

| Metric | Value |
|--------|-------|
| **Parse Success Rate** | 57-71% (remaining lines are non-data sentences) |
| **Compression Ratio** | 87-89% (raw NMEA → GeoParquet) |
| **Processing Speed** | ~50,000 sentences/second (NMEA→CSV) |
| **Throughput** | ~190,000 rows/second (CSV→GeoParquet) |

**Note**: Not all NMEA lines contain position data. Lines like `$GPDTM` (datum reference), `$GPGLL` (geographic position), and checksum-only lines are skipped.

**Real-World Example** (27 MB NMEA file):
- Lines read: 606,242
- Lines parsed: 431,810 (71%)
- Data points merged: 260,000 (after timestamp deduplication)
- Output size: 3.5 MB GeoParquet (89% compression)
- Processing time: ~11 seconds total

### Sensor Detection

NMEA data is automatically detected and tagged with the **gnss-navigation** sensor:

- **Sensor ID**: `gnss-navigation`
- **Name**: "GNSS Navigation Receiver"
- **Manufacturer**: Various (Furuno, Trimble, Garmin, etc.)
- **Detection**: Based on column patterns and data types

This enables:
- Automatic STAC metadata generation with sensor information
- Consistent naming across different GNSS hardware brands
- Integration with sensor-aware analysis workflows
- Proper attribution in data catalogs

## Output Data Structure

### Generated CSV (Intermediate)

When NMEA is converted to CSV, the following columns are created:

| Column | Type | Description | Source |
|--------|------|-------------|--------|
| `time` | datetime | ISO 8601 timestamp | External timestamp or GGA+RMC/ZDA |
| `latitude` | float | Latitude in decimal degrees | GGA/RMC/GNS |
| `longitude` | float | Longitude in decimal degrees | GGA/RMC/GNS |
| `altitude` | float | Altitude above MSL (meters) | GGA/GNS |
| `fix_quality` | int | GPS fix quality (0-9) | GGA |
| `num_satellites` | int | Number of satellites | GGA/GNS |
| `hdop` | float | Horizontal dilution of precision | GGA/GNS |
| `speed_knots` | float | Speed over ground (knots) | RMC/VTG |
| `course` | float | Course over ground (degrees) | RMC/VTG |
| `magnetic_variation` | float | Magnetic variation (degrees) | RMC |

### Final GeoParquet Schema

After processing into GeoParquet:

```
time                datetime64[ns]
latitude            float64
longitude           float64
geometry            object (WKT POINT)
altitude            float64
fix_quality         int64
num_satellites      int64
hdop                float64
speed_knots         float64
course              float64
magnetic_variation  float64
platform_id         string
campaign_id         string
lat_bin             int64
lon_bin             int64
```

## Common Use Cases

### Use Case 1: Ship Navigation Archive

Convert historical ship navigation logs:

```bash
# Process all NMEA logs from a research cruise
oceanstream process geotrack convert \
  --input-source archive/RV_atlantis_2023/gnss/ \
  --campaign-id at42_cruise_2023 \
  --platform-id rv_atlantis \
  --nmea-sentence-types GGA RMC \
  --nmea-sampling-interval 60.0 \
  --attribution "Woods Hole Oceanographic Institution" \
  --verbose
```

### Use Case 2: USV Real-Time Processing

Process Saildrone navigation data:

```bash
# Saildrones output NMEA logs in addition to CSV data
oceanstream process geotrack convert \
  --input-source raw_data/sd1030_nmea_logs/ \
  --campaign-id tpos_2023 \
  --platform-id 1030 \
  --nmea-sentence-types GGA \
  --nmea-sampling-interval 10.0 \
  --generate-pmtiles \
  --verbose
```

### Use Case 3: Glider Navigation

Process autonomous glider surface GPS fixes:

```bash
# Gliders typically get GPS fixes at surface intervals
oceanstream process geotrack convert \
  --input-source raw_data/glider_gps.txt \
  --campaign-id antarctica_survey_2024 \
  --platform-id sg001 \
  --nmea-sentence-types GGA \
  --attribution "Southern Ocean Observing System" \
  --verbose
```

### Use Case 4: Multi-Platform Expedition

Process NMEA from multiple vessels:

```bash
# Process each vessel separately, then combine via campaign
for vessel in ship1 ship2 ship3; do
  oceanstream process geotrack convert \
    --input-source raw_data/${vessel}_gnss.txt \
    --campaign-id arctic_expedition_2024 \
    --platform-id $vessel \
    --nmea-sampling-interval 10.0 \
    --verbose
done

# Result: Single campaign with 3 platforms
```

## Troubleshooting

### Invalid NMEA Format

**Error:**
```
ValueError: File gnss_log.txt is a .txt file but does not contain NMEA sentences.
```

**Solutions:**
1. Check file contains `$` prefix: `head -20 gnss_log.txt`
2. Verify NMEA format: Lines should start with `$GPXXX` or `$GNXXX`
3. Check for corruption: Binary data mixed with text?

### Missing Date Information

**Error:**
```
Warning: No date information found in NMEA sentences. Using current date.
```

**Solutions:**
1. Include RMC or ZDA sentences (contain date)
2. Use timestamped NMEA format (ISO 8601 prefix)
3. Manually set date in post-processing

### Empty Output

**Error:**
```
ValueError: No usable data after per-file processing.
```

**Solutions:**
1. Check sentence type filter: `--nmea-sentence-types GGA RMC`
2. Verify sentences contain position data: `grep GPGGA gnss_log.txt | head`
3. Check for valid fix: GGA field 6 should be 1 or 2 (not 0)

### High Memory Usage

**Issue:** Large NMEA files consuming too much memory.

**Solutions:**
1. Use sampling: `--nmea-sampling-interval 10.0` (reduce by 90%)
2. Split large files: `split -l 100000 gnss_log.txt gnss_part_`
3. Process in batches by date/time

### Incorrect Positions

**Issue:** Positions appear wrong or outside expected area.

**Solutions:**
1. Check coordinate format: NMEA uses DDMM.MMMM (degrees + minutes)
2. Verify hemisphere: N/S for latitude, E/W for longitude
3. Inspect raw sentences: `grep GPGGA gnss_log.txt | head -5`
4. Validate with known position

## Performance Guidelines

### File Size Recommendations

| File Size | Rows (approx) | Recommendation |
|-----------|---------------|----------------|
| < 10 MB | < 100k | Process as-is |
| 10-100 MB | 100k-1M | Use 10s sampling |
| 100-500 MB | 1M-5M | Use 30s sampling |
| > 500 MB | > 5M | Use 60s sampling or split files |

### Sampling Guidelines

Real-world decimation results (606k lines, 24 hours):

| Sampling Interval | Points Output | Decimation Ratio | File Size | Reduction |
|-------------------|--------------|------------------|-----------|-----------|
| None (all points) | 431,810 | 100% | 32.7 MB | - |
| 1 second | 76,616 | 17.7% | 5.7 MB | 82.3% |
| 10 seconds | 8,563 | 2.0% | 627 KB | 98.0% |
| 60 seconds | ~1,440 | ~0.3% | ~100 KB | ~99.7% |

**Use Cases:**
- **1-second sampling**: High-frequency GPS (10 Hz) → 1 Hz, 82% reduction
- **10-second sampling**: Slow-moving platforms (ships, buoys), 98% reduction  
- **60-second sampling**: Trajectory tracking only, 99.7% reduction

### Processing Speed

Typical processing rates (on modern laptop):

- **NMEA → CSV**: ~50,000 sentences/second
- **CSV → GeoParquet**: ~190,000 rows/second
- **GeoParquet → PMTiles**: ~5,000 points/second

**Real Example:** 606k NMEA sentences (27 MB file)
- NMEA → CSV: ~10 seconds
- CSV → GeoParquet: ~1.4 seconds
- **Total**: ~11 seconds
- **Compression**: 89% (27 MB → 3.5 MB)

## See Also

- [Geotrack Convert Overview](../core-concepts/geotrack-convert-overview.md) - Full command documentation
- [CLI Reference](../core-concepts/geotrack-convert-reference.md) - All command-line options
- [Saildrone Tutorial](../examples/saildrone-basic.md) - CSV data processing
- [NMEA 0183 Standard](https://www.nmea.org/content/STANDARDS/NMEA_0183_Standard) - Official specification
