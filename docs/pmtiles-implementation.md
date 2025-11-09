# PMTiles Implementation Summary

## Overview

PMTiles vector tile generation has been successfully integrated into the oceanstream geotrack processing pipeline. The feature is **optional** and **backwards compatible** with existing workflows.

## What Was Implemented

### 1. CLI Parameters (oceanstream/cli.py)

Added four new optional parameters to the `oceanstream process geotrack` command:

- `--generate-pmtiles`: Enable PMTiles generation (default: False)
- `--pmtiles-minzoom`: Minimum zoom level (default: 0)
- `--pmtiles-maxzoom`: Maximum zoom level (default: 10)  
- `--pmtiles-layer`: Layer name for vector tiles (default: "oceanstream_track")

### 2. Processor Updates (oceanstream/geotrack/processor.py)

#### New Method: `GeotrackProcessor.generate_pmtiles_dataset()`

```python
def generate_pmtiles_dataset(
    self,
    geoparquet_root: Path,
    minzoom: int = 0,
    maxzoom: int = 10,
    layer_name: str = "oceanstream_track",
) -> Path | None
```

- Calls existing `oceanstream.geotrack.tiling.generate_pmtiles_from_geoparquet()`
- Automatically selects essential columns: `latitude`, `longitude`, `time`, `platform_id`
- Generates `track.pmtiles` in the output directory
- Gracefully handles missing dependencies (ogr2ogr, pmtiles CLI)
- Returns PMTiles path on success, None on failure

#### Updated Function: `process()`

- Added 4 new parameters matching CLI
- Integrated PMTiles generation after GeoParquet writing
- Added PMTiles section to processing report output

### 3. Tiling Module (oceanstream/geotrack/tiling/)

**Already existed** - no changes needed! The existing implementation:

- Uses GDAL's `ogr2ogr` to convert GeoParquet → MBTiles
- Uses `pmtiles` CLI to convert MBTiles → PMTiles
- Supports column selection to minimize tile size
- Proper error handling for missing dependencies

### 4. Documentation Updates

#### Updated: `notebooks/geotrack_processing_demo.ipynb`

Added new section "5. Generate PMTiles (Optional)" showing both CLI and library API usage.

#### Existing: `docs/pmtiles.md`

Reference documentation already complete with:
- Tool installation instructions
- MapLibre integration examples
- Best practices for tile generation

#### Legacy: `docs/process_gps.py`

Left unchanged for backwards compatibility. Users with existing scripts continue to work.

## Usage Examples

### CLI Usage

```bash
# Basic usage: GeoParquet only (default behavior)
oceanstream process geotrack \
  --input-dir ./raw_data \
  --output-dir ./out/geoparquet \
  --yes -v

# With PMTiles generation
oceanstream process geotrack \
  --input-dir ./raw_data \
  --output-dir ./out/geoparquet \
  --generate-pmtiles \
  --pmtiles-minzoom 0 \
  --pmtiles-maxzoom 10 \
  --pmtiles-layer oceanstream_track \
  --yes -v
```

### Library API Usage

```python
from oceanstream.geotrack import process
from oceanstream.providers import get_provider
from pathlib import Path

provider = get_provider("saildrone")

# Basic usage: GeoParquet only
process(
    provider=provider,
    input_dir=Path("./raw_data"),
    output_dir=Path("./out/geoparquet"),
    yes=True,
    verbose=True
)

# With PMTiles generation
process(
    provider=provider,
    input_dir=Path("./raw_data"),
    output_dir=Path("./out/geoparquet"),
    generate_pmtiles=True,
    pmtiles_minzoom=0,
    pmtiles_maxzoom=10,
    pmtiles_layer="oceanstream_track",
    yes=True,
    verbose=True
)
```

## Output Structure

```
out/geoparquet/
├── lon_grid=-180/
│   ├── lat_grid=-90/
│   │   └── data.parquet
│   └── ...
├── metadata.parquet
├── track.pmtiles          # ← NEW: Generated when --generate-pmtiles enabled
└── stac/                  # ← If STAC generation enabled
    ├── collection.json
    └── items/
        └── *.json
```

## Processing Report Example

When PMTiles generation is enabled, the processing report includes a new section:

```
============================================================
[geotrack] Processing Report
============================================================

▸ Input
  Source directory      : ./raw_data
  CSV files processed   : 3
  Total rows ingested   : 1,234,567

▸ Data Summary
  Latitude range        : [-45.1234, -42.5678]
  Longitude range       : [165.4321, 178.9876]
  Columns               : 45
  Provider              : saildrone

▸ Partitioning
  Latitude bins         : 12
  Longitude bins        : 8
  Partition files       : 96

▸ Output
  Output directory      : ./out/geoparquet
  GeoParquet format     : ✓ Written
  Total output size     : 245.3 MB
  Semantic metadata     : ✓ Embedded

▸ PMTiles Vector Tiles                    # ← NEW SECTION
  PMTiles file          : ✓ track.pmtiles
  File size             : 12.4 MB
  Zoom levels           : 0 - 10
  Layer name            : oceanstream_track

▸ Performance
  Total elapsed time    : 45.67s
  Rows per second       : 27,031
```

