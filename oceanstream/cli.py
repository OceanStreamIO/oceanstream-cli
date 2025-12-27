from __future__ import annotations
import os
import time
from time import perf_counter
import sys
from pathlib import Path
from typing import Any, Optional
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
    # Global callback to handle --config-file option
    @app.callback()
    def main_callback(
        config_file: Optional[Path] = typer.Option(
            None,
            "--config-file",
            "-c",
            help="Path to configuration file (default: ./oceanstream.toml if exists)",
            exists=True,
            dir_okay=False,
        ),
    ) -> None:
        """OceanStream - Process oceanographic and acoustic data."""
        if config_file:
            # Load configuration from specified file
            from .configuration import get_config
            try:
                config = get_config(config_file)
                # Config is now loaded and will be used by Settings
            except Exception as e:
                typer.echo(f"Error loading configuration from {config_file}: {e}", err=True)
                raise typer.Exit(code=1)
    
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
        campaign_id: str = typer.Argument(None, help="Campaign/cruise identifier (e.g., FK161229, SD1030_2023). If omitted, interactive mode is used."),
        output_dir: str = typer.Option(None, "--output-dir", "-o", help="Default output path for processed data. Local path or cloud URI (az://container/path, s3://bucket/path)."),
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
        
        If no campaign_id is provided, an interactive wizard guides you through
        all available fields with help text and examples.
        
        Example (interactive wizard):
        
            oceanstream campaign create
        
        Example (minimal CLI):
        
            oceanstream campaign create FK161229
        
        Example (with cloud output):
        
            oceanstream campaign create FK161229 --output-dir az://mycontainer/campaigns
        
        Example (with metadata):
        
            oceanstream campaign create FK161229 \\
                --output-dir az://mycontainer/campaigns \\
                --platform-id "R/V Falkor" \\
                --platform-name "Research Vessel Falkor" \\
                --description "Hydrothermal vent study in the Pacific" \\
                --start-date 2016-12-29 \\
                --end-date 2017-01-20 \\
                --attribution "Schmidt Ocean Institute"
        """
        from .geotrack.campaign import create_campaign
        
        # Interactive mode: prompt for all fields when no campaign_id provided
        if campaign_id is None:
            typer.echo()
            typer.echo(typer.style("◆ Create Campaign", bold=True))
            typer.echo(typer.style("  Press Enter to skip optional fields", dim=True))
            typer.echo()
            
            # Required field
            campaign_id = typer.prompt(
                typer.style("  Campaign ID", bold=True),
                prompt_suffix=typer.style(" (e.g., FK161229) ", dim=True) + ": ",
            )
            
            # Output
            typer.echo()
            typer.echo(typer.style("◇ Output", bold=True))
            output_dir_input = typer.prompt(
                "  Output directory",
                default="",
                prompt_suffix=typer.style(" (path or az://...) ", dim=True) + ": ",
                show_default=False,
            )
            output_dir = output_dir_input if output_dir_input else None
            
            # Platform
            typer.echo()
            typer.echo(typer.style("◇ Platform", bold=True))
            platform_id_input = typer.prompt(
                "  Platform ID",
                default="",
                prompt_suffix=typer.style(" (e.g., sd1030, FK) ", dim=True) + ": ",
                show_default=False,
            )
            platform_id = platform_id_input if platform_id_input else None
            
            platform_name_input = typer.prompt(
                "  Platform name",
                default="",
                prompt_suffix=typer.style(" (e.g., R/V Falkor) ", dim=True) + ": ",
                show_default=False,
            )
            platform_name = platform_name_input if platform_name_input else None
            
            typer.echo(typer.style("  Type: 1=USV  2=AUV  3=Research Vessel  4=Buoy/Mooring  5=Shore Station  6=Other", dim=True))
            platform_type_input = typer.prompt(
                "  Platform type",
                default="",
                prompt_suffix=": ",
                show_default=False,
            )
            if platform_type_input:
                type_map = {"1": "USV", "2": "AUV", "3": "Research Vessel", "4": "Buoy/Mooring", "5": "Shore Station"}
                platform_type = type_map.get(platform_type_input, platform_type_input if platform_type_input != "6" else None)
                if platform_type_input == "6":
                    platform_type = typer.prompt("  Custom type", default="") or None
            
            # Details
            typer.echo()
            typer.echo(typer.style("◇ Details", bold=True))
            description_input = typer.prompt(
                "  Description",
                default="",
                prompt_suffix=": ",
                show_default=False,
            )
            description = description_input if description_input else None
            
            start_date_input = typer.prompt(
                "  Start date",
                default="",
                prompt_suffix=typer.style(" (YYYY-MM-DD) ", dim=True) + ": ",
                show_default=False,
            )
            start_date = start_date_input if start_date_input else None
            
            end_date_input = typer.prompt(
                "  End date",
                default="",
                prompt_suffix=typer.style(" (YYYY-MM-DD) ", dim=True) + ": ",
                show_default=False,
            )
            end_date = end_date_input if end_date_input else None
            
            bbox_input = typer.prompt(
                "  Bounding box",
                default="",
                prompt_suffix=typer.style(" (minlon,minlat,maxlon,maxlat) ", dim=True) + ": ",
                show_default=False,
            )
            bbox = bbox_input if bbox_input else None
            
            # Attribution
            typer.echo()
            typer.echo(typer.style("◇ Attribution", bold=True))
            attribution_input = typer.prompt(
                "  Attribution",
                default="",
                prompt_suffix=": ",
                show_default=False,
            )
            attribution = attribution_input if attribution_input else None
            
            typer.echo(typer.style("  License: 1=CC-BY-4.0  2=CC0  3=MIT  4=Other", dim=True))
            license_input = typer.prompt(
                "  License",
                default="",
                prompt_suffix=": ",
                show_default=False,
            )
            if license_input:
                license_map = {"1": "CC-BY-4.0", "2": "CC0", "3": "MIT"}
                license = license_map.get(license_input, license_input if license_input != "4" else None)
                if license_input == "4":
                    license = typer.prompt("  Custom license", default="") or None
            
            doi_input = typer.prompt(
                "  DOI",
                default="",
                prompt_suffix=typer.style(" (e.g., 10.1234/example) ", dim=True) + ": ",
                show_default=False,
            )
            doi = doi_input if doi_input else None
            
            source_repository_input = typer.prompt(
                "  Source repository",
                default="",
                prompt_suffix=typer.style(" (URL) ", dim=True) + ": ",
                show_default=False,
            )
            source_repository = source_repository_input if source_repository_input else None
            
            # Team
            typer.echo()
            typer.echo(typer.style("◇ Team & Project", bold=True))
            chief_scientist_input = typer.prompt(
                "  Chief scientist",
                default="",
                prompt_suffix=": ",
                show_default=False,
            )
            chief_scientist = chief_scientist_input if chief_scientist_input else None
            
            institution_input = typer.prompt(
                "  Institution",
                default="",
                prompt_suffix=": ",
                show_default=False,
            )
            institution = institution_input if institution_input else None
            
            project_input = typer.prompt(
                "  Project",
                default="",
                prompt_suffix=": ",
                show_default=False,
            )
            project = project_input if project_input else None
            
            funding_input = typer.prompt(
                "  Funding",
                default="",
                prompt_suffix=": ",
                show_default=False,
            )
            funding = funding_input if funding_input else None
            
            keywords_input = typer.prompt(
                "  Keywords",
                default="",
                prompt_suffix=typer.style(" (comma-separated) ", dim=True) + ": ",
                show_default=False,
            )
            keywords = keywords_input if keywords_input else None
            
            typer.echo()
        
        # Validate campaign_id is provided
        if not campaign_id:
            typer.echo("[campaign create] ERROR: campaign_id is required")
            typer.echo("  Use: oceanstream campaign create <campaign_id>")
            typer.echo("  Or:  oceanstream campaign create --interactive")
            raise typer.Exit(code=1)
        
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
            'output_dir': output_dir,
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
            if output_dir:
                typer.echo(f"[campaign create]   Output directory: {output_dir}")
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
            
            if verbose:  # pragma: no cover
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
                        typer.echo(f"  • {campaign_id} [{platform}] - {description}")
                    else:
                        typer.echo(f"  • {campaign_id} [{platform}]")
                
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
            if verbose:  # pragma: no cover
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
            help="Path to a data file (.csv, .geocsv, .txt NMEA, .hex CTD) or directory/archive. NMEA and SeaBird CTD files are automatically converted.",
        ),
        output_dir: str = typer.Option(
            "out/geoparquet",
            help="Output path for GeoParquet. Local path or cloud URI (az://container/path, s3://bucket/path). Campaign subdirectories are auto-created.",
        ),
        provider: str = typer.Option(None, "--provider", help="Data provider type (overrides global --provider setting). Available: saildrone, r2r."),
        upload: bool = typer.Option(False, "--upload", help="(Deprecated) Use --use-cloud-storage instead."),
        use_cloud_storage: bool = typer.Option(False, "--use-cloud-storage", help="Write output to configured cloud storage (requires 'oceanstream storage add' first)."),
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
        pmtiles_measurement_columns: list[str] = typer.Option(None, help="Specific measurement columns to include (None = auto-discover from data)."),
        pmtiles_exclude_patterns: list[str] = typer.Option(None, help="Regex patterns to exclude when auto-discovering columns (e.g., '.*_STDDEV$'). Defaults exclude _STDDEV, _MIN, _MAX, _PEAK suffixes. Pass empty list to include all."),
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
        
        # Allow provider override at command level
        if provider is not None:
            try:
                provider_obj = get_provider(provider)
                if verbose:  # pragma: no cover
                    typer.echo(f"[geotrack] Using provider override: {provider}")
            except ValueError as e:
                typer.echo(f"[geotrack] ERROR: Invalid provider '{provider}': {e}")
                raise typer.Exit(code=1)
        else:
            provider_obj = _provider_obj
            
        if provider_obj is None:
            typer.echo("[geotrack] ERROR: Provider not initialized")
            raise typer.Exit(code=1)
        
        if not provider_obj.supports_module("geotrack"):
            typer.echo(f"[geotrack] ERROR: Provider '{provider_obj.name}' does not support geotrack processing")
            raise typer.Exit(code=1)
        
        # Check if we should use output_dir from campaign metadata
        effective_output_dir = output_dir
        if campaign_id and output_dir == "out/geoparquet":
            # User didn't override output_dir, check campaign metadata
            from .geotrack.campaign import load_campaign_metadata
            campaign_meta = load_campaign_metadata(campaign_id)
            if campaign_meta and campaign_meta.get("output_dir"):
                effective_output_dir = campaign_meta["output_dir"]
                if verbose:
                    typer.echo(f"[geotrack] Using output_dir from campaign metadata: {effective_output_dir}")
        
        try:
            geotrack.convert(
                provider=provider_obj,
                input_source=input_source,
                output_dir=effective_output_dir,
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
                pmtiles_exclude_patterns=pmtiles_exclude_patterns,
                campaign_id=campaign_id,
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
    
    @geotrack_app.command(
        "report",
        help="Generate a processing report from an existing GeoParquet dataset.",
    )
    def report_command(
        dataset_path: Optional[Path] = typer.Argument(
            None,
            help="Path to the GeoParquet dataset directory. If not provided, uses --campaign-id to look up the path.",
        ),
        output: Path = typer.Option(
            None,
            "--output", "-o",
            help="Output file path (default: prints to stdout).",
        ),
        output_format: str = typer.Option(
            "markdown",
            "--format", "-f",
            help="Output format: 'markdown' or 'json'.",
        ),
        campaign_id: str = typer.Option(
            None,
            "--campaign-id", "-c",
            help="Campaign ID to look up dataset path from registered campaigns, or to use in the report title.",
        ),
        verbose: bool = typer.Option(False, "-v", help="Emit detailed progress information."),
    ) -> None:
        """Generate a comprehensive report from a GeoParquet dataset.
        
        Analyzes the dataset and produces a report with:
        - Dataset statistics (rows, columns, size)
        - Temporal and spatial extent
        - Platform breakdown
        - Detected sensors (from STAC metadata)
        - Oceanographic and meteorological measurement statistics
        - Column categories
        - Usage examples
        
        The dataset path can be provided directly, or inferred from --campaign-id
        by looking up the registered campaign's output_directory.
        
        Examples:
        
            # Using dataset path directly
            oceanstream process geotrack report ./out/tpos_2023
            
            # Using campaign ID (looks up path from ~/.oceanstream/campaigns/)
            oceanstream process geotrack report --campaign-id tpos_2023
            
            # Save to file
            oceanstream process geotrack report ./out/tpos_2023 -o report.md
            
            # JSON output
            oceanstream process geotrack report -c tpos_2023 -f json -o report.json
        """
        from .geotrack.report import generate_report
        from .geotrack.campaign import load_campaign_metadata
        
        # Resolve dataset path
        resolved_path = dataset_path
        resolved_campaign_id = campaign_id
        
        if resolved_path is None:
            # Try to get path from campaign metadata
            if campaign_id is None:
                typer.echo("[report] ERROR: Either dataset_path or --campaign-id must be provided.")
                raise typer.Exit(code=1)
            
            metadata = load_campaign_metadata(campaign_id)
            if metadata is None:
                typer.echo(f"[report] ERROR: Campaign '{campaign_id}' not found in ~/.oceanstream/campaigns/")
                typer.echo("[report] Use 'oceanstream campaign list' to see registered campaigns.")
                raise typer.Exit(code=1)
            
            output_dir = metadata.get("output_directory")
            if not output_dir:
                typer.echo(f"[report] ERROR: Campaign '{campaign_id}' has no output_directory registered.")
                raise typer.Exit(code=1)
            
            resolved_path = Path(output_dir)
            if verbose:
                typer.echo(f"[report] Using dataset path from campaign '{campaign_id}': {resolved_path}")
        
        # Validate path exists
        if not resolved_path.exists():
            typer.echo(f"[report] ERROR: Dataset path does not exist: {resolved_path}")
            raise typer.Exit(code=1)
        
        if not resolved_path.is_dir():
            typer.echo(f"[report] ERROR: Dataset path is not a directory: {resolved_path}")
            raise typer.Exit(code=1)
        
        if output_format not in ("markdown", "json"):
            typer.echo(f"[report] ERROR: Invalid format '{output_format}'. Use 'markdown' or 'json'.")
            raise typer.Exit(code=1)
        
        try:
            result = generate_report(
                dataset_path=resolved_path,
                output_path=output,
                output_format=output_format,
                campaign_id=resolved_campaign_id,
                verbose=verbose,
            )
            
            # If no output file specified, print to stdout
            if output is None:
                if output_format == "json":
                    import json
                    typer.echo(json.dumps(result, indent=2, default=str))
                else:
                    typer.echo(result)
            else:
                typer.echo(f"[report] ✓ Report written to: {output}")
                
        except FileNotFoundError as e:
            typer.echo(f"[report] ERROR: {e}")
            raise typer.Exit(code=1)
        except ValueError as e:
            typer.echo(f"[report] ERROR: {e}")
            raise typer.Exit(code=1)
        except Exception as e:
            typer.echo(f"[report] ERROR: {e}")
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

    # ============================================================================
    # Configure Command - Interactive Configuration Wizard
    # ============================================================================
    
    @app.command("configure")
    def configure_command() -> None:
        """Interactive configuration wizard for OceanStream.
        
        Configure storage providers and other settings. Credentials are encrypted
        and stored in ~/.oceanstream/storage.json.
        
        If configuration exists, current values will be shown as defaults.
        
        Example:
            oceanstream configure
        """
        from oceanstream.storage.manager import (
            load_storage_configuration,
            add_azure_storage,
            add_local_storage,
            get_storage_config_path,
        )
        
        typer.echo()
        typer.echo("═" * 70)
        typer.echo("  🔧  OceanStream Configuration Wizard")
        typer.echo("═" * 70)
        typer.echo()
        
        # Load existing configuration if available
        existing_config = None
        try:
            existing_config = load_storage_configuration()
            active_name, active_config = existing_config.get_active_config()
            typer.echo(f"📋 Current configuration found: {active_config.provider}")
            typer.echo()
        except (FileNotFoundError, ValueError):
            typer.echo("📋 No existing configuration found.")
            typer.echo()
        
        # Storage Provider Selection
        typer.echo("━" * 70)
        typer.echo("  📦 Storage Configuration")
        typer.echo("━" * 70)
        typer.echo()
        typer.echo("Select storage provider:")
        typer.echo()
        typer.echo("  1. 🏠  Local Filesystem (default)")
        typer.echo("  2. ☁️   Azure Blob Storage")
        typer.echo("  3. 📁  AWS S3 (coming soon)")
        typer.echo("  4. 🌐  Google Cloud Storage (coming soon)")
        typer.echo()
        
        # Determine default choice based on existing config
        default_choice = "1"
        if existing_config:
            _, active = existing_config.get_active_config()
            if active.provider == "azure":
                default_choice = "2"
            elif active.provider == "s3":
                default_choice = "3"
            elif active.provider == "gcs":
                default_choice = "4"
        
        choice = typer.prompt("Select provider", default=default_choice)
        
        if choice == "1":
            provider = "local"
        elif choice == "2":
            provider = "azure"
        elif choice in ["3", "4"]:
            typer.echo()
            typer.echo("⚠️  This provider is not yet implemented.")
            typer.echo("   Currently supported: local, azure")
            raise typer.Exit(code=1)
        else:
            typer.echo("❌ Invalid selection")
            raise typer.Exit(code=1)
        
        typer.echo()
        typer.echo(f"🔧 Configuring {provider.upper()} storage...")
        typer.echo()
        
        try:
            if provider == "local":
                # Local storage configuration
                typer.echo("📁 Local filesystem storage")
                typer.echo()
                
                # Get default from existing config if available
                default_path = ""
                if existing_config:
                    try:
                        _, active = existing_config.get_active_config()
                        if active.provider == "local" and hasattr(active, "base_path"):
                            default_path = str(active.base_path) if active.base_path else ""
                    except (ValueError, AttributeError):
                        pass
                
                base_path_str = typer.prompt(
                    "Base path for output",
                    default=default_path if default_path else ".",
                )
                
                base_path = Path(base_path_str) if base_path_str and base_path_str != "." else None
                
                add_local_storage(
                    base_path=base_path,
                )
            
            elif provider == "azure":
                # Azure Blob Storage configuration
                typer.echo("☁️  Azure Blob Storage configuration")
                typer.echo()
                typer.echo("You can provide either:")
                typer.echo("  • Connection string (recommended), OR")
                typer.echo("  • Account name + Account key")
                typer.echo()
                
                # Get defaults from existing config if available
                default_container = ""
                default_account_name = ""
                has_existing = False
                
                if existing_config:
                    try:
                        _, active = existing_config.get_active_config()
                        if active.provider == "azure":
                            has_existing = True
                            if hasattr(active, "container_name"):
                                default_container = active.container_name
                            if hasattr(active, "account_name") and active.account_name:
                                default_account_name = active.account_name
                    except (ValueError, AttributeError):
                        pass
                
                use_connection_string = typer.confirm(
                    "Use connection string?",
                    default=True,
                )
                
                if use_connection_string:
                    if has_existing:
                        typer.echo("(Current connection string is encrypted - enter new one or press Enter to keep existing)")
                    connection_string_input = typer.prompt(
                        "Azure Storage connection string",
                        default="" if has_existing else ...,
                        show_default=False,
                        hide_input=True,
                    )
                    # If empty and has existing, keep existing (don't update)
                    connection_string = connection_string_input if connection_string_input else None
                    account_name = None
                    account_key = None
                else:
                    connection_string = None
                    account_name = typer.prompt(
                        "Azure Storage account name",
                        default=default_account_name if default_account_name else ...,
                    )
                    if has_existing:
                        typer.echo("(Current account key is encrypted - enter new one or press Enter to keep existing)")
                    account_key_input = typer.prompt(
                        "Azure Storage account key",
                        default="" if has_existing else ...,
                        show_default=False,
                        hide_input=True,
                    )
                    account_key = account_key_input if account_key_input else None
                
                container_name = typer.prompt(
                    "Container name",
                    default=default_container if default_container else ...,
                )
                
                # If we have existing config and user didn't provide new credentials, reload them
                if has_existing and (not connection_string and not account_key):
                    _, active = existing_config.get_active_config()
                    if use_connection_string:
                        connection_string = active.connection_string
                    else:
                        account_key = active.account_key
                
                add_azure_storage(
                    container_name=container_name,
                    connection_string=connection_string,
                    account_name=account_name,
                    access_key=account_key,
                )
            
            # Success message
            config_path = get_storage_config_path()
            typer.echo()
            typer.echo("✅ Configuration saved successfully!")
            typer.echo()
            typer.echo(f"   Provider: {provider}")
            typer.echo(f"   Config file: {config_path}")
            typer.echo()
            
        except ValueError as e:
            typer.echo()
            typer.echo(f"❌ Configuration error: {e}")
            raise typer.Exit(code=1)
        except Exception as e:
            typer.echo()
            typer.echo(f"❌ Unexpected error: {e}")
            raise typer.Exit(code=1)


def main() -> None:
    """Entry point that runs the Typer app."""
    if app is None:
        raise RuntimeError("Typer is required for the CLI. Please install the 'typer' extra/dependency.")
    app()


if __name__ == "__main__":  # pragma: no cover - manual invocation
    main()
