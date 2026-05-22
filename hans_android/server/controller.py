import queue
import sys
import math
from pathlib import Path
import os
import time
from datetime import datetime
import threading
import json

import cv2
import torch
import numpy as np
import pandas as pd
from playsound import playsound

file = Path(__file__).resolve()
root = file.parents[0]
for path in ['/yolov5', '/strongsort', '/unidepth', '/midas']:
    if str(root) + path not in sys.path:
        sys.path.append(str(root) + path)

from labels import coco_labels
from yolov5.models.common import DetectMultiBackend
from yolov5.utils.dataloaders import IMG_FORMATS, VID_FORMATS, LoadImages, LoadStreams
from yolov5.utils.general import (
    Profile, check_file, check_img_size, check_imshow,
    increment_path, non_max_suppression,
    scale_boxes, strip_optimizer, xyxy2xywh, xywh2xyxy
)
from yolov5.utils.plots import Annotator, colors
from yolov5.utils.torch_utils import select_device, smart_inference_mode
from strongsort.strong_sort import StrongSORT

# Module-level utilities

def beginning_sound():
    playsound('resources/sound/beginning.mp3')

def play_start():
    threading.Thread(target=beginning_sound, daemon=True).start()


def bbs_to_depth(image, depth=None, bbs=None):
    """Assign depth values from a depth map to bounding box entries."""
    if bbs is not None and depth is not None:
        outputs = []
        for bb in bbs:
            if bb[7] == -1:
                x_c, y_c, w, h = [int(c) for c in bb[:4]]
                sw, sh = int(w * 0.25), int(h * 0.25)
                x1 = max(0, x_c - sw)
                y1 = max(0, y_c - sh)
                x2 = min(depth.shape[1], x_c + sw)
                y2 = min(depth.shape[0], y_c + sh)
                roi = depth[y1:y2, x1:x2]
                valid = roi[roi > 0]
                bb[7] = float(np.percentile(valid, 15)) if valid.size > 0 else -1.0
            outputs.append(bb)
        return np.array(outputs)
    return bbs


def close_app(controller):
    if controller:
        controller.stop_vibration()
    cv2.destroyAllWindows()
    for t in threading.enumerate():
        t._tstate_lock = None
        t._stop()
    if controller:
        controller.disconnect_belt()
    print("Application closed.")
    sys.exit()


# AutoAssign base

class AutoAssign:
    def __init__(self, mcp_queue=None, shared_state=None, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)
        self.mcp_queue = mcp_queue
        self.shared_state = shared_state


# TaskController

