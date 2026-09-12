#!/usr/bin/env python3
"""
Belt familiarisation helper (experiment protocol).

Purpose (from Belt–Bracelet Experiment Protocol):
  - Let the participant feel each of the 16 waist motors in turn
  - Find / mark the green navel (front) motor
  - Confirm front / right / back / left are clear and comfortable

This is NOT a long intensity-tuning session. Use a comfortable default
intensity; only nudge it if a motor is too weak or too strong to notice.

Prerequisites: HANS server running + phone connected + belt on.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request

DEFAULT_BASE = 'http://127.0.0.1:8000'


def _post(base: str, path: str, body: dict | None = None, timeout: float = 180.0) -> dict:
    data = None if body is None else json.dumps(body).encode('utf-8')
    req = urllib.request.Request(
        base.rstrip('/') + path,
        data=data,
        headers={'Content-Type': 'application/json'} if body is not None else {},
        method='POST',
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        raw = e.read().decode('utf-8', errors='replace')
        return {'ok': False, 'error': f'HTTP {e.code}: {raw}'}
    except Exception as e:
        return {'ok': False, 'error': str(e)}


def _print(result: dict) -> None:
    print(json.dumps(result, indent=2))


def _menu(intensity: int) -> None:
    print(
        f"""
=========== Belt familiarisation (intensity {intensity}%) ===========
  Say to the participant: "You'll feel one motor at a time around
  the waist — just notice where it is."

  1) Feel all 16 motors in order (identify / locate each)
  2) Feel next motor
  3) Feel previous motor
  4) Repeat the same motor
  5) Mark the green-marker motor as navel / front
  6) Feel the four directions (front → right → back → left)
  7) Feel one named direction (front/right/back/left)
  8) Flip left/right if those feel swapped
  9) Change buzz intensity for the next tests (±5% if needed)
  0) Done
===============================================================
"""
    )


def main() -> int:
    parser = argparse.ArgumentParser(description='Belt familiarisation helper')
    parser.add_argument('--base', default=DEFAULT_BASE)
    parser.add_argument('--intensity', type=int, default=50,
                        help='Comfortable default (protocol starts near 50%)')
    args = parser.parse_args()
    base = args.base
    intensity = max(0, min(100, args.intensity))
    probe = 0

    print(f'Server: {base}')
    print('Belt on, green marker at the navel, phone connected.')
    print('Goal: participant recognises each motor location — not fine intensity tuning.')

    while True:
        _menu(intensity)
        choice = input('Choice: ').strip().lower()
        if choice in ('0', 'q', 'quit', 'exit', 'done'):
            print('Belt familiarisation finished.')
            return 0

        if choice == '1':
            print('Buzzing motors 0→15. Ask which index sits on the green marker...')
            _print(_post(base, f'/belt/test_all_motors?intensity={intensity}', None))

        elif choice == '2':
            probe = (probe + 1) % 16
            print(f'→ Motor index {probe}')
            _print(_post(base, '/belt/test_motor_index',
                         {'index': probe, 'intensity': intensity}))

        elif choice == '3':
            probe = (probe - 1) % 16
            print(f'→ Motor index {probe}')
            _print(_post(base, '/belt/test_motor_index',
                         {'index': probe, 'intensity': intensity}))

        elif choice == '4':
            print(f'→ Repeat motor index {probe}')
            _print(_post(base, '/belt/test_motor_index',
                         {'index': probe, 'intensity': intensity}))

        elif choice == '5':
            raw = input(f'Green-marker / navel index [Enter = {probe}]: ').strip()
            idx = probe if raw == '' else int(raw)
            _print(_post(base, '/belt/set_navel', {'index': idx}))
            probe = idx

        elif choice == '6':
            print('front → right → back → left')
            _print(_post(base, f'/belt/test_cardinals?intensity={intensity}', None, timeout=90))

        elif choice == '7':
            which = input('Direction [front/right/back/left]: ').strip().lower() or 'front'
            _print(_post(base, '/belt/test_motor',
                         {'motor': which, 'intensity': intensity}))

        elif choice == '8':
            _print(_post(base, '/belt/flip_direction', {}))

        elif choice == '9':
            raw = input(f'New intensity 0–100 [{intensity}] (hint: ±5 from 50): ').strip()
            if raw:
                intensity = max(0, min(100, int(raw)))
            print(f'Using intensity {intensity}% for further buzzes.')

        else:
            print('Unknown choice.')


if __name__ == '__main__':
    sys.exit(main())
