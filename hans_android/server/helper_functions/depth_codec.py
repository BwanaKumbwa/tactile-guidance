"""Shared ARCore depth encode/decode utilities.

The Android client packs ARCore DEPTH16 (millimetres) into a PNG:
  R = high byte, G = low byte, B = 0
The server reverses this and converts to metres.
"""
from __future__ import annotations

import numpy as np
import cv2


def decode_arcore_depth_png(depth_bytes: bytes) -> np.ndarray | None:
    """Decode multiplexed ARCore depth PNG bytes to a float32 depth map in metres."""
    if not depth_bytes:
        return None

    depth_arr = np.frombuffer(depth_bytes, np.uint8)
    depth_bgr = cv2.imdecode(depth_arr, cv2.IMREAD_COLOR)
    if depth_bgr is None:
        return None

    # OpenCV loads as BGR: index 2 = red (high byte), index 1 = green (low byte)
    depth_mm = (depth_bgr[:, :, 2].astype(np.float32) * 256.0) + depth_bgr[:, :, 1].astype(np.float32)
    depth_m = depth_mm / 1000.0
    return cv2.rotate(depth_m, cv2.ROTATE_90_CLOCKWISE)


def encode_depth_m_to_png(depth_m: np.ndarray) -> bytes:
    """Encode a depth map (metres) into the same PNG format the Android client uses."""
    depth_mm = np.clip(depth_m * 1000.0, 0, 65535).astype(np.uint16)
    r = (depth_mm >> 8).astype(np.uint8)
    g = (depth_mm & 0xFF).astype(np.uint8)
    b = np.zeros_like(r)
    bgr = cv2.merge([b, g, r])
    ok, buf = cv2.imencode('.png', bgr)
    if not ok:
        raise RuntimeError('Failed to encode depth PNG')
    return buf.tobytes()


def unpack_multiplexed_frame(data: bytes) -> tuple[np.ndarray | None, np.ndarray | None]:
    """Unpack the binary WebSocket payload sent by the Android app."""
    if len(data) < 4:
        return None, None

    rgb_len = int.from_bytes(data[0:4], byteorder='big')
    if len(data) < 4 + rgb_len:
        return None, None

    rgb_bytes = data[4:4 + rgb_len]
    depth_bytes = data[4 + rgb_len:]

    frame = cv2.imdecode(np.frombuffer(rgb_bytes, np.uint8), cv2.IMREAD_COLOR)
    depth_map = decode_arcore_depth_png(depth_bytes)

    if frame is not None:
        frame = cv2.rotate(frame, cv2.ROTATE_90_CLOCKWISE)

    return frame, depth_map


def sample_depth_at_point(depth_m: np.ndarray, x: int, y: int, radius: int = 5) -> dict:
    """Sample depth statistics in a square window around (x, y)."""
    h, w = depth_m.shape[:2]
    x1, x2 = max(0, x - radius), min(w, x + radius + 1)
    y1, y2 = max(0, y - radius), min(h, y + radius + 1)
    roi = depth_m[y1:y2, x1:x2]
    valid = roi[roi > 0]
    if valid.size == 0:
        return {
            'median_m': None,
            'p15_m': None,
            'mean_m': None,
            'valid_ratio': 0.0,
            'n_valid': 0,
        }
    return {
        'median_m': float(np.median(valid)),
        'p15_m': float(np.percentile(valid, 15)),
        'mean_m': float(np.mean(valid)),
        'valid_ratio': float(valid.size / roi.size),
        'n_valid': int(valid.size),
    }


def sample_bbox_depth(depth_m: np.ndarray, xc: int, yc: int, w: int, h: int) -> float:
    """Replicate vision_pipeline._bbs_to_depth: 15th percentile in central 25% crop."""
    sw, sh = max(1, int(w * 0.25)), max(1, int(h * 0.25))
    x1, y1 = max(0, xc - sw), max(0, yc - sh)
    x2, y2 = min(depth_m.shape[1], xc + sw), min(depth_m.shape[0], yc + sh)
    roi = depth_m[y1:y2, x1:x2]
    valid = roi[roi > 0]
    return float(np.percentile(valid, 15)) if valid.size > 0 else -1.0