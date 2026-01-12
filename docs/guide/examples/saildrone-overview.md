# Saildrone Data Processing

## Overview

OceanStream provides comprehensive support for processing oceanographic data from Saildrone uncrewed surface vehicles (USVs). This section demonstrates how to ingest, process, and analyze Saildrone mission data using real examples from NOAA's PMEL (Pacific Marine Environmental Laboratory) missions.

## About Saildrone

Saildrone USVs are wind and solar-powered autonomous vehicles that collect high-resolution oceanographic and atmospheric data across vast ocean regions. These platforms carry a suite of sensors for measuring:

- **Physical oceanography**: Temperature, salinity, conductivity, ocean currents (ADCP)
- **Atmospheric measurements**: Wind speed/direction, air temperature, humidity, pressure
- **Radiation**: Solar irradiance, PAR (photosynthetically active radiation)
- **Biogeochemical**: Dissolved oxygen, chlorophyll, pH, pCO₂
- **Biomass**: Acoustic backscatter (EK80 echosounder)
- **Navigation**: GPS position, speed over ground, course, heading

The Saildrone Explorer-class platform is particularly well-suited for tropical Pacific missions, providing continuous measurements at the air-sea interface over missions lasting 100+ days.

## TPOS 2023 Mission Background

### Mission Overview

The examples in this guide use data from the **TPOS 2023 mission** (Tropical Pacific Observing System), conducted from June to November 2023. This mission deployed three Saildrone USVs (SD1030, SD1033, SD1079) to study:

1. **El Niño conditions** - The mission occurred during an El Niño phase, capturing above-average sea surface temperatures in the central and eastern tropical Pacific
2. **Equatorial upwelling** - Adaptive sampling techniques measured ocean upwelling along the equator
3. **Eastern edge of warm pool (EEWP)** - Tracked the warm pool expansion during El Niño
4. **Biomass surveys** - Tested the EK80 echosounder for fisheries science applications
5. **Air-sea interactions** - High-resolution flux measurements in convectively active regions

### Mission Details

- **Duration**: June 22 - November 5, 2023 (136 days)
- **Platforms**: SD1030, SD1033, SD1079 (Saildrone Explorer class)
- **Study Region**: Central tropical Pacific (18°N to 0°, 155°W to 170°W)
- **Total Distance**: ~10,000 km combined
- **Data Points**: Millions of observations across 70+ variables

**Key Highlights**:

- **Adaptive Sampling**: Drones performed box patterns at the equator to estimate upwelling velocities
- **Ship Intercomparison**: Successful fly-by with R/V Antea for EK80 validation
- **TAO Moorings**: Multiple intercomparisons with Tropical Atmosphere-Ocean buoys
- **El Niño Phase**: Captured the development of El Niño conditions in real-time
- **Global Ocean Pattern**: Established "Go-USV" repeat section for future missions

### Scientific Context

The mission contributed to several research objectives:

**Tropical Pacific Observing System (TPOS)**:  
TPOS aims to enhance the observing network in the tropical Pacific through a combination of moorings, Argo floats, gliders, and now USVs. Saildrones provide complementary measurements to fill spatial and temporal gaps.

**ENSO Monitoring**:  
El Niño-Southern Oscillation (ENSO) is a major driver of global climate variability. The 2023 mission captured El Niño development, including:
- Weakening of easterly trade winds
- Reduction in equatorial upwelling
- Eastward expansion of the warm pool
- Changes in atmospheric convection patterns

**Fisheries Science Collaboration**:  
Partnership with NOAA NMFS Pacific Islands Fisheries Science Center tested USVs for:
- Fish biomass surveys using EK80 acoustics
- Mesopelagic zone (200-1000m depth) organism detection
- Cost-effective alternative to ship-based surveys

## Data Access

### PMEL ERDDAP Server

All Saildrone data from TPOS missions are publicly available through NOAA PMEL's ERDDAP server:

