#!/usr/bin/env python3
"""
Script to run the MCP server in STDIO mode for testing with streamable HTTP client.
"""
import sys
import os

# Add the server directory to the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'server'))

# Import and run the server in STDIO mode
from server.terminal_server import mcp

if __name__ == "__main__":
    print("Starting MCP server in STDIO mode...")
    print("This server will work with streamable HTTP clients.")
    mcp.run(transport="stdio") 