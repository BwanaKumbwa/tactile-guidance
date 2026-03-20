import sys
import os

# Use the project file packages instead of the conda packages, i.e. add to system path for import
parent_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.append(parent_dir)

import keyboard
import time
import json
from pybelt.belt_controller import (BeltOrientationType, BeltVibrationPattern)
##from bracelet import connect_belt
from controller import close_app
from belt import connect_navigation_belt as connect_belt

def calibrate_intensity(direction):
    """
    Calibrates the vibration intensity of a belt based on user input.
    This function sends continuous vibration commands to a belt controller
    and allows the user to adjust the intensity [5,100] using keyboard inputs (+/-5).
    The function runs in a loop until the experimenter confirms the calibration ('y').

    Args:
        direction (str): The direction for which the belt should vibrate.
                     Must be one of 'bottom', 'top', 'left', or 'right'.

    Returns:
        intensity: The final calibrated intensity value.
    """
    intensity = 50  # initial value
    orientation_mapping = {"bottom": 60,
                           "top": 90,
                           "left": 45,
                           "right": 120}
    orientation = orientation_mapping[direction]

    while True:
        if belt_controller:
            belt_controller.send_vibration_command(
                channel_index=0,
                pattern=BeltVibrationPattern.CONTINUOUS,
                intensity=intensity,
                orientation_type=BeltOrientationType.ANGLE,
                orientation=orientation,  # down
                pattern_iterations=None,
                pattern_period=500,
                pattern_start_time=0,
                exclusive_channel=False,
                clear_other_channels=False
            )
        print(f'Vibrating at intensity {intensity}.')

        if keyboard.is_pressed('up') and intensity < 100:
            intensity += 5
            time.sleep(0.1)
        elif keyboard.is_pressed('down') and intensity > 5:  # no reason to vibrate with intensity of 0
            intensity -= 5
            time.sleep(0.1)
        elif keyboard.is_pressed('y'):
            belt_controller.stop_vibration()
            time.sleep(1)
            return intensity


if __name__ == '__main__':

    participant = 33
    output_path = str(parent_dir) + '/results/calibration/'

    if not os.path.exists(output_path):
        os.makedirs(output_path)

    # Connect the bracelet
    connection_check, belt_controller = connect_belt()
    if connection_check:
        print('Bracelet connection successful.')
    else:
        print('Error connecting bracelet. Aborting.')
        sys.exit()

    directions = ["bottom", "top", "left", "right"]  # top motor - number 2, right motor - number 1
    output = {}

    try:
        # Calibrate bracelet for each direction
        for motor_direction in directions:
            motor_intensity = calibrate_intensity(motor_direction)
            print(f"Direction: {motor_direction}, intensity: {motor_intensity}")
            output[motor_direction] = motor_intensity

        # Save the calibration results after experimenter confirmation
        with open(output_path + f"calibration_participant_{participant}.json", "w") as json_file:
            json.dump(output, json_file)

    except KeyboardInterrupt:
        close_app(belt_controller)

    # In the end, close all processes
    close_app(belt_controller)