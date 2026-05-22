"""MCP Server + Android Communication Bridge.

This module handles:
  - MCP (Model Context Protocol) server for AI integration (server_hans.py)
  - Query processing (user input from Android app via HANSBrain)
  - Android-Python communication via android_loader.py
  - Server orchestration via server_main.py

Components:
  - server_hans: MCP server with FastMCP tools
  - HANSBrain: Query processor for LLM integration
  - AndroidBridge/AndroidSource: Protocol bridge between Android (Kotlin) and Python
"""

# Import what actually exists in these modules
from .query_processing import HANSBrain
from .android_loader import AndroidSource

# Optional: Import server_hans components if needed
try:
    from .server_hans import mcp, call_custom_vision_api
except ImportError as e:
    print(f"Warning: Could not import MCP server components: {e}")
    mcp = None
    call_custom_vision_api = None

# Alias for compatibility (if you want AndroidBridge)
AndroidBridge = AndroidSource

__all__ = [
    'HANSBrain',
    'AndroidSource',
    'AndroidBridge',
    'mcp',
    'call_custom_vision_api',
]