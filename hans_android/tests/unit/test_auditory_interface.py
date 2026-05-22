import pytest
from unittest.mock import Mock
from server.auditory_interface import HANSBrain, AndroidBridge, AndroidSource


class TestAndroidBridgeInit:
    """Test Android bridge initialization."""
    
    def test_android_source_init(self):
        """Verify AndroidSource initializes."""
        frame_queue = Mock()
        source = AndroidSource(frame_queue)
        assert source is not None
        assert source.frame_queue == frame_queue
    
    def test_android_bridge_alias(self):
        """Verify AndroidBridge is an alias for AndroidSource."""
        frame_queue = Mock()
        bridge = AndroidBridge(frame_queue)
        assert isinstance(bridge, AndroidSource)


class TestHANSBrainInit:
    """Test HANSBrain initialization."""
    
    def test_hans_brain_creates_instance(self):
        """Verify HANSBrain instantiates."""
        brain = HANSBrain()
        assert brain is not None
    
    def test_hans_brain_has_api_key(self):
        """HANSBrain should have API key from environment."""
        brain = HANSBrain()
        assert brain.api_key is not None
    
    def test_hans_brain_verbosity_default(self):
        """Default verbosity should be 'concise'."""
        brain = HANSBrain()
        assert brain.verbosity == "concise"
    
    def test_hans_brain_speed_default(self):
        """Default speed should be 'normal'."""
        brain = HANSBrain()
        assert brain.speed == "normal"


class TestHANSBrainMessageHandling:
    """Test message processing in HANSBrain."""
    
    def test_hans_brain_message_list_init(self):
        """HANSBrain should initialize with empty message list."""
        brain = HANSBrain()
        assert hasattr(brain, 'messages')
        assert isinstance(brain.messages, list)
    
    def test_hans_brain_system_prompt_update(self):
        """Verify update_system_prompt method exists."""
        brain = HANSBrain()
        # Should not raise an error
        brain.update_system_prompt()