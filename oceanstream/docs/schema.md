# Oceanstream GeoParquet schema

This document defines the dataset schema and metadata for ingesting Saildrone (or similar) CSVs into a partitioned GeoParquet dataset.

## Geometry and CRS
- Geometry column: `geometry` as POINT(longitude, latitude)
- Encoding: WKB (Well-Known Binary)
- CRS: EPSG:4326. Store coordinates as (lon, lat) in degrees.
- GeoParquet metadata: see `docs/geo-metadata.json` (to be embedded in Parquet file key-value metadata under key `geo`).

## Required columns (canonical types)
- `platform_id` (string): derived from filename by convention; example: `sd1030` from `sd1030_tpos_2023_*.csv`.
- `platform_token` (string): second underscore-delimited token from filename; example: `tpos`.
- `trajectory` (int32): numeric platform identifier if present in CSV (e.g., 1030).
- `time` (timestamp[ms, tz=UTC]): parsed from CSV `time` column (ISO-8601, e.g., `2023-11-06T00:00:00Z`).
- `latitude` (float64, degrees_north): range [-90, 90].
- `longitude` (float64, degrees_east): range [-180, 180].
- `geometry` (binary/WKB): POINT constructed from (`longitude`, `latitude`).
- `lat_bin` (categorical/string): bin label from `pandas.cut(latitude, lat_edges)`; used for partitioning.
- `lon_bin` (categorical/string): bin label from `pandas.cut(longitude, lon_edges)`; used for partitioning.

## Column naming policy
- Retain original CSV column names in the dataset (no renaming on write).
- Column aliases are generated dynamically by the provider's `alias_mapping()` method using `_normalize_alias()`.
- Normalized aliases are snake_case versions of column names and are embedded in Parquet file key-value metadata under `oceanstream:aliases`.

## Measurement fields (complete list)
All columns below are nullable unless stated. Types are logical Arrow types. Units come from the CSV's second header row and should also be preserved in Parquet file metadata under `oceanstream:units`.

Angles, orientation, and kinematics
- csv: `SOG` → field: `speed_over_ground` (float32, m s-1)
- csv: `SOG_FILTERED_MEAN` → `speed_over_ground_filtered_mean` (float32, m s-1)
- csv: `SOG_FILTERED_STDDEV` → `speed_over_ground_filtered_stddev` (float32, m s-1)
- csv: `SOG_FILTERED_MAX` → `speed_over_ground_filtered_max` (float32, m s-1)
- csv: `SOG_FILTERED_MIN` → `speed_over_ground_filtered_min` (float32, m s-1)
- csv: `COG` → `course_over_ground` (float32, degree)
- csv: `COG_FILTERED_MEAN` → `course_over_ground_filtered_mean` (float32, degree)
- csv: `COG_FILTERED_STDDEV` → `course_over_ground_filtered_stddev` (float32, degree)
- csv: `HDG` → `heading` (float32, degree)
- csv: `HDG_FILTERED_MEAN` → `heading_filtered_mean` (float32, degree)
- csv: `HDG_FILTERED_STDDEV` → `heading_filtered_stddev` (float32, degree)
- csv: `ROLL_FILTERED_MEAN` → `roll_filtered_mean` (float32, degree)
- csv: `ROLL_FILTERED_STDDEV` → `roll_filtered_stddev` (float32, degree)
- csv: `ROLL_FILTERED_PEAK` → `roll_filtered_peak` (float32, degree)
- csv: `PITCH_FILTERED_MEAN` → `pitch_filtered_mean` (float32, degree)
- csv: `PITCH_FILTERED_STDDEV` → `pitch_filtered_stddev` (float32, degree)
- csv: `PITCH_FILTERED_PEAK` → `pitch_filtered_peak` (float32, degree)

Wing state
- csv: `HDG_WING` → `wing_heading` (float32, degree)
- csv: `WING_HDG_FILTERED_MEAN` → `wing_heading_filtered_mean` (float32, degree)
- csv: `WING_HDG_FILTERED_STDDEV` → `wing_heading_filtered_stddev` (float32, degree)
- csv: `WING_ROLL_FILTERED_MEAN` → `wing_roll_filtered_mean` (float32, degree)
- csv: `WING_ROLL_FILTERED_STDDEV` → `wing_roll_filtered_stddev` (float32, degree)
- csv: `WING_ROLL_FILTERED_PEAK` → `wing_roll_filtered_peak` (float32, degree)
- csv: `WING_PITCH_FILTERED_MEAN` → `wing_pitch_filtered_mean` (float32, degree)
- csv: `WING_PITCH_FILTERED_STDDEV` → `wing_pitch_filtered_stddev` (float32, degree)
- csv: `WING_PITCH_FILTERED_PEAK` → `wing_pitch_filtered_peak` (float32, degree)
- csv: `WING_ANGLE` → `wing_angle` (float32, degree)

