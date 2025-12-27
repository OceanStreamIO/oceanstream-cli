# Quick Start

Get started with OceanStream in 5 minutes! This guide walks you through processing your first oceanographic dataset.

## Prerequisites

- OceanStream installed (see [Installation](installation.md))
- A CSV file with oceanographic data (latitude, longitude, time columns)
- 5 minutes of your time ⏱️

## Your First Processing Run

### Step 1: Prepare Sample Data

You can use your own data or download a sample:

```bash
# Create a working directory
mkdir oceanstream-quickstart
cd oceanstream-quickstart

# If you have OceanStream installed from source:
# Use the bundled test data
cp -r /path/to/oceanstream/tests/data/raw_data ./sample-data

# Or create a simple test CSV
cat > test-data.csv << 'EOF'
time,latitude,longitude,TEMP_CTD_RBR_MEAN,SAL_SBE37_MEAN
2023-01-01T00:00:00Z,45.5,-123.5,12.5,33.2
2023-01-01T01:00:00Z,45.6,-123.4,12.6,33.3
2023-01-01T02:00:00Z,45.7,-123.3,12.4,33.1
2023-01-01T03:00:00Z,45.8,-123.2,12.7,33.4
2023-01-01T04:00:00Z,45.9,-123.1,12.5,33.2
EOF
```

### Step 2: Process the Data

Run OceanStream to convert CSV to GeoParquet:

```bash
oceanstream process geotrack \
  --input-source ./test-data.csv \
  --output-dir ./output \
  --campaign-id my_first_campaign \
  --verbose
```

**What this does:**
- Reads your CSV file
- Validates latitude, longitude, and time columns
- Creates spatial bins (1° x 1° grid)
- Generates GeoParquet files with Hive partitioning
- Creates STAC metadata for data discovery
- Registers the campaign for tracking

### Step 3: Explore the Output

```bash
# View output structure
tree output/my_first_campaign

# Should show:
# output/my_first_campaign/
# ├── lat_bin=45/
# │   └── lon_bin=-124/
# │       └── part-0.parquet
# └── stac/
#     ├── collection.json
#     └── items/
#         └── test-data.json
```

### Step 4: Verify the Results

```bash
# Check how many rows were processed
python << 'EOF'
import pandas as pd
from pathlib import Path

# Read the GeoParquet file
parquet_files = list(Path("output/my_first_campaign").rglob("*.parquet"))
parquet_files = [f for f in parquet_files if 'stac' not in f.parts]

df = pd.concat([pd.read_parquet(f) for f in parquet_files])
print(f"✅ Processed {len(df)} rows")
print(f"✅ Columns: {', '.join(df.columns)}")
print(f"\nFirst few rows:\n{df.head()}")
EOF
```

## Common Workflows

### Process a Directory of CSV Files

```bash
# Process all CSV files in a directory
oceanstream process geotrack \
  --input-source ./raw-data/ \
  --output-dir ./output \
  --campaign-id cruise_2024 \
  --verbose
```

### Process with Cloud Upload

First, configure Azure storage:

```bash
# Configure storage (you'll be prompted for credentials)
oceanstream configure storage \
  --provider azure \
  --container-name oceanstream-data
```

Then process and upload:

```bash
oceanstream process geotrack \
  --input-source ./data/ \
  --output-dir ./output \
  --campaign-id cruise_2024 \
  --upload \
  --verbose
```

### Dry Run (Preview Without Processing)

```bash
# See what would be processed without actually doing it
oceanstream process geotrack \
  --input-source ./data/ \
  --output-dir ./output \
  --dry-run \
  --verbose
```

### List Available Columns

```bash
# See what columns are in your data
oceanstream process geotrack \
  --input-source ./data/my-cruise.csv \
  --list-columns
```

## Understanding the Output

### Directory Structure

```
output/
└── campaign_id/
    ├── lat_bin=X/lon_bin=Y/     # Spatial partitions
    │   └── *.parquet             # Data files
    └── stac/                     # STAC metadata
        ├── collection.json       # Campaign metadata
        └── items/                # Per-file metadata
            └── *.json
```

