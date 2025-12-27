# R2R Multi-Sensor Processing

This guide demonstrates advanced processing of R2R (Rolling Deck to Repository) data, including CTD profiles, winch telemetry, navigation, and auxiliary sensors. Assumes familiarity with OceanStream fundamentals and CLI usage.

## Prerequisites

```bash
# Verify installation with R2R-specific dependencies
oceanstream --version
python -c "import seabirdscientific; print(f'SeaBird library: {seabirdscientific.__version__}')"

# Environment setup
export CRUISE_ID="RR2402"
export WORK_DIR="$HOME/r2r_processing/$CRUISE_ID"
mkdir -p "$WORK_DIR"/{raw,processed}
cd "$WORK_DIR"
```

!!! note "Required Dependencies"
    CTD hex file processing requires the `seabirdscientific` library:
    ```bash
    pip install seabirdscientific>=2.7.0
    ```

---

## Data Acquisition

### Direct Download from R2R

R2R archives are available via Globus or direct download:

```bash
# Navigation data (GeoCSV)
curl -L -o raw/RR2402_160202_r2rnav.tar.gz \
  "https://g-1e773d.99817a.0ec8.data.globus.org/RR2402/RR2402_160202_r2rnav.tar.gz"

# CTD profiles (hex format)
curl -L -o raw/RR2402_160202_ctd.tar.gz \
  "https://g-1e773d.99817a.0ec8.data.globus.org/RR2402/RR2402_160202_ctd.tar.gz"

# Winch telemetry (LCI-90i)
curl -L -o raw/RR2402_160202_winch.tar.gz \
  "https://g-1e773d.99817a.0ec8.data.globus.org/RR2402/RR2402_160202_winch.tar.gz"

# Surface sound velocity
curl -L -o raw/RR2402_160202_ssv.tar.gz \
  "https://g-1e773d.99817a.0ec8.data.globus.org/RR2402/RR2402_160202_ssv.tar.gz"

# Fluorometer (optical sensors)
curl -L -o raw/RR2402_160202_fluorometer.tar.gz \
  "https://g-1e773d.99817a.0ec8.data.globus.org/RR2402/RR2402_160202_fluorometer.tar.gz"
```

### Archive Inspection

Examine archive metadata before processing:

```bash
# View bag-info.txt for R2R metadata
tar -xzf raw/RR2402_160202_ctd.tar.gz --to-stdout "*/bag-info.txt" 2>/dev/null

# Example output:
# R2R-CruiseID: RR2402
# R2R-DeviceType: ctd
# R2R-DeviceModel: SBE 911plus
# R2R-ProcessType: 0 (raw)
# Source-Organization: Rolling Deck to Repository (R2R) Program
```

---

## CTD Profile Processing

### Understanding CTD Archives

R2R CTD archives contain SeaBird SBE-911plus raw data:

| File Type | Extension | Description |
|-----------|-----------|-------------|
| Hex data  | `.hex`    | Raw sensor voltages (hexadecimal) |
| Header    | `.hdr`    | Station metadata (lat/lon, time, cast number) |
| Config    | `.xmlcon` | Sensor calibration coefficients |

### Single Cast Processing

```bash
# Process a single CTD hex file
oceanstream process geotrack convert \
  --provider r2r \
  --input-source raw/RR2402_cast007.hex \
  --output-dir processed \
  --campaign-id "$CRUISE_ID"
```

### Full Archive Processing

```bash
# Process entire CTD archive (all casts)
oceanstream process geotrack convert \
  --provider r2r \
  --input-source raw/RR2402_160202_ctd.tar.gz \
  --output-dir processed \
  --campaign-id "$CRUISE_ID" \
  --verbose
```

**Processing output**:
```
[INFO] Extracting archive: RR2402_160202_ctd.tar.gz
[INFO] Found 24 CTD cast files
[INFO] Processing cast: RR2402_cast001.hex (station 001)
[INFO]   → Parsed 4,521 scans, depth range: 0-1,523m
[INFO] Processing cast: RR2402_cast002.hex (station 002)
[INFO]   → Parsed 6,102 scans, depth range: 0-2,145m
...
[INFO] CTD processing complete: 24 casts, 142,156 total scans
[INFO] Output: processed/RR2402/ctd/
```

### CTD Output Variables

The processor extracts calibrated scientific values:

