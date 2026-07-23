"""Feedback device adapters and mock implementations."""

from .base import FeedbackDevice, NavigationContext
from .mock import MockFeedbackDevice
from .virtual_belt import VirtualBeltController
from .adapters import BraceletAdapter, SpatialAudioAdapter, BeltAdapter
from .handoff import HANDOFF_ENTER_CM, HANDOFF_EXIT_CM, MAX_APPROACH_CM

__all__ = [
    'FeedbackDevice',
    'NavigationContext',
    'MockFeedbackDevice',
    'VirtualBeltController',
    'BraceletAdapter',
    'SpatialAudioAdapter',
    'BeltAdapter',
    'HANDOFF_ENTER_CM',
    'HANDOFF_EXIT_CM',
    'MAX_APPROACH_CM',
]
