# Feature Spec: Acoustic Data Enrichment & Visualization

## 1. Goal
Enable spatial-temporal enrichment of Oceanstream (or other vessel) oceanographic data with co-located acoustic measurements from echosounders (EK60/EK80), processed via echopype and stored as Zarr. Produce enriched GeoParquet datasets and optionally generate PMTiles for interactive web-based visualization of acoustic metrics along vessel tracks.

## 2. Non-Goals (MVP Boundaries)
- No real-time acoustic processing (pre-processed echopype Zarr required).
- No advanced classification/species identification (raw Sv/MVBS only).
- No acoustic model inversion (target strength → biomass conversion optional).
- No multi-vessel acoustic fusion (single Zarr source per enrichment run).
- No custom web UI framework (static HTML + MapLibre GL JS only).

## 3. Problem Statement
Researchers collecting simultaneous oceanographic (CTD, GPS) and acoustic (echosounder) data from vessels face these challenges:
- **Data Silos**: Acoustic and environmental data stored separately (Zarr vs CSV).
- **Manual Merging**: Spatial-temporal alignment requires custom scripting.
- **Visualization Gap**: No turnkey solution to visualize acoustic backscatter along tracks.
- **Metadata Loss**: Provenance and match quality metrics often discarded.
- **Scalability**: Large acoustic datasets (100K+ pings) require efficient indexing.

## 4. Use Cases

### Primary
1. **Marine Ecology**: Correlate fish biomass proxies (Sv, NASC) with temperature/salinity fronts.
2. **Diel Vertical Migration**: Analyze how organisms move through water column over time.
3. **Survey Planning**: Identify high-density regions for targeted sampling.
4. **Data QA/QC**: Detect acoustic dropout zones or GPS/time sync issues.

### Secondary
5. **Educational Outreach**: Interactive maps showing ocean biomass patterns.
6. **Multi-Campaign Comparison**: Standardized enriched datasets for meta-analysis.

## 5. Data Assumptions

### Input: Oceanstream CSV
- Mandatory columns: `latitude`, `longitude`, `time` (or `timestamp`).
- Optional: `platform_id`, `trajectory`, environmental variables (TEMP_*, SAL_*, etc.).
- Temporal resolution: 1-60 second intervals (typical).

### Input: Echopype Zarr
- Structure: Standard echopype converted Zarr dataset.
- Coordinate variables: `latitude`, `longitude`, `ping_time`.
- Data variables: `Sv` (volume backscatter strength), optionally `MVBS` (mean volume backscatter).
- Dimensions: `ping_time`, `range_sample` (or `depth`), `frequency` (if multi-frequency).
- Expected size: 10-500 GB per survey.

### Spatial-Temporal Alignment
- Typical offset: 0-5 km spatial, 0-10 minutes temporal (depending on vessel proximity).
- Match strategy: Nearest neighbor within configurable tolerance.

## 6. Architecture Overview

```
┌─────────────────┐      ┌──────────────────┐
│  Saildrone CSV  │      │  Echopype Zarr   │
│  (lat/lon/time) │      │  (Sv/MVBS/depth) │
└────────┬────────┘      └────────┬─────────┘
         │                        │
         └────────┬───────────────┘
                  ▼
         ┌────────────────────┐
         │ Zarr Adapter Layer │
         │ - Trajectory index │
         │ - Spatial query    │
         │ - Depth aggregation│
         └────────┬───────────┘
                  ▼
         ┌────────────────────┐
         │ Enrichment Engine  │
         │ - Match algorithm  │
         │ - Quality metrics  │
         │ - Metadata embed   │
         └────────┬───────────┘
                  ▼
         ┌────────────────────┐
         │ Enhanced GeoParquet│
         │ + acoustic columns │
         └────────┬───────────┘
                  │
      ┌───────────┴───────────┐
      ▼                       ▼
┌─────────────┐      ┌────────────────┐
│ PMTiles Gen │      │  Analysis APIs │
│ - tippecanoe│      │  - geopandas   │
│ - styling   │      │  - xarray      │
└──────┬──────┘      └────────────────┘
       ▼
┌──────────────┐
│ Web Viewer   │
│ - MapLibre   │
│ - Popups     │
│ - Legends    │
└──────────────┘
```

