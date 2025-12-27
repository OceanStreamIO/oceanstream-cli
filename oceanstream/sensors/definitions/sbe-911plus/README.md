# Sea-Bird SBE 911plus CTD

The SBE 911plus is the industry-standard ship-based profiling CTD system used by research vessels worldwide. It provides continuous vertical profiles of temperature, conductivity, and pressure, with support for multiple auxiliary sensors.

## Typical Configuration

A standard SBE 911plus package includes:

- **Primary sensors**: Temperature (SBE 3plus), Conductivity (SBE 4C), Pressure (Digiquartz)
- **Secondary sensors**: Redundant T/C pair for quality control
- **Auxiliary sensors** (via voltage channels):
  - Dissolved oxygen (SBE 43)
  - Fluorometer (WET Labs, Seapoint)
  - Transmissometer (WET Labs C-Star)
  - PAR sensor
  - Altimeter
  - SUNA nitrate sensor

## File Formats

| Extension | Description |
|-----------|-------------|
| `.hex` | Raw hexadecimal data (frequency counts, voltages) |
| `.hdr` | Header metadata (lat/lon, time, station info) |
| `.xmlcon` | XML configuration with sensor calibration coefficients |
| `.cnv` | Processed ASCII data from SBE Data Processing |
| `.bl` | Bottle fire timestamps |
| `.btl` | Bottle summary (averaged values at bottle depths) |

## Processing with OceanStream

The R2R CTD processor uses the official `seabirdscientific` Python library to parse hex files:

```python
from oceanstream.sensors.processors.r2r_ctd import process_ctd_cast, find_cast_files

# Find all casts in a directory
casts = find_cast_files(Path("/path/to/ctd/data"))

# Process a single cast
for cast in casts:
    df = process_ctd_cast(cast, output_dir=Path("/output"))
```

## Data Variables

### Raw (frequency/voltage)
- `temperature_freq` - Primary temperature frequency (Hz)
- `conductivity_freq` - Primary conductivity frequency (Hz)
- `pressure_freq` - Digiquartz pressure frequency (Hz)
- `volt0`-`volt7` - Auxiliary voltage channels (0-5V)

### Processed (engineering units)
- `temperature` - Temperature (°C, ITS-90)
- `conductivity` - Conductivity (S/m)
- `pressure` - Pressure (dbar)
- `salinity` - Practical salinity (PSU)
- `depth` - Depth (m)
- `density` - Density (kg/m³)
- `oxygen` - Dissolved oxygen (ml/L or µmol/kg)

## References

- [Sea-Bird SBE 911plus Product Page](https://www.seabird.com/sbe-911plus-ctd/product?id=60761421596)
- [seabirdscientific GitHub](https://github.com/Sea-BirdScientific/seabirdscientific)
- [SBE Data Processing Manual](https://www.seabird.com/software)
