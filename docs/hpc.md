# Running the batch pipeline on a Slurm cluster

`oceanstream hpc` runs `scripts/batch_processing/process_from_raw.py` on a Slurm cluster whose machines may **not connect to external hosts**. Raw input and outputs move through S3-compatible object storage. Every transfer is opened from this side and streamed over SSH to the cluster's transfer node (`oceanstream.hpc.transfer`). The code is the `oceanstream.hpc` package; the batch scripts it submits only set up the environment and call `python -m oceanstream.hpc run-day` (or `run-node`) on the compute node.

```
source ──(oceanstream campaign mirror)──▶ S3 raw prefix          (or NCEI's public bucket directly)
                                             │  oceanstream hpc stage        (streamed over SSH from here)
                                             ▼
                               cluster scratch  raw/<cruise>/<day>/
                                             │  oceanstream hpc submit       (sbatch: run-day / run-node)
                                             ▼
                               cluster scratch  products/<container>/<day>/
                                             │  oceanstream hpc push         (over SSH to here, verified)
                                             ▼
                               S3 hpc/products/<cruise>/<day>/ ── oceanstream hpc publish-stac ──▶ item.json, collection.json,
                                                                                                    tiles/<slug>_echodata.pmtiles
```

`oceanstream hpc run-cruise <campaign>` does all of it for a whole cruise and resumes where it stopped; see [hpc-campaigns.md](hpc-campaigns.md) for preparing a campaign (catalogue metadata, GPS, mirrors, EDITO tests) and running it.

## Assumed cluster shape

- A **login node** that accepts `sbatch` for the compute partition.
- **Compute nodes without internet.** Everything they read must already be on the cluster filesystem.
- A **transfer node** reachable over SSH, which is where the data lands. It needs no internet access, no rclone and no object-storage credentials.
- Environment modules that provide Python 3.12.

## Setup

1. Copy `scripts/hpc/site.env.example` to `scripts/hpc/site.env` and fill it in, or export the variables. The site file is gitignored, so **never commit site values**. `HPC_SITE_FILE` points at another file; the environment always wins over the file. `oceanstream hpc site` shows the settings in use and what is missing.
2. Put an SSH key without a passphrase in `authorized_keys` on the cluster. Unattended jobs cannot answer a passphrase prompt. `oceanstream hpc site --check` logs in to both hosts.
3. Create the directory layout and check free space: `oceanstream hpc bootstrap --dirs --require-gb 150`.
4. Prove the transfer path: `oceanstream hpc smoke-test`. The cluster never connects out (the site does not allow its machines to reach external hosts), so every transfer is opened from this side and streamed over SSH to the transfer node. The cluster needs no object-storage credentials and no rclone. Until October 2026 transfers ran on the site's queued rclone wrapper, which reached object storage from some of its nodes by accident of their network; the site has since confirmed that is not permitted.
5. Build the wheelhouse (see below), copy it to `$HPC_ROOT/wheelhouse`, sync the code and build the venv: `oceanstream hpc sync-code && oceanstream hpc bootstrap --env`. `oceanstream hpc bootstrap --verify` only checks the imports.

### Wheelhouse (offline install)

`requirements-hpc.txt` is a frozen dependency set. Build the wheels on a machine with internet, using a **real Python 3.12**. Running `pip download --python-version 3.12` from another interpreter silently drops dependencies that are gated on the Python version. Restrict the manylinux tags to the cluster's glibc:

```bash
grep -vE '^(#|$|echopype @)' requirements-hpc.txt > reqs.txt
pip download --no-deps --only-binary=:all: -d wheelhouse \
  --implementation cp --python-version 3.12 --abi cp312 --abi abi3 --abi none \
  --platform manylinux_2_28_x86_64 --platform manylinux2014_x86_64 --platform any \
  -r reqs.txt            # per line, to collect the sdist-only packages
pip wheel --no-deps -w wheelhouse <sdist-only packages> "echopype @ git+https://...@<sha>"
```

