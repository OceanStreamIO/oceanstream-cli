# Saildrone TPOS 2023 — Full Survey Processing Report

**Campaign**: Saildrone TPOS 2023 (Tropical Pacific Observing System)  
**Date range**: 2023-05-30 to 2023-11-05 (160 days, 141 with EK80 data)  
**Processed**: 11 April 2026  
**Branch**: `feat/batch_processing`  

---

## 1. Infrastructure

| Resource | Details |
|----------|---------|
| **VM** | `oceanstream-batch-spot` (Standard_E48ds_v6) |
| **CPU** | 48 vCPU — Intel Xeon Platinum 8573C |
| **RAM** | 384 GB |
| **Data Disk** | 1 TB Premium SSD (`oceanstream-batch-spot-data-disk`) |
| **Location** | North Europe (`northeurope`) |
| **Resource Group** | `ne1-saildrone1-rg` |
| **Public IP** | `20.223.137.44` |
| **SSH** | `ssh oceanstream@20.223.137.44` |
| **Storage Account** | `ne1osvmdevtest` |
| **Raw Data Source** | Azure File Share `saildroneraw/DATA` |
| **GPS Source** | Azure Blob container `gpsdata` |

### Disk Usage (as of completion)

| Area | Size |
|------|------|
| Raw Sv zarrs | 193 GB |
| Denoised zarrs | 91 GB |
| MVBS zarrs | 9 GB |
| Campaign MVBS combined | 8.9 GB |
| NASC zarrs | 44 MB |
| Combined daily zarrs (new) | ~82 GB |
| Per-day echograms (new) | 3.3 GB |
| Converted echodata | 5 GB |
| Campaign echograms | 593 MB |
| Tiles + GeoJSON + Heatmaps | 4 MB |
| **Total used** | **396 GB / 1 TB** |

---

## 2. Processing Pipeline Overview

The pipeline (`scripts/batch_processing/build_full_survey.py`) has **13 stages**:

```
Stage 1:  Discover raw EK80 files from Azure File Share
Stage 2:  Download raw files to local disk
Stage 3:  Convert raw EK80 → echopype EchoData (zarr)
Stage 4:  Compute Sv (volume backscattering strength)
Stage 5:  Calibrate + Enrich (env variables, GPS merge)
Stage 6:  Denoise (4-stage: background, impulse, attenuation, transient)
Stage 7:  Per-day MVBS + NASC
Stage 8:  Per-day echograms (skipped — see notes)
Stage 9:  Campaign combined MVBS zarr
Stage 10: Campaign echograms (4 segments × 3 colormaps)
Stage 11: Echodata PMTiles (vector tiles for map viz)
Stage 12: NASC Biomass GeoJSON (depth-frequency merged points)
Stage 13: NASC Heatmap COGs (raster overlays + PNG previews)
Stage 14: Combined daily products + per-day echograms (NEW)
```

### Key Scripts

| Script | Purpose |
|--------|---------|
| `build_full_survey.py` | Main 13-stage pipeline (~2900 lines) |
| `run_nasc_parallel.py` | Fast parallel NASC via numpy (replaced stage 7 NASC) |
| `run_stages_9_to_13.py` | Standalone post-processing (stages 9–13 without re-running 1–8) |
| `run_combine_daily.py` | Merge pulse modes per day + generate echograms with pulse markings |
| `local_storage.py` | Monkey-patches Azure storage calls to local disk I/O |

---

## 3. Stage-by-Stage Results

### Stage 1–2: Discovery & Download

- **277 raw EK80 files** discovered on Azure File Share
- Files grouped into **141 day directories** (2023-05-30 to 2023-11-05)
- Two pulse modes per day: **short_pulse** (38+200 kHz) and **long_pulse** (38 kHz)

### Stage 3: Convert Raw → EchoData

- **141 converted echodata zarrs** (one per day)
- Sonar model: EK80, CW waveform, complex encoding
- Stored in `/mnt/data/output/echodata/`

### Stage 4: Compute Sv