## 7. Implementation Components

### 7.1 Zarr Adapter Module
**File**: `oceanstream/pipeline/zarr_adapter.py`

**Responsibilities**:
- Open echopype Zarr datasets (local or cloud S3/Azure).
- Extract trajectory (lat/lon/time) and build spatial index.
- Query acoustic data by spatiotemporal window.
- Aggregate depth profiles (mean, max, integrated NASC).

**Key Classes**:

```python
class AcousticDataAdapter:
    """Interface to echopype Zarr for spatial joins."""
    
    def __init__(
        self, 
        zarr_path: Path | str,
        cache_trajectory: bool = True,
        use_dask: bool = True
    ):
        """Load Zarr and optionally build R-tree index."""
        
    def to_trajectory_dataframe(self) -> pd.DataFrame:
        """Convert ping coordinates to flat DataFrame."""
        
    def build_spatial_index(self) -> rtree.index.Index:
        """Create R-tree for fast bbox queries."""
        
    def query_spatiotemporal(
        self,
        lat: float,
        lon: float,
        time: pd.Timestamp,
        spatial_tol_km: float = 1.0,
        temporal_tol_min: int = 5
    ) -> Optional[dict]:
        """Find nearest acoustic ping within tolerance."""
        
    def aggregate_depth_profile(
        self,
        ping_idx: int,
        depth_ranges: List[tuple[float, float]]
    ) -> dict[str, float]:
        """Compute mean Sv for depth bins (e.g., 0-50m, 50-100m)."""
        
    def get_metadata(self) -> dict:
        """Extract echosounder model, frequencies, calibration info."""
```

**Dependencies**:
- `xarray`, `zarr`, `dask` (lazy loading)
- `rtree` or `shapely.STRtree` (spatial indexing)
- `pyproj` (distance calculations)

### 7.2 Enrichment Engine
**File**: `oceanstream/pipeline/acoustic_enrichment.py`

**Responsibilities**:
- Iterate through Oceanstream DataFrame records.
- For each record, query acoustic adapter.
- Append new columns with acoustic metrics + match quality.
- Embed provenance metadata.

**Key Functions**:

```python
def enrich_with_acoustic(
    df: pd.DataFrame,
    acoustic_adapter: AcousticDataAdapter,
    config: AcousticEnrichmentConfig
) -> pd.DataFrame:
    """Add acoustic columns to DataFrame."""
    
def compute_match_quality(
    spatial_offset_km: float,
    temporal_offset_sec: float,
    config: AcousticEnrichmentConfig
) -> str:
    """Classify match as 'exact', 'good', 'fair', 'none'."""
    
def embed_acoustic_metadata(
    table: pa.Table,
    acoustic_adapter: AcousticDataAdapter,
    match_stats: dict
) -> pa.Table:
    """Add oceanstream:enrichment metadata block."""
```

**Configuration**:

```python
@dataclass
class AcousticEnrichmentConfig:
    spatial_tolerance_km: float = 1.0
    temporal_tolerance_min: int = 5
    depth_ranges: List[tuple[float, float]] = field(
        default_factory=lambda: [(0, 50), (50, 100), (100, 200)]
    )
    frequencies: Optional[List[int]] = None  # Filter by frequency [38000, 120000]
    compute_nasc: bool = True
    interpolate_missing: bool = False
    parallel: bool = True
    max_workers: int = 4
```

### 7.3 PMTiles Generator
**File**: `oceanstream/pipeline/pmtiles_generator.py`

