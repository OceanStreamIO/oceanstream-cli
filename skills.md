# OceanStream Skills Reference

> **Package**: `oceanstream` 1.0.0rc1 | **Python**: 3.11–3.13 | **License**: MIT
>
> **Install**: `pip install "oceanstream==1.0.0rc1"`
>
> Reference for AI agents using the OceanStream CLI and Python library in other projects. Checked against `main` at bb394fb.

---

## What OceanStream Does

OceanStream converts raw oceanographic sensor data into Hive-partitioned Parquet with STAC metadata, PMTiles and COG heatmaps, and processes echosounder, multibeam and ADCP recordings.

**Inputs**:
- Track and sensor data (`process geotrack convert`): Saildrone CSVs from NOAA PMEL, R2R cruise archives (`.tar.gz`, `.tgz`), NMEA logs (`.txt`), SeaBird CTD `.hex` files, and CSV/GeoCSV downloaded from CMEMS, EMODnet, EMSO, NorSOOP, OceanLab, OOI or PLOCAN, or any CSV with latitude, longitude and time columns
- EK60/EK80 echosounder `.raw` files (`process echodata`)
- Kongsberg `.all` multibeam files (`process multibeam`)
- Teledyne RDI ADCP `.raw` files (`process adcp`)

**Outputs**:
- GeoParquet partitioned into 2°×2° latitude/longitude bins: a WKB point `geometry` column built from `latitude`/`longitude` and GeoParquet 1.1 `geo` metadata (CRS84). Output written before this was added has neither; positions are then only the `latitude` and `longitude` columns.
- STAC 1.0.0 Collection and Items, written only when semantic mapping is on (see Configuration)
- PMTiles vector tiles of the track (needs the `tippecanoe` and `pmtiles` executables)
- COG heatmaps of interpolated measurements
- Zarr stores for echosounder data, NetCDF for ADCP, COG grids and Parquet soundings for multibeam

---

## Installation

Release candidates install only when named; without `==1.0.0rc1` pip installs 0.1.1 (January 2026), which lacks most of what this file describes.

```bash
pip install "oceanstream==1.0.0rc1"

# With extras
pip install "oceanstream[echodata,echodata-viz]==1.0.0rc1"

# Unreleased code from GitHub
pip install "oceanstream @ git+https://github.com/OceanStreamIO/oceanstream-cli.git"

# Verify
oceanstream --help
python -c "import oceanstream; print(oceanstream.__version__)"   # 1.0.0rc1
```

A plain install covers `process geotrack`, the Python `convert`, and NMEA and CTD inputs: geopandas, shapely, pynmea2 and seabirdscientific (2.7.0 to 2.7.8) are core dependencies.

| Extra | Adds | Needed for |
|-------|------|------------|
| `echodata` | OceanStreamIO's echopype fork (`oceanstream-echopype` 0.11.1a3 on PyPI; it installs the `echopype` module and replaces upstream echopype), xarray, dask, zarr, netCDF4, fsspec, s3fs, adlfs, gsw, copernicusmarine and more | `process echodata`, `EchodataProcessor` |
| `echodata-viz` | matplotlib, hvplot, datashader, holoviews, panel, bokeh | Echogram plots |
| `adcp` | dolfyn, scipy, setuptools<81, xarray | `process adcp` |
| `multibeam` | themachinethatgoesping 0.37.1 (Python 3.12+), numpy, scipy, rasterio, pyproj, matplotlib, pmtiles | `process multibeam` |
| `norsoop` | erddapy | NorSOOP FerryBox access |
| `all` | Every extra above except `multibeam` | |

External tools: PMTiles need `tippecanoe` and `pmtiles` on `PATH`. Heatmaps need `scipy`, which comes with seabirdscientific, and `rasterio` (`pip install rasterio`, or the `all` extra) and use `gdal_translate` for COG output when it is installed.

---

## Running OceanStream From an Agent

- `process geotrack convert` asks `Proceed with processing? [Y/n]` before writing. Pass `--yes` (`yes=True` in Python). Without a terminal the prompt reads end-of-file and the run ends with `Processing cancelled.`
- `campaign create` without an ID starts an interactive wizard, `configure` is always interactive, and `campaign delete` prompts unless given `--yes`.
- These cases print a message and exit with status 0 without writing anything: input files already processed, a campaign registered at another output location, no campaign id found (printed as `ERROR`), a cancelled prompt. Look for `✓ Completed successfully` in the output. Exceptions during processing print `[geotrack] ERROR: ...` and exit with status 1.
- Files written outside the output directory:
  - `./.oceanstream_work/` in the current directory: extracted archives and CSVs converted from NMEA and CTD files
  - `./.oceanstream_staging/<campaign>/`: local STAC, tiles and heatmaps for cloud output
  - `~/.oceanstream/campaigns/<campaign>/campaign.json`: campaign registry (`OCEANSTREAM_CAMPAIGNS_DIR` moves it)
  - `~/.oceanstream/metadata/<campaign>.json`: SHA256 record of processed input files (`OCEANSTREAM_METADATA_DIR` moves it)
  - `~/.oceanstream/storage.json`: cloud credentials saved by `oceanstream configure`
- `python -m oceanstream.cli <command> --help` lists every option; this file covers the common ones.

---

## CLI Commands

### Process Geotrack Data (CSV, NMEA, CTD, archives → Parquet)

