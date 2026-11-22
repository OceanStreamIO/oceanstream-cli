"""MCP Resources — static and dynamic data resources."""

from __future__ import annotations

from typing import Any

from .server import mcp_app

mcp = mcp_app


@mcp.resource("oceanstream://version")
def get_version() -> str:
    """OceanStream library version and installed extras."""
    import importlib.util
    from oceanstream import __version__

    extras = {
        "geotrack": importlib.util.find_spec("geopandas") is not None,
        "echodata": importlib.util.find_spec("echopype") is not None,
        "adcp": importlib.util.find_spec("dolfyn") is not None,
        "norsoop": importlib.util.find_spec("erddapy") is not None,
    }

    installed = [name for name, available in extras.items() if available]
    missing = [name for name, available in extras.items() if not available]

    import json
    return json.dumps({
        "version": __version__,
        "installed_extras": installed,
        "missing_extras": missing,
    }, indent=2)


@mcp.resource("oceanstream://providers")
def get_providers_resource() -> str:
    """All registered data providers with their capabilities."""
    import json
    from oceanstream.providers import list_providers, get_provider

    providers = []
    for name in list_providers():
        p = get_provider(name)
        providers.append({
            "name": name,
            "supported_modules": list(getattr(p, "supported_modules", [])),
            "is_stationary": getattr(p, "is_stationary", False),
        })

    return json.dumps(providers, indent=2)


@mcp.resource("oceanstream://instruments")
def get_instruments_resource() -> str:
    """Full sensor catalogue — all supported oceanographic instruments."""
    import json
    from oceanstream.sensors import get_sensor_catalogue

    catalogue = get_sensor_catalogue()
    sensors = []
    for s in catalogue.list_all():
        sensors.append({
            "id": s.id,
            "name": s.name,
            "manufacturer": s.manufacturer,
            "model": s.model,
            "sensor_type": s.sensor_type.value,
            "description": s.description,
            "variables": s.variables,
        })

    return json.dumps(sensors, indent=2)


@mcp.resource("oceanstream://instruments/{sensor_id}")
def get_instrument_resource(sensor_id: str) -> str:
    """Detailed information about a specific instrument."""
    import json
    from oceanstream.sensors import get_sensor_catalogue

    catalogue = get_sensor_catalogue()
    sensor = catalogue.get(sensor_id)
    if sensor is None:
        return json.dumps({"error": f"Sensor '{sensor_id}' not found"})

    return json.dumps({
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
    }, indent=2)


@mcp.resource("oceanstream://capabilities")
def get_capabilities_resource() -> str:
    """Summary of all processing modules and their current status."""
    import importlib.util
    import json

    modules = [
        {
            "name": "geotrack",
            "description": "GPS/navigation track data → GeoParquet",
            "status": "available" if importlib.util.find_spec("geopandas") else "requires: pip install oceanstream[geotrack]",
            "input_formats": ["CSV", "NMEA", "Sea-Bird HEX/CNV", "tar.gz"],
            "output_formats": ["GeoParquet", "PMTiles", "COG heatmaps", "STAC metadata"],
        },
        {
            "name": "echodata",
            "description": "Echosounder raw data → calibrated Sv → MVBS/NASC",
            "status": "available" if importlib.util.find_spec("echopype") else "requires: pip install oceanstream[echodata]",
            "input_formats": ["EK60/EK80/AZFP/AD2CP .raw"],
            "output_formats": ["Zarr", "NetCDF4", "PNG echograms"],
        },
        {
            "name": "adcp",
            "description": "Acoustic Doppler Current Profiler data processing",
            "status": "available" if importlib.util.find_spec("dolfyn") else "requires: pip install oceanstream[adcp]",
            "input_formats": ["RDI .raw/.000", "Nortek .ad2cp"],
            "output_formats": ["NetCDF4"],
        },
        {
            "name": "multibeam",
            "description": "Multibeam sonar data processing",
            "status": "planned — not yet implemented",
            "input_formats": [],
            "output_formats": [],
        },
    ]

    return json.dumps(modules, indent=2)