`bootstrap --env` installs with `--no-index --find-links`. It rewrites direct references (`name @ git+...`) to the bare name, so pip takes the prebuilt wheel. The oceanstream package itself is not installed: jobs put the synced source on `PYTHONPATH`, so a code sync never needs a venv rebuild.

## Job shapes

`oceanstream hpc submit <campaign> --days … --mode …` renders one of two templates (`oceanstream/hpc/templates/`):

| Mode | Slurm job | When |
|---|---|---|
| `day` | one job for one day | a single test day |
| `array` | one array, one day per task, `array_limit` at once | days that do not fit scratch together |
| `node` | one job on one whole node (`--exclusive`); `run-node` starts each day as a job step `srun --exact … -J day-<date>`, *P* at a time | a whole cruise staged at once (the default) |

For `node`, *P* = min(days, ⌊cores per node / cores per day⌋, ⌊0.8 × node memory / memory per day⌋). Node memory is `HPC_MEM_PER_CORE_GB[_CONSTRAINED]` × `HPC_CORES_PER_NODE`. Memory per day comes from the campaign's `job.day_memory_gb`, measured on the EDITO test days. Each step is named `day-<date>`, so `oceanstream hpc metrics <jobid>` reports elapsed time, CPU and peak memory per day. Each day also leaves a `.<day>.DONE` or `.<day>.FAILED` marker next to its products. `job.step_mem_gb` adds `--mem` to each step, which caps a runaway day where the site allows it.

`--render-only` prints the batch script, and `--test-only` asks Slurm for an estimated start time without submitting.

### Memory

Some sites reject `--mem` and derive RAM from the core count. The budget is `HPC_MEM_PER_CORE_GB[_CONSTRAINED]` × cores, and 3/4 of it goes to Dask as `--memory-limit`; in `node` mode each day gets 3/4 of `day_memory_gb`. A single day of Saildrone EK80 data (24 files, 5.7 GB of raw) peaked at about 33 GiB on the reference run; an RR2205 day peaked at 27 GB.

### GPS

Positions come from the NMEA in the raw files or from GPS GeoParquet (`--gps-dir`). Without positions the pipeline still writes Sv, denoised, pruned and MVBS products, but it **skips NASC** and the day has no track. `oceanstream campaign gps` builds the GeoParquet, and `oceanstream hpc stage <campaign> --gps` stages it.

## Metadata: STAC

Each published day gets `<day>/item.json`, a STAC Item whose assets are the day's Zarr stores, NetCDF exports and echograms (with pulse, variant, colormap and channel as properties). Its geometry is the day's track, and `oceanstream:metrics` holds the job's accounting. The Item is written **after** the products, so its presence means the day is complete. `collection.json` is rebuilt from the Items, and consumers list a cruise's data by reading it. No filename parsing is needed.

The Collection's descriptive fields (title, licence, providers, citation, DOI links, platform) come from the campaign's `stac-collection.json`. When a publish gets none, as from the Prefect flows, they are kept from the Collection already published, so they are never reset to the defaults.

## Publishing the catalogue

Serving the STAC documents to a browser (e.g. a STAC Browser instance) needs, on the bucket:

- `s3:GetObject` for the products prefix, so Items, Collection and assets are readable;
- `s3:ListBucket` limited to that prefix with a condition, so Zarr stores can be listed while the rest of the bucket stays private;
- a CORS rule allowing `GET`/`HEAD` from the browser's origin;
- `Content-Type: application/json` on the documents, which the emitter sets when the store supports it;
- absolute `self` links (`public_base` in `oceanstream.stac.publish.publish`).

Anonymous readers must open stores with an explicit Zarr format:

```python
fs = s3fs.S3FileSystem(anon=True, client_kwargs={"endpoint_url": "https://..."})
ds = xr.open_zarr(fs.get_mapper(f"{bucket}/{key}"), zarr_format=3, consolidated=True)
```

Without `zarr_format=3`, xarray probes for Zarr v2 files (`.zmetadata`, `.zgroup`, `.zattrs`). Those do not exist, and an anonymous caller gets `403` rather than `404` for a missing key when listing is prefix-scoped, which breaks the fallback. Denoised and pruned stores hold masks, so rebuilding Sv from them needs `oceanstream.echodata.products` (`open_product_uri`).

