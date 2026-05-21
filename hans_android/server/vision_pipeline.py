from __future__ import annotations

import json
import math
import queue
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np
import torch

for _p in ['/yolov5', '/strongsort', '/unidepth', '/midas']:
    if _p not in sys.path:
        sys.path.append(_p)

from labels import coco_labels
from yolov5.models.common import DetectMultiBackend
from yolov5.utils.dataloaders import IMG_FORMATS, VID_FORMATS, LoadImages, LoadStreams
from yolov5.utils.general import (
    check_imshow, non_max_suppression, scale_boxes, xyxy2xywh, xywh2xyxy,
)
from yolov5.utils.plots import Annotator, colors
from yolov5.utils.torch_utils import select_device, smart_inference_mode
from strongsort.strong_sort import StrongSORT


# Module-level helpers

def _put_replace(q: queue.Queue, item: Any) -> None:
    """Non-blocking put that silently drops the oldest item when the queue is full."""
    while True:
        try:
            q.put_nowait(item)
            return
        except queue.Full:
            try:
                q.get_nowait()
            except queue.Empty:
                pass


def _bbs_to_depth(depth: np.ndarray, bbs: list) -> list:
    """
    Fill depth field (index 7) of each bounding box using the 15th percentile
    of valid depth pixels in the central 25% crop of each bbox.
    """
    if depth is None or not bbs:
        return bbs
    for bb in bbs:
        if bb[7] != -1:
            continue
        xc, yc, w, h = int(bb[0]), int(bb[1]), int(bb[2]), int(bb[3])
        sw, sh = max(1, int(w * 0.25)), max(1, int(h * 0.25))
        x1, y1 = max(0, xc - sw), max(0, yc - sh)
        x2, y2 = min(depth.shape[1], xc + sw), min(depth.shape[0], yc + sh)
        roi = depth[y1:y2, x1:x2]
        valid = roi[roi > 0]
        bb[7] = float(np.percentile(valid, 15)) if valid.size > 0 else -1.0
    return bbs


def _validate_boxes(preds: torch.Tensor, img_h: int, img_w: int,
                    min_side: int = 2) -> torch.Tensor:
    """
    Clip bounding boxes (xyxy) to image boundaries, then remove any whose
    width or height is smaller than min_side pixels.

    This prevents StrongSORT's _get_features from slicing a zero-area crop
    out of the image and passing it to cv2.resize, which raises:
        cv2.error: !ssize.empty() in function 'cv::resize'
    """
    if len(preds) == 0:
        return preds

    # 1. Clip to image dimensions
    preds = preds.clone()
    preds[:, 0].clamp_(min=0, max=img_w - 1)   # x1
    preds[:, 1].clamp_(min=0, max=img_h - 1)   # y1
    preds[:, 2].clamp_(min=0, max=img_w - 1)   # x2  (use img_w-1 so x2>x1 is possible)
    preds[:, 3].clamp_(min=0, max=img_h - 1)   # y2

    # 2. Keep only boxes with both sides >= min_side after clipping
    w_box = preds[:, 2] - preds[:, 0]
    h_box = preds[:, 3] - preds[:, 1]
    valid = (w_box >= min_side) & (h_box >= min_side)
    return preds[valid]


# Configuration dataclass

@dataclass
class PipelineConfig:
    """Single source of truth for all pipeline parameters."""

    # --- Weights ---
    weights_obj:             str   = 'weights/yolov5s.pt'
    weights_hand:            str   = 'weights/hand_v5_optivist.pt'
    weights_tracker:         str   = 'weights/osnet_x0_25_market1501.pt'
    weights_depth:           str   = 'midas_v21_384'

    # --- Detection ---
    conf_thres:              float = 0.70
    iou_thres:               float = 0.45
    max_det:                 int   = 1000
    imgsz:                   tuple = (640, 640)
    classes_obj:             list  = field(default_factory=lambda: [1,39,40,41,42,45,46,47,58,74])
    classes_hand:            list  = field(default_factory=lambda: [0, 1])
    agnostic_nms:            bool  = False
    augment:                 bool  = False

    # --- Features ---
    run_tracker:             bool  = True
    # run_depth=True means "process depth data when available" (hardware depth
    # from the Android phone is essentially free and always preferred).
    # Set to False only if you want to completely disable all depth processing.
    run_depth:               bool  = True
    # use_ml_depth_fallback=True loads UniDepth / MiDAS and runs them when
    # no hardware depth arrives from the phone. Set to False (the default)
    # to never load these heavy models — hardware depth from Android only.
    use_ml_depth_fallback:   bool  = False
    metric_depth:            bool  = False   # True=UniDepth, False=MiDAS (only if fallback)

    # --- Tracker ---
    tracker_max_age:         int   = 60
    tracker_n_init:          int   = 5

    # --- Device / Precision ---
    device:                  str   = ''
    use_fp16:                bool  = True    # auto-disabled on CPU
    use_cuda_streams:        bool  = True    # parallel inference

    # --- Adaptive depth ---
    depth_interval_s:        float = 0.33
    depth_stable_thresh_px:  float = 15.0
    depth_force_interval_s:  float = 1.0

    # --- Camera / IO ---
    source:                  str   = '0'
    vid_stride:              int   = 1
    nosave:                  bool  = True
    save_crop:               bool  = False
    line_thickness:          int   = 2
    hide_labels:             bool  = False
    hide_conf:               bool  = False
    project:                 str   = 'results/'
    name:                    str   = 'video/'

    # --- Participant ---
    participant:             int   = 1
    condition:               str   = 'grasping'
    output_path:             str   = 'results/grasping/'