```bash
# Saildrone CSVs. Give --provider for a directory: auto-detection only reads a single file.
oceanstream process geotrack convert \
    --input-source ./data/saildrone_csvs \
    --output-dir ./output \
    --campaign-id TPOS_2023 \
    --provider noaa_pmel \
    --yes -v

# R2R cruise archive. Without --campaign-id the cruise id comes from the archive's metadata.
oceanstream process geotrack convert \
    --input-source ./RR2402_615519_r2rnav.tar.gz \
    --output-dir ./output \
    --provider r2r \
    --yes

# NMEA log, one fix per 10 s, from GGA and RMC sentences only (GGA,RMC,GNS,VTG,ZDA by default)
oceanstream process geotrack convert \
    --input-source ./RR2401_gnss_gp170_aft-2024-02-17.txt \
    --output-dir ./output --campaign-id RR2401 \
    --nmea-sentence-types GGA,RMC --nmea-sampling-interval 10 --yes

# Analyse without writing (rows, lat/lon range, bin counts)
oceanstream process geotrack convert --dry-run --input-source ./data --provider noaa_pmel -v

# Columns found in the input, and the output schema
oceanstream process geotrack convert --list-columns --input-source ./data --provider noaa_pmel
oceanstream process geotrack convert --print-schema --input-source ./data --provider noaa_pmel
```

`--list-columns`, `--print-schema` and `--provider-metadata` stop before writing, but like a conversion they refuse files already in the campaign's SHA256 record; `--dry-run` skips that check.

**Key Options**:
| Option | Description |
|--------|-------------|
| `--input-source` | File, directory (top level only) or archive (searched recursively). Default `raw_data` |
| `--output-dir` | Base output path, local or `az://`, `s3://`, `gs://`. The campaign folder is created inside it unless the path already ends with the campaign id. Default `out/geoparquet`, or the `--output-dir` given to `campaign create` |
| `--campaign-id` | Campaign folder name and `campaign_id` column. Without it: the R2R cruise id from archive metadata, else the platform id from the file name (`sd1030_tpos_2023` for `sd1030_tpos_2023_7ef2_e8f7_98f9.csv`) |
| `--provider` | `noaa_pmel` (alias `saildrone`), `r2r`, `generic`, `cmems`, `emodnet`, `emso`, `norsoop`, `oceanlab`, `ooi`, `plocan`. Auto-detected when `--input-source` is one file; a directory gets `generic` |
| `--platform-id` | Override the platform id parsed from file names |
| `--yes`, `-y` | Skip the confirmation prompt |
| `--dry-run` | Analyse only |
| `--force-reprocess` | Clear the SHA256 record and replace the campaign's partitions with this run's data (see Incremental Processing) |
| `--fill-positions-max-gap` | Minutes. Rows with measurements and no position get a position interpolated between fixes at most this far apart, flagged in `position_interpolated`. 0 (default) drops them |
| `--attribution`, `--creation-date`, `--source-dataset`, `--source-repository` | Provenance overrides |
| `--use-cloud-storage` | Write under the active provider in `~/.oceanstream/storage.json` |
| `-v` | Verbose output |

`oceanstream process --provider r2r geotrack convert ...` sets the provider for every `process` subcommand. When a directory holds files of several platforms and `--campaign-id` is missing, each detected campaign is converted into its own folder, and that path drops `--generate-heatmaps`, the arrow options and `--fill-positions-max-gap`.

### Process Echosounder Data (EK60/EK80 → Zarr)

```bash
# Raw files -> ./out/SE2204/echodata/raw/<file>.zarr (no --campaign-id: straight into --output-dir)
oceanstream process echodata convert \
    --input-source ./raw_data/ek80 \
    --output-dir ./out \
    --campaign-id SE2204 \
    --sonar-model EK80

# Saildrone calibration spreadsheet baked into the converted files
oceanstream process echodata convert \
    --input-source ./raw_data/ek80 --output-dir ./out --campaign-id TPOS_2023 \
    --calibration-file ./calibration_values.xlsx

# Sv. Give -o: later steps look for *_Sv.zarr
oceanstream process echodata compute-sv --input-source ./out/SE2204/echodata/raw -o ./out/SE2204/echodata/sv

# Positions from a geotrack campaign (compute-nasc needs them); overwrites the input unless -o
oceanstream process echodata enrich-location --input-source ./out/SE2204/echodata/sv --campaign-dir ./output/SE2204

oceanstream process echodata denoise --input-source ./out/SE2204/echodata/sv -o ./out/SE2204/echodata/denoised \
    --methods background,transient,impulse,attenuation
oceanstream process echodata compute-mvbs --input-source ./out/SE2204/echodata/denoised -o ./out/SE2204/echodata/mvbs \
    --range-bin 1m --ping-time-bin 5s
oceanstream process echodata compute-nasc --input-source ./out/SE2204/echodata/sv -o ./out/SE2204/echodata/nasc \
    --range-bin 10m --dist-bin 0.5nmi --transducer-depth 0.6
oceanstream process echodata plot --input-source ./out/SE2204/echodata/sv -o ./out/SE2204/echodata/echograms \
    --vmin -80 --vmax -50
```

- `--calibration-file` reads the Saildrone `.xlsx` format. An Echoview `.ecs` file raises an error after the conversion has run; in Python, `oceanstream.echodata.calibrate.ecs.build_cal_params_from_ecs()` handles `.ecs`.
- `convert --compute-sv` is accepted and ignored: run `compute-sv`. `--enrich-environment` does nothing without `--geoparquet-dir`; with it, each converted store gets per-ping `temperature`, `salinity` and `sound_speed` in `Environment` from the campaign's `TEMP_SBE37_MEAN`/`SAL_SBE37_MEAN`, and positions in `Platform`. The store is rewritten in place; `compute-sv` then uses that sound speed.
- `compute-sv` without `-o` writes `<stem>.Sv.zarr` beside the input; the later steps then miss the `*_Sv.zarr` pattern and take every `*.zarr` in the folder, raw stores included.
- No CLI command runs the whole chain. In Python, `EchodataProcessor.run()` does (below). `oceanstream hpc` runs `scripts/batch_processing/process_from_raw.py`, a separate pipeline script, one day per job on a Slurm cluster over SSH.
- `process echodata presets list|show|match|check|path`: the library of denoise parameter presets the HPC pipeline uses.
- Echodata processing needs the `echodata` extra, which installs the echopype fork.

