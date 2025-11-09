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


app = typer.Typer(help="Oceanstream data processing CLI (process oceanographic & acoustic data).") if typer else None
process_app = typer.Typer(help="Process raw measurement data into standardized outputs.") if typer else None

# Global state for provider (set by process callback, used by subcommands)
_provider_obj = None


if typer:
    # Register nested app for 'process'
    app.add_typer(process_app, name="process")
    
    @app.command("providers")
    def providers_command() -> None:
        """List all available data providers."""
        available = list_providers()
        typer.echo("Available providers:")
        for p in available:
            typer.echo(f"  - {p}")
    
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
    geotrack_app = typer.Typer(help="Process geotrack data or generate tiles from existing GeoParquet.")
    
    @geotrack_app.command(
        "convert",
        help="Convert CSV files into standardized GeoParquet datasets (and optionally PMTiles).",
    )
    def convert_command(
        input_source: Path = typer.Option(
            Path("raw_data"),
            exists=True,
            help="Path to a CSV file or directory containing CSV files (default: ./raw_data).",
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
