import sys
import os
import re
import json
import requests
from mcp.server.fastmcp import FastMCP
from dotenv import load_dotenv

# Load environment variables
load_dotenv(override=True)

# Reroute stdout to prevent breaking MCP JSON-RPC pipe
original_stdout = sys.stdout
sys.stdout = sys.stderr

mcp = FastMCP("HANS-Controller")

# Custom API configuration (matching query_processing.py)
API_URL = os.getenv("API_URL")
API_KEY = os.getenv("API_KEY")
LLM_MODEL = os.getenv("LLM_MODEL", "openai/gpt-oss-120b")

CUSTOM_API_URL = f"{API_URL.rstrip('/')}/v1/chat/completions"

# The internal URL of your FastAPI server
FASTAPI_URL = "http://localhost:8000/internal"

def call_custom_vision_api(messages: list, max_tokens: int = 100):
    """Helper to route vision requests through the University Gateway."""
    if not API_KEY:
        return "Error: API_KEY not set in environment."

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {API_KEY}"
    }
    
    payload = {
        "model": LLM_MODEL,
        "messages": messages,
        "stream": False
        #"max_tokens": max_tokens
    }

    try:
        response = requests.post(CUSTOM_API_URL, headers=headers, json=payload, timeout=30)
        response.raise_for_status()
        data = response.json()
        return data["choices"][0]["message"]["content"]
    except Exception as e:
        return f"Error calling Custom API ({LLM_MODEL}): {e}"

# Existing Tools (Set Target, Lists, etc.)

from difflib import SequenceMatcher
import re

# Fuzzy matching for speech errors

@mcp.tool()
def find_similar_target(target_name: str) -> str:
    """
    Search for objects similar to the requested target.
    Handles speech-to-text errors (e.g., 'cop' -> 'cup').
    
    Returns: JSON with matched target or similar suggestions.
    """
    try:
        # Get visible objects
        state = requests.get(f"{FASTAPI_URL}/state").json()
        visible_objects = state.get("visible_objects", [])
        
        if not visible_objects:
            return json.dumps({
                "status": "no_objects",
                "message": "No objects currently visible.",
                "requested": target_name
            })
        
        # Extract object names
        available = [obj['name'] for obj in visible_objects]
        
        # Fuzzy match: find similarity scores
        matches = []
        target_lower = target_name.lower().strip()
        
        for obj_name in available:
            obj_lower = obj_name.lower().strip()
            
            # Exact match
            if target_lower == obj_lower:
                return json.dumps({
                    "status": "exact_match",
                    "matched_target": obj_name,
                    "requested": target_name,
                    "confidence": 1.0
                })
            
            # Fuzzy match using SequenceMatcher
            similarity = SequenceMatcher(None, target_lower, obj_lower).ratio()
            
            # Also check for levenshtein-like distance for single-char errors
            # "cop" vs "cup" = only 1 char different
            if abs(len(target_lower) - len(obj_lower)) <= 1:
                matches.append({
                    "target": obj_name,
                    "similarity": similarity,
                    "requested": target_name
                })
            elif similarity > 0.7:  # High similarity threshold
                matches.append({
                    "target": obj_name,
                    "similarity": similarity,
                    "requested": target_name
                })
        
        # Sort by similarity score
        matches.sort(key=lambda x: x['similarity'], reverse=True)
        
        if matches:
            best_match = matches[0]
            return json.dumps({
                "status": "similar_match",
                "matched_target": best_match['target'],
                "requested": target_name,
                "confidence": best_match['similarity'],
                "alternatives": [m['target'] for m in matches[1:3]]  # Top 3 alternatives
            })
        else:
            return json.dumps({
                "status": "no_match",
                "requested": target_name,
                "available_objects": available,
                "message": f"No objects similar to '{target_name}'. Available: {', '.join(available)}"
            })
    
    except Exception as e:
        return json.dumps({
            "status": "error",
            "message": str(e)
        })


@mcp.tool()
def set_target_with_fuzzy_match(target_name: str) -> str:
    """
    Set a target, automatically correcting for speech-to-text errors.
    Finds the best match from visible objects.
    """
    try:
        # First, try fuzzy matching
        match_result = find_similar_target(target_name)
        match_data = json.loads(match_result)
        
        # Extract the best matched target
        if match_data["status"] in ["exact_match", "similar_match"]:
            matched_target = match_data["matched_target"]
            confidence = match_data.get("confidence", 1.0)
            
            # Set the matched target
            requests.post(
                f"{FASTAPI_URL}/command",
                json={
                    "instruction": "set_target_list",
                    "value": json.dumps({"targets": [matched_target], "mode": "unordered"})
                }
            )
            
            # Prepare response
            if match_data["status"] == "exact_match":
                return f"Target set to {matched_target}."
            else:
                # Inform user of the correction
                return f"Did you mean '{matched_target}'? (You said '{target_name}'). Setting target. Confidence: {confidence:.0%}"
        
        else:
            # No good match found
            available = match_data.get("available_objects", [])
            if available:
                return f"I couldn't find '{target_name}'. Available objects: {', '.join(available)}. Which one?"
            else:
                return match_data.get("message", "No objects currently visible.")
    
    except Exception as e:
        return f"Error setting target: {str(e)}"

