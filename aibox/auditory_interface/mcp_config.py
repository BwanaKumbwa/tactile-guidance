import sys
from mcp import StdioServerParameters

def get_server_parameters(script_name="server_hans.py"):
    """
    Returns the configuration to launch the MCP server subprocess.
    """
    return StdioServerParameters(
        command=sys.executable,
        args=[script_name],
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