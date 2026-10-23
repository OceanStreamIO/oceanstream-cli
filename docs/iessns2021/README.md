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

## Checks so far

A smoke test ran on 8 files on 2021-07-09, 19:02–19:48: six complex files, then two power/angle files after the switch at 19:14:45. Both modes produced 3,846 pings, 974 complex then 2,872 power/angle, with the calibration XML applied.

| Mode | Time | Peak memory | Peak disk |
|---|---|---|---|
| Combine the day, then Sv | 458 s | 15.5 GiB | 12.7 GiB |
| `--sv-per-file` | 526 s | 8.9 GiB | 4.1 GiB |

The two modes give identical Sv: largest difference 0.0 dB on every channel, the same NaN pattern, positions within 10⁻⁶ degrees.

**18 kHz differs between the two sample types.** Across the switch the seabed rose from 188 to 178 m. Its echo summed over ±3 m changed by +0.2 to +1.3 dB at 38–200 kHz, but by −1.9 dB at 18 kHz. That puts 18 kHz about 2.5 dB low relative to the others, and lower still in weak water-column bands. The 18 kHz pulse uses a taper slope of 0.5, against 0.03–0.1 on the other channels. The complex path uses the effective duration of that tapered pulse; the power path uses the nominal duration plus a Sa correction of 0.06 dB, which looks too small for that taper. Until the second switch (2021-07-08 01:20) is checked, treat absolute 18 kHz Sv as unconfirmed.

## Next

1. Sv for the test days 2021-07-12 (deep water) and 2021-07-16 (shelf) on EDITO, then a first denoise round with the draft preset `research-vessel-nordic-shelf`. Its windows assume pings about 1 s apart and a 500 m range.
2. Check the 18 kHz offset at the 07-08 switch.
3. Plan the MareNostrum 5 run. Group scratch had 179 GB free on 2026-10-23, less than one of the two complex days.