### Multibeam and ADCP

```bash
# Kongsberg .all files (searched recursively; unzip .gz first) -> per-day soundings, grids, quick-looks
oceanstream process multibeam --input-dir ./em304 --output-dir ./out/multibeam --resolution 1.0 -v

# Combine the day grids into grids/campaign, then render PMTiles (WebP) into ./out/multibeam/tiles
oceanstream process multibeam --output-dir ./out/multibeam --mosaic --tiles

# RDI ADCP .raw -> <stem>_processed.nc (beam-to-earth, 120 s ensembles, transducer at 7 m)
oceanstream process adcp --input-dir ./raw_adcp --output-dir ./out/adcp -v
```

Multibeam output: `grid.json`, `soundings/<day>.parquet`, `grids/days/<day>/<var>/*.tif` with a `<var>.vrt`, `grids/campaign/`, `quicklooks/`, `summaries/<day|campaign>.json`. Depths are below the water line at each ping, with no tide reduction. Grids use the data's UTM zone unless `--epsg` is given.

### Campaign Management

```bash
# Create campaign with platforms ("id:name:type"; name and type optional)
oceanstream campaign create TPOS_2023 \
    --platform "sd1030:Saildrone 1030:Saildrone Explorer" \
    --platform "sd1033:Saildrone 1033:Saildrone Explorer" \
    --description "TPOS 2023 multi-USV deployment"

# A default output path: later `convert --campaign-id FK161229` without --output-dir writes there
oceanstream campaign create FK161229 \
    --platform "falkor:R/V Falkor:Research Vessel" --output-dir ./output

oceanstream campaign list
oceanstream campaign show TPOS_2023
oceanstream campaign inspect TPOS_2023 --output-dir ./output -n 5   # first rows, STAC and PMTiles found
oceanstream campaign delete TPOS_2023 --yes   # registry entry only: data and SHA256 record stay

# Report on a converted campaign (Markdown or JSON)
oceanstream process geotrack report ./output/TPOS_2023 -f json -o report.json
oceanstream process geotrack report --campaign-id TPOS_2023 -o report.md
```

`convert` also registers the campaign it writes. Each conversion refreshes `campaign.json` with what it learns from the data (output folder, dates, extent, row and file counts, platforms, sensors) and keeps the other fields, such as the description and licence given to `campaign create`. Other `campaign` commands:
- `campaign argo <id>`: Argo float profiles inside the campaign's bbox and dates, fetched from Argovis into one GeoParquet file (`--bbox`, `--start-date`, `--end-date`, `--buffer-km`, `--buffer-days`, `--track`, `--layout profiles|levels`).
- `campaign metadata|days|gps|mirror <id>`: prepare a campaign for the HPC echodata pipeline (catalogue metadata, days to process, a GPS parquet from ERDDAP or a published track, a copy of the raw files in the project bucket).

### Utility Commands

```bash
# List available data providers
oceanstream providers

# Use a config file; --config-file goes before the subcommand
oceanstream --config-file ./my-config.toml process geotrack convert ...

# Interactive: local or Azure storage, saved encrypted in ~/.oceanstream/storage.json
oceanstream configure
```

---

## Python Library API

### Geotrack Processing

```python
from pathlib import Path
from oceanstream.geotrack import convert
from oceanstream.providers import get_provider

convert(
    provider=get_provider("noaa_pmel"),   # a provider object from get_provider()
    input_source=Path("./data/saildrone"),
    output_dir=Path("./output"),
    campaign_id="TPOS_2023",
    yes=True,          # without it convert() waits on input()
    verbose=True,
)
# Returns None. Output: ./output/TPOS_2023/. It prints a report; read the files for results.
```

`provider` is required and must be a provider object: a name string makes every file fail to read (`No usable data after per-file processing`). Every CLI option is a keyword argument (`generate_pmtiles`, `generate_heatmaps`, `dry_run`, `force_reprocess`, `platform_id`, `fill_positions_max_gap_minutes`, `use_cloud_storage`, ...). `from oceanstream import convert, get_provider` also works.

### Using the Processor Class

```python
from pathlib import Path
from oceanstream.geotrack.processor import GeotrackProcessor
from oceanstream.providers import get_provider

processor = GeotrackProcessor(get_provider("noaa_pmel"), campaign_id="TPOS_2023", verbose=True)

files = processor.scan_input_source(Path("./data"))   # extracts archives, converts NMEA/CTD to CSV
df = processor.process_files(files)                    # one DataFrame; nothing is written

print(df.columns.tolist()[:5])
print(f"Time range: {df['time'].min()} to {df['time'].max()}")   # tz-aware UTC
```

### Echodata Processing

```python
from pathlib import Path
from oceanstream.echodata.processor import EchodataProcessor
from oceanstream.echodata.config import EchodataConfig

config = EchodataConfig(sonar_model="EK80", parallel=True, n_workers=4)
# or EchodataConfig.from_toml(Path("oceanstream.toml")) to read its [echodata] table

processor = EchodataProcessor(config=config, campaign_id="TPOS_2023", verbose=True)
result = processor.run(
    input_dir=Path("./raw_data/ek80"),
    output_dir=Path("./output"),          # products go to ./output/TPOS_2023/echodata/
    geoparquet_dir=None,                  # campaign Parquet with CTD data for sound speed/absorption
    calibration_file=None,
)

if result.success:
    print(f"Pipeline completed in {result.total_duration:.1f}s")
for step in result.steps:
    print(f"  {step.step}: {step.success} {step.message}")
```