**Responsibilities**:
- Convert enriched GeoParquet to GeoJSON with acoustic properties.
- Add derived visualization columns (biomass category, color hints).
- Invoke `tippecanoe` to generate PMTiles.
- Produce tileset metadata JSON.

**Key Functions**:

```python
class PMTilesGenerator:
    def prepare_geojson(
        self,
        geoparquet_path: Path,
        acoustic_columns: List[str],
        simplify_tolerance: float = 0.001
    ) -> Path:
        """Convert to GeoJSON with selected properties."""
        
    def add_visualization_columns(self, gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
        """Compute biomass_category, color_value for styling."""
        
    def generate_pmtiles(
        self,
        geojson_path: Path,
        output_path: Path,
        min_zoom: int = 0,
        max_zoom: int = 14,
        layer_name: str = "oceanstream_acoustic"
    ) -> Path:
        """Run tippecanoe to create PMTiles."""
        
    def generate_style_json(self, output_path: Path) -> Path:
        """Create MapLibre style.json with acoustic styling rules."""
```

**tippecanoe Command**:

```bash
tippecanoe \
    -o oceanstream_acoustic.pmtiles \
  -Z 0 -z 14 \
    --layer oceanstream_acoustic \
    --attribution "Oceanstream + Echopype" \
  --drop-densest-as-needed \
  --extend-zooms-if-still-dropping \
  --simplification=10 \
  input.geojson
```

### 7.4 Web Viewer
**File**: `oceanstream/web/acoustic_viewer.html`

**Features**:
- Interactive map with zoom/pan (MapLibre GL JS).
- Track line + color-coded acoustic points.
- Popup with full record details on click.
- Legend showing Sv/NASC categories.
- Optional time slider for animated playback.

**Styling Example**:

```javascript
{
    'id': 'acoustic-points',
    'type': 'circle',
    'paint': {
        'circle-radius': ['interpolate', ['linear'], ['zoom'], 8, 3, 16, 10],
        'circle-color': [
            'step',
            ['get', 'sv_mean_0_50m'],
            '#fee5d9', -70,  // Low biomass
            '#fcae91', -60,  // Medium
            '#fb6a4a', -50,  // High
            '#cb181d'        // Very high
        ],
        'circle-opacity': 0.8,
        'circle-stroke-width': 1,
        'circle-stroke-color': '#fff'
    }
}
```

## 8. Data Schema

### 8.1 Enriched GeoParquet Columns

| Column Name | Type | Unit | Description | Source |
|-------------|------|------|-------------|--------|
| `latitude` | float64 | degrees | WGS84 latitude | Original |
| `longitude` | float64 | degrees | WGS84 longitude | Original |
| `time` | datetime64 | UTC | Measurement timestamp | Original |
| `platform_id` | string | - | Vessel identifier | Original |
| `sv_mean_0_50m` | float64 | dB re 1 m⁻¹ | Mean Sv 0-50m depth | Acoustic |
| `sv_mean_50_100m` | float64 | dB re 1 m⁻¹ | Mean Sv 50-100m depth | Acoustic |
| `sv_mean_100_200m` | float64 | dB re 1 m⁻¹ | Mean Sv 100-200m depth | Acoustic |
| `nasc_integrated` | float64 | m² nmi⁻² | Nautical Area Scattering Coefficient | Acoustic |
| `acoustic_frequency_hz` | float64 | Hz | Nominal echosounder frequency | Acoustic |
| `acoustic_time_offset_sec` | float64 | seconds | Time diff to matched ping | Quality |
| `acoustic_distance_km` | float64 | km | Spatial offset to matched ping | Quality |
| `acoustic_match_quality` | string | - | 'exact', 'good', 'fair', 'none' | Quality |
| `biomass_category` | string | - | 'low', 'medium', 'high', 'very_high' | Derived |

### 8.2 Metadata Schema