- **277 per-pulse-mode Sv zarrs** (137 short_pulse + 140 long_pulse) — intermediates
- Typical shape: `(channels=2, ping_time=~15000-32000, range_sample=~3600-7200)`
- **Final per-day products**: 141 combined Sv zarrs (both pulse modes merged, channels: `38kHz`, `200kHz`)

### Stage 5–6: Calibrate + Enrich + Denoise

- **268 per-pulse-mode denoised zarrs** (132 short_pulse + 136 long_pulse) — intermediates
- 9 raw zarrs had no matching GPS or failed calibration → skipped
- 4-stage denoising: background noise removal → impulse noise → attenuation correction → transient removal
- GPS (latitude/longitude) merged from `gpsdata` container into denoised datasets
- **Final per-day products**: 140 combined denoised zarrs (both pulse modes merged)

**GPS coverage issue**: 34 denoised zarrs have all-NaN GPS coordinates. These are consistently one pulse mode per affected day — the GPS merge succeeded for one mode but not the other (likely timing mismatch between GPS timestamps and sonar ping times for the alternate pulse mode).

### Stage 7: Per-day MVBS

- **261 per-pulse-mode MVBS zarrs** (+ 261 NetCDF copies) — intermediates
- Bins: `range_bin=1m`, `ping_time_bin=10s`
- Computed with `echopype.commongrid.compute_MVBS()`
- **Final per-day products**: 137 combined MVBS zarrs (both pulse modes merged)

### Stage 7 (NASC): Per-day NASC — Fast Vectorized

**Original approach** (echopype `compute_NASC`): ~90 GB RAM, 15–60 min per zarr. Only 5 zarrs completed before the pipeline was killed due to stalled computation.

**Replacement** (`run_nasc_parallel.py`): Pure numpy + haversine + `np.bincount`. ~7 GB per worker, 1–17 seconds per zarr. **~600× faster.**

- **229 per-pulse-mode NASC zarrs** (+ 229 NetCDF copies) — intermediates
  - 109 short_pulse + 120 long_pulse
- Bins: `range_bin=10m`, `dist_bin=0.5nmi`
- **222 computed in 2 minutes** (10 parallel workers)
- 34 skipped (all-NaN GPS), 5 failed (see §4)
- **Final per-day products**: 216 combined NASC zarrs (per-frequency: 38kHz + 200kHz)

### Stage 8: Per-day Echograms — SKIPPED

Skipped with `--skip-perday-echograms` to prioritise campaign-level products. Can be generated later with `run_stages_9_to_13.py`.

### Stage 9: Campaign Combined MVBS

- **1 combined zarr**: `campaign_mvbs_combined_38kHz.zarr` (8.9 GB)
- 38 kHz frequency only (200 kHz was skipped — see §4)
- Concatenates all per-day MVBS zarrs along `ping_time`
- Stored at `/mnt/data/output/campaign_mvbs_combined_38kHz.zarr`

### Stage 10: Campaign Echograms

- **12 PNG files** (593 MB total)
- 4 temporal segments × 3 colormaps (`jet`, `ocean_r`, `ek500`)
- Segment breaks at gaps > 30 minutes
- Coverage: full campaign at 38 kHz
- Stored in `/mnt/data/output/campaign_echograms/`

### Stage 11: Echodata PMTiles

- **1 PMTiles file**: `saildrone_tpos_2023_echodata.pmtiles` (1.3 MB)
- 141 track features (LineStrings), one per day
- Built with tippecanoe v2.49 (zooms 0–14, no simplification)
- Layer name: `echodata`
- Also: source GeoJSON (690 KB)
- Stored in `/mnt/data/output/tiles/`

### Stage 12: NASC Biomass GeoJSON

- **6,135 point features** in `saildrone_tpos_2023.geojson` (1.5 MB)
- Coverage: 132 unique days (2023-05-30 to 2023-11-05)
- Depth-frequency merge strategy:
  - 200 kHz: sum NASC over 10–150 m (shallow, reliable)
  - 38 kHz: sum NASC over 150–500 m (deep, where 200 kHz is noise)
  - `nasc_combined` = shallow + deep
