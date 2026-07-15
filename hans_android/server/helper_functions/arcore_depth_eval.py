#!/usr/bin/env python3
"""
ARCore depth accuracy evaluation tool for HANS.

Run this BEFORE integrating belt/bracelet handoff logic. It measures how
accurate ARCore depth is on your specific phone, at the distances you care
about (recommended: 0.5 m – 3.0 m).

Usage
-----
1. Set SERVER_IP in hans_android/.env and build withArcoreDebug on the phone.
2. Start this script (from hans_android/server):
       python helper_functions/arcore_depth_eval.py
3. Open the HANS app on the phone (withArcore flavor). It connects to ws://<IP>:8000/ws/video
4. Follow the on-screen protocol.

Controls
--------
  SPACE   Log a sample (prompts for tape-measured ground-truth distance in metres)
  c       Toggle crosshair position (centre / click-to-set — click in window)
  r       Reset all logged samples
  s       Save CSV + print error statistics
  q       Quit

Output
------
  results/arcore_depth_eval_<timestamp>.csv
"""
from __future__ import annotations

import argparse
import csv
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

# Ensure helper_functions is importable when launched from server/
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import cv2
import numpy as np
import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from depth_codec import (
    sample_bbox_depth,
    sample_depth_at_point,
    unpack_multiplexed_frame,
)

RESULTS_DIR = Path(__file__).resolve().parents[1] / 'results'

# Shared state between WebSocket receiver and OpenCV UI thread
_frame_lock = threading.Lock()
_latest_rgb: np.ndarray | None = None
_latest_depth: np.ndarray | None = None
_crosshair = {'x': None, 'y': None}  # None → use image centre
_samples: list[dict] = []
_stop_event = threading.Event()

app = FastAPI()


@app.websocket('/ws/video')
async def video_ws(websocket: WebSocket):
    global _latest_rgb, _latest_depth
    await websocket.accept()
    print('Phone connected — move the device slowly until depth map appears.')

    try:
        while not _stop_event.is_set():
            data = await websocket.receive_bytes()
            frame, depth_map = unpack_multiplexed_frame(data)
            if frame is None:
                continue
            with _frame_lock:
                _latest_rgb = frame
                _latest_depth = depth_map
    except WebSocketDisconnect:
        print('Phone disconnected.')
    except Exception as exc:
        print(f'WebSocket error: {exc}')


def _depth_colormap(depth_m: np.ndarray) -> np.ndarray:
    valid = depth_m[depth_m > 0]
    if valid.size == 0:
        return np.zeros((*depth_m.shape, 3), dtype=np.uint8)
    scene_max = min(float(np.percentile(valid, 98)), 5.0)
    scene_max = max(scene_max, 0.1)
    norm = np.clip(depth_m / scene_max, 0, 1) * 255.0
    d8 = cv2.medianBlur(norm.astype(np.uint8), 5)
    cmap = cv2.applyColorMap(d8, cv2.COLORMAP_MAGMA)
    cmap[depth_m == 0] = [0, 0, 0]
    return cmap


def _compute_stats(samples: list[dict]) -> dict:
    if not samples:
        return {}
    errors = [s['error_m'] for s in samples]
    abs_errors = [abs(e) for e in errors]
    gt = [s['ground_truth_m'] for s in samples]
    est = [s['estimated_m'] for s in samples]
    return {
        'n': len(samples),
        'mae_m': float(np.mean(abs_errors)),
        'rmse_m': float(np.sqrt(np.mean(np.square(errors)))),
        'bias_m': float(np.mean(errors)),  # positive = overestimate
        'mape_pct': float(np.mean([abs(e) / g * 100 for e, g in zip(errors, gt) if g > 0])),
        'min_gt_m': float(min(gt)),
        'max_gt_m': float(max(gt)),
    }