```json
{
    "oceanstream:enrichment": {
        "acoustic": {
            "source_zarr": "s3://echopype-data/cruise_2024/acoustic.zarr",
            "echopype_version": "0.8.4",
            "echosounder_model": "EK80",
            "frequencies": [38000, 70000, 120000, 200000],
            "depth_ranges": [[0, 50], [50, 100], [100, 200]],
            "spatial_tolerance_km": 1.0,
            "temporal_tolerance_min": 5,
            "interpolation_enabled": false,
            "match_statistics": {
                "total_records": 9600,
                "matched_records": 8734,
                "match_rate": 0.91,
                "mean_time_offset_sec": 28.3,
                "mean_spatial_offset_km": 0.18,
                "quality_distribution": {
                    "exact": 245,
                    "good": 6821,
                    "fair": 1668,
                    "none": 866
                }
            },
            "enrichment_timestamp": "2024-11-07T19:45:00Z",
            "processing_time_sec": 142.3
        }
    },
    "oceanstream:units": {
        "sv_mean_0_50m": "dB re 1 m^-1",
        "sv_mean_50_100m": "dB re 1 m^-1",
        "nasc_integrated": "m^2 nmi^-2",
        "acoustic_distance_km": "km"
    }
}
```

## 9. CLI Interface

### 9.1 New Flags

Add to existing `oceanstream/cli.py`:

```python
# Acoustic enrichment flags
parser.add_argument(
    '--enrich-acoustic',
    type=Path,
    metavar='ZARR_PATH',
    help='Enrich with echopype Zarr acoustic data (Sv/MVBS)'
)
parser.add_argument(
    '--acoustic-spatial-tolerance',
    type=float,
    default=1.0,
    metavar='KM',
    help='Spatial matching tolerance in kilometers (default: 1.0)'
)
parser.add_argument(
    '--acoustic-temporal-tolerance',
    type=int,
    default=5,
    metavar='MIN',
    help='Temporal matching tolerance in minutes (default: 5)'
)
parser.add_argument(
    '--acoustic-depth-ranges',
    type=str,
    default='0-50,50-100,100-200',
    help='Comma-separated depth ranges in meters (default: 0-50,50-100,100-200)'
)
parser.add_argument(
    '--acoustic-frequencies',
    type=str,
    help='Comma-separated frequencies to include (Hz), e.g., "38000,120000"'
)
parser.add_argument(
    '--acoustic-compute-nasc',
    action='store_true',
    default=True,
    help='Compute integrated NASC values (default: enabled)'
)
parser.add_argument(
    '--acoustic-interpolate',
    action='store_true',
    help='Interpolate missing acoustic values (experimental)'
)

# PMTiles generation flags
parser.add_argument(
    '--generate-pmtiles',
    action='store_true',
    help='Generate PMTiles from enriched GeoParquet for web display'
)
parser.add_argument(
    '--pmtiles-output',
    type=Path,
    default=Path('out/oceanstream_acoustic.pmtiles'),
    help='Output path for PMTiles file (default: out/oceanstream_acoustic.pmtiles)'
)
parser.add_argument(
    '--pmtiles-max-zoom',
    type=int,
    default=14,
    help='Maximum zoom level for PMTiles (default: 14)'
)
parser.add_argument(
    '--pmtiles-layer-name',
    type=str,
    default='oceanstream_acoustic',
    help='Layer name in PMTiles (default: oceanstream_acoustic)'
)
```

### 9.2 Usage Examples

**Basic enrichment**:
```bash
oceanstream \
  --input-dir raw_data/ \
  --output-dir out/geoparquet \
  --enrich-acoustic /data/echopype/cruise_2024.zarr \
  -v
```

**Custom tolerances + PMTiles**:
```bash
oceanstream \
  --input-dir raw_data/ \
  --output-dir out/geoparquet \
  --enrich-acoustic s3://echopype-bucket/acoustic.zarr \
  --acoustic-spatial-tolerance 0.5 \
  --acoustic-temporal-tolerance 2 \
  --acoustic-depth-ranges 0-30,30-60,60-100 \
  --generate-pmtiles \
  --pmtiles-output out/web/tracks.pmtiles \
  --pmtiles-max-zoom 12 \
  -v
```