**Key concepts:**
- **Spatial Binning**: Data is organized into 1° x 1° lat/lon bins for efficient spatial queries
- **Hive Partitioning**: Directory structure follows Hive conventions (`lat_bin=X/lon_bin=Y`)
- **STAC Metadata**: Standard spatiotemporal metadata for data discovery

### GeoParquet Files

Each `.parquet` file contains:
- Original data columns from your CSV
- `geometry` column with Point geometries (WKT format)
- `time` column (datetime64[ns])
- Spatial index for fast queries
- Embedded metadata (CRS, bounds, etc.)

### STAC Metadata

- **collection.json**: Describes the entire campaign
  - Temporal extent
  - Spatial bounds
  - Sensor information (if detected)
  - Summary statistics

- **items/*.json**: Describes each input file
  - Source file metadata
  - Processing timestamp
  - File-level statistics

## Working with Your Data

### Load in Python

```python
import geopandas as gpd
from pathlib import Path

# Read GeoParquet
gdf = gpd.read_parquet("output/my_first_campaign/")

# Check the data
print(f"Total rows: {len(gdf)}")
print(f"CRS: {gdf.crs}")
print(f"Columns: {gdf.columns.tolist()}")

# Plot on map
gdf.plot(figsize=(10, 6), markersize=1)
```

### Query with DuckDB

```bash
# Install DuckDB CLI
pip install duckdb

# Query the data
duckdb << 'EOF'
INSTALL spatial;
LOAD spatial;

SELECT 
  count(*) as total_rows,
  min(time) as start_time,
  max(time) as end_time
FROM read_parquet('output/my_first_campaign/**/*.parquet');
EOF
```

### Open in QGIS

1. Open QGIS
2. Layer → Add Layer → Add Vector Layer
3. Navigate to `output/my_first_campaign/`
4. Select any `.parquet` file
5. Click "Add"

The data will load with correct CRS and geometry!

## Campaign Management

### List Your Campaigns

```bash
oceanstream campaign list
```

### View Campaign Details

```bash
oceanstream campaign show my_first_campaign
```

### Remove a Campaign

```bash
oceanstream campaign remove my_first_campaign
```

**Note**: This removes metadata only, not the actual data files.

## Next Steps

Now that you've processed your first dataset:

1. **[Configuration Guide](configuration.md)** - Customize OceanStream behavior
2. **[Cloud Storage](../guide/features/cloud-storage.md)** - Upload data to Azure/S3/GCS
3. **CLI Reference** - Explore all commands (see `oceanstream --help`)
4. **Python API** - Use OceanStream programmatically

## Common Issues

### "Campaign ID required"

**Problem**: OceanStream couldn't detect campaign_id from your data.

**Solution**: Provide it explicitly:
```bash
oceanstream process geotrack \
  --input-source ./data.csv \
  --campaign-id my_campaign \
  --output-dir ./output
```

### "No CSV files found"

**Problem**: No `.csv` files in the input directory.

**Solution**: 
- Check the directory path
- Ensure files have `.csv` extension (not `.CSV`)
- Use `--input-source` to point to the correct directory

### "Missing required columns"

**Problem**: CSV doesn't have lat/lon/time columns.

**Solution**:
```bash
# First, see what columns exist
oceanstream process geotrack --input-source ./data.csv --list-columns

# The tool will auto-detect standard column names like:
# latitude, lat, LAT, Latitude, LATITUDE
# longitude, lon, LON, Longitude, LONGITUDE
# time, Time, TIME, datetime, timestamp
```

### Output Directory Already Exists

**Problem**: Campaign already processed to that location.

**Solution**: Use `--force-reprocess` to start fresh:
```bash
oceanstream process geotrack \
  --input-source ./data.csv \
  --campaign-id test \
  --output-dir ./output \
  --force-reprocess
```

## Getting Help

Having trouble? Here's how to get help:

1. **Check the docs**: Most issues are covered in our guides
2. **Run with `--verbose`**: See detailed processing information
3. **Use `--dry-run`**: Preview without making changes
4. **GitHub Issues**: [Report bugs or request features](https://github.com/OceanStreamIO/oceanstream-newcli/issues)

---

**Congratulations!** 🎉 You've successfully processed your first oceanographic dataset with OceanStream.

Continue to [Configuration](configuration.md) to customize your setup, or jump to [Cloud Storage](../guide/features/cloud-storage.md) to learn about uploading data.
