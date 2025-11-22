"""Campaign metadata tracking for append/update functionality."""
from __future__ import annotations
import json
import hashlib
from pathlib import Path
from datetime import datetime, timezone
from typing import Any


def _compute_file_hash(file_path: Path) -> str:
    """Compute SHA256 hash of a file for duplicate detection.
    
    Args:
        file_path: Path to file
        
    Returns:
        Hex string of SHA256 hash
    """
    sha256 = hashlib.sha256()
    with open(file_path, 'rb') as f:
        # Read in chunks to handle large files
        for chunk in iter(lambda: f.read(8192), b''):
            sha256.update(chunk)
    return sha256.hexdigest()


class CampaignMetadata:
    """Manages campaign metadata for tracking processed files and append operations."""
    
    def __init__(self, campaign_id: str, metadata_dir: Path):
        """
        Initialize campaign metadata.
        
        Args:
            campaign_id: Campaign identifier (e.g., "mission_2024")
            metadata_dir: Directory where metadata files are stored (e.g., ~/.oceanstream/metadata)
        """
        self.campaign_id = campaign_id
        self.metadata_dir = Path(metadata_dir)
        self.metadata_file = self.metadata_dir / f"{campaign_id}.json"
        self._data: dict[str, Any] = self._load()
    
    def _load(self) -> dict[str, Any]:
        """Load existing metadata or create new structure."""
        if self.metadata_file.exists():
            try:
                with open(self.metadata_file, 'r') as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError):
                # Corrupted or unreadable, start fresh
                return self._empty_metadata()
        return self._empty_metadata()
    
    def _empty_metadata(self) -> dict[str, Any]:
        """Create empty metadata structure."""
        now = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
        return {
            "version": "1.0",
            "campaign_created": now,
            "last_updated": now,
            "processed_files": {},  # filename -> {hash, processed_at, size, rows}
            "total_runs": 0,
            "total_files_processed": 0,
        }
    
    def save(self) -> None:
        """Save metadata to disk."""
        self._data["last_updated"] = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
        self.metadata_dir.mkdir(parents=True, exist_ok=True)
        with open(self.metadata_file, 'w') as f:
            json.dump(self._data, f, indent=2)
    
    def is_file_processed(self, file_path: Path) -> bool:
        """Check if a file has been processed before.
        
        Args:
            file_path: Path to CSV file
            
        Returns:
            True if file has been processed (based on filename and hash)
        """
        filename = file_path.name
        if filename not in self._data["processed_files"]:
            return False
        
        # Check if hash matches (file might have been updated)
        current_hash = _compute_file_hash(file_path)
        stored_hash = self._data["processed_files"][filename].get("hash")
        
        return current_hash == stored_hash
    
    def get_file_info(self, file_path: Path) -> dict[str, Any] | None:
        """Get stored info about a processed file.
        
        Args:
            file_path: Path to CSV file
            
        Returns:
            Dict with file info or None if not processed
        """
        filename = file_path.name
        return self._data["processed_files"].get(filename)
    
    def mark_file_processed(
        self, 
        file_path: Path, 
        rows_processed: int
    ) -> None:
        """Mark a file as processed.
        
        Args:
            file_path: Path to CSV file
            rows_processed: Number of rows processed from this file
        """
        filename = file_path.name
        file_hash = _compute_file_hash(file_path)
        file_size = file_path.stat().st_size
        
        self._data["processed_files"][filename] = {
            "hash": file_hash,
            "processed_at": datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
            "size": file_size,
            "rows": rows_processed,
        }
    
    def increment_run_count(self) -> int:
        """Increment total runs counter.
        
        Returns:
            New run count
        """
        self._data["total_runs"] += 1
        return self._data["total_runs"]
    
    def get_run_count(self) -> int:
        """Get total number of processing runs."""
        return self._data["total_runs"]
    
    def get_processed_file_count(self) -> int:
        """Get number of unique files processed."""
        return len(self._data["processed_files"])
    
    def clear(self) -> None:
        """Clear all metadata (for testing or force reprocessing)."""
        self._data = self._empty_metadata()
        if self.metadata_file.exists():
            self.metadata_file.unlink()
