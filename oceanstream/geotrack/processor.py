"""Geotrack processing module for converting CSV data to GeoParquet."""
from __future__ import annotations
import os
import sys
import tarfile
from pathlib import Path
from time import perf_counter
from typing import Any, Callable, TYPE_CHECKING
import pandas as pd

if TYPE_CHECKING:
    import pyarrow.fs as pafs

try:
    from tqdm.auto import tqdm
    HAS_TQDM = True
except ImportError:
    HAS_TQDM = False

from .csv_reader import _sanitize_column_types, extract_platform_id, is_geocsv, read_geocsv
from .geoparquet_writer import write_geoparquet
from .metadata import CampaignMetadata
from .deduplication import (
    deduplicate_dataframe,
    read_existing_campaign_data,
    merge_with_deduplication,
    check_schema_compatibility,
)
from .interpolation import enrich_sensor_data_from_campaign, has_spatial_coordinates
from ..stac import emit_stac_collection_and_item
from ..config.settings import Settings
from ..semantic.semantic import SemanticMapper, SemanticConfig, semantic_to_parquet_metadata
from .binning import suggest_lat_lon_bins_from_data
from ..providers.base import ProviderBase
from ..sensors import get_sensor_catalogue, Sensor
from ..sensors.saildrone import detect_saildrone_platform, get_platform_sensors
from ..sensors.processors.nmea_gnss import process_nmea_raw

# Optional CTD processing support
try:
    from ..sensors.processors.r2r_ctd import (
        find_cast_files as find_ctd_casts,
        process_ctd_cast,
        CTDInput,
        CTDCast,
    )
    HAS_CTD_SUPPORT = True
except ImportError:
    HAS_CTD_SUPPORT = False


def _format_file_size(size_bytes: int) -> str:
    """Format file size in human-readable format."""
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size_bytes < 1024.0:
            return f"{size_bytes:3.1f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.1f} PB"


def _is_tar_gz_archive(file_path: Path) -> bool:
    """Check if a file is a .tar.gz archive.
    
    Args:
        file_path: Path to file to check
        
    Returns:
        True if file is a .tar.gz archive
    """
    name_lower = file_path.name.lower()
    return name_lower.endswith('.tar.gz') or name_lower.endswith('.tgz')


def _extract_archive(
    archive_path: Path,
    work_dir: Path,
    verbose: bool = False,
) -> Path:
    """Extract a .tar.gz archive to a working directory.
    
    Args:
        archive_path: Path to .tar.gz archive
        work_dir: Working directory for extraction
        verbose: Whether to show progress messages
        
    Returns:
        Path to extracted directory containing the archive contents
    """
    if not archive_path.exists():
        raise FileNotFoundError(f"Archive not found: {archive_path}")
    
    if not _is_tar_gz_archive(archive_path):
        raise ValueError(f"Not a .tar.gz archive: {archive_path}")
    
    # Create extraction directory based on archive name (without .tar.gz)
    archive_stem = archive_path.name
    for suffix in ['.tar.gz', '.tgz']:
        if archive_stem.endswith(suffix):
            archive_stem = archive_stem[:-len(suffix)]
            break
    
    extract_dir = work_dir / archive_stem
    extract_dir.mkdir(parents=True, exist_ok=True)
    
    if verbose:
        print(f"[geotrack] Extracting archive: {archive_path.name}")
        print(f"[geotrack]   → {extract_dir}")
    
    # Extract archive
    try:
        with tarfile.open(archive_path, 'r:gz') as tar:
            tar.extractall(extract_dir)
    except Exception as e:
        raise RuntimeError(f"Failed to extract archive {archive_path.name}: {e}")
    
    if verbose:
        print(f"[geotrack]   ✓ Extracted successfully")
    
    return extract_dir


def _find_data_files_in_archive(extract_dir: Path) -> tuple[list[Path], list[Path]]:
    """Find processable data files within an extracted archive.
    
    Looks for:
    - CSV/GeoCSV files (.csv, .geocsv)
    - NMEA files (.txt)
    - SeaBird CTD hex files (.hex)
    - Files in a 'data' subdirectory (common R2R pattern)
    
    Args:
        extract_dir: Path to extracted archive directory
        
    Returns:
        Tuple of (data_files, ctd_hex_files) where:
        - data_files: List of CSV/GeoCSV/NMEA files
        - ctd_hex_files: List of SeaBird CTD hex files
    """
    data_files = []
    ctd_hex_files = []
    
    # Check for 'data' subdirectory (R2R pattern)
    data_subdir = None
    for item in extract_dir.rglob('*'):
        if item.is_dir() and item.name.lower() == 'data':
            data_subdir = item
            break
    
    # Search for data files
    search_dir = data_subdir if data_subdir else extract_dir
    
    for item in search_dir.rglob('*'):
        if item.is_file():
            suffix = item.suffix.lower()
            if suffix in ['.csv', '.geocsv', '.txt']:
                data_files.append(item)
            elif suffix == '.hex':
                ctd_hex_files.append(item)
    
    return data_files, ctd_hex_files


def _display_files_summary(input_source: Path, csv_files: list[Path]) -> bool:
    """
    Display a summary table of detected files and prompt for confirmation.
    
    Args:
        input_source: Path to CSV file or directory containing CSV files
        csv_files: List of Path objects for CSV files
        
    Returns:
        True if user confirms to proceed, False otherwise
    """
    print(f"\n[geotrack] Detected {len(csv_files)} file(s) in {input_source}:\n")
    
    # Collect file info
    file_info = []
    total_size = 0
    for file_path in csv_files:
        try:
            size_bytes = os.path.getsize(file_path)
            total_size += size_bytes
            file_info.append((file_path.name, size_bytes))
        except OSError:
            file_info.append((file_path.name, 0))
    
    # Calculate column widths
    max_filename_len = max(len(fname) for fname, _ in file_info)
    filename_width = max(max_filename_len, len("Filename"))
    
    # Print table header
    print(f"  {'Filename':<{filename_width}}  {'Size':>10}")
    print(f"  {'-' * filename_width}  {'-' * 10}")
    
    # Print each file
    for fname, size_bytes in file_info:
        size_str = _format_file_size(size_bytes)
        print(f"  {fname:<{filename_width}}  {size_str:>10}")
    
    # Print total
    print(f"  {'-' * filename_width}  {'-' * 10}")
    total_str = _format_file_size(total_size)
    print(f"  {'Total':<{filename_width}}  {total_str:>10}\n")
    
    # Prompt for confirmation
    try:
        response = input("Proceed with processing? [Y/n]: ").strip().lower()
        if response == '' or response == 'y' or response == 'yes':
            return True
        return False
    except (EOFError, KeyboardInterrupt):
        print("\n[geotrack] Cancelled by user.")
        return False


def _read_single_csv(file_path: Path, filename: str, provider: ProviderBase | None = None, allow_non_spatial: bool = False) -> tuple[pd.DataFrame | None, dict | None]:
    """
    Read and validate a single CSV or GeoCSV file.
    
    Args:
        file_path: Path to CSV file
        filename: Filename for platform ID extraction
        provider: Optional provider for platform identification
        allow_non_spatial: If True, allow files without spatial coordinates
                          (they will be enriched via interpolation later)
    
    Returns:
        Tuple of (DataFrame, metadata_dict) where metadata_dict is None for regular CSV
        and contains GeoCSV metadata for GeoCSV files.
    """
    metadata = None
    
    # Check if this is a GeoCSV file
    if is_geocsv(file_path):
        # Read GeoCSV with metadata
        df, metadata = read_geocsv(file_path)
    else:
        # Regular CSV reading
        df = pd.read_csv(file_path, on_bad_lines='skip', low_memory=False)
        df = df.replace(to_replace=["nan", "NaN", "NULL", "None"], value=pd.NA)
        df = df.replace(r"^\s*$", pd.NA, regex=True)
    
    # Validate required columns (check both standard and provider-specific names)
    has_coords = False
    if 'latitude' in df.columns and 'longitude' in df.columns:
        has_coords = True
    # R2R uses ship_latitude/ship_longitude before enrichment
    elif 'ship_latitude' in df.columns and 'ship_longitude' in df.columns:
        has_coords = True
    
    if not has_coords:
        if not allow_non_spatial:
            # Old behavior: skip files without spatial coordinates
            return None, None
        else:
            # New behavior: keep file for later interpolation
            print(f"[geotrack] File {filename} has no spatial coordinates, will attempt interpolation")
    
    # Only validate coordinates if they're already in standard format
    # R2R files will be handled by the provider's enrich_dataframe
    if 'latitude' in df.columns and 'longitude' in df.columns:
        df['latitude'] = pd.to_numeric(df['latitude'], errors='coerce')
        df['longitude'] = pd.to_numeric(df['longitude'], errors='coerce')
        df = df.dropna(subset=['latitude', 'longitude'])
        
        if df.empty:
            return None, None
    
    # Add platform_id
    # Try provider's identify_platform first, then fall back to extract_platform_id
    if provider:
        platform_id = provider.identify_platform(filename)
        if platform_id:
            df['platform_id'] = platform_id
        elif not metadata or 'cruise_id' not in metadata:
            # Fall back to filename parsing
            df['platform_id'] = extract_platform_id(filename)
    else:
        df['platform_id'] = extract_platform_id(filename)
    
    df = _sanitize_column_types(df)
    na_subset = [c for c in df.columns if c != 'platform_id']
    df = df.dropna(how='all', subset=na_subset)
    df = df.dropna(axis=1, how='all')
    
    return df, metadata


def _concat_data_frames(frames: list[pd.DataFrame]) -> pd.DataFrame:
    """Concatenate multiple DataFrames and clean."""
    non_empty = [d for d in frames if d is not None and not d.empty]
    if not non_empty:
        return pd.DataFrame(columns=['platform_id', 'latitude', 'longitude'])
    df = pd.concat(non_empty, ignore_index=True)
    na_subset = [c for c in df.columns if c != 'platform_id']
    df = df.dropna(how='all', subset=na_subset)
    df = df.dropna(axis=1, how='all')
    return df


def _is_nmea_file(file_path: Path) -> bool:
    """Check if a .txt file contains NMEA sentences.
    
    Args:
        file_path: Path to .txt file
        
    Returns:
        True if file contains NMEA sentences (lines with $ prefix)
    """
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            # Check first 10 lines for NMEA patterns
            for i, line in enumerate(f):
                if i >= 10:
                    break
                line = line.strip()
                if not line:
                    continue
                # NMEA sentences start with $ (with or without timestamp prefix)
                # Format: <ISO8601_timestamp> $GPGGA,... or just $GPGGA,...
                if '$' in line:
                    # Check if it looks like a NMEA sentence ($ followed by letters)
                    parts = line.split('$', 1)
                    if len(parts) == 2 and len(parts[1]) > 2 and parts[1][:2].isalpha():
                        return True
        return False
    except Exception:
        return False


