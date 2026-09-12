"""
Study session / trial logging for belt–bracelet human-subjects experiments.

Writes analysis-friendly artifacts under:
  results/study/P{participant}/{session_id}/
    session.json
    trials.csv
    trial_001/
      frames.csv
      events.jsonl
      overlay.mp4   (optional)

Thread-safe for calls from VisionPipeline's post-processing thread and from
HTTP / OpenCV key handlers on other threads.
"""
from __future__ import annotations

import csv
import json
import subprocess
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import cv2
import numpy as np

TRIAL_TIMEOUT_S = 15.0

FRAME_FIELDS = [
    't_unix',
    't_rel_s',
    'trial_id',
    'bottle_detected',
    'target_depth_m',
    'target_xc',
    'target_yc',
    'belt_in_approach',
    'belt_nav_mode',
    'belt_avoidance_active',
    'belt_avoid_side',
    'belt_plan_locked',
    'belt_plan_has_obstacle',
    'belt_phase',
    'belt_last_cue',
    'bracelet_active',
    'last_belt_cmd_unix',
    'last_bracelet_cmd_unix',
]

TRIALS_FIELDS = [
    'trial_id',
    'block',
    'approach_type',
    'distance_m',
    'include_in_analysis',
    't0_unix',
    't0_iso',
    'sync_unix',
    'end_unix',
    'outcome',
    'timeout_s',
]


