"""
belt.py — 16-Motor Navigation Belt Controller

Mirrors the structure of bracelet.py but adapted for a 16-motor
navigation belt that guides users toward a GPS destination.

Motor layout (clockwise from front):
    Motor  0 (N)    =   0.0°  Front
    Motor  1 (NNE)  =  22.5°
    Motor  2 (NE)   =  45.0°
    Motor  3 (ENE)  =  67.5°
    Motor  4 (E)    =  90.0°  Right
    Motor  5 (ESE)  = 112.5°
    Motor  6 (SE)   = 135.0°
    Motor  7 (SSE)  = 157.5°
    Motor  8 (S)    = 180.0°  Back
    Motor  9 (SSW)  = 202.5°
    Motor 10 (SW)   = 225.0°
    Motor 11 (WSW)  = 247.5°
    Motor 12 (W)    = 270.0°  Left
    Motor 13 (WNW)  = 292.5°
    Motor 14 (NW)   = 315.0°
    Motor 15 (NNW)  = 337.5°

Usage:
    from belt import connect_navigation_belt, NavigationBeltController

    # Connect hardware
    connection_ok, belt_controller = connect_navigation_belt()

    # Create logic controller
    nav = NavigationBeltController(vibration_intensities=calibration, navigation_type=0)

    # In main loop:
    arrived, info = nav.navigate(belt_controller, target_bearing=45.0, distance_m=120.0, user_heading=10.0)
"""

import numpy as np
import time
import json
from pybelt.belt_controller import (
    BeltConnectionState,
    BeltController,
    BeltControllerDelegate,
    BeltMode,
    BeltOrientationType,
    BeltVibrationTimerOption,
    BeltVibrationPattern,
)
from auto_connect import interactive_belt_connect, setup_logger


# ══════════════════════════════════════════════════════════════════════════════
# Constants
# ══════════════════════════════════════════════════════════════════════════════

NUM_MOTORS = 16
MOTOR_SPACING_DEG = 360.0 / NUM_MOTORS  # 22.5°

DIRECTION_LABELS = [
    "N",   "NNE",  "NE",  "ENE",
    "E",   "ESE",  "SE",  "SSE",
    "S",   "SSW",  "SW",  "WSW",
    "W",   "WNW",  "NW",  "NNW",
]

# Motor index → angle in degrees (clockwise from front)
MOTOR_ANGLES = [i * MOTOR_SPACING_DEG for i in range(NUM_MOTORS)]


# ══════════════════════════════════════════════════════════════════════════════
# Delegate
# ══════════════════════════════════════════════════════════════════════════════

class Delegate(BeltControllerDelegate):
    """Minimal delegate matching bracelet.py's pattern."""
    pass


# ══════════════════════════════════════════════════════════════════════════════
# Connection
# ══════════════════════════════════════════════════════════════════════════════

def connect_navigation_belt():
    """
    Connect to the 16-motor navigation belt via Bluetooth.
    Follows the same pattern as bracelet.py's connect_belt().

    Returns:
        tuple: (success: bool, belt_controller: BeltController)
    """
    setup_logger()
    belt_controller_delegate = Delegate()
    belt_controller = BeltController(belt_controller_delegate)
    interactive_belt_connect(belt_controller)

    if belt_controller.get_connection_state() != BeltConnectionState.CONNECTED:
        print("Connection failed.")
        return False, belt_controller
    else:
        belt_controller.set_belt_mode(BeltMode.APP_MODE)
        return True, belt_controller


# ══════════════════════════════════════════════════════════════════════════════
# Navigation Belt Controller
# ══════════════════════════════════════════════════════════════════════════════

