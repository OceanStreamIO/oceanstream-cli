# PMTiles Troubleshooting

Common issues and solutions when working with PMTiles.

## Generation Issues

### "tippecanoe not found"

**Problem**: PMTiles generation fails with `tippecanoe: command not found`.

**Solution**:

Install Tippecanoe:

**macOS**:
```bash
brew install tippecanoe
```

**Ubuntu/Debian**:
```bash
sudo apt-get install -y build-essential libsqlite3-dev zlib1g-dev
git clone https://github.com/felt/tippecanoe.git
cd tippecanoe && make -j && sudo make install
```

**Verify installation**:
```bash
tippecanoe --version
# Should output: tippecanoe v2.x.x
```

**Fallback**: Use ogr2ogr (less features):
```bash
# Check if GDAL/ogr2ogr is available
ogr2ogr --version

# OceanStream will automatically use ogr2ogr if tippecanoe is missing
```

### "pmtiles not found"

**Problem**: PMTiles generation fails with `pmtiles: command not found`.

**Solution**:

Install PMTiles CLI:

**macOS**:
```bash
brew install pmtiles
```

**Linux/Manual**:
```bash
# Download binary from GitHub releases
wget https://github.com/protomaps/go-pmtiles/releases/download/v1.11.1/pmtiles_1.11.1_Linux_x86_64.tar.gz
tar -xzf pmtiles_1.11.1_Linux_x86_64.tar.gz
sudo mv pmtiles /usr/local/bin/
chmod +x /usr/local/bin/pmtiles
```

**Verify installation**:
```bash
pmtiles --version
# Should output: pmtiles version 1.x.x
```

### "ogr2ogr: Unable to open datasource"

**Problem**: Fallback to ogr2ogr fails with "Unable to open datasource".

**Solution**:

Ensure GDAL has Parquet support:

```bash
# Check GDAL drivers
ogrinfo --formats | grep -i parquet

# Should see:
# "Parquet" (read/write)
```

**If Parquet driver missing**:

**macOS**:
```bash
brew reinstall gdal --with-parquet
```

**Ubuntu/Debian**:
```bash
sudo apt-get install gdal-bin libgdal-dev python3-gdal
```

### PMTiles file is too large (>50 MB)

**Problem**: Generated PMTiles file is larger than expected, causing slow load times.

**Causes**:
- Low sampling rate (sample_rate=1 or 2)
- High max zoom (maxzoom=14 or 15)
- Large dataset (millions of points)

**Solutions**:

**1. Increase sampling rate**:
```bash
# Original (large file)
--pmtiles-sample-rate 1  # Every point

# Optimized (smaller file)
--pmtiles-sample-rate 10  # Every 10th point
```

**2. Reduce max zoom**:
```bash
# Original (large file)
--pmtiles-maxzoom 14

# Optimized (smaller file)
--pmtiles-maxzoom 10
```

**3. Increase min zoom** (skip world view):
```bash
# Original (large file)
--pmtiles-minzoom 0

# Optimized (smaller file)
--pmtiles-minzoom 2
```

**Example optimization**:
```bash
# Before: 30 MB file
oceanstream process geotrack \
  --input-source ./data \
  --output-dir ./output \
  --campaign-id sd1030_2023 \
  --generate-pmtiles \
  --pmtiles-sample-rate 1 \
  --pmtiles-maxzoom 14

# After: 5 MB file
oceanstream process geotrack \
  --input-source ./data \
  --output-dir ./output \
  --campaign-id sd1030_2023 \
  --generate-pmtiles \
  --pmtiles-sample-rate 10 \
  --pmtiles-maxzoom 10 \
  --pmtiles-minzoom 2
```

### No segments or day markers appear

**Problem**: Track displays but segments are not separated.

**Causes**:
- Using ogr2ogr fallback (no segmentation support)
- Continuous data with no time gaps

**Solutions**:

**1. Install tippecanoe** (required for segmentation):
```bash
brew install tippecanoe pmtiles
```

**2. Check time gaps in data**:
```python
import pandas as pd
import geopandas as gpd

gdf = gpd.read_parquet("./output/sd1030_2023/")
gdf = gdf.sort_values("time")
time_diffs = gdf["time"].diff()

print(f"Max time gap: {time_diffs.max()}")
# Should be > 1 hour for segmentation to occur
```

**3. Verify segmentation in PMTiles**:
```bash
pmtiles show track.pmtiles

# Look for properties like:
# "segment_id", "day_marker", "start_time", "end_time"
```

## Display Issues

### Map shows no data

**Problem**: Map loads but PMTiles track doesn't appear.

**Solutions**:

**1. Check layer name matches**:
```javascript
// PMTiles default layer name is "track"
map.addLayer({
  id: 'track-lines',
  type: 'line',
  source: 'oceanstream',
  'source-layer': 'track',  // ← Must match PMTiles layer
  paint: { 'line-color': '#ff6600', 'line-width': 2 }
});
```

**Verify layer name**:
```bash
pmtiles show track.pmtiles
# Look for "vector_layers" → "id"
```

**2. Check PMTiles URL**:
```javascript
// ✅ CORRECT: Uses pmtiles:// protocol
url: 'pmtiles://https://your-domain.com/tiles/track.pmtiles'

// ❌ WRONG: Missing protocol
url: 'https://your-domain.com/tiles/track.pmtiles'
```

**3. Verify map zoom/center**:
```javascript
// Check if map is zoomed to data extent
map.on('load', () => {
  console.log('Map center:', map.getCenter());
  console.log('Map zoom:', map.getZoom());
  
  // Adjust to your data's location
  map.setCenter([-170, -43]);  // Example: South Pacific
  map.setZoom(5);
});
```

**4. Check browser console for errors**:
```javascript
// Open browser DevTools (F12) and check Console tab
// Look for errors like:
// - "Failed to load resource"
// - "CORS error"
// - "Layer 'track' not found"
```

### CORS error in browser

**Problem**: Browser console shows CORS error:
```
Access to fetch at '...' from origin '...' has been blocked by CORS policy
```

**Solutions**:

**1. Verify CORS headers on server**:
```bash
# Test with curl
curl -I https://your-domain.com/tiles/track.pmtiles

# Should include:
# Access-Control-Allow-Origin: *
# Access-Control-Allow-Methods: GET, HEAD
```

**2. Configure CORS on Azure Blob Storage**:
```bash
az storage cors add \
  --account-name <storage-account> \
  --services b \
  --methods GET HEAD \
  --origins "*" \
  --allowed-headers "*" \
  --exposed-headers "*" \
  --max-age 3600
```

**3. Configure CORS on S3**:
```json
{
  "CORSRules": [
    {
      "AllowedOrigins": ["*"],
      "AllowedMethods": ["GET", "HEAD"],
      "AllowedHeaders": ["*"],
      "ExposeHeaders": ["ETag"],
      "MaxAgeSeconds": 3600
    }
  ]
}
```

Apply:
```bash
aws s3api put-bucket-cors \
  --bucket oceanstream-tiles \
  --cors-configuration file://cors.json
```

**4. Use local server for development**:
```bash
# Python server with CORS
python -m http.server 8000

# Or Node.js with CORS
npx http-server ./output/tiles -p 8000 --cors
```

### PMTiles loads slowly

**Problem**: PMTiles file takes 5+ seconds to load.

**Causes**:
- Large file size (>20 MB)
- No CDN (direct storage access)
- High latency to origin server

**Solutions**:

**1. Optimize file size** (see "PMTiles file is too large" above).

**2. Enable CDN**:

**Azure CDN**:
```bash
az cdn profile create --name oceanstream-cdn --resource-group <rg> --sku Standard_Microsoft
az cdn endpoint create --name oceanstream-tiles --profile-name oceanstream-cdn --origin <storage-account>.blob.core.windows.net
```

**AWS CloudFront**:
```bash
aws cloudfront create-distribution --origin-domain-name oceanstream-tiles.s3.amazonaws.com
```

**3. Set cache headers**:
```
Cache-Control: public, max-age=31536000, immutable
```

**4. Use HTTP/2** (most hosting providers enable this by default).

**5. Preload critical tiles**:
```javascript
// Preload tiles at initial view
map.on('load', () => {
  // Force MapLibre to load tiles at current view
  map.triggerRepaint();
});
```

### Measurements show as "undefined" or "N/A"

**Problem**: Popup shows `Temperature: N/A` or `undefined`.

**Causes**:
- Measurement not present in dataset
- Incorrect property name
- Missing optional chaining

**Solutions**:

**1. Use optional chaining**:
```javascript
// ✅ CORRECT: Safe access with fallback
const temp = props.TEMP_AIR_MEAN?.toFixed(2) || 'N/A';

// ❌ WRONG: Will throw error if undefined
const temp = props.TEMP_AIR_MEAN.toFixed(2);
```

**2. Check available properties**:
```javascript
map.on('click', 'track-lines', (e) => {
  console.log('Available properties:', e.features[0].properties);
});
```

**3. Verify measurements in PMTiles**:
```bash
pmtiles show track.pmtiles

# Look for "vector_layers" → "fields"
# Should list available measurements
```

**4. Check source data**:
```python
import geopandas as gpd

gdf = gpd.read_parquet("./output/sd1030_2023/")
print(gdf.columns.tolist())

# Verify measurement columns exist
print(gdf["TEMP_AIR_MEAN"].head())
```

