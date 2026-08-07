import base64

from feedback_devices.orientation import ANGLE, MAGNETIC_BEARING, MOTOR_INDEX


class VirtualBeltController:
    def __init__(self, result_queue, target_device="all"):
        self.result_queue = result_queue
        self.target_device = target_device

    def send_vibration_command(self, channel_index, pattern, intensity, orientation_type, orientation, **kwargs):
        if intensity == 0:
            return self.stop_vibration()

        intensity = max(0, min(int(intensity), 100))
        orientation_int = self._normalize_orientation(orientation_type, orientation)

        # Guaranteed ~2 s block; adapters refresh before expiry.
        on_duration_ms = 2000

        command_bytes = bytes([
            0x40,                            # Command Flag: Vibrate (pulse-style)
            channel_index & 0xFF,
            orientation_type & 0xFF,
            orientation_int & 0xFF,
            (orientation_int >> 8) & 0xFF,
            intensity & 0xFF,
            on_duration_ms & 0xFF,
            (on_duration_ms >> 8) & 0xFF,
            0x01,                            # 1 pulse iteration
            0x01,                            # 1 series iteration
            on_duration_ms & 0xFF,
            (on_duration_ms >> 8) & 0xFF,
            on_duration_ms & 0xFF,
            (on_duration_ms >> 8) & 0xFF,
            0x00,                            # Timer Option (Reset)
            0x00,                            # Not exclusive
            0x00                             # Don't clear others
        ])

        self._send_raw(command_bytes)
        return True

    def send_pulse_command(self, channel_index, intensity, orientation_type, orientation, on_duration_ms, pulse_period, pulse_iterations, series_period, **kwargs):
        intensity = max(0, min(int(intensity), 100))
        orientation_int = self._normalize_orientation(orientation_type, orientation)

        command_bytes = bytes([
            0x40, channel_index & 0xFF, orientation_type & 0xFF,
            orientation_int & 0xFF, (orientation_int >> 8) & 0xFF,
            intensity & 0xFF, on_duration_ms & 0xFF, (on_duration_ms >> 8) & 0xFF,
            pulse_iterations & 0xFF, 0x01, pulse_period & 0xFF, (pulse_period >> 8) & 0xFF,
            series_period & 0xFF, (series_period >> 8) & 0xFF, 0x00, 0x00, 0x00
        ])
        self._send_raw(command_bytes)
        return True

    def stop_vibration(self, **kwargs):
        self._send_raw(bytes([0x30, 0xFF]))
        return True

    @staticmethod
    def _normalize_orientation(orientation_type, orientation) -> int:
        """Match pybelt range rules for each orientation type."""
        orientation_int = int(orientation)
        if orientation_type in (ANGLE, MAGNETIC_BEARING):
            return orientation_int % 360
        if orientation_type == MOTOR_INDEX:
            return orientation_int % 16
        # BINARY_MASK: leave bit pattern unchanged
        return orientation_int

    def _send_raw(self, byte_array):
        if self.result_queue is not None and not self.result_queue.full():
            b64_str = base64.b64encode(byte_array).decode('utf-8')
            self.result_queue.put({
                "vibration_command": b64_str,
                "target_device": self.target_device
            })

    def disconnect_belt(self):
        self.stop_vibration()