# Inter-thread data containers

@dataclass
class _PostItem:
    im0:          np.ndarray
    tensor_shape: tuple
    pred_obj:     object
    pred_hand:    object
    hw_depth:     Optional[np.ndarray]
    timestamp:    float


@dataclass
class DisplayItem:
    annotated_im0: np.ndarray
    raw_outputs:   list
    depth_img:     Optional[np.ndarray]
    fps:           float


# VisionPipeline

class VisionPipeline:
    """
    Self-contained computer-vision and haptic-navigation service.
    Has NO knowledge of experiment trials, CSV output, or OpenCV windows.
    """

    def __init__(
        self,
        cfg:                               PipelineConfig,
        mcp_queue:                         queue.Queue,
        shared_state,
        result_queue:                      queue.Queue,
        frame_source,
        feedback_devices:                  list,
        participant_vibration_intensities: dict,
        latest_frame_ref:                  dict,
    ):
        self._cfg               = cfg
        self._mcp_queue         = mcp_queue
        self._shared_state      = shared_state
        self._result_queue      = result_queue
        self._frame_source      = frame_source
        self._feedback_devices  = feedback_devices
        self._vib_intensities   = participant_vibration_intensities
        self._latest_frame_ref  = latest_frame_ref

        self._stop_event = threading.Event()
        self._post_q     = queue.Queue(maxsize=3)
        self._display_q  = queue.Queue(maxsize=2)
        self._threads: List[threading.Thread] = []

        self._stream_obj:  Optional[torch.cuda.Stream] = None
        self._stream_hand: Optional[torch.cuda.Stream] = None

        self._class_target_obj:    int   = -1
        self._active_classes_obj:  list  = list(cfg.classes_obj)
        self._grasped:             bool  = False
        self._vibration_timer             = None
        self._navigation_paused:   bool  = False
        self._specific_track_id:   int   = -1
        self._specific_bbox:       Optional[list] = None
        self._last_known_grasp_t          = 'NA'

        self._depth_img:           Optional[np.ndarray] = None
        self._last_depth_time:     float = 0.0
        self._prev_depth_target:   Optional[np.ndarray] = None

        self._memory:              dict  = {}
        self._memory_file_path:    str   = ''
        self._log_file_path:       str   = ''
        self._last_memory_save:    float = 0.0

        self._model_obj          = None
        self._model_hand         = None
        self._tracker            = None
        self._depth_estimator    = None
        self._device             = None
        self._use_fp16:    bool  = False
        self._index_add:   int   = 0
        self._master_label: dict = {}
        self._dataset            = None
        self._names_obj:   dict  = {}
        self._names_hand:  dict  = {}
        self._pt_hand:     bool  = False
        self._cmd_table:   dict  = {}

    # Public API

    @property
    def display_queue(self) -> queue.Queue:
        return self._display_q

    @property
    def is_running(self) -> bool:
        return not self._stop_event.is_set()

    @property
    def class_target_obj(self) -> int:
        return self._class_target_obj

    @property
    def bracelet_controller(self):
        for dev in self._feedback_devices:
            if hasattr(dev, '_bc'):
                return dev._bc
        return None

    def set_target(self, name: str) -> None:
        if name == 'none':
            self._full_engine_reset()
            self._class_target_obj = -1
            self._publish_target('none')
        elif name in coco_labels.values():
            self._full_engine_reset()
            self._cmd_set_target(name)

    def set_vibrate(self, state: bool) -> None:
        bc = self.bracelet_controller
        if bc:
            bc.vibrate = state
        if not state:
            for dev in self._feedback_devices:
                dev.stop()

    # Lifecycle

    def start(self) -> 'VisionPipeline':
        self._setup()
        t_inf  = threading.Thread(target=self._inference_loop,
                                   name='pipeline.inference', daemon=True)
        t_post = threading.Thread(target=self._post_loop,
                                   name='pipeline.post', daemon=True)
        self._threads = [t_inf, t_post]
        t_inf.start()
        t_post.start()
        return self

    def stop(self) -> None:
        self._stop_event.set()

    def wait(self) -> None:
        self._stop_event.wait()
        for t in self._threads:
            t.join(timeout=5.0)

    # Setup

    @smart_inference_mode()
    def _setup(self) -> None:
        cfg = self._cfg
        print('[Pipeline] Loading models ...')
        self._device = select_device(cfg.device)

        self._use_fp16 = cfg.use_fp16 and (self._device.type != 'cpu')
        if self._use_fp16:
            print('[Pipeline] FP16 precision enabled.')

        self._model_obj  = DetectMultiBackend(cfg.weights_obj,  device=self._device,
                                               dnn=False, fp16=self._use_fp16)
        self._model_hand = DetectMultiBackend(cfg.weights_hand, device=self._device,
                                               dnn=False, fp16=self._use_fp16)
        self._names_obj  = self._model_obj.names
        self._names_hand = self._model_hand.names
        self._pt_hand    = self._model_hand.pt
        self._index_add  = len(self._names_obj)
        self._master_label = self._names_obj | {
            k + self._index_add: v for k, v in self._names_hand.items()
        }

        if cfg.use_cuda_streams and torch.cuda.is_available():
            self._stream_obj  = torch.cuda.Stream(device=self._device)
            self._stream_hand = torch.cuda.Stream(device=self._device)
            print('[Pipeline] CUDA streams created — parallel inference active.')
        else:
            print('[Pipeline] Sequential inference (CPU or streams disabled).')

        if cfg.run_tracker:
            print('[Pipeline] Loading StrongSORT ...')
            self._tracker = StrongSORT(
                model_weights=cfg.weights_tracker,
                device=self._device, fp16=False,
                max_dist=0.5, max_iou_distance=0.7,
                max_age=cfg.tracker_max_age, n_init=cfg.tracker_n_init,
                nn_budget=100, mc_lambda=0.995, ema_alpha=0.9,
            )

        # Depth estimator
        #
        # Hardware depth from the Android phone (ARCore / depth sensor) is
        # always the primary source and costs nothing extra to process.
        #
        # The ML depth estimator (UniDepth / MiDAS) is large and slow.
        # It is ONLY loaded when cfg.use_ml_depth_fallback is explicitly True.
        # In normal Android operation leave it False — if hw_depth is None
        # for a frame we simply propagate depth from the previous frame instead.
        if cfg.run_depth:
            if cfg.use_ml_depth_fallback:
                print('[Pipeline] Loading ML depth estimator (fallback mode) ...')
                if cfg.metric_depth:
                    from unidepth_estimator import UniDepthEstimator
                    self._depth_estimator = UniDepthEstimator(cfg.weights_depth, self._device)
                else:
                    from midas_estimator import MidasDepthEstimator
                    self._depth_estimator = MidasDepthEstimator(cfg.weights_depth, self._device)
                print('[Pipeline] ML depth estimator loaded (will only run when hw_depth is None).')
            else:
                self._depth_estimator = None
                print('[Pipeline] Depth: hardware-only mode. ML estimator not loaded.')
        else:
            self._depth_estimator = None
            print('[Pipeline] Depth processing disabled.')

        print('[Pipeline] Warming up ...')
        bs    = 1
        dummy = (bs, 3, *cfg.imgsz)
        self._model_obj.warmup(imgsz=dummy)
        self._model_hand.warmup(imgsz=dummy)
        if cfg.run_tracker and self._tracker:
            self._tracker.model.warmup()

        if self._frame_source is not None:
            self._dataset = self._frame_source
            print('[Pipeline] Using injected frame source.')
        else:
            src = cfg.source
            is_stream = (src.isnumeric() or src.endswith('.streams') or
                         src.lower().startswith(('rtsp://', 'rtmp://', 'http://', 'https://')))
            try:
                self._dataset = (LoadStreams(src, imgsz=cfg.imgsz[0], vid_stride=cfg.vid_stride)
                                 if is_stream else
                                 LoadImages(src, img_size=cfg.imgsz[0]))
            except Exception as e:
                print(f'[Pipeline] Failed to open source {src!r}: {e}')
                raise

        self._init_memory()
        self._cmd_table = self._build_command_table()
        self._publish_available_classes()
        print('[Pipeline] Ready.')

    # Thread A: Inference

    def _inference_loop(self) -> None:
        if torch.cuda.is_available():
            torch.cuda.set_device(self._device)

        try:
            for _, (path, im, im0s, vid_cap, _) in enumerate(self._dataset):
                if self._stop_event.is_set():
                    break

                im0 = im0s[0].copy() if isinstance(im0s, list) else im0s.copy()

                hw_depth: Optional[np.ndarray] = getattr(self._dataset, 'current_depth', None)
                if hw_depth is not None and hw_depth.shape[:2] != im0.shape[:2]:
                    hw_depth = cv2.resize(hw_depth, (im0.shape[1], im0.shape[0]),
                                          interpolation=cv2.INTER_NEAREST)

                self._latest_frame_ref['img'] = im0.copy()

                tensor = self._preprocess(im)
                pred_obj, pred_hand = self._run_parallel_inference(tensor)

                _put_replace(self._post_q, _PostItem(
                    im0=im0,
                    tensor_shape=im.shape[2:],
                    pred_obj=pred_obj,
                    pred_hand=pred_hand,
                    hw_depth=hw_depth,
                    timestamp=time.time(),
                ))

        except Exception as exc:
            print(f'[Pipeline] Inference loop error: {exc}', file=sys.stderr)
            import traceback
            traceback.print_exc(file=sys.stderr)
        finally:
            self._stop_event.set()

    # Thread B: Post-processing

    def _post_loop(self) -> None:
        prev_frames:  Optional[np.ndarray] = None
        prev_outputs: np.ndarray           = np.array([])

        while not self._stop_event.is_set() or not self._post_q.empty():
            try:
                item: _PostItem = self._post_q.get(timeout=0.1)
            except queue.Empty:
                continue

            t_start = time.time()
            im0     = item.im0

            # MCP commands
            if not self._process_mcp_commands():
                self._stop_event.set()
                break

            # NMS
            cfg = self._cfg
            pred_target = non_max_suppression(
                item.pred_obj, cfg.conf_thres, cfg.iou_thres,
                self._active_classes_obj, cfg.agnostic_nms, max_det=cfg.max_det)
            pred_hand = non_max_suppression(
                item.pred_hand, cfg.conf_thres, cfg.iou_thres,
                cfg.classes_hand, cfg.agnostic_nms, max_det=cfg.max_det)

            for hd in pred_hand[0]:
                if len(hd):
                    hd[5] += self._index_add

            # Scale + merge
            preds = torch.cat((pred_target[0], pred_hand[0]), dim=0)
            if len(preds) > 0:
                preds[:, :4] = scale_boxes(
                    item.tensor_shape, preds[:, :4], im0.shape).round()

            # Validate boxes before tracker
            #
            # FIX: StrongSORT's _get_features slices image crops with:
            #   im = ori_img[y1:y2, x1:x2]
            # If a box has x1==x2 or y1==y2 (zero-area) after scale+round,
            # the slice is empty. cv2.resize then raises:
            #   cv2.error: !ssize.empty() in function 'cv::resize'
            #
            # _validate_boxes clips all boxes to frame boundaries first
            # (fixing out-of-frame detections), then drops any whose resulting
            # width or height is < 2 px. This is always safe because a 1×1
            # pixel crop carries no meaningful ReID information anyway.
            img_h, img_w = im0.shape[:2]
            if len(preds) > 0:
                preds = _validate_boxes(preds, img_h, img_w, min_side=2)

            # Tracking
            curr_frames = im0
            hand_ids    = [h + self._index_add for h in cfg.classes_hand]
            if cfg.run_tracker and self._tracker is not None:
                if prev_frames is not None:
                    self._tracker.tracker.camera_update(prev_frames, curr_frames)
                if len(preds) > 0:
                    xywhs = xyxy2xywh(preds[:, :4])
                    with torch.no_grad():
                        outputs = self._tracker.update(
                            xywhs.cpu(), preds[:, 4].cpu(), preds[:, 5].cpu(), im0)
                    outputs = [o for o in outputs
                               if o[5] in self._active_classes_obj + hand_ids]
                else:
                    outputs = []
            else:
                if len(preds) > 0:
                    arr = np.array(preds.cpu())
                    arr = np.insert(arr, 4, -1, axis=1)
                    arr[:, [5, 6]] = arr[:, [6, 5]]
                    outputs = list(arr)
                else:
                    outputs = []

            outputs = [np.concatenate((xyxy2xywh(bb[:4]), bb[4:])) for bb in outputs]
            outputs = [np.append(bb, -1.0) for bb in outputs]

            # Camera-shake frame drop
            if prev_frames is not None and len(outputs) > 0:
                g1 = cv2.cvtColor(curr_frames, cv2.COLOR_BGR2GRAY)
                g2 = cv2.cvtColor(prev_frames, cv2.COLOR_BGR2GRAY)
                if g1.shape != g2.shape:
                    # Resize depth to match RGB - TO UPDATE
                    g2 = cv2.resize(g2, (g1.shape[1], g1.shape[0]))
                if np.mean(cv2.absdiff(g1, g2)) > 30:
                    outputs = []

            prev_frames = curr_frames

            # Depth
            outputs, depth_img = self._resolve_depth(
                outputs, im0, item.hw_depth, prev_outputs)
            prev_outputs = np.array(outputs) if outputs else np.array([])

            # Publish + opportunistic lock
            self._publish_visible_objects(outputs, im0.shape)
            self._opportunistic_lock(outputs)

            # Haptic engine
            curr_target = None
            if self._class_target_obj != -1 and not self._navigation_paused:
                curr_target = self._run_haptic_engine(outputs)

            # WebSocket
            self._push_result_queue(outputs, im0.shape)

            # Display queue
            fps = 1.0 / max(time.time() - t_start, 1e-6)
            ann = self._annotate_frame(im0, outputs, curr_target, fps)
            _put_replace(self._display_q, DisplayItem(
                annotated_im0=ann,
                raw_outputs=list(outputs),
                depth_img=depth_img,
                fps=fps,
            ))

            # Memory flush
            self._periodic_memory_save()

    # Preprocessing + inference (no smart_inference_mode — see prior fix note)

    def _preprocess(self, im: np.ndarray) -> torch.Tensor:
        tensor = torch.from_numpy(im).to(self._device)
        tensor = tensor.half() if self._use_fp16 else tensor.float()
        tensor /= 255.0
        if tensor.ndim == 3:
            tensor = tensor.unsqueeze(0)
        return tensor

    def _run_parallel_inference(self, tensor: torch.Tensor) -> Tuple[Any, Any]:
        if self._stream_obj is not None:
            with torch.no_grad():
                with torch.cuda.stream(self._stream_obj):
                    pred_obj  = self._model_obj(tensor, augment=self._cfg.augment)
                with torch.cuda.stream(self._stream_hand):
                    pred_hand = self._model_hand(tensor, augment=self._cfg.augment)
                self._stream_obj.synchronize()
                self._stream_hand.synchronize()
        else:
            with torch.no_grad():
                pred_obj  = self._model_obj(tensor, augment=self._cfg.augment)
                pred_hand = self._model_hand(tensor, augment=self._cfg.augment)
        return pred_obj, pred_hand

    # Depth resolution

    def _should_estimate_depth(self, current_target_bbox: Optional[np.ndarray]) -> bool:
        """Three-gate decision for ML depth (only runs when use_ml_depth_fallback=True)."""
        now     = time.time()
        elapsed = now - self._last_depth_time
        if elapsed < self._cfg.depth_interval_s:
            return False
        if elapsed >= self._cfg.depth_force_interval_s:
            return True
        if self._prev_depth_target is not None and current_target_bbox is not None:
            dx = current_target_bbox[0] - self._prev_depth_target[0]
            dy = current_target_bbox[1] - self._prev_depth_target[1]
            if math.hypot(dx, dy) < self._cfg.depth_stable_thresh_px:
                return False
        return True

    def _resolve_depth(self,
                       outputs: list,
                       im0: np.ndarray,
                       hw_depth: Optional[np.ndarray],
                       prev_outputs: np.ndarray) -> Tuple[list, Optional[np.ndarray]]:
        """
        Depth priority:
          1. Hardware depth from Android phone  — always used when present (free).
          2. ML depth estimator                 — only when use_ml_depth_fallback=True
                                                  AND hw_depth is None.
          3. Previous-frame propagation via track_id — free, always attempted last.
        """
        if not self._cfg.run_depth:
            return outputs, None

        # 1. Hardware path (ARCore / phone depth sensor)
        if hw_depth is not None:
            self._depth_img = hw_depth
            return _bbs_to_depth(self._depth_img, outputs), self._depth_img

        # 2. ML fallback (optional, heavy, disabled by default)
        if self._depth_estimator is not None:
            current_target = next(
                (bb for bb in outputs if bb[5] == self._class_target_obj), None)
            if self._should_estimate_depth(current_target):
                with torch.no_grad():
                    self._depth_img, _ = self._depth_estimator.predict_depth(im0)
                self._last_depth_time   = time.time()
                self._prev_depth_target = (np.array(current_target[:4])
                                           if current_target is not None else None)
                return _bbs_to_depth(self._depth_img, outputs), self._depth_img

        # 3. Propagate from previous frame via track_id (always free)
        if self._depth_img is not None and prev_outputs.size > 0:
            for bb in outputs:
                key   = bb[4] if bb[4] != -1 else None
                match = (prev_outputs[prev_outputs[:, 4] == key]
                         if key is not None
                         else prev_outputs[prev_outputs[:, 5] == bb[5]])
                bb[7] = match[0][7] if match.size > 0 else -1.0

        return outputs, self._depth_img

    # Haptic engine

    def _run_haptic_engine(self, outputs: list) -> Optional[np.ndarray]:
        if self._grasped:
            if self._vibration_timer is None:
                self._vibration_timer = time.time()
            elif self._vibration_timer > 0 and time.time() - self._vibration_timer > 1.5:
                for dev in self._feedback_devices:
                    dev.stop()
                self._vibration_timer = -1
            return None

        specific_id = self._specific_track_id
        if specific_id != -1:
            det = next((d for d in outputs
                        if d[4] == specific_id and d[5] == self._class_target_obj), None)
            if det is None and self._specific_bbox:
                xc0, yc0, w0, h0 = self._specific_bbox
                best_dist, det = float('inf'), None
                for d in outputs:
                    if d[5] == self._class_target_obj:
                        dist = math.hypot(d[0] - xc0, d[1] - yc0)
                        if dist < max(w0, h0) * 1.5 and dist < best_dist:
                            best_dist, det = dist, d
                if det is not None:
                    self._specific_track_id = int(det[4])
                    specific_id = self._specific_track_id
                    print(f'[Pipeline] Specific ID recovered → {specific_id}')
            if det is not None:
                self._specific_bbox = list(map(float, det[:4]))

        hand_ids = [h + self._index_add for h in self._cfg.classes_hand]
        filtered = [
            d for d in outputs
            if d[5] in hand_ids
            or (d[5] == self._class_target_obj
                and (specific_id == -1 or d[4] == specific_id))
        ]

        depth_for_haptics = None
        if self._depth_img is not None:
            depth_for_haptics = self._depth_img.copy()
            depth_for_haptics[depth_for_haptics == 0] = 10.0

        try:
            from feedback_device import NavigationContext
        except ImportError:
            from dataclasses import make_dataclass
            NavigationContext = make_dataclass('NavigationContext', [
                'raw_detections', 'target_class_id', 'hand_class_ids',
                'depth_img', 'vibration_intensities', 'metric'])

        ctx = NavigationContext(
            raw_detections=filtered,
            target_class_id=self._class_target_obj,
            hand_class_ids=hand_ids,
            depth_img=depth_for_haptics,
            vibration_intensities=self._vib_intensities,
            metric=self._cfg.metric_depth,
        )

        curr_target = None
        for device in self._feedback_devices:
            result = device.update(ctx)
            if isinstance(result, tuple):
                overlapping, target = result
                if overlapping:
                    self._grasped = True
                    for dev in self._feedback_devices:
                        if not hasattr(dev, '_bc'):
                            dev.signal_event('grasped')
                if target is not None and curr_target is None:
                    curr_target = target
            elif result is not None and curr_target is None:
                curr_target = result

        bc = self.bracelet_controller
        if bc and bc.grasping_time != 'NA' and bc.grasping_time != self._last_known_grasp_t:
            self._last_known_grasp_t = bc.grasping_time
            self._grasped = True

        return curr_target

    def _full_engine_reset(self) -> None:
        self._specific_track_id  = -1
        self._specific_bbox      = None
        self._grasped            = False
        self._vibration_timer    = None
        self._last_known_grasp_t = 'NA'
        for dev in self._feedback_devices:
            if hasattr(dev, '_bc'):
                bc = dev._bc
                bc.prev_target     = None
                bc.frozen          = False
                bc.was_guiding     = False
                bc.grasping_time   = 'NA'
                bc.navigation_time = 'NA'
                bc.freezing_time   = 'NA'

    def _opportunistic_lock(self, outputs: list) -> None:
        if self._class_target_obj != -1:
            return
        if self._memory.get('list_mode') != 'unordered':
            return
        for *_, obj_id, cls, conf, depth in outputs:
            name = self._master_label.get(int(cls), '')
            if name in self._memory.get('target_list', []):
                self._full_engine_reset()
                self._cmd_set_target(name)
                print(f'[Pipeline] Opportunistic lock: {name}')
                break

    # MCP command dispatch

    _RESET_CMDS = frozenset({
        'set_target', 'set_target_list', 'mark_grasped',
        'clear_list', 'set_specific_target',
    })

    def _build_command_table(self) -> dict:
        return {
            'stop':                self._cmd_stop,
            'set_target':          self._cmd_set_target,
            'pause_navigation':    self._cmd_pause,
            'resume_navigation':   self._cmd_resume,
            'adjust_intensity':    self._cmd_adjust_intensity,
            'set_target_list':     self._cmd_set_target_list,
            'mark_grasped':        self._cmd_mark_grasped,
            'log_interaction':     self._cmd_log_interaction,
            'update_preferences':  self._cmd_update_preferences,
            'clear_list':          self._cmd_clear_list,
            'set_specific_target': self._cmd_set_specific_target,
        }

    def _process_mcp_commands(self) -> bool:
        if not self._mcp_queue:
            return True
        try:
            for _ in range(16):
                cmd         = self._mcp_queue.get_nowait()
                instruction = cmd.get('instruction', '')
                value       = cmd.get('value', '')
                print(f'[Pipeline] CMD {instruction!r}', file=sys.stderr, flush=True)
                if instruction in self._RESET_CMDS:
                    self._full_engine_reset()
                handler = self._cmd_table.get(instruction)
                if handler:
                    if handler(value) is False:
                        return False
        except queue.Empty:
            pass
        return True

    def _cmd_stop(self, _):
        self._stop_event.set()
        return False

    def _cmd_set_target(self, value: str):
        if value not in coco_labels.values():
            print(f'[Pipeline] Unknown label: {value!r}', file=sys.stderr)
            return
        new_id = next(k for k, v in coco_labels.items() if v == value)
        self._class_target_obj   = new_id
        self._active_classes_obj = [new_id]
        for dev in self._feedback_devices:
            if hasattr(dev, '_bc'):
                bc              = dev._bc
                bc.vibrate      = True
                bc.was_guiding  = False
                bc.searching    = True
                bc.prev_target  = None
                bc.prev_hand    = None
        self._publish_target(value)
        print(f'[Pipeline] Target → {value} (ID {new_id})', file=sys.stderr)

    def _cmd_pause(self, _):
        self._navigation_paused = True
        for dev in self._feedback_devices:
            dev.stop()
        print('[Pipeline] Navigation paused.', file=sys.stderr)

    def _cmd_resume(self, _):
        self._navigation_paused = False
        print('[Pipeline] Navigation resumed.', file=sys.stderr)

    def _cmd_adjust_intensity(self, value: str):
        motor, intensity_str = value.split(':')
        intensity = int(intensity_str)
        self._vib_intensities[motor]                       = intensity
        self._memory.setdefault('calibration', {})[motor] = intensity
        self._save_memory_async()
        print(f'[Pipeline] {motor} intensity → {intensity}', file=sys.stderr)

    def _cmd_set_target_list(self, value: str):
        data = json.loads(value)
        self._memory['target_list'] = data['targets']
        self._memory['list_mode']   = data['mode']
        self._save_memory_async()
        if self._shared_state:
            self._shared_state.set_target_list_state(data['targets'], data['mode'])
        if data['mode'] == 'ordered' and data['targets']:
            first = data['targets'][0]
            if first in coco_labels.values():
                self._cmd_set_target(first)
        else:
            self._class_target_obj = -1
            self._publish_target('none')

    def _cmd_mark_grasped(self, _):
        curr = self._shared_state.get_target() if self._shared_state else 'none'
        if curr == 'none':
            return
        grasped_list = self._memory.setdefault('grasped_objects', [])
        if curr not in grasped_list:
            grasped_list.append(curr)
        target_list = self._memory.get('target_list', [])
        if curr in target_list:
            target_list.remove(curr)
        self._save_memory_async()
        if self._shared_state:
            self._shared_state.set_target_list_state(
                target_list, self._memory.get('list_mode', 'ordered'))
        if not target_list:
            self._class_target_obj = -1
            self._publish_target('none')
            for dev in self._feedback_devices:
                dev.stop()
        elif self._memory.get('list_mode') == 'ordered':
            self._cmd_set_target(target_list[0])
        else:
            self._class_target_obj = -1
            self._publish_target('none')
            for dev in self._feedback_devices:
                dev.stop()

    def _cmd_log_interaction(self, value: str):
        data  = json.loads(value)
        entry = {
            'time':         datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'user_command': data.get('user_text', ''),
            'ai_response':  data.get('ai_response', ''),
        }
        path = self._log_file_path
        threading.Thread(
            target=lambda: open(path, 'a', encoding='utf-8').write(
                json.dumps(entry) + '\n'),
            daemon=True
        ).start()

    def _cmd_update_preferences(self, value: str):
        data  = json.loads(value)
        prefs = self._memory.setdefault('preferences', {})
        for k in ('speech_speed', 'verbosity', 'battery_saver', 'play_welcome_message'):
            if k in data:
                prefs[k] = data[k]
        self._save_memory_async()
        if self._shared_state:
            self._shared_state.set_preferences(prefs)

    def _cmd_clear_list(self, _):
        self._memory['target_list'] = []
        self._save_memory_async()
        if self._shared_state:
            self._shared_state.set_target_list_state([], 'ordered')
        self._class_target_obj = -1
        self._publish_target('none')
        for dev in self._feedback_devices:
            dev.stop()
        print('[Pipeline] Target list cleared.', file=sys.stderr)

    def _cmd_set_specific_target(self, value: str):
        data                     = json.loads(value)
        cls_name                 = data['class_name']
        self._specific_track_id  = data['track_id']
        self._specific_bbox      = data.get('bbox')
        new_id                   = next(k for k, v in coco_labels.items() if v == cls_name)
        self._class_target_obj   = new_id
        self._active_classes_obj = [new_id]
        label                    = f"{data.get('description', '')} {cls_name}".strip()
        self._publish_target(label)
        print(f'[Pipeline] Specific lock: {cls_name} (ID={self._specific_track_id})')

    # State publishers

    def _publish_target(self, name: str) -> None:
        if self._shared_state:
            self._shared_state.set_target(name)

    def _publish_visible_objects(self, outputs: list, img_shape: tuple) -> None:
        if not self._shared_state:
            return
        img_h, img_w = img_shape[:2]
        now_str = datetime.now().strftime('%I:%M:%S %p')
        visible, new_map = [], {}
        for item in outputs:
            cls  = int(item[5])
            name = self._master_label.get(cls)
            if name is None:
                continue
            conf      = float(item[6])
            track_id  = int(item[4])
            depth     = float(item[7]) if len(item) > 7 else -1.0
            x_norm    = float(item[0]) / img_w
            y_norm    = float(item[1]) / img_h
            x_str     = 'Left'   if x_norm < 0.33 else ('Right'  if x_norm > 0.66 else 'Center')
            y_str     = 'Top'    if y_norm < 0.33 else ('Bottom' if y_norm > 0.66 else 'Middle')
            depth_str = f'{depth:.2f}m' if depth > 0 else 'unknown depth'
            loc = f'{y_str} {x_str}, {depth_str}'
            visible.append({'name': name, 'confidence': conf, 'track_id': track_id,
                            'depth': depth, 'location': loc,
                            'bbox': [float(item[0]), float(item[1]),
                                     float(item[2]), float(item[3])]})
            if conf > 0.60:
                new_map[name] = {'location': loc, 'last_seen': now_str}
        self._shared_state.set_visible_objects(visible)
        if new_map:
            self._shared_state.update_world_map(new_map)
            self._memory.setdefault('world_map', {}).update(new_map)

    def _publish_available_classes(self) -> None:
        if self._shared_state:
            self._shared_state.set_available_classes(list(coco_labels.values()))

    # Memory management

    def _init_memory(self) -> None:
        cfg      = self._cfg
        root     = Path(__file__).resolve().parent
        mem_file = root / 'results' / f'memory_participant_{cfg.participant}.json'
        log_file = root / 'results' / f'interaction_log_participant_{cfg.participant}.jsonl'
        mem_file.parent.mkdir(parents=True, exist_ok=True)
        self._memory_file_path = str(mem_file)
        self._log_file_path    = str(log_file)

        if mem_file.exists():
            with open(self._memory_file_path) as f:
                self._memory = json.load(f)
            if self._shared_state:
                self._shared_state.set_memory_existed(True)
            self._vib_intensities = self._memory.get('calibration', self._vib_intensities)
            print(f'[Pipeline] Memory loaded: {self._memory_file_path}')
        else:
            if self._shared_state:
                self._shared_state.set_memory_existed(False)
            self._memory = {
                'target_list': [], 'list_mode': 'ordered', 'grasped_objects': [],
                'calibration': dict(self._vib_intensities),
                'preferences': {'speech_speed': 'normal', 'verbosity': 'normal',
                                'battery_saver': False, 'play_welcome_message': True},
                'command_history': [],
            }
            print('[Pipeline] Fresh memory created.')

        if self._shared_state:
            prefs = self._memory.setdefault('preferences', {})
            self._shared_state.set_preferences(prefs)
            self._shared_state.set_target_list_state(
                self._memory.get('target_list', []),
                self._memory.get('list_mode', 'ordered'))
            self._shared_state.update_world_map(self._memory.get('world_map', {}))

        tlist = self._memory.get('target_list', [])
        if tlist and self._memory.get('list_mode') == 'ordered':
            first = tlist[0]
            if first in coco_labels.values():
                self._class_target_obj   = next(k for k, v in coco_labels.items() if v == first)
                self._active_classes_obj = [self._class_target_obj]
                self._publish_target(first)
                print(f'[Pipeline] Resumed target: {first}')
        else:
            self._publish_target('none')

    def _save_memory_async(self) -> None:
        snapshot = json.dumps(self._memory, indent=4)
        path     = self._memory_file_path
        threading.Thread(
            target=lambda: open(path, 'w').write(snapshot),
            daemon=True
        ).start()

    def _periodic_memory_save(self) -> None:
        if time.time() - self._last_memory_save >= 5.0:
            self._save_memory_async()
            self._last_memory_save = time.time()

    # Rendering

    def _annotate_frame(self, im0: np.ndarray, outputs: list,
                         curr_target, fps: float) -> np.ndarray:
        cfg       = self._cfg
        annotator = Annotator(im0.copy(), line_width=cfg.line_thickness,
                               example=str(self._names_obj))
        for *xywh, obj_id, cls, conf, depth in outputs:
            xyxy      = xywh2xyxy(np.array(xywh))
            obj_class = int(cls)
            is_target = (curr_target is not None and
                         np.array_equal(curr_target, [*xywh, obj_id, cls, conf, depth]))
            parts = []
            if not cfg.hide_labels:
                parts.append('Target ' if is_target
                             else f'{self._master_label.get(obj_class, str(obj_class))} ')
            if not cfg.hide_conf:
                parts.append(f'{conf * 100:.0f}% ')
            if cfg.run_tracker:
                parts.append(f'ID:{int(obj_id)} ')
            if cfg.run_depth and depth != -1.0:
                parts.append(f'{depth:.2f}m ')
            labelcolor = (0, 0, 0) if is_target else colors(obj_class, True)
            annotator.cv_font = cv2.FONT_HERSHEY_SIMPLEX
            annotator.tf      = max(annotator.lw - 1, 1)
            annotator.sf      = annotator.lw / 3
            annotator.box_label(xyxy, ''.join(parts), color=labelcolor)
        result = annotator.result()
        cv2.putText(result, f'FPS:{int(fps)}', (20, 70),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 255, 0), 1)
        return result

    def depth_side_by_side(self, im0: np.ndarray, depth_img: np.ndarray) -> np.ndarray:
        valid = depth_img[depth_img > 0]
        if valid.size > 0:
            scene_max = min(float(np.percentile(valid, 98)), 5.0)
            scene_max = max(scene_max, 0.1)
            norm = np.clip(depth_img / scene_max, 0, 1) * 255.0
            d8   = cv2.medianBlur(norm.astype(np.uint8), 5)
            cmap = cv2.applyColorMap(d8, cv2.COLORMAP_MAGMA)
            cmap[depth_img == 0] = [0, 0, 0]
            if cmap.shape[:2] != im0.shape[:2]:
                cmap = cv2.resize(cmap, (im0.shape[1], im0.shape[0]))
        else:
            cmap = np.zeros_like(im0)
        return np.hstack((im0, cmap))

    def _push_result_queue(self, outputs: list, img_shape: tuple) -> None:
        if not self._result_queue or self._result_queue.full():
            return
        img_h, img_w = img_shape[:2]
        boxes = [{'x': float(d[0]) / img_w, 'y': float(d[1]) / img_h,
                   'w': float(d[2]) / img_w, 'h': float(d[3]) / img_h,
                   'label': self._master_label.get(int(d[5]), '?')}
                 for d in outputs]
        try:
            self._result_queue.put_nowait(boxes)
        except queue.Full:
            pass
