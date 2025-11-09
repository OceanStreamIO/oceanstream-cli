# Feature Spec: Multibeam Bathymetry & Backscatter Enrichment

## 1. Goal
Enable enrichment of Saildrone (or other vessel) oceanographic trackline data with high-resolution seafloor bathymetry and acoustic backscatter from multibeam echosounder (MBES) surveys. Provide simple point-sampling from pre-gridded multibeam products and optional direct point cloud queries for advanced use cases.

## 2. Non-Goals (MVP Boundaries)
- No raw multibeam processing (requires MB-System, Qimera, or CARIS HIPS).
- No real-time multibeam acquisition integration.
- No bathymetric grid generation or interpolation (use external tools).
- No uncertainty propagation modeling (TPU - Total Propagated Uncertainty).
- No sidescan sonar mosaics (focus on bathymetry + backscatter only).
- No terrain analysis beyond basic slope/aspect (post-MVP).

## 3. Problem Statement
Marine scientists collecting vessel trackline data (temperature, salinity, chlorophyll) often need to correlate measurements with seafloor characteristics:
- **Habitat Mapping**: Relate biological observations to substrate type (hard/soft bottom via backscatter).
- **Frontal Zones**: Identify shelf breaks, canyons, seamounts influencing oceanography.
- **Survey Optimization**: Avoid shallow hazards, target specific depth ranges.
- **Cross-Dataset Integration**: Merge trackline + multibeam from separate surveys.

**Current Pain Points**:
- Manual GIS workflows (QGIS extract by points, slow for large datasets).
- Proprietary software required (Arc/Hypack expensive).
- No standard Python library for multibeam + trackline fusion.
- Metadata provenance often lost (which multibeam grid? what processing?).

## 4. Use Cases

### Primary
1. **Benthic Habitat Studies**: Correlate plankton/fish abundance with bottom type (rocky vs sandy).
2. **Upwelling Analysis**: Detect bathymetric features (ridges, canyons) driving upwelling.
3. **Marine Protected Area Planning**: Quantify habitat diversity along survey tracks.
4. **Acoustic Ground-Truthing**: Compare water column Sv with seafloor backscatter.

### Secondary
5. **Navigation Safety**: Flag shallow areas (<10m) for USV path planning.
6. **Tidal Model Validation**: Use depth observations for model calibration.
7. **Change Detection**: Compare trackline depths across survey years.

## 5. Data Assumptions

### Input: Oceanstream CSV
- Mandatory columns: `latitude`, `longitude`, `time`.
- Typical resolution: 1-60 second intervals.
- Spatial extent: 10s-1000s of kilometers.

### Input: Multibeam Grids
- **Format**: GeoTIFF (preferably Cloud-Optimized GeoTIFF - COG) or NetCDF/Zarr.
- **Bathymetry Grid**: Single-band raster, depth values (positive = above datum, negative = below).
- **Backscatter Grid** (optional): Single-band raster, dB values (seafloor reflectivity).
- **Resolution**: 1-100 meters (typical: 5-25m depending on sonar frequency/depth).
- **CRS**: WGS84 (EPSG:4326) or projected (UTM).
- **Size**: 100 MB - 50 GB per grid (larger surveys 100s of GB, requires tiling/COG).

### Processing Assumptions
- Grids are pre-processed: cleaned, interpolated, projected.
- Vertical datum documented (MSL, MLLW, ellipsoid).
- Grids cover survey area (or user accepts NaN for out-of-bounds points).

## 6. Architecture Overview

