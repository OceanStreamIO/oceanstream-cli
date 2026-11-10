# Multibeam processing

`oceanstream process multibeam` turns Kongsberg `.all` files into per-day soundings, bathymetry and backscatter grids, quick-looks and summaries, and combines the days into a campaign mosaic. The files are read with [themachinethatgoesping](https://github.com/themachinethatgoesping/themachinethatgoesping) (MPL-2.0), pinned at 0.37.1 because its API is still changing.

## Install

`pip install 'oceanstream[multibeam]'` installs themachinethatgoesping with all its dependencies, including PySide6 and other Qt viewer packages (about 1.7 GB in all). The readers do not need those, so EDITO Jobs install it without them:

```bash
pip install numpy scipy matplotlib rasterio pyproj tqdm
pip install --no-deps themachinethatgoesping==0.37.1 nanobind-backend pytimeparse2
```

## Use

```bash
oceanstream process multibeam --input-dir raw/2021-06-19 --output-dir out/NF2104 --day 2021-06-19 -v
oceanstream process multibeam --output-dir out/NF2104 --mosaic
```

- `--input-dir` is searched recursively for `.all` files. Gzipped files must be unzipped first: NCEI's `x.all.mb58.gz` becomes `x.all` (`.mb58` is MB-System's format number).
- `--day` keeps only pings of that UTC day; repeat it for several days. Without it, every day in the files is processed.
- `--resolution` (default 1 m) and `--tile-size` (default 1000 cells) set the grid; `--epsg` its CRS, by default the UTM zone of the data. The first run writes them to `grid.json`, and later runs into the same output directory must match.
- `--mosaic` combines the day grids already in `--output-dir` into `grids/campaign`.
- `--regrid` grids the soundings already in `--output-dir/soundings` again (every day, or those given with `--day`), without the `.all` files, for example after a change to the backscatter correction. The grid definition stays that of `grid.json`.
- Summaries record the oceanstream revision when the environment variable `OCEANSTREAM_REVISION` is set, as the EDITO Job does.
- The `.wcd` water-column files are not read yet.

## Outputs

| Path | Contents |
|---|---|
| `grid.json` | CRS, cell size and tile size shared by every grid |
| `soundings/<day>.parquet` | Every valid sounding: ping time, beam, latitude, longitude, easting, northing, depth, backscatter, beam angle, quality factor (zstd Parquet; CRS in the schema metadata) |
| `grids/days/<day>/<var>/E<easting>_N<northing>.tif` | Tiles of 1000 × 1000 cells as deflate-compressed Cloud-Optimized GeoTIFFs, named by their lower-left corner; `<var>.vrt` mosaics them |
| `grids/campaign/...` | The same, combined over the days |
| `quicklooks/<day or campaign>/{depth,backscatter}-NN.png` | Greyscale quick-looks, one per patch of touching tiles (a survey box, a transit), largest first, at most 12; `--mosaic` redraws the days' too |
| `summaries/<day or campaign>.json` | Counts, area, depth and backscatter percentiles, per-cell depth spread, the backscatter angular response and the software version |

The variables are `depth` (mean in each cell), `depth_std` (standard deviation of the soundings in the cell), `backscatter` (linear mean, in dB) and `count` (soundings). In the campaign mosaic, `depth_std` also includes the differences between days.

## Method

- **Soundings** come from each ping's XYZ88 datagram, valid detections only. Each sounding is placed from the vessel reference point's position at the ping, which the library interpolates from the position datagrams, and the heading in the XYZ88 datagram. The along- and across-track distances are rotated by the heading and applied on the WGS84 ellipsoid, then projected to the grid's CRS.
- **Depth** is below the water line at the ping: the XYZ88 depth below the transmit transducer plus the transducer depth the datagram records. The sound speed is as applied at sea. There is no tide reduction, so depths are not on a chart datum.
- **Backscatter** is the beam backscatter of the XYZ88 datagram. The median response per degree of signed beam angle over the day is removed, port and starboard separately, and the soundings are brought to the level of 30–50° on both sides. On NF2104's EM710, port read 4 dB lower than starboard at 30–50°; pooling the two sides left stripes between adjacent lines. A faint stripe can remain under the ship, where the response changes fastest with angle. Seabed-image samples (the `Y` datagram) and calibration are not used.

## Checked on

NF2104 (NOAA Ship *Nancy Foster*, EM710, North Carolina shelf, June 2021). On one file of 2021-06-19, the nadir depth below the transducer matched the EK60 seabed line of the published echosounder products to -0.04 m median (`ek-rawdata-candidates/multibeam-sidescan-test-data.md` in the EDITO workspace). The EDITO Job that runs whole cruises is `edito/multibeam/` in oceanstream-hpc-runs.
