# Web Integration with MapLibre GL JS

Complete guide to integrating PMTiles with web maps using MapLibre GL JS.

## Complete Working Example

Full HTML page with temperature-styled track and interactive popups:

```html
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>OceanStream Track Viewer</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  
  <!-- MapLibre GL JS -->
  <script src="https://unpkg.com/maplibre-gl@3.6.2/dist/maplibre-gl.js"></script>
  <link href="https://unpkg.com/maplibre-gl@3.6.2/dist/maplibre-gl.css" rel="stylesheet" />
  
  <!-- PMTiles Protocol -->
  <script src="https://unpkg.com/pmtiles@3.0.4/dist/pmtiles.js"></script>
  
  <style>
    body { margin: 0; padding: 0; }
    #map { position: absolute; top: 0; bottom: 0; width: 100%; }
    
    .maplibregl-popup-content {
      padding: 15px;
      max-width: 300px;
    }
    .popup-row {
      display: flex;
      justify-content: space-between;
      margin: 5px 0;
    }
    .popup-label {
      font-weight: bold;
      margin-right: 10px;
    }
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
    
    // Add navigation controls
    map.addControl(new maplibregl.NavigationControl(), 'top-right');
    
    map.on('load', () => {
      // Add PMTiles source
      map.addSource('oceanstream', {
        type: 'vector',
        url: 'pmtiles://https://your-domain.com/tiles/track.pmtiles'
      });
      
      // Track lines styled by temperature
      map.addLayer({
        id: 'track-lines',
        type: 'line',
        source: 'oceanstream',
        'source-layer': 'track',
        paint: {
          'line-color': [
            'interpolate',
            ['linear'],
            ['get', 'TEMP_AIR_MEAN'],
            -5, '#0000ff',  // Cold: blue
            10, '#00ffff',  // Cool: cyan
            20, '#00ff00',  // Warm: green
            25, '#ffff00',  // Warmer: yellow
            30, '#ff0000'   // Hot: red
          ],
          'line-width': 2,
          'line-opacity': 0.8
        }
      });
      
      // Day markers (circles at segment starts)
      map.addLayer({
        id: 'day-markers',
        type: 'circle',
        source: 'oceanstream',
        'source-layer': 'track',
        filter: ['==', ['get', 'day_marker'], true],
        paint: {
          'circle-radius': 6,
          'circle-color': '#ffffff',
          'circle-stroke-width': 2,
          'circle-stroke-color': '#ff6600'
        }
      });
      
      // Interactive popup on click
      map.on('click', 'track-lines', (e) => {
        const props = e.features[0].properties;
        
        const html = `
          <h3>Track Data</h3>
          <div class="popup-row">
            <span class="popup-label">Time:</span>
            <span>${new Date(props.time).toLocaleString()}</span>
          </div>
          <div class="popup-row">
            <span class="popup-label">Location:</span>
            <span>${props.latitude.toFixed(4)}°, ${props.longitude.toFixed(4)}°</span>
          </div>
          <hr>
          <div class="popup-row">
            <span class="popup-label">Air Temp:</span>
            <span>${props.TEMP_AIR_MEAN?.toFixed(2) || 'N/A'} °C</span>
          </div>
          <div class="popup-row">
            <span class="popup-label">Wind Speed:</span>
            <span>${props.WIND_SPEED_MEAN?.toFixed(2) || 'N/A'} m/s</span>
          </div>
          <div class="popup-row">
            <span class="popup-label">Wave Height:</span>
            <span>${props.WAVE_SIGNIFICANT_HEIGHT?.toFixed(2) || 'N/A'} m</span>
          </div>
          ${props.segment_id !== undefined ? `
          <hr>
          <div class="popup-row">
            <span class="popup-label">Segment:</span>
            <span>#${props.segment_id} (${props.duration_hours?.toFixed(1)} hrs)</span>
          </div>
          ` : ''}
        `;
        
        new maplibregl.Popup()
          .setLngLat(e.lngLat)
          .setHTML(html)
          .addTo(map);
      });
      
      // Cursor changes on hover
      map.on('mouseenter', 'track-lines', () => {
        map.getCanvas().style.cursor = 'pointer';
      });
      
      map.on('mouseleave', 'track-lines', () => {
        map.getCanvas().style.cursor = '';
      });
    });
  </script>
</body>
</html>
```

