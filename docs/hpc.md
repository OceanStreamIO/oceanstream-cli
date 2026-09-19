# Running the batch pipeline on a Slurm cluster

`scripts/hpc/` runs `scripts/batch_processing/process_from_raw.py` one day at a
time on a Slurm cluster whose compute nodes have **no internet access**. Raw
input and outputs move through S3-compatible object storage. The cluster's data
transfer service stages data in and out.

```
Azure File Share ──mirror_raw_to_s3.py──▶ S3 hpc/raw/<cruise>/<day>/
                                             │  stage-raw.sh (queued rclone)
                                             ▼
                               cluster scratch  raw/<cruise>/<day>/
                                             │  submit-day.sh (sbatch)
                                             ▼
                               cluster scratch  products/<container>/<day>/
                                             │  push-products.sh (queued rclone, verified)
                                             ▼
                               S3 hpc/products/<cruise>/<day>/ ── publish_stac.py ──▶ item.json, collection.json
                                                                   build_campaign_tiles.py ──▶ tiles/<slug>_echodata.pmtiles
```

## Assumed cluster shape

- A **login node** that accepts `sbatch` for the compute partition.
- **Compute nodes without internet.** Everything they read must already be on
  the cluster filesystem.
- A **transfer node** that runs rclone. It may go through a queued wrapper that
  submits the transfer as a job on a separate data-transfer cluster, and that
  path must be able to reach S3.
- Environment modules that provide Python 3.12.

## Setup

1. `cp scripts/hpc/site.env.example scripts/hpc/site.env` and fill it in. The
   `site.env` file is gitignored, so **never commit site values**.
2. Put an SSH key without a passphrase in `authorized_keys` on the cluster.
   Unattended jobs cannot answer a passphrase prompt.
3. Create the directory layout and check free space:
   `./scripts/hpc/bootstrap-dirs.sh --require-gb 150`
4. Install the S3 remote on the transfer node, then prove the queued path
   reaches the bucket:
   `./scripts/hpc/bootstrap-rclone-s3.sh && ./scripts/hpc/smoke-test.sh`
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
    --denoise-config deploy/slurm/presets/<preset>.toml --preset-key <preset> \
    -- <process_from_raw.py flags> --gps-dir "$HPC_STAGE/gps/C"
./scripts/hpc/push-products.sh --container C --day D
python scripts/hpc/publish_stac.py --root s3://B/hpc/products/C --cruise-id C --days D
```

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

## Prefect

`scripts/hpc/flows.py` defines `process-day-hpc` (sync, stage, submit, push,
publish, and optionally tiles and cleanup) and `publish-campaign`. The flows
run on a small process worker (`deploy/edito/Dockerfile.hpc-submitter`) that
only drives the scripts over SSH. Register them with
`python scripts/hpc/deploy_flows.py`.