| Variable | Unit | Description |
|----------|------|-------------|
| `time` | ISO8601 | Scan timestamp |
| `latitude` | degrees_north | Station latitude (from .hdr) |
| `longitude` | degrees_east | Station longitude (from .hdr) |
| `pressure_dbar` | dbar | Pressure (strain gauge) |
| `depth_m` | m | Calculated depth |
| `temperature_C` | °C | In-situ temperature (ITS-90) |
| `temperature_2_C` | °C | Secondary temperature |
| `conductivity_mScm` | mS/cm | Conductivity |
| `conductivity_2_mScm` | mS/cm | Secondary conductivity |
| `salinity_psu` | PSU | Practical salinity |
| `oxygen_umol_kg` | µmol/kg | Dissolved oxygen |
| `fluorescence_ug_L` | µg/L | Chlorophyll fluorescence |
| `cast_number` | - | Station/cast identifier |

### Python: CTD Analysis

```python
import polars as pl
import matplotlib.pyplot as plt

# Load CTD data
ctd = pl.read_parquet("processed/RR2402/ctd/**/*.parquet")

# Profile summary
print(f"Total scans: {len(ctd):,}")
print(f"Casts: {ctd['cast_number'].n_unique()}")
print(f"Depth range: {ctd['depth_m'].min():.1f} - {ctd['depth_m'].max():.1f} m")

# T-S diagram for all casts
fig, ax = plt.subplots(figsize=(10, 8))
scatter = ax.scatter(
    ctd["salinity_psu"].to_numpy(),
    ctd["temperature_C"].to_numpy(),
    c=ctd["depth_m"].to_numpy(),
    cmap="viridis_r",
    s=1,
    alpha=0.5
)
ax.set_xlabel("Salinity (PSU)")
ax.set_ylabel("Temperature (°C)")
ax.set_title(f"T-S Diagram - {CRUISE_ID}")
plt.colorbar(scatter, label="Depth (m)")
plt.savefig("ts_diagram.png", dpi=150)
```

---

## Winch Telemetry Processing

### LCI-90i Data Format

R2R winch archives contain MacArtney/Markey LCI-90i monitoring data—high-frequency (20 Hz) telemetry of wire payout, tension, and speed.

```bash
# Process winch archive
oceanstream process geotrack convert \
  --provider r2r \
  --input-source raw/RR2402_160202_winch.tar.gz \
  --output-dir processed \
  --campaign-id "$CRUISE_ID"
```

### Raw Data Format

LCI-90i data uses a binary-delimited ASCII format:

```
2022-06-14T06:25:23.876888Z <RS><SOH>03RD,2022-05-14T16:17:36.502,-0000168,00000000,-00004.8,2839
```

Where:
- `<RS><SOH>` = Record Separator (0x1E) + Start of Header (0x01)
- Fields: timestamp, device_id, instrument_time, wire_out, turns, speed, tension

### Output Variables

| Variable | Unit | Description |
|----------|------|-------------|
| `time` | ISO8601 | Logged timestamp |
| `device_id` | - | Winch identifier (e.g., "03RD") |
| `time_instrument` | ISO8601 | Instrument timestamp |
| `wire_out_m` | m | Wire payout (negative = out) |
| `turns` | count | Drum rotation counter |
| `wire_speed_mps` | m/s | Wire speed (negative = paying out) |
| `tension_lbs` | lbs | Wire tension |

### Python: Winch Analysis

```python
import polars as pl
import matplotlib.pyplot as plt

# Load winch data
winch = pl.read_csv("processed/RR2402/winch.csv", try_parse_dates=True)

print(f"Total records: {len(winch):,}")
print(f"Time span: {winch['time'].min()} to {winch['time'].max()}")

# Identify deployment events (wire out increases rapidly)
deployments = winch.filter(
    (pl.col("wire_out_m").abs() > 100) & 
    (pl.col("wire_speed_mps") < -0.5)  # Paying out
)

print(f"Active deployment records: {len(deployments):,}")

# Plot tension vs wire out for a deployment
fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 8), sharex=True)

# Wire out over time
ax1.plot(winch["time"].to_numpy(), -winch["wire_out_m"].to_numpy(), linewidth=0.5)
ax1.set_ylabel("Wire Out (m)")
ax1.set_title(f"Winch Operations - {CRUISE_ID}")
ax1.grid(True, alpha=0.3)

# Tension over time
ax2.plot(winch["time"].to_numpy(), winch["tension_lbs"].to_numpy(), linewidth=0.5, color="orange")
ax2.set_ylabel("Tension (lbs)")
ax2.set_xlabel("Time")
ax2.grid(True, alpha=0.3)

plt.tight_layout()
plt.savefig("winch_operations.png", dpi=150)
```

