# Oceanstream

This project processes oceanographic measurements collected from Unmanned Surface Vehicles (USVs). The pipeline reads CSV files from a specified directory, consolidates the data, and stores it in a GeoParquet format partitioned by latitude and longitude bins. Optionally, it can upload the resulting GeoParquet files to Azure Blob Storage.

## Project Structure

```
oceanstream
├── src
│   ├── app.py                # Main entry point for the application
│   ├── cli.py                # Command-line interface for running the pipeline
│   ├── config                # Configuration settings
│   │   ├── __init__.py
│   │   └── settings.py
│   ├── pipeline              # Data processing pipeline
│   │   ├── __init__.py
│   │   ├── csv_reader.py     # Functions for reading CSV files
│   │   ├── binning.py        # Functions for data partitioning
│   │   └── geoparquet_writer.py # Functions for writing geoparquet files
│   ├── storage               # Storage handling
│   │   ├── __init__.py
│   │   ├── local.py          # Local storage functions
│   │   └── azure_blob.py     # Azure Blob Storage functions
│   └── types                 # Data models and types
│       ├── __init__.py
│       └── models.py
├── data
│   └── raw_data              # Directory for raw CSV data
│       └── .gitkeep
├── tests                     # Unit tests for the application
│   ├── __init__.py
│   └── test_pipeline.py
├── .env.example              # Template for environment variables
├── .gitignore                # Git ignore file
├── .python-version           # Python version specification
├── pyproject.toml            # Project dependencies and configuration
└── README.md                 # Project documentation
```

## Setup Instructions

1. **Clone the Repository**: 
   ```bash
   git clone <repository-url>
   cd oceanstream
   ```

2. **Create a Virtual Environment**:
   ```bash
   python3.12 -m venv venv
   source venv/bin/activate  # On Windows use `venv\Scripts\activate`
   ```

3. **Install Dependencies**:
   
   Oceanstream is organized into optional processing modules. Install only what you need:
   
   ```bash
   # Install core + geotrack processing (GPS/navigation data → GeoParquet)
   pip install -e ".[geotrack]"
   
   # Install core + echodata processing (echosounder data → Zarr)
   pip install -e ".[echodata]"
   
   # Install all processing modules
   pip install -e ".[all]"
   
   # Install for development (includes all modules + dev tools)
   pip install -e ".[all]" -r requirements-dev.txt
   ```
   
   **Available extras:**
   - `geotrack` - GPS/navigation track processing (pandas, geopandas, shapely)
   - `echodata` - Echosounder data processing (echopype, xarray, zarr, netcdf4)
   - `multibeam` - Multibeam sonar processing (planned)
   - `adcp` - ADCP current profiler processing (planned)
   - `all` - All processing modules
   - `geo` - Legacy alias for `geotrack`

4. **Configure Environment Variables**:
   Copy `.env.example` to `.env` and fill in the necessary configuration values.

5. **Run Processing Commands**:
   
   Oceanstream provides separate commands for each data type:
   
   ```bash
   # Process geotrack data (CSV → GeoParquet)
   oceanstream process geotrack --input-dir raw_data --output-dir out/geoparquet -v
   
   # Process echosounder data (planned - requires echodata extra)
   oceanstream process echodata --input-dir raw_echodata --output-dir out/echodata -v
   
   # Process multibeam data (planned - requires multibeam extra)
   oceanstream process multibeam --input-dir raw_multibeam --output-dir out/multibeam -v
   
   # Process ADCP data (planned - requires adcp extra)
   oceanstream process adcp --input-dir raw_adcp --output-dir out/adcp -v
   
   # List available data providers
   oceanstream providers
   ```
   
   All processing commands support `--provider` flag to specify the data source:
   ```bash
   oceanstream process --provider saildrone geotrack --input-dir data -v
   ```

## Usage

### Geotrack Processing (GPS/Navigation Data)

CLI usage examples:

```bash
# Process sample fixture data bundled with tests
oceanstream process geotrack --input-dir oceanstream/tests/data/raw_data --output-dir out/geoparquet -v

# Process your raw_data directory at repo root (default input-dir is ./raw_data)
oceanstream process geotrack --output-dir out/geoparquet -v

# Dry run to see what would be processed
oceanstream process geotrack --input-dir raw_data --dry-run -v

# List available columns in the data
oceanstream process geotrack --input-dir raw_data --list-columns
```

The CLI reads CSVs, auto-derives coarse 5° bins, and writes a partitioned GeoParquet dataset with metadata. Use `-v` for progress logs.

### Processing Modules

Oceanstream is organized into separate processing modules:

- **`oceanstream.geotrack`** - Process GPS/navigation track data into GeoParquet format
- **`oceanstream.echodata`** - Process echosounder data (EK60/EK80) into Zarr (coming soon)
- **`oceanstream.multibeam`** - Process multibeam sonar data (coming soon)
- **`oceanstream.adcp`** - Process ADCP current profiler data (coming soon)

Each module can be installed independently using pip extras (see Installation section).

## Using OceanStream Data in GIS Tools

OceanStream generates cloud-optimized GeoParquet files designed to work seamlessly with modern GIS tools and data analysis frameworks. Our output includes:

- **GeoParquet**: Columnar format with embedded geometry and spatial partitioning
- **STAC Metadata**: Standard catalog format for discovery and integration
- **PMTiles** (optional): Vector tiles for web-based visualization

### Comprehensive GIS Integration Guides

We provide detailed integration guides for popular GIS tools and frameworks:

**Desktop GIS:**
- [QGIS](../docs/gis-integration/qgis.md) - Open-source desktop GIS
- [ArcGIS Pro](../docs/gis-integration/arcgis-pro.md) - Professional ESRI platform

**Data Analysis:**
- [DuckDB](../docs/gis-integration/duckdb.md) - Fast in-process SQL analytics
- [GeoPandas](../docs/gis-integration/geopandas.md) - Python spatial data analysis

**Web GIS** (coming soon):
- Leaflet + PMTiles
- Mapbox GL JS
- STAC Browser

**See [GIS Integration Documentation](../docs/gis-integration/) for complete guides with:**
- Installation instructions
- Step-by-step usage examples
- Code samples and workflows
- Performance optimization tips
- Troubleshooting guides

### Quick Start Examples

**Load in QGIS:**
```bash
# Generate data
oceanstream process geotrack --input-source ./data/sample.csv --output-dir ./output

# Open QGIS and drag-and-drop .parquet files from:
# output/campaign_id/lat_bin=X/lon_bin=Y/*.parquet
```

**Query with DuckDB:**
```sql
INSTALL spatial;
LOAD spatial;

SELECT time, latitude, longitude, temperature_sea_water
FROM read_parquet('output/campaign_id/**/*.parquet')
WHERE lat_bin = 30 AND lon_bin = -120
LIMIT 10;
```

**Analyze with GeoPandas:**
```python
import geopandas as gpd

# Read all spatial partitions
gdf = gpd.read_parquet('output/campaign_id/')

# Filter and analyze
warm_water = gdf[gdf['temperature_sea_water'] > 25]
print(f"Found {len(warm_water)} warm water measurements")
```

## Contributing

Contributions are welcome! Please open an issue or submit a pull request for any enhancements or bug fixes.

## License

This project is licensed under the MIT License. See the LICENSE file for details.