**Multi-frequency filtering**:
```bash
oceanstream \
  --input-dir raw_data/ \
  --output-dir out/geoparquet \
  --enrich-acoustic /data/multi_freq.zarr \
  --acoustic-frequencies 38000,120000 \
  --acoustic-compute-nasc \
  -v
```

## 10. Configuration (Settings)

Extend `oceanstream/config/settings.py`:

```python
class Settings:
    # Existing settings...
    
    # Acoustic enrichment
    ACOUSTIC_ENABLE = os.getenv("ACOUSTIC_ENABLE", "false").lower() == "true"
    ACOUSTIC_ZARR_PATH = os.getenv("ACOUSTIC_ZARR_PATH")
    ACOUSTIC_SPATIAL_TOLERANCE_KM = float(os.getenv("ACOUSTIC_SPATIAL_TOLERANCE_KM", "1.0"))
    ACOUSTIC_TEMPORAL_TOLERANCE_MIN = int(os.getenv("ACOUSTIC_TEMPORAL_TOLERANCE_MIN", "5"))
    ACOUSTIC_DEPTH_RANGES = os.getenv("ACOUSTIC_DEPTH_RANGES", "0-50,50-100,100-200")
    ACOUSTIC_COMPUTE_NASC = os.getenv("ACOUSTIC_COMPUTE_NASC", "true").lower() == "true"
    
    # PMTiles
    PMTILES_ENABLE = os.getenv("PMTILES_ENABLE", "false").lower() == "true"
    PMTILES_OUTPUT_PATH = Path(os.getenv("PMTILES_OUTPUT_PATH", "out/oceanstream.pmtiles"))
    PMTILES_MAX_ZOOM = int(os.getenv("PMTILES_MAX_ZOOM", "14"))
```

## 11. Error Handling & Edge Cases

### 11.1 Missing Acoustic Matches
- **Scenario**: Oceanstream record outside acoustic survey area or time window.
- **Handling**: Set acoustic columns to `null`, `acoustic_match_quality = 'none'`.
- **User Feedback**: Print summary: "Matched 8734/9600 records (91%)".

### 11.2 Zarr Load Failures
- **Scenario**: Invalid path, corrupted Zarr, missing coordinates.
- **Handling**: Raise `AcousticDataError` with diagnostic message.
- **Example**: "Failed to open Zarr: missing 'ping_time' coordinate. Ensure echopype conversion completed successfully."

### 11.3 Time Zone Mismatches
- **Scenario**: CSV uses local time, Zarr uses UTC.
- **Handling**: Require all timestamps in UTC (enforce via config flag `--assume-utc`).
- **Validation**: Warn if >50% of records have no match and suggest timezone check.

### 11.4 Large Memory Consumption
- **Scenario**: Zarr dataset > 100 GB loaded into memory.
- **Handling**: Use Dask chunking (`chunks={'ping_time': 10000}`), lazy evaluation.
- **Optimization**: Build persistent spatial index (save to `.idx` file for reuse).

### 11.5 Tippecanoe Not Installed
- **Scenario**: `--generate-pmtiles` flag used without tippecanoe.
- **Handling**: Check `shutil.which('tippecanoe')`, raise helpful error with install instructions.

### 11.6 Ambiguous Depth Dimensions
- **Scenario**: Zarr uses `range_sample` instead of `depth`.
- **Handling**: Auto-detect common dimension names (`depth`, `range_sample`, `echo_range`).
- **Fallback**: Require user to specify `--acoustic-depth-dim range_sample`.

## 12. Performance Considerations

### 12.1 Indexing Strategy
- **Problem**: O(n*m) brute-force matching for n=10K records, m=100K pings = 1B comparisons.
- **Solution**: R-tree spatial index reduces to O(n log m).
- **Implementation**: `rtree.index.Index` with bbox queries.
- **Benchmark**: 10K records × 100K pings: 2 min (indexed) vs 45 min (brute-force).

