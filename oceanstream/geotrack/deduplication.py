"""Deduplication utilities for GeoParquet data."""
from __future__ import annotations
from pathlib import Path
from typing import Sequence
import pandas as pd
import pyarrow.parquet as pq


# Primary key columns for identifying duplicate rows
PRIMARY_KEY_COLUMNS = ['time', 'latitude', 'longitude', 'trajectory']


def deduplicate_dataframe(
    df: pd.DataFrame,
    primary_keys: Sequence[str] | None = None
) -> pd.DataFrame:
    """Remove duplicate rows based on primary keys.
    
    Args:
        df: DataFrame to deduplicate
        primary_keys: Column names to use as primary keys. 
                     Defaults to PRIMARY_KEY_COLUMNS.
    
    Returns:
        DataFrame with duplicates removed, keeping first occurrence
    """
    if primary_keys is None:
        primary_keys = PRIMARY_KEY_COLUMNS
    
    # Only use primary key columns that exist in the DataFrame
    available_keys = [col for col in primary_keys if col in df.columns]
    
    if not available_keys:
        # No primary key columns available, can't deduplicate
        return df
    
    # Drop duplicates keeping first occurrence
    initial_rows = len(df)
    df_deduped = df.drop_duplicates(subset=available_keys, keep='first')
    duplicates_removed = initial_rows - len(df_deduped)
    
    if duplicates_removed > 0:
        print(f"[geotrack] Removed {duplicates_removed} duplicate rows based on {available_keys}")
    
    return df_deduped


def read_existing_campaign_data(
    campaign_dir: Path,
    columns: Sequence[str] | None = None
) -> pd.DataFrame | None:
    """Read existing GeoParquet data from campaign directory.
    
    Args:
        campaign_dir: Path to campaign directory containing parquet partitions
        columns: Specific columns to read (None = all columns)
    
    Returns:
        DataFrame with existing data, or None if no data exists
    """
    # Find all parquet files in campaign directory
    parquet_files = list(campaign_dir.rglob("*.parquet"))
    
    # Exclude STAC directory
    parquet_files = [f for f in parquet_files if 'stac' not in f.parts]
    
    if not parquet_files:
        return None
    
    # Read all parquet files
    try:
        if columns:
            # Only read specified columns for efficiency
            available_columns = set()
            # Check first file to see what columns are available
            schema = pq.read_schema(parquet_files[0])
            available_columns = set(schema.names)
            read_columns = [col for col in columns if col in available_columns]
            
            if not read_columns:
                return None
            
            dfs = [pd.read_parquet(f, columns=read_columns) for f in parquet_files]
        else:
            dfs = [pd.read_parquet(f) for f in parquet_files]
        
        if not dfs:
            return None
        
        # Concatenate all partitions
        existing_data = pd.concat(dfs, ignore_index=True)
        return existing_data
    
    except Exception as e:
        print(f"[geotrack] Warning: Could not read existing campaign data: {e}")
        return None


def merge_with_deduplication(
    new_data: pd.DataFrame,
    existing_data: pd.DataFrame,
    primary_keys: Sequence[str] | None = None
) -> pd.DataFrame:
    """Merge new data with existing data, removing duplicates.
    
    Args:
        new_data: New DataFrame to append
        existing_data: Existing DataFrame from campaign
        primary_keys: Column names to use as primary keys.
                     Defaults to PRIMARY_KEY_COLUMNS.
    
    Returns:
        Combined DataFrame with duplicates removed (existing data takes precedence)
    """
    if primary_keys is None:
        primary_keys = PRIMARY_KEY_COLUMNS
    
    # Combine datasets
    combined = pd.concat([existing_data, new_data], ignore_index=True)
    
    # Deduplicate (keeping first occurrence = existing data wins)
    return deduplicate_dataframe(combined, primary_keys)


def check_schema_compatibility(
    new_df: pd.DataFrame,
    existing_df: pd.DataFrame
) -> tuple[bool, list[str]]:
    """Check if new data is compatible with existing schema.
    
    Args:
        new_df: New DataFrame
        existing_df: Existing DataFrame
    
    Returns:
        Tuple of (is_compatible, list_of_issues)
    """
    issues = []
    
    # Check if critical columns exist in both
    critical_cols = ['time', 'latitude', 'longitude', 'geometry']
    for col in critical_cols:
        if col in existing_df.columns and col not in new_df.columns:
            issues.append(f"New data missing critical column: {col}")
        if col in new_df.columns and col not in existing_df.columns:
            issues.append(f"Existing data missing critical column: {col}")
    
    # Check for dtype mismatches on shared columns
    shared_cols = set(new_df.columns) & set(existing_df.columns)
    for col in shared_cols:
        new_dtype = new_df[col].dtype
        existing_dtype = existing_df[col].dtype
        
        # Allow some flexibility (e.g., int64 vs float64)
        if not _dtypes_compatible(new_dtype, existing_dtype):
            issues.append(
                f"Column '{col}' dtype mismatch: "
                f"existing={existing_dtype}, new={new_dtype}"
            )
    
    return len(issues) == 0, issues


def _dtypes_compatible(dtype1: pd.api.typing.Dtype, dtype2: pd.api.typing.Dtype) -> bool:
    """Check if two pandas dtypes are compatible."""
    # Exact match
    if dtype1 == dtype2:
        return True
    
    # Numeric types are generally compatible
    numeric_types = ['int8', 'int16', 'int32', 'int64', 
                     'uint8', 'uint16', 'uint32', 'uint64',
                     'float16', 'float32', 'float64']
    
    if str(dtype1) in numeric_types and str(dtype2) in numeric_types:
        return True
    
    # String types
    if pd.api.types.is_string_dtype(dtype1) and pd.api.types.is_string_dtype(dtype2):
        return True
    
    # Datetime types
    if pd.api.types.is_datetime64_any_dtype(dtype1) and pd.api.types.is_datetime64_any_dtype(dtype2):
        return True
    
    return False
