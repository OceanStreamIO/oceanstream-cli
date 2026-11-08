# Processing a catalogue cruise on the cluster

How a cruise from the raw-data catalogue (a CSV with one row per cruise: `cruise_id`, `platform_name`, `institution`, `dataset_doi`, `licence`, `download_url`, …) becomes a published, web-ready campaign. Each step is an `oceanstream` command; the campaign directory holds everything the steps need and record. The cluster side is described in [hpc.md](hpc.md).

```
oceanstream campaign create <ID> --from-catalogue cruises.csv [--catalogue-id "<cruise_id>"]
oceanstream campaign days <ID>                       # days.txt from the source listing
oceanstream campaign gps <ID> --from …               # only if the raw files carry no positions
oceanstream campaign mirror <ID> --from …            # only if the source is not S3
  … test two days on EDITO, then set hpc-run.toml …
oceanstream hpc run-cruise <ID>                      # stage, one whole-node job, push, publish
```

`--campaigns-dir DIR` (or `OCEANSTREAM_CAMPAIGNS_DIR`) puts the campaign directories somewhere other than `~/.oceanstream/campaigns`, e.g. next to the runs.

## 1. Create the campaign from the catalogue

`oceanstream campaign create SE2204 --from-catalogue cruises.csv` reads the row and writes into `<campaigns dir>/SE2204/`:

| File | What it is |
|---|---|
| `campaign.json` | The campaign (platform, dates, bbox, licence, DOI, people, contacts), with the whole catalogue row under `catalogue` |
| `stac-collection.json` | The descriptive fields of the published STAC Collection: title, description, licence (with a `license` link), keywords, providers (producer, host, OceanStream as processor), `sci:citation`, `sci:publications`, `sci:doi`, links (`cite-as` DOI, landing page, cruise report), and `oceanstream:platform` / `oceanstream:campaign` blocks |
| `oceanstream-ingest.json` | What the web app needs to ingest the campaign (below) |
| `summary.md` | The catalogue row, readable |
| `hpc-run.toml` | A starting run configuration (step 4), only if none exists |

The campaign id names the products' S3 container, so it may hold only letters, digits, `_`, `.` and `-`. Give `--catalogue-id` when the catalogue's `cruise_id` is not a valid id (e.g. `"Sailbuoy Echo PolarFront 2022"`). E-mail addresses stay in `campaign.json` and `summary.md`; nothing published carries them.

Corrections the catalogue lacks go in `metadata-overrides.json` in the campaign directory, merged last: `{"stac": {...}, "ingest": {...}, "notes": ["…"]}`. `oceanstream campaign metadata <ID>` rewrites the three metadata files after an override or a catalogue update (`--catalogue` re-reads the CSV; `--check` only validates `oceanstream-ingest.json`).

### What the web app needs (`oceanstream-ingest.json`)

The web app ingests a campaign with `POST /api/ingest/stac-collection` (`server/routes/ingest_stac.py`), which runs `server/scripts/import_stac_echodata.py` on the Collection. `oceanstream-ingest.json` collects every value it and the campaign page use:

