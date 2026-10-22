# RR2205 whole-cruise run on MareNostrum 5

On 2026-10-22 all 32 EK80 days of RR2205 (2022-06-15 to 2022-07-16, 2,541 raw files, 264.5 GB) were processed on MareNostrum 5 with the configuration chosen in [round 5](denoise-round5.md) and the CTD sound speed and absorption. Every day completed, and its products are in `s3://oceanstream-bucket/hpc/products/RR2205/<day>/`, checked file by file against the cluster copy.

## Configuration

- Denoise preset `research-vessel-open-ocean-ryan-short` (Ryan transient detector, 6 dB, ±8 pings) with the quantile background estimator.
- Seabed mask on the edge line with a 5 m offset, 10 m surface exclusion, 60 s MVBS bins, no cross-talk pruning.
- Sv per channel from the CW complex samples on a 0.18 m range grid, stored as float32.
- Sound speed and absorption from the CCHDO CTD casts (`33RR20220613_ctd.nc`), and the 6.2 m transducer depth from the EK80 configuration.
- oceanstream-cli `feat/denoise-presets` at 2699b55 and echopype c93006b3, the same versions as the EDITO worker image. On 2022-06-21 the Sv matches the EDITO `sv-ctd` store bit for bit (151 sampled pings per channel, largest difference 0.0 dB).

## How it ran

`scripts/hpc/run-ncei-cruise.sh` took the cruise in batches of six days, because group scratch had about 238 GB free. For each batch it streamed the raw files from NCEI's public bucket through EDITO to scratch over SSH, ran one Slurm array (16 cores, highmem, 4 h limit), and removed the raw files when the array ended. While the next batch staged and ran, it pushed the finished days to S3, checked them against scratch and freed them. The state file records each day's progress, so running the same command again resumes the run.

## Results

| | |
|---|---|
| Days | 32 of 32 completed and verified |
| Products | 128,920 objects, 13.0 GB |
| Task time per day | 1 to 47 min, median 35 min |
| Peak memory per task | 27 GB, of 144 GB allocated |
| Wall clock | 7 h 19 min, from the first staging (11:40 UTC) to the last push (18:59 UTC) |
| Slurm billing | 558.5 hours, which is 279 physical core-hours |

Each day has its Sv store and the masked, denoised, pruned, MVBS and NASC stores, plus `denoise_stats.json`. It also has per-channel echograms of the raw, denoised and pruned Sv and of the MVBS, and the NASC plots.

MareNostrum 5 counts 224 CPUs per node (112 cores with two threads each). Slurm gave each task 32 CPUs, which is 16 whole cores, and billed it 32. BSC may charge the allocation either way. Either way the cruise used about 0.1 % of the 504,000 core-hours. The tasks kept only about two cores busy on average: 2.07 CPU-seconds per second of task time, and 2.36 at most.

## STAC

The products are catalogued as STAC 1.0 at `https://oceanstream-bucket.s3.waw3-1.cloudferro.com/hpc/products/RR2205/collection.json`. That is one Collection plus one Item per day (`<day>/item.json`), and all 33 documents pass stac-validator. Each Item lists the day's Zarr stores, `denoise_stats.json` and echograms as assets. It also records the day's track as a LineString, the frequencies, the echopype version, the run id `mn5-rr2205-20261022` and the day's Slurm metrics. The project's STAC browser opens it at `https://project-oceanstream-576873-0.lab.dive.edito.eu/external/oceanstream-bucket.s3.waw3-1.cloudferro.com/hpc/products/RR2205/collection.json`. The track tiles and the web-app import are not done yet.

| Day | Raw files | Raw GB | Task min | Products GB |
|---|---|---|---|---|
| 2022-06-15 | 26 | 2.7 | 12 | 0.07 |
| 2022-06-16 | 60 | 6.3 | 25 | 0.30 |
| 2022-06-17 | 94 | 9.8 | 37 | 0.48 |
| 2022-06-18 | 71 | 7.4 | 29 | 0.39 |
| 2022-06-19 | 54 | 5.2 | 22 | 0.28 |
| 2022-06-20 | 98 | 10.3 | 39 | 0.50 |
| 2022-06-21 | 96 | 10.1 | 38 | 0.51 |
| 2022-06-22 | 85 | 8.9 | 34 | 0.42 |
| 2022-06-23 | 104 | 11.0 | 41 | 0.56 |
| 2022-06-24 | 88 | 9.1 | 37 | 0.43 |
| 2022-06-25 | 90 | 9.4 | 37 | 0.49 |
| 2022-06-26 | 92 | 9.6 | 42 | 0.49 |
| 2022-06-27 | 89 | 9.2 | 33 | 0.42 |
| 2022-06-28 | 79 | 8.1 | 31 | 0.34 |
| 2022-06-29 | 93 | 9.7 | 37 | 0.49 |
| 2022-06-30 | 71 | 7.2 | 34 | 0.36 |
| 2022-07-01 | 97 | 10.2 | 42 | 0.52 |
| 2022-07-02 | 74 | 7.6 | 30 | 0.40 |
| 2022-07-03 | 97 | 10.1 | 47 | 0.52 |
| 2022-07-04 | 87 | 9.1 | 35 | 0.45 |
| 2022-07-05 | 82 | 8.5 | 34 | 0.43 |
| 2022-07-06 | 83 | 8.6 | 34 | 0.43 |
| 2022-07-07 | 87 | 9.2 | 36 | 0.45 |
| 2022-07-08 | 88 | 9.3 | 36 | 0.44 |
| 2022-07-09 | 113 | 11.8 | 42 | 0.57 |
| 2022-07-10 | 99 | 10.3 | 37 | 0.49 |
| 2022-07-11 | 88 | 9.1 | 34 | 0.45 |
| 2022-07-12 | 81 | 8.6 | 32 | 0.44 |
| 2022-07-13 | 61 | 6.3 | 26 | 0.32 |
| 2022-07-14 | 76 | 8.0 | 31 | 0.37 |
| 2022-07-15 | 37 | 3.9 | 15 | 0.16 |
| 2022-07-16 | 1 | 0.1 | 1 | 0.01 |

