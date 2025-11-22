"""Campaign creation and management utilities.

Campaigns are stored in ~/.oceanstream/campaigns/ for persistence and discoverability.
This is separate from the output data directory which users can delete/recreate.
"""
from __future__ import annotations
import json
from pathlib import Path
from datetime import datetime, timezone
from typing import Any


def get_campaigns_dir() -> Path:
    """Get the campaigns directory in the user's home directory.
    
    Returns:
        Path to ~/.oceanstream/campaigns/
    """
    campaigns_dir = Path.home() / ".oceanstream" / "campaigns"
    campaigns_dir.mkdir(parents=True, exist_ok=True)
    return campaigns_dir


def create_campaign(
    campaign_id: str,
    metadata: dict[str, Any],
    verbose: bool = False,
) -> Path:
    """Create a new campaign with metadata.
    
    This function creates a campaign directory and stores metadata in
    ~/.oceanstream/campaigns/{campaign_id}/campaign.json for persistence.
    This is separate from the output data directory which users can delete/recreate.
    
    Args:
        campaign_id: Campaign identifier (e.g., "FK161229", "SD1030_2023")
        metadata: Campaign metadata dictionary with optional fields:
            - platform_id: Platform identifier
            - platform_name: Full platform name
            - platform_type: Platform type
            - description: Campaign description
            - start_date: Campaign start date (ISO 8601)
            - end_date: Campaign end date (ISO 8601)
            - bbox: Spatial bounding box [minlon, minlat, maxlon, maxlat]
            - attribution: Data attribution/citation
            - license: Data license
            - doi: Dataset DOI
            - source_repository: Source repository DOI/URL
            - keywords: List of keywords
            - chief_scientist: Chief scientist name
            - institution: Institution name
            - project: Project name
            - funding: Funding information
        verbose: Print detailed information
        
    Returns:
        Path to created campaign directory (~/.oceanstream/campaigns/{campaign_id})
        
    Raises:
        ValueError: If campaign already exists or invalid parameters
        OSError: If directory creation fails
    """
    campaigns_dir = get_campaigns_dir()
    campaign_dir = campaigns_dir / campaign_id
    metadata_file = campaign_dir / "campaign.json"
    
    # Check if campaign already exists
    if campaign_dir.exists():
        if metadata_file.exists():
            raise ValueError(
                f"Campaign '{campaign_id}' already exists at {campaign_dir}. "
                f"Use a different campaign ID or delete the existing campaign first."
            )
        else:
            if verbose:
                print(f"[campaign create] Campaign directory exists but no metadata found, will create metadata")
    
    # Create campaign directory
    campaign_dir.mkdir(parents=True, exist_ok=True)
    
    # Add system metadata
    now = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
    full_metadata = {
        "campaign_id": campaign_id,
        "created_at": now,
        "updated_at": now,
        "oceanstream_version": "0.1.0",  # TODO: Get from package version
        **metadata,
    }
    
    # Validate dates if provided
    if "start_date" in full_metadata:
        try:
            # Try to parse as datetime to validate format
            datetime.fromisoformat(full_metadata["start_date"].replace('Z', '+00:00'))
        except ValueError as e:
            raise ValueError(f"Invalid start_date format: {e}. Use ISO 8601 format (YYYY-MM-DD or YYYY-MM-DDTHH:MM:SSZ)")
    
    if "end_date" in full_metadata:
        try:
            datetime.fromisoformat(full_metadata["end_date"].replace('Z', '+00:00'))
        except ValueError as e:
            raise ValueError(f"Invalid end_date format: {e}. Use ISO 8601 format (YYYY-MM-DD or YYYY-MM-DDTHH:MM:SSZ)")
    
    # Validate bbox if provided
    if "bbox" in full_metadata:
        bbox = full_metadata["bbox"]
        if not isinstance(bbox, list) or len(bbox) != 4:
            raise ValueError(f"bbox must be a list of 4 numbers [minlon, minlat, maxlon, maxlat], got: {bbox}")
        minlon, minlat, maxlon, maxlat = bbox
        if not (-180 <= minlon <= 180 and -180 <= maxlon <= 180):
            raise ValueError(f"Longitude values must be in range [-180, 180], got: {minlon}, {maxlon}")
        if not (-90 <= minlat <= 90 and -90 <= maxlat <= 90):
            raise ValueError(f"Latitude values must be in range [-90, 90], got: {minlat}, {maxlat}")
        if minlon >= maxlon:
            raise ValueError(f"minlon ({minlon}) must be less than maxlon ({maxlon})")
        if minlat >= maxlat:
            raise ValueError(f"minlat ({minlat}) must be less than maxlat ({maxlat})")
    
    # Write metadata file
    with open(metadata_file, 'w') as f:
        json.dump(full_metadata, f, indent=2)
    
    if verbose:
        print(f"[campaign create] Created campaign directory: {campaign_dir}")
        print(f"[campaign create] Wrote metadata to: {metadata_file}")
        print(f"[campaign create] Metadata fields:")
        for key, value in full_metadata.items():
            if key not in ['created_at', 'updated_at', 'oceanstream_version']:
                print(f"  - {key}: {value}")
    
    return campaign_dir


