# belt_calibration.py — 16-Motor Navigation Belt Calibration
# Based on bracelet_calibration.py, extended for 16 motors.

import sys
import os

# Use the project file packages instead of the conda packages
parent_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.append(parent_dir)

import keyboard
import time
import json
from pybelt.belt_controller import (BeltOrientationType, BeltVibrationPattern)
from controller import close_app
from belt import connect_navigation_belt as connect_belt

# ── 16-Motor Layout ─────────────────────────────────────────────────────────

NUM_MOTORS = 16
MOTOR_SPACING_DEG = 360.0 / NUM_MOTORS  # 22.5°

DIRECTION_LABELS = [
    "N",   "NNE",  "NE",  "ENE",
    "E",   "ESE",  "SE",  "SSE",
    "S",   "SSW",  "SW",  "WSW",
    "W",   "WNW",  "NW",  "NNW",
]

DIRECTION_DESCRIPTIONS = {
    "N":   "Front",
    "NNE": "Front-Right (slight)",
    "NE":  "Front-Right (diagonal)",
    "ENE": "Right-Front",
    "E":   "Right",
    "ESE": "Right-Back",
    "SE":  "Back-Right (diagonal)",
    "SSE": "Back-Right (slight)",
    "S":   "Back",
    "SSW": "Back-Left (slight)",
    "SW":  "Back-Left (diagonal)",
    "WSW": "Left-Back",
    "W":   "Left",
    "WNW": "Left-Front",
    "NW":  "Front-Left (diagonal)",
    "NNW": "Front-Left (slight)",
}

# Direction label → angle in degrees (same mapping as belt.py)
ORIENTATION_MAPPING = {
    label: i * MOTOR_SPACING_DEG
    for i, label in enumerate(DIRECTION_LABELS)
}


# ── Calibration function ────────────────────────────────────────────────────

def calibrate_intensity(direction):
    """
    Calibrates the vibration intensity of a single belt motor based on user input.
    Sends continuous vibration and allows the user to adjust the intensity [5,100]
    using keyboard inputs (+/-5). Runs until the experimenter confirms ('y').

    Mirrors bracelet_calibration.py's calibrate_intensity() exactly,
    but uses the 16-motor orientation mapping.

    Args:
        direction (str): The direction label for the motor to calibrate.
                         Must be one of the 16 DIRECTION_LABELS (e.g. "N", "NNE", "E").

    Returns:
        int: The final calibrated intensity value.
    """
    intensity = 50  # initial value
    orientation = ORIENTATION_MAPPING[direction]
    motor_index = DIRECTION_LABELS.index(direction)
    description = DIRECTION_DESCRIPTIONS[direction]

    print(f'\n  Motor {motor_index:>2} | {direction:>3} | {orientation:>5.1f}° | {description}')
    print(f'  ↑/↓ = adjust intensity (±5)  |  y = confirm')

    while True:
        if belt_controller:
            belt_controller.send_vibration_command(
                channel_index=0,
                pattern=BeltVibrationPattern.CONTINUOUS,
                intensity=intensity,
                orientation_type=BeltOrientationType.ANGLE,
                orientation=int(orientation),
                pattern_iterations=None,
                pattern_period=500,
                pattern_start_time=0,
                exclusive_channel=False,
                clear_other_channels=True,
            )
        print(f'\r  Vibrating at intensity {intensity:>3} / 100', end='', flush=True)

        if keyboard.is_pressed('up') and intensity < 100:
            intensity += 5
            time.sleep(0.1)
        elif keyboard.is_pressed('down') and intensity > 5:
            intensity -= 5
            time.sleep(0.1)
        elif keyboard.is_pressed('y'):
            belt_controller.stop_vibration()
            print(f'\n  ✓ Confirmed: {direction} = {intensity}')
            time.sleep(1)
            return intensity


