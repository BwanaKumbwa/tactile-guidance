from __future__ import annotations
import time
from typing import Optional
from feedback_devices.base import FeedbackDevice, NavigationContext

class BeltAdapter(FeedbackDevice):
    DISTANCE_THRESHOLD_CM = 70.0  
    FORWARD_INTENSITY = 60        
    FORWARD_ORIENTATION = 0b010000  # Use motor bitmask for the front motor

    def __init__(self, virtual_belt_controller):
        self._virtual_belt = virtual_belt_controller
        self._connected = True
        self._currently_vibrating = False
        
        # ✅ Throttle tracker
        self._last_cmd_time = 0.0

    def connect(self) -> bool:
        self._connected = True
        return True

    def disconnect(self) -> None:
        if self._virtual_belt:
            self._virtual_belt.stop_vibration()
        self._connected = False

    def update(self, ctx: NavigationContext) -> Optional[object]:
        if not self._connected:
            return None
        
        if ctx.raw_detections is None or len(ctx.raw_detections) == 0:
            if self._currently_vibrating:
                self.stop()
                self._currently_vibrating = False
            return None
        
        target = next((det for det in ctx.raw_detections if det[5] == ctx.target_class_id), None)
        
        if target is None:
            if self._currently_vibrating:
                self.stop()
                self._currently_vibrating = False
            return None
        
        target_depth_cm = float(target[7]) * 100.0 if len(target) > 7 else -1.0
        
        if target_depth_cm < 0:
            if self._currently_vibrating:
                self.stop()
                self._currently_vibrating = False
            return target
        
        if target_depth_cm > self.DISTANCE_THRESHOLD_CM:
            now = time.time()
            if not self._currently_vibrating:
                self._vibrate_forward()
                self._currently_vibrating = True
                self._last_cmd_time = now
            else:
                # THROTTLE: Refresh the 2-second command every 1 second
                if now - self._last_cmd_time > 1.0:
                    self._vibrate_forward()
                    self._last_cmd_time = now
        else:
            if self._currently_vibrating:
                self.stop()
                self._currently_vibrating = False
        
        return target

    def signal_event(self, event: str) -> None:
        events_map = {
            'grasped': {'intensity': 80, 'orientation': 0b111111, 'duration_ms': 150, 'iterations': 5},
            'target_found': {'intensity': 50, 'orientation': 0b010000, 'duration_ms': 100, 'iterations': 3},
            'obstacle': {'intensity': 40, 'orientation': 0b101000, 'duration_ms': 100, 'iterations': 4},
        }
        p = events_map.get(event)
        if p and self._virtual_belt:
            self._virtual_belt.send_pulse_command(
                channel_index=1, intensity=p['intensity'], orientation_type=1,
                orientation=p['orientation'], on_duration_ms=p['duration_ms'],
                pulse_period=300, pulse_iterations=p['iterations'],
                series_period=1000, series_iterations=1,
            )

    def stop(self) -> None:
        if self._virtual_belt:
            self._virtual_belt.stop_vibration()

    def get_status(self) -> dict:
        return {'connected': self._connected, 'type': 'belt_distance', 'battery': None, 'vibrating': self._currently_vibrating}

    def _vibrate_forward(self) -> None:
        if self._virtual_belt:
            self._virtual_belt.send_vibration_command(
                channel_index=1, pattern=0, intensity=self.FORWARD_INTENSITY,
                orientation_type=1, orientation=self.FORWARD_ORIENTATION, # Changed to type 1 (Motor Index)
            )