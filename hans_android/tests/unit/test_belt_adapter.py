"""Unit tests for belt approach, avoidance, calibration, and nav cues."""
from __future__ import annotations

import time

import numpy as np

from server.feedback_devices.adapters.belt_adapter import BeltAdapter, MOTOR_COUNT
from server.feedback_devices.base import NavigationContext
from server.feedback_devices.handoff import (
    HANDOFF_ENTER_CM,
    HANDOFF_EXIT_CM,
    PRE_HANDOFF_CM,
)
from server.feedback_devices.orientation import ANGLE, MOTOR_INDEX


class _FakeBelt:
    def __init__(self):
        self.commands = []
        self.pulses = []
        self.stopped = 0

    def send_vibration_command(self, **kwargs):
        self.commands.append(kwargs)
        return True

    def send_pulse_command(self, **kwargs):
        self.pulses.append(kwargs)
        return True

    def stop_vibration(self, **kwargs):
        self.stopped += 1
        return True


def _ctx(detections, frame_shape=(480, 640), depth_img=None):
    return NavigationContext(
        raw_detections=detections,
        target_class_id=39,  # bottle
        hand_class_ids=[80, 81],
        depth_img=depth_img,
        vibration_intensities={'belt': 60},
        metric=True,
        frame_shape=frame_shape,
    )


def _det(xc, yc, depth_m, cls=39):
    return [xc, yc, 40, 60, 1, cls, 0.9, depth_m]


def _depth_map(h=480, w=640, fill_m=2.0):
    return np.full((h, w), fill_m, dtype=np.float32)


def _mid_obstacle(depth, depth_m=0.8):
    # Standing obstacle in the forward corridor mid-band
    d = depth.copy()
    d[140:260, 280:360] = depth_m
    return d


def _feed(belt, detections, depth_img=None, n=1):
    """Run n update frames, clearing cue hold so continuous steering can run."""
    for _ in range(n):
        belt.update(_ctx(detections, depth_img=depth_img))
        belt._cue_hold_until = 0.0
        belt._last_dir_change_time = 0.0


def _confirm_obstacle_frames(belt):
    """Frames needed before avoidance steering arms (respects lowered PLAN_LOCK)."""
    return min(belt.OBSTACLE_CONFIRM_FRAMES, belt.PLAN_LOCK_FRAMES)


def _uncalibrated_belt(tmp_path, monkeypatch):
    """BeltAdapter without loading the on-disk navel calibration."""
    from server.feedback_devices.adapters import belt_adapter as ba
    monkeypatch.setattr(ba, '_NAVEL_CALIB_PATH', tmp_path / 'no_navel.json')
    return BeltAdapter(_FakeBelt())