`run()` also takes `skip_concatenation`, `skip_denoising`, `skip_seabed`, `skip_shoals`, `skip_mvbs`, `skip_nasc`, `skip_echograms` and `save_intermediate`.

### Working with Providers

```python
from pathlib import Path
import pandas as pd
from oceanstream.providers import get_provider, list_providers, detect_provider

print(list_providers())
# ['cmems', 'emodnet', 'emso', 'generic', 'noaa_pmel', 'norsoop', 'oceanlab', 'ooi', 'plocan', 'r2r']

provider = get_provider("saildrone")       # hidden alias of noaa_pmel
print(provider.name)                       # "noaa_pmel"

# Platform (and default campaign) id from a file name
print(provider.identify_platform("sd1030_tpos_2023_7ef2_e8f7_98f9.csv"))   # "sd1030_tpos_2023"

# Score every provider against a file's header and name
print(detect_provider(Path("./data/sd1030_tpos_2023_7ef2_e8f7_98f9.csv")).name)   # "noaa_pmel"

# Saildrone CSVs from ERDDAP have a units row under the header
raw_df = pd.read_csv("./data/sd1030_tpos_2023_7ef2_e8f7_98f9.csv", skiprows=[1])
df = provider.enrich_dataframe(raw_df)     # parses `time` to UTC datetimes

aliases = provider.alias_mapping(raw_df.columns)
print(aliases["TEMP_SBE37_MEAN"], aliases["SOG"])
# water_temperature_ctd_mean_c speed_over_ground_ms
```

### STAC Metadata Generation

```python
from pathlib import Path
from oceanstream.stac import emit_stac_collection_and_item

collection_path, item_paths = emit_stac_collection_and_item(
    Path("./output/TPOS_2023"),    # campaign folder holding the partitions
    df,                            # the converted DataFrame; sets the extent
    None,                          # semantic metadata dict, or None
    provider_name="noaa_pmel",
    collection_id="tpos-2023",     # default "oceanstream-<provider_name>-geoparquet"
    instruments=None,              # list of oceanstream.sensors.Sensor
    platforms=None,                # list of platform dicts
)

# Output structure:
# ./output/TPOS_2023/stac/
# ├── collection.json
# └── items/
#     ├── item-0.json    # one Item per lat/lon partition, its Parquet files as assets
#     └── item-1.json
```

`convert` writes the same files itself only when `SEMANTIC_ENABLE` and `SEMANTIC_GENERATE_STAC` are both true. Its collection id is `oceanstream-<provider>-geoparquet` (for example `oceanstream-noaa_pmel-geoparquet`), so it names the provider and not the campaign.

### Semantic Mapping (CF Standard Names)

```python
from oceanstream.semantic.semantic import SemanticMapper, SemanticConfig

mapper = SemanticMapper(SemanticConfig(rename_columns=False))
result = mapper.apply(df)           # numeric columns only

print(result.canonical_mapping["TEMP_SBE37_MEAN"])   # 'sea_water_temperature'
print(result.cf_mapping["SAL_SBE37_MEAN"])
# {'cf_standard_name': 'sea_water_practical_salinity', 'confidence': 1.0}
print(result.units["sea_water_temperature"])         # 'degC'
result.dataframe                    # a copy, renamed to canonical names when rename_columns=True
```

Units come from name heuristics (`degC`, `1e-3`, `m s-1`, `m`, else `None`). `SemanticConfig.min_confidence` is not applied by `apply()`.

---

## Output Structure

### Geotrack Campaign Output

```
<output-dir>/
├── TPOS_2023/                               # campaign folder
│   ├── lat_bin=lat_20_22/
│   │   ├── lon_bin=lon_-158_-156/
│   │   │   └── 7f2535abd5e549d8ada04a04a747664c-0.parquet
│   │   └── lon_bin=lon_-156_-154/
│   │       └── 7f2535abd5e549d8ada04a04a747664c-0.parquet
│   ├── stac/                                # only with SEMANTIC_ENABLE and SEMANTIC_GENERATE_STAC
│   │   ├── collection.json
│   │   └── items/item-0.json, item-1.json
│   ├── heatmaps/                            # convert --generate-heatmaps
│   │   ├── temp_sbe37_mean.tif
│   │   └── manifest.json
│   └── tiles/
│       └── track.pmtiles                    # convert --generate-pmtiles
```

- Bins are 2°×2° with edges on even degrees, covering the data extent plus a 1° margin. When the track overlaps the band from 15°S to 15°N, the bins stop at ±15° and rows further out go into the outermost bin.
- Partition values are strings such as `lat_-46_-44` and `lon_170_172`. Parquet file names are pyarrow UUIDs.
- Columns: the columns as the provider reads them, `platform_id`, `campaign_id`, `position_interpolated` (with `--fill-positions-max-gap`), `geometry` (WKB points; null where a row has no position), and the `lat_bin`/`lon_bin` partition columns.
- Schema metadata keys: `geo` (GeoParquet 1.1), `oceanstream:provider`, `oceanstream:aliases` (original → canonical column names), `oceanstream:units` when the provider or semantic mapping knows units, and with semantic mapping on `oceanstream:cf_standard_names` and `oceanstream:semantic_version`.
- The SHA256 record of processed files is `~/.oceanstream/metadata/<campaign>.json`; the output folder holds no tracking file.
- The collection's `pmtiles` asset points to `../tiles/track.pmtiles`, the file inside the campaign folder; cloud output uploads it to `<campaign>/tiles/`. Each campaign has its own, also when several share an output folder.

