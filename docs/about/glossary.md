# Glossary

## Terms

**Campaign**
: A collection of related oceanographic data from a single mission or cruise. Used as the top-level organizational unit in OceanStream.

**GeoParquet**
: Columnar file format (Apache Parquet) with geospatial extensions for storing geographic data efficiently.

**Hive Partitioning**
: Data organization strategy using directory structure (e.g., `lat_bin=10/lon_bin=-126/`) for efficient spatial queries.

**PMTiles**
: Vector tile format for web-based map visualization. Stores map data in a single file with spatial indexing.

**R2R**
: Rolling Deck to Repository - a platform for managing oceanographic cruise data.

**Spatial Binning**
: Organizing data into geographic grid cells (default: 1°×1° lat/lon bins).

**STAC**
: SpatioTemporal Asset Catalog - a standard for geospatial metadata that enables data discovery.

**Storage Provider**
: Abstraction layer for uploading data to different backends (Local, Azure, S3, GCS).

## Next Steps

- <!-- TODO: Add FAQ page --> - Common questions
- [Guide](../getting-started/installation.md) - Get started
