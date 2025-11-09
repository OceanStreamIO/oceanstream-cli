"""
R2R (Rolling Deck to Repository) data provider.

Handles GeoCSV format data from the R2R program, which manages underway
oceanographic data from research vessels.

Key characteristics:
- GeoCSV format with rich metadata headers (# prefix lines)
- Per-instrument files (navigation, CTD, SVP, ADCP, etc.)
- Cruise ID based platform identification
- Standard column names: ship_longitude, ship_latitude, iso_time
- DOI links for data provenance
"""
import re
from typing import Dict, Any, List, Literal
import pandas as pd

from .base import ProviderBase, ProcessingModule


class R2RProvider(ProviderBase):
    """Provider for R2R (Rolling Deck to Repository) GeoCSV data."""
    
    name: str = "r2r"
    supported_modules: List[ProcessingModule] = ["geotrack"]
    
    # Column name mappings: R2R name -> oceanstream standard
    COLUMN_MAPPINGS = {
        # R2R uses "ship_*" prefix for vessel position
        "ship_longitude": "longitude",
        "ship_latitude": "latitude",
        "ship_depth": "depth",
        
        # R2R uses "iso_time" for timestamps
        "iso_time": "time",
        
        # GPS quality indicators
        "nmea_quality": "gps_quality",
        "nsv": "num_satellites",
        "hdop": "horizontal_dilution",
        
        # Navigation parameters
        "speed_made_good": "speed_over_ground",
        "course_made_good": "course_over_ground",
        "antenna_height": "gps_antenna_height",
    }
    
    # Units mappings for standard columns
    STANDARD_UNITS = {
        "longitude": "degree_east",
        "latitude": "degree_north",
        "depth": "meters",
        "time": "ISO_8601",
        "speed_over_ground": "meters_per_second",
        "course_over_ground": "degree",
        "gps_antenna_height": "meters",
    }
    
    def identify_platform(self, filename: str) -> str | None:
        """
        Extract platform/cruise ID from R2R filename.
        
        R2R filenames typically start with cruise ID, e.g.:
        - FK161229_607994_r2rnav.geocsv -> FK161229
        - AT42-10_some_data.geocsv -> AT42-10
        - NBP1402_ctd_001.geocsv -> NBP1402
        
        Args:
            filename: Name of the R2R file
            
        Returns:
            Cruise ID or None if cannot be determined
        """
        # R2R cruise IDs are typically at start of filename before first underscore
        # Format: <CruiseID>_<EventID>_<InstrumentType>.geocsv
        if "_" in filename:
            cruise_id = filename.split("_")[0]
            # Validate it looks like a cruise ID (letters + numbers, possibly with hyphen)
            if re.match(r'^[A-Z]{2,4}\d{2,6}(-\d+)?$', cruise_id, re.IGNORECASE):
                return cruise_id.upper()
        
        return None
    
    def enrich_dataframe(self, df: pd.DataFrame, metadata: Dict[str, Any] | None = None) -> pd.DataFrame:
        """
        Enrich and standardize R2R dataframe.
        
        Performs:
        - Column renaming (ship_longitude -> longitude, iso_time -> time, etc.)
        - Data type conversions
        - Coordinate validation
        - Time parsing
        
        Args:
            df: Input dataframe
            metadata: Optional GeoCSV metadata dict
            
        Returns:
            Enriched dataframe with standardized column names
        """
        df = df.copy()
        
        # Rename columns to oceanstream standards
        rename_map = {
            col: new_col 
            for col, new_col in self.COLUMN_MAPPINGS.items() 
            if col in df.columns
        }
        if rename_map:
            df = df.rename(columns=rename_map)
        
        # Ensure required columns exist
        if 'latitude' in df.columns and 'longitude' in df.columns:
            # Convert to numeric
            df['latitude'] = pd.to_numeric(df['latitude'], errors='coerce')
            df['longitude'] = pd.to_numeric(df['longitude'], errors='coerce')
            
            # Drop invalid coordinates
            df = df.dropna(subset=['latitude', 'longitude'])
            
            # Validate coordinate ranges
            df = df[(df['latitude'] >= -90) & (df['latitude'] <= 90)]
            df = df[(df['longitude'] >= -180) & (df['longitude'] <= 180)]
        
        # Parse time column
        if 'time' in df.columns and not pd.api.types.is_datetime64_any_dtype(df['time']):
            try:
                df['time'] = pd.to_datetime(df['time'], errors='coerce')
            except Exception:
                pass
        
        # Convert numeric columns
        numeric_cols = [
            'depth', 'gps_quality', 'num_satellites', 
            'horizontal_dilution', 'speed_over_ground',
            'course_over_ground', 'gps_antenna_height'
        ]
        for col in numeric_cols:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce')
        
        # Add platform_id from metadata if available
        if metadata and 'cruise_id' in metadata and 'platform_id' not in df.columns:
            df['platform_id'] = metadata['cruise_id']
        
        return df
    
    def units_mapping(self, header: List[str], units_row: List[str] | None = None, 
                     metadata: Dict[str, str] | None = None) -> Dict[str, str]:
        """
        Extract units mapping from GeoCSV metadata or data.
        
        R2R GeoCSV files include a #field_unit metadata line specifying units
        for each column, e.g.:
        #field_unit: ISO_8601,degree_east,degree_north,(unitless),m,...
        
        Args:
            header: Column names from CSV
            units_row: Optional units row from CSV (not used for GeoCSV)
            metadata: GeoCSV metadata dict containing field_unit
            
        Returns:
            Dictionary mapping column names to units
        """
        units_map = {}
        
        # Parse from GeoCSV metadata if available
        if metadata and 'field_unit' in metadata:
            # field_unit format: "unit1,unit2,unit3,..."
            units_str = metadata['field_unit']
            unit_parts = [u.strip() for u in units_str.split(',')]
            
            # Map units to columns
            for col, unit in zip(header, unit_parts):
                if unit and unit != '(unitless)':
                    units_map[col] = unit
        
        # Add standard units for renamed columns
        for col, unit in self.STANDARD_UNITS.items():
            if col in header or col in units_map:
                units_map[col] = unit
        
        return units_map
    
    def alias_mapping(self, columns: List[str]) -> Dict[str, str]:
        """
        Get column name aliases for R2R data.
        
        Maps R2R column names to oceanstream standard names.
        
        Args:
            columns: List of column names from the dataset
            
        Returns:
            Dictionary mapping original names to standard names
        """
        return {
            original: standard
            for original, standard in self.COLUMN_MAPPINGS.items()
            if original in columns
        }
    
    def parquet_metadata(self, df: pd.DataFrame, metadata: Dict[str, str] | None = None) -> Dict[str, str]:
        """
        Generate parquet metadata from R2R GeoCSV metadata.
        
        Preserves important provenance information:
        - cruise_id: Platform/cruise identifier
        - source_repository: DOI for data repository
        - source_event: DOI for cruise event
        - source_dataset: DOI for specific dataset
        - attribution: R2R attribution text
        - creation_date: Processing timestamp
        
        Args:
            df: Dataframe to generate metadata for
            metadata: GeoCSV metadata dict
            
        Returns:
            Dictionary of metadata key-value pairs for parquet
        """
        parquet_meta = {
            "oceanstream:provider": self.name,
        }
        
        if metadata:
            # Preserve R2R-specific metadata
            r2r_keys = [
                'cruise_id', 'dataset', 'field_type', 'field_standard_name',
                'source_repository', 'source_event', 'source_dataset',
                'attribution', 'creation_date', 'modification_date'
            ]
            
            for key in r2r_keys:
                if key in metadata:
                    parquet_meta[f"r2r:{key}"] = metadata[key]
            
            # Also add cruise_id to standard location for easy access
            if 'cruise_id' in metadata:
                parquet_meta["oceanstream:cruise_id"] = metadata['cruise_id']
                parquet_meta["oceanstream:platform"] = metadata['cruise_id']
        
        # Add row count
        parquet_meta["oceanstream:row_count"] = str(len(df))
        
        # Add column info
        if 'latitude' in df.columns and 'longitude' in df.columns:
            parquet_meta["oceanstream:has_coordinates"] = "true"
        
        if 'time' in df.columns:
            parquet_meta["oceanstream:has_time"] = "true"
            if pd.api.types.is_datetime64_any_dtype(df['time']):
                time_range = f"{df['time'].min().isoformat()}/{df['time'].max().isoformat()}"
                parquet_meta["oceanstream:time_range"] = time_range
        
        return parquet_meta
    
    def supports_module(self, module: ProcessingModule) -> bool:
        """
        Check if this provider supports a processing module.
        
        Currently R2R provider supports:
        - geotrack: Navigation trackline data
        
        Future support planned:
        - echodata: If R2R provides echosounder data
        - adcp: R2R ADCP raw files
        - multibeam: R2R multibeam sonar
        
        Args:
            module: Processing module name
            
        Returns:
            True if module is supported
        """
        return module in self.supported_modules