class TestBeltApproach:
    def test_vibrates_when_far_and_centered(self, tmp_path, monkeypatch):
        belt = _uncalibrated_belt(tmp_path, monkeypatch)
        fake = belt._virtual_belt
        belt.update(_ctx([_det(320, 240, 2.5)]))
        belt._cue_hold_until = 0.0
        belt.update(_ctx([_det(320, 240, 2.5)]))
        assert fake.commands, 'expected a vibration command'
        cmd = fake.commands[-1]
        assert cmd['orientation_type'] == ANGLE
        assert cmd['orientation'] == 0
        assert belt.get_status()['in_approach'] is True

    def test_steers_right_when_target_on_right(self, tmp_path, monkeypatch):
        belt = _uncalibrated_belt(tmp_path, monkeypatch)
        fake = belt._virtual_belt
        belt.update(_ctx([_det(560, 240, 2.5)]))
        belt._cue_hold_until = 0.0
        belt.update(_ctx([_det(560, 240, 2.5)]))
        assert fake.commands
        angle = fake.commands[-1]['orientation']
        assert 60 <= angle <= 90

    def test_steers_left_when_target_on_left(self, tmp_path, monkeypatch):
        belt = _uncalibrated_belt(tmp_path, monkeypatch)
        fake = belt._virtual_belt
        belt.update(_ctx([_det(80, 240, 2.5)]))
        belt._cue_hold_until = 0.0
        belt.update(_ctx([_det(80, 240, 2.5)]))
        assert fake.commands
        angle = fake.commands[-1]['orientation']
        assert angle >= 270 or angle == 0

    def test_stops_at_handoff_and_pulses(self, tmp_path, monkeypatch):
        belt = _uncalibrated_belt(tmp_path, monkeypatch)
        fake = belt._virtual_belt
        belt.update(_ctx([_det(320, 240, 2.5)]))
        belt._cue_hold_until = 0.0
        belt.update(_ctx([_det(320, 240, 2.5)]))
        assert belt.get_status()['in_approach'] is True
        n_cmd = len(fake.commands)

        belt.update(_ctx([_det(320, 240, HANDOFF_ENTER_CM / 100.0)]))
        assert belt.get_status()['in_approach'] is False
        assert fake.stopped >= 1
        assert fake.pulses
        assert len(fake.commands) == n_cmd

    def test_hysteresis_keeps_belt_off_until_exit(self, tmp_path, monkeypatch):
        belt = _uncalibrated_belt(tmp_path, monkeypatch)
        fake = belt._virtual_belt
        belt.update(_ctx([_det(320, 240, 2.5)]))
        belt._cue_hold_until = 0.0
        belt.update(_ctx([_det(320, 240, 2.5)]))
        belt.update(_ctx([_det(320, 240, HANDOFF_ENTER_CM / 100.0)]))
        fake.commands.clear()

        mid = (HANDOFF_ENTER_CM + HANDOFF_EXIT_CM) / 2 / 100.0
        belt.update(_ctx([_det(320, 240, mid)]))
        assert belt.get_status()['in_approach'] is False
        assert not fake.commands

        belt._cue_hold_until = 0.0
        belt.update(_ctx([_det(320, 240, HANDOFF_EXIT_CM / 100.0 + 0.05)]))
        assert belt.get_status()['in_approach'] is True
        assert fake.commands

    def test_unknown_depth_still_guides_by_bearing(self, tmp_path, monkeypatch):
        belt = _uncalibrated_belt(tmp_path, monkeypatch)
        fake = belt._virtual_belt
        belt.update(_ctx([_det(500, 240, -1.0)]))
        belt._cue_hold_until = 0.0
        belt.update(_ctx([_det(500, 240, -1.0)]))
        assert fake.commands
        assert belt.get_status()['in_approach'] is True


class TestBeltNavCues:
    def test_nav_start_cue_on_first_update(self):
        fake = _FakeBelt()
        belt = BeltAdapter(fake)
        belt.update(_ctx([_det(320, 240, 2.5)]))
        assert fake.pulses
        assert belt.get_status().get('last_cue') == 'nav_start'
        assert fake.pulses[-1]['orientation_type'] == MOTOR_INDEX
        assert fake.pulses[-1]['pulse_iterations'] == 3

    def test_pre_handoff_cue(self):
        fake = _FakeBelt()
        belt = BeltAdapter(fake)
        belt.update(_ctx([_det(320, 240, 2.5)]))
        belt._cue_hold_until = 0.0
        fake.pulses.clear()
        belt.update(_ctx([_det(320, 240, PRE_HANDOFF_CM / 100.0)]))
        assert belt.get_status().get('last_cue') == 'pre_handoff'
        assert fake.pulses
        assert fake.pulses[-1]['orientation_type'] == MOTOR_INDEX
        assert fake.pulses[-1]['pulse_iterations'] == 4

    def test_pre_handoff_uses_calibrated_navel(self, tmp_path, monkeypatch):
        from server.feedback_devices.adapters import belt_adapter as ba
        monkeypatch.setattr(ba, '_NAVEL_CALIB_PATH', tmp_path / 'belt_navel_motor.json')

        fake = _FakeBelt()
        belt = BeltAdapter(fake)
        belt.MOTOR_TEST_DURATION_S = 0.01
        belt.set_navel_motor(0)
        belt._calibration_until = 0.0
        belt._reset_nav_cues()
        belt.update(_ctx([_det(320, 240, 2.5)]))
        belt._cue_hold_until = 0.0
        fake.pulses.clear()
        belt.update(_ctx([_det(320, 240, PRE_HANDOFF_CM / 100.0)]))
        assert fake.pulses[-1]['orientation'] == 0

    def test_direction_change_one_all_around_pulse(self, tmp_path, monkeypatch):
        belt = _uncalibrated_belt(tmp_path, monkeypatch)
        fake = belt._virtual_belt
        belt.BEARING_SMOOTH_ALPHA = 1.0  # no smoothing — sector must flip cleanly
        belt.update(_ctx([_det(320, 240, 2.5)]))  # center → nav_start
        belt._cue_hold_until = 0.0
        belt._last_dir_change_time = 0.0
        fake.pulses.clear()
        belt.update(_ctx([_det(560, 240, 2.5)]))  # right sector
        assert belt.get_status().get('last_cue') == 'direction_change'
        assert fake.pulses
        assert fake.pulses[-1]['pulse_iterations'] == 1
        assert fake.pulses[-1]['orientation'] == 0b111111