def _save_csv(samples: list[dict]) -> Path:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    path = RESULTS_DIR / f'arcore_depth_eval_{ts}.csv'
    fields = [
        'timestamp', 'ground_truth_m', 'estimated_m', 'error_m', 'abs_error_m',
        'method', 'crosshair_x', 'crosshair_y', 'valid_ratio', 'p15_m', 'median_m',
    ]
    with path.open('w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in samples:
            writer.writerow({k: row.get(k, '') for k in fields})
    return path


def _mouse_callback(event, x, y, _flags, _param):
    if event == cv2.EVENT_LBUTTONDOWN:
        # Only accept clicks on the RGB panel (left half)
        with _frame_lock:
            rgb = _latest_rgb
        if rgb is not None and x < rgb.shape[1]:
            _crosshair['x'] = x
            _crosshair['y'] = y


def _run_ui(host: str, port: int):
    print(__doc__)
    print(f'Listening on ws://{host}:{port}/ws/video\n')

    cv2.namedWindow('ARCore Depth Eval', cv2.WINDOW_NORMAL)
    cv2.setMouseCallback('ARCore Depth Eval', _mouse_callback)

    while not _stop_event.is_set():
        with _frame_lock:
            rgb = None if _latest_rgb is None else _latest_rgb.copy()
            depth = None if _latest_depth is None else _latest_depth.copy()

        if rgb is None:
            blank = np.zeros((480, 640, 3), dtype=np.uint8)
            cv2.putText(blank, 'Waiting for phone...', (120, 240),
                        cv2.FONT_HERSHEY_SIMPLEX, 1.0, (200, 200, 200), 2)
            cv2.imshow('ARCore Depth Eval', blank)
            if cv2.waitKey(100) & 0xFF == ord('q'):
                break
            continue

        h, w = rgb.shape[:2]
        cx = _crosshair['x'] if _crosshair['x'] is not None else w // 2
        cy = _crosshair['y'] if _crosshair['y'] is not None else h // 2

        display = rgb.copy()
        if depth is not None:
            if depth.shape[:2] != rgb.shape[:2]:
                depth = cv2.resize(depth, (w, h), interpolation=cv2.INTER_NEAREST)
            side = _depth_colormap(depth)
            display = np.hstack((rgb, side))

            point_stats = sample_depth_at_point(depth, cx, cy, radius=8)
            # Simulate a ~20 cm object bbox at crosshair (typical grasp target size at 1–2 m)
            bbox_depth = sample_bbox_depth(depth, cx, cy, w=80, h=80)

            cv2.drawMarker(display, (cx, cy), (0, 255, 0), cv2.MARKER_CROSS, 20, 2)
            if cx + w < display.shape[1]:
                cv2.drawMarker(display, (cx + w, cy), (0, 255, 0), cv2.MARKER_CROSS, 20, 2)

            lines = [
                f'RGB {w}x{h}  Depth {depth.shape[1]}x{depth.shape[0]}  aligned: {depth.shape[:2] == rgb.shape[:2]}',
                f'Crosshair ({cx},{cy})  valid: {point_stats["valid_ratio"]:.0%}',
                f'  median: {point_stats["median_m"]:.3f} m' if point_stats['median_m'] else '  median: N/A',
                f'  p15:    {point_stats["p15_m"]:.3f} m' if point_stats['p15_m'] else '  p15:    N/A',
                f'Bbox sim (80px, p15): {bbox_depth:.3f} m' if bbox_depth > 0 else 'Bbox sim: N/A',
                'Click object on LEFT (RGB) panel. Rebuild app if aligned=False.',
                f'Samples: {len(_samples)}  |  SPACE=log  s=save  r=reset  q=quit',
            ]
            y0 = 30
            for i, line in enumerate(lines):
                cv2.putText(display, line, (10, y0 + i * 28),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.65, (0, 255, 0), 2)
        else:
            cv2.putText(display, 'No depth yet — move phone to initialise ARCore',
                        (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)

        cv2.imshow('ARCore Depth Eval', display)
        key = cv2.waitKey(30) & 0xFF

        if key == ord('q'):
            break
        if key == ord('r'):
            _samples.clear()
            print('Samples cleared.')
        if key == ord('c'):
            _crosshair['x'] = None
            _crosshair['y'] = None
            print('Crosshair reset to centre.')
        if key == ord('s'):
            if not _samples:
                print('No samples to save.')
            else:
                path = _save_csv(_samples)
                stats = _compute_stats(_samples)
                print(f'\nSaved {len(_samples)} samples → {path}')
                print(f"  MAE:  {stats['mae_m']:.3f} m")
                print(f"  RMSE: {stats['rmse_m']:.3f} m")
                print(f"  Bias: {stats['bias_m']:+.3f} m  (+ = ARCore reads farther than truth)")
                print(f"  MAPE: {stats['mape_pct']:.1f} %")
        if key == ord(' '):
            if depth is None or point_stats.get('median_m') is None:
                print('Cannot log — no valid depth at crosshair.')
                continue
            try:
                gt_str = input('\nTape-measured distance (metres), e.g. 1.50: ').strip()
                gt = float(gt_str)
            except ValueError:
                print('Invalid input, skipped.')
                continue

            est = point_stats['p15_m']  # matches vision_pipeline bbox method
            err = est - gt
            row = {
                'timestamp': datetime.now().isoformat(timespec='seconds'),
                'ground_truth_m': gt,
                'estimated_m': est,
                'error_m': err,
                'abs_error_m': abs(err),
                'method': 'crosshair_p15',
                'crosshair_x': cx,
                'crosshair_y': cy,
                'valid_ratio': point_stats['valid_ratio'],
                'p15_m': point_stats['p15_m'],
                'median_m': point_stats['median_m'],
            }
            _samples.append(row)
            print(f'  Logged: truth={gt:.3f} m  est={est:.3f} m  error={err:+.3f} m')

    _stop_event.set()
    cv2.destroyAllWindows()


def main():
    parser = argparse.ArgumentParser(description='Evaluate ARCore depth accuracy for HANS')
    parser.add_argument('--host', default='0.0.0.0', help='Bind address (default: 0.0.0.0)')
    parser.add_argument('--port', type=int, default=8000, help='Port (default: 8000, same as HANS)')
    args = parser.parse_args()

    ui_thread = threading.Thread(target=_run_ui, args=(args.host, args.port), daemon=True)
    ui_thread.start()

    try:
        uvicorn.run(app, host=args.host, port=args.port, log_level='warning')
    finally:
        _stop_event.set()
        ui_thread.join(timeout=2)


if __name__ == '__main__':
    main()