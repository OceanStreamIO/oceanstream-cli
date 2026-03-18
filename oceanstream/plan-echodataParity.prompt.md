# Plan: Echodata Module Parity Audit & Remediation

**TL;DR**: Deep comparison of `oceanstream/echodata/` against the `saildrone-data/saildrone/` reference reveals **6 confirmed bugs**, **~8 functional gaps**, and several areas where oceanstream already exceeds the reference. Priority: fix bugs → close feature gaps → verify parity.

---

## Phase 1: Bug Fixes (Critical — blocks correctness)

**Bug 1 — Missing `_save_intermediate` method in `processor.py`**
Called at ~3 locations but never defined. `save_intermediate=True` will raise `AttributeError`. Fix: implement the method or remove the calls.

**Bug 2 — Missing `_select_channel` in `seabed/detection.py`**
`detect_seabed_blackwell` calls `_select_channel(ds, channel)` — function doesn't exist. The `"blackwell"` method is also missing from the `Literal` type hint. The entire blackwell path is broken.

**Bug 3 — Signature mismatch: `environment/geoparquet.py` → `environment/blended.py`**
Caller passes `insitu_temp=, insitu_sal=, channel_ids=, target_depth_m=` but the callee expects `insitu_df=, channels=, target_depth=, frequency_hz=`. This path raises `TypeError`.

**Bug 4 — Unreachable return in `concat.py`**
`return dt` after `return merged` in `merge_location_data` — dead code. Remove it.

**Bug 5 — Calibration key type inconsistency in `calibrate/calibration.py`**
`validate_calibration_params` expects numeric frequency keys but loaders produce string keys like `"38kHz"` or `"38k_short"`. Validation will reject valid calibration data.

**Bug 6 — `detect_sonar_model` is a stub in `convert.py`**
Always returns `"EK80"`. Non-critical but incorrect for non-EK80 data. Implement basic header detection.

---

## Phase 2: Feature Gaps (needed for saildrone-data parity)

| # | Gap | Reference (saildrone-data) | Status in oceanstream | Fix |
|---|-----|---------------------------|----------------------|-----|
| 1 | `depth_offset` not wired in main `compute_sv` | `sv_dataset.py` `compute_sv(depth_offset=0)` | Helper exists in `enrich_sv_dataset` but primary `compute_sv()` skips it | Add param and wire through |
| 2 | No Pydantic denoise parameter models | `pydantic_models/denoise.py` — per-freq, per-pulse, 38kHz inheritance | Has `DenoiseConfig` dataclass + `FREQUENCY_PRESETS` but not Pydantic | Create Pydantic models with same per-freq/pulse-length schema |
| 3 | No pulse-category splitting for concat/export | `export.py` / concat flow separates `short_pulse`/`long_pulse` | `concat.py` groups by day only | Add pulse-mode detection + category grouping |
| 4 | No batch time-window grouping for concatenation | Prefect flow `_batch_key` day-window logic | Basic concat only | Add time-window batch utility |
| 5 | No standalone `compute_and_save_nasc`/`mvbs` wrappers | `workflow.py` zarr-open→compute→save helpers | Has compute functions but no zarr-to-zarr wrappers | Add thin wrappers or ensure processor covers this |
| 6 | No bathymetry gating for seabed step | `workflow.py` skips seabed detection if depth > instrument range | Processor always runs seabed detection | Add bathymetry check before seabed step |
| 7 | No NASC DB persistence | `NASCPointService` PostgreSQL | Has NASC GeoParquet export instead | **Parity via different approach** — exclude |
| 8 | No Azure IoT integration | `azure_iot/` module | Not present | **Out of scope** — edge deployment feature |

---

## Phase 3: Consistency & Quality Improvements

1. **Two incompatible ECS parsers** in `calibrate/calibration.py`: `_load_ecs_calibration` uses INI-style parsing; `parse_ecs_file` uses XML. Consolidate to one correct parser.
2. **Calibration write inconsistency**: Generic `apply_calibration` and Saildrone-specific `calibrate_saildrone` write to echodata groups differently. Ensure consistency.
3. **Test coverage**: Verify unit tests exist for all denoise algorithms, all seabed detection methods (including blackwell), calibration paths, and compute paths.

---

## What Oceanstream Already Has Beyond Saildrone-Data

Not gaps — these are advancements:
- **Environment module** — Copernicus CDS integration, blended profiles, absorption/sound-speed equations (nothing equivalent in saildrone-data)
- **STAC metadata** — Collection/item emission for echodata products
- **NASC GeoParquet export** — Spatial Hive-partitioned output
- **TOML config with frequency presets** — Centralized configuration
- **Provider architecture** — Pluggable data source adapters
- **Geotrack module** — Replaces and exceeds `process_gps.py`
- **Composite/deltaSv seabed detection** — Additional algorithms beyond saildrone-data's main path

---

## Verification

1. After bugs: `make test-unit` + `ruff check . && mypy oceanstream` pass clean
2. After gaps: Integration test running full pipeline (convert → calibrate → Sv → denoise → seabed → MVBS/NASC → echogram)
3. Specific: `detect_seabed(method="blackwell")` runs without error; `.ecs` and `.xlsx` calibration pass validation; geoparquet→blended path completes; processor with `save_intermediate=True` writes Zarr checkpoints
4. Cross-validation: Same denoise config on same test file in both projects, compare output masks

---

## Scope Decisions

- **In scope**: All 6 bugs, gaps 1-6, quality improvements
- **Out of scope**: Azure IoT (edge feature), PostgreSQL NASC persistence (GeoParquet approach is better), Prefect orchestration (oceanstream uses class-based processor)
- **Assumption**: Core denoise *algorithms* are at parity — gaps are in plumbing/config/integration
- **Assumption**: Geotrack module adequately replaces `process_gps.py` — not re-audited separately

---

## Further Considerations

1. **Pydantic models location**: Should they live in `oceanstream/echodata/config.py` alongside existing dataclasses, or in a new `oceanstream/echodata/models.py`? Recommend new `models.py` to keep concerns separated.
2. **Batch concatenation complexity**: The saildrone-data time-window grouping is tightly coupled to Prefect. Oceanstream could implement this as a pure library utility in `concat.py` that the processor class calls, making it usable outside any orchestration framework.
3. **ECS parser consolidation**: ECS files from Simrad are INI-style (`.ini` variant). The XML parser path may have been added for a different calibration file format — need to verify before removing it.
