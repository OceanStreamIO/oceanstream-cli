# Argo profiles for a campaign

`oceanstream campaign argo` collects the Argo float profiles that fall inside a
campaign's bounding box and dates and writes them to one GeoParquet file. Use it
as auxiliary temperature and salinity data when a cruise has no CTD casts, or to
put a survey in its oceanographic context.

The profiles come from [Argovis](https://argovis.colorado.edu) (API v2), which
serves the Argo GDAC holdings. Every measured variable is kept with its Argo QC
flags and data mode. Nothing is averaged or interpolated.

## Quick start

```bash
# Extent from a stored campaign (oceanstream campaign create ... --bbox --start-date --end-date)
oceanstream campaign argo FK161229

# Wider net, matched to the vessel track, only profiles within 150 km of it
oceanstream campaign argo FK161229 --buffer-km 50 --buffer-days 5 \
    --track out/geoparquet/FK161229 --max-track-km 150

# No stored campaign: give the extent; one row per depth level
oceanstream campaign argo IESSNS2021 --bbox=-31.01,62.69,-6.47,69.32 \
    --start-date 2021-07-05 --end-date 2021-07-26 --layout levels
```

A bare `--end-date` (`2021-07-26`) includes that whole day.
When the campaign exists in `~/.oceanstream/campaigns/`, the command records the
output path and counts under `argo_dataset` in `campaign.json`, and
`oceanstream campaign show` lists them.

| Option | Default | Effect |
|---|---|---|
| `-o, --output` | `./<campaign_id>_argo_<layout>.parquet` | File to write (local path) |
| `--bbox`, `--start-date`, `--end-date` | the campaign's | Override the extent |
| `--buffer-km`, `--buffer-days` | 0 | Widen the bbox and the time window |
| `--track` | none | Vessel track: GeoParquet file or dataset (geotrack output works as-is), or CSV with latitude, longitude and time |
| `--max-track-km` | none | Keep only profiles this close to the track (needs `--track`) |
| `--layout` | `profiles` | `profiles` or `levels` (see below) |
| `--qc` | `raw` | `raw` keeps values as published; `good` sets values with rejected QC flags to null |
| `--chunk-days` | 10 | Days per Argovis request |
| `--argovis-key` | `$ARGOVIS_API_KEY` | Argovis API key; requests work without one at a lower rate limit |
| `--dry-run` | | Query and print the summary, write nothing |

## Output

One GeoParquet 1.1 file of `Point` geometries in longitude/latitude (WGS 84),
encoded as WKB in the `geometry` column. The schema is the same for every run,
so files from several campaigns can be appended to one table.

### `profiles` layout (default)

One row per profile. Each variable is a list column indexed by depth level, with
a list of Argo QC flags beside it:

| Column | Type | Notes |
|---|---|---|
| `campaign_id` | string | The CLI argument |
| `profile_id` | string | Argovis id, `<wmo>_<cycle>` (a `D` suffix marks a descending profile) |
| `wmo_id`, `cycle_number`, `profile_direction` | string, int32, string | `A` ascending, `D` descending |
| `network` | string | `core`, `deep` or `bgc` |
| `time` | timestamp (UTC) | Profile time |
| `latitude`, `longitude` | double | Profile position |
| `position_qc`, `time_qc` | int8 | Argo QC flags of position and time |
| `data_mode` | string | Least processed mode of pressure, temperature and salinity: `R` real-time, `A` adjusted, `D` delayed-mode |
| `data_modes` | string (JSON) | Mode for each variable |
| `n_levels`, `pressure_min`, `pressure_max` | int32, double, double | |
| `variables` | list of string | Variables measured besides pressure |
| `vertical_sampling_scheme`, `source_url`, `source_updated` | string, string, timestamp | `source_url` is the GDAC NetCDF file |
| `nearest_track_km` | double | Distance to the closest vessel position (null without `--track`) |
| `nearest_track_time`, `nearest_track_lat`, `nearest_track_lon` | timestamp, double, double | That vessel position |
| `time_offset_hours` | double | Profile time minus vessel time; positive when the float surfaced after the vessel passed |
| `pressure`, `temperature`, `salinity` | list of double | Always present |
| `pressure_qc`, `temperature_qc`, `salinity_qc` | list of int8 | Argo QC flags |
| `<variable>`, `<variable>_qc` | list of double, list of int8 | One pair for every other variable found (BGC floats: `doxy`, `chla`, ...) |
| `geometry` | binary (WKB) | Point |

### `levels` layout

One row per profile and depth level, with scalar columns: the identity, time,
position and match-up columns above, plus `level` (index within the profile),
`pressure`, `pressure_qc` and a value and flag column for each variable. Use it
in tools that do not read list columns.

### QC

Argo QC flags 1 (good), 2 (probably good), 5 (value changed) and 8 (estimated)
are usually accepted; 3, 4 and 9 are not. With `--qc raw` the values are as
published and you filter them yourself. With `--qc good`, values with any other
flag are null, and a rejected pressure nulls the whole level (the `levels`
layout drops that row). The flag columns are kept in both modes.

### File metadata

The Parquet footer carries:

- `geo`: GeoParquet metadata, including the bounding box of the points.
- `oceanstream:argo`: the query (bbox, time window, buffers), retrieval time,
  counts by network and data mode, the track distance and time offset summary,
  the QC policy, and the Argo citation, DOI, licence and acknowledgement.
- `oceanstream:units`: unit of each variable as Argovis reports it.

```python
import json
import pyarrow.parquet as pq

meta = pq.read_schema("FK161229_argo_profiles.parquet").metadata
argo = json.loads(meta[b"oceanstream:argo"])
print(argo["n_profiles"], argo["retrieved_at"], argo["citation"])
```

## Using the file

### PostGIS

GDAL 3.5 or later reads GeoParquet. List columns become PostgreSQL arrays
(`float8[]` for values, `int2[]` for QC flags, `varchar[]` for `variables`) and
timestamps become `timestamptz`:

```bash
ogr2ogr -f PostgreSQL PG:"dbname=oceanstream host=localhost" \
    FK161229_argo_profiles.parquet -nln argo_profiles -lco GEOMETRY_NAME=geom

# Another campaign into the same table
ogr2ogr -f PostgreSQL PG:"dbname=oceanstream host=localhost" -append \
    IESSNS2021_argo_profiles.parquet -nln argo_profiles
```

### pandas and geopandas

```python
import geopandas as gpd
import pandas as pd

profiles = gpd.read_parquet("FK161229_argo_profiles.parquet")

# Long format from the profiles layout
levels = pd.read_parquet("FK161229_argo_profiles.parquet").explode(
    ["pressure", "pressure_qc", "temperature", "temperature_qc", "salinity", "salinity_qc"],
    ignore_index=True,
)
```

### Python API

```python
from datetime import datetime

from oceanstream.argo import build_argo_dataset, load_track

ds = build_argo_dataset(
    [-31.01, 62.69, -6.47, 69.32],
    datetime(2021, 7, 5), datetime(2021, 7, 27),
    track=load_track("out/geoparquet/IESSNS2021"),
    max_track_km=150,
    campaign_id="IESSNS2021",
)
ds.to_geoparquet("iessns2021_argo_profiles.parquet")
levels = ds.to_dataframe(layout="levels", qc="good")
```

`ds.profiles` holds the parsed profiles as dicts and `ds.provenance` holds the
metadata written to `oceanstream:argo`.

## Notes

- **Snapshot.** Argo data are reprocessed. Real-time profiles become
  delayed-mode, and values and flags change, so a later run can differ. The
  retrieval time is in the file metadata.
- **Time window.** Profiles are kept when their time falls inside the window,
  buffers included. A profile just outside the dates but next to the track is
  only included with `--buffer-days`.
- **Track matching.** The distance is to the closest recorded vessel position,
  great-circle. Tracks longer than 50,000 positions are thinned evenly first.
- **Large queries.** Requests go out in `--chunk-days` windows. When Argovis
  rejects one as too large, it is halved and retried.
- **Citation and licence.** Argo data are CC BY 4.0. Cite Argo (2000),
  https://doi.org/10.17882/42182, and include the Argo acknowledgement (its
  text is in the file metadata). When the data came through Argovis, also cite Tucker et al.
  (2020), https://doi.org/10.1175/JTECH-D-19-0041.1.
