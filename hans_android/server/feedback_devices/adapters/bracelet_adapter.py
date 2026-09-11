from __future__ import annotations

import asyncio
import math
import threading
import time
from typing import Optional

from feedback_devices.base import FeedbackDevice, NavigationContext
from feedback_devices.adapters.bracelet_protocol import (
    build_orientation_command,
    build_set_intensity_command,
    build_set_mode_command,
    send_command,
)

from pybracelet import PATTERN_SINGLE, MODE_APPLICATION

class BraceletAdapter(FeedbackDevice):
    DISTANCE_THRESHOLD_CM = 70.0

    def __init__(self, virtual_belt_controller=None, vibration_intensities: dict = None):
        # Tetap menerima parameter ini supaya server_main.py
        # tidak perlu diubah.
        self._virtual_belt = virtual_belt_controller

        self._vib_intensities = vibration_intensities or {
            'left': 50,
            'right': 50,
        }

        self._connected = False
        self._is_close_target = False

        # pybracelet / asyncio
        self._controller = None
        self._loop = None
        self._thread = None

        # Untuk menunggu hasil koneksi
        self._connect_result = False
        self._connect_done = threading.Event()

        # Throttle trackers
        self._last_angle = None
        self._last_cmd_time = 0.0

        # Grasping & Freezing state
        self._frozen_target = None
        self._grasp_cooldown_end = 0.0

    # ============================================================
    # CONNECTION
    # ============================================================

    def connect(self) -> bool:
        # Jika ada VirtualBeltController, bracelet dikontrol
        # melalui HP/WebSocket. Jangan mengambil koneksi BLE
        # dari HP dengan pybracelet.
        if self._virtual_belt is not None:
            self._connected = True
            self._connect_result = True

            print(
                "[BraceletAdapter] "
                "Virtual mode: BLE connection handled by phone."
            )

            return True

        # Mode direct BLE:
        # dipakai jika BraceletAdapter dibuat tanpa
        # VirtualBeltController.
        from pybracelet import BraceletController

        self._connect_result = False
        self._connect_done.clear()

        def run_ble_loop():
            try:
                self._loop = asyncio.new_event_loop()
                asyncio.set_event_loop(self._loop)

                self._controller = BraceletController(loop=self._loop)

                self._connect_result = self._loop.run_until_complete(
                    self._controller.scan_and_connect()
                )

                self._connected = self._connect_result

                print(
                    f"[BraceletAdapter] "
                    f"pybracelet connected: {self._connected}"
                )

                self._connect_done.set()

                if self._connected:
                    self._loop.run_forever()

            except Exception as e:
                print(f"[BraceletAdapter] Connection error: {e}")
                self._connected = False
                self._connect_result = False
                self._connect_done.set()

            finally:
                if self._loop is not None:
                    try:
                        self._loop.close()
                    except Exception:
                        pass

        self._thread = threading.Thread(
            target=run_ble_loop,
            daemon=True,
            name="pybracelet-loop",
        )

        self._thread.start()

        self._connect_done.wait(timeout=10.0)

        return self._connect_result

    def disconnect(self) -> None:
        if self._controller is not None and self._loop is not None:
            try:
                future = asyncio.run_coroutine_threadsafe(
                    self._controller.disconnect(),
                    self._loop,
                )

                future.result(timeout=3.0)

            except Exception as e:
                print(f"[BraceletAdapter] Disconnect error: {e}")

            try:
                self._loop.call_soon_threadsafe(
                    self._loop.stop
                )
            except Exception:
                pass

        self._connected = False
        self._controller = None
        self._loop = None

    # ============================================================
    # NAVIGATION
    # ============================================================

    def update(self, ctx: NavigationContext) -> Optional[object]:
        if not self._connected:
            return None

        if time.time() < self._grasp_cooldown_end:
            return None

        hand_bbox = (
            self._find_bbox(
                ctx.raw_detections,
                ctx.hand_class_ids
            )
            if ctx.raw_detections
            else None
        )

        live_target = next(
            (
                det
                for det in (ctx.raw_detections or [])
                if det[5] == ctx.target_class_id
            ),
            None,
        )

        # 1. Target Selection Logic
        if self._frozen_target is not None and hand_bbox is not None:
            active_target_bbox = self._frozen_target
            target_depth_cm = 50.0

            viz_target = [
                active_target_bbox[0],
                active_target_bbox[1],
                active_target_bbox[2],
                active_target_bbox[3],
                -1,
                ctx.target_class_id,
                1.0,
                -1.0,
            ]

        elif live_target is not None:
            active_target_bbox = [
                float(x)
                for x in live_target[:4]
            ]

            target_depth_cm = (
                float(live_target[7]) * 100.0
                if len(live_target) > 7
                else -1.0
            )

            viz_target = live_target

        else:
            self._frozen_target = None

            if self._is_close_target:
                self._is_close_target = False
                self.stop()

            return None

        if (
            target_depth_cm > 0
            and target_depth_cm <= self.DISTANCE_THRESHOLD_CM
        ):
            self._is_close_target = True

            if hand_bbox is not None:
                hx, hy, hw, hh = [
                    float(x)
                    for x in hand_bbox
                ]

                tx, ty, tw, th = active_target_bbox

                hand_l = hx - hw / 2
                hand_r = hx + hw / 2
                hand_t = hy - hh / 2
                hand_b = hy + hh / 2

                tgt_l = tx - tw / 2
                tgt_r = tx + tw / 2
                tgt_t = ty - th / 2
                tgt_b = ty + th / 2

                is_touched = (
                    hand_r >= tgt_l
                    and hand_l <= tgt_r
                    and hand_b >= tgt_t
                    and hand_t <= tgt_b
                )

                dist_centers = math.hypot(
                    tx - hx,
                    ty - hy,
                )

                is_center_inside = (
                    tgt_l <= hx <= tgt_r
                    and tgt_t <= hy <= tgt_b
                )

                is_close_enough = (
                    dist_centers < max(tw, th) * 0.8
                )

                if is_center_inside or (
                    is_touched and is_close_enough
                ):
                    self._frozen_target = None
                    self._is_close_target = False

                    self.stop()
                    self.signal_event("grasped")

                    self._grasp_cooldown_end = (
                        time.time() + 2.0
                    )

                    return viz_target

                # Freeze target on first touch
                if is_touched:
                    if self._frozen_target is None:
                        self._frozen_target = active_target_bbox
                else:
                    self._frozen_target = None

                # Navigation angle
                angle_deg = self._calculate_angle(
                    hand_bbox,
                    active_target_bbox,
                )

                # Throttle
                now = time.time()
                angle_diff = 999

                if self._last_angle is not None:
                    angle_diff = abs(
                        angle_deg - self._last_angle
                    )

                    if angle_diff > 180:
                        angle_diff = 360 - angle_diff

                if (
                    now - self._last_cmd_time > 0.5
                    or angle_diff > 15
                ):
                    self._send_navigation_command(
                        angle_deg
                    )

                    self._last_angle = angle_deg
                    self._last_cmd_time = now

            return viz_target

        else:
            self._frozen_target = None

            if self._is_close_target:
                self._is_close_target = False
                self.stop()

            return viz_target

    # ============================================================
    # EVENTS
    # ============================================================

    def signal_event(self, event: str) -> None:
        if (
            self._controller is None
            or self._loop is None
            or not self._connected
        ):
            return

        from pybracelet import (MOTOR_LEFT, MOTOR_DOWN, MOTOR_RIGHT, MOTOR_TOP_FRONT, MOTOR_TOP, MOTOR_TOP_BACK)

        patterns = {
            'grasped': {
                'motors': [MOTOR_RIGHT, MOTOR_LEFT, MOTOR_DOWN],
                'intensity': 50,
                'duration_ms': 150,
                'iters': 5,
            },
            'obstacle': {
                'motors': [MOTOR_DOWN, MOTOR_TOP],
                'intensity': 30,
                'duration_ms': 100,
                'iters': 5,
            },
            'target_found': {
                'motors': [MOTOR_TOP],
                'intensity': 40,
                'duration_ms': 100,
                'iters': 3,
            },
            'list_complete': {
                'motors': [MOTOR_TOP_FRONT, MOTOR_TOP, MOTOR_TOP_BACK],
                'intensity': 60,
                'duration_ms': 150,
                'iters': 8,
            },
        }

        p = patterns.get(event)
        if p is None:
            return

        intensities = [5, 5, 5, 5, 5, 5]

        for motor in p['motors']:
            intensities[motor] = p['intensity']

        try:
            future = asyncio.run_coroutine_threadsafe(
                self._controller.set_intensity(intensities),
                self._loop,
            )
            future.result(timeout=2.0)

            # Protocol bracelet hanya menerima 3 motor per command.
            # Jadi motor dibagi menjadi beberapa grup.
            motor_groups = [
                p['motors'][i:i + 3]
                for i in range(0, len(p['motors']), 3)
            ]

            for _ in range(p['iters']):
                for group in motor_groups:
                    motors = group + [group[-1]] * (3 - len(group))

                    future = asyncio.run_coroutine_threadsafe(
                        self._controller.start_vibration_at_position(
                            channel=0,
                            first_motor=motors[0],
                            second_motor=motors[1],
                            third_motor=motors[2],
                            on_duration=p['duration_ms'],
                            period=p['duration_ms'] * 2,
                            delay=0,
                            reset=True,
                        ),
                        self._loop,
                    )
                    future.result(timeout=2.0)

                time.sleep(p['duration_ms'] / 1000.0)

            print(
                f"[BraceletAdapter] Event: {event}, "
                f"motors={p['motors']}, "
                f"intensity={p['intensity']}, "
                f"iters={p['iters']}"
            )

        except Exception as e:
            print(
                f"[BraceletAdapter] Event vibration error: {e}"
            )

    def stop(self) -> None:
        if (
            self._controller is not None
            and self._loop is not None
            and self._connected
        ):
            try:
                future = asyncio.run_coroutine_threadsafe(
                    self._controller.stop_vibration(),
                    self._loop,
                )

                future.result(timeout=2.0)

            except Exception as e:
                print(
                    f"[BraceletAdapter] "
                    f"Stop vibration error: {e}"
                )

    # ============================================================
    # STATUS
    # ============================================================

    def get_status(self) -> dict:
        return {
            'connected': self._connected,
            'type': 'bracelet',
            'battery': None,
            'is_navigating': self._is_close_target,
        }

    # ============================================================
    # HELPERS
    # ============================================================

    def _find_bbox(
        self,
        detections: list,
        class_ids: list,
    ) -> Optional[list]:

        for det in detections:
            if det[5] in class_ids:
                return det[:4]

        return None

    def _calculate_angle(
        self,
        hand_bbox: list,
        target_bbox: list,
    ) -> float:

        dx = target_bbox[0] - hand_bbox[0]
        dy = target_bbox[1] - hand_bbox[1]

        return (90 - math.degrees(math.atan2(dy, dx))) % 360

    def _send_navigation_command(
        self,
        angle_deg: float,
    ) -> None:

        from pybracelet import (MOTOR_LEFT, MOTOR_DOWN, MOTOR_RIGHT, MOTOR_TOP)

        angle = angle_deg % 360
        bracelet_roll = (180 - angle) % 360

        if 315 <= bracelet_roll or bracelet_roll < 45:
            motor = MOTOR_TOP
            direction = "TOP"

        elif 45 <= bracelet_roll < 135:
            motor = MOTOR_RIGHT
            direction = "RIGHT"

        elif 135 <= bracelet_roll < 225:
            motor = MOTOR_DOWN
            direction = "DOWN"

        else:
            motor = MOTOR_LEFT
            direction = "LEFT"

        try:
            # ========================================================
            # VIRTUAL MODE:
            # Server -> WebSocket -> HP -> Bracelet
            # ========================================================
            if self._virtual_belt is not None:
                mode = build_set_mode_command(MODE_APPLICATION)
                mode_ok = send_command(self._virtual_belt.result_queue, "bracelet", mode)
                
                intensities = build_set_intensity_command([
                    50, 50, 50, 50, 50, 50
                ])

                orientation = build_orientation_command(
                    channel=0,
                    pattern=PATTERN_SINGLE,
                    roll=int(bracelet_roll),
                    on_duration=300,
                    period=600,
                    delay=0,
                    reset=True,
                )

                intensity_ok = send_command(
                    self._virtual_belt.result_queue,
                    "bracelet",
                    intensities,
                )

                vibration_ok = send_command(
                    self._virtual_belt.result_queue,
                    "bracelet",
                    orientation,
                )

                print(
                    "[BraceletAdapter] "
                    f"Navigation: {direction} "
                    f"({angle:.1f}°), "
                    f"intensity_sent={intensity_ok}, "
                    f"vibration_sent={vibration_ok}"
                )

                return

            # ========================================================
            # DIRECT BLE MODE:
            # PC -> pybracelet -> Bracelet
            # ========================================================
            if (
                self._controller is None
                or self._loop is None
                or not self._connected
            ):
                return

            future = asyncio.run_coroutine_threadsafe(
                self._controller.set_intensity([
                    50,
                    50,
                    50,
                    50,
                    50,
                    50,
                ]),
                self._loop,
            )

            future.result(timeout=2.0)

            future = asyncio.run_coroutine_threadsafe(
                self._controller.start_vibration_at_position(
                    channel=0,
                    first_motor=motor,
                    second_motor=motor,
                    third_motor=motor,
                    on_duration=300,
                    period=600,
                    delay=0,
                    reset=True,
                ),
                self._loop,
            )

            success = future.result(timeout=2.0)

            print(
                "[BraceletAdapter] "
                f"Navigation: {direction} "
                f"({angle:.1f}°), success={success}"
            )

        except Exception as e:
            print(
                "[BraceletAdapter] "
                f"Navigation vibration error: {e}"
            )

