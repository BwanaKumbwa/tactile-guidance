"""Unit tests for belt approach guidance and avoidance (no hardware required)."""
from __future__ import annotations

import numpy as np

from server.feedback_devices.adapters.belt_adapter import BeltAdapter
from server.feedback_devices.base import NavigationContext
from server.feedback_devices.handoff import HANDOFF_ENTER_CM, HANDOFF_EXIT_CM


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


def _mid_obstacle(depth, depth_m=0.7):
    y0 = int(480 * 0.25)
    y1 = int(480 * 0.50)
    depth[y0:y1, 280:360] = depth_m
    return depth


class TestBeltApproach:
    def test_vibrates_when_far_and_centered(self):
        fake = _FakeBelt()
        belt = BeltAdapter(fake)
        belt.update(_ctx([_det(320, 240, 2.0)]))
        assert fake.commands, 'expected a vibration command'
        cmd = fake.commands[-1]
        assert cmd['orientation_type'] == 0
        assert cmd['orientation'] == 0
        assert belt.get_status()['in_approach'] is True

    def test_steers_right_when_target_on_right(self):
        fake = _FakeBelt()
        belt = BeltAdapter(fake)
        belt.update(_ctx([_det(560, 240, 1.5)]))
        assert fake.commands
        angle = fake.commands[-1]['orientation']
        assert 60 <= angle <= 90

    def test_steers_left_when_target_on_left(self):
        fake = _FakeBelt()
        belt = BeltAdapter(fake)
        belt.update(_ctx([_det(80, 240, 1.5)]))
        assert fake.commands
        angle = fake.commands[-1]['orientation']
        assert angle >= 270 or angle == 0

    def test_stops_at_handoff_and_pulses(self):
        fake = _FakeBelt()
        belt = BeltAdapter(fake)
        belt.update(_ctx([_det(320, 240, 2.0)]))
        assert belt.get_status()['in_approach'] is True
        n_cmd = len(fake.commands)

        belt.update(_ctx([_det(320, 240, HANDOFF_ENTER_CM / 100.0)]))
        assert belt.get_status()['in_approach'] is False
        assert fake.stopped >= 1
        assert fake.pulses
        assert len(fake.commands) == n_cmd

    def test_hysteresis_keeps_belt_off_until_exit(self):
        fake = _FakeBelt()
        belt = BeltAdapter(fake)
        belt.update(_ctx([_det(320, 240, 2.0)]))
        belt.update(_ctx([_det(320, 240, HANDOFF_ENTER_CM / 100.0)]))
        fake.commands.clear()

        mid = (HANDOFF_ENTER_CM + HANDOFF_EXIT_CM) / 2 / 100.0
        belt.update(_ctx([_det(320, 240, mid)]))
        assert belt.get_status()['in_approach'] is False
        assert not fake.commands

        belt.update(_ctx([_det(320, 240, HANDOFF_EXIT_CM / 100.0 + 0.05)]))
        assert belt.get_status()['in_approach'] is True
        assert fake.commands

    def test_unknown_depth_still_guides_by_bearing(self):
        fake = _FakeBelt()
        belt = BeltAdapter(fake)
        belt.update(_ctx([_det(500, 240, -1.0)]))
        assert fake.commands
        assert belt.get_status()['in_approach'] is True


