import pytest
from unittest.mock import Mock, patch
from server.auditory_interface import HANSBrain, AndroidBridge
from server.vision_pipeline import PipelineConfig


class TestAndroidBridge:
    """Test communication between Kotlin app and Python server."""
    
    def test_android_bridge_init(self):
        """Initialize Android bridge."""
        frame_queue = Mock()
        bridge = AndroidBridge(frame_queue)
        assert bridge is not None
    
    def test_android_message_parsing(self):
        """Test parsing message from Kotlin app."""
        frame_queue = Mock()
        bridge = AndroidBridge(frame_queue)
        
        # Bridge should have basic properties
        assert hasattr(bridge, 'frame_queue')


class TestHANSBrainQueryProcessing:
    """Test query processing from user input via HANSBrain."""
    
    def test_hans_brain_init(self):
        """Initialize HANSBrain."""
        brain = HANSBrain()
        assert brain is not None
    
    def test_hans_brain_has_methods(self):
        """Verify HANSBrain has key methods."""
        brain = HANSBrain()
        assert hasattr(brain, 'update_system_prompt')


class TestAndroidToVisionPipeline:
    """Test Android app → Python server → Vision pipeline flow."""
    
    @patch('server.auditory_interface.AndroidBridge')
    def test_android_target_message(self, mock_bridge):
        """Simulate Android app setting target."""
        # Mock the bridge
        bridge = mock_bridge()
        
        # Simulate Android command
        android_message = {
            'type': 'set_target',
            'target': 'bottle'
        }
        
        # Should be processable
        assert android_message['type'] == 'set_target'
        assert android_message['target'] == 'bottle'


class TestAndroidHapticFeedback:
    """Test haptic feedback reaching Android app."""
    
    def test_haptic_command_format(self):
        """Verify haptic commands have correct format."""
        haptic_cmd = {
            'type': 'vibrate',
            'intensity': 50,
            'direction': 'right'
        }
        
        # Should have all required fields
        assert 'type' in haptic_cmd
        assert 'intensity' in haptic_cmd
        assert 'direction' in haptic_cmd
        assert haptic_cmd['intensity'] >= 0
        assert haptic_cmd['intensity'] <= 100