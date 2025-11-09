# Geotrack Module

The `oceanstream.geotrack` module provides comprehensive processing capabilities for oceanographic GPS/navigation track data with associated sensor measurements. It converts CSV time-series data into spatially-partitioned GeoParquet datasets with optional STAC metadata and PMTiles vector tile generation.

## Overview

This module is designed to handle large-scale oceanographic survey data (e.g., from Saildrone platforms) and transform it into cloud-optimized, geospatially-indexed formats suitable for analysis, visualization, and distribution.

### Key Features

- **CSV to GeoParquet Conversion**: Reads CSV files with latitude/longitude coordinates and converts them to GeoParquet format with proper geometry encoding
- **Spatial Partitioning**: Automatically bins and partitions data by lat/lon ranges for efficient spatial queries
- **Semantic Metadata**: Maps sensor measurements to CF (Climate and Forecast) conventions with units and standard names
- **STAC Catalog Generation**: Produces SpatioTemporal Asset Catalog (STAC) metadata for dataset discovery
- **PMTiles Vector Tiles**: Generates single-file PMTiles for web map visualization
- **Type Coercion**: Intelligently handles mixed-type columns and converts numeric strings to proper types
- **Azure Integration**: Supports upload to Azure Blob Storage

## Installation

Install with geotrack dependencies:

```bash
pip install -e ".[geotrack]"
```

Or install all optional dependencies:

```bash
pip install -e ".[all]"
```

### Dependencies

Core dependencies for geotrack:
- `geopandas` - Geospatial data manipulation
- `shapely` - Geometry operations
- `pandas` - Data processing
- `pyarrow` - Parquet I/O
- `pystac` - STAC metadata generation

Optional dependencies for tiling:
- GDAL (ogr2ogr) ≥ 3.5 with Parquet and MBTiles/MVT support
- PMTiles CLI tool

## Module Structure

```
oceanstream/geotrack/
├── __init__.py              # Module exports
├── README.md                # This file
├── processor.py             # Main processing orchestration
├── csv_reader.py            # CSV reading and sanitization
├── geoparquet_writer.py     # GeoParquet writing with partitioning
├── binning.py               # Spatial binning logic
└── tiling/                  # Vector tile generation
    ├── __init__.py
    └── pmtiles.py           # PMTiles generation from GeoParquet
```

## Usage

### Command Line Interface

Process geotrack data using the CLI:

```bash
# Basic processing
oceanstream process geotrack \
  --provider saildrone \
  --input-dir ./raw_data \
  --output-dir ./processed

# With custom binning
oceanstream process geotrack \
  --provider saildrone \
  --input-dir ./raw_data \
  --output-dir ./processed \
  --lat-bin-size 5.0 \
  --lon-bin-size 5.0

# Dry run to preview
oceanstream process geotrack \
  --provider saildrone \
  --input-dir ./raw_data \
  --output-dir ./processed \
  --dry-run

# Verbose output
oceanstream process geotrack \
  --provider saildrone \
  --input-dir ./raw_data \
  --output-dir ./processed \
  --verbose
```

### Python API

#### Basic Processing

```python
from oceanstream.geotrack import process
from oceanstream.providers.saildrone import SaildroneProvider

provider = SaildroneProvider()

process(
    provider=provider,
    input_dir="./raw_data",
    output_dir="./processed",
    verbose=True
)
```

#### Using the Processor Class

```python
from oceanstream.geotrack.processor import GeotrackProcessor
from oceanstream.providers.saildrone import SaildroneProvider
from oceanstream.config.settings import Settings

settings = Settings(
    input_dir="./raw_data",
    output_dir="./processed"
)

provider = SaildroneProvider()
processor = GeotrackProcessor(settings, provider)

# Scan input directory
files = processor.scan_input_directory()
print(f"Found {len(files)} CSV files")

# Process files
df = processor.process_files(files)
print(f"Processed {len(df)} rows")

# Apply semantic mapping
semantic_result = processor.apply_semantic_mapping(df)

# Write GeoParquet with partitioning
processor.write_geoparquet_dataset(df, semantic_result)

# Emit STAC metadata
processor.emit_stac_metadata(df, semantic_result)
```

#### Direct CSV Reading

```python
from oceanstream.geotrack.csv_reader import read_csv_files, extract_platform_id

# Read multiple CSV files
files = ["sd1030_data.csv", "sd1033_data.csv"]
df = read_csv_files(files)

# Extract platform ID from filename
platform_id = extract_platform_id("sd1030_tpos_2023.csv")
print(platform_id)  # "sd1030"
```