def _convert_nmea_to_csv(
    nmea_path: Path,
    work_dir: Path,
    verbose: bool = False,
    sentence_types: list[str] | None = None,
    sampling_interval: float | None = None,
) -> Path:
    """Convert NMEA .txt file to CSV format.
    
    Args:
        nmea_path: Path to NMEA .txt file
        work_dir: Working directory for temporary CSV files
        verbose: Whether to show progress messages
        sentence_types: List of NMEA sentence types to process (e.g., ['GGA', 'RMC']).
                       If None, processes all supported types (GGA, RMC, GNS, VTG, ZDA).
        sampling_interval: Time interval in seconds for sampling/decimation.
                          If None, keeps all data points.
                          Example: 10.0 = 1 point per 10 seconds.
        
    Returns:
        Path to generated CSV file
    """
    # Create work directory if it doesn't exist
    work_dir.mkdir(parents=True, exist_ok=True)
    
    # Generate output CSV path (same name with .csv extension)
    csv_path = work_dir / f"{nmea_path.stem}.csv"
    
    if verbose:
        print(f"[geotrack] Converting NMEA file: {nmea_path.name} → {csv_path.name}")
        if sentence_types:
            print(f"[geotrack]   Sentence types: {', '.join(sentence_types)}")
        if sampling_interval:
            print(f"[geotrack]   Sampling interval: {sampling_interval}s")
    
    # Process NMEA file
    try:
        stats = process_nmea_raw(
            input_path=nmea_path,
            output_path=csv_path,
            sentence_types=sentence_types,
            sampling_interval=sampling_interval,
        )
        
        if verbose:
            print(f"[geotrack]   ✓ Converted {stats['lines_parsed']} NMEA sentences → {stats['data_points_written']} CSV rows")
        
        return csv_path
    except Exception as e:
        raise ValueError(f"Failed to convert NMEA file {nmea_path.name}: {e}")


def _is_ctd_hex_file(file_path: Path) -> bool:
    """Check if a file is a SeaBird CTD hex file.
    
    Args:
        file_path: Path to check
        
    Returns:
        True if file appears to be a CTD hex file
    """
    return file_path.suffix.lower() == '.hex'


def _is_ctd_archive(archive_path: Path) -> bool:
    """Check if an archive contains CTD data (by naming convention).
    
    R2R CTD archives typically have 'ctd' in the name.
    
    Args:
        archive_path: Path to archive
        
    Returns:
        True if archive appears to contain CTD data
    """
    name_lower = archive_path.name.lower()
    return '_ctd' in name_lower or name_lower.startswith('ctd')


def _find_ctd_files_in_directory(directory: Path) -> list[Path]:
    """Find CTD hex files in a directory.
    
    Args:
        directory: Directory to search
        
    Returns:
        List of .hex file paths
    """
    hex_files = []
    for item in directory.rglob('*.hex'):
        if item.is_file():
            hex_files.append(item)
    return hex_files


def _convert_ctd_to_csv(
    hex_path: Path,
    work_dir: Path,
    verbose: bool = False,
) -> Path | None:
    """Convert a single CTD hex file to CSV.
    
    Args:
        hex_path: Path to .hex file
        work_dir: Working directory for output
        verbose: Whether to show progress
        
    Returns:
        Path to generated CSV file, or None if conversion fails
    """
    if not HAS_CTD_SUPPORT:
        raise ImportError(
            "CTD processing requires seabirdscientific. "
            "Install with: pip install seabirdscientific"
        )
    
    work_dir.mkdir(parents=True, exist_ok=True)
    
    # Find associated files (.hdr, .xmlcon)
    casts = find_ctd_casts(hex_path.parent)
    
    # Find the cast matching this hex file
    hex_stem = hex_path.stem.lower()
    cast = None
    for c in casts:
        if c.hex_file.stem.lower() == hex_stem:
            cast = c
            break
    
    if cast is None:
        # Create minimal cast info
        parts = hex_path.stem.split('_')
        cruise_id = parts[0] if len(parts) > 1 else 'unknown'
        cast_id = parts[-1] if len(parts) > 1 else hex_path.stem
        cast = CTDCast(
            cast_id=cast_id.lower(),
            cruise_id=cruise_id,
            hex_file=hex_path,
        )
    
    if verbose:
        print(f"[geotrack] Converting CTD hex file: {hex_path.name}")
    
    try:
        df = process_ctd_cast(cast, output_dir=work_dir)
        if df is not None and len(df) > 0:
            csv_path = work_dir / f"{hex_path.stem}.csv"
            if verbose:
                print(f"[geotrack]   ✓ Converted {len(df)} CTD scans → {csv_path.name}")
            return csv_path
    except Exception as e:
        if verbose:
            print(f"[geotrack]   ✗ Failed to convert CTD: {e}")
    
    return None


def _convert_ctd_archive_to_csv(
    extract_dir: Path,
    work_dir: Path,
    verbose: bool = False,
) -> list[Path]:
    """Convert all CTD hex files from an extracted archive to CSV.
    
    Args:
        extract_dir: Directory containing extracted archive
        work_dir: Working directory for output CSV files
        verbose: Whether to show progress
        
    Returns:
        List of generated CSV file paths
    """
    if not HAS_CTD_SUPPORT:
        raise ImportError(
            "CTD processing requires seabirdscientific. "
            "Install with: pip install seabirdscientific"
        )
    
    work_dir.mkdir(parents=True, exist_ok=True)
    csv_files = []
    
    # Find CTD data directory (R2R BagIt structure has 'data' folder)
    data_dir = None
    for path in extract_dir.rglob('*'):
        if path.is_dir() and path.name == 'data':
            data_dir = path
            break
    
    if data_dir is None:
        data_dir = extract_dir
    
    # Find all CTD casts
    casts = find_ctd_casts(data_dir)
    
    if not casts:
        if verbose:
            print(f"[geotrack]   No CTD casts found in {extract_dir.name}")
        return []
    
    if verbose:
        print(f"[geotrack]   Found {len(casts)} CTD casts, converting...")
    
    for cast in casts:
        try:
            df = process_ctd_cast(cast, output_dir=work_dir)
            if df is not None and len(df) > 0:
                csv_path = work_dir / f"{cast.cruise_id}_{cast.cast_id}.csv"
                csv_files.append(csv_path)
        except Exception as e:
            if verbose:
                print(f"[geotrack]   ✗ Failed to convert cast {cast.cast_id}: {e}")
    
    if verbose:
        print(f"[geotrack]   ✓ Converted {len(csv_files)}/{len(casts)} CTD casts to CSV")
    
    return csv_files


