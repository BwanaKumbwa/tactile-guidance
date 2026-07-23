"""Handoff behaviour for belt approach (bracelet left at stock threshold)."""
from __future__ import annotations

from server.feedback_devices.adapters.belt_adapter import BeltAdapter
from server.feedback_devices.adapters.bracelet_adapter import BraceletAdapter
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
        target_class_id=39,
        hand_class_ids=[80, 81],
        depth_img=None,
        vibration_intensities={'belt': 60, 'left': 50, 'right': 50},
        metric=True,
        frame_shape=frame_shape,
    )


def _det(xc, yc, depth_m, cls=39):
    return [xc, yc, 40, 60, 1, cls, 0.9, depth_m]


def _hand(xc=120, yc=240):
    return [xc, yc, 50, 50, 2, 80, 0.9, 0.4]


class TestBeltBraceletHandoff:
    def test_far_only_belt_active(self):
        belt_hw, br_hw = _FakeBelt(), _FakeBelt()
        belt = BeltAdapter(belt_hw)
        bracelet = BraceletAdapter(br_hw)

        ctx = _ctx([_det(320, 240, 2.0)])
        belt.update(ctx)
        bracelet.update(ctx)

        assert belt.get_status()['in_approach'] is True
        assert belt_hw.commands
        assert bracelet.get_status()['is_navigating'] is False
        assert not br_hw.commands

    def test_belt_stops_at_handoff_enter(self):
        belt_hw = _FakeBelt()
        belt = BeltAdapter(belt_hw)

        belt.update(_ctx([_det(320, 240, 2.0)]))
        belt.update(_ctx([_det(320, 240, HANDOFF_ENTER_CM / 100.0)]))

        assert belt.get_status()['in_approach'] is False
        assert belt_hw.stopped >= 1

    def test_hysteresis_mid_band_keeps_belt_off(self):
        belt_hw = _FakeBelt()
        belt = BeltAdapter(belt_hw)

        belt.update(_ctx([_det(320, 240, 2.0)]))
        belt.update(_ctx([_det(320, 240, HANDOFF_ENTER_CM / 100.0)]))
        belt_hw.commands.clear()

        mid_m = (HANDOFF_ENTER_CM + HANDOFF_EXIT_CM) / 2 / 100.0
        belt.update(_ctx([_det(320, 240, mid_m)]))

        assert belt.get_status()['in_approach'] is False
        assert not belt_hw.commands

    def test_exit_band_returns_to_belt(self):
        belt_hw, br_hw = _FakeBelt(), _FakeBelt()
        belt = BeltAdapter(belt_hw)
        bracelet = BraceletAdapter(br_hw)

        belt.update(_ctx([_det(320, 240, 2.0)]))
        belt.update(_ctx([_det(320, 240, HANDOFF_ENTER_CM / 100.0)]))
        bracelet.update(_ctx([_det(320, 240, HANDOFF_ENTER_CM / 100.0), _hand()]))

        exit_m = HANDOFF_EXIT_CM / 100.0 + 0.05
        far_again = _ctx([_det(320, 240, exit_m)])
        belt.update(far_again)
        bracelet.update(far_again)

        assert belt.get_status()['in_approach'] is True
        assert belt_hw.commands