#### GeoParquet Writing

```python
from oceanstream.geotrack.geoparquet_writer import write_geoparquet
import pandas as pd

# Prepare DataFrame with geometry
df = pd.DataFrame({
    "latitude": [10.0, 10.5, 11.0],
    "longitude": [-150.0, -149.5, -149.0],
    "TEMP_SBE37_MEAN": [18.5, 18.6, 18.7],
    "time": pd.to_datetime(["2023-01-01", "2023-01-02", "2023-01-03"])
})

# Write with automatic partitioning
write_geoparquet(
    df=df,
    output_dir="./geoparquet",
    lat_col="latitude",
    lon_col="longitude",
    lat_bins=10,  # 10-degree bins
    lon_bins=10,
    partition_label="platform=sd1030"
)
```

#### Spatial Binning

```python
from oceanstream.geotrack.binning import suggest_lat_lon_bins_from_data
import pandas as pd

df = pd.DataFrame({
    "latitude": [10.0, 20.0, 30.0],
    "longitude": [-150.0, -140.0, -130.0]
})

# Get suggested bin sizes based on data extent
lat_bins, lon_bins = suggest_lat_lon_bins_from_data(
    df, 
    lat_col="latitude",
    lon_col="longitude",
    target_partitions=100
)

print(f"Suggested bins: lat={lat_bins}°, lon={lon_bins}°")
```

### PMTiles Generation

Generate vector tiles from GeoParquet for web visualization:

```python
from oceanstream.geotrack.tiling import generate_pmtiles_from_geoparquet

# Generate PMTiles
pmtiles_path = generate_pmtiles_from_geoparquet(
    geoparquet_root="./processed/geoparquet",
    pmtiles_path="./tiles/saildrone_2023.pmtiles",
    minzoom=0,
    maxzoom=10,
    layer_name="saildrone_points",
    select_columns=["platform_id", "time", "TEMP_SBE37_MEAN", "SAL_SBE37_MEAN"]
)

print(f"PMTiles generated at: {pmtiles_path}")
```

Upload to Azure:

```python
from oceanstream.geotrack.tiling import upload_pmtiles_to_azure

upload_pmtiles_to_azure(
    pmtiles_path="./tiles/saildrone_2023.pmtiles",
    container_name="tiles",
    blob_name="saildrone/2023/tracks.pmtiles"
)
```

## Data Format Requirements

### Input CSV Format

CSV files must contain:
- **Required columns**: `latitude`, `longitude` (or `LAT`, `LON`, `lat`, `lon`)
- **Optional time column**: `time`, `TIME`, or ISO 8601 datetime string
- **Sensor measurements**: Any additional columns (e.g., `TEMP_SBE37_MEAN`, `SAL_SBE37_MEAN`)

CSV format conventions:
- First row: column names
- Optional second row: units (detected by presence of unit markers like `°C`, `PSU`, `m/s`)
- Remaining rows: data values

Example:

```csv
time,latitude,longitude,TEMP_SBE37_MEAN,SAL_SBE37_MEAN
°C,PSU
2023-01-01T00:00:00Z,10.5,-150.2,18.5,35.1
2023-01-01T01:00:00Z,10.6,-150.1,18.6,35.2
```

### Output GeoParquet Format

The module produces GeoParquet files with:
- **Geometry column**: WKB-encoded POINT geometries (EPSG:4326)
- **Spatial partitioning**: Files organized by lat/lon bins (e.g., `lat_bin=10_20/lon_bin=-160_-150/`)
- **GeoParquet metadata**: Proper geo metadata following OGC GeoParquet 1.0 spec
- **Semantic metadata**: CF convention mappings in Parquet schema metadata
- **Optimized storage**: Snappy compression, row group size ~64MB

Partition structure:

```
geoparquet/
├── lat_bin=10_20/
│   ├── lon_bin=-160_-150/
│   │   └── part-0.parquet
│   └── lon_bin=-150_-140/
│       └── part-0.parquet
└── lat_bin=20_30/
    └── lon_bin=-160_-150/
        └── part-0.parquet
```

## Processing Pipeline Details

### 1. CSV Reading (`csv_reader.py`)

- Scans input directory for CSV files matching provider patterns
- Extracts platform ID from filename
- Detects and skips units row if present
- Handles bad lines gracefully with warnings
- Sanitizes column types (converts numeric strings to proper types)
- Coerces object columns to numeric where possible

