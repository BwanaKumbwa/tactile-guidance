import numpy as np
from pybelt.belt_controller import (
    BeltConnectionState, BeltController, BeltControllerDelegate,
    BeltMode, BeltOrientationType,
    BeltVibrationTimerOption, BeltVibrationPattern
)
from auto_connect import interactive_belt_connect, setup_logger
from depth_navigation_functions import (
    map_obstacles, check_obstacles_between_points, find_obstacle_target_point
)
import time


class Delegate(BeltControllerDelegate):
    pass


def connect_belt():
    """Connect to the physical tactile belt and switch it to APP mode."""
    setup_logger()
    delegate       = Delegate()
    belt_controller = BeltController(delegate)
    interactive_belt_connect(belt_controller)

    if belt_controller.get_connection_state() != BeltConnectionState.CONNECTED:
        print("Connection failed.")
        return False, belt_controller
    belt_controller.set_belt_mode(BeltMode.APP_MODE)
    return True, belt_controller


class BraceletController:

    def __init__(self,
                 vibration_intensities=None,
                 navigation_type: int = 0):
        if vibration_intensities is None:
            vibration_intensities = {'bottom': 50, 'top': 50, 'left': 50, 'right': 50}

        self.vibration_intensities = vibration_intensities
        self.searching        = False
        self.prev_hand        = None
        self.prev_target      = None
        self.frozen_x         = -1
        self.frozen_y         = -1
        self.frozen_w         = -1
        self.frozen_h         = -1
        self.frozen           = False
        self.vibrate          = True
        self.is_inside        = False
        self.is_touched       = False
        self.obstacle_target  = None
        self.corners          = None
        self.roi_coords       = None
        self.roi_min_y        = -1
        self.navigation_time  = 'NA'
        self.freezing_time    = 'NA'
        self.grasping_time    = 'NA'
        self.target_confidence_list  = []
        self.target_detections_list  = []
        self.target_class_list       = []
        self.target_class_track_ids  = []
        self.target_object_track_ids = []
        self.target_position         = []
        self.navigation_type = navigation_type
        self.was_guiding           = False
        self.prev_right_intensity  = 0
        self.prev_left_intensity   = 0
        self.prev_top_intensity    = 0
        self.prev_bot_intensity    = 0
        self.mock_navigate         = False
        self.hand_position         = []
        self.hand_confidence_list  = []
        self.last_vib_update_time  = 0.0
        # Wall-clock timeout for smoothened guidance (replaces frame counter)
        self._smoothed_guidance_start: float = 0.0

    # Detection selector

    def choose_detection(self, bboxes, previous_bbox=None, hand=False, w=1920, h=1080):
        track_id_weight   = 1000
        exponential_weight = 2
        distance_weight   = 100
        candidates = []

        for bbox in bboxes:
            if bbox[0] <= w and bbox[1] <= h:
                confidence       = bbox[6]
                confidence_score = exponential_weight ** confidence - 1
                current_track_id  = bbox[4]
                previous_track_id = previous_bbox[4] if previous_bbox is not None else -1
                track_id_score    = track_id_weight if current_track_id == previous_track_id else 1
                if previous_bbox is None:
                    distance_inverted = 1
                else:
                    distance = np.linalg.norm(bbox[:2] - previous_bbox[:2])
                    distance_inverted = 1 / distance if distance >= 100 else distance_weight
                candidates.append(track_id_score * confidence_score * distance_inverted)
            else:
                candidates.append(0)

        if not candidates and hand:
            return None
        return bboxes[np.argmax(candidates)] if candidates else (previous_bbox if (self.is_inside or self.is_touched) and not hand else None)

    # Bounding box helper functions

    def get_bb_bounds(self, BB):
        BB_x, BB_y, BB_w, BB_h = BB[:4]
        return (BB_x + BB_w // 2,   # right
                BB_x - BB_w // 2,   # left
                BB_y - BB_h // 2,   # top
                BB_y + BB_h // 2)   # bottom

    def get_intensity(self, handBB, targetBB, vibration_intensities, depth_img=None):
        """
        Calculate per-motor vibration intensities from the hand→target angle.

        NOTE: Depth-based front/back motor control has been removed because it
        was unreachable (existed after an unconditional `return`).  The placeholder
        50 for depth_intensity is still returned to keep the call signature stable.
        When a front motor is wired up, implement depth_intensity here before the
        return statement.
        """
        xc_hand,   yc_hand   = handBB[:2]
        xc_target, yc_target = targetBB[:2]
        angle = np.degrees(
            np.arctan2(yc_hand - yc_target, xc_target - xc_hand)
        ) % 360

        right_int = left_int = top_int = bot_int = 0
        max_b = vibration_intensities["bottom"]
        max_t = vibration_intensities["top"]
        max_l = vibration_intensities["left"]
        max_r = vibration_intensities["right"]

        if self.navigation_type == 0:
            # Quadrant: horizontal → vertical cardinal directions only
            if 0 <= angle < 80 or 280 <= angle < 360:
                right_int = max_r
            elif 80 <= angle < 100:
                top_int = max_t
            elif 100 <= angle < 260:
                left_int = max_l
            elif 260 <= angle < 280:
                bot_int = max_b

        elif self.navigation_type == 1:
            # Octant: simultaneous two-motor blending
            if 0 <= angle < 45:
                right_int = max_r
                top_int   = (angle / 45) * max_t
            elif angle == 45:
                right_int = max_r;  top_int = max_t
            elif 45 < angle < 90:
                right_int = ((90 - angle) / 45) * max_r;  top_int = max_t
            elif 90 <= angle < 135:
                top_int  = max_t
                left_int = ((angle - 90) / 45) * max_l
            elif angle == 135:
                top_int = max_t;  left_int = max_l
            elif 135 < angle < 180:
                top_int  = ((180 - angle) / 45) * max_t;  left_int = max_l
            elif 180 <= angle < 225:
                left_int = max_l
                bot_int  = ((angle - 180) / 45) * max_b
            elif angle == 225:
                left_int = max_l;  bot_int = max_b
            elif 225 < angle < 270:
                left_int = ((270 - angle) / 45) * max_l;  bot_int = max_b
            elif 270 <= angle < 315:
                bot_int   = max_b
                right_int = ((angle - 270) / 45) * max_r
            elif angle == 315:
                bot_int = max_b;  right_int = max_r
            elif 315 < angle <= 360:
                bot_int   = ((360 - angle) / 45) * max_b;  right_int = max_r

        elif self.navigation_type == 2:
            # Quadrant: smooth linear blending across full 90° arcs
            if 0 <= angle < 90:
                right_int = ((90 - angle) / 90) * max_r
                top_int   = (angle / 90) * max_t
            elif 90 <= angle < 180:
                top_int  = ((180 - angle) / 90) * max_t
                left_int = ((angle - 90) / 90) * max_l
            elif 180 <= angle < 270:
                left_int = ((270 - angle) / 90) * max_l
                bot_int  = ((angle - 180) / 90) * max_b
            elif 270 <= angle < 360:
                bot_int   = ((360 - angle) / 90) * max_b
                right_int = ((angle - 270) / 90) * max_r

        # depth_intensity placeholder (front/back motor not yet wired)
        depth_intensity = 50
        return int(right_int), int(left_int), int(top_int), int(bot_int), depth_intensity

    # Overlap (grasping detection)

    def check_overlap(self, handBB, targetBB, frozen=False):
        hand_x,   hand_y,   hand_w,   hand_h   = handBB[:4]
        target_x, target_y, target_w, target_h = targetBB[:4]

        hand_r,  hand_l  = hand_x   + hand_w   // 2, hand_x   - hand_w   // 2
        hand_t,  hand_b  = hand_y   - hand_h   // 2, hand_y   + hand_h   // 2
        tgt_r,   tgt_l   = target_x + target_w // 2, target_x - target_w // 2
        tgt_t,   tgt_b   = target_y - target_h // 2, target_y + target_h // 2

        touched_l = hand_r >= tgt_l and hand_l <= tgt_l and hand_t <= tgt_b and hand_b >= tgt_t
        touched_r = hand_l <= tgt_r and hand_r >= tgt_r and hand_t <= tgt_b and hand_b >= tgt_t
        touched_t = hand_b >= tgt_t and hand_t <= tgt_t and hand_r >= tgt_l and hand_l <= tgt_r
        touched_b = hand_t <= tgt_b and hand_b >= tgt_b and hand_r >= tgt_l and hand_l <= tgt_r
        self.is_inside  = (hand_l >= tgt_l and hand_r <= tgt_r and
                           hand_t >= tgt_t and hand_b <= tgt_b)
        self.is_touched = touched_l or touched_r or touched_t or touched_b

        if self.is_touched or self.is_inside:
            frozen = True
            inside_center = (tgt_l <= hand_x <= tgt_r) and (tgt_t <= hand_y <= tgt_b)
            return inside_center, target_x, target_y, target_w, target_h, frozen
        return False, target_x, target_y, target_w, target_h, False

    # Hand navigation function

    def navigate_hand(self, belt_controller, bboxes, target_cls,
                      hand_clss, depth_img, vibration_intensities=None, metric=False):
        """
        Navigate the hand toward the target object.

        BLE write rate is capped at 5 Hz (200 ms) on EVERY vibration path.
        The smoothened-guidance (case 3) timeout is now wall-clock based (~2 s)
        instead of frame-count based.
        """
        if vibration_intensities is None:
            vibration_intensities = self.vibration_intensities

        overlapping = False

        bboxes_hands   = [d for d in bboxes if d[5] in hand_clss]
        bboxes_objects = [d for d in bboxes if d[5] == target_cls]

        hand   = self.choose_detection(bboxes_hands,   self.prev_hand,   hand=True)
        target = self.choose_detection(bboxes_objects, self.prev_target, hand=False)
        self.prev_hand   = hand
        self.prev_target = target

        # Data collection
        if self.navigation_time != 'NA':
            if target is not None:
                self.target_detections_list.append(1)
                self.target_confidence_list.append(target[6])
                for t in bboxes_objects:
                    self.target_class_list.append(int(t[4]))
                self.target_class_track_ids.append(self.target_class_list)
                self.target_class_list = []
                self.target_object_track_ids.append(int(target[4]))
                self.target_position.append([target[0], target[1]])
            else:
                self.target_detections_list.append(0)
                self.target_confidence_list.append(0)
                self.target_class_track_ids.append([])
                self.target_object_track_ids.append('NA')
                self.target_position.append([0, 0])
            if hand is not None:
                self.hand_position.append([hand[0], hand[1]])
                self.hand_confidence_list.append(hand[6])
            else:
                self.hand_position.append([0, 0])
                self.hand_confidence_list.append('NA')

        # Intensitites computation
        right_int = left_int = top_int = bot_int = depth_int = 0

        if hand is not None and target is not None:
            if self.navigation_time == 'NA':
                self.navigation_time = time.time()
                self.target_detections_list.append(1)
                self.target_confidence_list.append(target[6])
                for t in bboxes_objects:
                    self.target_class_list.append(int(t[4]))
                self.target_class_track_ids.append(self.target_class_list)
                self.target_class_list = []
                self.target_object_track_ids.append(int(target[4]))
                self.target_position.append([target[0], target[1]])
                self.hand_position.append([hand[0], hand[1]])
                self.hand_confidence_list.append(hand[6])

            if depth_img is None:
                if self.obstacle_target is not None and not np.array_equal(self.obstacle_target, target[:2]):
                    if self.roi_min_y > 5:
                        right_int, left_int, top_int, bot_int, depth_int = \
                            self.get_intensity(hand, self.obstacle_target, vibration_intensities)
                    else:
                        return self._signal_move_back(belt_controller)
                else:
                    right_int, left_int, top_int, bot_int, depth_int = \
                        self.get_intensity(hand, target, vibration_intensities)
            else:
                obstacles_mask = map_obstacles(hand, target, depth_img, metric)
                if not check_obstacles_between_points(hand, target, obstacles_mask, 1):
                    self.obstacle_target = None
                    right_int, left_int, top_int, bot_int, depth_int = \
                        self.get_intensity(hand, target, vibration_intensities)
                else:
                    self.obstacle_target, self.corners, self.roi_coords, self.roi_min_y = \
                        find_obstacle_target_point(hand, target, obstacles_mask)
                    if self.roi_min_y > 5:
                        right_int, left_int, top_int, bot_int, depth_int = \
                            self.get_intensity(hand, self.obstacle_target, vibration_intensities, depth_img)
                    else:
                        return self._signal_move_back(belt_controller)

            frozen_BB  = [self.frozen_x, self.frozen_y, self.frozen_w, self.frozen_h]
            frozen_target = target

            if not self.frozen:
                overlapping, self.frozen_x, self.frozen_y, self.frozen_w, self.frozen_h, self.frozen = \
                    self.check_overlap(hand, target, self.frozen)
            else:
                if self.freezing_time == 'NA':
                    self.freezing_time = time.time()
                overlapping, self.frozen_x, self.frozen_y, self.frozen_w, self.frozen_h, self.frozen = \
                    self.check_overlap(hand, frozen_BB, self.frozen)
                frozen_target[:4] = frozen_BB

        elif hand is None:
            frozen_target = None
            self.frozen     = False
            self.is_inside  = False
            self.is_touched = False

        # 1. Overlap -> send grasping signal
        if overlapping:
            self.grasping_time = time.time()
            self.searching = True
            if belt_controller and self.vibrate:
                belt_controller.stop_vibration()
                belt_controller.send_pulse_command(
                    channel_index=1,
                    orientation_type=BeltOrientationType.BINARY_MASK,
                    orientation=0b111100,
                    intensity=abs(depth_int),
                    on_duration_ms=150, pulse_period=300, pulse_iterations=5,
                    series_period=5000, series_iterations=1,
                    timer_option=BeltVibrationTimerOption.RESET_TIMER,
                    exclusive_channel=False, clear_other_channels=False)
                self.vibrate = False
                self.prev_target = None
                frozen_target = None
                self.was_guiding = False
            print("GRASP! Success? (Y/N)")
            return overlapping, frozen_target

        # 2. Active guidance (hand + target both visible)
        if hand is not None and target is not None:
            self.searching    = True
            self.was_guiding  = True
            self._smoothed_guidance_start = 0.0   # reset smoothed-guidance timer

            now = time.time()
            if now - self.last_vib_update_time >= 0.2:
                intensity_changed = (
                    abs(right_int - self.prev_right_intensity) > 5 or
                    abs(left_int  - self.prev_left_intensity)  > 5 or
                    abs(top_int   - self.prev_top_intensity)   > 5 or
                    abs(bot_int   - self.prev_bot_intensity)   > 5
                )
                if intensity_changed:
                    self.prev_right_intensity = right_int
                    self.prev_left_intensity  = left_int
                    self.prev_top_intensity   = top_int
                    self.prev_bot_intensity   = bot_int
                    self.last_vib_update_time = now

                    if belt_controller and self.vibrate:
                        belt_controller.send_vibration_command(
                            channel_index=0, pattern=1, intensity=right_int,
                            orientation_type=2, orientation=120,
                            pattern_iterations=None, pattern_period=100,
                            pattern_start_time=0,
                            exclusive_channel=False, clear_other_channels=False)
                        belt_controller.send_vibration_command(
                            channel_index=1, pattern=1, intensity=left_int,
                            orientation_type=2, orientation=45,
                            pattern_iterations=None, pattern_period=100,
                            pattern_start_time=0,
                            exclusive_channel=False, clear_other_channels=False)
                        belt_controller.send_vibration_command(
                            channel_index=0, pattern=1, intensity=top_int,
                            orientation_type=2, orientation=90,
                            pattern_iterations=None, pattern_period=100,
                            pattern_start_time=0,
                            exclusive_channel=False, clear_other_channels=False)
                        belt_controller.send_vibration_command(
                            channel_index=1, pattern=1, intensity=bot_int,
                            orientation_type=2, orientation=60,
                            pattern_iterations=None, pattern_period=100,
                            pattern_start_time=0,
                            exclusive_channel=False, clear_other_channels=False)

            if self.mock_navigate:
                print(f'Guidance; R:{right_int} L:{left_int} T:{top_int} B:{bot_int}')
            return overlapping, frozen_target

        # 3. Smoothened guidance — target/hand temporarily lost
        if self.was_guiding:
            self.searching = True

            # Wall-clock timeout initialisation
            if self._smoothed_guidance_start == 0.0:
                self._smoothed_guidance_start = time.time()

            # Send last known intensities at 5 Hz (same throttle as active guidance)
            now = time.time()
            if belt_controller and self.vibrate and (now - self.last_vib_update_time >= 0.2):
                self.last_vib_update_time = now
                for channel, intensity, orientation in [
                    (0, self.prev_right_intensity, 120),
                    (1, self.prev_left_intensity,  45),
                    (2, self.prev_top_intensity,   90),
                    (3, self.prev_bot_intensity,   60),
                ]:
                    belt_controller.send_vibration_command(
                        channel_index=channel,
                        pattern=BeltVibrationPattern.CONTINUOUS,
                        intensity=intensity,
                        orientation_type=BeltOrientationType.ANGLE,
                        orientation=orientation,
                        pattern_iterations=None, pattern_period=100,
                        pattern_start_time=0,
                        exclusive_channel=False, clear_other_channels=False)

            # Wall-clock ~2 second timeout (replaces `if self.timer >= 40`)
            if now - self._smoothed_guidance_start >= 2.0:
                self.was_guiding = False
                self._smoothed_guidance_start = 0.0
                if belt_controller and self.vibrate:
                    belt_controller.stop_vibration()

            if self.mock_navigate:
                print(f'Smoothed; R:{self.prev_right_intensity} L:{self.prev_left_intensity} '
                      f'T:{self.prev_top_intensity} B:{self.prev_bot_intensity}')
            return overlapping, None

        # 4. Target visible, hand not yet in frame
        if target is not None:
            if belt_controller and self.vibrate and self.searching:
                self.searching = False
                belt_controller.stop_vibration()
                belt_controller.send_pulse_command(
                    channel_index=0,
                    orientation_type=BeltOrientationType.ANGLE,
                    orientation=60,
                    intensity=max(vibration_intensities['bottom'], 20),
                    on_duration_ms=150, pulse_period=500, pulse_iterations=5,
                    series_period=5000, series_iterations=1,
                    timer_option=BeltVibrationTimerOption.RESET_TIMER,
                    exclusive_channel=False, clear_other_channels=False)
            if self.mock_navigate:
                print("4. Target located, hand absent")
            return overlapping, target

        # 5. Neither target nor hand visible
        self.searching = True
        if belt_controller and self.vibrate:
            belt_controller.stop_vibration()
        if self.mock_navigate:
            pass
        return overlapping, None

    # Helper function: signal obstacle / retreat
    def _signal_move_back(self, belt_controller):
        self.searching = True
        if belt_controller and self.vibrate:
            belt_controller.stop_vibration()
            belt_controller.send_pulse_command(
                channel_index=1,
                orientation_type=BeltOrientationType.BINARY_MASK,
                orientation=0b101000,
                intensity=30,
                on_duration_ms=150, pulse_period=300, pulse_iterations=5,
                series_period=5000, series_iterations=1,
                timer_option=BeltVibrationTimerOption.RESET_TIMER,
                exclusive_channel=False, clear_other_channels=False)
        return False, None
