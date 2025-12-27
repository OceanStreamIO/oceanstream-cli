# Missing Documentation Pages

This document tracks documentation pages that are referenced but not yet created. These references have been commented out in the current documentation to prevent broken link warnings.

## High Priority

### API Reference
- **api-reference/cli-reference.md** - Complete CLI command reference
- **api-reference/python-api.md** - Python API documentation

### Core Guides
- **guide/core-concepts/processing-pipeline.md** - Detailed pipeline explanation

## Medium Priority

### Advanced Topics
- **guide/advanced/append-update.md** - Incremental data processing guide

### Architecture
- **guide/architecture/storage-providers.md** - Storage provider system design

### Integration Guides
- **guide/integrations/azure-storage.md** - Azure-specific integration details
- **gis-integration/qgis.md** - QGIS integration guide

### Feature Guides
- **guide/features/sensor-detection.md** - Sensor detection algorithm details
- **guide/features/semantic-enrichment.md** - CF Standard Names enrichment
- **guide/features/pmtiles-generation.md** - PMTiles generation guide

## Low Priority

### Example Workflows
- **guide/examples/saildrone-multi-platform.md** - Multi-platform processing
- **guide/examples/saildrone-append.md** - Append workflow example
- **guide/examples/saildrone-cloud.md** - Cloud upload example
- **guide/examples/saildrone-timeseries.md** - Time series analysis
- **guide/examples/saildrone-spatial.md** - Spatial analysis
- **guide/examples/saildrone-sensors.md** - Sensor-specific processing

### Data Provider Guides
- **guide/features/data-providers/creating-providers.md** - Custom provider development
- **guide/features/data-providers/semantic-mappings.md** - Cross-provider interoperability

### Individual Sensor Pages
- **guide/features/supported-sensors/apogee-si111.md** - Apogee SI-111 IR Radiometer
- **guide/features/supported-sensors/imu-navigation.md** - IMU & GPS Navigation
- **guide/features/supported-sensors/wave-imu.md** - IMU-Derived Wave Sensor
- **guide/features/supported-sensors/licor-li190r.md** - LI-COR LI-190R (covered in radiation-sensors.md)
- **guide/features/supported-sensors/kipp-zonen-cmp.md** - Kipp & Zonen CMP (covered in radiation-sensors.md)

### Additional Pages
- **about/faq.md** - Frequently Asked Questions

## Implementation Notes

### Sensors Already Documented
- LI-COR LI-190R and Kipp & Zonen CMP are documented in `guide/features/supported-sensors/radiation-sensors.md`
- Links have been updated to point to anchor sections in that page

### Commented Links
All links to missing pages have been replaced with HTML comments like:
```markdown
<!-- TODO: Add [page-name] guide -->
```

This prevents broken link warnings while preserving the intent to create these pages.

## Status: Clean Build ✅

As of the latest build, there are **zero broken link warnings**. The documentation builds successfully with only informational messages about:
- README.md exclusion (expected)
- Anchor link recommendations (optional optimization)
