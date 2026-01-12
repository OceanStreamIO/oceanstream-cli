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
        platform: list[str] = typer.Option(
            None,
            "--platform",
            help="Platform specification as 'id:name:type' (e.g., 'sd1030:Saildrone 1030:Saildrone Explorer'). "
                 "Can be specified multiple times for multi-platform campaigns. "
                 "Name and type are optional: 'sd1030' or 'sd1030:Saildrone 1030' are valid.",
        ),
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
        
        Example (single platform - new style):
        
            oceanstream campaign create FK161229 \\
                --platform "falkor:Research Vessel Falkor:Research Vessel"
        
        Example (multi-platform campaign):
        
            oceanstream campaign create TPOS_2023 \\
                --platform "sd1030:Saildrone 1030:Saildrone Explorer" \\
                --platform "sd1033:Saildrone 1033:Saildrone Explorer" \\
                --platform "sd1079:Saildrone 1079:Saildrone Explorer" \\
                --description "TPOS 2023 multi-platform deployment"
        """
        from .geotrack.campaign import create_campaign
        
        # Initialize platform variables (used in interactive mode, but need to exist for CLI mode too)
        platform_id = None
        platform_name = None
        platform_type = None
        
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
        
        # Parse platforms from new --platform option
        platforms_list = []
        if platform:
            for p in platform:
                parts = p.split(":", 2)  # Split on first two colons only
                p_id = parts[0].strip()
                p_name = parts[1].strip() if len(parts) > 1 and parts[1].strip() else None
                p_type = parts[2].strip() if len(parts) > 2 and parts[2].strip() else None
                
                if not p_id:
                    typer.echo(f"[campaign create] ERROR: Platform ID is required in --platform '{p}'")
                    raise typer.Exit(code=1)
                
                platform_dict = {"id": p_id}
                if p_name:
                    platform_dict["name"] = p_name
                if p_type:
                    platform_dict["type"] = p_type
                platforms_list.append(platform_dict)
        
        # Create campaign metadata dict
        metadata = {
            'campaign_id': campaign_id,
            'output_dir': output_dir,
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
        
        # Handle platforms - from --platform CLI option or interactive mode
        if platforms_list:
            # From --platform CLI option(s)
            metadata['platforms'] = platforms_list
            # Also set legacy fields for backward compatibility (first platform)
            metadata['platform_id'] = platforms_list[0]['id']
            if 'name' in platforms_list[0]:
                metadata['platform_name'] = platforms_list[0]['name']
            if 'type' in platforms_list[0]:
                metadata['platform_type'] = platforms_list[0]['type']
        elif platform_id:
            # From interactive mode - convert to platforms array
            platform_dict = {"id": platform_id}
            if platform_name:
                platform_dict["name"] = platform_name
            if platform_type:
                platform_dict["type"] = platform_type
            metadata['platforms'] = [platform_dict]
            # Also set legacy fields
            metadata['platform_id'] = platform_id
            if platform_name:
                metadata['platform_name'] = platform_name
            if platform_type:
                metadata['platform_type'] = platform_type
        
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
            if platforms_list:
                if len(platforms_list) == 1:
                    typer.echo(f"[campaign create]   Platform: {platforms_list[0]['id']}")
                else:
                    typer.echo(f"[campaign create]   Platforms: {len(platforms_list)}")
                    for p in platforms_list:
                        typer.echo(f"[campaign create]     - {p['id']}")
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
            
            # Platforms (new multi-platform format)
            if "platforms" in metadata and metadata["platforms"]:
                platforms = metadata["platforms"]
                if len(platforms) == 1:
                    p = platforms[0]
                    typer.echo(f"Platform ID:        {p.get('id', 'N/A')}")
                    if 'name' in p:
                        typer.echo(f"Platform Name:      {p['name']}")
                    if 'type' in p:
                        typer.echo(f"Platform Type:      {p['type']}")
                    if 'row_count' in p:
                        typer.echo(f"Row Count:          {p['row_count']:,}")
                else:
                    typer.echo(f"Platforms:          {len(platforms)}")
                    for i, p in enumerate(platforms, 1):
                        typer.echo(f"  [{i}] {p.get('id', 'unknown')}")
                        if 'name' in p:
                            typer.echo(f"      Name: {p['name']}")
                        if 'type' in p:
                            typer.echo(f"      Type: {p['type']}")
                        if 'row_count' in p:
                            typer.echo(f"      Rows: {p['row_count']:,}")
            # Legacy single-platform fields (backward compatibility)
            elif "platform_id" in metadata:
                typer.echo(f"Platform ID:        {metadata['platform_id']}")
                if "platform_name" in metadata:
                    typer.echo(f"Platform Name:      {metadata['platform_name']}")
                if "platform_type" in metadata:
                    typer.echo(f"Platform Type:      {metadata['platform_type']}")
            
            typer.echo()
            
            # Data statistics (if available from processing)
            if "total_rows" in metadata:
                typer.echo(f"Total Rows:         {metadata['total_rows']:,}")
            if "total_files" in metadata:
                typer.echo(f"Total Files:        {metadata['total_files']}")
            
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
            
            # Sensors (if available from processing)
            if "sensors" in metadata and metadata["sensors"]:
                typer.echo()
                typer.echo(f"Sensors:            {len(metadata['sensors'])}")
                for sensor in metadata["sensors"][:5]:  # Show first 5
                    typer.echo(f"  - {sensor.get('name', sensor.get('id', 'unknown'))}")
                if len(metadata["sensors"]) > 5:
                    typer.echo(f"  ... and {len(metadata['sensors']) - 5} more")
            
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
                    
                    # Show platforms (new format) or legacy platform_id
                    if "platforms" in campaign and campaign["platforms"]:
                        platforms = campaign["platforms"]
                        if len(platforms) == 1:
                            typer.echo(f"   Platform:     {platforms[0].get('id', 'N/A')}")
                        else:
                            typer.echo(f"   Platforms:    {len(platforms)} ({', '.join(p.get('id', '?') for p in platforms)})")
                    elif "platform_id" in campaign:
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
                    if "total_rows" in campaign:
                        typer.echo(f"   Total Rows:   {campaign['total_rows']:,}")
                    
                    typer.echo(f"   Created:      {campaign.get('created_at', 'N/A')}")
                    typer.echo(f"   Updated:      {campaign.get('updated_at', 'N/A')}")
                    typer.echo()
            else:
                # Compact view
                for campaign in campaigns:
                    campaign_id = campaign.get("campaign_id", "unknown")
                    
                    # Build platform display string
                    if "platforms" in campaign and campaign["platforms"]:
                        platforms = campaign["platforms"]
                        if len(platforms) == 1:
                            platform_str = platforms[0].get('id', 'N/A')
                        else:
                            platform_str = f"{len(platforms)} platforms"
                    else:
                        platform_str = campaign.get("platform_id", "N/A")
                    
                    description = campaign.get("description", "")
                    
                    if description and len(description) > 40:
                        description = description[:37] + "..."
                    
                    if description:
                        typer.echo(f"  • {campaign_id} [{platform_str}] - {description}")
                    else:
                        typer.echo(f"  • {campaign_id} [{platform_str}]")
                
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
                
                # Show platforms (new format) or legacy platform_id
                if "platforms" in metadata and metadata["platforms"]:
                    platforms = metadata["platforms"]
                    if len(platforms) == 1:
                        typer.echo(f"[campaign delete]   Platform: {platforms[0].get('id', 'N/A')}")
                    else:
                        typer.echo(f"[campaign delete]   Platforms: {len(platforms)} ({', '.join(p.get('id', '?') for p in platforms)})")
                elif "platform_id" in metadata:
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
                # Show platforms (new format) or legacy platform_id
                if "platforms" in metadata and metadata["platforms"]:
                    platforms = metadata["platforms"]
                    if len(platforms) == 1:
                        typer.echo(f"Platform:         {platforms[0].get('id', 'N/A')}")
                    else:
                        typer.echo(f"Platforms:        {len(platforms)}")
                        for p in platforms:
                            row_info = f" ({p['row_count']:,} rows)" if 'row_count' in p else ""
                            typer.echo(f"  - {p.get('id', 'unknown')}{row_info}")
                elif "platform_id" in metadata:
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

    # ============================================================================
    # Echodata Processing Commands (EK60/EK80 echosounders)
    # ============================================================================
    
    echodata_app = typer.Typer(
        help="Process echosounder data (EK60/EK80) into Zarr with STAC metadata.",
        no_args_is_help=True,
    )
    
    @echodata_app.command(
        "convert",
        help="Convert raw echosounder files to EchoData Zarr format.",
    )
    def echodata_convert_command(
        input_source: Path = typer.Option(
            ...,
            "--input-source",
            exists=True,
            help="Path to raw .raw file or directory containing raw files.",
        ),
        output_dir: Path = typer.Option(
            Path("out/echodata"),
            "--output-dir", "-o",
            help="Output directory for Zarr stores.",
        ),
        campaign_id: str = typer.Option(
            None,
            "--campaign-id",
            help="Campaign identifier for organizing outputs.",
        ),
        sonar_model: str = typer.Option(
            "EK80",
            "--sonar-model",
            help="Echosounder model: EK80 or EK60.",
        ),
        calibration_file: Optional[Path] = typer.Option(
            None,
            "--calibration-file",
            exists=True,
            help="Path to calibration file (.xlsx, .ecs, .json).",
        ),
        compute_sv: bool = typer.Option(
            False,
            "--compute-sv",
            help="Compute Sv after conversion.",
        ),
        enrich_environment: bool = typer.Option(
            False,
            "--enrich-environment",
            help="Enrich with environmental data from geoparquet (or Copernicus fallback).",
        ),
        geoparquet_dir: Optional[Path] = typer.Option(
            None,
            "--geoparquet-dir",
            exists=True,
            help="Path to geoparquet campaign directory for environment enrichment.",
        ),
        parallel: bool = typer.Option(
            True,
            "--parallel/--no-parallel",
            help="Enable parallel processing with Dask.",
        ),
        workers: int = typer.Option(
            4,
            "--workers",
            help="Number of Dask workers for parallel processing.",
        ),
        verbose: bool = typer.Option(False, "-v", help="Emit detailed progress."),
        dry_run: bool = typer.Option(False, "--dry-run", help="Show planned actions without executing."),
    ) -> None:
        """Convert raw EK60/EK80 files to EchoData Zarr format.
        
        Example:
            oceanstream process echodata convert \\
                --input-source ./raw_data/saildrone-ek80-raw \\
                --output-dir ./out/echodata \\
                --campaign-id TPOS2023 \\
                --sonar-model EK80 \\
                --calibration-file ./calibration_values.xlsx
        """
        from oceanstream.echodata.convert import convert_raw_file, convert_raw_files
        from oceanstream.echodata.calibrate import apply_calibration
        
        input_source = Path(input_source)
        output_dir = Path(output_dir)
        
        # Discover raw files
        if input_source.is_file():
            raw_files = [input_source]
        else:
            raw_files = sorted(input_source.glob("*.raw"))
        
        if not raw_files:
            typer.echo(f"[echodata] No .raw files found in {input_source}")
            raise typer.Exit(code=1)
        
        if verbose:
            typer.echo(f"[echodata] Found {len(raw_files)} raw files")
            for f in raw_files[:5]:
                typer.echo(f"  - {f.name}")
            if len(raw_files) > 5:
                typer.echo(f"  ... and {len(raw_files) - 5} more")
        
        if dry_run:
            typer.echo("\n[echodata] Dry Run Summary")
            typer.echo("─" * 40)
            typer.echo(f"Input:           {input_source}")
            typer.echo(f"Output:          {output_dir}")
            typer.echo(f"Campaign:        {campaign_id or '(auto)'}")
            typer.echo(f"Sonar model:     {sonar_model}")
            typer.echo(f"Calibration:     {calibration_file or '(none)'}")
            typer.echo(f"Raw files:       {len(raw_files)}")
            typer.echo(f"Compute Sv:      {compute_sv}")
            typer.echo(f"Enrich env:      {enrich_environment}")
            typer.echo(f"Parallel:        {parallel} ({workers} workers)")
            return
        
        # Setup output directory
        if campaign_id:
            output_dir = output_dir / campaign_id / "echodata" / "raw"
        output_dir.mkdir(parents=True, exist_ok=True)
        
        try:
            # Convert files
            if verbose:
                typer.echo(f"\n[echodata] Converting {len(raw_files)} files...")
            
            zarr_paths = convert_raw_files(
                raw_files,
                output_dir,
                sonar_model=sonar_model,
                parallel=parallel,
                n_workers=workers,
            )
            
            if verbose:
                typer.echo(f"[echodata] Converted {len(zarr_paths)} files to Zarr")
            
            # Apply calibration
            if calibration_file:
                if verbose:
                    typer.echo(f"[echodata] Applying calibration from {calibration_file}")
                
                from oceanstream.echodata.convert import open_converted
                
                for zarr_path in zarr_paths:
                    ed = open_converted(zarr_path)
                    ed = apply_calibration(ed, calibration_file)
                    ed.to_zarr(zarr_path, overwrite=True)
            
            # Enrich environment
            if enrich_environment and geoparquet_dir:
                if verbose:
                    typer.echo(f"[echodata] Enriching with environmental data from {geoparquet_dir}")
                
                from oceanstream.echodata.environment import enrich_environment as enrich_env
                
                for zarr_path in zarr_paths:
                    enrich_env(zarr_path, geoparquet_dir)
            
            typer.echo(f"\n[echodata] ✓ Conversion complete: {len(zarr_paths)} files")
            typer.echo(f"  Output: {output_dir}")
            
        except ImportError as e:
            typer.echo(f"[echodata] ERROR: {e}")
            typer.echo("\nInstall echodata dependencies:")
            typer.echo('  pip install "oceanstream[echodata]"')
            typer.echo("  pip install git+https://github.com/OceanStreamIO/echopype-dev.git@oceanstream-iotedge")
            raise typer.Exit(code=1)
        except Exception as e:
            typer.echo(f"[echodata] ERROR: {e}")
            raise typer.Exit(code=1)
    
    @echodata_app.command(
        "compute-sv",
        help="Compute Sv (Volume Backscattering Strength) from EchoData.",
    )
    def echodata_compute_sv_command(
        input_source: Path = typer.Option(
            ...,
            "--input-source",
            exists=True,
            help="Path to EchoData Zarr store or directory.",
        ),
        output_dir: Path = typer.Option(
            None,
            "--output-dir", "-o",
            help="Output directory for Sv Zarr (default: alongside input).",
        ),
        add_depth: bool = typer.Option(True, help="Add depth coordinate."),
        add_location: bool = typer.Option(True, help="Add lat/lon coordinates."),
        verbose: bool = typer.Option(False, "-v", help="Emit detailed progress."),
    ) -> None:
        """Compute Sv from calibrated EchoData.
        
        Example:
            oceanstream process echodata compute-sv \\
                --input-source ./out/echodata/TPOS2023/raw \\
                --output-dir ./out/echodata/TPOS2023/sv
        """
        from oceanstream.echodata.compute import compute_sv
        
        input_source = Path(input_source)
        
        # Find Zarr stores
        if input_source.suffix == ".zarr":
            zarr_paths = [input_source]
        else:
            zarr_paths = sorted(input_source.glob("*.zarr"))
        
        if not zarr_paths:
            typer.echo(f"[echodata] No .zarr stores found in {input_source}")
            raise typer.Exit(code=1)
        
        if verbose:
            typer.echo(f"[echodata] Computing Sv for {len(zarr_paths)} datasets")
        
        for zarr_path in zarr_paths:
            sv_output = output_dir / f"{zarr_path.stem}_Sv.zarr" if output_dir else zarr_path.with_suffix(".Sv.zarr")
            
            if verbose:
                typer.echo(f"  {zarr_path.name} -> {sv_output.name}")
            
            compute_sv(
                zarr_path,
                output_path=sv_output,
                add_depth=add_depth,
                add_location=add_location,
            )
        
        typer.echo(f"[echodata] ✓ Computed Sv for {len(zarr_paths)} datasets")
    
    @echodata_app.command(
        "compute-mvbs",
        help="Compute MVBS (Mean Volume Backscattering Strength).",
    )
    def echodata_compute_mvbs_command(
        input_source: Path = typer.Option(
            ...,
            "--input-source",
            exists=True,
            help="Path to Sv Zarr store or directory.",
        ),
        output_dir: Path = typer.Option(
            None,
            "--output-dir", "-o",
            help="Output directory for MVBS Zarr.",
        ),
        range_bin: str = typer.Option("1m", help="Vertical bin size (e.g., 1m, 5m)."),
        ping_time_bin: str = typer.Option("5s", help="Temporal bin size (e.g., 5s, 10s)."),
        verbose: bool = typer.Option(False, "-v", help="Emit detailed progress."),
    ) -> None:
        """Compute MVBS from Sv data.
        
        Example:
            oceanstream process echodata compute-mvbs \\
                --input-source ./out/echodata/TPOS2023/sv \\
                --range-bin 1m --ping-time-bin 5s
        """
        from oceanstream.echodata.compute import compute_mvbs
        import xarray as xr
        
        input_source = Path(input_source)
        
        # Find Sv Zarr stores
        if input_source.suffix == ".zarr":
            zarr_paths = [input_source]
        else:
            zarr_paths = sorted(input_source.glob("*_Sv.zarr")) or sorted(input_source.glob("*.zarr"))
        
        if not zarr_paths:
            typer.echo(f"[echodata] No Sv .zarr stores found in {input_source}")
            raise typer.Exit(code=1)
        
        if verbose:
            typer.echo(f"[echodata] Computing MVBS for {len(zarr_paths)} datasets")
            typer.echo(f"  range_bin={range_bin}, ping_time_bin={ping_time_bin}")
        
        for zarr_path in zarr_paths:
            sv_ds = xr.open_zarr(zarr_path)
            mvbs_output = output_dir / f"{zarr_path.stem}_mvbs.zarr" if output_dir else zarr_path.parent / f"{zarr_path.stem}_mvbs.zarr"
            
            if verbose:
                typer.echo(f"  {zarr_path.name} -> {mvbs_output.name}")
            
            compute_mvbs(
                sv_ds,
                range_bin=range_bin,
                ping_time_bin=ping_time_bin,
                output_path=mvbs_output,
            )
        
        typer.echo(f"[echodata] ✓ Computed MVBS for {len(zarr_paths)} datasets")
    
    @echodata_app.command(
        "denoise",
        help="Apply denoising pipeline to Sv data.",
    )
    def echodata_denoise_command(
        input_source: Path = typer.Option(
            ...,
            "--input-source",
            exists=True,
            help="Path to Sv Zarr store or directory.",
        ),
        output_dir: Path = typer.Option(
            None,
            "--output-dir", "-o",
            help="Output directory for denoised Zarr.",
        ),
        methods: str = typer.Option(
            "background,transient,impulse,attenuation",
            help="Comma-separated denoising methods.",
        ),
        verbose: bool = typer.Option(False, "-v", help="Emit detailed progress."),
    ) -> None:
        """Apply denoising to Sv data.
        
        Methods: background, transient, impulse, attenuation
        
        Example:
            oceanstream process echodata denoise \\
                --input-source ./out/echodata/TPOS2023/sv \\
                --methods background,impulse
        """
        from oceanstream.echodata.denoise import apply_denoising
        from oceanstream.echodata.config import DenoiseConfig
        
        input_source = Path(input_source)
        method_list = [m.strip() for m in methods.split(",")]
        
        # Find Sv Zarr stores
        if input_source.suffix == ".zarr":
            zarr_paths = [input_source]
        else:
            zarr_paths = sorted(input_source.glob("*_Sv.zarr")) or sorted(input_source.glob("*.zarr"))
        
        if not zarr_paths:
            typer.echo(f"[echodata] No Sv .zarr stores found in {input_source}")
            raise typer.Exit(code=1)
        
        if verbose:
            typer.echo(f"[echodata] Applying denoising to {len(zarr_paths)} datasets")
            typer.echo(f"  methods: {method_list}")
        
        config = DenoiseConfig(methods=method_list)
        
        for zarr_path in zarr_paths:
            denoised_output = output_dir / f"{zarr_path.stem}_denoised.zarr" if output_dir else zarr_path.parent / f"{zarr_path.stem}_denoised.zarr"
            
            if verbose:
                typer.echo(f"  {zarr_path.name} -> {denoised_output.name}")
            
            apply_denoising(
                zarr_path,
                methods=method_list,
                config=config,
                output_path=denoised_output,
            )
        
        typer.echo(f"[echodata] ✓ Denoised {len(zarr_paths)} datasets")
    
    @echodata_app.command(
        "compute-nasc",
        help="Compute NASC (Nautical Area Scattering Coefficient).",
    )
    def echodata_compute_nasc_command(
        input_source: Path = typer.Option(
            ...,
            "--input-source",
            exists=True,
            help="Path to Sv Zarr store or directory.",
        ),
        output_dir: Path = typer.Option(
            None,
            "--output-dir", "-o",
            help="Output directory for NASC Zarr.",
        ),
        range_bin: str = typer.Option("10m", help="Vertical bin size (e.g., 10m, 20m)."),
        dist_bin: str = typer.Option("0.5nmi", help="Distance bin size (e.g., 0.5nmi, 1nmi)."),
        transducer_depth: float = typer.Option(
            0.0,
            "--transducer-depth",
            help="Transducer depth below surface in meters (e.g., 0.6 for Saildrone).",
        ),
        verbose: bool = typer.Option(False, "-v", help="Emit detailed progress."),
    ) -> None:
        """Compute NASC from Sv data.
        
        NASC integrates acoustic backscatter over depth layers and
        horizontal distance, providing a measure of acoustic biomass
        per unit area (m² per nautical mile²).
        
        Requirements:
        - Sv dataset must have latitude/longitude (use enrich-location first)
        - Sv dataset must have echo_range or depth
        - Sv dataset must have frequency_nominal
        
        Example:
            # First enrich with location data
            oceanstream process echodata enrich-location \\
                --input-source ./sv/TPOS2023_Sv.zarr \\
                --campaign-id TPOS2023
            
            # Then compute NASC
            oceanstream process echodata compute-nasc \\
                --input-source ./sv/TPOS2023_Sv.zarr \\
                --range-bin 10m --dist-bin 0.5nmi \\
                --transducer-depth 0.6
        """
        from oceanstream.echodata.compute import compute_nasc
        import xarray as xr
        
        input_source = Path(input_source)
        
        # Find Sv Zarr stores
        if input_source.suffix == ".zarr":
            zarr_paths = [input_source]
        else:
            zarr_paths = sorted(input_source.glob("*_Sv.zarr")) or sorted(input_source.glob("*.zarr"))
        
        if not zarr_paths:
            typer.echo(f"[echodata] No Sv .zarr stores found in {input_source}")
            raise typer.Exit(code=1)
        
        if verbose:
            typer.echo(f"[echodata] Computing NASC for {len(zarr_paths)} datasets")
            typer.echo(f"  range_bin={range_bin}, dist_bin={dist_bin}, transducer_depth={transducer_depth}m")
        
        if output_dir:
            output_dir = Path(output_dir)
            output_dir.mkdir(parents=True, exist_ok=True)
        
        for zarr_path in zarr_paths:
            sv_ds = xr.open_zarr(zarr_path)
            nasc_output = output_dir / f"{zarr_path.stem}_nasc.zarr" if output_dir else zarr_path.parent / f"{zarr_path.stem}_nasc.zarr"
            
            if verbose:
                typer.echo(f"  {zarr_path.name} -> {nasc_output.name}")
            
            try:
                compute_nasc(
                    sv_ds,
                    range_bin=range_bin,
                    dist_bin=dist_bin,
                    transducer_depth=transducer_depth,
                    output_path=nasc_output,
                )
            except ValueError as e:
                typer.echo(f"[echodata] ⚠️ Failed to compute NASC for {zarr_path.name}: {e}")
                typer.echo("  Hint: Use 'enrich-location' command first if missing lat/lon")
                continue
        
        typer.echo(f"[echodata] ✓ Computed NASC for {len(zarr_paths)} datasets")

    @echodata_app.command(
        "enrich-location",
        help="Enrich Sv dataset with GPS location data from geoparquet.",
    )
    def echodata_enrich_location_command(
        input_source: Path = typer.Option(
            ...,
            "--input-source",
            exists=True,
            help="Path to Sv Zarr store or directory.",
        ),
        campaign_dir: Path = typer.Option(
            None,
            "--campaign-dir",
            help="Path to geoparquet campaign directory with GPS data.",
        ),
        campaign_id: str = typer.Option(
            None,
            "--campaign-id",
            help="Campaign ID to look up in ~/.oceanstream/campaigns/.",
        ),
        geoparquet_url: str = typer.Option(
            None,
            "--geoparquet-url",
            help="Cloud URL to geoparquet file (az://, s3://, gs://, https://).",
        ),
        time_col: str = typer.Option(
            "time",
            "--time-col",
            help="Column name for time in geoparquet (used with --geoparquet-url).",
        ),
        lat_col: str = typer.Option(
            "latitude",
            "--lat-col",
            help="Column name for latitude in geoparquet (used with --geoparquet-url).",
        ),
        lon_col: str = typer.Option(
            "longitude",
            "--lon-col",
            help="Column name for longitude in geoparquet (used with --geoparquet-url).",
        ),
        output_dir: Path = typer.Option(
            None,
            "--output-dir", "-o",
            help="Output directory for enriched Sv Zarr (default: overwrites input).",
        ),
        verbose: bool = typer.Option(False, "-v", help="Emit detailed progress."),
    ) -> None:
        """Enrich Sv dataset with GPS coordinates from geoparquet.
        
        This command adds latitude/longitude coordinates to an Sv dataset
        by interpolating from a campaign's geoparquet trajectory data.
        This is required for NASC computation which needs location data
        for distance calculations.
        
        Similar to how environment enrichment adds sound speed and absorption
        from geoparquet CTD data, this enriches Sv with GPS data.
        
        Supports three source modes:
        
        1. **Campaign directory** - local path to campaign with geoparquet
        2. **Campaign ID** - looks up path from ~/.oceanstream/campaigns/
        3. **Geoparquet URL** - cloud-native source with custom column mappings
        
        Examples:
            # Using campaign directory path
            oceanstream process echodata enrich-location \\
                --input-source ./out/sv/TPOS2023_Sv.zarr \\
                --campaign-dir ./campaigns/TPOS2023
            
            # Using campaign ID (looks up path from ~/.oceanstream/campaigns/)
            oceanstream process echodata enrich-location \\
                --input-source ./out/sv/TPOS2023_Sv.zarr \\
                --campaign-id TPOS2023
            
            # Using cloud geoparquet with custom column names
            oceanstream process echodata enrich-location \\
                --input-source ./out/sv/cruise_Sv.zarr \\
                --geoparquet-url az://container/path/to/nav.parquet \\
                --time-col iso_time \\
                --lat-col ship_latitude \\
                --lon-col ship_longitude
        """
        from oceanstream.echodata.environment import (
            enrich_sv_with_location,
            enrich_sv_with_location_from_url,
        )
        import xarray as xr
        
        # Count how many source options were provided
        sources = [campaign_dir, campaign_id, geoparquet_url]
        source_count = sum(1 for s in sources if s is not None)
        
        if source_count == 0:
            typer.echo("[echodata] Error: Must provide one of: --campaign-dir, --campaign-id, or --geoparquet-url")
            raise typer.Exit(code=1)
        
        if source_count > 1:
            typer.echo("[echodata] Error: Provide only one of: --campaign-dir, --campaign-id, or --geoparquet-url")
            raise typer.Exit(code=1)
        
        input_source = Path(input_source)
        
        # Find Sv Zarr stores
        if input_source.suffix == ".zarr":
            zarr_paths = [input_source]
        else:
            zarr_paths = sorted(input_source.glob("*_Sv.zarr")) or sorted(input_source.glob("*.zarr"))
        
        if not zarr_paths:
            typer.echo(f"[echodata] No Sv .zarr stores found in {input_source}")
            raise typer.Exit(code=1)
        
        # Describe the source
        if geoparquet_url:
            source_desc = geoparquet_url
        elif campaign_id:
            source_desc = f"campaign:{campaign_id}"
        else:
            source_desc = str(campaign_dir)
            
        if verbose:
            typer.echo(f"[echodata] Enriching location for {len(zarr_paths)} datasets from {source_desc}")
            if geoparquet_url:
                typer.echo(f"  Column mappings: time={time_col}, lat={lat_col}, lon={lon_col}")
        
        for zarr_path in zarr_paths:
            sv_ds = xr.open_zarr(zarr_path)
            
            # Determine output path
            if output_dir:
                output_path = Path(output_dir) / zarr_path.name
                output_path.parent.mkdir(parents=True, exist_ok=True)
            else:
                output_path = zarr_path  # Overwrite
            
            if verbose:
                typer.echo(f"  {zarr_path.name} -> {output_path}")
            
            try:
                if geoparquet_url:
                    sv_enriched = enrich_sv_with_location_from_url(
                        sv_ds,
                        url=geoparquet_url,
                        time_col=time_col,
                        lat_col=lat_col,
                        lon_col=lon_col,
                    )
                else:
                    sv_enriched = enrich_sv_with_location(
                        sv_ds,
                        campaign_dir=campaign_dir,
                        campaign_id=campaign_id,
                    )
                sv_enriched.to_zarr(output_path, mode="w")
                
                if verbose:
                    lat_range = f"[{float(sv_enriched.latitude.min()):.3f}, {float(sv_enriched.latitude.max()):.3f}]"
                    lon_range = f"[{float(sv_enriched.longitude.min()):.3f}, {float(sv_enriched.longitude.max()):.3f}]"
                    typer.echo(f"    Added: lat={lat_range}, lon={lon_range}")
            except Exception as e:
                typer.echo(f"[echodata] ⚠️ Failed to enrich {zarr_path.name}: {e}")
        
        typer.echo(f"[echodata] ✓ Enriched location for {len(zarr_paths)} datasets")

    @echodata_app.command(
        "plot",
        help="Generate echogram visualizations from Sv data.",
    )
    def echodata_plot_command(
        input_source: Path = typer.Option(
            ...,
            "--input-source",
            exists=True,
            help="Path to Sv Zarr store or directory.",
        ),
        output_dir: Path = typer.Option(
            None,
            "--output-dir", "-o",
            help="Output directory for echogram images.",
        ),
        channels: str = typer.Option(
            None,
            "--channels",
            help="Comma-separated channel indices to plot (default: all).",
        ),
        cmap: str = typer.Option("ocean_r", help="Matplotlib colormap."),
        vmin: float = typer.Option(-80.0, help="Minimum Sv value for color scale (dB)."),
        vmax: float = typer.Option(-50.0, help="Maximum Sv value for color scale (dB)."),
        dpi: int = typer.Option(180, help="Output image resolution."),
        file_base_name: str = typer.Option(None, help="Base name for output files."),
        verbose: bool = typer.Option(False, "-v", help="Emit detailed progress."),
    ) -> None:
        """Generate echogram PNG visualizations from Sv data.
        
        Produces publication-quality echogram plots for each channel,
        with configurable color scale, resolution, and colormap.
        
        Example:
            oceanstream process echodata plot \\
                --input-source ./out/echodata/TPOS2023/sv \\
                --output-dir ./echograms \\
                --vmin -80 --vmax -50 --cmap ocean_r
        """
        from oceanstream.echodata.plot import generate_echograms
        import xarray as xr
        
        input_source = Path(input_source)
        
        # Find Sv Zarr stores
        if input_source.suffix == ".zarr":
            zarr_paths = [input_source]
        else:
            zarr_paths = sorted(input_source.glob("*_Sv.zarr")) or sorted(input_source.glob("*.zarr"))
        
        if not zarr_paths:
            typer.echo(f"[echodata] No Sv .zarr stores found in {input_source}")
            raise typer.Exit(code=1)
        
        # Parse channels
        channel_list = None
        if channels:
            channel_list = [int(c.strip()) for c in channels.split(",")]
        
        if verbose:
            typer.echo(f"[echodata] Generating echograms for {len(zarr_paths)} datasets")
            typer.echo(f"  channels: {channel_list or 'all'}")
            typer.echo(f"  cmap={cmap}, vmin={vmin}, vmax={vmax}, dpi={dpi}")
        
        all_echograms = []
        for zarr_path in zarr_paths:
            sv_ds = xr.open_zarr(zarr_path)
            
            # Determine output directory
            if output_dir:
                plot_output_dir = Path(output_dir)
            else:
                plot_output_dir = zarr_path.parent / "echograms"
            plot_output_dir.mkdir(parents=True, exist_ok=True)
            
            # Determine base name
            base_name = file_base_name or zarr_path.stem.replace("_Sv", "")
            
            if verbose:
                typer.echo(f"  {zarr_path.name} -> {plot_output_dir}/")
            
            echogram_files = generate_echograms(
                sv_ds,
                output_dir=plot_output_dir,
                file_base_name=base_name,
                channels=channel_list,
                cmap=cmap,
                vmin=vmin,
                vmax=vmax,
                dpi=dpi,
            )
            all_echograms.extend(echogram_files)
            
            if verbose:
                for ef in echogram_files:
                    typer.echo(f"    Created: {ef.name}")
        
        typer.echo(f"[echodata] ✓ Generated {len(all_echograms)} echograms")
    
    # Register echodata app
    process_app.add_typer(echodata_app, name="echodata")

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