### Reading Output Data

```python
import pandas as pd
import geopandas as gpd

# Skip stac/, tiles/ and heatmaps/: without this, pyarrow tries to read their JSON, PMTiles and TIFF files and fails
SKIP = [".", "_", "stac", "tiles", "heatmaps"]
df = pd.read_parquet("./output/TPOS_2023", ignore_prefixes=SKIP)

# Partition filters compare strings
df_bin = pd.read_parquet(
    "./output/TPOS_2023",
    ignore_prefixes=SKIP,
    filters=[("lat_bin", "==", "lat_20_22"), ("lon_bin", "in", ["lon_-158_-156"])],
)

# Area filter on the coordinates
df_box = df[df["latitude"].between(20.5, 21.0) & df["longitude"].between(-157.0, -155.0)]

# GeoDataFrame with the point geometry (CRS84)
gdf = gpd.read_parquet("./output/TPOS_2023", ignore_prefixes=SKIP)

# Output written before the geometry column was added: build the points from the coordinates
gdf = gpd.GeoDataFrame(
    df, geometry=gpd.points_from_xy(df["longitude"], df["latitude"]), crs="EPSG:4326"
)
```

DuckDB: `SELECT * FROM read_parquet('output/TPOS_2023/**/*.parquet', hive_partitioning = true)`.

---

## PMTiles Generation

OceanStream builds a PMTiles file of the track with segments, day markers and direction arrows. It needs `tippecanoe` and `pmtiles` on `PATH`; when either is missing, `convert` leaves the PMTiles section out of its report (the reason is printed only with `-v`) and `tiles` exits with `PMTiles generation failed`.

### CLI Usage

```bash
# During conversion: writes ./output/TPOS_2023/tiles/track.pmtiles
oceanstream process geotrack convert --input-source ./data --output-dir ./output \
    --campaign-id TPOS_2023 --provider noaa_pmel --generate-pmtiles --yes -v

# From an existing campaign folder: writes ./output/TPOS_2023/tiles/track.pmtiles,
# or <--output-dir>/track.pmtiles when given
oceanstream process geotrack tiles --geoparquet-dir ./output/TPOS_2023 -v
```

Tiles from earlier versions sit in `<output-dir>/tiles/track.pmtiles`, one file shared by every campaign in that folder; regenerate them with `tiles` to give each campaign its own.

### PMTiles Options

| `convert` option | `tiles` option | Description | Default |
|------------------|----------------|-------------|---------|
| `--generate-pmtiles` | | Enable tile generation | off |
| `--pmtiles-minzoom` | `--minzoom` | Minimum zoom | `0` |
| `--pmtiles-maxzoom` | `--maxzoom` | Maximum zoom | chosen by tippecanoe (`-zg`) from the track density |
| `--pmtiles-layer` | `--layer-name` | Layer name | `track` |
| `--pmtiles-sample-rate` | `--sample-rate` | Keep every Nth point | `5` |
| `--pmtiles-time-gap` | `--time-gap-minutes` | Gap in minutes that splits segments | `60` |
| `--[no-]pmtiles-include-measurements` | `--[no-]include-measurements` | Averaged measurements on segments | on |
| `--pmtiles-measurement-columns` | `--measurement-columns` | Columns to average (repeat the flag) | auto |
| `--pmtiles-exclude-patterns` | | Regexes left out of auto-discovery | `_STDDEV`, `_STD`, `_MIN`, `_MAX`, `_PEAK`, wind components, wing sensors, raw `HDG`/`SOG`/`COG` |
| `--[no-]pmtiles-include-arrows` | `--[no-]include-arrows` | Direction arrows | on |
| `--pmtiles-arrows-per-segment` | `--arrows-per-segment` | Arrows per segment | `3` |
| `--pmtiles-arrow-min-points` | `--arrow-min-points` | Minimum segment points for arrows | `5` |

### Tile Feature Types

One layer (`track` by default) holds three kinds of feature:

**1. Track Segments (LineString)**: no `type` property; measurement columns are segment averages
```json
{"segment_id": 0, "points": 397, "sample_rate": 5, "time_gap_min": 60,
 "t_start": "2023-11-06T00:00:00+00:00", "t_end": "2023-11-07T09:00:00+00:00",
 "day": "2023-11-06", "platform_id": "sd1030_tpos_2023",
 "TEMP_SBE37_MEAN": 26.581, "SAL_SBE37_MEAN": 34.828}
```

**2. Day Markers (Point)**: a `start` and an `end` point per UTC day and platform
```json
{"day": "2023-11-06", "kind": "start", "t": "2023-11-06T00:00:00+00:00", "platform_id": "sd1030_tpos_2023"}
```

**3. Direction Arrows (Point)**
```json
{"type": "arrow", "bearing": 303.1, "day": "2023-11-06", "platform_id": "sd1030_tpos_2023",
 "t": "2023-11-06T08:15:00+00:00"}
```

### Python API

```python
from oceanstream.geotrack.tiling import (
    generate_pmtiles_from_geoparquet,
    calculate_bearing,
)

# Generate PMTiles from an existing campaign folder
pmtiles_path = generate_pmtiles_from_geoparquet(
    "./output/TPOS_2023",               # geoparquet_root
    "./output/TPOS_2023/tiles/track.pmtiles",  # pmtiles_path: the file to write
    minzoom=0,
    layer_name="track",
    sample_rate=5,
    time_gap_minutes=60,
    include_measurements=True,
    include_arrows=True,
    arrows_per_segment=3,
    platform_id=None,                   # used when the data has no platform_id column
    tippecanoe_opts=None,
)

# Bearing between (lon, lat) points
bearing = calculate_bearing((-122.0, 37.0), (-122.1, 37.1))
print(f"Bearing: {bearing:.1f}°")  # 321.4° (northwest)
```

