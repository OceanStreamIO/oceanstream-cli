# QGIS Integration Guide

[QGIS](https://qgis.org/) is a free and open-source desktop Geographic Information System (GIS) application that supports viewing, editing, and analyzing geospatial data.

## Overview

QGIS has native support for GeoParquet files through GDAL/OGR. OceanStream's GeoParquet output can be loaded directly as vector layers with full attribute support.

## Prerequisites

- **QGIS Version**: 3.30+ (Long Term Release) or 3.34+ (Latest Release)
- **GDAL Version**: 3.5+ with Parquet driver support
- **Operating System**: Windows, macOS, or Linux

### Check GDAL Support

1. Open QGIS
2. Go to **Settings** → **Options** → **GDAL**
3. Verify Parquet driver is listed in available drivers

Or check from terminal:
```bash
ogrinfo --formats | grep -i parquet
```

Expected output:
```
Parquet -vector- (rw+vs): (Geo)Parquet
```

## Installation

### macOS
```bash
# Install QGIS via Homebrew
brew install --cask qgis

# Or download from official website
# https://qgis.org/download/
```

### Linux (Ubuntu/Debian)
```bash
# Add QGIS repository
sudo add-apt-repository ppa:ubuntugis/ubuntugis-unstable
sudo apt update

# Install QGIS
sudo apt install qgis qgis-plugin-grass
```

### Windows
Download installer from [QGIS Downloads](https://qgis.org/download/) and follow installation wizard.

## Loading GeoParquet Data

### Method 1: Drag and Drop

1. Generate OceanStream output:
   ```bash
   oceanstream process geotrack \
     --input-source ./data/sample.csv \
     --output-dir ./output
   ```

2. Open QGIS
3. Locate your GeoParquet files in file browser
4. Drag and drop `.parquet` files into QGIS map canvas
5. Data will be loaded as a vector layer

### Method 2: Add Vector Layer Dialog

1. Go to **Layer** → **Add Layer** → **Add Vector Layer**
2. Set **Source type** to "File"
3. Click **Browse** button
4. Navigate to: `output/campaign_id/lat_bin=X/lon_bin=Y/`
5. Select one or more `.parquet` files
6. Click **Add**

### Method 3: Load All Partitions

To load all spatial partitions at once:

1. Open **Layer** → **Add Layer** → **Add Vector Layer**
2. For **Source**, use wildcard pattern:
   ```
   output/campaign_id/**/*.parquet
   ```
3. Or use GDAL virtual format (VRT):
   ```bash
   # Create VRT file
   ogrinfo -al output/campaign_id/lat_bin=30/lon_bin=-120/data.parquet
   ```

### Method 4: DB Manager (SQL Queries)

1. Open **Database** → **DB Manager**
2. Select **Virtual Layers** → **Project Layers**
3. Create a new virtual layer with SQL:
   ```sql
   SELECT * FROM "campaign_parquet"
   WHERE temperature_sea_water > 20
   ```

## Working with Attributes

### View Attribute Table

1. Right-click on layer in **Layers Panel**
2. Select **Open Attribute Table**
3. All columns from CSV/GeoCSV are preserved:
   - `time` - Timestamp of measurement
   - `geometry` - Point geometry (WKT format)
   - `latitude`, `longitude` - Coordinates
   - Sensor measurements (temperature, salinity, etc.)
   - Metadata fields (platform_id, campaign_id, etc.)

### Filter by Attributes

1. Right-click layer → **Filter**
2. Use expression builder:
   ```sql
   "temperature_sea_water" > 20 AND "salinity" < 35
   ```
3. Or use **Select by Expression** tool from toolbar

### Field Calculator

Create new calculated fields:

1. Open **Attribute Table**
2. Click **Field Calculator** icon
3. Example - Convert Celsius to Fahrenheit:
   ```python
   "temperature_sea_water" * 9/5 + 32
   ```

## Spatial Operations

### Select by Location

1. **Vector** → **Research Tools** → **Select by Location**
2. Select features from oceanographic layer
3. That intersect/within another layer (e.g., study area polygon)

### Spatial Queries

Use spatial bins for efficient filtering:

1. Right-click layer → **Filter**
2. Filter by partition columns:
   ```sql
   "lat_bin" = 30 AND "lon_bin" BETWEEN -125 AND -115
   ```

### Buffer Analysis

Create buffers around points:

1. **Vector** → **Geoprocessing Tools** → **Buffer**
2. Input: OceanStream point layer
3. Distance: Set buffer distance (e.g., 10 km)
4. Result: Buffered areas around measurement points

## Visualization

### Style by Value

1. Right-click layer → **Properties** → **Symbology**
2. Select **Graduated** style
3. Choose column: `temperature_sea_water`
4. Select color ramp (e.g., YlOrRd for temperature)
5. Click **Classify** to create classes
6. Click **OK**

### Time-based Animation

1. Enable temporal controller:
   - **View** → **Panels** → **Temporal Controller**
2. Layer Properties → **Temporal** tab
3. Enable temporal control
4. Set **Time field**: `time`
5. Use temporal controller to animate through time

### Heatmap Visualization

1. Right-click layer → **Properties** → **Symbology**
2. Select **Heatmap** style
3. Configure:
   - **Radius**: Spatial extent of heat influence
   - **Weight**: Use measurement field (e.g., `temperature_sea_water`)
   - **Color ramp**: Choose appropriate gradient

## Exporting Data

### Export to Other Formats

1. Right-click layer → **Export** → **Save Features As**
2. Choose format:
   - **GeoPackage**: Recommended for local analysis
   - **Shapefile**: Legacy format (limited column names)
   - **GeoJSON**: Web-friendly format
   - **CSV**: Tabular data with WKT geometry

### Export Styled Layer

1. Right-click layer → **Export** → **Save as Layer Definition**
2. Saves layer with style (`.qlr` file)
3. Share with other QGIS users to maintain styling

## STAC Integration

### QGIS STAC Plugin

Install the STAC Browser plugin:

1. **Plugins** → **Manage and Install Plugins**
2. Search for "STAC API Browser"
3. Click **Install Plugin**

Usage:
1. **Web** → **STAC API Browser**
2. Add STAC API endpoint URL
3. Browse collections and items
4. Click items to load associated assets (GeoParquet files)

## Performance Tips

### Spatial Indexing

QGIS automatically creates spatial indexes for vector layers. For better performance:

1. **Vector** → **Data Management Tools** → **Create Spatial Index**
2. Select your layer
3. Index is saved alongside data

### Load Specific Partitions Only

Instead of loading all data:

```bash
# Load only specific spatial bin
output/campaign_id/lat_bin=30/lon_bin=-120/*.parquet
```

### Use Virtual Layers

For complex queries without loading all data:

1. **Layer** → **Add Layer** → **Add/Edit Virtual Layer**
2. Use SQL to filter before loading:
   ```sql
   SELECT * FROM parquet_layer
   WHERE "lat_bin" = 30 
     AND "time" > '2023-01-01'
   ```

## Verification Steps

After loading OceanStream data, verify:

1. **Geometry Display**: ✅ Points appear on map at correct locations
2. **Attribute Table**: ✅ All columns from CSV are present
3. **CRS Detection**: ✅ Coordinate system is EPSG:4326 (WGS84)
4. **Temporal Data**: ✅ Time field recognized as datetime
5. **Symbology**: ✅ Can style by measurement values

## Troubleshooting

### Issue: GeoParquet not recognized

**Solution**: Update GDAL/QGIS to latest version
```bash
# macOS
brew upgrade qgis

# Check GDAL version
gdalinfo --version
# Should be 3.5.0 or higher
```

### Issue: Missing Parquet driver

**Solution**: Rebuild GDAL with Parquet support
```bash
# macOS with Homebrew
brew reinstall gdal --with-parquet

# Linux
sudo apt install libgdal-dev gdal-bin
```

### Issue: Cannot load multiple partitions

**Solution**: Use VRT (Virtual Dataset) or load partitions individually

### Issue: Slow loading with many partitions

**Solution**: 
- Filter by lat_bin/lon_bin before loading
- Use virtual layers with SQL filtering
- Consider merging partitions for specific analysis areas

### Issue: Attribute names truncated

**Solution**: GeoParquet supports full column names (unlike Shapefile). If names appear truncated, check:
- QGIS version (should be 3.30+)
- Layer properties → Fields tab for full names

## Example Workflow

### Analyzing Sea Surface Temperature

1. **Load Data**:
   ```bash
   oceanstream process geotrack \
     --input-source ./saildrone_data/ \
     --output-dir ./analysis/sst
   ```

2. **Load in QGIS**: Add `sst/campaign_id/**/*.parquet`

3. **Filter Data**:
   - Open Attribute Table
   - Filter: `"temperature_sea_water" IS NOT NULL`

4. **Create Temperature Map**:
   - Symbology → Graduated
   - Column: `temperature_sea_water`
   - Color ramp: Spectral (inverted)
   - Classes: 7-10 classes

5. **Add Temporal Animation**:
   - Enable Temporal Controller
   - Set time field: `time`
   - Play animation to see temperature changes

6. **Export Analysis**:
   - Right-click layer → Save As
   - Format: GeoPackage
   - File: `sst_analysis.gpkg`

## Advanced Usage

### Processing Toolbox

Use QGIS Processing algorithms:

1. **Vector** → **Geoprocessing Tools**
2. Examples:
   - **Convex Hull**: Outline of survey area
   - **Voronoi Polygons**: Spatial interpolation zones
   - **Density Analysis**: Point concentration maps

### PyQGIS Scripting

Automate workflows with Python:

```python
from qgis.core import QgsVectorLayer

# Load GeoParquet
uri = "/path/to/output/campaign_id/lat_bin=30/lon_bin=-120/data.parquet"
layer = QgsVectorLayer(uri, "OceanStream Data", "ogr")

if layer.isValid():
    QgsProject.instance().addMapLayer(layer)
    
    # Get feature count
    count = layer.featureCount()
    print(f"Loaded {count} features")
    
    # Iterate features
    for feature in layer.getFeatures():
        temp = feature['temperature_sea_water']
        if temp and temp > 25:
            print(f"High temp: {temp}°C")
```

## Additional Resources

- [QGIS Documentation](https://docs.qgis.org/)
- [QGIS Training Manual](https://docs.qgis.org/latest/en/docs/training_manual/)
- [PyQGIS Cookbook](https://docs.qgis.org/latest/en/docs/pyqgis_developer_cookbook/)
- [GDAL Parquet Driver](https://gdal.org/drivers/vector/parquet.html)