@mcp.tool()
def set_target_list(targets: list[str], mode: str) -> str:
    """Set a list of targets (ordered/unordered)."""
    data = {"targets": targets, "mode": mode}
    requests.post(f"{FASTAPI_URL}/command", json={"instruction": "set_target_list", "value": json.dumps(data)})
    return f"Target list updated to {mode} mode with targets: {targets}"

@mcp.tool()
def add_targets_to_list(target_names: list[str], mode: str = "unordered") -> str:
    """
    Add multiple targets to the target list.
    RESTRICTED: Targets must be from the available COCO object classes.
    Does fuzzy matching to find the correct class name.
    
    Args:
        target_names: List of target object names (e.g., ["apple", "cup"])
        mode: "ordered" (visit in sequence) or "unordered" (any order)
    
    Returns: Confirmation with validated targets or list of available classes
    """
    try:
        # Get available classes from COCO
        state = requests.get(f"{FASTAPI_URL}/state").json()
        available_classes = state.get("available_classes", [])  # All COCO classes
        visible_objects = state.get("visible_objects", [])
        visible_names = [obj['name'] for obj in visible_objects]
        
        if not available_classes:
            return "Error: No available classes loaded. System may still be initializing."
        
        # Process each requested target
        processed_targets = []
        validation_status = []
        rejected_targets = []
        
        for target_name in target_names:
            target_lower = target_name.lower().strip()
            
            # 1. Exact match in available classes
            exact_match = next((cls for cls in available_classes if cls.lower() == target_lower), None)
            
            if exact_match:
                processed_targets.append(exact_match)
                if exact_match in visible_names:
                    validation_status.append(f"✓ {exact_match} (valid class, currently visible)")
                else:
                    validation_status.append(f"✓ {exact_match} (valid class, will search)")
            else:
                # 2. Fuzzy match against available classes
                best_match = None
                best_score = 0.0
                
                for cls_name in available_classes:
                    similarity = SequenceMatcher(None, target_lower, cls_name.lower()).ratio()
                    if similarity > best_score and similarity > 0.70:
                        best_match = cls_name
                        best_score = similarity
                
                if best_match:
                    # Found fuzzy match
                    processed_targets.append(best_match)
                    if best_match in visible_names:
                        validation_status.append(f"~ {best_match} (matched '{target_name}', visible, {best_score:.0%} confidence)")
                    else:
                        validation_status.append(f"~ {best_match} (matched '{target_name}', will search, {best_score:.0%} confidence)")
                else:
                    # No match found - REJECT this target
                    rejected_targets.append(target_name)
                    validation_status.append(f"✗ '{target_name}' is not a valid object class")
        
        # If all targets rejected, show available options
        if not processed_targets:
            available_str = ", ".join(sorted(available_classes))
            return f"❌ None of your targets are valid. Available classes: {available_str}"
        
        # Remove duplicates while preserving order
        processed_targets = list(dict.fromkeys(processed_targets))
        
        # Set the validated target list
        requests.post(
            f"{FASTAPI_URL}/command",
            json={
                "instruction": "set_target_list",
                "value": json.dumps({"targets": processed_targets, "mode": mode})
            }
        )
        
        # Build response
        response = f"Target list set to {mode} mode:\n"
        response += "\n".join(validation_status)
        
        if rejected_targets:
            response += f"\n\n⚠️ Rejected (not valid classes): {', '.join(rejected_targets)}"
        
        return response.strip()
        
    except Exception as e:
        return f"Error updating target list: {str(e)}"


@mcp.tool()
def clear_target_list() -> str:
    """Clear all targets from the list."""
    try:
        requests.post(
            f"{FASTAPI_URL}/command",
            json={
                "instruction": "set_target_list",
                "value": json.dumps({"targets": [], "mode": "unordered"})
            }
        )
        return "Target list cleared."
    except Exception as e:
        return f"Error clearing targets: {str(e)}"


@mcp.tool()
def mark_grasped() -> str:
    """Marks current object as grasped and advances list."""
    requests.post(f"{FASTAPI_URL}/command", json={"instruction": "mark_grasped", "value": ""})
    return "Object marked as grasped."


