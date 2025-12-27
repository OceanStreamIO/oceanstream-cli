# PMTiles Configuration

Complete guide to all PMTiles generation options.

## CLI Flags Reference

### Primary Flag

```bash
--generate-pmtiles
```

Enables PMTiles generation (default: disabled).

### Customization Flags

#### `--pmtiles-minzoom <int>`

Minimum zoom level for vector tiles.

- **Default**: `0` (world view)
- **Range**: `0-15`
- **Guidelines**:
  - `0`: Global campaigns (world view)
  - `2-3`: Regional surveys (ocean basins)
  - `4-5`: Coastal studies
  - `6+`: Small area studies

**Example**:
```bash
--pmtiles-minzoom 2  # Start at regional scale
```

#### `--pmtiles-maxzoom <int>`

Maximum zoom level for vector tiles.

- **Default**: `10`
- **Range**: `0-15`
- **Guidelines**:
  - `8`: Basic track visualization
  - `10`: Standard detail (default)
  - `12`: High detail (measurements visible)
  - `14`: Very detailed (large file size)

**Example**:
```bash
--pmtiles-maxzoom 12  # Higher detail
```

#### `--pmtiles-layer <string>`

Layer name in the PMTiles file.

- **Default**: `"track"`
- **Format**: Alphanumeric + underscores (no spaces)
- **Use cases**:
  - Multiple campaigns: `"sd1030_track"`
  - Vessel type: `"drone_track"`
  - Custom naming: `"vessel_path"`

**Example**:
```bash
--pmtiles-layer "vessel_track"
```

⚠️ **Important**: Update JavaScript to match:
```javascript
'source-layer': 'vessel_track'  // Must match CLI flag
```

#### `--pmtiles-sample-rate <int>`

Point sampling rate (every Nth point).

- **Default**: `5` (every 5th point)
- **Range**: `1-100`
- **Trade-offs**:
  - Lower = more points = larger file + more detail
  - Higher = fewer points = smaller file + less detail

**Guidelines**:
| Sample Rate | Use Case | File Size Impact |
|-------------|----------|------------------|
| 1 | Maximum detail | ~5-10x larger |
| 3 | High detail | ~2-3x larger |
| 5 | Standard (default) | Baseline |
| 10 | Simplified track | ~50% smaller |
| 20 | Overview only | ~75% smaller |

**Example**:
```bash
--pmtiles-sample-rate 3  # More detail
```

## Complete CLI Example

```bash
oceanstream process geotrack \
  --input-source ./raw_data/sd1030_2023 \
  --output-dir ./output \
  --campaign-id sd1030_tpos_2023 \
  --generate-pmtiles \
  --pmtiles-minzoom 2 \
  --pmtiles-maxzoom 12 \
  --pmtiles-layer "saildrone_track" \
  --pmtiles-sample-rate 3 \
  --yes
```

## Python API

### Basic Generation

```python
from oceanstream.geotrack.processor import convert
from oceanstream.providers import get_provider
from pathlib import Path

provider = get_provider("saildrone")
convert(
    provider=provider,
    input_source=Path("./raw_data"),
    output_dir=Path("./output"),
    campaign_id="sd1030_2023",
    generate_pmtiles=True,
    yes=True
)
```

### Custom Configuration

```python
convert(
    provider=provider,
    input_source=Path("./raw_data"),
    output_dir=Path("./output"),
    campaign_id="sd1030_2023",
    generate_pmtiles=True,
    pmtiles_minzoom=2,
    pmtiles_maxzoom=12,
    pmtiles_layer="vessel_track",
    pmtiles_sample_rate=3,
    yes=True
)
```

### Direct PMTiles Generation

For advanced use cases, call the PMTiles generator directly:

```python
from oceanstream.geotrack.tiling.pmtiles import generate_pmtiles_from_geoparquet
from pathlib import Path

# Generate from existing GeoParquet
pmtiles_path = generate_pmtiles_from_geoparquet(
    parquet_dir=Path("./output/sd1030_2023"),
    output_path=Path("./output/tiles/custom_track.pmtiles"),
    minzoom=0,
    maxzoom=10,
    layer_name="track",
    sample_rate=5
)

print(f"PMTiles created: {pmtiles_path}")
```