def load_campaign_metadata(campaign_id: str) -> dict[str, Any] | None:
    """Load campaign metadata for a given campaign ID.
    
    Args:
        campaign_id: Campaign identifier
        
    Returns:
        Campaign metadata dict or None if not found
    """
    campaigns_dir = get_campaigns_dir()
    campaign_dir = campaigns_dir / campaign_id
    metadata_file = campaign_dir / "campaign.json"
    
    if not metadata_file.exists():
        return None
    
    try:
        with open(metadata_file, 'r') as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return None


def update_campaign_metadata(campaign_id: str, updates: dict[str, Any]) -> None:
    """Update campaign metadata with new fields.
    
    Args:
        campaign_id: Campaign identifier
        updates: Dictionary of fields to update
        
    Raises:
        FileNotFoundError: If campaign metadata doesn't exist
    """
    campaigns_dir = get_campaigns_dir()
    campaign_dir = campaigns_dir / campaign_id
    metadata_file = campaign_dir / "campaign.json"
    
    if not metadata_file.exists():
        raise FileNotFoundError(f"Campaign metadata not found: {metadata_file}")
    
    # Load existing metadata
    metadata = load_campaign_metadata(campaign_id)
    if metadata is None:
        raise FileNotFoundError(f"Could not load campaign metadata: {metadata_file}")
    
    # Update fields
    metadata.update(updates)
    metadata["updated_at"] = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
    
    # Write back
    with open(metadata_file, 'w') as f:
        json.dump(metadata, f, indent=2)


def list_campaigns() -> list[dict[str, Any]]:
    """List all campaigns in the campaigns directory.
    
    Returns:
        List of campaign metadata dictionaries, sorted by campaign_id
    """
    campaigns_dir = get_campaigns_dir()
    campaigns = []
    
    # Iterate through all subdirectories
    for campaign_dir in sorted(campaigns_dir.iterdir()):
        if not campaign_dir.is_dir():
            continue
        
        # Try to load metadata
        metadata_file = campaign_dir / "campaign.json"
        if metadata_file.exists():
            try:
                with open(metadata_file) as f:
                    metadata = json.load(f)
                    campaigns.append(metadata)
            except (json.JSONDecodeError, IOError):
                # Skip invalid metadata files
                continue
    
    return campaigns


