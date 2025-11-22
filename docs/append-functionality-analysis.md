# Append/Update Functionality Analysis

**Date**: 2024
**Status**: ✅ FULLY IMPLEMENTED - Complete deduplication system with file tracking and CLI controls

## Summary

The OceanStream data ingestion pipeline **FULLY SUPPORTS append/update functionality** with intelligent deduplication, file tracking, and flexible processing modes. When running `convert()` multiple times with the same `campaign_id` and `output_dir`, the system:

- Tracks processed files to prevent accidental duplicates
- Performs row-level deduplication based on primary keys
- Validates schema compatibility between runs
- Provides CLI flags for different processing scenarios

## Implementation Overview

The append/update system consists of three main components:

1. **Metadata Tracking** (`oceanstream/geotrack/metadata.py`)
   - Tracks processed files by SHA256 hash
   - Stores processing history in `.oceanstream_metadata.json`
   - Detects when same file is processed multiple times

2. **Deduplication Logic** (`oceanstream/geotrack/deduplication.py`)
   - Row-level deduplication based on primary keys: `time`, `latitude`, `longitude`, `trajectory`
   - Schema compatibility checking with dtype flexibility
   - Merge-and-rewrite strategy for deduplicated data

3. **CLI Controls** (`oceanstream/cli.py`)
   - `--deduplicate` (default: True): Enable row-level deduplication
   - `--allow-duplicates` (default: False): Allow reprocessing same files
   - `--force-reprocess` (default: False): Clear metadata and start fresh

## Current Behavior

### ✅ What Works (Fully Implemented)

1. **Intelligent Append to Existing Dataset**
   - Running `convert()` multiple times with same campaign appends new data
   - Automatically deduplicates based on primary keys
   - Example:
     ```
     Run 1: 20 rows from file A → 20 rows written
     Run 2: 1 row from file B  → 21 rows total (appended with dedup)
     Total: 21 unique rows
     ```

2. **File Tracking & Duplicate Prevention**
   - System remembers which files have been processed
   - Prevents accidental reprocessing of same files
   - Example:
     ```
     Run 1: file A → processed successfully
     Run 2: file A (same) → ⚠️  WARNING: File already processed, stopping
     ```
   - Use `--allow-duplicates` to bypass this check if needed

3. **Row-Level Deduplication**
   - Removes duplicate rows based on primary keys
   - Example:
     ```
     Run 1: 20 rows from file A → 20 rows total
     Run 2: 20 rows from file A (forced reprocess) → 20 rows total (not 40!)
     Deduplication removed 20 duplicate rows
     ```
   - Enabled by default, disable with `--no-deduplicate`

4. **Schema Compatibility Checking**
   - Validates new data schema matches existing data
   - Warns about dtype mismatches and missing columns
   - Allows proceeding with merge (with warning)

5. **Partition Structure**
   - Campaign-based folder structure: `output_dir/campaign_id/lat_bin=X/lon_bin=Y/`
   - When deduplication occurs, old partitions are deleted and merged data is rewritten
   - Ensures clean, deduplicated parquet files without duplicates

6. **STAC Metadata Updates**
   - STAC collection and items are regenerated on each run
   - Collection reflects the full temporal/spatial extent after each run

### CLI Usage Examples

**Default Behavior (Smart Append with Deduplication)**:
```bash
# Run 1: Process initial data
oceanstream process geotrack --input-source ./day1_data --output-dir ./out --campaign-id mission_2024

# Run 2: Append new data (different files)
oceanstream process geotrack --input-source ./day2_data --output-dir ./out --campaign-id mission_2024
# Result: Data appended, duplicates automatically removed
```

**Prevent Accidental Reprocessing**:
```bash
# Initial processing
oceanstream process geotrack --input-source ./data --output-dir ./out --campaign-id mission_2024

# Try to reprocess same files (PREVENTED by default)
oceanstream process geotrack --input-source ./data --output-dir ./out --campaign-id mission_2024
# Result: ⚠️  WARNING: Files already processed, operation stopped
```

**Force Reprocess from Scratch**:
```bash
# Clear metadata and reprocess everything
oceanstream process geotrack --input-source ./data --output-dir ./out --campaign-id mission_2024 --force-reprocess
# Result: Metadata cleared, all data reprocessed with deduplication
```

