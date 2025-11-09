# Feature Spec: Automatic Semantic & Standards Enrichment (MVP)

## 1. Goal
Provide an automated enrichment layer that inspects ingested oceanographic (or general geospatial) tabular data and attaches standardized semantic metadata: CF Standard Names, canonical units, aliases, and a generated STAC Collection + Items referencing produced GeoParquet partitions. This eliminates manual mapping effort and increases interoperability with existing geospatial / Earth data tooling.

## 2. Non-Goals (MVP Boundaries)
- No UI or hosted API (CLI + library only).
- No advanced ontology reasoning (simple rule + heuristic matching only).
- No dynamic external vocabulary sync (single packaged snapshot of CF + auxiliary mapping tables).
- No write-back of transformed numeric data (units conversions optional but off by default).
- Not implementing full OGC API Features; we only emit static STAC JSON.

## 3. Data Assumptions
- Input DataFrame contains columns representing measured variables, plus mandatory spatial columns: `latitude`, `longitude`. Time column optional (`time` or `timestamp`).
- Variable names may use snake_case, camelCase, or include common sensor prefixes (e.g. `sd_`, `sea_water_`).
- Column values for candidate numeric variables are float/int; strings are ignored for semantic mapping except for taxonomy columns (not MVP).

## 4. Inputs / Outputs Contract
- Input: `pandas.DataFrame` (pre-binning) OR enriched `DataFrame` after binning; config parameters:
  - `semantic.enable: bool` (default True)
  - `semantic.min_variable_presence_ratio: float` (default 0.7) for deciding if a variable has enough non-null data to be considered.
  - `semantic.units_conversion: bool` (default False)
  - `semantic.generate_stac: bool` (default True)
  - `semantic.alias_table_path: Optional[str]` (override packaged alias rules)
  - `semantic.cf_table_path: Optional[str]` (override packaged CF mapping snapshot)
- Output:
  - Updated Parquet schema metadata keys:
    - `b"oceanstream:units"` (units mapping)
    - `b"oceanstream:aliases"` (normalized alias mapping)
    - `b"oceanstream:cf_standard_names"` (column->CF name JSON)
    - `b"oceanstream:semantic_version"` (schema version tag, e.g., `"sem-v0.1"`)
  - Optionally: STAC Collection JSON file and STAC Item JSON files (one per partition group or single dataset) in `geoparquet_root/stac/`.

## 5. Pipeline Stages (MVP)
1. Column Profiling
   - Gather basic stats: non-null ratio, dtype classification (numeric / temporal / geometry / categorical / other), min/max.
2. Candidate Variable Identification
   - Select numeric columns with non-null ratio >= threshold.
3. Name Normalization
   - Lowercase, replace camelCase boundaries with `_`, strip vendor prefixes (`sd_`, `saildrone_`, trailing `_raw`).
4. Alias Matching
   - Look up normalized name in alias table (JSON) -> returns canonical alias. If not found, canonical alias = normalized name.
5. CF Standard Name Mapping
   - Compare canonical alias against CF snapshot table (keyed by standard_name). Multi-pass heuristic:
     - Exact match.
     - Levenshtein distance <= 2 OR Jaro-Winkler >= 0.93 (library: `rapidfuzz`).
     - Token subset match (e.g., tokens of `sea_water_temperature` inside candidate).
     - If ambiguous (multiple candidates), choose the one with highest similarity score; record `confidence` metric.
6. Units Assignment & Optional Conversion
   - If CF mapping succeeded and CF table includes canonical units (e.g., `degC`), assign these.
   - If `units_conversion` enabled and column units differ (detected via suffix or known alternative, e.g., `temp_f`), convert data (store original name -> `<name>_raw` optionally, NOT in MVP unless trivial).
7. Metadata Assembly
   - Build dictionaries for aliases, units, CF names (with confidence). Confidence < 0.85 flagged.
8. STAC Generation (if enabled)
   - Create minimal `collection.json` with:
     - `id`, `description`, `license`, spatial extent (bbox from data), temporal extent (min/max time if available), `keywords` (CF names), links to assets.
   - Create one `item.json` representing entire dataset (MVP) referencing GeoParquet root; include `properties` with summary stats (counts, time range, variable list). Partition-level Items deferred.

## 6. Data Structures
- Alias Table JSON Example:
```json
{
  "sea_water_temperature": ["sst", "temp", "sea_temp"],
  "sea_water_salinity": ["salinity", "psu"]
}
```
- CF Snapshot (packaged) simplified JSON Example:
```json
{
  "sea_water_temperature": {"units": "degC", "description": "Sea water temperature"},
  "sea_water_salinity": {"units": "1e-3", "description": "Sea water salinity"}
}
```
- Produced Mapping JSON (embedded):
```json
{
  "columns": {
    "sea_water_temperature": {"cf_standard_name": "sea_water_temperature", "confidence": 1.0},
    "sea_water_salinity": {"cf_standard_name": "sea_water_salinity", "confidence": 0.94}
  }
}
```

