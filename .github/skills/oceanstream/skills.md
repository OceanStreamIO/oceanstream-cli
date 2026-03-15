# OceanStream Skills Reference

> **Version**: 0.1.1 | **Python**: 3.11+ | **License**: MIT | **PyPI**: `pip install oceanstream`
> 
> Reference for AI agents using OceanStream CLI and library in projects.

---

## What OceanStream Does

OceanStream converts raw oceanographic sensor data into **analysis-ready, cloud-optimized GeoParquet** with **STAC metadata** for discovery.

**Supported Data Sources**:
- **Saildrone** USV sensor CSVs
- **R2R** (Rolling Deck to Repository) cruise archives
- **EK60/EK80** echosounder raw files

**Outputs**:
- Hive-partitioned GeoParquet (1°×1° spatial bins)
- STAC 1.0 Collection + Items for data discovery
- PMTiles for web map visualization
- Zarr stores for echosounder data

---

## Installation

```bash
# Standard install
pip install oceanstream

# With echodata support
pip install oceanstream[echodata]

# Verify installation
oceanstream --help
```

---

## CLI Commands

### Process Geotrack Data (CSV → GeoParquet)

```bash
# Convert Saildrone CSV files to GeoParquet
oceanstream process geotrack convert \
    --input-source ./data/saildrone_csvs \
    --output-dir ./output \
    --campaign-id TPOS_2023 \
    --provider saildrone \
    -v

# Convert R2R cruise archive
oceanstream process geotrack convert \
    --input-source ./FK161229.tar.gz \
    --output-dir ./output \
    --campaign-id FK161229 \
    --provider r2r

# Preview without writing (dry run)
oceanstream process geotrack convert \
    --dry-run \
    --input-source ./data \
    -v

# Analyze available columns
oceanstream process geotrack convert \
    --list-columns \
    --input-source ./data

# Show output schema
oceanstream process geotrack convert \
    --print-schema \
    --input-source ./data
```

**Key Options**:
| Option | Description |
|--------|-------------|
| `--input-source` | Directory, file, or archive path |
| `--output-dir` | Output directory (local or cloud: `az://`, `s3://`) |
| `--campaign-id` | Identifier for the campaign/cruise |
| `--provider` | Data provider: `saildrone`, `r2r` |
| `--dry-run` | Analyze only, no output written |
| `-v` | Verbose output |
| `--yes` | Skip confirmation prompts |

### Process Echosounder Data (EK60/EK80 → Zarr)

```bash
# Convert raw echosounder files
oceanstream process echodata convert \
    --input-source ./raw_data/ek80 \
    --output-dir ./output/echodata \
    --campaign-id TPOS_2023

# With calibration file
oceanstream process echodata convert \
    --input-source ./raw_data \
    --calibration-file ./saildrone_calibration.ecs \
    --output-dir ./output
```

> **Note**: Echodata processing requires the echopype fork. See installation docs for details.

### Campaign Management

```bash
# Interactive campaign creation wizard
oceanstream campaign create

# Create campaign with platforms
oceanstream campaign create TPOS_2023 \
    --platform "sd1030:Saildrone 1030:Saildrone Explorer" \
    --platform "sd1033:Saildrone 1033:Saildrone Explorer" \
    --description "TPOS 2023 multi-USV deployment"

# Platform format: "id:name:type" (name and type optional)
oceanstream campaign create FK161229 \
    --platform "falkor:R/V Falkor:Research Vessel"

# List all campaigns
oceanstream campaign list

# Show campaign details
oceanstream campaign show TPOS_2023
```

### Utility Commands

```bash
# List available data providers
oceanstream providers

# Use custom config file
oceanstream --config-file ./my-config.toml process geotrack convert ...
```

---

## Python Library API

### Geotrack Processing

```python
from pathlib import Path
from oceanstream.geotrack import convert

# Simple conversion
result = convert(
    input_source=Path("./data/saildrone"),
    output_dir=Path("./output"),
    campaign_id="TPOS_2023",
    provider="saildrone",
    verbose=True,
)

print(f"Processed {result['row_count']} rows")
print(f"Output: {result['output_path']}")
```

### Using the Processor Class

```python
from pathlib import Path
from oceanstream.geotrack.processor import GeotrackProcessor
from oceanstream.providers import get_provider

# Initialize processor
processor = GeotrackProcessor(
    output_dir=Path("./output"),
    campaign_id="TPOS_2023",
    provider=get_provider("saildrone"),
    verbose=True,
)

# Process files
csv_files = list(Path("./data").glob("*.csv"))
df = processor.process_files(csv_files)

# Access results
print(f"Columns: {df.columns.tolist()}")
print(f"Time range: {df['time'].min()} to {df['time'].max()}")
```

