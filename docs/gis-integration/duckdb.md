# DuckDB Integration Guide

[DuckDB](https://duckdb.org/) is an in-process SQL OLAP database with excellent support for analyzing Parquet files, including GeoParquet with spatial extensions.

## Overview

DuckDB can directly query OceanStream's GeoParquet output without loading data into memory. It's ideal for exploratory analysis, filtering, aggregation, and spatial queries on large datasets.

## Prerequisites

- **DuckDB Version**: 0.9.0+ (recommended 0.10.0+)
- **Extensions**: `spatial` extension for geometry operations
- **Python** (optional): For integration with Python workflows
- **Operating System**: Windows, macOS, or Linux

## Installation

### Command Line Interface

```bash
# macOS (Homebrew)
brew install duckdb

# Linux (download binary)
wget https://github.com/duckdb/duckdb/releases/latest/download/duckdb_cli-linux-amd64.zip
unzip duckdb_cli-linux-amd64.zip

# Windows (download from releases)
# https://github.com/duckdb/duckdb/releases
```

### Python Library

```bash
# Install via pip
pip install duckdb

# Or in project venv
source venv/bin/activate
pip install duckdb
```

## Basic Usage

### CLI: Query GeoParquet Files

```sql
-- Start DuckDB
duckdb

-- Install and load spatial extension
INSTALL spatial;
LOAD spatial;

-- Query GeoParquet file
SELECT 
    time,
    latitude,
    longitude,
    temperature_sea_water,
    salinity
FROM read_parquet('output/campaign_id/lat_bin=30/lon_bin=-120/*.parquet')
LIMIT 10;
```

### Query All Partitions

```sql
-- Use glob pattern to query all spatial bins
SELECT 
    COUNT(*) as total_measurements,
    AVG(temperature_sea_water) as mean_temp,
    MIN(time) as start_time,
    MAX(time) as end_time
FROM read_parquet('output/campaign_id/**/*.parquet');
```

### Spatial Queries

```sql
-- Install spatial extension
INSTALL spatial;
LOAD spatial;

-- Parse WKT geometry and calculate distances
SELECT 
    time,
    ST_AsText(ST_GeomFromText(geometry)) as point,
    temperature_sea_water,
    -- Distance from a reference point (in degrees)
    ST_Distance(
        ST_GeomFromText(geometry),
        ST_Point(-120.5, 30.5)
    ) as distance_from_ref
FROM read_parquet('output/campaign_id/lat_bin=30/lon_bin=-120/*.parquet')
WHERE ST_Distance(
    ST_GeomFromText(geometry),
    ST_Point(-120.5, 30.5)
) < 1.0  -- Within ~111 km
ORDER BY distance_from_ref
LIMIT 20;
```

## Python Integration

### Basic Query

```python
import duckdb

# Connect to DuckDB (in-memory)
con = duckdb.connect()

# Install spatial extension
con.execute("INSTALL spatial")
con.execute("LOAD spatial")

# Query GeoParquet
result = con.execute("""
    SELECT 
        time,
        latitude,
        longitude,
        temperature_sea_water,
        salinity
    FROM read_parquet('output/campaign_id/**/*.parquet')
    WHERE temperature_sea_water > 20
    ORDER BY time
    LIMIT 100
""").fetchall()

print(f"Found {len(result)} measurements")
for row in result[:5]:
    print(row)
```

### Query to Pandas DataFrame

```python
import duckdb
import pandas as pd

con = duckdb.connect()

# Query directly to pandas
df = con.execute("""
    SELECT 
        time,
        latitude,
        longitude,
        temperature_sea_water,
        salinity,
        lat_bin,
        lon_bin
    FROM read_parquet('output/campaign_id/**/*.parquet')
    WHERE lat_bin = 30 AND lon_bin BETWEEN -125 AND -115
""").df()

print(df.head())
print(f"\nShape: {df.shape}")
print(f"Columns: {df.columns.tolist()}")
```

### Partition-Aware Queries

Leverage Hive partitioning for efficient filtering:

```python
import duckdb

con = duckdb.connect()

# Query specific spatial bins using partition columns
query = """
    SELECT 
        lat_bin,
        lon_bin,
        COUNT(*) as measurement_count,
        AVG(temperature_sea_water) as mean_temp,
        AVG(salinity) as mean_salinity
    FROM read_parquet('output/campaign_id/**/*.parquet', hive_partitioning=1)
    WHERE lat_bin BETWEEN 25 AND 35
      AND lon_bin BETWEEN -130 AND -110
    GROUP BY lat_bin, lon_bin
    ORDER BY lat_bin, lon_bin
"""

result = con.execute(query).df()
print(result)
```

## Common Analysis Patterns

### Time Series Aggregation

```sql
-- Hourly averages
SELECT 
    DATE_TRUNC('hour', time) as hour,
    COUNT(*) as num_measurements,
    AVG(temperature_sea_water) as mean_temp,
    STDDEV(temperature_sea_water) as stddev_temp,
    AVG(salinity) as mean_salinity
FROM read_parquet('output/campaign_id/**/*.parquet')
WHERE temperature_sea_water IS NOT NULL
GROUP BY hour
ORDER BY hour;
```

```python
import duckdb
import matplotlib.pyplot as plt

con = duckdb.connect()
con.execute("INSTALL spatial")
con.execute("LOAD spatial")

# Daily time series
df = con.execute("""
    SELECT 
        DATE_TRUNC('day', time) as date,
        AVG(temperature_sea_water) as mean_temp,
        MIN(temperature_sea_water) as min_temp,
        MAX(temperature_sea_water) as max_temp
    FROM read_parquet('output/campaign_id/**/*.parquet')
    GROUP BY date
    ORDER BY date
""").df()

# Plot
plt.figure(figsize=(12, 6))
plt.plot(df['date'], df['mean_temp'], label='Mean')
plt.fill_between(df['date'], df['min_temp'], df['max_temp'], alpha=0.3)
plt.xlabel('Date')
plt.ylabel('Temperature (°C)')
plt.title('Sea Surface Temperature Time Series')
plt.legend()
plt.grid(True)
plt.tight_layout()
plt.savefig('temperature_timeseries.png')
```

### Spatial Statistics

```sql
-- Statistics by spatial bin
SELECT 
    lat_bin,
    lon_bin,
    COUNT(*) as num_measurements,
    AVG(temperature_sea_water) as mean_temp,
    MIN(temperature_sea_water) as min_temp,
    MAX(temperature_sea_water) as max_temp,
    AVG(salinity) as mean_salinity
FROM read_parquet('output/campaign_id/**/*.parquet', hive_partitioning=1)
GROUP BY lat_bin, lon_bin
HAVING num_measurements > 100
ORDER BY mean_temp DESC;
```

### Filtering and Export

```python
import duckdb

con = duckdb.connect()

# Filter high-quality measurements
query = """
    COPY (
        SELECT *
        FROM read_parquet('output/campaign_id/**/*.parquet')
        WHERE temperature_sea_water BETWEEN -2 AND 35
          AND salinity BETWEEN 0 AND 40
          AND quality_flag = 'good'
    ) TO 'filtered_data.parquet' (FORMAT PARQUET)
"""

con.execute(query)
print("Filtered data exported to filtered_data.parquet")
```

### Join with Other Data

```python
import duckdb
import pandas as pd

con = duckdb.connect()

# Load reference data
regions = pd.DataFrame({
    'region_name': ['North Pacific', 'Tropical Pacific', 'South Pacific'],
    'lat_min': [30, -10, -60],
    'lat_max': [60, 10, -10],
    'lon_min': [-180, -180, -180],
    'lon_max': [-100, -100, -100]
})

# Register DataFrame as table
con.register('regions', regions)

# Join oceanographic data with regions
result = con.execute("""
    SELECT 
        r.region_name,
        COUNT(*) as num_measurements,
        AVG(o.temperature_sea_water) as mean_temp,
        AVG(o.salinity) as mean_salinity
    FROM read_parquet('output/campaign_id/**/*.parquet') o
    JOIN regions r ON 
        o.latitude BETWEEN r.lat_min AND r.lat_max
        AND o.longitude BETWEEN r.lon_min AND r.lon_max
    GROUP BY r.region_name
    ORDER BY r.region_name
""").df()

print(result)
```

## Spatial Analysis

### Point-in-Polygon

```python
import duckdb

con = duckdb.connect()
con.execute("INSTALL spatial")
con.execute("LOAD spatial")

# Define study area polygon
study_area_wkt = "POLYGON((-125 30, -115 30, -115 35, -125 35, -125 30))"

# Find points within polygon
query = f"""
    SELECT 
        time,
        latitude,
        longitude,
        temperature_sea_water
    FROM read_parquet('output/campaign_id/**/*.parquet')
    WHERE ST_Within(
        ST_GeomFromText(geometry),
        ST_GeomFromText('{study_area_wkt}')
    )
"""

result = con.execute(query).df()
print(f"Found {len(result)} points within study area")
```

### Nearest Neighbor

```python
import duckdb

con = duckdb.connect()
con.execute("INSTALL spatial")
con.execute("LOAD spatial")

# Find measurements nearest to a location
reference_point = "POINT(-120.5 30.5)"

query = f"""
    SELECT 
        time,
        latitude,
        longitude,
        temperature_sea_water,
        ST_Distance(
            ST_GeomFromText(geometry),
            ST_GeomFromText('{reference_point}')
        ) as distance
    FROM read_parquet('output/campaign_id/**/*.parquet')
    ORDER BY distance
    LIMIT 10
"""

nearest = con.execute(query).df()
print(nearest)
```

### Convex Hull

```python
import duckdb

con = duckdb.connect()
con.execute("INSTALL spatial")
con.execute("LOAD spatial")

# Calculate convex hull of all measurements
query = """
    SELECT ST_AsText(
        ST_ConvexHull(
            ST_Collect(ST_GeomFromText(geometry))
        )
    ) as survey_boundary
    FROM read_parquet('output/campaign_id/**/*.parquet')
"""

hull = con.execute(query).fetchone()[0]
print(f"Survey boundary: {hull}")
```

## Performance Optimization

### Use Partition Filters

```python
# ✅ EFFICIENT - Uses partition pruning
query = """
    SELECT *
    FROM read_parquet('output/campaign_id/**/*.parquet', hive_partitioning=1)
    WHERE lat_bin = 30 AND lon_bin = -120
"""

# ❌ INEFFICIENT - Scans all files
query = """
    SELECT *
    FROM read_parquet('output/campaign_id/**/*.parquet')
    WHERE latitude BETWEEN 30 AND 31 AND longitude BETWEEN -121 AND -119
"""
```

### Column Projection

```python
# ✅ EFFICIENT - Only reads needed columns
query = """
    SELECT time, temperature_sea_water, salinity
    FROM read_parquet('output/campaign_id/**/*.parquet')
"""

# ❌ INEFFICIENT - Reads all columns
query = """
    SELECT *
    FROM read_parquet('output/campaign_id/**/*.parquet')
"""
```

### Persistent Database

For repeated queries, create a persistent database:

```python
import duckdb

# Create database file
con = duckdb.connect('oceanstream_analysis.duckdb')
con.execute("INSTALL spatial")
con.execute("LOAD spatial")

# Create view of parquet data
con.execute("""
    CREATE VIEW oceanstream_data AS
    SELECT *
    FROM read_parquet('output/campaign_id/**/*.parquet', hive_partitioning=1)
""")

# Query the view (faster for repeated queries)
result = con.execute("""
    SELECT COUNT(*), AVG(temperature_sea_water)
    FROM oceanstream_data
    WHERE lat_bin = 30
""").fetchall()

con.close()
```

## Export Options

### Export to CSV

```sql
COPY (
    SELECT time, latitude, longitude, temperature_sea_water, salinity
    FROM read_parquet('output/campaign_id/**/*.parquet')
    WHERE temperature_sea_water > 20
) TO 'warm_water.csv' (HEADER, DELIMITER ',');
```

### Export to Parquet

```sql
-- Aggregate and export
COPY (
    SELECT 
        DATE_TRUNC('day', time) as date,
        lat_bin,
        lon_bin,
        AVG(temperature_sea_water) as mean_temp,
        COUNT(*) as num_measurements
    FROM read_parquet('output/campaign_id/**/*.parquet', hive_partitioning=1)
    GROUP BY date, lat_bin, lon_bin
) TO 'daily_summaries.parquet' (FORMAT PARQUET, COMPRESSION ZSTD);
```

### Export to GeoJSON

```python
import duckdb
import json

con = duckdb.connect()
con.execute("INSTALL spatial")
con.execute("LOAD spatial")

# Export to GeoJSON
query = """
    SELECT ST_AsGeoJSON(
        ST_GeomFromText(geometry)
    ) as geometry,
    time,
    temperature_sea_water,
    salinity
    FROM read_parquet('output/campaign_id/lat_bin=30/lon_bin=-120/*.parquet')
    LIMIT 100
"""

result = con.execute(query).fetchall()

# Build GeoJSON
features = []
for row in result:
    features.append({
        'type': 'Feature',
        'geometry': json.loads(row[0]),
        'properties': {
            'time': str(row[1]),
            'temperature': row[2],
            'salinity': row[3]
        }
    })

geojson = {
    'type': 'FeatureCollection',
    'features': features
}

with open('oceanstream_sample.geojson', 'w') as f:
    json.dump(geojson, f)
```

## Verification Steps

Test DuckDB integration:

```python
import duckdb

con = duckdb.connect()
con.execute("INSTALL spatial")
con.execute("LOAD spatial")

# 1. Check file accessibility
result = con.execute("""
    SELECT COUNT(*) FROM read_parquet('output/campaign_id/**/*.parquet')
""").fetchone()
print(f"✅ Total records: {result[0]}")

# 2. Check geometry column
result = con.execute("""
    SELECT geometry FROM read_parquet('output/campaign_id/**/*.parquet')
    LIMIT 1
""").fetchone()
print(f"✅ Geometry: {result[0]}")

# 3. Check spatial operations
result = con.execute("""
    SELECT ST_AsText(ST_GeomFromText(geometry)) as point
    FROM read_parquet('output/campaign_id/**/*.parquet')
    LIMIT 1
""").fetchone()
print(f"✅ Spatial parsing: {result[0]}")

# 4. Check partition columns
result = con.execute("""
    SELECT DISTINCT lat_bin, lon_bin 
    FROM read_parquet('output/campaign_id/**/*.parquet', hive_partitioning=1)
    ORDER BY lat_bin, lon_bin
""").fetchall()
print(f"✅ Partitions: {len(result)} spatial bins")
```

## Troubleshooting

### Issue: Spatial extension not found

**Solution**: Install spatial extension
```python
import duckdb
con = duckdb.connect()
con.execute("INSTALL spatial")
con.execute("LOAD spatial")
```

### Issue: Geometry column not recognized

**Solution**: Use ST_GeomFromText to parse WKT
```sql
SELECT ST_AsText(ST_GeomFromText(geometry)) FROM ...
```

### Issue: Slow queries on large datasets

**Solutions**:
- Use partition filters: `WHERE lat_bin = X AND lon_bin = Y`
- Select only needed columns
- Use persistent database for repeated queries
- Create indexes if using persistent database

### Issue: Memory errors with large results

**Solutions**:
- Stream results with cursor
- Use LIMIT for testing
- Export to file instead of loading into memory
- Increase DuckDB memory limit:

```python
con = duckdb.connect()
con.execute("SET memory_limit='8GB'")
```

## Example Workflow

### Complete Analysis Pipeline

```python
import duckdb
import pandas as pd
import matplotlib.pyplot as plt

# Connect and setup
con = duckdb.connect('analysis.duckdb')
con.execute("INSTALL spatial")
con.execute("LOAD spatial")

# 1. Data Quality Check
quality = con.execute("""
    SELECT 
        COUNT(*) as total,
        COUNT(CASE WHEN temperature_sea_water IS NOT NULL THEN 1 END) as has_temp,
        COUNT(CASE WHEN salinity IS NOT NULL THEN 1 END) as has_salinity
    FROM read_parquet('output/campaign_id/**/*.parquet')
""").df()
print("Data Quality:")
print(quality)

# 2. Spatial Coverage
coverage = con.execute("""
    SELECT 
        lat_bin,
        lon_bin,
        COUNT(*) as num_measurements
    FROM read_parquet('output/campaign_id/**/*.parquet', hive_partitioning=1)
    GROUP BY lat_bin, lon_bin
    ORDER BY lat_bin, lon_bin
""").df()
print(f"\nSpatial coverage: {len(coverage)} bins")

# 3. Temporal Analysis
temporal = con.execute("""
    SELECT 
        DATE_TRUNC('day', time) as date,
        COUNT(*) as num_measurements,
        AVG(temperature_sea_water) as mean_temp,
        STDDEV(temperature_sea_water) as std_temp
    FROM read_parquet('output/campaign_id/**/*.parquet')
    WHERE temperature_sea_water IS NOT NULL
    GROUP BY date
    ORDER BY date
""").df()

# 4. Export filtered data
con.execute("""
    COPY (
        SELECT *
        FROM read_parquet('output/campaign_id/**/*.parquet')
        WHERE temperature_sea_water BETWEEN 0 AND 35
          AND salinity BETWEEN 30 AND 40
    ) TO 'quality_controlled_data.parquet' (FORMAT PARQUET)
""")

# 5. Visualization
plt.figure(figsize=(14, 5))

plt.subplot(1, 2, 1)
plt.scatter(coverage['lon_bin'], coverage['lat_bin'], 
            s=coverage['num_measurements']/100, alpha=0.6)
plt.xlabel('Longitude Bin')
plt.ylabel('Latitude Bin')
plt.title('Spatial Distribution')
plt.grid(True)

plt.subplot(1, 2, 2)
plt.plot(temporal['date'], temporal['mean_temp'])
plt.xlabel('Date')
plt.ylabel('Temperature (°C)')
plt.title('Mean Daily Temperature')
plt.grid(True)

plt.tight_layout()
plt.savefig('analysis_summary.png', dpi=150)

con.close()
print("\n✅ Analysis complete!")
```

## Additional Resources

- [DuckDB Documentation](https://duckdb.org/docs/)
- [DuckDB Spatial Extension](https://duckdb.org/docs/extensions/spatial.html)
- [DuckDB Python API](https://duckdb.org/docs/api/python/overview)
- [Parquet in DuckDB](https://duckdb.org/docs/data/parquet)
