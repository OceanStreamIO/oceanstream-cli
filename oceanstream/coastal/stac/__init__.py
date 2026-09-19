"""STAC 1.0 item and asset emission for coastal products."""

from __future__ import annotations

from oceanstream.coastal.stac.coastal_emit import (
    STAC_VERSION,
    build_collection,
    build_item,
    collection_id_for,
    emit_stac,
    geographic_bounds,
    item_id,
    merge_item_into_collection,
)

__all__ = [
    "STAC_VERSION",
    "build_collection",
    "build_item",
    "collection_id_for",
    "emit_stac",
    "geographic_bounds",
    "item_id",
    "merge_item_into_collection",
]