## Auto-Selected Measurements

PMTiles automatically includes 12 key oceanographic measurements (when available):

### Temperature (3)
- `TEMP_AIR_MEAN` - Air temperature
- `TEMP_SBE37_MEAN` - Sea surface temperature (SBE37)
- `TEMP_DEPTH_HALFMETER_MEAN` - Temperature at 0.5m depth

### Salinity (1)
- `SAL_SBE37_MEAN` - Sea surface salinity

### Dissolved Oxygen (2)
- `O2_CONC_SBE37_MEAN` - Oxygen concentration
- `O2_SAT_SBE37_MEAN` - Oxygen saturation

### Chlorophyll (1)
- `CHLOR_WETLABS_MEAN` - Chlorophyll concentration

### Wind (2)
- `WIND_SPEED_MEAN` - Wind speed
- `WIND_FROM_MEAN` - Wind direction

### Waves (2)
- `WAVE_SIGNIFICANT_HEIGHT` - Wave height
- `WAVE_DOMINANT_PERIOD` - Wave period

### Pressure (1)
- `BARO_PRES_MEAN` - Barometric pressure

These measurements are accessible in JavaScript:

```javascript
map.on('click', 'track-lines', (e) => {
  const properties = e.features[0].properties;
  console.log('Temperature:', properties.TEMP_AIR_MEAN);
  console.log('Wind Speed:', properties.WIND_SPEED_MEAN);
});
```

## Track Segmentation

Tracks are automatically split into segments based on time gaps.

### Default Behavior
- **Time Gap**: 60 minutes (1 hour)
- **Logic**: If time between consecutive points exceeds 1 hour, start a new segment
- **Purpose**: Separate deployment phases, overnight stops, or data gaps

### Segment Properties

Each segment includes:
- `segment_id`: Unique integer (0, 1, 2, ...)
- `start_time`: ISO 8601 timestamp
- `end_time`: ISO 8601 timestamp
- `duration_hours`: Segment duration in hours

**Access in JavaScript**:
```javascript
const segment = e.features[0].properties;
console.log(`Segment ${segment.segment_id}: ${segment.duration_hours} hours`);
```

### Custom Segmentation (Python API)

```python
from oceanstream.geotrack.tiling.pmtiles import _segments_from_points
from datetime import timedelta

# Custom 30-minute segments
segments = _segments_from_points(
    points=track_points,
    time_gap=timedelta(minutes=30)
)
```

## Zoom Level Guidelines

### Data Volume Considerations

| Data Volume | Min Zoom | Max Zoom | Rationale |
|-------------|----------|----------|-----------|
| 100k - 500k pts | 0 | 10 | Standard detail |
| 500k - 1M pts | 0 | 12 | Higher detail viable |
| 1M - 5M pts | 2 | 10 | Skip world view |
| 5M+ pts | 2 | 8 | Simplify for performance |

### Use Case Recommendations

**Global Campaigns** (multiple ocean basins):
```bash
--pmtiles-minzoom 0 --pmtiles-maxzoom 10
```

**Regional Surveys** (single ocean basin):
```bash
--pmtiles-minzoom 2 --pmtiles-maxzoom 12
```

**Coastal Studies** (localized area):
```bash
--pmtiles-minzoom 4 --pmtiles-maxzoom 14
```

**Quick Preview** (fast load):
```bash
--pmtiles-minzoom 0 --pmtiles-maxzoom 8 --pmtiles-sample-rate 10
```

## Sampling Strategies

### Scenario 1: Maximum Detail

**Goal**: Preserve all scientific measurements.

```bash
--pmtiles-sample-rate 1  # No sampling (all points)
--pmtiles-maxzoom 14     # Very detailed tiles
```

**Trade-offs**:
- ✅ Full data fidelity
- ✅ All measurements preserved
- ❌ Large file size (~10-50 MB)
- ❌ Slower load times

### Scenario 2: Balanced (Default)

**Goal**: Good detail with reasonable file size.

