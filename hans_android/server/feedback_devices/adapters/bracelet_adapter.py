from __future__ import annotations
import time
from typing import Optional
import math
from feedback_devices.base import FeedbackDevice, NavigationContext

class BraceletAdapter(FeedbackDevice):
    DISTANCE_THRESHOLD_CM = 70.0

    def __init__(self, virtual_belt_controller, vibration_intensities: dict = None):
        self._virtual_belt = virtual_belt_controller
        self._vib_intensities = vibration_intensities or {'left': 50, 'right': 50}
        self._connected = True
        self._is_close_target = False
        
        # ✅ Throttle trackers
        self._last_angle = None
        self._last_cmd_time = 0.0
        
        # Grasping & Freezing state
        self._frozen_target = None
        self._grasp_cooldown_end = 0.0

    def connect(self) -> bool:
        self._connected = True
        return True

    def disconnect(self) -> None:
        if self._virtual_belt:
            self._virtual_belt.stop_vibration()
        self._connected = False

    def update(self, ctx: NavigationContext) -> Optional[object]:
        if not self._connected:
            return None
            
        if time.time() < self._grasp_cooldown_end:
            return None
        
        hand_bbox = self._find_bbox(ctx.raw_detections, ctx.hand_class_ids) if ctx.raw_detections else None
        live_target = next((det for det in (ctx.raw_detections or []) if det[5] == ctx.target_class_id), None)
        
        # 1. Target Selection Logic: Lock onto frozen target if available to prevent occlusion drops
        if self._frozen_target is not None and hand_bbox is not None:
            active_target_bbox = self._frozen_target
            target_depth_cm = 50.0  # Force it to remain active if frozen
            viz_target = [active_target_bbox[0], active_target_bbox[1], active_target_bbox[2], active_target_bbox[3], -1, ctx.target_class_id, 1.0, -1.0]
        elif live_target is not None:
            # Extract to standard Python floats to avoid Numpy memory corruption
            active_target_bbox = [float(x) for x in live_target[:4]]
            target_depth_cm = float(live_target[7]) * 100.0 if len(live_target) > 7 else -1.0
            viz_target = live_target
        else:
            self._frozen_target = None
            if self._is_close_target:
                self._is_close_target = False
                self.stop()
            return None
        
        if target_depth_cm > 0 and target_depth_cm <= self.DISTANCE_THRESHOLD_CM:
            self._is_close_target = True
            
            if hand_bbox is not None:
                hx, hy, hw, hh = [float(x) for x in hand_bbox]
                tx, ty, tw, th = active_target_bbox
                
                hand_l, hand_r = hx - hw/2, hx + hw/2
                hand_t, hand_b = hy - hh/2, hy + hh/2
                tgt_l, tgt_r = tx - tw/2, tx + tw/2
                tgt_t, tgt_b = ty - th/2, ty + th/2
                
                # Check for any physical overlap between the boxes
                is_touched = (hand_r >= tgt_l and hand_l <= tgt_r and hand_b >= tgt_t and hand_t <= tgt_b)
                
                # Distance between the center points
                dist_centers = math.hypot(tx - hx, ty - hy)
                
                # 2. Grasp Condition: 
                # Center is inside OR (they are touching AND hand is reasonably close to the center)
                is_center_inside = (tgt_l <= hx <= tgt_r) and (tgt_t <= hy <= tgt_b)
                is_close_enough = dist_centers < (max(tw, th) * 0.8)  # 80% tolerance
                
                if is_center_inside or (is_touched and is_close_enough):
                    self._frozen_target = None
                    self._is_close_target = False
                    self.stop()
                    self.signal_event('grasped')
                    self._grasp_cooldown_end = time.time() + 2.0
                    return viz_target

                # 3. Freeze target on FIRST touch to preserve original un-shrunk dimensions
                if is_touched:
                    if self._frozen_target is None:
                        self._frozen_target = active_target_bbox
                else:
                    self._frozen_target = None # Release freeze if hand retreats
                
                # Navigate towards active target
                angle_deg = self._calculate_angle(hand_bbox, active_target_bbox)
                
                # 4. THROTTLE LOGIC
                now = time.time()
                angle_diff = 999
                if self._last_angle is not None:
                    angle_diff = abs(angle_deg - self._last_angle)
                    if angle_diff > 180: angle_diff = 360 - angle_diff
                    
                if (now - self._last_cmd_time > 0.5) or (angle_diff > 15):
                    self._send_navigation_command(angle_deg)
                    self._last_angle = angle_deg
                    self._last_cmd_time = now
                    
            return viz_target
        else:
            self._frozen_target = None
            if self._is_close_target:
                self._is_close_target = False
                self.stop()
            return viz_target

    def signal_event(self, event: str) -> None:
        patterns = {
            'grasped':       {'intensity': 50, 'orientation': 0b111100, 'duration_ms': 150, 'iters': 5},
            'obstacle':      {'intensity': 30, 'orientation': 0b101000, 'duration_ms': 100, 'iters': 5},
            'target_found':  {'intensity': 40, 'orientation': 0b010000, 'duration_ms': 100, 'iters': 3},
            'list_complete': {'intensity': 60, 'orientation': 0b111111, 'duration_ms': 150, 'iters': 8},
        }
        p = patterns.get(event)
        if p and self._virtual_belt:
            self._virtual_belt.send_pulse_command(
                channel_index=1, intensity=p['intensity'],
                orientation_type=0,  # BINARY_MASK
                orientation=p['orientation'], on_duration_ms=p['duration_ms'],
                pulse_period=300, pulse_iterations=p['iters'],
                series_period=5000, series_iterations=1,
            )

    def stop(self) -> None:
        if self._virtual_belt:
            self._virtual_belt.stop_vibration()

    def get_status(self) -> dict:
        return {'connected': self._connected, 'type': 'bracelet', 'battery': None, 'is_navigating': self._is_close_target}

    def _find_bbox(self, detections: list, class_ids: list) -> Optional[list]:
        for det in detections:
            if det[5] in class_ids:
                return det[:4]
        return None

    def _calculate_angle(self, hand_bbox: list, target_bbox: list) -> float:
        dx = target_bbox[0] - hand_bbox[0]
        dy = target_bbox[1] - hand_bbox[1]
        return math.degrees(math.atan2(dy, dx)) % 360

    def _send_navigation_command(self, angle_deg: float) -> None:
        if self._virtual_belt:
            self._virtual_belt.send_vibration_command(
                channel_index=1, pattern=0, intensity=50,
                orientation_type=2,  # ANGLE
                orientation=int(angle_deg),
            )