def delete_campaign(campaign_id: str, verbose: bool = False) -> None:
    """Delete a campaign and its metadata.
    
    Args:
        campaign_id: Campaign identifier
        verbose: If True, print detailed information
        
    Raises:
        FileNotFoundError: If campaign does not exist
    """
    campaigns_dir = get_campaigns_dir()
    campaign_dir = campaigns_dir / campaign_id
    
    if not campaign_dir.exists():
        raise FileNotFoundError(f"Campaign '{campaign_id}' not found at {campaign_dir}")
    
    # Remove the campaign directory and all contents
    import shutil
    shutil.rmtree(campaign_dir)
    
    if verbose:
        print(f"Deleted campaign: {campaign_id}")
        print(f"  Location: {campaign_dir}")


def inspect_campaign_data(
    campaign_id: str,
    output_dir: Path,
    limit: int = 10,
    verbose: bool = False,
) -> dict[str, Any]:
    """Inspect campaign data and return information about processed datasets.
    
    Args:
        campaign_id: Campaign identifier
        output_dir: Base output directory where campaign data is stored
        limit: Number of rows to return from GeoParquet (default: 10)
        verbose: If True, print detailed information
        
    Returns:
        Dictionary with:
            - 'campaign_dir': Path to campaign output directory
            - 'has_geoparquet': Boolean
            - 'geoparquet_sample': DataFrame with sample rows (if exists)
            - 'geoparquet_info': Dict with row count, columns, size (if exists)
            - 'stac_collection': Path to STAC collection (if exists)
            - 'stac_items': List of STAC item paths (if exists)
            - 'pmtiles': List of PMTiles file paths (if exists)
            
    Raises:
        FileNotFoundError: If campaign output directory doesn't exist
    """
    import pandas as pd
    
    campaign_dir = output_dir / campaign_id
    
    if not campaign_dir.exists():
        raise FileNotFoundError(
            f"No processed data found for campaign '{campaign_id}' in {output_dir}. "
            f"Have you run 'oceanstream process geotrack convert' yet?"
        )
    
    result: dict[str, Any] = {
        'campaign_dir': campaign_dir,
        'has_geoparquet': False,
        'geoparquet_sample': None,
        'geoparquet_info': None,
        'stac_collection': None,
        'stac_items': [],
        'pmtiles': [],
    }
    
    # Check for GeoParquet files (exclude STAC directory)
    parquet_files = [f for f in campaign_dir.glob("**/*.parquet") 
                     if 'stac' not in f.parts]
    if parquet_files:
        result['has_geoparquet'] = True
        
        if verbose:
            print(f"[inspect] Found {len(parquet_files)} GeoParquet file(s)")
        
        # Read all parquet files and get sample
        # Note: Using pandas.read_parquet() instead of geopandas because
        # the files may not have geo metadata if no geometry column was written
        try:
            # Read all parquet data files - pandas will combine them
            df = pd.concat([pd.read_parquet(f) for f in parquet_files], ignore_index=True)
            
            result['geoparquet_info'] = {
                'total_rows': len(df),
                'columns': list(df.columns),
                'memory_usage_mb': df.memory_usage(deep=True).sum() / 1024 / 1024,
            }
            
            # Get sample (first N rows)
            result['geoparquet_sample'] = df.head(limit)
            
            if verbose:
                print(f"[inspect] Total rows: {len(df):,}")
                print(f"[inspect] Columns: {len(df.columns)}")
        except Exception as e:
            if verbose:
                print(f"[inspect] Warning: Could not read GeoParquet: {e}")
    
    # Check for STAC metadata
    stac_dir = campaign_dir / "stac"
    if stac_dir.exists():
        collection_file = stac_dir / "collection.json"
        if collection_file.exists():
            result['stac_collection'] = collection_file
        
        items_dir = stac_dir / "items"
        if items_dir.exists():
            result['stac_items'] = sorted(items_dir.glob("*.json"))
    
    # Check for PMTiles
    pmtiles_files = list(campaign_dir.glob("*.pmtiles"))
    if pmtiles_files:
        result['pmtiles'] = sorted(pmtiles_files)
    
    return result
