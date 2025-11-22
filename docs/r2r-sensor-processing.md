# R2R Sensor Data: Spatial-Temporal Integration Analysis

**Date**: 2025-11-17  
**Issue**: R2R sensor archives contain **time-series data WITHOUT spatial coordinates**  
**Question**: How to process multiple R2R sensor archives with the same campaign_id?

---

## Problem Statement

### Current R2R Data Structure

**SSV (Sound Velocity) Output**:
```csv
date,time,sound_velocity
12/28/2016,21:32:23.071,1541.876
12/28/2016,21:32:23.554,1541.874
```
❌ **NO latitude/longitude columns**

**Fluorometer Output**:
```csv
local_date,local_time,data_date,data_time,ch1,ch2,ch3
01/01/2017,00:00:01.732,01/01/17,00:06:50,695,813,527
```
❌ **NO latitude/longitude columns**

### GeoTrack Pipeline Requirements

**From `oceanstream/geotrack/csv_reader.py`**:
```python
if 'latitude' not in df.columns or 'longitude' not in df.columns:
    continue  # SKIPS files without spatial coordinates
```

**Primary Keys for Deduplication**:
```python
PRIMARY_KEY_COLUMNS = ['time', 'latitude', 'longitude', 'trajectory']
```

**Result**: 
- ✅ Current geotrack pipeline **requires** latitude/longitude
- ❌ R2R sensor archives **don't have** spatial coordinates in raw data
- ❌ Cannot use current `oceanstream process geotrack convert` with R2R sensor outputs

---

## Architecture: Sensor Data vs. Track Data

### Two Types of Oceanographic Data

#### 1. **Track/Navigation Data** (Current Pipeline)
- **Source**: GPS, ship navigation systems, platform telemetry
- **Structure**: `time, lat, lon, [optional measurements]`
- **Examples**: 
  - Saildrone trajectory files
  - Ship navigation logs
  - Platform position data
- **Processing**: GeoTrack pipeline → GeoParquet with spatial binning

#### 2. **Sensor Time-Series Data** (R2R Archives)
- **Source**: Instruments (fluorometer, SSV, CTD, ADCP, etc.)
- **Structure**: `time, [measurements]` ← **NO spatial coordinates**
- **Examples**:
  - Fluorometer chlorophyll readings
  - Sound velocity measurements
  - CTD profiles
  - ADCP currents
- **Processing**: ❌ **NOT SUPPORTED** by current geotrack pipeline

---

## Current Behavior: Multiple R2R Archives with Same Campaign

### Scenario
```bash
# Process SSV archive
oceanstream process geotrack convert \
  --input-source /tmp/ssv/FK161229/124690/data/ssv.csv \
  --campaign-id FK161229 \
  --output-dir ./output

# Process Fluorometer archive
oceanstream process geotrack convert \
  --input-source /tmp/fluoro/FK161229/124688/data/fluorometer.csv \
  --campaign-id FK161229 \
  --output-dir ./output
```

### What Happens ❌

1. **SSV file** → `csv_reader.py` checks for lat/lon → **SKIPPED** (no spatial coords)
2. **Fluorometer file** → `csv_reader.py` checks for lat/lon → **SKIPPED** (no spatial coords)
3. **Result**: No GeoParquet output created

### Why This Happens

The geotrack pipeline is designed for **spatially-explicit trajectory data**, not pure sensor time-series.

---

## Solution Options

### Option 1: ⭐ Spatial-Temporal Interpolation (RECOMMENDED)

**Concept**: Join sensor time-series with ship navigation track

**Requirements**:
1. **Navigation track file** with `time, lat, lon` for the cruise
2. **Sensor time-series files** with `time, measurements`
3. **Interpolation logic** to assign lat/lon to each sensor reading based on timestamp

**Architecture**:
```
┌─────────────────┐      ┌──────────────────────┐
│  Navigation     │      │  Sensor Time-Series  │
│  (time, lat,    │      │  (time, measurement) │
│   lon, heading) │      │                      │
└────────┬────────┘      └──────────┬───────────┘
         │                          │
         │    Temporal Join         │
         │    (interpolate)         │
         └──────────┬───────────────┘
                    ▼
         ┌──────────────────────────┐
         │  Enriched Sensor Data    │
         │  (time, lat, lon,        │
         │   measurement)           │
         └──────────────────────────┘
                    │
                    ▼
         ┌──────────────────────────┐
         │  GeoTrack Pipeline       │
         │  → GeoParquet + STAC     │
         └──────────────────────────┘
```

**Implementation Steps**:

1. **New Pipeline**: `oceanstream process timeseries join`
   ```bash
   oceanstream process timeseries join \
     --nav-track /data/FK161229_navigation.csv \
     --sensor-data /data/FK161229_124690_ssv.csv \
     --output enriched_ssv.csv \
     --interpolation linear
   ```

2. **Interpolation Methods**:
   - **Nearest**: Closest navigation point in time
   - **Linear**: Linear interpolation between nav points
   - **Spline**: Smooth interpolation (for high-frequency sensors)

3. **Then Process with GeoTrack**:
   ```bash
   oceanstream process geotrack convert \
     --input-source enriched_ssv.csv \
     --campaign-id FK161229 \
     --output-dir ./output
   ```

**Advantages**:
- ✅ Accurate spatial assignment
- ✅ Reuses existing geotrack pipeline
- ✅ Works with any sensor type
- ✅ Handles different sampling rates

**Challenges**:
- ⚠️ Requires navigation track file
- ⚠️ Interpolation can introduce small spatial errors
- ⚠️ Time synchronization must be accurate

---

### Option 2: Append as Non-Spatial Time-Series

**Concept**: Store sensor data WITHOUT spatial coordinates

**Architecture**:
```
Sensor Time-Series → Parquet (time-partitioned)
                   → NO spatial binning
                   → NO GeoParquet (plain Parquet)
```

**Implementation**:
```bash
oceanstream process timeseries store \
  --input-source /data/FK161229_124690_ssv.csv \
  --campaign-id FK161229 \
  --output-dir ./output \
  --partition-by time
```

**Output Structure**:
```
output/
  └── FK161229/
      └── sensors/
          ├── ssv/
          │   └── year=2016/month=12/day=28/*.parquet
          └── fluorometer/
              └── year=2017/month=01/day=01/*.parquet
```

**Advantages**:
- ✅ Preserves original sensor data
- ✅ Time-based partitioning for efficient queries
- ✅ No interpolation errors

**Disadvantages**:
- ❌ Not spatially queryable
- ❌ Can't use spatial binning
- ❌ Separate from geotrack data

---

### Option 3: Store as Sensor-Specific Collections

**Concept**: Each sensor type gets its own collection/dataset

**Current Behavior** (if we had spatial coords):
```
output/
  └── FK161229/  # Campaign
      ├── lat_bin=X/lon_bin=Y/*.parquet  # Mixed spatial data
      └── stac/collection.json
```

**Proposed** (sensor-specific):
```
output/
  └── FK161229/  # Campaign
      ├── navigation/
      │   └── lat_bin=X/lon_bin=Y/*.parquet
      ├── sensors/
      │   ├── ssv/
      │   │   └── time_bin=YYYY-MM-DD/*.parquet
      │   └── fluorometer/
      │       └── time_bin=YYYY-MM-DD/*.parquet
      └── stac/
          ├── navigation-collection.json
          ├── ssv-collection.json
          └── fluorometer-collection.json
```

**Advantages**:
- ✅ Clear separation of concerns
- ✅ Each sensor has appropriate indexing
- ✅ STAC metadata per sensor type

**Disadvantages**:
- ⚠️ More complex data management
- ⚠️ Analysis requires joining datasets

---

## Recommended Approach: Phased Implementation

### Phase 1: Document Current Limitation ✅ (THIS DOCUMENT)

**Status**: ✅ COMPLETE

**Key Points**:
- R2R sensor archives lack spatial coordinates
- Current geotrack pipeline requires lat/lon
- Need spatial-temporal join for sensor data

### Phase 2: Implement Temporal Join Tool

**Priority**: HIGH  
**Estimated Effort**: 3-5 days

**Deliverables**:
1. `oceanstream/timeseries/` module
2. `join.py` with interpolation logic
3. CLI: `oceanstream process timeseries join`
4. Support for:
   - Nearest-neighbor interpolation
   - Linear interpolation
   - Time tolerance (max gap for valid join)
5. Tests with R2R + navigation data

**Example Usage**:
```bash
# Step 1: Join sensor data with navigation
oceanstream process timeseries join \
  --nav-track FK161229_navigation.csv \
  --sensor-data FK161229_124690_ssv.csv \
  --output FK161229_ssv_spatial.csv \
  --method linear \
  --max-gap 60s

# Step 2: Process as geotrack
oceanstream process geotrack convert \
  --input-source FK161229_ssv_spatial.csv \
  --campaign-id FK161229 \
  --output-dir ./output
```

### Phase 3: Automated Campaign Processing

**Priority**: MEDIUM  
**Estimated Effort**: 2-3 days

**Concept**: Process entire R2R campaign automatically