**Advanced: Allow Duplicates but Deduplicate Rows**:
```bash
# Bypass file tracking but still remove row duplicates
oceanstream process geotrack --input-source ./data --output-dir ./out --campaign-id mission_2024 --allow-duplicates --deduplicate
# Result: Files processed again, but row-level dedup ensures no duplicates
```

## Implementation Details

### Metadata Tracking

**File**: `oceanstream/geotrack/metadata.py`

Key components:
- `CampaignMetadata` class manages `.oceanstream_metadata.json`
- SHA256 file hashing for duplicate detection
- Tracks: run count, processed files, timestamps, row counts
- Methods: `is_file_processed()`, `mark_file_processed()`, `increment_run_count()`

**Metadata Format**:
```json
{
  "version": "1.0",
  "campaign_created": "2024-11-10T12:00:00Z",
  "last_updated": "2024-11-10T13:00:00Z",
  "processed_files": {
    "file.csv": {
      "hash": "sha256...",
      "processed_at": "2024-11-10T12:00:00Z",
      "size": 12345,
      "rows": 100
    }
  },
  "total_runs": 2,
  "total_files_processed": 1
}
```

### Deduplication Logic

**File**: `oceanstream/geotrack/deduplication.py`

Key components:
- **Primary Keys**: `['time', 'latitude', 'longitude', 'trajectory']`
- `deduplicate_dataframe()`: Remove duplicates within single DataFrame
- `read_existing_campaign_data()`: Load existing parquet partitions
- `merge_with_deduplication()`: Merge new + existing data, remove duplicates
- `check_schema_compatibility()`: Validate dtype compatibility

**Strategy**:
1. Read existing campaign data from all partitions
2. Concatenate with new data
3. Remove duplicates based on primary keys (keep first occurrence)
4. Delete old partition directories
5. Write merged, deduplicated data to new partitions

### Integration in Processor

**File**: `oceanstream/geotrack/processor.py`

Workflow in `convert()` function:
1. Create `CampaignMetadata` instance
2. Check if files already processed (stop unless `--allow-duplicates`)
3. Process data (existing logic)
4. If `--deduplicate`:
   - Read existing campaign data
   - Merge with new data and deduplicate
   - Delete old partitions
   - Write merged data
5. Update metadata with processed files

## Test Coverage

### Integration Tests

**File**: `oceanstream/tests/integration/test_append_update.py`

Six comprehensive tests covering all scenarios:

1. ✅ **test_multiple_runs_different_files_appends**
   - Verifies append behavior with different files
   - Checks row count increases correctly (20 → 41 rows)

2. ✅ **test_same_file_twice_warns_and_prevents_duplicates**
   - Verifies file tracking prevents reprocessing
   - Checks warning is issued when duplicate file detected

3. ✅ **test_allow_duplicates_flag_creates_duplicates**
   - Verifies `--allow-duplicates` flag bypasses file tracking
   - Checks duplicates are created when flag is used (no dedup)

4. ✅ **test_deduplicate_removes_duplicates**
   - Verifies row-level deduplication works correctly
   - Checks 20 + 20 rows = 20 unique rows (not 40)

5. ✅ **test_force_reprocess_clears_metadata**
   - Verifies `--force-reprocess` clears metadata
   - Checks data is reprocessed from scratch

6. ✅ **test_metadata_tracking_accuracy**
   - Verifies metadata tracking is accurate
   - Checks run count, file count, timestamps

**Test Results**: All 6 tests passing ✅

### Full Test Suite Status

- **Total Tests**: 150 passed, 4 skipped
- **No Regressions**: Implementation doesn't break existing functionality
- **Coverage**: Append/update functionality fully tested

## Behavior Summary

| Scenario | Flags | Behavior |
|----------|-------|----------|
| First run | (none) | Creates data + metadata |
| New file, same campaign | (none) | Appends data, deduplicates automatically |
| Same file again | (none) | ⚠️ WARNING + operation stopped |
| Same file with `--allow-duplicates` | `--allow-duplicates` | Creates duplicates (no dedup unless also using `--deduplicate`) |
| Same file with both flags | `--allow-duplicates --deduplicate` | Processes file but removes row duplicates |
| Fresh start | `--force-reprocess` | Clears metadata, reprocesses from scratch |
| Disable deduplication | `--no-deduplicate` | Appends without row-level deduplication |

