"""STAC metadata generation for echodata products.

Provides functions to emit STAC-compliant metadata for:
- MVBS (Mean Volume Backscattering Strength) Zarr datasets
- NASC (Nautical Area Scattering Coefficient) Zarr datasets
- Echogram PNG images with track segment coordinates

Can operate in two modes:
1. **Integrated**: Extend existing geotrack STAC collection with echodata assets
2. **Standalone**: Create new STAC collection for acoustic-only campaigns

Follows STAC 1.0.0 specification.
"""

from oceanstream.echodata.stac.echodata_emit import (
    emit_stac,
    emit_echodata_collection,
    emit_echodata_item,
    add_echodata_to_collection,
    get_echodata_summaries,
)

from oceanstream.echodata.stac.segments import (
    create_segments_geojson,
    create_echogram_segment,
    extract_segment_coordinates,
)

__all__ = [
    # Main functions
    "emit_stac",
    "emit_echodata_collection",
    "emit_echodata_item",
    "add_echodata_to_collection",
    "get_echodata_summaries",
    # Segment utilities
    "create_segments_geojson",
    "create_echogram_segment",
    "extract_segment_coordinates",
]