`maxzoom=None` (the default) passes `-zg`, letting tippecanoe choose the maximum zoom; a number passes `-z`. `tippecanoe_opts` replaces the default `--drop-densest-as-needed --no-tile-size-limit --read-parallel`. A missing executable raises `MissingDependencyError`. `keep_intermediate_files=True` keeps the NDJSON and MBTiles beside the output.

### Using PMTiles in Web Maps

```typescript
// MapLibre GL JS with the pmtiles package
import maplibregl from 'maplibre-gl';
import { Protocol } from 'pmtiles';

const protocol = new Protocol();
maplibregl.addProtocol('pmtiles', protocol.tile);

map.addSource('tracks', {
  type: 'vector',
  url: 'pmtiles://https://storage.example.com/tiles/track.pmtiles',
});

// Track segments: the line layer draws only the LineStrings
map.addLayer({
  id: 'track-lines',
  type: 'line',
  source: 'tracks',
  'source-layer': 'track',
  filter: ['!', ['has', 'type']],
  paint: { 'line-color': '#0066cc', 'line-width': 2 },
});

// Day start/end markers: filter ['has', 'kind']
// Direction arrows (add an image named 'arrow' with map.addImage first)
map.addLayer({
  id: 'track-arrows',
  type: 'symbol',
  source: 'tracks',
  'source-layer': 'track',
  filter: ['==', ['get', 'type'], 'arrow'],
  layout: {
    'icon-image': 'arrow',
    'icon-rotate': ['get', 'bearing'],
    'icon-rotation-alignment': 'map',
  },
});
```

---

## Heatmap Generation (COG Rasters)

OceanStream interpolates measurements onto a regular grid with SciPy's `griddata` and writes one Cloud Optimized GeoTIFF per variable, plus a `manifest.json`.

### CLI Usage

```bash
# During conversion: writes ./output/TPOS_2023/heatmaps/
oceanstream process geotrack convert --input-source ./data --output-dir ./output \
    --campaign-id TPOS_2023 --provider noaa_pmel --generate-heatmaps --yes -v

# From an existing campaign folder: writes ./output/heatmaps/ (<geoparquet-dir>/../heatmaps)
oceanstream process geotrack heatmaps --geoparquet-dir ./output/TPOS_2023 \
    --variable TEMP_SBE37_MEAN --variable SAL_SBE37_MEAN -v

# Custom resolution and interpolation
oceanstream process geotrack heatmaps --geoparquet-dir ./output/TPOS_2023 \
    --output-dir ./output/heatmaps-coarse \
    --resolution 0.1 --method cubic --search-radius 1.0 --max-variables 5
```

### Heatmap Options

| `convert` option | `heatmaps` option | Description | Default |
|------------------|-------------------|-------------|---------|
| `--generate-heatmaps` | | Enable heatmap generation | off |
| `--heatmap-resolution` | `--resolution` | Grid resolution in degrees (0.05 ≈ 5.6 km) | `0.05` |
| `--heatmap-method` | `--method` | `linear`, `nearest` or `cubic` | `linear` |
| `--heatmap-search-radius` | `--search-radius` | Cells further than this (degrees) from data are no-data | `0.5` |
| `--heatmap-variables` | `--variable`, `-var` | Variables to grid (repeat the flag) | auto |
| `--heatmap-max-variables` | `--max-variables` | Maximum variables when auto-detecting | `10` |
| | `--sample-rate` | Keep every Nth point | `1` |
| | `--output-dir` | Output folder | `<geoparquet-dir>/../heatmaps` |

### Supported Variables

Auto-detection takes uppercase columns and columns ending in `_MEAN`, puts the variables below first, and stops at the maximum count. The variables below carry a label, unit and colormap in the manifest; other columns get their own name as label and `viridis`.

| Variable | Label | Unit | Colormap |
|----------|-------|------|----------|
| `TEMP_SBE37_MEAN` | Sea Temperature (CTD) | °C | thermal |
| `TEMP_AIR_MEAN` | Air Temperature | °C | thermal |
| `SAL_SBE37_MEAN` | Salinity | PSU | viridis |
| `CHLOR_WETLABS_MEAN` | Chlorophyll | µg/L | algae |
| `BARO_PRES_MEAN` | Barometric Pressure | hPa | coolwarm |
| `RH_MEAN` | Relative Humidity | % | Blues |
| `WIND_SPEED_MEAN` | Wind Speed | m/s | wind |

### Python API

```python
from pathlib import Path
from oceanstream.geotrack.raster import (
    generate_measurement_cog,
    generate_all_heatmaps,
)

# One variable
cog_path = generate_measurement_cog(
    Path("./output/TPOS_2023"),
    Path("./output/heatmaps/temp_sbe37_mean.tif"),
    "TEMP_SBE37_MEAN",
    resolution_deg=0.05,
    method="linear",
    search_radius_deg=0.5,
)

# All detected variables, plus manifest.json
manifest = generate_all_heatmaps(
    geoparquet_root=Path("./output/TPOS_2023"),
    output_dir=Path("./output/heatmaps"),
    variables=None,  # auto-detect
    resolution_deg=0.05,
    max_variables=10,
)

print(f"Generated {len(manifest['variables'])} heatmaps")
```

### Manifest File