@mcp.tool()
def get_current_target_list() -> str:
    """
    Get the current target list and list mode (ordered/unordered).
    Reads from shared state maintained by the vision pipeline.
    
    Returns: Current target list with mode
    Example: User asks "What am I looking for?" or "What's my target list?"
    """
    try:
        state = requests.get(f"{FASTAPI_URL}/state").json()
        targets = state.get("target_list", [])  # NOT target_list_state
        mode = state.get("list_mode", "unordered")
        
        if not targets:
            return "Your target list is empty. Say 'Add [object name]' to add a target."
        
        if mode == "ordered":
            response = f"Ordered target list ({len(targets)} item{'s' if len(targets) > 1 else ''}):\n"
            for i, target in enumerate(targets, 1):
                response += f"{i}. {target}\n"
        else:
            response = f"Unordered target list ({len(targets)} item{'s' if len(targets) > 1 else ''}):\n"
            response += ", ".join(targets)
        
        return response.strip()
    except Exception as e:
        return f"Error retrieving target list: {str(e)}"


@mcp.tool()
def get_grasped_objects_history() -> str:
    """
    Get the list of previously grasped objects from the memory file.
    
    Returns: List of successfully grasped objects
    Example: User asks "What have I already grasped?" or "Show my progress"
    """
    try:
        import json
        from pathlib import Path
        
        # Find the memory file
        # Try participant 1 first, then search for any memory file
        possible_paths = [
            Path("results") / "memory_participant_1.json",
            Path("./results") / "memory_participant_1.json",
            Path("/app/results") / "memory_participant_1.json",  # Docker path
        ]
        
        # Also search for any participant memory file
        results_dir = Path("results")
        if results_dir.exists():
            for mem_file in results_dir.glob("memory_participant_*.json"):
                possible_paths.insert(0, mem_file)
        
        memory = None
        for path in possible_paths:
            if path.exists():
                try:
                    with open(path, 'r') as f:
                        memory = json.load(f)
                    break
                except Exception:
                    continue
        
        if memory is None:
            return "No grasped objects yet. Complete a target to add to your history!"
        
        grasped_list = memory.get("grasped_objects", [])
        
        if not grasped_list:
            return "You haven't grasped any objects yet. Start a target to begin!"
        
        response = f"Objects successfully grasped ({len(grasped_list)}):\n"
        for obj in grasped_list:
            response += f"✓ {obj}\n"
        
        return response.strip()
    except Exception as e:
        return f"Error retrieving grasped objects: {str(e)}"

# Vision tools

@mcp.tool()
def analyze_camera_view(question: str) -> str:
    """
    Asks the AI to describe the scene or answer questions about the visual environment.
    Now uses the custom university gateway.
    """
    # 1. Fetch frame from local server
    try:
        resp = requests.get(f"{FASTAPI_URL}/latest_frame").json()
        if resp.get("status") != "ok":
            return "Error: Camera feed is currently unavailable."
        b64_image = resp["image_b64"]
    except Exception as e:
        return f"Error fetching camera frame: {e}"

    # 2. Build Message for Custom API
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": f"You are acting as the eyes of a visually impaired user. Answer this briefly and concisely: {question}"},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64_image}"}}
            ]
        }
    ]

    print(f"[Vision] Sending frame to {LLM_MODEL} for analysis...")
    answer = call_custom_vision_api(messages, max_tokens=150)
    return f"Visual Analysis Result: {answer}"

@mcp.tool()
def find_specific_object(class_name: str, description: str) -> str:
    """
    Finds a specific version of an object (e.g. 'blue cup').
    """
    # Fix: If LLM passes description same as class_name (e.g. cup, cup), clean it up
    clean_desc = description.replace(class_name, "").strip()
    display_name = f"{clean_desc} {class_name}".strip()
    try:
        # 1. ATTEMPT FAST LOCAL OPENCV DETECTION
        # Use description for color, but if description is just the class name, this fails safely
        color_url = f"{FASTAPI_URL}/find_by_color?class_name={class_name}&color={description}"
        color_resp = requests.get(color_url).json()
        
        if color_resp.get("status") == "ok":
            cmd_data = {
                "class_name": class_name, 
                "track_id": color_resp["track_id"], 
                "description": clean_desc if clean_desc else class_name,
                "bbox": color_resp["bbox"]
            }
            requests.post(f"{FASTAPI_URL}/command", json={"instruction": "set_specific_target", "value": json.dumps(cmd_data)})
            return f"Successfully locked onto the {display_name} using local color analysis."

        # 2. FALLBACK TO CUSTOM API VISION
        print(f"[Vision] OpenCV failed. Falling back to {LLM_MODEL}...")
        resp = requests.get(f"{FASTAPI_URL}/get_crops?class_name={class_name}").json()
        
        if resp.get("status") != "ok":
            return resp.get("message", "Error finding objects.")
        
        crops = resp["crops"]
        if len(crops) == 1:
            track_id = crops[0]["track_id"]
            target_bbox = crops[0]["bbox"]
        else:
            # Build multi-image message for visual re-identification
            content = [{"type": "text", "text": f"Which of these images matches the description: '{description}'? Return ONLY the track_id integer."}]
            for c in crops:
                content.append({"type": "text", "text": f"Image track_id: {c['track_id']}"})
                content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{c['image_b64']}"}})
            
            messages = [{"role": "user", "content": content}]
            answer = call_custom_vision_api(messages, max_tokens=10)
            
            # Extract track_id from AI response
            match = re.search(r'\d+', answer)
            if not match:
                return f"The {LLM_MODEL} could not identify which object is the {description} one."
            
            track_id = int(match.group())
            target_bbox = next((c["bbox"] for c in crops if c["track_id"] == track_id), None)

        # 3. Execute Command
        cmd_data = {"class_name": class_name, "track_id": track_id, "description": description, "bbox": target_bbox}
        requests.post(f"{FASTAPI_URL}/command", json={"instruction": "set_specific_target", "value": json.dumps(cmd_data)})
        
        return f"Successfully locked tracking onto the {display_name}."
        
    except Exception as e:
        return f"Failed to run specific target lock: {e}"