## Basic Setup

### 1. Include Dependencies

```html
<!-- MapLibre GL JS -->
<script src="https://unpkg.com/maplibre-gl@3.6.2/dist/maplibre-gl.js"></script>
<link href="https://unpkg.com/maplibre-gl@3.6.2/dist/maplibre-gl.css" rel="stylesheet" />

<!-- PMTiles Protocol -->
<script src="https://unpkg.com/pmtiles@3.0.4/dist/pmtiles.js"></script>
```

### 2. Register PMTiles Protocol

```javascript
const protocol = new pmtiles.Protocol();
maplibregl.addProtocol('pmtiles', protocol.tile);
```

### 3. Create Map

```javascript
const map = new maplibregl.Map({
  container: 'map',
  style: 'https://demotiles.maplibre.org/style.json',
  center: [-170, -43],  // Adjust to your data
  zoom: 5
});
```

### 4. Add PMTiles Source

```javascript
map.on('load', () => {
  map.addSource('oceanstream', {
    type: 'vector',
    url: 'pmtiles://https://your-domain.com/tiles/track.pmtiles'
  });
});
```

## Styling Track Lines

### Simple Solid Color

```javascript
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
```

### Temperature-Based Colors

```javascript
paint: {
  'line-color': [
    'interpolate',
    ['linear'],
    ['get', 'TEMP_AIR_MEAN'],
    0, '#0000ff',   // 0°C: blue
    10, '#00ffff',  // 10°C: cyan
    20, '#00ff00',  // 20°C: green
    30, '#ff0000'   // 30°C: red
  ],
  'line-width': 2
}
```

### Wind Speed-Based Width

```javascript
paint: {
  'line-color': '#0066cc',
  'line-width': [
    'interpolate',
    ['linear'],
    ['get', 'WIND_SPEED_MEAN'],
    0, 1,    // 0 m/s: thin line
    5, 2,    // 5 m/s: medium
    10, 4,   // 10 m/s: thick
    20, 8    // 20 m/s: very thick
  ]
}
```

### Salinity Gradient

```javascript
paint: {
  'line-color': [
    'interpolate',
    ['linear'],
    ['get', 'SAL_SBE37_MEAN'],
    30, '#ff0000',  // Low salinity: red
    33, '#ffff00',  // Medium: yellow
    35, '#00ff00',  // Normal: green
    37, '#0000ff'   // High: blue
  ],
  'line-width': 2
}
```

## Day Markers and Segments

### Segment Start Markers

```javascript
map.addLayer({
  id: 'day-markers',
  type: 'circle',
  source: 'oceanstream',
  'source-layer': 'track',
  filter: ['==', ['get', 'day_marker'], true],
  paint: {
    'circle-radius': 6,
    'circle-color': '#ffffff',
    'circle-stroke-width': 2,
    'circle-stroke-color': '#ff6600'
  }
});
```

### Segment-Based Styling

Style different segments with different colors:

```javascript
map.addLayer({
  id: 'track-segments',
  type: 'line',
  source: 'oceanstream',
  'source-layer': 'track',
  paint: {
    'line-color': [
      'match',
      ['get', 'segment_id'],
      0, '#ff0000',   // Segment 0: red
      1, '#00ff00',   // Segment 1: green
      2, '#0000ff',   // Segment 2: blue
      3, '#ffff00',   // Segment 3: yellow
      '#999999'       // Default: gray
    ],
    'line-width': 2
  }
});
```

## Interactive Features

### Click Popup