class NavigationBeltController:
    """
    High-level controller for the 16-motor navigation belt.
    Mirrors BraceletController's architecture but adapted for
    GPS-based directional navigation instead of camera-based hand guidance.

    The raw pybelt belt_controller is passed as a parameter to methods
    (same pattern as BraceletController.navigate_hand receives belt_controller).

    Navigation types:
        0 — Single motor: only the closest motor vibrates
        1 — Spread: primary motor + adjacent motors with decaying intensity
        2 — Interpolated: two nearest motors at proportional intensities
    """

    def __init__(self, vibration_intensities=None, navigation_type=0):
        """
        Args:
            vibration_intensities: dict {direction_label: intensity} for all 16 motors.
                                   Keys should be from DIRECTION_LABELS (e.g. "N", "NNE", ...).
                                   Default: all motors at 50.
            navigation_type: 0=single, 1=spread, 2=interpolated
        """
        # Per-motor calibration
        if vibration_intensities is None:
            self.vibration_intensities = {d: 50 for d in DIRECTION_LABELS}
        else:
            self.vibration_intensities = vibration_intensities

        self.navigation_type = navigation_type
        self.vibrate = True
        self.mock_navigate = False

        # State tracking (mirrors bracelet's prev_right_intensity etc.)
        self.prev_motor = -1
        self.prev_intensity = 0
        self._active = False
        self.was_navigating = False
        self.signal_lost_timer = 0
        self.last_vib_update_time = 0
        self.searching = True

        # Configuration
        self.update_interval = 0.2      # seconds between BLE updates (matches bracelet)
        self.arrival_threshold = 3.0    # meters — at this distance, signal arrival
        self.signal_lost_max_frames = 40  # ~2 sec at 20fps (matches bracelet's timer)

    # ──────────────────────────────────────────────────────────────────────
    # Geometry helpers
    # ──────────────────────────────────────────────────────────────────────

    @staticmethod
    def bearing_to_motor_index(bearing_deg):
        """
        Map a bearing (0–360, 0=North, clockwise) to the nearest motor index.

        Args:
            bearing_deg: bearing in degrees

        Returns:
            int: motor index (0–15)
        """
        return int(round(bearing_deg % 360 / MOTOR_SPACING_DEG)) % NUM_MOTORS

    @staticmethod
    def relative_bearing(target_bearing, user_heading):
        """
        Calculate the relative bearing from the user's heading to the target.

        Args:
            target_bearing: absolute bearing to target (degrees, 0=N, CW)
            user_heading: user's current compass heading (degrees, 0=N, CW)

        Returns:
            float: relative bearing in [0, 360). 0 = straight ahead.
        """
        return (target_bearing - user_heading) % 360

    @staticmethod
    def get_adjacent_motors(primary_motor, spread=1):
        """
        Get motor indices adjacent to the primary motor (wrapping around).

        Args:
            primary_motor: index of the primary motor (0–15)
            spread: number of neighbors on each side (1 or 2)

        Returns:
            list of tuples: [(motor_index, offset_from_primary), ...]
        """
        adjacent = []
        for offset in range(1, spread + 1):
            adjacent.append(((primary_motor + offset) % NUM_MOTORS, offset))   # clockwise
            adjacent.append(((primary_motor - offset) % NUM_MOTORS, offset))   # counter-CW
        return adjacent

    # ──────────────────────────────────────────────────────────────────────
    # Intensity calculation
    # ──────────────────────────────────────────────────────────────────────

    def _get_raw_intensity(self, distance_m,
                           max_intensity=100, min_intensity=15,
                           near_m=3.0, far_m=200.0):
        """
        Map distance to a raw (uncalibrated) vibration intensity.
        Closer → stronger vibration.

        Args:
            distance_m:    distance to target in meters
            max_intensity: intensity when distance ≤ near_m
            min_intensity: intensity when distance ≥ far_m
            near_m:        threshold for maximum intensity
            far_m:         threshold for minimum intensity

        Returns:
            int: raw intensity (0–100), not yet calibrated
        """
        if distance_m <= near_m:
            return max_intensity
        if distance_m >= far_m:
            return min_intensity
        ratio = 1.0 - (distance_m - near_m) / (far_m - near_m)
        return int(min_intensity + ratio * (max_intensity - min_intensity))

    def _apply_calibration(self, motor_index, raw_intensity):
        """
        Scale raw intensity by per-motor calibration factor.
        Calibration value of 50 = no change (baseline).
        Values > 50 boost, values < 50 reduce.

        Args:
            motor_index:   motor index (0–15)
            raw_intensity: uncalibrated intensity

        Returns:
            int: calibrated intensity clamped to [0, 100]
        """
        label = DIRECTION_LABELS[motor_index]
        cal = self.vibration_intensities.get(label, 50)
        return max(0, min(100, int(raw_intensity * cal / 50)))

    def get_intensity(self, distance_m, motor_index):
        """
        Get calibrated vibration intensity for a motor given distance.
        Combines _get_raw_intensity and _apply_calibration.

        Args:
            distance_m:  distance to target in meters
            motor_index: motor index (0–15)

        Returns:
            int: calibrated intensity (0–100)
        """
        raw = self._get_raw_intensity(distance_m)
        return self._apply_calibration(motor_index, raw)

    # ──────────────────────────────────────────────────────────────────────
    # Vibration send methods (private) — one per navigation type
    # ──────────────────────────────────────────────────────────────────────

    def _send_single(self, belt_controller, motor_index, intensity):
        """
        Navigation type 0: vibrate a single motor.

        Args:
            belt_controller: raw pybelt BeltController
            motor_index: motor to activate (0–15)
            intensity: calibrated intensity (0–100)
        """
        if belt_controller and self.vibrate:
            belt_controller.send_vibration_command(
                channel_index=0,
                pattern=BeltVibrationPattern.CONTINUOUS,
                intensity=intensity,
                orientation_type=BeltOrientationType.ANGLE,
                orientation=int(MOTOR_ANGLES[motor_index]),
                pattern_iterations=None,
                pattern_period=500,
                pattern_start_time=0,
                exclusive_channel=False,
                clear_other_channels=True,
            )

    def _send_spread(self, belt_controller, primary_motor, raw_intensity, spread=1):
        """
        Navigation type 1: primary motor at full intensity + adjacent motors
        with decaying intensity. Similar to bracelet's octant navigation.

        Args:
            belt_controller: raw pybelt BeltController
            primary_motor: primary motor index (0–15)
            raw_intensity: uncalibrated base intensity
            spread: number of adjacent motors on each side (1 or 2)
        """
        if not (belt_controller and self.vibrate):
            return

        # Primary motor — full intensity, clear all other channels
        primary_int = self._apply_calibration(primary_motor, raw_intensity)
        belt_controller.send_vibration_command(
            channel_index=0,
            pattern=BeltVibrationPattern.CONTINUOUS,
            intensity=primary_int,
            orientation_type=BeltOrientationType.ANGLE,
            orientation=int(MOTOR_ANGLES[primary_motor]),
            pattern_iterations=None,
            pattern_period=500,
            pattern_start_time=0,
            exclusive_channel=False,
            clear_other_channels=True,
        )

        # Adjacent motors — decaying intensity
        adjacent = self.get_adjacent_motors(primary_motor, spread)
        channel = 1
        for adj_motor, offset in adjacent:
            decay = max(0.0, 1.0 - offset * 0.45)  # offset 1 → 55%, offset 2 → 10%
            adj_raw = int(raw_intensity * decay)
            adj_int = self._apply_calibration(adj_motor, adj_raw)

            if adj_int < 10 or channel > 5:
                continue

            belt_controller.send_vibration_command(
                channel_index=channel,
                pattern=BeltVibrationPattern.CONTINUOUS,
                intensity=adj_int,
                orientation_type=BeltOrientationType.ANGLE,
                orientation=int(MOTOR_ANGLES[adj_motor]),
                pattern_iterations=None,
                pattern_period=500,
                pattern_start_time=0,
                exclusive_channel=False,
                clear_other_channels=False,
            )
            channel += 1

    def _send_interpolated(self, belt_controller, rel_bearing, raw_intensity):
        """
        Navigation type 2: interpolate between the two nearest motors based
        on exact angle. Similar to bracelet's quadrant navigation.

        If the target is exactly between motor 3 (67.5°) and motor 4 (90°),
        both vibrate equally. If closer to motor 4, motor 4 gets more intensity.

        Args:
            belt_controller: raw pybelt BeltController
            rel_bearing: relative bearing in degrees (0–360)
            raw_intensity: uncalibrated base intensity
        """
        if not (belt_controller and self.vibrate):
            return

        # Find the two bracketing motors
        exact_position = (rel_bearing % 360) / MOTOR_SPACING_DEG
        motor_low = int(exact_position) % NUM_MOTORS
        motor_high = (motor_low + 1) % NUM_MOTORS

        # Interpolation weights (0.0–1.0)
        weight_high = exact_position - int(exact_position)
        weight_low = 1.0 - weight_high

        # Compute calibrated intensities
        int_low = self._apply_calibration(motor_low, int(raw_intensity * weight_low))
        int_high = self._apply_calibration(motor_high, int(raw_intensity * weight_high))

        # Send motor_low — clear others
        if int_low >= 5:
            belt_controller.send_vibration_command(
                channel_index=0,
                pattern=BeltVibrationPattern.CONTINUOUS,
                intensity=int_low,
                orientation_type=BeltOrientationType.ANGLE,
                orientation=int(MOTOR_ANGLES[motor_low]),
                pattern_iterations=None,
                pattern_period=500,
                pattern_start_time=0,
                exclusive_channel=False,
                clear_other_channels=True,
            )
        else:
            belt_controller.send_vibration_command(
                channel_index=0,
                pattern=BeltVibrationPattern.CONTINUOUS,
                intensity=0,
                orientation_type=BeltOrientationType.ANGLE,
                orientation=int(MOTOR_ANGLES[motor_low]),
                pattern_iterations=None,
                pattern_period=500,
                pattern_start_time=0,
                exclusive_channel=False,
                clear_other_channels=True,
            )

        # Send motor_high — add to existing
        if int_high >= 5:
            belt_controller.send_vibration_command(
                channel_index=1,
                pattern=BeltVibrationPattern.CONTINUOUS,
                intensity=int_high,
                orientation_type=BeltOrientationType.ANGLE,
                orientation=int(MOTOR_ANGLES[motor_high]),
                pattern_iterations=None,
                pattern_period=500,
                pattern_start_time=0,
                exclusive_channel=False,
                clear_other_channels=False,
            )

    # ──────────────────────────────────────────────────────────────────────
    # Main navigation method
    # ──────────────────────────────────────────────────────────────────────

    def navigate(self, belt_controller, target_bearing, distance_m, user_heading=0.0):
        """
        Main navigation function — called each frame from the control loop.
        Mirrors bracelet.py's navigate_hand() structure with analogous cases:

            Bracelet (navigate_hand)             Belt (navigate)
            ─────────────────────────            ──────────────────────
            1. Hand overlaps target  →  grasp    1. Distance < threshold → arrival
            2. Hand + target visible →  guide    2. Valid GPS data       → guide
            3. Detection lost briefly → hold     3. GPS lost briefly     → hold
            4. Target only (no hand) → pulse     (not applicable)
            5. Nothing detected      → stop      4. No GPS data          → stop

        Args:
            belt_controller: raw pybelt BeltController (or None for mock mode)
            target_bearing:  absolute bearing to destination (degrees, 0=N, CW).
                             Pass None if GPS signal is lost.
            distance_m:      distance to destination in meters.
                             Pass None if GPS signal is lost.
            user_heading:    user's current compass heading (degrees, 0=N, CW).

        Returns:
            tuple: (arrived: bool, motor_info: dict or None)
        """

        # ── Case 1: Arrival (like bracelet's grasping/overlap) ────────────
        if distance_m is not None and distance_m <= self.arrival_threshold:
            self.signal_arrival(belt_controller)
            self.was_navigating = False
            self._active = False
            self.prev_motor = -1
            self.prev_intensity = 0

            if self.mock_navigate:
                print(f'[Belt] ARRIVED — distance {distance_m:.1f}m')

            return True, {
                "state": "arrived",
                "distance": distance_m,
            }

        # ── Case 2: Active navigation (like bracelet's guidance) ──────────
        if target_bearing is not None and distance_m is not None:

            self.was_navigating = True
            self.signal_lost_timer = 0
            self.searching = True
            self._active = True

            # Rate-limit BLE updates (matches bracelet's 200ms throttle)
            current_time = time.time()
            if current_time - self.last_vib_update_time < self.update_interval:
                return False, None

            # Compute relative bearing
            rel_bearing = self.relative_bearing(target_bearing, user_heading)

            # Map to primary motor
            primary_motor = self.bearing_to_motor_index(rel_bearing)

            # Raw + calibrated intensity
            raw_intensity = self._get_raw_intensity(distance_m)
            intensity = self._apply_calibration(primary_motor, raw_intensity)

            # Skip BLE update if nothing changed significantly
            # (mirrors bracelet's abs(right_int - prev_right_intensity) > 5 check)
            if (primary_motor == self.prev_motor
                    and abs(intensity - self.prev_intensity) < 5):
                return False, None

            self.last_vib_update_time = current_time

            # Send vibration based on navigation type
            if self.navigation_type == 0:
                self._send_single(belt_controller, primary_motor, intensity)
            elif self.navigation_type == 1:
                self._send_spread(belt_controller, primary_motor, raw_intensity, spread=1)
            elif self.navigation_type == 2:
                self._send_interpolated(belt_controller, rel_bearing, raw_intensity)

            # Update state
            self.prev_motor = primary_motor
            self.prev_intensity = intensity

            if self.mock_navigate:
                print(
                    f'[Belt] Navigate: Motor {primary_motor} '
                    f'({DIRECTION_LABELS[primary_motor]}) '
                    f'intensity={intensity} dist={distance_m:.1f}m '
                    f'rel_bearing={rel_bearing:.0f}°'
                )

            return False, {
                "state": "navigating",
                "motor": primary_motor,
                "direction": DIRECTION_LABELS[primary_motor],
                "intensity": intensity,
                "rel_bearing": rel_bearing,
                "distance": distance_m,
            }

        # ── Case 3: GPS signal lost — continue last direction ─────────────
        #    (like bracelet's "guidance for several frames if target lost")
        if self.was_navigating:
            self.signal_lost_timer += 1

            if self.signal_lost_timer < self.signal_lost_max_frames:
                # Keep vibrating at last known motor and intensity
                if belt_controller and self.vibrate and self.prev_motor >= 0:
                    belt_controller.send_vibration_command(
                        channel_index=0,
                        pattern=BeltVibrationPattern.CONTINUOUS,
                        intensity=self.prev_intensity,
                        orientation_type=BeltOrientationType.ANGLE,
                        orientation=int(MOTOR_ANGLES[self.prev_motor]),
                        pattern_iterations=None,
                        pattern_period=500,
                        pattern_start_time=0,
                        exclusive_channel=False,
                        clear_other_channels=True,
                    )

                if self.mock_navigate:
                    print(
                        f'[Belt] GPS lost — holding last direction '
                        f'Motor {self.prev_motor} ({DIRECTION_LABELS[self.prev_motor]}) '
                        f'({self.signal_lost_timer}/{self.signal_lost_max_frames})'
                    )

                return False, {"state": "signal_lost_holding"}

            else:
                # Lost for too long — stop
                self.was_navigating = False
                self.signal_lost_timer = 0
                self._active = False
                self.searching = True
                if belt_controller and self.vibrate:
                    belt_controller.stop_vibration()

                if self.mock_navigate:
                    print('[Belt] GPS lost too long — stopping vibration')

                return False, {"state": "signal_lost_stopped"}

        # ── Case 4: No GPS data and not navigating (idle) ─────────────────
        #    (like bracelet's "target not in the frame yet")
        self._active = False
        if belt_controller and self.vibrate:
            belt_controller.stop_vibration()

        if self.mock_navigate:
            pass  # silent when idle

        return False, {"state": "idle"}

    # ──────────────────────────────────────────────────────────────────────
    # Signals
    # ──────────────────────────────────────────────────────────────────────

    def signal_arrival(self, belt_controller, intensity=80):
        """
        Pulse ALL 16 motors — arrival/destination reached signal.
        Analogous to bracelet's grasping signal (overlapping pulse).

        Args:
            belt_controller: raw pybelt BeltController
            intensity: pulse intensity (0–100)
        """
        if belt_controller and self.vibrate:
            belt_controller.stop_vibration()
            belt_controller.send_pulse_command(
                channel_index=0,
                orientation_type=BeltOrientationType.BINARY_MASK,
                orientation=0xFFFF,   # all 16 motors
                intensity=intensity,
                on_duration_ms=200,
                pulse_period=400,
                pulse_iterations=5,
                series_period=2500,
                series_iterations=2,
                timer_option=BeltVibrationTimerOption.RESET_TIMER,
                exclusive_channel=True,
                clear_other_channels=True,
            )

        if self.mock_navigate:
            print('[Belt] ✓ ARRIVAL signal')

    def signal_warning(self, belt_controller, motor_index, intensity=70):
        """
        Short pulse on a specific motor — obstacle/hazard warning.
        Analogous to bracelet's "move back" pulse.

        Args:
            belt_controller: raw pybelt BeltController
            motor_index: motor to pulse (0–15)
            intensity: pulse intensity (0–100)
        """
        if belt_controller and self.vibrate:
            belt_controller.send_pulse_command(
                channel_index=1,
                orientation_type=BeltOrientationType.ANGLE,
                orientation=int(MOTOR_ANGLES[motor_index]),
                intensity=intensity,
                on_duration_ms=150,
                pulse_period=300,
                pulse_iterations=3,
                series_period=3000,
                series_iterations=1,
                timer_option=BeltVibrationTimerOption.RESET_TIMER,
                exclusive_channel=False,
                clear_other_channels=False,
            )

        if self.mock_navigate:
            print(
                f'[Belt] ⚠ WARNING: Motor {motor_index} '
                f'({DIRECTION_LABELS[motor_index]})'
            )

    def signal_off_course(self, belt_controller, intensity=60):
        """
        Double pulse on back motor (Motor 8 = South = 180°).
        Tells the user they are walking the wrong way.

        Args:
            belt_controller: raw pybelt BeltController
            intensity: pulse intensity (0–100)
        """
        back_motor = 8  # S = 180°
        if belt_controller and self.vibrate:
            belt_controller.send_pulse_command(
                channel_index=0,
                orientation_type=BeltOrientationType.ANGLE,
                orientation=int(MOTOR_ANGLES[back_motor]),
                intensity=intensity,
                on_duration_ms=100,
                pulse_period=250,
                pulse_iterations=2,
                series_period=2000,
                series_iterations=1,
                timer_option=BeltVibrationTimerOption.RESET_TIMER,
                exclusive_channel=False,
                clear_other_channels=False,
            )

        if self.mock_navigate:
            print('[Belt] ↩ OFF COURSE signal (back motor)')

    def signal_recalculating(self, belt_controller, intensity=40):
        """
        Sequential pulse on cardinal motors (N → E → S → W).
        Indicates route is being recalculated.

        Args:
            belt_controller: raw pybelt BeltController
            intensity: pulse intensity (0–100)
        """
        cardinal_motors = [0, 4, 8, 12]  # N, E, S, W
        if belt_controller and self.vibrate:
            belt_controller.stop_vibration()
            for i, motor in enumerate(cardinal_motors):
                belt_controller.send_pulse_command(
                    channel_index=i,
                    orientation_type=BeltOrientationType.ANGLE,
                    orientation=int(MOTOR_ANGLES[motor]),
                    intensity=intensity,
                    on_duration_ms=100,
                    pulse_period=200,
                    pulse_iterations=1,
                    series_period=1000,
                    series_iterations=1,
                    timer_option=BeltVibrationTimerOption.RESET_TIMER,
                    exclusive_channel=False,
                    clear_other_channels=False,
                )

        if self.mock_navigate:
            print('[Belt] ↻ RECALCULATING signal')

    # ──────────────────────────────────────────────────────────────────────
    # Lifecycle
    # ──────────────────────────────────────────────────────────────────────

    def stop_vibration(self, belt_controller):
        """Stop all motors and reset state."""
        if belt_controller:
            belt_controller.stop_vibration()
        self._active = False
        self.prev_motor = -1
        self.prev_intensity = 0

    def disconnect_belt(self, belt_controller):
        """Stop vibration and disconnect Bluetooth."""
        self.stop_vibration(belt_controller)
        if belt_controller:
            belt_controller.disconnect_belt()
        print("[Belt] Disconnected.")

    @property
    def is_active(self):
        """Whether the belt is currently vibrating."""
        return self._active

    # ──────────────────────────────────────────────────────────────────────
    # Calibration persistence
    # ──────────────────────────────────────────────────────────────────────

    def save_calibration(self, filepath):
        """Save current per-motor calibration to JSON."""
        with open(filepath, 'w') as f:
            json.dump(self.vibration_intensities, f, indent=4)
        print(f"[Belt] Calibration saved → {filepath}")

    def load_calibration(self, filepath):
        """Load per-motor calibration from JSON."""
        with open(filepath, 'r') as f:
            self.vibration_intensities = json.load(f)
        print(f"[Belt] Calibration loaded ← {filepath}")