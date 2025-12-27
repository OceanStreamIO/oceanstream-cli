# OceanStream Documentation

Welcome to the OceanStream documentation. OceanStream is a cloud-optimized oceanographic data processing pipeline that converts raw sensor data into analysis-ready GeoParquet format with STAC metadata.

## What is OceanStream?

OceanStream processes oceanographic data from autonomous platforms (like Saildrone, R2R research vessels) and converts it into cloud-optimized formats:

- **Input**: CSV/GeoCSV files from oceanographic platforms
- **Processing**: Validation, spatial binning, semantic mapping
- **Output**: GeoParquet with STAC metadata, optional PMTiles

## Key Features

### Data Providers
Support for multiple oceanographic data sources with semantic column mapping:
- **Saildrone**: Autonomous surface vehicles with 60+ sensor mappings
- **R2R**: NSF research vessel data (Rolling Deck to Repository)
- **Extensible**: Easy to add new providers

[Learn more about Data Providers →](guide/features/data-providers/overview.md)

### Sensor Catalogue
Comprehensive registry of oceanographic sensors and instruments:
- **12 sensors** across 7 categories (CTD, fluorometer, meteorological, radiation, navigation, wave, thermistor)
- Automatic sensor detection from data variables
- STAC-compliant instrument metadata

[Explore Supported Sensors →](guide/features/supported-sensors/overview.md)

### GeoTrack Processing
Convert trajectory data to cloud-optimized GeoParquet:
- Spatial binning (1° x 1° lat/lon by default)
- Hive partitioning for efficient spatial queries
- Preserves all original columns and metadata

[GeoTrack Overview →](guide/core-concepts/geotrack-convert-overview.md)

### STAC Metadata
Generate STAC (SpatioTemporal Asset Catalog) metadata:
- Collection and item metadata
- Sensor/instrument information
- Temporal and spatial extents
- Provider and platform details

[STAC Metadata Guide →](guide/features/stac-metadata.md)

### PMTiles Generation
Create vector tiles for web visualization:
- Automatic PMTiles generation from GeoParquet
- Web-ready for Mapbox GL JS, Leaflet, etc.
- No tile server required

[PMTiles Documentation →](guide/integrations/pmtiles/overview.md)

### NMEA Processing
Parse and process NMEA 0183 marine data:
- Real-time streaming support
- Sentence validation and parsing
- Multiple output formats

[NMEA Processing Guide →](guide/features/nmea-processing.md)

### Campaign Management
Organize data by oceanographic campaigns:
- Campaign-based folder structure
- Multiple files per campaign
- Incremental data processing with deduplication

[Campaign Management →](guide/core-concepts/campaigns.md)

## Quick Start

### Installation

```bash
# Clone the repository
git clone https://github.com/OceanStreamIO/oceanstream-newcli.git
cd oceanstream-newcli

# Create virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install OceanStream
pip install -e .
```

[Detailed Installation Guide →](getting-started/installation.md)

### Process Saildrone Data

```bash
# Process Saildrone CSV to GeoParquet
oceanstream process geotrack \
  --input-source ./raw_data/sd1030_tpos_2023.csv \
  --output-dir ./output \
  --provider saildrone

# Output:
# output/
#   └── sd1030_tpos_2023/
#       ├── lat_bin=X/lon_bin=Y/*.parquet
#       └── stac/
#           ├── collection.json
#           └── items/*.json
```

[Quick Start Guide →](getting-started/quick-start.md)

### Python API

```python
from oceanstream.geotrack.processor import GeoTrackProcessor
from pathlib import Path

# Initialize processor
processor = GeoTrackProcessor(
    input_source=Path("raw_data/sd1030_tpos_2023.csv"),
    output_dir=Path("output"),
    provider="saildrone"
)

# Run processing
processor.run()
```

## Architecture

```
┌─────────────────┐
│   Raw CSV/      │
│   GeoCSV Data   │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│   Provider      │  ← Semantic mappings
│   Detection     │     (Saildrone, R2R)
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│   Validation    │
│   & Parsing     │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│   Spatial       │  ← 1°x1° lat/lon bins
│   Binning       │     Hive partitioning
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│   GeoParquet    │  ← Cloud-optimized
│   Generation    │     WKT geometry
└────────┬────────┘
         │
         ├─────────────────┐
         ▼                 ▼
┌─────────────────┐ ┌─────────────────┐
│   STAC          │ │   PMTiles       │
│   Metadata      │ │   (Optional)    │
└─────────────────┘ └─────────────────┘
```

## Use Cases

### Research & Science
- Process autonomous platform data (Saildrone, gliders)
- Research vessel data management
- Multi-platform data integration
- Long-term dataset curation

### Cloud Analytics
- Query data with DuckDB, Apache Sedona
- STAC-based data discovery
- Spatial and temporal subsetting
- Integration with cloud data lakes

### Web Visualization
- PMTiles for interactive maps
- STAC Browser for data discovery
- Real-time data dashboards
- Public data portals

## Documentation Sections

<div class="grid cards" markdown>

-   **Getting Started**

    ---

    Install OceanStream, configure your environment, and process your first dataset

    [Installation](getting-started/installation.md) • [Quick Start](getting-started/quick-start.md) • [Configuration](getting-started/configuration.md)

-   **Core Concepts**

    ---

    Understand the key concepts: GeoTrack processing, NMEA parsing, STAC metadata, campaigns, providers

    [GeoTrack](guide/core-concepts/geotrack-convert-overview.md) • [NMEA](guide/features/nmea-processing.md) • [STAC](guide/features/stac-metadata.md) • [Campaigns](guide/core-concepts/campaigns.md) • [Providers](guide/core-concepts/providers.md)

-   **Features**

    ---

    Explore advanced features: data providers, sensor catalogue, PMTiles, cloud storage

    [Data Providers](guide/features/data-providers/overview.md) • [Sensors](guide/features/supported-sensors/overview.md) • [PMTiles](guide/integrations/pmtiles/overview.md)

-   **Examples**

    ---

    Learn by example with real-world processing workflows and Jupyter notebooks

    [Saildrone Processing](guide/examples/saildrone-overview.md) • [NMEA Notebook](guide/examples/nmea-processing-notebook.ipynb)

</div>

## Community & Support

- **GitHub**: [OceanStreamIO/oceanstream-newcli](https://github.com/OceanStreamIO/oceanstream-newcli)
- **Issues**: [Report bugs or request features](https://github.com/OceanStreamIO/oceanstream-newcli/issues)
- **Discussions**: [Ask questions and share ideas](https://github.com/OceanStreamIO/oceanstream-newcli/discussions)

## License

OceanStream is open source software licensed under the [MIT License](https://github.com/OceanStreamIO/oceanstream-newcli/blob/main/LICENSE).

## Contributing

We welcome contributions! See our [Contributing Guide](https://github.com/OceanStreamIO/oceanstream-newcli/blob/main/CONTRIBUTING.md) for details.