### Echodata Processing

```python
from pathlib import Path
from oceanstream.echodata.processor import EchodataProcessor
from oceanstream.echodata.config import EchodataConfig

# Configure
config = EchodataConfig(
    sonar_model="EK80",
    parallel=True,
    n_workers=4,
)

# Process
processor = EchodataProcessor(config=config, campaign_id="TPOS_2023", verbose=True)
result = processor.run(
    input_dir=Path("./raw_data/ek80"),
    output_dir=Path("./output"),
)

if result.success:
    print(f"Pipeline completed in {result.total_duration:.1f}s")
    for step in result.steps:
        print(f"  {step.step}: {step.message}")
```

### Working with Providers

```python
from oceanstream.providers import get_provider, list_providers

# List available providers
print(list_providers())  # ['r2r', 'saildrone']

# Get provider and use it
provider = get_provider("saildrone")

# Identify platform from filename
platform_id = provider.identify_platform("sd1030_arctic_2023.csv")
print(f"Platform: {platform_id}")  # "sd1030"

# Normalize columns
import pandas as pd
raw_df = pd.read_csv("./data/sd1030_data.csv")
normalized_df = provider.enrich_dataframe(raw_df)

# Get column aliases
aliases = provider.alias_mapping(raw_df.columns)
print(aliases)  # {'SD_LATITUDE': 'latitude', 'SD_LONGITUDE': 'longitude', ...}
```

### STAC Metadata Generation

```python
from pathlib import Path
from oceanstream.stac import emit_stac_collection_and_item

# Generate STAC after processing
emit_stac_collection_and_item(
    df=processed_dataframe,
    output_dir=Path("./output/stac"),
    campaign_id="TPOS_2023",
    sensors=detected_sensors,  # Optional: list of Sensor objects
)

# Output structure:
# ./output/stac/
# ├── collection.json    # STAC Collection
# └── item_*.json        # STAC Items per file/day
```

### Semantic Mapping (CF Standard Names)

```python
from oceanstream.semantic.semantic import SemanticMapper, SemanticConfig

# Configure semantic mapping
config = SemanticConfig(
    enabled=True,
    min_confidence=0.7,
    rename_columns=False,  # Keep original names, just add metadata
)

mapper = SemanticMapper(config)
result = mapper.map(dataframe)

# Results
print(result.canonical_mapping)
# {'SD_TEMP_O2': 'sea_water_temperature', 'SD_SAL': 'sea_water_salinity'}

print(result.cf_mapping)
# {'SD_TEMP_O2': {'cf_standard_name': 'sea_water_temperature', 'confidence': 0.95}}

print(result.units)
# {'sea_water_temperature': 'degree_C', 'sea_water_salinity': 'PSU'}
```

---

## Output Structure

### GeoParquet Campaign Output

```
output_dir/campaign_id/
├── lat_bin=-45/
│   └── lon_bin=170/
│       └── data_2023-01-15.parquet
├── lat_bin=-44/
│   └── lon_bin=171/
│       └── data_2023-01-15.parquet
├── stac/
│   ├── collection.json         # STAC 1.0 Collection
│   └── item_2023-01-15.json    # STAC Items
├── tiles/
│   └── track.pmtiles           # Vector tiles for web maps
├── heatmaps/                   # COG rasters for measurements
│   ├── temp_sbe37_mean.tif
│   ├── sal_sbe37_mean.tif
│   └── manifest.json
└── .oceanstream_metadata.json   # Processing tracking (SHA256)
```

### Reading Output Data

```python
import pandas as pd
import geopandas as gpd

# Read all parquet files
df = pd.read_parquet("./output/TPOS_2023/")

# Read as GeoDataFrame
gdf = gpd.read_parquet("./output/TPOS_2023/")

# Filter by spatial bin
df_region = pd.read_parquet(
    "./output/TPOS_2023/",
    filters=[("lat_bin", ">=", -46), ("lat_bin", "<=", -44)]
)
```

---

## PMTiles Generation

OceanStream generates **PMTiles** vector tiles for efficient web map visualization. Tiles include track segments, day markers, and direction arrows.

### CLI Usage

```bash
# Generate PMTiles during conversion
oceanstream process geotrack convert \
    --input-source ./data \
    --output-dir ./output \
    --campaign-id TPOS_2023 \
    --generate-pmtiles \
    -v

# Generate tiles from existing GeoParquet
oceanstream process geotrack tiles \
    --input-dir ./output/TPOS_2023 \
    --output-dir ./output/TPOS_2023/tiles
```