class GeotrackProcessor:
    """Processor for geotrack data."""
    
    def __init__(
        self, 
        provider: ProviderBase, 
        verbose: bool = False, 
        campaign_id: str | None = None, 
        platform_id: str | None = None,
        attribution: str | None = None,
        creation_date: str | None = None,
        source_dataset: str | None = None,
        source_repository: str | None = None,
        nmea_sentence_types: list[str] | None = None,
        nmea_sampling_interval: float | None = None,
        filesystem: "pafs.FileSystem | None" = None,
    ):
        self.provider = provider
        self.verbose = verbose
        self._start_time = perf_counter()
        self._file_metadata = {}  # Store metadata per file for later use
        self._campaign_id = campaign_id  # User-supplied campaign ID
        self._platform_id = platform_id  # User-supplied platform ID
        self._attribution = attribution  # User-supplied attribution
        self._creation_date = creation_date  # User-supplied creation date
        self._source_dataset = source_dataset  # User-supplied source dataset DOI
        self._source_repository = source_repository  # User-supplied source repository DOI
        self._nmea_sentence_types = nmea_sentence_types  # NMEA sentence types to process
        self._nmea_sampling_interval = nmea_sampling_interval  # NMEA sampling interval in seconds
        self._filesystem = filesystem  # PyArrow filesystem for cloud storage
    
    def log(self, message: str) -> None:
        """Log a message if verbose is enabled."""
        if self.verbose:
            print(f"[geotrack] {message}")
    
    def step(self, label: str) -> float:
        """Start a timed step."""
        if self.verbose:
            print(f"[geotrack] • {label} ...")
        return perf_counter()
    
    def done(self, label: str, t0: float) -> None:
        """Complete a timed step."""
        if self.verbose:
            print(f"[geotrack]   ✓ {label} ({perf_counter() - t0:0.2f}s)")
    
    def scan_input_source(self, input_source: Path) -> list[Path]:
        """Scan input source (file or directory) for processable files.
        
        Supports:
        - CSV files (.csv, .geocsv)
        - NMEA raw data files (.txt with NMEA sentences)
        - R2R archives (.tar.gz) - automatically extracted
        
        Archives are extracted to .oceanstream_work/archives/ and then scanned
        for data files. NMEA files are automatically converted to CSV before processing.
        
        Args:
            input_source: Path to a data file, archive, or directory containing data files/archives
            
        Returns:
            List of Path objects for CSV files to process (includes converted NMEA files)
        """
        t0 = self.step(f"scanning input source {input_source}")
        
        if not input_source.exists():
            raise FileNotFoundError(f"Input source not found: {input_source}")
        
        csv_files = []
        nmea_files = []
        archives = []
        ctd_hex_files = []
        
        if input_source.is_file():
            # Single file input (case-insensitive extension check)
            if _is_tar_gz_archive(input_source):
                archives = [input_source]
            else:
                suffix = input_source.suffix.lower()
                if suffix in [".csv", ".geocsv"]:
                    csv_files = [input_source]
                elif suffix == ".txt":
                    # Check if it's a NMEA file
                    if _is_nmea_file(input_source):
                        nmea_files = [input_source]
                    else:
                        raise ValueError(
                            f"File {input_source.name} is a .txt file but does not contain NMEA sentences. "
                            "Supported formats: .csv, .geocsv, .txt (NMEA), .hex (CTD), or .tar.gz (archive)."
                        )
                elif suffix == ".hex":
                    # SeaBird CTD hex file
                    if not HAS_CTD_SUPPORT:
                        raise ValueError(
                            f"CTD hex file detected but seabirdscientific not installed. "
                            "Install with: pip install seabirdscientific"
                        )
                    ctd_hex_files = [input_source]
                else:
                    raise ValueError(
                        f"Unsupported file type: {suffix}. "
                        "Supported formats: .csv, .geocsv, .txt (NMEA), .hex (CTD), or .tar.gz (archive)."
                    )
        elif input_source.is_dir():
            # Directory input - scan for CSV, NMEA, and archive files (case-insensitive)
            for f in os.listdir(input_source):
                f_lower = f.lower()
                file_path = input_source / f
                
                if not file_path.is_file():
                    continue
                
                if _is_tar_gz_archive(file_path):
                    archives.append(file_path)
                elif f_lower.endswith(".csv") or f_lower.endswith(".geocsv"):
                    csv_files.append(file_path)
                elif f_lower.endswith(".txt"):
                    # Check if it's a NMEA file
                    if _is_nmea_file(file_path):
                        nmea_files.append(file_path)
                elif f_lower.endswith(".hex"):
                    # CTD hex files - will be processed if seabirdscientific is available
                    pass  # Handled separately below
        else:
            raise ValueError(f"Input source must be a file or directory: {input_source}")
        
        # Check for CTD hex files in directory (append to list initialized above)
        if input_source.is_dir():
            for f in os.listdir(input_source):
                if f.lower().endswith('.hex'):
                    if HAS_CTD_SUPPORT:
                        ctd_hex_files.append(input_source / f)
        
        # Extract archives and find data files
        if archives:
            self.log(f"found {len(archives)} archive(s), extracting...")
            if self.verbose:  # pragma: no cover
                print("[geotrack]")
                print(f"[geotrack] Archive Summary:")
                for arch in archives:
                    try:
                        size = arch.stat().st_size
                        size_str = _format_file_size(size)
                        print(f"[geotrack]   • {arch.name} ({size_str})")
                    except OSError:
                        print(f"[geotrack]   • {arch.name}")
                print("[geotrack]")
            
            work_dir = Path.cwd() / ".oceanstream_work" / "archives"
            
            for idx, archive_path in enumerate(archives, 1):
                try:
                    if len(archives) > 1 and self.verbose:
                        print(f"[geotrack] Extracting archive {idx}/{len(archives)}: {archive_path.name}")
                    
                    extract_dir = _extract_archive(archive_path, work_dir, verbose=self.verbose)
                    
                    # Find data files in extracted archive (returns tuple now)
                    archive_data_files, archive_ctd_files = _find_data_files_in_archive(extract_dir)
                    
                    # Check if this is a CTD archive (has hex files)
                    if archive_ctd_files and not archive_data_files:
                        # Pure CTD archive - convert hex files to CSV
                        if HAS_CTD_SUPPORT:
                            if self.verbose:
                                print(f"[geotrack]   CTD archive detected with {len(archive_ctd_files)} hex files")
                            ctd_work_dir = Path.cwd() / ".oceanstream_work" / "ctd_conversions"
                            converted_csvs = _convert_ctd_archive_to_csv(
                                extract_dir, ctd_work_dir, verbose=self.verbose
                            )
                            csv_files.extend(converted_csvs)
                            continue
                        else:
                            self.log(f"  ⚠ CTD archive but seabirdscientific not installed")
                            continue
                    
                    if not archive_data_files and not archive_ctd_files:
                        self.log(f"  ⚠ No data files found in {archive_path.name}")
                        continue
                    
                    # Count file types
                    csv_count = 0
                    nmea_count = 0
                    
                    # Categorize files from archive
                    for data_file in archive_data_files:
                        suffix = data_file.suffix.lower()
                        if suffix in ['.csv', '.geocsv']:
                            csv_files.append(data_file)
                            csv_count += 1
                        elif suffix == '.txt' and _is_nmea_file(data_file):
                            nmea_files.append(data_file)
                            nmea_count += 1
                    
                    # Also collect CTD hex files from mixed archives
                    ctd_hex_files.extend(archive_ctd_files)
                    
                    if self.verbose:
                        file_summary = []
                        if csv_count > 0:
                            file_summary.append(f"{csv_count} CSV/GeoCSV")
                        if nmea_count > 0:
                            file_summary.append(f"{nmea_count} NMEA")
                        if archive_ctd_files:
                            file_summary.append(f"{len(archive_ctd_files)} CTD hex")
                        total_files = csv_count + nmea_count + len(archive_ctd_files)
                        print(f"[geotrack]   ✓ Found {total_files} file(s): {', '.join(file_summary)}")
                
                except Exception as e:
                    self.log(f"  ✗ Failed to process archive {archive_path.name}: {e}")
                    # Continue with other archives rather than failing completely
                    continue
        
        # Convert CTD hex files to CSV
        if ctd_hex_files:
            if not HAS_CTD_SUPPORT:
                self.log(f"found {len(ctd_hex_files)} CTD hex file(s) but seabirdscientific not installed, skipping")
            else:
                self.log(f"found {len(ctd_hex_files)} CTD hex file(s), converting to CSV...")
                ctd_work_dir = Path.cwd() / ".oceanstream_work" / "ctd_conversions"
                
                for hex_path in ctd_hex_files:
                    csv_path = _convert_ctd_to_csv(hex_path, ctd_work_dir, verbose=self.verbose)
                    if csv_path:
                        csv_files.append(csv_path)
        
        # Convert NMEA files to CSV
        if nmea_files:
            self.log(f"found {len(nmea_files)} NMEA file(s), converting to CSV...")
            work_dir = Path.cwd() / ".oceanstream_work" / "nmea_conversions"
            
            for nmea_path in nmea_files:
                csv_path = _convert_nmea_to_csv(
                    nmea_path, 
                    work_dir, 
                    verbose=self.verbose,
                    sentence_types=self._nmea_sentence_types,
                    sampling_interval=self._nmea_sampling_interval,
                )
                csv_files.append(csv_path)
        
        if not csv_files:
            if archives:
                raise ValueError(
                    f"No processable files found in {len(archives)} archive(s). "
                    "Archives should contain .csv, .geocsv, .txt (NMEA), or .hex (CTD) files."
                )
            else:
                raise ValueError(
                    f"No processable files found in {input_source}. "
                    "Looking for: .csv, .geocsv, .txt (NMEA), .hex (CTD), or .tar.gz (archives)."
                )
        
        extract_msg = f" ({len(archives)} archive(s) extracted)" if archives else ""
        nmea_msg = f" ({len(nmea_files)} converted from NMEA)" if nmea_files else ""
        ctd_msg = f" ({len(ctd_hex_files)} converted from CTD)" if ctd_hex_files else ""
        self.done(f"found {len(csv_files)} file(s) to process{extract_msg}{nmea_msg}{ctd_msg}", t0)
        return csv_files
    
    def process_files(self, csv_files: list[Path]) -> pd.DataFrame:
        """Process CSV files with optional progress bars.
        
        Args:
            csv_files: List of Path objects for CSV files to process
            
        Returns:
            Concatenated DataFrame with all data
        """
        data_frames = []
        
        # Show processing header if verbose
        if self.verbose and len(csv_files) > 1:
            print(f"[geotrack] Processing {len(csv_files)} file(s)...")
        
        # Use tqdm for progress bar if available (works in Jupyter and terminal)
        # Show progress bar if we have tqdm and more than 1 file, regardless of verbose mode
        if HAS_TQDM and len(csv_files) > 1:
            iterator = tqdm(
                csv_files,
                desc="Processing files",
                unit="file",
                dynamic_ncols=True,
                leave=True,  # Keep the progress bar visible after completion
                disable=False  # Always show if tqdm is available
            )
        else:
            iterator = csv_files
        
        for idx, file_path in enumerate(iterator, 1):
            if self.verbose and not HAS_TQDM:
                print(f"[geotrack]   [{idx}/{len(csv_files)}] Processing {file_path.name}...")
            self._process_single_file(file_path, data_frames)
        
        if not data_frames:
            raise ValueError("No usable data after per-file processing.")
        
        df = _concat_data_frames(data_frames)
        if self.verbose:
            print(f"\n[geotrack] ✓ Consolidated {len(df):,} rows from {len(data_frames)} file(s)")
        return df
    
    def _process_single_file(self, file_path: Path, data_frames: list[pd.DataFrame]) -> None:
        """Process a single CSV file.
        
        Args:
            file_path: Path object for the CSV file
            data_frames: List to append processed DataFrame to
        """
        fname = file_path.name
        try:
            # Allow non-spatial files for now, we'll try to interpolate later
            df_file, file_metadata = _read_single_csv(file_path, fname, self.provider, allow_non_spatial=True)
        except Exception as e:
            if self.verbose:
                print(f"[geotrack]   ! Skipping {fname} (read error: {e})")
            return
        
        if df_file is None or df_file.empty:
            if self.verbose:
                print(f"[geotrack]   · Skipping {fname} (no usable rows)")
            return
        
        # Store metadata for later use (e.g., in parquet_metadata)
        if file_metadata:
            self._file_metadata[fname] = file_metadata
        
        # Enrichment - pass metadata to provider
        df_enriched = self.provider.enrich_dataframe(df_file, metadata=file_metadata)
        if df_enriched.empty:
            if self.verbose:
                print(f"[geotrack]   · Skipping {fname} after enrichment (no data)")
            return
        
        # Override platform_id if user supplied one
        if self._platform_id:
            df_enriched['platform_id'] = self._platform_id
        
        # Add campaign_id column
        # Priority: 1) user-supplied, 2) from file metadata (R2R cruise_id), 3) platform_id
        if self._campaign_id:
            df_enriched['campaign_id'] = self._campaign_id
        elif file_metadata and 'cruise_id' in file_metadata:
            df_enriched['campaign_id'] = file_metadata['cruise_id']
        elif 'platform_id' in df_enriched.columns:
            # Fallback: use platform_id as campaign_id if not specified
            df_enriched['campaign_id'] = df_enriched['platform_id']
        
        data_frames.append(df_enriched)
        if self.verbose:
            print(f"[geotrack]   ✓ {fname} rows={len(df_enriched)}")
    
    def apply_semantic_mapping(self, df: pd.DataFrame) -> dict[str, Any] | None:
        """Apply semantic metadata mapping if enabled."""
        if not Settings.SEMANTIC_ENABLE:
            return None
        
        sem_cfg = SemanticConfig(
            enabled=True,
            cf_table_path=Settings.SEMANTIC_CF_TABLE or None,
            alias_table_path=Settings.SEMANTIC_ALIAS_TABLE or None,
            min_confidence=Settings.SEMANTIC_MIN_CONFIDENCE,
            rename_columns=False,
        )
        mapper = SemanticMapper(sem_cfg)
        sem_result = mapper.apply(df)
        return semantic_to_parquet_metadata(sem_result)
    
    def enrich_non_spatial_data(self, df: pd.DataFrame, campaign_dir: Path, interpolation_method: str = "linear", max_time_gap: float = 60.0) -> pd.DataFrame:
        """Enrich non-spatial sensor data with coordinates via interpolation.
        
        This method attempts to add lat/lon coordinates to data that doesn't have them
        by interpolating from existing campaign data. If interpolation fails, empty
        coordinates are added as a fallback.
        
        Args:
            df: DataFrame that may contain data without spatial coordinates
            campaign_dir: Path to campaign directory with existing GeoParquet data
            interpolation_method: Method to use for interpolation (nearest, linear, ffill, bfill)
            max_time_gap: Maximum time gap in seconds for valid interpolation
            
        Returns:
            DataFrame with spatial coordinates enriched
        """
        # Check if data already has spatial coordinates
        if has_spatial_coordinates(df):
            return df
        
        if self.verbose:
            print(f"[geotrack] Attempting spatial-temporal interpolation for non-spatial data...")
        
        # Try interpolation from existing campaign data
        try:
            enriched_df, success = enrich_sensor_data_from_campaign(
                df, 
                campaign_dir,
                time_column='time',
                method=interpolation_method,
                max_time_gap_seconds=max_time_gap
            )
            
            if success:
                if self.verbose:  # pragma: no cover
                    # Count how many rows got interpolated coordinates
                    non_null_coords = enriched_df[['latitude', 'longitude']].notna().all(axis=1).sum()
                    print(f"[geotrack]   ✓ Successfully interpolated coordinates for {non_null_coords}/{len(enriched_df)} rows")
                return enriched_df
            else:
                if self.verbose:  # pragma: no cover
                    print(f"[geotrack]   ⚠️  Interpolation failed or no reference data available, adding empty coordinates")
                # Fall through to add empty coordinates
        except Exception as e:
            if self.verbose:  # pragma: no cover
                print(f"[geotrack]   ⚠️  Interpolation error: {e}, adding empty coordinates")
            # Fall through to add empty coordinates
        
        # Fallback: add empty lat/lon columns
        if 'latitude' not in df.columns:
            df['latitude'] = pd.NA
        if 'longitude' not in df.columns:
            df['longitude'] = pd.NA
        
        return df
    
    def detect_sensors_and_platform(self, df: pd.DataFrame) -> tuple[list[Sensor], dict[str, Any]]:
        """Detect sensors and platform info from DataFrame (backward compatible).
        
        For multi-platform support, use detect_sensors_and_platforms() instead.
        This method returns only the FIRST platform for backward compatibility.
        
        Args:
            df: Consolidated DataFrame with all data
            
        Returns:
            Tuple of (detected_sensors, platform_metadata) - single platform dict
        """
        detected_sensors, all_platforms = self.detect_sensors_and_platforms(df)
        # Return first platform for backward compatibility
        platform_metadata = all_platforms[0] if all_platforms else {}
        return detected_sensors, platform_metadata
    
    def detect_sensors_and_platforms(self, df: pd.DataFrame) -> tuple[list[Sensor], list[dict[str, Any]]]:
        """Detect sensors and ALL platforms from DataFrame.
        
        Args:
            df: Consolidated DataFrame with all data
            
        Returns:
            Tuple of (detected_sensors, platforms_list) where platforms_list
            contains metadata for ALL platforms in the dataset
        """
        # Detect sensors from available columns
        available_vars = set(df.columns)
        catalogue = get_sensor_catalogue()
        detected_sensors = catalogue.detect_sensors(available_vars)
        
        # Extract ALL platforms from the data
        platforms: list[dict[str, Any]] = []
        
        # Get unique trajectory/platform IDs
        if 'trajectory' in df.columns:
            trajectory_values = df['trajectory'].dropna().unique()
            for trajectory_id in sorted(trajectory_values):
                trajectory_id = int(trajectory_id)
                platform_type = detect_saildrone_platform(trajectory_id)
                
                platform_metadata: dict[str, Any] = {
                    'id': f'sd{trajectory_id}',
                    'trajectory': trajectory_id,
                    'type': f'Saildrone {platform_type}',
                    'model': platform_type,
                }
                
                # Add specifications based on platform type
                if platform_type == "Explorer":
                    platform_metadata['specifications'] = {
                        'length': '7m',
                        'draft': '2.5m',
                        'displacement': '~750 kg',
                        'wing_height': '5m',
                        'speed_range': '0-6 knots',
                        'endurance': '12+ months',
                        'power': 'solar + wind generator',
                        'communication': 'Iridium satellite'
                    }
                elif platform_type == "Surveyor":
                    platform_metadata['specifications'] = {
                        'length': '10m or 12m',
                        'draft': '4m',
                        'displacement': '~2500 kg',
                        'wing_height': '5m',
                        'speed_range': '0-8 knots',
                        'endurance': '12+ months',
                        'power': 'solar + wind generator',
                        'communication': 'Iridium satellite + high-bandwidth'
                    }
                
                # Add platform_id from column if available (match by trajectory)
                if 'platform_id' in df.columns:
                    platform_rows = df[df['trajectory'] == trajectory_id]
                    if len(platform_rows) > 0:
                        platform_id_val = str(platform_rows['platform_id'].iloc[0])
                        platform_metadata['platform_id'] = platform_id_val
                
                # Add campaign_id from column if available
                if 'campaign_id' in df.columns:
                    campaign_id_value = df['campaign_id'].iloc[0]
                    if pd.notna(campaign_id_value):
                        platform_metadata['campaign_id'] = str(campaign_id_value)
                
                # Count rows for this platform
                platform_rows = df[df['trajectory'] == trajectory_id]
                platform_metadata['row_count'] = len(platform_rows)
                
                platforms.append(platform_metadata)
        
        # Fallback: if no trajectory column, use platform_id column
        elif 'platform_id' in df.columns:
            unique_platform_ids = df['platform_id'].dropna().unique()
            for platform_id_val in sorted(unique_platform_ids):
                platform_id_val = str(platform_id_val)
                platform_metadata = {
                    'platform_id': platform_id_val,
                }
                
                # For R2R data, try to get actual vessel name from cruise ID
                if self.provider.name == 'r2r':
                    from oceanstream.providers.r2r.r2r import R2RProvider
                    if isinstance(self.provider, R2RProvider):
                        vessel_name = self.provider.get_platform_from_cruise_id(platform_id_val)
                        if vessel_name:
                            platform_metadata['name'] = vessel_name
                            platform_metadata['type'] = vessel_name
                
                # Add campaign_id from column if available
                if 'campaign_id' in df.columns:
                    campaign_id_value = df['campaign_id'].iloc[0]
                    if pd.notna(campaign_id_value):
                        platform_metadata['campaign_id'] = str(campaign_id_value)
                
                # Count rows for this platform
                platform_rows = df[df['platform_id'] == platform_id_val]
                platform_metadata['row_count'] = len(platform_rows)
                
                platforms.append(platform_metadata)
        
        # Add citation and provenance metadata to ALL platforms
        file_metadata = next(iter(self._file_metadata.values())) if self._file_metadata else None
        
        provenance: dict[str, Any] = {}
        if self._attribution:
            provenance['attribution'] = self._attribution
        elif file_metadata and 'attribution' in file_metadata:
            provenance['attribution'] = file_metadata['attribution']
        
        if self._creation_date:
            provenance['creation_date'] = self._creation_date
        elif file_metadata and 'creation_date' in file_metadata:
            provenance['creation_date'] = file_metadata['creation_date']
        
        if self._source_dataset:
            provenance['source_dataset'] = self._source_dataset
        elif file_metadata and 'source_dataset' in file_metadata:
            provenance['source_dataset'] = file_metadata['source_dataset']
        
        if self._source_repository:
            provenance['source_repository'] = self._source_repository
        elif file_metadata and 'source_repository' in file_metadata:
            provenance['source_repository'] = file_metadata['source_repository']
        
        # Add provenance to each platform
        for platform in platforms:
            platform.update(provenance)
        
        # Log findings
        if self.verbose:
            print(f"[geotrack]   Detected {len(detected_sensors)} sensors")
            for sensor in detected_sensors[:3]:  # Show first 3
                print(f"[geotrack]     • {sensor.name}")
            if len(detected_sensors) > 3:
                print(f"[geotrack]     • ... and {len(detected_sensors) - 3} more")
            if platforms:
                print(f"[geotrack]   Platforms detected: {len(platforms)}")
                for p in platforms[:3]:  # Show first 3
                    print(f"[geotrack]     • {p.get('id', p.get('platform_id', 'Unknown'))} ({p.get('row_count', '?')} rows)")
                if len(platforms) > 3:
                    print(f"[geotrack]     • ... and {len(platforms) - 3} more")
        
        return detected_sensors, platforms
    
    def write_geoparquet_dataset(
        self,
        df: pd.DataFrame,
        output_dir: Path | str,
        semantic_meta: dict[str, Any] | None = None,
    ) -> None:
        """Write the GeoParquet dataset.
        
        Args:
            df: DataFrame to write.
            output_dir: Output directory path (local or cloud path).
            semantic_meta: Optional semantic metadata.
        """
        # Derive bins
        t0 = self.step("deriving latitude/longitude bins")
        lat_bins, lon_bins = suggest_lat_lon_bins_from_data(df)
        self.done(f"{len(lat_bins)-1} lat bins, {len(lon_bins)-1} lon bins", t0)
        
        # Prepare metadata
        t0 = self.step("preparing metadata (aliases, units, provider)")
        aliases = self.provider.alias_mapping(df.columns)
        
        # For R2R and other providers that need metadata from files
        # Pass the first file's metadata if available
        file_metadata = next(iter(self._file_metadata.values())) if self._file_metadata else None
        units = self.provider.units_mapping(df.columns, metadata=file_metadata)
        if units and not any(v for v in units.values() if v):
            units = None
        prov_meta = self.provider.parquet_metadata(df, metadata=file_metadata)
        self.done("metadata prepared", t0)
        
        # Write dataset
        storage_desc = str(output_dir)
        if self._filesystem is not None:
            storage_desc = f"{type(self._filesystem).__name__}:{output_dir}"
        t0 = self.step(f"writing GeoParquet dataset to {storage_desc}")
        write_geoparquet(
            df,
            output_dir,
            lat_bins,
            lon_bins,
            units_metadata=units or None,
            alias_mapping=aliases or None,
            provider_metadata=prov_meta or None,
            semantic_metadata=semantic_meta or None,
            filesystem=self._filesystem,
        )
        self.done("dataset write complete", t0)
    
    def emit_stac_metadata(
        self,
        output_dir: Path,
        df: pd.DataFrame,
        semantic_meta: dict[str, Any] | None,
        detected_sensors: list[Sensor] | None = None,
        platform_metadata: dict[str, Any] | None = None,
        platforms: list[dict[str, Any]] | None = None,
        pmtiles_path: Path | None = None,
        measurement_columns: list[str] | None = None,
    ) -> None:
        """Emit STAC Collection and Items.
        
        Args:
            output_dir: Output directory for STAC files
            df: DataFrame with the data
            semantic_meta: Semantic metadata
            detected_sensors: List of detected sensors
            platform_metadata: Platform metadata (deprecated, use platforms)
            platforms: List of platform metadata dicts for multi-platform campaigns
            pmtiles_path: Optional path to PMTiles file
            measurement_columns: Optional list of measurement columns for statistics
        """
        if not (Settings.SEMANTIC_ENABLE and Settings.SEMANTIC_GENERATE_STAC):
            return
        
        t1 = self.step("emitting STAC Collection + Item")
        try:
            from ..stac.emit import calculate_measurement_statistics
            
            # Calculate measurement statistics
            measurement_stats = None
            if measurement_columns:
                measurement_stats = calculate_measurement_statistics(df, measurement_columns)
            
            # Get software version from package
            try:
                from importlib.metadata import version
                software_version = version("oceanstream")
            except Exception:
                software_version = "0.1.0"
            
            emit_stac_collection_and_item(
                output_dir,
                df,
                semantic_meta,
                provider_name=self.provider.name,
                instruments=detected_sensors,
                platform=platform_metadata,
                platforms=platforms,
                pmtiles_path=pmtiles_path,
                measurement_stats=measurement_stats,
                software_version=software_version,
            )
            self.done("STAC JSON emitted", t1)
        except Exception as e:  # pragma: no cover
            if self.verbose:
                print(f"[geotrack]   ! STAC emission failed: {e}")
    
    def generate_pmtiles_dataset(
        self,
        geoparquet_root: Path,
        minzoom: int = 0,
        maxzoom: int = 10,
        layer_name: str = "track",
        sample_rate: int = 5,
        time_gap_minutes: int = 60,
        platform_id: str | None = None,
        include_measurements: bool = True,
        measurement_columns: list[str] | None = None,
        exclude_patterns: list[str] | None = None,
    ) -> Path | None:
        """Generate PMTiles from GeoParquet dataset with segments and day markers.
        
        Args:
            geoparquet_root: Root directory of partitioned GeoParquet dataset
            minzoom: Minimum zoom level (0-15)
            maxzoom: Maximum zoom level (0-15)
            layer_name: Layer name for vector tiles
            sample_rate: Take every Nth point (1=all, 5=every 5th)
            time_gap_minutes: Minutes of gap to split track segments
            platform_id: Platform/cruise identifier
            include_measurements: Include oceanographic measurements
            measurement_columns: Specific columns to include (None = auto-discover)
            exclude_patterns: Regex patterns to exclude when auto-discovering (None = use defaults)
            
        Returns:
            Path to generated PMTiles file, or None if generation failed
        """
        from .tiling import generate_pmtiles_from_geoparquet, MissingDependencyError
        
        t0 = self.step("generating PMTiles with segments and day markers")
        
        try:
            # PMTiles file goes in tiles/ subdirectory parallel to geoparquet output
            tiles_dir = geoparquet_root.parent / "tiles"
            tiles_dir.mkdir(parents=True, exist_ok=True)
            pmtiles_path = tiles_dir / "track.pmtiles"
            
            generate_pmtiles_from_geoparquet(
                geoparquet_root=geoparquet_root,
                pmtiles_path=pmtiles_path,
                minzoom=minzoom,
                maxzoom=maxzoom,
                layer_name=layer_name,
                sample_rate=sample_rate,
                time_gap_minutes=time_gap_minutes,
                platform_id=platform_id,
                use_tippecanoe=True,  # Use tippecanoe for segments
                include_measurements=include_measurements,
                measurement_columns=measurement_columns,
                exclude_patterns=exclude_patterns,
            )
            
            self.done(f"PMTiles generated: {pmtiles_path.name}", t0)
            return pmtiles_path
            
        except MissingDependencyError as e:
            if self.verbose:
                print(f"[geotrack]   ! PMTiles generation failed: {e}")
                print(f"[geotrack]   ! Install required tools: tippecanoe and pmtiles CLI")
            return None
        except Exception as e:  # pragma: no cover
            if self.verbose:
                print(f"[geotrack]   ! PMTiles generation failed: {e}")
            return None
    
    def elapsed_time(self) -> float:
        """Get elapsed time since processor initialization."""
        return perf_counter() - self._start_time