```javascript
map.on('click', 'track-lines', (e) => {
  const props = e.features[0].properties;
  
  const html = `
    <strong>Location:</strong> ${props.latitude.toFixed(4)}°, ${props.longitude.toFixed(4)}°<br>
    <strong>Time:</strong> ${new Date(props.time).toLocaleString()}<br>
    <strong>Temperature:</strong> ${props.TEMP_AIR_MEAN?.toFixed(2) || 'N/A'} °C<br>
    <strong>Wind Speed:</strong> ${props.WIND_SPEED_MEAN?.toFixed(2) || 'N/A'} m/s
  `;
  
  new maplibregl.Popup()
    .setLngLat(e.lngLat)
    .setHTML(html)
    .addTo(map);
});
```

### Hover Tooltip

```javascript
let popup = null;

map.on('mousemove', 'track-lines', (e) => {
  const props = e.features[0].properties;
  
  // Remove existing popup
  if (popup) popup.remove();
  
  // Create new popup
  popup = new maplibregl.Popup({
    closeButton: false,
    closeOnClick: false
  })
    .setLngLat(e.lngLat)
    .setHTML(`Temp: ${props.TEMP_AIR_MEAN?.toFixed(1)}°C`)
    .addTo(map);
});

map.on('mouseleave', 'track-lines', () => {
  if (popup) {
    popup.remove();
    popup = null;
  }
});
```

### Cursor Change

```javascript
map.on('mouseenter', 'track-lines', () => {
  map.getCanvas().style.cursor = 'pointer';
});

map.on('mouseleave', 'track-lines', () => {
  map.getCanvas().style.cursor = '';
});
```

## Multiple Campaigns

Display multiple PMTiles files:

```javascript
// Campaign 1
map.addSource('campaign1', {
  type: 'vector',
  url: 'pmtiles://https://your-domain.com/tiles/sd1030.pmtiles'
});

map.addLayer({
  id: 'campaign1-track',
  type: 'line',
  source: 'campaign1',
  'source-layer': 'track',
  paint: {
    'line-color': '#ff0000',
    'line-width': 2
  }
});

// Campaign 2
map.addSource('campaign2', {
  type: 'vector',
  url: 'pmtiles://https://your-domain.com/tiles/sd1033.pmtiles'
});

map.addLayer({
  id: 'campaign2-track',
  type: 'line',
  source: 'campaign2',
  'source-layer': 'track',
  paint: {
    'line-color': '#0000ff',
    'line-width': 2
  }
});
```

## Layer Visibility Toggle

Add buttons to show/hide layers:

```html
<div id="controls">
  <button id="toggle-track">Toggle Track</button>
  <button id="toggle-markers">Toggle Markers</button>
</div>

<script>
  document.getElementById('toggle-track').addEventListener('click', () => {
    const visibility = map.getLayoutProperty('track-lines', 'visibility');
    if (visibility === 'visible') {
      map.setLayoutProperty('track-lines', 'visibility', 'none');
    } else {
      map.setLayoutProperty('track-lines', 'visibility', 'visible');
    }
  });
  
  document.getElementById('toggle-markers').addEventListener('click', () => {
    const visibility = map.getLayoutProperty('day-markers', 'visibility');
    if (visibility === 'visible') {
      map.setLayoutProperty('day-markers', 'visibility', 'none');
    } else {
      map.setLayoutProperty('day-markers', 'visibility', 'visible');
    }
  });
</script>
```

## Legend Component

Create a custom legend:

```html
<div id="legend">
  <h4>Temperature (°C)</h4>
  <div><span style="background: #0000ff;"></span> < 10</div>
  <div><span style="background: #00ffff;"></span> 10-20</div>
  <div><span style="background: #00ff00;"></span> 20-25</div>
  <div><span style="background: #ffff00;"></span> 25-30</div>
  <div><span style="background: #ff0000;"></span> > 30</div>
</div>

<style>
  #legend {
    position: absolute;
    top: 10px;
    right: 10px;
    background: white;
    padding: 10px;
    border-radius: 5px;
    box-shadow: 0 2px 4px rgba(0,0,0,0.3);
  }
  #legend h4 {
    margin: 0 0 10px 0;
  }
  #legend div {
    display: flex;
    align-items: center;
    margin: 5px 0;
  }
  #legend span {
    display: inline-block;
    width: 20px;
    height: 10px;
    margin-right: 10px;
  }
</style>
```