### PMTiles Options

| Option | Description | Default |
|--------|-------------|---------|
| `--generate-pmtiles` | Enable tile generation | `False` |
| `--pmtiles-minzoom` | Minimum zoom level (0-15) | `0` |
| `--pmtiles-maxzoom` | Maximum zoom level (0-15) | `10` |
| `--pmtiles-layer` | Layer name in tiles | `track` |
| `--pmtiles-sample-rate` | Take every Nth point | `5` |
| `--pmtiles-time-gap` | Minutes gap to split segments | `60` |
| `--pmtiles-include-measurements` | Include sensor data | `True` |
| `--pmtiles-include-arrows` | Generate direction arrows | `True` |
| `--pmtiles-arrows-per-segment` | Arrows per track segment | `3` |
| `--pmtiles-arrow-min-points` | Min points for arrow generation | `5` |

### Tile Feature Types

The generated PMTiles contain three feature types:

**1. Track Segments (LineString)**
```json
{
  "type": "segment",
  "segment_id": 1,
  "platform_id": "sd1030",
  "day": "2024-01-15",
  "t_start": "2024-01-15T00:00:00",
  "t_end": "2024-01-15T01:00:00",
  "TEMP_SBE37_MEAN": 25.3,
  "SAL_SBE37_MEAN": 35.1
}
```

**2. Day Markers (Point)**
```json
{
  "kind": "start",
  "platform_id": "sd1030",
  "day": "2024-01-15",
  "t": "2024-01-15T00:00:00"
}
```

**3. Direction Arrows (Point)**
```json
{
  "type": "arrow",
  "platform_id": "sd1030",
  "bearing": 321.5,
  "t": "2024-01-15T01:30:00",
  "day": "2024-01-15"
}
```

### Python API

```python
from pathlib import Path
from oceanstream.geotrack.tiling import (
    generate_pmtiles_from_geoparquet,
    calculate_bearing,
)

# Generate PMTiles from existing GeoParquet
pmtiles_path = generate_pmtiles_from_geoparquet(
    geoparquet_root=Path("./output/TPOS_2023"),
    output_dir=Path("./output/TPOS_2023/tiles"),
    layer_name="track",
    minzoom=0,
    maxzoom=10,
    sample_rate=5,
    time_gap_minutes=60,
    include_measurements=True,
    include_arrows=True,
    arrows_per_segment=3,
    platform_id="sd1030",
)

# Calculate bearing between points
bearing = calculate_bearing(
    point_a=(-122.0, 37.0),  # (lon, lat)
    point_b=(-122.1, 37.1),
)
print(f"Bearing: {bearing:.1f}°")  # ~321.5° (northwest)
```

### Using PMTiles in Web Maps

PMTiles can be loaded directly in MapLibre GL JS or ArcGIS Maps SDK:

```typescript
// MapLibre example
import { PMTiles, Protocol } from 'pmtiles';

const protocol = new Protocol();
maplibregl.addProtocol('pmtiles', protocol.tile);

map.addSource('tracks', {
  type: 'vector',
  url: 'pmtiles://https://storage.example.com/tiles/track.pmtiles',
});

// Style track segments
map.addLayer({
  id: 'track-lines',
  type: 'line',
  source: 'tracks',
  'source-layer': 'track',
  filter: ['!', ['has', 'type']],  // Segments don't have 'type'
  paint: {
    'line-color': '#0066cc',
    'line-width': 2,
  },
});

// Style direction arrows
map.addLayer({
  id: 'track-arrows',
  type: 'symbol',
  source: 'tracks',
  'source-layer': 'track',
  filter: ['==', ['get', 'type'], 'arrow'],
  layout: {
    'icon-image': 'arrow',
    'icon-rotate': ['get', 'bearing'],
    'icon-rotation-alignment': 'map',
  },
});
```

---

## Heatmap Generation (COG Rasters)

OceanStream generates **Cloud Optimized GeoTIFF (COG)** heatmaps from interpolated measurement data for fast web visualization without server-side computation.

### CLI Usage

```bash
# Generate heatmaps during conversion
oceanstream process geotrack convert \
    --input-source ./data \
    --output-dir ./output \
    --campaign-id TPOS_2023 \
    --generate-heatmaps \
    -v

# Generate heatmaps from existing GeoParquet
oceanstream process geotrack heatmaps \
    --geoparquet-dir ./output/TPOS_2023 \
    --variable TEMP_SBE37_MEAN \
    --variable SAL_SBE37_MEAN \
    -v

# Custom resolution and interpolation
oceanstream process geotrack heatmaps \
    --geoparquet-dir ./output/TPOS_2023 \
    --resolution 0.1 \
    --method cubic \
    --search-radius 1.0 \
    --max-variables 5
```

