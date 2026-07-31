"""
Belt adapter — approach / walk-to-target guidance with simple avoidance.

Role in the dual-device pipeline
--------------------------------
While the target is farther than the handoff distance, the naviBelt steers
the user toward the object (bearing around the waist + intensity from range).
Once the user is close enough, the belt stops so the bracelet can take over
for hand→object grasping.

If a single standing obstacle appears in the forward depth corridor (target
still visible behind it), navigation splits into two steps:
  Phase A — steer toward a side waypoint that clears the obstacle
  Phase B — once the corridor is clear (user has crossed), steer to target
"""
from __future__ import annotations

import math
import time
from typing import Optional, Tuple

import numpy as np

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

    # Forward-corridor obstacle detection (approach phase only).
    # Tuned for standing blockers; floor/table band is excluded.
    CORRIDOR_HALF_WIDTH_FRAC = 0.10
    CORRIDOR_HALF_WIDTH_FRAC_HOLD = 0.18  # wider while already avoiding
    CORRIDOR_Y0_FRAC = 0.22
    CORRIDOR_Y1_FRAC = 0.55
    OBSTACLE_MARGIN_M = 0.40
    OBSTACLE_MAX_DEPTH_M = 2.00
    OBSTACLE_FRACTION_ENTER = 0.22
    OBSTACLE_FRACTION_EXIT = 0.06
    OBSTACLE_MIN_VALID = 40
    TARGET_BBOX_CLEAR_FRAC = 0.35

    # Avoidance waypoint (Phase A)
    SAFE_MARGIN_M = 0.20
    ASSUMED_HFOV_DEG = 60.0
    CLEAR_FRAMES_TO_EXIT = 30         # sustained clear before Phase B
    TARGET_MISS_TOLERANCE = 20        # don't abort avoidance if YOLO drops target briefly
    TARGET_SIDE_DEADZONE_PX = 20

    def __init__(self, virtual_belt_controller):
        self._virtual_belt = virtual_belt_controller
        self._connected = True
        self._currently_vibrating = False
        self._in_approach = False

        self._last_cmd_time = 0.0
        self._last_angle: Optional[float] = None
        self._last_intensity: Optional[int] = None
        self._signaled_handoff = False

        # Avoidance state (single static obstacle)
        self._avoidance_active = False
        self._avoid_side: Optional[str] = None  # 'left' | 'right'
        self._clear_frames = 0
        self._target_miss_frames = 0
        self._obstacle_depth_m: Optional[float] = None
        self._last_obs_cx: Optional[float] = None
        self._last_target = None
        # Latest geometry for OpenCV / console debug overlay
        self._debug_viz: dict = {}
        self._last_logged_phase: Optional[str] = None

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

        target = None
        if ctx.raw_detections:
            target = next(
                (det for det in ctx.raw_detections
                 if int(det[5]) == int(ctx.target_class_id)),
                None,
            )

        # Brief YOLO dropouts are common when a chair occludes the bottle.
        # Keep avoidance alive instead of resetting (that caused log flicker).
        if target is None:
            self._target_miss_frames += 1
            if (self._avoidance_active
                    and self._target_miss_frames <= self.TARGET_MISS_TOLERANCE
                    and self._last_target is not None):
                return self._steer_hold_avoidance(ctx, self._last_target)
            self._idle_stop()
            return None

        self._target_miss_frames = 0
        self._last_target = target

        depth_m = float(target[7]) if len(target) > 7 else -1.0
        depth_cm = depth_m * 100.0 if depth_m > 0 else -1.0

        # --- Phase selection with hysteresis ---
        if depth_cm > 0:
            if self._in_approach:
                if depth_cm <= HANDOFF_ENTER_CM:
                    self._enter_handoff()
                    return target
            else:
                if depth_cm >= HANDOFF_EXIT_CM:
                    self._in_approach = True
                    self._signaled_handoff = False
                else:
                    if self._currently_vibrating:
                        self.stop()
                        self._currently_vibrating = False
                    return target
        else:
            # Unknown depth: keep guiding by bearing
            self._in_approach = True

        if not self._in_approach:
            return target

        return self._steer_approach_or_avoid(ctx, target, depth_m, depth_cm)

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
            'avoidance_active': self._avoidance_active,
            'avoid_side': self._avoid_side,
            'obstacle_depth_m': self._obstacle_depth_m,
            'debug_viz': dict(self._debug_viz),
        }

    def get_debug_viz(self) -> dict:
        """Geometry snapshot for the testing OpenCV overlay."""
        return dict(self._debug_viz)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _enter_handoff(self) -> None:
        self._in_approach = False
        self._reset_avoidance('handoff')
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
        self._target_miss_frames = 0
        self._last_target = None
        self._reset_avoidance('target lost')

    def _reset_avoidance(self, reason: str = 'obstacle cleared') -> None:
        was_active = self._avoidance_active
        self._avoidance_active = False
        self._avoid_side = None
        self._clear_frames = 0
        self._obstacle_depth_m = None
        self._last_obs_cx = None
        if was_active and self._last_logged_phase != 'approach':
            self._last_logged_phase = 'approach'
            print(f'[BeltAvoid] phase=approach ({reason})')

    def _steer_hold_avoidance(self, ctx: NavigationContext, target) -> object:
        """Keep Phase-A steering when the target bbox is briefly missing."""
        depth_m = float(target[7]) if len(target) > 7 else -1.0
        depth_cm = depth_m * 100.0 if depth_m > 0 else -1.0
        frame_w, frame_h = self._frame_size(ctx)
        x0, x1, y0, y1 = self._corridor_bounds(frame_h, frame_w)
        if self._last_obs_cx is not None and self._obstacle_depth_m:
            steer_x = self._waypoint_x(
                self._last_obs_cx, self._obstacle_depth_m, frame_w,
                self._avoid_side or 'right')
            obs_cx, obs_depth = self._last_obs_cx, self._obstacle_depth_m
        else:
            steer_x = float(target[0])
            obs_cx = obs_depth = None
        angle_deg = self._bearing_deg(steer_x, frame_w)
        intensity = self._intensity_for_depth(depth_cm, ctx)
        self._update_debug_viz(
            phase='avoid_A_hold',
            frame_w=frame_w, frame_h=frame_h,
            target_x=float(target[0]), target_y=float(target[1]),
            target_depth_m=depth_m, steer_x=steer_x, angle_deg=angle_deg,
            intensity=intensity, obs_cx=obs_cx, obs_depth=obs_depth,
            corridor=(x0, y0, x1, y1),
        )
        now = time.time()
        if self._should_send(now, angle_deg, intensity):
            self._vibrate(angle_deg, intensity)
            self._currently_vibrating = True
            self._last_cmd_time = now
            self._last_angle = angle_deg
            self._last_intensity = intensity
        return target

    def _steer_approach_or_avoid(
        self, ctx: NavigationContext, target, depth_m: float, depth_cm: float,
    ) -> object:
        frame_w, frame_h = self._frame_size(ctx)
        steer_x = float(target[0])
        phase = 'approach'
        obs_cx = obs_depth = None
        x0, x1, y0, y1 = self._corridor_bounds(frame_h, frame_w)
        corridor = (x0, y0, x1, y1)

        min_frac = (
            self.OBSTACLE_FRACTION_EXIT if self._avoidance_active
            else self.OBSTACLE_FRACTION_ENTER
        )
        obs = None
        if depth_m > 0:
            obs = self._detect_obstacle(ctx, target, depth_m, min_frac)

        if obs is not None:
            self._clear_frames = 0
            obs_cx, obs_depth, closer_mask, origin = obs
            self._last_obs_cx = obs_cx
            self._obstacle_depth_m = obs_depth
            if not self._avoidance_active:
                self._avoidance_active = True
                self._avoid_side = self._choose_avoid_side(
                    float(target[0]), obs_cx, closer_mask, origin)

            steer_x = self._waypoint_x(
                obs_cx, obs_depth, frame_w, self._avoid_side or 'right')
            phase = 'avoid_A'
        elif self._avoidance_active:
            self._clear_frames += 1
            if self._clear_frames >= self.CLEAR_FRAMES_TO_EXIT:
                self._reset_avoidance('obstacle cleared')
                steer_x = float(target[0])
                phase = 'approach'
            else:
                phase = 'avoid_B_clearing'
                if self._last_obs_cx is not None and self._obstacle_depth_m:
                    steer_x = self._waypoint_x(
                        self._last_obs_cx, self._obstacle_depth_m, frame_w,
                        self._avoid_side or 'right')
                    obs_cx = self._last_obs_cx
                    obs_depth = self._obstacle_depth_m
                else:
                    steer_x = float(target[0])
        else:
            steer_x = float(target[0])
            phase = 'approach'

        angle_deg = self._bearing_deg(steer_x, frame_w)
        intensity = self._intensity_for_depth(depth_cm, ctx)
        self._update_debug_viz(
            phase=phase,
            frame_w=frame_w, frame_h=frame_h,
            target_x=float(target[0]), target_y=float(target[1]),
            target_depth_m=depth_m, steer_x=steer_x, angle_deg=angle_deg,
            intensity=intensity, obs_cx=obs_cx, obs_depth=obs_depth,
            corridor=corridor,
        )
        now = time.time()
        if self._should_send(now, angle_deg, intensity):
            self._vibrate(angle_deg, intensity)
            self._currently_vibrating = True
            self._last_cmd_time = now
            self._last_angle = angle_deg
            self._last_intensity = intensity
        return target

    def _update_debug_viz(
        self,
        phase: str,
        frame_w: int,
        frame_h: int,
        target_x: float,
        target_y: float,
        target_depth_m: float,
        steer_x: float,
        angle_deg: float,
        intensity: int,
        obs_cx: Optional[float],
        obs_depth: Optional[float],
        corridor: Optional[tuple],
    ) -> None:
        self._debug_viz = {
            'phase': phase,
            'frame_w': frame_w,
            'frame_h': frame_h,
            'target_xy': (target_x, target_y),
            'target_depth_m': target_depth_m,
            'steer_x': steer_x,
            'angle_deg': angle_deg,
            'intensity': intensity,
            'avoid_side': self._avoid_side,
            'obs_cx': obs_cx,
            'obs_depth_m': obs_depth,
            'corridor': corridor,  # (x0, y0, x1, y1)
            'clear_frames': self._clear_frames,
        }
        if phase != self._last_logged_phase:
            self._last_logged_phase = phase
            side = self._avoid_side or '-'
            obs_d = f'{obs_depth:.2f}m' if obs_depth is not None else '-'
            print(
                f'[BeltAvoid] phase={phase} side={side} '
                f'obs_depth={obs_d} steer_x={steer_x:.0f} '
                f'angle={angle_deg:.0f}° target_depth={target_depth_m:.2f}m'
            )

    def _corridor_bounds(self, h: int, w: int) -> Tuple[int, int, int, int]:
        half = (
            self.CORRIDOR_HALF_WIDTH_FRAC_HOLD if self._avoidance_active
            else self.CORRIDOR_HALF_WIDTH_FRAC
        )
        x0 = max(0, int(w * (0.5 - half)))
        x1 = min(w, int(w * (0.5 + half)))
        y0 = max(0, int(h * self.CORRIDOR_Y0_FRAC))
        y1 = min(h, int(h * self.CORRIDOR_Y1_FRAC))
        return x0, x1, y0, y1

    def _detect_obstacle(
        self,
        ctx: NavigationContext,
        target,
        target_depth_m: float,
        min_fraction: float,
    ) -> Optional[Tuple[float, float, np.ndarray, Tuple[int, int]]]:
        """
        Returns (obstacle_cx, obstacle_depth_m, closer_mask_roi, (x0, y0))
        or None if the forward corridor is clear.
        """
        depth = ctx.depth_img
        if depth is None or not hasattr(depth, 'shape') or depth.size == 0:
            return None
        if target_depth_m <= 0:
            return None

        h, w = depth.shape[:2]
        x0, x1, y0, y1 = self._corridor_bounds(h, w)
        if x1 <= x0 or y1 <= y0:
            return None

        roi = np.asarray(depth[y0:y1, x0:x1], dtype=np.float32)

        tx, ty, tw, th = [float(v) for v in target[:4]]
        clear = max(tw, th) * self.TARGET_BBOX_CLEAR_FRAC
        tgt_l = tx - tw / 2 - clear
        tgt_r = tx + tw / 2 + clear
        tgt_t = ty - th / 2 - clear
        tgt_b = ty + th / 2 + clear

        yy, xx = np.mgrid[y0:y1, x0:x1]
        in_target = (xx >= tgt_l) & (xx <= tgt_r) & (yy >= tgt_t) & (yy <= tgt_b)

        valid = (roi > 0.05) & (roi < 8.0) & (~in_target)
        n_valid = int(valid.sum())
        if n_valid < self.OBSTACLE_MIN_VALID:
            return None

        max_obs = min(target_depth_m - self.OBSTACLE_MARGIN_M, self.OBSTACLE_MAX_DEPTH_M)
        if max_obs <= 0.05:
            return None
        closer = valid & (roi < max_obs)
        if (float(closer.sum()) / float(n_valid)) < min_fraction:
            return None

        ys, xs = np.where(closer)
        if len(xs) == 0:
            return None

        obs_cx = float(xs.mean() + x0)
        obs_depth = float(np.median(roi[closer]))
        return obs_cx, obs_depth, closer, (x0, y0)

    def _choose_avoid_side(
        self,
        target_x: float,
        obs_cx: float,
        closer_mask: np.ndarray,
        origin: Tuple[int, int],
    ) -> str:
        """
        Pass on the side of the obstacle toward the target (shortest path).
        Compare target to obstacle center, not image center.
        """
        if target_x < obs_cx - self.TARGET_SIDE_DEADZONE_PX:
            return 'left'
        if target_x > obs_cx + self.TARGET_SIDE_DEADZONE_PX:
            return 'right'

        # Target almost behind the obstacle — side with more free corridor space
        mid = closer_mask.shape[1] // 2
        left_obs = int(closer_mask[:, :mid].sum())
        right_obs = int(closer_mask[:, mid:].sum())
        return 'left' if left_obs <= right_obs else 'right'

    def _waypoint_x(
        self,
        obs_cx: float,
        obs_depth_m: float,
        frame_w: int,
        side: str,
    ) -> float:
        """Image x of a point SAFE_MARGIN_M past the obstacle on the chosen side."""
        depth = max(obs_depth_m, 0.2)
        half_w_m = depth * math.tan(math.radians(self.ASSUMED_HFOV_DEG * 0.5))
        px_per_m = (frame_w * 0.5) / max(half_w_m, 1e-3)
        offset_px = self.SAFE_MARGIN_M * px_per_m

        if side == 'left':
            wp = obs_cx - offset_px
        else:
            wp = obs_cx + offset_px
        return float(max(0.0, min(float(frame_w - 1), wp)))

    def _frame_size(self, ctx: NavigationContext) -> tuple[int, int]:
        if ctx.depth_img is not None and ctx.depth_img.size > 0:
            h, w = ctx.depth_img.shape[:2]
            return int(w), int(h)
        if ctx.frame_shape and len(ctx.frame_shape) >= 2:
            return int(ctx.frame_shape[1]), int(ctx.frame_shape[0])
        return 640, 640

    def _bearing_deg(self, target_x: float, frame_w: int) -> float:
        """
        Map horizontal aim point to a feelSpace belt angle.

        0°   = forward (front motor)
        90°  = right
        270° = left
        """
        cx = frame_w * 0.5
        half = max(frame_w * 0.5, 1.0)
        dx_norm = (target_x - cx) / half
        if abs(dx_norm) <= self.CENTER_DEADZONE_FRAC:
            return 0.0
        dx_norm = max(-1.0, min(1.0, dx_norm))
        angle = dx_norm * 90.0
        if angle < 0:
            angle += 360.0
        return angle % 360.0

    def _intensity_for_depth(self, depth_cm: float, ctx: NavigationContext) -> int:
        base = ctx.vibration_intensities.get('belt') if ctx.vibration_intensities else None
        if base is not None:
            near_i = int(base)
            far_i = max(20, int(base * 0.55))
        else:
            near_i, far_i = self.INTENSITY_NEAR, self.INTENSITY_FAR

        if depth_cm <= 0:
            return int(0.5 * (near_i + far_i))

        span = max(MAX_APPROACH_CM - HANDOFF_ENTER_CM, 1.0)
        t = (depth_cm - HANDOFF_ENTER_CM) / span
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
            orientation_type=0,
            orientation=int(round(angle_deg)) % 360,
        )