## Performance Issues

### High bandwidth usage

**Problem**: Unexpected bandwidth costs from tile requests.

**Causes**:
- No CDN caching
- Missing cache headers
- Frequent tile regeneration

**Solutions**:

**1. Enable CDN** (see "PMTiles loads slowly" above).

**2. Set long cache TTL**:
```
Cache-Control: public, max-age=31536000, immutable
```

**3. Monitor bandwidth**:

**Azure**:
```bash
az monitor metrics list \
  --resource /subscriptions/<sub>/resourceGroups/<rg>/providers/Microsoft.Storage/storageAccounts/<account> \
  --metric Egress \
  --start-time 2024-12-01T00:00:00Z \
  --end-time 2024-12-02T00:00:00Z
```

**AWS**:
```bash
aws cloudwatch get-metric-statistics \
  --namespace AWS/S3 \
  --metric-name BytesDownloaded \
  --dimensions Name=BucketName,Value=oceanstream-tiles \
  --start-time 2024-12-01T00:00:00Z \
  --end-time 2024-12-02T00:00:00Z \
  --period 3600 \
  --statistics Sum
```

**4. Set cost alerts**:
```bash
# Azure
az monitor action-group create --name alert-group --resource-group <rg>
az monitor metrics alert create --name bandwidth-alert --resource-group <rg> --scopes <resource-id> --condition "total Egress > 100000000"

# AWS
aws cloudwatch put-metric-alarm --alarm-name s3-bandwidth-alert --metric-name BytesDownloaded --threshold 100000000
```

### Browser crashes or freezes

**Problem**: Browser becomes unresponsive when loading PMTiles.

**Causes**:
- Very large PMTiles file (>100 MB)
- Too many simultaneous tile requests
- Memory leak in custom code

**Solutions**:

**1. Reduce file size** (see "PMTiles file is too large" above).

**2. Lazy load PMTiles**:
```javascript
// Only load when zoomed in
map.on('zoom', () => {
  if (map.getZoom() > 4 && !map.getSource('oceanstream')) {
    map.addSource('oceanstream', {
      type: 'vector',
      url: 'pmtiles://https://your-domain.com/tiles/track.pmtiles'
    });
    // Add layers...
  }
});
```

**3. Use clustering** for many campaigns (see web-integration guide).

**4. Check for memory leaks**:
```javascript
// Remove old popups
let popup = null;
map.on('click', 'track-lines', (e) => {
  if (popup) popup.remove();  // ← Important: clean up
  popup = new maplibregl.Popup().setLngLat(e.lngLat).setHTML('...').addTo(map);
});
```

## Debugging Tools

### Inspect PMTiles file

```bash
# Show metadata
pmtiles show track.pmtiles

# Output includes:
# - File size
# - Tile count
# - Zoom levels
# - Layer names
# - Available fields
```

### Test HTTP range requests

```bash
# Test range request support
curl -I -H "Range: bytes=0-1023" https://your-domain.com/tiles/track.pmtiles

# Should return:
# HTTP/1.1 206 Partial Content
# Content-Range: bytes 0-1023/12345678
```

### Check STAC metadata

```bash
# Verify PMTiles asset in STAC
cat output/sd1030_2023/stac/collection.json | jq '.assets.pmtiles'

# Should show:
# {
#   "href": "../tiles/track.pmtiles",
#   "type": "application/vnd.pmtiles",
#   "title": "PMTiles vector tiles",
#   "roles": ["tiles"]
# }
```

### Browser DevTools

**Network tab**:
- Check PMTiles file loads (206 status)
- Verify range requests (Request Headers: `Range: bytes=...`)
- Monitor bandwidth usage

**Console tab**:
- Look for JavaScript errors
- Check MapLibre warnings
- Verify source/layer loading

**Performance tab**:
- Profile page load
- Check for memory leaks
- Monitor FPS during map interactions

## Getting Help

Still having issues? Gather this information:

1. **Error messages** (browser console + terminal)
2. **PMTiles metadata**:
   ```bash
   pmtiles show track.pmtiles
   ```
3. **MapLibre configuration**:
   ```javascript
   console.log('Source:', map.getSource('oceanstream'));
   console.log('Layer:', map.getLayer('track-lines'));
   ```
4. **Browser info**: Chrome/Firefox/Safari version
5. **OceanStream version**:
   ```bash
   oceanstream --version
   ```

**Report issues**: [GitHub Issues](https://github.com/OceanStreamIO/oceanstream/issues)

## Next Steps

- [Configuration](configuration.md) - Customize PMTiles generation
- [Web Integration](web-integration.md) - MapLibre examples
- [Hosting](hosting.md) - Deploy to production
- [Overview](overview.md) - PMTiles concepts
