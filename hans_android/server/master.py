from __future__ import annotations

import json
import queue
import sys
from pathlib import Path

import torch

# Ensure sub-package paths are available
_root = Path(__file__).resolve().parent
for _p in ['/yolov5', '/strongsort', '/unidepth', '/midas']:
    _full = str(_root) + _p
    if _full not in sys.path:
        sys.path.append(_full)

import argparse
from vision_pipeline import PipelineConfig, VisionPipeline
from experiment_runner import ExperimentRunner


# Virtual bracelet adapter (used in server / Android deployment)

class _VirtualBraceletAdapter:
    """
    Minimal FeedbackDevice that routes BraceletController output through
    VirtualBeltController (the existing WebSocket-based belt mock).
    No changes to bracelet.py or feedback_device.py required.
    """

    def __init__(self, virtual_belt, intensities: dict, navigation_type: int = 1):
        from bracelet import BraceletController
        self._bc   = BraceletController(intensities, navigation_type)
        self._belt = virtual_belt

    def connect(self) -> bool:
        return True

    def disconnect(self) -> None:
        if self._belt:
            try:
                self._belt.stop_vibration()
            except Exception:
                pass

    def update(self, ctx):
        """Proxy to navigate_hand; returns (overlapping, curr_target)."""
        return self._bc.navigate_hand(
            self._belt,
            ctx.raw_detections,
            ctx.target_class_id,
            ctx.hand_class_ids,
            ctx.depth_img,
            ctx.vibration_intensities,
            ctx.metric,
        )

    def signal_event(self, event: str) -> None:
        pass   # Virtual belt has no discrete event signals

    def stop(self) -> None:
        if self._belt:
            try:
                self._belt.stop_vibration()
            except Exception:
                pass

    def get_status(self) -> dict:
        return {'connected': True, 'type': 'virtual_bracelet', 'battery': None}

    # Passthrough so VisionPipeline can set vibrate/mock flags
    @property
    def vibrate(self) -> bool:
        return self._bc.vibrate

    @vibrate.setter
    def vibrate(self, v: bool):
        self._bc.vibrate = v

    @property
    def mock_navigate(self) -> bool:
        return self._bc.mock_navigate

    @mock_navigate.setter
    def mock_navigate(self, v: bool):
        self._bc.mock_navigate = v


# Calibration loader

def _load_calibration(participant: int) -> dict:
    baseline = 30
    default  = {'bottom': baseline, 'top': baseline,
                'left': baseline,   'right': baseline}
    try:
        path = f'results/calibration/calibration_participant_{participant}.json'
        with open(path) as f:
            return json.load(f)
    except Exception:
        print(f'[master] Calibration file not found. Using baseline ({baseline}).')
        return default


# Main orchestrator

