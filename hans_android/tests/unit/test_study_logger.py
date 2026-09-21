"""Unit tests for StudyLogger (no hardware / no VisionPipeline)."""
from __future__ import annotations

import csv
import json
import time

import numpy as np
import pytest

from server.study_logger import (
    FRAME_FIELDS,
    StudyLogger,
    build_frame_snapshot,
)


@pytest.fixture
def logger(tmp_path):
    log = StudyLogger(
        participant_id=99,
        runner='desktop',
        base_dir=tmp_path / 'study',
        notes='unit-test',
        save_video=False,
    )
    yield log
    log.close()


def test_session_json_written(logger):
    session_path = logger.session_dir / 'session.json'
    assert session_path.exists()
    data = json.loads(session_path.read_text())
    assert data['participant_id'] == 99
    assert data['runner'] == 'desktop'
    assert data['notes'] == 'unit-test'
    assert (logger.session_dir / 'trials.csv').exists()


def test_start_frame_event_end(logger):
    info = logger.start_trial(
        block='direct',
        approach_type='direct',
        distance_m=2.0,
        include_in_analysis=True,
    )
    assert info['trial_id'] == 1
    assert logger.trial_active

    trial_dir = logger.session_dir / 'trial_001'
    assert trial_dir.exists()
    assert (trial_dir / 'frames.csv').exists()
    assert (trial_dir / 'events.jsonl').exists()

    snap = build_frame_snapshot(
        trial_id=1,
        outputs=[
            [320, 240, 40, 60, 7, 39, 0.9, 2.5],
            [280, 300, 50, 50, 3, 80, 0.85, 0.6],
        ],
        target_class_id=39,
        hand_class_ids=[80, 81],
        belt_status={
            'in_approach': True,
            'navigation_mode': 'direct',
            'avoidance_active': False,
            'avoid_side': None,
            'plan_locked': True,
            'plan_has_obstacle': False,
            'plan_obstacle_done': True,
            'last_cue': 'nav_start',
            'last_cmd_unix': 1.0,
            'last_angle': 45.0,
            'last_intensity': 60,
            'last_motor_index': 4,
            'debug_viz': {'phase': 'approach'},
        },
        bracelet_status={
            'is_navigating': True,
            'last_cmd_unix': 2.0,
            'last_angle': 90.0,
            'last_intensity': 50,
            'last_motor': 'top',
            'zone_enter_unix': 1.5,
        },
    )
    assert snap['target_w'] == 40.0
    assert snap['target_h'] == 60.0
    assert snap['target_track_id'] == 7
    assert snap['target_class_id'] == 39
    assert snap['target_conf'] == 0.9
    assert snap['hand_detected'] is True
    assert snap['hand_xc'] == 280.0
    assert snap['hand_class_id'] == 80
    assert snap['belt_vib_angle_deg'] == 45.0
    assert snap['belt_vib_intensity'] == 60
    assert snap['belt_vib_motor_index'] == 4
    assert snap['bracelet_vib_angle_deg'] == 90.0
    assert snap['bracelet_vib_intensity'] == 50
    assert snap['bracelet_vib_motor'] == 'top'
    logger.on_frame(None, snap)
    # Second frame with handoff cue → event
    snap2 = dict(snap)
    snap2['belt_last_cue'] = 'handoff'
    snap2['belt_phase'] = 'approach'
    logger.on_frame(None, snap2)

    sync = logger.mark_sync()
    assert sync['sync_unix'] > 0

    end = logger.end_trial(handoff_outcome='success', grasp_outcome='success')
    assert end['handoff_outcome'] == 'success'
    assert end['grasp_outcome'] == 'success'
    assert end.get('end_reason', '') in ('', None)
    assert end['include_in_analysis'] is True
    assert not logger.trial_active

    # frames.csv has header + >=1 data row
    with open(trial_dir / 'frames.csv', newline='', encoding='utf-8') as f:
        rows = list(csv.DictReader(f))
    assert rows
    assert set(FRAME_FIELDS) == set(rows[0].keys())
    assert int(rows[0]['bottle_detected']) == 1
    assert float(rows[0]['target_w']) == 40.0
    assert int(rows[0]['hand_detected']) == 1
    assert float(rows[0]['belt_vib_intensity']) == 60
    assert rows[0]['bracelet_vib_motor'] == 'top'

    events = [
        json.loads(line)
        for line in (trial_dir / 'events.jsonl').read_text().splitlines()
        if line.strip()
    ]
    names = [e['event'] for e in events]
    assert 'trial_start' in names
    assert 'nav_start' in names
    assert 'handoff' in names
    assert 'sync_marker' in names
    assert 'plan_locked_direct' in names
    assert 'trial_end' in names

    with open(logger.session_dir / 'trials.csv', newline='', encoding='utf-8') as f:
        trials = list(csv.DictReader(f))
    assert len(trials) == 1
    assert trials[0]['block'] == 'direct'
    assert float(trials[0]['distance_m']) == 2.0
    assert trials[0]['handoff_outcome'] == 'success'
    assert trials[0]['grasp_outcome'] == 'success'
    assert 'outcome' not in trials[0]


