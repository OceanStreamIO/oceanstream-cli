"""Read-only information tools — providers, instruments, capabilities."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..server import mcp_app


@mcp_app.tool()
def list_providers() -> list[dict[str, Any]]:
    """List all registered data providers with their supported processing modules.

    Returns details about each provider including name, supported modules
    (geotrack, echodata, multibeam, adcp), and whether it represents
    a stationary platform.
    """
    from oceanstream.providers import list_providers as _list_providers, get_provider

    results = []
    for name in _list_providers():
        provider = get_provider(name)
        results.append({
            "name": name,
            "supported_modules": list(getattr(provider, "supported_modules", [])),
            "is_stationary": getattr(provider, "is_stationary", False),
        })
    return results


@mcp_app.tool()
def list_instruments() -> list[dict[str, Any]]:
    """List all supported oceanographic instruments/sensors from the catalogue.

    Returns a summary of each instrument including ID, name, manufacturer,
    sensor type, and measured variables.
    """
    from oceanstream.sensors import get_sensor_catalogue

    catalogue = get_sensor_catalogue()
    results = []
    for sensor in catalogue.list_all():
        results.append({
            "id": sensor.id,
            "name": sensor.name,
            "manufacturer": sensor.manufacturer,
            "model": sensor.model,
            "sensor_type": sensor.sensor_type.value,
            "variables": sensor.variables,
            "description": sensor.description,
        })
    return results


@mcp_app.tool()
def get_instrument_details(sensor_id: str) -> dict[str, Any]:
    """Get full details for a specific instrument/sensor by its ID.

    Args:
        sensor_id: Sensor identifier (e.g. 'sbe37-odo', 'nortek-ad2cp', 'airmar-150wx')
    """
    from oceanstream.sensors import get_sensor_catalogue

    catalogue = get_sensor_catalogue()
    sensor = catalogue.get(sensor_id)
    if sensor is None:
        available = [s.id for s in catalogue.list_all()]
        return {"error": f"Sensor '{sensor_id}' not found. Available: {available}"}

    return {
        "id": sensor.id,
        "name": sensor.name,
        "manufacturer": sensor.manufacturer,
        "model": sensor.model,
        "sensor_type": sensor.sensor_type.value,
        "description": sensor.description,
        "variables": sensor.variables,
        "specifications": sensor.specifications,
        "documentation_url": sensor.documentation_url,
        "typical_depth": sensor.typical_depth,
        "typical_mount": sensor.typical_mount,
    }


@mcp_app.tool()
def detect_provider(file_path: str) -> dict[str, Any]:
    """Auto-detect which data provider matches a given file.

    Reads the file header and scores all registered providers to find the best match.

    Args:
        file_path: Path to the data file to analyze
    """
    from oceanstream.providers.factory import detect_provider as _detect_provider

    path = Path(file_path)
    if not path.exists():
        return {"error": f"File not found: {file_path}"}

    try:
        provider = _detect_provider(path)
        return {
            "provider": provider.name,
            "is_stationary": getattr(provider, "is_stationary", False),
            "supported_modules": list(getattr(provider, "supported_modules", [])),
        }
    except Exception as e:
        return {"error": f"Detection failed: {e}"}


@mcp_app.tool()
def detect_sensors(variables: list[str]) -> list[dict[str, str]]:
    """Detect which instruments/sensors are likely present based on variable names.

    Pass a list of column names or variable names from a dataset, and this tool
    identifies which sensors probably produced the data.

    Args:
        variables: List of variable/column names from the dataset
    """
    from oceanstream.sensors import get_sensor_catalogue

    catalogue = get_sensor_catalogue()
    detected = catalogue.detect_sensors(set(variables))
    return [
        {
            "id": s.id,
            "name": s.name,
            "sensor_type": s.sensor_type.value,
            "matched_variables": [v for v in s.variables if v in set(variables)],
        }
        for s in detected
    ]


@mcp_app.tool()
def list_processing_commands() -> list[dict[str, Any]]:
    """List all available processing commands with their parameters.

    Returns a structured list of processing pipelines (geotrack, echodata, adcp,
    multibeam) and their sub-commands with parameter descriptions.
    """
    return [
        {
            "module": "geotrack",
            "description": "Process GPS/navigation track data into GeoParquet format",
            "commands": [
                {
                    "name": "convert",
                    "description": "Convert CSV/NMEA/CTD files to GeoParquet with STAC metadata",
                    "parameters": {
                        "input_source": "Path to input file or directory",
                        "output_dir": "Output directory (default: ./output/)",
                        "provider": "Data provider name (auto-detected if not specified)",
                        "campaign_id": "Campaign identifier",
                        "generate_pmtiles": "Generate PMTiles vector tiles (default: true)",
                        "generate_heatmaps": "Generate COG heatmap rasters (default: true)",
                        "force_reprocess": "Force reprocessing even if output exists",
                    },
                },
                {
                    "name": "generate_tiles",
                    "description": "Generate PMTiles from existing GeoParquet output",
                    "parameters": {
                        "input_source": "Path to GeoParquet file or directory",
                        "output_dir": "Output directory for PMTiles",
                    },
                },
                {
                    "name": "generate_heatmaps",
                    "description": "Generate COG heatmap rasters from GeoParquet",
                    "parameters": {
                        "input_source": "Path to GeoParquet file or directory",
                        "output_dir": "Output directory for heatmaps",
                        "resolution": "Grid resolution in degrees (default: 0.05)",
                    },
                },
                {
                    "name": "generate_report",
                    "description": "Generate a processing report for a campaign",
                    "parameters": {
                        "input_source": "Path to processed output directory",
                        "output_dir": "Output directory for the report",
                    },
                },
            ],
        },
        {
            "module": "echodata",
            "description": "Process echosounder data (EK60/EK80/AZFP raw files)",
            "commands": [
                {
                    "name": "convert",
                    "description": "Convert raw sonar files to EchoData Zarr format",
                    "parameters": {
                        "input_source": "Path to raw file(s) or directory",
                        "output_dir": "Output directory for Zarr stores",
                        "sonar_model": "Sonar model (EK60, EK80, AZFP, AD2CP)",
                    },
                },
                {
                    "name": "compute_sv",
                    "description": "Compute volume backscattering strength (Sv)",
                    "parameters": {
                        "input_source": "Path to converted EchoData Zarr",
                        "output_dir": "Output directory",
                    },
                },
                {
                    "name": "denoise",
                    "description": "Apply denoising cascade (impulse, transient, background, attenuation, frequency differencing)",
                    "parameters": {
                        "input_source": "Path to Sv Zarr dataset",
                        "output_dir": "Output directory",
                        "config": "Path to denoise config TOML (optional)",
                    },
                },
                {
                    "name": "compute_mvbs",
                    "description": "Compute Mean Volume Backscattering Strength",
                    "parameters": {
                        "input_source": "Path to (denoised) Sv dataset",
                        "output_dir": "Output directory",
                        "range_bin": "Range bin size in meters (default: 5)",
                        "ping_bin": "Ping time bin size (default: 20s)",
                    },
                },
                {
                    "name": "compute_nasc",
                    "description": "Compute Nautical Area Scattering Coefficient",
                    "parameters": {
                        "input_source": "Path to Sv dataset",
                        "output_dir": "Output directory",
                    },
                },
                {
                    "name": "enrich_location",
                    "description": "Add GPS/location data to echodata",
                    "parameters": {
                        "input_source": "Path to echodata Zarr",
                        "gps_file": "Path to GPS data file (NMEA or CSV)",
                        "output_dir": "Output directory",
                    },
                },
                {
                    "name": "seabed_mask",
                    "description": "Apply seabed detection and masking",
                    "parameters": {
                        "input_source": "Path to Sv dataset",
                        "output_dir": "Output directory",
                    },
                },
                {
                    "name": "plot",
                    "description": "Generate echogram plots",
                    "parameters": {
                        "input_source": "Path to Sv or MVBS dataset",
                        "output_dir": "Output directory for PNG images",
                    },
                },
                {
                    "name": "run_pipeline",
                    "description": "Run full multi-step echodata pipeline (convert → Sv → denoise → MVBS/NASC → echograms)",
                    "parameters": {
                        "input_source": "Path to raw file(s) or directory",
                        "output_dir": "Output directory",
                        "sonar_model": "Sonar model (EK60, EK80, AZFP, AD2CP)",
                        "skip_denoise": "Skip denoising step (default: false)",
                        "skip_mvbs": "Skip MVBS computation (default: false)",
                        "skip_nasc": "Skip NASC computation (default: false)",
                    },
                },
            ],
        },
        {
            "module": "adcp",
            "description": "Process Acoustic Doppler Current Profiler data",
            "commands": [
                {
                    "name": "process",
                    "description": "Process ADCP data files (RDI .raw or Nortek .ad2cp)",
                    "parameters": {
                        "input_source": "Path to ADCP data file(s)",
                        "output_dir": "Output directory",
                        "instrument_type": "Instrument type: rdi or ad2cp (auto-detected)",
                    },
                },
            ],
        },
        {
            "module": "multibeam",
            "description": "Process multibeam sonar data (planned — not yet implemented)",
            "commands": [],
        },
    ]


@mcp_app.tool()
def get_supported_file_formats() -> dict[str, Any]:
    """Return supported input file formats for each processing module.

    Shows which file extensions and formats each module can process.
    """
    return {
        "geotrack": {
            "extensions": [".csv", ".txt", ".nmea", ".hex", ".cnv", ".tar.gz", ".tgz"],
            "formats": {
                "csv": "Comma-separated values (auto-detect columns)",
                "nmea": "NMEA 0183 sentences (GGA, RMC, VTG, ZDA)",
                "hex": "Sea-Bird CTD raw hex data (requires .hdr + .XMLCON companion files)",
                "cnv": "Sea-Bird processed CTD data",
                "tar.gz": "Compressed archive (extracts and processes contents)",
            },
        },
        "echodata": {
            "extensions": [".raw", ".zarr"],
            "formats": {
                "raw": "Raw echosounder data (EK60, EK80, AZFP, AD2CP)",
                "zarr": "Zarr store (intermediate/converted format)",
            },
            "sonar_models": ["EK60", "EK80", "AZFP", "AZFP6", "AD2CP"],
        },
        "adcp": {
            "extensions": [".raw", ".000", ".ad2cp"],
            "formats": {
                "raw/.000": "RDI (Teledyne) binary ADCP data",
                "ad2cp": "Nortek AD2CP binary data",
            },
        },
        "multibeam": {
            "extensions": [],
            "formats": {},
            "status": "Not yet implemented",
        },
    }
