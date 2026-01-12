# Changelog

All notable changes to OceanStream will be documented here.

## [Unreleased]

### Added
- **Multi-platform campaign support**: Campaigns can now contain data from multiple platforms (e.g., multiple Saildrone USVs). All platforms are automatically detected and tracked with row counts.
- **PMTiles as collection-level asset**: PMTiles are now stored as a collection-level asset in `collection.json` instead of per-item, correctly reflecting that PMTiles cover the entire dataset.
- **Platforms array in STAC**: STAC collections now include `summaries.platforms` array with metadata for all platforms in the campaign.
- **Platform IDs in STAC items**: STAC items now include `platform_ids` array property for multi-platform support.
- Storage provider system with Azure, Local support
- Configuration management with encrypted credentials
- Spatial binning with Hive partitioning
- STAC metadata generation
- Sensor detection system
- PMTiles generation (optional)
- Deduplication with file tracking
- Campaign-based organization

### Changed
- `detect_sensors_and_platform()` now internally uses `detect_sensors_and_platforms()` which returns all platforms
- Campaign metadata now stores `platforms` array in addition to single `platform_id` for backward compatibility

### Documentation
- Complete documentation site structure
- Getting started guides
- API reference
- Contributing guidelines
- Updated STAC metadata documentation with multi-platform examples
- Updated campaign management documentation with multi-platform workflows

## [Pre-1.0]

Working towards first stable release.

---

**Note**: This project follows [Semantic Versioning](https://semver.org/) after v1.0.0.
