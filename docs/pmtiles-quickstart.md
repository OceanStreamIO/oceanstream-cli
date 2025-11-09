# Quick Start: PMTiles Generation

## Prerequisites

Install required CLI tools:

```bash
# macOS
brew install gdal
brew install pmtiles

# Ubuntu/Debian
sudo apt-get install gdal-bin
# For pmtiles, download from: https://github.com/protomaps/go-pmtiles/releases
```

## Basic Usage

### CLI

```bash
# GeoParquet + PMTiles in one command
oceanstream process geotrack \
  --input-dir ./raw_data \
  --output-dir ./out/geoparquet \
  --generate-pmtiles \
  --yes -v
```

### Python Library

```python
from oceanstream.geotrack import process
from oceanstream.providers import get_provider
from pathlib import Path

provider = get_provider("saildrone")

process(
    provider=provider,
    input_dir=Path("./raw_data"),
    output_dir=Path("./out/geoparquet"),
    generate_pmtiles=True,
    yes=True,
    verbose=True
)
```

## Output

```
out/geoparquet/
├── lon_grid=-180/...
├── metadata.parquet
└── track.pmtiles  ← Your PMTiles file!
```

## Using PMTiles in MapLibre

```html
<!DOCTYPE html>
<html>
<head>
  <script src="https://unpkg.com/maplibre-gl/dist/maplibre-gl.js"></script>
  <script src="https://unpkg.com/pmtiles/dist/pmtiles.js"></script>
  <link href="https://unpkg.com/maplibre-gl/dist/maplibre-gl.css" rel="stylesheet" />
</head>
<body>
  <div id="map" style="width: 100%; height: 500px;"></div>
  
  <script>
    // Setup PMTiles protocol
    const protocol = new pmtiles.Protocol();
    maplibregl.addProtocol("pmtiles", protocol.tile);
    
    // Create map
    const map = new maplibregl.Map({
      container: 'map',
      style: 'https://demotiles.maplibre.org/style.json',
      center: [-170, -43],
      zoom: 5
    });
    
    map.on('load', () => {
      // Add your PMTiles source
      map.addSource('oceanstream', {
        type: 'vector',
        url: 'pmtiles://https://your-server.com/path/to/track.pmtiles'
      });
      
      // Add track layer
      map.addLayer({
        id: 'track-points',
        type: 'circle',
        source: 'oceanstream',
        'source-layer': 'oceanstream_track',
        paint: {
          'circle-radius': 3,
          'circle-color': '#ff6600',
          'circle-opacity': 0.7
        }
      });
    });
  </script>
</body>
</html>
```

## Advanced Options

```bash
# Customize zoom levels and layer name
oceanstream process geotrack \
  --input-dir ./raw_data \
  --output-dir ./out/geoparquet \
  --generate-pmtiles \
  --pmtiles-minzoom 0 \
  --pmtiles-maxzoom 12 \
  --pmtiles-layer my_custom_layer \
  --yes -v
```

## Troubleshooting

### "ogr2ogr not found"

Install GDAL:
- macOS: `brew install gdal`
- Ubuntu: `sudo apt-get install gdal-bin`
- Windows: Download from https://gdal.org/download.html

### "pmtiles not found"

Install pmtiles CLI:
- Download: https://github.com/protomaps/go-pmtiles/releases
- Or with npm: `npm install -g pmtiles`

### PMTiles file is too large

Try reducing maxzoom:
```bash
--pmtiles-maxzoom 8  # Instead of default 10
```

Or process data in smaller time/spatial chunks.

## Next Steps

- See `docs/pmtiles-implementation.md` for full implementation details
- See `docs/pmtiles.md` for comprehensive PMTiles documentation
- See `notebooks/geotrack_processing_demo.ipynb` for library API examples