**Data Portal**: [https://data.pmel.noaa.gov/pmel/erddap/](https://data.pmel.noaa.gov/pmel/erddap/)

**Direct TPOS Search**: [TPOS Datasets](https://data.pmel.noaa.gov/pmel/erddap/search/index.html?page=1&itemsPerPage=1000&searchFor=TPOS)

### Downloading Data

**Via ERDDAP Web Interface**:

1. Navigate to the ERDDAP TPOS search page
2. Select mission year (e.g., "2023_TPOS")
3. Choose individual drone datasets (SD1030, SD1033, SD1079)
4. Click "data" to access the data form
5. Select desired variables and time constraints
6. Choose output format (CSV recommended for OceanStream)
7. Click "Submit" to download

**Supported Formats**:
- `.csv` - Human-readable, works with OceanStream
- `.nc` - NetCDF binary format (more metadata)
- `.mat` - MATLAB format
- `.htmlTable` - Web browser viewing
- `.json` - JSON format for APIs

**Via Command Line**:

```bash
# Download full mission data for SD1030
wget "https://data.pmel.noaa.gov/pmel/erddap/tabledap/sd1030_2023_tpos.csv" \
  -O sd1030_tpos_2023.csv

# Download with time constraints
wget "https://data.pmel.noaa.gov/pmel/erddap/tabledap/sd1030_2023_tpos.csv?\
time,latitude,longitude,TEMP_SBE37_MEAN,SAL_SBE37_MEAN&\
time>=2023-07-01T00:00:00Z&time<=2023-07-31T23:59:59Z" \
  -O sd1030_july2023.csv

# Download specific variables only
wget "https://data.pmel.noaa.gov/pmel/erddap/tabledap/sd1030_2023_tpos.csv?\
time,latitude,longitude,trajectory,\
TEMP_SBE37_MEAN,SAL_SBE37_MEAN,O2_CONC_SBE37_MEAN,CHLOR_WETLABS_MEAN" \
  -O sd1030_bio_physical.csv
```

### Data Characteristics

**Temporal Resolution**: 1-minute sampling (primary dataset)

**Spatial Coverage**:
- Latitude: 0° to 20°N (equatorial to subtropical)
- Longitude: 155°W to 170°W (central Pacific)

**Data Volume**:
- Single drone, full mission: ~200,000 rows, 70+ columns
- File size: ~50-100 MB per drone (CSV format)
- Combined mission: ~600,000 observations

**Quality Control**:
- Real-time quality flags available in ERDDAP
- Post-mission calibrations applied
- Sensor-specific QC for each variable type

## Quick Start Example

Let's process data from SD1030 (the first drone deployed):

```bash
# Download sample data
wget "https://data.pmel.noaa.gov/pmel/erddap/tabledap/sd1030_2023_tpos.csv" \
  -O sd1030_tpos_2023.csv

# Process with OceanStream
oceanstream process geotrack \
  --input-source ./sd1030_tpos_2023.csv \
  --output-dir ./saildrone_output \
  --campaign-id tpos_2023

# View campaign info
oceanstream campaign show tpos_2023
```

This creates:
- **GeoParquet files**: Spatially binned by 1° × 1° tiles
- **STAC metadata**: Collection and item-level metadata
- **Campaign tracking**: Registered in `~/.oceanstream/campaigns/`

## Example Data Files

The examples in this guide use sample CSV files extracted from the TPOS 2023 mission:

| File | Platform | Time Range | Rows | Size | Description |
|------|----------|------------|------|------|-------------|
| `sd1030_tpos_2023_*.csv` | SD1030 | Nov 6-8, 2023 | ~11,500 | ~2 MB | Mission end near Hawaii |
| `sd1033_tpos_2023_*.csv` | SD1033 | Oct 15-17, 2023 | ~8,600 | ~1.5 MB | Equatorial upwelling study |
| `sd1079_tpos_2023_*.csv` | SD1079 | Sep 10-12, 2023 | ~10,200 | ~1.8 MB | EK80 biomass surveys |

These files demonstrate typical Saildrone data structure and can be used to test OceanStream workflows without downloading the full multi-gigabyte datasets.

## Next Steps

Explore the detailed tutorials:

1. **[Basic Processing](saildrone-basic.md)** - Process a single Saildrone CSV file
<!-- TODO: Add these advanced examples
2. **Multiple Platforms** - Combine data from all three drones
3. **Time Series Analysis** - Extract and analyze temporal patterns
4. **Spatial Analysis** - Work with spatial bins and GeoParquet
5. **Sensor-Specific** - Focus on particular sensor types
-->

## Additional Resources

**PMEL Resources**:
- [TPOS 2023 Mission Blog](https://www.pmel.noaa.gov/ocs/ocs-saildrone-mission-tpos-2023)
- [Saildrone Data Access Guide](https://www.pmel.noaa.gov/ocs/saildrone/data-access)
- [Real-time Dashboard](https://viz.pmel.noaa.gov/saildrone/)

**Saildrone Inc**:
- [Platform Specifications](https://www.saildrone.com/products/explorer)
- [Sensor Suite Documentation](https://www.saildrone.com/science)

**Scientific Background**:
- [TPOS-2020 Project](https://tropicalpacific.org/)
- [ENSO at NOAA](https://www.pmel.noaa.gov/elnino/)
- [Ocean Climate Stations](https://www.pmel.noaa.gov/ocs/)
