"""
This script is using code from the following sources:
- YOLOv5 🚀 by Ultralytics, AGPL-3.0 license, https://github.com/ultralytics/yolov5
- StrongSORT MOT, https://github.com/dyhBUPT/StrongSORT, https://pypi.org/project/strongsort/
- Youtube Tutorial "Simple YOLOv8 Object Detection & Tracking with StrongSORT & ByteTrack" by Nicolai Nielsen, https://www.youtube.com/watch?v=oDALtKbprHg
- https://github.com/zenjieli/Yolov5StrongSORT/blob/master/track.py, original: https://github.com/mikel-brostrom/yolo_tracking/commit/9fec03ddba453959f03ab59bffc36669ae2e932a
"""

import queue
import sys

import sys
from pathlib import Path
import os

# Use the project file packages instead of the conda packages, i.e. add to system path for import
file = Path(__file__).resolve()
root = file.parents[0]
paths_to_add = ['/yolov5', '/strongsort', '/unidepth', '/midas']
for path in paths_to_add:
    if str(root) + path not in sys.path:
        sys.path.append(str(root) + path)

# Utility
import time
from datetime import datetime
import pandas as pd
import numpy as np
import threading
from playsound import playsound
import json

# Image processing
import cv2

# Object tracking
import torch
from labels import coco_labels # COCO labels dictionary
from yolov5.models.common import DetectMultiBackend
from yolov5.utils.dataloaders import IMG_FORMATS, VID_FORMATS, LoadImages, LoadScreenshots, LoadStreams
from yolov5.utils.general import (LOGGER, Profile, check_file, check_img_size, check_imshow, check_requirements, colorstr, cv2,
                           increment_path, non_max_suppression, print_args, scale_boxes, strip_optimizer, xyxy2xywh, xywh2xyxy)
from yolov5.utils.plots import Annotator, colors, save_one_box
from yolov5.utils.torch_utils import select_device, smart_inference_mode
from strongsort.strong_sort import StrongSORT # there is also a pip install, but it has multiple errors
from ultralytics import YOLO
from ultralytics.nn.autobackend import AutoBackend

# Depth Estimation
#from unidepth_estimator import UniDepthEstimator # metric
#from midas_estimator import MidasDepthEstimator # relative
#from midas.run import create_side_by_side


def beginning_sound():
    file = 'resources/sound/beginning.mp3'
    playsound(str(file))

def play_start():
    play_start_thread = threading.Thread(target=beginning_sound, name='play_start')
    play_start_thread.start()


def bbs_to_depth(image, depth=None, bbs=None):
    if bbs is not None and depth is not None:
        outputs = []
        for bb in bbs:
            if bb[7] == -1:
                x_c, y_c, w, h = [int(coord) for coord in bb[:4]]
                
                # Shrink the search window to the center 50% of the bounding box.
                search_w = int(w * 0.25) 
                search_h = int(h * 0.25)
                
                x1 = max(0, x_c - search_w)
                y1 = max(0, y_c - search_h)
                x2 = min(depth.shape[1], x_c + search_w)
                y2 = min(depth.shape[0], y_c + search_h)
                
                roi = depth[y1:y2, x1:x2]
                
                valid_pixels = roi[roi > 0]
                
                if valid_pixels.size > 0:
                    # Find the 15th percentile foreground object
                    true_depth_meters = float(np.percentile(valid_pixels, 15))
                else:
                    true_depth_meters = -1.0 # No data
                    
                bb[7] = true_depth_meters
            outputs.append(bb)
        return np.array(outputs)
    return bbs


def close_app(controller):
    controller.stop_vibration() if controller else None
    cv2.destroyAllWindows()
    threads = threading.enumerate()
    for thread in threads:
        thread._tstate_lock = None
        thread._stop()
    controller.disconnect_belt() if controller else None
    print("Application will be closed.")
    sys.exit()


class AutoAssign:

    def __init__(self, mcp_queue=None, shared_state=None, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)
        self.mcp_queue = mcp_queue
        self.shared_state = shared_state


