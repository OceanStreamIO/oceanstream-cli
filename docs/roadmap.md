# Roadmap

This document outlines planned enhancements to evolve the ingestion pipeline into a generic, extensible CSV → GeoParquet utility for any dataset containing latitude/longitude/time coordinates.

## 1. Coordinate & Time Detection
- Auto-detect columns: `lat`|`latitude`|`y`, `lon`|`longitude`|`x`, `time`|`timestamp`|`datetime`|`date`.
- Add CLI overrides: `--lat-col`, `--lon-col`, `--time-col`.
- Fail fast with a clear message if any mandatory coordinate cannot be resolved.
- Time parsing via `pandas.to_datetime(..., utc=True, errors='coerce')` and drop rows with missing coordinates/time (unless `--allow-missing-time`).

## 2. Units Row Detection
- Detect a header-adjacent "units" row (patterns: contains `/`, `%`, `deg`, `m`, `C`, `Pa`, `s`).
- Persist mapping as metadata key: `oceanstream:units`.

## 3. Field Role Classification
Classify non-coordinate columns into roles:
- `measure`: numeric-coercible columns.
- `dimension`: low-cardinality strings (unique/rows < 0.05).
- `attribute`: all other textual columns.
Store in metadata: `oceanstream:field_roles`.

## 4. Alias Generation
- Heuristic: lowercase → replace spaces/symbols with `_` → collapse repeats → strip leading digits.
- De-duplicate by numeric suffix if needed.
- Save only changed mappings: `oceanstream:aliases`.

## 5. Schema Metadata Blob
Embed full inference snapshot:
```json
{
  "original_columns": ["..."],
  "inferred_types": {"col": "dtype"},
  "roles": {"col": "measure|dimension|attribute"},
  "units": {"col": "unit"},
  "created_at": "UTC timestamp",
  "tool_version": "x.y.z"
}
```
Metadata key: `oceanstream:schema`.

## 6. CLI Enhancements
- `--auto / --no-auto` (default: auto enabled).
- Overrides: `--lat-col`, `--lon-col`, `--time-col`.
- `--no-aliases` to disable alias generation.
- `--export-schema <path>` writes the schema JSON externally.
- Extend `--print-schema` output with roles, units, and alias changes.

## 7. Partition Strategy Generalization
- Dynamic bin sizing: if latitude or longitude span < 10°, use a single bin for that axis.
- User controls: `--lat-step`, `--lon-step` or explicit `--lat-bins`, `--lon-bins`.
- Optional temporal partitioning: `--time-partition (daily|monthly)` when range > 1 day.

## 8. Validation Phase
Pre-write summary:
- Row counts (total, dropped for NA coords/time, dropped all-NA).
- Counts of measures/dimensions/attributes.
- Warnings: numeric columns with high string ratio, excessive cardinality dimensions.

## 9. Extensibility via External Schema File
Support user-supplied JSON/YAML schema:
```json
{
  "columns": {
    "sst": {"source": "TEMP_SEA", "role": "measure", "unit": "degC", "dtype": "float32"}
  },
  "partitioning": {"lat_step": 5, "lon_step": 5}
}
```
- Apply overrides before inference; heuristics fill gaps.

## 10. Implementation Order (Incremental)
1. Coordinate & time detection + overrides.
2. Units row detection + metadata.
3. Field role classification + alias heuristic.
4. Schema metadata blob + export flag.
5. CLI flags & updated `--print-schema` output.
6. Partition strategy generalization & optional time partitioning.
7. Validation summary & warnings.
8. External schema override support.

## 11. Testing Strategy
- Unit tests for: coordinate detection, role classification, alias generation uniqueness, units row detection.
- Integration tests for: end-to-end schema export, partitioning with small vs large spatial extents, temporal partition creation.
- Golden JSON schema fixture comparison.

## 12. Performance Considerations
- Sample-based numeric coercion (head + random sample of up to 100 non-null values) to avoid full-column conversion during role inference.
- Lazy evaluation: skip alias generation if no transformation needed.
- Guardrail for very wide datasets: cap role inference sampling to reduce memory spikes.

## 13. Backward Compatibility
- Default behavior with existing legacy datasets remains unchanged (explicit lat/lon/time names still recognized).
- New auto features can be disabled via `--no-auto`.
- Partition labels unchanged unless custom steps/time partition flags used.

## 14. Open Questions
- Should we support EPSG other than WGS84? (Future: detect CRS from metadata row or user flag.)
- Do we need a plugin interface for custom role classifiers?
- Handling extremely high-cardinality dimensions (>10k unique) — auto demote to attribute?

## 15. Future Ideas (Post-Core)
- Time gap analysis & interpolation suggestions.
- Automatic outlier flag column (z-score based) for measures.
- Optional geometry creation for track segments (LineString) downstream.

---
Last updated: 2025-11-07