def test_separate_handoff_and_grasp_outcomes(logger):
    logger.start_trial(block='direct', approach_type='direct', distance_m=2.0)
    end = logger.end_trial(handoff_outcome='success', grasp_outcome='fail')
    assert end['handoff_outcome'] == 'success'
    assert end['grasp_outcome'] == 'fail'
    assert end.get('end_reason', '') == ''

    with open(logger.session_dir / 'trials.csv', newline='', encoding='utf-8') as f:
        trials = list(csv.DictReader(f))
    assert trials[-1]['handoff_outcome'] == 'success'
    assert trials[-1]['grasp_outcome'] == 'fail'
    assert 'outcome' not in trials[-1]

    events = [
        json.loads(line)
        for line in (logger.session_dir / 'trial_001' / 'events.jsonl')
        .read_text().splitlines()
        if line.strip()
    ]
    end_ev = [e for e in events if e['event'] == 'trial_end'][-1]
    assert end_ev['data']['handoff_outcome'] == 'success'
    assert end_ev['data']['grasp_outcome'] == 'fail'


def test_tech_failure_excluded(logger):
    logger.start_trial(
        block='lateral', approach_type='lateral', distance_m=3.0,
    )
    logger.end_trial(end_reason='tech_failure')
    with open(logger.session_dir / 'trials.csv', newline='', encoding='utf-8') as f:
        trials = list(csv.DictReader(f))
    assert str(trials[-1]['include_in_analysis']).lower() in ('false', '0')
    assert trials[-1]['end_reason'] == 'tech_failure'
    assert trials[-1]['handoff_outcome'] == ''
    assert trials[-1]['grasp_outcome'] == ''


def test_cannot_double_start(logger):
    logger.start_trial(block='direct', approach_type='direct', distance_m=1.0)
    with pytest.raises(RuntimeError):
        logger.start_trial(block='direct', approach_type='direct', distance_m=1.0)
    logger.end_trial('abort')


def test_pending_meta(logger):
    logger.set_pending_meta(
        block='dynamic', approach_type='dynamic', distance_m=1.5,
        include_in_analysis=True,
    )
    info = logger.start_trial()
    assert info['trial_id'] == 1
    logger.end_trial('success')
    with open(logger.session_dir / 'trials.csv', newline='', encoding='utf-8') as f:
        trials = list(csv.DictReader(f))
    assert trials[0]['approach_type'] == 'dynamic'
    assert float(trials[0]['distance_m']) == 1.5


