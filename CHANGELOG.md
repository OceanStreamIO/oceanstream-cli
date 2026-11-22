# Changelog

## 1.0.0rc1 (2026-11-22)

Release candidate for 1.0.0. Changes since 0.1.1 (January 2026). Install it by naming the version: `pip install "oceanstream==1.0.0rc1"`.

### Installation

- Python 3.13 is supported (3.11 to 3.13).
- `pip install oceanstream` installs everything track and sensor processing needs: geopandas, shapely, pynmea2 and seabirdscientific are core dependencies. The `geotrack` and `geo` extras are removed. `pip install "oceanstream[geotrack]"` still installs the package, with a pip warning that the extra does not exist.
- seabirdscientific is held at 2.7.0 to 2.7.8. Later 2.7 and 2.8 releases pin NumPy 2.0 and SciPy 1.13, which have no Python 3.13 wheels, and 3.x changes the hex reader's return type.
- The `echodata` extra installs OceanStream's echopype fork, published as [oceanstream-echopype](https://pypi.org/project/oceanstream-echopype/) 0.11.1a3. It provides the `echopype` module, so uninstall upstream `echopype` first.
- The `adcp`, `multibeam` and `norsoop` extras install their readers. `multibeam` needs Python 3.12 or later.
- `oceanstream.__version__` reports the installed version.

### Tracks and sensors (`process geotrack`)

- New providers: CMEMS, EMODnet, EMSO, NorSOOP, OceanLab, OOI and PLOCAN. `oceanstream providers` lists them all.
- SeaBird CTD `.hex` files are converted with calibrated values.
- Output files carry a WKB point `geometry` column and GeoParquet 1.1 metadata (CRS84), so `geopandas.read_parquet` and GIS tools read the positions directly.
- PMTiles are written inside each campaign folder (`<campaign>/tiles/track.pmtiles`). Several campaigns in one output folder no longer share a file. `--pmtiles-maxzoom` and `tiles --output-dir` take effect; without a max zoom, tippecanoe chooses one from the track density.
- `--nmea-sentence-types GGA,RMC` keeps only the listed sentences.
- A conversion keeps the description, licence and other fields given to `campaign create`, and every later conversion updates `campaign.json`.
- `--force-reprocess` replaces a campaign's rows instead of appending duplicates. `--dry-run` leaves existing partitions in place.

### Echosounders (`process echodata`)

- EK60 files, EK80 complex and power data, and EK80 channels that ping in turn are converted and calibrated. Calibration comes from Saildrone `.xlsx` files, Echoview `.ecs` files (Python) or the raw files.
- `convert --enrich-environment --geoparquet-dir` adds per-ping temperature, salinity and sound speed from a geotrack campaign. Sound speed and absorption can also come from CTD casts.
- Denoising has a preset library (`process echodata presets`), Ryan et al.'s (2015) transient-noise detector and an experimental quantile background-noise estimator. `drop_noisy_pings(drop_threshold=0)` keeps pings with nothing removed.
- Seabed detection picks the leading edge of the seabed echo and masks every cell below the line.
- MVBS and NASC take their integration layer from the preset, count cells below the noise as zero, and export NASC as GeoParquet.
- Echograms close recording gaps and draw the seabed line.
- `EchodataProcessor.run()` runs the chain from raw files to echograms. Stores can be on local disk, S3 or Azure.

### Multibeam (`process multibeam`)

- New: Kongsberg `.all` files are gridded with themachinethatgoesping into per-day soundings (Parquet), bathymetry and backscatter grids (COG with VRT) and quick-looks. `--mosaic` combines the days, and `--tiles` renders the mosaic as PMTiles.

### ADCP (`process adcp`)

- New: Teledyne RDI `.raw` files become ensemble-averaged velocity profiles in NetCDF. In Python, Nortek AD2CP files are read and their echo amplitude converted to Sv.

### Campaigns

- `campaign argo` collects the Argo float profiles inside a campaign's extent into GeoParquet.
- `campaign create --from-catalogue` describes a cruise from a raw-data catalogue. `campaign metadata`, `days`, `gps` and `mirror` write its STAC and ingest metadata, list its days, build its GPS file and copy its raw files to a bucket.

### Slurm clusters (`oceanstream hpc`)

- New: runs the echosounder pipeline on a Slurm cluster over SSH. It stages raw data, submits days as job arrays or a whole cruise as one job, pushes the products to S3 and publishes STAC. See [docs/hpc.md](https://github.com/OceanStreamIO/oceanstream-cli/blob/main/docs/hpc.md).