def verify_calibration(output):
    """
    Quick verification pass: activates each calibrated motor for 1.5 seconds
    at its saved intensity so the user can feel the result.

    Args:
        output (dict): {direction_label: intensity} calibration results.
    """
    print('\n  ── VERIFICATION PASS ──')
    print('  Each motor will vibrate at its calibrated intensity.')
    input('  Press Enter to begin...')

    for direction in DIRECTION_LABELS:
        if direction not in output:
            continue

        intensity = output[direction]
        orientation = ORIENTATION_MAPPING[direction]
        motor_index = DIRECTION_LABELS.index(direction)

        print(f'  Motor {motor_index:>2} ({direction:>3}) @ {orientation:>5.1f}° → intensity {intensity}')

        if belt_controller:
            belt_controller.send_vibration_command(
                channel_index=0,
                pattern=BeltVibrationPattern.CONTINUOUS,
                intensity=intensity,
                orientation_type=BeltOrientationType.ANGLE,
                orientation=int(orientation),
                pattern_iterations=None,
                pattern_period=500,
                pattern_start_time=0,
                exclusive_channel=False,
                clear_other_channels=True,
            )
        time.sleep(1.5)
        belt_controller.stop_vibration()
        time.sleep(0.3)

    print('  ✓ Verification complete.\n')


def recalibrate_single():
    """
    Allow the user to recalibrate a single motor by entering its index (0–15).

    Returns:
        tuple: (direction_label, new_intensity) or (None, None) if cancelled.
    """
    print(f'\n  Available motors:')
    for i, label in enumerate(DIRECTION_LABELS):
        desc = DIRECTION_DESCRIPTIONS[label]
        print(f'    {i:>2}. {label:>3} — {desc}')

    user_input = input('\n  Enter motor number to recalibrate (0-15), or "skip": ').strip()

    if user_input.lower() == 'skip':
        return None, None

    try:
        idx = int(user_input)
        if 0 <= idx < NUM_MOTORS:
            direction = DIRECTION_LABELS[idx]
            new_intensity = calibrate_intensity(direction)
            return direction, new_intensity
        else:
            print('  ⚠ Must be 0-15.')
            return None, None
    except ValueError:
        print('  ⚠ Enter a number or "skip".')
        return None, None


# ── Main ─────────────────────────────────────────────────────────────────────

if __name__ == '__main__':

    participant = 33
    output_path = str(parent_dir) + '/results/calibration/'

    if not os.path.exists(output_path):
        os.makedirs(output_path)

    # Connect the belt
    connection_check, belt_controller = connect_belt()
    if connection_check:
        print('Belt connection successful.')
    else:
        print('Error connecting belt. Aborting.')
        sys.exit()

    output = {}

    try:
        print('\n' + '═' * 60)
        print('  16-MOTOR NAVIGATION BELT CALIBRATION')
        print('═' * 60)
        print(f'  Motors to calibrate: {NUM_MOTORS}')
        print('  Adjust each motor so ALL motors feel EQUAL in intensity.')
        print('  Use ↑/↓ to adjust, "y" to confirm.\n')
        input('  Press Enter to begin...')

        # ── Phase 1: Calibrate all 16 motors ────────────────────────────
        for motor_direction in DIRECTION_LABELS:
            motor_intensity = calibrate_intensity(motor_direction)
            print(f"  Direction: {motor_direction}, intensity: {motor_intensity}")
            output[motor_direction] = motor_intensity

        # ── Phase 2: Verification pass ───────────────────────────────────
        verify_calibration(output)

        # ── Phase 3: Optional recalibration ──────────────────────────────
        while True:
            redo = input('  Recalibrate a motor? (y/n): ').strip().lower()
            if redo != 'y':
                break
            direction, new_intensity = recalibrate_single()
            if direction is not None:
                output[direction] = new_intensity
                print(f'  Updated: {direction} = {new_intensity}')

        # If any recalibration happened, offer another verification
        reverify = input('  Run verification again? (y/n): ').strip().lower()
        if reverify == 'y':
            verify_calibration(output)

        # ── Save ─────────────────────────────────────────────────────────
        with open(output_path + f"belt_calibration_participant_{participant}.json", "w") as json_file:
            json.dump(output, json_file, indent=4)

        print(f'\n  ✓ Calibration saved → {output_path}belt_calibration_participant_{participant}.json')
        print(json.dumps(output, indent=2))

    except KeyboardInterrupt:
        close_app(belt_controller)

    # In the end, close all processes
    close_app(belt_controller)