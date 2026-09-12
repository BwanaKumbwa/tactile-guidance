#!/usr/bin/env python3
"""
Bracelet familiarisation helper (experiment protocol).

Purpose (from Belt–Bracelet Experiment Protocol):
  - Identify each of the 4 wrist motors in turn (left / right / top / bottom)
  - Give the participant a clear idea of how each motor feels
  - Optionally set a comfortable intensity (start ~50%, ±5% only if needed)

Mounting reminders from the protocol:
  - Control box on dominant-side upper arm, wires down
  - Bracelet two fingers above the wrist
  - Stagger motors: up/down toward the body, left/right toward the hand
  - Black felt: top = motor 2, right = motor 1
  - White felt: top motor marked with a red dot

Prerequisites: HANS server running + phone connected + bracelet on.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request

DEFAULT_BASE = 'http://127.0.0.1:8000'
MOTORS = ('left', 'right', 'top', 'bottom')


def _request(base: str, method: str, path: str, body: dict | None = None, timeout: float = 90.0) -> dict:
    data = None if body is None else json.dumps(body).encode('utf-8')
    headers = {'Content-Type': 'application/json'} if body is not None else {}
    req = urllib.request.Request(
        base.rstrip('/') + path,
        data=data,
        headers=headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        raw = e.read().decode('utf-8', errors='replace')
        return {'ok': False, 'error': f'HTTP {e.code}: {raw}'}
    except Exception as e:
        return {'ok': False, 'error': str(e)}


def _post(base: str, path: str, body: dict | None = None, timeout: float = 90.0) -> dict:
    return _request(base, 'POST', path, body, timeout=timeout)


def _get(base: str, path: str) -> dict:
    return _request(base, 'GET', path, None)


def _print(result: dict) -> None:
    print(json.dumps(result, indent=2))


def _menu(participant: int, intensity: int) -> None:
    print(
        f"""
======== Bracelet familiarisation (P{participant}, {intensity}%) ========
  Say to the participant: "You'll feel one motor at a time on the
  wrist — tell me which side it feels like."

  1) Feel all 4 motors (left → right → top → bottom)
  2) Feel one motor
  3) Comfort check: buzz one motor, ± intensity ±5% if needed
  4) Show current intensities
  5) Save comfort levels for this participant (optional)
  6) Stop vibration
  0) Done
=================================================================
"""
    )


def _comfort_check(base: str, motor: str, intensity: int) -> int:
    """Short feel-and-adjust: identify the motor, only change intensity if unclear."""
    inten = intensity
    print(f'\nFeeling {motor}. Ask: "Where do you feel that?"')
    print('Then: [Enter]=OK / clear  +=a bit stronger  -=a bit weaker  q=next motor')
    while True:
        print(f'→ {motor} @ {inten}%')
        _print(_post(base, '/bracelet/test_motor',
                     {'motor': motor, 'intensity': inten}))
        cmd = input('  [Enter/+/- /q]: ').strip().lower()
        if cmd in ('q', 'quit', 'done', 'n', 'next'):
            _post(base, '/bracelet/set_intensity',
                  {'motor': motor, 'intensity': inten})
            return inten
        if cmd in ('+', '='):
            inten = min(100, inten + 5)
        elif cmd == '-':
            inten = max(0, inten - 5)
        # Enter = retest same level so they can confirm the location


def main() -> int:
    parser = argparse.ArgumentParser(description='Bracelet familiarisation helper')
    parser.add_argument('--base', default=DEFAULT_BASE)
    parser.add_argument('--participant', type=int, default=1)
    parser.add_argument('--intensity', type=int, default=50,
                        help='Comfortable default (protocol ~50% baseline)')
    args = parser.parse_args()
    base = args.base
    participant = args.participant
    intensity = max(0, min(100, args.intensity))

    print(f'Server: {base}')
    print(f'Participant: {participant}')
    print('Goal: know how each wrist motor feels — not a long calibration exam.')

    while True:
        _menu(participant, intensity)
        choice = input('Choice: ').strip().lower()
        if choice in ('0', 'q', 'quit', 'exit', 'done'):
            print('Bracelet familiarisation finished.')
            return 0

        if choice == '1':
            _print(_post(base, f'/bracelet/test_all_motors?intensity={intensity}', None))

        elif choice == '2':
            motor = input('Motor [left / right / top / bottom]: ').strip().lower().strip('\'"')
            _print(_post(base, '/bracelet/test_motor',
                         {'motor': motor, 'intensity': intensity}))

        elif choice == '3':
            motor = input('Motor [left / right / top / bottom / all]: ').strip().lower().strip('\'"')
            if motor == 'all':
                for m in MOTORS:
                    intensity = _comfort_check(base, m, intensity)
            else:
                intensity = _comfort_check(base, motor, intensity)

        elif choice == '4':
            _print(_get(base, '/bracelet/intensities'))

        elif choice == '5':
            _print(_post(base, '/bracelet/save_calibration',
                         {'participant': participant}))

        elif choice == '6':
            _print(_post(base, '/bracelet/stop', {}))

        else:
            print('Unknown choice.')


if __name__ == '__main__':
    sys.exit(main())
