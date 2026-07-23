"""Unit tests for belt approach guidance (no hardware required)."""
from __future__ import annotations

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


def _ctx(detections, frame_shape=(480, 640)):
    return NavigationContext(
        raw_detections=detections,
        target_class_id=39,  # bottle
        hand_class_ids=[80, 81],
        depth_img=None,
        vibration_intensities={'belt': 60},
        metric=True,
        frame_shape=frame_shape,
    )


def _det(xc, yc, depth_m, cls=39):
    return [xc, yc, 40, 60, 1, cls, 0.9, depth_m]


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