```bash
oceanstream process r2r campaign \
  --cruise-id FK161229 \
  --nav-archive FK161229_navigation.tar.gz \
  --sensor-archives FK161229_124688_fluorometer.tar.gz FK161229_124690_ssv.tar.gz \
  --output-dir ./output
```

**Automation**:
1. Extract all archives
2. Process navigation → reference track
3. Join each sensor archive with navigation
4. Convert to GeoParquet
5. Generate unified STAC metadata

---

## Current State: Deduplication Works for Spatial Data

### What Works Now ✅

**Scenario**: Multiple Saildrone trajectory files
```bash
# Run 1: Process trajectory file 1
oceanstream process geotrack convert --input-source sd1030.csv --campaign-id TPOS2023

# Run 2: Process trajectory file 2
oceanstream process geotrack convert --input-source sd1033.csv --campaign-id TPOS2023
```

**Behavior**:
1. ✅ Both files have `time, lat, lon, trajectory`
2. ✅ Data appended to same campaign folder
3. ✅ Automatic row-level deduplication (by time, lat, lon, trajectory)
4. ✅ File tracking prevents duplicate processing
5. ✅ STAC metadata updated

### What DOESN'T Work ❌

**Scenario**: Multiple R2R sensor archives (current question)
```bash
# Run 1: Process SSV
oceanstream process geotrack convert --input-source ssv.csv --campaign-id FK161229
# ❌ SKIPPED: No lat/lon columns

# Run 2: Process Fluorometer
oceanstream process geotrack convert --input-source fluorometer.csv --campaign-id FK161229
# ❌ SKIPPED: No lat/lon columns
```

**Why**:
- R2R sensor CSVs lack spatial coordinates
- GeoTrack pipeline requires lat/lon for spatial binning
- Need interpolation/join step first

---

## Decision Matrix

| Approach | Spatial Accuracy | Implementation | Query Performance | Data Volume |
|----------|-----------------|----------------|-------------------|-------------|
| **Temporal Join** | ⭐⭐⭐ High | ⭐⭐ Medium | ⭐⭐⭐ Excellent | ⭐⭐⭐ Efficient |
| **Non-Spatial** | ❌ None | ⭐⭐⭐ Easy | ⭐⭐ Good | ⭐⭐⭐ Efficient |
| **Sensor Collections** | ❌ None | ⭐⭐ Medium | ⭐⭐ Good | ⭐⭐ Moderate |

**Recommendation**: **Option 1 (Temporal Join)** for production use

---

## Immediate Action Items

### For Your Current Question

**Q**: What happens when we process R2R archives repeatedly with same campaign_id?

**A**: Currently, **nothing happens** because:
1. R2R sensor CSVs are **skipped** by geotrack pipeline (no lat/lon)
2. No GeoParquet files are created
3. Deduplication doesn't apply (no data processed)

### To Process R2R Sensor Data

**Option A**: Manual interpolation (immediate)
1. Find/create navigation track for FK161229 cruise
2. Write custom script to join sensor timestamps with navigation
3. Generate CSV with `time, lat, lon, [sensor measurements]`
4. Process with existing geotrack pipeline

**Option B**: Wait for Phase 2 implementation (future)
- New `timeseries join` command will automate this

### Documentation Update

**File**: `docs/r2r-sensor-processing.md` (this document)
- Documents current limitation
- Explains spatial-temporal join requirement
- Provides roadmap for implementation

---

## References

1. **Primary Keys**: `oceanstream/geotrack/deduplication.py:11`
   ```python
   PRIMARY_KEY_COLUMNS = ['time', 'latitude', 'longitude', 'trajectory']
   ```

2. **Spatial Requirement**: `oceanstream/geotrack/csv_reader.py:115`
   ```python
   if 'latitude' not in df.columns or 'longitude' not in df.columns:
       continue  # Skips non-spatial data
   ```

3. **R2R Data Format**:
   - SSV: `date, time, sound_velocity`
   - Fluorometer: `local_date, local_time, data_date, data_time, ch1, ch2, ch3`

4. **Existing Tests**: `oceanstream/tests/integration/test_append_update.py`
   - 6 tests for spatial data append/deduplication
   - All assume data has lat/lon columns

---

## Conclusion

**Current State**: R2R sensor archives **cannot** be processed with the existing geotrack pipeline because they lack spatial coordinates.

**Solution**: Implement spatial-temporal interpolation to join sensor time-series with navigation tracks, then process the enriched data through the existing pipeline.

**Immediate Workaround**: Manual joining of sensor data with navigation using pandas merge_asof or similar techniques.

**Long-term**: Build `oceanstream process timeseries join` command to automate this workflow.