class TestBeltAvoidance:
    def test_enters_avoidance_without_obstacle_pulse(self):
        fake = _FakeBelt()
        belt = BeltAdapter(fake)
        depth = _mid_obstacle(_depth_map(fill_m=2.0))

        belt.update(_ctx([_det(320, 240, 2.0)], depth_img=depth))
        assert belt.get_status()['avoidance_active'] is True
        assert not any(p.get('orientation') == 0b101000 for p in fake.pulses)
        assert fake.commands  # continuous steering to waypoint

    def test_steers_left_when_target_is_on_left(self):
        fake = _FakeBelt()
        belt = BeltAdapter(fake)
        depth = _mid_obstacle(_depth_map(fill_m=2.0))

        # Target on the left of the FOV → pass left of obstacle
        belt.update(_ctx([_det(120, 240, 2.0)], depth_img=depth))
        status = belt.get_status()
        assert status['avoidance_active'] is True
        assert status['avoid_side'] == 'left'
        angle = fake.commands[-1]['orientation']
        assert angle >= 270 or angle == 0

    def test_steers_right_when_target_is_on_right(self):
        fake = _FakeBelt()
        belt = BeltAdapter(fake)
        depth = _mid_obstacle(_depth_map(fill_m=2.0))

        belt.update(_ctx([_det(520, 240, 2.0)], depth_img=depth))
        status = belt.get_status()
        assert status['avoidance_active'] is True
        assert status['avoid_side'] == 'right'
        angle = fake.commands[-1]['orientation']
        # Waypoint is only ~0.2 m past the obstacle → moderate right bearing
        assert 0 < angle <= 90

    def test_locks_side_until_obstacle_cleared(self):
        fake = _FakeBelt()
        belt = BeltAdapter(fake)
        depth = _mid_obstacle(_depth_map(fill_m=2.0))

        belt.update(_ctx([_det(120, 240, 2.0)], depth_img=depth))
        assert belt.get_status()['avoid_side'] == 'left'

        # Target jumps right while still blocked — side stays locked
        belt.update(_ctx([_det(520, 240, 2.0)], depth_img=depth))
        assert belt.get_status()['avoid_side'] == 'left'

    def test_exits_avoidance_after_corridor_clear(self):
        fake = _FakeBelt()
        belt = BeltAdapter(fake)
        blocked = _mid_obstacle(_depth_map(fill_m=2.0))
        clear = _depth_map(fill_m=2.0)

        belt.update(_ctx([_det(520, 240, 2.0)], depth_img=blocked))
        assert belt.get_status()['avoidance_active'] is True

        # Brief clears should NOT exit yet
        for _ in range(5):
            belt.update(_ctx([_det(520, 240, 2.0)], depth_img=clear))
        assert belt.get_status()['avoidance_active'] is True

        for _ in range(BeltAdapter.CLEAR_FRAMES_TO_EXIT):
            belt.update(_ctx([_det(520, 240, 2.0)], depth_img=clear))

        assert belt.get_status()['avoidance_active'] is False
        assert belt.get_status()['avoid_side'] is None

    def test_keeps_waypoint_while_clearing(self):
        fake = _FakeBelt()
        belt = BeltAdapter(fake)
        blocked = _mid_obstacle(_depth_map(fill_m=2.0))
        clear = _depth_map(fill_m=2.0)

        belt.update(_ctx([_det(520, 240, 2.0)], depth_img=blocked))
        belt.update(_ctx([_det(520, 240, 2.0)], depth_img=clear))
        viz = belt.get_debug_viz()
        assert belt.get_status()['avoidance_active'] is True
        assert viz.get('phase') == 'avoid_B_clearing'
        # Still aiming at the side waypoint, not the target center
        assert viz.get('steer_x') != 520
        assert 0 < viz.get('angle_deg', 0) <= 90

    def test_target_dropout_does_not_reset_avoidance(self):
        fake = _FakeBelt()
        belt = BeltAdapter(fake)
        blocked = _mid_obstacle(_depth_map(fill_m=2.0))

        belt.update(_ctx([_det(520, 240, 2.0)], depth_img=blocked))
        assert belt.get_status()['avoidance_active'] is True

        # Bottle briefly missing from detections (occlusion)
        belt.update(_ctx([], depth_img=blocked))
        assert belt.get_status()['avoidance_active'] is True
        assert belt.get_debug_viz().get('phase') == 'avoid_A_hold'

    def test_floor_band_does_not_start_avoidance(self):
        fake = _FakeBelt()
        belt = BeltAdapter(fake)
        depth = _depth_map(fill_m=2.0)
        depth[360:480, 200:440] = 0.6

        belt.update(_ctx([_det(320, 240, 2.0)], depth_img=depth))
        assert belt.get_status()['avoidance_active'] is False

    def test_no_avoidance_after_handoff(self):
        fake = _FakeBelt()
        belt = BeltAdapter(fake)
        depth = _mid_obstacle(_depth_map(fill_m=0.4), depth_m=0.2)

        belt.update(_ctx([_det(320, 240, 2.0)], depth_img=_depth_map(fill_m=2.0)))
        belt.update(_ctx([_det(320, 240, 0.4)], depth_img=depth))
        assert belt.get_status()['in_approach'] is False
        assert belt.get_status()['avoidance_active'] is False