def generate_tiles(
    geoparquet_dir: Path,
    output_dir: Path | None = None,
    provider: ProviderBase | None = None,
    verbose: bool = False,
    minzoom: int = 0,
    maxzoom: int = 10,
    layer_name: str = "track",
    sample_rate: int = 5,
    time_gap_minutes: int = 60,
    include_measurements: bool = True,
    measurement_columns: list[str] | None = None,
) -> Path | None:
    """
    Generate PMTiles from an existing GeoParquet dataset.
    
    Args:
        geoparquet_dir: Path to GeoParquet dataset root
        output_dir: Optional output directory for tiles (default: geoparquet_dir/../tiles)
        provider: Optional provider for column standardization
        verbose: Enable detailed progress information
        minzoom: Minimum zoom level (0-15)
        maxzoom: Maximum zoom level (0-15)
        layer_name: Layer name for vector tiles
        sample_rate: Take every Nth point (1=all, 5=every 5th)
        time_gap_minutes: Minutes of gap to split track segments
        include_measurements: Include oceanographic measurements in tiles
        measurement_columns: Specific columns to include (None = auto-select important ones)
        
    Returns:
        Path to generated PMTiles file, or None if generation failed
    """
    if not geoparquet_dir.exists():
        raise FileNotFoundError(f"GeoParquet directory not found: {geoparquet_dir}")
    
    # Initialize processor (minimal, no file processing)
    if provider is None:
        from ..providers import get_provider
        provider = get_provider("saildrone")  # Default provider
    
    processor = GeotrackProcessor(provider, verbose=verbose)
    
    # Determine output location
    if output_dir is None:
        output_dir = geoparquet_dir.parent / "tiles"
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Extract platform_id from GeoParquet if possible
    platform_id = None
    try:
        metadata_path = geoparquet_dir / "metadata.parquet"
        if metadata_path.exists():
            meta_df = pd.read_parquet(metadata_path)
            if len(meta_df) > 0:
                # Try to read first partition to get platform_id
                partition_path = Path(str(meta_df.iloc[0]['partition_path']))
                if not partition_path.is_absolute():
                    partition_path = geoparquet_dir / partition_path
                
                if partition_path.exists():
                    sample_df = pd.read_parquet(partition_path, columns=['platform_id'] if 'platform_id' in pd.read_parquet(partition_path, nrows=0).columns else None)
                    if 'platform_id' in sample_df.columns and len(sample_df) > 0:
                        platform_id = str(sample_df['platform_id'].iloc[0])
    except Exception as e:
        if verbose:
            print(f"[tiles] Note: Could not extract platform_id: {e}")
    
    # Generate PMTiles
    pmtiles_path = output_dir / "track.pmtiles"
    
    if verbose:  # pragma: no cover
        print(f"\n[tiles] Generating PMTiles from GeoParquet")
        print(f"[tiles] • Source: {geoparquet_dir}")
        print(f"[tiles] • Output: {pmtiles_path}")
        print(f"[tiles] • Zoom levels: {minzoom}-{maxzoom}")
        print(f"[tiles] • Sample rate: every {sample_rate} point(s)")
        print(f"[tiles] • Time gap: {time_gap_minutes} minutes")
        if include_measurements:
            print(f"[tiles] • Measurements: {'auto-selected' if measurement_columns is None else f'{len(measurement_columns)} columns'}")
    
    result = processor.generate_pmtiles_dataset(
        geoparquet_root=geoparquet_dir,
        minzoom=minzoom,
        maxzoom=maxzoom,
        layer_name=layer_name,
        sample_rate=sample_rate,
        time_gap_minutes=time_gap_minutes,
        platform_id=platform_id,
        include_measurements=include_measurements,
        measurement_columns=measurement_columns,
    )
    
    if result and result.exists():
        size_bytes = os.path.getsize(result)
        if verbose:  # pragma: no cover
            print(f"\n[tiles] ✓ PMTiles generated successfully")
            print(f"[tiles] • File: {result.name}")
            print(f"[tiles] • Size: {_format_file_size(size_bytes)}")
        return result
    else:
        if verbose:  # pragma: no cover
            print(f"\n[tiles] ✗ PMTiles generation failed")
        return None


