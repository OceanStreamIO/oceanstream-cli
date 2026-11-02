# Running the batch pipeline on a Slurm cluster

`scripts/hpc/` runs `scripts/batch_processing/process_from_raw.py` one day at a
time on a Slurm cluster whose machines may **not connect to external hosts**. Raw
input and outputs move through S3-compatible object storage, and every transfer
is opened from the EDITO side and streamed over SSH to the cluster's transfer
node (`scripts/hpc/xfer.py`).

```
Azure File Share ──mirror_raw_to_s3.py──▶ S3 hpc/raw/<cruise>/<day>/
                                             │  stage-raw.sh (streamed over SSH from EDITO)
                                             ▼
                               cluster scratch  raw/<cruise>/<day>/
                                             │  submit-day.sh (sbatch)
                                             ▼
                               cluster scratch  products/<container>/<day>/
                                             │  push-products.sh (over SSH to EDITO, verified)
                                             ▼
                               S3 hpc/products/<cruise>/<day>/ ── publish_stac.py ──▶ item.json, collection.json
                                                                   build_campaign_tiles.py ──▶ tiles/<slug>_echodata.pmtiles
```

## Assumed cluster shape

- A **login node** that accepts `sbatch` for the compute partition.
- **Compute nodes without internet.** Everything they read must already be on
  the cluster filesystem.
- A **transfer node** reachable over SSH, which is where the data lands. It
  needs no internet access, no rclone and no object-storage credentials.
- Environment modules that provide Python 3.12.

## Setup

1. `cp scripts/hpc/site.env.example scripts/hpc/site.env` and fill it in. The
   `site.env` file is gitignored, so **never commit site values**.
2. Put an SSH key without a passphrase in `authorized_keys` on the cluster.
   Unattended jobs cannot answer a passphrase prompt.
3. Create the directory layout and check free space:
   `./scripts/hpc/bootstrap-dirs.sh --require-gb 150`
4. Prove the transfer path: `./scripts/hpc/smoke-test.sh`. The cluster never
   connects out -- the site does not allow its machines to reach external
   hosts -- so every transfer is opened from this side and streamed over SSH
   to the transfer node (`scripts/hpc/xfer.py`). The cluster needs no
   object-storage credentials and no rclone. Until October 2026 transfers ran
   on the site's queued rclone wrapper, which reached object storage from some
   of its nodes by accident of their network; the site has since confirmed that
   is not permitted.
5. Build the wheelhouse (see below), copy it to `$HPC_ROOT/wheelhouse`, sync
   the code and build the venv:
   `./scripts/hpc/sync-code.sh && ./scripts/hpc/bootstrap-env.sh`

### Wheelhouse (offline install)

`requirements-hpc.txt` is a frozen dependency set. Build the wheels on a
machine with internet, using a **real Python 3.12**. Running `pip download
--python-version 3.12` from another interpreter silently drops dependencies
that are gated on the Python version. Restrict the manylinux tags to the
cluster's glibc:

```bash
grep -vE '^(#|$|echopype @)' requirements-hpc.txt > reqs.txt
pip download --no-deps --only-binary=:all: -d wheelhouse \
  --implementation cp --python-version 3.12 --abi cp312 --abi abi3 --abi none \
  --platform manylinux_2_28_x86_64 --platform manylinux2014_x86_64 --platform any \
  -r reqs.txt            # per line, to collect the sdist-only packages
pip wheel --no-deps -w wheelhouse <sdist-only packages> "echopype @ git+https://...@<sha>"
```

`bootstrap-env.sh` installs with `--no-index --find-links`. It rewrites
direct references (`name @ git+...`) to the bare name, so pip takes the
prebuilt wheel. The oceanstream package itself is not installed: jobs put
the synced source on `PYTHONPATH`.

## One day

```bash
python scripts/hpc/mirror_raw_to_s3.py --cruise-id C --start-date D --end-date D --bucket B
python scripts/hpc/mirror_raw_to_s3.py --cruise-id C --gps-container gpsdata --bucket B   # once per cruise
./scripts/hpc/stage-raw.sh --cruise C --day D
./scripts/hpc/stage-raw.sh --cruise C --gps
./scripts/hpc/submit-day.sh --cruise C --day D --cpus 16 \
    --preset <preset> \
    -- <process_from_raw.py flags> --gps-dir "$HPC_STAGE/gps/C"
./scripts/hpc/push-products.sh --container C --day D
python scripts/hpc/publish_stac.py --root s3://B/hpc/products/C --cruise-id C --days D
```

`--preset` takes a key from the denoise preset library; see [denoise-presets.md](denoise-presets.md) for choosing and tuning one.

`submit-day.sh --render-only` prints the job script, and `--test-only` asks
Slurm for an estimated start time without submitting.

### GPS

Positions come only from GPS GeoParquet. Without GPS the pipeline still writes
Sv, denoised, pruned and MVBS products, but it **skips NASC** and the day has
no track. Mirror the cruise's GPS once, stage it, and pass `--gps-dir`.

### Memory

Some sites reject `--mem` and derive RAM from the core count. `submit-day.sh`
computes the budget from `HPC_MEM_PER_CORE_GB[_CONSTRAINED]` and passes 3/4 of
it to Dask as `--memory-limit`. A single day of Saildrone EK80 data (24 files,
5.7 GB of raw) peaked at about 33 GiB on the reference run.

## Metadata: STAC

Each published day gets `<day>/item.json`, a STAC Item whose assets are the
day's Zarr stores, NetCDF exports and echograms (with pulse, variant, colormap
and channel as properties). Its geometry is the day's track. The Item is written
**after** the products, so its presence means the day is complete.
`collection.json` is rebuilt from the Items, and consumers list a cruise's data
by reading it. No filename parsing is needed.

## Publishing the catalogue

Serving the STAC documents to a browser (e.g. a STAC Browser instance) needs, on the bucket:

- `s3:GetObject` for the products prefix, so Items, Collection and assets are readable;
- `s3:ListBucket` limited to that prefix with a condition, so Zarr stores can be listed while the
  rest of the bucket stays private;
- a CORS rule allowing `GET`/`HEAD` from the browser's origin;
- `Content-Type: application/json` on the documents, which the emitter sets when the store
  supports it;
- absolute `self` links, from `publish_stac.py --public-base <public https url of the root>`.

Anonymous readers must open stores with an explicit Zarr format:

```python
fs = s3fs.S3FileSystem(anon=True, client_kwargs={"endpoint_url": "https://..."})
ds = xr.open_zarr(fs.get_mapper(f"{bucket}/{key}"), zarr_format=3, consolidated=True)
```

Without `zarr_format=3`, xarray probes for Zarr v2 files (`.zmetadata`, `.zgroup`, `.zattrs`).
Those do not exist, and an anonymous caller gets `403` rather than `404` for a missing key when
listing is prefix-scoped, which breaks the fallback. Denoised and pruned stores hold masks, so
rebuilding Sv from them needs `oceanstream.echodata.products` (`open_product_uri`).

## Prefect

`scripts/hpc/flows.py` defines four flows. They run on a small process worker (`deploy/edito/Dockerfile.hpc-submitter`, pool `oceanstream-hpc`) that only drives the scripts over SSH. Register them with `python scripts/hpc/deploy_flows.py`. The pure logic (day selection, batching, guards) lives in `scripts/hpc/flowlib.py`, which has unit tests.

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

Raw on scratch is a copy of S3: `stage-raw.sh --cleanup` removes staged days, and `quota.sh` prints `bsc_quota` from the transfer host.