### 12.2 Parallel Processing
- **Approach**: Partition Oceanstream DataFrame by spatial bins, process each partition independently.
- **Library**: `concurrent.futures.ProcessPoolExecutor` with `max_workers=4`.
- **Expected Speedup**: 3-4× on multi-core machines.

### 12.3 Lazy Zarr Loading
- **Strategy**: Open Zarr with Dask, defer loading until query time.
- **Memory**: Keep <2GB resident for 100GB Zarr (chunk size tuning).

### 12.4 PMTiles Optimization
- **GeoJSON Simplification**: `simplify(tolerance=0.001)` reduces file size 40-60%.
- **Zoom Levels**: Limit to z14 for point data (higher zooms unnecessary).
- **Expected Size**: 10K points → 5-10 MB PMTiles (compressed).

## 13. Testing Strategy

### 13.1 Unit Tests

**File**: `oceanstream/tests/unit/test_zarr_adapter.py`

```python
def test_zarr_adapter_loads_trajectory():
    adapter = AcousticDataAdapter('tests/fixtures/test_acoustic.zarr')
    df = adapter.to_trajectory_dataframe()
    assert 'acoustic_lat' in df.columns
    assert len(df) > 0

def test_spatiotemporal_query_exact_match():
    adapter = AcousticDataAdapter('tests/fixtures/test_acoustic.zarr')
    match = adapter.query_spatiotemporal(
        lat=-8.234, lon=-152.456, time=pd.Timestamp('2024-08-15T14:23:00Z'),
        spatial_tol_km=0.1, temporal_tol_min=1
    )
    assert match is not None
    assert 'sv_mean_0_50m' in match

def test_query_no_match_returns_none():
    adapter = AcousticDataAdapter('tests/fixtures/test_acoustic.zarr')
    match = adapter.query_spatiotemporal(
        lat=0, lon=0, time=pd.Timestamp('2020-01-01'),
        spatial_tol_km=0.1, temporal_tol_min=1
    )
    assert match is None
```

**File**: `oceanstream/tests/unit/test_acoustic_enrichment.py`

```python
def test_enrich_adds_acoustic_columns():
    df = pd.DataFrame({
        'latitude': [-8.234],
        'longitude': [-152.456],
        'time': [pd.Timestamp('2024-08-15T14:23:00Z')]
    })
    adapter = AcousticDataAdapter('tests/fixtures/test_acoustic.zarr')
    enriched = enrich_with_acoustic(df, adapter, AcousticEnrichmentConfig())
    
    assert 'sv_mean_0_50m' in enriched.columns
    assert 'acoustic_match_quality' in enriched.columns

def test_match_quality_classification():
    quality = compute_match_quality(
        spatial_offset_km=0.2,
        temporal_offset_sec=30,
        config=AcousticEnrichmentConfig()
    )
    assert quality == 'good'
```

### 13.2 Integration Tests

**File**: `oceanstream/tests/integration/test_acoustic_pipeline.py`

```python
def test_end_to_end_enrichment_and_pmtiles(tmp_path):
    # Prepare test data
    csv_path = tmp_path / 'raw_data'
    csv_path.mkdir()
    # ... create test CSV
    
    zarr_path = tmp_path / 'acoustic.zarr'
    # ... create test Zarr
    
    # Run enrichment
    result = subprocess.run([
    'oceanstream',
        '--input-dir', str(csv_path),
        '--output-dir', str(tmp_path / 'out'),
        '--enrich-acoustic', str(zarr_path),
        '--generate-pmtiles',
        '--pmtiles-output', str(tmp_path / 'tracks.pmtiles')
    ], capture_output=True)
    
    assert result.returncode == 0
    assert (tmp_path / 'out').exists()
    assert (tmp_path / 'tracks.pmtiles').exists()
    
    # Validate enriched parquet
    gdf = gpd.read_parquet(tmp_path / 'out')
    assert 'sv_mean_0_50m' in gdf.columns
```

