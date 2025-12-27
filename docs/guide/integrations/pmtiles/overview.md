# PMTiles Overview

Generate cloud-optimized vector tiles for interactive web maps from oceanographic track data.

## What is PMTiles?

PMTiles is a single-file format for storing vector map tiles that can be efficiently served over HTTP with range requests. OceanStream automatically generates PMTiles from GeoParquet data, creating:

- **Track Segments**: Time-based line segments with oceanographic measurements
- **Day Markers**: Start/end points for each UTC day of the campaign
- **Optimized Tiles**: Smart simplification across zoom levels (0-15)

## Key Benefits

- **Single File**: One `.pmtiles` file instead of thousands of individual tiles
- **HTTP Range Requests**: Efficient streaming from any static file server or CDN
- **Cloud-Native**: Works seamlessly with Azure Blob Storage, AWS S3, or any HTTP server
- **MapLibre Compatible**: Drop-in support for modern web mapping libraries
- **Self-Contained**: No database or tile server required

## When to Use PMTiles

### ✅ Ideal For

- **Web Visualization**: Interactive maps in browsers or web applications
- **Real-Time Dashboards**: Monitor vessel tracks and measurements live
- **Data Exploration**: Quick visual inspection of campaign data
- **Public Data Sharing**: Simple hosting on CDN or static site
- **Mobile Applications**: Offline-capable map tiles
- **Prototyping**: Rapid visualization without infrastructure setup

### ❌ Not Recommended For

- **Analysis workflows**: Use GeoParquet directly (better for spatial queries)
- **Desktop GIS**: Use GeoParquet with QGIS/ArcGIS (native support)
- **Large-scale processing**: GeoParquet is more efficient
- **Real-time updates**: PMTiles are immutable (regenerate for updates)

## How It Works

```
GeoParquet Data → Track Segmentation → Vector Tiles → PMTiles File
   (millions of points)  (time-based split)   (zoom levels)    (single file)
```

### Pipeline Stages

1. **Read GeoParquet**: Load partitioned oceanographic data
2. **Apply Sampling**: Take every Nth point (default: every 5th) to reduce density
3. **Detect Time Gaps**: Split tracks on gaps > 60 minutes (configurable)
4. **Create Segments**: Build LineString geometries with averaged measurements
5. **Add Day Markers**: Generate start/end Point features per UTC day
6. **Build Tiles**: Use Tippecanoe to create optimized vector tiles (zoom 0-10)
7. **Convert to PMTiles**: Package MBTiles into single HTTP-range-friendly file

## Output

### File Structure

```
output/
├── campaign_id/                # GeoParquet data
│   ├── lat_bin=X/lon_bin=Y/*.parquet
│   └── stac/
│       └── collection.json     # PMTiles linked here
└── tiles/
    └── track.pmtiles           # Single vector tiles file
```

### Typical File Sizes

| Data Volume | Duration | Sample Rate | PMTiles Size |
|-------------|----------|-------------|--------------|
| 100k points | 1 week | 5 | ~2 MB |
| 500k points | 1 month | 5 | ~5-8 MB |
| 2M points | 3 months | 5 | ~15-25 MB |
| 10M points | 1 year | 10 | ~40-60 MB |

## Quick Example

### Generate PMTiles

```bash
oceanstream process geotrack \
  --input-source ./raw_data \
  --output-dir ./output \
  --campaign-id sd1030_2023 \
  --generate-pmtiles \
  --yes
```

### View in Browser

```html
<script src="https://unpkg.com/maplibre-gl/dist/maplibre-gl.js"></script>
<script src="https://unpkg.com/pmtiles/dist/pmtiles.js"></script>

<script>
  const protocol = new pmtiles.Protocol();
  maplibregl.addProtocol('pmtiles', protocol.tile);
  
  const map = new maplibregl.Map({
    container: 'map',
    style: 'https://demotiles.maplibre.org/style.json',
    center: [-170, -43],
    zoom: 5
  });
  
  map.on('load', () => {
    map.addSource('oceanstream', {
      type: 'vector',
      url: 'pmtiles://https://example.com/tiles/track.pmtiles'
    });
    
    map.addLayer({
      id: 'track-lines',
      type: 'line',
      source: 'oceanstream',
      'source-layer': 'track',
      paint: {
        'line-color': '#ff6600',
        'line-width': 2
      }
    });
  });
</script>
```

## Next Steps

- [Quick Start Guide](quickstart.md) - Get started in 5 minutes
- [Configuration](configuration.md) - Customize zoom levels, sampling, and measurements
- [Web Integration](web-integration.md) - Complete MapLibre examples
- [Hosting](hosting.md) - Deploy to Azure, S3, or static hosting

## See Also

- [STAC Metadata Guide](../../features/stac-metadata.md) - PMTiles automatically linked in STAC
- [Geotrack Convert Reference](../../core-concepts/geotrack-convert-reference.md) - All CLI options
- [PMTiles Specification](https://github.com/protomaps/PMTiles) - Format details
