"""Unit tests for AndroidSource."""

import pytest
from unittest.mock import Mock
from server.auditory_interface import AndroidSource, AndroidBridge


class TestAndroidSourceCreation:
    """Test AndroidSource (mimics YOLOv5 LoadStreams)."""
    
    def test_create_android_source(self):
        """Create AndroidSource for frame streaming."""
        frame_queue = Mock()
        source = AndroidSource(
            frame_queue=frame_queue,
            img_size=640,
            stride=32,
            auto=True
        )
        
        assert source.img_size == 640
        assert source.stride == 32
        assert source.auto is True
    
    def test_android_source_iteration_interface(self):
        """Verify AndroidSource can be iterated (mimics LoadStreams)."""
        frame_queue = Mock()
        source = AndroidSource(frame_queue)
        
        # Should have iteration interface
        assert hasattr(source, '__iter__')
        assert hasattr(source, '__next__')
    
    def test_android_source_stop_event(self):
        """Verify stop_event for graceful shutdown."""
        frame_queue = Mock()
        source = AndroidSource(frame_queue)
        
        assert hasattr(source, 'stop_event')
        source.stop_event.set()
        assert source.stop_event.is_set()


class TestAndroidBridgeAlias:
    """Verify AndroidBridge is alias for AndroidSource."""
    
    def test_android_bridge_same_as_source(self):
        """AndroidBridge should be AndroidSource."""
        from server.auditory_interface import AndroidBridge
        assert AndroidBridge is AndroidSource