```bash
--pmtiles-sample-rate 5  # Every 5th point
--pmtiles-maxzoom 10     # Standard detail
```

**Trade-offs**:
- ✅ Good visual detail
- ✅ Manageable file size (~2-10 MB)
- ✅ Fast load times
- ⚠️ Some data gaps

### Scenario 3: Overview Only

**Goal**: Fast loading, simple track visualization.

```bash
--pmtiles-sample-rate 10  # Every 10th point
--pmtiles-maxzoom 8       # Basic detail
```

**Trade-offs**:
- ✅ Very small file (~1-3 MB)
- ✅ Instant load
- ❌ Coarse resolution
- ❌ Loss of detail at high zoom

### Scenario 4: High-Resolution Analysis

**Goal**: Preserve detail for specific campaigns.

```bash
--pmtiles-sample-rate 2   # Every 2nd point
--pmtiles-maxzoom 12      # High detail
--pmtiles-minzoom 2       # Skip world view
```

**Trade-offs**:
- ✅ Excellent detail
- ✅ Measurements accessible
- ⚠️ Larger file (~5-20 MB)
- ⚠️ Moderate load times

## Performance Tuning

### File Size Optimization

**Target file size**: 2-10 MB for web delivery.

**If file is too large (>20 MB)**:
1. Increase `--pmtiles-sample-rate` to 10 or higher
2. Reduce `--pmtiles-maxzoom` to 8
3. Increase `--pmtiles-minzoom` to 2

**Example**:
```bash
# Before: 30 MB file
--pmtiles-sample-rate 1 --pmtiles-maxzoom 12

# After: 5 MB file
--pmtiles-sample-rate 10 --pmtiles-maxzoom 10
```

### Load Time Optimization

**For instant loading** (<1 second):
- Keep file under 5 MB
- Use CDN for hosting
- Enable HTTP/2
- Use browser caching

**Example**:
```bash
--pmtiles-sample-rate 10 \
--pmtiles-maxzoom 8 \
--pmtiles-minzoom 2
```

### Bandwidth Optimization

PMTiles uses HTTP range requests (only downloads needed tiles).

**Typical bandwidth usage**:
- Initial load: ~200-500 KB (metadata + visible tiles)
- Pan/zoom: ~50-200 KB per interaction
- Total for 5 minutes browsing: ~1-2 MB

**No optimization needed** - PMTiles handles this automatically.

## Output Structure

After generation:

```
output/
├── sd1030_2023/                # GeoParquet (primary data)
│   ├── lat_bin=-43/lon_bin=-170/
│   │   └── part-0.parquet
│   └── stac/
│       ├── collection.json      # Includes PMTiles asset link
│       └── items/
└── tiles/
    └── track.pmtiles            # Vector tiles file
```

PMTiles file automatically linked in STAC:

```json
{
  "assets": {
    "pmtiles": {
      "href": "../tiles/track.pmtiles",
      "title": "PMTiles vector tiles",
      "type": "application/vnd.pmtiles",
      "roles": ["tiles"]
    }
  }
}
```

## Dependencies

### Required CLI Tools

PMTiles generation requires external tools:

**Option 1: Tippecanoe + PMTiles CLI (Recommended)**

```bash
# macOS
brew install tippecanoe pmtiles

# Linux
# Build tippecanoe from source
# Download PMTiles binary
```

**Features**:
- ✅ Track segmentation
- ✅ Day markers
- ✅ Measurement properties
- ✅ Optimized tile generation

**Option 2: ogr2ogr (Fallback)**

```bash
# Usually pre-installed with GDAL
ogr2ogr --version
```

**Features**:
- ✅ Basic track visualization
- ❌ No segmentation
- ❌ No day markers
- ❌ Limited optimization

**Recommendation**: Always use Option 1 for production.

## Next Steps

- [Web Integration](web-integration.md) - MapLibre examples and styling
- [Hosting Guide](hosting.md) - Deploy to Azure, S3, or static hosting
- [Troubleshooting](troubleshooting.md) - Common issues and solutions
- [Overview](overview.md) - PMTiles concepts and benefits
