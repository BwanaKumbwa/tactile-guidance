"""
feedback_device.py
🟡 FeedbackDevice abstraction layer.

Introduces a protocol/interface that any assistive output device must
implement, so the vision pipeline in controller.py does not need to know
whether it is driving a bracelet, a bone-conduction headset, a vibrating
shoe, a second belt, or a mock device for testing.

Usage in master.py:
    from feedback_device import BraceletAdapter, VirtualBeltAdapter

    devices = []
    bracelet = BraceletAdapter(intensities, navigation_type=1)
    if bracelet.connect():
        devices.append(bracelet)
    if args.enable_audio:
        audio = SpatialAudioAdapter()
        if audio.connect():
            devices.append(audio)

    task_controller = TaskController(
        feedback_devices=devices,   # replaces belt_controller + bracelet_controller
        ...
    )
"""

from __future__ import annotations

import math
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional

import numpy as np


# ---------------------------------------------------------------------------
# NavigationContext — device-agnostic navigation snapshot
# ---------------------------------------------------------------------------

@dataclass
class NavigationContext:
    """
    Passed to every FeedbackDevice.update() call each frame.

    The raw_detections field carries the filtered YOLO outputs that
    existing bracelet code (navigate_hand) already understands.
    Pre-computed geometric fields (angle_deg, distance_px …) are
    provided so new, non-bracelet devices can consume them without
    reimplementing the geometry.
    """

    # Raw data (for BraceletAdapter which proxies to navigate_hand)
    raw_detections:       list              # filtered [xc, yc, w, h, id, cls, conf, depth]
    target_class_id:      int
    hand_class_ids:       list
    depth_img:            Optional[np.ndarray]
    vibration_intensities: dict
    metric:               bool

    # Pre-computed geometry (populated by TaskController before calling update)
    hand_bbox:            Optional[np.ndarray] = None   # [xc, yc, w, h, …]
    target_bbox:          Optional[np.ndarray] = None
    angle_deg:            Optional[float]      = None   # 0–360, 0=right, CCW+
    distance_px:          Optional[float]      = None
    depth_delta_m:        Optional[float]      = None   # hand_depth – target_depth
    is_overlapping:       bool                 = False
    is_touching:          bool                 = False
    obstacle_detected:    bool                 = False
    frame_shape:          tuple                = (640, 640)


# ---------------------------------------------------------------------------
# FeedbackDevice — abstract interface
# ---------------------------------------------------------------------------

class FeedbackDevice(ABC):
    """
    Any assistive output device must implement this interface.
    The vision pipeline calls update() every frame and signal_event()
    for discrete haptic/audio events.  It never imports bracelet.py,
    pybelt, pyaudio, or any other hardware library directly.
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
        Called once per vision frame.  The device translates ctx into
        its own output modality.  Returns the 'curr_target' bbox or None
        so the caller can use it for visualisation (mirrors navigate_hand).
        """
        ...

    @abstractmethod
    def signal_event(self, event: str) -> None:
        """
        Signal a discrete named event.  Standard event names:
          'grasped'      — hand centre is inside target bbox
          'target_found' — target becomes visible for the first time
          'target_lost'  — target disappears from the frame
          'obstacle'     — obstacle between hand and target
          'list_complete'— all targets in the ordered list grasped
        """
        ...

    @abstractmethod
    def stop(self) -> None:
        """Immediately cease all output (called on pause / disconnect)."""
        ...

    @abstractmethod
    def get_status(self) -> dict:
        """
        Returns {'connected': bool, 'type': str, 'battery': int|None}.
        'type' is a stable lower-case string, e.g. 'bracelet', 'audio', 'shoe'.
        """
        ...


# ---------------------------------------------------------------------------
# BraceletAdapter — wraps existing BraceletController behind the interface
# ---------------------------------------------------------------------------