```
┌─────────────────┐      ┌──────────────────────┐
│  Trackline CSV  │      │  Multibeam Grids     │
│  (lat/lon/time) │      │  - bathymetry.tif    │
└────────┬────────┘      │  - backscatter.tif   │
         │               └──────────┬───────────┘
         │                          │
         └────────┬─────────────────┘
                  ▼
         ┌────────────────────────┐
         │ Raster Adapter Layer   │
         │ - rasterio open()      │
         │ - sample() at points   │
         │ - CRS transform        │
         │ - windowed reading     │
         └────────┬───────────────┘
                  ▼
         ┌────────────────────────┐
         │ Enrichment Engine      │
         │ - Point sampling       │
         │ - Neighbor interpolate │
         │ - Quality flags        │
         │ - Metadata embed       │
         └────────┬───────────────┘
                  ▼
         ┌────────────────────────┐
         │ Enhanced GeoParquet    │
         │ + depth/backscatter    │
         └────────┬───────────────┘
                  │
      ┌───────────┴───────────────┐
      ▼                           ▼
┌─────────────┐          ┌────────────────┐
│ PMTiles Gen │          │  Analysis APIs │
│ - depth viz │          │  - bathymetry  │
│ - terrain   │          │  - correlations│
└─────────────┘          └────────────────┘
```

## 7. Implementation Components

### 7.1 Raster Adapter Module
**File**: `saildrone-geoparquet/pipeline/multibeam_adapter.py`

**Responsibilities**:
- Open GeoTIFF/COG grids (local or cloud S3/Azure).
- Sample raster values at point coordinates.
- Handle CRS transformations (WGS84 ↔ UTM).
- Manage large grids via windowed reading.
- Compute derived metrics (slope, aspect, roughness).

**Key Classes**:

```python
import rasterio
from rasterio.warp import transform
from rasterio.windows import Window
from typing import Optional, Dict, Tuple
import numpy as np
from pathlib import Path

class MultibeamGridAdapter:
    """Interface to gridded multibeam data for point sampling."""
    
    def __init__(
        self,
        bathymetry_path: Path | str,
        backscatter_path: Optional[Path | str] = None,
        cache_window_size: int = 1024
    ):
        """
        Open multibeam grids for sampling.
        
        Args:
            bathymetry_path: Path to bathymetry GeoTIFF
            backscatter_path: Optional path to backscatter GeoTIFF
            cache_window_size: Window size for chunked reading (pixels)
        """
        self.bathy_src = rasterio.open(bathymetry_path)
        self.bs_src = rasterio.open(backscatter_path) if backscatter_path else None
        self.cache_window_size = cache_window_size
        
    def sample_at_point(
        self, 
        lon: float, 
        lat: float,
        interpolate: bool = True
    ) -> Dict[str, float]:
        """
        Sample bathymetry and backscatter at a geographic point.
        
        Args:
            lon: Longitude (degrees)
            lat: Latitude (degrees)
            interpolate: Use bilinear interpolation vs nearest neighbor
            
        Returns:
            Dictionary with depth, backscatter, quality metrics
        """
        # Transform to raster CRS if needed
        if self.bathy_src.crs != 'EPSG:4326':
            lon_proj, lat_proj = transform(
                'EPSG:4326', self.bathy_src.crs, [lon], [lat]
            )
        else:
            lon_proj, lat_proj = [lon], [lat]
        
        # Sample bathymetry
        coords = list(zip(lon_proj, lat_proj))
        bathy_values = list(self.bathy_src.sample(coords, indexes=1))
        depth = float(bathy_values[0]) if bathy_values else np.nan
        
        # Sample backscatter
        backscatter = np.nan
        if self.bs_src:
        Enable enrichment of Oceanstream (or other vessel) oceanographic trackline data with high-resolution seafloor bathymetry and acoustic backscatter from multibeam echosounder (MBES) surveys. Provide simple point-sampling from pre-gridded multibeam products and optional direct point cloud queries for advanced use cases.
            backscatter = float(bs_values[0]) if bs_values else np.nan
        
        # Get grid metadata
        resolution = self._get_resolution_meters()
         Mandatory columns: `latitude`, `longitude`, `time`.
        return {
            'multibeam_depth_m': depth,
         **File**: `oceanstream/pipeline/multibeam_adapter.py`
         **File**: `oceanstream/pipeline/multibeam_enrichment.py`
         **File**: `oceanstream/cli.py`
        }
    
    def sample_batch(
        self,
        lons: np.ndarray,
            if b'oceanstream:enrichment' in meta:
                existing = json.loads(meta[b'oceanstream:enrichment'].decode('utf-8'))
                existing.update(enrichment_meta)
                meta[b'oceanstream:enrichment'] = json.dumps(existing).encode('utf-8')
        
                meta[b'oceanstream:enrichment'] = json.dumps(enrichment_meta).encode('utf-8')
            if b'oceanstream:units' in meta:
                existing_units = json.loads(meta[b'oceanstream:units'].decode('utf-8'))
                meta[b'oceanstream:units'] = json.dumps(existing_units).encode('utf-8')
        Returns:
                meta[b'oceanstream:units'] = json.dumps(units).encode('utf-8')
        """
        coords = list(zip(lons, lats))
        
        depths = np.array([v[0] for v in self.bathy_src.sample(coords, indexes=1)])
        oceanstream \
        backscatter = np.full_like(depths, np.nan)
        oceanstream \
            backscatter = np.array([v[0] for v in self.bs_src.sample(coords, indexes=1)])
        oceanstream \
        return {
        oceanstream \
            'multibeam_backscatter_db': backscatter,
            'multibeam_resolution_m': np.full(len(depths), self._get_resolution_meters())
        }
    
    def _get_resolution_meters(self) -> float:
        """Compute grid resolution in meters (approximate for lat/lon grids)."""
        res_deg = self.bathy_src.res[0]  # degrees
        if self.bathy_src.crs == 'EPSG:4326':
            # Rough approximation: 1 degree ~ 111 km at equator
            return abs(res_deg * 111000)
        else:
            # Projected CRS (meters)
            return abs(res_deg)
    
    def get_metadata(self) -> Dict:
        """Extract grid metadata for provenance."""
        return {
            'bathymetry_source': str(self.bathy_src.name),
            'backscatter_source': str(self.bs_src.name) if self.bs_src else None,
            'crs': self.bathy_src.crs.to_string(),
            'bounds': self.bathy_src.bounds,
            'resolution_deg': self.bathy_src.res,
            'resolution_m': self._get_resolution_meters(),
            'shape': self.bathy_src.shape,
            'dtype': str(self.bathy_src.dtypes[0])
        }
    
    def compute_terrain_metrics(
        self,
        lon: float,
        lat: float,
        window_size: int = 3
    ) -> Dict[str, float]:
        """
        Compute slope, aspect, roughness in neighborhood (post-MVP).
        
        Args:
            lon, lat: Center point
            window_size: Neighborhood size (pixels)
            
        Returns:
            Terrain metrics dictionary
        """
        # TODO: Implement gradient-based slope/aspect
        return {
            'slope_deg': np.nan,
            'aspect_deg': np.nan,
            'roughness': np.nan
        }
```

