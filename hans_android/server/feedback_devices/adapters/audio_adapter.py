"""
Spatial audio adapter for bone-conduction / earphone guidance.
"""
from __future__ import annotations
import math
from typing import Optional

import numpy as np

from feedback_devices.base import FeedbackDevice, NavigationContext


class SpatialAudioAdapter(FeedbackDevice):
    """
    Renders navigation guidance as spatial audio tones.
    - Angle  → stereo pan  (left ear = target to the left)
    - Distance → pitch     (closer = higher frequency)
    - Depth delta → volume (deeper mismatch = louder)
    """

    TONE_HZ_FAR  = 220.0
    TONE_HZ_NEAR = 880.0
    DIST_MAX_PX  = 600.0

    def __init__(self):
        self._engine = None
        self._connected = False

    def connect(self) -> bool:
        # TODO: initialise audio engine
        self._connected = True
        return True

    def disconnect(self) -> None:
        self.stop()
        self._connected = False

    def update(self, ctx: NavigationContext) -> None:
        if ctx.angle_deg is None or ctx.distance_px is None:
            self.stop()
            return None

        pan  = math.cos(math.radians(ctx.angle_deg))
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
            pass  # TODO: self._engine.play_file(path)

    def stop(self) -> None:
        pass  # TODO: self._engine.stop()

    def get_status(self) -> dict:
        return {'connected': self._connected, 'type': 'audio', 'battery': None}