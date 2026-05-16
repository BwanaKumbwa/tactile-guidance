"""
experiment_runner.py

Change 1: All experiment-trial concerns extracted from the vision pipeline.
VisionPipeline knows nothing about trials, participants, or CSV files.
ExperimentRunner wraps the pipeline and adds:
  - OpenCV display loop (must run on the main thread)
  - Trial start/stop state machine (S / Y / N / F / T / C keys)
  - Per-trial data collection from bracelet_controller
  - CSV output

Usage (standalone research mode):
    pipeline = VisionPipeline(cfg, ...)
    runner   = ExperimentRunner(pipeline, participant=1, condition='grasping',
                                target_objs=['cup', 'bottle'], manual_entry=False)
    runner.run()   # blocks until 'C' is pressed or all targets are exhausted

Usage (deployment — server_main.py):
    pipeline.start()
    pipeline.wait()   # no ExperimentRunner involved
"""

from __future__ import annotations

import queue
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

import cv2
import numpy as np
import pandas as pd

from labels import coco_labels
from vision_pipeline import VisionPipeline, DisplayItem


# ═══════════════════════════════════════════════════════════════════════════
# Trial constants
# ═══════════════════════════════════════════════════════════════════════════

class _Key:
    TRIAL_SUCCESSFUL   = ord('y')
    TRIAL_FAILED       = ord('n')
    SYSTEM_FAILED      = ord('f')
    WRONG_TARGET       = ord('t')
    START_TRIAL        = ord('s')
    SAVE_AND_QUIT      = ord('c')

    RESULT_KEYS = {
        ord('y'): 'SUCCESSFUL',
        ord('n'): 'FAILED',
        ord('f'): 'SYSTEM FAILED',
        ord('t'): 'WRONG TARGET',
    }


# ═══════════════════════════════════════════════════════════════════════════
# ExperimentRunner
# ═══════════════════════════════════════════════════════════════════════════

