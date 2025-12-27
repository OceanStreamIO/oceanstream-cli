"""
Report generation for OceanStream GeoParquet datasets.

This module provides functions to analyze GeoParquet datasets and generate
comprehensive reports in Markdown or JSON format.
"""

from __future__ import annotations

import glob
import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow.parquet as pq


@dataclass
class DatasetStats:
    """Container for dataset statistics."""

    total_rows: int = 0
    total_columns: int = 0
    parquet_files: int = 0
    total_size_mb: float = 0.0

    # Temporal
    start_time: datetime | None = None
    end_time: datetime | None = None
    duration_days: int = 0

    # Spatial
    lat_min: float = 0.0
    lat_max: float = 0.0
    lon_min: float = 0.0
    lon_max: float = 0.0

    # Partitioning
    lat_bins: int = 0
    lon_bins: int = 0

    # Platform breakdown
    platforms: dict[str, int] = field(default_factory=dict)

    # Column categories
    nav_columns: list[str] = field(default_factory=list)
    met_columns: list[str] = field(default_factory=list)
    ocean_columns: list[str] = field(default_factory=list)
    other_columns: list[str] = field(default_factory=list)

    # Measurement statistics
    oceanographic: dict[str, dict[str, float]] = field(default_factory=dict)
    meteorological: dict[str, dict[str, float]] = field(default_factory=dict)


@dataclass
class STACMetadata:
    """Container for STAC metadata."""

    collection_id: str = ""
    stac_version: str = ""
    description: str = ""
    license: str = ""
    keywords: list[str] = field(default_factory=list)
    providers: list[dict] = field(default_factory=list)
    instruments: list[dict] = field(default_factory=list)
    platform: dict = field(default_factory=dict)
    processing: dict = field(default_factory=dict)
    item_count: int = 0


def find_parquet_files(dataset_path: Path) -> list[Path]:
    """Find all parquet files in the dataset directory.
    
    Args:
        dataset_path: Path to the dataset directory.
        
    Returns:
        List of paths to parquet files (excluding those in stac/ directory).
    """
    # Use a single recursive glob pattern that covers all depths
    pattern = str(dataset_path / "**" / "*.parquet")
    files = glob.glob(pattern, recursive=True)

    # Exclude files in stac directory
    return [Path(f) for f in files if "/stac/" not in str(f)]


def load_dataset(parquet_files: list[Path]) -> pd.DataFrame:
    """Load all parquet files into a single DataFrame.
    
    Args:
        parquet_files: List of paths to parquet files.
        
    Returns:
        Combined DataFrame with all data.
        
    Raises:
        ValueError: If no parquet files found or none could be read.
    """
    if not parquet_files:
        raise ValueError("No parquet files found")

    dfs = []
    for pf in parquet_files:
        try:
            df = pq.read_table(pf).to_pandas()
            dfs.append(df)
        except Exception:
            # Skip files that can't be read
            pass

    if not dfs:
        raise ValueError("Could not read any parquet files")

    return pd.concat(dfs, ignore_index=True)