## Problems met

- **2022-06-19 failed on its first attempt.** It has four one-ping start-up files, each on a single channel, and a four-ping file without 18 kHz, and their channel sets did not combine with the rest of the day. The pipeline now keeps the channel set holding most of the day's pings and skips the other files with a warning (bc015f3). The rerun took 22 minutes.
- **Pushing one file at a time ran at about 1 MB/s.** Each day is about 4,000 small Zarr chunks. The driver now pushes in the background over eight SSH streams while the next batch runs (2699b55).
- **Raw files were left behind for days resumed from scratch.** After the driver restart, five days whose products were already on scratch were pushed but kept their raw files (34 GB). These were removed by hand, and fixed in 9d6f1fe.
- **Four days have no raw echograms.** On 2022-06-20, 07-04, 07-06 and 07-08 the raw (pre-denoise) echograms failed with "No such file or directory". Each failure began a few seconds after another task of the same array finished on the same node, which clears `/tmp` when a job ends. The renderer now writes under `$TMPDIR`, which the Slurm template sets per job (ec9ac95). The Sv stores and every other product of those days are complete; their 20 raw echograms (5 channels × 4 days) still need rendering.
- **Reading NCEI's bucket needs two settings.** `xfer.py --opendata` needs `HPC_OPENDATA_REGION=us-east-1` and `AWS_ENDPOINT_URL` unset, otherwise botocore sends the request to our own endpoint and gets NoSuchBucket. The driver sets both.

## For the next cruises

- Each batch took about an hour: roughly 14 minutes to stage six days, then an array as long as its slowest day. Larger batches would shorten the run if scratch has room.
- At 27 GB, the tasks use a fifth of the memory that highmem nodes give them. Standard nodes give 32 GB at 16 cores, which is probably enough but leaves little margin. Test on one long day before switching.
- With about two busy cores per task, 16 cores is mostly idle. Either give each task fewer cores (four would roughly quarter the billing at the same speed) or make the pipeline use more of them.
- As a planning rate, this cruise needed about 2.1 billing-hours per GB of raw EK80 data. Cruises with FM pulses or more channels will differ.

## Reproducing

The CTD file and the R2R navigation must already be on scratch, in `ctd/RR2205/` and `gps/RR2205/` under `/gpfs/scratch/ehpc812/oceanstream/`. `days.txt` lists the 32 days, one per line.

```bash
export HPC_CODE_DIR=/gpfs/projects/ehpc812/oceanstream/code-rr2205
export HPC_VENV=/gpfs/projects/ehpc812/oceanstream/venv-rr2205
scripts/hpc/run-ncei-cruise.sh --ship Roger_Revelle --cruise RR2205 \
    --days-file days.txt --batch 6 --dest hpc/products/RR2205 --state state.tsv \
    --container RR2205 --cpus 16 --time 04:00:00 \
    --preset research-vessel-open-ocean-ryan-short -- \
    --expected-categories short_pulse --waveform-mode CW --encode-mode complex \
    --sv-float32 --sv-range-bin-m 0.18 --chunk-ping-time 50 --depth-offset 6.2 \
    --ctd-file /gpfs/scratch/ehpc812/oceanstream/ctd/RR2205/33RR20220613_ctd.nc \
    --background-estimator quantile --seabed-mask --seabed-line edge --seabed-mask-offset 5 \
    --mvbs-ping-time-bin 60s --no-crosstalk --surface-exclusion-depth 10 \
    --emit-denoise-diagnostics --skip-combined-echograms --skip-campaign-echograms \
    --gps-dir /gpfs/scratch/ehpc812/oceanstream/gps/RR2205
```