class TestBeltMotorCalibration:
    def test_motor_index_uses_motor_index_type(self, tmp_path, monkeypatch):
        from server.feedback_devices.adapters import belt_adapter as ba
        monkeypatch.setattr(ba, '_NAVEL_CALIB_PATH', tmp_path / 'belt_navel_motor.json')

        fake = _FakeBelt()
        belt = BeltAdapter(fake)
        belt.MOTOR_TEST_DURATION_S = 0.01
        out = belt.test_motor_index(5)
        assert out['ok'] is True
        assert out['motor_index'] == 5
        assert fake.commands[-1]['orientation_type'] == MOTOR_INDEX
        assert fake.commands[-1]['orientation'] == 5

    def test_indices_4_and_5_are_distinct(self, tmp_path, monkeypatch):
        """Regression: old bitmask-as-MOTOR_INDEX made 4 and 5 both map to motor 0."""
        from server.feedback_devices.adapters import belt_adapter as ba
        monkeypatch.setattr(ba, '_NAVEL_CALIB_PATH', tmp_path / 'belt_navel_motor.json')

        fake = _FakeBelt()
        belt = BeltAdapter(fake)
        belt.MOTOR_TEST_DURATION_S = 0.01
        belt.test_motor_index(4)
        belt.test_motor_index(5)
        assert fake.commands[-2]['orientation'] == 4
        assert fake.commands[-1]['orientation'] == 5

    def test_set_navel_maps_cardinals(self, tmp_path, monkeypatch):
        from server.feedback_devices.adapters import belt_adapter as ba
        monkeypatch.setattr(ba, '_NAVEL_CALIB_PATH', tmp_path / 'belt_navel_motor.json')

        fake = _FakeBelt()
        belt = BeltAdapter(fake)
        belt.MOTOR_TEST_DURATION_S = 0.01
        belt.MOTOR_TEST_GAP_S = 0.0
        belt.set_navel_motor(3)
        assert belt._navel_motor_index == 3
        assert belt._motor_index_for_body_angle(0) == 3
        assert belt._motor_index_for_body_angle(90) == (3 + 4) % MOTOR_COUNT
        assert belt._motor_index_for_body_angle(270) == (3 - 4) % MOTOR_COUNT

    def test_calibrated_navigation_uses_motor_index(self, tmp_path, monkeypatch):
        from server.feedback_devices.adapters import belt_adapter as ba
        monkeypatch.setattr(ba, '_NAVEL_CALIB_PATH', tmp_path / 'belt_navel_motor.json')

        fake = _FakeBelt()
        belt = BeltAdapter(fake)
        belt.MOTOR_TEST_DURATION_S = 0.01
        belt.set_navel_motor(2)
        fake.commands.clear()
        belt._calibration_until = 0.0
        belt._cue_hold_until = 0.0
        # Reset cue state so first update after calib doesn't only fire nav_start hold
        belt._reset_nav_cues()
        belt.update(_ctx([_det(320, 240, 2.5)]))
        belt._cue_hold_until = 0.0
        belt.update(_ctx([_det(320, 240, 2.5)]))
        assert fake.commands
        cmd = fake.commands[-1]
        assert cmd['orientation_type'] == MOTOR_INDEX
        assert cmd['orientation'] == 2

    def test_calibration_blocks_navigation_update(self):
        fake = _FakeBelt()
        belt = BeltAdapter(fake)
        belt._calibration_until = time.time() + 5.0
        belt.update(_ctx([_det(320, 240, 2.0)]))
        assert fake.commands == []


