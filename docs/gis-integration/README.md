# GIS Tool Integration Guide

This section provides detailed guides for integrating OceanStream GeoParquet output with popular GIS tools and frameworks.

## Overview

OceanStream generates cloud-optimized GeoParquet files with spatial binning and STAC metadata, designed to work seamlessly with modern GIS tools. Our output format follows industry standards to ensure maximum compatibility.

## Output Formats

- **GeoParquet**: Cloud-optimized columnar format with embedded geometry (WKT POINT)
- **Spatial Binning**: Hive-partitioned by 1° x 1° lat/lon bins (`lat_bin=X/lon_bin=Y/`)
- **STAC Metadata**: Standard SpatioTemporal Asset Catalog for discovery and cataloging
- **PMTiles** (Optional): Vector tiles for web-based visualization

## Supported GIS Tools

### Desktop GIS
1. **[QGIS](qgis.md)** - Open-source desktop GIS with native GeoParquet support
2. **[ArcGIS Pro](arcgis-pro.md)** - Professional ESRI desktop GIS platform

### Web-Based GIS
3. **[Leaflet + PMTiles](leaflet-pmtiles.md)** - Lightweight web mapping with vector tiles
4. **[Mapbox GL JS](mapbox-gl-js.md)** - Interactive web maps with PMTiles support
5. **[STAC Browser](stac-browser.md)** - Web interface for browsing STAC catalogs

### Cloud-Native Data Tools
6. **[DuckDB](duckdb.md)** - In-process SQL analytics on GeoParquet
7. **[Apache Sedona](apache-sedona.md)** - Distributed spatial analytics on Spark
8. **[GeoPandas](geopandas.md)** - Python spatial data analysis

### Programming & Analysis
9. **[Python Workflows](python-workflows.md)** - General Python-based analysis patterns
10. **[R Workflows](r-workflows.md)** - R spatial analysis with sf and arrow

## Quick Start

### Load GeoParquet in QGIS
```bash
# Generate test data
oceanstream process geotrack --input-source ./data/sample.csv --output-dir ./output

# Open QGIS and add vector layer
# Navigate to: output/campaign_id/lat_bin=X/lon_bin=Y/*.parquet
```

### Query with DuckDB
```sql
INSTALL spatial;
LOAD spatial;

SELECT 
    time, 
    ST_AsText(ST_GeomFromText(geometry)) as point,
    temperature_sea_water,
    salinity
FROM read_parquet('output/campaign_id/**/*.parquet')
WHERE lat_bin = 30 AND lon_bin = -120
LIMIT 10;
```

### Load in Python
```python
import geopandas as gpd

# Read all partitions
gdf = gpd.read_parquet('output/campaign_id/')

# Filter by spatial bin
gdf_filtered = gpd.read_parquet(
    'output/campaign_id/',
    filters=[('lat_bin', '==', 30), ('lon_bin', '==', -120)]
)
```

## Integration Testing

Each tool guide includes:
- **Prerequisites**: Required software and versions
- **Setup Instructions**: Step-by-step configuration
- **Usage Examples**: Common workflows and operations
- **Verification Steps**: How to confirm correct integration
- **Troubleshooting**: Common issues and solutions

## STAC Metadata Integration

Our STAC metadata enables:
- **Discovery**: Search across campaigns, platforms, and sensors
- **Temporal Filtering**: Query by date ranges
- **Spatial Filtering**: Find data by geographic bounds
- **Provenance Tracking**: Access attribution and source information

See [STAC Browser Guide](stac-browser.md) for catalog browsing, or use programmatic access with [pystac-client](python-workflows.md#stac-access).

## Performance Considerations

### Spatial Partitioning
- Data is partitioned by 1° x 1° lat/lon bins
- Use partition filters for efficient queries
- Avoid reading all partitions when possible

### Compression
- Default: Snappy compression (balanced speed/size)
- Alternative: Gzip for better compression ratio
- No compression: Faster reads, larger files

### Memory Management
- Use chunked reading for large datasets
- Filter at read time using partition columns
- Leverage spatial indexing in analysis tools

## Contributing

Found an issue with a specific tool integration? Please:
1. Check the troubleshooting section in the tool's guide
2. Open an issue with tool version and error details
3. Submit a PR with fixes or improvements

## Additional Resources

- [GeoParquet Specification](https://geoparquet.org/)
- [STAC Specification](https://stacspec.org/)
- [PMTiles Specification](https://docs.protomaps.com/pmtiles/)
- [OceanStream Main Documentation](../../README.md)
