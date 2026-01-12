# OceanStream CLI Tool - TODO

## Direction Arrows in PMTiles

**Priority**: Medium  
**Complexity**: Medium  
**Tracking**: [oceanstream_web_app conversation 2026-01-08]

### Background

Currently, direction arrows indicating travel direction along ocean tracks are rendered dynamically in the frontend by:

1. Fetching point data from PostGIS (`/api/postgis/campaigns/{id}/geotrack?sample_uniform=true`)
2. Grouping points by platform and sorting by time
3. Calculating bearing angles between consecutive points
4. Rendering arrow markers at evenly-spaced intervals along each track

This approach has several drawbacks:
- **Extra API call** on every map load and zoom change
- **Latency** waiting for PostGIS query (especially with time filtering)
- **Computation** done client-side for bearing calculations
- **Inconsistency** - arrows regenerate on zoom, causing visual flicker

### Proposed Solution

Embed direction arrow data directly in the PMTiles during tile generation, similar to how we already emit `day` start/end point markers.

### Implementation Details

#### 1. Modify `_build_ndjson_from_geoparquet()` in `geotrack/tiling/pmtiles.py`

Add a new feature type for direction arrows alongside segments and day markers:

```python
# After segment creation, add arrow point features
# These would be Point geometries with bearing/direction properties

arrow_props = {
    "type": "arrow",           # Feature type identifier
    "platform_id": str,        # Platform/vessel identifier
    "bearing": float,          # Direction in degrees (0-360, clockwise from north)
    "t": str,                  # ISO timestamp
    "day": str,                # Date string for filtering
}
```

#### 2. Arrow Placement Strategy

Place arrows at regular intervals along each segment:

```python
def _generate_arrow_features(
    segment_coords: list[tuple[float, float]],
    segment_timestamps: list[datetime],  # Would need to preserve per-point timestamps
    platform_id: str,
    arrows_per_segment: int = 3,  # Configurable density
) -> list[dict]:
    """
    Generate arrow point features for a track segment.
    
    Args:
        segment_coords: List of (lon, lat) tuples forming the segment
        segment_timestamps: Corresponding timestamps for each coordinate
        platform_id: Platform identifier
        arrows_per_segment: How many arrows to place per segment
        
    Returns:
        List of GeoJSON Point features with bearing properties
    """
    if len(segment_coords) < 2:
        return []
    
    arrows = []
    step = max(1, len(segment_coords) // (arrows_per_segment + 1))
    
    for i in range(step, len(segment_coords) - 1, step):
        prev_coord = segment_coords[max(0, i - 1)]
        curr_coord = segment_coords[i]
        next_coord = segment_coords[min(len(segment_coords) - 1, i + 1)]
        
        # Calculate bearing from prev to next for smoother direction
        bearing = calculate_bearing(prev_coord, next_coord)
        
        feature = {
            "type": "Feature",
            "properties": {
                "type": "arrow",
                "platform_id": platform_id,
                "bearing": round(bearing, 1),
                "t": segment_timestamps[i].isoformat() if segment_timestamps else None,
            },
            "geometry": {
                "type": "Point",
                "coordinates": [curr_coord[0], curr_coord[1]]
            }
        }
        arrows.append(feature)
    
    return arrows


def calculate_bearing(point_a: tuple[float, float], point_b: tuple[float, float]) -> float:
    """
    Calculate compass bearing from point A to point B.
    
    Args:
        point_a: (longitude, latitude) of starting point
        point_b: (longitude, latitude) of ending point
        
    Returns:
        Bearing in degrees (0-360, clockwise from north)
    """
    import math
    
    lon1, lat1 = math.radians(point_a[0]), math.radians(point_a[1])
    lon2, lat2 = math.radians(point_b[0]), math.radians(point_b[1])
    
    d_lon = lon2 - lon1
    
    y = math.sin(d_lon) * math.cos(lat2)
    x = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(d_lon)
    
    bearing = math.atan2(y, x)
    bearing = math.degrees(bearing)
    bearing = (bearing + 360) % 360
    
    return bearing
```

#### 3. Preserve Per-Point Timestamps

Current implementation loses individual timestamps when building segments - only `t_start` and `t_end` are preserved. To support arrow timestamps, modify `_segments_from_points()` to optionally preserve all timestamps:

```python
# In _segments_from_points(), add to segment dict:
segments.append({
    "coords": coords,
    "timestamps": [t for _, _, t, _, _ in current],  # NEW: all timestamps
    "t_start": current[0][2],
    "t_end": current[-1][2],
    "measurements": avg_measurements,
    "platform_id": seg_platform_id,
})
```

#### 4. CLI Parameters

Add new parameters to `generate_pmtiles_from_geoparquet()`:

```python
def generate_pmtiles_from_geoparquet(
    ...
    include_arrows: bool = True,           # NEW: Generate arrow features
    arrows_per_segment: int = 3,           # NEW: Arrow density per segment
    arrow_min_segment_points: int = 5,     # NEW: Min points in segment to add arrows
) -> Path:
```

#### 5. Frontend Changes

Update `EsriMapView.tsx` to render arrows from VectorTileLayer instead of fetching separately:

```typescript
// In buildVectorTileStyle(), add arrow layer:
{
  id: 'track-arrows',
  type: 'symbol',
  source: 'esri',
  'source-layer': 'track',
  filter: ['==', ['get', 'type'], 'arrow'],
  minzoom: 4,  // Only show at reasonable zoom
  layout: {
    'icon-image': 'triangle-11',  // Or custom arrow sprite
    'icon-rotate': ['get', 'bearing'],
    'icon-rotation-alignment': 'map',
    'icon-allow-overlap': true,
    'icon-size': [
      'interpolate', ['linear'], ['zoom'],
      4, 0.4,
      8, 0.6,
      12, 0.8
    ],
  },
  paint: {
    'icon-color': [
      'match', ['get', 'platform_id'],
      'sd1030', '#666666',
      'sd1031', '#808080',
      // ... platform colors
      '#999999'  // default
    ],
    'icon-opacity': 0.85,
  },
}
```

**Note**: MapLibre/ArcGIS symbol layers require sprite images. Alternative: use `circle` layer with rotation (may need custom approach).

#### 6. Alternative: Use Rotated Circles

If sprite-based arrows are complex, consider encoding arrow direction as a rotated circle or using the existing GraphicsLayer approach but with data from the tile's `arrow` features instead of a separate API call:

```typescript
// Query arrow features from already-loaded VectorTileLayer
const arrowFeatures = vectorTileLayer.queryFeatures({
  where: "type = 'arrow'",
  outFields: ['platform_id', 'bearing'],
  returnGeometry: true,
});
```

### Benefits

1. **No additional API calls** - arrows load with tiles
2. **Consistent rendering** - same data at all zoom levels
3. **Offline support** - works with cached tiles
4. **Time filtering** - use existing `day` property for temporal filtering
5. **Reduced server load** - no PostGIS queries for arrows

### Considerations

- **Tile size increase** - Each arrow is a Point feature (~50-100 bytes)
  - Estimate: 3 arrows × 100 segments × campaigns = minimal impact
- **Arrow density** - May need zoom-dependent visibility in style
- **Backwards compatibility** - Old tiles won't have arrows; frontend should gracefully handle missing arrow features

### Testing

1. Generate test PMTiles with `include_arrows=True`
2. Inspect NDJSON intermediate file to verify arrow features
3. Load in frontend and verify arrow display
4. Compare tile sizes with/without arrows
5. Test time filtering still works (arrows have `day` property)

### Files to Modify

| File | Changes |
|------|---------|
| `geotrack/tiling/pmtiles.py` | Add `calculate_bearing()`, modify `_segments_from_points()`, add `_generate_arrow_features()`, update `_build_ndjson_from_geoparquet()` |
| `geotrack/tiling/__init__.py` | Export new parameters if needed |
| `cli.py` / `cli_new.py` | Add `--include-arrows`, `--arrows-per-segment` CLI flags |
| Frontend: `EsriMapView.tsx` | Remove `generateArrows()` fetch logic, add tile-based arrow rendering |

### Related

- Current arrow implementation: `oceanstream_web_app/src/components/EsriMapView.tsx` lines 726-830
- PostGIS endpoint with uniform sampling: `oceanstream_web_app/server/routes/postgis_geotrack.py`
- Segment properties already include: `t_start`, `t_end`, `day`, `platform_id`

---

## Pre-Generated Measurement Heatmaps (COG Rasters)

**Priority**: High  
**Complexity**: Medium-High  
**Tracking**: [oceanstream_web_app conversation 2026-01-08]

