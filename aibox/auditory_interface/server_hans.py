import sys
import os
import threading
import queue
from pathlib import Path

# Reroute stdout
# We must capture ALL prints during import/setup and send them to stderr.
# If "Loading models..." hits stdout, the MCP connection will crash immediately.
original_stdout = sys.stdout
sys.stdout = sys.stderr

from mcp.server.fastmcp import FastMCP

# Setup paths (assuming server is in /aibox/auditory_interface)
current_dir = Path(__file__).resolve()
# Adjust this .parents index based on your actual folder structure
project_root = current_dir.parents[1] 

# Add project root to sys.path
if str(project_root) not in sys.path:
    sys.path.append(str(project_root))

# Change working directory to root so weights (relative paths) load correctly
os.chdir(project_root)

# Add submodules (Ensure these paths are correct relative to project_root)
# On Windows, be careful with leading slashes. Using os.path.join is safer.
sys.path.append(os.path.join(project_root, 'yolov5'))
sys.path.append(os.path.join(project_root, 'strongsort'))
sys.path.append(os.path.join(project_root, 'midas'))
sys.path.append(os.path.join(project_root, 'unidepth'))

try:
    import master # Import master.py
    # Also verify we can import controller to catch errors early
    import controller
except ImportError as e:
    print(f"Error importing master/controller: {e}", file=sys.stderr)
    # If imports fail, we must exit, but restoration isn't strictly necessary as we crash anyway
    sys.exit(1)
except Exception as e:
    print(f"Crash during import: {e}", file=sys.stderr)
    sys.exit(1)

# Queue setup
command_bridge_queue = queue.Queue()
mcp = FastMCP("HANS-Controller")

# MCP tools definitions
@mcp.tool()
def control_vision(instruction: str, value: str = "") -> str:
    """
    Basic tool to control the vision system.
    Instructions: 
    - 'stop': Stops the experiment. Stop the only experiment only when user explicitly says they want to stop.
    - 'set_target': Changes target object (e.g., value='bottle', 'cup').
    """
    payload = {"instruction": instruction, "value": value}
    # Log to stderr
    print(f"[MCP] Sending: {payload}", file=sys.stderr, flush=True)
    command_bridge_queue.put(payload)
    return f"Sent instruction '{instruction}' with value '{value}'"

# Main
if __name__ == "__main__":
    print(f"--- Working Directory: {os.getcwd()} ---", file=sys.stderr)
    print("--- Initializing Vision System Thread ---", file=sys.stderr)

    # Mimic argparse object
    class AppArgs:
        def __init__(self):
            # SET YOUR DEFAULT PARAMETERS HERE
            self.participant = 1
            self.condition = "grasping" 
            self.relative = False
            self.mock_navigate = True # Set True to test without bracelet connected
            self.save_video = False

    args = AppArgs()

    try:
        # Start master.py logic in a background thread
        system_thread = threading.Thread(
            target=master.run_experiment_logic, 
            args=(args, command_bridge_queue),
            daemon=True
        )
        system_thread.start()
        
        print("--- Thread Started. Handing control to MCP ---", file=sys.stderr)

        # Restore stdout
        # FastMCP needs the real stdout now to communicate with the client.
        sys.stdout = original_stdout
        
        mcp.run()

    except Exception as e:
        sys.stdout = sys.stderr
        print(f"CRITICAL ERROR STARTING APP: {e}", file=sys.stderr)