### 7.2 Enrichment Engine
**File**: `saildrone-geoparquet/pipeline/multibeam_enrichment.py`

**Responsibilities**:
- Batch-process Oceanstream DataFrame with multibeam sampling.
- Handle out-of-bounds points gracefully (NaN).
- Compute quality metrics (in_bounds, interpolation flags).
- Embed metadata in GeoParquet schema.

**Key Functions**:

```python
import pandas as pd
import pyarrow as pa
from dataclasses import dataclass
from typing import Optional

@dataclass
class MultibeamEnrichmentConfig:
    """Configuration for multibeam enrichment."""
    interpolate: bool = True
    compute_terrain: bool = False
    fill_out_of_bounds: Optional[float] = None  # Fill value for missing data
    vertical_datum: str = "MSL"  # Mean Sea Level, MLLW, ellipsoid, etc.
    batch_size: int = 1000  # Process N records at a time

def enrich_with_multibeam(
    df: pd.DataFrame,
    multibeam_adapter: MultibeamGridAdapter,
    config: MultibeamEnrichmentConfig
) -> pd.DataFrame:
    """
    Add multibeam depth and backscatter columns to DataFrame.
    
    Args:
        df: Input DataFrame with latitude, longitude
        multibeam_adapter: Initialized grid adapter
        config: Enrichment configuration
        
    Returns:
        DataFrame with added multibeam columns
    """
    # Batch sampling for performance
    results = multibeam_adapter.sample_batch(
        df['longitude'].values,
        df['latitude'].values
    )
    
    # Add columns
    for col_name, values in results.items():
        df[col_name] = values
    
    # Handle out-of-bounds
    if config.fill_out_of_bounds is not None:
        df['multibeam_depth_m'].fillna(config.fill_out_of_bounds, inplace=True)
    
    return df

def embed_multibeam_metadata(
    table: pa.Table,
    multibeam_adapter: MultibeamGridAdapter,
    config: MultibeamEnrichmentConfig
) -> pa.Table:
    """
    Add multibeam enrichment metadata to GeoParquet schema.
    
    Args:
        table: PyArrow table
        multibeam_adapter: Adapter with grid info
        config: Enrichment config
        
    Returns:
        Table with updated metadata
    """
    import json
    
    meta = dict(table.schema.metadata or {})
    
    grid_meta = multibeam_adapter.get_metadata()
    
    enrichment_meta = {
        'multibeam': {
            **grid_meta,
            'vertical_datum': config.vertical_datum,
            'interpolation_enabled': config.interpolate,
            'enrichment_timestamp': pd.Timestamp.now().isoformat()
        }
    }
    
    # Merge with existing enrichment metadata
    if b'oceanstream:enrichment' in meta:
        existing = json.loads(meta[b'oceanstream:enrichment'].decode('utf-8'))
        existing.update(enrichment_meta)
        meta[b'oceanstream:enrichment'] = json.dumps(existing).encode('utf-8')
    else:
        meta[b'oceanstream:enrichment'] = json.dumps(enrichment_meta).encode('utf-8')
    
    # Add units
    units = {
        'multibeam_depth_m': 'm',
        'multibeam_backscatter_db': 'dB',
        'multibeam_resolution_m': 'm'
    }
    
    if b'oceanstream:units' in meta:
        existing_units = json.loads(meta[b'oceanstream:units'].decode('utf-8'))
        existing_units.update(units)
        meta[b'oceanstream:units'] = json.dumps(existing_units).encode('utf-8')
    else:
        meta[b'oceanstream:units'] = json.dumps(units).encode('utf-8')
    
    return table.replace_schema_metadata(meta)
```

