import os
import signal
import time
import asyncio
import cv2
import numpy as np
import base64
import threading
import queue
import json
import sys
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from pydantic import BaseModel
from contextlib import asynccontextmanager
from pathlib import Path

file = Path(__file__).resolve()
root = file.parents[0] 
for path in ['/yolov5', '/strongsort', '/unidepth', '/midas']:
    if str(root) + path not in sys.path:
        sys.path.append(str(root) + path)

from android_loader import AndroidSource
from virtual_belt import VirtualBeltController
import master 
from query_processing import HANSBrain
from mcp.client.stdio import stdio_client
from mcp import ClientSession
from mcp_config import get_server_parameters, convert_mcp_to_openai_tools

# Shared state
class SharedState:
    """Thread-safe container updated by YOLO, read by MCP."""
    def __init__(self):
        self._lock = threading.Lock()
        self._current_target = "none"
        self._visible_objects = []
        self._available_classes = []
        self._bracelet_connected = False
        self._belt_connected = False

    def set_target(self, target: str):
        with self._lock: self._current_target = target
    def get_target(self):
        with self._lock: return self._current_target
    def set_visible_objects(self, objects: list):
        with self._lock: self._visible_objects = list(objects)
    def get_visible_objects(self):
        with self._lock: return list(self._visible_objects)
    def set_available_classes(self, classes: list):
        with self._lock: self._available_classes = list(classes)
    def get_available_classes(self):
        with self._lock: return list(self._available_classes)
    def set_hardware_status(self, bracelet: bool, belt: bool):
        with self._lock:
            self._bracelet_connected = bracelet
            self._belt_connected = belt
    def get_hardware_status(self) -> dict:
        with self._lock:
            return {"bracelet": self._bracelet_connected, "belt": self._belt_connected}

shared_state = SharedState()

# Global queues and state
result_queue = queue.Queue(maxsize=10)
frame_queue = queue.Queue(maxsize=1)
mcp_queue = queue.Queue()
latest_frame_ref = {"img": None}

brain = HANSBrain()
mcp_session_global = None
openai_tools_global = []

# Configuration
class SimArgs:
    def __init__(self):
        self.participant = 1
        self.condition = 'multiple_objects' # grasping, multiple_objects, depth_navigation
        self.relative = False
        self.mock_navigate = False 
        self.save_video = False

def run_ai_logic():
    print("🧠 AI Vision Thread Started")
    android_loader = AndroidSource(frame_queue, img_size=640)
    args = SimArgs()
    virtual_belt = VirtualBeltController(result_queue)
    
    try:
        master.run_experiment_logic(
            args, 
            mcp_queue=mcp_queue, 
            shared_state=shared_state,
            custom_loader=android_loader,
            result_queue=result_queue,
            custom_belt=virtual_belt 
        )
    except Exception as e:
        print(f"❌ Error in AI Loop: {e}")

