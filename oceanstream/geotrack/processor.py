"""Geotrack processing module for converting CSV data to GeoParquet."""
from __future__ import annotations
import os
import sys
from pathlib import Path
from time import perf_counter
from typing import Any, Callable
import pandas as pd

try:
    from tqdm.auto import tqdm
    HAS_TQDM = True
except ImportError:
    HAS_TQDM = False

from .csv_reader import _sanitize_column_types, extract_platform_id
from .geoparquet_writer import write_geoparquet
from ..stac import emit_stac_collection_and_item
from ..config.settings import Settings
from ..semantic.semantic import SemanticMapper, SemanticConfig, semantic_to_parquet_metadata
from .binning import suggest_lat_lon_bins_from_data
from ..providers.base import ProviderBase
from ..sensors import get_sensor_catalogue, Sensor
from ..sensors.saildrone import detect_saildrone_platform, get_platform_sensors


def _format_file_size(size_bytes: int) -> str:
    """Format file size in human-readable format."""
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size_bytes < 1024.0:
            return f"{size_bytes:3.1f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.1f} PB"


def _display_files_summary(input_dir: Path, csv_files: list[str]) -> bool:
    """
    Display a summary table of detected files and prompt for confirmation.
    
    Args:
        input_dir: Directory containing the CSV files
        csv_files: List of CSV filenames
        
    Returns:
        True if user confirms to proceed, False otherwise
    """
    print(f"\n[geotrack] Detected {len(csv_files)} file(s) in {input_dir}:\n")
    
    # Collect file info
    file_info = []
    total_size = 0
    for fname in csv_files:
        file_path = input_dir / fname
        try:
            size_bytes = os.path.getsize(file_path)
            total_size += size_bytes
            file_info.append((fname, size_bytes))
        except OSError:
            file_info.append((fname, 0))
    
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


def _read_single_csv(file_path: Path, filename: str) -> pd.DataFrame | None:
    """Read and validate a single CSV file."""
    df = pd.read_csv(file_path, on_bad_lines='skip', low_memory=False)
    df = df.replace(to_replace=["nan", "NaN", "NULL", "None"], value=pd.NA)
    df = df.replace(r"^\s*$", pd.NA, regex=True)
    if 'latitude' not in df.columns or 'longitude' not in df.columns:
        return None
    df['latitude'] = pd.to_numeric(df['latitude'], errors='coerce')
    df['longitude'] = pd.to_numeric(df['longitude'], errors='coerce')
    df = df.dropna(subset=['latitude', 'longitude'])
    if df.empty:
        return None
    df['platform_id'] = extract_platform_id(filename)
    df = _sanitize_column_types(df)
    na_subset = [c for c in df.columns if c != 'platform_id']
    df = df.dropna(how='all', subset=na_subset)
    df = df.dropna(axis=1, how='all')
    return df


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


