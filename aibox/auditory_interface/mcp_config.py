import os
import sys
from pathlib import Path
from mcp.client.stdio import StdioServerParameters

def get_server_parameters(script_name: str) -> StdioServerParameters:
    # Get the directory where mcp_config.py lives
    current_dir = Path(__file__).resolve().parent
    
    # Build the absolute path to server file
    script_path = current_dir / script_name
    
    # Verify the file exists
    if not script_path.exists():
        print(f"❌ CRITICAL ERROR: MCP Server file not found at {script_path}", file=sys.stderr)
        # Fallback: Maybe it's in the parent directory?
        alt_path = current_dir.parent / script_name
        if alt_path.exists():
            print(f"⚠️ Found it at {alt_path} instead.", file=sys.stderr)
            script_path = alt_path
        else:
            raise FileNotFoundError(f"Could not find {script_name} anywhere near {current_dir}")

    # Get the exact Python executable running FastAPI
    python_exe = sys.executable
    
    print(f"--- Launching MCP Subprocess ---")
    print(f"Python: {python_exe}")
    print(f"Script: {script_path}")
    
    # Pass the environment variables (important for OpenAI API Key)
    env = os.environ.copy()
    
    return StdioServerParameters(
        command=python_exe,
        args=[str(script_path)],
        env=env
    )

def convert_mcp_to_openai_tools(mcp_tools_list):
    """
    Converts MCP tool definitions into the format OpenAI expects.
    """
    return [{
        "type": "function",
        "function": {
            "name": t.name,
            "description": t.description,
            "parameters": t.inputSchema
        }
    } for t in mcp_tools_list.tools]