def calculate_stats(df: pd.DataFrame, parquet_files: list[Path]) -> DatasetStats:
    """Calculate comprehensive statistics from the dataset.
    
    Args:
        df: DataFrame with dataset contents.
        parquet_files: List of parquet file paths (for size calculation).
        
    Returns:
        DatasetStats object with computed statistics.
    """
    stats = DatasetStats()

    # Basic counts
    stats.total_rows = len(df)
    stats.total_columns = len(df.columns)
    stats.parquet_files = len(parquet_files)
    stats.total_size_mb = sum(f.stat().st_size for f in parquet_files) / (1024 * 1024)

    # Temporal extent
    if "time" in df.columns:
        stats.start_time = df["time"].min()
        stats.end_time = df["time"].max()
        if stats.start_time and stats.end_time:
            stats.duration_days = (stats.end_time - stats.start_time).days

    # Spatial extent
    if "latitude" in df.columns:
        stats.lat_min = float(df["latitude"].min())
        stats.lat_max = float(df["latitude"].max())
    if "longitude" in df.columns:
        stats.lon_min = float(df["longitude"].min())
        stats.lon_max = float(df["longitude"].max())

    # Partitioning
    if "lat_bin" in df.columns:
        stats.lat_bins = df["lat_bin"].nunique()
    if "lon_bin" in df.columns:
        stats.lon_bins = df["lon_bin"].nunique()

    # Platform breakdown
    trajectory_col = None
    for col in ["trajectory", "platform_id", "platform"]:
        if col in df.columns:
            trajectory_col = col
            break

    if trajectory_col:
        for val, count in df[trajectory_col].value_counts().items():
            key = str(val)
            if trajectory_col == "trajectory" and isinstance(val, (int, float)):
                key = f"SD{int(val)}"
            stats.platforms[key] = int(count)

    # Categorize columns
    for col in df.columns:
        col_upper = col.upper()
        if any(x in col_upper for x in ["SOG", "COG", "HDG", "ROLL", "PITCH", "WING", "YAW"]):
            stats.nav_columns.append(col)
        elif any(x in col_upper for x in ["WIND", "TEMP_AIR", "RH", "BARO", "PAR", "SW_IRRAD", "GUST"]):
            stats.met_columns.append(col)
        elif any(x in col_upper for x in ["TEMP_SBE", "TEMP_DEPTH", "TEMP_IR", "SAL", "COND", "O2", "CHLOR", "WAVE"]):
            stats.ocean_columns.append(col)
        else:
            stats.other_columns.append(col)

    # Oceanographic measurement stats
    ocean_vars = [
        "TEMP_SBE37_MEAN", "SAL_SBE37_MEAN", "O2_CONC_SBE37_MEAN",
        "CHLOR_WETLABS_MEAN", "TEMP_DEPTH_HALFMETER_MEAN", "WAVE_SIGNIFICANT_HEIGHT",
        "TEMP_CTD", "SAL_CTD", "sea_water_temperature", "sea_water_practical_salinity",
    ]
    for var in ocean_vars:
        if var in df.columns:
            col = pd.to_numeric(df[var], errors="coerce")
            valid = col.dropna()
            if len(valid) > 0:
                stats.oceanographic[var] = {
                    "min": float(valid.min()),
                    "max": float(valid.max()),
                    "mean": float(valid.mean()),
                    "valid_count": int(len(valid)),
                    "valid_pct": 100 * len(valid) / len(df),
                }

    # Meteorological measurement stats
    met_vars = [
        "TEMP_AIR_MEAN", "WIND_SPEED_MEAN", "BARO_PRES_MEAN", "RH_MEAN",
        "air_temperature", "wind_speed", "air_pressure", "relative_humidity",
    ]
    for var in met_vars:
        if var in df.columns:
            col = pd.to_numeric(df[var], errors="coerce")
            valid = col.dropna()
            if len(valid) > 0:
                stats.meteorological[var] = {
                    "min": float(valid.min()),
                    "max": float(valid.max()),
                    "mean": float(valid.mean()),
                }

    return stats


def load_stac_metadata(dataset_path: Path) -> STACMetadata | None:
    """Load STAC collection metadata if available.
    
    Args:
        dataset_path: Path to the dataset directory.
        
    Returns:
        STACMetadata object if collection.json exists, None otherwise.
    """
    collection_path = dataset_path / "stac" / "collection.json"
    if not collection_path.exists():
        return None

    try:
        with open(collection_path) as f:
            data = json.load(f)

        stac = STACMetadata()
        stac.collection_id = data.get("id", "")
        stac.stac_version = data.get("stac_version", "")
        stac.description = data.get("description", "")
        stac.license = data.get("license", "")
        stac.keywords = data.get("keywords", [])
        stac.providers = data.get("providers", [])

        summaries = data.get("summaries", {})
        stac.instruments = summaries.get("instruments", [])
        stac.platform = summaries.get("platform", {})
        stac.processing = summaries.get("processing", {})

        # Count items
        items_dir = dataset_path / "stac" / "items"
        if items_dir.exists():
            stac.item_count = len(list(items_dir.glob("*.json")))

        return stac
    except Exception:
        return None