## Architecture Decisions

### Why Merge-and-Rewrite Strategy?

**Considered Approaches**:
1. **PyArrow Append**: Write new files alongside existing (creates duplicates)
2. **Read-Merge-Write**: Read existing, merge with dedup, rewrite all data
3. **Partition-Level Dedup**: Deduplicate only affected partitions

**Chosen**: Read-Merge-Write (approach #2)

**Rationale**:
- Simple and reliable
- Ensures clean, deduplicated parquet files
- No fragmentation or accumulation of duplicate files
- Easier to reason about data integrity
- Performance acceptable for typical campaign sizes

**Trade-offs**:
- Rewrites all data on each dedup operation
- May be slower for very large campaigns (100GB+)
- Disk I/O higher than append-only approach

### Why Primary Keys: time, lat, lon, trajectory?

These fields uniquely identify a single observation:
- `time`: Temporal dimension
- `latitude`, `longitude`: Spatial dimension
- `trajectory`: Platform/instrument identifier

**Considered Alternatives**:
- All columns (too strict, minor differences would pass through)
- Configurable keys (adds complexity, unclear use case)

### Why SHA256 File Hashing?

**Alternatives considered**:
- File path only: Fails if file is moved or renamed
- Modification timestamp: Unreliable (can be changed)
- Content sampling: Faster but less reliable

**Chosen**: SHA256 hash of full file content

**Rationale**:
- Detects true file identity regardless of path/name
- Detects content changes (even if filename unchanged)
- Performance acceptable for typical CSV file sizes

## Known Limitations

1. **Large Campaigns**: Merge-and-rewrite may be slow for campaigns >100GB
   - Future optimization: Partition-level deduplication
   
2. **Schema Evolution**: System allows schema changes with warning
   - Future enhancement: Explicit schema versioning and migration

3. **No Partial Deduplication**: System deduplicates all or nothing
   - Future enhancement: `--deduplicate-keys` to specify custom keys

4. **Memory Usage**: Reads entire campaign into memory for deduplication
   - Future optimization: Chunked reading and writing

## Verification History

### Original Analysis (Before Implementation)

Created two test scripts to verify behavior:

1. **test_append_behavior.py**
   - Verified: Different files → append works
   - Result: Run 1 (20 rows) + Run 2 (1 row) = 21 rows total ✅

2. **test_append_duplicates.py**
   - Verified: Same file twice → duplicates created
   - Result: Run 1 (20 rows) + Run 2 (20 rows) = 40 rows (20 duplicates) ⚠️

These tests revealed the need for deduplication implementation.

### Post-Implementation Testing

- 6 integration tests covering all scenarios
- All tests passing
- Full test suite: 150 passed, 4 skipped
- No regressions

## Future Enhancements

### Considered for Future Versions

1. **Partition-Level Deduplication** (Performance)
   - Only read/rewrite affected partitions
   - Faster for large campaigns with localized updates
   - Priority: Medium

2. **Incremental STAC Updates** (Efficiency)
   - Only regenerate changed/new STAC items
   - Preserve existing items where possible
   - Priority: Low

3. **Configurable Primary Keys** (Flexibility)
   - Allow user to specify deduplication keys
   - Use case: Different observation types
   - Priority: Low

4. **Schema Versioning** (Robustness)
   - Explicit schema version tracking
   - Automated schema migration
   - Priority: Medium

5. **Chunked Deduplication** (Scalability)
   - Process data in chunks to reduce memory usage
   - Enable handling of very large campaigns (>1TB)
   - Priority: Low

## Conclusion

The OceanStream data ingestion pipeline now provides **robust append/update functionality** with:
- ✅ Intelligent file tracking to prevent accidental duplicates
- ✅ Row-level deduplication based on primary keys
- ✅ Schema compatibility checking
- ✅ Flexible CLI controls for different workflows
- ✅ Comprehensive test coverage

The implementation is **production-ready** and suitable for:
- Incremental data processing workflows
- Campaign-based data organization
- Multi-day/multi-source data collection
- Avoiding accidental duplicate processing

For most use cases, the default behavior (deduplication enabled) provides the best experience.
