"""
Abstract feedback device interfaces and shared data structures.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional
import numpy as np


@dataclass
class NavigationContext:
    """
    Device-agnostic navigation snapshot passed to every FeedbackDevice.update() call.
    """
    raw_detections:       list              # filtered [xc, yc, w, h, id, cls, conf, depth]
    target_class_id:      int
    hand_class_ids:       list
    depth_img:            Optional[np.ndarray]
    vibration_intensities: dict
    metric:               bool

    # Pre-computed geometry (populated by TaskController before calling update)
    hand_bbox:            Optional[np.ndarray] = None
    target_bbox:          Optional[np.ndarray] = None
    angle_deg:            Optional[float]      = None
    distance_px:          Optional[float]      = None
    depth_delta_m:        Optional[float]      = None
    is_overlapping:       bool                 = False
    is_touching:          bool                 = False
    obstacle_detected:    bool                 = False
    frame_shape:          tuple                = (640, 640)


class FeedbackDevice(ABC):
    """
    Abstract base for any assistive output device.
    Vision pipeline calls update() every frame and signal_event() for discrete events.
    """

    @abstractmethod
    def connect(self) -> bool:
        """Establish hardware connection. Returns True on success."""
        ...

    @abstractmethod
    def disconnect(self) -> None:
        """Gracefully release all hardware resources."""
        ...

    @abstractmethod
    def update(self, ctx: NavigationContext) -> Optional[object]:
        """
        Called once per vision frame. Translate ctx into device output.
        Returns 'curr_target' bbox or None for visualization.
        """
        ...

    @abstractmethod
    def signal_event(self, event: str) -> None:
        """
        Signal a discrete named event:
          'grasped'       — hand centre is inside target bbox
          'target_found'  — target becomes visible for first time
          'target_lost'   — target disappears from frame
          'obstacle'      — obstacle between hand and target
          'list_complete' — all targets in ordered list grasped
        """
        ...

    @abstractmethod
    def stop(self) -> None:
        """Immediately cease all output (called on pause / disconnect)."""
        ...

    @abstractmethod
    def get_status(self) -> dict:
        """Return {'connected': bool, 'type': str, 'battery': int|None}."""
        ...