"""Unit tests for StudyLogger (no hardware / no VisionPipeline)."""
from __future__ import annotations

import csv
import json

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
        outputs=[[320, 240, 40, 60, -1, 39, 0.9, 2.5]],
        target_class_id=39,
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
            'debug_viz': {'phase': 'approach'},
        },
        bracelet_status={'is_navigating': False, 'last_cmd_unix': None},
    )
    logger.on_frame(None, snap)
    # Second frame with handoff cue → event
    snap2 = dict(snap)
    snap2['belt_last_cue'] = 'handoff'
    snap2['belt_phase'] = 'approach'
    logger.on_frame(None, snap2)

    sync = logger.mark_sync()
    assert sync['sync_unix'] > 0

    end = logger.end_trial('success')
    assert end['outcome'] == 'success'
    assert end['include_in_analysis'] is True
    assert not logger.trial_active

    # frames.csv has header + >=1 data row
    with open(trial_dir / 'frames.csv', newline='', encoding='utf-8') as f:
        rows = list(csv.DictReader(f))
    assert rows
    assert set(FRAME_FIELDS) == set(rows[0].keys())
    assert int(rows[0]['bottle_detected']) == 1

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
    assert trials[0]['outcome'] == 'success'


def test_tech_failure_excluded(logger):
    logger.start_trial(
        block='lateral', approach_type='lateral', distance_m=3.0,
    )
    logger.end_trial('tech_failure')
    with open(logger.session_dir / 'trials.csv', newline='', encoding='utf-8') as f:
        trials = list(csv.DictReader(f))
    assert str(trials[-1]['include_in_analysis']).lower() in ('false', '0')


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