### 7.3 CLI Integration
**File**: `oceanstream/cli.py`

**New Flags**:

```python
# Multibeam enrichment flags
parser.add_argument(
    '--enrich-multibeam',
    type=Path,
    metavar='BATHYMETRY_TIF',
    help='Enrich with multibeam bathymetry grid (GeoTIFF/COG)'
)
parser.add_argument(
    '--enrich-multibeam-backscatter',
    type=Path,
    metavar='BACKSCATTER_TIF',
    help='Enrich with multibeam backscatter grid (GeoTIFF/COG)'
)
parser.add_argument(
    '--multibeam-vertical-datum',
    type=str,
    default='MSL',
    choices=['MSL', 'MLLW', 'MLW', 'MHW', 'NAVD88', 'ellipsoid'],
    help='Vertical datum for bathymetry grid (default: MSL)'
)
parser.add_argument(
    '--multibeam-interpolate',
    action='store_true',
    default=True,
    help='Use bilinear interpolation for sampling (default: enabled)'
)
parser.add_argument(
    '--multibeam-fill-missing',
    type=float,
    metavar='VALUE',
    help='Fill out-of-bounds points with this depth value (default: NaN)'
)
```

**Usage in CLI Main**:

```python
# In cli.py main() function
if args.enrich_multibeam:
    print(f"[cli] Loading multibeam bathymetry from {args.enrich_multibeam} ...")
    
    from pipeline.multibeam_adapter import MultibeamGridAdapter
    from pipeline.multibeam_enrichment import (
        enrich_with_multibeam,
        MultibeamEnrichmentConfig,
        embed_multibeam_metadata
    )
    
    multibeam = MultibeamGridAdapter(
        bathymetry_path=args.enrich_multibeam,
        backscatter_path=args.enrich_multibeam_backscatter
    )
    
    config = MultibeamEnrichmentConfig(
        interpolate=args.multibeam_interpolate,
        fill_out_of_bounds=args.multibeam_fill_missing,
        vertical_datum=args.multibeam_vertical_datum
    )
    
    print(f"[cli] Enriching {len(df)} records with multibeam data ...")
    df = enrich_with_multibeam(df, multibeam, config)
    
    # Later, before writing GeoParquet
    table = embed_multibeam_metadata(table, multibeam, config)
    
    in_bounds = df['multibeam_in_bounds'].sum()
    print(f"[cli] Sampled {in_bounds}/{len(df)} points within multibeam grid bounds")
```

