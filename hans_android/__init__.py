"""HANS - Automated Hand Guidance System for blind users.

Architecture:
  - Server: Python-based vision pipeline, object tracking, haptic control
  - Auditory Interface: MCP server + Android communication bridge
  - Android Client: Kotlin app connecting to the server
"""

__version__ = "1.0.0-beta.1"
__author__ = "Your Team"

# Lightweight modules (safe to import at top level)
try:
    from server.bracelet import BraceletController
    from server.feedback_device import FeedbackDevice
    from server.shared_state import SharedState
except ImportError as e:
    print(f"Warning: Could not import server modules: {e}")

# Auditory interface
try:
    from server.auditory_interface import HANSBrain, AndroidSource, AndroidBridge
except ImportError as e:
    print(f"Warning: Could not import auditory_interface: {e}")

# Heavy modules: lazy load on demand
def get_vision_pipeline():
    """Import VisionPipeline on-demand to avoid YOLOv5 sys.path issues."""
    from server.vision_pipeline import VisionPipeline, PipelineConfig
    return VisionPipeline, PipelineConfig

def get_controller():
    """Import Controller on-demand."""
    from server.controller import Controller
    return Controller

def get_master():
    """Import Master on-demand."""
    from server.master import Master
    return Master

__all__ = [
    'BraceletController',
    'FeedbackDevice',
    'SharedState',
    'HANSBrain',
    'AndroidSource',
    'AndroidBridge',
    'get_vision_pipeline',
    'get_controller',
    'get_master',
]