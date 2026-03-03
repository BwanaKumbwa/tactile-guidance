import sys
import os
import json
import requests
from mcp.server.fastmcp import FastMCP

# Reroute stdout to prevent breaking MCP JSON-RPC pipe
original_stdout = sys.stdout
sys.stdout = sys.stderr

mcp = FastMCP("HANS-Controller")

# The internal URL of your FastAPI server
FASTAPI_URL = "http://localhost:8000/internal"

@mcp.tool()
def set_target_list(targets: list[str], mode: str) -> str:
    """
    Set a list of targets for the user to find and grasp.
    Mode MUST be 'ordered' (find one by one in exact order) OR 'unordered' (find whichever is seen first).
    By default, if the user asks for a list, use 'ordered'.
    """
    data = {"targets": targets, "mode": mode}
    requests.post(
        f"{FASTAPI_URL}/command", json={"instruction": "set_target_list", "value": json.dumps(data)}
    )
    return f"Target list updated to {mode} mode with targets: {targets}"

@mcp.tool()
def clear_target_list() -> str:
    """
    Call this when the user wants to cancel navigation, clear their active targets, 
    or abort a list of objects they were searching for.
    """
    requests.post(f"{FASTAPI_URL}/command", json={"instruction": "clear_list", "value": ""})
    return "The target list has been wiped clean and navigation has stopped."

@mcp.tool()
def update_user_preferences(speech_speed: str = None, verbosity: str = None, battery_saver: bool = None) -> str:
    """
    Call this when the user asks to change settings, talk faster/slower, or turn battery saver on/off.
    Allowed speech_speed: "slow", "normal", "fast".
    Allowed verbosity: "low", "normal", "high".
    battery_saver: True (turns on dynamic FPS to save battery), False (always high FPS).
    """
    data = {}
    if speech_speed: data["speech_speed"] = speech_speed.lower()
    if verbosity: data["verbosity"] = verbosity.lower()
    if battery_saver is not None: data["battery_saver"] = battery_saver
    
    requests.post(f"{FASTAPI_URL}/command", json={"instruction": "update_preferences", "value": json.dumps(data)})
    return f"Saved preferences to memory: {data}"

@mcp.tool()
def control_vision(instruction: str, value: str = "") -> str:
    """
    Controls the vision system targets and power state.
    Instructions: 'set_target', 'disconnect', 'shutdown'.
    
    CRITICAL RULES:
    1. If the user wants to leave, disconnect, or stop for now: instruction='disconnect'. 
       You MUST start your final reply to the user with [DISCONNECT].
    2. If the user explicitly wants to turn off the PC server completely: instruction='shutdown'. 
       You MUST start your final reply to the user with [SHUTDOWN].
    """
    try:
        requests.post(f"{FASTAPI_URL}/command", json={"instruction": instruction, "value": value})
        
        if instruction == "disconnect":
            return "Disconnecting. You MUST start your reply with [DISCONNECT]."
        elif instruction == "shutdown":
            return "Shutting down. You MUST start your reply with [SHUTDOWN]."
            
        return f"Success: {instruction} -> {value}"
    except Exception as e:
        return f"Failed: {e}"
    
@mcp.tool()
def mark_grasped() -> str:
    """
    Call this ONLY when the user confirms they have successfully grasped the object 
    (e.g., "Hans, grasped", "I got it"). It removes the item from the list and advances to the next object.
    """
    requests.post(
        f"{FASTAPI_URL}/command", 
        json={"instruction": "mark_grasped", "value": ""}
    )
    return "Object marked as grasped. The system will automatically advance to the next target."


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