Wind
- csv: `WIND_FROM_MEAN` → `wind_from_mean` (float32, degree)
- csv: `WIND_FROM_STDDEV` → `wind_from_stddev` (float32, degree)
- csv: `WIND_SPEED_MEAN` → `wind_speed_mean` (float32, m s-1)
- csv: `WIND_SPEED_STDDEV` → `wind_speed_stddev` (float32, m s-1)
- csv: `UWND_MEAN` → `wind_u_mean` (float32, m s-1)
- csv: `UWND_STDDEV` → `wind_u_stddev` (float32, m s-1)
- csv: `VWND_MEAN` → `wind_v_mean` (float32, m s-1)
- csv: `VWND_STDDEV` → `wind_v_stddev` (float32, m s-1)
- csv: `WWND_MEAN` → `wind_w_mean` (float32, m s-1)
- csv: `WWND_STDDEV` → `wind_w_stddev` (float32, m s-1)
- csv: `GUST_WND_MEAN` → `wind_gust_mean` (float32, m s-1)
- csv: `GUST_WND_STDDEV` → `wind_gust_stddev` (float32, m s-1)
- csv: `WIND_MEASUREMENT_HEIGHT_MEAN` → `wind_measurement_height_mean` (float32, m)
- csv: `WIND_MEASUREMENT_HEIGHT_STDDEV` → `wind_measurement_height_stddev` (float32, m)

Atmospheric state
- csv: `TEMP_AIR_MEAN` → `air_temperature_mean` (float32, degree_C)
- csv: `TEMP_AIR_STDDEV` → `air_temperature_stddev` (float32, degree_C)
- csv: `RH_MEAN` → `relative_humidity_mean` (float32, percent)
- csv: `RH_STDDEV` → `relative_humidity_stddev` (float32, percent)
- csv: `BARO_PRES_MEAN` → `barometric_pressure_mean` (float32, hPa)
- csv: `BARO_PRES_STDDEV` → `barometric_pressure_stddev` (float32, hPa)

Radiation and optics (atmosphere)
- csv: `PAR_AIR_MEAN` → `par_air_mean` (float32, micromol s-1 m-2)
- csv: `PAR_AIR_STDDEV` → `par_air_stddev` (float32, micromol s-1 m-2)
- csv: `SW_IRRAD_TOTAL_MEAN` → `shortwave_irradiance_total_mean` (float32, W m-2)
- csv: `SW_IRRAD_TOTAL_STDDEV` → `shortwave_irradiance_total_stddev` (float32, W m-2)
- csv: `SW_IRRAD_DIFFUSE_MEAN` → `shortwave_irradiance_diffuse_mean` (float32, W m-2)
- csv: `SW_IRRAD_DIFFUSE_STDDEV` → `shortwave_irradiance_diffuse_stddev` (float32, W m-2)

Sea-surface temperature (IR wing)
- csv: `TEMP_IR_SEA_WING_UNCOMP_MEAN` → `sea_surface_temp_ir_uncomp_mean` (float32, degree_C)
- csv: `TEMP_IR_SEA_WING_UNCOMP_STDDEV` → `sea_surface_temp_ir_uncomp_stddev` (float32, degree_C)

Waves
- csv: `WAVE_DOMINANT_PERIOD` → `wave_dominant_period` (float32, s)
- csv: `WAVE_SIGNIFICANT_HEIGHT` → `wave_significant_height` (float32, m)

Subsurface (hull/thermistor)
- csv: `TEMP_DEPTH_HALFMETER_MEAN` → `temperature_0p5m_mean` (float32, degree_C)
- csv: `TEMP_DEPTH_HALFMETER_STDDEV` → `temperature_0p5m_stddev` (float32, degree_C)

