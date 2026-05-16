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

# --- CUSTOM API CONFIGURATION (Matching query_processing.py) ---
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

# --- Existing Tools (Set Target, Lists, etc.) ---

from difflib import SequenceMatcher
import re

# ========== FUZZY MATCHING FOR SPEECH ERRORS ==========

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
    Handles both visible and non-visible targets.
    Does fuzzy matching on visible objects, but allows any target name.
    
    Args:
        target_names: List of target object names (e.g., ["apple", "cup"])
        mode: "ordered" (visit in sequence) or "unordered" (any order)
    
    Returns: Confirmation with matched/unmatched targets
    """
    try:
        # Get visible objects for fuzzy matching
        state = requests.get(f"{FASTAPI_URL}/state").json()
        visible_objects = state.get("visible_objects", [])
        available_names = [obj['name'] for obj in visible_objects]
        
        # Process each target
        processed_targets = []
        visibility_status = []
        
        for target_name in target_names:
            target_lower = target_name.lower().strip()
            
            # Check for exact match in visible objects
            exact_match = next((name for name in available_names if name.lower() == target_lower), None)
            
            if exact_match:
                # Use the exact visible object name
                processed_targets.append(exact_match)
                visibility_status.append(f"✓ {exact_match} (visible)")
            else:
                # Try fuzzy matching
                best_match = None
                best_score = 0.0
                
                for obj_name in available_names:
                    similarity = SequenceMatcher(None, target_lower, obj_name.lower()).ratio()
                    if similarity > best_score and similarity > 0.75:
                        best_match = obj_name
                        best_score = similarity
                
                if best_match:
                    # Found a fuzzy match
                    processed_targets.append(best_match)
                    visibility_status.append(f"~ {best_match} (matched from '{target_name}', {best_score:.0%} confidence)")
                else:
                    # No visible match, but add anyway (might appear later)
                    processed_targets.append(target_name)
                    visibility_status.append(f"○ {target_name} (not currently visible - will search when it appears)")
        
        # Remove duplicates while preserving order
        processed_targets = list(dict.fromkeys(processed_targets))
        
        # Set the target list
        requests.post(
            f"{FASTAPI_URL}/command",
            json={
                "instruction": "set_target_list",
                "value": json.dumps({"targets": processed_targets, "mode": mode})
            }
        )
        
        # Build response
        response = f"Target list set to {mode} mode:\n"
        response += "\n".join(visibility_status)
        
        return response
    
    except Exception as e:
        return f"Error adding targets: {str(e)}"


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

# --- REWRITTEN VISION TOOLS ---

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

# --- Utility / Hardware Tools ---

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
def control_vision(instruction: str, value: str = "") -> str:
    """Generic vision control (set_target, disconnect, shutdown)."""
    requests.post(f"{FASTAPI_URL}/command", json={"instruction": instruction, "value": value})
    return f"Instruction {instruction} sent."

if __name__ == "__main__":
    print(f"--- Starting MCP Server (Model: {LLM_MODEL}) ---", file=sys.stderr)
    sys.stdout = original_stdout
    mcp.run()