```json
{"variables": [
  {"name": "TEMP_SBE37_MEAN", "file": "temp_sbe37_mean.tif", "label": "Sea Temperature (CTD)",
   "unit": "°C", "colormap": "thermal", "min": 26.46, "max": 27.112}
]}
```

COG files support HTTP range requests, so web map clients such as the ArcGIS `ImageryTileLayer` can load them by URL.

### Dependencies

`pip install scipy rasterio`. COG conversion uses `gdal_translate` (`brew install gdal`, `apt-get install gdal-bin`); without it each file stays a plain GeoTIFF named `<variable>.tmp.tif`. No-data cells hold `-9999`.

---

### Echodata Output

`EchodataProcessor.run()` writes:

```
<output_dir>/<campaign_id>/echodata/   # straight into <output_dir> without a campaign id
├── raw/                  # EchoData Zarr, one store per .raw file
├── daily/                # files concatenated by day (when there are several)
├── sv/                   # Volume backscattering strength
├── denoised/             # Denoised Sv
├── seabed_masked/
├── shoal_masked/
├── mvbs/                 # Mean volume backscattering strength
├── nasc/                 # NASC
├── echograms/            # PNG
└── pipeline_report.json  # steps, durations, output paths
```

`process echodata convert --campaign-id <id>` writes `<output-dir>/<id>/echodata/raw/`. Neither writes STAC.

---

## Configuration

### oceanstream.toml

Read from the current directory, or from `--config-file`:

```toml
[paths]
metadata_dir = "~/.oceanstream"   # SHA256 records go to <metadata_dir>/metadata/

[semantic]
enable = true            # default false; STAC needs it
generate_stac = true     # default true
cf_table = ""            # custom CF standard-name table (JSON)
alias_table = ""         # custom alias table (JSON)
```

- Environment variables override the TOML, which overrides the defaults.
- No code reads `paths.output_dir`, `[campaigns]`, `[processing]` or `[storage]`, although `oceanstream.toml.example` shows them. The campaign registry location comes only from `OCEANSTREAM_CAMPAIGNS_DIR`.
- `[echodata]` (`sonar_model`, `parallel`, `n_workers`, and `denoise`, `mvbs`, `nasc` sub-tables) is read only by an explicit `EchodataConfig.from_toml(path)` call. The CLI and `EchodataProcessor` do not call it.
- `Settings` reads all values once, when `oceanstream.config.settings` is first imported (calling `convert` does this). Set environment variables before that point in a Python process.
- On import, `oceanstream/config/settings.py` calls python-dotenv's `load_dotenv()`. It loads the first `.env` found in the installed package's folder or a folder above it (the working directory in a REPL or notebook). Variables already set in the environment win. A venv inside a project, or an editable install, therefore picks up the project's `.env`.

### Environment Variables

| Variable | Purpose | Default |
|----------|---------|---------|
| `SEMANTIC_ENABLE` | Semantic mapping; also gates STAC | `false` |
| `SEMANTIC_GENERATE_STAC` | Write STAC; ignored unless `SEMANTIC_ENABLE` is true | `true` |
| `SEMANTIC_MIN_CONFIDENCE` | Read into `Settings`; nothing filters on it | `0.7` |
| `SEMANTIC_CF_TABLE`, `SEMANTIC_ALIAS_TABLE` | Paths of custom semantic tables | built-in |
| `OCEANSTREAM_METADATA_DIR` | Folder of the SHA256 records (used as given) | `~/.oceanstream/metadata` |
| `OCEANSTREAM_CAMPAIGNS_DIR` | Campaign registry folder | `~/.oceanstream/campaigns` |
| `AZURE_CONNECTION_STRING`, or `AZURE_STORAGE_ACCOUNT` + `AZURE_STORAGE_KEY` | Credentials for `az://` output when `storage.json` has none | unset |

Boolean variables accept `1`, `true` or `yes`.

---

## Cloud Storage

Geotrack output goes straight to cloud storage through `pyarrow.fs`; no fsspec package is needed:

```bash
# Azure Blob Storage (az:// or abfs://)
export AZURE_CONNECTION_STRING="DefaultEndpointsProtocol=https;AccountName=...;AccountKey=..."
oceanstream process geotrack convert --input-source ./data --provider noaa_pmel \
    --campaign-id TPOS_2023 --yes --output-dir az://mycontainer/campaigns

# AWS S3, with pyarrow's default credentials (AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY, ~/.aws)
#   --output-dir s3://mybucket/campaigns
# Google Cloud Storage, with Application Default Credentials
#   --output-dir gs://mybucket/campaigns
```

- Azure credentials come from `oceanstream configure` (`~/.oceanstream/storage.json`) or the variables above. `configure` offers local and Azure storage only. `--use-cloud-storage` writes under the active configured provider, with `--output-dir` as the prefix.
- The campaign id is appended to the URI unless the URI already ends with it.
- STAC, PMTiles and heatmaps are built in `./.oceanstream_staging/<campaign>/` and uploaded to `<campaign>/stac/`, `<campaign>/tiles/track.pmtiles` and `<campaign>/heatmaps/`. Heatmaps read the local staging copy of the Parquet, which exists only when `--generate-pmtiles` is also given.
- Merging with earlier runs reads the local staging folder, so rows already in the bucket are not deduplicated. The SHA256 record still blocks files seen before.

---

## Common Patterns

### Batch Processing Multiple Campaigns

```python
from pathlib import Path
from oceanstream.geotrack import convert
from oceanstream.providers import get_provider

provider = get_provider("noaa_pmel")
campaigns = [
    ("TPOS_2023", "./data/tpos_2023"),
    ("ARCTIC_2024", "./data/arctic_2024"),
]

for campaign_id, input_path in campaigns:
    convert(
        provider=provider,
        input_source=Path(input_path),
        output_dir=Path("./output"),     # each campaign gets ./output/<campaign_id>/
        campaign_id=campaign_id,
        yes=True,
    )
```