- Outlier capping at P99 (61 values capped)
- Orphan points in sparse 1° bins removed (4 points)
- NASC combined range: 2 – 65,596,384 m² nmi⁻²
- Stored in `/mnt/data/output/nasc_biomass/`

### Stage 13: NASC Heatmap COGs

- **3 Cloud-Optimised GeoTIFFs** + **3 PNG previews** + `manifest.json`
- Variables: `nasc_combined` (YlOrRd), `nasc_38` (Blues), `nasc_200` (Greens)
- Grid: 0.5° resolution, scipy griddata interpolation, cKDTree search radius 0.5°
- Stored in `/mnt/data/output/heatmaps/`

### Stage 14: Pulse-Mode Merge + Per-day Echograms

The raw pipeline (stages 4–7) processes each pulse mode separately, producing per-pulse-mode intermediate zarrs. Stage 14 merges these into the **final per-day products** — one zarr per day per product level, with both pulse modes combined.

Channels renamed from instrument IDs (`EKA 266972-07 ES38-18|200-18C`) to frequency labels (`38kHz`, `200kHz`). Each dataset includes a `pulse_mode` variable (0=long, 1=short) for provenance.

**Final per-day products:**

| Product | Count | Merge method | Example filename |
|---------|-------|-------------|------------------|
| Sv (raw) | 141 | Interpolated to 0.5m common depth grid, concat along `ping_time` | `2023-07-15--combined--sv.zarr` |
| Denoised Sv | 140 | Same interpolation as raw Sv | `2023-07-15--combined--denoised.zarr` |
| MVBS | 137 | Concat along `ping_time` (depth already aligned at 1m) | `2023-07-15--combined--mvbs.zarr` |
| NASC (per-freq) | 216 | Concat along `distance` (offset to avoid overlap) | `2023-07-15--combined--nasc--38kHz.zarr` |

The per-pulse-mode zarrs (`*--short_pulse--*.zarr`, `*--long_pulse--*.zarr`) remain on disk as intermediates but are **not the deliverable products**.

**Per-day echograms:**

- **1,610 PNG files** (3.3 GB total)
- Generated from the combined zarrs (not per-pulse-mode)
- 3 products (MVBS, denoised, raw Sv) × 2 frequencies (38kHz, 200kHz) × 2 colormaps (`ocean_r`, `EK500`)
- Each echogram has a **pulse-mode colour bar** at the bottom: orange = Short pulse, blue = Long pulse
- Time axis labelled with hourly ticks (UTC)
- Stored in `/mnt/data/output/perday_echograms/`

**Processing**: 141 days × 4 workers = **~62 minutes** (`run_combine_daily.py`)

---

## 4. Issues Found and Fixed

### Issue 1: Disk Full at 256 GB

**Problem**: The initial 256 GB data disk filled to 100% during stage 6 (denoising), triggering VM auto-shutdown at 01:10 UTC.

**Fix**: Deallocated VM → resized data disk from 256 GB to **1 TB** (`az disk update --size-gb 1024`) → restarted VM → `sudo growpart /dev/nvme0n2 1 && sudo resize2fs /dev/nvme0n2p1`.

### Issue 2: NASC Computation Prohibitively Slow

**Problem**: `echopype.commongrid.compute_NASC` with `dist_bin=0.5nmi` consumed ~90 GB RAM and 15–60 minutes per zarr, even with `scheduler="synchronous"`. At 263 zarrs × 40 min = ~175 hours (7+ days).

**Fix**: Wrote `run_nasc_parallel.py` using pure numpy vectorised operations:
- Haversine cumulative distance for horizontal binning
- `np.bincount` for (distance × depth) 2D aggregation
- Eager loading (`chunks=None`) — no dask graph overhead
- Result: **1–17 seconds per zarr, ~7 GB RAM** per worker. 222 zarrs in 2 minutes.

**Commit**: `06b4fc9` — `feat(batch): fast vectorized NASC — numpy bincount replaces echopype`

### Issue 3: numpy 2.x StringDType Crash

**Problem**: Stage 9 (`normalize_string_dtypes`) failed with `TypeError: cannot cast dtype StringDType()` — numpy 2.x introduced `StringDType()` which doesn't support `.astype(str)`.

