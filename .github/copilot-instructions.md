# GitHub Copilot Instructions for OceanStream

## Overview
OceanStream ingests oceanographic CSV/GeoCSV data → cloud-optimized GeoParquet with STAC metadata.
**Pipeline**: CSV → Provider Detection → Semantic Mapping → 1°×1° Spatial Binning → GeoParquet → STAC

## Quick Start
```bash
# Setup (Python 3.12+ recommended)
source venv/bin/activate && make test-unit   # Fast unit tests

# Geotrack processing
oceanstream process geotrack convert --input-source ./data --campaign-id my_campaign -v
oceanstream process geotrack convert --dry-run --input-source ./data  # Preview only

# Echodata processing
python -m pytest oceanstream/tests/unit/echodata/ -v  # Echodata tests
oceanstream process echodata convert --input-source ./raw_data/saildrone-ek80-raw --output-dir ./out
```

## Architecture
| Module | Purpose |
|--------|---------|
| `oceanstream/cli.py` | Typer CLI entry point |
| `oceanstream/geotrack/processor.py` | Main processing pipeline |
| `oceanstream/providers/` | Data source adapters (Saildrone, R2R) - implement `ProviderBase` |
| `oceanstream/stac/emit.py` | STAC 1.0 metadata generation |
| `oceanstream/sensors/definitions/` | JSON sensor configs for auto-detection |

**Provider Pattern** - Register new providers in `factory.py`:
```python
# oceanstream/providers/factory.py
_REGISTRY: dict[str, Type[ProviderBase]] = {"saildrone": SaildroneProvider, "r2r": R2RProvider}
```

**Provider-Specific Details**:
- **Saildrone**: CSV with `SD_` column prefixes, auto-detects platform from filename (e.g., `sd1030_`)
- **R2R**: GeoCSV with `# ` metadata headers, cruise ID patterns (`FK161229`, `AT42-05`), supports `.tar.gz` archive extraction

**Column Mappings** (each provider normalizes to standard names):
```python
# Example from R2R provider
COLUMN_MAPPINGS = {
    "ship_longitude": "longitude",
    "ship_latitude": "latitude", 
    "iso_time": "time",
    "speed_made_good": "speed_over_ground",
}
```

## Critical Patterns

### Always use `pathlib.Path`
```python
from pathlib import Path
output = Path(output_dir) / file_path.name  # ✅
data = pd.read_csv(f"{input_dir}/{filename}")  # ❌
```

### Test paths must be relative
```python
project_root = Path(__file__).resolve().parents[3]  # ✅ Portable
test_data_dir = "/Users/andrei/..."  # ❌ Breaks CI
```

### Integration tests: isolate metadata
```python
@pytest.mark.integration
def test_something(tmp_path: Path, monkeypatch):
    metadata_dir = tmp_path / "metadata"
    metadata_dir.mkdir(parents=True)
    monkeypatch.setattr(Settings, "METADATA_DIR", metadata_dir)
```

### Error messages must be actionable
```python
raise ValueError("campaign_id required. Supply via --campaign-id or ensure it's in file metadata.")  # ✅
raise ValueError("Missing campaign_id")  # ❌
```

## Output Structure
```
output_dir/campaign_id/
├── lat_bin=X/lon_bin=Y/*.parquet   # Hive-partitioned GeoParquet
├── stac/collection.json            # STAC 1.0 catalog
├── tiles/track.pmtiles             # Optional vector tiles
└── .oceanstream_metadata.json      # SHA256 file tracking
```

## Multi-Platform Campaigns
Campaigns can contain data from multiple platforms (e.g., multiple USVs in a deployment).

**CLI**:
```bash
oceanstream campaign create TPOS_2023 \
    --platform "sd1030:Saildrone 1030:Saildrone Explorer" \
    --platform "sd1033" --platform "sd1079"
# Format: --platform "id:name:type" (name/type optional)
```

**campaign.json** (`~/.oceanstream/campaigns/{campaign_id}/campaign.json`):
```json
{
  "campaign_id": "tpos_2023",
  "platforms": [
    {"id": "sd1030", "type": "Saildrone Explorer", "row_count": 9600},
    {"id": "sd1033", "type": "Saildrone Explorer", "row_count": 192974}
  ],
  "total_rows": 356661,
  "sensors": [...]
}
```

**STAC collection.json**: `summaries.platforms` array, `summaries.instruments` aggregated sensors

## Testing
```bash
make test-unit          # Fast (~45 files)
make test-integration   # End-to-end (~20 files)
make coverage-html      # HTML report → ./htmlcov/
```

**CLI test pattern**:
```python
from typer.testing import CliRunner
from oceanstream import cli as cli_module
result = runner.invoke(cli_module.app, ["process", "geotrack", "convert", "--input-source", str(path), "--yes"])
assert result.exit_code == 0, result.output
```

## Code Style
- **Linting**: `ruff check . --fix`
- **Types**: `mypy oceanstream` - all public APIs typed
- **Python**: 3.11+ with absolute imports

## Echodata Processing
- Processing EK60/EK80 echosounder data with echopype fork
- 9-step pipeline: Raw → Convert → Calibrate → GPS Interpolation → Daily Concatenation → Sv → Denoise → MVBS/NASC/Echograms → STAC

## Adding New Features

### New Provider
1. Create `oceanstream/providers/<name>.py` implementing `ProviderBase` protocol
2. Register in `oceanstream/providers/factory.py`:
   ```python
   from .<name> import <Name>Provider
   _REGISTRY["<name>"] = <Name>Provider
   ```
3. Add tests in `tests/unit/test_<name>_provider.py`
4. For complex providers, create subpackage `oceanstream/providers/<name>/`

### New Sensor
1. Create `oceanstream/sensors/definitions/<sensor-id>/sensor.json` + `README.md`
2. Register variables for auto-detection in the sensor JSON
3. Optionally add processor in `oceanstream/sensors/processors/`

### New CLI Command
1. Add to `oceanstream/cli.py` using Typer decorators (`@app.command()`)
2. Use nested Typer apps for subcommands (see `process_app`, `campaign_app`)
3. Add integration test in `tests/integration/`
