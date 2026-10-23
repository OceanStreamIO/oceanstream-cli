# IESSNS 2021, Icelandic part

The Icelandic part of the International Ecosystem Summer Survey in Nordic Seas, R/V Árni Friðriksson, 5–26 July 2021. The EK80 data are on PANGAEA as dataset [975782](https://doi.org/10.1594/PANGAEA.975782) (CC-BY-4.0). This page records what the data hold, the pipeline changes they needed, and the checks made so far. The dataset was chosen in [../datasets/next-candidates.md](../datasets/next-candidates.md).

## The data

There are 2,114 raw files (1,024.8 GB) over 22 days. Five WBT channels (18, 38, 70, 120 and 200 kHz) ping together every 0.7–1 s with a 1.024 ms CW pulse, to about 500 m. The transducers are hull-mounted at 9 m. The configuration records 0 m, so the depth is set with `--depth-offset 9`.

The survey changed sample type twice:

| Period (UTC) | Files | Size | Samples |
|---|---|---|---|
| 07-05 15:14 → 07-08 01:20 | 166 | 81 GB | power and angle |
| 07-08 01:20 → 07-09 19:14 | 891 | 444 GB | complex, four sectors |
| 07-09 19:14 → 07-26 | 1,056 | 500 GB | power and angle |

Complex samples take about eight times the space, so 2021-07-08 holds 209 GB and 2021-07-09 243 GB, against 22–40 GB on the other days. Both of those days mix the two sample types.

Other features of the data:
- **Seabed in range.** GEBCO along the track puts the seabed within the 500 m range on most days. 07-05, 07-16, 07-24 and 07-26 are entirely on the shelf; 07-11, 07-12, 07-14 and 07-22 are entirely deeper.
- **Positions.** The files carry the ship's INS position strings (`$INGGA`).
- **Environment.** The environment XML carries a measured sound-speed profile, with a single temperature and salinity (5 °C, 34.9 PSU) for absorption.
- **Calibration.** PANGAEA's `calibration_2021.xml` gives the gain, Sa correction and equivalent beam angle per channel. The beam angles are calibrated (38 kHz −20.30 dB); the raw files carry Simrad's nominal −20.7 dB. The gains differ by up to 0.08 dB.

Our copy of the data is in `s3://oceanstream-bucket/pangaea/IESSNS2021/`:
- `raw/`: the mirrored raw files
- `meta/`: the calibration XML and PANGAEA's file table

`deploy/edito/pangaea/mirror.py` makes the mirror. It starts PANGAEA's tape recalls ahead of the downloads and checks every file's MD5. PANGAEA serves each file at 1–4 MB/s.

## Pipeline changes

All are in `scripts/batch_processing/process_from_raw.py`:
- **`--encode-mode auto`.** Picks complex or power per group of files. On a day that mixes both, each kind is combined on its own and their Sv is joined in time.
- **`--cal-params-file`.** Reads the `<calibration><case kHz= g= SA= equivalentBeamAngle=/>` XML (`oceanstream/echodata/calibrate/case_xml.py`). Its values replace the gain, Sa correction and equivalent beam angle from the raw files. Sv records which file was used.
- **Positions from the raw files.** Without `--gps-dir`, positions come from the files' NMEA (GGA fixes).
- **`--sv-per-file`.** Computes Sv for each file right after conversion and deletes the converted copy. The day's Sv is then the files' Sv joined in time. Power and angle samples grow about three times on conversion, so combining a day first needs disk for the raw files plus two converted copies.
- **`--delete-raw-after-convert`.** Removes each staged raw file once converted.
- **`--sv-workers N`.** Converts and calibrates N files at once, one process each, about 9–11 GiB per process. On 2021-07-12, 56 files took 18 minutes with 6 processes, against about 67 minutes one at a time.

Three fixes came out of the test days:
- **Pruning counted the cells below the seabed** (`denoise.py`). Pings whose water column is mostly empty are dropped. That share used to be counted over the whole range, so over a shelf with the seabed at 180 m of a 500 m range, a quarter of the pings went. Cells flagged as seabed, or past a channel's range, no longer count.
- **Echograms of a whole day ran out of memory** (`process_campaign.py`). A day of pings every 0.7–1 s (76,000–113,000 pings) drawn ping by ping needed more than 72 GiB. Panels now average blocks of pings down to at most 6,000 columns, for display only.
- **False seabed in deep water** (`process_campaign.py`, `seabed/detection.py`). For channels that ping together, the pipeline used the composite detector on its own. Where the seabed was out of range it joined a few detections on fish or noise into a line across hours of water column, and masked it all. On 2021-07-12 it masked 5–10 % of cells, with a line at 15 m from 22:30 to midnight. The guarded detector built for RR2205 is now used in both cases. A line must have a seabed echo of at least −35 dB behind it, gaps are bridged only up to 30 minutes between agreeing detections, and pings more than 2 minutes from any detection get no line. In a test, a 16-ping school in deep water made the old detector draw 900 pings of seabed and the new one none.

## Checks so far

A smoke test ran on 8 files on 2021-07-09, 19:02–19:48: six complex files, then two power/angle files after the switch at 19:14:45. Both modes produced 3,846 pings, 974 complex then 2,872 power/angle, with the calibration XML applied.

| Mode | Time | Peak memory | Peak disk |
|---|---|---|---|
| Combine the day, then Sv | 458 s | 15.5 GiB | 12.7 GiB |
| `--sv-per-file` | 526 s | 8.9 GiB | 4.1 GiB |

The two modes give identical Sv: largest difference 0.0 dB on every channel, the same NaN pattern, positions within 10⁻⁶ degrees.

**18 kHz differs between the two sample types.** Across the switch the seabed rose from 188 to 178 m. Its echo summed over ±3 m changed by +0.2 to +1.3 dB at 38–200 kHz, but by −1.9 dB at 18 kHz. That puts 18 kHz about 2.5 dB low relative to the others, and lower still in weak water-column bands. The 18 kHz pulse uses a taper slope of 0.5, against 0.03–0.1 on the other channels. The complex path uses the effective duration of that tapered pulse; the power path uses the nominal duration plus a Sa correction of 0.06 dB, which looks too small for that taper. Until the second switch (2021-07-08 01:20) is checked, treat absolute 18 kHz Sv as unconfirmed.

## Test days on EDITO

Sv was computed for 2021-07-12 (deep water all day, 56 files), 2021-07-16 (shelf, 81 files) and 2021-07-09 17:00–21:13 (70 files across the complex-to-power switch).

| Day | Pings | Ping interval | Sv store | Sv time (workers) | Peak memory |
|---|---|---|---|---|---|
| 07-09 window | 21,343 | 0.70 s | — | 84 min (1) | 38 GiB |
| 07-12 | 75,882 | 0.92 s | 3.6 GB | 18 min (6) | 56 GiB |
| 07-16 | 113,002 | 0.71 s | — | 24.5 min (6) | 67 GiB |

Each denoise round used `--background-estimator quantile --seabed-mask --seabed-line edge --seabed-mask-offset 5 --mvbs-ping-time-bin 60s --no-crosstalk --surface-exclusion-depth 12`. Stages 5–9 took 21 minutes for the 07-09 window, 74 for 07-12 and 107 for 07-16.

| Round | Change | Result |
|---|---|---|
| 1 | draft preset | 07-12 ran out of memory drawing echograms; pruning kept 75.6 % of the 07-09 pings |
| 2 | pruning ignores seabed cells; thinned echograms | pruning keeps 97.5 % on 07-09; 07-12 masked hours of false seabed |
| 3 | guarded seabed detector | 07-12 has no seabed line; 07-09 unchanged (seabed on 100 % of pings) |
| 4 | background noise capped at deep-water level + 6 dB | running |

**Background noise over the shelf.** The noise estimate follows the seabed depth. At 38 kHz it is −151 to −157 dB re 1 W all day on 07-12. On 07-16 it is −147 to −153 dB where the seabed is about 200 m deep, and −133 to −140 dB where it is 59–112 m. On 07-09 it is −129 dB over a 65 m seabed and −155 dB over 180 m. Below a shallow seabed the 500 m range is filled with multiple echoes and reverberation, so no part of the ping is noise only.

In a two-hour window of 07-16 (seabed 70–150 m), background removal took 89 % of the water column at 38 kHz. The removed cells had a median raw Sv of −80 dB, no weaker than the cells kept. Round 4 caps the estimate per channel at the 07-12 median plus 6 dB, which leaves deep water unchanged.

**Open method questions for the products.** In that same window the mean 38 kHz water-column Sv is −67.9 dB when removed cells are left out, as MVBS and NASC do now (echopype averages skip NaN). It is −81.5 dB when they count as zero, a 13.6 dB difference. Pruning also drops pings whose water column is mostly removed, 30 % of 07-16, and those pings are empty water, not bad data. Both raise MVBS and NASC where scattering is weak, on RR2205 and TPOS as well. A fix would treat cells removed as below the noise as zero in integration, and prune only pings the noise detectors flag. That changes every product, so it is a decision rather than a tuning step.

## Next

1. Compare round 4 with round 3 on 07-16 and 07-09.
2. Check the 18 kHz offset at the 07-08 switch.
3. The MareNostrum 5 run. Group scratch had 179 GB free on 2026-10-23. Three power/angle days fit per batch (about 135 GB with their products), but the complex days (209 and 243 GB raw) do not fit whole.
