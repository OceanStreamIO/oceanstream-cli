# STAC Metadata

OceanStream automatically generates **STAC (SpatioTemporal Asset Catalog)** metadata for all processed datasets, enabling discovery, cataloging, and interoperability with modern geospatial tools.

## What is STAC?

[STAC](https://stacspec.org/) is an open specification for describing geospatial data. It provides a common structure to index spatial assets, making them searchable and accessible across different systems.

**Key benefits:**

- **Standardized discovery**: Search datasets by location, time, and properties
- **Tool interoperability**: Works with QGIS, STAC Browser, pystac-client, etc.
- **Cloud-native**: Optimized for cloud storage and distributed access
- **Extensible**: Supports custom properties and extensions

## STAC Structure

OceanStream generates a complete STAC catalog for each campaign:

```
output/campaign_id/
└── stac/
    ├── collection.json        ← STAC Collection (catalog-level metadata)
    └── items/
        ├── item-0.json        ← STAC Item (dataset-level metadata)
        ├── item-1.json
        └── ...
```

### STAC Collection

The **Collection** represents the entire campaign/dataset with aggregated metadata:

- Campaign-level information (ID, description, providers)
- Spatial extent (bounding box covering all data)
- Temporal extent (time range of observations)
- Summaries: instruments, platforms, measurements
- Links to individual items

### STAC Items

Each **Item** represents a subset of the data (typically by spatial partition):

- Geographic footprint (bounding box + geometry)
- Temporal properties (start/end datetime)
- Platform identifiers (array for multi-platform campaigns)
- Assets: Links to GeoParquet files
- Item-level metadata and measurements

## STAC Generation

STAC metadata is generated automatically by default during the `process geotrack convert` command.

### Enable/Disable STAC

```bash
# STAC enabled by default
oceanstream process geotrack convert --input-source ./data --output-dir ./out

# Disable STAC generation
oceanstream process geotrack convert --input-source ./data --output-dir ./out --no-stac
```

### Environment Variable

You can also control STAC generation globally:

```bash
# Disable STAC generation
export SEMANTIC_GENERATE_STAC=false

# Enable (default)
export SEMANTIC_GENERATE_STAC=true
```

## STAC Collection Structure

### Basic Collection Example

```json
{
  "type": "Collection",
  "stac_version": "1.0.0",
  "id": "oceanstream-saildrone-geoparquet",
  "description": "Oceanstream GeoParquet dataset for provider 'saildrone'.",
  "license": "MIT",
  "keywords": [
    "sea_surface_temperature",
    "wind_speed",
    "air_temperature"
  ],
  "extent": {
    "spatial": {
      "bbox": [[-140.0, -10.0, -110.0, 10.0]]
    },
    "temporal": {
      "interval": [["2023-01-01T00:00:00Z", "2023-12-31T23:59:59Z"]]
    }
  },
  "links": [
    {"rel": "self", "href": "collection.json"},
    {"rel": "items", "href": "items/"}
  ],
  "providers": [
    {
      "name": "saildrone",
      "roles": ["producer"]
    }
  ],
  "assets": {
    "pmtiles": {
      "href": "../tiles/track.pmtiles",
      "type": "application/vnd.pmtiles",
      "roles": ["visual", "tiles"],
      "title": "PMTiles vector tiles with track segments and measurements"
    }
  },
  "summaries": {
    "instruments": [
      {
        "name": "atmospheric_suite",
        "type": "sensor",
        "description": "Atmospheric measurements (temperature, humidity, pressure)"
      },
      {
        "name": "sea_surface_temperature",
        "type": "sensor",
        "description": "Sea surface temperature measurements"
      }
    ],
    "platforms": [
      {
        "id": "sd1030",
        "type": "Saildrone Explorer",
        "model": "Explorer",
        "row_count": 9600
      },
      {
        "id": "sd1033",
        "type": "Saildrone Explorer",
        "model": "Explorer",
        "row_count": 192974
      },
      {
        "id": "sd1079",
        "type": "Saildrone Explorer",
        "model": "Explorer",
        "row_count": 154087
      }
    ],
    "measurements": {
      "sea_surface_temperature": {
        "min": 24.5,
        "max": 29.8,
        "mean": 27.2,
        "count": 145623
      },
      "wind_speed": {
        "min": 0.1,
        "max": 15.3,
        "mean": 6.8,
        "count": 145623
      }
    },
    "processing": {
      "software": "oceanstream",
      "version": "0.1.0",
      "processing_date": "2024-12-02T10:30:00",
      "processing_level": "L2"
    }
  }
}
```

### Collection Fields

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | Unique collection identifier (typically `campaign_id`) |
| `description` | string | Human-readable description |
| `license` | string | Data license (default: MIT) |
| `keywords` | array | CF Standard Names or measurement types |
| `extent.spatial.bbox` | array | Bounding box `[[lon_min, lat_min, lon_max, lat_max]]` |
| `extent.temporal.interval` | array | Time range `[["start_iso", "end_iso"]]` |
| `providers` | array | Data provider information |
| `assets.pmtiles` | object | PMTiles vector tiles (collection-level) |
| `summaries.instruments` | array | Detected sensors/instruments |
| `summaries.platforms` | array | Platform metadata for all platforms in campaign |
| `summaries.measurements` | object | Statistical summaries (min/max/mean/count) |
| `summaries.processing` | object | Processing provenance (software, version, date) |

## STAC Item Structure

### Basic Item Example

```json
{
  "type": "Feature",
  "stac_version": "1.0.0",
  "id": "oceanstream-saildrone-geoparquet-item-0",
  "collection": "oceanstream-saildrone-geoparquet",
  "bbox": [-140.0, -10.0, -139.0, -9.0],
  "geometry": {
    "type": "Polygon",
    "coordinates": [[
      [-140.0, -10.0],
      [-139.0, -10.0],
      [-139.0, -9.0],
      [-140.0, -9.0],
      [-140.0, -10.0]
    ]]
  },
  "properties": {
    "platform_ids": ["sd1030", "sd1033", "sd1079"],
    "campaign_id": "tpos_2023",
    "start_datetime": "2023-01-01T00:00:00Z",
    "end_datetime": "2023-01-31T23:59:59Z"
  },
  "assets": {
    "geoparquet": {
      "href": "../lat_bin=-10/lon_bin=-140/data.parquet",
      "type": "application/x-parquet",
      "roles": ["data"]
    }
  },
  "links": [
    {"rel": "collection", "href": "../collection.json"}
  ]
}
```

### Item Fields

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | Unique item identifier |
| `collection` | string | Parent collection ID |
| `bbox` | array | Item bounding box `[lon_min, lat_min, lon_max, lat_max]` |
| `geometry` | object | GeoJSON polygon of item footprint |
| `properties.platform_ids` | array | Platform identifiers (for multi-platform campaigns) |
| `properties.campaign_id` | string | Campaign identifier |
| `properties.start_datetime` | string | Start time (ISO 8601) |
| `properties.end_datetime` | string | End time (ISO 8601) |
| `assets` | object | Links to data files (GeoParquet) |

## Asset Types

### GeoParquet Assets

Every STAC Item includes one or more GeoParquet assets:

```json
{
  "geoparquet": {
    "href": "../lat_bin=-10/lon_bin=-140/data.parquet",
    "type": "application/x-parquet",
    "roles": ["data"]
  }
}
```

For items with multiple partition files:

```json
{
  "geoparquet": {
    "href": "../lat_bin=-10/lon_bin=-140/file1.parquet",
    "type": "application/x-parquet",
    "roles": ["data"]
  },
  "geoparquet_1": {
    "href": "../lat_bin=-10/lon_bin=-140/file2.parquet",
    "type": "application/x-parquet",
    "roles": ["data"]
  }
}
```

### PMTiles Assets

If PMTiles are generated (using `--generate-pmtiles`), they are stored as a **collection-level asset** in `collection.json` since PMTiles cover the entire dataset:

```json
{
  "assets": {
    "pmtiles": {
      "href": "../tiles/track.pmtiles",
      "type": "application/vnd.pmtiles",
      "roles": ["visual", "tiles"],
      "title": "PMTiles vector tiles with track segments and measurements"
    }
  }
}
```

PMTiles enable web-based visualization without loading full datasets.

## Using STAC Metadata

### 1. Validate STAC Files

Use the official STAC validator:

```bash
# Install pystac
pip install pystac

# Validate collection
python -c "import pystac; c = pystac.Collection.from_file('output/campaign_id/stac/collection.json'); print('✅ Valid')"

# Validate item
python -c "import pystac; i = pystac.Item.from_file('output/campaign_id/stac/items/item-0.json'); print('✅ Valid')"
```

### 2. Browse with STAC Browser

[STAC Browser](https://github.com/radiantearth/stac-browser) provides a web UI for exploring STAC catalogs:

```bash
# Serve locally
npx http-server output/campaign_id/stac -p 8080

# Open STAC Browser pointing to your catalog
# https://radiantearth.github.io/stac-browser/#/external/localhost:8080/collection.json
```

### 3. Query with pystac-client

```python
from pystac_client import Client

# Open local STAC catalog
catalog = Client.open("output/campaign_id/stac/collection.json")

# Search by bounding box
search = catalog.search(
    bbox=[-140, -10, -110, 10],
    datetime="2023-01-01/2023-12-31"
)

items = list(search.items())
print(f"Found {len(items)} items")

# Access assets
for item in items:
    for key, asset in item.assets.items():
        print(f"{key}: {asset.href}")
```

### 4. Load with GeoPandas

```python
import geopandas as gpd
import pystac

# Load STAC item
item = pystac.Item.from_file("output/campaign_id/stac/items/item-0.json")

# Get GeoParquet asset
asset = item.assets["geoparquet"]
parquet_path = f"output/campaign_id/{asset.href.replace('../', '')}"

# Load data
gdf = gpd.read_parquet(parquet_path)
print(gdf.head())
```

### 5. Integrate with QGIS

QGIS has a STAC API Browser plugin for direct STAC catalog access:

1. Install **QGIS STAC API Browser** plugin
2. Add your STAC API endpoint (or serve locally)
3. Browse collections and add layers directly to QGIS

<!-- TODO: Add QGIS Integration Guide -->
QGIS can load GeoParquet files directly via GDAL.

## Measurement Statistics

OceanStream automatically calculates statistical summaries for numeric measurement columns:

```json
{
  "summaries": {
    "measurements": {
      "sea_surface_temperature": {
        "min": 24.5,
        "max": 29.8,
        "mean": 27.2,
        "count": 145623
      },
      "salinity": {
        "min": 34.1,
        "max": 35.8,
        "mean": 34.9,
        "count": 145623
      }
    }
  }
}
```

These statistics are:

- Calculated across the entire campaign dataset
- Include min, max, mean, and count (non-null values)
- Automatically exclude standard columns (latitude, longitude, time, bins)
- Useful for quick data quality checks and discovery

## Sensor Detection Integration

Detected sensors are included in the STAC collection's `summaries.instruments`:

```json
{
  "summaries": {
    "instruments": [
      {
        "name": "atmospheric_suite",
        "type": "sensor",
        "description": "Atmospheric measurements (temperature, humidity, pressure)",
        "manufacturer": "Airmar",
        "columns": ["air_temperature", "humidity", "barometric_pressure"]
      },
      {
        "name": "sea_surface_temperature",
        "type": "sensor",
        "description": "Sea surface temperature measurements",
        "columns": ["sea_surface_temperature"]
      }
    ]
  }
}
```

Sensor detection is automatic based on column names and patterns. See [Supported Sensors](supported-sensors/overview.md) for details.

## Platform Metadata

Platform and campaign identifiers flow through to STAC metadata:

```json
{
  "summaries": {
    "platform": {
      "platform_id": "sd1030",
      "campaign_id": "tpos_2023"
    }
  }
}
```

These identifiers are determined by priority:

1. User-supplied via CLI (`--platform` for campaigns, `--campaign-id`)
2. File metadata headers (GeoCSV)
3. Derived from filename patterns

## Processing Provenance

Every STAC collection includes processing provenance:

```json
{
  "summaries": {
    "processing": {
      "software": "oceanstream",
      "version": "0.1.0",
      "processing_date": "2024-12-02T10:30:00",
      "processing_level": "L2"
    }
  }
}
```

This tracks:

- **Software**: Always "oceanstream"
- **Version**: Installed package version
- **Processing date**: When the data was processed (ISO 8601)
- **Processing level**: L2 (calibrated, geolocated, quality-controlled)

## Semantic Enrichment

If semantic enrichment is enabled (default), CF Standard Names are included as keywords:

```json
{
  "keywords": [
    "sea_water_temperature",
    "sea_water_salinity",
    "wind_speed",
    "air_temperature"
  ]
}
```

This enables:

- Discovery by measurement type
- Semantic search across campaigns
- Interoperability with CF-compliant tools

Standard names and units follow CF conventions where applicable.

## Configuration

### CLI Options

```bash
# Default: STAC enabled
oceanstream process geotrack convert --input-source ./data --output-dir ./out

# Disable STAC
oceanstream process geotrack convert --input-source ./data --output-dir ./out --no-stac

# STAC generated with all options
oceanstream process geotrack convert \
  --input-source ./data \
  --output-dir ./out \
  --campaign-id arctic_2024 \
  --generate-pmtiles \
  --pmtiles-include-measurements
```

### Environment Variables

```bash
# Enable/disable STAC generation
export SEMANTIC_GENERATE_STAC=true   # default
export SEMANTIC_GENERATE_STAC=false  # disable

# Enable/disable semantic enrichment (affects keywords)
export SEMANTIC_ENABLE=true   # default
export SEMANTIC_ENABLE=false  # disable
```

### Python API

```python
from oceanstream.geotrack.processor import GeotrackProcessor
from pathlib import Path

processor = GeotrackProcessor(
    provider_name="saildrone",
    verbose=True
)

# STAC controlled by settings
from oceanstream.config.settings import Settings
Settings.SEMANTIC_GENERATE_STAC = True  # Enable (default)
Settings.SEMANTIC_ENABLE = True         # Enable semantic enrichment

processor.convert(
    input_source=Path("./data"),
    output_dir=Path("./out"),
    campaign_id="tpos_2023",
    platform_id="sd1030"
)
```

## Complete Example

### Generate STAC for Saildrone Data

```bash
# Process Saildrone data with STAC
oceanstream process geotrack convert \
  --provider saildrone \
  --input-source ./raw_data/sd1030_tpos_2023.csv \
  --output-dir ./output \
  --campaign-id tpos_2023 \
  --generate-pmtiles \
  --pmtiles-include-measurements
```

**Output structure:**

```
output/
└── tpos_2023/
    ├── lat_bin=-10/
    │   └── lon_bin=-140/
    │       └── data.parquet
    ├── stac/
    │   ├── collection.json        ← STAC Collection
    │   └── items/
    │       └── item-0.json        ← STAC Item
    └── tpos_2023.pmtiles
```

### Validate and Explore

```bash
# Validate STAC collection
python -c "
import pystac
c = pystac.Collection.from_file('output/tpos_2023/stac/collection.json')
print(f'✅ Collection: {c.id}')
print(f'   Extent: {c.extent.spatial.bboxes[0]}')
print(f'   Items: {len(list(c.get_items()))}')
"

# Explore with Python
python -c "
import json
with open('output/tpos_2023/stac/collection.json') as f:
    data = json.load(f)
    
print(f\"Instruments: {data['summaries']['instruments']}\")
print(f\"Measurements: {list(data['summaries']['measurements'].keys())}\")
"
```

### Load Data via STAC

```python
import pystac
import geopandas as gpd

# Load STAC item
item = pystac.Item.from_file("output/tpos_2023/stac/items/item-0.json")

# Get all GeoParquet assets
parquet_files = []
for key, asset in item.assets.items():
    if key.startswith("geoparquet"):
        # Resolve relative path
        rel_path = asset.href.replace("../", "")
        parquet_files.append(f"output/tpos_2023/{rel_path}")

# Load all data
gdf = gpd.read_parquet(parquet_files[0])
print(f"Loaded {len(gdf)} rows")
print(f"Columns: {list(gdf.columns)}")
print(f"Time range: {gdf['time'].min()} to {gdf['time'].max()}")
```

## Troubleshooting

### STAC Files Not Generated

**Problem**: No `stac/` directory after processing.

**Solutions**:

1. Check if STAC is disabled:
   ```bash
   # Enable STAC
   unset SEMANTIC_GENERATE_STAC  # Use default (true)
   # Or explicitly enable
   export SEMANTIC_GENERATE_STAC=true
   ```

2. Ensure semantic enrichment is enabled:
   ```bash
   export SEMANTIC_ENABLE=true
   ```

3. Check for processing errors:
   ```bash
   oceanstream process geotrack convert --input-source ./data --output-dir ./out --verbose
   ```

### Invalid STAC JSON

**Problem**: STAC validation fails.

**Solutions**:

1. Ensure data has required fields (latitude, longitude):
   ```bash
   oceanstream process geotrack convert --input-source ./data --output-dir ./out --list-columns
   ```

2. Check for missing time data:
   - STAC allows missing temporal extent (open intervals)
   - If `time` column exists, ensure valid datetime format

3. Validate with pystac:
   ```python
   import pystac
   
   try:
       c = pystac.Collection.from_file("output/campaign_id/stac/collection.json")
       c.validate()
       print("✅ Valid STAC Collection")
   except Exception as e:
       print(f"❌ Validation error: {e}")
   ```

### Relative Paths Not Resolving

**Problem**: Asset `href` paths don't resolve correctly.

**Solutions**:

1. STAC Items use relative paths from `stac/items/` directory:
   ```
   stac/items/item-0.json
   └─> "../lat_bin=X/lon_bin=Y/data.parquet"
   ```

2. Resolve manually:
   ```python
   from pathlib import Path
   
   stac_dir = Path("output/campaign_id/stac")
   item_path = stac_dir / "items" / "item-0.json"
   
   # Read item
   import json
   with open(item_path) as f:
       item = json.load(f)
   
   # Resolve asset path
   asset_href = item["assets"]["geoparquet"]["href"]
   resolved_path = (item_path.parent / asset_href).resolve()
   print(f"Resolved: {resolved_path}")
   ```

### PMTiles Asset Missing

**Problem**: No PMTiles asset in STAC Item.

**Solutions**:

1. Ensure PMTiles generation is enabled:
   ```bash
   oceanstream process geotrack convert \
     --input-source ./data \
     --output-dir ./out \
     --generate-pmtiles
   ```

2. Check PMTiles file exists:
   ```bash
   ls output/campaign_id/*.pmtiles
   ```

3. PMTiles asset only added to **first** STAC Item (covers all data):
   ```bash
   # Check first item
   cat output/campaign_id/stac/items/item-0.json | jq '.assets.pmtiles'
   ```

## See Also

- [Geotrack Convert Overview](../core-concepts/geotrack-convert-overview.md) - Main processing pipeline
- [Geotrack Convert Reference](../core-concepts/geotrack-convert-reference.md) - All CLI options
<!-- TODO: Add these guides
- **PMTiles Generation** - Vector tiles
- **Semantic Enrichment** - CF Standard Names
-->
- <!-- TODO: Add QGIS integration guide --> - GIS tool integration
- [STAC Specification](https://stacspec.org/) - Official STAC docs
