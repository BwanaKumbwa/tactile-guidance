"""
navigation_belt.py

Vision-based navigation controller for a 16-motor vibration belt.
Guides a blind user towards a detected target object using directional
vibrations mapped to 8 compass directions (N, NE, E, SE, S, SW, W, NW).

When the user is close enough to the target (based on depth estimation),
signals a switch to the BraceletController for close-range grasping.

Camera center = user's forward direction = North on the belt.
Target position relative to center determines which belt motors activate.

Motor layout (16 motors, 2 per direction):
    N (front)  → 0°       NE → 45°
    E (right)  → 90°      SE → 135°
    S (back)   → 180°     SW → 225°
    W (left)   → 270°     NW → 315°
"""

import numpy as np
from pybelt.belt_controller import (BeltConnectionState, BeltController,
                                    BeltControllerDelegate, BeltMode,
                                    BeltOrientationType,
                                    BeltVibrationTimerOption, BeltVibrationPattern)
from auto_connect import interactive_belt_connect, setup_logger
import time


class NavigationBeltDelegate(BeltControllerDelegate):
    """Delegate for navigation belt BLE callbacks."""
    pass


def connect_navigation_belt():
    """
    Connect to the 16-motor navigation belt via BLE.

    Returns:
        tuple: (success: bool, belt_controller: BeltController)
    """
    setup_logger()
    delegate = NavigationBeltDelegate()
    belt_controller = BeltController(delegate)
    interactive_belt_connect(belt_controller)

    if belt_controller.get_connection_state() != BeltConnectionState.CONNECTED:
        print("Navigation belt connection failed.")
        return False, belt_controller
    else:
        belt_controller.set_belt_mode(BeltMode.APP_MODE)
        print("Navigation belt connected — APP_MODE active.")
        return True, belt_controller


