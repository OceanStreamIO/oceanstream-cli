"""MCP Prompts — reusable prompt templates for guided interactions."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.prompts import base

from .server import mcp_app

mcp = mcp_app


@mcp.prompt()
def process_data(file_path: str) -> str:
    """Guided prompt for processing an oceanographic data file.

    Detects the file format and provider, then suggests the appropriate
    processing pipeline with recommended parameters.

    Args:
        file_path: Path to the data file to process
    """
    return (
        f"I have a data file at: {file_path}\n\n"
        "Please help me process this file:\n"
        "1. First, use detect_provider to identify the data source\n"
        "2. Use get_supported_file_formats to confirm the file type is supported\n"
        "3. Based on the detection results, suggest the appropriate processing pipeline\n"
        "4. Ask me to confirm before running the processing command\n\n"
        "If the file is a CSV/NMEA/CTD file, suggest geotrack_convert.\n"
        "If the file is a .raw echosounder file, suggest echodata_run_pipeline.\n"
        "If the file is an ADCP file (.raw/.000/.ad2cp), suggest adcp_process."
    )


@mcp.prompt()
def diagnose_file(file_path: str) -> str:
    """Inspect a file and report what it contains.

    Detects the provider, identifies sensors, and describes the data contents
    without performing any processing.

    Args:
        file_path: Path to the data file to inspect
    """
    return (
        f"Please inspect and diagnose this data file: {file_path}\n\n"
        "Steps:\n"
        "1. Use detect_provider to identify the data source and format\n"
        "2. Read the first few lines of the file to identify column names\n"
        "3. Use detect_sensors with the column names to identify instruments\n"
        "4. Report:\n"
        "   - Detected provider and confidence\n"
        "   - File format and structure\n"
        "   - Detected instruments/sensors\n"
        "   - Recommended processing pipeline\n"
        "   - Any potential issues or missing data"
    )
