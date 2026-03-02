import sys
import os
import requests
from mcp.server.fastmcp import FastMCP

# Reroute stdout to prevent breaking MCP JSON-RPC pipe
original_stdout = sys.stdout
sys.stdout = sys.stderr

mcp = FastMCP("HANS-Controller")

# The internal URL of your FastAPI server
FASTAPI_URL = "http://localhost:8000/internal"

@mcp.tool()
def control_vision(instruction: str, value: str = "") -> str:
    """
    Controls the vision system. Instructions: 'stop', 'set_target'.
    
    CRITICAL: If the user explicitly asks to stop, quit, or shut down the system, 
    you must call this tool with instruction='stop'. 
    AFTER calling 'stop', you MUST include the exact text [SHUTDOWN] 
    at the very beginning of your final reply to the user.
    """
    try:
        requests.post(f"{FASTAPI_URL}/command", json={"instruction": instruction, "value": value})
        if instruction == "stop":
            return "System stopping. You MUST start your reply with [SHUTDOWN]."
        return f"Success: {instruction} -> {value}"
    except Exception as e:
        return f"Failed: {e}"

@mcp.tool()
def pause_navigation() -> str:
    """Pauses vibration and navigation without stopping the experiment."""
    requests.post(f"{FASTAPI_URL}/command", json={"instruction": "pause_navigation", "value": ""})
    return "Navigation paused — bracelet will stop vibrating."

@mcp.tool()
def resume_navigation() -> str:
    """Resumes vibration and navigation after a pause."""
    requests.post(f"{FASTAPI_URL}/command", json={"instruction": "resume_navigation", "value": ""})
    return "Navigation resumed."

@mcp.tool()
def get_current_target() -> str:
    """Returns the name of the object the vision system is currently tracking."""
    try:
        state = requests.get(f"{FASTAPI_URL}/state").json()
        target = state.get("target", "none")
        return f"Current target: {target}"
    except:
        return "Failed to fetch current target."

@mcp.tool()
def get_visible_objects() -> str:
    """Returns the list of objects currently visible in the camera feed."""
    try:
        state = requests.get(f"{FASTAPI_URL}/state").json()
        objects = state.get("visible_objects", [])
    except:
        return "Failed to fetch visible objects."

    if not objects:
        return "No objects currently visible in the scene."

    lines = []
    for obj in objects:
        parts = [f"{obj['name']} (confidence: {obj['confidence']:.0%})"]
        tid = obj.get("track_id", -1)
        if tid != -1: parts.append(f"track_id: {tid}")
        depth = obj.get("depth", -1)
        if depth is not None and depth != -1: parts.append(f"depth: {depth:.2f}")
        lines.append("- " + ", ".join(parts))

    return f"Currently visible objects ({len(objects)}):\n" + "\n".join(lines)

@mcp.tool()
def get_available_target_classes() -> str:
    """Returns every object class name that can be passed as a target."""
    try:
        state = requests.get(f"{FASTAPI_URL}/state").json()
        classes = state.get("available_classes", [])
    except:
        return "Failed to fetch classes."

    if not classes:
        return "Available classes have not been published yet."

    return "Available target classes:\n" + ", ".join(sorted(classes))

@mcp.tool()
def adjust_vibration_intensity(motor: str, intensity: int) -> str:
    """Adjusts the vibration intensity for a specific motor."""
    if motor not in ('left', 'right', 'top', 'bottom', 'all'):
        return f"Invalid motor '{motor}'."
    if not 0 <= intensity <= 100:
        return f"Intensity must be between 0 and 100."
    
    requests.post(f"{FASTAPI_URL}/command", json={
        "instruction": "adjust_intensity", "value": f"{motor}:{intensity}"
    })
    return f"Set {motor} motor intensity to {intensity}."

@mcp.tool()
def set_verbosity(level: str) -> str:
    """
    Changes how talkative you are.
    Args:
        level: Must be 'concise', 'normal', or 'verbose'.
    Use this if the user says 'talk less', 'be brief', 'give more details', etc.
    """
    if level not in ["concise", "normal", "verbose"]:
         return "Invalid level."
    requests.post(f"{FASTAPI_URL}/verbosity", json={"level": level})
    return f"Verbosity set to {level}. From now on, abide by the rules of this verbosity level."

@mcp.tool()
def set_speech_speed(speed: str) -> str:
    """
    Changes how fast the phone speaks.
    Args:
        speed: Must be 'slow', 'normal', or 'fast'.
        
    CRITICAL: After calling this tool, you MUST include the exact tag 
    [SPEED:SLOW], [SPEED:NORMAL], or [SPEED:FAST] at the very beginning 
    of your final text response to the user.
    """
    if speed in ["slow", "normal", "fast"]:
        return f"System speed updated internally to {speed}. You must now prepend [SPEED:{speed.upper()}] to your reply."
    return "Invalid speed. Choose 'slow', 'normal', or 'fast'."

@mcp.tool()
def get_hardware_status() -> str:
    """
    Checks if the Bluetooth wearable devices (Bracelet/Belt) are currently connected.
    """
    try:
        # Get the full JSON payload
        response = requests.get(f"{FASTAPI_URL}/hardware_state").json()
        
        # Extract the nested 'status' dictionary
        state = response.get("status", {})
        
        # Check the boolean values
        bracelet = "Connected" if state.get('bracelet', False) else "Disconnected"
        belt = "Connected" if state.get('belt', False) else "Disconnected"
        
        return f"Bracelet is {bracelet}. Belt is {belt}."
    except Exception as e:
        # It's helpful to print the actual error to stderr for debugging!
        print(f"[MCP Error] get_hardware_status failed: {e}", file=sys.stderr)
        return "Cannot determine hardware status at this time. Make sure the phone is connected."

if __name__ == "__main__":
    print("--- Starting Decoupled MCP Server ---", file=sys.stderr)
    # Restore stdout so MCP JSON-RPC protocol works
    sys.stdout = original_stdout
    mcp.run()