class TaskController(AutoAssign):

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        # Haptic / navigation state (instance vars so command handlers can reset them)
        self.grasped: bool = False
        self.vibration_timer = None
        self.specific_track_id: int = -1
        self.specific_bbox = None

        # Depth state shared across frames
        self.depth_img = None
        self.last_depth_estimate_time: float = 0.0   # Wall-clock throttle

        # Tracking state shared across frames
        self.prev_frames = None
        self.curr_frames = None
        self.prev_outputs = np.array([])
        self.fpss: list = []

        # Trial / experiment state
        self.obj_index: int = 0
        self.ready_for_next_trial: bool = True
        self.target_entered: bool = True
        self.class_target_obj: int = -1
        self.orig_classes_obj: list = []
        self.trial_start_time = 'NA'
        self.trial_end_time = 'NA'
        self.pressed_key: int = -1

        # Memory / IO
        self.memory: dict = {}
        self.memory_file_path: str = ''
        self.log_file_path: str = ''
        self.last_memory_save_time: float = 0.0

        # Built lazily in experiment_loop
        self._command_table: dict = {}

    # State publishers

    def _publish_target(self, name: str) -> None:
        if self.shared_state is not None:
            self.shared_state.set_target(name)

    def _publish_visible_objects(self, outputs, img_shape=(640, 640)) -> None:
        if self.shared_state is None:
            return
        img_h, img_w = img_shape[:2]
        now_str = datetime.now().strftime("%I:%M:%S %p")
        visible, new_map = [], {}

        for item in outputs:
            cls = int(item[5])
            if not (hasattr(self, 'master_label') and cls in self.master_label):
                continue
            name = self.master_label[cls]
            conf = float(item[6])
            track_id = int(item[4])
            depth = float(item[7]) if len(item) > 7 else -1.0
            x_norm = float(item[0]) / img_w
            y_norm = float(item[1]) / img_h
            x_str = "Left" if x_norm < 0.33 else ("Right" if x_norm > 0.66 else "Center")
            y_str = "Top" if y_norm < 0.33 else ("Bottom" if y_norm > 0.66 else "Middle")
            depth_str = f"{depth:.2f} meters away" if depth > 0 else "Unknown depth"
            location_str = f"{y_str} {x_str}, {depth_str}"

            visible.append({
                "name": name, "confidence": conf, "track_id": track_id,
                "depth": depth, "location": location_str,
                "bbox": [float(item[0]), float(item[1]), float(item[2]), float(item[3])]
            })
            if conf > 0.60:
                new_map[name] = {"location": location_str, "last_seen": now_str}

        self.shared_state.set_visible_objects(visible)
        if new_map:
            self.shared_state.update_world_map(new_map)
            self.memory.setdefault("world_map", {}).update(new_map)

    def _publish_available_classes(self) -> None:
        if self.shared_state is not None:
            self.shared_state.set_available_classes(list(coco_labels.values()))

    # Engine reset

    def _full_engine_reset(self) -> None:
        """Reset all navigation state before switching to a new target."""
        self.specific_track_id = -1
        self.specific_bbox = None
        self.grasped = False
        self.vibration_timer = None
        bc = getattr(self, 'bracelet_controller', None)
        if bc:
            bc.prev_target = None
            bc.frozen = False

    # MCP command dispatch table
    # Commands that require a full engine reset before their handler runs
    _RESET_INSTRUCTIONS = frozenset({
        "set_target", "set_target_list", "mark_grasped",
        "clear_list", "set_specific_target"
    })

    def _build_command_table(self) -> dict:
        return {
            "stop":                self._cmd_stop,
            "set_target":          self._cmd_set_target,
            "pause_navigation":    self._cmd_pause_navigation,
            "resume_navigation":   self._cmd_resume_navigation,
            "adjust_intensity":    self._cmd_adjust_intensity,
            "set_target_list":     self._cmd_set_target_list,
            "mark_grasped":        self._cmd_mark_grasped,
            "log_interaction":     self._cmd_log_interaction,
            "update_preferences":  self._cmd_update_preferences,
            "clear_list":          self._cmd_clear_list,
            "set_specific_target": self._cmd_set_specific_target,
        }

    def _dispatch_command(self, instruction: str, value: str) -> None:
        if instruction in self._RESET_INSTRUCTIONS:
            self._full_engine_reset()
        handler = self._command_table.get(instruction)
        if handler:
            handler(value)
        else:
            print(f"[System] Unknown instruction: {instruction}", file=sys.stderr)

    # Individual command handlers

    def _cmd_stop(self, _):
        print("[System] Stop command received.", file=sys.stderr)
        raise StopIteration

    def _cmd_set_target(self, value: str):
        if value not in coco_labels.values():
            print(f"[System] '{value}' is not a valid COCO label.", file=sys.stderr)
            return
        new_id = next(k for k, v in coco_labels.items() if v == value)
        self.class_target_obj = new_id
        self.classes_obj = [new_id]
        self.target_entered = True
        self.ready_for_next_trial = False
        self.trial_start_time = time.time()
        bc = getattr(self, 'bracelet_controller', None)
        if bc:
            bc.vibrate = True
            bc.frozen = False
            bc.was_guiding = False
            bc.searching = True
            bc.prev_target = None
            bc.prev_hand = None
        self._publish_target(value)
        print(f"[System] Target → {value} (ID {new_id})", file=sys.stderr)

    def _cmd_pause_navigation(self, _):
        self.bracelet_controller.vibrate = False
        if self.belt_controller:
            self.belt_controller.stop_vibration()
        print("[System] Navigation paused.", file=sys.stderr)

    def _cmd_resume_navigation(self, _):
        self.bracelet_controller.vibrate = True
        print("[System] Navigation resumed.", file=sys.stderr)

    def _cmd_adjust_intensity(self, value: str):
        motor, intensity = value.split(":")
        intensity = int(intensity)
        self.participant_vibration_intensities[motor] = intensity
        self.memory["calibration"][motor] = intensity
        self._save_memory_async()
        print(f"[System] {motor} intensity → {intensity}", file=sys.stderr)

    def _cmd_set_target_list(self, value: str):
        data = json.loads(value)
        self.memory["target_list"] = data["targets"]
        self.memory["list_mode"] = data["mode"]
        self._save_memory_async()
        if self.shared_state:
            self.shared_state.set_target_list_state(data["targets"], data["mode"])
        if data["mode"] == "ordered" and data["targets"]:
            first = data["targets"][0]
            if first in coco_labels.values():
                self.class_target_obj = next(k for k, v in coco_labels.items() if v == first)
                self.classes_obj = [self.class_target_obj]
                self._publish_target(first)
                print(f"[System] Ordered list started. First target: {first}")
        else:
            self.class_target_obj = -1
            self._publish_target("none")
            print("[System] Unordered list started.")

    def _cmd_mark_grasped(self, _):
        curr = self.shared_state.get_target() if self.shared_state else "none"
        if curr == "none":
            return
        if curr not in self.memory["grasped_objects"]:
            self.memory["grasped_objects"].append(curr)
        if curr in self.memory["target_list"]:
            self.memory["target_list"].remove(curr)
        self._save_memory_async()
        if self.shared_state:
            self.shared_state.set_target_list_state(
                self.memory["target_list"], self.memory["list_mode"])

        if not self.memory["target_list"]:
            print("[System] List complete. Going idle.")
            self.class_target_obj = -1
            self._publish_target("none")
            if self.belt_controller:
                self.belt_controller.stop_vibration()
        elif self.memory["list_mode"] == "ordered":
            nxt = self.memory["target_list"][0]
            self.class_target_obj = next(k for k, v in coco_labels.items() if v == nxt)
            self.classes_obj = [self.class_target_obj]
            self._publish_target(nxt)
            print(f"[System] Next ordered target: {nxt}")
        else:
            self.class_target_obj = -1
            self._publish_target("none")
            if self.belt_controller:
                self.belt_controller.stop_vibration()

    def _cmd_log_interaction(self, value: str):
        data = json.loads(value)
        entry = {
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "user_command": data.get("user_text", ""),
            "ai_response": data.get("ai_response", "")
        }
        path = self.log_file_path
        def _write():
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
        threading.Thread(target=_write, daemon=True).start()

    def _cmd_update_preferences(self, value: str):
        data = json.loads(value)
        prefs = self.memory.setdefault("preferences", {})
        for key in ("speech_speed", "verbosity", "battery_saver", "play_welcome_message"):
            if key in data:
                prefs[key] = data[key]
        self._save_memory_async()
        if self.shared_state:
            self.shared_state.set_preferences(prefs)
        print(f"[System] Preferences updated: {prefs}")

    def _cmd_clear_list(self, _):
        self.memory["target_list"] = []
        self._save_memory_async()
        if self.shared_state:
            self.shared_state.set_target_list_state([], "ordered")
        self.class_target_obj = -1
        self._publish_target("none")
        if self.belt_controller:
            self.belt_controller.stop_vibration()
        print("[System] Target list cleared.")

    def _cmd_set_specific_target(self, value: str):
        data = json.loads(value)
        cls_name = data["class_name"]
        self.specific_track_id = data["track_id"]
        self.specific_bbox = data.get("bbox", None)
        self.class_target_obj = next(k for k, v in coco_labels.items() if v == cls_name)
        self.classes_obj = [self.class_target_obj]
        label = f"{data.get('description', '')} {cls_name}".strip()
        self._publish_target(label)
        print(f"[System] Locked onto {cls_name} (track_id={self.specific_track_id})")

    # Pipeline stage 1 — MCP command processing

    def _process_mcp_commands(self) -> bool:
        """
        Drain the MCP queue and dispatch all pending commands.
        Returns False if the loop should stop (stop instruction received).
        Drains ALL queued commands per frame (prevents backlog buildup).
        """
        if not (hasattr(self, 'mcp_queue') and self.mcp_queue):
            return True
        try:
            for _ in range(16): # Safety cap: max 16 commands per frame
                cmd = self.mcp_queue.get_nowait()
                print(f"\n[System] CMD: {cmd}", file=sys.stderr, flush=True)
                try:
                    self._dispatch_command(cmd.get("instruction", ""), cmd.get("value", ""))
                except StopIteration:
                    return False
        except queue.Empty:
            pass
        except Exception as e:
            print(f"[System] Command error: {e}", file=sys.stderr)
        return True

    # Pipeline stage 2 — YOLO inference

    def _run_inference(self, im):
        """Returns (pred_target, pred_hand) after NMS."""
        with self.dt[0]:
            image = torch.from_numpy(im).to(self.model_obj.device)
            image = image.half() if self.model_hand.fp16 else image.float()
            image /= 255
            if len(image.shape) == 3:
                image = image[None]
        with self.dt[1]:
            pred_target = self.model_obj(image, augment=self.augment, visualize=False)
            pred_hand   = self.model_hand(image, augment=self.augment, visualize=False)
        with self.dt[2]:
            pred_target = non_max_suppression(
                pred_target, self.conf_thres, self.iou_thres,
                self.classes_obj, self.agnostic_nms, max_det=self.max_det)
            pred_hand = non_max_suppression(
                pred_hand, self.conf_thres, self.iou_thres,
                self.classes_hand, self.agnostic_nms, max_det=self.max_det)
        return pred_target, pred_hand

    # Pipeline stage 3 — Tracking + output normalisation

    def _run_tracking(self, pred_target, pred_hand, im, im0, index_add) -> list:
        """
        Applies StrongSORT (or identity when tracker is off), merges predictions,
        runs camera-motion compensation, and returns a normalised output list
        where every entry is [xc, yc, w, h, track_id, cls, conf, depth=-1].
        """
        # Shift hand class IDs to avoid collision with object IDs
        for hand in pred_hand[0]:
            if len(hand):
                hand[5] += index_add

        self.curr_frames = im0

        preds = torch.cat((pred_target[0], pred_hand[0]), dim=0)
        if len(preds) > 0:
            preds[:, :4] = scale_boxes(im.shape[2:], preds[:, :4], im0.shape).round()
            xywhs = xyxy2xywh(preds[:, :4])
            confs  = preds[:, 4]
            clss   = preds[:, 5]
        else:
            xywhs = torch.empty(0, 4)
            confs  = torch.empty(0)
            clss   = torch.empty(0)

        if self.run_object_tracker:
            if self.prev_frames is not None:
                self.tracker.tracker.camera_update(self.prev_frames, self.curr_frames)
            outputs = self.tracker.update(xywhs.cpu(), confs.cpu(), clss.cpu(), im0)
            if not self.ready_for_next_trial:
                hand_ids = [h + index_add for h in self.classes_hand]
                outputs = [o for o in outputs if o[5] in self.classes_obj + hand_ids]
        else:
            if len(preds) > 0:
                outputs = np.array(preds.cpu())
                outputs = np.insert(outputs, 4, -1, axis=1)
                outputs[:, [5, 6]] = outputs[:, [6, 5]]
            else:
                outputs = []

        # xyxy → xywh, then append depth placeholder
        outputs = [np.concatenate((xyxy2xywh(bb[:4]), bb[4:])) for bb in outputs]
        outputs = [np.append(bb, -1) for bb in outputs]

        # Drop frame on large inter-frame motion (camera shake / fast movement)
        if self.prev_frames is not None:
            g1 = cv2.cvtColor(self.curr_frames, cv2.COLOR_BGR2GRAY)
            g2 = cv2.cvtColor(self.prev_frames, cv2.COLOR_BGR2GRAY)
            if np.mean(cv2.absdiff(g1, g2)) > 30:
                outputs = []

        return outputs

    # Pipeline stage 4 — Depth estimation (wall-clock throttled)

    def _estimate_depth(self, outputs: list, im0) -> list:
        """
        Hardware depth is used every frame when available.
        ML depth estimator is throttled to ~3 Hz via wall-clock time
        (replaces the brittle `frame % 10` counter).
        Between ML runs, depth is propagated from the previous frame by track_id.
        """
        if not self.run_depth_estimator:
            self.depth_img = None
            return outputs

        # Hardware path (Android ARCore depth)
        hw_depth = getattr(self.dataset, 'current_depth', None)
        if hw_depth is not None:
            if hw_depth.shape[:2] != im0.shape[:2]:
                hw_depth = cv2.resize(
                    hw_depth, (im0.shape[1], im0.shape[0]),
                    interpolation=cv2.INTER_NEAREST)
            self.depth_img = hw_depth
            return bbs_to_depth(im0, self.depth_img, outputs)

        # ML fallback: ~3 Hz
        now = time.time()
        estimator = getattr(self, 'depth_estimator', None)
        if estimator is not None and (now - self.last_depth_estimate_time) >= 0.33:
            self.depth_img, _ = estimator.predict_depth(im0)
            self.last_depth_estimate_time = now
            outputs = bbs_to_depth(im0, self.depth_img, outputs)
        elif self.prev_outputs.size > 0 and self.depth_img is not None:
            # Propagate depth from previous frame via track_id
            for output in outputs:
                key = output[4] if output[4] != -1 else None
                if key is not None:
                    match = self.prev_outputs[self.prev_outputs[:, 4] == key]
                else:
                    match = self.prev_outputs[self.prev_outputs[:, 5] == output[5]]
                output[7] = match[0][7] if match.size > 0 else -1
        return outputs

    # Pipeline stage 5 — Opportunistic unordered target locking

    def _opportunistic_target_lock(self, outputs: list) -> None:
        """When in unordered mode with no active target, lock onto the first visible list item."""
        if self.class_target_obj != -1:
            return
        if self.memory.get("list_mode") != "unordered":
            return
        target_list = self.memory.get("target_list", [])
        if not target_list:
            return
        for *xywh, obj_id, cls, conf, depth in outputs:
            obj_name = self.master_label.get(int(cls), "")
            if obj_name in target_list:
                self.class_target_obj = int(cls)
                self.classes_obj = [self.class_target_obj]
                self._publish_target(obj_name)
                self.grasped = False
                self.vibration_timer = None
                self.ready_for_next_trial = False
                bc = getattr(self, 'bracelet_controller', None)
                if bc:
                    bc.vibrate = True
                    bc.searching = True
                    bc.prev_target = None
                print(f"[System] Opportunistic lock: {obj_name}")
                break

    # Pipeline stage 6 — Haptic engine

    def _run_haptic_engine(self, outputs: list, index_add: int):
        """
        Navigate hand toward target, manage grasp state.
        Returns curr_target (for visualisation) or None.
        Modifies self.grasped and self.vibration_timer in-place.
        """
        # Still in post-grasp cooldown
        if self.grasped:
            if self.vibration_timer is None:
                self.vibration_timer = time.time()
            elif self.vibration_timer > 0:
                if time.time() - self.vibration_timer > 1.5:
                    if self.belt_controller:
                        self.belt_controller.stop_vibration()
                    self.vibration_timer = -1
            return None

        # Specific-ID re-acquisition
        specific_id = self.specific_track_id
        if specific_id != -1:
            target_det = next(
                (d for d in outputs if d[4] == specific_id and d[5] == self.class_target_obj),
                None)
            if target_det is None and self.specific_bbox is not None:
                xc_old, yc_old, w_old, h_old = self.specific_bbox
                best_dist, best_det = float('inf'), None
                for det in outputs:
                    if det[5] == self.class_target_obj:
                        dist = math.hypot(det[0] - xc_old, det[1] - yc_old)
                        if dist < max(w_old, h_old) * 1.5 and dist < best_dist:
                            best_dist, best_det = dist, det
                if best_det is not None:
                    self.specific_track_id = int(best_det[4])
                    specific_id = self.specific_track_id
                    target_det = best_det
                    print(f"[Tracker] ID recovered → {specific_id}")
            if target_det is not None:
                self.specific_bbox = list(map(float, target_det[:4]))

        # Filter: hands + relevant target only
        hand_ids = [h + index_add for h in self.classes_hand]
        filtered = [
            det for det in outputs
            if det[5] in hand_ids
            or (det[5] == self.class_target_obj
                and (specific_id == -1 or det[4] == specific_id))
        ]

        depth_for_haptics = None
        if self.depth_img is not None:
            depth_for_haptics = self.depth_img.copy()
            depth_for_haptics[depth_for_haptics == 0] = 10.0   # push unknowns away

        new_grasped, curr_target = self.bracelet_controller.navigate_hand(
            self.belt_controller, filtered, self.class_target_obj,
            hand_ids, depth_for_haptics,
            self.participant_vibration_intensities, self.metric
        )
        self.grasped = bool(new_grasped)
        return curr_target

    # Pipeline stage 7 — Render + WebSocket dispatch

    def _render_and_send(self, outputs: list, im0, curr_target,
                          fps: float, save_img: bool,
                          save_dir, vid_path: list, vid_writer: list,
                          save_path: str) -> str:
        """
        Draws bounding boxes, sends JSON detections over WebSocket,
        handles depth side-by-side view, and optionally saves video.
        Returns (potentially updated) save_path.
        """
        annotator = Annotator(im0, line_width=self.line_thickness,
                               example=str(self.names_obj))

        for *xywh, obj_id, cls, conf, depth in outputs:
            obj_class = int(cls)
            xyxy = xywh2xyxy(np.array(xywh))
            if self.view_img or save_img or self.save_crop:
                labelcolor = colors(obj_class, True)   # default
                label_parts = []
                if not self.hide_labels:
                    is_curr = (curr_target is not None and
                               np.array_equal(curr_target, [*xywh, obj_id, cls, conf, depth]))
                    if is_curr:
                        label_parts.append("Target ")
                        labelcolor = (0, 0, 0)
                    else:
                        label_parts.append(f"{self.master_label.get(obj_class, str(obj_class))} ")
                    if not self.hide_conf:
                        label_parts.append(f"{conf * 100:.0f}% ")
                    if self.run_object_tracker:
                        label_parts.append(f"ID:{int(obj_id)} ")
                    if self.run_depth_estimator and depth != -1.0:
                        label_parts.append(f"{depth:.2f}m ")
                annotator.cv_font = cv2.FONT_HERSHEY_SIMPLEX
                annotator.tf = max(annotator.lw - 1, 1)
                annotator.sf = annotator.lw / 3
                annotator.box_label(xyxy, "".join(label_parts), color=labelcolor)

        im0 = annotator.result()

        # JSON bounding-box dispatch to Android (WebSocket sender)
        if hasattr(self, 'result_queue') and self.result_queue is not None:
            if not self.result_queue.full():
                img_h, img_w = im0.shape[:2]
                boxes = []
                for *xywh, obj_id, cls, conf, depth in outputs:
                    x_c, y_c, w, h = xywh
                    boxes.append({
                        "x": float(x_c) / img_w, "y": float(y_c) / img_h,
                        "w": float(w) / img_w,   "h": float(h) / img_h,
                        "label": self.master_label.get(int(cls), "?")
                    })
                self.result_queue.put(boxes)

        # Display
        if self.view_img:
            cv2.putText(im0, f"FPS:{int(fps)} Avg:{int(np.mean(self.fpss))}",
                        (20, 70), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 255, 0), 1)

            if self.run_depth_estimator and self.depth_img is not None:
                im0 = self._make_depth_side_by_side(im0, self.depth_img)
                if visual_mode == "testing" and threading.current_thread() is threading.main_thread():
                    try:
                        cv2.imshow("AIBox & Depth", im0)
                        pressed_key = cv2.waitKey(1)
                    except Exception as e:
                        print(f"[Warning] Could not display: {e}")
                else:
                    pressed_key = -1
                #cv2.imshow("AIBox & Depth", im0)
            else:
                #cv2.imshow("AIBox", im0)
                if visual_mode == "testing" and threading.current_thread() is threading.main_thread():
                    try:
                        cv2.imshow("AIBox", im0)
                        pressed_key = cv2.waitKey(1)
                    except Exception as e:
                        print(f"[Warning] Could not display: {e}")
                else:
                    pressed_key = -1
                cv2.setWindowProperty("AIBox", cv2.WND_PROP_TOPMOST, 1)

        # Video save
        if save_img:
            if self.dataset.mode == 'image':
                cv2.imwrite(save_path, im0)
            else:
                if vid_path[0] != save_path:
                    vid_path[0] = save_path
                    if isinstance(vid_writer[0], cv2.VideoWriter):
                        vid_writer[0].release()
                    vc = getattr(self.dataset, 'cap', None)
                    if vc:
                        fps_v = vc.get(cv2.CAP_PROP_FPS)
                        w_v   = int(vc.get(cv2.CAP_PROP_FRAME_WIDTH))
                        h_v   = int(vc.get(cv2.CAP_PROP_FRAME_HEIGHT))
                    else:
                        fps_v, w_v, h_v = 10.0, im0.shape[1], im0.shape[0]
                    save_path = str(Path(save_path).with_suffix('.mp4'))
                    vid_writer[0] = cv2.VideoWriter(
                        save_path, cv2.VideoWriter_fourcc(*'mp4v'), fps_v, (w_v, h_v))
                vid_writer[0].write(im0)
        return save_path

    def _make_depth_side_by_side(self, im0, depth_img):
        """Render a colourised depth map and return a side-by-side image."""
        valid = depth_img[depth_img > 0]
        if valid.size > 0:
            scene_max = min(np.percentile(valid, 98), 5.0)
            scene_max = max(scene_max, 0.1)
            norm = np.clip(depth_img / scene_max, 0, 1) * 255.0
            d8 = cv2.medianBlur(norm.astype(np.uint8), 5)
            colourmap = cv2.applyColorMap(d8, cv2.COLORMAP_MAGMA)
            colourmap[depth_img == 0] = [0, 0, 0]
            if colourmap.shape[:2] != im0.shape[:2]:
                colourmap = cv2.resize(colourmap, (im0.shape[1], im0.shape[0]))
        else:
            colourmap = np.zeros_like(im0)
        return np.hstack((im0, colourmap))

    # Async memory persistence

    def _save_memory_async(self) -> None:
        """
        Snapshot the memory dict and write to disk in a daemon thread.
        This means zero disk I/O on the vision hot-path.
        """
        snapshot = json.dumps(self.memory, indent=4)
        path = self.memory_file_path
        def _write():
            with open(path, "w") as f:
                f.write(snapshot)
        threading.Thread(target=_write, daemon=True).start()

    def _periodic_memory_save(self) -> None:
        """Called every frame; fires async save every 5 seconds."""
        if time.time() - self.last_memory_save_time >= 5.0:
            self._save_memory_async()
            self.last_memory_save_time = time.time()

    # Memory initialisation

    def _init_memory(self, root: Path) -> None:
        mem_file  = root / 'results' / f"memory_participant_{self.participant}.json"
        log_file  = root / 'results' / f"interaction_log_participant_{self.participant}.jsonl"
        mem_file.parent.mkdir(parents=True, exist_ok=True)
        self.memory_file_path = str(mem_file)
        self.log_file_path    = str(log_file)

        if mem_file.exists():
            with open(self.memory_file_path) as f:
                self.memory = json.load(f)
            if self.shared_state:
                self.shared_state.set_memory_existed(True)
            self.participant_vibration_intensities = self.memory.get(
                "calibration", self.participant_vibration_intensities)
            print(f"[System] Memory loaded from {self.memory_file_path}.")
        else:
            if self.shared_state:
                self.shared_state.set_memory_existed(False)
            self.memory = {
                "target_list": [], "list_mode": "ordered",
                "grasped_objects": [],
                "calibration": self.participant_vibration_intensities,
                "preferences": {
                    "speech_speed": "normal", "verbosity": "normal",
                    "battery_saver": False, "play_welcome_message": True
                },
                "command_history": []
            }
            print("[System] Fresh memory created.")

        if self.shared_state:
            prefs = self.memory.setdefault("preferences", {})
            for k, v in [("speech_speed","normal"),("verbosity","normal"),
                          ("battery_saver",False),("play_welcome_message",True)]:
                prefs.setdefault(k, v)
            self.shared_state.set_preferences(prefs)
            self.shared_state.set_target_list_state(
                self.memory.get("target_list", []),
                self.memory.get("list_mode", "ordered"))
            self.shared_state.update_world_map(self.memory.get("world_map", {}))

    # Main loop — clean orchestration of pipeline stages

    def experiment_loop(self, save_dir, save_img, index_add, vid_path, vid_writer):
        print('\nSTARTING MAIN LOOP')
        self._init_memory(Path(__file__).resolve().parent)
        self._command_table = self._build_command_table()

        # Restore active target from saved memory
        if self.memory.get("target_list") and self.memory.get("list_mode") == "ordered":
            first = self.memory["target_list"][0]
            if first in coco_labels.values():
                self.class_target_obj = next(k for k, v in coco_labels.items() if v == first)
                self.classes_obj = [self.class_target_obj]
                self._publish_target(first)
                print(f"[System] Resumed saved target: {first}")
        else:
            self._publish_target("none")

        self.orig_classes_obj = self.classes_obj
        self.last_memory_save_time = time.time()
        save_path = str(save_dir)

        for frame, (path, im, im0s, vid_cap, _) in enumerate(self.dataset):
            t_start = time.perf_counter()
            im0 = im0s[0].copy() if isinstance(im0s, list) else im0s.copy()

            # 1. Commands
            if not self._process_mcp_commands():
                break

            # 2. Inference
            pred_target, pred_hand = self._run_inference(im)

            # 3. Tracking
            outputs = self._run_tracking(pred_target, pred_hand, im, im0, index_add)

            # 4. Depth
            outputs = self._estimate_depth(outputs, im0)
            self.prev_outputs = np.array(outputs) if outputs else np.array([])

            # 5. Publish visible objects
            self._publish_visible_objects(outputs, im0.shape)

            # 6. Opportunistic unordered lock
            self._opportunistic_target_lock(outputs)

            # 7. Manual target entry (experiment mode)
            if not self.target_entered and self.manual_entry:
                print(f"Available objects:\n{coco_labels}")
                key = input("Enter target key: ")
                if int(key) in coco_labels:
                    self.class_target_obj = int(key)
                    self._publish_target(coco_labels[self.class_target_obj])
                self.target_entered = True
                self.classes_obj = [self.class_target_obj]
                self.grasped = False
                self.vibration_timer = None

            # 8. Haptic engine
            curr_target = None
            if self.class_target_obj != -1:
                curr_target = self._run_haptic_engine(outputs, index_add)

            # 9. Render + WebSocket
            fps = 1.0 / max(time.perf_counter() - t_start, 1e-6)
            self.fpss.append(fps)
            save_path = self._render_and_send(
                outputs, im0, curr_target, fps, save_img,
                save_dir, vid_path, vid_writer, save_path)

            # 10. Periodic memory save
            self._periodic_memory_save()

            # 11. Trial key-handling (experimental only)
            if self.view_img:
                self.pressed_key = cv2.waitKey(1)
                if self.experiment_trial_logic(self.pressed_key) == "break":
                    break

            self.prev_frames = self.curr_frames

    # Trial logic (experimental; separated from the vision pipeline)

    def experiment_trial_logic(self, pressed_key: int):
        """
        Manages the start/stop/record cycle of individual experiment trials.
        This is entirely decoupled from the vision pipeline above.
        """
        RESULT_LABELS = {
            ord('y'): "SUCCESSFUL",
            ord('n'): "FAILED",
            ord('f'): "SYSTEM FAILED",
            ord('t'): "WRONG TARGET"
        }

        if pressed_key in RESULT_LABELS and not self.ready_for_next_trial:
            self.trial_end_time = time.time()
            self.append_output_data()
            self.classes_obj = self.orig_classes_obj
            self.bracelet_controller.frozen = False
            self.bracelet_controller.was_guiding = False
            print(f"TRIAL {RESULT_LABELS[pressed_key]}")
            if not self.manual_entry:
                if self.obj_index >= len(self.target_objs) - 1:
                    print("ALL TARGETS COVERED")
                    self.save_output_data()
                    return "break"
                self.obj_index += 1
                self.ready_for_next_trial = True
                self.class_target_obj = -1
                self._publish_target("none")
            else:
                self.ready_for_next_trial = True

        elif pressed_key == ord('s') and self.ready_for_next_trial:
            print("STARTING NEXT TRIAL")
            self.trial_start_time = time.time()
            self.target_entered = False
            self.ready_for_next_trial = False
            self.bracelet_controller.vibrate = True

        elif pressed_key == ord('c'):
            self.append_output_data()
            self.save_output_data()
            if self.belt_controller:
                self.belt_controller.stop_vibration()
            return "break"

    # Data output

    def append_output_data(self):
        bc = self.bracelet_controller
        row = [
            self.class_target_obj, self.trial_start_time,
            bc.navigation_time, bc.freezing_time, bc.grasping_time,
            self.trial_end_time, chr(self.pressed_key),
            bc.target_detections_list[:], bc.target_confidence_list[:],
            bc.target_class_track_ids[:], bc.target_object_track_ids[:],
            bc.target_position[:], bc.hand_confidence_list[:], bc.hand_position[:]
        ]
        self.trial_start_time = self.trial_end_time = 'NA'
        for attr in ('navigation_time', 'freezing_time', 'grasping_time'):
            setattr(bc, attr, 'NA')
        for attr in ('target_detections_list', 'target_confidence_list',
                     'target_class_track_ids', 'target_object_track_ids',
                     'target_position', 'hand_confidence_list', 'hand_position'):
            setattr(bc, attr, [])
        self.output_data.append(row)

    def save_output_data(self):
        import pandas as pd
        df = pd.DataFrame(self.output_data)
        df.to_csv(
            self.output_path + f"{self.condition}_participant_{self.participant}.csv",
            index=False)

    # Model loaders

    def load_object_detector(self):
        print('\nLOADING OBJECT DETECTORS')
        self.device = select_device(self.device)
        self.model_obj  = DetectMultiBackend(self.weights_obj,  device=self.device, dnn=self.dnn, fp16=self.half)
        self.model_hand = DetectMultiBackend(self.weights_hand, device=self.device, dnn=self.dnn, fp16=self.half)
        self.names_obj  = self.model_obj.names
        self.stride_hand, self.names_hand, self.pt_hand = (
            self.model_hand.stride, self.model_hand.names, self.model_hand.pt)
        self.dt = (Profile(), Profile(), Profile())
        print('\nOBJECT DETECTORS LOADED')

    def load_object_tracker(self, max_age=70, n_init=3):
        print('\nLOADING OBJECT TRACKER')
        self.tracker = StrongSORT(
            model_weights=self.weights_tracker, device=self.device, fp16=False,
            max_dist=0.5, max_iou_distance=0.7, max_age=max_age, n_init=n_init,
            nn_budget=100, mc_lambda=0.995, ema_alpha=0.9)
        print('\nOBJECT TRACKER LOADED')

    def load_depth_estimator(self):
        print('\nLOADING DEPTH ESTIMATOR')
        if self.metric:
            self.depth_estimator = UniDepthEstimator(
                model_type=self.weights_depth_estimator, device=self.device)
        else:
            self.depth_estimator = MidasDepthEstimator(
                model_type=self.weights_depth_estimator, device=self.device)
        print('\nDEPTH ESTIMATOR LOADED')

    def warmup_model(self, model, type='detector'):
        print('\nWARMING UP...')
        if type == 'detector':
            model.warmup(imgsz=(1 if self.pt_hand or self.model_hand.triton else self.bs,
                                 3, *self.imgsz))
        elif type == 'tracker':
            model.warmup()

    # Entry point

    @smart_inference_mode()
    def run(self):
        source   = self.source
        save_img = not self.nosave and not source.endswith('.txt')
        is_file  = Path(source).suffix[1:] in (IMG_FORMATS + VID_FORMATS)
        is_url   = source.lower().startswith(('rtsp://', 'rtmp://', 'http://', 'https://'))

        if is_url and is_file:
            source = check_file(source)

        base_dir = Path(self.project) / self.name
        base_dir.mkdir(parents=True, exist_ok=True)
        existing = list(base_dir.glob(f'{self.condition}_participant_{self.participant}_trial*'))
        max_ctr  = max(
            [int(f.stem.split('_')[-1].replace('trial', ''))
             for f in existing
             if f.stem.split('_')[-1].replace('trial', '').isdigit()],
            default=0)
        save_dir = base_dir / f'{self.condition}_participant_{self.participant}_trial_{max_ctr+1}'

        self.load_object_detector()
        self._publish_available_classes()
        self.bs = 1
        check_imshow(warn=True)

        if not (hasattr(self, 'dataset') and self.dataset is not None):
            try:
                if os.path.isdir(source) or os.path.isfile(source):
                    self.dataset = LoadImages(source, img_size=480)
                else:
                    self.dataset = LoadStreams(source)
            except AssertionError:
                self.dataset = LoadStreams('0', img_size=480)

        self.bs = len(self.dataset)
        vid_path, vid_writer = [None] * self.bs, [None] * self.bs

        index_add = len(self.names_obj)
        self.master_label = self.names_obj | {k + index_add: v for k, v in self.names_hand.items()}

        if self.run_object_tracker:
            self.load_object_tracker(max_age=self.tracker_max_age, n_init=self.tracker_n_init)
        else:
            print('SKIPPING OBJECT TRACKER')

        if self.run_depth_estimator:
            if type(getattr(self, 'dataset', None)).__name__ == 'AndroidSource':
                print('HARDWARE DEPTH: SKIPPING ML DEPTH ESTIMATOR')
                self.depth_estimator = None
            else:
                self.load_depth_estimator()
        else:
            print('SKIPPING DEPTH ESTIMATOR')

        self.warmup_model(self.model_hand)
        if self.run_object_tracker:
            self.warmup_model(self.tracker.model, 'tracker')

        self.bracelet_controller.mock_navigate = bool(self.mock_navigate)
        self.experiment_loop(save_dir, save_img, index_add, vid_path, vid_writer)