### Heatmap Options

| Option | Description | Default |
|--------|-------------|---------|
| `--generate-heatmaps` | Enable heatmap generation | `False` |
| `--heatmap-resolution` | Grid resolution in degrees (0.05 ≈ 5km) | `0.05` |
| `--heatmap-method` | Interpolation: linear, nearest, cubic | `linear` |
| `--heatmap-search-radius` | Max interpolation distance (deg) | `0.5` |
| `--heatmap-variables` | Specific variables to process | Auto-detect |
| `--heatmap-max-variables` | Maximum variables to process | `10` |

### Supported Variables

Auto-detected oceanographic variables with metadata:

| Variable | Label | Unit | Colormap |
|----------|-------|------|----------|
| `TEMP_SBE37_MEAN` | Sea Temperature (CTD) | °C | thermal |
| `TEMP_AIR_MEAN` | Air Temperature | °C | thermal |
| `SAL_SBE37_MEAN` | Salinity | PSU | viridis |
| `CHLOR_WETLABS_MEAN` | Chlorophyll | µg/L | algae |
| `BARO_PRES_MEAN` | Barometric Pressure | hPa | coolwarm |
| `RH_MEAN` | Relative Humidity | % | Blues |
| `WIND_SPEED_MEAN` | Wind Speed | m/s | wind |

### Python API

```python
from pathlib import Path
from oceanstream.geotrack.raster import (
    generate_measurement_cog,
    generate_all_heatmaps,
)

# Generate single variable heatmap
cog_path = generate_measurement_cog(
    geoparquet_root=Path("./output/TPOS_2023"),
    output_path=Path("./output/TPOS_2023/heatmaps/temp_sbe37_mean.tif"),
    variable="TEMP_SBE37_MEAN",
    resolution_deg=0.05,
    method="linear",
    search_radius_deg=0.5,
)

# Generate all detected heatmaps
manifest = generate_all_heatmaps(
    geoparquet_root=Path("./output/TPOS_2023"),
    output_dir=Path("./output/TPOS_2023/heatmaps"),
    variables=None,  # Auto-detect
    resolution_deg=0.05,
    max_variables=10,
)

print(f"Generated {len(manifest['variables'])} heatmaps")
```

### Manifest File

Generated heatmaps include a `manifest.json` with metadata:

```json
{
  "variables": [
    {
      "name": "TEMP_SBE37_MEAN",
      "file": "temp_sbe37_mean.tif",
      "label": "Sea Temperature (CTD)",
      "unit": "°C",
      "colormap": "thermal",
      "min": 20.5,
      "max": 31.2
    }
  ]
}
```

### Using COGs in Web Maps

COG files support HTTP range requests for efficient tile loading:

```typescript
// ArcGIS Maps SDK
import ImageryTileLayer from '@arcgis/core/layers/ImageryTileLayer';

const heatmapLayer = new ImageryTileLayer({
  url: 'https://storage.example.com/heatmaps/temp_sbe37_mean.tif',
  title: 'Sea Temperature',
  opacity: 0.6,
});

map.add(heatmapLayer);
```

### Output Structure

```
campaign_data/heatmaps/
├── temp_sbe37_mean.tif      # COG file (~1-10MB each)
├── sal_sbe37_mean.tif
├── chlor_wetlabs_mean.tif
└── manifest.json            # Variable metadata with min/max
```

### Dependencies

Heatmap generation requires:
```bash
pip install scipy rasterio
```

For COG optimization (recommended), install GDAL:
```bash
# macOS
brew install gdal

# Ubuntu/Debian
apt-get install gdal-bin
```

Without `gdal_translate`, files are saved as standard GeoTIFF (still functional, less optimized for web).

---

### Echodata Output

```
output_dir/campaign_id/
├── raw/                  # Converted Zarr stores
│   └── *.zarr/
├── sv/                   # Volume backscattering strength
├── denoised/             # Cleaned Sv data
├── mvbs/                 # Mean volume backscattering
├── nasc/                 # NASC acoustic indices
├── echograms/            # PNG visualizations
└── stac/                 # Echodata STAC items
```

---

## Configuration

### oceanstream.toml

```toml
[paths]
metadata_dir = "~/.oceanstream"
output_dir = "./output"

[processing]
default_provider = "saildrone"
lat_bin_size = 1.0
lon_bin_size = 1.0

[semantic]
enable = true
generate_stac = true
min_confidence = 0.7

[echodata]
sonar_model = "EK80"
parallel = true
n_workers = 4
```