# Lifecycle (MCP startup)
@asynccontextmanager
async def lifespan(app: FastAPI):
    global mcp_session_global, openai_tools_global
    
    # 1. Start YOLO Thread
    threading.Thread(target=run_ai_logic, daemon=True).start()
    
    # 2. Start MCP Robustly in background task (won't crash FastAPI if it fails)
    async def init_mcp():
        global mcp_session_global, openai_tools_global
        try:
            print("--- Starting MCP Server Process ---")
            server_params = get_server_parameters("server_hans_updated.py")
            async with stdio_client(server_params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    mcp_tools = await session.list_tools()
                    openai_tools_global = convert_mcp_to_openai_tools(mcp_tools)
                    mcp_session_global = session
                    print(f"✅ MCP Connected! Tools: {[t.name for t in mcp_tools.tools]}")
                    
                    await asyncio.Event().wait() # Keep connection open
        except Exception as e:
            print(f"❌ MCP Connection Failed: {e}")
            print("⚠️ Server will run, but LLM Commands will be disabled.")

            import traceback
            traceback.print_exc()

    mcp_task = asyncio.create_task(init_mcp())
    yield
    mcp_task.cancel()

app = FastAPI(lifespan=lifespan)

# Internal endpoints
class InternalCommand(BaseModel):
    instruction: str
    value: str

@app.post("/internal/command")
def receive_internal_command(cmd: InternalCommand):
    
    if cmd.instruction == "shutdown":
        # 1. Tell YOLO to break its loop and close resources
        mcp_queue.put({"instruction": "stop", "value": ""})
        # 2. Start the suicide timer for the Web Server
        def shutdown_server():
            import time, os, signal
            time.sleep(3)
            print("🛑 Shutting down FastAPI server...")
            os.kill(os.getpid(), signal.SIGINT)
        threading.Thread(target=shutdown_server, daemon=True).start()
        
    elif cmd.instruction == "disconnect":
        # Don't kill the server! Just tell YOLO to clear its target.
        # This effectively puts the AI into "Sleep Mode" waiting for the next connection.
        mcp_queue.put({"instruction": "set_target", "value": "none"})
        shared_state.set_target("none")
        
    else:
        # Standard commands (set_target, pause, etc)
        mcp_queue.put({"instruction": cmd.instruction, "value": cmd.value})
        if cmd.instruction == "set_target":
            shared_state.set_target(cmd.value)
            
    return {"status": "ok"}

@app.get("/internal/state")
def get_internal_state():
    """server_hans.py calls this to read what YOLO sees"""
    return {
        "target": shared_state.get_target(),
        "visible_objects": shared_state.get_visible_objects(),
        "available_classes": shared_state.get_available_classes()
    }

@app.get("/internal/hardware_state")
def get_internal_state():
    """server_hans.py calls this to see hardware state"""
    return {
        "status": shared_state.get_hardware_status()
    }

class VerbosityRequest(BaseModel):
    level: str

@app.post("/internal/verbosity")
def set_verbosity(req: VerbosityRequest):
    """server_hans calls this to change LLM prompt"""
    brain.set_verbosity(req.level)
    return {"status": "ok"}

# Android endpoints
@app.websocket("/ws/video")
async def video_endpoint(websocket: WebSocket):
    await websocket.accept()
    print("✅ Phone Connected")
    
    async def sender_task():
        class NumpyEncoder(json.JSONEncoder):
            def default(self, obj):
                if isinstance(obj, np.integer):
                    return int(obj)
                if isinstance(obj, np.floating):
                    return float(obj)
                if isinstance(obj, np.ndarray):
                    return obj.tolist()
                return super(NumpyEncoder, self).default(obj)

        try:
            while True:
                if not result_queue.empty():
                    item = result_queue.get()
                    
                    # 1. Protect against raw images
                    # If an image frame somehow gets in the queue, skip it
                    # (Sending a raw image as a JSON list crashes the phone)
                    if isinstance(item, np.ndarray):
                        continue 
                    
                    # 2. Serialize safely using the Custom Encoder
                    json_str = json.dumps(item, cls=NumpyEncoder)
                    
                    # 3. Send to Android
                    await websocket.send_text(json_str)
                    
                await asyncio.sleep(0.01) # Yield to event loop
        except Exception as e:
            print(f"Sender task error: {e}")

    sender_future = asyncio.create_task(sender_task())

    try:
        while True:
            data = await websocket.receive_bytes() 
            
            # --- BINARY UNPACKING ---
            # Extract RGB Image
            rgb_len = int.from_bytes(data[0:4], byteorder='big')
            rgb_bytes = data[4 : 4+rgb_len]
            np_arr = np.frombuffer(rgb_bytes, np.uint8)
            frame = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)
            
            # Extract Depth Map (if phone sent it)
            depth_map = None
            if len(data) > 4 + rgb_len:
                depth_bytes = data[4+rgb_len : ]
                depth_arr = np.frombuffer(depth_bytes, np.uint8)
                # Use IMREAD_ANYDEPTH to preserve 16-bit hardware depth if sent as PNG
                depth_map = cv2.imdecode(depth_arr, cv2.IMREAD_ANYDEPTH) 
                if depth_map is not None:
                    depth_map = cv2.rotate(depth_map, cv2.ROTATE_90_CLOCKWISE)

            if frame is not None:
                frame = cv2.rotate(frame, cv2.ROTATE_90_CLOCKWISE)
                latest_frame_ref["img"] = frame.copy()

                if frame_queue.full():
                    try: frame_queue.get_nowait()
                    except queue.Empty: pass
                
                # Push BOTH images to the YOLO loop
                frame_queue.put((frame, depth_map)) 
                
    except WebSocketDisconnect:
        print("❌ Phone Disconnected")
    finally:
        sender_future.cancel()

class CommandRequest(BaseModel):
    text: str
    bracelet_connected: bool = False
    belt_connected: bool = False

@app.post("/api/command")
async def process_command(req: CommandRequest):
    print(f"\n🎤 User audio transcribed as: {req.text}")

    # Save hardware status to shared state so tools can read it
    shared_state.set_hardware_status(req.bracelet_connected, req.belt_connected)
    
    if mcp_session_global is None:
        return {"answer": "The AI is currently booting up, please wait."}

    try:
        # LLM processing
        answer = await brain.process_query(
            mcp_session_global, 
            req.text, 
            openai_tools_global
        )
        return {"answer": answer}
        
    except Exception as e:
        print(f"❌ LLM Processing Error: {e}")
        return {"answer": "I encountered an error analyzing your request."}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)