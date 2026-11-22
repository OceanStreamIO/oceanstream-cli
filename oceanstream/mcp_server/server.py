"""OceanStream MCP Server — FastMCP application definition."""

from __future__ import annotations

import sys
from mcp.server.fastmcp import FastMCP

mcp_app = FastMCP(
    "OceanStream",
    instructions=(
        "OceanStream processes raw oceanographic sensor data into analysis-ready, "
        "cloud-optimized formats (GeoParquet, Zarr, PMTiles). Use the info tools to "
        "discover supported instruments, providers, and processing capabilities. "
        "Use the processing tools to convert and analyze data files."
    ),
)

# Register tools and resources by importing the modules
from .tools import info  # noqa: E402, F401
from .tools import geotrack  # noqa: E402, F401
from .tools import echodata  # noqa: E402, F401
from .tools import adcp  # noqa: E402, F401
from . import resources  # noqa: E402, F401
from . import prompts  # noqa: E402, F401

# Alias for backward compatibility
mcp = mcp_app


def main() -> None:
    """Entry point for the oceanstream-mcp command."""
    import argparse

    parser = argparse.ArgumentParser(
        prog="oceanstream-mcp",
        description="OceanStream MCP Server — AI assistant integration for oceanographic data processing",
    )
    parser.add_argument(
        "--http",
        action="store_true",
        help="Use streamable HTTP transport instead of stdio",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8060,
        help="Port for HTTP transport (default: 8060)",
    )
    parser.add_argument(
        "--host",
        default="0.0.0.0",
        help="Host for HTTP transport (default: 0.0.0.0)",
    )

    args = parser.parse_args()

    if args.http:
        mcp_app.settings.host = args.host
        mcp_app.settings.port = args.port
        mcp_app.run(transport="streamable-http")
    else:
        mcp_app.run(transport="stdio")


if __name__ == "__main__":
    main()
