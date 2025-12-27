"""Campaign validation utilities.

Validates campaign metadata consistency with actual output data.
"""
from __future__ import annotations
from pathlib import Path
from typing import Any


def validate_campaign_output(campaign_metadata: dict[str, Any]) -> dict[str, Any]:
    """Validate that campaign output directory exists and contains data.
    
    Args:
        campaign_metadata: Campaign metadata dict with 'output_directory' field
        
    Returns:
        Dict with validation results:
        - 'valid': bool - Whether output is valid
        - 'output_exists': bool - Whether output directory exists
        - 'has_parquet': bool - Whether parquet files exist
        - 'parquet_count': int - Number of parquet files found
        - 'has_stac': bool - Whether STAC metadata exists
        - 'issues': list[str] - List of issues found
    """
    result = {
        'valid': True,
        'output_exists': False,
        'has_parquet': False,
        'parquet_count': 0,
        'has_stac': False,
        'issues': [],
    }
    
    # Check if output_directory is specified
    if 'output_directory' not in campaign_metadata:
        result['valid'] = False
        result['issues'].append("No output_directory specified in metadata")
        return result
    
    output_dir = Path(campaign_metadata['output_directory'])
    
    # Check if directory exists
    if not output_dir.exists():
        result['valid'] = False
        result['issues'].append(f"Output directory does not exist: {output_dir}")
        return result
    
    result['output_exists'] = True
    
    # Check for parquet files
    parquet_files = list(output_dir.rglob("*.parquet"))
    result['parquet_count'] = len(parquet_files)
    result['has_parquet'] = len(parquet_files) > 0
    
    if not result['has_parquet']:
        result['valid'] = False
        result['issues'].append(f"No parquet files found in: {output_dir}")
    
    # Check for STAC metadata
    stac_dir = output_dir / "stac"
    collection_file = stac_dir / "collection.json"
    result['has_stac'] = collection_file.exists()
    
    if not result['has_stac']:
        result['issues'].append(f"STAC metadata not found in: {stac_dir}")
    
    return result


def should_reprocess_campaign(
    campaign_metadata: dict[str, Any],
    force_reprocess: bool = False
) -> tuple[bool, list[str]]:
    """Determine if a campaign should be reprocessed.
    
    Args:
        campaign_metadata: Campaign metadata dict
        force_reprocess: Force reprocessing regardless of validation
        
    Returns:
        Tuple of (should_reprocess: bool, reasons: list[str])
    """
    reasons = []
    
    # Always reprocess if forced
    if force_reprocess:
        reasons.append("Force reprocess flag set")
        return True, reasons
    
    # Validate output
    validation = validate_campaign_output(campaign_metadata)
    
    if not validation['valid']:
        reasons.extend(validation['issues'])
        return True, reasons
    
    # Output is valid, no need to reprocess
    return False, []


def clear_invalid_campaign_metadata(
    campaign_id: str,
    campaigns_dir: Path,
    verbose: bool = False
) -> bool:
    """Clear campaign metadata if output is invalid.
    
    Args:
        campaign_id: Campaign identifier
        campaigns_dir: Path to campaigns directory (e.g., ~/.oceanstream/campaigns)
        verbose: Print detailed information
        
    Returns:
        True if metadata was cleared, False otherwise
    """
    import shutil
    from .campaign import load_campaign_metadata
    
    metadata = load_campaign_metadata(campaign_id)
    if not metadata:
        return False
    
    validation = validate_campaign_output(metadata)
    
    if not validation['valid']:
        if verbose:
            print(f"[campaign validation] Campaign '{campaign_id}' output is invalid:")
            for issue in validation['issues']:
                print(f"  - {issue}")
            print(f"[campaign validation] Clearing campaign metadata to allow reprocessing")
        
        # Remove campaign metadata directory
        campaign_dir = campaigns_dir / campaign_id
        if campaign_dir.exists():
            shutil.rmtree(campaign_dir)
            if verbose:
                print(f"[campaign validation] Removed: {campaign_dir}")
            return True
    
    return False


def clear_campaign_metadata(
    campaign_id: str,
    campaigns_dir: Path,
    verbose: bool = False
) -> bool:
    """Unconditionally clear campaign metadata.
    
    Use this when you need to clear metadata for reasons other than invalid output
    (e.g., output directory changed, force reprocess).
    
    Args:
        campaign_id: Campaign identifier
        campaigns_dir: Path to campaigns directory (e.g., ~/.oceanstream/campaigns)
        verbose: Print detailed information
        
    Returns:
        True if metadata was cleared, False if campaign didn't exist
    """
    import shutil
    
    campaign_dir = campaigns_dir / campaign_id
    if campaign_dir.exists():
        shutil.rmtree(campaign_dir)
        if verbose:
            print(f"[campaign metadata] Removed: {campaign_dir}")
        return True
    
    return False
