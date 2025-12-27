# Basic Saildrone Data Processing

This tutorial walks through processing a single Saildrone CSV file from the TPOS 2023 mission.

## Prerequisites

```bash
# Ensure OceanStream is installed
oceanstream --version

# Create working directory
mkdir -p ~/saildrone_tutorial
cd ~/saildrone_tutorial
```

## Step 1: Download Sample Data

We'll use SD1030 data from early November 2023 (near mission end):

```bash
# Download from ERDDAP (replace with actual URL)
wget "https://data.pmel.noaa.gov/pmel/erddap/tabledap/sd1030_2023_tpos.csv?\
time,latitude,longitude,trajectory,\
TEMP_SBE37_MEAN,SAL_SBE37_MEAN,COND_SBE37_MEAN,\
O2_CONC_SBE37_MEAN,O2_SAT_SBE37_MEAN,CHLOR_WETLABS_MEAN,\
TEMP_AIR_MEAN,RH_MEAN,BARO_PRES_MEAN,WIND_SPEED_MEAN,WIND_FROM_MEAN&\
time>=2023-11-06T00:00:00Z&time<=2023-11-08T23:59:59Z" \
  -O sd1030_nov2023.csv

# Or use the local example file
cp /path/to/raw_data/saildrone/sd1030_tpos_2023_*.csv ./
```

## Step 2: Inspect the Data

Let's examine the CSV structure:

```bash
# View first few lines
head -20 sd1030_tpos_2023_*.csv

# Check number of rows
wc -l sd1030_tpos_2023_*.csv

# Count variables (columns)
head -1 sd1030_tpos_2023_*.csv | tr ',' '\n' | wc -l
```

**Expected Output**:
```
time,latitude,longitude,trajectory,SOG,SOG_FILTERED_MEAN,...
UTC,degrees_north,degrees_east,,m s-1,m s-1,...
2023-11-06T00:00:00Z,20.3855728,-154.8648832,1030.0,...
```

**Key Observations**:
- Row 1: Column names (variable names)
- Row 2: Units
- Row 3+: Data values
- Trajectory column contains platform ID (1030)
- Time in ISO 8601 format (UTC)
- ~11,500 rows (3 days at 1-minute sampling)

## Step 3: Process with OceanStream

### Basic Processing

```bash
oceanstream process geotrack \
  --input-source ./sd1030_tpos_2023_*.csv \
  --output-dir ./processed \
  --campaign-id tpos_2023 \
  --platform-id 1030
```

**What happens**:
1. ✅ Detects campaign_id from filename or uses provided value
2. ✅ Reads CSV with automatic column detection
3. ✅ Extracts time, lat, lon, trajectory
4. ✅ Creates WKT geometry from coordinates
5. ✅ Bins data spatially (1° × 1° tiles)
6. ✅ Writes GeoParquet files to `processed/tpos_2023/lat_bin=X/lon_bin=Y/`
7. ✅ Generates STAC metadata in `processed/tpos_2023/stac/`
8. ✅ Registers campaign in `~/.oceanstream/campaigns/tpos_2023/`

### View Processing Results

```bash
# Check output structure
tree processed/

# Output:
# processed/
# └── tpos_2023/
#     ├── lat_bin=20/
#     │   └── lon_bin=-155/
#     │       └── part-0.parquet
#     └── stac/
#         ├── collection.json
#         └── items/
#             └── sd1030_tpos_2023_*.json
```

## Step 4: Inspect Campaign Metadata

```bash
# List all campaigns
oceanstream campaign list

# Show details for tpos_2023
oceanstream campaign show tpos_2023
```

**Expected Output**:
```yaml
Campaign: tpos_2023
  Platform: 1030
  Output Directory: /Users/.../processed/tpos_2023
  Created: 2024-12-02 15:30:45
  Last Updated: 2024-12-02 15:30:45
  Total Files Processed: 1
  Total Runs: 1
  Processed Files:
    - sd1030_tpos_2023_*.csv (11,523 rows, 2.1 MB)
```

## Step 5: Query the GeoParquet Data

### Using Python (GeoPandas)

```python
import geopandas as gpd
from pathlib import Path

# Read GeoParquet
gdf = gpd.read_parquet("processed/tpos_2023/")

print(f"Total observations: {len(gdf)}")
print(f"Columns: {list(gdf.columns)}")
print(f"CRS: {gdf.crs}")
print(f"Spatial extent: {gdf.total_bounds}")

# Basic statistics
print("\nSea Surface Temperature:")
print(gdf['TEMP_SBE37_MEAN'].describe())

# Filter by time
nov6 = gdf[gdf['time'].dt.date == '2023-11-06']
print(f"\nNov 6 observations: {len(nov6)}")

# Spatial query
near_hawaii = gdf[(gdf.latitude > 20) & (gdf.longitude < -154)]
print(f"Near Hawaii: {len(near_hawaii)}")
```