class TestBeltAvoidance:
    def test_enters_avoidance_without_obstacle_pulse(self):
        fake = _FakeBelt()
        belt = BeltAdapter(fake)
        belt._navel_motor_index = None  # ANGLE mode for assertions
        depth = _mid_obstacle(_depth_map(fill_m=2.5))

        _feed(belt, [_det(320, 240, 2.5)], depth, n=_confirm_obstacle_frames(belt))
        assert belt.get_status()['avoidance_active'] is True
        assert not any(p.get('orientation') == 0b101000 for p in fake.pulses)
        assert fake.commands  # continuous steering to waypoint

    def test_steers_left_when_target_is_on_left(self):
        fake = _FakeBelt()
        belt = BeltAdapter(fake)
        belt._navel_motor_index = None  # ANGLE mode for assertions
        depth = _mid_obstacle(_depth_map(fill_m=2.5))

        # Target on the left of the FOV → pass left of obstacle
        _feed(belt, [_det(120, 240, 2.5)], depth, n=_confirm_obstacle_frames(belt))
        status = belt.get_status()
        assert status['avoidance_active'] is True
        assert status['avoid_side'] == 'left'
        angle = fake.commands[-1]['orientation']
        assert angle >= 270 or angle == 0

    def test_steers_right_when_target_is_on_right(self):
        fake = _FakeBelt()
        belt = BeltAdapter(fake)
        belt._navel_motor_index = None  # ANGLE mode for assertions
        depth = _mid_obstacle(_depth_map(fill_m=2.5))

        _feed(belt, [_det(520, 240, 2.5)], depth, n=_confirm_obstacle_frames(belt))
        status = belt.get_status()
        assert status['avoidance_active'] is True
        assert status['avoid_side'] == 'right'
        angle = fake.commands[-1]['orientation']
        # Waypoint is only ~0.2 m past the obstacle → moderate right bearing
        assert 0 < angle <= 90

    def test_locks_side_until_obstacle_cleared(self):
        fake = _FakeBelt()
        belt = BeltAdapter(fake)
        belt._navel_motor_index = None  # ANGLE mode for assertions
        depth = _mid_obstacle(_depth_map(fill_m=2.5))

        _feed(belt, [_det(120, 240, 2.5)], depth, n=_confirm_obstacle_frames(belt))
        assert belt.get_status()['avoid_side'] == 'left'

        # Target jumps right while still blocked — side stays locked
        _feed(belt, [_det(520, 240, 2.5)], depth, n=1)
        assert belt.get_status()['avoid_side'] == 'left'

    def test_exits_avoidance_after_corridor_clear(self):
        fake = _FakeBelt()
        belt = BeltAdapter(fake)
        belt._navel_motor_index = None  # ANGLE mode for assertions
        blocked = _mid_obstacle(_depth_map(fill_m=2.5))
        clear = _depth_map(fill_m=2.5)

        _feed(belt, [_det(520, 240, 2.5)], blocked, n=_confirm_obstacle_frames(belt))
        assert belt.get_status()['avoidance_active'] is True

        # Brief clears should NOT exit yet
        _feed(belt, [_det(520, 240, 2.5)], clear, n=5)
        assert belt.get_status()['avoidance_active'] is True

        _feed(belt, [_det(520, 240, 2.5)], clear, n=BeltAdapter.CLEAR_FRAMES_TO_EXIT)

        assert belt.get_status()['avoidance_active'] is False
        # Side may be retained once a plan is locked (static strategy)
        status = belt.get_status()
        if status.get('plan_locked') and status.get('plan_has_obstacle'):
            assert status.get('plan_obstacle_done') is True
            assert status.get('avoid_side') == 'right'
        else:
            assert status.get('avoid_side') is None

    def test_keeps_waypoint_while_clearing(self):
        fake = _FakeBelt()
        belt = BeltAdapter(fake)
        belt._navel_motor_index = None  # ANGLE mode for assertions
        blocked = _mid_obstacle(_depth_map(fill_m=2.5))
        clear = _depth_map(fill_m=2.5)

        _feed(belt, [_det(520, 240, 2.5)], blocked, n=_confirm_obstacle_frames(belt))
        _feed(belt, [_det(520, 240, 2.5)], clear, n=1)
        viz = belt.get_debug_viz()
        assert belt.get_status()['avoidance_active'] is True
        assert viz.get('phase') == 'avoid_B_clearing'
        # Still aiming at the side waypoint, not the target center
        assert viz.get('steer_x') != 520
        assert 0 < viz.get('angle_deg', 0) <= 90

    def test_target_dropout_does_not_reset_avoidance(self):
        fake = _FakeBelt()
        belt = BeltAdapter(fake)
        belt._navel_motor_index = None  # ANGLE mode for assertions
        blocked = _mid_obstacle(_depth_map(fill_m=2.5))

        _feed(belt, [_det(520, 240, 2.5)], blocked, n=_confirm_obstacle_frames(belt))
        assert belt.get_status()['avoidance_active'] is True

        # Bottle briefly missing from detections (occlusion)
        _feed(belt, [], blocked, n=1)
        assert belt.get_status()['avoidance_active'] is True
        assert belt.get_debug_viz().get('phase') == 'avoid_A_hold'

    def test_floor_band_does_not_start_avoidance(self):
        fake = _FakeBelt()
        belt = BeltAdapter(fake)
        belt._navel_motor_index = None  # ANGLE mode for assertions
        depth = _depth_map(fill_m=2.5)
        depth[360:480, 200:440] = 0.6

        _feed(belt, [_det(320, 240, 2.5)], depth, n=_confirm_obstacle_frames(belt))
        assert belt.get_status()['avoidance_active'] is False

    def test_no_avoidance_after_handoff(self):
        fake = _FakeBelt()
        belt = BeltAdapter(fake)
        belt._navel_motor_index = None  # ANGLE mode for assertions
        depth = _mid_obstacle(_depth_map(fill_m=0.4), depth_m=0.2)

        _feed(belt, [_det(320, 240, 2.5)], _depth_map(fill_m=2.5), n=1)
        _feed(belt, [_det(320, 240, 0.4)], depth, n=1)
        assert belt.get_status()['in_approach'] is False
        assert belt.get_status()['avoidance_active'] is False