| Field | Used for |
|---|---|
| `campaignSlug` | The campaign's id in the web app; must match `^[a-z0-9_]+$` (`<provider or repository>_<id>`) |
| `name` | The campaign name (else the Collection title) |
| `provider` | One of the web app's providers: `saildrone`, `r2r`, … or `other` |
| `collectionUrl` | The published `collection.json`; its prefix must be public-read |
| `martinSource`, `tiles` | The echodata track layer: `tiles/<slug>_echodata.pmtiles`, served by Martin as `<slug>_echodata` (add it to the web app's Martin config) |
| `platform` | Name, operator, and the web app's platform type (`RESEARCH_VESSEL`, `USV`, `GLIDER`, `OTHER`) |
| `instrument` | The echosounder (the importer always records `acoustic-simrad-ek80`) |
| `campaignForm` | Chief scientist, project, funding, institution: today only the campaign form sets these |
| `license`, `doi` | Shown on the campaign page (they also come from the Collection) |

Known gaps in the web app's importer, to fix there: it creates a platform only from `R/V <name>` in the Collection title, always as a research vessel, so NOAA Ships, Saildrones and Sailbuoys get none until it reads `oceanstream:platform`; and it does not read `oceanstream:campaign`.

## 2. Days

`oceanstream campaign days <ID>` lists the source (NCEI's public bucket, or the mirror in the project bucket), groups the raw files by the date in their names, keeps the cruise's own dates (NCEI folders also hold calibration and test files) and writes `days.txt` and `listing.tsv`. `--start`/`--end` narrow the window, e.g. to the part of a cruise that has a track.

## 3. Positions and mirrors

Raw files that carry no NMEA need a track: `oceanstream campaign gps <ID> --from …` writes `gps/<ID>.parquet` (UTC `time`, `latitude`, `longitude`) and uploads it to the project bucket's GPS prefix, where `hpc stage` finds it.

- `--from s3://noaa-wcsd-pds/data/raw/Saildrone/SD1046-2020-01/ancillary/` reads every Echoview `*.gps.csv` there (`GPS_date,GPS_time,Latitude,Longitude`).
- `--from erddap:sd1046_west_coast_survey_2019` reads an ERDDAP track (NOAA PMEL by default; `--erddap-server` for another).
- `--from <path or URL>` reads any CSV with time, latitude and longitude columns.

`gps/coverage.json` records the span, the median step and the longest gap; days without a track get no NASC and no map line.

Sources that are not in S3 are mirrored into the project bucket first, to `source.uri` in `hpc-run.toml` (`s3://oceanstream-bucket/mirror/<ID>/raw/` by default): `oceanstream campaign mirror <ID> --from https://…/cruise.tar.gz` streams an R2R BagIt tarball member by member and keeps only the `.raw`/`.idx` files; `--from zenodo:<record>` copies a Zenodo record's files and unpacks its zips; `--from griidc:<submission>` copies a GRIIDC dataset's files (the number is `data-submission` in the dataset page's source); `--from azure` copies a Saildrone file-share cruise. Mirrors resume, skipping what is already there. For a long mirror, run the command in an EDITO Job rather than the workspace pod (`edito/mirror/job.yaml` in the oceanstream-hpc-runs repository).

## 4. Test on EDITO, then fix `hpc-run.toml`

Two days of every cruise run on EDITO before the cluster (`edito/ncei-sv/` in the oceanstream-hpc-runs repository: Sv, then denoising with a preset, then the echogram report), typically one ordinary day and one edge case (deep water, shelf, a settings change). They decide the preset and the pipeline flags, and they measure the peak memory and the minutes per day, which set `job.day_memory_gb` and `job.walltime`. Record them in the campaign's `README.md`.

```toml
cruise = "SE2204"                 # optional; default: the campaign id

[days]
file = "days.txt"
test = ["2022-06-21", "2022-07-02"]

[source]
kind = "ncei"                     # or "s3" with uri = "s3://bucket/prefix/"
ship = "Oscar_Elton_Sette"        # ncei: the folder under data/raw/
instrument = "EK80"               # ncei: the folder; EK80-format data is sometimes filed under EK60
layout = "flat"                   # file names carry the date; "days" = one folder per day
day_shift_hours = 0               # -7 for a recorder clock 7 h fast: UTC = file time - 7 h (staging window and --ping-time-offset)

[gps]
enabled = false                   # true: stage gps/<ID>/ and pass --gps-dir

[job]
mode = "node"                     # one whole-node job; "array" for one array per batch
cpus = 4                          # per day
day_memory_gb = 30                # peak per day, from the EDITO test
walltime = "12:00:00"
# parallel = 0                    # days at once; 0 = as many as cores and memory allow
# step_mem_gb = 0                 # cap each day's memory where the site allows --mem on steps

[pipeline]
preset = "research-vessel-open-ocean-ryan-short"
args = ["--encode-mode", "power", "--depth-offset", "5", "..."]

[output]
dest = "hpc/products/SE2204"
```

True EK60 files need `"--sonar-model", "EK60"` in `pipeline.args`, whatever folder they are in (the default is EK80). With `"--encode-mode", "auto"`, a file that holds complex and power channels (WBT and GPT under EK80 software) gets every channel, each calibrated from its own samples; `power` or `complex` keeps only that kind. A complex channel whose pulse changes inside a group of files keeps that group's commonest pulse, because echopype cannot calibrate it otherwise; files with different settings are combined apart and joined. `--single-pulse` keeps only the channel's commonest pulse of the whole day, for a change between files that would otherwise put a step in Sv. Dropped pings are logged and listed in the Sv attribute `pings_dropped_for_changing_pulse`.

`oceanstream hpc run-cruise <ID> --dry-run` prints the batch script without touching the cluster.

## 5. Run

`oceanstream hpc run-cruise <ID>` stages every pending day, runs them as one whole-node job, removes the raw files, pushes and verifies the products, frees scratch, and publishes: Items with the job's per-day metrics, the Collection with `stac-collection.json`, and the track tiles. `state.tsv` records each day's events and `metrics/` each run's accounting; running the command again resumes. While one cruise runs, `oceanstream hpc stage <next ID>` can stage the next one, if both fit scratch.

The web app import is a separate, deliberate step: post `oceanstream-ingest.json`'s `collectionUrl`, `campaignSlug`, `name` and `provider` to the web app (or run the Prefect `publish-campaign` flow with the slug), then fill the campaign form fields from `campaignForm`.