def _format_datetime(dt: datetime | None) -> str:
    """Format datetime for display."""
    if dt is None:
        return "N/A"
    return dt.strftime("%Y-%m-%d %H:%M:%S UTC")


def generate_markdown_report(
    dataset_path: Path,
    stats: DatasetStats,
    stac: STACMetadata | None,
    campaign_id: str | None = None,
) -> str:
    """Generate a Markdown report from the statistics.
    
    Args:
        dataset_path: Path to the dataset directory.
        stats: Computed dataset statistics.
        stac: STAC metadata (optional).
        campaign_id: Campaign identifier override.
        
    Returns:
        Markdown-formatted report string.
    """
    lines = []

    # Header
    campaign = campaign_id or dataset_path.name
    lines.append(f"# OceanStream Processing Report: {campaign}")
    lines.append("")
    lines.append(f"**Generated**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"**Dataset Path**: `{dataset_path}`")
    lines.append("")
    lines.append("---")
    lines.append("")

    # Executive Summary
    lines.append("## Executive Summary")
    lines.append("")
    lines.append(f"Processed **{stats.parquet_files} GeoParquet files** containing "
                 f"**{stats.total_rows:,} observations**.")
    if stats.duration_days:
        lines.append(f"Data spans **{stats.duration_days} days** "
                     f"({_format_datetime(stats.start_time).split()[0]} – "
                     f"{_format_datetime(stats.end_time).split()[0]}).")
    lines.append("")
    lines.append("| Metric | Value |")
    lines.append("|--------|-------|")
    lines.append(f"| Parquet files | {stats.parquet_files} |")
    lines.append(f"| Total rows | {stats.total_rows:,} |")
    lines.append(f"| Total columns | {stats.total_columns} |")
    lines.append(f"| Dataset size | {stats.total_size_mb:.1f} MB |")
    lines.append(f"| Partitions | {stats.lat_bins} lat × {stats.lon_bins} lon |")
    lines.append("")
    lines.append("---")
    lines.append("")

    # Platforms
    if stats.platforms:
        lines.append("## Platforms")
        lines.append("")
        lines.append("| Platform ID | Rows | % of Total |")
        lines.append("|-------------|------|------------|")
        for platform, count in sorted(stats.platforms.items(), key=lambda x: -x[1]):
            pct = 100 * count / stats.total_rows
            lines.append(f"| {platform} | {count:,} | {pct:.1f}% |")
        lines.append("")
        lines.append("---")
        lines.append("")

    # Sensors (from STAC)
    if stac and stac.instruments:
        lines.append("## Detected Sensors")
        lines.append("")
        lines.append(f"**{len(stac.instruments)} sensors** detected:")
        lines.append("")
        lines.append("| Sensor | Manufacturer | Type |")
        lines.append("|--------|--------------|------|")
        for inst in stac.instruments:
            name = inst.get("name", "Unknown")
            mfr = inst.get("manufacturer", "Unknown")
            stype = inst.get("type", "Unknown")
            lines.append(f"| {name} | {mfr} | {stype} |")
        lines.append("")
        lines.append("---")
        lines.append("")

    # Temporal Extent
    lines.append("## Temporal Extent")
    lines.append("")
    lines.append(f"- **Start**: {_format_datetime(stats.start_time)}")
    lines.append(f"- **End**: {_format_datetime(stats.end_time)}")
    lines.append(f"- **Duration**: {stats.duration_days} days")
    lines.append("")
    lines.append("---")
    lines.append("")

    # Spatial Extent
    lines.append("## Spatial Extent")
    lines.append("")
    lines.append(f"- **Latitude**: [{stats.lat_min:.4f}, {stats.lat_max:.4f}]")
    lines.append(f"- **Longitude**: [{stats.lon_min:.4f}, {stats.lon_max:.4f}]")
    lines.append("")
    lines.append("---")
    lines.append("")

    # Oceanographic Measurements
    if stats.oceanographic:
        lines.append("## Oceanographic Measurements")
        lines.append("")
        lines.append("| Variable | Range | Mean | Valid % |")
        lines.append("|----------|-------|------|---------|")
        for var, s in stats.oceanographic.items():
            lines.append(f"| {var} | {s['min']:.2f} – {s['max']:.2f} | {s['mean']:.2f} | {s['valid_pct']:.1f}% |")
        lines.append("")
        lines.append("---")
        lines.append("")

    # Meteorological Measurements
    if stats.meteorological:
        lines.append("## Meteorological Measurements")
        lines.append("")
        lines.append("| Variable | Range | Mean |")
        lines.append("|----------|-------|------|")
        for var, s in stats.meteorological.items():
            lines.append(f"| {var} | {s['min']:.2f} – {s['max']:.2f} | {s['mean']:.2f} |")
        lines.append("")
        lines.append("---")
        lines.append("")

    # Column Categories
    lines.append("## Column Categories")
    lines.append("")
    lines.append("| Category | Count |")
    lines.append("|----------|-------|")
    lines.append(f"| Navigation/IMU | {len(stats.nav_columns)} |")
    lines.append(f"| Meteorological | {len(stats.met_columns)} |")
    lines.append(f"| Oceanographic | {len(stats.ocean_columns)} |")
    lines.append(f"| Other | {len(stats.other_columns)} |")
    lines.append("")
    lines.append("---")
    lines.append("")

    # STAC Metadata
    if stac:
        lines.append("## STAC Metadata")
        lines.append("")
        lines.append(f"- **Collection ID**: `{stac.collection_id}`")
        lines.append(f"- **STAC Version**: {stac.stac_version}")
        lines.append(f"- **License**: {stac.license}")
        lines.append(f"- **Items**: {stac.item_count}")
        if stac.processing:
            lines.append(f"- **Software**: {stac.processing.get('software', 'N/A')} "
                         f"v{stac.processing.get('version', 'N/A')}")
        lines.append("")
        lines.append("---")
        lines.append("")

    # Usage Examples
    lines.append("## Usage Examples")
    lines.append("")
    lines.append("### Read with GeoPandas")
    lines.append("")
    lines.append("```python")
    lines.append("import geopandas as gpd")
    lines.append("")
    lines.append(f"gdf = gpd.read_parquet('{dataset_path}/')")
    lines.append("print(gdf.head())")
    lines.append("```")
    lines.append("")
    lines.append("### Query with DuckDB")
    lines.append("")
    lines.append("```sql")
    lines.append("SELECT")
    lines.append("    date_trunc('day', time) as day,")
    lines.append("    count(*) as observations")
    lines.append(f"FROM read_parquet('{dataset_path}/**/*.parquet')")
    lines.append("GROUP BY 1")
    lines.append("ORDER BY 1;")
    lines.append("```")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("*Report generated by OceanStream*")

    return "\n".join(lines)


def generate_json_report(
    dataset_path: Path,
    stats: DatasetStats,
    stac: STACMetadata | None,
    campaign_id: str | None = None,
) -> dict[str, Any]:
    """Generate a JSON report from the statistics.
    
    Args:
        dataset_path: Path to the dataset directory.
        stats: Computed dataset statistics.
        stac: STAC metadata (optional).
        campaign_id: Campaign identifier override.
        
    Returns:
        Dictionary with report data.
    """
    report: dict[str, Any] = {
        "generated_at": datetime.now().isoformat(),
        "dataset_path": str(dataset_path),
        "campaign_id": campaign_id or dataset_path.name,
        "summary": {
            "total_rows": stats.total_rows,
            "total_columns": stats.total_columns,
            "parquet_files": stats.parquet_files,
            "size_mb": round(stats.total_size_mb, 2),
        },
        "temporal_extent": {
            "start": stats.start_time.isoformat() if stats.start_time else None,
            "end": stats.end_time.isoformat() if stats.end_time else None,
            "duration_days": stats.duration_days,
        },
        "spatial_extent": {
            "latitude": {"min": stats.lat_min, "max": stats.lat_max},
            "longitude": {"min": stats.lon_min, "max": stats.lon_max},
        },
        "partitioning": {
            "lat_bins": stats.lat_bins,
            "lon_bins": stats.lon_bins,
        },
        "platforms": stats.platforms,
        "columns": {
            "navigation": stats.nav_columns,
            "meteorological": stats.met_columns,
            "oceanographic": stats.ocean_columns,
            "other": stats.other_columns,
        },
        "measurements": {
            "oceanographic": stats.oceanographic,
            "meteorological": stats.meteorological,
        },
    }

    if stac:
        report["stac"] = {
            "collection_id": stac.collection_id,
            "version": stac.stac_version,
            "license": stac.license,
            "keywords": stac.keywords,
            "instruments": stac.instruments,
            "platform": stac.platform,
            "processing": stac.processing,
            "item_count": stac.item_count,
        }

    return report


def generate_report(
    dataset_path: Path,
    output_path: Path | None = None,
    output_format: str = "markdown",
    campaign_id: str | None = None,
    verbose: bool = False,
) -> str | dict[str, Any]:
    """Generate a processing report for a GeoParquet dataset.
    
    Args:
        dataset_path: Path to the GeoParquet dataset directory.
        output_path: Optional path to write the report to.
        output_format: Output format ('markdown' or 'json').
        campaign_id: Campaign identifier override.
        verbose: Print progress messages.
        
    Returns:
        Report content (string for markdown, dict for json).
        
    Raises:
        FileNotFoundError: If dataset_path doesn't exist.
        ValueError: If no parquet files found or invalid format.
    """
    import sys
    
    if not dataset_path.exists():
        raise FileNotFoundError(f"Dataset path does not exist: {dataset_path}")

    if verbose:
        print(f"[report] Analyzing dataset: {dataset_path}", file=sys.stderr)

    # Find parquet files
    parquet_files = find_parquet_files(dataset_path)
    if not parquet_files:
        raise ValueError(f"No parquet files found in {dataset_path}")

    if verbose:
        print(f"[report] Found {len(parquet_files)} parquet files", file=sys.stderr)

    # Load dataset
    if verbose:
        print("[report] Loading dataset...", file=sys.stderr)
    df = load_dataset(parquet_files)
    if verbose:
        print(f"[report] Loaded {len(df):,} rows", file=sys.stderr)

    # Calculate statistics
    if verbose:
        print("[report] Calculating statistics...", file=sys.stderr)
    stats = calculate_stats(df, parquet_files)

    # Load STAC metadata
    stac = load_stac_metadata(dataset_path)
    if stac and verbose:
        print(f"[report] Loaded STAC metadata: {stac.collection_id}", file=sys.stderr)

    # Generate report
    if verbose:
        print("[report] Generating report...", file=sys.stderr)
        
    if output_format == "json":
        report: str | dict[str, Any] = generate_json_report(dataset_path, stats, stac, campaign_id)
        output_content = json.dumps(report, indent=2, default=str)
    elif output_format == "markdown":
        report = generate_markdown_report(dataset_path, stats, stac, campaign_id)
        output_content = report
    else:
        raise ValueError(f"Invalid output format: {output_format}. Use 'markdown' or 'json'.")

    # Write output if path provided
    if output_path:
        output_path.write_text(output_content)
        if verbose:
            print(f"[report] Report written to: {output_path}", file=sys.stderr)

    return report
