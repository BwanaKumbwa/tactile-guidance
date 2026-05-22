"""Test suite for HANS system."""

import pytest
import sys
from pathlib import Path
from unittest.mock import Mock

# Add project root to path
project_root = Path(__file__).parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

# Import what's actually available
from server.bracelet import BraceletController
from server.shared_state import SharedState
from server.auditory_interface import HANSBrain, AndroidSource

@pytest.fixture
def bracelet_controller():
    """Tactile bracelet controller."""
    return BraceletController()

@pytest.fixture
def shared_state():
    """Shared state manager."""
    return SharedState()

@pytest.fixture
def hans_brain():
    """HANSBrain for query processing."""
    return HANSBrain()

@pytest.fixture
def android_source():
    """Android frame source."""
    frame_queue = Mock()
    return AndroidSource(frame_queue)