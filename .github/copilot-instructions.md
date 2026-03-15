# GitHub Copilot Instructions for OceanStream

## Overview

OceanStream ingests oceanographic CSV/GeoCSV data → cloud-optimized GeoParquet with STAC metadata.
**Pipeline**: CSV → Provider Detection → Semantic Mapping → 1°×1° Spatial Binning → GeoParquet → STAC

## Build & Test

```bash
source venv/bin/activate          # Python 3.11–3.13 (lint/type targets: 3.12)
make test-unit                    # Fast unit tests
make test-integration             # End-to-end tests (marks: @pytest.mark.integration)
make coverage-html                # HTML report → ./htmlcov/
ruff check . --fix && ruff format # Lint + format
mypy oceanstream                  # Type checking (strict, ignore_missing_imports)
```

Build system: **Poetry** (`poetry-core`). Entry point: `oceanstream = oceanstream.cli:app`.

## Architecture

| Module | Purpose |
|--------|---------|
| `oceanstream/cli.py` | Typer CLI — apps: `process_app` (geotrack, echodata, multibeam, adcp), `campaign_app` |
| `oceanstream/geotrack/processor.py` | Geotrack pipeline: scan → process files → detect sensors → enrich → dedup → GeoParquet → STAC |
| `oceanstream/echodata/processor.py` | Echodata pipeline: convert → calibrate → environment → concat → Sv → denoise → seabed → MVBS/NASC → echograms |
| `oceanstream/providers/` | Data source adapters — implement `ProviderBase` protocol (see `base.py`), register in `factory.py` |
| `oceanstream/sensors/` | JSON-driven sensor catalogue — definitions auto-loaded from `sensors/definitions/*/sensor.json` |
| `oceanstream/stac/emit.py` | STAC 1.0 collection + item generation |
| `oceanstream/storage/` | Storage abstraction — Local and Azure providers; config encrypted at rest via `manager.py` |
| `oceanstream/configuration.py` | TOML config with env var substitution (`${VAR}`, `${VAR:-default}`) |

### Provider Protocol

New providers must implement `ProviderBase` from `oceanstream/providers/base.py`:
- `name`, `supported_modules` (Literal: geotrack, echodata, multibeam, adcp)
- `identify_platform(filename) → str | None`
- `enrich_dataframe(df) → DataFrame`
- `alias_mapping(columns) → dict[str, str]`
- `units_mapping(header, units_row) → dict`
- `parquet_metadata(df) → dict`

Register in `factory.py`: `_REGISTRY["name"] = NameProvider`

## Critical Patterns

### Always use `pathlib.Path`
```python
from pathlib import Path
output = Path(output_dir) / file_path.name  # ✅
data = pd.read_csv(f"{input_dir}/{filename}")  # ❌
```

### Future annotations and TYPE_CHECKING
```python
from __future__ import annotations       # ✅ Used project-wide
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    import pandas as pd                   # ✅ Heavy imports behind TYPE_CHECKING
```

### Optional dependency guards
```python
try:
    import typer
except ImportError:
    typer = None                          # ✅ Graceful degradation for optional deps
```

### Test paths must be relative
```python
project_root = Path(__file__).resolve().parents[3]  # ✅ Portable
test_data_dir = "/Users/andrei/..."                 # ❌ Breaks CI
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

## Code Style

- **Line length**: 100 (`ruff.toml`)
- **Ruff rules**: E, F, I, UP, PL — quote-style preserve, indent spaces, LF endings
- **mypy**: `disallow_untyped_defs`, `strict_optional`, `no_implicit_optional` — all public APIs typed
- **Python**: 3.11+ with absolute imports, `from __future__ import annotations` everywhere

## Testing

Test root: `oceanstream/tests/`. Markers: `integration`. Shared test data: `tests/data/`. E2E uses `raw_data/` at repo root.

**CLI test pattern**:
```python
from typer.testing import CliRunner
from oceanstream import cli as cli_module
result = runner.invoke(cli_module.app, ["process", "geotrack", "convert", "--input-source", str(path), "--yes"])
assert result.exit_code == 0, result.output
```

**Integration conftest** auto-isolates `Settings.METADATA_DIR` via monkeypatch (autouse fixture).

## Output Structure

```
output_dir/campaign_id/
├── lat_bin=X/lon_bin=Y/*.parquet   # Hive-partitioned GeoParquet
├── stac/collection.json            # STAC 1.0 catalog
├── tiles/track.pmtiles             # Optional vector tiles
└── .oceanstream_metadata.json      # SHA256 file tracking
```

## Configuration & Environment

Config lookup: CLI flag → `./oceanstream.toml` → internal defaults. Settings in `oceanstream/config/settings.py` load `.env` via python-dotenv.

Key env vars: `OCEANSTREAM_METADATA_DIR`, `AZURE_STORAGE_CONNECTION_STRING`, `AZURE_CONTAINER_NAME`, `RAW_DATA_PATH`, `OUTPUT_PATH`, `SEMANTIC_ENABLE`, `SEMANTIC_GENERATE_STAC`.

## Multi-Platform Campaigns

Campaigns group data from multiple platforms. CLI: `oceanstream campaign create TPOS_2023 --platform "sd1030:Saildrone 1030:Saildrone Explorer" --platform "sd1033"`. Metadata stored at `~/.oceanstream/campaigns/{campaign_id}/campaign.json`.

## Adding New Features

### New Provider
1. Create `oceanstream/providers/<name>.py` implementing `ProviderBase` protocol
2. Register in `oceanstream/providers/factory.py`: `_REGISTRY["<name>"] = <Name>Provider`
3. Add tests in `tests/unit/test_<name>_provider.py`

### New Sensor
1. Create `oceanstream/sensors/definitions/<sensor-id>/sensor.json` + `README.md`
2. Variables in JSON are auto-detected by the catalogue singleton at import time
3. Optionally add processor in `oceanstream/sensors/processors/`

### New CLI Command
1. Add to `oceanstream/cli.py` using Typer decorators
2. Use nested Typer apps for subcommands (see `process_app`, `campaign_app`, `geotrack_app`, `echodata_app`)
3. Add integration test in `tests/integration/`