def _group_files_by_campaign_id(
    csv_files: list[Path],
    provider: ProviderBase,
    user_campaign_id: str | None = None,
    verbose: bool = False,
) -> dict[str, list[Path]]:
    """
    Group CSV files by their detected campaign_id.
    
    This is used when processing a directory containing files from multiple campaigns.
    Each campaign will be processed separately to create distinct output directories.
    
    Args:
        csv_files: List of CSV file paths to group
        provider: Data provider instance (for campaign_id detection)
        user_campaign_id: User-supplied campaign_id override (if provided, all files use this)
        verbose: Enable detailed progress information
        
    Returns:
        Dictionary mapping campaign_id -> list of file paths
        
    Example:
        {
            "sd1030_tpos_2023": [Path("sd1030_tpos_2023_*.csv")],
            "sd1033_tpos_2023": [Path("sd1033_tpos_2023_*.csv")],
            "sd1079_tpos_2023": [Path("sd1079_tpos_2023_*.csv")]
        }
    """
    if user_campaign_id:
        # User provided campaign_id - all files belong to same campaign
        return {user_campaign_id: csv_files}
    
    from .csv_reader import extract_platform_id
    
    campaign_groups: dict[str, list[Path]] = {}
    
    for csv_file in csv_files:
        # Try provider-specific identification first
        detected_id = provider.identify_platform(csv_file.name)
        
        # Fallback to generic extraction
        if not detected_id:
            detected_id = extract_platform_id(csv_file.name)
        
        # If still no campaign_id, use filename stem as fallback
        if not detected_id:
            detected_id = csv_file.stem
            if verbose:
                print(f"[geotrack] Warning: Could not detect campaign_id for {csv_file.name}, using filename stem: {detected_id}")
        
        # Add to group
        if detected_id not in campaign_groups:
            campaign_groups[detected_id] = []
        campaign_groups[detected_id].append(csv_file)
    
    return campaign_groups


