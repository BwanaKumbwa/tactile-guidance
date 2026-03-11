import sys
import os
import time
import keyboard

# Use project packages
parent_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.append(parent_dir)

from pybelt.belt_controller import BeltOrientationType, BeltVibrationPattern
from bracelet import connect_belt

# ORIENTATION BINARY_MASK
ORIENTATIONS = {
    'top': 0b0000000000010000,
    'right': 0b0000000000100000,
    'left': 0b0000000000000100,
    'down': 0b0000000000001000,
    'top_front': 0b0000000000000010,
    'top_back': 0b0000000000000001
}

NUMBER_TO_DIRECTION = {
    1: 'top',
    2: 'right',
    3: 'left',
    4: 'down',
    7: 'top_front',
    8: 'top_back'
}

# TIMING PARAMETERS (seconds)
DOS = 0.2       # Duration of stimulus (200 ms)
SOA = 0.15      # Stimulus onset asynchrony (150 ms)
INTERVAL = 0.4  # Interval between sequences (400 ms)

# CONTINUOUS VIBRATION (Stop with Y)
def vibrate_until_key(orientation, intensity=50):
    belt_controller.send_vibration_command(
        channel_index=0,
        pattern=BeltVibrationPattern.CONTINUOUS,
        intensity=intensity,
        orientation_type=BeltOrientationType.BINARY_MASK,
        orientation=orientation,
        pattern_iterations=None,
        pattern_period=500,
        pattern_start_time=0,
        exclusive_channel=False,
        clear_other_channels=False
    )

    print("Vibrating... Press 'Y' to stop")

    while True:
        if keyboard.is_pressed('y'):
            belt_controller.stop_vibration()
            time.sleep(0.1)
            break

# SEQUENCE VIBRATIONS WITH DOS, SOA, INTERVAL
def vibrate_sequence(tactors):
    for orientation in tactors:
        # Vibrate current tactor
        belt_controller.send_vibration_command(
            channel_index=0,
            pattern=BeltVibrationPattern.CONTINUOUS,
            intensity=50,
            orientation_type=BeltOrientationType.BINARY_MASK,
            orientation=orientation,
            pattern_iterations=None,
            pattern_period=500,
            pattern_start_time=0,
            exclusive_channel=False,
            clear_other_channels=True  # Stop previous tactor for smoothness
        )

        # Keep ON for DOS
        time.sleep(DOS)

        # Wait SOA before next tactor
        time.sleep(SOA)

    # Stop all tactors at end
    belt_controller.stop_vibration()
    time.sleep(INTERVAL)

# FORWARD AND BACKWARD SEQUENCES (dynamic motor count)
def vibrate_forward(num_motors=2):
    if num_motors == 2:
        tactors = [ORIENTATIONS['top_back'], ORIENTATIONS['top']]
    elif num_motors == 3:
        tactors = [ORIENTATIONS['top_back'], ORIENTATIONS['top'], ORIENTATIONS['top_front']]
    else:
        print("Invalid number of motors for forward (choose 2 or 3).")
        return
    vibrate_sequence(tactors)

def vibrate_backward(num_motors=2):
    if num_motors == 2:
        tactors = [ORIENTATIONS['top'], ORIENTATIONS['top_back']]
    elif num_motors == 3:
        tactors = [ORIENTATIONS['top_front'], ORIENTATIONS['top'], ORIENTATIONS['top_back']]
    else:
        print("Invalid number of motors for backward (choose 2 or 3).")
        return
    vibrate_sequence(tactors)

# USER SELECTION
def play_vibration(choice):
    choice_str = str(choice)
    if choice_str in ['1', '2', '3', '4','7','8']:
        direction_name = NUMBER_TO_DIRECTION[int(choice_str)]
        vibrate_until_key(ORIENTATIONS[direction_name])
    elif choice_str in ['52', '53', '62', '63']:
        main = int(choice_str[0])
        motors = int(choice_str[1])
        if main == 5:
            vibrate_forward(motors)
        elif main == 6:
            vibrate_backward(motors)
    else:
        print("Invalid option")

# MAIN PROGRAM
if __name__ == '__main__':
    print("Connect the belt.")
    connection_check, belt_controller = connect_belt()

    if connection_check:
        print('Bracelet connection successful.')
    else:
        print('Error connecting bracelet. Aborting.')
        sys.exit()

    try:
        while True:
            print("\nChoose vibration type:")
            print("1 = Top")
            print("2 = Right")
            print("3 = Left")
            print("4 = Down")
            print("5 = Forward (2 Motors or 3 Motors)")
            print("6 = Backward (2 Motors or 3 Motors)")
            print("7 = Top Front")
            print("8 = Top Back")
            print("0 = Exit")

            choice = int(input("Enter type: "))

            if choice == 0:
                break

            play_vibration(choice)

    except KeyboardInterrupt:
        pass

    finally:
        try:
            belt_controller.stop_vibration()
            belt_controller.disconnect_belt()
            print("Belt safely disconnected.")
        except:
            pass