## 7. CLI Additions
- New command group or flags on existing CLI:
  - `--semantic` (bool, default true)
  - `--semantic-alias-table PATH`
  - `--semantic-cf-table PATH`
  - `--semantic-units-conversion`
  - `--semantic-dump-report PATH` (writes profiling + matches as JSON/Markdown)
  - `--no-stac` (disable STAC generation)

## 8. Configuration Integration
Extend `Settings` with:
```python
SEMANTIC_ENABLE = os.getenv("SEMANTIC_ENABLE", "true").lower() == "true"
SEMANTIC_UNITS_CONVERSION = os.getenv("SEMANTIC_UNITS_CONVERSION", "false").lower() == "true"
SEMANTIC_ALIAS_TABLE = os.getenv("SEMANTIC_ALIAS_TABLE")  # optional path
SEMANTIC_CF_TABLE = os.getenv("SEMANTIC_CF_TABLE")        # optional path
SEMANTIC_MIN_PRESENCE = float(os.getenv("SEMANTIC_MIN_PRESENCE", "0.7"))
SEMANTIC_GENERATE_STAC = os.getenv("SEMANTIC_GENERATE_STAC", "true").lower() == "true"
```

## 9. Extensibility (Design for vNext)
- Abstract `SemanticMapper` class with method hooks: `profile(df)`, `match_aliases(cols)`, `map_cf(columns)`, `assign_units(mapping)`, `emit_metadata(mapping)`, `emit_stac(...)`.
- Plugin registration via entry points (later) or simple dictionary.
- Future: Additional vocabularies (GCMD, SWEET), multi-language synonyms, external API enrichment.

## 10. Error & Edge Cases
- Missing `latitude`/`longitude`: skip STAC extent; still enrich variables (warn).
- No candidate numeric columns: produce empty enrichment metadata (schema tag only).
- Ambiguous CF mapping (two equal similarity scores): choose first deterministic sort order, include `"ambiguous": true`.
- Units unknown: fallback to raw detection, omit conversion.
- Time column missing: STAC temporal extent omitted; spec allows open intervals.
- Extremely large number of columns (>200): cap processing to first 150 by presence ratio (configurable) to avoid slow fuzzy matching.

## 11. Libraries & Dependencies
- Add `rapidfuzz` for fuzzy matching (lightweight, MIT). Pin version range.
- Reuse existing `pandas`, `pyarrow`.

## 12. Minimal Test Plan (MVP)
1. Alias + CF exact match test: input with `sea_water_temperature` -> CF/units recognized.
2. Fuzzy match test: `seaWaterTemp` -> normalized and mapped with confidence < 1 but > threshold.
3. Ambiguous test: two close names produce deterministic mapping and `ambiguous` flag.
4. STAC generation test: collection and item JSON created, bbox/time correct.
5. Disable flags test: `--no-stac` results in no STAC directory; `SEMANTIC_ENABLE=false` embeds no semantic metadata.

## 13. Implementation Steps (Ordered)
1. Package static alias + CF JSON snapshots under `data/semantic/`.
2. Implement `semantic.py` module with `SemanticMapper` and simple subclass `CFMapper`.
3. Add CLI flags + wiring: parse -> pass into writer/enrichment stage.
4. Integrate enrichment before calling `write_geoparquet` (operating on DataFrame copy).
5. Metadata embedding + STAC JSON emission.
6. Add tests.
7. Update docs (`README.md` usage + this spec cross-link).

## 14. Performance Considerations
- Fuzzy matching cached by memoizing tokenization results.
- Short-circuit if DataFrame column count * candidate CF names > threshold (to prevent O(n*m) blow-up); degrade to exact + token subset only.

## 15. Security & Licensing Notes
- Only packaged static JSON; no external calls (privacy-friendly).
- Ensure CF snapshot license compatibility (CF Standard Name table is publicly available; include attribution line in docs).

## 16. MVP Success Criteria
- Running CLI with `--semantic` produces Parquet files containing new metadata keys and a STAC folder.
- At least 80% of common ocean variables in sample dataset receive correct CF mapping (confidence >= 0.9) in tests.
- End-to-end run adds <15% overhead vs. baseline ingestion for <=50 columns.

## 17. Future Enhancements (Post-MVP)
- Dynamic vocabulary updates via remote registry.
- Interactive report with suggested fixes for low-confidence mappings.
- Real-time streaming enrichment for live ingestion.
- OGC API Features endpoint generation.
- Multi-resolution STAC Items per partition; asset-level stats.

---
**Status:** Draft ready for implementation.