### Correlating CTD Casts with Winch Data

```python
import polars as pl

# Load both datasets
ctd = pl.read_parquet("processed/RR2402/ctd/**/*.parquet")
winch = pl.read_csv("processed/RR2402/winch.csv", try_parse_dates=True)

# For each CTD cast, find corresponding winch data
for cast_num in ctd["cast_number"].unique().sort():
    cast_data = ctd.filter(pl.col("cast_number") == cast_num)
    
    cast_start = cast_data["time"].min()
    cast_end = cast_data["time"].max()
    max_depth = cast_data["depth_m"].max()
    
    # Find winch data during this cast
    winch_during_cast = winch.filter(
        (pl.col("time") >= cast_start) & 
        (pl.col("time") <= cast_end)
    )
    
    if len(winch_during_cast) > 0:
        max_wire_out = winch_during_cast["wire_out_m"].abs().max()
        max_tension = winch_during_cast["tension_lbs"].max()
        
        print(f"Cast {cast_num:03d}: depth={max_depth:.0f}m, "
              f"wire_out={max_wire_out:.0f}m, max_tension={max_tension:.0f}lbs")
```

---

## Navigation Data (GeoCSV)

### Processing Navigation Archives

```bash
oceanstream process geotrack convert \
  --provider r2r \
  --input-source raw/RR2402_160202_r2rnav.tar.gz \
  --output-dir processed \
  --campaign-id "$CRUISE_ID"
```

### Navigation Variables

| Variable | Unit | Description |
|----------|------|-------------|
| `time` | ISO8601 | Timestamp |
| `latitude` | degrees_north | Ship latitude |
| `longitude` | degrees_east | Ship longitude |
| `depth` | m | Water depth (echosounder) |
| `speed_over_ground` | m/s | Speed made good |
| `course_over_ground` | degrees | Course made good |
| `heading` | degrees | Ship heading (gyro) |

### Track Visualization

```python
import geopandas as gpd
import matplotlib.pyplot as plt
import contextily as cx

# Load navigation data
nav = gpd.read_parquet("processed/RR2402/**/*.parquet")

# Plot ship track
fig, ax = plt.subplots(figsize=(12, 10))
nav.plot(ax=ax, markersize=0.5, alpha=0.6)

# Add basemap
cx.add_basemap(ax, crs=nav.crs, source=cx.providers.Esri.OceanBasemap)

ax.set_title(f"Cruise Track - {CRUISE_ID}")
plt.savefig("cruise_track.png", dpi=150, bbox_inches="tight")
```

---

## Auxiliary Sensors

### Surface Sound Velocity (SSV)

```bash
oceanstream process geotrack convert \
  --provider r2r \
  --input-source raw/RR2402_160202_ssv.tar.gz \
  --output-dir processed \
  --campaign-id "$CRUISE_ID"
```

**Output variables**: `time`, `sound_velocity` (m/s)

### Fluorometer

```bash
oceanstream process geotrack convert \
  --provider r2r \
  --input-source raw/RR2402_160202_fluorometer.tar.gz \
  --output-dir processed \
  --campaign-id "$CRUISE_ID"
```

**Output variables**: `time`, `local_date`, `local_time`, `ch1`, `ch2`, `ch3` (fluorescence channels)

---

## Batch Processing Workflow

### Process All Sensors from a Cruise

```bash
#!/bin/bash
# process_cruise.sh

CRUISE_ID="${1:-RR2402}"
RAW_DIR="raw"
OUT_DIR="processed"

echo "Processing cruise: $CRUISE_ID"

# Find all archives for this cruise
for archive in "$RAW_DIR"/${CRUISE_ID}*.tar.gz; do
    if [[ -f "$archive" ]]; then
        sensor=$(basename "$archive" | sed -E 's/.*_([^_]+)\.tar\.gz$/\1/')
        echo "Processing: $archive ($sensor)"
        
        oceanstream process geotrack convert \
            --provider r2r \
            --input-source "$archive" \
            --output-dir "$OUT_DIR" \
            --campaign-id "$CRUISE_ID" \
            --verbose 2>&1 | tee -a "processing_${CRUISE_ID}.log"
    fi
done

echo "Processing complete. Outputs in: $OUT_DIR/$CRUISE_ID/"
```

```bash
chmod +x process_cruise.sh
./process_cruise.sh RR2402
```