def test_avoidance_events(logger):
    logger.start_trial(block='dynamic', approach_type='dynamic', distance_m=2.0)
    snap = build_frame_snapshot(
        trial_id=1,
        outputs=[],
        target_class_id=39,
        belt_status={
            'in_approach': True,
            'navigation_mode': 'avoid',
            'avoidance_active': True,
            'avoid_side': 'left',
            'plan_locked': True,
            'plan_has_obstacle': True,
            'plan_obstacle_done': False,
            'last_cue': None,
            'debug_viz': {'phase': 'avoid_A'},
        },
        bracelet_status={},
    )
    logger.on_frame(None, snap)
    snap2 = dict(snap)
    snap2['belt_phase'] = 'approach'
    snap2['belt_avoidance_active'] = False
    snap2['belt_plan_obstacle_done'] = True
    logger.on_frame(None, snap2)
    logger.end_trial('success')

    events = [
        json.loads(line)
        for line in (logger.session_dir / 'trial_001' / 'events.jsonl')
        .read_text().splitlines()
        if line.strip()
    ]
    names = [e['event'] for e in events]
    assert 'avoidance_enter' in names
    assert 'plan_locked_avoid' in names
    assert 'avoidance_clear_resume' in names


def test_video_writer_optional(tmp_path):
    log = StudyLogger(
        participant_id=1,
        runner='desktop',
        base_dir=tmp_path / 'study',
        save_video=True,
        video_fps=5.0,
    )
    log.start_trial(block='direct', approach_type='direct', distance_m=0.5)
    frame = np.zeros((120, 160, 3), dtype=np.uint8)
    snap = build_frame_snapshot(1, [], 39, {}, {})
    log.on_frame(frame, snap)
    log.on_frame(frame, snap)
    log.end_trial('success')
    overlay = log.session_dir / 'trial_001' / 'overlay.mp4'
    assert overlay.exists()
    assert overlay.stat().st_size > 0
    log.close()


def test_build_frame_snapshot_empty():
    snap = build_frame_snapshot(1, [], -1, None, None)
    assert snap['bottle_detected'] is False
    assert snap['target_depth_m'] == ''


def test_handoff_from_signaled_flag_without_cue(logger):
    logger.start_trial(block='direct', approach_type='direct', distance_m=2.0)
    snap = build_frame_snapshot(
        trial_id=1,
        outputs=[[320, 240, 40, 60, -1, 39, 0.9, 0.55]],
        target_class_id=39,
        belt_status={
            'in_approach': False,
            'navigation_mode': 'locking',
            'avoidance_active': False,
            'plan_locked': False,
            'plan_has_obstacle': False,
            'plan_obstacle_done': False,
            'last_cue': '',
            'signaled_handoff': True,
            'handoff_unix': 123.4,
            'debug_viz': {'phase': ''},
        },
        bracelet_status={
            'is_navigating': True,
            'last_cmd_unix': None,
            'zone_enter_unix': 123.5,
        },
    )
    logger.on_frame(None, snap)
    # second frame with an actual bracelet command (must be >= t0)
    snap2 = dict(snap)
    snap2['last_bracelet_cmd_unix'] = time.time()
    logger.on_frame(None, snap2)
    logger.end_trial('success')

    events = [
        json.loads(line)
        for line in (logger.session_dir / 'trial_001' / 'events.jsonl')
        .read_text().splitlines()
        if line.strip()
    ]
    names = [e['event'] for e in events]
    assert 'handoff' in names
    assert 'bracelet_zone_enter' in names
    assert 'bracelet_first_cmd' in names

    with open(logger.session_dir / 'trial_001' / 'frames.csv', newline='', encoding='utf-8') as f:
        rows = list(csv.DictReader(f))
    assert 'belt_signaled_handoff' in rows[0]
    assert 'bracelet_zone_enter_unix' in rows[0]
    assert int(rows[0]['belt_signaled_handoff']) == 1
    assert 'target_w' in rows[0]
    assert 'hand_detected' in rows[0]
    assert 'belt_vib_intensity' in rows[0]
    assert 'bracelet_vib_motor' in rows[0]


def test_build_frame_snapshot_hand_and_vib_empty():
    snap = build_frame_snapshot(
        trial_id=1,
        outputs=[[100, 100, 20, 30, 1, 39, 0.8, 1.2]],
        target_class_id=39,
        hand_class_ids=[80],
        belt_status={},
        bracelet_status={},
    )
    assert snap['bottle_detected'] is True
    assert snap['hand_detected'] is False
    assert snap['hand_xc'] == ''
    assert snap['belt_vib_angle_deg'] == ''
    assert snap['bracelet_vib_motor'] == ''
