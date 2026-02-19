import sys
import os
import threading
import queue
from pathlib import Path

# Reroute stdout
original_stdout = sys.stdout
sys.stdout = sys.stderr

from mcp.server.fastmcp import FastMCP

# Setup paths
current_dir = Path(__file__).resolve()
project_root = current_dir.parents[1]

if str(project_root) not in sys.path:
    sys.path.append(str(project_root))

os.chdir(project_root)

sys.path.append(os.path.join(project_root, 'yolov5'))
sys.path.append(os.path.join(project_root, 'strongsort'))
sys.path.append(os.path.join(project_root, 'midas'))
sys.path.append(os.path.join(project_root, 'unidepth'))

try:
    import master
    import controller
except ImportError as e:
    print(f"Error importing master/controller: {e}", file=sys.stderr)
    sys.exit(1)
except Exception as e:
    print(f"Crash during import: {e}", file=sys.stderr)
    sys.exit(1)


# ---------- Shared state ----------
class SharedState:
    """Thread-safe container that the vision pipeline writes to
    and MCP tools read from."""

    def __init__(self):
        self._lock = threading.Lock()
        self._current_target: str = "none"
        self._visible_objects: list = []
        self._available_classes: list = []

    # --- target ---
    def set_target(self, target: str) -> None:
        with self._lock:
            self._current_target = target

    def get_target(self) -> str:
        with self._lock:
            return self._current_target

    # --- visible objects ---
    def set_visible_objects(self, objects: list) -> None:
        with self._lock:
            self._visible_objects = list(objects)

    def get_visible_objects(self) -> list:
        with self._lock:
            return list(self._visible_objects)

    # --- available target classes ---
    def set_available_classes(self, classes: list) -> None:
        with self._lock:
            self._available_classes = list(classes)

    def get_available_classes(self) -> list:
        with self._lock:
            return list(self._available_classes)


shared_state = SharedState()

# ---------- Queue & MCP ----------
command_bridge_queue = queue.Queue()
mcp = FastMCP("HANS-Controller")


@mcp.tool()
def control_vision(instruction: str, value: str = "") -> str:
    """
    Basic tool to control the vision system.
    Instructions:
    - 'stop': Stops the experiment. Stop only when user explicitly says so.
    - 'set_target': Changes target object (e.g., value='bottle', 'cup').
      Use get_available_target_classes() first to find the exact class name
      that matches the user's intent.
    """
    payload = {"instruction": instruction, "value": value}
    print(f"[MCP] Sending: {payload}", file=sys.stderr, flush=True)
    command_bridge_queue.put(payload)

    # Mirror the target in shared_state so reads are immediate
    if instruction == "set_target" and value:
        shared_state.set_target(value)

    return f"Sent instruction '{instruction}' with value '{value}'"

@mcp.tool()
def pause_navigation() -> str:
    """
    Pauses vibration and navigation without stopping the experiment.
    The system keeps detecting and tracking, but does not send
    signals to the bracelet. Use resume_navigation to continue.
    """
    command_bridge_queue.put({"instruction": "pause_navigation", "value": ""})
    return "Navigation paused — bracelet will stop vibrating."

@mcp.tool()
def resume_navigation() -> str:
    """
    Resumes vibration and navigation after a pause.
    """
    command_bridge_queue.put({"instruction": "resume_navigation", "value": ""})
    return "Navigation resumed."

@mcp.tool()
def get_current_target() -> str:
    """
    Returns the name of the object the vision system is currently
    guiding the user towards (e.g. 'bottle', 'cup').
    Returns 'none' if no target has been set yet.
    """
    target = shared_state.get_target()
    print(f"[MCP] Current target queried: {target}", file=sys.stderr, flush=True)
    return f"Current target: {target}"

@mcp.tool()
def get_visible_objects() -> str:
    """
    Returns the list of objects currently visible in the camera feed.
    Each entry includes the object class name, detection confidence,
    tracking ID (if object tracking is enabled), and estimated depth
    (if depth estimation is enabled).
    Use this to understand what the system can currently see before
    deciding on a target.
    """
    objects = shared_state.get_visible_objects()
    print(f"[MCP] Visible objects queried: {len(objects)} object(s)",
          file=sys.stderr, flush=True)

    if not objects:
        return "No objects currently visible in the scene."

    lines = []
    for obj in objects:
        parts = [f"{obj['name']} (confidence: {obj['confidence']:.0%})"]
        tid = obj.get("track_id", -1)
        if tid != -1:
            parts.append(f"track_id: {tid}")
        depth = obj.get("depth", -1)
        if depth is not None and depth != -1:
            parts.append(f"depth: {depth:.2f}")
        lines.append("- " + ", ".join(parts))

    return f"Currently visible objects ({len(objects)}):\n" + "\n".join(lines)

@mcp.tool()
def get_available_target_classes() -> str:
    """
    Returns every object class name that can be passed as the 'value'
    parameter to control_vision(instruction='set_target', value=...).

    Use this when the user's request is ambiguous. For example if the
    user asks for 'coffee', check this list: if 'coffee cup' is not
    available but 'cup' is, use 'cup'.

    The names returned here are the exact strings the system accepts —
    do NOT invent class names that are not on this list.
    """
    classes = shared_state.get_available_classes()
    print(f"[MCP] Available classes queried: {len(classes)} class(es)",
          file=sys.stderr, flush=True)

    if not classes:
        return ("Available classes have not been published yet. "
                "The vision system may still be loading.")

    return ("Available target classes (use these exact names with "
            "control_vision set_target):\n" + ", ".join(sorted(classes)))

@mcp.tool()
def adjust_vibration_intensity(motor: str, intensity: int) -> str:
    """
    Adjusts the vibration intensity for a specific motor on the bracelet.
    
    Args:
        motor: One of 'left', 'right', 'top', 'bottom', or 'all' (then adjust intensity for all 4 motors)
        intensity: Value between 0 and 100
    
    This is useful when the user reports that a particular direction
    feels too strong or too weak.
    """
    if motor not in ('left', 'right', 'top', 'bottom', 'all'):
        return f"Invalid motor '{motor}'. Must be left, right, top, or bottom."
    if not 0 <= intensity <= 100:
        return f"Intensity must be between 0 and 100, got {intensity}."
    command_bridge_queue.put({
        "instruction": "adjust_intensity",
        "value": f"{motor}:{intensity}"
    })
    return f"Set {motor} motor intensity to {intensity}."


if __name__ == "__main__":
    print(f"--- Working Directory: {os.getcwd()} ---", file=sys.stderr)
    print("--- Initializing Vision System Thread ---", file=sys.stderr)

    class AppArgs:
        def __init__(self):
            self.participant = 1
            self.condition = "grasping"
            self.relative = False
            self.mock_navigate = True
            self.save_video = False

    args = AppArgs()

    try:
        system_thread = threading.Thread(
            target=master.run_experiment_logic,
            args=(args, command_bridge_queue, shared_state),
            daemon=True,
        )
        system_thread.start()

        print("--- Thread Started. Handing control to MCP ---", file=sys.stderr)
        sys.stdout = original_stdout
        mcp.run()

    except Exception as e:
        sys.stdout = sys.stderr
        print(f"CRITICAL ERROR STARTING APP: {e}", file=sys.stderr)