def run_experiment_logic(
    args,
    mcp_queue        = None,
    shared_state     = None,
    custom_loader    = None,
    result_queue     = None,
    custom_belt      = None,
    latest_frame_ref = None,
    deployment_mode: bool = False,   # True → server_main.py, False → standalone
):
    """
    Build the pipeline from args, wire feedback devices, and run.

    deployment_mode=False  →  ExperimentRunner manages OpenCV window + CSV.
    deployment_mode=True   →  Pipeline runs headlessly as a background service.
    """
    participant  = args.participant
    condition    = args.condition
    mock_nav     = args.mock_navigate
    metric_depth = (not args.relative) and torch.cuda.is_available()
    use_ml_depth_fallback = getattr(args, 'depth_fallback', False)
    use_metric_depth      = getattr(args, 'metric_depth', False)

    # PipelineConfig (Change 6: FP16 auto-enabled on CUDA)
    cfg = PipelineConfig(
        participant       = participant,
        condition         = condition,
        output_path       = f'results/{condition}/',
        run_tracker       = condition in ('multiple_objects', 'depth_navigation'),
        run_depth              = True,
        use_ml_depth_fallback  = use_ml_depth_fallback,
        metric_depth      = use_metric_depth,
        nosave            = not args.save_video,
    )

    # Ensure output directory exists
    Path(cfg.output_path).mkdir(parents=True, exist_ok=True)

    # Calibration
    intensities = _load_calibration(participant)

    # Feedback devices
    devices: list = []

    if custom_belt is not None:
        # Deployment / Android mode: virtual belt routes to WebSocket
        adapter = _VirtualBraceletAdapter(custom_belt, intensities, navigation_type=1)
        adapter.mock_navigate = mock_nav
        devices.append(adapter)

    elif mock_nav:
        # Development / CI: mock device — no hardware required
        from feedback_device import MockFeedbackDevice
        devices.append(MockFeedbackDevice())

    else:
        # Hardware bracelet (physical BLE device)
        try:
            from feedback_device import BraceletAdapter
            bracelet = BraceletAdapter(intensities, navigation_type=1)
            if bracelet.connect():
                print('[master] Hardware bracelet connected.')
                devices.append(bracelet)
            else:
                print('[master] Bracelet connection failed. Aborting.', file=sys.stderr)
                sys.exit(1)
        except ImportError:
            # feedback_device.py not present — fall back to raw BraceletController
            from bracelet import connect_belt, BraceletController
            ok, belt_ctrl = connect_belt()
            if not ok:
                print('[master] Belt connection failed. Aborting.', file=sys.stderr)
                sys.exit(1)
            raw = _VirtualBraceletAdapter(belt_ctrl, intensities, navigation_type=1)
            devices.append(raw)

    # Pipeline
    pipeline = VisionPipeline(
        cfg                               = cfg,
        mcp_queue                         = mcp_queue or queue.Queue(),
        shared_state                      = shared_state,
        result_queue                      = result_queue or queue.Queue(maxsize=10),
        frame_source                      = custom_loader,
        feedback_devices                  = devices,
        participant_vibration_intensities = intensities,
        latest_frame_ref                  = latest_frame_ref or {'img': None},
    )

    # Run
    if deployment_mode:
        # Server / headless mode: pipeline runs in background threads,
        # calling thread blocks here until pipeline stops.
        pipeline.start()
        pipeline.wait()

    else:
        # Research / standalone mode: ExperimentRunner owns the main thread,
        # shows OpenCV windows, handles key presses, writes CSV.
        target_objs  = getattr(args, 'target_objs',   [])
        manual_entry = getattr(args, 'manual_entry',  True)

        runner = ExperimentRunner(
            pipeline      = pipeline,
            participant   = participant,
            condition     = condition,
            output_path   = cfg.output_path,
            target_objs   = target_objs,
            manual_entry  = manual_entry,
            mock_navigate = mock_nav,
        )
        runner.run()   # blocks until done; pipeline.stop() called inside


# Standalone entry point

class _DefaultArgs:
    """Fallback args when called from server_main without argparse."""
    participant   = 1
    condition     = 'depth_navigation'
    relative      = False
    mock_navigate = False
    save_video    = False
    target_objs   = []
    manual_entry  = True


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='HANS Tactile Guidance System')
    parser.add_argument('-p', '--participant', type=int, required=True)
    parser.add_argument('-c', '--condition', type=str, required=True,
                        choices=['grasping', 'multiple_objects', 'depth_navigation'])
    parser.add_argument('--relative',      action='store_true',
                        help='Use relative (MiDAS) depth instead of metric (UniDepth)')
    parser.add_argument('--mock_navigate', action='store_true',
                        help='Run without hardware bracelet (prints guidance to console)')
    parser.add_argument('--save_video',    action='store_true')
    parser.add_argument('--auto',          action='store_true',
                        help='Auto-mode: specify --targets; otherwise manual entry')
    parser.add_argument('--targets',       nargs='*', default=[],
                        help='Ordered list of COCO class names for auto-mode')
    parser.add_argument('--depth-fallback', action='store_true',
                        help='Enable ML-based depth estimation fallback')
    parser.add_argument('--metric-depth', action='store_true',
                        help='Use UniDepth instead of MiDaS')
    _args = parser.parse_args()
    _args.target_objs  = _args.targets
    _args.manual_entry = not _args.auto

    run_experiment_logic(_args, deployment_mode=False)
