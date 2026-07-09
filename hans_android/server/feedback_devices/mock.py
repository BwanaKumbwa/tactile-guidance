"""
Mock feedback device for testing — no hardware required.
"""
from feedback_devices.base import FeedbackDevice, NavigationContext
from typing import Optional


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