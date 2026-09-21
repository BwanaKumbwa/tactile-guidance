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
    'target_w',
    'target_h',
    'target_track_id',
    'target_class_id',
    'target_conf',
    'hand_detected',
    'hand_xc',
    'hand_yc',
    'hand_w',
    'hand_h',
    'hand_track_id',
    'hand_class_id',
    'hand_conf',
    'hand_depth_m',
    'belt_in_approach',
    'belt_nav_mode',
    'belt_avoidance_active',
    'belt_avoid_side',
    'belt_plan_locked',
    'belt_plan_has_obstacle',
    'belt_phase',
    'belt_last_cue',
    'belt_signaled_handoff',
    'belt_handoff_unix',
    'belt_vib_angle_deg',
    'belt_vib_intensity',
    'belt_vib_motor_index',
    'bracelet_active',
    'last_belt_cmd_unix',
    'last_bracelet_cmd_unix',
    'bracelet_zone_enter_unix',
    'bracelet_vib_angle_deg',
    'bracelet_vib_intensity',
    'bracelet_vib_motor',
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
    'handoff_outcome',
    'grasp_outcome',
    'end_reason',
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
    handoff_outcome: Optional[str] = None
    grasp_outcome: Optional[str] = None
    end_reason: Optional[str] = None
    dir: Path = field(default_factory=Path)
    frames_path: Path = field(default_factory=Path)
    events_path: Path = field(default_factory=Path)
    frames_file: Any = None
    frames_writer: Any = None
    video_writer: Any = None
    video_path: Optional[Path] = None


_BINARY_OUTCOMES = frozenset({'success', 'fail'})
_END_REASONS = frozenset({'timeout', 'abort', 'tech_failure'})


def _norm_binary_outcome(value: Optional[str]) -> str:
    """Normalize experimenter success/fail (empty if unset)."""
    if value is None:
        return ''
    v = str(value).strip().lower()
    if v in ('y', 'yes', 'true', '1'):
        return 'success'
    if v in ('n', 'no', 'false', '0'):
        return 'fail'
    if v in _BINARY_OUTCOMES:
        return v
    if v in ('', 'na', 'n/a', 'none'):
        return ''
    raise ValueError(
        f'Invalid binary outcome {value!r}; use success|fail (or y/n)'
    )