## Base Map Styles

### Light Style
```javascript
style: 'https://demotiles.maplibre.org/style.json'
```

### Dark Style
```javascript
style: {
  version: 8,
  sources: {
    'carto-dark': {
      type: 'raster',
      tiles: ['https://a.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}.png'],
      tileSize: 256
    }
  },
  layers: [
    {
      id: 'carto-dark-layer',
      type: 'raster',
      source: 'carto-dark'
    }
  ]
}
```

### Satellite Style
```javascript
style: {
  version: 8,
  sources: {
    'satellite': {
      type: 'raster',
      tiles: ['https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}'],
      tileSize: 256
    }
  },
  layers: [
    {
      id: 'satellite-layer',
      type: 'raster',
      source: 'satellite'
    }
  ]
}
```

## Performance Optimization

### Lazy Loading

Only load PMTiles when user zooms in:

```javascript
map.on('zoom', () => {
  const zoom = map.getZoom();
  
  if (zoom > 4 && !map.getSource('oceanstream')) {
    map.addSource('oceanstream', {
      type: 'vector',
      url: 'pmtiles://https://your-domain.com/tiles/track.pmtiles'
    });
    
    // Add layers...
  }
});
```

### Clustering (for many campaigns)

If displaying hundreds of PMTiles, use clustering:

```javascript
map.addSource('campaigns', {
  type: 'geojson',
  data: {
    type: 'FeatureCollection',
    features: campaigns.map(c => ({
      type: 'Feature',
      geometry: { type: 'Point', coordinates: c.center },
      properties: { id: c.id, name: c.name }
    }))
  },
  cluster: true,
  clusterMaxZoom: 8,
  clusterRadius: 50
});

// Cluster circles
map.addLayer({
  id: 'clusters',
  type: 'circle',
  source: 'campaigns',
  filter: ['has', 'point_count'],
  paint: {
    'circle-color': '#0066cc',
    'circle-radius': 20
  }
});

// Cluster count labels
map.addLayer({
  id: 'cluster-count',
  type: 'symbol',
  source: 'campaigns',
  filter: ['has', 'point_count'],
  layout: {
    'text-field': '{point_count_abbreviated}',
    'text-size': 12
  }
});
```

## Browser Compatibility

PMTiles works in all modern browsers:

- ✅ Chrome 90+
- ✅ Firefox 88+
- ✅ Safari 14+
- ✅ Edge 90+

**Requirements**:
- ES6 module support
- Fetch API with range requests
- WebGL (for MapLibre)

## Troubleshooting

### Map Not Appearing

Check container has height:
```css
#map {
  height: 100vh;  /* or specific pixel value */
}
```

### PMTiles Not Loading

Verify URL uses `pmtiles://` protocol:
```javascript
url: 'pmtiles://https://your-domain.com/tiles/track.pmtiles'
//     ^^^^^^^^^^^^ Required prefix
```

### Wrong Layer Name

Ensure `source-layer` matches PMTiles layer:
```javascript
'source-layer': 'track'  // Default in OceanStream
```

Check actual layer name:
```bash
pmtiles show track.pmtiles
# Look for "vector_layers" -> "id"
```

### Missing Measurements

Not all measurements exist in all datasets. Use optional chaining:
```javascript
${props.TEMP_AIR_MEAN?.toFixed(2) || 'N/A'}
//                     ^^^ Prevents errors if undefined
```

## Next Steps

- [Hosting Guide](hosting.md) - Deploy PMTiles to production
- [Configuration](configuration.md) - Customize generation settings
- [Troubleshooting](troubleshooting.md) - Common issues and solutions
- [MapLibre Documentation](https://maplibre.org/maplibre-gl-js/docs/) - Full API reference
