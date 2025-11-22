# WET Labs ECO-FLNTU Fluorometer

## Overview

The WET Labs ECO-FLNTU is a combined fluorometer and turbidity sensor designed for oceanographic research. It measures chlorophyll fluorescence and turbidity (nephelometric turbidity units) in seawater.

## Key Features

- Simultaneous fluorescence and turbidity measurements
- Low power consumption
- High sampling rate (6 Hz)
- Depth-rated to 600 meters
- Digital output via RS-232 or RS-485

## Measured Variables

- **Chlorophyll Fluorescence** (`CHL_FLUOR`): Chlorophyll-a concentration in μg/L
- **Turbidity** (`TURBIDITY`): Nephelometric turbidity in NTU
- **Backscatter** (`BACKSCATTER`): Optical backscatter coefficient

## Technical Specifications

| Parameter | Value |
|-----------|-------|
| Chlorophyll Range | 0-50 μg/L |
| Chlorophyll Resolution | 0.025 μg/L |
| Turbidity Range | 0-25 NTU |
| Turbidity Resolution | 0.01 NTU |
| Sampling Rate | 6 Hz |
| Depth Rating | 600 m |
| Power Consumption | ~1W @ 12V |

## Deployment Notes

- Typically deployed on CTD rosettes or hull-mounted on research vessels
- Requires regular cleaning to prevent biofouling
- Factory calibration recommended annually
- Raw data requires conversion using manufacturer calibration coefficients

## Manufacturer Documentation

- [Sea-Bird ECO-FLNTU Product Page](https://www.seabird.com/eco-flntu/product?id=54627923875)
- [User Manual](https://www.seabird.com/asset-get.download.jsa?id=54627862351)

## Data Processing

R2R fluorometer raw data (`.Raw` files) is processed by the `r2r_fluorometer` sensor processor, which:

1. Parses proprietary binary/text format into standardized CSV
2. Applies timestamp corrections
3. Extracts three primary channels (typically chlorophyll, turbidity, backscatter)

Processed data is then available for geotrack pipeline ingestion.
