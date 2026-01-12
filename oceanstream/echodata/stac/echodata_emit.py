"""STAC emission for echodata products.

Creates STAC-compliant metadata for acoustic data products (MVBS, NASC, echograms).
Can extend existing geotrack STAC or create standalone echodata STAC.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional, Union

import numpy as np

if TYPE_CHECKING:
    import xarray as xr

logger = logging.getLogger(__name__)

STAC_VERSION = "1.0.0"


def emit_stac(
    campaign_dir: Path,
    campaign_id: str,
    *,
    include_mvbs: bool = True,
    include_nasc: bool = True,
    include_echograms: bool = True,
    extend_existing: bool = True,
    sonar_model: str = "EK80",
    frequencies_khz: Optional[list[float]] = None,
    platform_ids: Optional[list[str]] = None,
    denoising_methods: Optional[list[str]] = None,
) -> Path:
    """
    Emit STAC metadata for echodata products.
    
    Operates in two modes:
    1. If extend_existing=True and geotrack STAC exists, adds echodata to it
    2. Otherwise, creates a standalone echodata STAC collection
    
    Args:
        campaign_dir: Root campaign directory containing echodata/ subfolder
        campaign_id: Campaign identifier (used as collection ID)
        include_mvbs: Include MVBS Zarr in STAC
        include_nasc: Include NASC Zarr in STAC
        include_echograms: Include echogram PNGs in STAC
        extend_existing: If True, extend existing geotrack STAC; else create new
        sonar_model: Echosounder model (EK60, EK80)
        frequencies_khz: List of frequencies in kHz (auto-detected if None)
        platform_ids: Platform identifiers (auto-detected if None)
        denoising_methods: List of denoising methods applied
        
    Returns:
        Path to STAC collection.json
        
    Example:
        # Extend existing geotrack STAC
        collection = emit_stac(
            campaign_dir=Path("./output/TPOS2023"),
            campaign_id="TPOS2023",
            sonar_model="EK80",
            frequencies_khz=[38, 200],
        )
        
        # Create standalone echodata STAC
        collection = emit_stac(
            campaign_dir=Path("./output/acoustic_only"),
            campaign_id="acoustic_survey_2024",
            extend_existing=False,
        )
    """
    campaign_dir = Path(campaign_dir)
    echodata_dir = campaign_dir / "echodata"
    
    if not echodata_dir.exists():
        raise ValueError(f"No echodata directory found at {echodata_dir}")
    
    # Auto-detect frequencies from MVBS/NASC if not provided
    if frequencies_khz is None:
        frequencies_khz = _detect_frequencies(echodata_dir)
    
    # Check for existing geotrack STAC
    existing_stac = campaign_dir / "stac" / "collection.json"
    
    if extend_existing and existing_stac.exists():
        logger.info(f"Extending existing STAC at {existing_stac}")
        collection_path = add_echodata_to_collection(
            existing_stac,
            echodata_dir,
            sonar_model=sonar_model,
            frequencies_khz=frequencies_khz,
            platform_ids=platform_ids,
            denoising_methods=denoising_methods,
            include_mvbs=include_mvbs,
            include_nasc=include_nasc,
            include_echograms=include_echograms,
        )
    else:
        logger.info(f"Creating standalone echodata STAC for {campaign_id}")
        collection_path = emit_echodata_collection(
            campaign_dir,
            campaign_id,
            sonar_model=sonar_model,
            frequencies_khz=frequencies_khz,
            platform_ids=platform_ids,
            denoising_methods=denoising_methods,
            include_mvbs=include_mvbs,
            include_nasc=include_nasc,
            include_echograms=include_echograms,
        )
    
    return collection_path


def emit_echodata_collection(
    campaign_dir: Path,
    campaign_id: str,
    *,
    sonar_model: str = "EK80",
    frequencies_khz: Optional[list[float]] = None,
    platform_ids: Optional[list[str]] = None,
    denoising_methods: Optional[list[str]] = None,
    include_mvbs: bool = True,
    include_nasc: bool = True,
    include_echograms: bool = True,
) -> Path:
    """
    Create a standalone STAC collection for echodata products.
    
    Used when no geotrack data exists (acoustic-only campaigns).
    
    Args:
        campaign_dir: Root campaign directory
        campaign_id: Collection identifier
        sonar_model: Echosounder model
        frequencies_khz: Frequencies in kHz
        platform_ids: Platform identifiers
        denoising_methods: Denoising methods applied
        include_mvbs: Include MVBS in STAC
        include_nasc: Include NASC in STAC
        include_echograms: Include echograms in STAC
        
    Returns:
        Path to collection.json
    """
    campaign_dir = Path(campaign_dir)
    echodata_dir = campaign_dir / "echodata"
    stac_dir = campaign_dir / "stac"
    stac_dir.mkdir(parents=True, exist_ok=True)
    items_dir = stac_dir / "items"
    items_dir.mkdir(exist_ok=True)
    
    # Get temporal/spatial extent from echodata
    extent = _get_echodata_extent(echodata_dir)
    
    # Build echodata summaries
    echodata_summaries = get_echodata_summaries(
        sonar_model=sonar_model,
        frequencies_khz=frequencies_khz or [],
        denoising_methods=denoising_methods,
        include_mvbs=include_mvbs,
        include_nasc=include_nasc,
        include_echograms=include_echograms,
    )
    
    # Create collection
    collection = {
        "type": "Collection",
        "stac_version": STAC_VERSION,
        "id": campaign_id,
        "description": f"Acoustic echosounder data from campaign '{campaign_id}'.",
        "license": "MIT",
        "keywords": ["acoustics", "echosounder", sonar_model, "oceanography"],
        "extent": extent,
        "links": [
            {"rel": "self", "href": "collection.json"},
            {"rel": "items", "href": "items/"},
        ],
        "summaries": {
            "echodata": echodata_summaries,
            "processing": {
                "software": "oceanstream",
                "processing_date": datetime.now().isoformat(),
                "processing_level": "L3",
            },
        },
        "assets": {},
    }
    
    # Add platform summaries if provided
    if platform_ids:
        collection["summaries"]["platforms"] = [
            {"id": pid, "type": "platform"} for pid in platform_ids
        ]
    
    # Add echodata assets to collection
    _add_echodata_assets(
        collection,
        echodata_dir,
        stac_dir,
        frequencies_khz=frequencies_khz or [],
        include_mvbs=include_mvbs,
        include_nasc=include_nasc,
    )
    
    # Create items for echodata products
    item_paths = []
    
    if include_mvbs:
        mvbs_item = emit_echodata_item(
            echodata_dir,
            campaign_id,
            product="mvbs",
            sonar_model=sonar_model,
            frequencies_khz=frequencies_khz,
            platform_ids=platform_ids,
        )
        if mvbs_item:
            item_path = items_dir / "echodata-mvbs.json"
            with open(item_path, "w", encoding="utf-8") as f:
                json.dump(mvbs_item, f, indent=2)
            item_paths.append(item_path)
    
    if include_nasc:
        nasc_item = emit_echodata_item(
            echodata_dir,
            campaign_id,
            product="nasc",
            sonar_model=sonar_model,
            frequencies_khz=frequencies_khz,
            platform_ids=platform_ids,
        )
        if nasc_item:
            item_path = items_dir / "echodata-nasc.json"
            with open(item_path, "w", encoding="utf-8") as f:
                json.dump(nasc_item, f, indent=2)
            item_paths.append(item_path)
    
    if include_echograms:
        echograms_item = _create_echograms_item(
            echodata_dir,
            campaign_id,
            sonar_model=sonar_model,
            frequencies_khz=frequencies_khz,
            platform_ids=platform_ids,
        )
        if echograms_item:
            item_path = items_dir / "echodata-echograms.json"
            with open(item_path, "w", encoding="utf-8") as f:
                json.dump(echograms_item, f, indent=2)
            item_paths.append(item_path)
    
    # Write collection
    collection_path = stac_dir / "collection.json"
    with open(collection_path, "w", encoding="utf-8") as f:
        json.dump(collection, f, indent=2)
    
    logger.info(f"Created echodata STAC collection at {collection_path}")
    logger.info(f"Created {len(item_paths)} STAC items")
    
    return collection_path


def add_echodata_to_collection(
    collection_path: Path,
    echodata_dir: Path,
    *,
    sonar_model: str = "EK80",
    frequencies_khz: Optional[list[float]] = None,
    platform_ids: Optional[list[str]] = None,
    denoising_methods: Optional[list[str]] = None,
    include_mvbs: bool = True,
    include_nasc: bool = True,
    include_echograms: bool = True,
) -> Path:
    """
    Add echodata products to an existing STAC collection.
    
    Extends geotrack STAC with echodata summaries, assets, and items.
    
    Args:
        collection_path: Path to existing collection.json
        echodata_dir: Path to echodata directory
        sonar_model: Echosounder model
        frequencies_khz: Frequencies in kHz
        platform_ids: Platform identifiers
        denoising_methods: Denoising methods applied
        include_mvbs: Include MVBS in STAC
        include_nasc: Include NASC in STAC
        include_echograms: Include echograms in STAC
        
    Returns:
        Path to updated collection.json
    """
    collection_path = Path(collection_path)
    echodata_dir = Path(echodata_dir)
    stac_dir = collection_path.parent
    items_dir = stac_dir / "items"
    items_dir.mkdir(exist_ok=True)
    
    # Load existing collection
    with open(collection_path, "r", encoding="utf-8") as f:
        collection = json.load(f)
    
    campaign_id = collection.get("id", "campaign")
    
    # Add echodata summaries
    collection["summaries"] = collection.get("summaries", {})
    collection["summaries"]["echodata"] = get_echodata_summaries(
        sonar_model=sonar_model,
        frequencies_khz=frequencies_khz or [],
        denoising_methods=denoising_methods,
        include_mvbs=include_mvbs,
        include_nasc=include_nasc,
        include_echograms=include_echograms,
    )
    
    # Add echodata assets to collection
    _add_echodata_assets(
        collection,
        echodata_dir,
        stac_dir,
        frequencies_khz=frequencies_khz or [],
        include_mvbs=include_mvbs,
        include_nasc=include_nasc,
    )
    
    # Create echodata items
    if include_mvbs:
        mvbs_item = emit_echodata_item(
            echodata_dir,
            campaign_id,
            product="mvbs",
            sonar_model=sonar_model,
            frequencies_khz=frequencies_khz,
            platform_ids=platform_ids,
        )
        if mvbs_item:
            item_path = items_dir / "echodata-mvbs.json"
            with open(item_path, "w", encoding="utf-8") as f:
                json.dump(mvbs_item, f, indent=2)
    
    if include_nasc:
        nasc_item = emit_echodata_item(
            echodata_dir,
            campaign_id,
            product="nasc",
            sonar_model=sonar_model,
            frequencies_khz=frequencies_khz,
            platform_ids=platform_ids,
        )
        if nasc_item:
            item_path = items_dir / "echodata-nasc.json"
            with open(item_path, "w", encoding="utf-8") as f:
                json.dump(nasc_item, f, indent=2)
    
    if include_echograms:
        echograms_item = _create_echograms_item(
            echodata_dir,
            campaign_id,
            sonar_model=sonar_model,
            frequencies_khz=frequencies_khz,
            platform_ids=platform_ids,
        )
        if echograms_item:
            item_path = items_dir / "echodata-echograms.json"
            with open(item_path, "w", encoding="utf-8") as f:
                json.dump(echograms_item, f, indent=2)
    
    # Update collection file
    with open(collection_path, "w", encoding="utf-8") as f:
        json.dump(collection, f, indent=2)
    
    logger.info(f"Updated STAC collection with echodata at {collection_path}")
    
    return collection_path


def emit_echodata_item(
    echodata_dir: Path,
    campaign_id: str,
    product: str,
    *,
    sonar_model: str = "EK80",
    frequencies_khz: Optional[list[float]] = None,
    platform_ids: Optional[list[str]] = None,
    range_bin: Optional[str] = None,
    ping_time_bin: Optional[str] = None,
    dist_bin: Optional[str] = None,
) -> Optional[dict[str, Any]]:
    """
    Create a STAC item for a single echodata product (MVBS or NASC).
    
    Args:
        echodata_dir: Path to echodata directory
        campaign_id: Campaign identifier
        product: Product type ("mvbs" or "nasc")
        sonar_model: Echosounder model
        frequencies_khz: Frequencies in kHz
        platform_ids: Platform identifiers
        range_bin: Range bin size (e.g., "1m")
        ping_time_bin: Ping time bin size (e.g., "5s")
        dist_bin: Distance bin size (e.g., "0.5nmi")
        
    Returns:
        STAC item dict or None if product doesn't exist
    """
    echodata_dir = Path(echodata_dir)
    product_dir = echodata_dir / product
    
    # Find Zarr store
    zarr_path = None
    if product_dir.exists():
        zarr_stores = list(product_dir.glob("*.zarr"))
        if zarr_stores:
            zarr_path = zarr_stores[0]
        elif (product_dir / "mvbs.zarr").exists():
            zarr_path = product_dir / "mvbs.zarr"
        elif (product_dir / "nasc.zarr").exists():
            zarr_path = product_dir / "nasc.zarr"
    
    # Also check for direct zarr file
    if zarr_path is None:
        if (echodata_dir / f"{product}.zarr").exists():
            zarr_path = echodata_dir / f"{product}.zarr"
    
    if zarr_path is None or not zarr_path.exists():
        logger.warning(f"No {product} Zarr found in {echodata_dir}")
        return None
    
    # Get extent from Zarr
    extent = _get_zarr_extent(zarr_path)
    
    # Build item
    item = {
        "type": "Feature",
        "stac_version": STAC_VERSION,
        "id": f"{campaign_id}-echodata-{product}",
        "collection": campaign_id,
        "bbox": extent.get("bbox", [-180, -90, 180, 90]),
        "geometry": _bbox_to_polygon(extent.get("bbox", [-180, -90, 180, 90])),
        "properties": {
            "datetime": None,
            "start_datetime": extent.get("start_datetime"),
            "end_datetime": extent.get("end_datetime"),
            "echodata:sonar_model": sonar_model,
            "echodata:product": product,
        },
        "assets": {
            "zarr": {
                "href": f"../echodata/{product}/{zarr_path.name}",
                "type": "application/x-zarr",
                "roles": ["data"],
                "title": f"{product.upper()} Zarr dataset",
            }
        },
        "links": [
            {"rel": "collection", "href": "../collection.json"}
        ],
    }
    
    # Add optional properties
    if frequencies_khz:
        item["properties"]["echodata:frequencies_khz"] = frequencies_khz
    
    if platform_ids:
        item["properties"]["platform_ids"] = platform_ids
    
    if product == "mvbs":
        if range_bin:
            item["properties"]["echodata:range_bin_m"] = range_bin
        if ping_time_bin:
            item["properties"]["echodata:ping_time_bin_s"] = ping_time_bin
    elif product == "nasc":
        if range_bin:
            item["properties"]["echodata:range_bin_m"] = range_bin
        if dist_bin:
            item["properties"]["echodata:dist_bin_nmi"] = dist_bin
    
    return item


def get_echodata_summaries(
    sonar_model: str = "EK80",
    frequencies_khz: Optional[list[float]] = None,
    waveform_mode: str = "CW",
    processing_level: str = "L3",
    denoising_methods: Optional[list[str]] = None,
    include_mvbs: bool = True,
    include_nasc: bool = True,
    include_echograms: bool = True,
) -> dict[str, Any]:
    """
    Create echodata summaries for STAC collection.
    
    Returns:
        Dict with echodata summary fields
    """
    products = []
    if include_mvbs:
        products.append("mvbs")
    if include_nasc:
        products.append("nasc")
    if include_echograms:
        products.append("echograms")
    
    summaries = {
        "sonar_model": sonar_model,
        "waveform_mode": waveform_mode,
        "processing_level": processing_level,
        "products": products,
        "concatenation": "daily",
    }
    
    if frequencies_khz:
        summaries["frequencies_khz"] = frequencies_khz
    
    if denoising_methods:
        summaries["denoising_applied"] = denoising_methods
    
    return summaries


def _add_echodata_assets(
    collection: dict[str, Any],
    echodata_dir: Path,
    stac_dir: Path,
    frequencies_khz: list[float],
    include_mvbs: bool = True,
    include_nasc: bool = True,
) -> None:
    """Add MVBS and NASC assets to collection."""
    collection["assets"] = collection.get("assets", {})
    
    # Calculate relative path from stac_dir to echodata_dir
    try:
        rel_echodata = Path("..") / echodata_dir.relative_to(stac_dir.parent)
    except ValueError:
        rel_echodata = Path("../echodata")
    
    if include_mvbs:
        mvbs_dir = echodata_dir / "mvbs"
        if mvbs_dir.exists():
            zarr_stores = list(mvbs_dir.glob("*.zarr"))
            if zarr_stores:
                zarr_name = zarr_stores[0].name
            else:
                zarr_name = "mvbs.zarr"
            
            collection["assets"]["mvbs"] = {
                "href": f"{rel_echodata}/mvbs/{zarr_name}",
                "type": "application/x-zarr",
                "roles": ["data", "echodata"],
                "title": "Mean Volume Backscattering Strength (MVBS)",
            }
            if frequencies_khz:
                collection["assets"]["mvbs"]["echodata:frequencies_khz"] = frequencies_khz
    
    if include_nasc:
        nasc_dir = echodata_dir / "nasc"
        if nasc_dir.exists():
            zarr_stores = list(nasc_dir.glob("*.zarr"))
            if zarr_stores:
                zarr_name = zarr_stores[0].name
            else:
                zarr_name = "nasc.zarr"
            
            collection["assets"]["nasc"] = {
                "href": f"{rel_echodata}/nasc/{zarr_name}",
                "type": "application/x-zarr",
                "roles": ["data", "echodata"],
                "title": "Nautical Area Scattering Coefficient (NASC)",
            }
            if frequencies_khz:
                collection["assets"]["nasc"]["echodata:frequencies_khz"] = frequencies_khz


def _create_echograms_item(
    echodata_dir: Path,
    campaign_id: str,
    *,
    sonar_model: str = "EK80",
    frequencies_khz: Optional[list[float]] = None,
    platform_ids: Optional[list[str]] = None,
) -> Optional[dict[str, Any]]:
    """Create STAC item for echogram images with segment coordinates."""
    echograms_dir = echodata_dir / "echograms"
    
    if not echograms_dir.exists():
        return None
    
    png_files = sorted(echograms_dir.glob("*.png"))
    if not png_files:
        return None
    
    # Build item
    item = {
        "type": "Feature",
        "stac_version": STAC_VERSION,
        "id": f"{campaign_id}-echodata-echograms",
        "collection": campaign_id,
        "bbox": [-180, -90, 180, 90],  # Will be refined if segments available
        "geometry": {"type": "Polygon", "coordinates": [[
            [-180, -90], [180, -90], [180, 90], [-180, 90], [-180, -90]
        ]]},
        "properties": {
            "datetime": None,
            "echodata:sonar_model": sonar_model,
            "echodata:product": "echograms",
        },
        "assets": {},
        "links": [
            {"rel": "collection", "href": "../collection.json"}
        ],
    }
    
    if frequencies_khz:
        item["properties"]["echodata:frequencies_khz"] = frequencies_khz
    
    if platform_ids:
        item["properties"]["platform_ids"] = platform_ids
    
    # Try to load segments for coordinate enrichment
    segments_path = echodata_dir / "segments.geojson"
    segments = None
    if segments_path.exists():
        try:
            with open(segments_path, "r", encoding="utf-8") as f:
                segments = json.load(f)
        except Exception:
            pass
    
    # Add echogram assets
    for png_file in png_files:
        # Parse filename: YYYY-MM-DD_38kHz.png or similar
        stem = png_file.stem
        parts = stem.rsplit("_", 1)
        
        if len(parts) == 2:
            date_part, freq_part = parts
            freq_str = freq_part.replace("kHz", "").replace("khz", "")
            try:
                freq_khz = float(freq_str)
            except ValueError:
                freq_khz = None
        else:
            date_part = stem
            freq_khz = None
        
        asset_key = f"echogram_{stem}"
        asset = {
            "href": f"../echodata/echograms/{png_file.name}",
            "type": "image/png",
            "roles": ["thumbnail", "visual"],
            "title": f"Echogram - {stem}",
        }
        
        if freq_khz:
            asset["echodata:frequency_khz"] = freq_khz
        
        # Add segment coordinates if available
        if segments and "features" in segments:
            for feature in segments["features"]:
                props = feature.get("properties", {})
                if props.get("date") == date_part:
                    asset["echodata:date"] = date_part
                    asset["echodata:start_datetime"] = props.get("start_datetime")
                    asset["echodata:end_datetime"] = props.get("end_datetime")
                    
                    coords = feature.get("geometry", {}).get("coordinates", [])
                    if coords and len(coords) >= 2:
                        asset["echodata:start_lat"] = coords[0][1]
                        asset["echodata:start_lon"] = coords[0][0]
                        asset["echodata:end_lat"] = coords[-1][1]
                        asset["echodata:end_lon"] = coords[-1][0]
                    break
        
        item["assets"][asset_key] = asset
    
    return item


def _detect_frequencies(echodata_dir: Path) -> list[float]:
    """Auto-detect frequencies from MVBS/NASC Zarr metadata."""
    frequencies = []
    
    for product in ["mvbs", "nasc", "sv"]:
        product_dir = echodata_dir / product
        if not product_dir.exists():
            continue
        
        zarr_stores = list(product_dir.glob("*.zarr"))
        if not zarr_stores:
            continue
        
        try:
            import xarray as xr
            ds = xr.open_zarr(zarr_stores[0])
            
            if "frequency_nominal" in ds:
                freq_hz = ds["frequency_nominal"].values
                frequencies.extend([f / 1000.0 for f in freq_hz if not np.isnan(f)])
            elif "channel" in ds.dims:
                # Try to parse from channel names
                for ch in ds["channel"].values:
                    ch_str = str(ch)
                    # Look for patterns like "38kHz" or "ES38"
                    import re
                    match = re.search(r"(\d+)\s*k?[hH]z|ES(\d+)", ch_str)
                    if match:
                        freq = float(match.group(1) or match.group(2))
                        frequencies.append(freq)
            
            ds.close()
            break  # Found frequencies, no need to check other products
            
        except Exception as e:
            logger.debug(f"Could not auto-detect frequencies from {zarr_stores[0]}: {e}")
    
    return sorted(set(frequencies)) if frequencies else [38.0, 200.0]  # Default


def _get_echodata_extent(echodata_dir: Path) -> dict[str, Any]:
    """Get temporal and spatial extent from echodata products."""
    extent = {
        "spatial": {"bbox": [[-180, -90, 180, 90]]},
        "temporal": {"interval": [[None, None]]},
    }
    
    # Try to get extent from MVBS or NASC
    for product in ["mvbs", "nasc", "sv"]:
        product_dir = echodata_dir / product
        if not product_dir.exists():
            continue
        
        zarr_stores = list(product_dir.glob("*.zarr"))
        if not zarr_stores:
            continue
        
        zarr_extent = _get_zarr_extent(zarr_stores[0])
        if zarr_extent.get("bbox"):
            extent["spatial"]["bbox"] = [zarr_extent["bbox"]]
        if zarr_extent.get("start_datetime") or zarr_extent.get("end_datetime"):
            extent["temporal"]["interval"] = [[
                zarr_extent.get("start_datetime"),
                zarr_extent.get("end_datetime"),
            ]]
        break
    
    return extent


def _get_zarr_extent(zarr_path: Path) -> dict[str, Any]:
    """Extract temporal and spatial extent from Zarr store."""
    extent = {}
    
    try:
        import xarray as xr
        ds = xr.open_zarr(zarr_path)
        
        # Temporal extent
        if "ping_time" in ds.dims or "ping_time" in ds.coords:
            times = ds["ping_time"].values
            if len(times) > 0:
                extent["start_datetime"] = str(np.datetime_as_string(times.min(), unit='s')) + "Z"
                extent["end_datetime"] = str(np.datetime_as_string(times.max(), unit='s')) + "Z"
        
        # Spatial extent
        lat_var = None
        lon_var = None
        
        for var in ["latitude", "lat"]:
            if var in ds.data_vars or var in ds.coords:
                lat_var = var
                break
        
        for var in ["longitude", "lon"]:
            if var in ds.data_vars or var in ds.coords:
                lon_var = var
                break
        
        if lat_var and lon_var:
            lats = ds[lat_var].values.flatten()
            lons = ds[lon_var].values.flatten()
            lats = lats[~np.isnan(lats)]
            lons = lons[~np.isnan(lons)]
            
            if len(lats) > 0 and len(lons) > 0:
                extent["bbox"] = [
                    float(lons.min()),
                    float(lats.min()),
                    float(lons.max()),
                    float(lats.max()),
                ]
        
        ds.close()
        
    except Exception as e:
        logger.debug(f"Could not get extent from {zarr_path}: {e}")
    
    return extent


def _bbox_to_polygon(bbox: list[float]) -> dict[str, Any]:
    """Convert bbox to GeoJSON Polygon."""
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