class NavigationBeltController:
    """
    High-level navigation controller for the 16-motor belt.
    Guides the user's body towards a target object detected in the camera frame.
    Analogous to BraceletController for the 4-motor wrist device.
    """

    DIRECTIONS = ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW']
    DIRECTION_ANGLES = {
        'N': 0, 'NE': 45, 'E': 90, 'SE': 135,
        'S': 180, 'SW': 225, 'W': 270, 'NW': 315
    }

    def __init__(self, vibration_intensities=None, navigation_type=1, switch_distance=1.5):
        """
        Args:
            vibration_intensities: dict with max vibration intensity per direction (0–100).
                                   Defaults to 50 for all directions.
            navigation_type: vibration mapping strategy.
                0 = single nearest direction (one direction at a time)
                1 = octant interpolation (blend two adjacent directions)
            switch_distance: depth threshold in meters to switch to bracelet mode.
        """
        if vibration_intensities is None:
            vibration_intensities = {d: 50 for d in self.DIRECTIONS}
        self.vibration_intensities = vibration_intensities
        self.navigation_type = navigation_type
        self.switch_distance = switch_distance

        # State tracking (mirrors BraceletController pattern)
        self.searching = True
        self.prev_target = None
        self.vibrate = True
        self.was_guiding = False
        self.timer = 0
        self.mock_navigate = False
        self.mode = 'belt'  # 'belt' | 'bracelet'

        # BLE throttling (same 200ms pattern as bracelet)
        self.prev_intensities = {d: 0 for d in self.DIRECTIONS}
        self.last_vib_update_time = 0

        # Data logging (mirrors bracelet)
        self.navigation_time = 'NA'
        self.target_detections_list = []
        self.target_confidence_list = []
        self.target_class_track_ids = []
        self.target_object_track_ids = []
        self.target_position = []


    def choose_detection(self, bboxes, previous_bbox=None, w=640, h=640):
        """
        Choose the best bounding box detection based on confidence,
        tracking ID consistency, and spatial distance to previous detection.

        Same scoring logic as BraceletController.choose_detection().

        Args:
            bboxes (list): List of bounding boxes [x, y, w, h, id, cls, conf, depth].
            previous_bbox (list, optional): Previous frame's chosen detection.
            w (int): Frame width.
            h (int): Frame height.

        Returns:
            numpy array: Best bounding box, or None if no candidates.
        """
        track_id_weight = 1000
        exponential_weight = 2
        distance_weight = 100
        candidates = []

        for bbox in bboxes:
            if bbox[0] <= w and bbox[1] <= h:
                # Confidence score (exponential growth in [0,1])
                confidence = bbox[6]
                confidence_score = exponential_weight ** confidence - 1

                # Tracking ID consistency score
                current_track_id = bbox[4]
                previous_track_id = previous_bbox[4] if previous_bbox is not None else -1
                track_id_score = track_id_weight if current_track_id == previous_track_id else 1

                # Spatial proximity score
                if previous_bbox is None:
                    distance_inverted = 1
                else:
                    current_location = bbox[:2]
                    previous_location = previous_bbox[:2]
                    distance = np.linalg.norm(current_location - previous_location)
                    distance_inverted = 1 / distance if distance >= 100 else distance_weight

                score = track_id_score * confidence_score * distance_inverted
                candidates.append(score)
            else:
                candidates.append(0)

        if len(candidates) == 0:
            return None
        return bboxes[np.argmax(candidates)] if len(candidates) else None


    def get_direction_intensities(self, target_x, target_y, frame_w, frame_h, vibration_intensities):
        """
        Calculate vibration intensities for 8 belt directions based on
        where the target appears relative to the camera center.

        The camera center represents the user's forward direction (North on belt).
        A target to the right of center → East motors. Above center → North motors. Etc.

        Intensity scales with how far off-center the target is:
            - Centered (straight ahead): 30% intensity (gentle confirmation)
            - Edge of frame (need to turn): 100% intensity (urgent)

        Args:
            target_x (float): Target center x in pixel coordinates.
            target_y (float): Target center y in pixel coordinates.
            frame_w (int): Camera frame width.
            frame_h (int): Camera frame height.
            vibration_intensities (dict): Max intensity per direction.

        Returns:
            tuple: (intensities dict, angle in degrees 0–360)
        """
        center_x = frame_w / 2
        center_y = frame_h / 2

        dx = target_x - center_x
        dy = center_y - target_y  # inverted y-axis (image coordinates)

        # Angle: 0° = N (target above center/ahead),
        #        90° = E (right), 180° = S (below), 270° = W (left)
        angle = np.degrees(np.arctan2(dx, dy)) % 360

        # Off-center ratio for intensity scaling
        max_offset = np.sqrt(center_x ** 2 + center_y ** 2)
        offset = np.sqrt(dx ** 2 + dy ** 2)
        off_center_ratio = min(offset / max_offset, 1.0)

        # 30% base when centered (confirmation buzz), scaling to 100% at edges
        intensity_scale = 0.3 + 0.7 * off_center_ratio

        intensities = {d: 0 for d in self.DIRECTIONS}

        if self.navigation_type == 0:
            # --- Single nearest direction ---
            nearest = self._angle_to_direction(angle)
            intensities[nearest] = int(vibration_intensities[nearest] * intensity_scale)

        elif self.navigation_type == 1:
            # --- Octant interpolation (blend adjacent directions) ---
            # Same concept as BraceletController navigation_type 1
            sector_size = 45.0
            sector_pos = angle / sector_size
            lower_idx = int(sector_pos) % 8
            upper_idx = (lower_idx + 1) % 8
            fraction = sector_pos - int(sector_pos)

            lower_dir = self.DIRECTIONS[lower_idx]
            upper_dir = self.DIRECTIONS[upper_idx]

            intensities[lower_dir] = int(
                vibration_intensities[lower_dir] * (1 - fraction) * intensity_scale
            )
            intensities[upper_dir] = int(
                vibration_intensities[upper_dir] * fraction * intensity_scale
            )

            # Ensure at least one motor gives perceivable feedback
            if max(intensities.values()) < 5:
                intensities[lower_dir] = max(5, int(vibration_intensities[lower_dir] * 0.3))

        return intensities, angle


    def navigate_body(self, belt_controller, bboxes, target_cls, hand_clss,
                      depth_img, vibration_intensities=None, metric=False,
                      frame_width=640, frame_height=640):
        """
        Navigate the user's body towards the target object using belt vibrations.
        Analogous to BraceletController.navigate_hand().

        Called once per frame. Handles four cases:
            1. Target detected & far     → directional vibration guidance
            2. Target detected & close   → signal switch to bracelet mode
            3. Target lost (was guiding) → continue previous vibration briefly
            4. Target not in frame       → stop vibrations

        Args:
            belt_controller: pybelt BeltController for the 16-motor navigation belt.
            bboxes: detections in current frame [x, y, w, h, id, cls, conf, depth].
            target_cls: target object class ID.
            hand_clss: list of hand class IDs (filtered out from candidates).
            depth_img: depth map of the current frame (or None).
            vibration_intensities: dict with max intensity per direction.
            metric: whether depth values are in metric units (meters).
            frame_width: camera frame width in pixels.
            frame_height: camera frame height in pixels.

        Returns:
            switch_to_bracelet (bool): True if user is close enough for bracelet handoff.
            target: the chosen target bounding box, or None.
        """
        if vibration_intensities is None:
            vibration_intensities = self.vibration_intensities

        switch_to_bracelet = False

        # --- Filter for target detections (exclude hands) ---
        bboxes_targets = [det for det in bboxes
                          if det[5] == target_cls and det[5] not in hand_clss]
        target = self.choose_detection(bboxes_targets, self.prev_target,
                                       w=frame_width, h=frame_height)
        self.prev_target = target

        # --- Data logging ---
        if self.navigation_time != 'NA':
            if target is not None:
                self.target_detections_list.append(1)
                self.target_confidence_list.append(target[6])
                self.target_object_track_ids.append(int(target[4]))
                self.target_position.append([target[0], target[1]])
            else:
                self.target_detections_list.append(0)
                self.target_confidence_list.append(0)
                self.target_object_track_ids.append('NA')
                self.target_position.append([0, 0])

        # ==============================================================
        # TARGET DETECTED
        # ==============================================================
        if target is not None:

            # Initialise navigation timer on first detection
            if self.navigation_time == 'NA':
                self.navigation_time = time.time()
                self.target_detections_list.append(1)
                self.target_confidence_list.append(target[6])
                self.target_object_track_ids.append(int(target[4]))
                self.target_position.append([target[0], target[1]])

            # --- CASE 2: Close enough → switch to bracelet ---
            depth_value = target[7]
            if depth_value > 0 and depth_value <= self.switch_distance:
                self.mode = 'bracelet'
                switch_to_bracelet = True
                self.searching = True

                if belt_controller and self.vibrate:
                    belt_controller.stop_vibration()
                    # Triple-pulse on all motors: "switching to close-range mode"
                    belt_controller.send_pulse_command(
                        channel_index=0,
                        orientation_type=BeltOrientationType.BINARY_MASK,
                        orientation=0xFFFF,  # all 16 motors
                        intensity=60,
                        on_duration_ms=150,
                        pulse_period=300,
                        pulse_iterations=3,
                        series_period=2000,
                        series_iterations=1,
                        timer_option=BeltVibrationTimerOption.RESET_TIMER,
                        exclusive_channel=False,
                        clear_other_channels=True
                    )

                if self.mock_navigate:
                    print(f"[NavBelt] SWITCH TO BRACELET — depth: {depth_value:.2f}m")

                return switch_to_bracelet, target

            # --- CASE 1: Far away → directional guidance ---
            intensities, angle = self.get_direction_intensities(
                target[0], target[1], frame_width, frame_height,
                vibration_intensities
            )

            self.searching = True
            self.was_guiding = True
            self.timer = 0

            # Throttled BLE update (200ms interval, only on meaningful change)
            current_time = time.time()
            if current_time - self.last_vib_update_time > 0.2:
                self.last_vib_update_time = current_time

                changed = any(
                    abs(intensities[d] - self.prev_intensities[d]) > 5
                    for d in self.DIRECTIONS
                )

                if changed:
                    self.prev_intensities = intensities.copy()
                    if belt_controller and self.vibrate:
                        self._send_directional_vibration(belt_controller, intensities)

            if self.mock_navigate:
                active = {d: v for d, v in intensities.items() if v > 0}
                dist_str = f"{depth_value:.2f}m" if depth_value > 0 else "N/A"
                print(f"[NavBelt] Guiding — angle: {angle:.0f}° {active} dist: {dist_str}")

            return switch_to_bracelet, target

        # ==============================================================
        # CASE 3: Target lost but was recently guiding
        # ==============================================================
        elif self.was_guiding:
            self.searching = True

            # Continue previous vibration pattern for continuity
            if belt_controller and self.vibrate:
                self._send_directional_vibration(belt_controller, self.prev_intensities)

            self.timer += 1
            # ~2 seconds of grace period before stopping
            if self.timer >= 40:
                self.was_guiding = False
                self.timer = 0
                if belt_controller and self.vibrate:
                    belt_controller.stop_vibration()

            if self.mock_navigate:
                print(f"[NavBelt] Smoothened guidance — timer: {self.timer}/40")

            return switch_to_bracelet, None

        # ==============================================================
        # CASE 4: Target not in frame
        # ==============================================================
        else:
            self.timer = 0
            self.searching = True
            if belt_controller and self.vibrate:
                belt_controller.stop_vibration()

            return switch_to_bracelet, None


    # ----------------------------------------------------------------
    #  BLE vibration commands
    # ----------------------------------------------------------------

    def _send_directional_vibration(self, belt_controller, intensities):
        """
        Send vibration commands for all active directions.
        Each active direction uses a separate BLE channel (max 6).
        Uses BeltOrientationType.ANGLE so pybelt maps to the correct
        physical motors automatically.
        """
        channel = 0
        first = True

        for direction in self.DIRECTIONS:
            intensity = intensities[direction]
            if intensity > 0 and channel < 6:
                belt_controller.send_vibration_command(
                    channel_index=channel,
                    pattern=BeltVibrationPattern.CONTINUOUS,
                    intensity=intensity,
                    orientation_type=BeltOrientationType.ANGLE,
                    orientation=self.DIRECTION_ANGLES[direction],
                    pattern_iterations=None,
                    pattern_period=100,
                    pattern_start_time=0,
                    exclusive_channel=False,
                    clear_other_channels=first  # clear on first command only
                )
                first = False
                channel += 1


    def _angle_to_direction(self, angle):
        """Map a 0–360° angle to the nearest 8-way direction label."""
        index = round(angle / 45) % 8
        return self.DIRECTIONS[index]


    # ----------------------------------------------------------------
    #  State management
    # ----------------------------------------------------------------

    def reset(self):
        """Reset all navigation state for a new target or session."""
        self.prev_target = None
        self.was_guiding = False
        self.searching = True
        self.timer = 0
        self.mode = 'belt'
        self.navigation_time = 'NA'
        self.target_detections_list = []
        self.target_confidence_list = []
        self.target_class_track_ids = []
        self.target_object_track_ids = []
        self.target_position = []
        self.prev_intensities = {d: 0 for d in self.DIRECTIONS}