class GeotrackProcessor:
    """Processor for geotrack data."""
    
    def __init__(self, provider: ProviderBase, verbose: bool = False):
        self.provider = provider
        self.verbose = verbose
        self._start_time = perf_counter()
    
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
    
    def scan_input_directory(self, input_dir: Path) -> list[str]:
        """Scan input directory for CSV files."""
        t0 = self.step(f"scanning input directory {input_dir}")
        try:
            csv_files = [f for f in os.listdir(input_dir) if f.endswith(".csv")]
        except FileNotFoundError:
            raise FileNotFoundError(f"input directory not found: {input_dir}")
        self.done(f"found {len(csv_files)} CSV file(s)", t0)
        return csv_files
    
    def process_files(self, input_dir: Path, csv_files: list[str]) -> pd.DataFrame:
        """Process CSV files with optional progress bars."""
        data_frames = []
        
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
        
        for fname in iterator:
            self._process_single_file(input_dir, fname, data_frames)
        
        if not data_frames:
            raise ValueError("No usable data after per-file processing.")
        
        df = _concat_data_frames(data_frames)
        if self.verbose:
            print(f"[geotrack] Consolidated rows: {len(df)} from {len(data_frames)} file(s)")
        return df
    
    def _process_single_file(self, input_dir: Path, fname: str, data_frames: list[pd.DataFrame]) -> None:
        """Process a single CSV file."""
        file_path = input_dir / fname
        try:
            df_file = _read_single_csv(file_path, fname)
        except Exception as e:
            if self.verbose:
                print(f"[geotrack]   ! Skipping {fname} (read error: {e})")
            return
        
        if df_file is None or df_file.empty:
            if self.verbose:
                print(f"[geotrack]   · Skipping {fname} (no usable rows)")
            return
        
        # Enrichment
        df_enriched = self.provider.enrich_dataframe(df_file)
        if df_enriched.empty:
            if self.verbose:
                print(f"[geotrack]   · Skipping {fname} after enrichment (no lat/lon)")
            return
        
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
    
    def detect_sensors_and_platform(self, df: pd.DataFrame) -> tuple[list[Sensor], dict[str, Any]]:
        """Detect sensors and platform info from DataFrame.
        
        Args:
            df: Consolidated DataFrame with all data
            
        Returns:
            Tuple of (detected_sensors, platform_metadata)
        """
        # Detect sensors from available columns
        available_vars = set(df.columns)
        catalogue = get_sensor_catalogue()
        detected_sensors = catalogue.detect_sensors(available_vars)
        
        # Extract platform information
        platform_metadata = {}
        
        # Get trajectory/platform ID
        if 'trajectory' in df.columns:
            # Find first non-NaN trajectory value
            trajectory_values = df['trajectory'].dropna()
            if len(trajectory_values) > 0:
                trajectory_id = int(trajectory_values.iloc[0])
                platform_type = detect_saildrone_platform(trajectory_id)
            
                platform_metadata = {
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
        
        # Get platform_id from first row if available
        if 'platform_id' in df.columns:
            platform_metadata['platform_id'] = str(df['platform_id'].iloc[0])
        
        # Log findings
        if self.verbose:
            print(f"[geotrack]   Detected {len(detected_sensors)} sensors")
            for sensor in detected_sensors[:3]:  # Show first 3
                print(f"[geotrack]     • {sensor.name}")
            if len(detected_sensors) > 3:
                print(f"[geotrack]     • ... and {len(detected_sensors) - 3} more")
            if platform_metadata:
                print(f"[geotrack]   Platform: {platform_metadata.get('type', 'Unknown')}")
        
        return detected_sensors, platform_metadata
    
    def write_geoparquet_dataset(
        self,
        df: pd.DataFrame,
        output_dir: Path,
        semantic_meta: dict[str, Any] | None = None,
    ) -> None:
        """Write the GeoParquet dataset."""
        # Derive bins
        t0 = self.step("deriving latitude/longitude bins")
        lat_bins, lon_bins = suggest_lat_lon_bins_from_data(df)
        self.done(f"{len(lat_bins)-1} lat bins, {len(lon_bins)-1} lon bins", t0)
        
        # Prepare metadata
        t0 = self.step("preparing metadata (aliases, units, provider)")
        aliases = self.provider.alias_mapping(df.columns)
        units = self.provider.units_mapping(df.columns)
        if units and not any(v for v in units.values() if v):
            units = None
        prov_meta = self.provider.parquet_metadata(df)
        self.done("metadata prepared", t0)
        
        # Write dataset
        t0 = self.step(f"writing GeoParquet dataset to {output_dir}")
        write_geoparquet(
            df,
            output_dir,
            lat_bins,
            lon_bins,
            units_metadata=units or None,
            alias_mapping=aliases or None,
            provider_metadata=prov_meta or None,
            semantic_metadata=semantic_meta or None,
        )
        self.done("dataset write complete", t0)
    
    def emit_stac_metadata(
        self,
        output_dir: Path,
        df: pd.DataFrame,
        semantic_meta: dict[str, Any] | None,
        detected_sensors: list[Sensor] | None = None,
        platform_metadata: dict[str, Any] | None = None,
        pmtiles_path: Path | None = None,
        measurement_columns: list[str] | None = None,
    ) -> None:
        """Emit STAC Collection and Items.
        
        Args:
            output_dir: Output directory for STAC files
            df: DataFrame with the data
            semantic_meta: Semantic metadata
            detected_sensors: List of detected sensors
            platform_metadata: Platform metadata
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
            measurement_columns: Specific columns to include (None = auto-select)
            
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
    
    if verbose:
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
        if verbose:
            print(f"\n[tiles] ✓ PMTiles generated successfully")
            print(f"[tiles] • File: {result.name}")
            print(f"[tiles] • Size: {_format_file_size(size_bytes)}")
        return result
    else:
        if verbose:
            print(f"\n[tiles] ✗ PMTiles generation failed")
        return None


def convert(
    provider: ProviderBase,
    input_dir: Path,
    output_dir: Path,
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
) -> None:
    """
    Convert geotrack CSV data into GeoParquet format, and optionally PMTiles.
    
    Args:
        provider: Data provider instance
        input_dir: Directory containing input CSV files
        output_dir: Output directory for GeoParquet dataset
        verbose: Enable detailed progress information
        list_columns: List available columns and exit
        print_schema: Print GeoParquet schema and exit
        provider_metadata: Print provider metadata and exit
        dry_run: Analyze inputs without writing files
        upload: Upload processed dataset to cloud storage (future)
        yes: Skip confirmation prompts
        generate_pmtiles: Generate PMTiles vector tiles with segments and day markers
        pmtiles_minzoom: Minimum zoom level for PMTiles (0-15)
        pmtiles_maxzoom: Maximum zoom level for PMTiles (0-15)
        pmtiles_layer: Layer name for PMTiles
        pmtiles_sample_rate: Sample rate - take every Nth point (1=all, 5=every 5th)
        pmtiles_time_gap: Minutes of gap to split track segments
        pmtiles_include_measurements: Include oceanographic measurements in tiles
        pmtiles_measurement_columns: Specific columns to include (None = auto-select)
    """
    processor = GeotrackProcessor(provider, verbose=verbose)
    
    # Step 1: Scan input directory
    csv_files = processor.scan_input_directory(input_dir)
    if not csv_files:
        print("[geotrack] No CSV files to process.")
        return
    
    # Step 1.5: Display file summary and get confirmation (unless in dry-run or inspection mode)
    if not (dry_run or list_columns or print_schema or provider_metadata or yes):
        if not _display_files_summary(input_dir, csv_files):
            print("[geotrack] Processing cancelled.")
            return
    
    # Step 2-3: Process files
    df = processor.process_files(input_dir, csv_files)
    
    # Step 3.5: Detect sensors and platform
    detected_sensors, platform_metadata = processor.detect_sensors_and_platform(df)
    
    # Handle introspection flags
    if list_columns:
        print(f"[geotrack] Columns ({len(df.columns)}):")
        for c in df.columns:
            print(f"  - {c}")
        return
    
    if print_schema:
        dtype_map = {col: str(dt) for col, dt in df.dtypes.items()}
        print("[geotrack] GeoParquet schema preview:")
        for col, dt in dtype_map.items():
            print(f"  - {col}: {dt}")
        print("  (partition columns to be added: lat_bin, lon_bin)")
        return
    
    if provider_metadata:
        meta = provider.parquet_metadata(df)
        print("[geotrack] Provider metadata snapshot:")
        for k, v in meta.items():
            print(f"  {k}: {v}")
        return
    
    # Apply semantic mapping
    semantic_meta = processor.apply_semantic_mapping(df)
    
    # Dry-run summary
    if dry_run:
        lat_bins, lon_bins = suggest_lat_lon_bins_from_data(df)
        lat_min, lat_max = float(df['latitude'].min()), float(df['latitude'].max())
        lon_min, lon_max = float(df['longitude'].min()), float(df['longitude'].max())
        print("\n[geotrack] Dry Run Summary")
        print("--------------------------------")
        print(f"Source directory      : {input_dir}")
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
    
    # Write GeoParquet
    processor.write_geoparquet_dataset(df, output_dir, semantic_meta)
    
    # Generate PMTiles if requested (before STAC so we can include the path)
    pmtiles_generated = False
    pmtiles_path = None
    pmtiles_size = 0
    
    if generate_pmtiles:
        # Extract platform_id from first file if available
        platform_id = None
        if df is not None and 'platform_id' in df.columns and len(df) > 0:
            platform_id = str(df['platform_id'].iloc[0])
        
        pmtiles_path = processor.generate_pmtiles_dataset(
            geoparquet_root=output_dir,
            minzoom=pmtiles_minzoom,
            maxzoom=pmtiles_maxzoom,
            layer_name=pmtiles_layer,
            sample_rate=pmtiles_sample_rate,
            time_gap_minutes=pmtiles_time_gap,
            platform_id=platform_id,
            include_measurements=pmtiles_include_measurements,
            measurement_columns=pmtiles_measurement_columns,
        )
        if pmtiles_path and pmtiles_path.exists():
            pmtiles_generated = True
            try:
                pmtiles_size = os.path.getsize(pmtiles_path)
            except OSError:
                pass
    
    # Check if STAC was generated
    stac_generated = False
    stac_collection_path = None
    stac_items_count = 0
    
    if Settings.SEMANTIC_ENABLE and Settings.SEMANTIC_GENERATE_STAC:
        # Emit STAC metadata with PMTiles path and measurement columns
        processor.emit_stac_metadata(
            output_dir, 
            df, 
            semantic_meta, 
            detected_sensors, 
            platform_metadata,
            pmtiles_path=pmtiles_path,
            measurement_columns=pmtiles_measurement_columns if pmtiles_include_measurements else None,
        )
        
        # Check if files were actually created (STAC files are in stac/ subdirectory)
        stac_dir = output_dir / "stac"
        stac_collection_path = stac_dir / "collection.json"
        stac_items_dir = stac_dir / "items"
        
        if stac_collection_path.exists():
            stac_generated = True
            # Count item JSON files
            if stac_items_dir.exists():
                stac_items_count = len(list(stac_items_dir.glob("*.json")))
    
    # Count partition files
    partition_count = 0
    output_size = 0
    if output_dir.exists():
        for root, dirs, files in os.walk(output_dir):
            for file in files:
                if file.endswith('.parquet'):
                    partition_count += 1
                    file_path = Path(root) / file
                    try:
                        output_size += os.path.getsize(file_path)
                    except OSError:
                        pass
    
    # Print elaborate processing report
    print("\n" + "=" * 60)
    print("[geotrack] Processing Report")
    print("=" * 60)
    print(f"\n▸ Input")
    print(f"  Source directory      : {input_dir}")
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
    print(f"  Output directory      : {output_dir}")
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
        print(f"  STAC directory        : {output_dir / 'stac'}")
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