### Background

The web app currently has an experimental feature to visualize in-situ measurements as interpolated heatmaps (using IDW - Inverse Distance Weighting). However, generating these on-the-fly via API is too slow for production:

1. API endpoint receives viewport bounds + variable name
2. Fetches ~5000 sample points from PostGIS
3. Runs IDW interpolation to create a grid (O(n×m) complexity where n=points, m=grid cells)
4. Returns GeoJSON polygons representing grid cells
5. Frontend renders as GeoJSONLayer with color ramp

**Problems with current approach:**
- **Slow**: IDW computation takes 1-5 seconds per request
- **Redundant**: Same interpolation repeated on every pan/zoom
- **Resource intensive**: Server CPU spikes during interpolation
- **Poor UX**: Users see delayed loading on viewport changes

### Proposed Solution

Pre-generate interpolated raster files at data ingestion time:

1. **Cloud Optimized GeoTIFF (COG)** for each measurement variable
2. Serve via HTTP range requests (no tile server needed)
3. Frontend uses `ImageryTileLayer` or `WebTileLayer` to render

### Implementation Details

#### 1. New Module: `geotrack/raster/heatmap.py`

```python
"""
Generate interpolated measurement heatmaps as Cloud Optimized GeoTIFF (COG).
"""
import numpy as np
from scipy.interpolate import griddata
from osgeo import gdal, osr
import rasterio
from rasterio.transform import from_bounds

def generate_measurement_cog(
    geoparquet_root: Path,
    output_path: Path,
    variable: str,
    *,
    resolution_deg: float = 0.1,  # Grid cell size in degrees
    method: str = 'linear',       # 'linear', 'nearest', 'cubic'
    search_radius_deg: float = 1.0,  # Max extrapolation distance
    nodata: float = -9999.0,
) -> Path:
    """
    Generate a COG heatmap for a measurement variable.
    
    Args:
        geoparquet_root: Root of partitioned GeoParquet dataset
        output_path: Output .tif path
        variable: Variable name from properties (e.g., 'TEMP_SBE37_MEAN')
        resolution_deg: Grid resolution in degrees
        method: Interpolation method ('linear' recommended for oceanographic data)
        search_radius_deg: Maximum distance from data points to interpolate
        nodata: NoData value for areas outside interpolation range
        
    Returns:
        Path to generated COG file
    """
    # 1. Read measurement points from GeoParquet
    points, values = _extract_measurements(geoparquet_root, variable)
    
    # 2. Determine bounding box with small buffer
    min_lon, max_lon = points[:, 0].min() - 0.1, points[:, 0].max() + 0.1
    min_lat, max_lat = points[:, 1].min() - 0.1, points[:, 1].max() + 0.1
    
    # 3. Create grid
    x_steps = int((max_lon - min_lon) / resolution_deg)
    y_steps = int((max_lat - min_lat) / resolution_deg)
    
    xi = np.linspace(min_lon, max_lon, x_steps)
    yi = np.linspace(min_lat, max_lat, y_steps)
    xi, yi = np.meshgrid(xi, yi)
    
    # 4. Interpolate using scipy.griddata
    zi = griddata(points, values, (xi, yi), method=method, fill_value=nodata)
    
    # 5. Mask areas far from data points
    zi = _mask_distant_cells(zi, xi, yi, points, search_radius_deg, nodata)
    
    # 6. Write as GeoTIFF
    _write_geotiff(output_path, zi, min_lon, max_lon, min_lat, max_lat, nodata)
    
    # 7. Convert to COG (Cloud Optimized GeoTIFF)
    cog_path = _convert_to_cog(output_path)
    
    return cog_path


def _mask_distant_cells(
    grid: np.ndarray,
    xi: np.ndarray, 
    yi: np.ndarray,
    points: np.ndarray,
    max_dist: float,
    nodata: float,
) -> np.ndarray:
    """
    Set cells far from any data point to nodata.
    Uses KD-tree for efficient nearest neighbor search.
    """
    from scipy.spatial import cKDTree
    
    tree = cKDTree(points)
    grid_points = np.column_stack([xi.ravel(), yi.ravel()])
    distances, _ = tree.query(grid_points)
    
    mask = distances.reshape(grid.shape) > max_dist
    grid[mask] = nodata
    
    return grid
```

