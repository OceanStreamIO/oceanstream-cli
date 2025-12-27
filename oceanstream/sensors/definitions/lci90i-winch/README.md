# LCI-90i Winch Monitoring System

The LCI-90i is a precision winch monitoring system manufactured by MacArtney (formerly Markey) widely used on US academic research vessels. It provides real-time measurements of wire payout, tension, and speed for various oceanographic deployment operations.

## Variables

| Variable | Unit | Description |
|----------|------|-------------|
| `wire_out_m` | meters | Wire payout length (negative values indicate wire out) |
| `tension_lbs` | pounds | Wire tension at the winch |
| `wire_speed_mps` | m/s | Wire speed (negative = paying out) |
| `turns` | count | Drum rotation counter |

## R2R Data Format

R2R winch data files contain ASCII lines with the following format:

```
<timestamp_logged> <RS><SOH><device_id>,<timestamp_instrument>,<wire_out>,<turns>,<speed>,<tension>
```

Example:
```
2022-06-14T06:25:23.876888Z 03RD,2022-05-14T16:17:36.502,-0000168,00000000,-00004.8,2839
```

**Note**: The RS (Record Separator, 0x1E) and SOH (Start of Header, 0x01) control characters appear between the logged timestamp and device ID. Lines are typically terminated with `\r\n\n`.

## Processing

The `oceanstream.sensors.processors.r2r_winch` module provides:

- `parse_winch_file(file_path)` - Parse a single winch data file into records
- `winch_raw_processor(data_dir, ...)` - Convert winch files to CSV format

## Usage Example

```python
from pathlib import Path
from oceanstream.sensors.processors.r2r_winch import parse_winch_file

# Parse winch data
records = parse_winch_file(Path("winch_lci90i_rr_trawl-2022-06-14"))

for r in records[:5]:
    print(f"Time: {r['time']}, Wire: {r['wire_out_m']:.1f}m, Tension: {r['tension_lbs']}lbs")
```

## Typical Deployments

- **Trawl winch**: Used for net tows and bottom sampling
- **CTD winch**: Used for CTD/rosette deployments
- **Deep-tow winch**: Used for deep-sea instruments

## R2R Archives

Winch data archives from R2R are identified by:
- Filename pattern: `*_winch.tar.gz`
- `R2R-DeviceType: winch` in bag-info.txt
- `R2R-DeviceModel: Markey DUTW-9-11` (or similar model)