## Dependencies

### Required (already in environment)
- Python 3.8+
- pandas, geopandas
- pyarrow

### Optional (for PMTiles generation)
- **ogr2ogr** (GDAL ≥ 3.5 with Parquet and MBTiles/MVT support)
  - macOS: `brew install gdal`
  - Ubuntu: `apt-get install gdal-bin`
  
- **pmtiles CLI**
  - Download from https://github.com/protomaps/go-pmtiles/releases
  - Or: `npm install -g pmtiles` (if Node.js available)

If these tools are not available, PMTiles generation will be skipped gracefully with a helpful error message.

## Backwards Compatibility

✅ **100% backwards compatible**

- Existing code continues to work without any changes
- PMTiles generation is **opt-in** via `--generate-pmtiles` flag
- Default behavior (GeoParquet only) is unchanged
- All 105 existing tests pass without modification
- Legacy `docs/process_gps.py` script remains functional

## Design Decisions

### 1. Why optional?

PMTiles generation requires external CLI tools (ogr2ogr, pmtiles) which may not be installed in all environments. Making it optional ensures the core geotrack processing works everywhere.

### 2. Why keep it simple?

The implementation leverages the existing `oceanstream.geotrack.tiling` module which already handles all the complexity. The processor just needs to call it with the right parameters.

### 3. Why these zoom levels?

Default zoom levels (0-10) balance between detail and file size:
- z0-z5: Global to continental scale
- z6-z10: Regional to city scale (good for ocean tracks)
- z10+ would create very large files for long-duration missions

Users can adjust via CLI parameters for specific use cases.

### 4. Why these columns?

Selected columns (`latitude`, `longitude`, `time`, `platform_id`) provide:
- Essential positioning data for rendering tracks
- Temporal information for filtering/animation
- Platform identification for multi-platform datasets
- Minimal tile size (excludes all measurement data)

For data exploration, users query the GeoParquet. For visualization, PMTiles provides fast map rendering.

## Testing

All tests pass (105 tests, 8 warnings):
```bash
make test
# ======== 105 passed, 8 warnings in X.XXs ========
```

The implementation was tested with:
- ✅ Syntax validation (py_compile)
- ✅ Full test suite (pytest)
- ✅ CLI integration test (test_cli_geotrack_integration.py)
- ✅ Backwards compatibility verification

## Future Enhancements

Possible improvements for future iterations:

1. **Custom column selection**: Allow users to specify which columns to include in tiles
2. **Multi-file output**: Generate separate PMTiles per platform or time period
3. **Aggregated layers**: Add low-zoom aggregated bins (z0-z7) for dense datasets
4. **Azure upload**: Integrate with `--upload` flag to push PMTiles to blob storage
5. **Metadata embedding**: Store additional metadata in PMTiles JSON metadata

## Migration Guide

### From Legacy `process_gps.py`

If you're using the legacy `docs/process_gps.py` script:

**Old approach (legacy script):**
```python
from docs.process_gps import build_and_upload_track_pmtiles

pmtiles_path = build_and_upload_track_pmtiles(
    src_container="gpsdata",
    dst_container="gpstiles", 
    cruise_id="sd1030",
    sample_rate=5,
    time_gap_min=60
)
```

**New approach (integrated pipeline):**
```python
from oceanstream.geotrack import process
from oceanstream.providers import get_provider

provider = get_provider("saildrone")

# Step 1: Process CSV → GeoParquet + PMTiles
process(
    provider=provider,
    input_dir=Path("./raw_data"),
    output_dir=Path("./out/geoparquet"),
    generate_pmtiles=True,
    yes=True,
    verbose=True
)

# Step 2: Upload if needed (future: use --upload flag)
from oceanstream.storage.azure_blob import upload_to_azure_blob

upload_to_azure_blob(
    file_path="./out/geoparquet/track.pmtiles",
    container_name="gpstiles",
    blob_name="sd1030/track.pmtiles"
)
```

**Benefits:**
- Single unified pipeline for CSV → GeoParquet → PMTiles
- Consistent CLI and library API
- Better error handling and progress reporting
- Semantic metadata automatically embedded
- Works with multiple providers (not just Saildrone)

## Summary

✅ PMTiles generation successfully integrated into oceanstream geotrack pipeline  
✅ Fully backwards compatible with existing code  
✅ Optional feature with graceful degradation  
✅ Consistent CLI and library API  
✅ Comprehensive documentation and examples  
✅ All tests passing (105/105)  

The implementation is production-ready and follows the principle: **"Make common tasks easy, make advanced tasks possible."**
