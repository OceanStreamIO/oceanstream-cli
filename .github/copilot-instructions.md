# GitHub Copilot Instructions for OceanStream

## Overview

OceanStream converts oceanographic records (Saildrone and R2R CSV/GeoCSV, NMEA logs, SeaBird `.hex`, EK60/EK80 echosounder files, RDI ADCP files) into partitioned Parquet, Zarr stores and STAC catalogues.
**Geotrack pipeline**: files → provider detection → read and enrich → 2°×2° spatial binning → deduplication → partitioned Parquet → optional STAC, PMTiles and heatmaps

## Build & Test

```bash
source venv/bin/activate                  # Python 3.11–3.13 (lint/type targets: 3.12)
pip install -e ".[echodata]"              # or ".[all]"; `make install` installs the core, which covers geotrack
make test-unit                            # pytest -q oceanstream/tests/unit
make test-integration                     # pytest -q oceanstream/tests/integration (not e2e/)
make test-slow                            # pytest -m slow: the tests every other target leaves out
make coverage-html                        # whole suite, HTML report → ./coverage_html/
```

`raw_data/` holds the test inputs and is gitignored; most tests that need it skip without it. `scripts/fetch_test_data.py` is meant to download it, but the manifest it reads, `scripts/test_data_manifest.json`, isn't in the repository.

Build system: **Poetry** (`poetry-core`). Entry point: `oceanstream = oceanstream.cli:app`. The project's `pyproject.toml` is the one at the repository root; `oceanstream/pyproject.toml` is an old copy.

The `echodata` extra installs `oceanstream-echopype` from PyPI: OceanStreamIO's echopype fork (branch `oceanstream-integration` of OceanStreamIO/echopype), pinned to one version. It provides the `echopype` module, so uninstall an `echopype` installed from git before installing it; uninstalling `echopype` afterwards deletes the files both share. To try an unreleased fork commit: `pip install "oceanstream-echopype @ git+https://github.com/OceanStreamIO/echopype.git@<commit>"`.

Lint and type settings are in `ruff.toml` and `mypy.ini`, but the codebase as a whole doesn't pass them, and no CI workflow runs tests or lint (`.github/workflows/` only builds Docker images). Check only the files you change (`ruff check <files>`, `mypy <files>`), and don't run `ruff format` or `ruff check --fix` on the whole package: it would rewrite most of the repository.

The `Makefile` targets `run-cli-sample`, `dry-run`, `schema` and `list-columns` use an old command syntax and no longer work.

## Architecture

| Module | Purpose |
|--------|---------|
| `oceanstream/cli.py` | Typer CLI: `process` (`geotrack`, `echodata` with `presets`, `multibeam`, `adcp`), `campaign` (commands registered from `campaign/cli.py`), `hpc` (`hpc/cli.py`), `providers`, `configure` |
| `oceanstream/geotrack/processor.py` | Geotrack pipeline: scan → process files → detect sensors → enrich → dedup → Parquet → STAC; `convert()` is the entry point |
| `oceanstream/echodata/processor.py` | `EchodataProcessor.run()`: convert → calibrate → environment → daily concat → Sv → denoise → seabed → shoals → MVBS/NASC → echograms. No CLI command runs the whole pipeline; the CLI runs each step separately |
| `oceanstream/providers/` | Data source adapters: implement the `ProviderBase` protocol (`base.py`), register in `factory.py` |
| `oceanstream/sensors/` | JSON-driven sensor catalogue: definitions auto-loaded from `sensors/definitions/*/sensor.json` |
| `oceanstream/stac/emit.py` | STAC 1.0 collection + item generation |
| `oceanstream/storage/` | Output storage: local, Azure, S3 and GCS through `pyarrow.fs` (`filesystem.py`); credentials saved by `oceanstream configure`, encrypted with `crypto.py` |
| `oceanstream/configuration.py` | TOML config with env var substitution (`${VAR}`, `${VAR:-default}`) |

### Provider Protocol

New providers must implement `ProviderBase` from `oceanstream/providers/base.py`:
- `name`, `supported_modules` (Literal: geotrack, echodata, multibeam, adcp), `is_stationary`
- `identify_platform(filename) → str | None`
- `enrich_dataframe(df) → DataFrame`
- `alias_mapping(columns) → dict[str, str]`
- `units_mapping(header, units_row) → dict`
- `parquet_metadata(df) → dict`
- `supports_module(module) → bool`
- `detect_confidence(headers, metadata_lines, filename) → float` (0.0–1.0): `factory.detect_provider()` scores every registered provider with it and falls back to `GenericProvider`

Register in `factory.py`'s `_REGISTRY`, or at runtime with `register_provider(name, cls)`. `saildrone` is a hidden alias of `noaa_pmel`.

## Critical Patterns

### Always use `pathlib.Path`
```python
from pathlib import Path
output = Path(output_dir) / file_path.name  # ✅
data = pd.read_csv(f"{input_dir}/{filename}")  # ❌
```