### Using DuckDB

```bash
# Launch DuckDB
duckdb

# Load spatial extension and query
INSTALL spatial;
LOAD spatial;

SELECT 
    COUNT(*) as total_obs,
    MIN(latitude) as min_lat,
    MAX(latitude) as max_lat,
    AVG(TEMP_SBE37_MEAN) as avg_sst,
    AVG(SAL_SBE37_MEAN) as avg_salinity
FROM read_parquet('processed/tpos_2023/**/*.parquet');
```

## Step 6: Verify STAC Metadata

```bash
# View collection metadata
cat processed/tpos_2023/stac/collection.json | jq '.'

# List STAC items
ls processed/tpos_2023/stac/items/

# View item metadata
cat processed/tpos_2023/stac/items/sd1030_*.json | jq '.properties'
```

**Key STAC Fields**:
- `id`: Unique collection identifier
- `extent`: Temporal and spatial bounds
- `summaries`: Available sensors and platform info
- `assets`: Links to GeoParquet files
- `properties`: Campaign, platform, provenance metadata

## Step 7: Visualize in QGIS

1. Open QGIS
2. Go to **Layer → Add Layer → Add Vector Layer**
3. Choose `processed/tpos_2023/lat_bin=20/lon_bin=-155/part-0.parquet`
4. Click **Add**
5. Right-click layer → **Properties → Symbology**
6. Choose "Graduated" style
7. Select `TEMP_SBE37_MEAN` as value
8. Click **Classify**
9. Apply color ramp (e.g., "RdYlBu" reversed for temperature)

**Result**: You'll see the Saildrone track color-coded by sea surface temperature near Hawaii.

## Common Processing Options

### Dry Run (Preview Only)

```bash
oceanstream process geotrack \
  --input-source ./sd1030_tpos_2023_*.csv \
  --output-dir ./processed \
  --campaign-id tpos_2023 \
  --dry-run
```

Shows what would be processed without writing files.

### Custom Spatial Binning

```bash
# Use 0.5° bins for higher resolution
oceanstream process geotrack \
  --input-source ./sd1030_tpos_2023_*.csv \
  --output-dir ./processed_hires \
  --campaign-id tpos_2023_hires \
  --bin-size 0.5
```

### Skip STAC Generation

```bash
oceanstream process geotrack \
  --input-source ./sd1030_tpos_2023_*.csv \
  --output-dir ./processed \
  --campaign-id tpos_2023 \
  --no-stac
```

### List Available Columns

```bash
# See all columns before processing
oceanstream process geotrack \
  --input-source ./sd1030_tpos_2023_*.csv \
  --list-columns
```

## Troubleshooting

### Issue: "campaign_id is required but could not be detected"

**Solution**: Provide explicit campaign ID:
```bash
oceanstream process geotrack \
  --input-source ./data.csv \
  --output-dir ./processed \
  --campaign-id my_campaign  # Add this flag
```

### Issue: "No CSV files found in directory"

**Solution**: Check file extensions and path:
```bash
# Verify files exist
ls -la ./sd1030_*

# Ensure .csv extension (not .CSV or .txt)
mv data.txt data.csv
```

### Issue: Missing required columns (time, latitude, longitude)

**Solution**: OceanStream auto-detects standard column names. If detection fails:
```bash
# Check actual column names
head -1 your_file.csv

# Rename columns if needed (preprocessing)
# Or file an issue for new column name patterns
```

### Issue: Geometry errors or invalid coordinates

**Solution**: Verify coordinate ranges:
```bash
# Latitude should be -90 to 90
# Longitude should be -180 to 180
# OceanStream validates on ingest
```

## Next Steps

Now that you've processed a single file:

<!-- TODO: Add these advanced examples
1. **Multiple Platforms** - Process all three TPOS drones together
2. **Append Workflow** - Add more data to existing campaign
3. **Cloud Upload** - Upload processed data to Azure Blob Storage
-->

## Summary

You've learned to:

- ✅ Download Saildrone data from ERDDAP
- ✅ Process CSV to cloud-optimized GeoParquet
- ✅ Create STAC metadata for discovery
- ✅ Query data with Python/DuckDB
- ✅ Visualize tracks in QGIS
- ✅ Troubleshoot common issues

The processed GeoParquet files are:
- **Cloud-optimized** - Efficient for remote reading
- **Spatially indexed** - Fast bounding-box queries
- **Standards-compliant** - Works with modern GIS tools
- **Metadata-rich** - Self-describing with STAC
