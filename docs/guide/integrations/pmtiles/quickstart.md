# PMTiles Quick Start

Get started with PMTiles generation in 5 minutes.

## Prerequisites

Install required CLI tools:

```bash
# macOS
brew install tippecanoe
brew install pmtiles

# Ubuntu/Debian
sudo apt-get install -y build-essential libsqlite3-dev zlib1g-dev
git clone https://github.com/felt/tippecanoe.git
cd tippecanoe && make -j && sudo make install

# PMTiles (download binary for your platform)
# https://github.com/protomaps/go-pmtiles/releases
```

Verify installation:
```bash
tippecanoe --version
pmtiles --version
```

## Basic Usage

### CLI

Generate GeoParquet and PMTiles in one command:

```bash
oceanstream process geotrack \
  --input-source ./raw_data \
  --output-dir ./output \
  --campaign-id sd1030_2023 \
  --generate-pmtiles \
  --yes
```

### Python

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

## Output

```
output/
├── sd1030_2023/                    # GeoParquet data
│   ├── lat_bin=-43/lon_bin=-170/
│   │   └── part-0.parquet
│   └── stac/
│       ├── collection.json
│       └── items/
└── tiles/
    └── track.pmtiles                # Vector tiles (~2-10 MB)
```

## View in Browser

Create a simple HTML file:

```html
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>OceanStream Track Viewer</title>
  <script src="https://unpkg.com/maplibre-gl@3.6.2/dist/maplibre-gl.js"></script>
  <link href="https://unpkg.com/maplibre-gl@3.6.2/dist/maplibre-gl.css" rel="stylesheet" />
  <script src="https://unpkg.com/pmtiles@3.0.4/dist/pmtiles.js"></script>
  <style>
    body { margin: 0; padding: 0; }
    #map { position: absolute; top: 0; bottom: 0; width: 100%; }
  </style>
</head>
<body>
  <div id="map"></div>
  
  <script>
    // Register PMTiles protocol
    const protocol = new pmtiles.Protocol();
    maplibregl.addProtocol('pmtiles', protocol.tile);
    
    // Create map
    const map = new maplibregl.Map({
      container: 'map',
      style: 'https://demotiles.maplibre.org/style.json',
      center: [-170, -43],
      zoom: 5
    });
    
    // Add track layer
    map.on('load', () => {
      map.addSource('oceanstream', {
        type: 'vector',
        url: 'pmtiles://http://localhost:8000/track.pmtiles'
      });
      
      map.addLayer({
        id: 'track-lines',
        type: 'line',
        source: 'oceanstream',
        'source-layer': 'track',
        paint: {
          'line-color': '#ff6600',
          'line-width': 2,
          'line-opacity': 0.8
        }
      });
    });
  </script>
</body>
</html>
```

## Local Testing

Start a local server to test your PMTiles:

```bash
# Navigate to tiles directory
cd output/tiles

# Start Python HTTP server
python -m http.server 8000

# Open http://localhost:8000 in your browser
```

## Custom Settings

### Adjust Zoom Levels

```bash
oceanstream process geotrack \
  --input-source ./data \
  --output-dir ./output \
  --campaign-id cruise_2024 \
  --generate-pmtiles \
  --pmtiles-minzoom 2 \
  --pmtiles-maxzoom 12 \
  --yes
```

### Change Sampling Rate

Lower sampling = more points = larger file:

```bash
# Every 3rd point (more detail)
--pmtiles-sample-rate 3

# Every 10th point (less detail, smaller file)
--pmtiles-sample-rate 10
```

### Custom Layer Name

```bash
--pmtiles-layer "vessel_track"
```

Make sure to update your JavaScript:
```javascript
'source-layer': 'vessel_track'  // Must match --pmtiles-layer
```

## Troubleshooting

### "tippecanoe not found"

Install tippecanoe:
- macOS: `brew install tippecanoe`
- Linux: Build from source (see Prerequisites)
- Or use ogr2ogr fallback (less features)

### "pmtiles not found"

Download PMTiles CLI:
- https://github.com/protomaps/go-pmtiles/releases
- macOS: `brew install pmtiles`

### Map Shows No Data

Check layer name matches:
```javascript
'source-layer': 'track'  // Default layer name
```

Verify PMTiles URL uses `pmtiles://` protocol:
```javascript
url: 'pmtiles://http://localhost:8000/track.pmtiles'
```

### CORS Error (Remote Files)

If hosting PMTiles on a different domain, enable CORS:
```
Access-Control-Allow-Origin: *
Access-Control-Allow-Methods: GET, HEAD
```

## Next Steps

- [Configuration Guide](configuration.md) - Customize all settings
- [Web Integration](web-integration.md) - Advanced MapLibre examples
- [Hosting Guide](hosting.md) - Deploy to production
- [Troubleshooting](troubleshooting.md) - Common issues and solutions
