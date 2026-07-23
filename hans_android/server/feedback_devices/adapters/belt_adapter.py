"""
Belt adapter — approach / walk-to-target guidance.

Role in the dual-device pipeline
--------------------------------
While the target is farther than the handoff distance, the naviBelt steers
the user toward the object (bearing around the waist + intensity from range).
Once the user is close enough, the belt stops so the bracelet can take over
for hand→object grasping.
"""
from __future__ import annotations

import math
import time
from typing import Optional

from feedback_devices.base import FeedbackDevice, NavigationContext
from feedback_devices.handoff import (
    HANDOFF_ENTER_CM,
    HANDOFF_EXIT_CM,
    MAX_APPROACH_CM,
)


class BeltAdapter(FeedbackDevice):
    # Intensity: stronger when closer (more urgency to finish approach)
    INTENSITY_FAR = 40
    INTENSITY_NEAR = 75

    # Ignore small left/right offsets → keep vibrating "forward"
    CENTER_DEADZONE_FRAC = 0.08

    # Command refresh (vibration packets last ~2 s on the device)
    CMD_INTERVAL_S = 0.45
    ANGLE_RESEND_DEG = 18.0

    def __init__(self, virtual_belt_controller):
        self._virtual_belt = virtual_belt_controller
        self._connected = True
        self._currently_vibrating = False
        self._in_approach = False  # True while belt is responsible

        self._last_cmd_time = 0.0
        self._last_angle: Optional[float] = None
        self._last_intensity: Optional[int] = None
        self._signaled_handoff = False

    def connect(self) -> bool:
        self._connected = True
        return True

    def disconnect(self) -> None:
        self.stop()
        self._connected = False

    def update(self, ctx: NavigationContext) -> Optional[object]:
        if not self._connected:
            return None

        if ctx.target_class_id is None or ctx.target_class_id < 0:
            self._idle_stop()
            return None

        if not ctx.raw_detections:
            self._idle_stop()
            return None

        target = next(
            (det for det in ctx.raw_detections if int(det[5]) == int(ctx.target_class_id)),
            None,
        )
        if target is None:
            self._idle_stop()
            return None

        depth_m = float(target[7]) if len(target) > 7 else -1.0
        depth_cm = depth_m * 100.0 if depth_m > 0 else -1.0

        # --- Phase selection with hysteresis ---
        if depth_cm > 0:
            if self._in_approach:
                # Stay in approach until clearly inside handoff band
                if depth_cm <= HANDOFF_ENTER_CM:
                    self._enter_handoff()
                    return target
            else:
                # Resume approach only after exiting the hysteresis band
                if depth_cm >= HANDOFF_EXIT_CM:
                    self._in_approach = True
                    self._signaled_handoff = False
                else:
                    # Still in / below handoff zone — belt stays quiet
                    if self._currently_vibrating:
                        self.stop()
                        self._currently_vibrating = False
                    return target
        else:
            # Unknown depth: keep guiding by bearing so a missing depth sample
            # does not strand the user. Bracelet stays inactive without depth.
            self._in_approach = True

        if not self._in_approach:
            return target

        frame_w, frame_h = self._frame_size(ctx)
        tx = float(target[0])
        angle_deg = self._bearing_deg(tx, frame_w)
        intensity = self._intensity_for_depth(depth_cm, ctx)

        now = time.time()
        if self._should_send(now, angle_deg, intensity):
            self._vibrate(angle_deg, intensity)
            self._currently_vibrating = True
            self._last_cmd_time = now
            self._last_angle = angle_deg
            self._last_intensity = intensity

        return target

    def signal_event(self, event: str) -> None:
        events_map = {
            'grasped': {
                'intensity': 80, 'orientation': 0b111111,
                'duration_ms': 150, 'iterations': 5,
            },
            'target_found': {
                'intensity': 50, 'orientation': 0b010000,
                'duration_ms': 100, 'iterations': 3,
            },
            'handoff': {
                # Short all-around pulse: "bracelet takes over now"
                'intensity': 55, 'orientation': 0b111111,
                'duration_ms': 80, 'iterations': 2,
            },
            'obstacle': {
                'intensity': 40, 'orientation': 0b101000,
                'duration_ms': 100, 'iterations': 4,
            },
        }
        p = events_map.get(event)
        if p and self._virtual_belt:
            self._virtual_belt.send_pulse_command(
                channel_index=1,
                intensity=p['intensity'],
                orientation_type=1,
                orientation=p['orientation'],
                on_duration_ms=p['duration_ms'],
                pulse_period=300,
                pulse_iterations=p['iterations'],
                series_period=1000,
                series_iterations=1,
            )

    def stop(self) -> None:
        if self._virtual_belt:
            self._virtual_belt.stop_vibration()
        self._currently_vibrating = False
        self._last_angle = None
        self._last_intensity = None

    def get_status(self) -> dict:
        return {
            'connected': self._connected,
            'type': 'belt_distance',
            'battery': None,
            'vibrating': self._currently_vibrating,
            'in_approach': self._in_approach,
            'handoff_enter_cm': HANDOFF_ENTER_CM,
            'handoff_exit_cm': HANDOFF_EXIT_CM,
        }

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _enter_handoff(self) -> None:
        self._in_approach = False
        if self._currently_vibrating:
            self.stop()
        if not self._signaled_handoff:
            self.signal_event('handoff')
            self._signaled_handoff = True

    def _idle_stop(self) -> None:
        if self._currently_vibrating:
            self.stop()
        self._in_approach = False
        self._signaled_handoff = False

    def _frame_size(self, ctx: NavigationContext) -> tuple[int, int]:
        if ctx.depth_img is not None and ctx.depth_img.size > 0:
            h, w = ctx.depth_img.shape[:2]
            return int(w), int(h)
        if ctx.frame_shape and len(ctx.frame_shape) >= 2:
            # frame_shape is (H, W) like numpy
            return int(ctx.frame_shape[1]), int(ctx.frame_shape[0])
        return 640, 640

    def _bearing_deg(self, target_x: float, frame_w: int) -> float:
        """
        Map horizontal target offset to a feelSpace belt angle.

        0°   = forward (front motor)
        90°  = right
        270° = left

        Phone is assumed facing the same way as the user / belt front.
        """
        cx = frame_w * 0.5
        half = max(frame_w * 0.5, 1.0)
        dx_norm = (target_x - cx) / half  # -1 .. +1 (left .. right)
        if abs(dx_norm) <= self.CENTER_DEADZONE_FRAC:
            return 0.0
        dx_norm = max(-1.0, min(1.0, dx_norm))
        # ±90° around forward
        angle = dx_norm * 90.0
        if angle < 0:
            angle += 360.0
        return angle % 360.0

    def _intensity_for_depth(self, depth_cm: float, ctx: NavigationContext) -> int:
        # Prefer calibrated belt intensity if the app sent one
        base = ctx.vibration_intensities.get('belt') if ctx.vibration_intensities else None
        if base is not None:
            near_i = int(base)
            far_i = max(20, int(base * 0.55))
        else:
            near_i, far_i = self.INTENSITY_NEAR, self.INTENSITY_FAR

        if depth_cm <= 0:
            return int(0.5 * (near_i + far_i))

        span = max(MAX_APPROACH_CM - HANDOFF_ENTER_CM, 1.0)
        t = (depth_cm - HANDOFF_ENTER_CM) / span  # 0 at handoff, 1 at far
        t = max(0.0, min(1.0, t))
        return int(round(near_i * (1.0 - t) + far_i * t))

    def _should_send(self, now: float, angle_deg: float, intensity: int) -> bool:
        if not self._currently_vibrating:
            return True
        if now - self._last_cmd_time >= self.CMD_INTERVAL_S:
            return True
        if self._last_angle is not None:
            diff = abs(angle_deg - self._last_angle)
            if diff > 180:
                diff = 360 - diff
            if diff >= self.ANGLE_RESEND_DEG:
                return True
        if self._last_intensity is not None and abs(intensity - self._last_intensity) >= 12:
            return True
        return False

    def _vibrate(self, angle_deg: float, intensity: int) -> None:
        if not self._virtual_belt:
            return
        self._virtual_belt.send_vibration_command(
            channel_index=1,
            pattern=0,
            intensity=max(0, min(100, intensity)),
            orientation_type=0,          # ANGLE mode around the waist
            orientation=int(round(angle_deg)) % 360,
        )