def convert(
    provider: ProviderBase,
    input_source: Path,
    output_dir: Path | str,
    verbose: bool = False,
    list_columns: bool = False,
    print_schema: bool = False,
    provider_metadata: bool = False,
    dry_run: bool = False,
    upload: bool = False,
    yes: bool = False,
    generate_pmtiles: bool = False,
    pmtiles_minzoom: int = 0,
    pmtiles_maxzoom: int = 10,
    pmtiles_layer: str = "track",
    pmtiles_sample_rate: int = 5,
    pmtiles_time_gap: int = 60,
    pmtiles_include_measurements: bool = True,
    pmtiles_measurement_columns: list[str] | None = None,
    pmtiles_exclude_patterns: list[str] | None = None,
    campaign_id: str | None = None,
    platform_id: str | None = None,
    attribution: str | None = None,
    creation_date: str | None = None,
    source_dataset: str | None = None,
    source_repository: str | None = None,
    force_reprocess: bool = False,
    nmea_sentence_types: list[str] | None = None,
    nmea_sampling_interval: float | None = None,
    use_cloud_storage: bool = False,
) -> None:
    """
    Convert geotrack CSV data into GeoParquet format, and optionally PMTiles.
    
    Args:
        provider: Data provider instance
        input_source: Path to a CSV file or directory containing CSV files
        output_dir: Output directory for GeoParquet dataset. Can be:
                   - Local path (e.g., "./out/geoparquet")
                   - Cloud URI (e.g., "az://container/path", "s3://bucket/path")
        verbose: Enable detailed progress information
        list_columns: List available columns and exit
        print_schema: Print GeoParquet schema and exit
        provider_metadata: Print provider metadata and exit
        dry_run: Analyze inputs without writing files
        upload: (Deprecated) Use use_cloud_storage instead
        yes: Skip confirmation prompts
        generate_pmtiles: Generate PMTiles vector tiles with segments and day markers
        pmtiles_minzoom: Minimum zoom level for PMTiles (0-15)
        pmtiles_maxzoom: Maximum zoom level for PMTiles (0-15)
        pmtiles_layer: Layer name for PMTiles
        pmtiles_sample_rate: Sample rate - take every Nth point (1=all, 5=every 5th)
        pmtiles_time_gap: Minutes of gap to split track segments
        pmtiles_include_measurements: Include oceanographic measurements in tiles
        pmtiles_measurement_columns: Specific columns to include (None = auto-discover)
        pmtiles_exclude_patterns: Regex patterns to exclude when auto-discovering (None = use defaults)
        campaign_id: Campaign/cruise identifier (overrides provider detection)
        platform_id: Platform identifier (overrides provider detection)
        attribution: Data attribution/citation (overrides provider/file metadata)
        creation_date: Data creation date (overrides provider/file metadata)
        source_dataset: Source dataset DOI (overrides provider/file metadata)
        source_repository: Source repository DOI (overrides provider/file metadata)
        force_reprocess: Force reprocess all files, clearing previous metadata (default: False)
        nmea_sentence_types: List of NMEA sentence types to process (e.g., ['GGA', 'RMC']).
                            If None, processes all supported types (GGA, RMC, GNS, VTG, ZDA).
                            Only used for .txt NMEA files.
        nmea_sampling_interval: Time interval in seconds for NMEA data sampling/decimation.
                               If None, keeps all data points. Example: 10.0 = 1 point per 10 seconds.
                               Only used for .txt NMEA files.
        use_cloud_storage: If True, use active cloud storage from configuration (if configured).
                          Cloud URIs in output_dir always use cloud storage regardless of this flag.
    """
    # Resolve output path (local or cloud)
    from ..storage.filesystem import resolve_output_path, StoragePath
    
    storage_path: StoragePath | None = None
    filesystem = None
    resolved_output_dir = output_dir
    
    # Determine if we should use cloud storage
    output_str = str(output_dir)
    is_cloud_uri = output_str.startswith(("az://", "abfs://", "s3://", "gs://"))
    
    if is_cloud_uri or use_cloud_storage or upload:
        try:
            storage_path = resolve_output_path(
                output_dir,
                use_active_storage=(use_cloud_storage or upload) and not is_cloud_uri,
            )
            filesystem = storage_path.filesystem
            resolved_output_dir = storage_path.path
            
            if verbose and storage_path.is_cloud:
                print(f"[geotrack] Using cloud storage: {storage_path.provider}")
                print(f"[geotrack] Cloud path: {resolved_output_dir}")
        except Exception as e:
            if is_cloud_uri:
                # Cloud URI was explicitly specified, so this is an error
                raise ValueError(f"Failed to resolve cloud storage: {e}")
            # Otherwise, fall back to local storage
            if verbose:
                print(f"[geotrack] Cloud storage not configured, using local: {e}")
    
    processor = GeotrackProcessor(
        provider,
        verbose=verbose, 
        campaign_id=campaign_id, 
        platform_id=platform_id,
        attribution=attribution,
        creation_date=creation_date,
        source_dataset=source_dataset,
        source_repository=source_repository,
        nmea_sentence_types=nmea_sentence_types,
        nmea_sampling_interval=nmea_sampling_interval,
        filesystem=filesystem,
    )
    
    # Step 1: Scan input source (file or directory)
    if verbose:
        print(f"\n[geotrack] {'='*70}")
        print(f"[geotrack] OceanStream Geotrack Processing Pipeline")
        print(f"[geotrack] {'='*70}")
        print(f"[geotrack] Input source  : {input_source}")
        if storage_path and storage_path.is_cloud:
            print(f"[geotrack] Output        : {storage_path.provider}://{resolved_output_dir}")
        else:
            print(f"[geotrack] Output dir    : {output_dir}")
        print(f"[geotrack] Provider      : {provider.name}")
        if campaign_id:
            print(f"[geotrack] Campaign ID   : {campaign_id} (user-supplied)")
        print(f"[geotrack] {'='*70}\n")
    
    csv_files = processor.scan_input_source(input_source)
    if not csv_files:
        print("[geotrack] No CSV files to process.")
        return
    
    # Display comprehensive file summary
    if verbose and csv_files:
        print("\n[geotrack] Files to Process:")
        print(f"[geotrack] {'='*70}")
        total_size = 0
        for idx, file_path in enumerate(csv_files, 1):
            try:
                size = file_path.stat().st_size
                total_size += size
                size_str = _format_file_size(size)
                print(f"[geotrack]   {idx:2d}. {file_path.name:<50} {size_str:>10}")
            except OSError:
                print(f"[geotrack]   {idx:2d}. {file_path.name}")
        print(f"[geotrack] {'-'*70}")
        print(f"[geotrack]   Total: {len(csv_files)} file(s), {_format_file_size(total_size)}")
        
        # Show performance estimate for large datasets
        if total_size > 100 * 1024 * 1024:  # > 100 MB
            estimated_seconds = (total_size / (1024 * 1024)) * 0.05  # ~50ms per MB (rough estimate)
            estimated_minutes = estimated_seconds / 60
            if estimated_minutes < 1:
                time_str = f"{estimated_seconds:.0f} seconds"
            else:
                time_str = f"{estimated_minutes:.1f} minutes"
            print(f"[geotrack]   Estimated processing time: ~{time_str}")
            if total_size > 500 * 1024 * 1024:  # > 500 MB
                print(f"[geotrack]   💡 Tip: Large dataset detected - processing may take a while")
        
        print(f"[geotrack] {'='*70}\n")
    
    # Step 1.5: Group files by campaign_id (if multiple campaigns detected)
    from rich.console import Console
    console = Console()
    
    with console.status("[bold blue]Analyzing campaigns in input files...", spinner="dots"):
        campaign_groups = _group_files_by_campaign_id(csv_files, provider, campaign_id, verbose)
    
    # Check if multiple campaigns detected
    if len(campaign_groups) > 1:
        if verbose:
            print(f"\n[geotrack] Detected {len(campaign_groups)} campaigns in input directory:")
            for cid, files in campaign_groups.items():
                print(f"  - {cid}: {len(files)} file(s)")
            print("[geotrack] Processing each campaign separately...\n")
        
        # Process each campaign separately by calling convert() for each file in the group
        for campaign_idx, (detected_campaign_id, campaign_files) in enumerate(campaign_groups.items(), 1):
            if verbose:
                print(f"\n{'='*70}")
                print(f"[geotrack] Campaign {campaign_idx}/{len(campaign_groups)}: {detected_campaign_id}")
                print(f"  Files: {', '.join(f.name for f in campaign_files)}")
                print(f"{'='*70}\n")
            
            # Process each file in this campaign group
            # Files will be automatically merged via the deduplication logic
            for file_idx, campaign_file in enumerate(campaign_files, 1):
                if verbose and len(campaign_files) > 1:
                    print(f"\n[geotrack] Processing file {file_idx}/{len(campaign_files)} for campaign {detected_campaign_id}")
                
                # Recursively call convert() for this specific file with campaign_id locked
                convert(
                    provider=provider,
                    input_source=campaign_file,  # Process single file
                    output_dir=output_dir,
                    verbose=verbose,
                    list_columns=list_columns,
                    print_schema=print_schema,
                    provider_metadata=provider_metadata,
                    dry_run=dry_run,
                    upload=upload,
                    yes=True,  # Skip confirmation prompts in recursive calls
                    generate_pmtiles=generate_pmtiles,
                    pmtiles_minzoom=pmtiles_minzoom,
                    pmtiles_maxzoom=pmtiles_maxzoom,
                    pmtiles_layer=pmtiles_layer,
                    pmtiles_sample_rate=pmtiles_sample_rate,
                    pmtiles_time_gap=pmtiles_time_gap,
                    pmtiles_include_measurements=pmtiles_include_measurements,
                    pmtiles_measurement_columns=pmtiles_measurement_columns,
                    pmtiles_exclude_patterns=pmtiles_exclude_patterns,
                    campaign_id=detected_campaign_id,  # Lock to this specific campaign
                    platform_id=platform_id,
                    attribution=attribution,
                    creation_date=creation_date,
                    source_dataset=source_dataset,
                    source_repository=source_repository,
                    force_reprocess=force_reprocess,
                    nmea_sentence_types=nmea_sentence_types,
                    nmea_sampling_interval=nmea_sampling_interval,
                    use_cloud_storage=use_cloud_storage,
                )
        
        # All campaigns processed
        if verbose:
            print(f"\n{'='*70}")
            print(f"[geotrack] ✓ All {len(campaign_groups)} campaigns processed successfully")
            for cid, files in campaign_groups.items():
                print(f"  - {cid}: {len(files)} file(s) → {output_dir / cid}/")
            print(f"{'='*70}\n")
        return
    
    # Single campaign - continue with normal flow
    if verbose and len(campaign_groups) == 1:
        detected_id = list(campaign_groups.keys())[0]
        print(f"[geotrack] Single campaign detected: {detected_id}")
    
    # Step 1.6: Display file summary and get confirmation (unless in dry-run or inspection mode)
    if not (dry_run or list_columns or print_schema or provider_metadata or yes):
        if not _display_files_summary(input_source, csv_files):
            print("[geotrack] Processing cancelled.")
            return
    
    # Step 2-3: Process files
    df = processor.process_files(csv_files)
    
    # Step 3.5: Detect sensors and ALL platforms (multi-platform support)
    detected_sensors, detected_platforms = processor.detect_sensors_and_platforms(df)
    # For backward compatibility, also get single platform_metadata
    platform_metadata = detected_platforms[0] if detected_platforms else {}
    
    # Step 3.6: Validate campaign_id is present
    # campaign_id is now REQUIRED - check if it's in the DataFrame
    if 'campaign_id' not in df.columns or df['campaign_id'].isna().all():
        print("\n[geotrack] ERROR: campaign_id is required but could not be detected.")
        print("[geotrack] Please provide --campaign-id parameter or ensure files contain cruise_id metadata.")
        return
    
    # Extract the campaign_id value (should be consistent across all rows)
    detected_campaign_id = df['campaign_id'].iloc[0]
    
    # Step 3.7: Handle deduplication and metadata tracking
    # For cloud storage: resolved_output_dir is the cloud path, but we still need a local
    # directory for metadata tracking and STAC generation (which can then be uploaded)
    is_cloud = storage_path is not None and storage_path.is_cloud
    
    if is_cloud:
        # Cloud storage: combine cloud base path with campaign_id
        # BUT: check if user already included campaign_id in the path to avoid duplication
        resolved_path_parts = resolved_output_dir.rstrip('/').split('/')
        if resolved_path_parts[-1] == detected_campaign_id:
            # User already specified campaign_id in path (e.g., az://container/campaign_id)
            cloud_campaign_path = resolved_output_dir.rstrip('/')
        else:
            # Append campaign_id to the base path
            cloud_campaign_path = f"{resolved_output_dir.rstrip('/')}/{detected_campaign_id}"
        
        # For cloud storage, use a local staging directory for metadata/STAC generation
        # These will be uploaded to cloud after generation
        # Use current working directory as the local staging area
        local_output_dir = Path.cwd() / ".oceanstream_staging"
        campaign_output_dir = local_output_dir / detected_campaign_id
        campaign_output_dir.mkdir(parents=True, exist_ok=True)
    else:
        # Local storage: standard path handling
        # Also check if user already included campaign_id in the path
        resolved_path = Path(resolved_output_dir)
        if resolved_path.name == detected_campaign_id:
            # User already specified campaign_id in path
            local_output_dir = resolved_path.parent
            campaign_output_dir = resolved_path
        else:
            local_output_dir = resolved_path
            campaign_output_dir = local_output_dir / detected_campaign_id
        cloud_campaign_path = None
    
    # Step 3.8: Enrich non-spatial data with interpolation
    # If data lacks spatial coordinates, try to interpolate from existing campaign data
    df = processor.enrich_non_spatial_data(df, campaign_output_dir)
    
    # Get metadata directory from settings
    settings = Settings()
    metadata_dir = settings.METADATA_DIR
    campaign_metadata = CampaignMetadata(detected_campaign_id, metadata_dir)
    
    # Validate and possibly clear invalid campaign metadata (BEFORE checking file tracking)
    if not dry_run and not force_reprocess:
        from rich.console import Console
        from .campaign import load_campaign_metadata, get_campaigns_dir
        from .validation import validate_campaign_output, clear_invalid_campaign_metadata
        
        console = Console()
        
        # Check if campaign already exists
        with console.status(f"[bold blue]Checking campaign metadata for '{detected_campaign_id}'...", spinner="dots"):
            existing_metadata = load_campaign_metadata(detected_campaign_id)
        
        if existing_metadata:
            # Check if output directory has changed
            existing_output_dir = Path(existing_metadata.get('output_directory', ''))
            output_dir_changed = existing_output_dir != campaign_output_dir.resolve()
            
            if output_dir_changed:
                # Output directory has changed - check if old output still exists and is valid
                with console.status(f"[bold blue]Validating previous output location...", spinner="dots"):
                    old_output_validation = validate_campaign_output(existing_metadata)
                
                if old_output_validation['valid']:
                    # Old output is still valid - warn user about conflicting locations
                    print(f"\n[geotrack] ⚠️  WARNING: Campaign '{detected_campaign_id}' already exists with different output location:")
                    print(f"  Registered output: {existing_output_dir}")
                    print(f"  Current output:    {campaign_output_dir.resolve()}")
                    print(f"\n[geotrack] The registered output location still contains valid data.")
                    print(f"[geotrack] Use --force-reprocess to clear previous metadata and use the new location.")
                    return
                else:
                    # Old output is invalid/deleted - auto-clear metadata and use new location
                    if verbose:
                        print(f"[geotrack] Output directory has changed and previous output is invalid:")
                        print(f"  Previous: {existing_output_dir}")
                        print(f"  Current:  {campaign_output_dir.resolve()}")
                        print(f"[geotrack] Clearing invalid metadata to use new output location")
                    
                    # Clear both registries
                    campaigns_dir = get_campaigns_dir()
                    clear_invalid_campaign_metadata(detected_campaign_id, campaigns_dir, verbose=verbose)
                    campaign_metadata.clear()
            else:
                # Output directory unchanged, validate that it still exists and has data
                with console.status(f"[bold blue]Validating campaign output...", spinner="dots"):
                    validation = validate_campaign_output(existing_metadata)
                
                if not validation['valid']:
                    if verbose:
                        print(f"[geotrack] Campaign '{detected_campaign_id}' metadata exists but output is invalid:")
                        for issue in validation['issues']:
                            print(f"  - {issue}")
                        print(f"[geotrack] Clearing invalid campaign metadata to allow reprocessing")
                    
                    # Clear invalid metadata (both campaign registry and processing metadata)
                    campaigns_dir = get_campaigns_dir()
                    clear_invalid_campaign_metadata(detected_campaign_id, campaigns_dir, verbose=verbose)
                    campaign_metadata.clear()
    
    # Force reprocess: clear all previous metadata (both registries)
    if force_reprocess:
        if verbose:
            print("[geotrack] Force reprocess enabled - clearing all previous metadata")
        
        # Clear processing metadata
        campaign_metadata.clear()
        
        # Clear campaign registry if it exists
        if not dry_run:
            from .campaign import load_campaign_metadata, get_campaigns_dir
            existing_metadata = load_campaign_metadata(detected_campaign_id)
            if existing_metadata:
                campaigns_dir = get_campaigns_dir()
                campaign_dir = campaigns_dir / detected_campaign_id
                if campaign_dir.exists():
                    import shutil
                    shutil.rmtree(campaign_dir)
                    if verbose:
                        print(f"[geotrack] Cleared campaign registry: {campaign_dir}")
    
    # Check for previously processed files (unless force_reprocess or dry_run)
    if not (force_reprocess or dry_run):
        already_processed = []
        for csv_file in csv_files:
            if campaign_metadata.is_file_processed(csv_file):
                file_info = campaign_metadata.get_file_info(csv_file)
                already_processed.append((csv_file.name, file_info))
        
        if already_processed:
            print("\n[geotrack] ⚠️  WARNING: The following files have already been processed:")
            for filename, info in already_processed:
                processed_at = info.get('processed_at', 'unknown')
                rows = info.get('rows', 'unknown')
                print(f"  - {filename} (processed: {processed_at}, rows: {rows})")
            
            print("\n[geotrack] Processing these files again may create duplicates!")
            print("[geotrack] Duplicates will be automatically removed during merge.")
            print("[geotrack] Use --force-reprocess to clear metadata and reprocess everything from scratch.")
            return
    
    # Check for existing data in campaign directory for deduplication
    existing_data = None
    needs_full_rewrite = False
    if campaign_output_dir.exists() and not force_reprocess:
        if verbose:
            print("[geotrack] Checking for existing campaign data...")
        existing_data = read_existing_campaign_data(campaign_output_dir)
        
        if existing_data is not None and not existing_data.empty:
            if verbose:
                print(f"[geotrack] Found {len(existing_data)} existing rows in campaign")
            
            # Check schema compatibility
            compatible, issues = check_schema_compatibility(df, existing_data)
            if not compatible:
                print("\n[geotrack] ⚠️  Schema compatibility issues detected:")
                for issue in issues:
                    print(f"  - {issue}")
                print("\n[geotrack] Proceeding with merge, but results may be unpredictable.")
                print("[geotrack] Consider using --force-reprocess to start fresh.")
            
            # Merge with deduplication
            if verbose:
                print("[geotrack] Merging new data with existing data (deduplicating)...")
            df_before_dedup = len(df)
            df = merge_with_deduplication(df, existing_data)
            df_after_dedup = len(df)
            
            if df_after_dedup < df_before_dedup + len(existing_data):
                if verbose:
                    print(f"[geotrack] Removed duplicates: {df_before_dedup + len(existing_data) - df_after_dedup} rows")
            
            # Flag that we need to rewrite the entire dataset
            needs_full_rewrite = True
    
    # If deduplication occurred, we need to delete old data and write fresh
    if needs_full_rewrite and campaign_output_dir.exists():
        import shutil
        # Remove old parquet files but keep stac and metadata
        for partition_dir in campaign_output_dir.iterdir():
            if partition_dir.is_dir() and partition_dir.name.startswith(('lat_', 'lon_')):
                shutil.rmtree(partition_dir)
        if verbose:
            print("[geotrack] Removed old parquet partitions for clean rewrite")
    
    # Handle introspection flags    # Handle introspection flags
    if list_columns:  # pragma: no cover
        print(f"[geotrack] Columns ({len(df.columns)}):")
        for c in df.columns:
            print(f"  - {c}")
        return
    
    if print_schema:  # pragma: no cover
        dtype_map = {col: str(dt) for col, dt in df.dtypes.items()}
        print("[geotrack] GeoParquet schema preview:")
        for col, dt in dtype_map.items():
            print(f"  - {col}: {dt}")
        print("  (partition columns to be added: lat_bin, lon_bin)")
        return
    
    if provider_metadata:  # pragma: no cover
        meta = provider.parquet_metadata(df)
        print("[geotrack] Provider metadata snapshot:")
        for k, v in meta.items():
            print(f"  {k}: {v}")
        return
    
    # Apply semantic mapping
    semantic_meta = processor.apply_semantic_mapping(df)
    
    # Dry-run summary
    if dry_run:  # pragma: no cover
        lat_bins, lon_bins = suggest_lat_lon_bins_from_data(df)
        lat_min, lat_max = float(df['latitude'].min()), float(df['latitude'].max())
        lon_min, lon_max = float(df['longitude'].min()), float(df['longitude'].max())
        print("\n[geotrack] Dry Run Summary")
        print("--------------------------------")
        print(f"Source input          : {input_source}")
        print(f"CSV files processed   : {len(csv_files)}")
        print(f"Rows total            : {len(df)}")
        print(f"Latitude range        : [{lat_min:.4f}, {lat_max:.4f}]")
        print(f"Longitude range       : [{lon_min:.4f}, {lon_max:.4f}]")
        print(f"Latitude bins (count) : {len(lat_bins)-1}")
        print(f"Longitude bins (count): {len(lon_bins)-1}")
        print(f"Provider              : {provider.name}")
        sample_cols = list(df.columns)[:12]
        more_flag = " (… more)" if len(df.columns) > len(sample_cols) else ""
        print(f"Columns sample ({len(df.columns)} total): {sample_cols}{more_flag}")
        print(f"Estimated output root : {output_dir} (not written)\n")
        print(f"Total elapsed         : {processor.elapsed_time():0.2f}s")
        return
    
    # Calculate statistics before writing
    lat_bins, lon_bins = suggest_lat_lon_bins_from_data(df)
    lat_min, lat_max = float(df['latitude'].min()), float(df['latitude'].max())
    lon_min, lon_max = float(df['longitude'].min()), float(df['longitude'].max())
    
    # Write GeoParquet - use cloud path if available, otherwise local
    geoparquet_output_path = cloud_campaign_path if is_cloud else campaign_output_dir
    processor.write_geoparquet_dataset(df, geoparquet_output_path, semantic_meta)
    
    # Update metadata tracking (track processed files)
    if not dry_run:
        for csv_file in csv_files:
            # Calculate rows contributed by this file (approximate)
            rows_per_file = len(df) // len(csv_files)
            campaign_metadata.mark_file_processed(csv_file, rows_per_file)
        
        campaign_metadata.increment_run_count()
        campaign_metadata.save()
        
        if verbose:
            print(f"[geotrack] Metadata updated: run #{campaign_metadata.get_run_count()}, "
                  f"{campaign_metadata.get_processed_file_count()} unique files tracked")
    
    # Generate PMTiles if requested (before STAC so we can include the path)
    pmtiles_generated = False
    pmtiles_path = None
    pmtiles_size = 0
    
    if generate_pmtiles:
        # Extract platform_id from first file if available
        platform_id = None
        if df is not None and 'platform_id' in df.columns and len(df) > 0:
            platform_id = str(df['platform_id'].iloc[0])
        
        # For cloud storage, we need local parquet files for PMTiles generation
        pmtiles_geoparquet_root = campaign_output_dir
        if is_cloud:
            # Write parquet locally first for PMTiles to read
            if verbose:
                print("[geotrack] • writing local parquet for PMTiles generation ...")
            from .geoparquet_writer import write_geoparquet
            pmtiles_geoparquet_root = campaign_output_dir
            pmtiles_geoparquet_root.mkdir(parents=True, exist_ok=True)
            write_geoparquet(df, pmtiles_geoparquet_root, lat_bins, lon_bins)
        
        pmtiles_path = processor.generate_pmtiles_dataset(
            geoparquet_root=pmtiles_geoparquet_root,
            minzoom=pmtiles_minzoom,
            maxzoom=pmtiles_maxzoom,
            layer_name=pmtiles_layer,
            sample_rate=pmtiles_sample_rate,
            time_gap_minutes=pmtiles_time_gap,
            platform_id=platform_id,
            include_measurements=pmtiles_include_measurements,
            measurement_columns=pmtiles_measurement_columns,
            exclude_patterns=pmtiles_exclude_patterns,
        )
        if pmtiles_path and pmtiles_path.exists():
            pmtiles_generated = True
            try:
                pmtiles_size = os.path.getsize(pmtiles_path)
            except OSError:
                pass
            
            # Upload PMTiles to cloud if using cloud storage
            if is_cloud and filesystem is not None:
                try:
                    cloud_pmtiles_path = f"{cloud_campaign_path}/tiles/{pmtiles_path.name}"
                    with pmtiles_path.open('rb') as f:
                        with filesystem.open_output_stream(cloud_pmtiles_path) as out:
                            out.write(f.read())
                    if verbose:
                        print(f"[geotrack]   ✓ PMTiles uploaded to cloud: {cloud_pmtiles_path}")
                except Exception as e:
                    if verbose:
                        print(f"[geotrack]   ! Failed to upload PMTiles to cloud: {e}")
    
    # Check if STAC was generated
    stac_generated = False
    stac_collection_path = None
    stac_items_count = 0
    
    if Settings.SEMANTIC_ENABLE and Settings.SEMANTIC_GENERATE_STAC:
        # Emit STAC metadata with PMTiles path and measurement columns
        processor.emit_stac_metadata(
            campaign_output_dir, 
            df, 
            semantic_meta, 
            detected_sensors, 
            platform_metadata,
            platforms=detected_platforms,
            pmtiles_path=pmtiles_path,
            measurement_columns=pmtiles_measurement_columns if pmtiles_include_measurements else None,
        )
        
        # Check if files were actually created (STAC files are in stac/ subdirectory)
        stac_dir = campaign_output_dir / "stac"
        stac_collection_path = stac_dir / "collection.json"
        stac_items_dir = stac_dir / "items"
        
        if stac_collection_path.exists():
            stac_generated = True
            # Count item JSON files
            if stac_items_dir.exists():
                stac_items_count = len(list(stac_items_dir.glob("*.json")))
            
            # Upload STAC files to cloud if using cloud storage
            if is_cloud and filesystem is not None:
                try:
                    from pyarrow import fs as pafs
                    # Upload collection.json
                    cloud_stac_dir = f"{cloud_campaign_path}/stac"
                    with stac_collection_path.open('rb') as f:
                        with filesystem.open_output_stream(f"{cloud_stac_dir}/collection.json") as out:
                            out.write(f.read())
                    # Upload items
                    if stac_items_dir.exists():
                        for item_file in stac_items_dir.glob("*.json"):
                            with item_file.open('rb') as f:
                                with filesystem.open_output_stream(f"{cloud_stac_dir}/items/{item_file.name}") as out:
                                    out.write(f.read())
                    if verbose:
                        print(f"[geotrack]   ✓ STAC files uploaded to cloud")
                except Exception as e:
                    if verbose:
                        print(f"[geotrack]   ! Failed to upload STAC to cloud: {e}")
    
    # Register campaign metadata
    if not dry_run:
        from .campaign import create_campaign
        
        # Prepare campaign metadata
        campaign_meta = {
            "campaign_id": detected_campaign_id,
            "output_directory": str(campaign_output_dir.resolve()),
        }
        
        # Add cloud storage information if applicable
        if is_cloud and cloud_campaign_path:
            campaign_meta["cloud_storage"] = {
                "provider": storage_path.provider,
                "path": cloud_campaign_path,
            }
        
        # Add platform information if available
        # For backward compatibility, keep single platform_id/name/type
        if platform_metadata:
            if 'id' in platform_metadata:
                campaign_meta['platform_id'] = platform_metadata['id']
            if 'name' in platform_metadata:
                campaign_meta['platform_name'] = platform_metadata['name']
            if 'type' in platform_metadata:
                campaign_meta['platform_type'] = platform_metadata['type']
        
        # Add multi-platform support: store ALL platforms in platforms array
        if detected_platforms:
            campaign_meta['platforms'] = [
                {
                    'id': p.get('id'),
                    'platform_id': p.get('platform_id'),
                    'name': p.get('name'),
                    'type': p.get('type'),
                    'model': p.get('model'),
                    'row_count': p.get('row_count'),
                }
                for p in detected_platforms
            ]
        
        # Add temporal bounds if available in DataFrame
        if 'time' in df.columns and not df['time'].isna().all():
            try:
                campaign_meta['start_date'] = df['time'].min().isoformat()
                campaign_meta['end_date'] = df['time'].max().isoformat()
            except Exception:
                pass
        
        # Add spatial bounds
        if 'latitude' in df.columns and 'longitude' in df.columns:
            try:
                campaign_meta['spatial_extent'] = {
                    'bbox': [
                        float(df['longitude'].min()),
                        float(df['latitude'].min()),
                        float(df['longitude'].max()),
                        float(df['latitude'].max())
                    ]
                }
            except Exception:
                pass
        
        # Add data statistics
        campaign_meta['total_rows'] = len(df)
        campaign_meta['total_files'] = len(csv_files)
        campaign_meta['partition_count'] = partition_count if 'partition_count' in locals() else 0
        
        # Add sensor information
        if detected_sensors:
            campaign_meta['sensors'] = [
                {
                    'name': s.name,
                    'manufacturer': s.manufacturer,
                    'sensor_type': s.sensor_type.value if hasattr(s.sensor_type, 'value') else str(s.sensor_type)
                }
                for s in detected_sensors[:10]  # Limit to first 10
            ]
        
        # Add provenance information if available
        if attribution:
            campaign_meta['attribution'] = attribution
        if source_dataset:
            campaign_meta['source_dataset'] = source_dataset
        if source_repository:
            campaign_meta['source_repository'] = source_repository
        
        # Register the campaign
        try:
            create_campaign(detected_campaign_id, campaign_meta, verbose=verbose)
            if verbose:
                print(f"[geotrack] Campaign registered: {detected_campaign_id}")
        except Exception as e:
            if verbose:
                print(f"[geotrack] Warning: Could not register campaign: {e}")
    
    # Count partition files
    partition_count = 0
    output_size = 0
    
    # Calculate output stats - for cloud, query the filesystem directly
    if is_cloud and filesystem is not None:
        try:
            from pyarrow import fs as pafs
            selector = pafs.FileSelector(cloud_campaign_path, recursive=True)
            file_info = filesystem.get_file_info(selector)
            for f in file_info:
                if f.type.name == 'File' and f.path.endswith('.parquet'):
                    partition_count += 1
                    output_size += f.size
        except Exception:
            # Fall back to local if cloud query fails
            pass
    elif campaign_output_dir.exists():
        for root, dirs, files in os.walk(campaign_output_dir):
            for file in files:
                if file.endswith('.parquet'):
                    partition_count += 1
                    file_path = Path(root) / file
                    try:
                        output_size += os.path.getsize(file_path)
                    except OSError:
                        pass
    
    # Print elaborate processing report  # pragma: no cover
    print("\n" + "=" * 60)
    print("[geotrack] Processing Report")
    print("=" * 60)
    print(f"\n▸ Input")
    print(f"  Source input          : {input_source}")
    print(f"  CSV files processed   : {len(csv_files)}")
    print(f"  Total rows ingested   : {len(df):,}")
    
    print(f"\n▸ Data Summary")
    print(f"  Latitude range        : [{lat_min:.4f}, {lat_max:.4f}]")
    print(f"  Longitude range       : [{lon_min:.4f}, {lon_max:.4f}]")
    print(f"  Columns               : {len(df.columns)}")
    print(f"  Provider              : {provider.name}")
    
    print(f"\n▸ Partitioning")
    print(f"  Latitude bins         : {len(lat_bins)-1}")
    print(f"  Longitude bins        : {len(lon_bins)-1}")
    print(f"  Partition files       : {partition_count}")
    
    # Sensors & Platform section
    if detected_sensors or platform_metadata:
        print(f"\n▸ Sensors & Platform")
        if platform_metadata:
            platform_type = platform_metadata.get('type', 'Unknown')
            platform_id = platform_metadata.get('id', 'N/A')
            print(f"  Platform              : {platform_type} ({platform_id})")
        if detected_sensors:
            print(f"  Sensors detected      : {len(detected_sensors)}")
            for sensor in detected_sensors[:5]:  # Show first 5
                sensor_info = f"{sensor.name} ({sensor.manufacturer})"
                print(f"    • {sensor_info}")
            if len(detected_sensors) > 5:
                print(f"    • ... and {len(detected_sensors) - 5} more")
    
    print(f"\n▸ Output")
    print(f"  Base output directory : {output_dir}")
    # Show cloud path for cloud storage, local path otherwise
    display_campaign_dir = f"{storage_path.provider}://{cloud_campaign_path}" if is_cloud else campaign_output_dir
    print(f"  Campaign directory    : {display_campaign_dir}")
    print(f"  Campaign ID           : {detected_campaign_id}")
    print(f"  GeoParquet format     : ✓ Written")
    print(f"  Total output size     : {_format_file_size(output_size)}")
    
    if semantic_meta:
        print(f"  Semantic metadata     : ✓ Embedded")
    
    if pmtiles_generated:
        print(f"\n▸ PMTiles Vector Tiles")
        print(f"  PMTiles directory     : {pmtiles_path.parent}")
        print(f"  PMTiles file          : ✓ {pmtiles_path.name}")
        print(f"  File size             : {_format_file_size(pmtiles_size)}")
        print(f"  Zoom levels           : {pmtiles_minzoom} - {pmtiles_maxzoom}")
        print(f"  Layer name            : {pmtiles_layer}")
    
    if stac_generated:
        print(f"\n▸ STAC Metadata")
        display_stac_dir = f"{storage_path.provider}://{cloud_campaign_path}/stac" if is_cloud else campaign_output_dir / 'stac'
        print(f"  STAC directory        : {display_stac_dir}")
        print(f"  Collection JSON       : ✓ collection.json")
        print(f"  Item JSON files       : ✓ {stac_items_count} item(s)")
        print(f"  STAC version          : 1.0.0")
    
    # Sample columns
    sample_cols = list(df.columns)[:12]
    more_flag = f" (+ {len(df.columns) - len(sample_cols)} more)" if len(df.columns) > len(sample_cols) else ""
    print(f"\n▸ Column Sample")
    print(f"  {', '.join(sample_cols)}{more_flag}")
    
    print(f"\n▸ Performance")
    print(f"  Total elapsed time    : {processor.elapsed_time():0.2f}s")
    print(f"  Rows per second       : {len(df) / processor.elapsed_time():,.0f}")
    
    print("\n" + "=" * 60)
    print("[geotrack] ✓ Completed successfully")
    print("=" * 60 + "\n")


# Backward compatibility alias
process = convert