## 8. Data Schema

### 8.1 New GeoParquet Columns

| Column Name | Type | Unit | Description | Nullable |
|-------------|------|------|-------------|----------|
| `multibeam_depth_m` | float64 | meters | Seafloor depth (negative = below datum) | Yes |
| `multibeam_backscatter_db` | float64 | dB | Seafloor acoustic reflectivity | Yes |
| `multibeam_resolution_m` | float64 | meters | Spatial resolution of source grid | No |
| `multibeam_in_bounds` | bool | - | True if point within grid extent | No |
| `multibeam_slope_deg` | float64 | degrees | Terrain slope (post-MVP) | Yes |
| `multibeam_aspect_deg` | float64 | degrees | Terrain aspect (post-MVP) | Yes |

### 8.2 Metadata Schema

```json
{
    "oceanstream:enrichment": {
        "multibeam": {
            "bathymetry_source": "s3://surveys/2024/bathymetry_10m.tif",
            "backscatter_source": "s3://surveys/2024/backscatter_10m.tif",
            "sonar_model": "Kongsberg EM2040",
            "processing_software": "MB-System 5.7.9",
            "processing_date": "2024-10-15",
            "vertical_datum": "Mean Sea Level (MSL)",
            "horizontal_crs": "EPSG:32610",
            "crs_wkt": "PROJCS[\"WGS 84 / UTM zone 10N\"...]",
            "grid_resolution_m": 10.0,
            "grid_bounds": {
                "west": -155.5,
                "south": -10.2,
                "east": -150.3,
                "north": -5.8
            },
            "interpolation_enabled": true,
            "enrichment_timestamp": "2024-11-07T20:15:00Z",
            "match_statistics": {
                "total_points": 9600,
                "in_bounds": 8945,
                "out_of_bounds": 655,
                "coverage_rate": 0.93
            }
        }
    },
    "oceanstream:units": {
        "multibeam_depth_m": "m",
        "multibeam_backscatter_db": "dB",
        "multibeam_resolution_m": "m"
    }
}
```

## 9. Usage Examples

### 9.1 Basic Enrichment

```bash
# Sample bathymetry only
oceanstream \
  --input-dir raw_data \
  --output-dir out/geoparquet \
  --enrich-multibeam bathymetry_10m.tif \
  -v
```

### 9.2 Bathymetry + Backscatter

```bash
# Full multibeam enrichment
oceanstream \
  --input-dir raw_data \
  --output-dir out/geoparquet \
  --enrich-multibeam bathymetry_10m.tif \
  --enrich-multibeam-backscatter backscatter_10m.tif \
  --multibeam-vertical-datum MLLW \
  -v
```

### 9.3 Cloud-Optimized GeoTIFF from S3

```bash
# Direct cloud access (requires AWS credentials)
oceanstream \
  --input-dir raw_data \
  --output-dir out/geoparquet \
  --enrich-multibeam /vsis3/noaa-bathymetry/pacific/bathymetry.tif \
  --enrich-multibeam-backscatter /vsis3/noaa-bathymetry/pacific/backscatter.tif \
  -v
```

### 9.4 Combined Acoustic + Multibeam

```bash
# Enrich with both water column and seafloor acoustics
oceanstream \
  --input-dir raw_data \
  --output-dir out/geoparquet \
  --enrich-acoustic acoustic.zarr \
  --enrich-multibeam bathymetry.tif \
  --enrich-multibeam-backscatter backscatter.tif \
  --generate-pmtiles \
  --pmtiles-output out/web/full_acoustic.pmtiles \
  -v
```

