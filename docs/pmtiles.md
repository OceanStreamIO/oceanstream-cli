# PMTiles generation and web map integration

This guide describes how to generate a PMTiles file from the partitioned GeoParquet dataset and publish it to Azure Blob Storage for use in a MapLibre-based web map.

## Why PMTiles
- Single-file, HTTP range-request friendly vector tiles.
- Easy CDN caching and static hosting.
- Compatible with MapLibre via the `pmtiles` JS plugin.

## Inputs and outputs
- Input: the partitioned GeoParquet dataset root (e.g., `data/geoparquet/`), which contains a `geometry` POINT (EPSG:4326).
- Output: a single `*.pmtiles` file, stored alongside the GeoParquet in Azure Blob Storage.

## Tooling options
Pick one path based on your environment:

1) GDAL + PMTiles CLI (recommended, no intermediate GeoJSON):
- Read GeoParquet via GDAL Parquet driver.
- Write vector-tile MBTiles with GDAL.
- Convert MBTiles to PMTiles with the `pmtiles` CLI.

2) Tippecanoe + PMTiles CLI:
- Convert to newline-delimited GeoJSON (or let GDAL stream to Tippecanoe).
- Generate MBTiles with Tippecanoe.
- Convert to PMTiles with the `pmtiles` CLI.

The repo includes a light Python wrapper around option (1): `oceanstream/geotrack/tiling/pmtiles.py`.

## Suggested tiling parameters
- Zooms: `minzoom=0`, `maxzoom=10` (adjust based on density/perf).
- Layer name: `saildrone_points` (or `saildrone_bins` when using aggregated bins).
- Attributes: keep only essential fields to shrink tiles, e.g. `platform_id`, `time` (optional), and a few key measurements. For dense data, prefer aggregated layers below z8.

## Example: Python wrapper usage
```python
from oceanstream.geotrack.tiling import generate_pmtiles_from_geoparquet

pmtiles_path = "data/tiles/saildrone_2023.pmtiles"
geoparquet_root = "data/geoparquet"  # root folder written by the pipeline

generate_pmtiles_from_geoparquet(
    geoparquet_root,
    pmtiles_path,
    minzoom=0,
    maxzoom=10,
    layer_name="saildrone_points",
    select_columns=["platform_id", "time", "latitude", "longitude"],
)
```
Requirements: `ogr2ogr` (GDAL ≥ 3.5 with Parquet and MBTiles/MVT support) and `pmtiles` CLI available on PATH.

## Azure upload
Use existing uploader:
```python
from src.storage.azure_blob import upload_to_azure_blob

upload_to_azure_blob(
    file_path=pmtiles_path,
    container_name="<your-container>",
    blob_name="tiles/saildrone_2023.pmtiles",
)
```

## MapLibre integration
Use the pmtiles plugin to add a source referencing the PMTiles:
```html
<script src="https://unpkg.com/pmtiles@latest/dist/pmtiles.js"></script>
<script>
  const protocol = new pmtiles.Protocol();
  maplibregl.addProtocol("pmtiles", protocol.tile);
</script>
```
```js
const map = new maplibregl.Map({...});

// PMTiles URL (public blob URL or CDN), e.g. https://<account>.blob.core.windows.net/<container>/tiles/oceanstream_2023.pmtiles
const pmtilesURL = "https://.../tiles/oceanstream_2023.pmtiles";

map.on("load", () => {
  map.addSource("oceanstream", {
    type: "vector",
    url: `pmtiles://${pmtilesURL}`,
  });

  map.addLayer({
  id: "oceanstream-points",
    type: "circle",
  source: "oceanstream",
  "source-layer": "oceanstream_points",
    paint: {
      "circle-radius": 3,
      "circle-color": "#ff6600",
      "circle-opacity": 0.7
    }
  });
});
```

## Tips for large volumes
- Consider an aggregated layer for z0–z7 based on `lat_bin`/`lon_bin` with summary stats (count, mean temp, etc.).
- Use attribute whitelists and geometry simplification at low zooms.
- Split by time ranges (e.g., per-year PMTiles) to keep single-file sizes manageable.