### Future annotations and TYPE_CHECKING
```python
from __future__ import annotations       # ✅ Add to new and edited modules
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

### Tests: isolate `~/.oceanstream`
File tracking and campaigns are written to `~/.oceanstream/` by default. `oceanstream/tests/integration/conftest.py` isolates both with an autouse fixture; unit tests that run a conversion have to do the same:
```python
def test_something(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(Settings, "METADATA_DIR", tmp_path / "metadata")
    monkeypatch.setenv("OCEANSTREAM_CAMPAIGNS_DIR", str(tmp_path / "campaigns"))
```

### Error messages must be actionable
```python
raise ValueError("campaign_id required. Supply via --campaign-id or ensure it's in file metadata.")  # ✅
raise ValueError("Missing campaign_id")  # ❌
```

## Code Style

- **Line length**: 100 (`ruff.toml`)
- **Ruff rules**: E, F, I, UP, PL; quote-style preserve, indent spaces, LF endings
- **mypy** (`mypy.ini`): `disallow_untyped_defs`, `strict_optional`, `no_implicit_optional`; type every new public function
- **Python**: 3.11+ with absolute imports

## Testing

Test root: `oceanstream/tests/` (`unit/`, `integration/`, `e2e/`). Markers registered in `pytest.ini`: `integration`, and `slow`, which `addopts` leaves out of every run that doesn't pass its own `-m` (the SBE911 tests that decode a `.hex` cast, about 35 s); some tests also use an unregistered `e2e` marker. Inputs come from `raw_data/` at the repo root. `oceanstream/tests/data/` is gitignored, so a test that reads `oceanstream/tests/data/raw_data/` fails on a fresh clone; generate small inputs in `tmp_path` instead.

**CLI test pattern**:
```python
from typer.testing import CliRunner
from oceanstream import cli as cli_module
result = runner.invoke(cli_module.app, ["process", "geotrack", "convert", "--input-source", str(path), "--campaign-id", "TEST", "--yes"])
assert result.exit_code == 0, result.output
```

## Output Structure

```
output_dir/
├── campaign_id/
│   ├── lat_bin=lat_-46_-44/lon_bin=lon_170_172/<uuid>.parquet   # Hive partitions; bin labels are strings
│   ├── stac/collection.json, stac/items/item-N.json             # only when SEMANTIC_ENABLE and SEMANTIC_GENERATE_STAC are true
│   ├── tiles/track.pmtiles                                      # PMTiles; the STAC collection links ../tiles/track.pmtiles
│   └── echodata/{raw,daily,sv,denoised,seabed_masked,shoal_masked,mvbs,nasc,echograms}/, pipeline_report.json
```

Bins are 2°×2°, with latitude clamped to ±15° (`geotrack/binning.py`). `write_geoparquet` builds a WKB point `geometry` column from `latitude`/`longitude` and writes GeoParquet 1.1 `geo` metadata (CRS84). The campaign folder also holds `stac/` and `tiles/`, so `gpd.read_parquet` on it needs `ignore_prefixes=['.', '_', 'stac', 'tiles', 'heatmaps']`. SHA256 tracking of processed files is in `~/.oceanstream/metadata/<campaign_id>.json` (`geotrack/metadata.py`); rows are deduplicated on time, latitude, longitude and trajectory (`geotrack/deduplication.py`).

## Configuration & Environment

Config lookup: `--config-file` → `./oceanstream.toml` → internal defaults. Environment variables override the TOML. `oceanstream/config/settings.py` loads `.env` via python-dotenv.

Key env vars: `OCEANSTREAM_METADATA_DIR`, `OCEANSTREAM_CAMPAIGNS_DIR`, `SEMANTIC_ENABLE` (default false), `SEMANTIC_GENERATE_STAC` (needs `SEMANTIC_ENABLE`), and for `az://` output `AZURE_CONNECTION_STRING`, or `AZURE_STORAGE_ACCOUNT` with `AZURE_STORAGE_KEY`. `RAW_DATA_PATH` and `OUTPUT_PATH` are read only by the legacy `oceanstream/app.py`.

PMTiles generation calls the external `tippecanoe` and `pmtiles` executables.

## Multi-Platform Campaigns

Campaigns group data from multiple platforms. CLI: `oceanstream campaign create TPOS_2023 --platform "sd1030:Saildrone 1030:Saildrone Explorer" --platform "sd1033"`. Metadata stored at `~/.oceanstream/campaigns/{campaign_id}/campaign.json`, or under `$OCEANSTREAM_CAMPAIGNS_DIR`.

## Adding New Features

### New Provider
1. Create `oceanstream/providers/<name>.py` implementing the `ProviderBase` protocol, including `detect_confidence`
2. Register in `oceanstream/providers/factory.py`: `_REGISTRY["<name>"] = <Name>Provider`
3. Add tests in `oceanstream/tests/unit/test_<name>_provider.py`

### New Sensor
1. Create `oceanstream/sensors/definitions/<sensor-id>/sensor.json` + `README.md`
2. Variables in JSON are auto-detected by the catalogue singleton at import time
3. Optionally add processor in `oceanstream/sensors/processors/`

### New CLI Command
1. Add to `oceanstream/cli.py` using Typer decorators
2. Use nested Typer apps for subcommands (see `process_app`, `campaign_app`, `geotrack_app`, `echodata_app`)
3. Add integration test in `oceanstream/tests/integration/`