SBE37 (CTD + oxygen)
- csv: `TEMP_SBE37_MEAN` → `sbe37_temperature_mean` (float32, degree_C)
- csv: `TEMP_SBE37_STDDEV` → `sbe37_temperature_stddev` (float32, degree_C)
- csv: `SAL_SBE37_MEAN` → `sbe37_salinity_mean` (float32, 1)  # PSU
- csv: `SAL_SBE37_STDDEV` → `sbe37_salinity_stddev` (float32, 1)
- csv: `COND_SBE37_MEAN` → `sbe37_conductivity_mean` (float32, mS cm-1)
- csv: `COND_SBE37_STDDEV` → `sbe37_conductivity_stddev` (float32, mS cm-1)
- csv: `O2_CONC_SBE37_MEAN` → `sbe37_dissolved_oxygen_conc_mean` (float32, micromol L-1)
- csv: `O2_CONC_SBE37_STDDEV` → `sbe37_dissolved_oxygen_conc_stddev` (float32, micromol L-1)
- csv: `O2_SAT_SBE37_MEAN` → `sbe37_dissolved_oxygen_saturation_mean` (float32, percent)
- csv: `O2_SAT_SBE37_STDDEV` → `sbe37_dissolved_oxygen_saturation_stddev` (float32, percent)

Optical (chlorophyll)
- csv: `CHLOR_WETLABS_MEAN` → `chlorophyll_wetlabs_mean` (float32, microgram L-1)
- csv: `CHLOR_WETLABS_STDDEV` → `chlorophyll_wetlabs_stddev` (float32, microgram L-1)

Notes
- If additional columns appear in other voyages, include them by preserving original names and add normalized aliases following the patterns above.
- For angles in degrees, maintain [0,360) or documented instrument range; no unit conversion is performed during ingest.
- Where `*_mean`/`*_stddev` pairs exist, both are retained; peaks kept where provided.

Nullability: all measurement columns are nullable; keep NaN as null in Parquet/Arrow.

## Partitioning
- Dataset is written as a partitioned Parquet dataset with `partition_cols=['lat_bin','lon_bin']`.
- Treat the output path as a directory root; files are sharded under `lat_bin=.../lon_bin=.../part-*.parquet`.

## Vector tiles (PMTiles) integration
- After writing GeoParquet, generate a PMTiles file for web mapping (MapLibre).
- Recommended layer: point layer from the geometry, or an aggregated bin layer at low zooms.
- See `docs/pmtiles.md` for tooling, zoom levels, attribute selection, and Azure upload.

## Custom file metadata (embedded)
Embed the following JSON blocks in Parquet file key-value metadata to preserve provenance and column semantics:

- `oceanstream:units`: column -> unit mapping (when known)
- `oceanstream:aliases`: original column -> normalized alias (snake_case) mapping
- `oceanstream:provider`: provider info for the dataset/file (e.g., name, observed columns)

Example (store under key `oceanstream:units`):
```json
{
  "time": "UTC",
  "latitude": "degrees_north",
  "longitude": "degrees_east",
  "SOG": "m s-1",
  "COG": "degree",
  "TEMP_AIR_MEAN": "degree_C",
  "RH_MEAN": "percent",
  "BARO_PRES_MEAN": "hPa",
  "PAR_AIR_MEAN": "micromol s-1 m-2",
  "SW_IRRAD_TOTAL_MEAN": "W m-2"
}
```
This list is illustrative; derive the full mapping from the CSV's second header row.

Example alias mapping (`oceanstream:aliases`):
```json
{
  "TEMP_AIR_MEAN": "temp_air_mean",
  "SOG": "sog",
  "COG": "cog"
}
```

Example provider metadata (`oceanstream:provider`):
```json
{
  "name": "saildrone",
  "columns": ["platform_id", "latitude", "longitude", "time", "SOG", "TEMP_AIR_MEAN", "..."]
}
```
Downstream tools can use these blocks to interpret and standardize measurements without renaming stored columns.

## Ingestion rules
- Parse the CSV as having two header rows: row 1 = column names; row 2 = units (not data). Store row 2 in metadata and skip it as a data row.
- Add `platform_id` and `platform_token` from the filename. If `trajectory` exists, cast to `int32` when safe.
- Construct `geometry` from `longitude`, `latitude` with CRS EPSG:4326.
- Create `lat_bin`/`lon_bin` using consistent bin edges between binning and writer.
- Preserve all remaining columns by name; use float32 where feasible; use float64 for coordinates.

## Arrow/Parquet logical types (summary)
- `time`: timestamp[ms, tz=UTC]
- `latitude`, `longitude`: float64
- `trajectory`: int32 (nullable)
- `geometry`: binary (WKB)
- `lat_bin`, `lon_bin`: string (categorical labels)
- other numeric sensors: float32 (nullable)

## Validation checks
- `latitude` ∈ [-90, 90]; `longitude` ∈ [-180, 180]. Drop or quarantine rows outside range.
- `time` must parse to UTC; reject invalid timestamps.
- `geometry` non-null when lat/lon valid.
