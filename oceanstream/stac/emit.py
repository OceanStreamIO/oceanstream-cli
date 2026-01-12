from __future__ import annotations
from pathlib import Path
from typing import Any, Optional, TYPE_CHECKING
import json
from datetime import datetime
import pandas as pd
import numpy as np

if TYPE_CHECKING:
    from oceanstream.sensors.catalogue import Sensor

STAC_VERSION = "1.0.0"


def calculate_measurement_statistics(
    df: pd.DataFrame,
    measurement_columns: Optional[list[str]] = None,
) -> dict[str, dict[str, float]]:
    """Calculate min/max/mean statistics for measurement columns.
    
    Args:
        df: DataFrame with measurement data
        measurement_columns: List of columns to calculate stats for (default: all numeric columns)
        
    Returns:
        Dictionary mapping column name to {min, max, mean}
    """
    stats = {}
    
    if measurement_columns is None:
        # Auto-detect numeric columns (excluding standard geo columns)
        exclude_cols = {'latitude', 'longitude', 'time', 'lat_bin', 'lon_bin', 'platform_id'}
        measurement_columns = [
            col for col in df.columns 
            if pd.api.types.is_numeric_dtype(df[col]) and col not in exclude_cols
        ]
    
    for col in measurement_columns:
        if col not in df.columns:
            continue
        
        try:
            series = pd.to_numeric(df[col], errors='coerce').dropna()
            if len(series) == 0:
                continue
                
            stats[col] = {
                "min": float(series.min()),
                "max": float(series.max()),
                "mean": float(series.mean()),
                "count": int(len(series)),
            }
        except Exception:
            # Skip columns that can't be converted to numeric
            continue
    
    return stats


def _iso(dt: Optional[pd.Timestamp]) -> Optional[str]:
    if dt is None or pd.isna(dt):
        return None
    # Ensure timezone-aware ISO string when possible
    if isinstance(dt, pd.Timestamp):
        if dt.tzinfo is None:
            dt = dt.tz_localize("UTC")
        return dt.isoformat().replace("+00:00", "Z")
    if isinstance(dt, datetime):
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=None)
        return dt.isoformat()
    # Fallback
    try:
        return pd.to_datetime(dt, utc=True).isoformat().replace("+00:00", "Z")
    except Exception:
        return None


def _extent(df: pd.DataFrame) -> tuple[list[list[float]], list[list[Optional[str]]]]:
    lon_min = float(pd.to_numeric(df["longitude"], errors="coerce").min())
    lon_max = float(pd.to_numeric(df["longitude"], errors="coerce").max())
    lat_min = float(pd.to_numeric(df["latitude"], errors="coerce").min())
    lat_max = float(pd.to_numeric(df["latitude"], errors="coerce").max())
    bbox = [[lon_min, lat_min, lon_max, lat_max]]
    if "time" in df.columns and pd.api.types.is_datetime64_any_dtype(df["time"]):
        t0 = _iso(df["time"].min())
        t1 = _iso(df["time"].max())
        interval = [[t0, t1]]
    else:
        interval = [[None, None]]
    return bbox, interval


def _bbox_polygon(bbox: list[float]) -> dict[str, Any]:
    # bbox [lon_min, lat_min, lon_max, lat_max]
    lon_min, lat_min, lon_max, lat_max = bbox
    return {
        "type": "Polygon",
        "coordinates": [[
            [lon_min, lat_min],
            [lon_max, lat_min],
            [lon_max, lat_max],
            [lon_min, lat_max],
            [lon_min, lat_min],
        ]],
    }