### Environment Variables

| Variable | Purpose | Default |
|----------|---------|---------|
| `OCEANSTREAM_METADATA_DIR` | Campaign metadata storage | `~/.oceanstream/metadata` |
| `SEMANTIC_ENABLE` | Enable semantic mapping | `false` |
| `SEMANTIC_GENERATE_STAC` | Generate STAC catalogues | `true` |
| `SEMANTIC_MIN_CONFIDENCE` | CF name match threshold | `0.7` |

---

## Cloud Storage

OceanStream supports cloud output directly:

```bash
# Azure Blob Storage
oceanstream process geotrack convert \
    --input-source ./data \
    --output-dir az://mycontainer/campaigns/TPOS_2023

# AWS S3
oceanstream process geotrack convert \
    --input-source ./data \
    --output-dir s3://mybucket/campaigns/TPOS_2023

# Google Cloud Storage
oceanstream process geotrack convert \
    --input-source ./data \
    --output-dir gs://mybucket/campaigns/TPOS_2023
```

**Required packages** for cloud storage:
- Azure: `pip install adlfs`
- AWS: `pip install s3fs`
- GCS: `pip install gcsfs`

---

## Common Patterns

### Batch Processing Multiple Campaigns

```python
from pathlib import Path
from oceanstream.geotrack import convert

campaigns = [
    ("TPOS_2023", "./data/tpos_2023"),
    ("ARCTIC_2024", "./data/arctic_2024"),
    ("PACIFIC_2024", "./data/pacific_2024"),
]

for campaign_id, input_path in campaigns:
    result = convert(
        input_source=Path(input_path),
        output_dir=Path(f"./output/{campaign_id}"),
        campaign_id=campaign_id,
        provider="saildrone",
        verbose=True,
    )
    print(f"{campaign_id}: {result['row_count']} rows")
```

### Incremental Processing (Append Mode)

```python
from oceanstream.geotrack import convert

# First run
convert(
    input_source=Path("./data/batch1"),
    output_dir=Path("./output"),
    campaign_id="TPOS_2023",
)

# Subsequent runs - deduplication is automatic
convert(
    input_source=Path("./data/batch2"),
    output_dir=Path("./output"),  # Same output dir
    campaign_id="TPOS_2023",       # Same campaign
)
# Only new records are added; duplicates are detected via SHA256
```

### Inspecting STAC Metadata

```python
import json
from pathlib import Path

# Read collection metadata
collection_path = Path("./output/TPOS_2023/stac/collection.json")
with open(collection_path) as f:
    collection = json.load(f)

print(f"Campaign: {collection['id']}")
print(f"Time range: {collection['extent']['temporal']['interval']}")
print(f"Bbox: {collection['extent']['spatial']['bbox']}")
print(f"Platforms: {collection['summaries'].get('platforms', [])}")
print(f"Sensors: {collection['summaries'].get('instruments', [])}")
```

---

## Troubleshooting

### Common Issues

**"No files found"**
```bash
# Check input path exists and contains expected files
ls ./data/*.csv

# Use verbose mode to see what's being scanned
oceanstream process geotrack convert --dry-run --input-source ./data -v
```

**"Unknown provider"**
```bash
# List available providers
oceanstream providers
# Output: saildrone, r2r
```

**Column mapping issues**
```bash
# List columns to see what's available
oceanstream process geotrack convert --list-columns --input-source ./data
```

**Echodata dependency error**
```bash
# Echodata requires the echopype fork
pip install oceanstream[echodata]
# Then install echopype fork separately
```

### Debug Mode

```bash
# Maximum verbosity
oceanstream process geotrack convert \
    --input-source ./data \
    --dry-run \
    -v

# Check processed files tracking
cat ./output/TPOS_2023/.oceanstream_metadata.json
```

---

## Quick Reference

| Task | Command/Code |
|------|--------------|
| Install | `pip install oceanstream` |
| Convert CSV → GeoParquet | `oceanstream process geotrack convert --input-source ./data --output-dir ./out --campaign-id ID` |
| Dry run analysis | `oceanstream process geotrack convert --dry-run --input-source ./data -v` |
| List providers | `oceanstream providers` |
| Create campaign | `oceanstream campaign create CAMPAIGN_ID` |
| Python: convert | `from oceanstream.geotrack import convert` |
| Python: get provider | `from oceanstream.providers import get_provider` |
| Read output | `pd.read_parquet("./output/campaign/")` |

---

*OceanStream v0.1.1 | [Documentation](https://oceanstream.io) | [PyPI](https://pypi.org/project/oceanstream/)*