### 2. Data Validation

- Checks for required latitude/longitude columns
- Validates geometry creation
- Warns on invalid/null geometries
- Filters out rows with missing coordinates

### 3. Semantic Mapping (`../semantic/semantic.py`)

- Maps measurement columns to CF standard names
- Extracts units from data or column names
- Creates standardized metadata for climate/ocean science interoperability
- Stores mappings in Parquet schema metadata

### 4. Spatial Binning (`binning.py`)

- Calculates optimal lat/lon bin sizes based on data extent
- Creates partition keys for each row
- Ensures efficient spatial indexing for queries

### 5. GeoParquet Writing (`geoparquet_writer.py`)

- Converts lat/lon to WKB Point geometries
- Partitions data by spatial bins
- Writes Parquet files with proper GeoParquet metadata
- Includes geo schema, CRS definition, and bbox
- Optimizes for cloud storage with appropriate chunk sizes

### 6. STAC Metadata (`../stac/emit.py`)

- Generates STAC Collection with spatial/temporal extent
- Creates STAC Item for the dataset
- Includes CF keywords and measurement variables
- Provides links to data assets

### 7. PMTiles Generation (`tiling/pmtiles.py`)

- Reads GeoParquet using GDAL Parquet driver
- Generates vector MBTiles with ogr2ogr
- Converts to PMTiles format (single-file, range-request friendly)
- Supports column selection to reduce tile size
- Optional Azure Blob Storage upload

## Configuration

The module uses `oceanstream.config.settings.Settings` for configuration:

```python
from oceanstream.config.settings import Settings

settings = Settings(
    input_dir="./raw_data",
    output_dir="./processed",
    lat_bin_size=5.0,      # 5-degree latitude bins
    lon_bin_size=5.0,      # 5-degree longitude bins
    emit_stac=True,        # Generate STAC metadata
    storage_type="local",  # or "azure"
    azure_container="data" # if storage_type="azure"
)
```

## Provider Support

The module works with data providers through the `ProviderBase` protocol:

```python
from oceanstream.providers.base import ProviderBase

class CustomProvider(ProviderBase):
    name = "custom"
    supported_modules = ["geotrack"]
    
    def get_filename_pattern(self) -> str:
        return r"^custom_\d+_.*\.csv$"
    
    def extract_platform_id(self, filename: str) -> str:
        # Custom logic to extract platform ID
        return filename.split("_")[1]
```

Current providers:
- **Saildrone**: Supports geotrack processing with standard filename patterns

## Error Handling

The module includes comprehensive error handling:

- **Invalid geometries**: Logged as warnings, rows filtered out
- **Type coercion failures**: Columns converted with error='coerce', NaN for unparseable values
- **Missing columns**: Clear error messages indicating required columns
- **Bad CSV lines**: Skipped with warnings logged
- **Missing CLI tools**: `MissingDependencyError` for ogr2ogr/pmtiles

## Performance Considerations

- **Large datasets**: Data is processed in chunks and partitioned spatially
- **Memory efficiency**: Row group sizes optimized for streaming reads
- **Parallel processing**: GeoParquet partitions can be read/written in parallel
- **Cloud optimization**: Partitioning enables efficient spatial filtering in cloud object stores

## Testing

The module has comprehensive test coverage:

```bash
# Run all geotrack tests
pytest oceanstream/tests/unit/test_*.py -v

# Run specific test modules
pytest oceanstream/tests/unit/test_pipeline.py -v
pytest oceanstream/tests/unit/test_pmtiles.py -v

# Run integration tests
pytest oceanstream/tests/integration/test_cli_geotrack_stac_integration.py -v
```

## Examples

See the `docs/` directory for additional examples:
- `docs/pmtiles.md` - PMTiles generation and web map integration
- `docs/STAC.md` - STAC metadata workflow

## Contributing

When extending the geotrack module:

1. **Follow existing patterns**: Use the processor/reader/writer separation
2. **Add tests**: All new functionality should have unit tests
3. **Update documentation**: Keep this README and docstrings current
4. **Type hints**: Use Python type hints for all functions
5. **Error handling**: Provide clear error messages and graceful degradation

## License

See the main project LICENSE file.

## Support

For issues or questions:
- Open an issue on the project repository
- Check existing documentation in `docs/`
- Review test cases for usage examples