### 13.3 Fixture Data

Create minimal test fixtures:
- `tests/fixtures/test_acoustic.zarr`: 100 pings, 2 frequencies, 50m depth.
- `tests/fixtures/test_oceanstream.csv`: 50 records matching 40 pings.

## 14. Documentation Updates

### 14.1 README.md Additions

Add new section after existing features:

```markdown
## Acoustic Data Enrichment

Enrich oceanographic data with co-located echosounder measurements (EK60/EK80)
processed via [echopype](https://echopype.readthedocs.io).

### Quick Start

```bash
# Convert raw echosounder data
echopype convert --source-file raw.raw --output-file acoustic.zarr

# Enrich Oceanstream data
oceanstream \
  --input-dir raw_data \
  --output-dir out/enriched \
  --enrich-acoustic acoustic.zarr \
  -v

# Generate interactive web map
oceanstream \
  --input-dir raw_data \
  --output-dir out/enriched \
  --enrich-acoustic acoustic.zarr \
  --generate-pmtiles \
  --pmtiles-output web/tracks.pmtiles
```

### Enriched Columns

- `sv_mean_0_50m`: Mean volume backscatter 0-50m depth (dB)
- `nasc_integrated`: Nautical Area Scattering Coefficient (m²/nmi²)
- `acoustic_match_quality`: Quality indicator ('exact', 'good', 'fair', 'none')

See [docs/acoustic-enrichment.md](docs/acoustic-enrichment.md) for details.
```

### 14.2 New Documentation File

Create `docs/acoustic-enrichment.md` with:
- Detailed workflow diagram
- Depth range customization examples
- Multi-frequency processing guide
- PMTiles styling customization
- Performance tuning tips
- Troubleshooting common issues

## 15. Dependencies

### 15.1 New Python Packages

Add to `pyproject.toml`:

```toml
[tool.poetry.dependencies]
xarray = ">=2023.1"
zarr = ">=2.16"
dask = {version = ">=2023.1", extras = ["array"]}
rtree = ">=1.0"  # Spatial indexing
pyproj = ">=3.5"  # Geodesic distance
echopype = {version = ">=0.8", optional = true}

[tool.poetry.extras]
acoustic = ["xarray", "zarr", "dask", "rtree", "pyproj", "echopype"]
```

### 15.2 External Tools

- **tippecanoe**: Required for PMTiles generation.
  - Installation: `brew install tippecanoe` (macOS), or build from source.
  - Check: `tippecanoe --version`

- **echopype**: Required for Zarr preprocessing (user responsibility).
  - Installation: `pip install echopype`

## 16. Extensibility (Future)

### 16.1 Advanced Features (Post-MVP)

1. **Multi-Vessel Fusion**: Merge acoustic from multiple ships/gliders.
2. **Species Classification Integration**: Join with ML-based fish species predictions.
3. **Water Column Visualization**: 3D depth profiles in deck.gl.
4. **Target Strength Inversion**: Convert Sv → biomass density (kg/m³).
5. **Anomaly Detection**: Flag unusual acoustic patterns (dropout, interference).
6. **Real-Time Streaming**: Ingest live acoustic data from vessel MQTT feeds.

### 16.2 Plugin Architecture

```python
class AcousticEnricher(ABC):
    """Abstract base for custom acoustic enrichment strategies."""
    
    @abstractmethod
    def enrich_record(self, row: dict, acoustic_data: xr.Dataset) -> dict:
        """Custom enrichment logic."""
        pass

# User-defined enricher
class MyCustomEnricher(AcousticEnricher):
    def enrich_record(self, row, acoustic_data):
        # Custom depth weighting, multi-freq fusion, etc.
        return {'custom_metric': ...}
```