def _norm_end_reason(value: Optional[str]) -> str:
    if value is None:
        return ''
    v = str(value).strip().lower()
    if v in ('', 'na', 'n/a', 'none', 'ok'):
        return ''
    if v in _END_REASONS:
        return v
    raise ValueError(
        f'Invalid end_reason {value!r}; use timeout|abort|tech_failure'
    )


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
        self._prev_signaled_handoff: bool = False
        self._prev_bracelet_active: bool = False
        self._saw_bracelet_cmd: bool = False
        self._saw_bracelet_zone: bool = False
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

    def end_trial(
        self,
        handoff_outcome: Optional[str] = None,
        grasp_outcome: Optional[str] = None,
        end_reason: Optional[str] = None,
        **kwargs,
    ) -> dict:
        """
        End the active trial.

        Experimenter marks two independent results (not combined):
          - handoff_outcome: success if participant reports the belt stopped
            (thesis trial success is based on this)
          - grasp_outcome: success if participant reports bracelet helped grasp

        Optional end_reason: timeout | abort | tech_failure (no handoff/grasp marks).

        Backward-compat: end_trial('abort'|'tech_failure'|...) or
        end_trial(outcome='...') still works via kwargs / positional shim.
        """
        # Shim: end_trial('abort') / end_trial(outcome='fail') from older callers
        if 'outcome' in kwargs and end_reason is None and handoff_outcome is None:
            legacy = str(kwargs.pop('outcome')).strip().lower()
            if legacy in _END_REASONS:
                end_reason = legacy
            elif legacy in _BINARY_OUTCOMES:
                # Old single success/fail → treat as handoff mark only
                handoff_outcome = legacy
                if grasp_outcome is None:
                    grasp_outcome = ''
            else:
                end_reason = legacy
        if kwargs:
            raise TypeError(f'Unexpected end_trial kwargs: {sorted(kwargs)}')

        # Positional mistake: first arg was sometimes a string end reason
        if (
            isinstance(handoff_outcome, str)
            and handoff_outcome.strip().lower() in _END_REASONS
            and grasp_outcome is None
            and end_reason is None
        ):
            end_reason = handoff_outcome.strip().lower()
            handoff_outcome = None

        with self._lock:
            if self._trial is None or self._trial.end_unix is not None:
                raise RuntimeError('No active trial to end')
            t = self._trial
            end = time.time()
            t.end_unix = end

            reason = _norm_end_reason(end_reason)
            if reason:
                h_out = ''
                g_out = ''
            else:
                h_out = _norm_binary_outcome(handoff_outcome)
                g_out = _norm_binary_outcome(grasp_outcome)
                if not h_out and not g_out:
                    raise ValueError(
                        'Provide handoff_outcome and/or grasp_outcome '
                        '(success|fail), or end_reason '
                        '(timeout|abort|tech_failure)'
                    )

            t.handoff_outcome = h_out
            t.grasp_outcome = g_out
            t.end_reason = reason

            include = t.meta.include_in_analysis
            if reason == 'tech_failure' or t.meta.block == 'training':
                include = False

            self._emit_event_unlocked('trial_end', {
                'handoff_outcome': h_out,
                'grasp_outcome': g_out,
                'end_reason': reason,
            })
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
                'handoff_outcome': h_out,
                'grasp_outcome': g_out,
                'end_reason': reason,
                'timeout_s': t.meta.timeout_s,
            }
            with open(self._trials_csv, 'a', newline='', encoding='utf-8') as f:
                csv.DictWriter(f, fieldnames=TRIALS_FIELDS).writerow(row)

            print(
                f'[Study] trial {t.meta.trial_id} ended '
                f'handoff={h_out or "—"} grasp={g_out or "—"} '
                f'end_reason={reason or "—"} include={include}'
            )
            result = {
                'trial_id': t.meta.trial_id,
                'handoff_outcome': h_out,
                'grasp_outcome': g_out,
                'end_reason': reason,
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
                    self.end_trial(end_reason='abort')
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
                'bottle_detected', 'hand_detected',
                'belt_in_approach', 'belt_avoidance_active',
                'belt_plan_locked', 'belt_plan_has_obstacle', 'bracelet_active',
                'belt_signaled_handoff',
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
        self._prev_signaled_handoff = False
        self._prev_bracelet_active = False
        self._saw_bracelet_cmd = False
        self._saw_bracelet_zone = False

    def _diff_events_unlocked(self, snap: dict, now: float) -> None:
        cue = snap.get('belt_last_cue') or None
        if cue and cue != self._prev_cue:
            if cue in ('nav_start', 'direction_change', 'pre_handoff', 'handoff'):
                self._emit_event_unlocked(cue, {})
            self._prev_cue = cue

        # Backup: handoff flag rose even if last_cue frame was missed
        signaled = bool(snap.get('belt_signaled_handoff'))
        if signaled and not self._prev_signaled_handoff:
            if cue != 'handoff' and self._prev_cue != 'handoff':
                self._emit_event_unlocked('handoff', {
                    'source': 'signaled_handoff',
                    'handoff_unix': snap.get('belt_handoff_unix'),
                    'target_depth_m': snap.get('target_depth_m'),
                })
                self._prev_cue = 'handoff'
        self._prev_signaled_handoff = signaled

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
        # Only log clear-resume from plan when avoidance actually ran
        if (done and not self._prev_plan_obstacle_done
                and (self._prev_avoidance_active or avoidance
                     or (self._prev_phase and str(self._prev_phase).startswith('avoid')))):
            self._emit_event_unlocked('avoidance_clear_resume', {
                'source': 'plan_obstacle_done',
            })
        self._prev_plan_obstacle_done = done

        br_active = bool(snap.get('bracelet_active'))
        if br_active and not self._prev_bracelet_active and not self._saw_bracelet_zone:
            self._saw_bracelet_zone = True
            self._emit_event_unlocked('bracelet_zone_enter', {
                'bracelet_zone_enter_unix': snap.get('bracelet_zone_enter_unix'),
                'target_depth_m': snap.get('target_depth_m'),
            })
        self._prev_bracelet_active = br_active

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


def _det_fields(det) -> Dict[str, Any]:
    """Parse YOLO/track detection [xc, yc, w, h, track_id, class_id, conf, depth]."""
    out = {
        'xc': '', 'yc': '', 'w': '', 'h': '',
        'track_id': '', 'class_id': '', 'conf': '', 'depth_m': '',
    }
    if det is None:
        return out
    try:
        out['xc'] = float(det[0])
        out['yc'] = float(det[1])
        out['w'] = float(det[2])
        out['h'] = float(det[3])
    except Exception:
        pass
    try:
        if len(det) > 4:
            out['track_id'] = int(det[4])
        if len(det) > 5:
            out['class_id'] = int(det[5])
        if len(det) > 6:
            out['conf'] = float(det[6])
        if len(det) > 7:
            d = float(det[7])
            if d > 0:
                out['depth_m'] = d
    except Exception:
        pass
    return out


def _empty_to_blank(value: Any) -> Any:
    if value is None:
        return ''
    return value


def build_frame_snapshot(
    trial_id: int,
    outputs: list,
    target_class_id: int,
    belt_status: Optional[dict],
    bracelet_status: Optional[dict],
    hand_class_ids: Optional[list] = None,
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

    hand = None
    hand_ids = set(int(x) for x in (hand_class_ids or []))
    if outputs and hand_ids:
        for det in outputs:
            try:
                if int(det[5]) in hand_ids:
                    hand = det
                    break
            except Exception:
                continue

    belt = belt_status or {}
    bracelet = bracelet_status or {}
    viz = belt.get('debug_viz') or {}
    tgt = _det_fields(bottle)
    hnd = _det_fields(hand)

    return {
        'trial_id': trial_id,
        'bottle_detected': bottle is not None,
        'target_depth_m': tgt['depth_m'],
        'target_xc': tgt['xc'],
        'target_yc': tgt['yc'],
        'target_w': tgt['w'],
        'target_h': tgt['h'],
        'target_track_id': tgt['track_id'],
        'target_class_id': tgt['class_id'],
        'target_conf': tgt['conf'],
        'hand_detected': hand is not None,
        'hand_xc': hnd['xc'],
        'hand_yc': hnd['yc'],
        'hand_w': hnd['w'],
        'hand_h': hnd['h'],
        'hand_track_id': hnd['track_id'],
        'hand_class_id': hnd['class_id'],
        'hand_conf': hnd['conf'],
        'hand_depth_m': hnd['depth_m'],
        'belt_in_approach': belt.get('in_approach'),
        'belt_nav_mode': belt.get('navigation_mode', ''),
        'belt_avoidance_active': belt.get('avoidance_active'),
        'belt_avoid_side': belt.get('avoid_side') or '',
        'belt_plan_locked': belt.get('plan_locked'),
        'belt_plan_has_obstacle': belt.get('plan_has_obstacle'),
        'belt_plan_obstacle_done': belt.get('plan_obstacle_done'),
        'belt_phase': viz.get('phase', ''),
        'belt_last_cue': belt.get('last_cue') or '',
        'belt_signaled_handoff': belt.get('signaled_handoff'),
        'belt_handoff_unix': belt.get('handoff_unix') or '',
        'belt_vib_angle_deg': _empty_to_blank(belt.get('last_angle')),
        'belt_vib_intensity': _empty_to_blank(belt.get('last_intensity')),
        'belt_vib_motor_index': _empty_to_blank(belt.get('last_motor_index')),
        'bracelet_active': bracelet.get('is_navigating') or bracelet.get('vibrating'),
        'last_belt_cmd_unix': belt.get('last_cmd_unix', ''),
        'last_bracelet_cmd_unix': bracelet.get('last_cmd_unix', ''),
        'bracelet_zone_enter_unix': bracelet.get('zone_enter_unix') or '',
        'bracelet_vib_angle_deg': _empty_to_blank(bracelet.get('last_angle')),
        'bracelet_vib_intensity': _empty_to_blank(bracelet.get('last_intensity')),
        'bracelet_vib_motor': bracelet.get('last_motor') or '',
    }