class TaskController(AutoAssign):

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

    # ------------------------------------------------------------------
    # helpers for publishing state to the MCP server
    # ------------------------------------------------------------------
    def _publish_target(self, target_name: str) -> None:
        """Update SharedState so the MCP tool can read the current target."""
        if self.shared_state is not None:
            self.shared_state.set_target(target_name)

    def _publish_visible_objects(self, outputs) -> None:
        """Push the latest per-frame detections (objects only, no hands)
        into SharedState so get_visible_objects can return them."""
        if self.shared_state is None:
            return
        visible = []
        for item in outputs:
            cls = int(item[5])
            # self.master_label contains both COCO objects and hand classes
            if hasattr(self, 'master_label') and cls in self.master_label:
                visible.append({
                    "name":       self.master_label[cls],
                    "confidence": float(item[6]),
                    "track_id":   int(item[4]),
                    "depth":      float(item[7]) if len(item) > 7 else -1,
                })
        self.shared_state.set_visible_objects(visible)

    def _publish_available_classes(self) -> None:
        """Publish the full set of COCO label names that set_target
        validates against.  Called once after models are loaded."""
        if self.shared_state is not None:
            self.shared_state.set_available_classes(
                list(coco_labels.values())
            )

    # ------------------------------------------------------------------

    def append_output_data(self):

        output_data_row = []

        output_data_row.append(self.class_target_obj)

        output_data_row.append(self.trial_start_time)

        output_data_row.append(self.bracelet_controller.navigation_time)
        output_data_row.append(self.bracelet_controller.freezing_time)
        output_data_row.append(self.bracelet_controller.grasping_time)

        output_data_row.append(self.trial_end_time)
        
        self.trial_start_time = 'NA'
        self.trial_end_time = 'NA'

        self.bracelet_controller.navigation_time = 'NA'
        self.bracelet_controller.freezing_time = 'NA'
        self.bracelet_controller.grasping_time = 'NA'
        
        output_data_row.append(chr(self.pressed_key))

        output_data_row.append(self.bracelet_controller.target_detections_list)
        output_data_row.append(self.bracelet_controller.target_confidence_list)

        self.bracelet_controller.target_detections_list = []
        self.bracelet_controller.target_confidence_list = []

        output_data_row.append(self.bracelet_controller.target_class_track_ids)
        output_data_row.append(self.bracelet_controller.target_object_track_ids)
        output_data_row.append(self.bracelet_controller.target_position)

        self.bracelet_controller.target_class_track_ids = []
        self.bracelet_controller.target_object_track_ids = []
        self.bracelet_controller.target_position = []

        output_data_row.append(self.bracelet_controller.hand_confidence_list)
        output_data_row.append(self.bracelet_controller.hand_position)

        self.bracelet_controller.hand_confidence_list = []
        self.bracelet_controller.hand_position = []

        self.output_data.append(output_data_row)

    
    def save_output_data(self):

        df = pd.DataFrame(self.output_data)
        df.to_csv(self.output_path + f"{self.condition}_participant_{self.participant}.csv", index=False)


    def load_object_detector(self):
        
        print(f'\nLOADING OBJECT DETECTORS')
        
        self.device = select_device(self.device)
        self.model_obj = DetectMultiBackend(self.weights_obj, device=self.device, dnn=self.dnn, fp16=self.half)
        self.model_hand = DetectMultiBackend(self.weights_hand, device=self.device, dnn=self.dnn, fp16=self.half)

        self.names_obj = self.model_obj.names        
        self.stride_hand, self.names_hand, self.pt_hand = self.model_hand.stride, self.model_hand.names, self.model_hand.pt
        self.dt = (Profile(), Profile(), Profile())

        print(f'\nOBJECT DETECTORS LOADED SUCCESFULLY')


    def load_object_tracker(self, max_age=70, n_init=3):

        print(f'\nLOADING OBJECT TRACKER')

        self.tracker = StrongSORT(
                model_weights=self.weights_tracker, 
                device=self.device,
                fp16=False,
                max_dist=0.5,
                max_iou_distance=0.7,
                max_age=max_age,
                n_init=n_init,
                nn_budget=100,
                mc_lambda=0.995,
                ema_alpha=0.9
                )
    
        print(f'\nOBJECT TRACKER LOADED SUCCESFULLY')


    def load_depth_estimator(self):
        
        print(f'\nLOADING DEPTH ESTIMATOR')

        if self.metric:
            self.depth_estimator = UniDepthEstimator(
                model_type = self.weights_depth_estimator,
                device=self.device
            )
        else:
            self.depth_estimator = MidasDepthEstimator(
                model_type = self.weights_depth_estimator,
                device=self.device
            )

        print(f'\nDEPTH ESTIMATOR LOADED SUCCESFULLY')
        

    def warmup_model(self, model, type='detector'):

        print(f'\nWARMING UP MODEL...')

        if type == 'detector':
            model.warmup(imgsz=(1 if self.pt_hand or self.model_hand.triton else self.bs, 3, *self.imgsz))
        
        if type == 'tracker':
            model.warmup()

    def get_depth(self, im0, frame, outputs, prev_outputs, frame_factor=10):

        if frame % frame_factor == 0:
            depthmap, _ = self.depth_estimator.predict_depth(im0)
            outputs = bbs_to_depth(im0, depthmap, outputs)
        else:
            if prev_outputs.size > 0:
                for output in outputs:
                    match = prev_outputs[prev_outputs[:, 4] == output[4]]
                    if match.size > 0:
                        output[7] = match[0][7]
                    else:
                        output[7] = -1

        return depthmap, outputs


    def experiment_trial_logic(self, pressed_key):

        # end trial
        if pressed_key in [ord('y'), ord('n'), ord('f'), ord('t')] and not self.ready_for_next_trial:

            self.trial_end_time = time.time()

            self.append_output_data()

            self.classes_obj = self.orig_classes_obj

            self.bracelet_controller.frozen = False
            self.bracelet_controller.was_guiding = False
            
            if pressed_key == ord('y'):
                print("TRIAL SUCCESSFUL")
            elif pressed_key == ord('n'):
                print("TRIAL FAILED")
            elif pressed_key == ord('f'):
                print("SYSTEM FAILED")
            elif pressed_key == ord('t'):
                print("WRONG TARGET")
            
            if not self.manual_entry:
                if self.obj_index >= len(self.target_objs) - 1:
                    print("ALL TARGETS COVERED")
                    self.save_output_data()
                    return "break"
                else:
                    print("MOVING TO NEXT TARGET (S to start trial)")
                    self.obj_index += 1
                    self.ready_for_next_trial = True
                    self.class_target_obj = -1
                    self._publish_target("none")
            else:
                print("MOVING TO NEXT TARGET (S to start trial)")
                self.ready_for_next_trial = True

        # start next trial
        elif pressed_key == ord('s') and self.ready_for_next_trial:
            print("STARTING NEXT TRIAL")
            self.trial_start_time = time.time()
            self.target_entered = False
            self.ready_for_next_trial = False
            self.bracelet_controller.vibrate = True

        # end experiment
        elif pressed_key == ord('c'):

            self.append_output_data()
            self.save_output_data()

            if self.belt_controller:
                self.belt_controller.stop_vibration()
            return "break"


    def experiment_loop(self, save_dir, save_img, index_add, vid_path, vid_writer):

        print(f'\nSTARTING MAIN LOOP')

        # Memory component
        file = Path(__file__).resolve()
        root = file.parent
        
        # State file
        memory_file = root / 'results' / f"memory_participant_{self.participant}.json"
        memory_file.parent.mkdir(parents=True, exist_ok=True)
        memory_file_path = str(memory_file)

        # Conversations log file
        log_file = root / 'results' / f"interaction_log_participant_{self.participant}.jsonl"
        self.log_file_path = str(log_file)

        if os.path.exists(memory_file_path):
            with open(memory_file_path, "r") as f:
                self.memory = json.load(f)
            # Restore saved calibrations
            self.participant_vibration_intensities = self.memory.get("calibration", self.participant_vibration_intensities)
            print(f"[System] Memory loaded successfully from {memory_file_path}.")
        else:
            self.memory = {
                "target_list": [],
                "list_mode": "ordered", # ordered, unordered
                "grasped_objects": [],
                "calibration": self.participant_vibration_intensities,
                "preferences": {"speech_speed": "normal", "verbosity": "normal"},
                "command_history": []
            }
            print("[System] No memory file found. Creating new memory file.")

        def save_memory():
            with open(memory_file, "w") as f:
                json.dump(self.memory, f, indent=4)
        self.save_memory = save_memory

        if self.shared_state is not None:
            prefs = self.memory.setdefault("preferences", {})
            prefs.setdefault("speech_speed", "normal")
            prefs.setdefault("verbosity", "normal")
            prefs.setdefault("battery_saver", False) # Default is OFF
            
            self.shared_state.set_preferences(prefs)
            self.shared_state.set_target_list_state(self.memory.get("target_list", []), self.memory.get("list_mode", "ordered"))

        # Initialize vars for tracking
        prev_frames = None
        curr_frames = None
        fpss = []
        outputs = []
        prev_outputs = np.array([])

        self.obj_index = 0
        self.ready_for_next_trial = True
        self.target_entered = True
        self.class_target_obj = -1
        self.orig_classes_obj = self.classes_obj
        manual_experiment_msg = "The experiment will be run manually. You will enter the desired target for each run yourself."
        automatic_experiment_msg = f'The experiment will be run automatically. The selected target objects, in sequence, are:\n{self.target_objs}'
        print(manual_experiment_msg) if self.manual_entry else print(automatic_experiment_msg)
        
        # Be default, auto resume targets after restart - to validate whether it is optimal from the user perspective
        if self.memory.get("target_list") and self.memory.get("list_mode") == "ordered":
            first_target = self.memory["target_list"][0]
            if first_target in coco_labels.values():
                self.class_target_obj = next(k for k, v in coco_labels.items() if v == first_target)
                self.classes_obj = [self.class_target_obj]
                self._publish_target(first_target)
                print(f"[System] Resumed saved target: {first_target}")
        else:
            self._publish_target("none")

        self.trial_start_time = 'NA'
        self.trial_end_time = 'NA'

        grasped = False

        vibration_timer = None

        # Data processing: Iterate over each frame of the live stream
        for frame, (path, im, im0s, vid_cap, _) in enumerate(self.dataset):

            # MCP queue listener
            if hasattr(self, 'mcp_queue') and self.mcp_queue:
                try:
                    cmd_data = self.mcp_queue.get_nowait()
                    
                    print(f"\n[System] RECEIVED COMMAND: {cmd_data}", file=sys.stderr, flush=True)

                    with open("controller_mcp_debug_log.txt", "a") as f:
                        f.write(f"RECEIVED: {cmd_data}\n")

                    instruction = cmd_data.get("instruction")
                    value = cmd_data.get("value")

                    # 1. Stop system
                    if instruction == "stop":
                        print("[System] Stopping via Voice...", file=sys.stderr)
                        break 
                    
                    # 2. Change target object
                    elif instruction == "set_target":
                        if value in coco_labels.values():
                            new_id = next(k for k, v in coco_labels.items() if v == value)
                            self.class_target_obj = new_id
                            
                            self.classes_obj = [self.class_target_obj]
                            self.target_entered = True

                            # RESET ALL TRIAL AND VIBRATION FLAGS
                            self.ready_for_next_trial = False 
                            self.trial_start_time = time.time()
                            self.frozen = False
                            
                            if hasattr(self, 'bracelet_controller') and self.bracelet_controller:
                                self.bracelet_controller.vibrate = True
                                self.bracelet_controller.frozen = False
                                self.bracelet_controller.was_guiding = False
                                self.bracelet_controller.searching = True
                                self.bracelet_controller.prev_target = None
                                self.bracelet_controller.prev_hand = None

                            print(f"[System] Switched target to: {value} (ID: {new_id})", file=sys.stderr)

                            self._publish_target(value)
                        else:
                            print(f"[System] Error: '{value}' is not a valid COCO label.", file=sys.stderr)

                    # 3. Pause navigation
                    elif instruction == "pause_navigation":
                        self.bracelet_controller.vibrate = False
                        if self.belt_controller:
                            self.belt_controller.stop_vibration()
                        print("[System] Navigation paused", file=sys.stderr)

                    # 4. Resume navigation
                    elif instruction == "resume_navigation":
                        self.bracelet_controller.vibrate = True
                        print("[System] Navigation resumed", file=sys.stderr)

                    # 5. Adjust vibration intensity and save to memory
                    elif instruction == "adjust_intensity":
                        motor, intensity = value.split(":")
                        intensity = int(intensity)
                        self.participant_vibration_intensities[motor] = intensity
                        self.memory["calibration"][motor] = intensity
                        self.save_memory()
                        print(f"[System] {motor} intensity → {intensity} (Saved)", file=sys.stderr)

                    # 6. Receive a List of Targets
                    elif instruction == "set_target_list":
                        data = json.loads(value) # Expects: {"targets": ["cup", "bottle"], "mode": "ordered"}
                        self.memory["target_list"] = data["targets"]
                        self.memory["list_mode"] = data["mode"]
                        self.save_memory()
                        
                        if self.shared_state is not None:
                            self.shared_state.set_target_list_state(data["targets"], data["mode"])

                        if data["mode"] == "ordered" and len(data["targets"]) > 0:
                            first_target = data["targets"][0]
                            if first_target in coco_labels.values():
                                self.class_target_obj = next(k for k, v in coco_labels.items() if v == first_target)
                                self.classes_obj = [self.class_target_obj]
                                self._publish_target(first_target)
                                print(f"[System] Ordered list started. Target: {first_target}")
                        else:
                            self.class_target_obj = -1
                            self._publish_target("none")
                            print(f"[System] Unordered list started. Waiting to see objects...")

                    # 7. Mark as Grasped (Proceed to Next)
                    elif instruction == "mark_grasped":
                        curr_target = self.shared_state.get_target()
                        if curr_target != "none":
                            # Save to history
                            if curr_target not in self.memory["grasped_objects"]:
                                self.memory["grasped_objects"].append(curr_target)
                            # Remove from queue
                            if curr_target in self.memory["target_list"]:
                                self.memory["target_list"].remove(curr_target)
                            self.save_memory()

                            if self.shared_state is not None:
                                self.shared_state.set_target_list_state(self.memory["target_list"], self.memory["list_mode"])
                            
                            # Decide what to do next
                            if len(self.memory["target_list"]) == 0:
                                print("[System] List complete. Going idle.")
                                self.class_target_obj = -1
                                self._publish_target("none")
                                if self.belt_controller: self.belt_controller.stop_vibration()
                                
                            elif self.memory["list_mode"] == "ordered":
                                next_tgt = self.memory["target_list"][0]
                                self.class_target_obj = next(k for k, v in coco_labels.items() if v == next_tgt)
                                self.classes_obj = [self.class_target_obj]
                                self._publish_target(next_tgt)
                                print(f"[System] Next ordered target: {next_tgt}")
                                
                            else:
                                print("[System] Object Grasped. Returning to idle until next list item is seen.")
                                self.class_target_obj = -1
                                self._publish_target("none")
                                if self.belt_controller: self.belt_controller.stop_vibration()

                    # 8. Log command and response
                    elif instruction == "log_interaction":
                        data = json.loads(value)
                        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        
                        entry = {
                            "time": stamp, 
                            "user_command": data.get("user_text", ""),
                            "ai_response": data.get("ai_response", "")
                        }
                        
                        with open(self.log_file_path, "a", encoding="utf-8") as f:
                            f.write(json.dumps(entry) + "\n")

                    # 9. Update user preferences regarding speech speed and verbosity
                    elif instruction == "update_preferences":
                        data = json.loads(value)
                        prefs = self.memory.setdefault("preferences", {})
                        
                        if "speech_speed" in data: prefs["speech_speed"] = data["speech_speed"]
                        if "verbosity" in data: prefs["verbosity"] = data["verbosity"]
                        if "battery_saver" in data: prefs["battery_saver"] = data["battery_saver"]
                        
                        self.save_memory()
                        if self.shared_state is not None:
                            self.shared_state.set_preferences(prefs)
                        print(f"[System] Preferences updated: {prefs}")

                    # 10. Clear target list
                    elif instruction == "clear_list":
                        self.memory["target_list"] = []
                        self.save_memory()

                        if self.shared_state is not None:
                            self.shared_state.set_target_list_state([], "ordered")

                        self.class_target_obj = -1
                        self._publish_target("none")
                        if self.belt_controller: self.belt_controller.stop_vibration()
                        print("[System] Target list cleared.")

                except queue.Empty:
                    pass
                except Exception as e:
                    print(f"[System] Error processing command: {e}", file=sys.stderr)

            # Start timer for FPS measure
            start = time.perf_counter()
            if self.dataset.mode == 'image':
                p, im0 = Path(path), im0s.copy()
            else:
                p, im0 = Path(path[0]), im0s[0].copy()
            save_path = str(save_dir)
            annotator = Annotator(im0, line_width=self.line_thickness, example=str(self.names_obj))

            # Image pre-processing
            with self.dt[0]:
                image = torch.from_numpy(im).to(self.model_obj.device)
                image = image.half() if self.model_hand.fp16 else image.float()
                image /= 255
                if len(image.shape) == 3:
                    image = image[None]

            # Object detection inference
            with self.dt[1]:
                visualize = increment_path(save_dir / p.stem, mkdir=True) if self.visualize else False
                pred_target = self.model_obj(image, augment=self.augment, visualize=visualize)
                pred_hand = self.model_hand(image, augment=self.augment, visualize=visualize)

            # Non-maximal supression
            with self.dt[2]:
                pred_target = non_max_suppression(pred_target, self.conf_thres, self.iou_thres, self.classes_obj, self.agnostic_nms, max_det=self.max_det)
                pred_hand = non_max_suppression(pred_hand, self.conf_thres, self.iou_thres, self.classes_hand, self.agnostic_nms, max_det=self.max_det)

            for hand in pred_hand[0]:
                if len(hand):
                    hand[5] += index_add

            # Camera motion compensation for tracker (ECC)
            if self.run_object_tracker:
                curr_frames = im0
                self.tracker.tracker.camera_update(prev_frames, curr_frames)
            
            # Initialize/clear detections
            xywhs = torch.empty(0,4)
            confs = torch.empty(0)
            clss = torch.empty(0)

            # Process object detections
            preds = torch.cat((pred_target[0], pred_hand[0]), dim=0)
            if len(preds) > 0:
                preds[:, :4] = scale_boxes(im.shape[2:], preds[:, :4], im0.shape).round()
                xywhs = xyxy2xywh(preds[:, :4])
                confs = preds[:, 4]
                clss = preds[:, 5]

            # Generate tracker outputs for navigation
            if self.run_object_tracker:
                outputs = self.tracker.update(xywhs.cpu(), confs.cpu(), clss.cpu(), im0)
                if not self.ready_for_next_trial:
                    hand_index_list = [hand + index_add for hand in self.classes_hand]
                    outputs = [output for output in outputs if output[5] in self.classes_obj + hand_index_list]
            else:
                outputs = np.array(preds.cpu())
                outputs = np.insert(outputs, 4, -1, axis=1)
                outputs[:, [5, 6]] = outputs[:, [6, 5]]

            # Convert xyxy to xywh
            outputs = [np.concatenate((xyxy2xywh(bb[:4]), bb[4:])) for bb in outputs]

            # Add depth placeholder to outputs
            outputs = [np.append(bb, -1) for bb in outputs]

            # Calculate difference between current and previous frame
            if prev_frames is not None:
                img_gr_1, img_gr_2 = cv2.cvtColor(curr_frames, cv2.COLOR_BGR2GRAY), cv2.cvtColor(prev_frames, cv2.COLOR_BGR2GRAY)
                diff = cv2.absdiff(img_gr_1, img_gr_2)
                mean_diff = np.mean(diff)
                std_diff = np.std(diff)
                if mean_diff > 30:
                    outputs = []

            # Depth estimation
            if not self.run_depth_estimator:
                depth_img = None
            else:
                # Check for hardware depth map
                if hasattr(self.dataset, 'current_depth') and self.dataset.current_depth is not None:
                    depth_img = self.dataset.current_depth
                    
                    # Hardware depth maps are often lower resolution than RGB.
                    # Resize it to match the RGB frame so bounding boxes align perfectly.
                    if depth_img.shape[:2] != im0.shape[:2]:
                        depth_img = cv2.resize(depth_img, (im0.shape[1], im0.shape[0]), interpolation=cv2.INTER_NEAREST)
                        
                    outputs = bbs_to_depth(im0, depth_img, outputs)
                else:
                    # ML fallback (if phone doesn't send depth)
                    if frame % 10 == 0:
                        if hasattr(self, 'depth_estimator') and self.depth_estimator is not None:
                            depth_img, _ = self.depth_estimator.predict_depth(im0)
                            outputs = bbs_to_depth(im0, depth_img, outputs)
                        else:
                            depth_img = None # Safety net
                    else:
                        if prev_outputs.size > 0:
                            for output in outputs:
                                if output[4] != -1:
                                    match = prev_outputs[prev_outputs[:, 4] == output[4]]
                                else:
                                    match = prev_outputs[prev_outputs[:, 5] == output[5]]
                                if match.size > 0:
                                    output[7] = match[0][7]
                                else:
                                    output[7] = -1

            # Set current tracking information as previous info
            prev_outputs = np.array(outputs)

            # Publish visible objects every frame
            self._publish_visible_objects(outputs)

            # Opportunistic targeting (unordered list)
            if self.class_target_obj == -1 and self.memory.get("list_mode") == "unordered" and len(self.memory.get("target_list", [])) > 0:
                for *xywh, obj_id, cls, conf, depth_val in outputs:
                    obj_name = self.master_label.get(int(cls), "unknown")
                    if obj_name in self.memory["target_list"]:
                        # Lock onto the first list item that becomes visible
                        self.class_target_obj = int(cls)
                        self.classes_obj = [self.class_target_obj]
                        self._publish_target(obj_name)
                        print(f"[System] Opportunistic lock on visible target: {obj_name}")
                        break

            # Get FPS
            end = time.perf_counter()
            runtime = end - start
            fps = 1 / runtime
            fpss.append(fps)
            prev_frames = curr_frames

            # Get the target object class
            if not self.target_entered:
                if self.manual_entry:
                    print(f"These are the available objects:\n{coco_labels}")
                    target_obj_verb = input('Enter the object key you want to target: ')

                    if int(target_obj_verb) in coco_labels.keys():
                        self.class_target_obj = int(target_obj_verb)
                        self._publish_target(coco_labels[self.class_target_obj])    # <-- existing
                    else:
                        print(f'The object {target_obj_verb} is not in the list of available targets. Please reselect.')
                else:
                    target_obj_verb = self.target_objs[self.obj_index]
                    self.class_target_obj = next(key for key, value in coco_labels.items() if value == target_obj_verb)
                    file = f'resources/sound/{target_obj_verb}.mp3'

                    self._publish_target(target_obj_verb)

                self.target_entered = True
                self.classes_obj = [self.class_target_obj]
                grasped = False
                vibration_timer = None

            # Navigate the hand
            if not grasped:
                if depth_img is not None:
                    depth_img_metric = depth_img.copy()
                    depth_img_metric[depth_img == 0] = 10.0 # push unknown space out of the way
                else:
                    depth_img_metric = None

                grasped, curr_target = self.bracelet_controller.navigate_hand(
                    self.belt_controller, outputs, self.class_target_obj, 
                    [hand + index_add for hand in self.classes_hand], 
                    depth_img_metric, self.participant_vibration_intensities, self.metric
                )
            else:
                if vibration_timer is None:
                    vibration_timer = time.time()
                    grasped, curr_target = True, None
                elif vibration_timer > 0:
                    if time.time() - vibration_timer > 1.5:
                        if self.belt_controller:
                            self.belt_controller.stop_vibration()
                        vibration_timer = -1

            # VISUALIZATIONS

            for *xywh, obj_id, cls, conf, depth in outputs:
                id, obj_class = int(obj_id), int(cls)
                xyxy = xywh2xyxy(np.array(xywh))

                if save_img or self.save_crop or self.view_img:
                    parts = []
                    if not self.hide_labels:
                        if np.array_equal(curr_target, [*xywh, obj_id, cls, conf, depth]):
                            parts.append(f'Target ')
                            labelcolor = (0,0,0)
                        else:
                            parts.append(f'{self.master_label[obj_class]} ')
                            labelcolor = colors(obj_class, True)

                        if not self.hide_conf:
                            parts.append(f'{conf*100:.0f}% ')
                        if self.run_object_tracker:
                            parts.append(f'ID: {id} ')
                        if self.run_depth_estimator:
                            if depth != -1.0:
                                parts.append(f'Depth: {depth:.2f}m ')
                            else:
                                parts.append(f'Depth: N/A ')

                    label = ''.join(parts)

                    annotator.cv_font = cv2.FONT_HERSHEY_SIMPLEX
                    annotator.tf = max(annotator.lw - 1, 1)
                    annotator.sf = annotator.lw / 3
                    annotator.box_label(xyxy, label, color=labelcolor)
            im0 = annotator.result()

            if hasattr(self, 'result_queue') and self.result_queue is not None:
                if not self.result_queue.full():
                    # We send a copy to avoid threading race conditions
                    self.result_queue.put(im0.copy())

            # JSON OPTIMIZATION: Extract Boxes instead of Image
            if hasattr(self, 'result_queue') and self.result_queue is not None:
                if not self.result_queue.full():
                    json_data = []
                    img_h, img_w, _ = im0.shape
                    
                    for *xywh, obj_id, cls, conf, depth in outputs:
                        x_c, y_c, w, h = xywh
                        label_name = self.master_label[int(cls)]
                        json_data.append({
                            "x": float(x_c) / img_w,
                            "y": float(y_c) / img_h,
                            "w": float(w) / img_w,
                            "h": float(h) / img_h,
                            "label": label_name
                        })
                    self.result_queue.put(json_data) # Send List of boxes

            if self.view_img:
                cv2.putText(im0, f'FPS: {int(fps)}, Avg: {int(np.mean(fpss))}', (20,70), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0,255,0), 1)

                if self.run_depth_estimator:
                    if self.bracelet_controller.roi_coords is not None:
                        print(f'coords: {self.bracelet_controller.roi_coords}')
                        (minyc, maxyc, minxc, maxxc) = self.bracelet_controller.roi_coords
                    if self.bracelet_controller.obstacle_target is not None:
                        target_x, target_y = map(int, self.bracelet_controller.obstacle_target)
                        print(f'target: {self.bracelet_controller.obstacle_target}')
                        cv2.circle(im0, (target_x+minxc, target_y+minyc), radius=5, color=(0, 0, 255), thickness=-1)
                    if self.bracelet_controller.corners is not None:
                        for corner in self.bracelet_controller.corners:
                            cv2.circle(im0, (corner[1]+minxc, corner[0]+minyc), radius=1, color=(0, 255, 0), thickness=-1)
                    
                    if depth_img is not None and depth_img.size > 0:
                        valid_depths = depth_img[depth_img > 0]
                        
                        if valid_depths.size > 0:
                            # Dynamic distance capping: Find the back wall (98th percentile to ignore noise)
                            scene_max_depth = np.percentile(valid_depths, 98)
                            
                            # Arbitrary Threshold: Cap it at 5.0 meters max
                            scene_max_depth = min(scene_max_depth, 5.0)
                            scene_max_depth = max(scene_max_depth, 0.1) # prevent division by zero

                            # Stretch the colors based on the room size
                            depth_norm = np.clip(depth_img / scene_max_depth, 0, 1) * 255.0
                            depth_8u = depth_norm.astype(np.uint8)

                            depth_filtered = cv2.medianBlur(depth_8u, 5)
                            depth_colormap = cv2.applyColorMap(depth_filtered, cv2.COLORMAP_MAGMA)
                            
                            # Black out invalid pixels
                            depth_colormap[depth_img == 0] = [0, 0, 0]

                            if depth_colormap.shape[:2] != im0.shape[:2]:
                                depth_colormap = cv2.resize(depth_colormap, (im0.shape[1], im0.shape[0]))

                            side_by_side = np.hstack((im0, depth_colormap))
                        else:
                            side_by_side = np.hstack((im0, np.zeros_like(im0)))
                    else:
                        black_placeholder = np.zeros_like(im0)
                        cv2.putText(black_placeholder, "Waiting for Hardware Depth...", 
                                    (50, im0.shape[0]//2), cv2.FONT_HERSHEY_SIMPLEX, 
                                    0.8, (255, 255, 255), 2)
                        side_by_side = np.hstack((im0, black_placeholder))

                    cv2.imshow("AIBox & Depth", side_by_side)
                else:
                    cv2.imshow("AIBox", im0)
                    cv2.setWindowProperty("AIBox", cv2.WND_PROP_TOPMOST, 1)

                self.pressed_key = cv2.waitKey(1)
                trial_info = self.experiment_trial_logic(self.pressed_key)
                
                if trial_info == "break":
                    break

            if save_img:
                if self.dataset.mode == 'image':
                    cv2.imwrite(save_path, im0)
                else:
                    if vid_path[0] != save_path:
                        vid_path[0] = save_path
                        if isinstance(vid_writer[0], cv2.VideoWriter):
                            vid_writer[0].release()
                        if vid_cap:
                            fps = vid_cap.get(cv2.CAP_PROP_FPS)
                            w = int(vid_cap.get(cv2.CAP_PROP_FRAME_WIDTH))
                            h = int(vid_cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
                        else:
                            fps, w, h = 10.0, im0.shape[1], im0.shape[0]
                        save_path = str(Path(save_path).with_suffix('.mp4'))
                        vid_writer[0] = cv2.VideoWriter(save_path, cv2.VideoWriter_fourcc(*'mp4v'), fps, (w, h))
                    vid_writer[0].write(im0)


    @smart_inference_mode()
    def run(self):

        horizontal_in, vertical_in = False, False
        self.target_entered = False

        source = self.source
        save_img = not self.nosave and not source.endswith('.txt')
        is_file = Path(source).suffix[1:] in (IMG_FORMATS + VID_FORMATS)
        is_url = source.lower().startswith(('rtsp://', 'rtmp://', 'http://', 'https://'))
        webcam = source.isnumeric() or source.endswith('.streams') or (is_url and not is_file)
        screenshot = source.lower().startswith('screen')

        if is_url and is_file:
            source = check_file(source)

        base_dir = Path(self.project) / self.name
        base_dir.mkdir(parents=True, exist_ok=True)
        existing_files = list(base_dir.glob(f'{self.condition}_participant_{self.participant}_trial*'))
        max_counter = max([int(f.stem.split('_')[-1].replace('trial', '')) for f in existing_files if f.stem.split('_')[-1].replace('trial', '').isdigit()]) if existing_files else 0
        new_counter = max_counter + 1
        save_dir = base_dir / f'{self.condition}_participant_{self.participant}_trial_{new_counter}'

        # Load object detection models
        self.load_object_detector()

        # Publish available classes once models are loaded
        self._publish_available_classes()

        # Load data stream
        self.bs = 1
        view_img = check_imshow(warn=True)

        # If 'dataset' was already injected (by server_main), skip opening a new source
        if hasattr(self, 'dataset') and self.dataset is not None:
            print("Using injected Android Data Source")
        else:
            try:
                if os.path.isdir(source) or os.path.isfile(source):
                    self.dataset = LoadImages(source, img_size=480)
                else:
                    self.dataset = LoadStreams(source)
            except AssertionError:
                change_camera = input(f'Failed to open camera with index {source}. Do you want to continue with source 0? (y/n)')
                if change_camera == 'y':
                    source = '0'
                    self.dataset = LoadStreams(source, img_size=480)
                elif change_camera == 'n':
                    exit()

        self.bs = len(self.dataset)
        vid_path, vid_writer = [None] * self.bs, [None] * self.bs

        index_add = len(self.names_obj)
        labels_hand_adj = {key + index_add: value for key, value in self.names_hand.items()}
        self.master_label = self.names_obj | labels_hand_adj

        if self.run_object_tracker:
            self.load_object_tracker(max_age=self.tracker_max_age, n_init=self.tracker_n_init)
        else:
            print('SKIPPING OBJECT TRACKER INITIALIZATION')

        if self.run_depth_estimator:
            # By default, use hardware depth obtained from Android app
            if hasattr(self, 'dataset') and type(self.dataset).__name__ == 'AndroidSource':
                print('USING ANDROID HARDWARE DEPTH: SKIPPING ML DEPTH ESTIMATOR INITIALIZATION')
                self.depth_estimator = None 
            else:
                self.load_depth_estimator()
        else:
            print('SKIPPING DEPTH ESTIMATOR INITIALIZATION')

        self.warmup_model(self.model_hand)
        if self.run_object_tracker:
            self.warmup_model(self.tracker.model,'tracker')

        self.bracelet_controller.mock_navigate = True if self.mock_navigate else False

        self.experiment_loop(save_dir, save_img, index_add, vid_path, vid_writer)