## 10. Analysis Use Cases

### 10.1 Habitat Correlation

```python
import geopandas as gpd
import seaborn as sns

# Load enriched data
gdf = gpd.read_parquet('out/geoparquet')

# Classify bottom type by backscatter
gdf['bottom_type'] = pd.cut(
    gdf['multibeam_backscatter_db'],
    bins=[-100, -30, -20, -10, 0],
    labels=['soft', 'mixed', 'hard', 'rock']
)

# Correlate with chlorophyll
sns.boxplot(data=gdf, x='bottom_type', y='CHLA_MEAN')
```

### 10.2 Bathymetric Feature Detection

```python
# Identify shelf break
gdf['depth_gradient'] = gdf['multibeam_depth_m'].diff() / gdf.geometry.distance(gdf.geometry.shift())
shelf_break = gdf[gdf['depth_gradient'].abs() > 0.05]  # >5% slope
```

### 10.3 PMTiles Visualization

Enhanced web viewer with seafloor styling:

```javascript
// Add bathymetry-styled track
map.addLayer({
    'id': 'bathymetry-track',
    'type': 'circle',
    'source': 'oceanstream',
    'paint': {
        'circle-radius': 4,
        'circle-color': [
            'interpolate', ['linear'], ['get', 'multibeam_depth_m'],
            0, '#d7191c',      // Shallow (red)
            -50, '#fdae61',    // Continental shelf (orange)
            -200, '#ffffbf',   // Slope (yellow)
            -1000, '#abd9e9',  // Abyss (light blue)
            -5000, '#2c7bb6'   // Deep trench (dark blue)
        ]
    }
});

// Add backscatter layer
map.addLayer({
    'id': 'backscatter-track',
    'type': 'circle',
    'source': 'oceanstream',
    'paint': {
        'circle-radius': 4,
        'circle-color': [
            'interpolate', ['linear'], ['get', 'multibeam_backscatter_db'],
            -40, '#fee5d9',    // Soft bottom (sand)
            -30, '#fcae91',
            -20, '#fb6a4a',    // Mixed
            -10, '#cb181d'     // Hard bottom (rock)
        ]
    }
});
```

## 11. Performance Considerations

### 11.1 Large Grid Handling

**Problem**: 50 GB bathymetry grid exceeds memory.

**Solution**: Cloud-Optimized GeoTIFF (COG) with windowed reading:

```bash
# Convert to COG (external step)
gdal_translate \
  -of COG \
  -co COMPRESS=DEFLATE \
  -co BLOCKSIZE=512 \
  -co OVERVIEW_RESAMPLING=BILINEAR \
  bathymetry.tif \
  bathymetry_cog.tif
```

**Python**: rasterio reads only needed tiles.

### 11.2 Batch Sampling

- **Vectorized**: Use `rasterio.sample()` with array of coordinates (10-100× faster than loop).
- **Benchmark**: 10K points sampled from 10 GB COG: ~5 seconds.

### 11.3 CRS Transformation Overhead

- **Problem**: Repeated `pyproj` transforms slow.
- **Solution**: Batch transform all coordinates once before sampling.

## 12. Testing Strategy

### 12.1 Unit Tests

**File**: `tests/unit/test_multibeam_adapter.py`

```python
def test_multibeam_adapter_opens_tif(tmp_path):
    # Create test GeoTIFF
    tif_path = tmp_path / 'test_bathy.tif'
    # ... create with rasterio
    
    adapter = MultibeamGridAdapter(tif_path)
    assert adapter.bathy_src is not None

def test_sample_at_point_in_bounds():
    adapter = MultibeamGridAdapter('tests/fixtures/bathy_test.tif')
    result = adapter.sample_at_point(lon=-152.5, lat=-8.5)
    
    assert 'multibeam_depth_m' in result
    assert result['multibeam_in_bounds'] is True
    assert not np.isnan(result['multibeam_depth_m'])

def test_sample_out_of_bounds_returns_nan():
    adapter = MultibeamGridAdapter('tests/fixtures/bathy_test.tif')
    result = adapter.sample_at_point(lon=0, lat=0)  # Far from grid
    
    assert np.isnan(result['multibeam_depth_m'])
    assert result['multibeam_in_bounds'] is False
```

