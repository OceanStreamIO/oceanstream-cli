import os
from typing import List, Dict, Tuple, Optional
from pathlib import Path

import pandas as pd


def is_geocsv(file_path: str | Path) -> bool:
    """
    Detect if a file is GeoCSV format by checking for metadata headers.
    
    GeoCSV files start with lines beginning with '#' containing metadata.
    
    Args:
        file_path: Path to file to check
        
    Returns:
        True if file appears to be GeoCSV format
    """
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            first_line = f.readline().strip()
            # GeoCSV files start with # metadata headers
            return first_line.startswith('#')
    except Exception:
        return False


def parse_geocsv_metadata(lines: List[str]) -> Dict[str, str]:
    """
    Parse GeoCSV metadata headers into a dictionary.
    
    GeoCSV metadata lines start with '#' followed by key: value pairs.
    
    Args:
        lines: List of lines from the file (including metadata headers)
        
    Returns:
        Dictionary of metadata key-value pairs
    """
    metadata = {}
    
    for line in lines:
        line = line.strip()
        if not line.startswith('#'):
            break  # End of metadata section
            
        # Remove leading '#' and whitespace
        content = line[1:].strip()
        
        # Parse key: value format
        if ':' in content:
            key, value = content.split(':', 1)
            metadata[key.strip()] = value.strip()
    
    return metadata


def read_geocsv(file_path: str | Path) -> Tuple[pd.DataFrame, Dict[str, str]]:
    """
    Read a GeoCSV file, parsing metadata headers and data.
    
    GeoCSV format specifications:
    - Metadata lines start with '#' and contain key: value pairs
    - Common metadata keys: dataset, cruise_id, field_unit, field_type, 
      field_standard_name, source_repository, source_event, source_dataset
    - Data follows after metadata headers
    
    Args:
        file_path: Path to GeoCSV file
        
    Returns:
        Tuple of (DataFrame with data, dict with metadata)
    """
    file_path = Path(file_path)
    
    # Read all lines to parse metadata
    with open(file_path, 'r', encoding='utf-8') as f:
        all_lines = f.readlines()
    
    # Parse metadata from header lines
    metadata = parse_geocsv_metadata(all_lines)
    
    # Find where data starts (first non-# line is column headers)
    data_start_line = 0
    for i, line in enumerate(all_lines):
        if not line.strip().startswith('#'):
            data_start_line = i
            break
    
    # Read CSV data (pandas will handle column headers automatically)
    df = pd.read_csv(
        file_path,
        skiprows=data_start_line,
        on_bad_lines='skip',
        low_memory=False
    )
    
    # Basic cleanup
    df = df.replace(to_replace=["nan", "NaN", "NULL", "None"], value=pd.NA)
    df = df.replace(r"^\s*$", pd.NA, regex=True)
    
    return df, metadata


def read_csv_files(raw_data_folder: str) -> pd.DataFrame:
    csv_files = [f for f in os.listdir(raw_data_folder) if f.endswith('.csv')]
    data_frames: List[pd.DataFrame] = []

    for csv_file in csv_files:
        file_path = os.path.join(raw_data_folder, csv_file)
        df = pd.read_csv(file_path, on_bad_lines='skip', low_memory=False)
        df = df.replace(to_replace=["nan", "NaN", "NULL", "None"], value=pd.NA)
        df = df.replace(r"^\s*$", pd.NA, regex=True)
        if 'latitude' not in df.columns or 'longitude' not in df.columns:
            continue
        df['latitude'] = pd.to_numeric(df['latitude'], errors='coerce')
        df['longitude'] = pd.to_numeric(df['longitude'], errors='coerce')
        df = df.dropna(subset=['latitude', 'longitude'])
        if df.empty: continue
        df['platform_id'] = extract_platform_id(csv_file)
        df = _sanitize_column_types(df)
        na_subset = [c for c in df.columns if c != 'platform_id']
        df = df.dropna(how='all', subset=na_subset)
        # Drop columns that are entirely NA in this chunk to avoid pandas FutureWarning
        # about concat with empty/all-NA entries changing dtype resolution in future.
        if not df.empty:
            df = df.dropna(axis=1, how='all')
        data_frames.append(df)

    if not data_frames:
        return pd.DataFrame(columns=['platform_id', 'latitude', 'longitude'])

    # Filter out any empty frames defensively before concatenation
    non_empty_frames = [d for d in data_frames if not d.empty]
    consolidated_data = pd.concat(non_empty_frames, ignore_index=True)
    na_subset = [c for c in consolidated_data.columns if c != 'platform_id']
    consolidated_data = consolidated_data.dropna(how='all', subset=na_subset)
    # Optionally drop columns that are all-NA across the concatenated result
    consolidated_data = consolidated_data.dropna(axis=1, how='all')
    return consolidated_data


def extract_platform_id(file_name: str) -> str | None:
    return file_name.split('_')[1] if '_' in file_name else None


def _sanitize_column_types(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()

    if 'time' in out.columns and not pd.api.types.is_datetime64_any_dtype(out['time']):
        try:
            out['time'] = pd.to_datetime(out['time'], errors='coerce')
        except Exception:
            pass

    if 'platform_id' in out.columns:
        out['platform_id'] = out['platform_id'].astype(str)

    for col in out.columns:
        if col in ('latitude', 'longitude', 'platform_id', 'time'):
            continue
        series = out[col]
        if pd.api.types.is_object_dtype(series):
            numeric = pd.to_numeric(series, errors='coerce')
            ratio = numeric.notna().mean() if len(series) else 0.0
            if ratio >= 0.6:
                out[col] = numeric
            else:
                try:
                    out[col] = series.astype("string")
                except Exception:
                    out[col] = series.astype("string[python]")
    return out