**Fix**: Changed to `.astype("U")` with fallback `np.array([str(v) for v in vals.flat])`.

**Commit**: `519c302` — `fix: normalize_string_dtypes — handle numpy 2.x StringDType`

### Issue 4: GPS Not Found in Denoised Zarrs

**Problem**: Stage 11 (PMTiles) found zero track features. `_extract_track_from_local_zarr()` checked `ds.coords` for lat/lon, but echopype stores GPS data in `ds.data_vars`, not `ds.coords`. Also, raw Sv zarrs don't have GPS merged — only denoised zarrs do.

**Fix**: Check both `ds.data_vars` and `ds.coords`; prefer denoised zarrs (which have GPS).

**Commit**: `dbb588f` — `fix(batch): stages 11-12 — look for lat/lon in data_vars, prefer denoised zarrs`

### Issue 5: NASC Channel Name Parsing

**Problem**: Stage 12 tried `float(channel_name)` on strings like `'EKA 266972-07 ES38-18|200-18C'`, causing failures. echopype uses full instrument serial/frequency strings, not numeric Hz values.

**Fix**: Try `float()` first, fall back to substring parsing (`ES38` → 38 kHz, `ES200` → 200 kHz).

**Commit**: `be0b8fc` — `fix(batch): NASC channel detection — handle string channel names`

### Issue 6: `_open_azure_zarr` Bypassing Local Storage Patch

