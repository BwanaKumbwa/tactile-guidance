"""Server-side processing: vision pipeline, detection, tracking, haptic control, MCP server."""

# Lightweight, pure-Python modules (import at package level)
try:
    from .bracelet import BraceletController
    from .controller import Controller
    from .feedback_device import FeedbackDevice
    from .shared_state import SharedState
    from .labels import coco_labels
except ImportError as e:
    print(f"Warning: Could not import server modules: {e}")

# Auditory interface (MCP + Android bridge)
try:
    from .auditory_interface import HANSBrain, AndroidSource, AndroidBridge
except ImportError as e:
    print(f"Warning: Could not import auditory_interface: {e}")

# Heavy modules imported on-demand to avoid YOLOv5 sys.path issues
def get_vision_pipeline():
    """Import VisionPipeline on-demand."""
    from .vision_pipeline import VisionPipeline, PipelineConfig
    return VisionPipeline, PipelineConfig

def get_controller():
    """Import Controller on-demand (uses YOLOv5)."""
    from .controller import Controller
    return Controller

def get_master():
    """Import Master on-demand."""
    from .master import Master
    return Master

__all__ = [
    'BraceletController',
    'Controller',
    'FeedbackDevice',
    'SharedState',
    'coco_labels',
    'HANSBrain',
    'AndroidSource',
    'AndroidBridge',
    'get_vision_pipeline',
    'get_controller',
    'get_master',
]