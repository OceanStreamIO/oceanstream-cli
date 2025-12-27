# Built-in Semantic Tables

This directory contains built-in CF Standard Names and alias tables that OceanStream uses by default for semantic mapping.

## Files

### cf-standard-names.json

A curated list of ~120 CF Standard Names commonly used in oceanographic and atmospheric observations. This subset focuses on:

- **Ocean variables**: temperature, salinity, pressure, density, currents
- **Atmospheric variables**: air temperature, pressure, humidity, wind
- **Wave parameters**: significant height, period, direction
- **Biogeochemical**: oxygen, pH, chlorophyll, nutrients, carbon dioxide
- **Platform/navigation**: course, speed, heading, pitch, roll
- **Optical**: turbidity, backscatter, CDOM, PAR

**Source**: Subset of the official [CF Standard Names Table](http://cfconventions.org/standard-names.html)

**Format**: JSON array of standard names

**Usage**: Automatically loaded if no custom CF table is provided via `SEMANTIC_CF_TABLE` or `semantic.cf_table` config.

### saildrone-aliases.json

A comprehensive mapping of Saildrone platform-specific column names to canonical forms. Includes:

- **Saildrone naming conventions**: `TEMP_CTD_RBR_MEAN`, `SAL_RBR_MEAN`, etc.
- **Common oceanographic abbreviations**: `sst`, `psal`, `chla`, `do`, etc.
- **Instrument-specific variants**: CTD, RBR, SBE37, WetLabs sensors
- **Atmospheric sensors**: barometric pressure, wind, radiation
- **Navigation sensors**: course over ground, speed, heading
- **Biogeochemical sensors**: O2, CO2, pH, chlorophyll, turbidity
- **Wave sensors**: significant height, period, direction

**Format**: JSON dictionary mapping canonical names to lists of aliases

**Usage**: Automatically loaded if no custom alias table is provided via `SEMANTIC_ALIAS_TABLE` or `semantic.alias_table` config.

## Extending the Built-in Tables

### To add more CF standard names:

1. Check the [official CF Standard Names](http://cfconventions.org/standard-names.html)
2. Add the exact standard name to `cf-standard-names.json` (keep lowercase for internal normalization)
3. Test with your data

### To add more aliases:

1. Identify column names in your data sources
2. Add them to the appropriate canonical name in `saildrone-aliases.json`
3. Consider creating a separate alias file for other platforms (e.g., `noaa-buoy-aliases.json`)

## Override Behavior

Users can override these built-in tables by:

1. **Environment variables**:
   ```bash
   SEMANTIC_CF_TABLE=/path/to/custom-cf.json
   SEMANTIC_ALIAS_TABLE=/path/to/custom-aliases.json
   ```

2. **Configuration file** (`oceanstream.toml`):
   ```toml
   [semantic]
   cf_table = "/path/to/custom-cf.json"
   alias_table = "/path/to/custom-aliases.json"
   ```

**Priority**: User-supplied paths > Built-in tables > Empty (heuristic-only fallback)

## Updating Built-in Tables

When updating these tables:

1. Ensure valid JSON syntax
2. Keep CF names lowercase (internal normalization requirement)
3. Test with representative datasets
4. Document significant additions in this README
5. Consider backward compatibility

## Related Documentation

- **User Guide**: `docs/semantic-tables-guide.md` - How to create and use custom tables
- **Feature Spec**: `docs/features.md` - Semantic enrichment MVP design
- **Configuration**: `docs/configuration.md` - How to configure semantic settings