class ExperimentRunner:
    """
    Research-only wrapper around VisionPipeline.

    Manages the experiment trial state machine and writes results to CSV.
    Has NO knowledge of YOLO, depth estimation, haptic intensities, or
    MCP commands — those all live in VisionPipeline.
    """

    def __init__(
        self,
        pipeline:      VisionPipeline,
        participant:   int,
        condition:     str,
        output_path:   str,
        target_objs:   Optional[List[str]] = None,
        manual_entry:  bool                = True,
        mock_navigate: bool                = False,
    ):
        self._pipeline      = pipeline
        self._participant   = participant
        self._condition     = condition
        self._output_path   = output_path
        self._target_objs   = target_objs or []
        self._manual_entry  = manual_entry
        self._mock_navigate = mock_navigate

        # Trial state
        self._trial_running:    bool  = False
        self._ready_for_next:   bool  = True
        self._obj_index:        int   = 0
        self._trial_start_time        = 'NA'
        self._trial_end_time          = 'NA'
        self._last_pressed_key: int   = -1

        # Data accumulator
        self._output_data: List[list] = []

    # ── Main entry point (blocking — must be called from the main thread) ─

    def run(self) -> None:
        """
        Start the pipeline, open an OpenCV window, and run the trial loop.
        Returns when the pipeline stops or the researcher presses 'C'.
        Must be called from the main OS thread (OpenCV requirement).
        """
        self._pipeline.start()

        if self._manual_entry:
            print(f'[Experiment] Manual mode. Press S to start a trial.\n'
                  f'Available COCO classes: {coco_labels}')
        else:
            print(f'[Experiment] Auto mode. Targets: {self._target_objs}')
        print('[Experiment] Keys: S=start  Y=success  N=fail  F=sys_fail  T=wrong  C=quit')

        try:
            self._main_loop()
        finally:
            self._pipeline.stop()
            self._pipeline.wait()
            cv2.destroyAllWindows()
            if self._output_data:
                self._save_output_data()

    def _main_loop(self) -> None:
        """OpenCV display + key-press loop running on the main thread."""
        while self._pipeline.is_running:
            # Non-blocking: try to get the latest annotated frame
            try:
                item: DisplayItem = self._pipeline.display_queue.get(timeout=0.05)
            except queue.Empty:
                # No new frame yet; still poll for key input to stay responsive
                key = cv2.waitKey(1)
                if key != -1:
                    if self._handle_key(key) == 'quit':
                        break
                continue

            # Show frame (optionally side-by-side with depth map)
            im0 = item.annotated_im0
            if item.depth_img is not None:
                view = self._pipeline.depth_side_by_side(im0, item.depth_img)
                cv2.imshow('AIBox & Depth', view)
            else:
                cv2.imshow('AIBox', im0)
                cv2.setWindowProperty('AIBox', cv2.WND_PROP_TOPMOST, 1)

            key = cv2.waitKey(1)
            if key != -1:
                if self._handle_key(key) == 'quit':
                    break

    # ── Key handler ───────────────────────────────────────────────────────

    def _handle_key(self, key: int) -> Optional[str]:
        self._last_pressed_key = key

        # End running trial
        if key in _Key.RESULT_KEYS and self._trial_running:
            self._end_trial(key)

        # Start next trial
        elif key == _Key.START_TRIAL and self._ready_for_next:
            self._start_trial()

        # Save data and quit
        elif key == _Key.SAVE_AND_QUIT:
            if self._trial_running:
                self._append_output_row()
            self._save_output_data()
            self._pipeline.stop()
            return 'quit'

        return None

    # ── Trial state machine ───────────────────────────────────────────────

    def _start_trial(self) -> None:
        """Resolve target name, tell the pipeline, start timing."""
        if self._manual_entry:
            # Researcher types target key — blocks intentionally (research use only)
            print(f'Available: {coco_labels}')
            try:
                key_str = input('Enter COCO class key: ').strip()
                target_key = int(key_str)
                if target_key not in coco_labels:
                    print(f'[Experiment] Invalid key {target_key}.')
                    return
                target_name = coco_labels[target_key]
            except (ValueError, EOFError):
                return
        else:
            if self._obj_index >= len(self._target_objs):
                print('[Experiment] All targets exhausted.')
                self._pipeline.stop()
                return
            target_name = self._target_objs[self._obj_index]

        self._pipeline.set_target(target_name)
        self._pipeline.set_vibrate(True)

        self._trial_start_time = time.time()
        self._trial_running    = True
        self._ready_for_next   = False
        print(f'[Experiment] Trial started — target: {target_name}')

    def _end_trial(self, key: int) -> None:
        """Record result, advance index, reset pipeline for next trial."""
        self._trial_end_time = time.time()
        result = _Key.RESULT_KEYS.get(key, '?')
        print(f'[Experiment] Trial ended — {result}')

        self._append_output_row()

        # Clear pipeline target and reset bracelet state
        self._pipeline.set_target('none')
        bc = self._pipeline.bracelet_controller
        if bc:
            bc.frozen      = False
            bc.was_guiding = False

        if not self._manual_entry:
            self._obj_index += 1
            if self._obj_index >= len(self._target_objs):
                print('[Experiment] All targets covered. Saving and stopping.')
                self._save_output_data()
                self._pipeline.stop()
                return

        self._trial_running    = False
        self._ready_for_next   = True
        self._trial_start_time = 'NA'
        self._trial_end_time   = 'NA'
        print('[Experiment] Ready for next trial (press S).')

    # ── Data collection ───────────────────────────────────────────────────

    def _append_output_row(self) -> None:
        """
        Snapshot bracelet_controller stats and reset them for the next trial.
        Called at trial end or on manual quit.
        """
        bc = self._pipeline.bracelet_controller
        if bc is None:
            return

        row = [
            self._pipeline.class_target_obj,
            self._trial_start_time,
            bc.navigation_time,
            bc.freezing_time,
            bc.grasping_time,
            self._trial_end_time,
            chr(self._last_pressed_key) if self._last_pressed_key > 0 else '?',
            list(bc.target_detections_list),
            list(bc.target_confidence_list),
            list(bc.target_class_track_ids),
            list(bc.target_object_track_ids),
            list(bc.target_position),
            list(bc.hand_confidence_list),
            list(bc.hand_position),
        ]

        # Reset bracelet stats for next trial
        for attr in ('navigation_time', 'freezing_time', 'grasping_time'):
            setattr(bc, attr, 'NA')
        for attr in ('target_detections_list', 'target_confidence_list',
                     'target_class_track_ids', 'target_object_track_ids',
                     'target_position', 'hand_confidence_list', 'hand_position'):
            setattr(bc, attr, [])

        self._output_data.append(row)

    def _save_output_data(self) -> None:
        if not self._output_data:
            print('[Experiment] No data to save.')
            return
        Path(self._output_path).mkdir(parents=True, exist_ok=True)
        csv_path = (f'{self._output_path}'
                    f'{self._condition}_participant_{self._participant}.csv')
        pd.DataFrame(self._output_data).to_csv(csv_path, index=False)
        print(f'[Experiment] Data saved → {csv_path}')