## Prefect

`oceanstream.hpc.flows` defines four flows. They run on a small process worker (`deploy/edito/Dockerfile.hpc-submitter`, pool `oceanstream-hpc`) that calls the same library over SSH. Register them with `python -m oceanstream.hpc.deploy`. The pure logic (day selection, batching, guards, quota parsing) lives in `oceanstream.hpc.flowlib`, which has unit tests.

| Flow | What it does |
|---|---|
| `process-day-hpc` | One day: sync, stage, submit, push, publish, and optionally tiles and cleanup. |
| `process-batch-hpc` | Many days as one Slurm job array, then push, publish, tiles and the web app import; frees the batch's products and raw from scratch. |
| `process-campaign-hpc` | A whole campaign as a sequence of `process-batch-hpc` batches (`batch_size`, default 40), so scratch holds one batch at a time. Before each batch it compares the batch's raw size in S3 (times two) with the free scratch quota from `bsc_quota` and stops cleanly if it is short. |
| `publish-campaign` | Rebuilds a campaign's Collection and track tiles. |

Parameters shared by the batch and campaign flows:

- `cruise_id`, and `days` or `start_date`/`end_date` (inclusive). Days without raw data under `hpc/raw/<cruise_id>/` are skipped.
- `reprocess`: without it, days that already have an `item.json` are skipped. With it, every day is redone; add `reprocess_since` (an ISO time, UTC if no zone is given) and a day whose `item.json` was written after that time counts as redone. That is how an in-place reprocess resumes: re-run with the same `reprocess_since`.
- `preset`: a library key, or, with `preset_toml`, the key under which that TOML text is shipped to the cluster (`$HPC_ROOT/presets/<preset>.toml`). The cluster keeps one file per name, so the key must change with the text; the web app sends Denoise Lab presets as `denoise-lab.<key>.<sha8>`.
- `pipeline_args`: processing flags for `process_from_raw.py`. The run mechanics in `flowlib.HPC_RUN_ARGS` always come first; with no `pipeline_args` the TPOS reference flags follow.
- `campaign_slug`, `campaign_name`, `provider`: import the Collection into the web app after each batch. `web_url` names the web app; it must be `OCEANSTREAM_WEB_URL` or listed in `OCEANSTREAM_WEB_URLS`, because the worker sends its ingest key there.
- `dry_run`: stage and submit nothing; the campaign flow returns the rendered batch script of its first batch.

## Commands

| Command | What it does |
|---|---|
| `oceanstream hpc site [--check]` | Settings in use, missing ones; `--check` logs in to both hosts |
| `oceanstream hpc bootstrap --dirs/--env/--verify` | Layout, venv, import check |
| `oceanstream hpc sync-code [--dry-run]` | Ship the source to `HPC_CODE_DIR` |
| `oceanstream hpc smoke-test` | Prove scratch → object storage over SSH |
| `oceanstream hpc quota` | `bsc_quota` from the transfer host |
| `oceanstream hpc transfer pull/push/check …` | Move files between object storage and the cluster |
| `oceanstream hpc stage <campaign> [--days] [--gps] [--cleanup]` | Raw input onto scratch, or off it |
| `oceanstream hpc submit <campaign> [--days] [--mode] [--render-only/--test-only]` | One job for staged days |
| `oceanstream hpc wait <jobid>` / `metrics <jobid>` | Follow a job; its accounting as JSON |
| `oceanstream hpc push <campaign> --days … [--replace] [--cleanup]` | Products to S3, verified |
| `oceanstream hpc run-cruise <campaign> [--dry-run]` | Everything above for every pending day; resumable |
| `oceanstream hpc publish-stac <campaign>` | Items, Collection with the campaign metadata, track tiles |
| `oceanstream hpc compare REF NEW` | Two runs of a day, store by store |
| `python -m oceanstream.hpc run-day / run-node` | What the batch scripts run on the compute node |

Raw on scratch is a copy of its source: `oceanstream hpc stage <campaign> --cleanup` removes staged days.
