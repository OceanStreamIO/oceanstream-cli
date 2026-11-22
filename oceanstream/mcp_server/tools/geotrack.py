"""Geotrack processing tools — convert CSV/NMEA/CTD to GeoParquet."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from ..server import mcp_app

mcp = mcp_app


@mcp_app.tool()
def geotrack_convert(
    input_source: str,
    output_dir: str = "./output",
    provider: Optional[str] = None,
    campaign_id: Optional[str] = None,
    generate_pmtiles: bool = True,
    generate_heatmaps: bool = True,
    force_reprocess: bool = False,
) -> dict[str, Any]:
    """Convert CSV/NMEA/CTD files to GeoParquet with STAC metadata.

    This is the main geotrack processing command. It scans the input source,
    detects the data format and provider, processes files into a partitioned
    GeoParquet dataset, and generates vector tiles and heatmaps.

    Args:
        input_source: Path to input file or directory containing data files
        output_dir: Output directory for processed data (default: ./output/)
        provider: Data provider name (e.g. 'saildrone', 'r2r'). Auto-detected if not specified.
        campaign_id: Campaign/cruise identifier. Derived from directory name if not specified.
        generate_pmtiles: Generate PMTiles vector tiles for map visualization
        generate_heatmaps: Generate COG heatmap rasters for in-situ measurements
        force_reprocess: Force reprocessing even if output already exists
    """
    try:
        from oceanstream.geotrack import convert
        from oceanstream.providers import get_provider, detect_or_get_provider
    except ImportError as e:
        return {"error": f"Geotrack dependencies not installed. Install with: pip install oceanstream[geotrack]. Details: {e}"}

    input_path = Path(input_source)
    if not input_path.exists():
        return {"error": f"Input path not found: {input_source}"}

    try:
        provider_obj = None
        if provider:
            provider_obj = get_provider(provider)

        result = convert(
            provider=provider_obj,
            input_source=input_path,
            output_dir=Path(output_dir),
            campaign_id=campaign_id,
            verbose=True,
            yes=True,
            force_reprocess=force_reprocess,
            generate_pmtiles=generate_pmtiles,
            pmtiles_include_arrows=True,
            generate_heatmaps=generate_heatmaps,
        )
        return {"status": "success", "result": str(result) if result else "completed"}
    except Exception as e:
        return {"error": f"Geotrack convert failed: {e}"}


@mcp.tool()
def geotrack_generate_tiles(
    input_source: str,
    output_dir: str = "./output",
) -> dict[str, Any]:
    """Generate PMTiles vector tiles from existing GeoParquet output.

    Use this after geotrack_convert to regenerate tiles, or to create tiles
    from GeoParquet data produced by other tools.

    Args:
        input_source: Path to GeoParquet file or directory
        output_dir: Output directory for PMTiles
    """
    try:
        from oceanstream.geotrack import generate_tiles
    except ImportError as e:
        return {"error": f"Geotrack dependencies not installed. Install with: pip install oceanstream[geotrack]. Details: {e}"}

    input_path = Path(input_source)
    if not input_path.exists():
        return {"error": f"Input path not found: {input_source}"}

    try:
        result = generate_tiles(
            geoparquet_dir=input_path,
            output_dir=Path(output_dir),
        )
        if result is None:
            return {"error": "Tile generation failed (are tippecanoe and pmtiles installed?)"}
        return {"status": "success", "result": str(result) if result else "completed"}
    except Exception as e:
        return {"error": f"Tile generation failed: {e}"}


@mcp.tool()
def geotrack_generate_report(
    input_source: str,
    output_dir: str = "./output",
) -> dict[str, Any]:
    """Generate a processing report for a geotrack campaign.

    Produces a summary report with statistics, coverage, and quality metrics
    for previously processed geotrack data.

    Args:
        input_source: Path to processed output directory or GeoParquet
        output_dir: Output directory for the report
    """
    try:
        from oceanstream.geotrack import generate_report
    except ImportError as e:
        return {"error": f"Geotrack dependencies not installed. Install with: pip install oceanstream[geotrack]. Details: {e}"}

    input_path = Path(input_source)
    if not input_path.exists():
        return {"error": f"Input path not found: {input_source}"}

    try:
        report_path = Path(output_dir) / "report.md"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        result = generate_report(dataset_path=input_path, output_path=report_path)
        return {"status": "success", "result": str(result) if result else "completed"}
    except Exception as e:
        return {"error": f"Report generation failed: {e}"}
