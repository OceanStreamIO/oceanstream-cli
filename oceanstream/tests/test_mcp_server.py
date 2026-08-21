"""Tests for the OceanStream MCP server."""

from __future__ import annotations

import json
import pytest


def test_mcp_server_import():
    """MCP server module can be imported."""
    from oceanstream.mcp_server import mcp_app as mcp
    assert mcp is not None
    assert mcp.name == "OceanStream"


def test_info_tools_registered():
    """All info tools are registered on the MCP server."""
    from oceanstream.mcp_server import mcp_app as mcp

    tool_names = [t.name for t in mcp._tool_manager.list_tools()]
    assert "list_providers" in tool_names
    assert "list_instruments" in tool_names
    assert "get_instrument_details" in tool_names
    assert "detect_provider" in tool_names
    assert "detect_sensors" in tool_names
    assert "list_processing_commands" in tool_names
    assert "get_supported_file_formats" in tool_names


def test_processing_tools_registered():
    """Processing tools are registered on the MCP server."""
    from oceanstream.mcp_server import mcp_app as mcp

    tool_names = [t.name for t in mcp._tool_manager.list_tools()]
    assert "geotrack_convert" in tool_names
    assert "echodata_convert" in tool_names
    assert "echodata_run_pipeline" in tool_names
    assert "adcp_process" in tool_names


def test_list_providers_returns_data():
    """list_providers tool returns provider data."""
    from oceanstream.mcp_server.tools.info import list_providers

    result = list_providers()
    assert isinstance(result, list)
    assert len(result) > 0

    # Check structure
    first = result[0]
    assert "name" in first
    assert "supported_modules" in first
    assert "is_stationary" in first


def test_list_instruments_returns_data():
    """list_instruments tool returns sensor catalogue data."""
    from oceanstream.mcp_server.tools.info import list_instruments

    result = list_instruments()
    assert isinstance(result, list)
    assert len(result) > 0

    first = result[0]
    assert "id" in first
    assert "name" in first
    assert "sensor_type" in first
    assert "variables" in first


def test_get_instrument_details_valid():
    """get_instrument_details returns full info for a known sensor."""
    from oceanstream.mcp_server.tools.info import get_instrument_details

    # Use a sensor we know exists from the catalogue
    result = get_instrument_details("gnss-navigation")
    assert "error" not in result
    assert result["id"] == "gnss-navigation"
    assert "variables" in result


def test_get_instrument_details_unknown():
    """get_instrument_details returns error for unknown sensor."""
    from oceanstream.mcp_server.tools.info import get_instrument_details

    result = get_instrument_details("nonexistent-sensor-xyz")
    assert "error" in result


def test_detect_provider_file_not_found():
    """detect_provider returns error for non-existent file."""
    from oceanstream.mcp_server.tools.info import detect_provider

    result = detect_provider("/nonexistent/path/data.csv")
    assert "error" in result
    assert "not found" in result["error"].lower()


def test_detect_sensors_with_variables():
    """detect_sensors identifies instruments from variable names."""
    from oceanstream.mcp_server.tools.info import detect_sensors

    # GPS-related variables should match navigation sensors
    result = detect_sensors(["latitude", "longitude", "heading", "speed"])
    assert isinstance(result, list)


def test_list_processing_commands_structure():
    """list_processing_commands returns properly structured data."""
    from oceanstream.mcp_server.tools.info import list_processing_commands

    result = list_processing_commands()
    assert isinstance(result, list)
    assert len(result) == 4  # geotrack, echodata, adcp, multibeam

    modules = {item["module"] for item in result}
    assert "geotrack" in modules
    assert "echodata" in modules
    assert "adcp" in modules
    assert "multibeam" in modules


def test_get_supported_file_formats():
    """get_supported_file_formats returns format info."""
    from oceanstream.mcp_server.tools.info import get_supported_file_formats

    result = get_supported_file_formats()
    assert "geotrack" in result
    assert "echodata" in result
    assert "adcp" in result
    assert "multibeam" in result
    assert ".csv" in result["geotrack"]["extensions"]
    assert ".raw" in result["echodata"]["extensions"]


def test_geotrack_convert_file_not_found():
    """geotrack_convert returns error for non-existent input."""
    from oceanstream.mcp_server.tools.geotrack import geotrack_convert

    result = geotrack_convert("/nonexistent/path/data.csv")
    assert "error" in result


def test_echodata_convert_file_not_found():
    """echodata_convert returns error for non-existent input."""
    from oceanstream.mcp_server.tools.echodata import echodata_convert

    result = echodata_convert("/nonexistent/path/data.raw")
    assert "error" in result


def test_adcp_process_file_not_found():
    """adcp_process returns error for non-existent input."""
    from oceanstream.mcp_server.tools.adcp import adcp_process

    result = adcp_process("/nonexistent/path/data.raw")
    assert "error" in result


def test_mcp_prompt_suppression(monkeypatch):
    """MCP prompt is suppressed by environment variable."""
    monkeypatch.setenv("OCEANSTREAM_NO_MCP_PROMPT", "1")

    from oceanstream.cli import _check_mcp_prompt
    # Should not raise or print anything
    _check_mcp_prompt()
