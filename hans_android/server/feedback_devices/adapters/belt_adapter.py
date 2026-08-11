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

For the static-scene thesis setup (bottle and obstacle do not move), a
navigation *plan* is locked after a short stability window: pass side and
obstacle geometry freeze, while waist steering still uses a smoothed live
bearing so the user can walk the plan without frame-to-frame re-planning.
"""
from __future__ import annotations

import json
import math
import time
from pathlib import Path
from typing import Optional, Tuple

import numpy as np

from feedback_devices.base import FeedbackDevice, NavigationContext
from feedback_devices.handoff import (
    HANDOFF_ENTER_CM,
    HANDOFF_EXIT_CM,
    MAX_APPROACH_CM,
    PRE_HANDOFF_CM,
)
from feedback_devices.orientation import ANGLE, BINARY_MASK, MOTOR_INDEX


# feelSpace naviBelt: 16 motors. Probe them with MOTOR_INDEX (0..15).
# Correct pybelt types: BINARY_MASK=0, MOTOR_INDEX=1, ANGLE=2.
MOTOR_COUNT = 16
MOTOR_STEP_DEG = 360.0 / MOTOR_COUNT  # 22.5°

# Body directions used for navigation (degrees, 0 = navel / green marker).
CARDINAL_BODY_ANGLES = {
    'front': 0,
    'center': 0,
    'straight': 0,
    'navel': 0,
    'right': 90,
    'back': 180,
    'left': 270,
}

_NAVEL_CALIB_PATH = Path(__file__).resolve().parents[2] / 'results' / 'belt_navel_motor.json'

class BeltAdapter(FeedbackDevice):
    # Intensity: stronger when closer (more urgency to finish approach)
    INTENSITY_FAR = 40
    INTENSITY_NEAR = 75

    # Ignore small left/right offsets → keep vibrating "forward"
    CENTER_DEADZONE_FRAC = 0.08

    # Command refresh (vibration packets last ~2 s on the device)
    CMD_INTERVAL_S = 0.45
    ANGLE_RESEND_DEG = 18.0

    MOTOR_TEST_DURATION_S = 1.4
    MOTOR_TEST_GAP_S = 0.45
    MOTOR_TEST_INTENSITY = 60

    # Let discrete nav cues finish before continuous steering resumes
    CUE_HOLD_S = 0.85
    DIR_CHANGE_HOLD_S = 0.45
    DIR_CHANGE_MIN_INTERVAL_S = 1.2

    # Freeze plan after this many consistent frames (static bottle + obstacle)
    PLAN_LOCK_FRAMES = 10
    # EMA on steering angle (0 = raw, 1 = frozen); circular blend
    BEARING_SMOOTH_ALPHA = 0.35

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

        # While > now, navigation / cues must not overwrite calibration vibrations
        self._calibration_until = 0.0

        # Nav cues (start / direction_change / pre-handoff / handoff)
        self._nav_started = False
        self._signaled_pre_handoff = False
        self._cue_hold_until = 0.0
        self._last_cue: Optional[str] = None
        self._last_dir_sector: Optional[str] = None
        self._last_dir_change_time = 0.0

        # Motor calibration (green-marker / navel)
        self._probe_index = 0
        self._navel_motor_index: Optional[int] = None
        self._motor_dir = 1
        self._load_navel_calibration()

        # Avoidance + frozen plan (static bottle / obstacle thesis setup)
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

        # Plan lock: strategy freezes; steering bearing stays live+smoothed
        self._plan_locked = False
        self._plan_has_obstacle = False
        self._plan_obstacle_done = False
        self._plan_obs_stable = 0
        self._plan_clear_stable = 0
        self._smoothed_angle: Optional[float] = None

    def connect(self) -> bool:
        self._connected = True
        return True

    def disconnect(self) -> None:
        self.stop()
        self._connected = False

    def update(self, ctx: NavigationContext) -> Optional[object]:
        if not self._connected:
            return None

        # Motor calibration takes exclusive control of the belt
        if time.time() < self._calibration_until:
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
        """
        Discrete belt cues:
          nav_start         — navigation begins (3 navel pulses)
          direction_change  — coarse L/C/R sector flipped (1 short all-around pulse)
          pre_handoff       — bracelet handoff soon (4 short navel pulses)
          handoff           — entered ≤50 cm grasp zone (all-around pulse)
        """
        navel = self._navel_motor_index if self._navel_motor_index is not None else 0

        # Navel / front cues via MOTOR_INDEX (respects green-marker calibration)
        if event in ('nav_start', 'pre_handoff'):
            iters = 3 if event == 'nav_start' else 4
            duration_ms = 120 if event == 'nav_start' else 70
            inten = 50 if event == 'nav_start' else 60
            if self._virtual_belt:
                self._virtual_belt.send_pulse_command(
                    channel_index=1,
                    intensity=inten,
                    orientation_type=MOTOR_INDEX,
                    orientation=navel,
                    on_duration_ms=duration_ms,
                    pulse_period=280,
                    pulse_iterations=iters,
                    series_period=1000,
                    series_iterations=1,
                )
            self._arm_cue_hold(event)
            return

        if event == 'direction_change':
            # One short all-around tap: "turn — follow the continuous buzz"
            if self._virtual_belt:
                self._virtual_belt.send_pulse_command(
                    channel_index=1,
                    intensity=50,
                    orientation_type=BINARY_MASK,
                    orientation=0b111111,
                    on_duration_ms=90,
                    pulse_period=280,
                    pulse_iterations=1,
                    series_period=1000,
                    series_iterations=1,
                )
            self._arm_cue_hold(event, hold_s=self.DIR_CHANGE_HOLD_S)
            return

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
                orientation_type=BINARY_MASK,
                orientation=p['orientation'],
                on_duration_ms=p['duration_ms'],
                pulse_period=300,
                pulse_iterations=p['iterations'],
                series_period=1000,
                series_iterations=1,
            )
            if event == 'handoff':
                self._arm_cue_hold(event)

    # ------------------------------------------------------------------
    # Motor calibration via MOTOR_INDEX (one physical motor at a time)
    # ------------------------------------------------------------------

    def test_motor_index(self, index: int, intensity: Optional[int] = None,
                         duration_s: Optional[float] = None) -> dict:
        """
        Vibrate exactly one motor by feelSpace motor index (0 .. 15).
        Put a finger on the green navel marker and note which index matches.
        """
        idx = int(index) % MOTOR_COUNT
        self._probe_index = idx
        inten = int(intensity if intensity is not None else self.MOTOR_TEST_INTENSITY)
        hold = float(duration_s if duration_s is not None else self.MOTOR_TEST_DURATION_S)

        self._calibration_until = max(self._calibration_until, time.time() + hold + 0.4)
        if self._virtual_belt:
            self._virtual_belt.stop_vibration()
            self._virtual_belt.send_vibration_command(
                channel_index=1, pattern=0, intensity=inten,
                orientation_type=MOTOR_INDEX,
                orientation=idx,
            )
        navel_note = ''
        if self._navel_motor_index is not None and idx == self._navel_motor_index:
            navel_note = '  << currently marked as NAVEL / green marker'
        print(f'[BeltMotorTest] MOTOR INDEX {idx:2d}/15{navel_note}')
        print('                 Finger on GREEN marker — is this the vibrating one? '
              'If yes, press M')
        time.sleep(hold)
        if self._virtual_belt:
            self._virtual_belt.stop_vibration()
        self._currently_vibrating = False
        self._last_angle = None
        return {
            'ok': True,
            'motor_index': idx,
            'intensity': inten,
            'navel_motor_index': self._navel_motor_index,
        }

    def probe_next(self, intensity: Optional[int] = None) -> dict:
        return self.test_motor_index((self._probe_index + 1) % MOTOR_COUNT, intensity=intensity)

    def probe_prev(self, intensity: Optional[int] = None) -> dict:
        return self.test_motor_index((self._probe_index - 1) % MOTOR_COUNT, intensity=intensity)

    def probe_current(self, intensity: Optional[int] = None) -> dict:
        return self.test_motor_index(self._probe_index, intensity=intensity)

    def set_navel_motor(self, index: Optional[int] = None) -> dict:
        """
        Mark a motor index as the green-marker / navel position.
        If index is None, uses the motor currently being probed.
        """
        idx = self._probe_index if index is None else int(index) % MOTOR_COUNT
        self._navel_motor_index = idx
        self._save_navel_calibration()
        print(f'[BeltMotorTest] NAVEL set to motor index {idx}.')
        print('                 Right = +90° from navel, Left = -90° / +270°.')
        print('                 Press V to verify: navel → right → left.')
        # Brief confirmation buzz on that motor
        self.test_motor_index(idx, duration_s=0.7)
        return {
            'ok': True,
            'navel_motor_index': idx,
            'motor_dir': self._motor_dir,
        }

    def flip_motor_direction(self) -> dict:
        """Swap whether increasing index goes toward the right or the left."""
        self._motor_dir = -1 if self._motor_dir >= 0 else 1
        self._save_navel_calibration()
        side = 'right' if self._motor_dir > 0 else 'left'
        print(f'[BeltMotorTest] motor_dir={self._motor_dir} '
              f'(increasing index → body {side}). Press V to re-check.')
        return {'ok': True, 'motor_dir': self._motor_dir,
                'navel_motor_index': self._navel_motor_index}

    def test_direction(self, which: str, intensity: Optional[int] = None) -> dict:
        """Vibrate a body direction using the navel calibration (bitmask)."""
        key = which.strip().lower()
        if key not in CARDINAL_BODY_ANGLES:
            return {
                'ok': False,
                'error': f'unknown direction {which!r}',
                'valid': sorted(CARDINAL_BODY_ANGLES),
            }
        body_angle = CARDINAL_BODY_ANGLES[key]
        if self._navel_motor_index is None:
            print('[BeltMotorTest] No navel motor set yet — using ANGLE mode fallback. '
                  'Run motor sweep (0) and press M on the green-marker motor.')
            return self._test_angle_fallback(body_angle, intensity=intensity)

        idx = self._motor_index_for_body_angle(body_angle)
        print(f'[BeltMotorTest] body {key} ({body_angle}°) → motor index {idx}')
        out = self.test_motor_index(idx, intensity=intensity)
        out['body_direction'] = key
        out['body_angle_deg'] = body_angle
        return out

    def test_cardinals(self, intensity: Optional[int] = None) -> dict:
        """navel/front → right → back → left (uses calibration if set)."""
        results = []
        order = ('front', 'right', 'back', 'left')
        total = len(order) * (self.MOTOR_TEST_DURATION_S + self.MOTOR_TEST_GAP_S) + 1.0
        self._calibration_until = time.time() + total
        print('[BeltMotorTest] verify cardinals: front → right → back → left')
        for name in order:
            results.append(self.test_direction(name, intensity=intensity))
            time.sleep(self.MOTOR_TEST_GAP_S)
        print('[BeltMotorTest] cardinals done.')
        return {'ok': True, 'results': results,
                'navel_motor_index': self._navel_motor_index}

    def test_all_motors(self, intensity: Optional[int] = None) -> dict:
        """
        Vibrate every motor by INDEX (bitmask). Wear the belt with the green
        marker on the navel; note which index buzzes under that marker, then
        press M while that motor is selected (or call set_navel_motor).
        """
        results = []
        total = MOTOR_COUNT * (self.MOTOR_TEST_DURATION_S + self.MOTOR_TEST_GAP_S) + 1.5
        self._calibration_until = time.time() + total
        print(f'[BeltMotorTest] INDEX sweep: {MOTOR_COUNT} motors (MOTOR_INDEX 0..15).')
        print('                 Green marker = navel. Remember the index that '
              'vibrates there, then press M.')
        for i in range(MOTOR_COUNT):
            print(f'[BeltMotorTest] --- {i + 1}/{MOTOR_COUNT} ---')
            results.append(self.test_motor_index(i, intensity=intensity))
            time.sleep(self.MOTOR_TEST_GAP_S)
        print('[BeltMotorTest] sweep done. Step with ] / [ then press M on the '
              'green-marker motor, or: POST /belt/set_navel {"index": N}')
        return {'ok': True, 'motor_count': MOTOR_COUNT, 'results': results}

    def _test_angle_fallback(self, angle_deg: float,
                             intensity: Optional[int] = None) -> dict:
        angle = int(round(angle_deg)) % 360
        inten = int(intensity if intensity is not None else self.MOTOR_TEST_INTENSITY)
        hold = self.MOTOR_TEST_DURATION_S
        self._calibration_until = max(self._calibration_until, time.time() + hold + 0.35)
        if self._virtual_belt:
            self._virtual_belt.stop_vibration()
            self._virtual_belt.send_vibration_command(
                channel_index=1, pattern=0, intensity=inten,
                orientation_type=ANGLE, orientation=angle,
            )
        print(f'[BeltMotorTest] ANGLE fallback {angle}°')
        time.sleep(hold)
        if self._virtual_belt:
            self._virtual_belt.stop_vibration()
        self._currently_vibrating = False
        return {'ok': True, 'angle_deg': angle, 'mode': 'angle_fallback'}

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
            'pre_handoff_cm': PRE_HANDOFF_CM,
            'avoidance_active': self._avoidance_active,
            'avoid_side': self._avoid_side,
            'obstacle_depth_m': self._obstacle_depth_m,
            'debug_viz': dict(self._debug_viz),
            'calibrating': time.time() < self._calibration_until,
            'navel_motor_index': self._navel_motor_index,
            'probe_index': self._probe_index,
            'motor_dir': self._motor_dir,
            'last_cue': self._last_cue,
            'plan_locked': self._plan_locked,
            'plan_has_obstacle': self._plan_has_obstacle,
            'plan_obstacle_done': self._plan_obstacle_done,
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
        self._reset_plan()
        if self._currently_vibrating:
            self.stop()
        self._signaled_pre_handoff = True
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
        self._reset_plan()
        self._reset_nav_cues()

    def _reset_avoidance(self, reason: str = 'obstacle cleared') -> None:
        was_active = self._avoidance_active
        self._avoidance_active = False
        # Keep _avoid_side if the plan is locked (strategy must not flip)
        if not self._plan_locked:
            self._avoid_side = None
            self._obstacle_depth_m = None
            self._last_obs_cx = None
        self._clear_frames = 0
        if was_active and self._last_logged_phase != 'approach':
            self._last_logged_phase = 'approach'
            print(f'[BeltAvoid] phase=approach ({reason})')

    def _reset_plan(self) -> None:
        self._plan_locked = False
        self._plan_has_obstacle = False
        self._plan_obstacle_done = False
        self._plan_obs_stable = 0
        self._plan_clear_stable = 0
        self._smoothed_angle = None
        self._avoid_side = None
        self._obstacle_depth_m = None
        self._last_obs_cx = None

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
        angle_deg = self._smooth_bearing(self._bearing_deg(steer_x, frame_w))
        intensity = self._intensity_for_depth(depth_cm, ctx)
        self._maybe_emit_nav_cues(depth_cm, angle_deg)
        self._update_debug_viz(
            phase='avoid_A_hold',
            frame_w=frame_w, frame_h=frame_h,
            target_x=float(target[0]), target_y=float(target[1]),
            target_depth_m=depth_m, steer_x=steer_x, angle_deg=angle_deg,
            intensity=intensity, obs_cx=obs_cx, obs_depth=obs_depth,
            corridor=(x0, y0, x1, y1),
        )
        now = time.time()
        if now < self._cue_hold_until:
            return target
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

        # Live corridor check (used to lock plan and to know when user has passed)
        min_frac = (
            self.OBSTACLE_FRACTION_EXIT
            if (self._avoidance_active or self._plan_has_obstacle)
            else self.OBSTACLE_FRACTION_ENTER
        )
        obs = None
        if depth_m > 0:
            obs = self._detect_obstacle(ctx, target, depth_m, min_frac)

        if not self._plan_locked:
            self._accumulate_plan_lock(target, obs)
            # Pre-lock: behave like before (live), but do not flip side once chosen
            if obs is not None:
                obs_cx, obs_depth, closer_mask, origin = obs
                self._last_obs_cx = obs_cx
                self._obstacle_depth_m = obs_depth
                self._clear_frames = 0
                if not self._avoidance_active:
                    self._avoidance_active = True
                    if self._avoid_side is None:
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
        else:
            # Locked plan: never re-choose side; use frozen obstacle geometry
            if self._plan_has_obstacle and not self._plan_obstacle_done:
                if obs is not None:
                    self._clear_frames = 0
                    # Keep live centroid only for debug; steering uses freeze
                    obs_cx, obs_depth = obs[0], obs[1]
                else:
                    self._clear_frames += 1
                    obs_cx = self._last_obs_cx
                    obs_depth = self._obstacle_depth_m

                if self._clear_frames >= self.CLEAR_FRAMES_TO_EXIT:
                    self._plan_obstacle_done = True
                    self._avoidance_active = False
                    self._clear_frames = 0
                    steer_x = float(target[0])
                    phase = 'approach'
                    print('[BeltPlan] obstacle cleared — resume target approach '
                          f'(side was {self._avoid_side})')
                else:
                    self._avoidance_active = True
                    if self._last_obs_cx is not None and self._obstacle_depth_m:
                        steer_x = self._waypoint_x(
                            self._last_obs_cx, self._obstacle_depth_m, frame_w,
                            self._avoid_side or 'right')
                        obs_cx = self._last_obs_cx
                        obs_depth = self._obstacle_depth_m
                    else:
                        steer_x = float(target[0])
                    phase = 'avoid_A' if obs is not None else 'avoid_B_clearing'
            else:
                # Direct approach (no obstacle in plan, or already cleared)
                self._avoidance_active = False
                steer_x = float(target[0])
                phase = 'approach'

        angle_deg = self._smooth_bearing(self._bearing_deg(steer_x, frame_w))
        intensity = self._intensity_for_depth(depth_cm, ctx)
        self._maybe_emit_nav_cues(depth_cm, angle_deg)
        self._update_debug_viz(
            phase=phase,
            frame_w=frame_w, frame_h=frame_h,
            target_x=float(target[0]), target_y=float(target[1]),
            target_depth_m=depth_m, steer_x=steer_x, angle_deg=angle_deg,
            intensity=intensity, obs_cx=obs_cx, obs_depth=obs_depth,
            corridor=corridor,
        )
        now = time.time()
        if now < self._cue_hold_until:
            return target
        if self._should_send(now, angle_deg, intensity):
            self._vibrate(angle_deg, intensity)
            self._currently_vibrating = True
            self._last_cmd_time = now
            self._last_angle = angle_deg
            self._last_intensity = intensity
        return target

    def _accumulate_plan_lock(self, target, obs) -> None:
        """
        Lock strategy after PLAN_LOCK_FRAMES of either consistent obstacle or
        clear corridor. Static thesis scenes: bottle and obstacle do not move.

        Once avoidance has started (side chosen), never lock as "direct" —
        finish locking the obstacle plan even while the corridor is clearing.
        """
        if obs is not None:
            self._plan_obs_stable += 1
            self._plan_clear_stable = 0
            obs_cx, obs_depth, closer_mask, origin = obs
            if self._avoid_side is None:
                self._avoid_side = self._choose_avoid_side(
                    float(target[0]), obs_cx, closer_mask, origin)
            # Refresh freeze candidates until lock
            self._last_obs_cx = obs_cx
            self._obstacle_depth_m = obs_depth
            if self._plan_obs_stable >= self.PLAN_LOCK_FRAMES:
                self._lock_plan(has_obstacle=True)
        elif self._avoid_side is not None:
            # Obstacle plan in progress (clearing / holding) — keep locking it
            self._plan_obs_stable += 1
            self._plan_clear_stable = 0
            if self._plan_obs_stable >= self.PLAN_LOCK_FRAMES:
                self._lock_plan(has_obstacle=True)
        else:
            self._plan_clear_stable += 1
            self._plan_obs_stable = 0
            if self._plan_clear_stable >= self.PLAN_LOCK_FRAMES:
                self._lock_plan(has_obstacle=False)

    def _lock_plan(self, has_obstacle: bool) -> None:
        self._plan_locked = True
        self._plan_has_obstacle = has_obstacle
        self._plan_obstacle_done = not has_obstacle
        if has_obstacle:
            self._avoidance_active = True
            print(
                f'[BeltPlan] LOCKED avoid side={self._avoid_side} '
                f'obs_cx={self._last_obs_cx:.0f} '
                f'obs_depth={self._obstacle_depth_m:.2f}m '
                f'(static scene — will not replan)'
            )
        else:
            self._avoidance_active = False
            print('[BeltPlan] LOCKED direct approach (corridor clear)')

    def _smooth_bearing(self, angle_deg: float) -> float:
        """Exponential circular smoothing to reduce motor thrash from jitter."""
        a = angle_deg % 360.0
        if self._smoothed_angle is None:
            self._smoothed_angle = a
            return a
        prev = self._smoothed_angle
        # Shortest-path delta in (-180, 180]
        delta = (a - prev + 180.0) % 360.0 - 180.0
        blended = (prev + self.BEARING_SMOOTH_ALPHA * delta) % 360.0
        self._smoothed_angle = blended
        return blended

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
            'plan_locked': self._plan_locked,
            'plan_has_obstacle': self._plan_has_obstacle,
            'plan_obstacle_done': self._plan_obstacle_done,
        }
        if phase != self._last_logged_phase:
            self._last_logged_phase = phase
            side = self._avoid_side or '-'
            obs_d = f'{obs_depth:.2f}m' if obs_depth is not None else '-'
            locked = 'locked' if self._plan_locked else 'locking'
            print(
                f'[BeltAvoid] phase={phase} side={side} plan={locked} '
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

    def _arm_cue_hold(self, name: str, hold_s: Optional[float] = None) -> None:
        self._last_cue = name
        self._cue_hold_until = time.time() + (self.CUE_HOLD_S if hold_s is None else hold_s)
        self._currently_vibrating = False
        self._last_angle = None
        print(f'[BeltCue] {name}')

    def _dir_sector(self, angle_deg: float) -> str:
        """Map steering angle to coarse left / center / right."""
        a = angle_deg % 360.0
        if a <= 25.0 or a >= 335.0:
            return 'center'
        if 25.0 < a <= 180.0:
            return 'right'
        return 'left'

    def _maybe_emit_nav_cues(self, depth_cm: float, angle_deg: float) -> None:
        now = time.time()

        if not self._nav_started:
            self._nav_started = True
            self._last_dir_sector = self._dir_sector(angle_deg)
            self.signal_event('nav_start')
            return

        # Coarse direction flip → one all-around tap (then continuous buzz shows side)
        sector = self._dir_sector(angle_deg)
        if self._last_dir_sector is None:
            self._last_dir_sector = sector
        elif sector != self._last_dir_sector:
            if now - self._last_dir_change_time >= self.DIR_CHANGE_MIN_INTERVAL_S:
                self.signal_event('direction_change')
                self._last_dir_change_time = now
            self._last_dir_sector = sector

        if depth_cm <= 0:
            return

        # Re-arm pre-handoff only after backing well away
        if depth_cm > PRE_HANDOFF_CM + 25.0:
            self._signaled_pre_handoff = False

        if (not self._signaled_pre_handoff
                and depth_cm <= PRE_HANDOFF_CM
                and depth_cm > HANDOFF_ENTER_CM):
            self._signaled_pre_handoff = True
            self.signal_event('pre_handoff')

    def _reset_nav_cues(self) -> None:
        self._nav_started = False
        self._signaled_pre_handoff = False
        self._cue_hold_until = 0.0
        self._last_cue = None
        self._last_dir_sector = None
        self._last_dir_change_time = 0.0

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

        0°   = navel / green marker (straight ahead)
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

    def _motor_index_for_body_angle(self, body_angle_deg: float) -> int:
        """Map body angle (0=navel) to a belt motor bit-index."""
        if self._navel_motor_index is None:
            return int(round((body_angle_deg % 360.0) / MOTOR_STEP_DEG)) % MOTOR_COUNT
        steps = int(round((body_angle_deg % 360.0) / MOTOR_STEP_DEG)) % MOTOR_COUNT
        if self._motor_dir < 0:
            steps = (-steps) % MOTOR_COUNT
        return (self._navel_motor_index + steps) % MOTOR_COUNT

    def _load_navel_calibration(self) -> None:
        try:
            if not _NAVEL_CALIB_PATH.exists():
                return
            data = json.loads(_NAVEL_CALIB_PATH.read_text())
            idx = data.get('navel_motor_index')
            if idx is not None:
                self._navel_motor_index = int(idx) % MOTOR_COUNT
            direction = data.get('motor_dir', 1)
            self._motor_dir = -1 if int(direction) < 0 else 1
            print(f'[BeltMotorTest] loaded navel motor={self._navel_motor_index} '
                  f'dir={self._motor_dir} from {_NAVEL_CALIB_PATH.name}')
        except Exception as exc:
            print(f'[BeltMotorTest] could not load navel calibration: {exc}')

    def _save_navel_calibration(self) -> None:
        try:
            _NAVEL_CALIB_PATH.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                'navel_motor_index': self._navel_motor_index,
                'motor_dir': self._motor_dir,
            }
            _NAVEL_CALIB_PATH.write_text(json.dumps(payload, indent=2) + '\n')
            print(f'[BeltMotorTest] saved calibration → {_NAVEL_CALIB_PATH}')
        except Exception as exc:
            print(f'[BeltMotorTest] could not save navel calibration: {exc}')

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
        inten = max(0, min(100, intensity))
        if self._navel_motor_index is not None:
            idx = self._motor_index_for_body_angle(angle_deg)
            self._virtual_belt.send_vibration_command(
                channel_index=1,
                pattern=0,
                intensity=inten,
                orientation_type=MOTOR_INDEX,
                orientation=idx,
            )
        else:
            self._virtual_belt.send_vibration_command(
                channel_index=1,
                pattern=0,
                intensity=inten,
                orientation_type=ANGLE,
                orientation=int(round(angle_deg)) % 360,
            )