### 12.2 Integration Tests

```python
def test_end_to_end_multibeam_enrichment(tmp_path):
    # Create test CSV and GeoTIFF
    csv_path = tmp_path / 'raw_data'
    csv_path.mkdir()
    # ... create test data
    
    tif_path = tmp_path / 'bathy.tif'
    # ... create test raster
    
    result = subprocess.run([
        'oceanstream',
        '--input-dir', str(csv_path),
        '--output-dir', str(tmp_path / 'out'),
        '--enrich-multibeam', str(tif_path)
    ], capture_output=True)
    
    assert result.returncode == 0
    
    gdf = gpd.read_parquet(tmp_path / 'out')
    assert 'multibeam_depth_m' in gdf.columns
    assert gdf['multibeam_depth_m'].notna().sum() > 0
```

## 13. Error Handling

### 13.1 Invalid Grid Path

```python
# Raise helpful error
try:
    adapter = MultibeamGridAdapter('/nonexistent/bathy.tif')
except FileNotFoundError as e:
    print("Error: Bathymetry grid not found. Check path and access permissions.")
```

### 13.2 CRS Mismatch

```python
# Warn if grid CRS differs from expected
if adapter.bathy_src.crs != 'EPSG:4326':
    print(f"Warning: Grid CRS is {adapter.bathy_src.crs}, will transform coordinates.")
```

### 13.3 Missing Backscatter Grid

```python
# Gracefully handle optional backscatter
if args.enrich_multibeam_backscatter and not Path(args.enrich_multibeam_backscatter).exists():
    print("Warning: Backscatter grid not found, skipping backscatter enrichment.")
    args.enrich_multibeam_backscatter = None
```

## 14. Dependencies

Add to `pyproject.toml`:

```toml
[tool.poetry.dependencies]
rasterio = ">=1.3.9"         # GeoTIFF I/O and sampling
shapely = ">=2.0"            # Geometry operations (already present)
pyproj = ">=3.5"             # CRS transformations

[tool.poetry.extras]
multibeam = ["rasterio", "pyproj"]
```

## 15. Documentation Updates

### 15.1 README.md Addition

```markdown
## Multibeam Bathymetry Enrichment

Enrich trackline data with high-resolution seafloor depth and backscatter
from multibeam echosounder surveys.

### Quick Start

```bash
# Enrich with bathymetry
saildrone \
  --input-dir raw_data \
  --output-dir out/enriched \
  --enrich-multibeam bathymetry_10m.tif \
  -v

# Add backscatter
saildrone \
  --input-dir raw_data \
  --output-dir out/enriched \
  --enrich-multibeam bathymetry_10m.tif \
  --enrich-multibeam-backscatter backscatter_10m.tif \
  -v
```

### Enriched Columns

- `multibeam_depth_m`: Seafloor depth in meters (negative = below MSL)
- `multibeam_backscatter_db`: Acoustic reflectivity (substrate proxy)
- `multibeam_resolution_m`: Grid spatial resolution

See [docs/multibeam-enrichment.md](docs/multibeam-enrichment.md) for details.
```

## 16. Future Enhancements (Post-MVP)

1. **Terrain Analysis**: Compute slope, aspect, curvature, rugosity.
2. **Point Cloud Support**: Direct LAZ/LAS sampling (no pre-gridding).
3. **Uncertainty Propagation**: Include TPU (Total Propagated Uncertainty) fields.
4. **Multi-Grid Mosaicking**: Merge overlapping multibeam surveys automatically.
5. **Temporal Changes**: Compare multi-year bathymetry grids (change detection).
6. **Sidescan Integration**: Add sidescan mosaic sampling for substrate classification.
7. **3D Visualization**: Export enriched data to Cesium/deck.gl 3D viewers.

## 17. Success Criteria (MVP)

