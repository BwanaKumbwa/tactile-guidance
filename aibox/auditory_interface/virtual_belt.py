import base64

class VirtualBeltController:
    """
    Acts as a network bridge. It catches the commands sent by bracelet.py
    and translates them into the JSON format expected by the Android App.
    """
    def __init__(self, result_queue):
        self.result_queue = result_queue

    def send_vibration_command(self, channel_index, pattern, intensity, orientation_type, orientation, **kwargs):
        """Builds the 18-byte continuous vibration command."""
        
        # Don't send empty commands
        if intensity == 0:
            return True

        # Ensure values are within hardware limits
        intensity = max(0, min(int(intensity), 100))
        
        # Map the angle/orientation to the belt's internal compass
        # Orientation is usually 0-360 degrees.
        orientation_int = int(orientation) % 360
        
        # The exact 18-byte packet structure from pybelt
        command_bytes = bytes([
            channel_index & 0xFF,
            pattern & 0xFF,
            intensity & 0xFF,
            (intensity >> 8) & 0xFF,
            0x00,
            0x00,
            orientation_type & 0xFF,
            orientation_int & 0xFF,
            (orientation_int >> 8) & 0xFF,
            0x00,
            0x00,
            0x00, # 0 = infinite iterations (Continuous)
            0x64, 0x00, # 100ms period (100 = 0x0064)
            0x00, 0x00, # Start time 0
            0x00, # Not exclusive (Let other channels play too)
            0x00  # Don't clear others
        ])

        self._send_raw(command_bytes)
        return True

    def send_pulse_command(self, channel_index, intensity, orientation_type, orientation, on_duration_ms, pulse_period, pulse_iterations, series_period, **kwargs):
        """Builds the 18-byte pulse command (used for Grasping/Backing up)."""
        intensity = max(0, min(int(intensity), 100))
        orientation_int = int(orientation) % 360
        
        command_bytes = bytes([
            0x40, # Pulse Command Flag
            channel_index & 0xFF,
            orientation_type & 0xFF,
            orientation_int & 0xFF,
            (orientation_int >> 8) & 0xFF,
            intensity & 0xFF,
            on_duration_ms & 0xFF,
            (on_duration_ms >> 8) & 0xFF,
            pulse_iterations & 0xFF,
            0x01, # 1 series iteration
            pulse_period & 0xFF,
            (pulse_period >> 8) & 0xFF,
            series_period & 0xFF,
            (series_period >> 8) & 0xFF,
            0x00, # Timer Option (Reset)
            0x00, # Not exclusive
            0x00  # Don't clear others
        ])
        
        self._send_raw(command_bytes)
        return True

    def stop_vibration(self, **kwargs):
        """Sends the Stop Command (0x30, 0xFF) to halt all motors."""
        stop_bytes = bytes([0x30, 0xFF])
        self._send_raw(stop_bytes)
        return True

    def _send_raw(self, byte_array):
        """Encodes the bytes to Base64 and puts them in the queue for Android."""
        if self.result_queue is not None and not self.result_queue.full():
            b64_str = base64.b64encode(byte_array).decode('utf-8')
            self.result_queue.put({"vibration_command": b64_str})

    def disconnect_belt(self):
        self.stop_vibration()