class TestBeltUnifiedClearAndObstacle:
    """One codebase must handle clear-path and obstacle trials equally well."""

    def test_single_frame_noise_does_not_start_avoidance(self, tmp_path, monkeypatch):
        belt = _uncalibrated_belt(tmp_path, monkeypatch)
        belt._navel_motor_index = None
        clear = _depth_map(fill_m=2.5)
        noisy = _mid_obstacle(clear)

        _feed(belt, [_det(320, 240, 2.5)], clear, n=2)
        _feed(belt, [_det(320, 240, 2.5)], noisy, n=1)  # one-frame glitch
        _feed(belt, [_det(320, 240, 2.5)], clear, n=2)

        status = belt.get_status()
        assert status['avoidance_active'] is False
        assert status['avoid_side'] is None
        viz = belt.get_debug_viz()
        assert viz.get('steer_x') == 320 or abs(viz.get('steer_x', 0) - 320) < 1

    def test_clear_path_locks_direct_and_steers_to_target(self, tmp_path, monkeypatch):
        belt = _uncalibrated_belt(tmp_path, monkeypatch)
        belt._navel_motor_index = None
        belt.PLAN_LOCK_FRAMES = 3
        clear = _depth_map(fill_m=2.5)

        _feed(belt, [_det(520, 240, 2.5)], clear, n=3)
        status = belt.get_status()
        assert status['plan_locked'] is True
        assert status['plan_has_obstacle'] is False
        assert status['navigation_mode'] == 'direct'
        assert status['avoidance_active'] is False
        angle = belt._virtual_belt.commands[-1]['orientation']
        assert 0 < angle <= 90  # right of center → toward bottle

    def test_locked_direct_ignores_late_false_obstacle(self, tmp_path, monkeypatch):
        belt = _uncalibrated_belt(tmp_path, monkeypatch)
        belt._navel_motor_index = None
        belt.PLAN_LOCK_FRAMES = 3
        clear = _depth_map(fill_m=2.5)
        noisy = _mid_obstacle(clear)

        _feed(belt, [_det(320, 240, 2.5)], clear, n=3)
        assert belt.get_status()['navigation_mode'] == 'direct'

        _feed(belt, [_det(320, 240, 2.5)], noisy, n=8)
        status = belt.get_status()
        assert status['plan_locked'] is True
        assert status['plan_has_obstacle'] is False
        assert status['navigation_mode'] == 'direct'
        assert status['avoidance_active'] is False
        assert belt.get_debug_viz().get('phase') == 'approach'
        assert abs(belt.get_debug_viz().get('steer_x', 0) - 320) < 1

    def test_sustained_obstacle_still_enters_avoidance(self, tmp_path, monkeypatch):
        belt = _uncalibrated_belt(tmp_path, monkeypatch)
        belt._navel_motor_index = None
        depth = _mid_obstacle(_depth_map(fill_m=2.5))

        # Fewer than confirm → still clear-path steering
        _feed(belt, [_det(520, 240, 2.5)], depth, n=_confirm_obstacle_frames(belt) - 1)
        assert belt.get_status()['avoidance_active'] is False
        assert belt.get_debug_viz().get('phase') in ('approach', 'approach_confirming_obs')

        _feed(belt, [_det(520, 240, 2.5)], depth, n=1)
        assert belt.get_status()['avoidance_active'] is True
        assert belt.get_status()['avoid_side'] == 'right'