def _utc_iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def _git_commit(cwd: Optional[Path] = None) -> Optional[str]:
    try:
        out = subprocess.check_output(
            ['git', 'rev-parse', '--short', 'HEAD'],
            cwd=str(cwd) if cwd else None,
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
        return out or None
    except Exception:
        return None


@dataclass
class TrialMeta:
    trial_id: int
    block: str
    approach_type: str
    distance_m: float
    include_in_analysis: bool = True
    timeout_s: float = TRIAL_TIMEOUT_S


@dataclass
class _TrialRuntime:
    meta: TrialMeta
    t0_unix: float
    sync_unix: Optional[float] = None
    end_unix: Optional[float] = None
    outcome: Optional[str] = None
    dir: Path = field(default_factory=Path)
    frames_path: Path = field(default_factory=Path)
    events_path: Path = field(default_factory=Path)
    frames_file: Any = None
    frames_writer: Any = None
    video_writer: Any = None
    video_path: Optional[Path] = None


class StudyLogger:
    """
    Session-scoped logger. Attach one instance to VisionPipeline via
    ``pipeline.set_study_logger(logger)``.
    """

    def __init__(
        self,
        participant_id: int,
        runner: str,
        base_dir: Optional[Path] = None,
        notes: str = '',
        save_video: bool = False,
        video_fps: float = 15.0,
    ):
        self.participant_id = int(participant_id)
        self.runner = runner  # 'desktop' | 'android'
        self.notes = notes or ''
        self.save_video = bool(save_video)
        self.video_fps = float(video_fps)

        root = Path(base_dir) if base_dir else Path('results') / 'study'
        self.session_id = datetime.now(tz=timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        self.session_dir = root / f'P{self.participant_id}' / self.session_id
        self.session_dir.mkdir(parents=True, exist_ok=True)

        self.started_at = time.time()
        self.git_commit = _git_commit(Path(__file__).resolve().parents[2])

        self._lock = threading.RLock()
        self._trial: Optional[_TrialRuntime] = None
        self._next_trial_id = 1
        self._trials_csv = self.session_dir / 'trials.csv'
        self._ensure_trials_header()

        # Diff state for sparse events
        self._prev_cue: Optional[str] = None
        self._prev_phase: Optional[str] = None
        self._prev_plan_locked: bool = False
        self._prev_plan_has_obstacle: Optional[bool] = None
        self._prev_avoidance_active: bool = False
        self._prev_plan_obstacle_done: bool = False
        self._saw_bracelet_cmd: bool = False
        self._pending_meta: Optional[Dict[str, Any]] = None

        self._write_session_json()

    # ------------------------------------------------------------------
    # Session / status
    # ------------------------------------------------------------------

    def _write_session_json(self) -> None:
        payload = {
            'participant_id': self.participant_id,
            'session_id': self.session_id,
            'started_at': self.started_at,
            'started_at_iso': _utc_iso(self.started_at),
            'git_commit': self.git_commit,
            'runner': self.runner,
            'notes': self.notes,
            'save_video': self.save_video,
            'session_dir': str(self.session_dir.resolve()),
        }
        path = self.session_dir / 'session.json'
        path.write_text(json.dumps(payload, indent=2), encoding='utf-8')

    def _ensure_trials_header(self) -> None:
        if not self._trials_csv.exists():
            with open(self._trials_csv, 'w', newline='', encoding='utf-8') as f:
                csv.DictWriter(f, fieldnames=TRIALS_FIELDS).writeheader()

    def get_status(self) -> dict:
        with self._lock:
            t = self._trial
            return {
                'participant_id': self.participant_id,
                'session_id': self.session_id,
                'session_dir': str(self.session_dir.resolve()),
                'runner': self.runner,
                'save_video': self.save_video,
                'trial_active': t is not None and t.end_unix is None,
                'trial_id': t.meta.trial_id if t else None,
                'pending_meta': dict(self._pending_meta) if self._pending_meta else None,
                'next_trial_id': self._next_trial_id,
            }

    def set_pending_meta(
        self,
        block: str,
        approach_type: str,
        distance_m: float,
        include_in_analysis: bool = True,
        trial_id: Optional[int] = None,
        timeout_s: float = TRIAL_TIMEOUT_S,
    ) -> dict:
        """Store meta used by the next start_trial() if kwargs omitted."""
        with self._lock:
            self._pending_meta = {
                'trial_id': trial_id,
                'block': block,
                'approach_type': approach_type,
                'distance_m': float(distance_m),
                'include_in_analysis': bool(include_in_analysis),
                'timeout_s': float(timeout_s),
            }
            return dict(self._pending_meta)

    # ------------------------------------------------------------------
    # Trial lifecycle
    # ------------------------------------------------------------------

    def start_trial(
        self,
        block: Optional[str] = None,
        approach_type: Optional[str] = None,
        distance_m: Optional[float] = None,
        include_in_analysis: Optional[bool] = None,
        trial_id: Optional[int] = None,
        timeout_s: float = TRIAL_TIMEOUT_S,
    ) -> dict:
        with self._lock:
            if self._trial is not None and self._trial.end_unix is None:
                raise RuntimeError(
                    f'Trial {self._trial.meta.trial_id} still running; end it first'
                )

            pending = self._pending_meta or {}
            block = block if block is not None else pending.get('block')
            approach_type = (
                approach_type if approach_type is not None
                else pending.get('approach_type')
            )
            distance_m = (
                distance_m if distance_m is not None
                else pending.get('distance_m')
            )
            if include_in_analysis is None:
                include_in_analysis = pending.get('include_in_analysis', True)
            if trial_id is None:
                trial_id = pending.get('trial_id')
            if timeout_s == TRIAL_TIMEOUT_S and pending.get('timeout_s'):
                timeout_s = float(pending['timeout_s'])

            if not block or not approach_type or distance_m is None:
                raise ValueError(
                    'block, approach_type, and distance_m are required '
                    '(pass them or call set_pending_meta first)'
                )

            if trial_id is None:
                trial_id = self._next_trial_id
            self._next_trial_id = max(self._next_trial_id, int(trial_id) + 1)

            meta = TrialMeta(
                trial_id=int(trial_id),
                block=str(block),
                approach_type=str(approach_type),
                distance_m=float(distance_m),
                include_in_analysis=bool(include_in_analysis),
                timeout_s=float(timeout_s),
            )
            t0 = time.time()
            trial_dir = self.session_dir / f'trial_{meta.trial_id:03d}'
            trial_dir.mkdir(parents=True, exist_ok=True)
            frames_path = trial_dir / 'frames.csv'
            events_path = trial_dir / 'events.jsonl'

            frames_file = open(frames_path, 'w', newline='', encoding='utf-8')
            frames_writer = csv.DictWriter(frames_file, fieldnames=FRAME_FIELDS)
            frames_writer.writeheader()
            frames_file.flush()

            # Truncate events file
            events_path.write_text('', encoding='utf-8')

            self._trial = _TrialRuntime(
                meta=meta,
                t0_unix=t0,
                dir=trial_dir,
                frames_path=frames_path,
                events_path=events_path,
                frames_file=frames_file,
                frames_writer=frames_writer,
            )
            self._reset_diff_state()
            self._emit_event_unlocked('trial_start', {
                'block': meta.block,
                'approach_type': meta.approach_type,
                'distance_m': meta.distance_m,
                'include_in_analysis': meta.include_in_analysis,
            })
            self._pending_meta = None
            print(
                f'[Study] trial {meta.trial_id} started '
                f'block={meta.block} approach={meta.approach_type} '
                f'distance={meta.distance_m}m → {trial_dir}'
            )
            return {
                'trial_id': meta.trial_id,
                't0_unix': t0,
                't0_iso': _utc_iso(t0),
                'trial_dir': str(trial_dir.resolve()),
            }

    def mark_sync(self) -> dict:
        with self._lock:
            if self._trial is None or self._trial.end_unix is not None:
                raise RuntimeError('No active trial for sync marker')
            ts = time.time()
            self._trial.sync_unix = ts
            self._emit_event_unlocked('sync_marker', {})
            print(f'[Study] sync marker trial={self._trial.meta.trial_id} t={ts:.3f}')
            return {'trial_id': self._trial.meta.trial_id, 'sync_unix': ts}

    def end_trial(self, outcome: str) -> dict:
        with self._lock:
            if self._trial is None or self._trial.end_unix is not None:
                raise RuntimeError('No active trial to end')
            t = self._trial
            end = time.time()
            t.end_unix = end
            t.outcome = str(outcome)

            include = t.meta.include_in_analysis
            if outcome in ('tech_failure', 'training') or t.meta.block == 'training':
                include = False
            if outcome == 'tech_failure':
                include = False

            self._emit_event_unlocked('trial_end', {'outcome': outcome})
            self._close_trial_files_unlocked()

            row = {
                'trial_id': t.meta.trial_id,
                'block': t.meta.block,
                'approach_type': t.meta.approach_type,
                'distance_m': t.meta.distance_m,
                'include_in_analysis': include,
                't0_unix': t.t0_unix,
                't0_iso': _utc_iso(t.t0_unix),
                'sync_unix': t.sync_unix if t.sync_unix is not None else '',
                'end_unix': end,
                'outcome': outcome,
                'timeout_s': t.meta.timeout_s,
            }
            with open(self._trials_csv, 'a', newline='', encoding='utf-8') as f:
                csv.DictWriter(f, fieldnames=TRIALS_FIELDS).writerow(row)

            print(
                f'[Study] trial {t.meta.trial_id} ended outcome={outcome} '
                f'include={include}'
            )
            result = {
                'trial_id': t.meta.trial_id,
                'outcome': outcome,
                'include_in_analysis': include,
                'end_unix': end,
                'trial_dir': str(t.dir.resolve()),
            }
            self._trial = None
            return result

    def _close_trial_files_unlocked(self) -> None:
        t = self._trial
        if t is None:
            return
        if t.frames_file is not None:
            try:
                t.frames_file.flush()
                t.frames_file.close()
            except Exception:
                pass
            t.frames_file = None
            t.frames_writer = None
        if t.video_writer is not None:
            try:
                t.video_writer.release()
            except Exception:
                pass
            t.video_writer = None

    def close(self) -> None:
        with self._lock:
            if self._trial is not None and self._trial.end_unix is None:
                try:
                    self.end_trial('abort')
                except Exception:
                    self._close_trial_files_unlocked()
                    self._trial = None

    # ------------------------------------------------------------------
    # Frame + event logging
    # ------------------------------------------------------------------

    @property
    def trial_active(self) -> bool:
        with self._lock:
            return self._trial is not None and self._trial.end_unix is None

    def on_frame(
        self,
        annotated_bgr: Optional[np.ndarray],
        snapshot: Dict[str, Any],
    ) -> None:
        """
        Called once per processed frame from VisionPipeline.

        ``snapshot`` keys match FRAME_FIELDS (except t_unix / t_rel_s which
        are filled here). Extra keys used for event diffs:
        belt_last_cue, belt_phase, belt_plan_locked, belt_plan_has_obstacle,
        belt_avoidance_active, belt_plan_obstacle_done, last_bracelet_cmd_unix
        """
        with self._lock:
            t = self._trial
            if t is None or t.end_unix is not None:
                return

            now = time.time()
            row = {k: snapshot.get(k, '') for k in FRAME_FIELDS}
            row['t_unix'] = now
            row['t_rel_s'] = now - t.t0_unix
            row['trial_id'] = t.meta.trial_id

            # Normalize booleans / None for CSV
            for key in (
                'bottle_detected', 'belt_in_approach', 'belt_avoidance_active',
                'belt_plan_locked', 'belt_plan_has_obstacle', 'bracelet_active',
            ):
                val = row.get(key)
                if val == '' or val is None:
                    row[key] = ''
                else:
                    row[key] = int(bool(val))

            if t.frames_writer is not None:
                t.frames_writer.writerow(row)
                t.frames_file.flush()

            self._diff_events_unlocked(snapshot, now)

            if self.save_video and annotated_bgr is not None:
                self._write_video_frame_unlocked(annotated_bgr)

    def emit_event(self, name: str, data: Optional[dict] = None) -> None:
        with self._lock:
            if self._trial is None or self._trial.end_unix is not None:
                return
            self._emit_event_unlocked(name, data or {})

    def _emit_event_unlocked(self, name: str, data: dict) -> None:
        t = self._trial
        if t is None:
            return
        now = time.time()
        entry = {
            't_unix': now,
            't_rel_s': now - t.t0_unix,
            'trial_id': t.meta.trial_id,
            'event': name,
            'data': data,
        }
        with open(t.events_path, 'a', encoding='utf-8') as f:
            f.write(json.dumps(entry) + '\n')

    def _reset_diff_state(self) -> None:
        self._prev_cue = None
        self._prev_phase = None
        self._prev_plan_locked = False
        self._prev_plan_has_obstacle = None
        self._prev_avoidance_active = False
        self._prev_plan_obstacle_done = False
        self._saw_bracelet_cmd = False

    def _diff_events_unlocked(self, snap: dict, now: float) -> None:
        cue = snap.get('belt_last_cue') or None
        if cue and cue != self._prev_cue:
            if cue in ('nav_start', 'direction_change', 'pre_handoff', 'handoff'):
                self._emit_event_unlocked(cue, {})
            self._prev_cue = cue

        phase = snap.get('belt_phase') or None
        if phase and phase != self._prev_phase:
            if phase in ('avoid_A', 'avoid_A_hold') and (
                self._prev_phase is None
                or not str(self._prev_phase).startswith('avoid')
            ):
                self._emit_event_unlocked('avoidance_enter', {
                    'phase': phase,
                    'avoid_side': snap.get('belt_avoid_side'),
                })
            if phase == 'approach' and self._prev_phase in (
                'avoid_A', 'avoid_A_hold', 'avoid_B_clearing',
            ):
                self._emit_event_unlocked('avoidance_clear_resume', {
                    'from_phase': self._prev_phase,
                })
            self._prev_phase = phase

        plan_locked = bool(snap.get('belt_plan_locked'))
        has_obs = snap.get('belt_plan_has_obstacle')
        if plan_locked and not self._prev_plan_locked:
            if has_obs:
                self._emit_event_unlocked('plan_locked_avoid', {
                    'avoid_side': snap.get('belt_avoid_side'),
                })
            else:
                self._emit_event_unlocked('plan_locked_direct', {})
        self._prev_plan_locked = plan_locked
        self._prev_plan_has_obstacle = has_obs

        avoidance = bool(snap.get('belt_avoidance_active'))
        if avoidance and not self._prev_avoidance_active:
            # Backup if phase string missed
            if self._prev_phase is None or not str(self._prev_phase).startswith('avoid'):
                self._emit_event_unlocked('avoidance_enter', {
                    'avoid_side': snap.get('belt_avoid_side'),
                    'source': 'avoidance_active_edge',
                })
        self._prev_avoidance_active = avoidance

        done = bool(snap.get('belt_plan_obstacle_done'))
        if done and not self._prev_plan_obstacle_done:
            self._emit_event_unlocked('avoidance_clear_resume', {
                'source': 'plan_obstacle_done',
            })
        self._prev_plan_obstacle_done = done

        br_cmd = snap.get('last_bracelet_cmd_unix')
        if br_cmd not in (None, '', 0, 0.0) and not self._saw_bracelet_cmd:
            try:
                if float(br_cmd) >= (self._trial.t0_unix if self._trial else 0):
                    self._saw_bracelet_cmd = True
                    self._emit_event_unlocked('bracelet_first_cmd', {
                        'last_bracelet_cmd_unix': float(br_cmd),
                    })
            except (TypeError, ValueError):
                pass

    def _write_video_frame_unlocked(self, frame: np.ndarray) -> None:
        t = self._trial
        if t is None:
            return
        if frame is None or not hasattr(frame, 'shape') or frame.size == 0:
            return
        h, w = frame.shape[:2]
        if t.video_writer is None:
            t.video_path = t.dir / 'overlay.mp4'
            fourcc = cv2.VideoWriter_fourcc(*'mp4v')
            t.video_writer = cv2.VideoWriter(
                str(t.video_path), fourcc, self.video_fps, (w, h),
            )
            if not t.video_writer.isOpened():
                print(f'[Study] WARNING: could not open VideoWriter → {t.video_path}')
                t.video_writer = None
                return
            t._video_size = (w, h)  # type: ignore[attr-defined]
            print(f'[Study] recording overlay → {t.video_path}')
        if t.video_writer is not None:
            out = frame
            size = getattr(t, '_video_size', (w, h))
            if (frame.shape[1], frame.shape[0]) != size:
                out = cv2.resize(frame, size)
            try:
                t.video_writer.write(out)
            except Exception as e:
                print(f'[Study] video write error: {e}')


def build_frame_snapshot(
    trial_id: int,
    outputs: list,
    target_class_id: int,
    belt_status: Optional[dict],
    bracelet_status: Optional[dict],
) -> Dict[str, Any]:
    """Assemble a frame row dict from pipeline + device status."""
    bottle = None
    if outputs and target_class_id is not None and target_class_id >= 0:
        for det in outputs:
            try:
                if int(det[5]) == int(target_class_id):
                    bottle = det
                    break
            except Exception:
                continue

    belt = belt_status or {}
    bracelet = bracelet_status or {}
    viz = belt.get('debug_viz') or {}

    depth_m = ''
    xc = yc = ''
    if bottle is not None:
        try:
            xc = float(bottle[0])
            yc = float(bottle[1])
        except Exception:
            pass
        if len(bottle) > 7:
            try:
                d = float(bottle[7])
                if d > 0:
                    depth_m = d
            except Exception:
                pass

    return {
        'trial_id': trial_id,
        'bottle_detected': bottle is not None,
        'target_depth_m': depth_m,
        'target_xc': xc,
        'target_yc': yc,
        'belt_in_approach': belt.get('in_approach'),
        'belt_nav_mode': belt.get('navigation_mode', ''),
        'belt_avoidance_active': belt.get('avoidance_active'),
        'belt_avoid_side': belt.get('avoid_side') or '',
        'belt_plan_locked': belt.get('plan_locked'),
        'belt_plan_has_obstacle': belt.get('plan_has_obstacle'),
        'belt_plan_obstacle_done': belt.get('plan_obstacle_done'),
        'belt_phase': viz.get('phase', ''),
        'belt_last_cue': belt.get('last_cue') or '',
        'bracelet_active': bracelet.get('is_navigating') or bracelet.get('vibrating'),
        'last_belt_cmd_unix': belt.get('last_cmd_unix', ''),
        'last_bracelet_cmd_unix': bracelet.get('last_cmd_unix', ''),
    }