**Problem**: Early pipeline runs called `xr.open_zarr()` directly instead of routing through `open_sv_from_azure()`, bypassing the `local_storage.patch_storage()` monkey-patch. Dataset loads silently fell back to Azure (which didn't have the data yet).

**Fix**: Updated `_open_azure_zarr` to use `open_sv_from_azure()` from storage module.

**Commit**: `a1e577e` — `fix: list_denoised_zarrs scans local disk when local_storage is patched`

### Issue 7: 200 kHz Campaign MVBS — No Data

**Status**: **Unresolved**. Stage 9 for 200 kHz reported "no data". 200 kHz is only present in short_pulse mode. The `select_frequency()` channel matching may not be handling the EK80 channel name format (`'EKA 266972-07 ES200-18C'` instead of `200000.0`). Does not affect 38 kHz products.

### Issue 8: 34 Denoised Zarrs with All-NaN GPS

**Status**: **Known limitation**. GPS merge during enrichment step fails for one pulse mode on certain days. The GPS timestamps don't align with the sonar ping times for the alternate pulse mode. These zarrs are excluded from NASC computation and PMTiles.

**Affected**: Consistently alternates between `short_pulse` and `long_pulse` per day. Most common in June–August. 34 of 268 denoised zarrs (~13%).

### Issue 9: 5 NASC Computation Failures

| Day/Mode | Error | Root Cause |
|----------|-------|------------|
| 2023-06-26/long_pulse | `arange: cannot compute length` | NaN depth values → invalid depth edges |
| 2023-07-16/short_pulse | `No valid distances` | GPS valid but all at same point → 0 distance |
| 2023-07-27/short_pulse | `arange: cannot compute length` | Same as above |
| 2023-07-31/short_pulse | `arange: cannot compute length` | Same as above |
| 2023-10-02/long_pulse | `No variable named 'Sv'` | Corrupted denoised zarr (only metadata vars) |

---

## 5. Data Storage & Access

### All data is currently on the VM local disk

```
/mnt/data/output/
├── sd-tpos2023-full-v01/           # ~380 GB — per-day products
│   ├── 2023-05-30/
│   │   ├── 2023-05-30--combined--sv.zarr           # ← FINAL: raw Sv (both pulse modes)
│   │   ├── 2023-05-30--combined--denoised.zarr     # ← FINAL: denoised Sv
│   │   ├── 2023-05-30--combined--mvbs.zarr          # ← FINAL: MVBS
│   │   ├── 2023-05-30--combined--nasc--38kHz.zarr   # ← FINAL: NASC 38 kHz
│   │   ├── 2023-05-30--combined--nasc--200kHz.zarr  # ← FINAL: NASC 200 kHz
│   │   ├── 2023-05-30--short_pulse.zarr             # intermediate
│   │   ├── 2023-05-30--short_pulse--denoised.zarr   # intermediate
│   │   ├── 2023-05-30--short_pulse--mvbs.zarr       # intermediate
│   │   ├── 2023-05-30--long_pulse.zarr              # intermediate
│   │   ├── 2023-05-30--long_pulse--denoised.zarr    # intermediate
│   │   └── ... (+ .nc copies, long_pulse mvbs/nasc)
│   ├── 2023-05-31/
│   ├── ... (141 day directories)
│   └── 2023-11-05/
├── echodata/                       # 5 GB — converted EchoData (intermediate)
├── campaign_mvbs_combined_38kHz.zarr  # 8.9 GB — concat'd campaign MVBS
├── campaign_echograms/             # 593 MB — 12 PNG echograms
├── tiles/                          # 1.9 MB — PMTiles + source GeoJSON
├── nasc_biomass/                   # 1.5 MB — NASC points GeoJSON
├── perday_echograms/                # 3.3 GB — 1,610 daily echogram PNGs (NEW)
├── heatmaps/                       # 656 KB — COGs + PNGs + manifest
├── raw_downloads/                  # empty (cleaned up)
└── *.log                           # pipeline logs
```

### Access via SSH

```bash
ssh oceanstream@20.223.137.44

# Browse outputs
ls /mnt/data/output/sd-tpos2023-full-v01/

# Open a zarr in Python
source ~/workspace/venv/bin/activate
python3 -c "
import xarray as xr
ds = xr.open_zarr('/mnt/data/output/sd-tpos2023-full-v01/2023-07-15/2023-07-15--combined--mvbs.zarr')
print(ds)
"
```

### Copy Files Locally

```bash
# Download a specific product
scp oceanstream@20.223.137.44:/mnt/data/output/nasc_biomass/saildrone_tpos_2023.geojson .
scp oceanstream@20.223.137.44:/mnt/data/output/tiles/saildrone_tpos_2023_echodata.pmtiles .
scp -r oceanstream@20.223.137.44:/mnt/data/output/heatmaps/ ./heatmaps/
scp -r oceanstream@20.223.137.44:/mnt/data/output/campaign_echograms/ ./echograms/
```

### Upload to Azure Blob (completed 11 Apr 2026)

All final products uploaded to container `sd-tpos2023-full-v01` on storage account `ne1osvmdevtest`.

| Product | Blob prefix | Files | Size |
|---------|-------------|-------|------|
| Combined per-day zarrs | `2023-XX-XX/*--combined--*.zarr/` | 221,102 | ~82 GB |
| Campaign MVBS | `campaign_mvbs_combined_38kHz.zarr/` | 2,681 | 9.5 GB |
| Campaign echograms | `campaign_echograms/` | 12 | 593 MB |
| Per-day echograms | `perday_echograms/` | 1,610 | 3.3 GB |
| PMTiles + GeoJSON | `tiles/` | 2 | 2 MB |
| NASC biomass | `nasc_biomass/` | 1 | 1.5 MB |
| NASC heatmaps | `heatmaps/` | 7 | 656 KB |

Per-pulse-mode intermediates (`*--short_pulse--*`, `*--long_pulse--*`) were **not** uploaded.

```bash
# Read a combined zarr from Azure (Python)
import xarray as xr
ds = xr.open_zarr(
    "az://sd-tpos2023-full-v01/2023-07-15/2023-07-15--combined--mvbs.zarr",
    storage_options={"account_name": "ne1osvmdevtest"}
)
print(ds)
```

---

## 6. Data Products Summary

**Final per-day products** (combined pulse modes — the deliverables):

| Product | Count | Size | Format | Filename pattern |
|---------|-------|------|--------|------------------|
| Sv (raw) | 141 | ~40 GB | zarr | `*--combined--sv.zarr` |
| Denoised Sv | 140 | ~30 GB | zarr | `*--combined--denoised.zarr` |
| MVBS | 137 | ~9 GB | zarr | `*--combined--mvbs.zarr` |
| NASC (per-freq) | 216 | ~3 MB | zarr | `*--combined--nasc--{38kHz,200kHz}.zarr` |
| Per-day echograms | 1,610 | 3.3 GB | PNG | `perday_echograms/` |

**Campaign-level products:**

| Product | Count | Size | Format | Location |
|---------|-------|------|--------|----------|
| Campaign MVBS (38 kHz) | 1 | 8.9 GB | zarr | `campaign_mvbs_combined_38kHz.zarr` |
| Campaign echograms | 12 | 593 MB | PNG | `campaign_echograms/` |
| Echodata track tiles | 1 | 1.3 MB | PMTiles | `tiles/` |
| NASC biomass points | 6,135 | 1.5 MB | GeoJSON | `nasc_biomass/` |
| NASC heatmaps | 3+3 | 656 KB | COG + PNG | `heatmaps/` |

**Intermediate per-pulse-mode products** (on disk but not deliverables):

| Product | Count | Size | Format |
|---------|-------|------|--------|
| Raw Sv | 277 | 193 GB | zarr |
| Denoised Sv | 268 | 91 GB | zarr |
| MVBS | 261 | 9 GB | zarr + nc |
| NASC | 229 | 44 MB | zarr + nc |

---

## 7. Pending Work

| Item | Priority | Notes |
|------|----------|-------|
| ~~Upload to Azure Blob~~ | ~~High~~ | ✅ All final products in `sd-tpos2023-full-v01` container |
| 200 kHz campaign MVBS | Medium | Debug channel matching for short_pulse 200 kHz |
| ~~Per-day echograms~~ | ~~Low~~ | ✅ 1,610 PNGs via `run_combine_daily.py` |
| Fix 5 failed NASC zarrs | Low | Edge cases: NaN depth, zero distance, corrupt zarr |
| Fix 34 NaN-GPS denoised | Low | Investigate GPS merge timing mismatch |
| ~~Deallocate VM~~ | ~~High~~ | ✅ Deallocated 11 Apr 2026 |

---

## 8. Timing

| Phase | Duration | Notes |
|-------|----------|-------|
| Stages 1–6 (raw → denoised) | ~12 hours | First run + resume after disk resize |
| Stage 7 MVBS | ~70 min | 261 zarrs sequentially |
| Stage 7 NASC (echopype) | ~3 hours | Only 5 zarrs completed, killed |
| **Stage 7 NASC (fast numpy)** | **2 min** | 222 zarrs, 10 workers |
| Stage 9 campaign MVBS | ~15 min | 38 kHz only |
| Stage 10 campaign echograms | ~20 min | 4 segments × 3 colormaps |
| Stage 11 PMTiles | ~10 sec | 141 tracks |
| Stage 12 NASC GeoJSON | ~5 sec | 6,135 points |
| Stage 13 NASC heatmaps | ~2 sec | 3 COGs + 3 PNGs |
| **Stage 14 combined daily** | **~62 min** | 141 days, 4 workers, 661 zarrs + 1,610 PNGs |
| **Total wall clock** | **~15 hours** | Including disk resize downtime |

---

## 9. Code Changes (feat/batch_processing)

```
06b4fc9 feat(batch): fast vectorized NASC — numpy bincount replaces echopype
681274e fix(batch): NASC parallel — pre-check GPS validity + track skips
be24434 feat(batch): parallel NASC computation script
be0b8fc fix(batch): NASC channel detection — handle string channel names
dbb588f fix(batch): stages 11-12 — look for lat/lon in data_vars, prefer denoised zarrs
7841599 feat(batch): add run_stages_9_to_13.py — standalone for post-processing stages
519c302 fix: normalize_string_dtypes — handle numpy 2.x StringDType
0c18a89 feat(batch): add stages 11-13 — echodata PMTiles, NASC biomass GeoJSON, NASC heatmap COGs
a1e577e fix: list_denoised_zarrs scans local disk when local_storage is patched
213047b fix(batch): deduplicate ping_time in MVBS/NASC combine too
550c68d fix(batch): deduplicate ping_time + error handling for resilient parallel processing
c8613b5 feat(batch): per-day pulse-mode merge + daily echograms with pulse markings
```