class TestBeltPlanFreeze:
    def test_locks_obstacle_plan_and_ignores_side_flip(self, tmp_path, monkeypatch):
        from server.feedback_devices.adapters import belt_adapter as ba
        monkeypatch.setattr(ba, '_NAVEL_CALIB_PATH', tmp_path / 'no_navel.json')

        fake = _FakeBelt()
        belt = BeltAdapter(fake)
        belt._navel_motor_index = None
        belt.PLAN_LOCK_FRAMES = 3
        depth = _mid_obstacle(_depth_map(fill_m=2.5))

        _feed(belt, [_det(120, 240, 2.5)], depth, n=3)
        assert belt.get_status()['plan_locked'] is True
        assert belt.get_status()['plan_has_obstacle'] is True
        assert belt.get_status()['avoid_side'] == 'left'
        assert belt.get_status()['navigation_mode'] == 'avoid'

        # Target jumps to the right — locked plan must keep left
        _feed(belt, [_det(520, 240, 2.5)], depth, n=5)
        assert belt.get_status()['avoid_side'] == 'left'
        assert belt.get_status()['plan_locked'] is True

    def test_locks_direct_approach_when_corridor_clear(self, tmp_path, monkeypatch):
        from server.feedback_devices.adapters import belt_adapter as ba
        monkeypatch.setattr(ba, '_NAVEL_CALIB_PATH', tmp_path / 'no_navel.json')

        fake = _FakeBelt()
        belt = BeltAdapter(fake)
        belt._navel_motor_index = None
        belt.PLAN_LOCK_FRAMES = 3
        clear = _depth_map(fill_m=2.5)

        _feed(belt, [_det(320, 240, 2.5)], clear, n=3)
        assert belt.get_status()['plan_locked'] is True
        assert belt.get_status()['plan_has_obstacle'] is False
        assert belt.get_status()['avoidance_active'] is False
        assert belt.get_status()['navigation_mode'] == 'direct'

    def test_idle_unlocks_plan(self, tmp_path, monkeypatch):
        from server.feedback_devices.adapters import belt_adapter as ba
        monkeypatch.setattr(ba, '_NAVEL_CALIB_PATH', tmp_path / 'no_navel.json')

        fake = _FakeBelt()
        belt = BeltAdapter(fake)
        belt._navel_motor_index = None
        belt.PLAN_LOCK_FRAMES = 2
        depth = _mid_obstacle(_depth_map(fill_m=2.5))
        _feed(belt, [_det(120, 240, 2.5)], depth, n=2)
        assert belt.get_status()['plan_locked'] is True

        for _ in range(BeltAdapter.TARGET_MISS_TOLERANCE + 1):
            belt.update(_ctx([]))  # sustained target loss → idle / unlock
        assert belt.get_status()['plan_locked'] is False
        assert belt.get_status()['avoid_side'] is None
