# OceanStream

OceanStream converts raw oceanographic data from ships, uncrewed surface vehicles and moorings into analysis-ready datasets:

- **Tracks and sensors**: Saildrone CSVs, R2R cruise archives, NMEA logs, SeaBird CTD `.hex` files, and CSV or GeoCSV from CMEMS, EMODnet, EMSO, NorSOOP, OceanLab, OOI and PLOCAN become GeoParquet partitioned by latitude and longitude, with STAC metadata, PMTiles of the track and COG heatmaps.
- **Echosounders**: Simrad EK60 and EK80 `.raw` files become EchoData Zarr stores, then Sv, denoised Sv, MVBS, NASC and echograms.
- **Multibeam**: Kongsberg `.all` files become per-day soundings in Parquet, bathymetry and backscatter grids as Cloud Optimized GeoTIFFs, and PMTiles.
- **ADCP**: Teledyne RDI `.raw` files become ensemble-averaged velocity profiles in NetCDF.

It also keeps a registry of campaigns, collects Argo float profiles along a cruise track, and runs the echosounder pipeline on a Slurm cluster over SSH.

## Installation

Python 3.11 to 3.13. A plain install processes tracks and sensor data, including NMEA logs and SeaBird CTD files. Add extras for the other instruments:

```bash
pip install oceanstream
pip install "oceanstream[echodata,echodata-viz]"
```

Release candidates install only when named, for example `pip install "oceanstream==1.0.0rc1"`.

| Extra | Installs | Needed for |
|-------|----------|------------|
| `echodata` | oceanstream-echopype, xarray, dask, zarr, netCDF4, fsspec, s3fs, gsw, copernicusmarine and others | `process echodata` |
| `echodata-viz` | matplotlib, hvplot, datashader, holoviews, panel, bokeh | Echogram plots |
| `multibeam` | themachinethatgoesping (Python 3.12+), scipy, rasterio, pyproj, matplotlib, pmtiles | `process multibeam` |
| `adcp` | dolfyn, scipy, xarray | `process adcp` |
| `norsoop` | erddapy | NorSOOP FerryBox data |
| `all` | Every extra except `multibeam` | |

The `echodata` extra installs [oceanstream-echopype](https://pypi.org/project/oceanstream-echopype/), OceanStream's fork of [echopype](https://github.com/OSOceanAcoustics/echopype). It provides the same `echopype` module as upstream echopype and replaces it, so run `pip uninstall echopype` before installing the extra into an environment that has echopype.

PMTiles of tracks need the [tippecanoe](https://github.com/felt/tippecanoe) and [pmtiles](https://github.com/protomaps/go-pmtiles) executables on `PATH`. Heatmaps also need rasterio: `pip install rasterio`, or the `all` extra.

## Command line

```bash
# Saildrone CSVs -> ./out/TPOS_2023/ (GeoParquet partitions)
oceanstream process geotrack convert \
    --input-source ./raw/saildrone --output-dir ./out \
    --campaign-id TPOS_2023 --provider noaa_pmel --yes

# The same, with PMTiles of the track and heatmaps
oceanstream process geotrack convert \
    --input-source ./raw/saildrone --output-dir ./out \
    --campaign-id TPOS_2023 --provider noaa_pmel --yes \
    --generate-pmtiles --generate-heatmaps

# EK80 .raw files -> ./out/SE2204/echodata/raw/*.zarr, then Sv
oceanstream process echodata convert \
    --input-source ./raw/ek80 --output-dir ./out --campaign-id SE2204 --sonar-model EK80
oceanstream process echodata compute-sv \
    --input-source ./out/SE2204/echodata/raw -o ./out/SE2204/echodata/sv

# Kongsberg .all files -> soundings, grids and quick-looks
oceanstream process multibeam --input-dir ./raw/em304 --output-dir ./out/multibeam

# RDI ADCP .raw files -> NetCDF
oceanstream process adcp --input-dir ./raw/adcp --output-dir ./out/adcp

# Campaigns and providers
oceanstream campaign list
oceanstream providers
```

`oceanstream --help` and `--help` on each command list every option. The echosounder steps after Sv are `denoise`, `enrich-location`, `compute-mvbs`, `compute-nasc` and `plot`. `process echodata presets` lists the denoise presets.

## Python

```python
from pathlib import Path
from oceanstream.geotrack import convert
from oceanstream.providers import get_provider

convert(
    provider=get_provider("noaa_pmel"),
    input_source=Path("./raw/saildrone"),
    output_dir=Path("./out"),
    campaign_id="TPOS_2023",
    yes=True,  # skip the confirmation prompt
)
```

`oceanstream.echodata.EchodataProcessor` runs the whole echosounder chain, from conversion to echograms, in one call.

## Output

A geotrack campaign is written as:

```
out/TPOS_2023/
├── lat_bin=lat_20_22/lon_bin=lon_-158_-156/<uuid>-0.parquet
├── stac/          # collection.json and items, when semantic mapping is enabled
├── tiles/         # track.pmtiles, with --generate-pmtiles
└── heatmaps/      # COG rasters, with --generate-heatmaps
```

Partitions cover 2° × 2°. Each file has a WKB point `geometry` column and GeoParquet 1.1 metadata, so GIS tools read the positions directly:

```python
import geopandas as gpd

gdf = gpd.read_parquet("out/TPOS_2023", ignore_prefixes=[".", "_", "stac", "tiles", "heatmaps"])
```

```sql
-- DuckDB
SELECT * FROM read_parquet('out/TPOS_2023/**/*.parquet', hive_partitioning = true);
```

## Documentation

Guides in the repository's [docs folder](https://github.com/OceanStreamIO/oceanstream-cli/tree/main/docs):

- [Denoise presets](https://github.com/OceanStreamIO/oceanstream-cli/blob/main/docs/denoise-presets.md)
- [Echo integration](https://github.com/OceanStreamIO/oceanstream-cli/blob/main/docs/echo-integration.md)
- [Multibeam](https://github.com/OceanStreamIO/oceanstream-cli/blob/main/docs/multibeam.md)
- [Argo profiles](https://github.com/OceanStreamIO/oceanstream-cli/blob/main/docs/argo.md)
- [Running on a Slurm cluster](https://github.com/OceanStreamIO/oceanstream-cli/blob/main/docs/hpc.md) and [processing campaigns there](https://github.com/OceanStreamIO/oceanstream-cli/blob/main/docs/hpc-campaigns.md)

Report problems at [github.com/OceanStreamIO/oceanstream-cli/issues](https://github.com/OceanStreamIO/oceanstream-cli/issues).

## License

MIT. See [LICENSE](https://github.com/OceanStreamIO/oceanstream-cli/blob/main/LICENSE). The oceanstream-echopype dependency is licensed under Apache 2.0.
