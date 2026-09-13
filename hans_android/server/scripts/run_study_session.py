#!/usr/bin/env python3
"""
Interactive experimenter helper for belt–bracelet study trials.

Talks to a running server at http://127.0.0.1:8000 /study/* endpoints.
Designed for ~108 trials: start → sync at t0 → end, with minimal typing.

Usage (server already running, phone/devices connected):
  /home/amislam/miniconda3/envs/hans_android/bin/python \
    hans_android/server/scripts/run_study_session.py --participant 1
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from typing import Any, Dict, Optional

DEFAULT_BASE = 'http://127.0.0.1:8000'

BLOCKS = ('direct', 'lateral', 'dynamic', 'verification', 'training')
APPROACHES = ('direct', 'lateral', 'dynamic')
OUTCOMES = {
    'y': 'success',
    'n': 'fail',
    't': 'timeout',
    'a': 'abort',
    'f': 'tech_failure',
}


def _req(base: str, method: str, path: str, body: Optional[dict] = None) -> dict:
    data = None
    headers = {}
    if body is not None:
        data = json.dumps(body).encode('utf-8')
        headers['Content-Type'] = 'application/json'
    req = urllib.request.Request(
        base.rstrip('/') + path,
        data=data,
        headers=headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        raw = e.read().decode('utf-8', errors='replace')
        try:
            return json.loads(raw)
        except Exception:
            return {'ok': False, 'error': f'HTTP {e.code}: {raw}'}
    except Exception as e:
        return {'ok': False, 'error': str(e)}


def _print(resp: Dict[str, Any]) -> None:
    print(json.dumps(resp, indent=2))


def _ask(prompt: str, default: Optional[str] = None) -> str:
    suffix = f' [{default}]' if default is not None else ''
    raw = input(f'{prompt}{suffix}: ').strip()
    if not raw and default is not None:
        return default
    return raw


def _ask_float(prompt: str, default: float) -> float:
    while True:
        raw = _ask(prompt, str(default))
        try:
            return float(raw)
        except ValueError:
            print('Enter a number, e.g. 2.0 or 1.5')


def _ask_approach(default: str = 'direct') -> str:
    """Require a named approach type — reject bare numbers like '3'."""
    while True:
        raw = _ask(
            f'approach_type ({"/".join(APPROACHES)}) — not the distance',
            default,
        ).lower()
        if raw in APPROACHES:
            return raw
        if raw.replace('.', '', 1).isdigit():
            print(f'"{raw}" looks like a distance. Enter approach type first '
                  f'(direct/lateral/dynamic), then distance_m separately.')
            continue
        print(f'Unknown approach {raw!r}. Use: {", ".join(APPROACHES)}')


def start_session(base: str, participant: int, notes: str) -> None:
    print('\n--- Starting study session ---')
    _print(_req(base, 'POST', '/study/session/start', {
        'participant_id': participant,
        'notes': notes,
    }))


def one_trial(base: str, defaults: dict) -> bool:
    """Run one trial loop. Returns False if user wants to quit."""
    print('\n======== New trial ========')
    print('Blocks:', ', '.join(BLOCKS))
    block = _ask('block', defaults.get('block', 'direct')).lower()
    if block in ('q', 'quit'):
        return False
    if block not in BLOCKS:
        print(f'Unknown block {block!r} — still sending (server may reject).')

    if block == 'verification':
        approach = 'direct'
        distance = 0.5
        print('verification → approach=direct, distance=0.5 m')
    elif block == 'dynamic':
        approach = 'dynamic'
        distance = _ask_float('distance_m (chair uses 1.5 not 1.0)', defaults.get('distance_m', 1.5))
    elif block == 'training':
        approach = _ask_approach(defaults.get('approach_type', 'direct'))
        distance = _ask_float('distance_m', defaults.get('distance_m', 2.0))
    else:
        approach = _ask_approach(defaults.get('approach_type', block))
        distance = _ask_float('distance_m', defaults.get('distance_m', 2.0))

    include = block != 'training'
    if block == 'training':
        print('training → include_in_analysis=false')
    else:
        inc_raw = _ask('include_in_analysis (y/n)', 'y').lower()
        include = inc_raw not in ('n', 'no', '0', 'false')

    defaults['block'] = block
    defaults['approach_type'] = approach
    defaults['distance_m'] = distance

    print('\n1) START trial')
    start = _req(base, 'POST', '/study/trial/start', {
        'block': block,
        'approach_type': approach,
        'distance_m': distance,
        'include_in_analysis': include,
    })
    _print(start)
    if not start.get('ok', True) and start.get('error'):
        print('Start failed — fix and try again.')
        return True

    print('\n2) IMPORTANT: press ENTER at t0 = when the voice command is parsed')
    print('   (too late makes sync useless for latency / camera alignment)')
    input('   Press ENTER now for SYNC…')
    print('SYNC')
    _print(_req(base, 'POST', '/study/sync'))

    print('\n3) Trial running. When finished, enter outcome:')
    print('   y=success  n=fail  t=timeout  a=abort  f=tech_failure')
    while True:
        key = _ask('outcome', 'y').lower()
        if key in OUTCOMES:
            outcome = OUTCOMES[key]
            break
        if key in OUTCOMES.values():
            outcome = key
            break
        print('Use y/n/t/a/f')

    print('END')
    _print(_req(base, 'POST', '/study/trial/end', {'outcome': outcome}))

    print('\nStatus:')
    _print(_req(base, 'GET', '/study/status'))
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description='Interactive study trial helper')
    parser.add_argument('--participant', type=int, default=1)
    parser.add_argument('--base', default=DEFAULT_BASE)
    parser.add_argument('--notes', default='')
    parser.add_argument('--skip-session-start', action='store_true',
                        help='Do not POST /study/session/start (reuse existing)')
    args = parser.parse_args()

    notes = args.notes or f'P{args.participant} interactive session'
    print(f'Server: {args.base}')
    print(f'Participant: {args.participant}')

    status = _req(args.base, 'GET', '/study/status')
    if status.get('error') and 'not ready' in str(status.get('error', '')).lower():
        print('\nStudy logger not ready yet.')
        print('Is the server running and has the phone connected / AI thread started?')
        _print(status)
        return 1
    if 'error' in status and status.get('ok') is False and 'Failed' in str(status.get('error', '')):
        print('\nCannot reach server. Start it first, then re-run this script.')
        _print(status)
        return 1

    print('\nCurrent /study/status:')
    _print(status)

    if not args.skip_session_start:
        ans = _ask('Start/replace study session for this participant? (y/n)', 'y').lower()
        if ans in ('y', 'yes'):
            start_session(args.base, args.participant, notes)

    defaults = {'block': 'direct', 'approach_type': 'direct', 'distance_m': 2.0}
    print('\nReady. For each trial: start → ENTER at t0 → outcome.')
    print('At block prompt, type q to quit.\n')
    print('Remember: NASA-TLX after each approach block; usability + debrief at end.')
    print('Fixed camera with 0.50 / 0.70 m tape should be recording separately.\n')

    while True:
        if not one_trial(args.base, defaults):
            break
        more = _ask('Another trial? (y/n)', 'y').lower()
        if more in ('n', 'no', 'q', 'quit'):
            break

    print('\nDone. Logs under hans_android/server/results/study/P{}/'.format(args.participant))
    return 0


if __name__ == '__main__':
    sys.exit(main())
