"""OceanStream MCP Server.

Exposes the oceanstream library as an MCP (Model Context Protocol) server,
providing tools for data processing, instrument discovery, and provider detection.

Usage:
    # stdio transport (for VS Code Copilot, Claude Desktop)
    oceanstream-mcp

    # HTTP transport (for network/edge use)
    oceanstream-mcp --http --port 8060

    # Setup command (installs deps + generates .vscode/mcp.json)
    oceanstream mcp setup
"""

from __future__ import annotations

from .server import mcp_app

__all__ = ["mcp_app"]
