"""Unit tests for belt approach guidance and minimal nav cues (no hardware)."""
from __future__ import annotations

import time

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