### Incremental Processing (Append Mode)

```python
from pathlib import Path
from oceanstream.geotrack import convert
from oceanstream.providers import get_provider

provider = get_provider("noaa_pmel")

# First delivery
convert(provider=provider, input_source=Path("./data/batch1"),
        output_dir=Path("./output"), campaign_id="TPOS_2023", yes=True)

# Later delivery: pass only the new files, as a separate folder or a single file
convert(provider=provider, input_source=Path("./data/batch2"),
        output_dir=Path("./output"), campaign_id="TPOS_2023", yes=True)
```

- Before converting, `convert` checks every input file's name and SHA256 against `~/.oceanstream/metadata/<campaign>.json`. If any of them was processed before, it prints `The following files have already been processed` and returns without converting anything, new files in the same input included. Re-running on a folder after adding files to it converts nothing.
- When none of the input files is in the record, the new rows are merged with the existing partitions, rows with the same `time`, `latitude`, `longitude` and `trajectory` are dropped, and the partitions are rewritten.
- `--force-reprocess` (`force_reprocess=True`) clears the record and replaces the campaign's partitions with the data of this run alone. The campaign record is kept and refreshed.
- `--dry-run`, `--list-columns`, `--print-schema` and `--provider-metadata` leave existing partitions alone.

### Inspecting STAC Metadata

```python
import json
from pathlib import Path

collection = json.loads(Path("./output/TPOS_2023/stac/collection.json").read_text())

print(f"Collection: {collection['id']}")    # oceanstream-noaa_pmel-geoparquet
print(f"Time range: {collection['extent']['temporal']['interval']}")
print(f"Bbox: {collection['extent']['spatial']['bbox']}")
print(f"Platforms: {[p['platform_id'] for p in collection['summaries'].get('platforms', [])]}")
print(f"Sensors: {[s['name'] for s in collection['summaries'].get('instruments', [])]}")
```

`summaries` also holds `processing` (software version and date) and, when `--pmtiles-measurement-columns` names columns, `measurements` (min, max, mean, count).

---

## Troubleshooting

### Common Issues

**"No processable files found"**: the directory scan reads the top level only and takes `.csv`, `.geocsv`, NMEA `.txt`, `.hex` and `.tar.gz`/`.tgz`. An archive with none of these inside (an R2R fluorometer archive, for example) gives `No processable files found in 1 archive(s)`. `convert --dry-run -v` lists the files found and the provider chosen.

**"Unknown provider"**: `oceanstream providers` lists `cmems`, `emodnet`, `emso`, `generic`, `noaa_pmel`, `norsoop`, `oceanlab`, `ooi`, `plocan`, `r2r`.

**Nothing written, exit status 0**: see Running OceanStream From an Agent. For `already been processed`, pass only new files or rebuild the campaign; for `already exists with different output location`, use the registered output directory or `--force-reprocess`; for `campaign_id is required`, pass `--campaign-id`.

**Column mapping issues**: `convert --list-columns --input-source ./data --provider noaa_pmel` prints the columns as read.

**`ArrowInvalid: ... stac/collection.json ... Parquet magic bytes not found`**: read the campaign with `ignore_prefixes=[".", "_", "stac", "heatmaps"]` (see Reading Output Data).

**`ArrowNotImplementedError: Function 'greater_equal' has no kernel matching input types (string, ...)`**: partition values are strings; filter with `==` or `in` on values like `lat_20_22`.

**`ModuleNotFoundError: No module named 'geopandas'`**: the installed oceanstream predates 1.0.0rc1, where geopandas became a core dependency. Run `pip install "oceanstream==1.0.0rc1"`.

**Echodata dependency error**: `pip install "oceanstream[echodata]==1.0.0rc1"` installs `oceanstream-echopype`, the OceanStreamIO fork of echopype. Run `pip uninstall echopype` first if upstream echopype is installed: both provide the `echopype` module. Check with `python -c "from oceanstream.echodata import EchodataProcessor"`.

### Debug Mode

```bash
# Processed-file tracking and the campaign registry
cat ~/.oceanstream/metadata/TPOS_2023.json
cat ~/.oceanstream/campaigns/TPOS_2023/campaign.json
```

---

## Quick Reference

| Task | Command/Code |
|------|--------------|
| Install | `pip install "oceanstream==1.0.0rc1"` |
| Convert CSV → Parquet | `oceanstream process geotrack convert --input-source ./data --output-dir ./out --campaign-id ID --provider noaa_pmel --yes` |
| Dry run analysis | `oceanstream process geotrack convert --dry-run --input-source ./data --provider noaa_pmel -v` |
| Tiles from output | `oceanstream process geotrack tiles --geoparquet-dir ./out/ID` |
| Heatmaps from output | `oceanstream process geotrack heatmaps --geoparquet-dir ./out/ID` |
| List providers | `oceanstream providers` |
| Create campaign | `oceanstream campaign create CAMPAIGN_ID` |
| Python: convert | `convert(provider=get_provider("noaa_pmel"), input_source=..., output_dir=..., campaign_id=..., yes=True)` |
| Python: get provider | `from oceanstream.providers import get_provider` |
| Read output | `pd.read_parquet("./out/ID", ignore_prefixes=[".", "_", "stac", "heatmaps"])` |

---

*OceanStream 1.0.0rc1 (`main`, bb394fb) | [Source](https://github.com/OceanStreamIO/oceanstream-cli) | [Website](https://oceanstream.io)*