#### 2. Integration with CLI

Add to `cli.py` or `cli_new.py`:

```python
@app.command()
def generate_heatmaps(
    geoparquet_root: Path,
    output_dir: Path,
    variables: Optional[List[str]] = None,  # None = auto-detect numeric variables
    resolution: float = 0.1,
    method: str = "linear",
):
    """Generate COG heatmaps for measurement variables."""
    if variables is None:
        variables = discover_numeric_variables(geoparquet_root)
    
    for var in variables:
        output_path = output_dir / f"{var.lower()}.tif"
        generate_measurement_cog(
            geoparquet_root,
            output_path,
            var,
            resolution_deg=resolution,
            method=method,
        )
        print(f"Generated: {output_path}")
```

#### 3. Storage Structure

```
campaign_data/
├── geoparquet/           # Track points (existing)
│   └── ...
├── pmtiles/              # Vector tiles (existing)
│   └── track.pmtiles
└── heatmaps/             # NEW: Raster heatmaps
    ├── temp_sbe37_mean.tif
    ├── sal_sbe37_mean.tif
    ├── chlor_wetlabs_mean.tif
    └── manifest.json     # Variable metadata (ranges, units)
```

#### 4. Manifest File

```json
{
  "variables": [
    {
      "name": "TEMP_SBE37_MEAN",
      "file": "temp_sbe37_mean.tif",
      "unit": "°C",
      "min": 20.5,
      "max": 31.2,
      "colormap": "thermal"
    },
    {
      "name": "SAL_SBE37_MEAN", 
      "file": "sal_sbe37_mean.tif",
      "unit": "PSU",
      "min": 33.5,
      "max": 36.8,
      "colormap": "viridis"
    }
  ]
}
```

#### 5. Frontend Integration

Replace GeoJSONLayer with ImageryTileLayer:

```typescript
// Load COG directly via HTTP range requests
const heatmapLayer = new ImageryTileLayer({
  url: `${storageUri}/heatmaps/${variable.toLowerCase()}.tif`,
  title: `${variable} (In-situ)`,
  opacity: 0.6,
  // COG renderer with color ramp
  renderer: {
    type: 'raster-stretch',
    colorRamp: {
      type: 'multipart',
      colorRamps: [
        { fromColor: '#313695', toColor: '#74add1' },
        { fromColor: '#74add1', toColor: '#ffffbf' },
        { fromColor: '#ffffbf', toColor: '#f46d43' },
        { fromColor: '#f46d43', toColor: '#a50026' },
      ],
    },
  },
});
```

### Dependencies

Add to `pyproject.toml`:
```toml
[tool.poetry.dependencies]
rasterio = "^1.3"
scipy = "^1.11"
```

Or for GDAL-based approach:
```toml
gdal = "^3.6"
```

### Benefits

1. **Fast loading** - COG supports HTTP range requests, loads only visible tiles
2. **No server computation** - Static files served directly from storage
3. **Cacheable** - CDN-friendly, browser caches tiles
4. **Standard format** - GeoTIFF works with any GIS software
5. **Offline support** - Can be downloaded and used locally

### Considerations

- **Storage size** - Each COG ~1-10MB per variable depending on extent/resolution
- **Update frequency** - Regenerate when source data changes
- **Multiple resolutions** - Consider generating overview pyramids for smooth zooming
- **Time dimension** - For time-varying data, could generate daily/weekly COGs

### Files to Create/Modify

| File | Changes |
|------|---------|
| `geotrack/raster/__init__.py` | New module |
| `geotrack/raster/heatmap.py` | Main COG generation logic |
| `cli.py` / `cli_new.py` | Add `generate-heatmaps` command |
| `pyproject.toml` | Add rasterio/scipy dependencies |
| Frontend: `EsriMapView.tsx` | Replace GeoJSONLayer with ImageryTileLayer |
| Frontend: `DataExplorer.tsx` | Load manifest.json for variable list |

### Related

- Current slow implementation: `oceanstream_web_app/server/routes/postgis_geotrack.py` `/heatmap` endpoint
- COG specification: https://www.cogeo.org/
- ArcGIS ImageryTileLayer: https://developers.arcgis.com/javascript/latest/api-reference/esri-layers-ImageryTileLayer.html
- rasterio COG guide: https://rasterio.readthedocs.io/en/latest/topics/windowed-rw.html
