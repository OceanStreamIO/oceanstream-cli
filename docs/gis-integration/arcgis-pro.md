# ArcGIS Pro Integration Guide

[ArcGIS Pro](https://www.esri.com/en-us/arcgis/products/arcgis-pro/) is Esri's professional desktop GIS application for visualizing, analyzing, and managing geospatial data.

## Overview

ArcGIS Pro supports GeoParquet files through its GDAL/OGR integration. OceanStream's GeoParquet output can be loaded as feature classes with full attribute support.

## Prerequisites

- **ArcGIS Pro Version**: 3.0+ (recommended 3.2+)
- **GDAL Support**: Built-in GDAL with Parquet driver
- **License**: Basic, Standard, or Advanced license
- **Operating System**: Windows 10/11 (64-bit)

### Check Parquet Support

Open ArcGIS Pro Python environment:

```python
import arcpy
import os

# Check GDAL version
print(arcpy.GetInstallInfo()['Version'])

# Test Parquet support
arcpy.env.workspace = r"C:\path\to\output\campaign_id"
```

## Installation

1. Download from [Esri Downloads](https://www.esri.com/en-us/arcgis/products/arcgis-pro/trial)
2. Install following Esri's installation guide
3. Activate license (sign in with ArcGIS account)
4. Install any required extensions

## Loading GeoParquet Data

### Method 1: Add Data Button

1. Generate OceanStream output:
   ```bash
   oceanstream process geotrack \
     --input-source ./data/sample.csv \
     --output-dir ./output
   ```

2. Open ArcGIS Pro and create a new project
3. Click **Add Data** button on the **Map** tab
4. Navigate to: `output/campaign_id/lat_bin=X/lon_bin=Y/`
5. Select `.parquet` file(s)
6. Click **OK** to add to map

### Method 2: Catalog Pane

1. Open **Catalog** pane (View → Catalog Pane)
2. Navigate to your output folder
3. Expand the campaign_id folder
4. Drag and drop `.parquet` files onto the map

### Method 3: Python Toolbox

Create a custom Python toolbox to load partitioned data:

```python
import arcpy
from pathlib import Path

class LoadOceanStreamData:
    def __init__(self):
        self.label = "Load OceanStream GeoParquet"
        self.description = "Load partitioned GeoParquet data"
        
    def execute(self, parameters, messages):
        campaign_dir = parameters[0].valueAsText
        output_gdb = parameters[1].valueAsText
        
        # Find all parquet files
        parquet_files = list(Path(campaign_dir).rglob("*.parquet"))
        
        # Create feature dataset
        arcpy.CreateFeatureDataset_management(
            output_gdb, 
            "oceanstream_data",
            arcpy.SpatialReference(4326)  # WGS84
        )
        
        # Load each partition
        for pf in parquet_files:
            fc_name = f"data_{pf.parent.parent.name}_{pf.parent.name}"
            arcpy.conversion.TableToTable(
                str(pf), 
                f"{output_gdb}/oceanstream_data",
                fc_name
            )
```

### Method 4: OGR2OGR Conversion

Convert to File Geodatabase first:

```bash
# Convert GeoParquet to FGDB
ogr2ogr -f "OpenFileGDB" \
  output.gdb \
  output/campaign_id/lat_bin=30/lon_bin=-120/data.parquet \
  -nln oceanstream_data

# Then open in ArcGIS Pro
```

## Working with Attributes

### View Attribute Table

1. Right-click layer in **Contents** pane
2. Select **Attribute Table**
3. All CSV/GeoCSV columns are preserved:
   - `time` - Timestamp (stored as Date field)
   - `Shape` - Point geometry
   - `latitude`, `longitude` - Coordinates
   - Sensor measurements
   - Metadata fields

### Field Calculations

1. Open **Attribute Table**
2. Right-click column header → **Calculate Field**
3. Example - Flag high temperatures:
   ```python
   def flag_temp(temp):
       return "High" if temp > 25 else "Normal"
   
   flag_temp(!temperature_sea_water!)
   ```

### Select by Attributes

1. Click **Select by Attributes** on **Map** tab
2. Build query:
   ```sql
   temperature_sea_water > 20 AND salinity < 35
   ```
3. Click **Run**

## Spatial Analysis

### Select by Location

1. **Map** tab → **Select by Location**
2. Select features from: OceanStream layer
3. That intersect: Study area polygon
4. Click **Run**

### Spatial Join

Join oceanographic data with other spatial layers:

1. **Analysis** tab → **Tools** → **Spatial Join**
2. Target: OceanStream point layer
3. Join: Polygon layer (e.g., marine regions)
4. Match option: Within
5. Output: New feature class with combined attributes

### Interpolation

Create continuous surfaces from point measurements:

1. **Analysis** tab → **Tools** → Search "IDW" or "Kriging"
2. Input: OceanStream point layer
3. Z value field: `temperature_sea_water`
4. Output: Raster surface of interpolated temperatures

### Hot Spot Analysis

Identify statistically significant clusters:

1. **Analysis** tab → **Tools** → "Hot Spot Analysis (Getis-Ord Gi*)"
2. Input: OceanStream layer
3. Analysis field: `temperature_sea_water`
4. Output: Shows hot and cold spot clusters

## Visualization

### Symbology

1. Right-click layer → **Symbology**
2. Choose **Graduated Colors**
3. Field: `temperature_sea_water`
4. Color scheme: Temperature (red-yellow-blue)
5. Classes: Adjust number and breaks
6. Click **Apply**

### Time Slider

Animate data through time:

1. Right-click layer → **Properties**
2. Go to **Time** tab
3. Enable time
4. Layer time: Each feature has a single time
5. Time field: `time`
6. Click **OK**
7. Enable **Time Slider** from **Map** tab
8. Use slider to animate through time

### 3D Visualization

Display data in 3D scene:

1. Insert → **New Map** → **New Local Scene**
2. Add OceanStream layer
3. Right-click layer → **Properties** → **Elevation**
4. Set elevation from: `depth` field (if available)
5. Or use constant elevation (e.g., sea surface = 0)

## Data Management

### Export to Geodatabase

1. Right-click layer → **Data** → **Export Features**
2. Output location: File Geodatabase
3. Output name: `oceanstream_export`
4. Click **OK**

### Create Feature Class

Permanently store in geodatabase:

```python
import arcpy

# Set workspace
arcpy.env.workspace = r"C:\project\data.gdb"

# Copy features from Parquet
arcpy.conversion.FeatureClassToFeatureClass(
    r"C:\output\campaign_id\lat_bin=30\lon_bin=-120\data.parquet",
    arcpy.env.workspace,
    "oceanstream_data"
)

# Add spatial index
arcpy.management.AddSpatialIndex("oceanstream_data")
```

### Merge Multiple Partitions

Combine multiple spatial bins:

```python
import arcpy
from pathlib import Path

# Find all parquet files
campaign_dir = Path(r"C:\output\campaign_id")
parquet_files = [str(p) for p in campaign_dir.rglob("*.parquet")]

# Merge into single feature class
arcpy.management.Merge(
    parquet_files,
    r"C:\project\data.gdb\oceanstream_merged"
)
```

## ArcPy Automation

### Load and Process Data

```python
import arcpy
from pathlib import Path

# Set up workspace
arcpy.env.workspace = r"C:\project\data.gdb"
arcpy.env.overwriteOutput = True

# Load GeoParquet
parquet_path = r"C:\output\campaign_id\lat_bin=30\lon_bin=-120\data.parquet"
feature_class = "oceanstream_data"

# Import data
arcpy.conversion.TableToTable(parquet_path, arcpy.env.workspace, feature_class)

# Create XY event layer from lat/lon
arcpy.management.MakeXYEventLayer(
    feature_class,
    "longitude",
    "latitude",
    "oceanstream_points",
    arcpy.SpatialReference(4326)
)

# Calculate statistics
result = arcpy.analysis.Statistics(
    "oceanstream_points",
    "temperature_stats",
    [["temperature_sea_water", "MEAN"], 
     ["temperature_sea_water", "MIN"],
     ["temperature_sea_water", "MAX"]]
)

# Print results
with arcpy.da.SearchCursor("temperature_stats", ["MEAN_temperature_sea_water"]) as cursor:
    for row in cursor:
        print(f"Mean temperature: {row[0]:.2f}°C")
```

### Batch Processing

Process multiple campaigns:

```python
import arcpy
from pathlib import Path

output_root = Path(r"C:\output")
gdb_path = r"C:\project\analysis.gdb"

# Find all campaign directories
campaigns = [d for d in output_root.iterdir() if d.is_dir()]

for campaign in campaigns:
    print(f"Processing {campaign.name}...")
    
    # Find parquet files
    parquet_files = list(campaign.rglob("*.parquet"))
    
    if parquet_files:
        # Merge all partitions
        arcpy.management.Merge(
            [str(pf) for pf in parquet_files],
            f"{gdb_path}\\{campaign.name}"
        )
        
        print(f"  Loaded {len(parquet_files)} partitions")
```

## Performance Tips

### Use File Geodatabase

Convert to FGDB for better ArcGIS Pro performance:

```python
import arcpy

arcpy.conversion.FeatureClassToFeatureClass(
    r"C:\output\campaign_id\data.parquet",
    r"C:\project\data.gdb",
    "oceanstream_optimized"
)

# Add indexes
arcpy.management.AddIndex(
    "oceanstream_optimized",
    ["time", "platform_id"],
    "idx_time_platform"
)
```

### Spatial Indexing

Always add spatial indexes:

```python
arcpy.management.AddSpatialIndex("oceanstream_data")
```

### Filter Before Loading

Use SQL to filter large datasets:

```python
# Create layer with definition query
arcpy.management.MakeFeatureLayer(
    "oceanstream_data",
    "filtered_layer",
    "temperature_sea_water > 20 AND time >= timestamp '2023-01-01'"
)
```

## Verification Steps

After loading OceanStream data, verify:

1. **Geometry**: ✅ Points display at correct locations (WGS84)
2. **Attributes**: ✅ All CSV columns present in attribute table
3. **Time Field**: ✅ Time field recognized as Date type
4. **Spatial Reference**: ✅ Coordinate system is GCS_WGS_1984
5. **Feature Count**: ✅ Number of features matches expected count

## Troubleshooting

### Issue: Parquet files not recognized

**Solution**: Use OGR2OGR to convert to FGDB first
```bash
ogr2ogr -f "OpenFileGDB" output.gdb data.parquet
```

### Issue: Time field not recognized as Date

**Solution**: Convert in ArcGIS Pro
```python
import arcpy

# Add new date field
arcpy.management.AddField("oceanstream_data", "time_date", "DATE")

# Convert string to date
arcpy.management.CalculateField(
    "oceanstream_data",
    "time_date",
    "!time!",
    "PYTHON3"
)
```

### Issue: Coordinate system not detected

**Solution**: Define projection explicitly
```python
arcpy.management.DefineProjection(
    "oceanstream_data",
    arcpy.SpatialReference(4326)  # WGS84
)
```

### Issue: Large datasets slow to load

**Solutions**:
- Load specific spatial bins only
- Convert to FGDB with spatial index
- Use definition queries to filter data
- Consider using enterprise geodatabase for very large datasets

## Example Workflow

### Marine Survey Analysis

1. **Load Data**:
   ```bash
   oceanstream process geotrack \
     --input-source ./survey_data/ \
     --output-dir ./arcgis/input
   ```

2. **Convert to FGDB**:
   ```python
   import arcpy
   from pathlib import Path
   
   gdb = r"C:\project\marine_survey.gdb"
   arcpy.management.CreateFileGDB(r"C:\project", "marine_survey.gdb")
   
   parquet_files = list(Path(r"C:\arcgis\input\campaign_id").rglob("*.parquet"))
   arcpy.management.Merge([str(p) for p in parquet_files], f"{gdb}\\survey_points")
   ```

3. **Spatial Join with Zones**:
   - Add marine zone polygons
   - Spatial Join → survey_points within zones
   - Result: Points attributed with zone information

4. **Calculate Statistics by Zone**:
   ```python
   arcpy.analysis.Statistics(
       "survey_points_joined",
       "zone_statistics",
       [["temperature_sea_water", "MEAN"], ["salinity", "MEAN"]],
       "zone_name"
   )
   ```

5. **Create Interpolated Surface**:
   - Geostatistical Analyst → IDW or Kriging
   - Input: survey_points
   - Z value: temperature_sea_water
   - Output: Temperature raster

6. **Export Results**:
   - Feature classes to geodatabase
   - Rasters to GeoTIFF
   - Create map layout with results

## Model Builder

Automate workflows with Model Builder:

1. **Analysis** tab → **ModelBuilder**
2. Add tools in sequence:
   - **Merge** → Combine partitions
   - **Add Spatial Index** → Index merged data
   - **Interpolate** → Create surface
   - **Clip** → Clip to study area
3. Save model for reuse
4. Export to Python script

## Publishing to ArcGIS Online

Share data and maps online:

1. Right-click layer → **Sharing** → **Share As Web Layer**
2. Choose layer type: **Feature Layer**
3. Configure settings:
   - Summary and tags
   - Sharing level (private/organization/public)
   - Feature access (query, editing)
4. Click **Publish**
5. Access via ArcGIS Online or Portal

## Additional Resources

- [ArcGIS Pro Documentation](https://pro.arcgis.com/en/pro-app/latest/help/)
- [ArcPy Reference](https://pro.arcgis.com/en/pro-app/latest/arcpy/main/arcgis-pro-arcpy-reference.htm)
- [Esri Training Catalog](https://www.esri.com/training/)
- [ArcGIS Pro Python Reference](https://pro.arcgis.com/en/pro-app/latest/arcpy/)