# Utility / Hardware Tools

@mcp.tool()
def get_visible_objects() -> str:
    """Returns the list of objects currently visible."""
    try:
        state = requests.get(f"{FASTAPI_URL}/state").json()
        objects = state.get("visible_objects", [])
        if not objects: return "No objects currently visible."
        
        lines = [f"- {obj['name']} (conf: {obj['confidence']:.0%}), ID: {obj.get('track_id','NA')}, Dist: {obj.get('depth','NA'):.2f}m" for obj in objects]
        return "Visible objects:\n" + "\n".join(lines)
    except:
        return "Failed to fetch visible objects."

@mcp.tool()
def get_hardware_status() -> str:
    """Checks Bluetooth connectivity of Bracelet and Belt."""
    try:
        response = requests.get(f"{FASTAPI_URL}/hardware_state").json()
        state = response.get("status", {})
        bracelet = "Connected" if state.get('bracelet') else "Disconnected"
        belt = "Connected" if state.get('belt') else "Disconnected"
        return f"Bracelet is {bracelet}. Belt is {belt}."
    except:
        return "Hardware status unavailable."
    
@mcp.tool()
def toggle_battery_saver(state: str) -> str:
    """
    Turn battery saver mode ON or OFF.
    
    Args:
        state: "on" to enable, "off" to disable
    
    When ON: Phone camera runs at 1 FPS when idle, high FPS only when actively targeting
    When OFF: Phone camera always runs at high FPS (uses more battery)
    
    Example: User says "Turn battery saver on" or "Turn off battery saver"
    """
    try:
        state_lower = state.lower().strip()
        
        if state_lower in ["on", "enable", "yes", "true", "1"]:
            enable = True
            status_text = "enabled"
        elif state_lower in ["off", "disable", "no", "false", "0"]:
            enable = False
            status_text = "disabled"
        else:
            return f"Please specify 'on' or 'off'. You said '{state}'."
        
        # Get current preferences
        prefs_resp = requests.get(f"{FASTAPI_URL}/state").json()
        prefs = prefs_resp.get("preferences", {})
        
        # Update battery saver setting
        prefs["battery_saver"] = enable
        
        # Send updated preferences back to server
        requests.post(
            f"{FASTAPI_URL}/command",
            json={
                "instruction": "update_preferences",
                "value": json.dumps(prefs)
            }
        )
        
        return f"Battery saver mode {status_text}. Phone will now use {'less' if enable else 'more'} battery."
        
    except Exception as e:
        return f"Error toggling battery saver: {str(e)}"


@mcp.tool()
def get_battery_saver_status() -> str:
    """
    Check if battery saver mode is currently ON or OFF.
    
    Example: User says "Is battery saver on?" or "Check battery mode"
    """
    try:
        state = requests.get(f"{FASTAPI_URL}/state").json()
        prefs = state.get("preferences", {})
        is_enabled = prefs.get("battery_saver", False)
        
        if is_enabled:
            return "Battery saver mode is ON. Phone camera will idle at low FPS when no target is active, and switch to high FPS when targeting."
        else:
            return "Battery saver mode is OFF. Phone camera always runs at high FPS for optimal responsiveness."
    
    except Exception as e:
        return f"Error checking battery saver status: {str(e)}"

@mcp.tool()
def control_vision(instruction: str, value: str = "") -> str:
    """Generic vision control (set_target, disconnect, shutdown)."""
    requests.post(f"{FASTAPI_URL}/command", json={"instruction": instruction, "value": value})
    return f"Instruction {instruction} sent."

if __name__ == "__main__":
    print(f"--- Starting MCP Server (Model: {LLM_MODEL}) ---", file=sys.stderr)
    sys.stdout = original_stdout
    mcp.run()