class BraceletAdapter(FeedbackDevice):
    """
    Thin adapter that proxies FeedbackDevice calls to the existing
    BraceletController / pybelt stack.  No changes to bracelet.py required.
    """

    def __init__(self, vibration_intensities: dict, navigation_type: int = 1):
        # Import lazily so the abstract interface file has no mandatory hardware dep
        from bracelet import BraceletController, connect_belt
        self._connect_belt = connect_belt
        self._bc = BraceletController(vibration_intensities, navigation_type)
        self._belt = None
        self._connected = False

    # -- Lifecycle ---------------------------------------------------------

    def connect(self) -> bool:
        success, self._belt = self._connect_belt()
        self._connected = success
        return success

    def disconnect(self) -> None:
        if self._belt:
            try:
                self._belt.stop_vibration()
                self._belt.disconnect_belt()
            except Exception:
                pass
        self._connected = False

    # -- Core interface ----------------------------------------------------

    def update(self, ctx: NavigationContext) -> Optional[object]:
        """Proxy to navigate_hand; returns curr_target for visualisation."""
        return self._bc.navigate_hand(
            self._belt,
            ctx.raw_detections,
            ctx.target_class_id,
            ctx.hand_class_ids,
            ctx.depth_img,
            ctx.vibration_intensities,
            ctx.metric
        )[1]   # navigate_hand returns (overlapping, curr_target); we expose curr_target

    def signal_event(self, event: str) -> None:
        """Map named events to pybelt pulse patterns."""
        from pybelt.belt_controller import BeltOrientationType, BeltVibrationTimerOption
        PATTERNS = {
            'grasped':       {'mask': 0b111100, 'intensity': 50, 'iters': 5},
            'obstacle':      {'mask': 0b101000, 'intensity': 30, 'iters': 5},
            'target_found':  {'mask': 0b010000, 'intensity': 40, 'iters': 3},
            'list_complete': {'mask': 0b111111, 'intensity': 60, 'iters': 8},
        }
        p = PATTERNS.get(event)
        if p and self._belt and self._bc.vibrate:
            self._belt.stop_vibration()
            self._belt.send_pulse_command(
                channel_index=1,
                orientation_type=BeltOrientationType.BINARY_MASK,
                orientation=p['mask'],
                intensity=p['intensity'],
                on_duration_ms=150, pulse_period=300,
                pulse_iterations=p['iters'],
                series_period=5000, series_iterations=1,
                timer_option=BeltVibrationTimerOption.RESET_TIMER,
                exclusive_channel=False, clear_other_channels=False)

    def stop(self) -> None:
        if self._belt:
            try:
                self._belt.stop_vibration()
            except Exception:
                pass

    def get_status(self) -> dict:
        return {
            'connected': self._connected,
            'type':      'bracelet',
            'battery':   None
        }

    # -- Passthrough props the rest of controller.py still uses -----------

    @property
    def vibrate(self) -> bool:
        return self._bc.vibrate

    @vibrate.setter
    def vibrate(self, v: bool):
        self._bc.vibrate = v

    @property
    def mock_navigate(self) -> bool:
        return self._bc.mock_navigate

    @mock_navigate.setter
    def mock_navigate(self, v: bool):
        self._bc.mock_navigate = v


# ---------------------------------------------------------------------------
# SpatialAudioAdapter — skeleton for a bone-conduction / earphone device
# ---------------------------------------------------------------------------

class SpatialAudioAdapter(FeedbackDevice):
    """
    Renders navigation guidance as spatial audio tones.
    - Angle  → stereo pan  (left ear = target to the left)
    - Distance → pitch     (closer = higher frequency)
    - Depth delta → volume (deeper mismatch = louder)

    Replace the _engine stub with sounddevice / pyaudio / pygame as needed.
    """

    TONE_HZ_FAR  = 220.0
    TONE_HZ_NEAR = 880.0
    DIST_MAX_PX  = 600.0

    def __init__(self):
        self._engine = None
        self._connected = False

    def connect(self) -> bool:
        # TODO: initialise your audio engine here, e.g.:
        # import sounddevice as sd
        # self._engine = sd
        self._connected = True   # stub
        return True

    def disconnect(self) -> None:
        self.stop()
        self._connected = False

    def update(self, ctx: NavigationContext) -> None:
        if ctx.angle_deg is None or ctx.distance_px is None:
            self.stop()
            return None

        pan  = math.cos(math.radians(ctx.angle_deg))          # -1 L … +1 R
        freq = np.interp(ctx.distance_px,
                         [0, self.DIST_MAX_PX],
                         [self.TONE_HZ_NEAR, self.TONE_HZ_FAR])
        vol  = float(np.clip(abs(ctx.depth_delta_m or 0) * 0.5, 0.1, 1.0))

        # TODO: self._engine.play_tone(freq=freq, pan=pan, volume=vol)
        return None

    def signal_event(self, event: str) -> None:
        SOUNDS = {
            'grasped':       'resources/sound/success.wav',
            'obstacle':      'resources/sound/warning.wav',
            'target_found':  'resources/sound/found.wav',
            'list_complete': 'resources/sound/complete.wav',
        }
        path = SOUNDS.get(event)
        if path:
            # TODO: self._engine.play_file(path)
            pass

    def stop(self) -> None:
        # TODO: self._engine.stop()
        pass

    def get_status(self) -> dict:
        return {'connected': self._connected, 'type': 'audio', 'battery': None}


# ---------------------------------------------------------------------------
# MockDevice — for unit tests and CI (no hardware required)
# ---------------------------------------------------------------------------

class MockFeedbackDevice(FeedbackDevice):
    """Records all calls; useful for testing pipeline logic without hardware."""

    def __init__(self):
        self.log: list = []
        self._connected = False

    def connect(self) -> bool:
        self._connected = True
        self.log.append(('connect', None))
        return True

    def disconnect(self) -> None:
        self._connected = False
        self.log.append(('disconnect', None))

    def update(self, ctx: NavigationContext) -> None:
        self.log.append(('update', {
            'angle': ctx.angle_deg,
            'distance': ctx.distance_px,
            'overlapping': ctx.is_overlapping
        }))
        return None

    def signal_event(self, event: str) -> None:
        self.log.append(('event', event))

    def stop(self) -> None:
        self.log.append(('stop', None))

    def get_status(self) -> dict:
        return {'connected': self._connected, 'type': 'mock', 'battery': None}
