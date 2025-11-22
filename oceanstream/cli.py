from __future__ import annotations
import os
import time
from time import perf_counter
import sys
from pathlib import Path
from typing import Any
import pandas as pd
try:
    import typer  # type: ignore
except Exception:  # pragma: no cover - optional dependency for nicer CLI
    typer = None  # type: ignore

from .providers import get_provider, list_providers
from . import geotrack, echodata, multibeam, adcp


app = typer.Typer(
    help="Oceanstream data processing CLI (process oceanographic & acoustic data).",
    no_args_is_help=True,  # Show help instead of error when no command provided
) if typer else None
process_app = typer.Typer(
    help="Process raw measurement data into standardized outputs.",
    no_args_is_help=True,  # Show help instead of error when no command provided
) if typer else None
campaign_app = typer.Typer(
    help="Manage campaigns (create, update, list, etc.)",
    no_args_is_help=True,  # Show help instead of error when no command provided
) if typer else None

# Global state for provider (set by process callback, used by subcommands)
_provider_obj = None


if typer:
    # Register nested apps
    app.add_typer(process_app, name="process")
    app.add_typer(campaign_app, name="campaign")
    
    @app.command("providers")
    def providers_command() -> None:
        """List all available data providers."""
        available = list_providers()
        typer.echo("Available providers:")
        for p in available:
            typer.echo(f"  - {p}")
    
    @campaign_app.command("create")
    def create_campaign_command(
        campaign_id: str = typer.Argument(..., help="Campaign/cruise identifier (e.g., FK161229, SD1030_2023)"),
        platform_id: str = typer.Option(None, help="Platform identifier (e.g., sd1030, R/V Falkor)"),
        platform_name: str = typer.Option(None, help="Full platform name (e.g., 'Saildrone Explorer 1030', 'R/V Falkor')"),
        platform_type: str = typer.Option(None, help="Platform type (e.g., 'Saildrone Explorer', 'Research Vessel')"),
        description: str = typer.Option(None, help="Campaign description"),
        start_date: str = typer.Option(None, help="Campaign start date in ISO 8601 format (YYYY-MM-DD or YYYY-MM-DDTHH:MM:SSZ)"),
        end_date: str = typer.Option(None, help="Campaign end date in ISO 8601 format (YYYY-MM-DD or YYYY-MM-DDTHH:MM:SSZ)"),
        bbox: str = typer.Option(None, help="Spatial bounding box as 'minlon,minlat,maxlon,maxlat' (e.g., '-180,-90,180,90')"),
        attribution: str = typer.Option(None, help="Data attribution/citation"),
        license: str = typer.Option(None, help="Data license (e.g., MIT, CC-BY-4.0)"),
        doi: str = typer.Option(None, help="Dataset DOI"),
        source_repository: str = typer.Option(None, help="Source repository DOI or URL"),
        keywords: str = typer.Option(None, help="Comma-separated keywords"),
        chief_scientist: str = typer.Option(None, help="Chief scientist name"),
        institution: str = typer.Option(None, help="Institution name"),
        project: str = typer.Option(None, help="Project name"),
        funding: str = typer.Option(None, help="Funding information"),
        verbose: bool = typer.Option(False, "-v", help="Emit detailed information"),
    ) -> None:
        """Create a new campaign with optional metadata.
        
        Campaign metadata is stored in ~/.oceanstream/campaigns/ for persistence.
        Only campaign_id is required; all other fields are optional and can be
        added later with 'oceanstream campaign update'.
        
        Example (minimal):
        
            oceanstream campaign create FK161229
        
        Example (with metadata):
        
            oceanstream campaign create FK161229 \\
                --platform-id "R/V Falkor" \\
                --platform-name "Research Vessel Falkor" \\
                --description "Hydrothermal vent study in the Pacific" \\
                --start-date 2016-12-29 \\
                --end-date 2017-01-20 \\
                --attribution "Schmidt Ocean Institute"
        """
        from .geotrack.campaign import create_campaign
        
        # Parse bbox if provided
        bbox_parsed = None
        if bbox:
            try:
                parts = [float(x.strip()) for x in bbox.split(',')]
                if len(parts) != 4:
                    typer.echo(f"[campaign create] ERROR: bbox must have 4 values (minlon,minlat,maxlon,maxlat), got {len(parts)}")
                    raise typer.Exit(code=1)
                bbox_parsed = parts
            except ValueError as e:
                typer.echo(f"[campaign create] ERROR: Invalid bbox format: {e}")
                raise typer.Exit(code=1)
        
        # Parse keywords if provided
        keywords_list = None
        if keywords:
            keywords_list = [k.strip() for k in keywords.split(',')]
        
        # Create campaign metadata dict
        metadata = {
            'campaign_id': campaign_id,
            'platform_id': platform_id,
            'platform_name': platform_name,
            'platform_type': platform_type,
            'description': description,
            'start_date': start_date,
            'end_date': end_date,
            'bbox': bbox_parsed,
            'attribution': attribution,
            'license': license,
            'doi': doi,
            'source_repository': source_repository,
            'keywords': keywords_list,
            'chief_scientist': chief_scientist,
            'institution': institution,
            'project': project,
            'funding': funding,
        }
        
        # Remove None values
        metadata = {k: v for k, v in metadata.items() if v is not None}
        
        try:
            campaign_path = create_campaign(
                campaign_id=campaign_id,
                metadata=metadata,
                verbose=verbose,
            )
            
            typer.echo(f"[campaign create] ✓ Campaign created successfully")
            typer.echo(f"[campaign create]   Campaign ID: {campaign_id}")
            typer.echo(f"[campaign create]   Metadata stored in: {campaign_path / 'campaign.json'}")
            typer.echo(f"\n[campaign create] You can now process data for this campaign:")
            typer.echo(f"  oceanstream process geotrack convert --campaign-id {campaign_id} --input-source <data>")
            
        except Exception as e:
            typer.echo(f"[campaign create] ERROR: {e}")
            raise typer.Exit(code=1)
    
    @campaign_app.command("show")
    def show_campaign_command(
        campaign_id: str = typer.Argument(..., help="Campaign identifier to display"),
    ) -> None:
        """Show detailed information about a campaign.
        
        Example:
        
            oceanstream campaign show FK161229
        """
        from .geotrack.campaign import load_campaign_metadata
        
        try:
            metadata = load_campaign_metadata(campaign_id)
            
            if metadata is None:
                typer.echo(f"[campaign show] ERROR: Campaign '{campaign_id}' not found")
                typer.echo(f"[campaign show] Use 'oceanstream campaign list' to see available campaigns")
                raise typer.Exit(code=1)
            
            # Display campaign information
            typer.echo(f"\n[campaign show] Campaign: {campaign_id}")
            typer.echo(f"{'=' * 60}")
            
            # Core fields
            if "platform_id" in metadata:
                typer.echo(f"Platform ID:        {metadata['platform_id']}")
            if "platform_name" in metadata:
                typer.echo(f"Platform Name:      {metadata['platform_name']}")
            if "platform_type" in metadata:
                typer.echo(f"Platform Type:      {metadata['platform_type']}")
            
            typer.echo()
            
            # Temporal bounds
            if "start_date" in metadata:
                typer.echo(f"Start Date:         {metadata['start_date']}")
            if "end_date" in metadata:
                typer.echo(f"End Date:           {metadata['end_date']}")
            
            # Spatial bounds
            if "bbox" in metadata and metadata["bbox"]:
                bbox = metadata["bbox"]
                typer.echo(f"Bounding Box:       [{bbox[0]}, {bbox[1]}, {bbox[2]}, {bbox[3]}]")
                typer.echo(f"                    (minlon, minlat, maxlon, maxlat)")
            
            typer.echo()
            
            # Description
            if "description" in metadata:
                typer.echo(f"Description:        {metadata['description']}")
            
            typer.echo()
            
            # Attribution and licensing
            if "attribution" in metadata:
                typer.echo(f"Attribution:        {metadata['attribution']}")
            if "license" in metadata:
                typer.echo(f"License:            {metadata['license']}")
            if "doi" in metadata:
                typer.echo(f"DOI:                {metadata['doi']}")
            if "source_repository" in metadata:
                typer.echo(f"Source Repository:  {metadata['source_repository']}")
            
            # Project information
            if "chief_scientist" in metadata:
                typer.echo(f"Chief Scientist:    {metadata['chief_scientist']}")
            if "institution" in metadata:
                typer.echo(f"Institution:        {metadata['institution']}")
            if "project" in metadata:
                typer.echo(f"Project:            {metadata['project']}")
            if "funding" in metadata:
                typer.echo(f"Funding:            {metadata['funding']}")
            
            # Keywords
            if "keywords" in metadata and metadata["keywords"]:
                typer.echo(f"Keywords:           {', '.join(metadata['keywords'])}")
            
            typer.echo()
            
            # Metadata
            typer.echo(f"Created:            {metadata.get('created_at', 'N/A')}")
            typer.echo(f"Updated:            {metadata.get('updated_at', 'N/A')}")
            typer.echo(f"OceanStream Version: {metadata.get('oceanstream_version', 'N/A')}")
            typer.echo()
            
        except Exception as e:
            typer.echo(f"[campaign show] ERROR: {e}")
            raise typer.Exit(code=1)
    
    @campaign_app.command("list")
    def list_campaigns_command(
        verbose: bool = typer.Option(False, "-v", help="Show detailed information for each campaign"),
    ) -> None:
        """List all campaigns.
        
        Example:
        
            oceanstream campaign list
            oceanstream campaign list -v
        """
        from .geotrack.campaign import list_campaigns
        
        try:
            campaigns = list_campaigns()
            
            if not campaigns:
                typer.echo("[campaign list] No campaigns found")
                typer.echo("[campaign list] Create a campaign with: oceanstream campaign create <campaign_id>")
                return
            
            typer.echo(f"[campaign list] Found {len(campaigns)} campaign(s):\n")
            
            if verbose:
                # Detailed view
                for i, campaign in enumerate(campaigns, 1):
                    campaign_id = campaign.get("campaign_id", "unknown")
                    typer.echo(f"{i}. {campaign_id}")
                    
                    if "platform_id" in campaign:
                        typer.echo(f"   Platform:     {campaign['platform_id']}")
                    if "description" in campaign:
                        desc = campaign['description']
                        if len(desc) > 60:
                            desc = desc[:57] + "..."
                        typer.echo(f"   Description:  {desc}")
                    if "start_date" in campaign:
                        typer.echo(f"   Start Date:   {campaign['start_date']}")
                    if "end_date" in campaign:
                        typer.echo(f"   End Date:     {campaign['end_date']}")
                    
                    typer.echo(f"   Created:      {campaign.get('created_at', 'N/A')}")
                    typer.echo(f"   Updated:      {campaign.get('updated_at', 'N/A')}")
                    typer.echo()
            else:
                # Compact view
                for campaign in campaigns:
                    campaign_id = campaign.get("campaign_id", "unknown")
                    platform = campaign.get("platform_id", "N/A")
                    description = campaign.get("description", "")
                    
                    if description and len(description) > 40:
                        description = description[:37] + "..."
                    
                    if description:
                        typer.echo(f"  • {campaign_id:20s} [{platform:15s}] - {description}")
                    else:
                        typer.echo(f"  • {campaign_id:20s} [{platform:15s}]")
                
                typer.echo(f"\nUse 'oceanstream campaign show <campaign_id>' for details")
            
        except Exception as e:
            typer.echo(f"[campaign list] ERROR: {e}")
            raise typer.Exit(code=1)
    
    @campaign_app.command("delete")
    def delete_campaign_command(
        campaign_id: str = typer.Argument(..., help="Campaign identifier to delete"),
        yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompt"),
        verbose: bool = typer.Option(False, "-v", help="Show detailed information"),
    ) -> None:
        """Delete a campaign and its metadata.
        
        WARNING: This only deletes the campaign metadata from ~/.oceanstream/campaigns/.
        It does NOT delete any processed data in your output directories.
        
        Example:
        
            oceanstream campaign delete TEST_CAMPAIGN
            oceanstream campaign delete TEST_CAMPAIGN --yes
        """
        from .geotrack.campaign import delete_campaign, load_campaign_metadata
        
        try:
            # Check if campaign exists
            metadata = load_campaign_metadata(campaign_id)
            if metadata is None:
                typer.echo(f"[campaign delete] ERROR: Campaign '{campaign_id}' not found")
                raise typer.Exit(code=1)
            
            # Confirmation prompt (unless --yes)
            if not yes:
                typer.echo(f"[campaign delete] About to delete campaign: {campaign_id}")
                if "platform_id" in metadata:
                    typer.echo(f"[campaign delete]   Platform: {metadata['platform_id']}")
                if "description" in metadata:
                    typer.echo(f"[campaign delete]   Description: {metadata['description']}")
                typer.echo(f"[campaign delete]")
                typer.echo(f"[campaign delete] WARNING: This will delete campaign metadata from ~/.oceanstream/campaigns/")
                typer.echo(f"[campaign delete]          (Processed data in output directories will NOT be deleted)")
                typer.echo()
                
                confirm = typer.confirm("Are you sure you want to delete this campaign?")
                if not confirm:
                    typer.echo("[campaign delete] Cancelled")
                    raise typer.Exit(code=0)
            
            # Delete the campaign
            delete_campaign(campaign_id, verbose=verbose)
            
            typer.echo(f"[campaign delete] ✓ Campaign '{campaign_id}' deleted successfully")
            
        except Exception as e:
            if "not found" not in str(e).lower():
                typer.echo(f"[campaign delete] ERROR: {e}")
            raise typer.Exit(code=1)
    
    @campaign_app.command("inspect")
    def inspect_campaign_command(
        campaign_id: str = typer.Argument(..., help="Campaign identifier to inspect"),
        output_dir: Path = typer.Option(
            Path("out/geoparquet"),
            help="Base output directory where campaign data is stored",
        ),
        limit: int = typer.Option(10, "--limit", "-n", help="Number of rows to display from GeoParquet"),
        verbose: bool = typer.Option(False, "-v", help="Show detailed information"),
    ) -> None:
        """Inspect processed data for a campaign.
        
        This command displays information about processed campaign data including:
        - GeoParquet dataset preview (first N rows)
        - STAC metadata location
        - PMTiles files (if generated)
        
        Example:
        
            oceanstream campaign inspect FK161229
            oceanstream campaign inspect FK161229 --limit 20
            oceanstream campaign inspect FK161229 --output-dir ./data/processed
        """
        from .geotrack.campaign import inspect_campaign_data, load_campaign_metadata
        
        try:
            # Load campaign metadata
            metadata = load_campaign_metadata(campaign_id)
            if metadata is None:
                typer.echo(f"[campaign inspect] WARNING: No campaign metadata found for '{campaign_id}'")
                typer.echo(f"[campaign inspect]          (Campaign may have been created before metadata tracking)")
            
            # Inspect the data
            info = inspect_campaign_data(campaign_id, output_dir, limit=limit, verbose=verbose)
            
            # Display campaign header
            typer.echo(f"\n[campaign inspect] Campaign: {campaign_id}")
            typer.echo(f"{'=' * 70}")
            
            if metadata:
                if "platform_id" in metadata:
                    typer.echo(f"Platform:         {metadata['platform_id']}")
                if "description" in metadata:
                    desc = metadata['description']
                    if len(desc) > 60:
                        desc = desc[:57] + "..."
                    typer.echo(f"Description:      {desc}")
                typer.echo()
            
            # Display data location
            typer.echo(f"Data Directory:   {info['campaign_dir']}")
            typer.echo()
            
            # GeoParquet information
            if info['has_geoparquet']:
                typer.echo("📊 GeoParquet Dataset")
                typer.echo("-" * 70)
                
                gp_info = info['geoparquet_info']
                if gp_info:
                    typer.echo(f"  Total Rows:     {gp_info['total_rows']:,}")
                    typer.echo(f"  Columns:        {len(gp_info['columns'])}")
                    typer.echo(f"  Memory Usage:   {gp_info['memory_usage_mb']:.2f} MB")
                    typer.echo()
                    
                    # Display sample data as table
                    if info['geoparquet_sample'] is not None:
                        sample = info['geoparquet_sample']
                        typer.echo(f"  First {len(sample)} rows:")
                        typer.echo()
                        
                        # Convert to string with nice formatting
                        import pandas as pd
                        pd.set_option('display.max_columns', None)
                        pd.set_option('display.width', None)
                        pd.set_option('display.max_colwidth', 50)
                        
                        # Format the dataframe
                        table_str = sample.to_string(index=True, max_rows=limit)
                        
                        # Indent each line
                        for line in table_str.split('\n'):
                            typer.echo(f"  {line}")
                        
                        typer.echo()
                        
                        # Show column names for reference
                        typer.echo(f"  Columns: {', '.join(gp_info['columns'][:10])}")
                        if len(gp_info['columns']) > 10:
                            typer.echo(f"           ... and {len(gp_info['columns']) - 10} more")
                        typer.echo()
            else:
                typer.echo("❌ No GeoParquet data found")
                typer.echo()
            
            # STAC metadata
            if info['stac_collection']:
                typer.echo("📋 STAC Metadata")
                typer.echo("-" * 70)
                typer.echo(f"  Collection:     {info['stac_collection']}")
                if info['stac_items']:
                    typer.echo(f"  Items:          {len(info['stac_items'])} file(s) in stac/items/")
                typer.echo()
            else:
                typer.echo("📋 STAC Metadata: Not found")
                typer.echo()
            
            # PMTiles
            if info['pmtiles']:
                typer.echo("🗺️  PMTiles Vector Tiles")
                typer.echo("-" * 70)
                for pmtiles_file in info['pmtiles']:
                    size_mb = pmtiles_file.stat().st_size / 1024 / 1024
                    typer.echo(f"  {pmtiles_file.name:30s} ({size_mb:.2f} MB)")
                typer.echo()
            
            # Summary
            typer.echo("💡 Next Steps:")
            if info['has_geoparquet']:
                typer.echo("  • Load in QGIS: Add Vector Layer → Select GeoParquet files")
                typer.echo("  • Query with DuckDB: SELECT * FROM read_parquet('path/**/*.parquet')")
                if info['stac_collection']:
                    typer.echo(f"  • View STAC: cat {info['stac_collection']}")
            else:
                typer.echo("  • Process data first:")
                typer.echo(f"    oceanstream process geotrack convert --campaign-id {campaign_id} --input-source <data>")
            typer.echo()
            
        except FileNotFoundError as e:
            typer.echo(f"[campaign inspect] ERROR: {e}")
            raise typer.Exit(code=1)
        except Exception as e:
            typer.echo(f"[campaign inspect] ERROR: {e}")
            if verbose:
                import traceback
                traceback.print_exc()
            raise typer.Exit(code=1)
    
    @process_app.callback()
    def process_callback(
        provider: str = typer.Option("saildrone", help="Data provider type (applies to all subcommands)."),
    ) -> None:
        """Global options for all process subcommands."""
        global _provider_obj
        try:
            _provider_obj = get_provider(provider)
        except ValueError as e:
            typer.echo(f"[process] ERROR: {e}")
            raise typer.Exit(code=1)

    # Nested geotrack command group
    geotrack_app = typer.Typer(
        help="Process geotrack data or generate tiles from existing GeoParquet.",
        no_args_is_help=True,  # Show help instead of error when no command provided
    )
    
    @geotrack_app.command(
        "convert",
        help="Convert CSV files into standardized GeoParquet datasets (and optionally PMTiles).",
    )
    def convert_command(
        input_source: Path = typer.Option(
            Path("raw_data"),
            exists=True,
            help="Path to a data file (.csv, .geocsv, .txt NMEA) or directory containing data files. NMEA files are automatically converted to CSV.",
        ),
        output_dir: Path = typer.Option(
            Path("out/geoparquet"),
            help="Base output directory for the partitioned GeoParquet dataset (campaign-based subdirectories will be created).",
        ),
        upload: bool = typer.Option(False, help="Upload processed dataset to cloud storage (future)."),
        verbose: bool = typer.Option(False, "-v", help="Emit detailed progress information."),
        list_columns: bool = typer.Option(False, help="List available columns from the input CSVs and exit."),
        print_schema: bool = typer.Option(False, help="Print the GeoParquet schema (column -> dtype plus partition columns) and exit."),
        provider_metadata: bool = typer.Option(False, help="Print provider metadata snapshot inferred from the data and exit."),
        dry_run: bool = typer.Option(False, "--dry-run", help="Analyze inputs and print derived bin info without writing any files."),
        yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompts."),
        generate_pmtiles: bool = typer.Option(False, "--generate-pmtiles", help="Generate PMTiles vector tiles with track segments and day markers (requires tippecanoe and pmtiles CLI)."),
        pmtiles_minzoom: int = typer.Option(0, help="Minimum zoom level for PMTiles (0-15)."),
        pmtiles_maxzoom: int = typer.Option(10, help="Maximum zoom level for PMTiles (0-15)."),
        pmtiles_layer: str = typer.Option("track", help="Layer name for PMTiles vector tiles."),
        pmtiles_sample_rate: int = typer.Option(5, help="Sample rate for PMTiles: take every Nth point (1=all points, 5=every 5th)."),
        pmtiles_time_gap: int = typer.Option(60, help="Time gap in minutes to split track segments for PMTiles."),
        pmtiles_include_measurements: bool = typer.Option(True, help="Include oceanographic measurements in PMTiles."),
        pmtiles_measurement_columns: list[str] = typer.Option(None, help="Specific measurement columns to include (defaults to auto-selected important ones)."),
        campaign_id: str = typer.Option(None, help="Campaign/cruise identifier (REQUIRED - provide if not auto-detected from filenames/metadata)."),
        platform_id: str = typer.Option(None, help="Platform identifier (overrides auto-detection from filenames)."),
        attribution: str = typer.Option(None, help="Data attribution/citation (overrides provider/file metadata)."),
        creation_date: str = typer.Option(None, help="Data creation date in ISO 8601 format (overrides provider/file metadata)."),
        source_dataset: str = typer.Option(None, help="Source dataset DOI (overrides provider/file metadata)."),
        source_repository: str = typer.Option(None, help="Source repository DOI (overrides provider/file metadata)."),
        force_reprocess: bool = typer.Option(False, "--force-reprocess", help="Clear previous metadata and reprocess all files from scratch."),
        nmea_sentence_types: list[str] = typer.Option(None, help="NMEA sentence types to process (e.g., GGA,RMC). If not specified, processes all supported types (GGA,RMC,GNS,VTG,ZDA). Only applies to .txt NMEA files."),
        nmea_sampling_interval: float = typer.Option(None, help="NMEA sampling interval in seconds (e.g., 10.0 = 1 point per 10 seconds). If not specified, keeps all data points. Only applies to .txt NMEA files."),
    ) -> None:
        global _provider_obj
        provider_obj = _provider_obj
        if provider_obj is None:
            typer.echo("[geotrack] ERROR: Provider not initialized")
            raise typer.Exit(code=1)
        
        if not provider_obj.supports_module("geotrack"):
            typer.echo(f"[geotrack] ERROR: Provider '{provider_obj.name}' does not support geotrack processing")
            raise typer.Exit(code=1)
        
        try:
            geotrack.convert(
                provider=provider_obj,
                input_source=input_source,
                output_dir=output_dir,
                verbose=verbose,
                list_columns=list_columns,
                print_schema=print_schema,
                provider_metadata=provider_metadata,
                dry_run=dry_run,
                upload=upload,
                yes=yes,
                generate_pmtiles=generate_pmtiles,
                pmtiles_minzoom=pmtiles_minzoom,
                pmtiles_maxzoom=pmtiles_maxzoom,
                pmtiles_layer=pmtiles_layer,
                pmtiles_sample_rate=pmtiles_sample_rate,
                pmtiles_time_gap=pmtiles_time_gap,
                pmtiles_include_measurements=pmtiles_include_measurements,
                pmtiles_measurement_columns=pmtiles_measurement_columns,
                campaign_id=campaign_id,
                platform_id=platform_id,
                attribution=attribution,
                creation_date=creation_date,
                source_dataset=source_dataset,
                source_repository=source_repository,
                force_reprocess=force_reprocess,
                nmea_sentence_types=nmea_sentence_types,
                nmea_sampling_interval=nmea_sampling_interval,
            )
        except FileNotFoundError as e:
            typer.echo(f"[geotrack] ERROR: {e}")
            raise typer.Exit(code=1)
        except ValueError as e:
            typer.echo(f"[geotrack] ERROR: {e}")
            raise typer.Exit(code=1)
        except Exception as e:
            typer.echo(f"[geotrack] ERROR: {e}")
            raise typer.Exit(code=1)
    
    @geotrack_app.command(
        "tiles",
        help="Generate PMTiles from an existing GeoParquet dataset.",
    )
    def tiles_command(
        geoparquet_dir: Path = typer.Option(
            ...,
            exists=True,
            file_okay=False,
            help="Directory containing GeoParquet dataset.",
        ),
        output_dir: Path = typer.Option(
            None,
            help="Output directory for PMTiles (default: <geoparquet_dir>/../tiles).",
        ),
        verbose: bool = typer.Option(False, "-v", help="Emit detailed progress information."),
        minzoom: int = typer.Option(0, help="Minimum zoom level for PMTiles (0-15)."),
        maxzoom: int = typer.Option(10, help="Maximum zoom level for PMTiles (0-15)."),
        layer_name: str = typer.Option("track", help="Layer name for PMTiles vector tiles."),
        sample_rate: int = typer.Option(5, help="Sample rate: take every Nth point (1=all points, 5=every 5th)."),
        time_gap_minutes: int = typer.Option(60, help="Time gap in minutes to split track segments."),
        include_measurements: bool = typer.Option(True, help="Include oceanographic measurements in tiles."),
        measurement_columns: list[str] = typer.Option(None, help="Specific measurement columns to include (defaults to auto-selected important ones)."),
    ) -> None:
        global _provider_obj
        provider_obj = _provider_obj
        if provider_obj is None:
            typer.echo("[geotrack] ERROR: Provider not initialized")
            raise typer.Exit(code=1)
        
        if not provider_obj.supports_module("geotrack"):
            typer.echo(f"[geotrack] ERROR: Provider '{provider_obj.name}' does not support geotrack processing")
            raise typer.Exit(code=1)
        
        try:
            result = geotrack.generate_tiles(
                geoparquet_dir=geoparquet_dir,
                output_dir=output_dir,
                provider=provider_obj,
                verbose=verbose,
                minzoom=minzoom,
                maxzoom=maxzoom,
                layer_name=layer_name,
                sample_rate=sample_rate,
                time_gap_minutes=time_gap_minutes,
                include_measurements=include_measurements,
                measurement_columns=measurement_columns,
            )
            if result is None:
                typer.echo("[geotrack] ERROR: PMTiles generation failed")
                raise typer.Exit(code=1)
        except FileNotFoundError as e:
            typer.echo(f"[geotrack] ERROR: {e}")
            raise typer.Exit(code=1)
        except ValueError as e:
            typer.echo(f"[geotrack] ERROR: {e}")
            raise typer.Exit(code=1)
        except Exception as e:
            typer.echo(f"[geotrack] ERROR: {e}")
            raise typer.Exit(code=1)
    
    # Register nested geotrack commands
    process_app.add_typer(geotrack_app, name="geotrack")

    @process_app.command(
        "echodata",
        help=(
            "Process raw echosounder data (EK60/EK80) into Zarr using echopype."
        ),
    )
    def echodata_command(
        input_dir: Path = typer.Option(Path("raw_echodata"), exists=True, file_okay=False, help="Directory containing raw echosounder files (EK60/EK80)."),
        output_dir: Path = typer.Option(Path("out/echodata"), help="Output directory for processed Zarr dataset."),
        verbose: bool = typer.Option(False, "-v", help="Emit progress information."),
        upload: bool = typer.Option(False, help="Upload processed data to cloud storage after conversion (future)."),
        dry_run: bool = typer.Option(False, "--dry-run", help="Show planned actions without executing."),
    ) -> None:
        global _provider_obj
        provider_obj = _provider_obj
        if provider_obj is None:
            typer.echo("[echodata] ERROR: Provider not initialized")
            raise typer.Exit(code=1)
        
        echodata.process(
            provider=provider_obj,
            input_dir=input_dir,
            output_dir=output_dir,
            verbose=verbose,
            dry_run=dry_run,
        )

    @process_app.command(
        "multibeam",
        help=(
            "Process raw multibeam backscatter data using MB-System."
        ),
    )
    def multibeam_command(
        input_dir: Path = typer.Option(Path("raw_multibeam"), exists=True, file_okay=False, help="Directory with raw multibeam backscatter data."),
        output_dir: Path = typer.Option(Path("out/multibeam"), help="Output directory for processed multibeam products."),
        verbose: bool = typer.Option(False, "-v", help="Emit progress information."),
        upload: bool = typer.Option(False, help="Upload processed data to cloud storage after conversion (future)."),
        dry_run: bool = typer.Option(False, "--dry-run", help="Show planned actions without executing."),
    ) -> None:
        global _provider_obj
        provider_obj = _provider_obj
        if provider_obj is None:
            typer.echo("[multibeam] ERROR: Provider not initialized")
            raise typer.Exit(code=1)
        
        multibeam.process(
            provider=provider_obj,
            input_dir=input_dir,
            output_dir=output_dir,
            verbose=verbose,
            dry_run=dry_run,
        )

    @process_app.command(
        "adcp",
        help=(
            "Process raw ADCP data (format-specific pipeline TBD)."
        ),
    )
    def adcp_command(
        input_dir: Path = typer.Option(Path("raw_adcp"), exists=True, file_okay=False, help="Directory with raw ADCP data."),
        output_dir: Path = typer.Option(Path("out/adcp"), help="Output directory for processed ADCP products."),
        verbose: bool = typer.Option(False, "-v", help="Emit progress information."),
        upload: bool = typer.Option(False, help="Upload processed data to cloud storage after conversion (future)."),
        dry_run: bool = typer.Option(False, "--dry-run", help="Show planned actions without executing."),
    ) -> None:
        global _provider_obj
        provider_obj = _provider_obj
        if provider_obj is None:
            typer.echo("[adcp] ERROR: Provider not initialized")
            raise typer.Exit(code=1)
        
        adcp.process(
            provider=provider_obj,
            input_dir=input_dir,
            output_dir=output_dir,
            verbose=verbose,
            dry_run=dry_run,
        )


def main() -> None:
    """Entry point that runs the Typer app."""
    if app is None:
        raise RuntimeError("Typer is required for the CLI. Please install the 'typer' extra/dependency.")
    app()


if __name__ == "__main__":  # pragma: no cover - manual invocation
    main()