## 17. Success Criteria (MVP)

### Functional
- [ ] Successfully enrich 10K record Saildrone dataset with 100K ping Zarr in <5 minutes.
- [ ] Achieve ≥85% match rate with default tolerances (1 km, 5 min).
- [ ] Generate PMTiles <10 MB for 10K points.
- [ ] Web viewer loads and displays acoustic data with correct styling.

### Quality
- [ ] All unit tests pass (>90% coverage for new modules).
- [ ] Integration test covers full CLI workflow.
- [ ] Documentation includes runnable examples.
- [ ] No memory leaks with 500GB Zarr (Dask chunking validated).

### Performance
- [ ] R-tree indexing provides >10× speedup vs brute-force.
- [ ] Parallel processing achieves 3× speedup on 4-core machine.
- [ ] PMTiles render smoothly at z0-z14 in web viewer.

## 18. Implementation Roadmap

### Phase 1: Core Enrichment (Week 1-2)
- [ ] Implement `zarr_adapter.py` with trajectory extraction.
- [ ] Build R-tree spatial index.
- [ ] Implement `acoustic_enrichment.py` with nearest-neighbor matching.
- [ ] Add CLI flags and configuration.
- [ ] Write unit tests for adapter and enrichment.

### Phase 2: Quality & Metadata (Week 2)
- [ ] Implement match quality classification.
- [ ] Embed enrichment metadata in GeoParquet schema.
- [ ] Add depth range aggregation.
- [ ] Handle edge cases (missing matches, timezone issues).

### Phase 3: PMTiles Generation (Week 3)
- [ ] Implement `pmtiles_generator.py`.
- [ ] Create visualization column derivations (biomass_category).
- [ ] Integrate tippecanoe invocation.
- [ ] Generate style.json for MapLibre.

### Phase 4: Web Viewer (Week 3)
- [ ] Create `web/acoustic_viewer.html`.
- [ ] Implement popups and legend.
- [ ] Add time slider (optional).
- [ ] Test cross-browser compatibility.

### Phase 5: Testing & Docs (Week 4)
- [ ] Create test fixtures (Zarr + CSV).
- [ ] Write integration test.
- [ ] Update README and create `docs/acoustic-enrichment.md`.
- [ ] Performance benchmarking and optimization.

### Phase 6: Polish & Release (Week 4)
- [ ] Error message improvements.
- [ ] CLI help text refinement.
- [ ] Example notebooks (Jupyter).
- [ ] GitHub release with demo dataset.

## 19. Open Questions

1. **Interpolation Strategy**: Should we support temporal interpolation between pings, or stick to nearest-neighbor?
   - **Decision**: Nearest-neighbor for MVP; add `--acoustic-interpolate` flag for future.

2. **Multi-Frequency Handling**: Average across frequencies or keep separate columns?
   - **Decision**: Keep separate if user specifies `--acoustic-frequencies`, otherwise use first available.

3. **NASC Calculation**: Use echopype's built-in or implement custom?
   - **Decision**: Leverage echopype if available, otherwise simple trapezoidal integration.

4. **PMTiles Hosting**: Provide S3/Azure deployment examples?
   - **Decision**: Document in `docs/deployment.md` with nginx/Caddy examples.

5. **Real-Time Mode**: Support streaming enrichment?
   - **Decision**: Post-MVP; add to roadmap as Phase 7.

## 20. Security & Privacy

- **Data Residency**: All processing local by default; cloud Zarr via signed URLs.
- **Credentials**: Azure/S3 access via standard env vars (no CLI exposure).
- **PMTiles**: Static files only; no server-side execution risk.

## 21. Licensing

- Ensure echopype dependency compatible with project license (Apache 2.0).
- Attribute echopype in generated metadata and web viewer footer.

---

**Status**: Draft ready for implementation.  
**Est. Effort**: 4 weeks (1 developer).  
**Priority**: High (enables key research workflows, strong community demand).