### Generate STAC Catalog

```bash
# Verify STAC metadata was generated
ls -la processed/$CRUISE_ID/stac/

# Validate STAC collection
cat processed/$CRUISE_ID/stac/collection.json | python -m json.tool
```

---

## Data Integration

### Multi-Sensor Time-Series Merge

```python
import polars as pl

# Load all datasets
nav = pl.read_parquet("processed/RR2402/**/*.parquet")
ssv = pl.read_csv("processed/RR2402/ssv.csv", try_parse_dates=True)
winch = pl.read_csv("processed/RR2402/winch.csv", try_parse_dates=True)

# Resample winch data to 1-second resolution (from 20Hz)
winch_1s = winch.group_by_dynamic("time", every="1s").agg([
    pl.col("wire_out_m").mean(),
    pl.col("tension_lbs").mean(),
    pl.col("wire_speed_mps").mean(),
])

# Join navigation with SSV
nav_with_ssv = nav.join_asof(
    ssv.select(["time", "sound_velocity"]),
    on="time",
    tolerance="5s"
)

# Join with winch data
full_dataset = nav_with_ssv.join_asof(
    winch_1s,
    on="time",
    tolerance="5s"
)

print(f"Merged dataset: {len(full_dataset):,} records")
print(f"Columns: {full_dataset.columns}")

# Export merged dataset
full_dataset.write_parquet(f"processed/RR2402/merged_sensors.parquet")
```

### Export for External Tools

```python
# Export to NetCDF for oceanographic analysis tools
import xarray as xr

ctd = pl.read_parquet("processed/RR2402/ctd/**/*.parquet")

# Convert to xarray
ds = xr.Dataset({
    "temperature": (["time"], ctd["temperature_C"].to_numpy()),
    "salinity": (["time"], ctd["salinity_psu"].to_numpy()),
    "pressure": (["time"], ctd["pressure_dbar"].to_numpy()),
    "depth": (["time"], ctd["depth_m"].to_numpy()),
    "oxygen": (["time"], ctd["oxygen_umol_kg"].to_numpy()),
},
coords={
    "time": ctd["time"].to_numpy(),
    "latitude": (["time"], ctd["latitude"].to_numpy()),
    "longitude": (["time"], ctd["longitude"].to_numpy()),
})

ds.attrs["cruise_id"] = "RR2402"
ds.attrs["vessel"] = "R/V Roger Revelle"
ds.attrs["conventions"] = "CF-1.8"

ds.to_netcdf("processed/RR2402/ctd_profiles.nc")
```

---

## Troubleshooting

### CTD Processing Errors

**Error**: `seabirdscientific not installed`
```bash
pip install seabirdscientific>=2.7.0
```

**Error**: `Missing .xmlcon calibration file`
```
Ensure the .xmlcon file is in the same directory as the .hex file,
or included in the archive. Without calibration coefficients, raw 
hex values cannot be converted to scientific units.
```

**Error**: `Invalid hex data format`
```
The hex file may be corrupted or from an unsupported instrument.
OceanStream supports SBE 911plus/917plus. Check the .hdr file for
instrument type information.
```

### Winch Data Issues

**Empty output**: Check that the raw data format matches LCI-90i specification:
```bash
# Inspect first few lines
tar -xzf raw/winch.tar.gz --to-stdout "*/data/*" 2>/dev/null | head -5 | od -c
```

Look for `\036\001` (RS+SOH) control characters between timestamp and device ID.

### Large Archive Processing

For multi-gigabyte archives, consider:

```bash
# Process with reduced memory footprint
oceanstream process geotrack convert \
  --provider r2r \
  --input-source raw/RR2402_winch.tar.gz \
  --output-dir processed \
  --campaign-id "$CRUISE_ID" \
  --chunk-size 100000
```

---

## Resources

| Resource | URL |
|----------|-----|
| R2R Data Portal | https://www.rvdata.us/ |
| Cruise Search | https://www.rvdata.us/search |
| Globus Endpoint | https://g-1e773d.99817a.0ec8.data.globus.org/ |
| SeaBird Scientific | https://www.seabird.com/ |
| seabirdscientific PyPI | https://pypi.org/project/seabirdscientific/ |

---

## Next Steps

- [R2R Provider Reference](../features/data-providers/r2r.md) - Column mappings, metadata extraction
- [Saildrone Example](saildrone-basic.md) - Autonomous vehicle data processing
- [STAC Integration](../integrations/stac.md) - Metadata catalogs and discovery