### Functional
- [ ] Successfully enrich 10K point Oceanstream dataset with 10m resolution bathymetry in <10 seconds.
- [ ] Handle 50 GB COG bathymetry grid without memory issues.
- [ ] Correctly transform coordinates between WGS84 and UTM grids.
- [ ] Generate PMTiles with bathymetry-styled track points.

### Quality
- [ ] All unit tests pass (>90% coverage).
- [ ] Integration test covers full CLI workflow.
- [ ] Documentation includes grid preparation examples.

### Performance
- [ ] Batch sampling 10K points from COG: <5 seconds.
- [ ] Memory usage <2 GB for 50 GB grid (windowed reading validated).

## 18. Implementation Roadmap

### Phase 1: Core Sampling (Week 1)
- [ ] Implement `MultibeamGridAdapter` class.
- [ ] Add rasterio-based point sampling.
- [ ] Handle CRS transformations.
- [ ] Write unit tests for adapter.

### Phase 2: Enrichment Engine (Week 1-2)
- [ ] Implement `enrich_with_multibeam()` function.
- [ ] Add batch sampling optimization.
- [ ] Embed metadata in GeoParquet schema.
- [ ] Handle out-of-bounds points gracefully.

### Phase 3: CLI Integration (Week 2)
- [ ] Add CLI flags (`--enrich-multibeam`, etc.).
- [ ] Wire adapter into main CLI flow.
- [ ] Add progress reporting and diagnostics.

### Phase 4: PMTiles Support (Week 2-3)
- [ ] Add bathymetry/backscatter styling to PMTiles generator.
- [ ] Update web viewer with depth color ramps.
- [ ] Add legend for seafloor types.

### Phase 5: Testing & Docs (Week 3)
- [ ] Create test fixtures (small GeoTIFFs).
- [ ] Write integration tests.
- [ ] Update README and create detailed docs.

### Phase 6: Optimization & Release (Week 3-4)
- [ ] Performance benchmarking.
- [ ] COG optimization guide.
- [ ] Example notebooks.
- [ ] GitHub release with demo data.

## 19. External Tools & Preprocessing

### 19.1 Multibeam Processing Software

Users must pre-process raw multibeam data using:

- **MB-System** (open source): `mbprocess`, `mbgrid`, `mbbackangle`
- **QPS Qimera** (commercial): Industry standard
- **CARIS HIPS/SIPS** (commercial): NOAA/academic
- **CloudCompare** (open source): Point cloud viewer/editor

### 19.2 Grid Generation Example

```bash
# MB-System workflow
mbm_grid \
  -I processed_multibeam.mb59 \
  -O bathymetry_10m \
  -E10/10/meters \
  -A2 \
  -G3 \
  -V

# Convert to COG
gdal_translate \
  -of COG \
  -co COMPRESS=DEFLATE \
  bathymetry_10m.grd \
  bathymetry_10m.tif
```

## 20. Open Questions

1. **Vertical Datum Conversion**: Should we support automatic datum transforms (MSL ↔ MLLW)?
   - **Decision**: Document datums only; user responsible for conversion (use VDatum tool).

2. **Interpolation Method**: Bilinear sufficient or support cubic/spline?
   - **Decision**: Bilinear for MVP; add `--multibeam-interpolation-method` flag later.

3. **Terrain Metrics**: Which are most valuable (slope, aspect, roughness)?
   - **Decision**: Defer to post-MVP; gather user feedback first.

4. **Multiple Grids**: Support automatic mosaicking of overlapping grids?
   - **Decision**: Not MVP; recommend users pre-merge grids with `gdal_merge.py`.

## 21. Security & Licensing

- **Cloud Access**: Use standard rasterio GDAL virtual file systems (`/vsis3/`, `/vsigs/`, `/vsiaz/`).
- **Credentials**: Respect AWS/Azure environment variables; no CLI exposure.
- **Data Attribution**: Include source grid provenance in metadata.

---

**Status**: Draft ready for implementation.  
**Est. Effort**: 3-4 weeks (1 developer).  
**Priority**: Medium-High (complements acoustic enrichment, strong research demand).
