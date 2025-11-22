# GeoPandas Integration Guide

[GeoPandas](https://geopandas.org/) is a Python library that makes working with geospatial data in pandas as easy as working with tabular data. It extends pandas DataFrames with spatial operations.

## Overview

GeoPandas can directly read OceanStream's GeoParquet output, providing seamless integration for spatial analysis, visualization, and data manipulation in Python workflows.

## Prerequisites

- **Python**: 3.9+ (3.12+ recommended)
- **GeoPandas**: 0.14.0+
- **Dependencies**: pandas, shapely, fiona, pyproj
- **Operating System**: Windows, macOS, or Linux

## Installation

### Using pip

```bash
# Activate project venv
source venv/bin/activate

# Install GeoPandas and dependencies
pip install geopandas

# For GeoParquet support
pip install pyarrow

# Optional: for plotting
pip install matplotlib contextily
```

### Using conda

```bash
conda create -n oceanstream python=3.12
conda activate oceanstream
conda install -c conda-forge geopandas pyarrow
```

### Verify Installation

```python
import geopandas as gpd
import pyarrow.parquet as pq

print(f"GeoPandas version: {gpd.__version__}")
print(f"PyArrow version: {pq.__version__}")
```

## Basic Usage

### Read GeoParquet File

```python
import geopandas as gpd

# Read single partition
gdf = gpd.read_parquet('output/campaign_id/lat_bin=30/lon_bin=-120/data.parquet')

print(f"Shape: {gdf.shape}")
print(f"CRS: {gdf.crs}")
print(gdf.head())
```

### Read All Partitions

```python
import geopandas as gpd

# Read all spatial bins
gdf = gpd.read_parquet('output/campaign_id/')

print(f"Total features: {len(gdf)}")
print(f"Columns: {gdf.columns.tolist()}")
print(f"Geometry type: {gdf.geometry.type.unique()}")
```

### Read with Filters

Leverage partition columns for efficient reading:

```python
import geopandas as gpd

# Read specific spatial bins only
gdf = gpd.read_parquet(
    'output/campaign_id/',
    filters=[
        ('lat_bin', '==', 30),
        ('lon_bin', 'in', [-120, -121, -122])
    ]
)

print(f"Filtered features: {len(gdf)}")
```

## Data Exploration

### Basic Information

```python
import geopandas as gpd

gdf = gpd.read_parquet('output/campaign_id/')

# Dataset overview
print(gdf.info())

# Spatial extent
print(f"\nBounds: {gdf.total_bounds}")
print(f"CRS: {gdf.crs}")

# Geometry stats
print(f"\nGeometry types: {gdf.geometry.type.value_counts()}")

# Attribute statistics
print("\nTemperature statistics:")
print(gdf['temperature_sea_water'].describe())
```

### View Sample Data

```python
# First few rows
print(gdf.head())

# Random sample
print(gdf.sample(5))

# Specific columns
print(gdf[['time', 'latitude', 'longitude', 'temperature_sea_water']].head())
```

## Spatial Operations

### Spatial Filtering

```python
import geopandas as gpd
from shapely.geometry import box

# Read data
gdf = gpd.read_parquet('output/campaign_id/')

# Define bounding box (minx, miny, maxx, maxy)
bbox = box(-125, 30, -115, 35)

# Filter points within bounding box
gdf_filtered = gdf[gdf.geometry.within(bbox)]

print(f"Points within bbox: {len(gdf_filtered)}")
```

### Buffer Analysis

```python
import geopandas as gpd

gdf = gpd.read_parquet('output/campaign_id/')

# Create 0.1 degree buffer around each point
gdf['buffer'] = gdf.geometry.buffer(0.1)

# Create new GeoDataFrame with buffers
gdf_buffers = gdf.set_geometry('buffer')

# Check for overlapping buffers
overlaps = gdf_buffers.overlay(gdf_buffers, how='intersection')
print(f"Overlapping areas: {len(overlaps)}")
```

### Distance Calculations

```python
import geopandas as gpd
from shapely.geometry import Point

gdf = gpd.read_parquet('output/campaign_id/')

# Reference point
ref_point = Point(-120.5, 30.5)

# Calculate distance to reference (in degrees)
gdf['distance_to_ref'] = gdf.geometry.distance(ref_point)

# Find nearest points
nearest = gdf.nsmallest(10, 'distance_to_ref')
print(nearest[['time', 'latitude', 'longitude', 'distance_to_ref']])
```

### Spatial Joins

```python
import geopandas as gpd
from shapely.geometry import Polygon

# Read oceanographic data
gdf_ocean = gpd.read_parquet('output/campaign_id/')

# Create study zones
zones = gpd.GeoDataFrame({
    'zone_name': ['North', 'Central', 'South'],
    'geometry': [
        Polygon([(-125, 33), (-115, 33), (-115, 35), (-125, 35)]),
        Polygon([(-125, 30), (-115, 30), (-115, 33), (-125, 33)]),
        Polygon([(-125, 27), (-115, 27), (-115, 30), (-125, 30)])
    ]
}, crs='EPSG:4326')

# Spatial join
gdf_joined = gpd.sjoin(gdf_ocean, zones, how='left', predicate='within')

# Statistics by zone
zone_stats = gdf_joined.groupby('zone_name').agg({
    'temperature_sea_water': ['mean', 'std', 'count'],
    'salinity': ['mean', 'std']
})

print(zone_stats)
```

## Temporal Analysis

### Time-based Filtering

```python
import geopandas as gpd
import pandas as pd

gdf = gpd.read_parquet('output/campaign_id/')

# Ensure time column is datetime
gdf['time'] = pd.to_datetime(gdf['time'])

# Filter by date range
start_date = '2023-01-01'
end_date = '2023-12-31'

gdf_2023 = gdf[(gdf['time'] >= start_date) & (gdf['time'] <= end_date)]

print(f"Measurements in 2023: {len(gdf_2023)}")
```

### Time Series Aggregation

```python
import geopandas as gpd
import pandas as pd

gdf = gpd.read_parquet('output/campaign_id/')
gdf['time'] = pd.to_datetime(gdf['time'])

# Set time as index
gdf = gdf.set_index('time')

# Daily resampling
daily_means = gdf['temperature_sea_water'].resample('D').mean()

print("Daily temperature means:")
print(daily_means.head(10))

# Monthly statistics
monthly_stats = gdf.resample('M').agg({
    'temperature_sea_water': ['mean', 'min', 'max', 'std'],
    'salinity': ['mean', 'std']
})

print("\nMonthly statistics:")
print(monthly_stats)
```

## Data Manipulation

### Filtering by Attributes

```python
import geopandas as gpd

gdf = gpd.read_parquet('output/campaign_id/')

# Single condition
warm_water = gdf[gdf['temperature_sea_water'] > 25]

# Multiple conditions
tropical = gdf[
    (gdf['temperature_sea_water'] > 25) &
    (gdf['salinity'] < 35) &
    (gdf['latitude'].between(-10, 10))
]

print(f"Warm water samples: {len(warm_water)}")
print(f"Tropical samples: {len(tropical)}")
```

### Creating New Columns

```python
import geopandas as gpd
import numpy as np

gdf = gpd.read_parquet('output/campaign_id/')

# Temperature in Fahrenheit
gdf['temp_fahrenheit'] = gdf['temperature_sea_water'] * 9/5 + 32

# Classify temperature
gdf['temp_class'] = pd.cut(
    gdf['temperature_sea_water'],
    bins=[-np.inf, 10, 20, 30, np.inf],
    labels=['Cold', 'Cool', 'Warm', 'Hot']
)

# Calculate derived metrics
gdf['abs_latitude'] = gdf['latitude'].abs()

print(gdf[['temperature_sea_water', 'temp_fahrenheit', 'temp_class']].head())
```

### Grouping and Aggregation

```python
import geopandas as gpd

gdf = gpd.read_parquet('output/campaign_id/')

# Group by spatial bin
bin_stats = gdf.groupby(['lat_bin', 'lon_bin']).agg({
    'temperature_sea_water': ['mean', 'std', 'min', 'max'],
    'salinity': ['mean', 'std'],
    'geometry': 'count'  # Count of measurements
}).round(2)

bin_stats.columns = ['_'.join(col) for col in bin_stats.columns]
bin_stats = bin_stats.rename(columns={'geometry_count': 'num_measurements'})

print(bin_stats.head(10))
```

## Visualization

### Basic Plot

```python
import geopandas as gpd
import matplotlib.pyplot as plt

gdf = gpd.read_parquet('output/campaign_id/')

# Simple point plot
fig, ax = plt.subplots(figsize=(12, 8))
gdf.plot(ax=ax, markersize=1, alpha=0.5)
ax.set_title('OceanStream Measurement Locations')
ax.set_xlabel('Longitude')
ax.set_ylabel('Latitude')
plt.tight_layout()
plt.savefig('measurement_locations.png', dpi=150)
```

### Colored by Value

```python
import geopandas as gpd
import matplotlib.pyplot as plt

gdf = gpd.read_parquet('output/campaign_id/')

# Plot colored by temperature
fig, ax = plt.subplots(figsize=(12, 8))
gdf.plot(
    column='temperature_sea_water',
    cmap='RdYlBu_r',  # Red-Yellow-Blue reversed
    legend=True,
    markersize=5,
    alpha=0.6,
    ax=ax
)
ax.set_title('Sea Surface Temperature')
ax.set_xlabel('Longitude')
ax.set_ylabel('Latitude')
plt.tight_layout()
plt.savefig('temperature_map.png', dpi=150)
```

### Interactive Maps

```python
import geopandas as gpd
import folium
from folium import plugins

# Read subset of data
gdf = gpd.read_parquet(
    'output/campaign_id/',
    filters=[('lat_bin', '==', 30)]
).sample(min(1000, len(gdf)))  # Limit for performance

# Create base map
m = folium.Map(
    location=[gdf.latitude.mean(), gdf.longitude.mean()],
    zoom_start=6,
    tiles='OpenStreetMap'
)

# Add heat map
heat_data = [[row.latitude, row.longitude, row.temperature_sea_water] 
             for idx, row in gdf.iterrows() 
             if pd.notna(row.temperature_sea_water)]

plugins.HeatMap(heat_data, radius=15, blur=25).add_to(m)

# Save map
m.save('temperature_heatmap.html')
print("Interactive map saved to temperature_heatmap.html")
```

### Contextual Basemap

```python
import geopandas as gpd
import matplotlib.pyplot as plt
import contextily as ctx

# Read data and ensure CRS
gdf = gpd.read_parquet('output/campaign_id/')
gdf = gdf.to_crs(epsg=3857)  # Web Mercator for basemap

# Plot with basemap
fig, ax = plt.subplots(figsize=(14, 10))
gdf.plot(
    column='temperature_sea_water',
    cmap='RdYlBu_r',
    legend=True,
    markersize=10,
    alpha=0.7,
    ax=ax,
    legend_kwds={'label': 'Temperature (°C)', 'orientation': 'horizontal'}
)

# Add basemap
ctx.add_basemap(
    ax,
    source=ctx.providers.OpenStreetMap.Mapnik,
    zoom=8
)

ax.set_title('Sea Surface Temperature with Basemap', fontsize=16)
ax.set_xlabel('Longitude')
ax.set_ylabel('Latitude')
plt.tight_layout()
plt.savefig('temperature_with_basemap.png', dpi=150)
```

## Export Options

### Export to Different Formats

```python
import geopandas as gpd

gdf = gpd.read_parquet('output/campaign_id/')

# GeoPackage (recommended)
gdf.to_file('oceanstream_data.gpkg', driver='GPKG', layer='measurements')

# Shapefile (column name limitations)
gdf.to_file('oceanstream_data.shp')

# GeoJSON (web-friendly)
gdf.to_file('oceanstream_data.geojson', driver='GeoJSON')

# CSV with WKT geometry
gdf['geometry_wkt'] = gdf.geometry.to_wkt()
gdf[['time', 'latitude', 'longitude', 'temperature_sea_water', 
     'geometry_wkt']].to_csv('oceanstream_data.csv', index=False)
```

### Export Filtered/Processed Data

```python
import geopandas as gpd

gdf = gpd.read_parquet('output/campaign_id/')

# Filter and export
high_quality = gdf[
    (gdf['temperature_sea_water'].between(0, 35)) &
    (gdf['salinity'].between(30, 40))
]

# Export to Parquet (maintains full precision)
high_quality.to_parquet('quality_controlled.parquet')

# Export to CSV
high_quality.to_csv('quality_controlled.csv', index=False)
```

## Integration with Other Libraries

### With Pandas

```python
import geopandas as gpd
import pandas as pd

gdf = gpd.read_parquet('output/campaign_id/')

# Convert to regular DataFrame (loses geometry)
df = pd.DataFrame(gdf.drop(columns='geometry'))

# Merge with external data
metadata = pd.read_csv('campaign_metadata.csv')
merged = gdf.merge(metadata, on='platform_id', how='left')
```

### With NumPy

```python
import geopandas as gpd
import numpy as np

gdf = gpd.read_parquet('output/campaign_id/')

# Extract coordinates as array
coords = np.array([[pt.x, pt.y] for pt in gdf.geometry])

# Statistical operations
mean_temp = np.mean(gdf['temperature_sea_water'].dropna())
std_temp = np.std(gdf['temperature_sea_water'].dropna())

print(f"Mean temperature: {mean_temp:.2f}°C ± {std_temp:.2f}°C")
```

### With Scikit-learn

```python
import geopandas as gpd
from sklearn.cluster import DBSCAN
import numpy as np

gdf = gpd.read_parquet('output/campaign_id/')

# Extract coordinates
coords = np.array([[pt.x, pt.y] for pt in gdf.geometry])

# Cluster points using DBSCAN
clustering = DBSCAN(eps=0.5, min_samples=10).fit(coords)
gdf['cluster'] = clustering.labels_

# Visualize clusters
import matplotlib.pyplot as plt

fig, ax = plt.subplots(figsize=(12, 8))
gdf.plot(column='cluster', cmap='tab20', legend=True, ax=ax, markersize=5)
ax.set_title('Spatial Clusters of Measurements')
plt.tight_layout()
plt.savefig('spatial_clusters.png', dpi=150)

print(f"Found {len(set(clustering.labels_)) - 1} clusters")
```

## Performance Tips

### Efficient Reading

```python
# ✅ EFFICIENT - Read only needed columns
gdf = gpd.read_parquet(
    'output/campaign_id/',
    columns=['geometry', 'time', 'temperature_sea_water', 'salinity']
)

# ✅ EFFICIENT - Use partition filters
gdf = gpd.read_parquet(
    'output/campaign_id/',
    filters=[('lat_bin', '==', 30)]
)

# ❌ INEFFICIENT - Read everything
gdf = gpd.read_parquet('output/campaign_id/')
```

### Memory Management

```python
import geopandas as gpd

# For large datasets, process in chunks
partitions = []
for lat in range(25, 36):
    for lon in range(-130, -110):
        try:
            chunk = gpd.read_parquet(
                'output/campaign_id/',
                filters=[('lat_bin', '==', lat), ('lon_bin', '==', lon)]
            )
            # Process chunk
            result = chunk['temperature_sea_water'].mean()
            partitions.append({'lat_bin': lat, 'lon_bin': lon, 'mean_temp': result})
        except:
            pass

import pandas as pd
results = pd.DataFrame(partitions)
```

### Spatial Indexing

```python
import geopandas as gpd

gdf = gpd.read_parquet('output/campaign_id/')

# Create spatial index for faster spatial queries
gdf.sindex

# Use spatial index for intersection
bbox = ((-125, 30, -115, 35))  # (minx, miny, maxx, maxy)
possible_matches_index = list(gdf.sindex.intersection(bbox))
possible_matches = gdf.iloc[possible_matches_index]

print(f"Spatial index found {len(possible_matches)} candidates")
```

## Verification Steps

```python
import geopandas as gpd

gdf = gpd.read_parquet('output/campaign_id/')

# 1. Check data loaded
print(f"✅ Loaded {len(gdf)} features")

# 2. Check CRS
assert gdf.crs is not None, "CRS should be set"
print(f"✅ CRS: {gdf.crs}")

# 3. Check geometry column
assert 'geometry' in gdf.columns, "Geometry column required"
print(f"✅ Geometry type: {gdf.geometry.type.unique()}")

# 4. Check attributes
required_cols = ['time', 'latitude', 'longitude']
assert all(col in gdf.columns for col in required_cols)
print(f"✅ All required columns present")

# 5. Check spatial extent
print(f"✅ Bounds: {gdf.total_bounds}")
```

## Troubleshooting

### Issue: Cannot read GeoParquet

**Solution**: Install pyarrow
```bash
pip install pyarrow
```

### Issue: CRS not recognized

**Solution**: Set CRS explicitly
```python
gdf = gpd.read_parquet('output/campaign_id/')
gdf = gdf.set_crs('EPSG:4326', allow_override=True)
```

### Issue: Memory error with large datasets

**Solutions**:
- Read with filters to load less data
- Process in chunks
- Use columns parameter to read only needed fields
- Increase system memory or use sampling

### Issue: Slow spatial operations

**Solutions**:
- Ensure spatial index is used: `gdf.sindex`
- Convert to projected CRS for distance calculations
- Use vectorized operations instead of loops

## Example Workflow

### Complete Analysis Pipeline

```python
import geopandas as gpd
import pandas as pd
import matplotlib.pyplot as plt
import numpy as np

# 1. Load data
print("Loading data...")
gdf = gpd.read_parquet('output/campaign_id/')
gdf['time'] = pd.to_datetime(gdf['time'])

# 2. Data quality filtering
print("Filtering data...")
gdf_clean = gdf[
    (gdf['temperature_sea_water'].between(-2, 40)) &
    (gdf['salinity'].between(0, 45))
].copy()

print(f"Removed {len(gdf) - len(gdf_clean)} outliers")

# 3. Spatial statistics by bin
print("Calculating spatial statistics...")
spatial_stats = gdf_clean.groupby(['lat_bin', 'lon_bin']).agg({
    'temperature_sea_water': ['mean', 'std', 'count'],
    'salinity': ['mean', 'std']
}).reset_index()

# 4. Temporal analysis
print("Temporal analysis...")
gdf_clean_indexed = gdf_clean.set_index('time')
daily_temp = gdf_clean_indexed['temperature_sea_water'].resample('D').mean()

# 5. Create visualizations
print("Creating visualizations...")
fig, axes = plt.subplots(2, 2, figsize=(16, 12))

# Spatial distribution
gdf_clean.plot(ax=axes[0, 0], markersize=1, alpha=0.3)
axes[0, 0].set_title('Measurement Locations')

# Temperature map
gdf_clean.plot(column='temperature_sea_water', cmap='RdYlBu_r', 
               legend=True, ax=axes[0, 1], markersize=3)
axes[0, 1].set_title('Sea Surface Temperature')

# Time series
daily_temp.plot(ax=axes[1, 0])
axes[1, 0].set_title('Daily Mean Temperature')
axes[1, 0].set_ylabel('Temperature (°C)')
axes[1, 0].grid(True)

# Temperature histogram
gdf_clean['temperature_sea_water'].hist(bins=50, ax=axes[1, 1])
axes[1, 1].set_title('Temperature Distribution')
axes[1, 1].set_xlabel('Temperature (°C)')
axes[1, 1].set_ylabel('Frequency')

plt.tight_layout()
plt.savefig('comprehensive_analysis.png', dpi=150)

# 6. Export results
print("Exporting results...")
gdf_clean.to_parquet('oceanstream_clean.parquet')
spatial_stats.to_csv('spatial_statistics.csv', index=False)
daily_temp.to_csv('daily_temperatures.csv')

print("\n✅ Analysis complete!")
print(f"Total measurements: {len(gdf)}")
print(f"Clean measurements: {len(gdf_clean)}")
print(f"Date range: {gdf_clean['time'].min()} to {gdf_clean['time'].max()}")
print(f"Temperature range: {gdf_clean['temperature_sea_water'].min():.2f} to {gdf_clean['temperature_sea_water'].max():.2f}°C")
```

## Additional Resources

- [GeoPandas Documentation](https://geopandas.org/)
- [GeoPandas Examples](https://geopandas.org/en/stable/gallery/index.html)
- [Shapely Manual](https://shapely.readthedocs.io/)
- [PyArrow Documentation](https://arrow.apache.org/docs/python/)