def emit_stac_collection_and_item(
    geoparquet_root: Path,
    df: pd.DataFrame,
    semantic_metadata: dict[str, Any] | None,
    *,
    provider_name: str = "provider",
    collection_id: Optional[str] = None,
    instruments: Optional[list[Sensor]] = None,
    platform: Optional[dict[str, Any]] = None,
    platforms: Optional[list[dict[str, Any]]] = None,
    pmtiles_path: Optional[Path] = None,
    measurement_stats: Optional[dict[str, dict[str, float]]] = None,
    software_version: str = "0.1.0",
) -> tuple[Path, list[Path]]:
    """Emit a STAC Collection and one Item per Parquet partition under geoparquet_root/stac/.

    Args:
        geoparquet_root: Root directory of partitioned GeoParquet dataset
        df: DataFrame with the data
        semantic_metadata: Optional semantic metadata
        provider_name: Name of the data provider
        collection_id: Optional collection ID
        instruments: List of sensors/instruments
        platform: Platform metadata (deprecated, use platforms)
        platforms: List of platform metadata dicts for multi-platform campaigns
        pmtiles_path: Optional path to PMTiles file
        measurement_stats: Optional statistics for measurements (min/max/mean)
        software_version: Version of oceanstream software

    Returns tuple of (collection_path, [item_paths]).
    """
    stac_dir = geoparquet_root / "stac"
    stac_dir.mkdir(parents=True, exist_ok=True)

    bbox_list, interval_list = _extent(df)
    keywords: list[str] = []
    if semantic_metadata and "oceanstream:cf_standard_names" in semantic_metadata:
        cf_map = semantic_metadata["oceanstream:cf_standard_names"]
        # collect CF names
        for v in cf_map.values():
            name = v.get("cf_standard_name") if isinstance(v, dict) else None
            if isinstance(name, str):
                keywords.append(name)
        keywords = sorted(set(keywords))

    coll_id = collection_id or f"oceanstream-{provider_name}-geoparquet"
    
    # Get current timestamp for processing provenance
    processing_datetime = datetime.now().isoformat()
    
    collection = {
        "type": "Collection",
        "stac_version": STAC_VERSION,
        "id": coll_id,
        "description": f"Oceanstream GeoParquet dataset for provider '{provider_name}'.",
        "license": "MIT",
        "keywords": keywords,
        "extent": {
            "spatial": {"bbox": bbox_list},
            "temporal": {"interval": interval_list},
        },
        "links": [
            {"rel": "self", "href": "collection.json"},
            {"rel": "items", "href": "items/"},
        ],
        "providers": [
            {
                "name": provider_name,
                "roles": ["producer"],
            }
        ],
    }
    
    # Add instruments if provided
    if instruments:
        collection["summaries"] = collection.get("summaries", {})
        collection["summaries"]["instruments"] = [sensor.to_stac_instrument() for sensor in instruments]
    
    # Add platforms array (hardware platforms: id, name, type, row_count)
    # For multi-platform campaigns, use 'platforms' (array)
    # For single-platform (backward compat), still use 'platforms' as a single-item array
    if platforms:
        collection["summaries"] = collection.get("summaries", {})
        collection["summaries"]["platforms"] = platforms
    elif platform:
        # Backward compatibility: wrap single platform in array
        collection["summaries"] = collection.get("summaries", {})
        collection["summaries"]["platforms"] = [platform]
    
    # Add measurement statistics if provided
    if measurement_stats:
        collection["summaries"] = collection.get("summaries", {})
        collection["summaries"]["measurements"] = measurement_stats
    
    # Add processing provenance
    collection["summaries"] = collection.get("summaries", {})
    collection["summaries"]["processing"] = {
        "software": "oceanstream",
        "version": software_version,
        "processing_date": processing_datetime,
        "processing_level": "L2",
    }
    
    # Add PMTiles as a collection-level asset (covers all data, not per-item)
    if pmtiles_path and pmtiles_path.exists():
        collection["assets"] = collection.get("assets", {})
        try:
            # Calculate relative path from stac directory to PMTiles file
            rel_pmtiles = Path("..") / pmtiles_path.relative_to(geoparquet_root)
            pmtiles_href = str(rel_pmtiles)
        except ValueError:
            # If pmtiles_path is not relative to geoparquet_root (e.g., cloud staging),
            # use a standard relative path assuming tiles/ sibling to stac/
            pmtiles_href = f"../tiles/{pmtiles_path.name}"
        
        collection["assets"]["pmtiles"] = {
            "href": pmtiles_href,
            "type": "application/vnd.pmtiles",
            "roles": ["visual", "tiles"],
            "title": "PMTiles vector tiles with track segments and measurements",
        }

    items_dir = stac_dir / "items"
    items_dir.mkdir(exist_ok=True)

    # Group parquet files by their immediate parent partition directory (excluding root)
    partition_dirs = {}
    parquet_files = sorted(geoparquet_root.rglob("*.parquet"))
    for pf in parquet_files:
        rel = pf.relative_to(geoparquet_root)
        parts = rel.parts[:-1]  # all parent dirs except the file
        part_key = "/".join(parts) if parts else "."
        partition_dirs.setdefault(part_key, []).append(pf)

    item_paths: list[Path] = []
    for idx, (partition, files) in enumerate(partition_dirs.items()):
        # Derive item-wide bbox/geometry and time for this partition by reading file data
        # Attempt to extract partition-specific data subset; fallback to using parent df
        # For robust tests, try to read the Parquet file for geometry, else fallback to overall
        item_bbox, item_interval = bbox_list, interval_list
        try:
            import pyarrow.parquet as pq
            import pandas as pd
            part_df = pd.concat([pq.read_table(f).to_pandas() for f in files], ignore_index=True)
            item_bbox, item_interval = _extent(part_df)
        except Exception:
            pass
        bbox = item_bbox[0]
        geometry = _bbox_polygon(bbox)

        item = {
            "type": "Feature",
            "stac_version": STAC_VERSION,
            "id": f"{coll_id}-item-{idx}",
            "collection": coll_id,
            "bbox": bbox,
            "geometry": geometry,
            "properties": {},
            "assets": {},
            "links": [
                {"rel": "collection", "href": "../collection.json"}
            ],
        }

        # Add platform identifiers to item properties if available
        # For multi-platform: store all platform IDs; for single platform: backward compat
        all_platforms = platforms if platforms else ([platform] if platform else [])
        if all_platforms:
            platform_ids = [p.get("platform_id") or p.get("id") for p in all_platforms if p.get("platform_id") or p.get("id")]
            if platform_ids:
                item["properties"]["platform_ids"] = platform_ids
            # Also include campaign_id from first platform (should be same for all)
            first_platform = all_platforms[0]
            if "campaign_id" in first_platform:
                item["properties"]["campaign_id"] = first_platform["campaign_id"]

        # Optional temporal properties
        if item_interval and item_interval[0] != [None, None]:
            start, end = item_interval[0]
            if start:
                item["properties"]["start_datetime"] = start
            if end:
                item["properties"]["end_datetime"] = end

        # Add all parquets in this partition as assets
        for i, pf in enumerate(files):
            rel_href = str(Path("..").joinpath(pf.relative_to(geoparquet_root)))
            asset_key = "geoparquet" if i == 0 else f"geoparquet_{i}"
            item["assets"][asset_key] = {
                "href": rel_href,
                "type": "application/x-parquet",
                "roles": ["data"],
            }

        item_path = items_dir / f"item-{idx}.json"
        with open(item_path, "w", encoding="utf-8") as f:
            json.dump(item, f, indent=2)
        item_paths.append(item_path)

    # Write collection
    collection_path = stac_dir / "collection.json"
    with open(collection_path, "w", encoding="utf-8") as f:
        json.dump(collection, f, indent=2)

    return collection_path, item_paths
